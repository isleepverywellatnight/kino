//! /api/torrents: the ranked sources of a film or an episode.

use std::collections::HashMap;

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, Method, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{Value, json};

use crate::body::JsonBody;
use crate::resolve::Resolver;
use crate::server::delegate;

use crate::server::Shared;
use crate::sources::search::{self, Search};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/torrents", get(torrents))
        .route("/api/debrid", post(debrid))
        .route("/api/debrid-status", post(debrid_status))
}

async fn torrents(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let cfg = tokio::task::spawn_blocking(search::load_settings)
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

/// The debrid link of a source. Without a debrid key, the Python server reads
/// the torrent peer to peer (not in Rust yet).
async fn debrid(
    State(s): State<Shared>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    raw: Bytes,
) -> Response {
    let Ok(Value::Object(body)) =
        serde_json::from_slice::<Value>(if raw.is_empty() { b"{}" } else { &raw })
    else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Requête invalide : un objet JSON est attendu."})),
        )
            .into_response();
    };
    let cfg = tokio::task::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let (provider, token) = search::provider_and_token(&cfg);
    if token.is_empty() {
        return delegate(&s, method, &uri, &headers, raw).await;
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
    let cfg = tokio::task::spawn_blocking(search::load_settings)
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
