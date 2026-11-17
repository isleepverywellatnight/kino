//! KINO's local HTTP server: the interface, the API, and while KINO moves to
//! Rust, every route not ported yet forwarded to the Python server.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use axum::body::{Body, Bytes};
use axum::extract::{ConnectInfo, Request, State};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode, Uri, header};
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
    /// The Python server for the routes not ported yet (`KINO_LEGACY`).
    pub legacy: Option<String>,
    pub http: reqwest::Client,
    pub net: Arc<Net>,
    pub sites: Sites,
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
    request: Request,
    next: Next,
) -> Response {
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

async fn index(State(state): State<Shared>) -> Response {
    let mut page = tokio::fs::read_to_string(state.web_root.join("index.html"))
        .await
        .unwrap_or_else(|_| {
            "<!DOCTYPE html><html><body><h1>KINO</h1><p>Interface introuvable.</p></body></html>"
                .to_string()
        });
    // The language chosen in Paramètres, known before web/js/i18n.js runs
    // (Windows' WebView does not keep localStorage between launches).
    if let Some(lang) = config::ui_language() {
        page = page.replacen(
            "<head>",
            &format!("<head><script>window.KINO_UI_LANGUAGE=\"{lang}\";</script>"),
            1,
        );
    }
    no_cache(([(header::CONTENT_TYPE, "text/html; charset=utf-8")], page).into_response())
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

async fn fallback(
    State(state): State<Shared>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let path = uri.path();
    if method == Method::GET && is_static(path) {
        return static_file(&state, path).await;
    }
    if method == Method::GET && path == "/qrcode.min.js" {
        return static_file(&state, "js/vendor/qrcode.min.js").await;
    }
    match &state.legacy {
        Some(base) => forward(&state, base, method, &uri, &headers, body).await,
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "Route introuvable"})),
        )
            .into_response(),
    }
}

/// Sends a request on to the Python server and streams its answer back.
async fn forward(
    state: &AppState,
    base: &str,
    method: Method,
    uri: &Uri,
    headers: &HeaderMap,
    body: Bytes,
) -> Response {
    let url = format!(
        "{}{}",
        base.trim_end_matches('/'),
        uri.path_and_query().map_or("/", |p| p.as_str())
    );
    let mut request = state.http.request(method, &url).body(body);
    for (name, value) in headers {
        // The Python server checks the origin and host of its own port: the
        // request was already checked here.
        if !matches!(
            name.as_str(),
            "host"
                | "origin"
                | "connection"
                | "content-length"
                | "sec-fetch-site"
                | "transfer-encoding"
        ) {
            request = request.header(name, value);
        }
    }
    match request.send().await {
        Ok(answer) => {
            let mut response = Response::builder().status(answer.status().as_u16());
            for (name, value) in answer.headers() {
                if !matches!(
                    name.as_str(),
                    "connection" | "transfer-encoding" | "content-length"
                ) {
                    response = response.header(name, value);
                }
            }
            response
                .body(Body::from_stream(answer.bytes_stream()))
                .unwrap_or_else(|_| StatusCode::BAD_GATEWAY.into_response())
        }
        Err(_) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": "Le serveur de KINO ne répond pas."})),
        )
            .into_response(),
    }
}
