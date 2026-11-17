//! Titles: the catalogue rows, a title's page, the instant search, and
//! recording a play. The anime catalogue still goes to the Python server.

use std::collections::HashMap;
use std::sync::LazyLock;

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, Method, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::body::JsonBody;
use crate::server::{Shared, delegate};
use crate::{catalog, library};

static TITLE_ID: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(?:tt\d{1,12}|[a-z]{2,12}:\d{1,12})$").unwrap());

type Params = Query<HashMap<String, String>>;

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/catalog", get(catalog_page))
        .route("/api/meta", get(meta))
        .route("/api/series-meta", get(series_meta))
        .route("/api/search-suggest", get(suggest))
        .route("/api/history-record", post(history_record))
        .route("/api/watched-toggle", post(watched_toggle))
}

fn refused(text: &str) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({"error": text}))).into_response()
}

fn down() -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({"error": "Le catalogue ne répond pas pour le moment."})),
    )
        .into_response()
}

async fn catalog_page(
    State(s): State<Shared>,
    Query(p): Params,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let kind = p.get("type").map(String::as_str).unwrap_or("movie");
    if !["movie", "series", "anime", "classics"].contains(&kind) {
        return refused("Catalogue inconnu.");
    }
    if kind == "anime" {
        return delegate(&s, method, &uri, &headers, Bytes::new()).await;
    }
    let skip = p
        .get("skip")
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(0);
    let genre = p.get("genre").map(String::as_str).unwrap_or("");
    let sort = p.get("sort").map(String::as_str).unwrap_or("top");
    match s.meta.catalog_top(kind, genre, skip, sort).await {
        Ok(metas) => Json(json!({"metas": metas})).into_response(),
        Err(_) => down(),
    }
}

async fn meta(State(s): State<Shared>, Query(p): Params) -> Response {
    let id = p
        .get("imdb_id")
        .map(|v| v.trim().to_string())
        .unwrap_or_default();
    if !TITLE_ID.is_match(&id) {
        return refused("Identifiant de titre invalide.");
    }
    let kind = p.get("type").map(String::as_str).unwrap_or("movie");
    match s
        .meta
        .media(p.get("imdb_id").map(String::as_str).unwrap_or(""), kind)
        .await
    {
        Ok(meta) => Json(json!({"meta": meta})).into_response(),
        Err(_) => down(),
    }
}

async fn series_meta(State(s): State<Shared>, Query(p): Params) -> Response {
    let id = p
        .get("imdb_id")
        .map(|v| v.trim().to_string())
        .unwrap_or_default();
    if !TITLE_ID.is_match(&id) {
        return refused("Identifiant de titre invalide.");
    }
    match s
        .meta
        .media(p.get("imdb_id").map(String::as_str).unwrap_or(""), "series")
        .await
    {
        Ok(meta) => Json(json!({"meta": meta})).into_response(),
        Err(_) => down(),
    }
}

async fn suggest(State(s): State<Shared>, Query(p): Params) -> Response {
    let q = p.get("q").map(|v| v.trim().to_string()).unwrap_or_default();
    let kind = p
        .get("type")
        .map(String::as_str)
        .filter(|k| *k == "movie" || *k == "series");
    let results = catalog::search_fast(s.net.db(), &q, kind, 8);
    Json(json!({"ok": true, "results": results})).into_response()
}

