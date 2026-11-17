//! The series the user follows: « Nouveaux épisodes » on the home page,
//! « Mes séries » and the « Calendrier ». Same rules everywhere: episodes
//! before the one reached count as seen, as do those marked or played to the
//! end. Only numbers and dates leave here for the last two, never a title.

use std::collections::HashSet;
use std::sync::LazyLock;
use std::time::Duration;

use chrono::{Datelike, NaiveDate};
use futures_util::StreamExt;
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::meta::Meta;

const SERIES_TYPES: [&str; 3] = ["series", "anime", "tv"];
static IMDB: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^tt\d{1,12}$").unwrap());
static CODE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)^s(\d{1,3})e(\d{1,4})$").unwrap());
static DAY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{4}-\d{2}-\d{2}$").unwrap());

// ---------- Python's own conversions ----------

fn text(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `value or default`, as the page gets it.
fn or(value: &Value, default: Value) -> Value {
    if truthy(value) {
        value.clone()
    } else {
        default
    }
}

/// Python's int(): None for what it refuses.
fn int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(b) => Some(*b as i64),
        Value::Number(n) => n.as_i64().or_else(|| {
            n.as_f64()
                .filter(|f| f.is_finite())
                .map(|f| f.trunc() as i64)
        }),
        Value::String(s) => s.trim().replace('_', "").parse().ok(),
        _ => None,
    }
}

