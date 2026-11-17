//! Trailers: a YouTube search in the language asked (VF or VO), the result
//! that names the film best, and a direct stream when yt-dlp is installed.

use std::collections::HashSet;
use std::sync::LazyLock;
use std::time::Duration;

use regex::Regex;
use serde_json::{Value, json};
use unicode_normalization::UnicodeNormalization;
use unicode_normalization::char::is_combining_mark;

use crate::meta::Meta;
use crate::net::BROWSER;
use crate::sources::stream::py_quote;

static INITIAL_DATA: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)var ytInitialData = (\{.*?\});</script>").unwrap());
static VIDEO_ID: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#""videoId":"([a-zA-Z0-9_-]{11})""#).unwrap());
static WORDS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[a-z0-9]+").unwrap());
static VF: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bvf\b|version fran[cç]aise|\bfrench\b").unwrap());
static VO: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bvost(?:fr)?\b|\bvo\b|official trailer").unwrap());
static TRAILER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"bande[ -]?annonce|trailer|teaser").unwrap());
static YEARS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(?:19|20)\d{2}\b").unwrap());

fn fold(text: &str) -> String {
    text.nfkd().filter(|c| !is_combining_mark(*c)).collect()
}

fn words(text: &str) -> HashSet<String> {
    let stop = ["the", "le", "la", "les", "de", "a"];
    WORDS
        .find_iter(&fold(text).to_lowercase())
        .map(|m| m.as_str().to_string())
        .filter(|w| !stop.contains(&w.as_str()))
        .collect()
}

