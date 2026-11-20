//! « Ce soir »: a few unwatched titles of Ma Liste for the time and mood
//! available, each with its reasons; what the user sets aside is learned, and
//! each choice can be undone.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;
use std::time::Duration;

use futures_util::StreamExt;
use regex::Regex;
use rusqlite::{OptionalExtension, params};
use serde_json::{Map, Value, json};
use unicode_normalization::UnicodeNormalization;
use unicode_normalization::char::is_combining_mark;

use crate::db::{Db, now};
use crate::meta::Meta;

static HOURS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(\d{1,2})\s*h(?:eures?)?\s*(\d{1,2})?").unwrap());
static MINUTES: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*(\d{1,3})\s*(?:min(?:ute)?s?)?\s*$").unwrap());
static IMDB: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^tt\d+$").unwrap());

#[derive(Debug)]
pub struct Refused;

fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false))
        && v != &json!(0)
        && v != &json!(0.0)
        && v != &json!("")
        && v != &json!([])
        && v != &json!({})
}

fn text(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

fn id_of(v: &Value) -> String {
    if truthy(&v["id"]) {
        text(&v["id"])
    } else {
        String::new()
    }
}

fn float(v: &Value) -> f64 {
    match v {
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(s) if !s.is_empty() => s.trim().parse().unwrap_or(0.0),
        Value::Bool(b) => *b as i64 as f64,
        _ => 0.0,
    }
}

fn int(v: &Value) -> i64 {
    match v {
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f.trunc() as i64))
            .unwrap_or(0),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        Value::Bool(b) => *b as i64,
        _ => 0,
    }
}

fn normalize(value: &str) -> String {
    let folded = value.to_lowercase().replace('ß', "ss");
    folded
        .nfkd()
        .filter(|c| !is_combining_mark(*c))
        .collect::<String>()
        .trim()
        .to_string()
}

fn mood_genres(mood: &str) -> &'static [&'static str] {
    match mood {
        "light" => &[
            "comedy",
            "comedie",
            "family",
            "familial",
            "slice of life",
            "tranche de vie",
        ],
        "intense" => &[
            "action", "crime", "thriller", "horror", "horreur", "mystery", "mystere",
        ],
        _ => &[],
    }
}

/// A duration in minutes; None when unknown.
pub fn runtime_minutes(value: &Value) -> Option<i64> {
    if let Value::Number(n) = value {
        let f = n.as_f64()?;
        return (f > 0.0 && f <= 1000.0).then_some(f.trunc() as i64);
    }
    let raw = match value {
        Value::Null | Value::Bool(false) => String::new(),
        v if !truthy(v) => String::new(),
        v => text(v),
    };
    let t = normalize(&raw);
    if let Some(c) = HOURS.captures(&t) {
        let total = c[1].parse::<i64>().ok()? * 60
            + c.get(2)
                .and_then(|m| m.as_str().parse::<i64>().ok())
                .unwrap_or(0);
        return (total > 0 && total <= 1000).then_some(total);
    }
    let minutes = MINUTES.captures(&t)?[1].parse::<i64>().ok()?;
    (minutes > 0).then_some(minutes)
}

fn watched_ids<'a>(history: impl IntoIterator<Item = &'a Value>) -> HashSet<String> {
    let mut watched = HashSet::new();
    for entry in history {
        let id = id_of(entry);
        if id.is_empty() {
            continue;
        }
        if truthy(&entry["imported_watched"]) || truthy(&entry["manual_watched"]) {
            watched.insert(id);
            continue;
        }
        // Completing one episode is not evidence that the entire series was seen.
        if ["series", "anime"].contains(&entry["type"].as_str().unwrap_or(""))
            && truthy(&entry["season"])
            && truthy(&entry["episode"])
        {
            continue;
        }
        if truthy(&entry["completed"]) || float(&entry["progress_pct"]) >= 85.0 {
            watched.insert(id);
        }
    }
    watched
}

fn genres_of(item: &Value) -> Vec<String> {
    match &item["genres"] {
        Value::String(s) if !s.is_empty() => s.split(',').map(str::to_string).collect(),
        Value::Array(a) => a.iter().filter(|g| truthy(g)).map(text).collect(),
        _ => vec![],
    }
}

