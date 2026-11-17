//! French and English subtitles from the OpenSubtitles add-on, ranked for the
//! release being played, and their lines for KINO's own player.

use std::collections::HashSet;
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use regex::Regex;
use serde_json::{Value, json};

use crate::meta::Meta;
use crate::net::BROWSER;
use crate::sources::stream::{py_quote, py_round};

static PARTIAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:^|[^a-z0-9])(?:cd|disc|part)[ ._-]*[1-9](?:[^0-9]|$)").unwrap()
});
static FORMATS: LazyLock<Vec<(&'static str, Regex)>> = LazyLock::new(|| {
    [
        ("web", r"web[ ._-]?(?:dl|rip)?|amzn|nf|dsnp|atvp|hmax"),
        ("bluray", r"blu[ ._-]?ray|bdrip|brrip|bdremux|remux"),
        ("hdtv", r"hdtv|pdtv"),
        ("dvd", r"dvd(?:rip|scr)?"),
    ]
    .into_iter()
    .map(|(family, pattern)| {
        (
            family,
            Regex::new(&format!(r"(?:^|[^a-z0-9])(?:{pattern})(?:[^a-z0-9]|$)")).unwrap(),
        )
    })
    .collect()
});
static WORDS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[a-z0-9]+").unwrap());
static SDH: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(sdh|hearing[ ._-]?impaired|malentendants?)\b").unwrap());
static FORCED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(forced|forc[eé]s?)\b").unwrap());
static TIME: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})")
        .unwrap()
});
static BLANK_LINE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\n\s*\n").unwrap());
static BRACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{[^}]+\}").unwrap());
static TAGS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>]+>").unwrap());
const COMMON: [&str; 24] = [
    "the", "a", "of", "and", "srt", "mkv", "mp4", "x264", "x265", "h264", "h265", "hevc", "1080p",
    "720p", "2160p", "480p", "aac", "ac3", "dts", "fr", "fre", "en", "eng", "",
];

/// Subtitle files KINO listed: their lines may be fetched.
static LISTED: LazyLock<Mutex<HashSet<String>>> = LazyLock::new(Default::default);

fn text(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

fn or_text(v: &Value) -> String {
    if matches!(v, Value::Null | Value::Bool(false)) || v == &json!(0) || v == &json!("") {
        String::new()
    } else {
        text(v)
    }
}

fn release_words(s: &str) -> HashSet<String> {
    WORDS
        .find_iter(&s.to_lowercase())
        .map(|m| m.as_str().to_string())
        .filter(|w| !COMMON.contains(&w.as_str()))
        .collect()
}

fn release_format(s: &str) -> &'static str {
    let low = s.to_lowercase();
    FORMATS
        .iter()
        .find(|(_, p)| p.is_match(&low))
        .map(|(f, _)| *f)
        .unwrap_or("")
}

/// Higher when a subtitle was timed for the release being played; partial (CD1) ones sink.
fn fit(candidate: &Value, release: &str, fps: Option<f64>) -> f64 {
    let names = format!(
        "{} {}",
        or_text(&candidate["file"]),
        or_text(&candidate["release"])
    );
    let mut score = if PARTIAL.is_match(&names) { -10.0 } else { 0.0 };
    if !release.is_empty() {
        let words = release_words(release);
        let group = or_text(&candidate["group"]).to_lowercase();
        if !group.is_empty() && words.contains(&group) {
            score += 4.0;
        }
        let family = release_format(release);
        let theirs = match release_format(&or_text(&candidate["format"])) {
            "" => release_format(&names),
            f => f,
        };
        if !family.is_empty() && family == theirs {
            score += 2.0;
        }
        score += release_words(&names).intersection(&words).count().min(6) as f64 * 0.25;
    }
    // A subtitle timed at 23.976 fps drifts on a 25 fps (PAL) file, and the reverse.
    let timed = candidate["fps_milli"].as_f64().unwrap_or(0.0);
    if let Some(fps) = fps.filter(|f| *f != 0.0)
        && timed != 0.0
    {
        score += if (fps * 1000.0 - timed).abs() <= 60.0 {
            3.0
        } else {
            -3.0
        };
    }
    score
}

