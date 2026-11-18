//! KINO's native player (macOS « Lecteur natif »): libmpv draws under the web
//! view and the page's controls stay on top. This part is the player's
//! language: the commands the page may send, its options, the subtitles it
//! is given. The window provides the player itself (`NativePlayer`).

use serde_json::{Map, Value, json};

use crate::play::text;

pub const FRENCH: &str = "fr,fre,fra,french";
/// « Voix » boost: dynamic normalisation lifts dialogue and softens peaks.
pub const VOICE_FILTER: &str = "lavfi=[dynaudnorm=f=150:g=15:p=0.9]";
pub const SUBTITLE_MAX_BYTES: usize = 2_000_000;
/// « Clarté » levels as (gamma, brightness, contrast), applied while mpv renders.
const CLARITY_LEVELS: [(i64, i64, i64); 4] = [(0, 0, 0), (12, 3, 2), (22, 6, 3), (32, 9, 4)];
/// KINO's own local redirections mpv may open (next episode, AllDebrid, TorBox).
const LOCAL_ROUTES: [&str; 3] = ["/api/auto-stream", "/api/ad-dl", "/api/torbox-dl"];

/// The options every native playback starts with.
pub fn options(
    pref_lang: crate::config::PrefLang,
    start: f64,
    volume: Option<f64>,
) -> Vec<(String, String)> {
    let mut options: Vec<(String, String)> = [
        ("cache", "yes"),
        ("demuxer-max-bytes", "150MiB"),
        ("demuxer-max-back-bytes", "50MiB"),
        ("sub-auto", "fuzzy"),
        // Subtitles above KINO's control bar.
        ("sub-margin-y", "90"),
        // Direct media only: no youtube-dl lookup, no built-in scripts.
        ("ytdl", "no"),
        ("load-scripts", "no"),
    ]
    .iter()
    .map(|(k, v)| (k.to_string(), v.to_string()))
    .collect();
    options.push((
        if pref_lang == crate::config::PrefLang::Vostfr {
            "slang"
        } else {
            "alang"
        }
        .into(),
        FRENCH.into(),
    ));
    if let Some(volume) = volume {
        options.push(("volume".into(), short(volume)));
    }
    if start > 0.0 {
        options.push(("start".into(), short(start)));
    }
    options
}

/// A number as Python's str(round(x, 3)).
fn short(x: f64) -> String {
    let r = crate::sources::stream::py_round(x, 3);
    if r.fract() == 0.0 {
        format!("{r:.1}")
    } else {
        format!("{r}")
    }
}

fn number(value: &Value, low: f64, high: f64) -> Result<f64, String> {
    let n = match value {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        _ => None,
    }
    .ok_or("number")?;
    if !(low..=high).contains(&n) {
        return Err("out of range".into());
    }
    Ok(crate::sources::stream::py_round(n, 3))
}

/// The page's command as mpv commands; anything else is refused.
pub fn mpv_commands(cmd: &str, value: &Value) -> Result<Vec<Vec<String>>, String> {
    let one = |args: &[&str]| Ok(vec![args.iter().map(|a| a.to_string()).collect()]);
    let n = |low, high| number(value, low, high).map(short);
    match cmd {
        "toggle" => one(&["cycle", "pause"]),
        "play" | "pause" => one(&["set", "pause", if cmd == "pause" { "yes" } else { "no" }]),
        "seek" => one(&["seek", &n(-36000.0, 36000.0)?, "relative"]),
        "goto" => one(&["seek", &n(0.0, 360000.0)?, "absolute"]),
        "volume" => one(&["set", "volume", &n(0.0, 130.0)?]),
        "mute" => one(&["cycle", "mute"]),
        "audio" | "subtitles" => {
            let off =
                matches!(value, Value::Null | Value::Bool(false)) || value == "" || value == "no";
            if off && cmd == "audio" {
                return Err("audio track".into());
            }
            let track = if off {
                "no".to_string()
            } else {
                (number(value, 1.0, 999.0)? as i64).to_string()
            };
            one(&["set", if cmd == "audio" { "aid" } else { "sid" }, &track])
        }
        "speed" => one(&["set", "speed", &n(0.25, 4.0)?]),
        "sub_scale" => one(&["set", "sub-scale", &n(0.5, 3.0)?]),
        "voice" => match value.as_str() {
            Some("boost") => one(&["set", "af", VOICE_FILTER]),
            Some("normal") => one(&["set", "af", ""]),
            _ => Err("voice mode".into()),
        },
        "clarity" => {
            let level = number(value, 0.0, 3.0)?;
            if level.fract() != 0.0 {
                return Err("clarity level".into());
            }
            let (g, b, c) = CLARITY_LEVELS[level as usize];
            Ok([("gamma", g), ("brightness", b), ("contrast", c)]
                .iter()
                .map(|(k, v)| vec!["set".into(), k.to_string(), v.to_string()])
                .collect())
        }
        _ => Err(format!("unknown command {cmd:?}")),
    }
}