/// The decisions and the times each title was shown, by id.
pub fn feedback_state(db: &Db) -> IndexedFeedback {
    db.read(|c| {
        let mut s = c.prepare(
            "SELECT media_id, name, media_type, genres_json, decision, until_ts, shown_count, last_shown, last_action_id, undo_json, updated_at FROM tonight_feedback",
        )?;
        let rows = s.query_map([], |r| {
            let genres: String = r.get(3)?;
            Ok((
                r.get::<_, String>(0)?,
                json!({"media_id": r.get::<_, String>(0)?, "name": r.get::<_, String>(1)?, "media_type": r.get::<_, String>(2)?,
                       "genres_json": genres, "decision": r.get::<_, String>(4)?, "until_ts": r.get::<_, f64>(5)?,
                       "shown_count": r.get::<_, i64>(6)?, "last_shown": r.get::<_, f64>(7)?, "last_action_id": r.get::<_, String>(8)?,
                       "undo_json": r.get::<_, String>(9)?, "updated_at": r.get::<_, f64>(10)?,
                       "genres": serde_json::from_str::<Value>(&genres).unwrap_or(json!([]))}),
            ))
        })?;
        rows.collect()
    })
    .unwrap_or_default()
}

pub type IndexedFeedback = indexmap::IndexMap<String, Value>;

/// The available offline downloads, by id: a completed file still on disk.
pub fn offline_downloads(db: &Db) -> Vec<Value> {
    let rows: Vec<Map<String, Value>> = db
        .with(|c| {
            let mut s = c.prepare("SELECT id, status, media_json, root_path, file_path, bytes_done, bytes_total FROM offline_downloads ORDER BY created_at DESC")?;
            let rows = s.query_map([], |r| {
                Ok(json!({"id": r.get::<_, String>(0)?, "status": r.get::<_, String>(1)?, "media_json": r.get::<_, String>(2)?,
                          "root_path": r.get::<_, String>(3)?, "file_path": r.get::<_, String>(4)?, "bytes_done": r.get::<_, i64>(5)?,
                          "bytes_total": r.get::<_, Option<i64>>(6)?.unwrap_or(0)})
                .as_object()
                .cloned()
                .unwrap_or_default())
            })?;
            rows.collect()
        })
        .unwrap_or_default();
    rows.into_iter()
        .map(|row| {
            let available = row["status"] == "completed" && file_ready(&row);
            if row["status"] == "completed" && !available {
                let _ = db.with(|c| {
                    c.execute(
                        "UPDATE offline_downloads SET status='interrupted', error_code='missing_file', updated_at=? WHERE id=?",
                        params![now(), text(&row["id"])],
                    )
                });
            }
            let media: Value = serde_json::from_str(row["media_json"].as_str().unwrap_or("{}")).unwrap_or(json!({}));
            json!({"id": row["id"], "available": available, "media": media})
        })
        .collect()
}

/// The managed file of a download, inside its own folder, of the size recorded.
fn file_ready(row: &Map<String, Value>) -> bool {
    let path = std::path::Path::new(row["file_path"].as_str().unwrap_or(""));
    let root = std::path::Path::new(row["root_path"].as_str().unwrap_or(""));
    let prefix = format!("{}_", text(&row["id"]));
    let named = path
        .file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|n| n.starts_with(&prefix));
    let link = |p: &std::path::Path| {
        std::fs::symlink_metadata(p).is_ok_and(|m| m.file_type().is_symlink())
    };
    if !named || link(path) || link(root) {
        return false;
    }
    let same_folder = match (
        path.parent().and_then(|p| p.canonicalize().ok()),
        root.canonicalize().ok(),
    ) {
        (Some(a), Some(b)) => a == b,
        _ => false,
    };
    let Ok(meta) = std::fs::metadata(path) else {
        return false;
    };
    let size = meta.len() as i64;
    let total = row["bytes_total"].as_i64().unwrap_or(0);
    same_folder
        && meta.is_file()
        && size > 0
        && size == row["bytes_done"].as_i64().unwrap_or(-1)
        && (total == 0 || size == total)
}

