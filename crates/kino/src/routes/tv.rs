//! « Lire sur la TV » and the stream the TV reads. Only the KINO app
//! casts: the bare server answers that it cannot.

use axum::Json;
use axum::Router;
use axum::body::Body;
use axum::extract::{Query, Request, State};
use axum::http::{Method, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use serde_json::{Map, Value, json};
use std::collections::HashMap;

use crate::play::text;
use crate::server::{Shared, needs_app};
use crate::tv::{DLNA_FEATURES, TvError};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/tv/devices", get(devices))
        .route("/api/tv/status", get(status))
        .route("/api/tv/device", post(choose))
        .route("/api/tv/play", post(play))
        .route("/api/tv/control", post(control))
        .route("/tv/stream/{*rest}", get(relay).head(relay))
}

async fn json_body(request: Request) -> Value {
    let body = axum::body::to_bytes(request.into_body(), 1 << 20)
        .await
        .unwrap_or_default();
    serde_json::from_slice(&body).unwrap_or_else(|_| json!({}))
}

fn failure(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"error": message}))).into_response()
}

fn answer(result: Result<Value, TvError>, refused: StatusCode, unreachable: &str) -> Response {
    match result {
        Ok(mut status) => {
            let mut out = Map::new();
            out.insert("ok".into(), json!(true));
            if let Some(fields) = status.as_object_mut() {
                out.append(fields);
            }
            Json(Value::Object(out)).into_response()
        }
        Err(TvError::Refused(message)) => failure(refused, &message),
        Err(TvError::Unreachable) => failure(StatusCode::GATEWAY_TIMEOUT, unreachable),
    }
}

async fn devices(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let Some(tv) = s.tv.clone() else {
        return needs_app();
    };
    let found = tv
        .devices(p.get("refresh").map(String::as_str) == Some("1"))
        .await;
    let chosen = tokio::task::spawn_blocking(crate::config::load)
        .await
        .ok()
        .and_then(|c| c.tv_device)
        .and_then(|d| serde_json::to_value(d).ok())
        .unwrap_or(json!({}));
    Json(json!({"devices": found, "chosen": chosen})).into_response()
}

async fn status(State(s): State<Shared>) -> Response {
    let Some(tv) = s.tv.clone() else {
        return needs_app();
    };
    Json(tv.status()).into_response()
}

async fn choose(State(s): State<Shared>, request: Request) -> Response {
    let Some(tv) = s.tv.clone() else {
        return needs_app();
    };
    let location = text(&json_body(request).await["location"]);
    let Some(device) = tv
        .devices(false)
        .await
        .into_iter()
        .find(|d| text(&d["location"]) == location)
    else {
        return failure(StatusCode::NOT_FOUND, "Cette TV n'est plus sur le réseau.");
    };
    let change = json!({"tv_device": device});
    let _ = tokio::task::spawn_blocking(move || crate::config::save(&change)).await;
    Json(json!({"ok": true, "chosen": device})).into_response()
}

async fn play(State(s): State<Shared>, request: Request) -> Response {
    let Some(tv) = s.tv.clone() else {
        return needs_app();
    };
    let body = json_body(request).await;
    let Some(device) = tv.chosen().await else {
        return failure(
            StatusCode::NOT_FOUND,
            "Aucune TV trouvée sur le réseau. Allumez-la, sur la même box que ce Mac.",
        );
    };
    let media = body["media"].as_object().cloned().unwrap_or_default();
    let resume = body["resume_sec"]
        .as_f64()
        .or_else(|| text(&body["resume_sec"]).trim().parse().ok())
        .unwrap_or(0.0);
    let started = tv
        .start(
            &device,
            &text(&body["stream_url"]),
            &text(&body["title"]),
            media,
            &text(&body["filename"]),
            resume,
        )
        .await;
    answer(
        started,
        StatusCode::BAD_GATEWAY,
        "La TV ne répond pas. Vérifiez qu'elle est allumée.",
    )
}

async fn control(State(s): State<Shared>, request: Request) -> Response {
    let Some(tv) = s.tv.clone() else {
        return needs_app();
    };
    let body = json_body(request).await;
    let done = tv.control(&text(&body["action"]), &body["value"]).await;
    answer(done, StatusCode::CONFLICT, "La TV ne répond pas.")
}

/// The TV asks KINO, KINO asks Debrid (https), Range passed through.
async fn relay(State(s): State<Shared>, request: Request) -> Response {
    let Some(tv) = s.tv.clone() else {
        return needs_app();
    };
    let peer = request
        .headers()
        .get("x-kino-peer")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .trim()
        .to_string();
    let Some((source, mime)) = tv.relay_allowed(&peer, request.uri().path()) else {
        return StatusCode::FORBIDDEN.into_response();
    };
    let head = request.method() == Method::HEAD;
    let mut upstream = s
        .http
        .request(if head { Method::HEAD } else { Method::GET }, &source)
        .header("User-Agent", "KINO");
    if let Some(range) = request.headers().get(header::RANGE) {
        upstream = upstream.header(header::RANGE, range);
    }
    let Ok(answer) = upstream.send().await else {
        return StatusCode::BAD_GATEWAY.into_response();
    };
    let mut response = Response::builder()
        .status(answer.status().as_u16())
        .header(header::CONTENT_TYPE, mime);
    for name in [
        header::CONTENT_LENGTH,
        header::CONTENT_RANGE,
        header::ACCEPT_RANGES,
    ] {
        if let Some(value) = answer.headers().get(&name) {
            response = response.header(name, value);
        }
    }
    response = response
        .header("transferMode.dlna.org", "Streaming")
        .header("contentFeatures.dlna.org", DLNA_FEATURES);
    let body = if head {
        Body::empty()
    } else {
        Body::from_stream(answer.bytes_stream())
    };
    response
        .body(body)
        .unwrap_or_else(|_| StatusCode::BAD_GATEWAY.into_response())
}
