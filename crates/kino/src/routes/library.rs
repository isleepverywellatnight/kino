//! Ma Liste, the history and small per-series preferences.
//! Recording a play (/api/history-record, /api/watched-toggle) still goes to
//! the Python server: it needs the next episode from the metadata engine.

use std::collections::HashMap;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{Value, json};

use crate::server::Shared;
use crate::{library, prefs};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/watchlist", post(watchlist))
        .route("/api/history-remove", post(history_remove))
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

async fn watchlist(State(s): State<Shared>, Json(body): Json<Value>) -> Response {
    Json(json!({"ok": true, "watchlist": library::toggle_watchlist(&s.db, &body)})).into_response()
}

async fn history_remove(State(s): State<Shared>, Json(body): Json<Value>) -> Response {
    let id = body["id"].as_str().unwrap_or("");
    Json(json!({"ok": true, "history": library::remove_history(&s.db, id)})).into_response()
}

async fn spoilers(State(s): State<Shared>) -> Response {
    Json(prefs::spoilers(&s.db)).into_response()
}

async fn update_spoilers(State(s): State<Shared>, Json(b): Json<Value>) -> Response {
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

async fn update_playback(State(s): State<Shared>, Json(b): Json<Value>) -> Response {
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
