//! /api/config: the settings the page shows, and the changes it saves.

use axum::extract::State;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use serde_json::{Map, Value, json};

use crate::body::JsonBody;
use crate::config;
use crate::server::Shared;

pub fn router() -> Router<Shared> {
    Router::new().route("/api/config", get(read).post(save))
}

async fn read(State(s): State<Shared>) -> Response {
    let cfg = tokio::task::spawn_blocking(config::load)
        .await
        .unwrap_or_default();
    let get = |k: &str| cfg.get(k).cloned().unwrap_or(Value::Null);
    let provider = cfg["debrid_provider"]
        .as_str()
        .unwrap_or("realdebrid")
        .to_string();
    let token = cfg["rd_token"].as_str().unwrap_or("").to_string();
    let retention = cfg["rd_retention_days"].as_i64().unwrap_or(0);
    let (user, status) = if token.is_empty() {
        (Value::Null, "unconfigured".to_string())
    } else {
        s.accounts.status(&s.net, &provider, &token).await
    };
    let configured: Map<String, Value> = cfg["provider_tokens"]
        .as_object()
        .into_iter()
        .flatten()
        .map(|(k, v)| (k.clone(), json!(v.as_str().is_some_and(|t| !t.is_empty()))))
        .collect();
    let or = |k: &str, default: Value| cfg.get(k).cloned().unwrap_or(default);
    Json(json!({
        "debrid_provider": provider, "configured_providers": configured, "has_token": !token.is_empty(), "user": user,
        "user_status": status, "download_dir": or("download_dir", json!(config::default_download_dir())),
        "player_mode": or("player_mode", json!("kino")), "pref_lang": or("pref_lang", json!("vf")),
        "pref_quality": or("pref_quality", json!("4k")), "hdr_mode": or("hdr_mode", json!("sdr_pref")),
        "source_priority": if get("source_priority") == json!("quality") { "quality" } else { "speed" },
        "audio_mode": or("audio_mode", json!("voice_boost")), "rd_retention_days": retention,
        "discord_rpc": or("discord_rpc", json!(true)),
        // On by default where it exists (macOS): the built-in web player stays the fallback.
        "native_player": cfg.get("native_player").map_or(s.native_player, |v| v.as_bool().unwrap_or(false)),
        "native_player_available": s.native_player,
        // French subtitles when a film or an episode exists only in its original language.
        "auto_fr_subs": get("auto_fr_subs") != json!(false), "episode_notifications": get("episode_notifications") != json!(false),
        "notifications_available": s.notifications,
        "discord_client_id": or("discord_client_id", json!(config::DISCORD_CLIENT_ID)),
        "has_tmdb_key": cfg.get("tmdb_key").and_then(Value::as_str).is_some_and(|k| !k.is_empty()),
    }))
    .into_response()
}

async fn save(JsonBody(body): JsonBody) -> Response {
    let _ = tokio::task::spawn_blocking(move || config::save(&body)).await;
    Json(json!({"ok": true})).into_response()
}