/// Python's float(value or 0), 0 for what it refuses.
fn float(value: &Value) -> f64 {
    match value {
        Value::Bool(b) => *b as i64 as f64,
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(s) if !s.is_empty() => s.trim().parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

fn chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn is_series(entry: &Value) -> bool {
    let id = if truthy(&entry["id"]) {
        text(&entry["id"])
    } else {
        String::new()
    };
    SERIES_TYPES.contains(&entry["type"].as_str().unwrap_or("\0")) && IMDB.is_match(&id)
}

fn day_shift(day: &str, days: i64) -> String {
    NaiveDate::parse_from_str(day, "%Y-%m-%d")
        .map(|d| (d + chrono::Duration::days(days)).to_string())
        .unwrap_or_default()
}

/// The UTC day of a timestamp, '' when there is none.
fn day_of(timestamp: &Value) -> String {
    let seconds = match timestamp {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        Value::Bool(b) => Some(*b as i64 as f64),
        _ => None,
    };
    seconds
        .and_then(|s| chrono::DateTime::from_timestamp(s.floor() as i64, 0))
        .map(|d| d.format("%Y-%m-%d").to_string())
        .unwrap_or_default()
}

pub fn local_today() -> String {
    chrono::Local::now().format("%Y-%m-%d").to_string()
}

// ---------- Episodes ----------

pub fn episode_code(season: i64, episode: i64) -> String {
    format!("S{season:02}E{episode:02}")
}

fn code_of(value: &Value) -> String {
    let raw = if truthy(value) {
        text(value)
    } else {
        String::new()
    };
    CODE.captures(&raw)
        .map(|c| episode_code(c[1].parse().unwrap_or(0), c[2].parse().unwrap_or(0)))
        .unwrap_or_default()
}

/// Episodes marked or played to the end, as « S02E08 » codes.
pub fn watched_codes(entry: &Value) -> HashSet<String> {
    let mut codes: HashSet<String> = entry["watched_episodes"]
        .as_array()
        .into_iter()
        .flatten()
        .map(code_of)
        .collect();
    for (code, position) in entry["ep_positions"].as_object().into_iter().flatten() {
        if position.is_object()
            && (truthy(&position["completed"]) || float(&position["pct"]) >= 85.0)
        {
            codes.insert(code_of(&json!(code)));
        }
    }
    codes.remove("");
    codes
}

#[derive(Clone, PartialEq)]
pub struct Aired {
    pub season: i64,
    pub episode: i64,
    pub released: String,
    pub name: String,
    pub thumbnail: String,
}

impl Aired {
    fn at(&self) -> (i64, i64) {
        (self.season, self.episode)
    }
}

/// Regular episodes with a release day, in order (specials and undated ones left out).
pub fn aired_episodes(videos: &[Value]) -> Vec<Aired> {
    let mut episodes = Vec::new();
    for video in videos {
        if !video.is_object() {
            continue;
        }
        let number = if truthy(&video["episode"]) {
            &video["episode"]
        } else {
            &video["number"]
        };
        let (Some(season), Some(episode)) = (
            if truthy(&video["season"]) {
                int(&video["season"])
            } else {
                Some(0)
            },
            if truthy(number) { int(number) } else { Some(0) },
        ) else {
            continue;
        };
        let released = chars(
            &if truthy(&video["released"]) {
                text(&video["released"])
            } else {
                String::new()
            },
            10,
        );
        if season > 0 && episode > 0 && DAY.is_match(&released) {
            let name = [&video["name"], &video["title"]]
                .into_iter()
                .find(|v| truthy(v))
                .map(text)
                .unwrap_or_default();
            let thumbnail = if truthy(&video["thumbnail"]) {
                text(&video["thumbnail"])
            } else {
                String::new()
            };
            episodes.push(Aired {
                season,
                episode,
                released,
                name: chars(&name, 200),
                thumbnail: if thumbnail.starts_with("https://") {
                    thumbnail
                } else {
                    String::new()
                },
            });
        }
    }
    episodes.sort_by_key(Aired::at);
    episodes
}

impl Meta {
    /// A series' episodes (number, name, release day, still), cached 6 h;
    /// None when Cinemeta does not answer.
    pub async fn series_videos(&self, imdb_id: &str) -> Option<Vec<Value>> {
        if !IMDB.is_match(imdb_id) {
            return None;
        }
        let url = format!("{}/meta/series/{imdb_id}.json", self.cinemeta);
        let value = self
            .net
            .cached(&format!("series_videos_v1:{imdb_id}"), 6.0 * 3600.0, || async {
                let Ok(data) = self.net.request_json(reqwest::Method::GET, &url, &[], None, Duration::from_secs(6), 1).await
                else {
                    return Ok(Value::Null);
                };
                let Some(videos) = data["meta"]["videos"].as_array() else { return Ok(Value::Null) };
                let list = videos.iter().filter(|v| v.is_object()).map(|v| {
                    let name = [&v["name"], &v["title"]].into_iter().find(|n| truthy(n)).map(text).unwrap_or_default();
                    let released = if truthy(&v["released"]) { text(&v["released"]) } else { String::new() };
                    json!({"season": v["season"], "episode": or(&v["episode"], v["number"].clone()),
                           "name": html_escape::decode_html_entities(&name), "released": chars(&released, 10),
                           "thumbnail": or(&v["thumbnail"], json!(""))})
                });
                Ok(Value::Array(list.collect()))
            })
            .await
            .ok()?;
        value.as_array().cloned()
    }
}

/// Each series' episode list, six at a time; None for one that fails.
async fn episode_lists(meta: &Meta, entries: &[&Value]) -> Vec<Option<Vec<Value>>> {
    let ids: Vec<String> = entries
        .iter()
        .map(|e| e["id"].as_str().unwrap_or("").to_string())
        .collect();
    let mut found = Vec::with_capacity(entries.len());
    // Owned futures: borrowed ones trip the Send check of the route handlers.
    let mut lists = futures_util::stream::iter(ids.into_iter().map(|id| {
        let meta = meta.clone();
        async move { meta.series_videos(&id).await }
    }))
    .buffered(6);
    while let Some(list) = lists.next().await {
        found.push(list);
    }
    found
}

/// The series the user follows: those of the history (not imported as seen),
/// then those of Ma Liste not started yet.
fn followed_series<'a>(
    history: &'a [Value],
    watchlist: &'a [Value],
    max: usize,
) -> (Vec<&'a Value>, Vec<&'a Value>) {
    let from_history: Vec<&Value> = history
        .iter()
        .filter(|e| is_series(e) && !truthy(&e["imported_watched"]))
        .take(max)
        .collect();
    let known: HashSet<String> = history
        .iter()
        .map(|e| {
            if truthy(&e["id"]) {
                text(&e["id"])
            } else {
                String::new()
            }
        })
        .collect();
    let listed: Vec<&Value> = watchlist
        .iter()
        .filter(|e| is_series(e) && !known.contains(&text(&e["id"])))
        .take(max)
        .collect();
    (from_history, listed)
}

// ---------- « Nouveaux épisodes » ----------

/// Series with an episode out since the last viewing, and series of Ma Liste
/// not started whose new season (or first one) came out within `days`.
pub async fn new_episodes(
    meta: &Meta,
    history: &[Value],
    watchlist: &[Value],
    today: &str,
) -> Vec<Value> {
    let cutoff = day_shift(today, -60);
    let (followed, listed) = followed_series(history, watchlist, 24);
    if followed.is_empty() && listed.is_empty() {
        return vec![];
    }
    let all: Vec<&Value> = followed.iter().chain(&listed).copied().collect();
    let lists = episode_lists(meta, &all).await;
    let mut items: Vec<Value> = listed
        .iter()
        .zip(&lists[followed.len()..])
        .filter_map(|(entry, videos)| {
            listed_item(entry, videos.as_deref().unwrap_or(&[]), today, &cutoff)
        })
        .collect();
    for (entry, videos) in followed.iter().zip(&lists[..followed.len()]) {
        let episodes: Vec<Aired> = aired_episodes(videos.as_deref().unwrap_or(&[]))
            .into_iter()
            .filter(|i| i.released.as_str() <= today)
            .collect();
        let seen_day = day_of(&entry["last_watched"]);
        if episodes.is_empty() || seen_day.is_empty() {
            continue;
        }
        let watched = watched_codes(entry);
        let number = |v: &Value| if truthy(v) { int(v) } else { Some(0) };
        let (Some(s), Some(e)) = (number(&entry["season"]), number(&entry["episode"])) else {
            continue;
        };
        let last = (s, e);
        let last_done = if s != 0 && e != 0 {
            truthy(&entry["completed"])
                || float(&entry["progress_pct"]) >= 85.0
                || watched.contains(&episode_code(s, e))
        } else {
            true
        };
        let ahead: Vec<&Aired> = episodes
            .iter()
            .filter(|i| {
                (i.at() > last || (i.at() == last && !last_done))
                    && !watched.contains(&episode_code(i.season, i.episode))
            })
            .collect();
        let new: Vec<&&Aired> = ahead
            .iter()
            .filter(|i| i.released > seen_day && i.released >= cutoff)
            .collect();
        if new.is_empty() {
            continue;
        }
        let upcoming = ahead[0];
        let newest = new
            .iter()
            .map(|i| i.released.clone())
            .max()
            .unwrap_or_default();
        items.push(json!({
            "id": entry["id"], "name": or(&entry["name"], json!("")), "type": or(&entry["type"], json!("series")),
            "year": or(&entry["year"], json!("")), "poster": or(&entry["poster"], json!("")),
            "season": upcoming.season, "episode": upcoming.episode, "episode_name": upcoming.name, "still": upcoming.thumbnail,
            "released": upcoming.released, "in_progress": upcoming.at() == last && !last_done,
            "is_new": new.iter().any(|i| **i == upcoming), "new_count": new.len(), "newest": newest, "kind": "followed",
        }));
    }
    items.sort_by(|a, b| b["newest"].as_str().cmp(&a["newest"].as_str()));
    items.truncate(12);
    items
}

/// A series of Ma Liste not started: its newest season, if it began within the window.
fn listed_item(entry: &Value, videos: &[Value], today: &str, cutoff: &str) -> Option<Value> {
    let episodes: Vec<Aired> = aired_episodes(videos)
        .into_iter()
        .filter(|i| i.released.as_str() <= today)
        .collect();
    let first_season = episodes.iter().map(|i| i.season).min()?;
    let mut premieres: Vec<&Aired> = Vec::new();
    for item in &episodes {
        match premieres.iter_mut().find(|p| p.season == item.season) {
            Some(current) if item.episode < current.episode => *current = item,
            Some(_) => {}
            None => premieres.push(item),
        }
    }
    let recent: Vec<&&Aired> = premieres
        .iter()
        .filter(|i| i.released.as_str() >= cutoff)
        .collect();
    // max() keeps the first of equals, as Python's.
    let opening = recent
        .iter()
        .rev()
        .max_by(|a, b| (&a.released, a.season).cmp(&(&b.released, b.season)))?;
    let season_episodes: Vec<&Aired> = episodes
        .iter()
        .filter(|i| i.season == opening.season)
        .collect();
    Some(json!({
        "id": entry["id"], "name": or(&entry["name"], json!("")), "type": or(&entry["type"], json!("series")),
        "year": or(&entry["year"], json!("")), "poster": or(&entry["poster"], json!("")),
        "season": opening.season, "episode": opening.episode, "episode_name": opening.name, "still": opening.thumbnail,
        "released": opening.released, "in_progress": false, "is_new": true, "new_count": season_episodes.len(),
        "newest": season_episodes.iter().map(|i| i.released.clone()).max(),
        "kind": if opening.season == first_season { "premiere" } else { "season" },
    }))
}

// ---------- « Mes séries » ----------

fn card(entry: &Value) -> Map<String, Value> {
    ["id", "name", "year", "poster"]
        .iter()
        .map(|k| (k.to_string(), or(&entry[*k], json!(""))))
        .collect()
}

/// The episode reached, its share played, whether it is under way and whether it is done.
fn position(entry: &Value) -> (i64, i64, f64, bool, bool) {
    let season = int(&entry["season"]).unwrap_or(1);
    let episode = int(&entry["episode"]).unwrap_or(1);
    let pct = float(&entry["progress_pct"]);
    let completed = truthy(&entry["completed"]);
    (
        season,
        episode,
        pct,
        !completed && (1.0..85.0).contains(&pct),
        pct >= 85.0 || completed,
    )
}

/// The episode under way, as (season, episode, share played).
fn resume_point(entry: &Value) -> Option<(i64, i64, i64)> {
    let (season, episode, pct, in_progress, _) = position(entry);
    in_progress.then_some((season, episode, pct.round_ties_even() as i64))
}

fn seen_test(entry: &Value) -> impl Fn(&Aired) -> bool {
    let (season, episode, _, _, done) = position(entry);
    let watched = watched_codes(entry);
    move |item| {
        item.at() < (season, episode)
            || (item.at() == (season, episode) && done)
            || watched.contains(&episode_code(item.season, item.episode))
    }
}

fn brief(item: &Aired) -> Map<String, Value> {
    json!({"season": item.season, "episode": item.episode, "released": item.released})
        .as_object()
        .cloned()
        .unwrap_or_default()
}

fn brief_next(item: &Aired) -> Value {
    let mut next = brief(item);
    next.insert("resume".into(), false.into());
    next.insert("progress_pct".into(), 0.into());
    Value::Object(next)
}

fn followed(entry: &Value, videos: Option<&[Value]>, today: &str) -> Value {
    let mut out = card(entry);
    let (season, episode, pct, in_progress, done) = position(entry);
    let shown = if in_progress {
        pct.round_ties_even() as i64
    } else if done {
        100
    } else {
        0
    };
    let current = json!({"season": season, "episode": episode, "progress_pct": shown});
    let all = aired_episodes(videos.unwrap_or(&[]));
    let last_watched = or(&entry["last_watched"], json!(0));
    if videos.is_none() || all.is_empty() {
        out.extend(json!({"group": "continue", "known": false, "current": current, "next": null, "upcoming": null, "aired": 0, "seen": 0,
                          "remaining": 0, "finished": truthy(&entry["series_finished"]), "last_watched": last_watched})
            .as_object().cloned().unwrap_or_default());
        return Value::Object(out);
    }
    let aired: Vec<&Aired> = all
        .iter()
        .filter(|i| i.released.as_str() <= today)
        .collect();
    let coming: Vec<&Aired> = all.iter().filter(|i| i.released.as_str() > today).collect();
    let seen = seen_test(entry);
    let left: Vec<&&Aired> = aired.iter().filter(|i| !seen(i)).collect();
    let next = if in_progress && aired.iter().any(|i| i.at() == (season, episode)) {
        json!({"season": season, "episode": episode, "released": "", "resume": true, "progress_pct": pct.round_ties_even() as i64})
    } else if let Some(first) = left.first() {
        brief_next(first)
    } else {
        Value::Null
    };
    out.extend(json!({
        "group": if next.is_null() { "uptodate" } else { "continue" }, "known": true, "current": current, "next": next,
        "upcoming": coming.first().map(|i| Value::Object(brief(i))), "aired": aired.len(), "seen": aired.len() - left.len(),
        "remaining": left.len(), "finished": truthy(&entry["series_finished"]) && left.is_empty() && coming.is_empty(),
        "last_watched": last_watched,
    }).as_object().cloned().unwrap_or_default());
    Value::Object(out)
}

fn to_start(entry: &Value, videos: Option<&[Value]>, today: &str) -> Value {
    let mut out = card(entry);
    let all = aired_episodes(videos.unwrap_or(&[]));
    let aired: Vec<&Aired> = all
        .iter()
        .filter(|i| i.released.as_str() <= today)
        .collect();
    let coming: Vec<&Aired> = all.iter().filter(|i| i.released.as_str() > today).collect();
    out.extend(json!({
        "group": "start", "known": videos.is_some() && !all.is_empty(), "current": null,
        "next": aired.first().map(|i| brief_next(i)), "upcoming": coming.first().map(|i| Value::Object(brief(i))),
        "aired": aired.len(), "seen": 0, "remaining": aired.len(), "finished": false, "last_watched": 0,
    }).as_object().cloned().unwrap_or_default());
    Value::Object(out)
}

/// « À continuer » most recent first, « À jour » by the next release, « À commencer » in list order.
pub async fn my_series(
    meta: &Meta,
    history: &[Value],
    watchlist: &[Value],
    today: &str,
) -> Vec<Value> {
    let (from_history, listed) = followed_series(history, watchlist, 40);
    if from_history.is_empty() && listed.is_empty() {
        return vec![];
    }
    let all: Vec<&Value> = from_history.iter().chain(&listed).copied().collect();
    let lists = episode_lists(meta, &all).await;
    let mut items: Vec<Value> = from_history
        .iter()
        .zip(&lists)
        .map(|(e, v)| followed(e, v.as_deref(), today))
        .collect();
    items.extend(
        listed
            .iter()
            .zip(&lists[from_history.len()..])
            .map(|(e, v)| to_start(e, v.as_deref(), today)),
    );
    let rank = |item: &Value| match item["group"].as_str() {
        Some("continue") => 0,
        Some("uptodate") => 1,
        _ => 2,
    };
    let recency = |item: &Value| {
        if rank(item) == 0 {
            -float(&item["last_watched"])
        } else {
            0.0
        }
    };
    let release = |item: &Value| {
        if rank(item) == 1 {
            item["upcoming"]["released"]
                .as_str()
                .unwrap_or("9999-99-99")
                .to_string()
        } else {
            String::new()
        }
    };
    items.sort_by(|a, b| {
        rank(a)
            .cmp(&rank(b))
            .then(recency(a).total_cmp(&recency(b)))
            .then(release(a).cmp(&release(b)))
    });
    items
}

// ---------- « Calendrier » ----------

/// Monday of this week, and the Monday after the last week shown.
fn window(today: &str, weeks: i64) -> (String, String) {
    let Ok(day) = NaiveDate::parse_from_str(today, "%Y-%m-%d") else {
        return (String::new(), String::new());
    };
    let start = day - chrono::Duration::days(day.weekday().num_days_from_monday() as i64);
    (
        start.to_string(),
        (start + chrono::Duration::days(7 * weeks)).to_string(),
    )
}

fn premiere(item: &Aired) -> &'static str {
    match (item.season, item.episode) {
        (_, e) if e != 1 => "",
        (1, _) => "series",
        _ => "season",
    }
}

