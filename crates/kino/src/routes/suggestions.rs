//! « Pour vous » and « Vous aimerez aussi »: KINO's own suggestions, and
//! TMDB's recommendations when the user gave a TMDB key. The pool itself is
//! still read from Cinemeta by the Python side at startup.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, LazyLock};
use std::time::Duration;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::StreamExt;
use indexmap::IndexMap;
use regex::Regex;
use serde_json::{Value, json};

use crate::body::JsonBody;
use crate::db::now;
use crate::meta::Meta;
use crate::recommender::{self, POOL_READY, Space, Store};
use crate::server::Shared;

static IMDB: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^tt\d{1,12}$").unwrap());

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/for-you", get(for_you))
        .route("/api/related", get(related))
        .route("/api/for-you/not-for-me", post(not_for_me))
        .route("/api/for-you/reset", post(reset))
        .route("/api/for-you/status", get(status))
}

fn text(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn kind_of(media_type: &str) -> &'static str {
    if media_type == "series" || media_type == "anime" {
        "series"
    } else {
        "movie"
    }
}

/// The user's titles as pool entries; those missing (40 at most) are read once.
async fn known_titles(
    s: &Shared,
    space: &Space,
    ids: &[String],
    kinds: &HashMap<String, &'static str>,
) -> IndexMap<String, Value> {
    let mut known: IndexMap<String, Value> = ids
        .iter()
        .filter_map(|i| space.entries.get(i).map(|e| (i.clone(), e.clone())))
        .collect();
    let missing: Vec<(String, bool)> = ids
        .iter()
        .filter(|i| !known.contains_key(*i))
        .take(40)
        .map(|i| (i.clone(), kinds.get(i).copied() == Some("series")))
        .collect();
    if missing.is_empty() {
        return known;
    }
    let fetched: Vec<Value> =
        futures_util::stream::iter(missing.into_iter().map(|(id, is_series)| {
            let meta: Meta = s.meta.clone();
            async move {
                let kind = if is_series { "series" } else { "movie" };
                let found = meta.media(&id, kind).await.ok()?;
                if matches!(&found["name"], Value::Null | Value::Bool(false))
                    || found["name"] == ""
                    || found["name"] == 0
                {
                    return None;
                }
                let mut record = found.as_object().cloned().unwrap_or_default();
                record.insert("imdb_id".into(), json!(id));
                recommender::compact(&Value::Object(record), kind)
            }
        }))
        .buffered(6)
        .filter_map(|e| async move { e })
        .collect()
        .await;
    if !fetched.is_empty() {
        s.suggestions.add_known(&s.db, &fetched, now());
        known.extend(fetched.into_iter().map(|e| (text(&e["id"]), e)));
    }
    known
}

fn user_kinds(history: &[Value], watchlist: &[Value]) -> HashMap<String, &'static str> {
    history
        .iter()
        .chain(watchlist)
        .map(|e| (text(&e["id"]), kind_of(e["type"].as_str().unwrap_or(""))))
        .collect()
}

/// The pool, read on a blocking thread: its first read parses thousands of titles.
async fn pool(s: &Shared) -> Option<Arc<Space>> {
    let s = s.clone();
    tokio::task::spawn_blocking(move || s.suggestions.space(&s.db))
        .await
        .ok()
}

async fn for_you(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let kind = kind_of(p.get("type").map(String::as_str).unwrap_or(""));
    let Some(space) = pool(&s).await else {
        return Json(json!({"ready": false, "items": [], "pool": 0})).into_response();
    };
    if space.entries.len() < POOL_READY {
        return Json(json!({"ready": false, "items": [], "pool": space.entries.len()}))
            .into_response();
    }
    let (history, watchlist) = (
        crate::library::history(&s.db),
        crate::library::watchlist(&s.db),
    );
    let signals =
        recommender::taste_signals(&history, &watchlist, &Store::not_for_me(&s.db), now());
    let ids: Vec<String> = signals.keys().cloned().collect();
    let known = known_titles(&s, &space, &ids, &user_kinds(&history, &watchlist)).await;
    let Some(space) = pool(&s).await else {
        return Json(json!({"ready": false, "items": [], "pool": 0})).into_response();
    };
    let count = space.entries.len();
    // The ranking takes milliseconds of computing: off the threads that answer requests.
    let items = tokio::task::spawn_blocking(move || {
        recommender::for_you(&space, &signals, &known, kind, 20)
    })
    .await
    .unwrap_or_default();
    Json(json!({"ready": true, "items": items, "pool": count})).into_response()
}