/// SRT text from KINO's subtitle cues [{start, end, text}] (offline subtitles).
pub fn cues_to_srt(cues: &Value) -> String {
    let stamp = |seconds: f64| {
        let millis = (seconds.max(0.0) * 1000.0).round_ties_even() as i64;
        format!(
            "{:02}:{:02}:{:02},{:03}",
            millis / 3_600_000,
            millis % 3_600_000 / 60_000,
            millis % 60_000 / 1000,
            millis % 1000
        )
    };
    let mut blocks: Vec<String> = vec![];
    for cue in cues.as_array().into_iter().flatten() {
        let (Some(start), Some(end)) = (cue["start"].as_f64(), cue["end"].as_f64()) else {
            continue;
        };
        let line = text(&cue["text"]).trim().to_string();
        if !line.is_empty() && end > start {
            blocks.push(format!(
                "{}\n{} --> {}\n{line}\n",
                blocks.len() + 1,
                stamp(start),
                stamp(end)
            ));
        }
    }
    blocks.join("\n")
}

/// Subtitle bytes as text: UTF-8 (without its mark) when valid, else Latin-1.
pub fn decode_subtitle(raw: &[u8]) -> String {
    let raw = &raw[..raw.len().min(SUBTITLE_MAX_BYTES)];
    let raw = raw.strip_prefix(b"\xef\xbb\xbf").unwrap_or(raw);
    crate::subtitles::decode(raw)
}

/// Debrid sources reach mpv only as an https address, or as one of KINO's own
/// local redirections: never a local file or one of mpv's special protocols.
pub fn stream_allowed(url: &str, port: u16) -> bool {
    if url.is_empty() || url.len() > 4000 || url.chars().any(|c| (c as u32) < 33) {
        return false;
    }
    let Ok(parts) = reqwest::Url::parse(url) else {
        return false;
    };
    if parts.scheme() == "https"
        && parts.host_str().is_some_and(|h| !h.is_empty())
        && parts.username().is_empty()
    {
        return true;
    }
    parts.scheme() == "http"
        && parts.host_str() == Some("127.0.0.1")
        && parts.port() == Some(port)
        && LOCAL_ROUTES.contains(&parts.path())
}

/// The native player the window provides. Its calls come from the server's
/// threads; the player moves to the main thread what must run there.
pub trait NativePlayer: Send + Sync {
    /// Opens a file or a stream; returns the state.
    fn open(
        &self,
        path: &str,
        start: f64,
        volume: Option<f64>,
        pref_lang: crate::config::PrefLang,
    ) -> Result<Value, String>;
    /// Runs mpv commands (already checked).
    fn command(&self, commands: &[Vec<String>]) -> Result<(), String>;
    /// Saves the current frame to `path`.
    fn screenshot(&self, path: &std::path::Path) -> Result<(), String>;
    /// Adds an SRT text written by KINO.
    fn add_subtitle(&self, srt: &str, title: &str, lang: &str, select: bool) -> Result<(), String>;
    /// {open: false} or the playback's state.
    fn state(&self) -> Value;
    fn close(&self) -> Value;
    /// The file being played and the position, for a capture by FFmpeg.
    fn source_and_time(&self) -> Option<(String, f64)>;
}

/// The page's « voix » and « clarté » choices, shown back in the state.
pub fn remember(settings: &mut Map<String, Value>, cmd: &str, value: &Value) {
    match cmd {
        "voice" => {
            settings.insert("voice".into(), value.clone());
        }
        "clarity" => {
            settings.insert(
                "clarity".into(),
                json!(number(value, 0.0, 3.0).unwrap_or(0.0) as i64),
            );
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn commands_and_texts() {
        assert_eq!(
            mpv_commands("seek", &json!(-10)).unwrap(),
            vec![vec!["seek", "-10.0", "relative"]]
        );
        assert_eq!(
            mpv_commands("subtitles", &json!("no")).unwrap(),
            vec![vec!["set", "sid", "no"]]
        );
        assert_eq!(
            mpv_commands("audio", &json!(2)).unwrap(),
            vec![vec!["set", "aid", "2"]]
        );
        assert!(mpv_commands("audio", &Value::Null).is_err());
        assert!(mpv_commands("volume", &json!(200)).is_err());
        assert!(mpv_commands("run", &json!("rm")).is_err());
        assert_eq!(mpv_commands("clarity", &json!(2)).unwrap().len(), 3);
        assert!(mpv_commands("clarity", &json!(1.5)).is_err());
        let srt = cues_to_srt(
            &json!([{"start": 1.5, "end": 3.25, "text": " Bonjour "}, {"start": 5, "end": 4, "text": "x"}, {"start": 3661.0, "end": 3662.0, "text": "Fin"}]),
        );
        assert_eq!(
            srt,
            "1\n00:00:01,500 --> 00:00:03,250\nBonjour\n\n2\n01:01:01,000 --> 01:01:02,000\nFin\n"
        );
        assert_eq!(decode_subtitle(b"\xef\xbb\xbfOui"), "Oui");
        assert_eq!(decode_subtitle(b"caf\xe9"), "café");
        assert!(stream_allowed("https://cdn.example/a.mkv", 8080));
        assert!(stream_allowed(
            "http://127.0.0.1:8080/api/auto-stream",
            8080
        ));
        assert!(!stream_allowed("http://127.0.0.1:8080/api/config", 8080));
        assert!(
            !stream_allowed("file:///etc/passwd", 8080)
                && !stream_allowed("https://user@x/a", 8080)
        );
        assert_eq!(
            options(crate::config::PrefLang::Vostfr, 12.0, Some(80.0))
                .last()
                .unwrap(),
            &("start".to_string(), "12.0".to_string())
        );
    }
}
