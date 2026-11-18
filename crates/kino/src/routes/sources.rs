//! /api/torrents: the ranked sources of a film or an episode.

use std::collections::HashMap;

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{Method, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{Value, json};

use crate::body::JsonBody;
use crate::resolve::Resolver;
use crate::server::needs_app;

use crate::server::Shared;
use crate::sources::search::{self, Search};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/torrents", get(torrents))
        .route("/api/debrid", post(debrid))
        .route("/api/debrid-status", post(debrid_status))
        .route("/api/rd-history", get(rd_history))
        .route("/api/rd-delete", post(rd_delete))
        .route("/api/rd-cleanup", post(rd_cleanup))
        .route("/api/cloud-titles", post(cloud_titles))
        .route("/api/ad-dl", get(ad_dl))
        .route("/api/torbox-dl", get(torbox_dl))
}

async fn torrents(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let cfg = crate::db::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let query = search::Query::from_params(&p, &cfg);
    let (provider, token) = search::provider_and_token(&cfg);
    let force = p.get("force").map(String::as_str) == Some("1");
    let found = Search { meta: &s.meta }
        .streams(&query, &provider, &token, force)
        .await;
    Json(json!({"torrents": found})).into_response()
}

/// A season or an episode number as the page sends it (a number, a text, or nothing).
fn number(value: &Value) -> Option<i64> {
    match value {
        Value::Null => None,
        Value::String(s) => s.trim().parse().ok(),
        other => other.as_i64().or_else(|| other.as_f64().map(|f| f as i64)),
    }
}

fn failed(error: crate::net::NetError) -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({"error": error.0})),
    )
        .into_response()
}

/// The debrid link of a source. Without a debrid key, the KINO app reads the
/// torrent peer to peer.
async fn debrid(State(s): State<Shared>, raw: Bytes) -> Response {
    let Ok(Value::Object(body)) =
        serde_json::from_slice::<Value>(if raw.is_empty() { b"{}" } else { &raw })
    else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Requête invalide : un objet JSON est attendu."})),
        )
            .into_response();
    };
    let cfg = crate::db::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let (provider, token) = search::provider_and_token(&cfg);
    if token.is_empty() {
        let Some(p2p) = s.p2p.clone() else {
            return needs_app();
        };
        // No debrid key: KINO reads the torrent itself, once the user agreed.
        let magnet = body.get("magnet").and_then(Value::as_str).unwrap_or("");
        let resolve_url = body
            .get("resolve_url")
            .and_then(Value::as_str)
            .unwrap_or("");
        return match p2p.prepare(magnet, resolve_url, &format!("http://127.0.0.1:{}", s.port)).await {
            Ok(found) => Json(found).into_response(),
            Err(e) if e == crate::p2p::CONSENT_NEEDED => (
                StatusCode::CONFLICT,
                Json(json!({"error": "Sans service de débridage, KINO lit les torrents en P2P : acceptez-le une fois pour continuer.", "code": "p2p_consent"})),
            )
                .into_response(),
            Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e}))).into_response(),
        };
    }
    let resolver = Resolver {
        net: &s.net,
        provider,
        token,
        hosts: Default::default(),
    };
    let magnet = body.get("magnet").and_then(Value::as_str).unwrap_or("");
    let resolve_url = body.get("resolve_url").and_then(Value::as_str);
    let get = |k: &str| body.get(k).map(number).unwrap_or(None);
    match resolver
        .magnet(magnet, get("season"), get("episode"), resolve_url)
        .await
    {
        Ok(found) => Json(found).into_response(),
        Err(e) => failed(e),
    }
}

async fn debrid_status(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let cfg = crate::db::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let (provider, token) = search::provider_and_token(&cfg);
    let resolver = Resolver {
        net: &s.net,
        provider,
        token,
        hosts: Default::default(),
    };
    let id = match &body["torrent_id"] {
        Value::String(t) => t.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    };
    match resolver
        .check(&id, number(&body["season"]), number(&body["episode"]))
        .await
    {
        Ok(found) => Json(found).into_response(),
        Err(e) => failed(e),
    }
}

fn account<'a>(
    s: &'a Shared,
    cfg: &crate::config::AppConfig,
    provider: Option<&str>,
) -> crate::cloud::Account<'a> {
    let (active, token) = search::provider_and_token(cfg);
    crate::cloud::Account {
        net: &s.net,
        provider: provider.map(str::to_string).unwrap_or(active),
        token,
        hosts: Default::default(),
        port: s.port,
    }
}

/// Days given as a number or its text; None when absent or unreadable.
fn days(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(t) => t.trim().parse().ok(),
        Value::Bool(b) => Some(*b as i64),
        _ => None,
    }
}

