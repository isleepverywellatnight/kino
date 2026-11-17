//! « Passer le générique »: opening, recap and ending times of an episode.
//! AniSkip (community times, anime only), else the chapters of the file
//! itself (ffprobe), else a default skip length.

use std::sync::LazyLock;
use std::time::Duration;

use regex::Regex;
use serde_json::{Value, json};

use crate::meta::{KNOWN_ANIME, Meta};
use crate::sources::stream::{py_quote, py_round};

static BRACKETS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\(\[\{].*?[\)\]\}]").unwrap());
static SEASON_TAIL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(?:s\d+|season\s*\d+|part\s*\d+)\b.*").unwrap());
static SPACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s+").unwrap());
static OPENING: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(intro|opening|theme|op|title sequence|générique)\b").unwrap()
});
static NOT_OPENING: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(ending|fin|credits)\b").unwrap());
static RECAP: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(recap|prologue|cold open|prélude|previously)\b").unwrap());
static ENDING: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(ending|ed|credits|générique fin|end credits)\b").unwrap());
const AGENT: &str = "KINO-Desktop/2.0 (IntroEngine)";

pub fn clean_series_name(title: &str) -> String {
    let s = BRACKETS.replace_all(title, "");
    let s = SEASON_TAIL.replace_all(&s, "");
    SPACES
        .replace_all(s.replace(['.', '-'], " ").trim(), " ")
        .to_string()
}

fn span(start: f64, end: f64) -> Value {
    json!({"start": py_round(start, 2), "end": py_round(end, 2)})
}

fn seconds(v: &Value) -> f64 {
    v.as_f64()
        .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0.0)
}

/// Opening, recap and ending from a file's chapters (named, else an unnamed
/// chapter of a standard opening's length near the start).
pub fn parse_chapters(chapters: &[Value]) -> (Value, Value, Value) {
    let (mut op, mut ed, mut recap) = (Value::Null, Value::Null, Value::Null);
    for ch in chapters {
        let title = ch["tags"]["title"]
            .as_str()
            .unwrap_or("")
            .trim()
            .to_lowercase();
        let (start, end) = (seconds(&ch["start_time"]), seconds(&ch["end_time"]));
        let duration = end - start;
        if OPENING.is_match(&title) && !NOT_OPENING.is_match(&title) {
            if (15.0..=210.0).contains(&duration) && start < 600.0 {
                op = span(start, end);
            }
        } else if RECAP.is_match(&title) {
            if start < 300.0 {
                recap = span(start, end);
            }
        } else if ENDING.is_match(&title) && start > 300.0 {
            ed = span(start, end);
        }
    }
    if op.is_null() && chapters.len() >= 3 {
        for (index, ch) in chapters.iter().take(4).enumerate() {
            let (start, end) = (seconds(&ch["start_time"]), seconds(&ch["end_time"]));
            if (78.0..=95.0).contains(&(end - start)) && start < 360.0 {
                op = span(start, end);
                if index > 0 {
                    let before = &chapters[index - 1];
                    recap = span(seconds(&before["start_time"]), seconds(&before["end_time"]));
                }
                break;
            }
        }
    }
    (op, ed, recap)
}

/// FFprobe on this computer: the PATH, then the usual places.
pub fn find_tool(name: &str) -> Option<std::path::PathBuf> {
    let exe = if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    };
    if let Some(path) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&path) {
            let candidate = dir.join(&exe);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    let mut places: Vec<std::path::PathBuf> = Vec::new();
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        places.push(
            std::path::Path::new(&local)
                .join("Microsoft")
                .join("WinGet")
                .join("Links")
                .join(&exe),
        );
    }
    for dir in [
        r"C:\Program Files\ffmpeg\bin",
        r"C:\ffmpeg\bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
    ] {
        places.push(std::path::Path::new(dir).join(&exe));
    }
    places.into_iter().find(|p| p.is_file())
}

