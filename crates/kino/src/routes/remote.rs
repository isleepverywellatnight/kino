//! The phone remote and the page player's state. Only the KINO app
//! (kino-desktop) answers: its TV cast and media keys share that state.

use axum::Json;
use axum::Router;
use axum::extract::{Request, State};
use axum::http::header;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use serde_json::{Value, json};

use crate::server::{Shared, needs_app};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/remote", get(page))
        .route("/api/remote/state", get(state))
        .route("/api/remote/control", post(control))
        .route("/api/remote/info", get(info))
        .route("/api/remote/qr", get(info))
        .route("/api/player/state", post(player_state))
        .route("/api/player/commands", get(commands))
        .route("/api/diagnostic", get(diagnostic))
}

/// Python's answer while the window is Python's.
async fn json_body(request: Request) -> Value {
    let body = axum::body::to_bytes(request.into_body(), 1 << 20)
        .await
        .unwrap_or_default();
    serde_json::from_slice(&body).unwrap_or_else(|_| json!({}))
}

async fn page(State(s): State<Shared>) -> Response {
    if s.player.is_none() {
        return needs_app();
    }
    match tokio::fs::read_to_string(s.web_root.join("remote.html")).await {
        Ok(html) => ([(header::CONTENT_TYPE, "text/html; charset=utf-8")], html).into_response(),
        Err(_) => (
            axum::http::StatusCode::NOT_FOUND,
            Json(json!({"error": "Route introuvable"})),
        )
            .into_response(),
    }
}

async fn state(State(s): State<Shared>) -> Response {
    if s.player.is_none() {
        return needs_app();
    }
    Json(s.remote.state_for_phone(s.port)).into_response()
}

async fn control(State(s): State<Shared>, request: Request) -> Response {
    if s.player.is_none() {
        return needs_app();
    }
    let body = json_body(request).await;
    let action = body.get("action").cloned().unwrap_or(json!(""));
    s.remote
        .push(&action, body.get("value").unwrap_or(&Value::Null));
    Json(json!({"ok": true, "action": action})).into_response()
}

async fn info(State(s): State<Shared>) -> Response {
    if s.player.is_none() {
        return needs_app();
    }
    let ip = crate::remote::local_ip();
    let url = format!("http://{ip}:{}/remote?token={}", s.port, s.pairing_token);
    Json(json!({"status": "ok", "url": url, "local_ip": ip, "port": s.port})).into_response()
}

async fn player_state(State(s): State<Shared>, request: Request) -> Response {
    if s.player.is_none() {
        return needs_app();
    }
    s.remote.update(&json_body(request).await);
    Json(json!({"ok": true})).into_response()
}

async fn commands(State(s): State<Shared>) -> Response {
    if s.player.is_none() {
        return needs_app();
    }
    Json(json!({"commands": s.remote.pop()})).into_response()
}

/// Diagnostic: Python's while the window (native player, notifications) is Python's.
async fn diagnostic(State(s): State<Shared>) -> Response {
    if s.player.is_none() {
        return needs_app();
    }
    let window = crate::diagnostic::Window {
        native_player: s.native_player,
        notifications: s.notifier.as_ref().map(|n| n.status()),
    };
    Json(crate::diagnostic::run(&s, &window).await).into_response()
}
