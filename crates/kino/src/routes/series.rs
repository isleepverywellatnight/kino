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