/// Ma Liste with the runtime and genres each title needs, from the cache
/// first, then at most 36 titles fetched within seven seconds.
pub async fn enrich(meta: &Meta, items: Vec<Value>, max_fetch: usize) -> (Vec<Value>, Value) {
    let db = meta.net.db();
    let mut enriched = items;
    let mut matched: HashSet<usize> = HashSet::new();
    fn merge(item: &mut Value, data: &Value) {
        if data.is_object() {
            for key in ["runtime", "runtime_minutes", "genres"] {
                if truthy(&data[key]) && !truthy(&item[key]) {
                    item[key] = data[key].clone();
                }
            }
        }
    }
    let complete = |item: &Value| {
        (truthy(&item["runtime"]) || truthy(&item["runtime_minutes"])) && truthy(&item["genres"])
    };
    let mut pending: Vec<(usize, String, bool)> = Vec::new();
    for (index, item) in enriched.iter_mut().enumerate() {
        let series = ["series", "anime"].contains(&item["type"].as_str().unwrap_or(""));
        let kind = if series { "series" } else { "movie" };
        let id = item.get("id").map(text).unwrap_or_default();
        let cached = db
            .cache_get(&format!("meta_fr_v2:{kind}:{id}"))
            .filter(truthy)
            .or_else(|| {
                db.cache_get(&format!("tonight_meta_v1:{kind}:{id}"))
                    .filter(truthy)
            });
        if let Some(cached) = cached {
            merge(item, &cached);
        }
        if complete(item) {
            matched.insert(index);
        } else if truthy(&item["id"]) {
            pending.push((index, id, series));
        }
    }
    if !pending.is_empty() && max_fetch > 0 {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(7);
        let mut fetches = futures_util::stream::iter(pending.into_iter().take(max_fetch).map(
            |(index, id, series)| {
                let meta = meta.clone();
                async move { (index, fetch_metadata(&meta, &id, series).await) }
            },
        ))
        .buffer_unordered(6);
        while let Ok(Some((index, data))) = tokio::time::timeout_at(deadline, fetches.next()).await
        {
            merge(&mut enriched[index], &data);
            if complete(&enriched[index]) {
                matched.insert(index);
            }
        }
    }
    let total = enriched.len();
    (
        enriched,
        json!({"enriched": matched.len(), "total": total, "partial": matched.len() < total}),
    )
}

async fn fetch_metadata(meta: &Meta, id: &str, series: bool) -> Value {
    if !IMDB.is_match(id) {
        return json!({});
    }
    let kind = if series { "series" } else { "movie" };
    let url = format!("{}/meta/{kind}/{id}.json", meta.cinemeta);
    let Ok(data) = meta
        .net
        .request_json(
            reqwest::Method::GET,
            &url,
            &[("User-Agent", crate::net::BROWSER)],
            None,
            Duration::from_secs(4),
            0,
        )
        .await
    else {
        return json!({});
    };
    let found = if truthy(&data["meta"]) {
        data["meta"].clone()
    } else {
        json!({})
    };
    if truthy(&found) {
        let _ = meta
            .net
            .db()
            .cache_set(&format!("tonight_meta_v1:{kind}:{id}"), &found, 86400.0);
    }
    found
}

/// What « Ce soir » answers: up to three choices, how complete the titles'
/// data was, why nothing came (when nothing did), and the titles set aside.
#[derive(Debug, Default)]
pub struct Page {
    pub choices: Vec<Value>,
    pub coverage: Value,
    pub empty_reason: &'static str,
    pub review: Vec<Value>,
}

/// The durations and moods a page can ask for.
pub const MOODS: [&str; 3] = ["light", "intense", "discovery"];

/// « Ce soir » for `minutes` (15 to 600) and `mood` (one of `MOODS`): as
/// `/api/tonight` answers, for the server and the native interface alike.
/// Choices shown are recorded, as on the page.
pub async fn page(
    meta: &Meta,
    db: &Db,
    minutes: i64,
    mood: &str,
    offline_only: bool,
) -> Result<Page, Refused> {
    if !(15..=600).contains(&minutes) || !MOODS.contains(&mood) {
        return Err(Refused);
    }
    let (watchlist, coverage) = enrich(
        meta,
        crate::library::watchlist(db),
        if offline_only { 0 } else { 36 },
    )
    .await;
    let downloads = offline_downloads(db);
    let feedback = feedback_state(db);
    let history = crate::library::history(db);
    let at = crate::db::now();
    let choices = choose(
        &watchlist,
        &history,
        minutes,
        mood,
        &feedback,
        at,
        &downloads,
        offline_only,
    );
    let reason = empty_reason(
        &watchlist,
        &history,
        &feedback,
        &downloads,
        offline_only,
        &choices,
        at,
    );
    record_impressions(db, &choices);
    let review: Vec<Value> = watchlist
        .iter()
        .filter_map(|item| {
            let id = item["id"].as_str()?;
            let f = feedback.get(id)?;
            let decision = f["decision"].as_str().unwrap_or("");
            let keep = decision == "excluded" || decision == "seen" || (decision == "deferred" && f["until_ts"].as_f64().unwrap_or(0.0) > crate::db::now());
            let name = if f["name"].as_str().is_some_and(|n| !n.is_empty()) { f["name"].clone() } else { item["name"].clone() };
            keep.then(|| json!({"id": id, "name": name, "decision": decision, "until_ts": f["until_ts"], "undo_token": f["last_action_id"]}))
        })
        .collect();
    Ok(Page {
        choices,
        coverage,
        empty_reason: reason,
        review,
    })
}

