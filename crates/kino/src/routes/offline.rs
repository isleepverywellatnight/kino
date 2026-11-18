//! The offline library: its list, new downloads (a film, a season), pause,
//! resume, restart and removal, and the files served back to the player.

use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, Method, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{Map, Value, json};
use tokio::io::{AsyncReadExt, AsyncSeekExt};

use crate::body::JsonBody;
use crate::offline::{DOWNLOAD_ID, Library, OfflineError};
use crate::server::Shared;

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/downloads", get(list))
        .route("/api/download", post(create))
        .route("/api/download-season", post(season))
        .route("/api/download-cancel", post(pause))
        .route("/api/download-resume", post(resume))
        .route("/api/download-restart", post(restart))
        .route("/api/download-remove", post(remove))
        .route("/api/offline/{kind}/{id}", get(asset).head(asset))
}

fn refused(error: OfflineError) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(json!({"error": error.message, "code": error.code})),
    )
        .into_response()
}

fn download_dir() -> String {
    Some(crate::config::load().download_dir)
        .filter(|d| !d.is_empty())
        .unwrap_or_else(crate::config::default_download_dir)
}

fn id_of(body: &Value) -> String {
    match &body["dl_id"] {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

async fn list(State(s): State<Shared>) -> Response {
    let library = s.offline.clone();
    let answer = tokio::task::spawn_blocking(move || {
        let dir = download_dir();
        json!({"downloads": library.list(), "used_bytes": library.usage_bytes(), "free_bytes": Library::free_bytes(&dir)})
    })
    .await
    .unwrap_or_default();
    Json(answer).into_response()
}

async fn create(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let dir = tokio::task::spawn_blocking(download_dir)
        .await
        .unwrap_or_default();
    let text = |v: &Value| {
        v.as_str().map(str::to_string).unwrap_or_else(|| {
            if v.is_null() {
                String::new()
            } else {
                v.to_string()
            }
        })
    };
    match s.offline.create(
        &text(&body["url"]),
        &text(&body["filename"]),
        &dir,
        &body["media"],
        &body["subtitles"],
    ) {
        Ok((id, duplicate)) => Json(json!({"dl_id": id, "duplicate": duplicate})).into_response(),
        Err(e) => refused(e),
    }
}

/// The episodes of a season, queued one after another (60 at most).
async fn season(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let dir = tokio::task::spawn_blocking(download_dir)
        .await
        .unwrap_or_default();
    let episodes = body["episodes"].as_array().cloned().unwrap_or_default();
    let truthy = |v: &Value| {
        !matches!(v, Value::Null | Value::Bool(false)) && v != &json!(0) && v != &json!("")
    };
    let media: Map<String, Value> = ["name", "year", "poster"]
        .iter()
        .filter(|k| truthy(&body[**k]))
        .map(|k| (k.to_string(), body[*k].clone()))
        .collect();
    let (mut queued, mut duplicates) = (0, 0);
    for episode in episodes.iter().take(60) {
        match s
            .offline
            .queue_episode(&body["imdb_id"], &body["season"], episode, &dir, &media)
        {
            Ok((_, true)) => duplicates += 1,
            Ok((_, false)) => queued += 1,
            Err(e) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"error": e.message, "code": e.code, "queued": queued})),
                )
                    .into_response();
            }
        }
    }
    Json(json!({"queued": queued, "duplicates": duplicates})).into_response()
}

fn done(result: Result<(), OfflineError>) -> Response {
    match result {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(e) => refused(e),
    }
}

async fn pause(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    done(s.offline.pause(&id_of(&body)))
}

async fn resume(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    done(s.offline.resume(&id_of(&body), false, None))
}

async fn restart(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    done(s.offline.resume(&id_of(&body), true, None))
}

async fn remove(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    done(s.offline.remove(&id_of(&body)))
}

fn status(code: StatusCode) -> Response {
    code.into_response()
}

