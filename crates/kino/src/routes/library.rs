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
        .route("/api/tonight", post(tonight))
        .route("/api/tonight/feedback", post(tonight_feedback))
        .route("/api/tonight/undo", post(tonight_undo))
        .route("/api/tonight/reset", post(tonight_reset))
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

/// Python's int(value or 0) for the minutes asked; None when it would fail.
fn whole(v: &Value) -> Option<i64> {
    match v {
        Value::Null => Some(0),
        Value::Bool(b) => Some(*b as i64),
        Value::Number(n) => n.as_i64().or_else(|| {
            n.as_f64()
                .filter(|f| f.is_finite())
                .map(|f| f.trunc() as i64)
        }),
        Value::String(s) if s.is_empty() => Some(0),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// « Ce soir »: a few titles of Ma Liste for the time and mood available.
async fn tonight(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    use crate::tonight;
    let minutes = whole(&body["minutes"]);
    let mood = match &body["mood"] {
        Value::Null | Value::Bool(false) => "discovery".to_string(),
        Value::String(m) if m.is_empty() => "discovery".to_string(),
        Value::String(m) => m.clone(),
        other => other.to_string(),
    };
    let offline_only = body["offline_only"] == true;
    let Some(minutes) = minutes.filter(|m| {
        (15..=600).contains(m) && ["light", "intense", "discovery"].contains(&mood.as_str())
    }) else {
        return refused("Choisissez une durée et une ambiance valides.");
    };
    let (watchlist, coverage) = tonight::enrich(
        &s.meta,
        library::watchlist(&s.db),
        if offline_only { 0 } else { 36 },
    )
    .await;
    let downloads = tonight::offline_downloads(&s.db);
    let feedback = tonight::feedback_state(&s.db);
    let history = library::history(&s.db);
    let at = crate::db::now();
    let choices = tonight::choose(
        &watchlist,
        &history,
        minutes,
        &mood,
        &feedback,
        at,
        &downloads,
        offline_only,
    );
    let reason = tonight::empty_reason(
        &watchlist,
        &history,
        &feedback,
        &downloads,
        offline_only,
        &choices,
        at,
    );
    tonight::record_impressions(&s.db, &choices);
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
    Json(json!({"ok": true, "choices": choices, "coverage": coverage, "offline_only": offline_only, "empty_reason": reason, "review": review})).into_response()
}

async fn tonight_feedback(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let id = match &body["id"] {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(t) => t.clone(),
        other => other.to_string(),
    };
    match crate::tonight::apply_feedback(&s.db, &id, &body["action"]) {
        Ok(token) => Json(json!({"ok": true, "undo_token": token})).into_response(),
        Err(_) => refused("Ce retour ne peut pas être enregistré."),
    }
}

async fn tonight_undo(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    match crate::tonight::undo_feedback(&s.db, &body["id"], &body["undo_token"]) {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(_) => (
            StatusCode::CONFLICT,
            Json(json!({"error": "Ce retour ne peut plus être annulé."})),
        )
            .into_response(),
    }
}

async fn tonight_reset(State(s): State<Shared>) -> Response {
    crate::tonight::reset_feedback(&s.db);
    Json(json!({"ok": true})).into_response()
}
