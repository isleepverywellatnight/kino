//! A stream's tracks (ffprobe, or FFmpeg's own listing) and the FFmpeg command
//! that turns a file the page's player cannot read into a fragmented MP4.

use std::time::Duration;

use regex::Regex;
use serde_json::{Value, json};

use crate::play::{text, truthy};
use crate::sources::stream::py_round;

/// « 24000/1001 » → 23.976; nothing usable → null.
fn frame_rate(value: &Value) -> Value {
    let rate = match value {
        Value::String(s) if s.contains('/') => {
            let (n, d) = s.split_once('/').unwrap_or_default();
            match (n.trim().parse::<f64>(), d.trim().parse::<f64>()) {
                (Ok(n), Ok(d)) if d != 0.0 => Some(n / d),
                _ => None,
            }
        }
        Value::String(s) => s.trim().parse().ok(),
        other => other.as_f64(),
    };
    match rate {
        Some(r) if r > 1.0 && r < 200.0 => json!(py_round(r, 3)),
        _ => Value::Null,
    }
}

fn track(stream: &Value) -> Value {
    let tags = &stream["tags"];
    let language = text(&tags["language"]);
    json!({
        "index": stream["index"].as_i64().unwrap_or(0),
        "codec": text(&stream["codec_name"]).to_lowercase(),
        "language": if language.is_empty() { "und".to_string() } else { language.to_lowercase() },
        "title": text(&tags["title"]).chars().take(120).collect::<String>(),
        "default": truthy(&stream["disposition"]["default"]),
        "original": truthy(&stream["disposition"]["original"]),
        "channels": stream["channels"], "width": stream["width"], "height": stream["height"],
        "pixel_format": text(&stream["pix_fmt"]),
        "fps": frame_rate(if truthy(&stream["avg_frame_rate"]) { &stream["avg_frame_rate"] } else { &stream["r_frame_rate"] }),
    })
}

pub fn parse_probe_json(output: &str) -> Option<Value> {
    let data: Value = serde_json::from_str(output).ok()?;
    let streams = data["streams"].as_array().cloned().unwrap_or_default();
    let of = |kind: &str| -> Vec<Value> {
        streams
            .iter()
            .filter(|s| s["codec_type"] == kind && s.get("index").is_some())
            .map(track)
            .collect()
    };
    Some(
        json!({"container": text(&data["format"]["format_name"]), "duration": data["format"]["duration"], "video": of("video"), "audio": of("audio")}),
    )
}

/// When only FFmpeg is there: its « Input #0 » listing.
pub fn parse_ffmpeg_output(output: &str) -> Value {
    static CONTAINER: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
        Regex::new(r"Input #\d+,\s*([^,]+(?:,[^,]+)*),\s*from ").unwrap()
    });
    static DURATION: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
        Regex::new(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)").unwrap()
    });
    static STREAM: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
        Regex::new(r"(?i)Stream #\d+:(\d+)(?:\[[^\]]+\])?(?:\(([^)]+)\))?:\s*(Video|Audio):\s*(\w+)([^\n]*)").unwrap()
    });
    static SIZE: std::sync::LazyLock<Regex> =
        std::sync::LazyLock::new(|| Regex::new(r"\b(\d{2,5})x(\d{2,5})\b").unwrap());
    static PIX: std::sync::LazyLock<Regex> =
        std::sync::LazyLock::new(|| Regex::new(r"\b(yuv\w+|p010\w*|nv12)\b").unwrap());
    static FPS: std::sync::LazyLock<Regex> =
        std::sync::LazyLock::new(|| Regex::new(r"\b(\d+(?:\.\d+)?) fps\b").unwrap());
    let mut result = json!({"container": "", "duration": null, "video": [], "audio": []});
    if let Some(c) = CONTAINER.captures(output) {
        result["container"] = json!(c[1].trim());
    }
    if let Some(d) = DURATION.captures(output) {
        let seconds =
            d[1].parse::<i64>().unwrap_or(0) * 3600 + d[2].parse::<i64>().unwrap_or(0) * 60;
        let total = seconds as f64 + d[3].parse::<f64>().unwrap_or(0.0);
        // As Python's str(float).
        result["duration"] = json!(if total.fract() == 0.0 {
            format!("{total:.1}")
        } else {
            format!("{total}")
        });
    }
    for m in STREAM.captures_iter(output) {
        let tail = m.get(5).map_or("", |t| t.as_str());
        let mut stream = json!({
            "index": m[1].parse::<i64>().unwrap_or(0), "codec": m[4].to_lowercase(),
            "language": m.get(2).map_or("und".to_string(), |l| l.as_str().to_lowercase()), "title": "",
            "default": tail.contains("(default)"), "original": tail.contains("(original)"),
            "channels": null, "width": null, "height": null, "pixel_format": "", "fps": null,
        });
        if m[3].eq_ignore_ascii_case("video") {
            if let Some(size) = SIZE.captures(tail) {
                stream["width"] = json!(size[1].parse::<i64>().unwrap_or(0));
                stream["height"] = json!(size[2].parse::<i64>().unwrap_or(0));
            }
            stream["pixel_format"] = json!(
                PIX.captures(tail)
                    .map_or(String::new(), |p| p[1].to_string())
            );
            stream["fps"] = FPS
                .captures(tail)
                .map_or(Value::Null, |f| frame_rate(&json!(f[1].to_string())));
            if let Some(v) = result["video"].as_array_mut() {
                v.push(stream);
            }
        } else {
            if let Some(v) = result["audio"].as_array_mut() {
                v.push(stream);
            }
        }
    }
    result
}

