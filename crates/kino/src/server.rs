//! KINO's local HTTP server: the interface and the API.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{ConnectInfo, Request, State};
use axum::http::{HeaderValue, Method, StatusCode, Uri, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use serde_json::json;

use crate::manga::sources::Sites;
use crate::net::Net;
use crate::{access, config, db::Db};

pub struct AppState {
    pub db: Arc<Db>,
    pub port: u16,
    pub pairing_token: String,
    pub web_root: PathBuf,
    pub http: reqwest::Client,
    pub net: Arc<Net>,
    pub sites: Sites,
    pub meta: crate::meta::Meta,
    pub accounts: crate::debrid::Accounts,
    /// Episode links already found, for the player's next episodes.
    pub links: Arc<crate::play::EpisodeLinks>,
    /// The offline library and its downloads.
    pub offline: Arc<crate::offline::Library>,
    /// The suggestion pool, read again when it changes.
    pub suggestions: crate::recommender::Store,
    /// What the window offers (set by the desktop app): the native player, notifications.
    pub native_player: bool,
    pub notifications: bool,
    /// The external player (mpv, IINA, VLC), when this process has the window.
    pub player: Option<Arc<crate::player::Player>>,
    /// The phone remote: the player's state and the commands waiting for it.
    pub remote: Arc<crate::remote::Remote>,
    /// « Lire sur la TV », when the window runs in this process.
    pub tv: Option<Arc<crate::tv::Cast>>,
    /// The system's notifications (new episodes), when the window gives them.
    pub notifier: Option<Arc<dyn crate::alerts::Notifier>>,
    /// KINO's native player (libmpv under the page), when the window has it.
    pub native: Option<Arc<dyn crate::native::NativePlayer>>,
    /// The page's « voix » and « clarté » choices for the native playback.
    pub native_settings: std::sync::Mutex<serde_json::Map<String, serde_json::Value>>,
    /// Torrents read peer to peer without a debrid key, when the window runs here.
    pub p2p: Option<Arc<crate::p2p::P2p>>,
}

pub type Shared = Arc<AppState>;

pub fn router(state: Shared) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/index.html", get(index))
        .merge(crate::routes::router())
        .fallback(fallback)
        .layer(middleware::from_fn_with_state(state.clone(), guard))
        .with_state(state)
}

async fn guard(
    State(state): State<Shared>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    mut request: Request,
    next: Next,
) -> Response {
    // The routes are told the real peer (the TV stream checks it), never one
    // a client wrote itself.
    request.headers_mut().remove("x-kino-peer");
    if let Ok(value) = HeaderValue::from_str(&peer.ip().to_string()) {
        request.headers_mut().insert("x-kino-peer", value);
    }
    let token = request.uri().query().and_then(|q| {
        q.split('&')
            .find_map(|pair| pair.strip_prefix("token="))
            .map(|t| t.to_string())
    });
    if !access::request_allowed(
        peer.ip(),
        request.uri().path(),
        request.headers(),
        token.as_deref(),
        state.port,
        &state.pairing_token,
    ) {
        return StatusCode::FORBIDDEN.into_response();
    }
    next.run(request).await
}

fn no_cache(mut response: Response) -> Response {
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    response
}

/// The page runs only KINO's own scripts: no inline code, no plug-in, no
/// other base address. Text from a site that slipped into the page as markup
/// still could not run.
const PAGE_POLICY: &str = "script-src 'self'; object-src 'none'; base-uri 'self'";

async fn index(State(state): State<Shared>) -> Response {
    let mut page = tokio::fs::read_to_string(state.web_root.join("index.html"))
        .await
        .unwrap_or_else(|_| {
            "<!DOCTYPE html><html><body><h1>KINO</h1><p>Interface introuvable.</p></body></html>"
                .to_string()
        });
    // The language chosen in Paramètres, known before web/js/i18n.js runs
    // (Windows' WebView does not keep localStorage between launches): data,
    // not code, as the page runs no inline script.
    if let Some(lang) = config::ui_language() {
        page = page.replacen(
            "<head>",
            &format!("<head><meta name=\"kino-ui-language\" content=\"{lang}\">"),
            1,
        );
    }
    no_cache(
        (
            [
                (header::CONTENT_TYPE, "text/html; charset=utf-8"),
                (header::CONTENT_SECURITY_POLICY, PAGE_POLICY),
            ],
            page,
        )
            .into_response(),
    )
}

fn content_type(path: &str) -> &'static str {
    match path.rsplit('.').next().unwrap_or("") {
        "css" => "text/css; charset=utf-8",
        "js" => "application/javascript; charset=utf-8",
        "png" => "image/png",
        "svg" => "image/svg+xml",
        _ => "application/octet-stream",
    }
}

/// A file of the interface, never outside its folder.
async fn static_file(state: &AppState, relative: &str) -> Response {
    let relative = relative.trim_start_matches('/');
    let relative = relative
        .strip_prefix("static/")
        .or_else(|| relative.strip_prefix("web/"))
        .unwrap_or(relative);
    if relative
        .split('/')
        .any(|part| part == ".." || part.is_empty())
    {
        return StatusCode::NOT_FOUND.into_response();
    }
    let root = match tokio::fs::canonicalize(&state.web_root).await {
        Ok(root) => root,
        Err(_) => return StatusCode::NOT_FOUND.into_response(),
    };
    match tokio::fs::canonicalize(root.join(relative)).await {
        Ok(target) if target.starts_with(&root) => match tokio::fs::read(&target).await {
            Ok(data) => {
                no_cache(([(header::CONTENT_TYPE, content_type(relative))], data).into_response())
            }
            Err(_) => StatusCode::NOT_FOUND.into_response(),
        },
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

fn is_static(path: &str) -> bool {
    path == "/icons.svg"
        || ["/css/", "/js/", "/static/", "/web/"]
            .iter()
            .any(|p| path.starts_with(p))
}

/// What only the KINO app can do (its window, player, peer-to-peer engine),
/// asked of the bare server.
pub fn needs_app() -> Response {
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(json!({"error": "Disponible dans l'application KINO."})),
    )
        .into_response()
}

async fn fallback(State(state): State<Shared>, method: Method, uri: Uri) -> Response {
    let path = uri.path();
    if method == Method::GET && is_static(path) {
        return static_file(&state, path).await;
    }
    if method == Method::GET && path == "/qrcode.min.js" {
        return static_file(&state, "js/vendor/qrcode.min.js").await;
    }
    (
        StatusCode::NOT_FOUND,
        Json(json!({"error": "Route introuvable"})),
    )
        .into_response()
}
