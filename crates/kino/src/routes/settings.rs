//! /api/config: the settings the page shows, and the changes it saves.

use axum::extract::State;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;

use crate::body::JsonBody;
use crate::config::{
    self, AudioMode, ConfigUpdate, DebridProvider, HdrMode, PlayerMode, PrefLang, Quality,
    SourcePriority,
};
use crate::server::Shared;

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/config", get(read).post(save))
        .route("/api/tmdb/key", axum::routing::post(tmdb_key))
        .route("/api/client-error", axum::routing::post(client_error))
        .route(
            "/api/stremio-addons",
            get(stremio_addons).post(change_stremio_addons),
        )
        .route("/api/stremio-addons/catalog", get(stremio_catalog))
        .route("/api/addons", get(addons))
        .route("/api/backups", get(backups))
        .route("/api/network", get(network))
        .route("/api/sync/gdrive", get(gdrive).post(gdrive))
        .route("/api/backups/now", axum::routing::post(backup_now))
        .route("/api/backups/restore", axum::routing::post(backup_restore))
        .route("/api/trakt/status", get(trakt_status))
        .route("/api/trakt/auth/start", axum::routing::post(trakt_start))
        .route("/api/trakt/auth/poll", axum::routing::post(trakt_poll))
        .route(
            "/api/trakt/disconnect",
            axum::routing::post(trakt_disconnect),
        )
        .route("/api/trakt/scrobble", axum::routing::post(trakt_scrobble))
        .route("/api/trakt/sync", axum::routing::post(trakt_sync))
        .route("/api/addons/toggle", axum::routing::post(toggle_addon))
}

/// What the settings page shows: the settings (never a key), and what this
/// computer offers.
#[derive(Serialize)]
struct ConfigView {
    debrid_provider: DebridProvider,
    configured_providers: BTreeMap<DebridProvider, bool>,
    has_token: bool,
    user: Value,
    user_status: String,
    download_dir: String,
    player_mode: PlayerMode,
    pref_lang: PrefLang,
    pref_quality: Quality,
    hdr_mode: HdrMode,
    source_priority: SourcePriority,
    audio_mode: AudioMode,
    rd_retention_days: i64,
    discord_rpc: bool,
    native_player: bool,
    native_player_available: bool,
    auto_fr_subs: bool,
    episode_notifications: bool,
    notifications_available: bool,
    discord_client_id: String,
    has_tmdb_key: bool,
}

async fn read(State(s): State<Shared>) -> Response {
    let cfg = crate::db::spawn_blocking(config::load)
        .await
        .unwrap_or_default();
    let (user, user_status) = if cfg.rd_token.is_empty() {
        (Value::Null, "unconfigured".to_string())
    } else {
        s.accounts
            .status(&s.net, cfg.debrid_provider.as_str(), &cfg.rd_token)
            .await
    };
    Json(ConfigView {
        debrid_provider: cfg.debrid_provider,
        configured_providers: cfg
            .provider_tokens
            .iter()
            .map(|(k, v)| (*k, !v.is_empty()))
            .collect(),
        has_token: !cfg.rd_token.is_empty(),
        user,
        user_status,
        download_dir: cfg.download_dir.clone(),
        player_mode: cfg.player_mode,
        pref_lang: cfg.pref_lang,
        pref_quality: cfg.pref_quality,
        hdr_mode: cfg.hdr_mode,
        source_priority: cfg.priority(),
        audio_mode: cfg.audio_mode,
        rd_retention_days: cfg.rd_retention_days,
        discord_rpc: cfg.discord_rpc,
        // On by default where it exists (macOS): the built-in web player stays the fallback.
        native_player: cfg.native_player.unwrap_or(s.native_player),
        native_player_available: s.native_player,
        // French subtitles when a film or an episode exists only in its original language.
        auto_fr_subs: cfg.auto_fr_subs != Some(false),
        episode_notifications: cfg.episode_notifications != Some(false),
        notifications_available: s.notifications,
        discord_client_id: cfg.discord_client_id.clone(),
        has_tmdb_key: !cfg.tmdb_key().is_empty(),
    })
    .into_response()
}

/// The page's changes, each checked as it is read.
async fn save(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let changes: ConfigUpdate = serde_json::from_value(body).unwrap_or_default();
    if let (Some(player), Some(on)) = (&s.player, changes.discord_rpc) {
        player.discord.set_enabled(on);
    }
    let _ = crate::db::spawn_blocking(move || config::save_update(&changes)).await;
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
        crate::db::spawn_blocking(move || config::save(&json!({"tmdb_key": value})))
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
    crate::db::spawn_blocking(move || {
        crate::errorlog::LOG.record(&place, &kind, &message, &frames)
    })
    .await
    .ok();
    Json(json!({"ok": true})).into_response()
}

fn installed_answer(s: &Shared) -> Response {
    Json(json!({"addons": crate::addons::installed(&s.db), "catalogs": crate::addons::catalogs(&s.db)})).into_response()
}

async fn stremio_addons(State(s): State<Shared>) -> Response {
    installed_answer(&s)
}

async fn change_stremio_addons(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    use crate::addons::{self, AddonError};
    let id = match &body["id"] {
        Value::Null => String::new(),
        other => field(&json!({"v": other}), "v"),
    };
    let done = match body["action"].as_str() {
        Some("add") => addons::add(&s.db, &s.net, &field(&body, "url"))
            .await
            .map(|_| ()),
        Some("remove") => addons::remove(&s.db, &id),
        Some("enable") => addons::set_enabled(&s.db, &id, body["enabled"] == true),
        _ => Err(AddonError("Action inconnue.".into())),
    };
    match done {
        Ok(()) => installed_answer(&s),
        Err(AddonError(message)) => (
            axum::http::StatusCode::BAD_REQUEST,
            Json(json!({"error": message})),
        )
            .into_response(),
    }
}