/// Titles close to one title by KINO's own traits; none until the pool is ready.
async fn kino_related(s: &Shared, imdb_id: &str, kind: &'static str) -> Vec<Value> {
    let Some(space) = pool(s).await else {
        return vec![];
    };
    if space.entries.len() < POOL_READY {
        return vec![];
    }
    let kinds = HashMap::from([(imdb_id.to_string(), kind)]);
    let known = known_titles(s, &space, &[imdb_id.to_string()], &kinds).await;
    let history = crate::library::history(&s.db);
    let truthy = |v: &Value| {
        !matches!(v, Value::Null | Value::Bool(false)) && v != &json!(0) && v != &json!("")
    };
    let mut exclude: HashSet<String> = history
        .iter()
        .filter(|e| truthy(&e["completed"]) || truthy(&e["imported_watched"]))
        .map(|e| text(&e["id"]))
        .collect();
    exclude.extend(Store::not_for_me(&s.db));
    let Some(space) = pool(s).await else {
        return vec![];
    };
    let source = known.get(imdb_id).cloned();
    tokio::task::spawn_blocking(move || {
        recommender::related(&space, source.as_ref(), kind, &exclude, 12)
    })
    .await
    .unwrap_or_default()
}

async fn related(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let imdb_id = p
        .get("imdb_id")
        .map(|i| i.trim().to_string())
        .unwrap_or_default();
    if !IMDB.is_match(&imdb_id) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Identifiant de titre invalide."})),
        )
            .into_response();
    }
    let kind = kind_of(p.get("type").map(String::as_str).unwrap_or(""));
    let cfg = tokio::task::spawn_blocking(crate::config::load)
        .await
        .unwrap_or_default();
    let key = cfg.tmdb_key().to_string();
    if !key.is_empty()
        && let Ok(items) = tmdb_related(&s.meta, &imdb_id, kind, &key).await
        && items.len() >= 4
    {
        return Json(json!({"source": "tmdb", "items": items})).into_response();
    }
    let items = kino_related(&s, &imdb_id, kind).await;
    if items.len() >= 4 {
        return Json(json!({"source": "kino", "items": items})).into_response();
    }
    let similar = s
        .meta
        .media(&imdb_id, kind)
        .await
        .ok()
        .map(|m| m["similar"].clone())
        .filter(|v| v.is_array())
        .unwrap_or(json!([]));
    Json(json!({"source": "genres", "items": similar})).into_response()
}

async fn not_for_me(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let id = match &body["id"] {
        Value::Null | Value::Bool(false) => String::new(),
        other => text(other),
    };
    if !IMDB.is_match(&id) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Identifiant de titre invalide."})),
        )
            .into_response();
    }
    Store::set_not_for_me(&s.db, &id, now());
    Json(json!({"ok": true})).into_response()
}

async fn reset(State(s): State<Shared>) -> Response {
    Store::reset_feedback(&s.db);
    Json(json!({"ok": true})).into_response()
}

// ---------- TMDB ----------

const TMDB: &str = "https://api.themoviedb.org/3";

/// A read access token (v4, a JWT) goes in a header; an API key (v3) in the address.
async fn tmdb_get(
    meta: &Meta,
    path: &str,
    key: &str,
    params: &[(&str, &str)],
) -> Result<Value, crate::net::NetError> {
    let key = key.trim();
    let bearer = format!("Bearer {key}");
    // The serializer is not Send: the address is built before the request.
    let mut headers = vec![("Accept", "application/json"), ("User-Agent", "KINO")];
    let url = {
        let mut query = form_urlencoded::Serializer::new(String::new());
        for (k, v) in params {
            query.append_pair(k, v);
        }
        if key.starts_with("eyJ") {
            headers.push(("Authorization", bearer.as_str()));
        } else {
            query.append_pair("api_key", key);
        }
        format!("{TMDB}{path}?{}", query.finish())
    };
    meta.net
        .request_json(
            reqwest::Method::GET,
            &url,
            &headers,
            None,
            Duration::from_secs(6),
            0,
        )
        .await
}

