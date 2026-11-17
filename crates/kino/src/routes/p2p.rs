//! Peer-to-peer playback: the user's consent and choices, and the files of
//! the torrents being read, served to the player with byte ranges. Answered
//! here when the window runs in this process; behind the Python window, by Python.

use axum::Json;
use axum::Router;
use axum::body::Body;
use axum::extract::{Path, Request, State};
use axum::http::{Method, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use serde_json::{Value, json};
use tokio::io::{AsyncReadExt, AsyncSeekExt};

use crate::server::{Shared, delegate};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/p2p", get(state))
        .route("/api/p2p/consent", post(consent))
        .route("/api/p2p/settings", post(settings))
        .route("/api/p2p/clear", post(clear))
        .route("/p2p/{hash}/{index}", get(file).head(file))
}

async fn delegated(s: &Shared, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let body = axum::body::to_bytes(body, 1 << 20)
        .await
        .unwrap_or_default();
    delegate(s, parts.method, &parts.uri, &parts.headers, body).await
}

async fn json_body(request: Request) -> Value {
    let body = axum::body::to_bytes(request.into_body(), 1 << 20)
        .await
        .unwrap_or_default();
    serde_json::from_slice(&body).unwrap_or_else(|_| json!({}))
}

async fn state(State(s): State<Shared>, request: Request) -> Response {
    let Some(p2p) = s.p2p.clone() else {
        return delegated(&s, request).await;
    };
    Json(
        tokio::task::spawn_blocking(move || p2p.state())
            .await
            .unwrap_or(json!({})),
    )
    .into_response()
}

async fn consent(State(s): State<Shared>, request: Request) -> Response {
    let Some(p2p) = s.p2p.clone() else {
        return delegated(&s, request).await;
    };
    let body = json_body(request).await;
    if let Err(e) = p2p
        .set_consent(body["accept"] == true, &body["upload"])
        .await
    {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e}))).into_response();
    }
    Json(p2p.state()).into_response()
}

async fn settings(State(s): State<Shared>, request: Request) -> Response {
    let Some(p2p) = s.p2p.clone() else {
        return delegated(&s, request).await;
    };
    let body = json_body(request).await;
    let changes: serde_json::Map<String, Value> = ["upload", "download", "encrypted", "port"]
        .iter()
        .filter_map(|k| body.get(*k).map(|v| (k.to_string(), v.clone())))
        .collect();
    match p2p.save_settings(&Value::Object(changes)).await {
        Ok(_) => Json(p2p.state()).into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({"error": e}))).into_response(),
    }
}

async fn clear(State(s): State<Shared>, request: Request) -> Response {
    let Some(p2p) = s.p2p.clone() else {
        return delegated(&s, request).await;
    };
    p2p.clear_cache().await;
    Json(p2p.state()).into_response()
}

/// One file of a torrent, piece by piece as it arrives, from the asked range.
async fn file(
    State(s): State<Shared>,
    Path((hash, index)): Path<(String, String)>,
    request: Request,
) -> Response {
    let Some(p2p) = s.p2p.clone() else {
        return delegated(&s, request).await;
    };
    let valid = hash.len() == 40
        && hash
            .chars()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
        && index.len() <= 5;
    let Some(index) = index.parse::<usize>().ok().filter(|_| valid) else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let Some((mut stream, size, kind)) = p2p.open(&hash, index).await else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let range = request
        .headers()
        .get(header::RANGE)
        .and_then(|v| v.to_str().ok())
        .and_then(|r| crate::p2p::byte_range(r, size));
    if let Some((start, _)) = range
        && start >= size
    {
        return Response::builder()
            .status(StatusCode::RANGE_NOT_SATISFIABLE)
            .header(header::CONTENT_RANGE, format!("bytes */{size}"))
            .body(Body::empty())
            .unwrap_or_else(|_| StatusCode::NOT_FOUND.into_response());
    }
    let (start, end) = range.unwrap_or((0, size.saturating_sub(1)));
    let mut response = Response::builder()
        .status(if range.is_some() {
            StatusCode::PARTIAL_CONTENT
        } else {
            StatusCode::OK
        })
        .header(header::CONTENT_TYPE, kind)
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::CONTENT_LENGTH, end - start + 1);
    if range.is_some() {
        response = response.header(header::CONTENT_RANGE, format!("bytes {start}-{end}/{size}"));
    }
    if request.method() == Method::HEAD {
        return response
            .body(Body::empty())
            .unwrap_or_else(|_| StatusCode::NOT_FOUND.into_response());
    }
    if stream.seek(std::io::SeekFrom::Start(start)).await.is_err() {
        return StatusCode::NOT_FOUND.into_response();
    }
    let body =
        tokio_util::io::ReaderStream::with_capacity(stream.take(end - start + 1), 256 * 1024);
    response
        .body(Body::from_stream(body))
        .unwrap_or_else(|_| StatusCode::NOT_FOUND.into_response())
}