fn base(entry: &Value, followed: bool) -> Map<String, Value> {
    let mut out = card(entry);
    out.insert("followed".into(), followed.into());
    out
}

fn py_lower(value: &Value) -> String {
    text(value).to_lowercase()
}

/// The episodes coming out this week and next, day by day, then each series'
/// first release after these two weeks.
pub async fn calendar(
    meta: &Meta,
    history: &[Value],
    watchlist: &[Value],
    today: &str,
    local_today: &str,
) -> Value {
    let (start, end) = window(local_today, 2);
    let (from_history, listed) = followed_series(history, watchlist, 40);
    let entries: Vec<&Value> = from_history.iter().chain(&listed).copied().collect();
    let lists = episode_lists(meta, &entries).await;
    let mut unavailable = 0;
    let (mut items, mut later): (Vec<Map<String, Value>>, Vec<Value>) = (vec![], vec![]);
    for (index, (entry, videos)) in entries.iter().zip(&lists).enumerate() {
        let Some(videos) = videos else {
            unavailable += 1;
            continue;
        };
        let episodes = aired_episodes(videos);
        let is_followed = index < from_history.len();
        let test = seen_test(entry);
        let seen = |item: &Aired| is_followed && test(item);
        let unseen: Vec<(i64, i64)> = episodes
            .iter()
            .filter(|i| i.released.as_str() <= today && !seen(i))
            .map(Aired::at)
            .collect();
        let under_way = if is_followed {
            resume_point(entry)
        } else {
            None
        };
        let mut own: Vec<Map<String, Value>> = vec![];
        for item in &episodes {
            if !(start.as_str() <= item.released.as_str() && item.released < end) {
                continue;
            }
            let was_seen = seen(item);
            if let Some(last) = own.last_mut()
                && last["released"] == item.released.as_str()
                && last["season"] == item.season
                && last["last_episode"].as_i64().unwrap_or(0) + 1 == item.episode
                && last["seen"] == was_seen
            {
                last.insert("last_episode".into(), item.episode.into());
                continue;
            }
            let mut row = base(entry, is_followed);
            row.extend(json!({
                "season": item.season, "episode": item.episode, "last_episode": item.episode, "released": item.released,
                "aired": item.released.as_str() <= today, "seen": was_seen,
                "before": unseen.iter().filter(|c| **c < item.at()).count(), "premiere": premiere(item),
                "resume_pct": under_way.filter(|u| (u.0, u.1) == item.at()).map(|u| u.2).unwrap_or(0),
            }).as_object().cloned().unwrap_or_default());
            own.push(row);
        }
        items.extend(own);
        if let Some(first) = episodes
            .iter()
            .filter(|i| i.released >= end)
            .min_by(|a, b| {
                (&a.released, a.season, a.episode).cmp(&(&b.released, b.season, b.episode))
            })
        {
            let mut row = base(entry, is_followed);
            row.extend(json!({"season": first.season, "episode": first.episode, "released": first.released, "premiere": premiere(first)})
                .as_object().cloned().unwrap_or_default());
            later.push(Value::Object(row));
        }
    }
    let key = |i: &Map<String, Value>| {
        (
            i["released"].as_str().unwrap_or("").to_string(),
            i["followed"] != true,
            py_lower(&i["name"]),
            i["season"].as_i64(),
            i["episode"].as_i64(),
        )
    };
    items.sort_by_key(key);
    let mut days: Vec<Value> = vec![];
    for item in items {
        let date = item["released"].clone();
        if days.last().is_none_or(|d| d["date"] != date) {
            days.push(json!({"date": date, "items": []}));
        }
        if let Some(list) = days.last_mut().and_then(|d| d["items"].as_array_mut()) {
            list.push(Value::Object(item));
        }
    }
    later.sort_by_key(|i| {
        (
            i["released"].as_str().unwrap_or("").to_string(),
            py_lower(&i["name"]),
        )
    });
    later.truncate(12);
    json!({"today": today, "local_today": local_today, "start": start, "end": end, "days": days, "later": later,
           "series": entries.len(), "unavailable": unavailable})
}