/// The chapters of a remote file, read by ffprobe through ranged requests.
async fn chapters_of(url: &str) -> Vec<Value> {
    let Some(ffprobe) = find_tool("ffprobe") else {
        return vec![];
    };
    let run = tokio::process::Command::new(ffprobe)
        .args([
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_chapters",
            url,
        ])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .output();
    match tokio::time::timeout(Duration::from_millis(3800), run).await {
        Ok(Ok(out)) if out.status.success() => serde_json::from_slice::<Value>(&out.stdout)
            .ok()
            .and_then(|d| d["chapters"].as_array().cloned())
            .unwrap_or_default(),
        _ => vec![],
    }
}

impl Meta {
    /// MyAnimeList's number of a series through Kitsu's mappings, kept 30 days.
    async fn mal_id(&self, clean: &str, season: i64) -> Option<i64> {
        let key = format!("mal_id:{}:s{season}", clean.to_lowercase());
        if let Some(cached) = self.net.db().cache_get(&key).filter(|v| !v.is_null()) {
            return cached.as_i64();
        }
        let mut queries = Vec::new();
        if season > 1 {
            queries.push(format!("{clean} Season {season}"));
            queries.push(format!("{clean} {season}"));
        }
        queries.push(clean.to_string());
        let headers = [("User-Agent", AGENT)];
        for q in queries {
            let url = format!(
                "https://kitsu.io/api/edge/anime?filter[text]={}&page[limit]=3",
                py_quote(&q)
            );
            let Ok(found) = self
                .net
                .request_json(
                    reqwest::Method::GET,
                    &url,
                    &headers,
                    None,
                    Duration::from_secs(4),
                    0,
                )
                .await
            else {
                continue;
            };
            for item in found["data"].as_array().into_iter().flatten() {
                let Some(kitsu) = item["id"].as_str().filter(|i| !i.is_empty()) else {
                    continue;
                };
                let url = format!("https://kitsu.io/api/edge/anime/{kitsu}/mappings");
                let Ok(maps) = self
                    .net
                    .request_json(
                        reqwest::Method::GET,
                        &url,
                        &headers,
                        None,
                        Duration::from_millis(3500),
                        0,
                    )
                    .await
                else {
                    continue;
                };
                let mal = maps["data"].as_array().into_iter().flatten().find_map(|m| {
                    let attr = &m["attributes"];
                    let id = attr["externalId"].as_str().unwrap_or("");
                    (attr["externalSite"] == "myanimelist/anime"
                        && !id.is_empty()
                        && id.chars().all(|c| c.is_ascii_digit()))
                    .then(|| id.parse::<i64>().ok())
                    .flatten()
                });
                if let Some(mal) = mal {
                    let _ = self.net.db().cache_set(&key, &json!(mal), 30.0 * 86400.0);
                    return Some(mal);
                }
            }
        }
        None
    }

    async fn aniskip(&self, mal: i64, episode: i64) -> Option<Value> {
        let url = format!(
            "https://api.aniskip.com/v2/skip-times/{mal}/{episode}?types[]=op&types[]=ed&types[]=recap&types[]=mixed-op&types[]=mixed-ed&episodeLength=0"
        );
        let data = self
            .net
            .request_json(
                reqwest::Method::GET,
                &url,
                &[("User-Agent", "Mozilla/5.0 KINO-Desktop/2.0")],
                None,
                Duration::from_millis(4500),
                0,
            )
            .await
            .ok()?;
        if data["found"] != true || data["results"].as_array().is_none_or(Vec::is_empty) {
            return None;
        }
        let (mut op, mut ed, mut recap) = (Value::Null, Value::Null, Value::Null);
        for item in data["results"].as_array().into_iter().flatten() {
            let interval = span(
                seconds(&item["interval"]["startTime"]),
                seconds(&item["interval"]["endTime"]),
            );
            match item["skipType"]
                .as_str()
                .unwrap_or("")
                .to_lowercase()
                .as_str()
            {
                "op" | "mixed-op" => op = interval,
                "ed" | "mixed-ed" => ed = interval,
                "recap" => recap = interval,
                _ => {}
            }
        }
        (!op.is_null() || !ed.is_null() || !recap.is_null()).then(|| {
            json!({"found": true, "source": "aniskip", "provider": "AniSkip (Communautaire)", "op": op, "ed": ed, "recap": recap, "default_skip": 90.0})
        })
    }
}

