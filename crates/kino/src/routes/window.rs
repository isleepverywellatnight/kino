//! What the page asks of the window and the computer: Discord for KINO's own
//! player, window actions, the download folder, a screenshot. Answered here
//! when the window runs in this process; behind the Python window, by Python.

use axum::Json;
use axum::Router;
use axum::extract::{Request, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use base64::Engine;
use serde_json::{Value, json};

use crate::discord::Playing;
use crate::play::{text, truthy};
use crate::server::{Shared, delegate};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/discord-rpc/status", get(discord_status))
        .route("/api/discord-rpc/update", post(discord_update))
        .route("/api/discord-rpc/clear", post(discord_clear))
        .route("/api/window/action", post(window_action))
        .route("/api/open-folder", post(open_folder))
        .route("/api/save-screenshot", post(save_screenshot))
        .route("/api/probe-media", post(probe_media))
        .route("/api/upload-torrent", post(upload_torrent))
        .route("/api/notifications/test", post(notifications_test))
        .route("/api/remux", get(remux))
}

async fn python(s: &Shared, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let body = axum::body::to_bytes(body, 64 << 20)
        .await
        .unwrap_or_default();
    delegate(s, parts.method, &parts.uri, &parts.headers, body).await
}

async fn json_body(request: Request) -> Value {
    let body = axum::body::to_bytes(request.into_body(), 64 << 20)
        .await
        .unwrap_or_default();
    serde_json::from_slice(&body).unwrap_or_else(|_| json!({}))
}

async fn discord_status(State(s): State<Shared>, request: Request) -> Response {
    let Some(player) = s.player.clone() else {
        return python(&s, request).await;
    };
    Json(json!({"status": "ok", "connected": player.discord.connected(), "enabled": player.discord.enabled()}))
        .into_response()
}

/// KINO's own player tells Discord what plays.
async fn discord_update(State(s): State<Shared>, request: Request) -> Response {
    let Some(player) = s.player.clone() else {
        return python(&s, request).await;
    };
    let body = json_body(request).await;
    let number = |k: &str| {
        let v = &body[k];
        v.as_f64().or_else(|| text(v).trim().parse().ok())
    };
    let whole = |k: &str| number(k).filter(|_| !body[k].is_null()).map(|n| n as i64);
    let (title, kind, poster, year) = (
        text(&body["title"]),
        body.get("media_type")
            .map(text)
            .unwrap_or_else(|| "movie".into()),
        text(&body["poster"]),
        text(&body["year"]),
    );
    let episode_title = text(&body["ep_title"]);
    player.discord.show(&Playing {
        title: &title,
        kind: &kind,
        season: whole("season"),
        episode: whole("episode"),
        episode_title: Some(&episode_title),
        position: number("current_time").unwrap_or(0.0),
        duration: number("duration").unwrap_or(0.0),
        poster: Some(&poster),
        paused: truthy(&body["is_paused"]),
        year: Some(&year),
    });
    Json(json!({"ok": true})).into_response()
}

async fn discord_clear(State(s): State<Shared>, request: Request) -> Response {
    let Some(player) = s.player.clone() else {
        return python(&s, request).await;
    };
    player.discord.clear();
    Json(json!({"ok": true})).into_response()
}

async fn window_action(State(s): State<Shared>, request: Request) -> Response {
    let Some(player) = s.player.clone() else {
        return python(&s, request).await;
    };
    let action = text(&json_body(request).await["action"]);
    match player.window() {
        Some(window) => {
            window.action(&action);
            Json(json!({"ok": true})).into_response()
        }
        None => Json(json!({"ok": false, "note": "Pas de callback fenetre"})).into_response(),
    }
}

pub(crate) fn download_dir() -> std::path::PathBuf {
    let cfg = crate::config::load();
    let dir = cfg
        .get("download_dir")
        .map(text)
        .filter(|d| !d.is_empty())
        .unwrap_or_else(crate::config::default_download_dir);
    std::path::PathBuf::from(dir)
}

/// The download folder in Finder, Explorer or the file manager.
async fn open_folder(State(s): State<Shared>, request: Request) -> Response {
    if s.player.is_none() {
        return python(&s, request).await;
    }
    let folder = tokio::task::spawn_blocking(download_dir)
        .await
        .unwrap_or_default();
    let _ = std::fs::create_dir_all(&folder);
    let program = if cfg!(windows) {
        "explorer.exe"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    let _ = std::process::Command::new(program).arg(&folder).spawn();
    Json(json!({"ok": true})).into_response()
}

/// Letters, digits, « _ » and « - » kept; each run of anything else becomes
/// one « _ » (`underscore`) or vanishes.
fn clean(value: &str, underscore: bool, limit: usize) -> String {
    let mut out = String::new();
    let mut gap = false;
    for c in value.chars() {
        if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
            out.push(c);
            gap = false;
        } else if underscore && !gap {
            out.push('_');
            gap = true;
        }
    }
    out.chars().take(limit).collect()
}