/// The eligible titles with factual reasons; the most fitting first, varied in genre.
#[allow(clippy::too_many_arguments)] // the inputs of one decision, as the page gives them
pub fn choose(
    items: &[Value],
    history: &[Value],
    max_minutes: i64,
    mood: &str,
    feedback: &IndexedFeedback,
    at: f64,
    downloads: &[Value],
    offline_only: bool,
) -> Vec<Value> {
    let limit = 3;
    let watched = watched_ids(history);
    let any_shown = feedback.values().any(|e| truthy(&e["shown_count"]));
    let mut rejected: HashMap<String, i64> = HashMap::new();
    for entry in feedback.values().filter(|e| e["decision"] == "excluded") {
        let set: HashSet<String> = entry["genres"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|g| truthy(g))
            .map(|g| normalize(&text(g)))
            .collect();
        for g in set {
            *rejected.entry(g).or_default() += 1;
        }
    }
    let often_rejected: HashSet<String> = rejected
        .into_iter()
        .filter(|(_, n)| *n >= 2)
        .map(|(g, _)| g)
        .collect();
    let mut offline: HashMap<String, Vec<&Value>> = HashMap::new();
    for d in downloads {
        let id = if truthy(&d["media"]["id"]) {
            text(&d["media"]["id"])
        } else {
            String::new()
        };
        if truthy(&d["available"]) && !id.is_empty() {
            offline.entry(id).or_default().push(d);
        }
    }
    for list in offline.values_mut() {
        list.sort_by_key(|d| (int(&d["media"]["season"]), int(&d["media"]["episode"])));
    }
    let wanted = mood_genres(mood);
    struct Candidate {
        out: Value,
        score: i64,
        genres: HashSet<String>,
        last_shown: f64,
        order: usize,
    }
    let mut selected: Vec<Candidate> = Vec::new();
    let mut seen = HashSet::new();
    for (order, item) in items.iter().enumerate() {
        let id = id_of(item);
        if id.is_empty() || seen.contains(&id) || watched.contains(&id) {
            continue;
        }
        seen.insert(id.clone());
        let empty = json!({});
        let preference = feedback.get(&id).unwrap_or(&empty);
        let decision = preference["decision"].as_str().unwrap_or("");
        if decision == "excluded"
            || decision == "seen"
            || (decision == "deferred" && float(&preference["until_ts"]) > at)
        {
            continue;
        }
        let available = offline.get(&id).cloned().unwrap_or_default();
        if offline_only && available.is_empty() {
            continue;
        }
        let duration = runtime_minutes(if truthy(&item["runtime_minutes"]) {
            &item["runtime_minutes"]
        } else {
            &item["runtime"]
        });
        if duration.is_some_and(|d| d > max_minutes) {
            continue;
        }
        let genres: HashSet<String> = genres_of(item).into_iter().map(|g| normalize(&g)).collect();
        let matching: HashSet<&String> = genres
            .iter()
            .filter(|g| wanted.contains(&g.as_str()))
            .collect();
        if mood != "discovery" && !genres.is_empty() && matching.is_empty() {
            continue;
        }
        let mut label = duration
            .map(|d| format!("{d} min"))
            .unwrap_or_else(|| "Durée inconnue".into());
        if duration.is_some() && ["series", "anime"].contains(&item["type"].as_str().unwrap_or(""))
        {
            label.push_str(" par épisode");
        }
        let mut reasons = vec![label];
        if !matching.is_empty() {
            reasons.push(
                if mood == "light" {
                    "Ambiance légère"
                } else {
                    "Ambiance intense"
                }
                .into(),
            );
        } else if mood == "discovery" {
            reasons.push("À découvrir dans votre liste".into());
        } else {
            reasons.push("Ambiance non renseignée".into());
        }
        let shown = int(&preference["shown_count"]).max(0);
        if any_shown && shown == 0 {
            reasons.push("Encore jamais proposé".into());
        }
        if !available.is_empty() {
            reasons.push("Disponible hors ligne".into());
        }
        let avoided = genres.iter().any(|g| often_rejected.contains(g));
        if !often_rejected.is_empty() && !genres.is_empty() && !avoided {
            reasons.push("Évite les genres souvent écartés".into());
        }
        let fit = if !matching.is_empty() {
            8
        } else if mood == "discovery" {
            2
        } else {
            0
        };
        let length = duration
            .map(|d| 3 + (4 * d / max_minutes).min(4))
            .unwrap_or(0);
        let score = fit + length - (shown * 3).min(12) - if avoided { 4 } else { 0 };
        selected.push(Candidate {
            out: json!({"item": item, "reasons": reasons, "duration_known": duration.is_some(),
                        "mood_known": if mood != "discovery" { !matching.is_empty() } else { true },
                        "offline_id": available.first().map(|d| d["id"].clone())}),
            score,
            genres,
            last_shown: float(&preference["last_shown"]),
            order,
        });
    }
    let mut result: Vec<Candidate> = Vec::new();
    while !selected.is_empty() && result.len() < limit {
        let used: HashSet<String> = result
            .iter()
            .flat_map(|c| c.genres.iter().cloned())
            .collect();
        let key = |c: &Candidate| {
            -(c.score
                - if c.genres.iter().any(|g| used.contains(g)) {
                    4
                } else {
                    0
                })
        };
        selected.sort_by(|a, b| {
            key(a)
                .cmp(&key(b))
                .then(a.last_shown.total_cmp(&b.last_shown))
                .then(a.order.cmp(&b.order))
        });
        let mut candidate = selected.remove(0);
        if !used.is_empty()
            && !candidate.genres.is_empty()
            && !candidate.genres.iter().any(|g| used.contains(g))
            && let Some(reasons) = candidate.out["reasons"].as_array_mut()
        {
            reasons.push(json!("Genre différent des autres idées"));
        }
        result.push(candidate);
    }
    result.into_iter().map(|c| c.out).collect()
}

