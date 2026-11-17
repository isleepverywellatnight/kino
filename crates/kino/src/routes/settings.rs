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
    Router::new()
        .route("/api/config", get(read).post(save))
        .route("/api/tmdb/key", axum::routing::post(tmdb_key))
        .route("/api/client-error", axum::routing::post(client_error))
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

fn field(body: &Value, key: &str) -> String {
    match &body[key] {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(t) => t.clone(),
        other => other.to_string(),
    }
}

/// The user's TMDB key: checked by TMDB before it is kept; empty removes it.
async fn tmdb_key(JsonBody(body): JsonBody) -> Response {
    use axum::http::StatusCode;
    let key = field(&body, "key").trim().to_string();
    let store = |value: String| {
        tokio::task::spawn_blocking(move || config::save(&json!({"tmdb_key": value})))
    };
    if key.is_empty() {
        let _ = store(String::new()).await;
        return Json(json!({"ok": true, "status": "none"})).into_response();
    }
    // A read access token (v4, a JWT) goes in a header; an API key (v3) in the address.
    let url = "https://api.themoviedb.org/3/configuration";
    let request = reqwest::Client::new()
        .get(url)
        .header("Accept", "application/json")
        .header("User-Agent", "KINO")
        .timeout(std::time::Duration::from_secs(6));
    let request = if key.starts_with("eyJ") {
        request.header("Authorization", format!("Bearer {key}"))
    } else {
        request.query(&[("api_key", key.as_str())])
    };
    match request.send().await {
        Err(_) => (
            StatusCode::GATEWAY_TIMEOUT,
            Json(json!({"error": "TMDB ne répond pas. Vérifiez la connexion et réessayez."})),
        )
            .into_response(),
        Ok(r) if !r.status().is_success() => {
            let refused = "TMDB refuse cette clé. Copiez la « Clé d'API » de themoviedb.org → Paramètres → API.";
            (StatusCode::BAD_REQUEST, Json(json!({"error": refused}))).into_response()
        }
        Ok(_) => {
            let _ = store(key).await;
            Json(json!({"ok": true, "status": "valid"})).into_response()
        }
    }
}

/// An error of the page itself, written to KINO's error log.
async fn client_error(JsonBody(body): JsonBody) -> Response {
    let cut = |s: String, n: usize| s.chars().take(n).collect::<String>();
    let place = format!("page {}", cut(field(&body, "tab"), 30))
        .trim()
        .to_string();
    let kind = match field(&body, "kind") {
        k if k.is_empty() => "Erreur de la page".to_string(),
        k => cut(k, 60),
    };
    let stack = format!(
        "{}\n{}",
        cut(field(&body, "source"), 200),
        cut(field(&body, "stack"), 2000)
    );
    let frames: Vec<String> = stack
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(str::to_string)
        .collect();
    let message = cut(field(&body, "message"), 500);
    tokio::task::spawn_blocking(move || {
        crate::errorlog::LOG.record(&place, &kind, &message, &frames)
    })
    .await
    .ok();
    Json(json!({"ok": true})).into_response()
}
