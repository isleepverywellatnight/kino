//! The API routes ported to Rust, by subject.

pub mod manga;

use axum::Router;

use crate::server::Shared;

pub fn router() -> Router<Shared> {
    Router::new().merge(manga::router())
}