/// Why nothing was proposed, for the page's message.
pub fn empty_reason(
    items: &[Value],
    history: &[Value],
    feedback: &IndexedFeedback,
    downloads: &[Value],
    offline_only: bool,
    choices: &[Value],
    at: f64,
) -> &'static str {
    if !choices.is_empty() {
        return "";
    }
    if items.is_empty() {
        return "empty_watchlist";
    }
    let ids: HashSet<String> = items.iter().map(id_of).collect();
    let mut watched = watched_ids(history);
    watched.extend(
        feedback
            .iter()
            .filter(|(_, e)| e["decision"] == "seen")
            .map(|(k, _)| k.clone()),
    );
    if ids.is_subset(&watched) {
        return "all_watched";
    }
    if offline_only
        && !downloads.iter().any(|d| {
            truthy(&d["available"])
                && ids.contains(
                    &(if truthy(&d["media"]["id"]) {
                        text(&d["media"]["id"])
                    } else {
                        String::new()
                    }),
                )
        })
    {
        return "no_offline";
    }
    let set_aside = |id: &String| {
        let e = feedback.get(id);
        let decision = e.and_then(|e| e["decision"].as_str()).unwrap_or("");
        watched.contains(id)
            || decision == "excluded"
            || (decision == "deferred" && e.is_some_and(|e| float(&e["until_ts"]) > at))
    };
    if ids.iter().all(set_aside) {
        return "all_deferred";
    }
    "no_match"
}

/// Counts the titles actually proposed.
pub fn record_impressions(db: &Db, choices: &[Value]) {
    let at = now();
    let _ = db.with(|c| {
        let tx = c.unchecked_transaction()?;
        for choice in choices {
            let item = &choice["item"];
            let id = id_of(item);
            if id.is_empty() {
                continue;
            }
            let genres: Vec<String> = genres_of(item).into_iter().map(|g| g.chars().take(80).collect()).take(20).collect();
            let name: String = (if truthy(&item["name"]) { text(&item["name"]) } else { String::new() }).chars().take(300).collect();
            let kind: String = (if truthy(&item["type"]) { text(&item["type"]) } else { "movie".into() }).chars().take(20).collect();
            tx.execute(
                "INSERT INTO tonight_feedback(media_id, name, media_type, genres_json, shown_count, last_shown, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?) \
                 ON CONFLICT(media_id) DO UPDATE SET name=excluded.name, media_type=excluded.media_type, genres_json=excluded.genres_json, \
                 shown_count=tonight_feedback.shown_count + 1, last_shown=excluded.last_shown, updated_at=excluded.updated_at",
                params![id, name, kind, serde_json::to_string(&genres).unwrap_or_else(|_| "[]".into()), at, at],
            )?;
        }
        tx.commit()
    });
}

