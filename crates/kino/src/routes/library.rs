//! Ma Liste, the history and small per-series preferences.

use std::collections::HashMap;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{Value, json};

use crate::body::JsonBody;
use crate::server::Shared;
use crate::{library, prefs};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/watchlist", post(watchlist))
        .route("/api/history-remove", post(history_remove))
        .route("/api/rating", post(rating))
        .route(
            "/api/spoiler-protection",
            get(spoilers).post(update_spoilers),
        )
        .route(
            "/api/series-playback-preference",
            get(playback).post(update_playback),
        )
}

fn refused(text: &str) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({"error": text}))).into_response()
}

async fn watchlist(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    Json(json!({"ok": true, "watchlist": library::toggle_watchlist(&s.db, &body)})).into_response()
}

async fn rating(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let id = match &body["id"] {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(t) => t.clone(),
        other => other.to_string(),
    };
    let empty = serde_json::Map::new();
    let fields = match library::rating_patch(body.as_object().unwrap_or(&empty), crate::db::now()) {
        Ok(f) => f,
        Err(e) => return refused(e),
    };
    if id.is_empty() || id.chars().count() > 300 || !library::patch_history(&s.db, &id, &fields) {
        let missing = json!({"error": "Ce titre n'est pas dans l'historique."});
        return (StatusCode::NOT_FOUND, Json(missing)).into_response();
    }
    Json(json!({"ok": true, "id": id, "fields": fields})).into_response()
}

async fn history_remove(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let id = body["id"].as_str().unwrap_or("");
    Json(json!({"ok": true, "history": library::remove_history(&s.db, id)})).into_response()
}

async fn spoilers(State(s): State<Shared>) -> Response {
    Json(prefs::spoilers(&s.db)).into_response()
}

async fn update_spoilers(State(s): State<Shared>, JsonBody(b): JsonBody) -> Response {
    let action = b["action"].as_str().unwrap_or("");
    match prefs::update_spoilers(
        &s.db,
        action,
        &b["enabled"],
        &b["series_id"],
        &b["season"],
        &b["episode"],
    ) {
        Ok(state) => Json(state).into_response(),
        Err(_) => refused("Réglage anti-spoilers invalide."),
    }
}

async fn playback(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let id = p.get("series_id").map_or(Value::Null, |v| json!(v));
    match prefs::playback(&s.db, &id) {
        Ok(preference) => Json(json!({"preference": preference})).into_response(),
        Err(_) => refused("Identifiant de série invalide."),
    }
}

async fn update_playback(State(s): State<Shared>, JsonBody(b): JsonBody) -> Response {
    let result = match b["action"].as_str() {
        Some("clear") => prefs::clear_playback(&s.db, &b["series_id"]).map(|_| Value::Null),
        Some("save") => prefs::save_playback(&s.db, &b["series_id"], &b["preference"]),
        _ => Err(prefs::Invalid),
    };
    match result {
        Ok(preference) => Json(json!({"ok": true, "preference": preference})).into_response(),
        Err(_) => refused("Préférence de lecture invalide."),
    }
}