/// The Cloud: the account's files grouped by work, after the retention cleanup.
async fn rd_history(State(s): State<Shared>) -> Response {
    let cfg = crate::db::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let cloud = account(&s, &cfg, None);
    let retention = cfg.rd_retention_days;
    let cleaned = if !cloud.token.is_empty() && retention > 0 {
        crate::cloud::auto_cleanup(&cloud, retention).await
    } else {
        json!({"deleted_downloads": 0, "deleted_torrents": 0})
    };
    match cloud.downloads(100).await {
        Ok(items) => {
            let groups = crate::cloud::group_items(&items, chrono::Local::now().date_naive());
            Json(json!({"items": items, "groups": groups, "cleaned": cleaned})).into_response()
        }
        Err(e) => failed(e),
    }
}

async fn rd_delete(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let cfg = crate::db::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let truthy = |v: &Value| {
        !matches!(v, Value::Null | Value::Bool(false))
            && v != &json!("")
            && v != &json!([])
            && v != &json!(0)
    };
    let given = if truthy(&body["ids"]) {
        body["ids"].clone()
    } else {
        body["id"].clone()
    };
    let ids: Vec<Value> = match given {
        Value::Array(list) => list,
        v if truthy(&v) => vec![v],
        _ => vec![],
    };
    let deleted = account(&s, &cfg, None).delete(&ids).await;
    Json(json!({"ok": true, "deleted": deleted})).into_response()
}

async fn rd_cleanup(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let cfg = crate::db::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let max_age = days(&body["max_age_days"])
        .or(Some(cfg.rd_retention_days))
        .unwrap_or(0);
    let cleaned = account(&s, &cfg, None).cleanup(max_age).await;
    Json(json!({"ok": true, "cleaned": cleaned})).into_response()
}

async fn cloud_titles(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let titles = body["titles"].as_array().cloned().unwrap_or_default();
    Json(json!({"matches": crate::cloud::match_titles(&s.meta, &titles).await})).into_response()
}

fn redirect(link: &str) -> Response {
    (
        StatusCode::FOUND,
        [
            (axum::http::header::LOCATION, link.to_string()),
            (axum::http::header::CACHE_CONTROL, "no-store".to_string()),
        ],
    )
        .into_response()
}

/// An AllDebrid Cloud file, unlocked when the player opens it.
async fn ad_dl(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let cfg = crate::db::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let token = search::provider_and_token(&cfg).1;
    let query = form_urlencoded::Serializer::new(String::new())
        .append_pair("agent", "KINO")
        .append_pair("apikey", &token)
        .append_pair("link", p.get("link").map(String::as_str).unwrap_or(""))
        .finish();
    let headers = [("User-Agent", crate::net::BROWSER)];
    let url = format!("https://api.alldebrid.com/v4/link/unlock?{query}");
    match s
        .net
        .request_json(
            Method::GET,
            &url,
            &headers,
            None,
            std::time::Duration::from_secs(20),
            3,
        )
        .await
    {
        Ok(u) => match u["data"]["link"].as_str().filter(|l| !l.is_empty()) {
            Some(link) => redirect(link),
            None => failed(crate::net::NetError(
                "Impossible de débrider ce lien AllDebrid.".into(),
            )),
        },
        Err(e) => failed(e),
    }
}

/// A TorBox Cloud file's link, asked when the player opens it.
async fn torbox_dl(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let cfg = crate::db::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let token = search::provider_and_token(&cfg).1;
    let query = form_urlencoded::Serializer::new(String::new())
        .append_pair("token", &token)
        .append_pair(
            "torrent_id",
            p.get("torrent_id").map(String::as_str).unwrap_or(""),
        )
        .append_pair(
            "file_id",
            p.get("file_id").map(String::as_str).unwrap_or("0"),
        )
        .finish();
    let bearer = format!("Bearer {token}");
    let headers = [
        ("User-Agent", crate::net::BROWSER),
        ("Authorization", bearer.as_str()),
    ];
    let url = format!("https://api.torbox.app/v1/api/torrents/requestdl?{query}");
    match s
        .net
        .request_json(
            Method::GET,
            &url,
            &headers,
            None,
            std::time::Duration::from_secs(20),
            3,
        )
        .await
    {
        Ok(res) => match res["data"].as_str().filter(|l| !l.is_empty()) {
            Some(link) => redirect(link),
            None => failed(crate::net::NetError(
                "Impossible d'obtenir le lien TorBox.".into(),
            )),
        },
        Err(e) => failed(e),
    }
}