/// A local file or a stream's tracks, with a bounded run and no shell.
pub async fn inspect(source: &str, timeout: Duration) -> Option<Value> {
    if let Some(ffprobe) = crate::intro::find_tool("ffprobe") {
        let entries = "format=format_name,duration:stream=index,codec_type,codec_name,channels,width,height,pix_fmt,avg_frame_rate:stream_tags=language,title:stream_disposition=default,original";
        let run = tokio::process::Command::new(ffprobe)
            .args([
                "-v",
                "error",
                "-show_entries",
                entries,
                "-of",
                "json",
                source,
            ])
            .kill_on_drop(true)
            .output();
        if let Ok(Ok(out)) = tokio::time::timeout(timeout, run).await
            && out.status.success()
            && let Some(found) = parse_probe_json(&String::from_utf8_lossy(&out.stdout))
        {
            return Some(found);
        }
    }
    let ffmpeg = crate::intro::find_tool("ffmpeg")?;
    let run = tokio::process::Command::new(ffmpeg)
        .args(["-hide_banner", "-i", source])
        .kill_on_drop(true)
        .output();
    let out = tokio::time::timeout(timeout, run).await.ok()?.ok()?;
    let parsed = parse_ffmpeg_output(&String::from_utf8_lossy(&out.stderr));
    let empty = |k: &str| parsed[k].as_array().is_none_or(Vec::is_empty);
    (!empty("video") || !empty("audio")).then_some(parsed)
}

/// A declared language, then a default track, then the first track.
pub fn select_audio(tracks: &[Value], preference: &str) -> Value {
    if tracks.is_empty() {
        return Value::Null;
    }
    let original = ["vost", "vostfr", "vo"].contains(&preference);
    if original && let Some(t) = tracks.iter().find(|t| truthy(&t["original"])) {
        return t.clone();
    }
    let wanted: &[&str] = if original {
        &["jpn", "ja", "japanese", "eng", "en", "english"]
    } else {
        &["fra", "fre", "fr", "french"]
    };
    for language in wanted {
        if let Some(t) = tracks.iter().find(|t| t["language"] == *language) {
            return t.clone();
        }
    }
    tracks
        .iter()
        .find(|t| truthy(&t["default"]))
        .unwrap_or(&tracks[0])
        .clone()
}

pub struct Remux<'a> {
    pub source: &'a str,
    pub start: f64,
    pub transcode_video: bool,
    pub audio_index: Option<u32>,
    pub transcode_audio: bool,
    /// The copied video is HEVC: tagged « hvc1 », which AVFoundation plays (not « hev1 »).
    pub hevc_tag: bool,
}