async fn stremio_catalog(
    State(s): State<Shared>,
    axum::extract::Query(p): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> Response {
    use axum::http::StatusCode;
    let get = |k: &str| p.get(k).cloned().unwrap_or_default();
    match crate::addons::catalog_items(&s.db, &s.net, &get("addon"), &get("type"), &get("id")).await
    {
        Ok(items) => Json(json!({"items": items})).into_response(),
        Err(crate::addons::CatalogError::Unknown) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Catalogue inconnu."})),
        )
            .into_response(),
        Err(crate::addons::CatalogError::Down) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": "Ce catalogue ne répond pas pour le moment.", "items": []})),
        )
            .into_response(),
    }
}

/// KINO's built-in sources (the Python plugin add-ons are not run any more).
async fn addons(State(s): State<Shared>) -> Response {
    Json(json!({"status": "ok", "addons": crate::addons::builtin(&s.db)})).into_response()
}

async fn toggle_addon(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let enabled = match body.get("enabled") {
        None => true,
        Some(v) => {
            !matches!(v, Value::Null | Value::Bool(false)) && v != &json!(0) && v != &json!("")
        }
    };
    crate::addons::toggle_builtin(&s.db, &field(&body, "id"), enabled);
    Json(json!({"ok": true, "addons": crate::addons::builtin(&s.db)})).into_response()
}

fn trakt(s: &Shared) -> crate::trakt::Trakt<'_> {
    crate::trakt::Trakt {
        db: &s.db,
        client: &s.http,
        base: crate::trakt::API.into(),
    }
}

async fn trakt_status(State(s): State<Shared>) -> Response {
    Json(trakt(&s).status()).into_response()
}

async fn trakt_start(State(s): State<Shared>) -> Response {
    Json(trakt(&s).start().await).into_response()
}

async fn trakt_poll(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    Json(trakt(&s).poll(&field(&body, "device_code")).await).into_response()
}

async fn trakt_disconnect(State(s): State<Shared>) -> Response {
    Json(trakt(&s).disconnect()).into_response()
}

async fn trakt_scrobble(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let action = body
        .get("action")
        .map(|a| field(&json!({"v": a}), "v"))
        .unwrap_or_else(|| "start".into());
    let media = body.get("media").cloned().unwrap_or(json!({}));
    let progress = match &body["progress"] {
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(t) => t.trim().parse().unwrap_or(0.0),
        _ => 0.0,
    };
    Json(trakt(&s).scrobble(&action, &media, progress).await).into_response()
}

async fn trakt_sync(State(s): State<Shared>) -> Response {
    Json(trakt(&s).sync().await).into_response()
}

async fn backups() -> Response {
    let items = crate::db::spawn_blocking(|| crate::backup::Backups::standard().list())
        .await
        .unwrap_or_default();
    Json(json!({"items": items, "keep": crate::backup::KEEP_DAILY})).into_response()
}

async fn backup_now() -> Response {
    let made = crate::db::spawn_blocking(|| {
        let backups = crate::backup::Backups::standard();
        backups.make(false).map(|entry| (entry, backups.list()))
    })
    .await;
    match made {
        Ok(Ok((entry, items))) => {
            Json(json!({"ok": true, "backup": entry, "items": items})).into_response()
        }
        Ok(Err(e)) => {
            crate::errorlog::LOG.record("sauvegarde", "Erreur", &format!("{e:?}"), &[]);
            (
                axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "La sauvegarde n'a pas pu être faite."})),
            )
                .into_response()
        }
        Err(_) => (
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "La sauvegarde n'a pas pu être faite."})),
        )
            .into_response(),
    }
}

async fn backup_restore(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    use crate::backup::BackupError;
    use axum::http::StatusCode;
    let id = field(&body, "id");
    let done =
        crate::db::spawn_blocking(move || crate::backup::Backups::standard().restore(&id)).await;
    match done {
        Ok(Ok(result)) => {
            s.net.forget();
            let mut out = serde_json::Map::new();
            out.insert("ok".into(), json!(true));
            out.extend(result.as_object().cloned().unwrap_or_default());
            Json(Value::Object(out)).into_response()
        }
        Ok(Err(BackupError::Refused(message))) => {
            (StatusCode::BAD_REQUEST, Json(json!({"error": message}))).into_response()
        }
        Ok(Err(e)) => {
            crate::errorlog::LOG.record("restauration", "Erreur", &format!("{e:?}"), &[]);
            let message = "La restauration n'a pas pu se faire. Vos données n'ont pas changé.";
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": message})),
            )
                .into_response()
        }
        Err(_) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "La restauration n'a pas pu se faire."})),
        )
            .into_response(),
    }
}

async fn gdrive(State(s): State<Shared>) -> Response {
    let db = s.db.clone();
    let done =
        crate::db::spawn_blocking(move || crate::gdrive::sync(&db, crate::gdrive::find_dir()))
            .await
            .unwrap_or_default();
    Json(done).into_response()
}

async fn network(
    axum::extract::Query(p): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> Response {
    let refresh = p.get("refresh").map(String::as_str) == Some("1");
    Json(json!({"online": crate::network::online(refresh).await})).into_response()
}