fn candidates(subs: &[Value]) -> Vec<Value> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for s in subs {
        let url = or_text(&s["url"]).trim().to_string();
        let lg = or_text(&s["lang"]).to_lowercase();
        if url.is_empty() || !seen.insert(url.clone()) {
            continue;
        }
        let lang = match lg.as_str() {
            "fre" | "fra" | "fr" => "fr",
            "eng" | "en" => "en",
            _ => continue,
        };
        let file = or_text(&s["subtitleFileName"]);
        let release = or_text(&s["movieReleaseName"]);
        let mut description = ["title", "id", "description"]
            .iter()
            .map(|k| or_text(&s[*k]))
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase();
        description = format!(
            "{description} {} {}",
            file.to_lowercase(),
            release.to_lowercase()
        );
        let variant = if s["hearing_impaired"] == true || SDH.is_match(&description) {
            "sdh"
        } else if s["forced"] == true || FORCED.is_match(&description) {
            "forced"
        } else {
            "standard"
        };
        let fps = or_text(&s["fpsMilli"]);
        let fps_milli: i64 = if !fps.is_empty() && fps.chars().all(|c| c.is_ascii_digit()) {
            fps.parse().unwrap_or(0)
        } else {
            0
        };
        let cut = |v: &str, n: usize| v.chars().take(n).collect::<String>();
        out.push(json!({"lang": lang, "url": url, "variant": variant, "file": cut(&file, 200), "release": cut(&release, 200),
                        "group": cut(&or_text(&s["releaseGroup"]), 60), "format": cut(&or_text(&s["releaseFormat"]), 30), "fps_milli": fps_milli}));
    }
    out
}

/// Four French and three English subtitles, best fit for the release first.
pub async fn opensubtitles(
    meta: &Meta,
    imdb_id: &str,
    kind: &str,
    season: i64,
    episode: i64,
    release: &str,
    fps: Option<f64>,
) -> Vec<Value> {
    let imdb_id = imdb_id.trim();
    if !imdb_id.starts_with("tt") {
        return vec![];
    }
    let target = if kind == "series" {
        format!("series/{imdb_id}:{season}:{episode}")
    } else {
        format!("movie/{imdb_id}")
    };
    let found = meta
        .net
        .cached(&format!("opensubs_v2:{target}"), 1800.0, || async {
            let url = format!("https://opensubtitles-v3.strem.io/subtitles/{target}.json");
            let headers = [
                ("User-Agent", BROWSER),
                ("Accept", "application/json, text/plain, */*"),
            ];
            let subs = match meta
                .net
                .request_json(
                    reqwest::Method::GET,
                    &url,
                    &headers,
                    None,
                    Duration::from_secs(6),
                    3,
                )
                .await
            {
                Ok(data) => data["subtitles"].as_array().cloned().unwrap_or_default(),
                Err(_) => vec![],
            };
            Ok(Value::Array(candidates(&subs)))
        })
        .await
        .ok()
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default();
    let mut result = Vec::new();
    for (lang, label, limit) in [("fr", "Français", 4), ("en", "English", 3)] {
        let mut ranked: Vec<(usize, &Value)> = found
            .iter()
            .enumerate()
            .filter(|(_, i)| i["lang"] == lang)
            .collect();
        ranked.sort_by(|a, b| {
            fit(b.1, release, fps)
                .total_cmp(&fit(a.1, release, fps))
                .then(a.0.cmp(&b.0))
        });
        for (n, (_, item)) in ranked.into_iter().take(limit).enumerate() {
            let url = text(&item["url"]);
            let names = format!("{} {}", or_text(&item["file"]), or_text(&item["release"]));
            result.push(json!({
                "id": format!("{lang}_{}", n + 1), "lang": lang, "label": format!("{label} #{}", n + 1), "variant": item["variant"],
                "partial": PARTIAL.is_match(&names), "url": url, "vtt_url": format!("/api/subtitle.vtt?url={}", py_quote(&url)),
            }));
        }
    }
    let mut listed = LISTED.lock().unwrap_or_else(|e| e.into_inner());
    if listed.len() > 5000 {
        listed.clear();
    }
    listed.extend(result.iter().map(|r| text(&r["url"])));
    result
}