/// FFmpeg's arguments for a browser-compatible MP4, converting only what must be.
pub fn remux_args(r: &Remux) -> Vec<String> {
    let mut args: Vec<String> = ["-hide_banner", "-loglevel", "error"]
        .map(String::from)
        .to_vec();
    if r.transcode_video && cfg!(target_os = "macos") {
        args.extend(["-hwaccel".into(), "videotoolbox".into()]);
    }
    if r.start > 0.0 {
        args.extend(["-ss".into(), format!("{:.2}", r.start)]);
    }
    args.extend(["-i".into(), r.source.into()]);
    let audio = r
        .audio_index
        .map_or("0:a:0?".to_string(), |i| format!("0:{i}?"));
    args.extend([
        "-map".into(),
        "0:v:0?".into(),
        "-map".into(),
        audio,
        "-sn".into(),
    ]);
    // A copied video plays from the previous keyframe; a required conversion
    // goes to 1080p at most on four threads (a 4K re-encode can freeze a Mac).
    if r.transcode_video {
        args.extend(
            [
                "-vf",
                "scale=-2:'min(1080,ih)'",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-threads",
                "4",
            ]
            .map(String::from),
        );
    } else {
        args.extend(["-c:v".into(), "copy".into()]);
        if r.hevc_tag {
            args.extend(["-tag:v".into(), "hvc1".into()]);
        }
    }
    if r.transcode_audio {
        args.extend(["-c:a", "aac", "-b:a", "192k", "-ac", "2"].map(String::from));
    } else {
        args.extend(["-c:a".into(), "copy".into()]);
    }
    args.extend(
        [
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration",
            "1000000",
            "-f",
            "mp4",
            "pipe:1",
        ]
        .map(String::from),
    );
    args
}

/// Only a web stream (or an offline file, checked apart) may reach FFmpeg,
/// which also opens local paths and its own protocols.
pub fn is_web_stream(source: &str) -> bool {
    source.len() <= 8192
        && reqwest::Url::parse(source).is_ok_and(|u| {
            ["http", "https"].contains(&u.scheme()) && u.host_str().is_some_and(|h| !h.is_empty())
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn listings_and_choice() {
        let probe = r#"{"format": {"format_name": "matroska,webm", "duration": "5400.5"}, "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "HEVC", "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le", "avg_frame_rate": "24000/1001"},
            {"index": 1, "codec_type": "audio", "codec_name": "eac3", "channels": 6, "tags": {"language": "ENG"}, "disposition": {"default": 1}},
            {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "fre", "title": "VFF"}}]}"#;
        let info = parse_probe_json(probe).unwrap();
        assert_eq!(info["video"][0]["fps"], json!(23.976));
        assert_eq!(info["video"][0]["codec"], "hevc");
        let audio = info["audio"].as_array().unwrap();
        assert_eq!(select_audio(audio, "vf")["index"], 2);
        assert_eq!(select_audio(audio, "vostfr")["index"], 1);
        let listing = "Input #0, matroska,webm, from 'x':\n  Duration: 01:30:00.50, start: 0\n  Stream #0:0(eng): Video: h264 (High), yuv420p(tv), 1920x1080, 23.98 fps (default)\n  Stream #0:1(fre): Audio: ac3, 48000 Hz, 5.1 (default)\n";
        let parsed = parse_ffmpeg_output(listing);
        assert_eq!(
            (parsed["container"].clone(), parsed["duration"].clone()),
            (json!("matroska,webm"), json!("5400.5"))
        );
        assert_eq!(
            (
                parsed["video"][0]["width"].clone(),
                parsed["video"][0]["fps"].clone()
            ),
            (json!(1920), json!(23.98))
        );
        assert_eq!(parsed["audio"][0]["language"], "fre");
        let args = remux_args(&Remux {
            source: "https://x/a.mkv",
            start: 12.0,
            transcode_video: false,
            audio_index: Some(2),
            transcode_audio: true,
            hevc_tag: true,
        });
        assert!(
            args.windows(2).any(|w| w == ["-ss", "12.00"])
                && args.windows(2).any(|w| w == ["-tag:v", "hvc1"])
        );
        assert!(args.windows(2).any(|w| w == ["-map", "0:2?"]));
        assert!(
            is_web_stream("https://cdn.example/a.mkv")
                && !is_web_stream("file:///etc/passwd")
                && !is_web_stream("concat:x")
        );
    }
}
