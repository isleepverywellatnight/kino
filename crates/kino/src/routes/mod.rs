//! The API routes ported to Rust, by subject.

use axum::Router;

use crate::server::Shared;

pub fn router() -> Router<Shared> {
    Router::new()
}