/// Position, duration and share kept only as finite numbers: a strange value
/// from a page must not stop the history.
fn clean_progress(entry: &mut Map<String, Value>) {
    for key in ["position", "duration", "progress_pct"] {
        let Some(value) = entry.get(key).filter(|v| !v.is_null()) else {
            continue;
        };
        let number = value
            .as_f64()
            .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
            .filter(|n: &f64| n.is_finite() && *n >= 0.0);
        match number {
            Some(n) => entry.insert(key.into(), json!(n)),
            None => entry.remove(key),
        };
    }
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn whole(value: Option<&Value>) -> i64 {
    value
        .and_then(|v| {
            v.as_i64()
                .or_else(|| v.as_f64().map(|f| f as i64))
                .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
        })
        .unwrap_or(0)
}

/// An episode is finished at 85 % (as the page says), else 88 % or 3 minutes from the end.
fn episode_finished(entry: &Map<String, Value>) -> bool {
    if truthy(entry.get("completed")) {
        return true;
    }
    let get = |k: &str| entry.get(k).and_then(Value::as_f64).unwrap_or(0.0);
    if get("progress_pct") >= 85.0 {
        return true;
    }
    let (position, duration) = (get("position"), get("duration"));
    duration > 0.0 && (position / duration >= 0.88 || duration - position <= 180.0)
}

async fn history_record(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let Some(mut entry) = body["media"]
        .as_object()
        .cloned()
        .filter(|m| truthy(m.get("id")))
    else {
        return Json(json!({"ok": true})).into_response();
    };
    clean_progress(&mut entry);
    let id = [entry.get("id"), entry.get("filename")]
        .into_iter()
        .flatten()
        .find(|v| truthy(Some(v)))
        .and_then(|v| v.as_str().map(str::to_string));
    if let Some(id) = id
        && truthy(entry.get("season"))
        && truthy(entry.get("episode"))
    {
        let finished = episode_finished(&entry);
        let (season, episode) = (whole(entry.get("season")), whole(entry.get("episode")));
        let (next_season, next_episode, series_done) =
            s.meta.next_episode(&id, season, episode, finished).await;
        entry.insert("next_season".into(), json!(next_season));
        entry.insert("next_episode".into(), json!(next_episode));
        entry.insert("series_finished".into(), json!(finished && series_done));
    }
    let db = s.net.clone();
    let history = tokio::task::spawn_blocking(move || library::record_history(db.db(), &entry))
        .await
        .unwrap_or_default();
    Json(json!({"ok": true, "history": history})).into_response()
}

fn get_or(payload: &Value, key: &str, default: Value) -> Value {
    payload
        .as_object()
        .and_then(|o| o.get(key))
        .cloned()
        .unwrap_or(default)
}

/// Marks a film, an episode or a whole season as watched, or not watched any more.
pub async fn toggle_watched(s: &Shared, payload: &Value) -> Vec<Value> {
    let db = s.net.db();
    let history = library::history(db);
    let id = payload["id"].as_str().unwrap_or("").trim().to_string();
    if id.is_empty() {
        return history;
    }
    let kind = get_or(payload, "type", json!("movie"));
    let force = truthy(payload.get("force_watched"));
    let existing = history
        .iter()
        .find(|x| x["id"].as_str() == Some(&id))
        .cloned();
    let series = kind == json!("series");
    let season_all = series && truthy(payload.get("season")) && truthy(payload.get("season_all"));
    let one_episode = series && truthy(payload.get("season")) && truthy(payload.get("episode"));
    if season_all || one_episode {
        let s_num = whole(payload.get("season"));
        let mut episodes: Vec<i64> = if season_all {
            payload["episodes"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|x| whole(Some(x)))
                .filter(|e| *e > 0)
                .collect()
        } else {
            vec![whole(payload.get("episode"))]
        };
        if season_all && episodes.is_empty() {
            episodes = (1..=10).collect();
        }
        let mut sorted = episodes.clone();
        sorted.sort_unstable();
        sorted.dedup();
        let codes: Vec<String> = sorted
            .iter()
            .map(|e| format!("S{s_num:02}E{e:02}"))
            .collect();
        let mut merged: Map<String, Value> = match &existing {
            Some(found) => found.as_object().cloned().unwrap_or_default(),
            None => {
                let mut m = Map::new();
                m.insert("id".into(), json!(id));
                m.insert("name".into(), get_or(payload, "name", json!("")));
                m.insert("type".into(), json!("series"));
                m.insert("year".into(), get_or(payload, "year", json!("")));
                m.insert("poster".into(), get_or(payload, "poster", json!("")));
                m
            }
        };
        let mut watched: Vec<String> = Vec::new();
        for code in merged
            .get("watched_episodes")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let code = library::normalize_episode_code(code.as_str().unwrap_or(&code.to_string()));
            if !watched.contains(&code) {
                watched.push(code);
            }
        }
        let mut positions: Map<String, Value> = merged
            .get("ep_positions")
            .and_then(Value::as_object)
            .map(|o| {
                o.iter()
                    .map(|(k, v)| (library::normalize_episode_code(k), v.clone()))
                    .collect()
            })
            .unwrap_or_default();
        let all_watched = codes.iter().all(|c| watched.contains(c));
        let same_place = if season_all {
            merged.get("season").and_then(Value::as_i64) == Some(s_num)
        } else {
            merged.get("season").and_then(Value::as_i64) == Some(s_num)
                && merged.get("episode").and_then(Value::as_i64) == Some(episodes[0])
        };
        if all_watched && !force {
            watched.retain(|c| !codes.contains(c));
            for c in &codes {
                positions.remove(c);
            }
            if same_place {
                merged.insert("completed".into(), json!(false));
                merged.insert("progress_pct".into(), json!(0));
                merged.insert("position".into(), json!(0));
                merged.insert("series_finished".into(), json!(false));
            }
        } else {
            for c in &codes {
                if !watched.contains(c) {
                    watched.push(c.clone());
                }
                positions.insert(c.clone(), json!({"pos": 2700, "dur": 2700, "pct": 100.0}));
            }
            let last = *episodes.iter().max().unwrap_or(&1);
            merged.insert("season".into(), json!(s_num));
            merged.insert("episode".into(), json!(last));
            merged.insert("completed".into(), json!(true));
            merged.insert("progress_pct".into(), json!(100.0));
            merged.insert("position".into(), json!(2700));
            merged.insert("duration".into(), json!(2700));
            let (next_season, next_episode, done) =
                s.meta.next_episode(&id, s_num, last, true).await;
            merged.insert("next_season".into(), json!(next_season));
            merged.insert("next_episode".into(), json!(next_episode));
            merged.insert("series_finished".into(), json!(done));
        }
        let keep = watched.len().saturating_sub(300);
        merged.insert("watched_episodes".into(), json!(watched[keep..]));
        merged.insert("ep_positions".into(), Value::Object(positions));
        merged.insert("updated_at".into(), json!(crate::db::now() as i64));
        return library::record_history(db, &merged);
    }
    // A film, or a whole series.
    let done = existing.as_ref().is_some_and(|x| {
        truthy(x.get("completed"))
            || x.get("progress_pct")
                .and_then(|p| {
                    p.as_f64()
                        .or_else(|| p.as_str().and_then(|s| s.parse().ok()))
                })
                .unwrap_or(0.0)
                >= 85.0
    });
    if done && !force {
        return library::remove_history(db, &id);
    }
    let mut entry = Map::new();
    entry.insert("id".into(), json!(id));
    for key in ["name", "year", "poster", "imdbRating"] {
        entry.insert(key.into(), get_or(payload, key, json!("")));
    }
    entry.insert("type".into(), kind);
    entry.insert("position".into(), json!(7200.0));
    entry.insert("duration".into(), json!(7200.0));
    // Same order of keys as the Python version.
    let ordered: Map<String, Value> = [
        "id",
        "name",
        "type",
        "year",
        "poster",
        "imdbRating",
        "position",
        "duration",
    ]
    .iter()
    .filter_map(|k| entry.get(*k).map(|v| (k.to_string(), v.clone())))
    .collect();
    library::record_history(db, &ordered)
}

async fn watched_toggle(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let history = toggle_watched(&s, &body).await;
    Json(json!({"ok": true, "history": history})).into_response()
}
