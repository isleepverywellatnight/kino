//! The API routes ported to Rust, by subject.

pub mod library;
pub mod manga;
pub mod play;
pub mod series;
pub mod settings;
pub mod sources;
pub mod titles;

use axum::Router;

use crate::server::Shared;

pub fn router() -> Router<Shared> {
    Router::new()
        .merge(manga::router())
        .merge(library::router())
        .merge(titles::router())
        .merge(settings::router())
        .merge(sources::router())
        .merge(play::router())
        .merge(series::router())
}