/// Whether KINO may fetch this subtitle file: one it listed, or an https file
/// of the subtitle add-on's own servers. Never another address the page sends.
pub fn may_fetch(url: &str) -> bool {
    if LISTED
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .contains(url)
    {
        return true;
    }
    reqwest::Url::parse(url).is_ok_and(|u| {
        u.scheme() == "https"
            && u.host_str()
                .is_some_and(|h| h == "strem.io" || h.ends_with(".strem.io"))
    })
}

/// The lines of an SRT file: [{start, end, text}] in seconds.
pub fn parse_srt(srt: &str) -> Vec<Value> {
    let text = srt.replace("\r\n", "\n").replace('\r', "\n");
    let text = text.trim_start_matches('\u{feff}');
    let mut cues = Vec::new();
    for block in BLANK_LINE.split(text.trim()) {
        let lines: Vec<&str> = block
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty())
            .collect();
        if lines.len() < 2 {
            continue;
        }
        let Some((index, c)) = lines
            .iter()
            .take(2)
            .enumerate()
            .find_map(|(i, l)| TIME.captures(l).map(|c| (i, c)))
        else {
            continue;
        };
        if index + 1 >= lines.len() {
            continue;
        }
        let n = |i: usize| c[i].parse::<f64>().unwrap_or(0.0);
        let start = n(1) * 3600.0 + n(2) * 60.0 + n(3) + n(4) / 1000.0;
        let end = n(5) * 3600.0 + n(6) * 60.0 + n(7) + n(8) / 1000.0;
        let joined = lines[index + 1..].join("\n");
        let cleaned = TAGS
            .replace_all(&BRACES.replace_all(&joined, ""), "")
            .trim()
            .to_string();
        if !cleaned.is_empty() {
            cues.push(
                json!({"start": py_round(start, 3), "end": py_round(end, 3), "text": cleaned}),
            );
        }
    }
    cues
}

/// UTF-8, else Latin-1 as most older SRT files are.
pub fn decode(raw: &[u8]) -> String {
    match std::str::from_utf8(raw) {
        Ok(s) => s.to_string(),
        Err(_) => raw.iter().map(|b| *b as char).collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lines_fit_and_fetch_rules() {
        let srt = "\u{feff}1\r\n00:00:01,500 --> 00:00:03,000\r\n<i>Hello</i> {\\an8}there\r\n\r\n2\n00:01:02.250 --> 00:01:04,000\n\n\n3\n00:02:00,000 --> 00:02:01,000\nLine one\nLine two\n";
        assert_eq!(
            parse_srt(srt),
            vec![
                json!({"start": 1.5, "end": 3.0, "text": "Hello there"}),
                json!({"start": 120.0, "end": 121.0, "text": "Line one\nLine two"})
            ]
        );
        assert_eq!(decode(&[0x63, 0x61, 0x66, 0xe9]), "café");
        let c = json!({"file": "Film.2014.1080p.WEB-DL.GRP.srt", "release": "", "group": "GRP", "format": "", "fps_milli": 23976});
        assert_eq!(
            fit(&c, "Film.2014.1080p.WEB-DL-GRP", Some(23.976)),
            10.25,
            "as the Python version"
        );
        assert_eq!(fit(&json!({"file": "Film.CD1.srt"}), "", None), -10.0);
        assert!(may_fetch("https://subs5.strem.io/en/download/x"));
        assert!(
            !may_fetch("http://subs5.strem.io/en/download/x"),
            "https only"
        );
        assert!(!may_fetch("https://192.168.1.1/admin"));
        assert!(!may_fetch("https://evilstrem.io/x"));
    }
}
