//! /api/manga/…: the Manga tab. Downloading a volume (/api/manga/volume…) needs
//! the KINO app's peer-to-peer engine.

use std::collections::HashMap;

use axum::extract::{Query, State};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{Value, json};

use crate::body::JsonBody;
use crate::manga::{self, Manga, MangaError, local, sources};
use crate::server::{Shared, needs_app};

type Params = Query<HashMap<String, String>>;

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/manga/search", get(search))
        .route("/api/manga/popular", get(popular))
        .route("/api/manga/row", get(row))
        .route("/api/manga/details", get(details))
        .route("/api/manga/chapters", get(chapters))
        .route("/api/manga/pages", get(pages))
        .route("/api/manga/volumes", get(volumes))
        .route("/api/manga/volume/files", get(volume_files))
        .route("/api/manga/volume/status", get(volume_status))
        .route("/api/manga/volume", axum::routing::post(volume_start))
        .route("/api/manga/local", get(local_series))
        .route("/api/manga/local/chapters", get(local_chapters))
        .route("/api/manga/local/page", get(local_page))
        .route("/api/manga/progress", get(progress).post(save_progress))
        .route("/api/manga/folder", post(folder))
}

pub fn answer(result: Result<Value, MangaError>) -> Response {
    match result {
        Ok(value) => Json(value).into_response(),
        Err(MangaError::Refused(text)) => (StatusCode::BAD_REQUEST, Json(json!({"error": text}))).into_response(),
        Err(MangaError::Down(_)) => {
            (StatusCode::BAD_GATEWAY, Json(json!({"error": "Les sources manga ne répondent pas pour le moment."}))).into_response()
        }
        Err(MangaError::Consent) => (
            StatusCode::CONFLICT,
            Json(json!({"error": "Sans service de débridage, KINO lit les torrents en P2P : acceptez-le une fois pour continuer.",
                        "code": "p2p_consent"})),
        )
            .into_response(),
    }
}

fn param<'a>(params: &'a HashMap<String, String>, key: &str, default: &'a str) -> &'a str {
    params.get(key).map(String::as_str).unwrap_or(default)
}

fn manga(state: &Shared) -> Manga<'_> {
    Manga {
        net: &state.net,
        sites: &state.sites,
    }
}

async fn search(State(s): State<Shared>, Query(p): Params) -> Response {
    answer(
        manga(&s)
            .search(param(&p, "q", ""))
            .await
            .map(|v| json!({"series": v})),
    )
}

async fn popular(State(s): State<Shared>) -> Response {
    answer(
        manga(&s)
            .row("trending")
            .await
            .map(|v| json!({"series": v})),
    )
}

async fn row(State(s): State<Shared>, Query(p): Params) -> Response {
    answer(
        manga(&s)
            .row(param(&p, "name", ""))
            .await
            .map(|v| json!({"series": v})),
    )
}

async fn details(State(s): State<Shared>, Query(p): Params) -> Response {
    answer(
        manga(&s)
            .details(param(&p, "id", ""), param(&p, "lang", "fr"))
            .await,
    )
}

async fn chapters(State(s): State<Shared>, Query(p): Params) -> Response {
    answer(
        manga(&s)
            .chapters(param(&p, "id", ""), param(&p, "lang", "fr"))
            .await
            .map(|v| json!({"chapters": v})),
    )
}

async fn pages(State(s): State<Shared>, Query(p): Params) -> Response {
    answer(
        manga(&s)
            .pages(param(&p, "chapter", ""))
            .await
            .map(|v| json!({"pages": v})),
    )
}

async fn volumes(State(s): State<Shared>, Query(p): Params) -> Response {
    let found = async {
        let titles = manga(&s).titles(param(&p, "id", "")).await?;
        Ok(sources::nyaa_volumes(&s.net, &s.sites, &titles, 12).await?)
    };
    answer(found.await.map(|v: Vec<Value>| json!({"torrents": v})))
}

async fn local_series(State(s): State<Shared>) -> Response {
    Json(local::series(s.net.db())).into_response()
}

async fn local_chapters(State(s): State<Shared>, Query(p): Params) -> Response {
    answer(local::chapters(s.net.db(), param(&p, "series", "")))
}

async fn local_page(State(s): State<Shared>, Query(p): Params) -> Response {
    match local::page(
        s.net.db(),
        param(&p, "series", ""),
        param(&p, "chapter", ""),
        param(&p, "page", ""),
    ) {
        Ok((data, kind)) => (
            [
                (header::CONTENT_TYPE, kind),
                (header::CACHE_CONTROL, "max-age=3600"),
            ],
            data,
        )
            .into_response(),
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}

async fn progress(State(s): State<Shared>) -> Response {
    Json(json!({"progress": manga::progress(s.net.db())})).into_response()
}

async fn save_progress(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    let db = s.net.db();
    if body["forget"].as_bool().unwrap_or(false) || body["forget"].as_i64().is_some_and(|n| n != 0)
    {
        manga::forget(
            db,
            body["source"].as_str().unwrap_or(""),
            body["series"].as_str().unwrap_or(""),
        );
        return Json(json!({"progress": manga::progress(db)})).into_response();
    }
    answer(manga::save_progress(db, &body).map(|r| json!({"saved": r})))
}

async fn folder(State(s): State<Shared>, JsonBody(body): JsonBody) -> Response {
    answer(local::set_folder(
        s.net.db(),
        body["path"].as_str().unwrap_or(""),
    ))
}

/// Python's answer while the window (and its P2P engine) is Python's.
/// The comic archives of a volume pack.
async fn volume_files(State(s): State<Shared>, Query(p): Params) -> Response {
    let Some(p2p) = s.p2p.clone() else {
        return needs_app();
    };
    answer(crate::manga::volumes::files(&p2p, param(&p, "hash", ""), param(&p, "name", "")).await)
}

async fn volume_status(State(s): State<Shared>, Query(p): Params) -> Response {
    let Some(p2p) = s.p2p.clone() else {
        return needs_app();
    };
    let index = json!(param(&p, "index", ""));
    answer(crate::manga::volumes::status(&p2p, &s.db, param(&p, "hash", ""), &index).await)
}

/// Fetches one volume of a pack into the manga folder.
async fn volume_start(State(s): State<Shared>, request: axum::extract::Request) -> Response {
    let Some(p2p) = s.p2p.clone() else {
        return needs_app();
    };
    let body = axum::body::to_bytes(request.into_body(), 1 << 20)
        .await
        .unwrap_or_default();
    let body: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    let (hash, series) = (
        crate::play::text(&body["hash"]),
        crate::play::text(&body["series"]),
    );
    let index = body.get("index").cloned().unwrap_or(json!(-1));
    answer(crate::manga::volumes::start(&p2p, &s.db, &hash, &index, &series).await)
}
