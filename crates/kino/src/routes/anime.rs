//! The Anime tab (rows, week planning, opening a work) and the « Parcours » of a franchise.

use std::collections::HashMap;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{Value, json};

use crate::anime_journey::{self as journey, Invalid};
use crate::body::JsonBody;
use crate::server::Shared;

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/anime/home", get(home))
        .route("/api/anime/week", get(week))
        .route("/api/anime/open", post(open))
        .route("/api/anime/journey", get(get_journey))
        .route("/api/anime/journey/resolve", post(resolve))
        .route("/api/anime/journey/order", post(order))
}

fn answer(result: Result<Value, Invalid>, refusal: &str) -> Response {
    match result {
        Ok(v) => Json(v).into_response(),
        Err(Invalid) => (StatusCode::BAD_REQUEST, Json(json!({"error": refusal}))).into_response(),
    }
}

/// A body field, with Python's `body.get(key, default)`.
fn field(body: &Value, key: &str, default: Value) -> Value {
    body.get(key).cloned().unwrap_or(default)
}

async fn home(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let force = p.get("force").map(String::as_str) == Some("1");
    Json(crate::anime_home::home(&s.meta, force).await).into_response()
}

async fn week(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let force = p.get("force").map(String::as_str) == Some("1");
    Json(crate::anime_home::week(&s.meta, force).await).into_response()
}

async fn open(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    answer(
        crate::anime_home::open_work(&s.meta, &body["id"]).await,
        "Œuvre anime invalide.",
    )
}

async fn get_journey(
    State(s): State<Shared>,
    Query(p): Query<HashMap<String, String>>,
) -> Response {
    let param = |k: &str| p.get(k).map(|v| json!(v)).unwrap_or(Value::Null);
    let kind = p.get("type").map(|v| json!(v)).unwrap_or(json!("series"));
    let force = p.get("refresh").map(String::as_str) == Some("1");
    let found = journey::get_journey(
        &s.meta,
        &param("imdb_id"),
        &param("title"),
        &param("year"),
        &kind,
        force,
    )
    .await;
    answer(found, "Œuvre anime invalide.")
}

async fn resolve(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let kind = field(&body, "type", json!("series"));
    let found =
        journey::resolve_member(&s.meta, &body["imdb_id"], &kind, &body["anilist_id"]).await;
    answer(found, "Œuvre du parcours invalide.")
}

/// Python's int(): whole numbers, their text, bools; floats cut.
fn py_int(v: &Value) -> Option<i64> {
    match v {
        Value::Bool(b) => Some(*b as i64),
        Value::Number(n) => n.as_i64().or_else(|| {
            n.as_f64()
                .filter(|f| f.is_finite())
                .map(|f| f.trunc() as i64)
        }),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

async fn order(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let db = s.meta.net.db();
    let kind = field(&body, "type", json!("series"));
    let result = match body["action"].as_str() {
        Some("reset") => (|| {
            let current = journey::cached_journey(db, &body["imdb_id"], &kind)?;
            if current["saga_id"].as_i64() != Some(py_int(&body["saga_id"]).ok_or(Invalid)?) {
                return Err(Invalid);
            }
            journey::reset_order(db, &body["saga_id"])?;
            journey::cached_journey(db, &body["imdb_id"], &kind)
        })(),
        Some("save") => {
            journey::save_order(db, &body["imdb_id"], &kind, &body["saga_id"], &body["ids"])
        }
        _ => Err(Invalid),
    };
    answer(result, "Ordre du parcours invalide.")
}