/// « Plus tard » (a day), « Pas pour moi », « Déjà vu » or « Remettre »; the token undoes it.
pub fn apply_feedback(db: &Db, id: &str, action: &Value) -> Result<String, Refused> {
    let id = id.trim();
    let action = action.as_str().unwrap_or("");
    if !["defer", "exclude", "restore", "seen"].contains(&action)
        || id.is_empty()
        || id.chars().count() > 200
    {
        return Err(Refused);
    }
    let token = hex::encode(rand::random::<[u8; 16]>());
    let at = now();
    db.with(|c| {
        let tx = c.unchecked_transaction()?;
        let media: Option<(String, String)> =
            tx.query_row("SELECT name, type FROM watchlist WHERE id=?", [id], |r| Ok((r.get::<_, Option<String>>(0)?.unwrap_or_default(), r.get::<_, Option<String>>(1)?.unwrap_or_default()))).optional()?;
        let Some((name, kind)) = media else { return Ok(Err(Refused)) };
        let previous: Option<(String, f64)> = tx.query_row("SELECT decision, until_ts FROM tonight_feedback WHERE media_id=?", [id], |r| Ok((r.get(0)?, r.get(1)?))).optional()?;
        let mut undo = json!({"decision": previous.as_ref().map(|p| p.0.clone()).unwrap_or_default(), "until_ts": previous.as_ref().map(|p| p.1).unwrap_or(0.0)});
        let decision = match action {
            "defer" => "deferred",
            "exclude" => "excluded",
            "seen" => "seen",
            _ => "",
        };
        let until = if action == "defer" { at + 86400.0 } else { 0.0 };
        if action == "seen" {
            let before: Option<String> = tx.query_row("SELECT data_json FROM watch_history WHERE id=?", [id], |r| r.get(0)).optional()?;
            let mut history: Value = before.as_deref().and_then(|b| serde_json::from_str(b).ok()).unwrap_or(json!({}));
            if watched_ids([&history]).contains(id) || previous.as_ref().is_some_and(|p| p.0 == "seen") {
                return Ok(Err(Refused));
            }
            let duration = float(&history["duration"]).max(7200.0);
            if let Some(h) = history.as_object_mut() {
                for (k, v) in [("id", json!(id)), ("name", json!(name)), ("type", json!(kind)), ("completed", json!(true)), ("manual_watched", json!(true)),
                               ("progress_pct", json!(100.0)), ("last_watched", json!(at)), ("duration", json!(duration)), ("position", json!(duration))] {
                    h.insert(k.into(), v);
                }
            }
            let after = history.to_string();
            tx.execute(
                "INSERT INTO watch_history(id, data_json, last_watched) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json, last_watched=excluded.last_watched",
                params![id, after, at],
            )?;
            undo["history_before"] = before.map(Value::from).unwrap_or(Value::Null);
            undo["history_after"] = json!(after);
        }
        tx.execute(
            "INSERT INTO tonight_feedback(media_id, name, media_type, decision, until_ts, last_action_id, undo_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) \
             ON CONFLICT(media_id) DO UPDATE SET decision=excluded.decision, until_ts=excluded.until_ts, last_action_id=excluded.last_action_id, \
             undo_json=excluded.undo_json, updated_at=excluded.updated_at",
            params![id, name, kind, decision, until, token, undo.to_string(), at],
        )?;
        tx.commit()?;
        Ok(Ok(token.clone()))
    })
    .map_err(|_| Refused)?
}