/// « KINO_Capture_<title>_<time>.png », safe for any file system.
pub(crate) fn capture_name(title: &str, time: &str, time_limit: usize) -> String {
    let title = if title.is_empty() { "KINO" } else { title };
    let safe = clean(title.trim(), true, 45);
    let safe = safe.trim_matches('_');
    let safe = if safe.is_empty() { "KINO" } else { safe };
    let time = if time.is_empty() { "00m00s" } else { time };
    let time = clean(time, false, time_limit);
    format!("KINO_Capture_{safe}_{time}.png")
}

/// A frame of KINO's player, saved as a PNG in the download folder.
async fn save_screenshot(State(s): State<Shared>, request: Request) -> Response {
    if s.player.is_none() {
        return python(&s, request).await;
    }
    let body = json_body(request).await;
    let data = text(&body["data_url"]);
    let data = data.split_once(',').map_or(data.as_str(), |(_, d)| d);
    let Ok(png) = base64::engine::general_purpose::STANDARD.decode(data.trim()) else {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "Image invalide."})),
        )
            .into_response();
    };
    let name = capture_name(&text(&body["title"]), &text(&body["time_str"]), usize::MAX);
    let folder = tokio::task::spawn_blocking(download_dir)
        .await
        .unwrap_or_default();
    let path = folder.join(&name);
    let saved = std::fs::create_dir_all(&folder).and_then(|_| std::fs::write(&path, png));
    match saved {
        Ok(()) => Json(json!({"ok": true, "filename": name, "path": path.display().to_string()}))
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": e.to_string()})),
        )
            .into_response(),
    }
}

/// A .torrent file chosen on the page, as a magnet link (no window needed).
async fn upload_torrent(request: Request) -> Response {
    let body = json_body(request).await;
    let data = text(&body["data_b64"]);
    if data.is_empty() {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "Fichier .torrent vide."})),
        )
            .into_response();
    }
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(data.trim())
        .map_err(|_| "Fichier .torrent illisible.".to_string())
        .and_then(|raw| crate::torrentfile::to_magnet(&raw));
    match decoded {
        Ok((magnet, name, info_hash)) => {
            Json(json!({"ok": true, "magnet": magnet, "name": name, "info_hash": info_hash}))
                .into_response()
        }
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e}))).into_response(),
    }
}

/// « Tester » in Paramètres: permission asked if needed, then one notification.
async fn notifications_test(State(s): State<Shared>, request: Request) -> Response {
    if s.player.is_none() {
        return python(&s, request).await;
    }
    let Some(notifier) = s.notifier.clone() else {
        return Json(json!({"ok": false, "status": "unavailable"})).into_response();
    };
    let status = tokio::task::spawn_blocking(move || {
        let mut status = notifier.status();
        if status == "not_determined" {
            notifier.request_permission();
            status = notifier.status();
        }
        let granted = crate::alerts::GRANTED.contains(&status.as_str());
        if granted {
            let id = format!("kino-test-{}", crate::db::now() as i64);
            notifier.send(
                &id,
                "KINO",
                "Les notifications des nouveaux épisodes fonctionnent.",
                &Default::default(),
            );
        }
        status
    })
    .await
    .unwrap_or_else(|_| "unknown".into());
    let ok = crate::alerts::GRANTED.contains(&status.as_str());
    Json(json!({"ok": ok, "status": status})).into_response()
}

/// A stream's video and audio tracks, and the audio track KINO would pick.
async fn probe_media(State(s): State<Shared>, request: Request) -> Response {
    if s.player.is_none() {
        return python(&s, request).await;
    }
    let source = text(&json_body(request).await["url"]);
    let source = crate::routes::play::offline_media_file(&s, &source)
        .or_else(|| crate::media::is_web_stream(&source).then_some(source));
    let Some(source) = source else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Adresse du flux invalide"})),
        )
            .into_response();
    };
    let Some(info) = crate::media::inspect(&source, std::time::Duration::from_secs(8)).await else {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"error": "Impossible d’analyser ce flux"})),
        )
            .into_response();
    };
    let preference = tokio::task::spawn_blocking(crate::config::load)
        .await
        .ok()
        .and_then(|c| c.get("pref_lang").map(text))
        .unwrap_or_else(|| "vf".into());
    let audio = info["audio"].as_array().cloned().unwrap_or_default();
    let selected = crate::media::select_audio(&audio, &preference);
    Json(json!({"ok": true, "media": info, "selected_audio": selected})).into_response()
}