/// Up to 12 titles close to this one from TMDB's recommendations (then « similar »), as KINO's cards.
async fn tmdb_related(
    meta: &Meta,
    imdb_id: &str,
    kind: &str,
    key: &str,
) -> Result<Vec<Value>, crate::net::NetError> {
    let tv = kind == "series";
    let cache_key = format!(
        "tmdb_related_v1:{}:{imdb_id}",
        if tv { "tv" } else { "movie" }
    );
    let found = meta
        .net
        .cached(&cache_key, 7.0 * 86400.0, || async {
            let find = tmdb_get(meta, &format!("/find/{imdb_id}"), key, &[("external_source", "imdb_id")]).await?;
            let groups = if tv { ["tv_results", "movie_results"] } else { ["movie_results", "tv_results"] };
            let source = groups.iter().find_map(|g| {
                find[*g].as_array().into_iter().flatten().find_map(|i| i["id"].as_i64().filter(|v| *v != 0)).map(|id| (id, *g == "tv_results"))
            });
            let Some((source_id, is_tv)) = source else { return Ok(json!([])) };
            let kind = if is_tv { "tv" } else { "movie" };
            let params = [("language", "en-US"), ("page", "1")];
            let mut results: Vec<Value> = tmdb_get(meta, &format!("/{kind}/{source_id}/recommendations"), key, &params).await?["results"]
                .as_array()
                .cloned()
                .unwrap_or_default();
            if results.len() < 8 {
                let seen: Vec<Value> = results.iter().map(|r| r["id"].clone()).collect();
                let similar = tmdb_get(meta, &format!("/{kind}/{source_id}/similar"), key, &params).await?;
                results.extend(similar["results"].as_array().into_iter().flatten().filter(|r| !seen.contains(&r["id"])).cloned());
            }
            let truthy = |v: &Value| !matches!(v, Value::Null | Value::Bool(false)) && v != &json!(0) && v != &json!("");
            results.retain(|r| truthy(&r["id"]) && !truthy(&r["adult"]));
            results.truncate(18);
            let meta_owned = meta.clone();
            let key_owned = key.to_string();
            let rids: Vec<i64> = results.iter().map(|r| r["id"].as_i64().unwrap_or(0)).collect();
            let ids: Vec<String> = futures_util::stream::iter(rids.into_iter().map(|rid| {
                let (meta, key) = (meta_owned.clone(), key_owned.clone());
                async move {
                    let cache = format!("tmdb_imdb:{kind}:{rid}");
                    meta.net
                        .cached(&cache, 90.0 * 86400.0, || async {
                            let ids = tmdb_get(&meta, &format!("/{kind}/{rid}/external_ids"), &key, &[]).await?;
                            Ok(json!(ids["imdb_id"].as_str().unwrap_or("")))
                        })
                        .await
                        .map(|v| text(&v))
                        .unwrap_or_default()
                }
            }))
            .buffered(6)
            .collect()
            .await;
            let mut items: Vec<Value> = Vec::new();
            for (result, rid) in results.iter().zip(ids) {
                if rid.is_empty() || rid == imdb_id || items.iter().any(|i| i["id"] == rid.as_str()) {
                    continue;
                }
                let votes = result["vote_count"].as_f64().unwrap_or(0.0) as i64;
                let rating = result["vote_average"].as_f64().unwrap_or(0.0);
                let date = [&result["release_date"], &result["first_air_date"]].into_iter().find(|v| truthy(v)).map(text).unwrap_or_default();
                let year: String = date.chars().take(4).collect();
                let poster = match result["poster_path"].as_str().filter(|p| !p.is_empty()) {
                    Some(path) => format!("https://image.tmdb.org/t/p/w342{path}"),
                    None => format!("https://images.metahub.space/poster/medium/{rid}/img"),
                };
                let name = [&result["title"], &result["name"]].into_iter().find(|v| truthy(v)).cloned().unwrap_or(json!(""));
                items.push(json!({
                    "id": rid, "name": name, "type": if is_tv { "series" } else { "movie" },
                    "year": if !year.is_empty() && year.chars().all(|c| c.is_ascii_digit()) { year } else { String::new() },
                    "poster": poster,
                    "imdbRating": if votes >= 20 && rating != 0.0 { format!("{rating:.1}") } else { String::new() },
                }));
                if items.len() >= 12 {
                    break;
                }
            }
            Ok(Value::Array(items))
        })
        .await?;
    Ok(found.as_array().cloned().unwrap_or_default())
}

async fn status(State(s): State<Shared>) -> Response {
    let (count, age) = s.suggestions.pool_age(&s.db, now());
    let updated = age.map(|a| now() - a);
    Json(json!({"pool": count, "updated_at": updated, "not_for_me": Store::not_for_me(&s.db).len(), "building": recommender::building()}))
        .into_response()
}
