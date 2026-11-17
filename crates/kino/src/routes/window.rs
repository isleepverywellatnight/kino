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

fn download_dir() -> std::path::PathBuf {
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
    let title = body
        .get("title")
        .map(text)
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| "KINO".into());
    let safe = clean(title.trim(), true, 45);
    let safe = safe.trim_matches('_');
    let safe = if safe.is_empty() { "KINO" } else { safe };
    let time = body
        .get("time_str")
        .map(text)
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| "00m00s".into());
    let time = clean(&time, false, usize::MAX);
    let name = format!("KINO_Capture_{safe}_{time}.png");
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
