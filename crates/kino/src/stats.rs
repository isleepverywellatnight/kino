//! Viewing statistics from the history. History keeps one entry per title
//! (the last position, the episodes seen), not each viewing: the figures say
//! what that allows and nothing more. Films imported as seen count as seen,
//! not in the viewing time or the months.

use std::collections::{BTreeMap, HashSet};
use std::sync::LazyLock;

use chrono::{DateTime, Datelike, Utc};
use futures_util::StreamExt;
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::db::Db;
use crate::meta::Meta;

static CODE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)^s0*(\d+)e0*(\d+)$").unwrap());
static YEAR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(1[89]\d\d|20\d\d)").unwrap());
const GENRE_FETCH: usize = 30;

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

fn text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

/// float(value or 0), 0 for what Python refuses.
fn number(value: &Value) -> f64 {
    match value {
        Value::Bool(b) => *b as i64 as f64,
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(s) if !s.is_empty() => s.trim().parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

fn is_watched(entry: &Value) -> bool {
    truthy(&entry["completed"])
        || number(&entry["progress_pct"]) >= 85.0
        || truthy(&entry["imported_watched"])
}

fn code(value: &Value) -> Option<String> {
    let raw = if truthy(value) {
        text(value)
    } else {
        String::new()
    };
    let c = CODE.captures(raw.trim())?;
    Some(format!(
        "s{}e{}",
        c[1].parse::<u64>().ok()?,
        c[2].parse::<u64>().ok()?
    ))
}

/// Codes « s1e2 » of the episodes seen: marked as seen, or played to the end.
fn episodes_seen(entry: &Value) -> HashSet<String> {
    let mut codes: HashSet<String> = entry["watched_episodes"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(code)
        .collect();
    for (key, position) in entry["ep_positions"].as_object().into_iter().flatten() {
        if position.is_object()
            && (truthy(&position["completed"]) || number(&position["pct"]) >= 85.0)
        {
            codes.extend(code(&json!(key)));
        }
    }
    codes
}

fn seconds(entry: &Value) -> f64 {
    if truthy(&entry["imported_watched"]) {
        return 0.0;
    }
    let part =
        |played: f64, duration: f64| played.min(if duration != 0.0 { duration } else { played });
    if entry["type"] == "series" {
        return entry["ep_positions"]
            .as_object()
            .into_iter()
            .flatten()
            .filter(|(_, p)| p.is_object())
            .map(|(_, p)| {
                let (duration, played) = (number(&p["duration"]), number(&p["position"]));
                if truthy(&p["completed"]) && duration != 0.0 {
                    duration
                } else {
                    part(played, duration)
                }
            })
            .sum();
    }
    let (duration, played) = (number(&entry["duration"]), number(&entry["position"]));
    if is_watched(entry) && duration != 0.0 {
        duration
    } else {
        part(played, duration)
    }
}

fn year(entry: &Value) -> Option<i64> {
    let raw = if truthy(&entry["year"]) {
        text(&entry["year"])
    } else {
        String::new()
    };
    YEAR.captures(&raw).and_then(|c| c[1].parse().ok())
}

fn month_keys(today: DateTime<Utc>) -> Vec<String> {
    let (mut y, mut m) = (today.year(), today.month());
    let mut keys = Vec::with_capacity(12);
    for _ in 0..12 {
        keys.push(format!("{y:04}-{m:02}"));
        m -= 1;
        if m == 0 {
            (y, m) = (y - 1, 12);
        }
    }
    keys.reverse();
    keys
}

/// Totals, favourite genres, titles opened per month (last 12), decades.
pub fn compute(
    history: &[Value],
    genres_for: impl Fn(&Value) -> Vec<String>,
    today: DateTime<Utc>,
) -> Value {
    let entries: Vec<&Value> = history
        .iter()
        .filter(|e| e.is_object() && truthy(&e["id"]))
        .collect();
    let films: Vec<&Value> = entries
        .iter()
        .copied()
        .filter(|e| e["type"] != "series")
        .collect();
    let series: Vec<&Value> = entries
        .iter()
        .copied()
        .filter(|e| e["type"] == "series")
        .collect();
    let films_seen: Vec<&Value> = films.iter().copied().filter(|e| is_watched(e)).collect();
    // One entry per id, the last one wins, as a Python dict.
    let mut codes_by_id: Vec<(String, HashSet<String>)> = Vec::new();
    for entry in &series {
        let id = text(&entry["id"]);
        let codes = episodes_seen(entry);
        match codes_by_id.iter_mut().find(|(k, _)| *k == id) {
            Some(slot) => slot.1 = codes,
            None => codes_by_id.push((id, codes)),
        }
    }
    let codes_of = |e: &Value| {
        codes_by_id
            .iter()
            .find(|(k, _)| *k == text(&e["id"]))
            .map(|(_, c)| !c.is_empty())
            .unwrap_or(false)
    };
    let finished: Vec<&Value> = series
        .iter()
        .copied()
        .filter(|e| truthy(&e["series_finished"]) && is_watched(e))
        .collect();
    let started = series.iter().filter(|e| !finished.contains(e)).count();
    let mut seen_titles = films_seen.clone();
    seen_titles.extend(
        series
            .iter()
            .copied()
            .filter(|e| codes_of(e) || is_watched(e)),
    );

    let mut counts: Vec<(String, i64)> = Vec::new();
    let mut known = 0;
    for entry in &seen_titles {
        let genres: Vec<String> = genres_for(entry)
            .into_iter()
            .map(|g| g.trim().to_string())
            .filter(|g| !g.is_empty())
            .collect();
        if !genres.is_empty() {
            known += 1;
        }
        let mut once: Vec<&String> = Vec::new();
        for name in &genres {
            if !once.contains(&name) {
                once.push(name);
            }
        }
        for name in once {
            match counts.iter_mut().find(|(n, _)| n == name) {
                Some(slot) => slot.1 += 1,
                None => counts.push((name.clone(), 1)),
            }
        }
    }
    counts.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    counts.truncate(8);

    let mut months: Vec<(String, i64)> = month_keys(today).into_iter().map(|k| (k, 0)).collect();
    for entry in &entries {
        if truthy(&entry["imported_watched"]) {
            continue;
        }
        let stamp = number(&entry["last_watched"]);
        if stamp > 0.0
            && let Some(when) = DateTime::from_timestamp(stamp.floor() as i64, 0)
            && let Some(slot) = months
                .iter_mut()
                .find(|(k, _)| *k == when.format("%Y-%m").to_string())
        {
            slot.1 += 1;
        }
    }
    let mut decades: BTreeMap<i64, i64> = BTreeMap::new();
    for entry in &seen_titles {
        if let Some(y) = year(entry) {
            *decades.entry(y / 10 * 10).or_default() += 1;
        }
    }
    let oldest = films_seen
        .iter()
        .filter_map(|e| year(e).map(|y| (*e, y)))
        // min_by_key keeps the first of equals, as Python's min().
        .min_by_key(|(_, y)| *y)
        .map(|(e, y)| json!({"id": e["id"], "name": if truthy(&e["name"]) { e["name"].clone() } else { json!("") }, "year": y}));
    let watch: f64 = entries.iter().map(|e| seconds(e)).sum();
    json!({
        "films": {"seen": films_seen.len(), "started": films.len() - films_seen.len(),
                  "imported": films_seen.iter().filter(|e| truthy(&e["imported_watched"])).count()},
        "episodes": {"seen": codes_by_id.iter().map(|(_, c)| c.len()).sum::<usize>()},
        "series": {"total": series.len(), "started": started, "finished": finished.len()},
        "watch_seconds": watch.trunc() as i64,
        "genres": counts.iter().map(|(n, c)| json!({"name": n, "count": c})).collect::<Vec<_>>(),
        "genres_known": known,
        "titles_seen": seen_titles.len(),
        "months": months.iter().map(|(m, c)| json!({"month": m, "count": c})).collect::<Vec<_>>(),
        "decades": decades.iter().map(|(d, c)| json!({"decade": d, "count": c})).collect::<Vec<_>>(),
        "oldest": oldest,
    })
}

/// The statistics of the history. Genres come from KINO's catalogue, else from
/// the title's details (cached), for at most 30 titles per request.
pub async fn viewing_stats(db: &Db, meta: &Meta, history: &[Value]) -> Value {
    let entries: Vec<&Value> = history
        .iter()
        .filter(|e| e.is_object() && e["id"].as_str().is_some_and(|i| i.starts_with("tt")))
        .collect();
    let ids: Vec<String> = entries.iter().map(|e| text(&e["id"])).collect();
    let mut genres: Map<String, Value> = crate::catalog::media_genres(db, &ids);
    let has = |g: &Map<String, Value>, id: &str| g.get(id).is_some_and(truthy);
    let missing: Vec<(String, bool)> = entries
        .iter()
        .filter(|e| !has(&genres, &text(&e["id"])))
        .take(GENRE_FETCH)
        .map(|e| (text(&e["id"]), e["type"] == "series"))
        .collect();
    if !missing.is_empty() {
        let found: Vec<(String, Vec<Value>)> =
            futures_util::stream::iter(missing.into_iter().map(|(id, is_series)| {
                let meta = meta.clone();
                async move {
                    let kind = if is_series { "series" } else { "movie" };
                    let names = match meta.media(&id, kind).await {
                        Ok(m) => {
                            let list = if truthy(&m["genres"]) {
                                &m["genres"]
                            } else {
                                &m["genre"]
                            };
                            list.as_array()
                                .into_iter()
                                .flatten()
                                .filter(|n| truthy(n))
                                .map(|n| json!(text(n)))
                                .collect()
                        }
                        Err(_) => vec![],
                    };
                    (id, names)
                }
            }))
            .buffered(6)
            .collect()
            .await;
        for (id, names) in found.into_iter().filter(|(_, n)| !n.is_empty()) {
            genres.insert(id, Value::Array(names));
        }
    }
    let genres_for = |e: &Value| {
        let id = e["id"].as_str().unwrap_or("");
        genres
            .get(id)
            .and_then(Value::as_array)
            .map(|l| l.iter().map(text).collect())
            .unwrap_or_default()
    };
    compute(history, genres_for, Utc::now())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn films_series_genres_months_and_the_oldest() {
        let today = DateTime::from_timestamp(1_763_337_600, 0).unwrap(); // 2025-11-17
        let history = [
            json!({"id": "tt1", "type": "movie", "name": "A", "year": "1972", "completed": true, "duration": 6000, "position": 10,
                   "last_watched": 1_762_000_000}),
            json!({"id": "tt2", "type": "movie", "name": "B", "year": "1972", "imported_watched": true}),
            json!({"id": "tt3", "type": "movie", "year": "2001", "progress_pct": 40, "position": 1200, "duration": 6000}),
            json!({"id": "tt4", "type": "series", "year": "2010", "watched_episodes": ["S01E01", "s1e01", "bad"],
                   "ep_positions": {"S01E02": {"pct": 90, "position": 2000, "duration": 2400, "completed": true}}}),
        ];
        let genres = |e: &Value| match e["id"].as_str() {
            Some("tt1") | Some("tt4") => {
                vec!["Drama".to_string(), " Drama ".into(), "Crime".into()]
            }
            _ => vec![],
        };
        let stats = compute(&history, genres, today);
        assert_eq!(
            stats["films"],
            json!({"seen": 2, "started": 1, "imported": 1})
        );
        assert_eq!(stats["episodes"]["seen"], 2, "s1e1 counted once");
        assert_eq!(stats["watch_seconds"], 6000 + 1200 + 2400);
        assert_eq!(
            stats["genres"],
            json!([{"name": "Crime", "count": 2}, {"name": "Drama", "count": 2}])
        );
        assert_eq!(
            stats["oldest"],
            json!({"id": "tt1", "name": "A", "year": 1972}),
            "the first of equals"
        );
        assert_eq!(stats["months"][11], json!({"month": "2025-11", "count": 1}));
        assert_eq!(
            stats["decades"],
            json!([{"decade": 1970, "count": 2}, {"decade": 2010, "count": 1}])
        );
    }
}
