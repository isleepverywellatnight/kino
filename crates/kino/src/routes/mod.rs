//! The API routes ported to Rust, by subject.

pub mod anime;
pub mod library;
pub mod manga;
pub mod native;
pub mod offline;
pub mod p2p;
pub mod play;
pub mod remote;
pub mod series;
pub mod settings;
pub mod sources;
pub mod suggestions;
pub mod titles;
pub mod tv;
pub mod window;

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
        .merge(suggestions::router())
        .merge(anime::router())
        .merge(offline::router())
        .merge(remote::router())
        .merge(tv::router())
        .merge(window::router())
        .merge(native::router())
        .merge(p2p::router())
}