/// FFmpeg's output, and the FFmpeg that makes it: ended when the page stops reading.
struct Converting {
    output: tokio_util::io::ReaderStream<tokio::process::ChildStdout>,
    _ffmpeg: tokio::process::Child,
}

impl futures_util::Stream for Converting {
    type Item = std::io::Result<axum::body::Bytes>;
    fn poll_next(
        mut self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<Self::Item>> {
        std::pin::Pin::new(&mut self.output).poll_next(cx)
    }
}

/// A file the page's player cannot read, converted on the fly into a fragmented MP4.
async fn remux(
    State(s): State<Shared>,
    axum::extract::Query(p): axum::extract::Query<std::collections::HashMap<String, String>>,
    request: Request,
) -> Response {
    if s.player.is_none() {
        return python(&s, request).await;
    }
    let failure = |status: StatusCode, body: Value| (status, Json(body)).into_response();
    let raw = p.get("url").cloned().unwrap_or_default();
    if raw.is_empty() {
        return failure(
            StatusCode::BAD_REQUEST,
            json!({"error": "Paramètre url manquant"}),
        );
    }
    let source = crate::routes::play::offline_media_file(&s, &raw)
        .or_else(|| crate::media::is_web_stream(&raw).then(|| raw.clone()));
    let Some(source) = source else {
        return failure(
            StatusCode::BAD_REQUEST,
            json!({"error": "Adresse du flux invalide", "code": "source_unavailable"}),
        );
    };
    let Some(ffmpeg) = crate::intro::find_tool("ffmpeg") else {
        return failure(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({"error": "FFmpeg introuvable sur le système"}),
        );
    };
    let start = p
        .get("ss")
        .and_then(|v| v.trim().parse::<f64>().ok())
        .filter(|v| v.is_finite())
        .unwrap_or(0.0)
        .max(0.0);
    let audio_index = match p.get("audio").map(|a| a.trim().parse::<u32>()) {
        None => None,
        Some(Ok(i)) if i <= 1024 => Some(i),
        Some(_) => {
            return failure(
                StatusCode::BAD_REQUEST,
                json!({"error": "Piste audio invalide"}),
            );
        }
    };
    let args = crate::media::remux_args(&crate::media::Remux {
        source: &source,
        start,
        transcode_video: p.get("video").map(String::as_str) == Some("h264"),
        audio_index,
        transcode_audio: p.get("acopy").map(String::as_str) != Some("1"),
        hevc_tag: p.get("hvc1").map(String::as_str) == Some("1"),
    });
    let spawned = tokio::process::Command::new(ffmpeg)
        .args(args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .spawn();
    let Ok(mut ffmpeg) = spawned else {
        return failure(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({"error": "FFmpeg introuvable sur le système"}),
        );
    };
    let Some(stdout) = ffmpeg.stdout.take() else {
        return failure(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({"error": "FFmpeg introuvable sur le système"}),
        );
    };
    let mut output = tokio_util::io::ReaderStream::with_capacity(stdout, 65536);
    // Nothing at all: the link expired or cannot be reached.
    let first = futures_util::StreamExt::next(&mut output).await;
    let Some(Ok(first)) = first.filter(|f| f.as_ref().is_ok_and(|b| !b.is_empty())) else {
        return failure(
            StatusCode::BAD_GATEWAY,
            json!({"error": "Ce lien de lecture est expiré ou inaccessible. Essayez une autre source.", "code": "source_unavailable"}),
        );
    };
    let rest = Converting {
        output,
        _ffmpeg: ffmpeg,
    };
    let body =
        futures_util::StreamExt::chain(futures_util::stream::once(async move { Ok(first) }), rest);
    Response::builder()
        .header("Content-Type", "video/mp4")
        .header("Accept-Ranges", "none")
        .header("Cache-Control", "no-cache, no-store, must-revalidate")
        .body(axum::body::Body::from_stream(body))
        .unwrap_or_else(|_| StatusCode::BAD_GATEWAY.into_response())
}

#[cfg(test)]
mod tests {
    use super::clean;

    #[test]
    fn screenshot_names() {
        // As Python: runs of other characters become one « _ » in the title, vanish in the time.
        assert_eq!(clean("Dune : Part Two!", true, 45), "Dune_Part_Two_");
        assert_eq!(clean("01h 02m:03s", false, usize::MAX), "01h02m03s");
    }
}