/// Whether KINO knows this title as an anime: AniSkip only holds anime, and
/// a title search there for another series finds an unrelated anime's times.
fn known_anime(meta: &Meta, kind: &str, title: &str, imdb_id: &str) -> bool {
    if kind == "anime" || title.to_lowercase().contains("anime") {
        return true;
    }
    if imdb_id.is_empty() {
        return false;
    }
    if KNOWN_ANIME.read().is_ok_and(|k| k.contains(imdb_id)) {
        return true;
    }
    ["series", "movie"].iter().any(|kind| {
        meta.net
            .db()
            .cache_get(&format!("anime_engine:pool:{kind}:v3"))
            .and_then(|p| p.as_array().cloned())
            .is_some_and(|pool| pool.iter().any(|m| m["id"] == imdb_id))
    })
}

pub async fn intro_times(
    meta: &Meta,
    title: &str,
    kind: &str,
    season: i64,
    episode: i64,
    url: Option<&str>,
    imdb_id: &str,
) -> Value {
    let clean = clean_series_name(title);
    let anime = known_anime(meta, kind, title, imdb_id);
    let key = format!("intro_v2:{}:s{season}:e{episode}", clean.to_lowercase());
    if let Some(cached) = meta.net.db().cache_get(&key).filter(|c| c["found"] == true) {
        return cached;
    }
    if anime
        && !clean.is_empty()
        && let Some(mal) = meta.mal_id(&clean, season).await
        && let Some(found) = meta.aniskip(mal, episode).await
    {
        let _ = meta.net.db().cache_set(&key, &found, 14.0 * 86400.0);
        return found;
    }
    let default_skip = if anime { 90.0 } else { 60.0 };
    // Only a web address goes to ffprobe, never a path on this computer.
    if let Some(url) = url.filter(|u| u.starts_with("http://") || u.starts_with("https://")) {
        let chapters = chapters_of(url).await;
        if !chapters.is_empty() {
            let (op, ed, recap) = parse_chapters(&chapters);
            if !op.is_null() || !ed.is_null() {
                let found = json!({"found": true, "source": "chapters", "provider": "Chapitres intégrés (MKV/MP4)", "op": op, "ed": ed,
                                   "recap": recap, "default_skip": default_skip});
                let _ = meta.net.db().cache_set(&key, &found, 7.0 * 86400.0);
                return found;
            }
        }
    }
    json!({"found": false, "source": "heuristic", "provider": "Calculateur adaptatif", "op": null, "ed": null, "recap": null,
           "default_skip": default_skip, "estimated_intro_start": if anime { 60.0 } else { 45.0 }})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_and_chapters() {
        assert_eq!(
            clean_series_name("Attack on Titan (2013) Season 2"),
            "Attack on Titan"
        );
        assert_eq!(
            clean_series_name("Re.Zero - Starting Life"),
            "Re Zero Starting Life"
        );
        let ch = |s: f64, e: f64, t: &str| json!({"start_time": format!("{s:.6}"), "end_time": format!("{e:.6}"), "tags": {"title": t}});
        let (op, ed, recap) = parse_chapters(&[
            ch(0.0, 40.0, "Prologue"),
            ch(40.0, 130.5, "Opening"),
            ch(130.5, 1300.0, "Part A"),
            ch(1300.0, 1390.0, "Ending"),
        ]);
        assert_eq!(
            (op, ed, recap),
            (
                json!({"start": 40.0, "end": 130.5}),
                json!({"start": 1300.0, "end": 1390.0}),
                json!({"start": 0.0, "end": 40.0})
            )
        );
        let (op, _, recap) = parse_chapters(&[
            ch(0.0, 120.0, "Chapter 1"),
            ch(120.0, 209.0, "Chapter 2"),
            ch(209.0, 1400.0, "Chapter 3"),
        ]);
        assert_eq!(
            (op, recap),
            (
                json!({"start": 120.0, "end": 209.0}),
                json!({"start": 0.0, "end": 120.0})
            ),
            "an unnamed opening's length"
        );
    }
}