/// (id, title, seconds) of the first video results of a YouTube search page.
fn video_results(html: &str, limit: usize) -> Vec<(String, String, i64)> {
    let Some(data) = INITIAL_DATA
        .captures(html)
        .and_then(|c| serde_json::from_str::<Value>(&c[1]).ok())
    else {
        return vec![];
    };
    let mut found = Vec::new();
    fn walk(node: &Value, found: &mut Vec<(String, String, i64)>, limit: usize) {
        if found.len() >= limit {
            return;
        }
        match node {
            Value::Object(map) => {
                let renderer = &node["videoRenderer"];
                if let Some(id) = renderer["videoId"].as_str().filter(|i| !i.is_empty()) {
                    let title: String = renderer["title"]["runs"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(|r| r["text"].as_str())
                        .collect();
                    let length = renderer["lengthText"]["simpleText"].as_str().unwrap_or("");
                    let seconds = length.split(':').fold(0, |acc, part| {
                        if !part.is_empty() && part.chars().all(|c| c.is_ascii_digit()) {
                            acc * 60 + part.parse::<i64>().unwrap_or(0)
                        } else {
                            acc
                        }
                    });
                    found.push((id.to_string(), title, seconds));
                }
                for value in map.values() {
                    walk(value, found, limit);
                }
            }
            Value::Array(list) => {
                for value in list {
                    walk(value, found, limit);
                }
            }
            _ => {}
        }
    }
    walk(&data, &mut found, limit);
    found
}

/// The trailer in the language asked whose title names the film.
fn pick(results: &[(String, String, i64)], title: &str, year: &str, lang: &str) -> Option<String> {
    let wanted = words(title);
    let language: &Regex = if lang == "vf" { &VF } else { &VO };
    let mut best: Option<(String, f64)> = None;
    for (rank, (id, video_title, seconds)) in results.iter().enumerate() {
        let low = fold(video_title).to_lowercase();
        let mut score = -0.1 * rank as f64;
        if language.is_match(&low) {
            score += 3.0;
        }
        if TRAILER.is_match(&low) {
            score += 2.0;
        }
        score += words(video_title).intersection(&wanted).count().min(4) as f64 * 0.5;
        if !year.is_empty() && video_title.contains(year) {
            score += 1.0;
        } else if let Ok(y) = year.parse::<i64>()
            && YEARS
                .find_iter(video_title)
                .filter_map(|m| m.as_str().parse::<i64>().ok())
                .any(|other| (other - y).abs() > 1)
        {
            score -= 3.0; // another film of the same name (« Les Misérables » 2012 vs 2026)
        }
        if *seconds != 0 && (*seconds < 20 || *seconds > 360) {
            score -= 5.0;
        }
        if best.as_ref().is_none_or(|(_, b)| score > *b) {
            best = Some((id.clone(), score));
        }
    }
    best.map(|(id, _)| id)
}

/// A direct stream of the video through yt-dlp, when it is installed.
async fn direct_stream(id: &str) -> String {
    let Some(ytdlp) = crate::intro::find_tool("yt-dlp") else {
        return String::new();
    };
    let watch = format!("https://www.youtube.com/watch?v={id}");
    let run = tokio::process::Command::new(ytdlp)
        .args([
            "-g",
            "--extractor-args",
            "youtube:player_client=android,web",
            "-f",
            "b",
            "--no-playlist",
            &watch,
        ])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .output();
    match tokio::time::timeout(Duration::from_secs(8), run).await {
        Ok(Ok(out)) => String::from_utf8_lossy(&out.stdout)
            .lines()
            .map(str::trim)
            .find(|l| l.starts_with("http"))
            .unwrap_or("")
            .to_string(),
        _ => String::new(),
    }
}

pub async fn trailer(meta: &Meta, title: &str, year: &str, yt_id: &str, lang: &str) -> Value {
    let title = title.trim().to_string();
    let year: String = year.trim().chars().take(4).collect();
    let yt_id = yt_id.trim().to_string();
    let lang = if lang.trim().is_empty() {
        "vf".to_string()
    } else {
        lang.trim().to_lowercase()
    };
    let key = format!("trailer_v2:{title}:{year}:{yt_id}:{lang}");
    meta.net
        .cached(&key, 3600.0, || async {
            let mut chosen = if !yt_id.is_empty() && lang == "vo" { yt_id.clone() } else { String::new() };
            if chosen.is_empty() && !title.is_empty() {
                let suffix = if lang == "vf" { "bande annonce VF" } else { "official trailer" };
                let query = py_quote(format!("{title} {year} {suffix}").trim());
                let url = format!("https://www.youtube.com/results?search_query={query}");
                let page = meta.net.get_text(&url, &[("Accept", "application/json, text/plain, */*")], BROWSER, Duration::from_secs(5)).await;
                if let Ok(html) = page {
                    // The first id of the page may be an international or unrelated video.
                    chosen = pick(&video_results(&html, 5), &title, &year, &lang)
                        .or_else(|| VIDEO_ID.captures(&html).map(|c| c[1].to_string()))
                        .unwrap_or_default();
                }
            }
            if chosen.is_empty() {
                chosen = yt_id.clone();
            }
            if chosen.is_empty() {
                return Ok(json!({"yt_id": "", "stream_url": "", "embed_url": ""}));
            }
            let stream = direct_stream(&chosen).await;
            Ok(json!({
                "yt_id": chosen, "stream_url": stream,
                "embed_url": format!("https://www.youtube-nocookie.com/embed/{chosen}?autoplay=1&rel=0"),
                "watch_url": format!("https://www.youtube.com/watch?v={chosen}"),
            }))
        })
        .await
        .unwrap_or_else(|_| json!({"yt_id": "", "stream_url": "", "embed_url": ""}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_trailer_that_names_the_film() {
        let results = vec![
            (
                "aaaaaaaaaaa".to_string(),
                "Les Misérables (2012) bande annonce VF".to_string(),
                150,
            ),
            (
                "bbbbbbbbbbb".to_string(),
                "Les Misérables - Bande-annonce VF".to_string(),
                120,
            ),
            (
                "ccccccccccc".to_string(),
                "Les Misérables 2026 full movie".to_string(),
                7200,
            ),
        ];
        assert_eq!(
            pick(&results, "Les Misérables", "2026", "vf").as_deref(),
            Some("bbbbbbbbbbb"),
            "another year sinks"
        );
        assert_eq!(
            pick(&results, "Les Misérables", "2012", "vf").as_deref(),
            Some("aaaaaaaaaaa")
        );
        let html = r#"<script>var ytInitialData = {"a":[{"videoRenderer":{"videoId":"xyz12345678","title":{"runs":[{"text":"T"}]},"lengthText":{"simpleText":"2:05"}}}]};</script>"#;
        assert_eq!(
            video_results(html, 5),
            vec![("xyz12345678".to_string(), "T".to_string(), 125)]
        );
    }
}