/// A kept file, its poster or its subtitles; video files answer ranges.
async fn asset(
    State(s): State<Shared>,
    Path((kind, id)): Path<(String, String)>,
    method: Method,
    headers: HeaderMap,
) -> Response {
    if !["media", "poster", "subtitles"].contains(&kind.as_str()) || !DOWNLOAD_ID.is_match(&id) {
        return status(StatusCode::NOT_FOUND);
    }
    let head = method == Method::HEAD;
    if kind == "subtitles" {
        return match s.offline.subtitles(&id) {
            None => status(StatusCode::NOT_FOUND),
            Some(_) if head => status(StatusCode::OK),
            Some(subtitle) => Json(json!({"subtitle": subtitle})).into_response(),
        };
    }
    let Some(path) = s.offline.asset(&id, &kind) else {
        return status(StatusCode::NOT_FOUND);
    };
    let Ok(meta) = tokio::fs::metadata(&path).await else {
        return status(StatusCode::NOT_FOUND);
    };
    let size = meta.len();
    let (mut start, mut end, mut partial) = (0, size.saturating_sub(1), false);
    if kind == "media"
        && let Some(range) = headers.get(header::RANGE).and_then(|v| v.to_str().ok())
    {
        let unsatisfiable = || {
            Response::builder()
                .status(StatusCode::RANGE_NOT_SATISFIABLE)
                .header(header::CONTENT_RANGE, format!("bytes */{size}"))
                .body(Body::empty())
                .unwrap_or_default()
        };
        let Some((first, last)) = range
            .trim()
            .strip_prefix("bytes=")
            .and_then(|r| r.split_once('-'))
        else {
            return unsatisfiable();
        };
        let digits = |s: &str| s.chars().all(|c| c.is_ascii_digit());
        if !digits(first) || !digits(last) || (first.is_empty() && last.is_empty()) {
            return unsatisfiable();
        }
        if !first.is_empty() {
            start = first.parse().unwrap_or(u64::MAX);
            end = if last.is_empty() {
                size.saturating_sub(1)
            } else {
                last.parse::<u64>()
                    .unwrap_or(u64::MAX)
                    .min(size.saturating_sub(1))
            };
        } else {
            start = size.saturating_sub(last.parse().unwrap_or(0));
        }
        if start >= size || end < start {
            return unsatisfiable();
        }
        partial = true;
    }
    let lower = path.to_string_lossy().to_lowercase();
    let content_type = if kind == "media" {
        if [".mp4", ".m4v", ".mov"].iter().any(|e| lower.ends_with(e)) {
            "video/mp4"
        } else if lower.ends_with(".mkv") {
            "video/x-matroska"
        } else {
            "application/octet-stream"
        }
    } else {
        let mut signature = [0u8; 12];
        let read = match tokio::fs::File::open(&path).await {
            Ok(mut f) => f.read(&mut signature).await.unwrap_or(0),
            Err(_) => 0,
        };
        let signature = &signature[..read];
        if signature.starts_with(b"\x89PNG") {
            "image/png"
        } else if signature.starts_with(b"RIFF") {
            "image/webp"
        } else {
            "image/jpeg"
        }
    };
    let length = if size == 0 { 0 } else { end - start + 1 };
    let mut response = Response::builder()
        .status(if partial {
            StatusCode::PARTIAL_CONTENT
        } else {
            StatusCode::OK
        })
        .header(header::CONTENT_TYPE, content_type)
        .header(header::CONTENT_LENGTH, length)
        .header(
            header::ACCEPT_RANGES,
            if kind == "media" { "bytes" } else { "none" },
        )
        .header(header::CACHE_CONTROL, "private, no-store");
    if partial {
        response = response.header(header::CONTENT_RANGE, format!("bytes {start}-{end}/{size}"));
    }
    if head {
        return response.body(Body::empty()).unwrap_or_default();
    }
    let Ok(mut file) = tokio::fs::File::open(&path).await else {
        return status(StatusCode::NOT_FOUND);
    };
    if file.seek(std::io::SeekFrom::Start(start)).await.is_err() {
        return status(StatusCode::NOT_FOUND);
    }
    let stream = tokio_util::io::ReaderStream::new(file.take(length));
    response.body(Body::from_stream(stream)).unwrap_or_default()
}
