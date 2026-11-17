//! /api/torrents: the ranked sources of a film or an episode.

use std::collections::HashMap;

use axum::extract::{Query, State};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use serde_json::json;

use crate::server::Shared;
use crate::sources::search::{self, Search};

pub fn router() -> Router<Shared> {
    Router::new().route("/api/torrents", get(torrents))
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