/// Takes back the last choice, unless the history changed since.
pub fn undo_feedback(db: &Db, id: &Value, token: &Value) -> Result<(), Refused> {
    let id = id.as_str().unwrap_or("").to_string();
    let token = token.as_str().unwrap_or("").to_string();
    db.with(|c| {
        let tx = c.unchecked_transaction()?;
        let row: Option<(String, String)> = tx.query_row("SELECT last_action_id, undo_json FROM tonight_feedback WHERE media_id=?", [&id], |r| Ok((r.get(0)?, r.get(1)?))).optional()?;
        let Some((last, undo)) = row.filter(|(last, _)| !token.is_empty() && *last == token) else { return Ok(Err(Refused)) };
        let _ = last;
        let previous: Value = serde_json::from_str(&undo).unwrap_or(json!({}));
        if let Some(after) = previous.get("history_after") {
            let current: Option<String> = tx.query_row("SELECT data_json FROM watch_history WHERE id=?", [&id], |r| r.get(0)).optional()?;
            if current.as_deref() != after.as_str() {
                return Ok(Err(Refused));
            }
            match previous["history_before"].as_str() {
                None => {
                    tx.execute("DELETE FROM watch_history WHERE id=?", [&id])?;
                }
                Some(before) => {
                    let restored: Value = serde_json::from_str(before).unwrap_or(json!({}));
                    tx.execute("UPDATE watch_history SET data_json=?, last_watched=? WHERE id=?", params![before, float(&restored["last_watched"]), id])?;
                }
            }
        }
        tx.execute(
            "UPDATE tonight_feedback SET decision=?, until_ts=?, last_action_id='', undo_json='{}', updated_at=? WHERE media_id=?",
            params![previous["decision"].as_str().unwrap_or(""), float(&previous["until_ts"]), now(), id],
        )?;
        tx.commit()?;
        Ok(Ok(()))
    })
    .map_err(|_| Refused)?
}

pub fn reset_feedback(db: &Db) {
    let _ = db.with(|c| c.execute("DELETE FROM tonight_feedback", []));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_page_needs_a_valid_duration_and_mood_and_says_why_it_is_empty() {
        let dir = tempfile::tempdir().unwrap();
        let db = std::sync::Arc::new(Db::open(&dir.path().join("kino.db")).unwrap());
        let meta = Meta {
            net: std::sync::Arc::new(crate::net::Net::new(db.clone())),
            cinemeta: crate::meta::CINEMETA.into(),
            translate: false,
        };
        assert!(page(&meta, &db, 5, "light", false).await.is_err());
        assert!(page(&meta, &db, 120, "sad", false).await.is_err());
        let found = page(&meta, &db, 120, "discovery", false).await.unwrap();
        assert!(found.choices.is_empty());
        assert_eq!(found.empty_reason, "empty_watchlist");
    }

    #[test]
    fn durations_and_choices() {
        assert_eq!(runtime_minutes(&json!(142)), Some(142));
        assert_eq!(runtime_minutes(&json!("2h 22min")), Some(142));
        assert_eq!(runtime_minutes(&json!("2 heures")), Some(120));
        assert_eq!(runtime_minutes(&json!("47 min")), Some(47));
        assert_eq!(runtime_minutes(&json!("unknown")), None);
        assert_eq!(runtime_minutes(&json!(0)), None);
        let items = vec![
            json!({"id": "tt1", "name": "Long", "type": "movie", "runtime": "180 min", "genres": ["Drama"]}),
            json!({"id": "tt2", "name": "Fun", "type": "movie", "runtime": "95 min", "genres": ["Comedy"]}),
            json!({"id": "tt3", "name": "Seen", "type": "movie", "runtime": "90 min", "genres": ["Comedy"]}),
            json!({"id": "tt4", "name": "Show", "type": "series", "runtime": "30 min", "genres": ["Comédie"]}),
        ];
        let history = vec![json!({"id": "tt3", "completed": true})];
        let picks = choose(
            &items,
            &history,
            120,
            "light",
            &IndexedFeedback::new(),
            0.0,
            &[],
            false,
        );
        let ids: Vec<&str> = picks
            .iter()
            .map(|p| p["item"]["id"].as_str().unwrap())
            .collect();
        assert_eq!(
            ids,
            vec!["tt2", "tt4"],
            "too long, seen and other moods left out; the longer fit first"
        );
        assert_eq!(
            picks[1]["reasons"],
            json!([
                "30 min par épisode",
                "Ambiance légère",
                "Genre différent des autres idées"
            ]),
            "« comédie » and « comedy » are two genres here, as in the Python version"
        );
        assert_eq!(
            empty_reason(
                &items[2..3],
                &history,
                &IndexedFeedback::new(),
                &[],
                false,
                &[],
                0.0
            ),
            "all_watched"
        );
    }
}
