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

async fn history_record(State(s): State<Shared>, Json(body): Json<Value>) -> Response {
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
