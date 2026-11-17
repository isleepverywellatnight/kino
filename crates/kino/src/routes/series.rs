//! The series the user follows (« Nouveaux épisodes », « Mes séries », the
//! « Calendrier ») and the lists as the page reads them.

use axum::extract::State;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use serde_json::{Value, json};

use crate::server::Shared;
use crate::{library, meta, series};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/new-episodes", get(new_episodes))
        .route("/api/my-series", get(my_series))
        .route("/api/calendar", get(calendar))
        .route("/api/user-lists", get(user_lists))
        .route("/api/stats", get(stats))
        .route("/api/community/curated", get(curated))
        .route("/api/letterboxd/custom-lists", get(custom_lists))
        .route(
            "/api/import-letterboxd",
            axum::routing::post(import_letterboxd),
        )
        .route(
            "/api/letterboxd/import-list",
            axum::routing::post(import_list),
        )
        .route(
            "/api/letterboxd/delete-list",
            axum::routing::post(delete_list),
        )
}

fn lists(s: &Shared) -> (Vec<Value>, Vec<Value>) {
    (library::history(&s.db), library::watchlist(&s.db))
}

async fn new_episodes(State(s): State<Shared>) -> Response {
    let today = meta::today_utc();
    let (history, watchlist) = lists(&s);
    let episodes = series::new_episodes(&s.meta, &history, &watchlist, &today).await;
    Json(json!({"episodes": episodes, "today": today})).into_response()
}

async fn my_series(State(s): State<Shared>) -> Response {
    let today = meta::today_utc();
    let (history, watchlist) = lists(&s);
    let found = series::my_series(&s.meta, &history, &watchlist, &today).await;
    Json(json!({"series": found, "today": today})).into_response()
}

async fn calendar(State(s): State<Shared>) -> Response {
    let today = meta::today_utc();
    let (history, watchlist) = lists(&s);
    Json(
        series::calendar(
            &s.meta,
            &history,
            &watchlist,
            &today,
            &series::local_today(),
        )
        .await,
    )
    .into_response()
}

async fn stats(State(s): State<Shared>) -> Response {
    let history = library::history(&s.db);
    Json(crate::stats::viewing_stats(&s.db, &s.meta, &history).await).into_response()
}

async fn user_lists(State(s): State<Shared>) -> Response {
    let (history, watchlist) = lists(&s);
    let cfg = tokio::task::spawn_blocking(crate::config::load)
        .await
        .unwrap_or_default();
    let user = cfg.get("letterboxd_user").cloned().unwrap_or(json!(""));
    Json(json!({"watchlist": watchlist, "history": history, "letterboxd_user": user}))
        .into_response()
}

async fn curated() -> Response {
    let lists = crate::letterboxd::curated();
    Json(json!({"status": "ok", "lists": lists, "collections": lists})).into_response()
}

async fn custom_lists() -> Response {
    let lists = tokio::task::spawn_blocking(crate::letterboxd::lists)
        .await
        .unwrap_or_default();
    Json(json!({"custom_lists": lists})).into_response()
}

fn failed(error: crate::net::NetError) -> Response {
    (
        axum::http::StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({"error": error.0})),
    )
        .into_response()
}

/// Joins {"ok": true} with the answer's own fields, as the page reads them.
fn ok_with(found: Value) -> Response {
    let mut out = serde_json::Map::new();
    out.insert("ok".into(), json!(true));
    out.extend(found.as_object().cloned().unwrap_or_default());
    Json(Value::Object(out)).into_response()
}

async fn import_letterboxd(
    State(s): State<Shared>,
    crate::body::JsonBody(body): crate::body::JsonBody,
) -> Response {
    match crate::letterboxd::import(&s.meta, &s.db, &body).await {
        Ok(found) => ok_with(found),
        Err(e) => failed(e),
    }
}

async fn import_list(
    State(s): State<Shared>,
    crate::body::JsonBody(body): crate::body::JsonBody,
) -> Response {
    let url = match &body["url"] {
        Value::String(u) => u.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    };
    match crate::letterboxd::import_list(&s.meta, &url).await {
        Ok(found) => ok_with(found),
        Err(e) => failed(e),
    }
}

async fn delete_list(crate::body::JsonBody(body): crate::body::JsonBody) -> Response {
    let id = body.get("id").cloned().unwrap_or(json!(""));
    Json(json!({"ok": true, "custom_lists": crate::letterboxd::delete_list(&id).await}))
        .into_response()
}
