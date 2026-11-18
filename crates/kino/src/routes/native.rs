//! « Lecteur natif » (macOS): offline films and Debrid sources played by the
//! window's libmpv. Answered here when the window runs in this process.

use axum::Json;
use axum::Router;
use axum::extract::{Request, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use serde_json::{Value, json};

use crate::native::{NativePlayer, cues_to_srt, decode_subtitle, mpv_commands, stream_allowed};
use crate::play::text;
use crate::server::{Shared, needs_app};

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/native-player/state", get(state))
        .route("/api/native-player/open", post(open))
        .route("/api/native-player/command", post(command))
        .route("/api/native-player/close", post(close))
        .route("/api/native-player/screenshot", post(screenshot))
        .route("/api/native-player/subtitle", post(subtitle))
}

async fn json_body(request: Request) -> Value {
    let body = axum::body::to_bytes(request.into_body(), 1 << 20)
        .await
        .unwrap_or_default();
    serde_json::from_slice(&body).unwrap_or_else(|_| json!({}))
}

fn failure(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"error": message}))).into_response()
}

const INVALID: &str = "Commande du lecteur invalide.";
const BROKEN: &str = "Le lecteur natif n'a pas pu démarrer.";

/// The native player, or the answer when there is none.
async fn player(
    s: &Shared,
    request: Request,
) -> Result<(std::sync::Arc<dyn NativePlayer>, Request), Response> {
    if s.player.is_none() {
        return Err(needs_app());
    }
    match s.native.clone() {
        Some(native) => Ok((native, request)),
        None => Err(failure(
            StatusCode::SERVICE_UNAVAILABLE,
            "Lecteur natif indisponible sur cette installation.",
        )),
    }
}

fn ok(state: Value) -> Response {
    Json(json!({"ok": true, "state": state})).into_response()
}

async fn blocking<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> Option<T> {
    tokio::task::spawn_blocking(work).await.ok()
}

async fn state(State(s): State<Shared>) -> Response {
    if s.player.is_none() {
        return needs_app();
    }
    let state = match s.native.clone() {
        Some(native) => with_settings(
            &s,
            blocking(move || native.state())
                .await
                .unwrap_or(json!({"open": false})),
        ),
        None => json!({"open": false}),
    };
    Json(state).into_response()
}

async fn open(State(s): State<Shared>, request: Request) -> Response {
    let (native, request) = match player(&s, request).await {
        Ok(found) => found,
        Err(answer) => return answer,
    };
    let body = json_body(request).await;
    let download_id = text(&body["offline_id"]);
    let path = match body.get("stream_url").filter(|v| !v.is_null()) {
        Some(stream) if download_id.is_empty() => {
            let stream = text(stream);
            if !stream_allowed(&stream, s.port) {
                return failure(StatusCode::BAD_REQUEST, "Adresse de lecture refusée.");
            }
            Some(stream)
        }
        _ if download_id.len() == 32
            && download_id
                .chars()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()) =>
        {
            crate::routes::play::offline_media_file(
                &s,
                &format!("/api/offline/media/{download_id}"),
            )
        }
        _ => None,
    };
    let Some(path) = path else {
        return failure(StatusCode::NOT_FOUND, "Ce film hors ligne est introuvable.");
    };
    let start = body["start"]
        .as_f64()
        .filter(|_| !body["start"].is_boolean())
        .unwrap_or(0.0)
        .clamp(0.0, 360000.0);
    let volume = body["volume"].as_f64();
    let pref_lang = blocking(crate::config::load)
        .await
        .map(|c| c.pref_lang)
        .unwrap_or_default();
    let opener = native.clone();
    let lang = pref_lang;
    let Some(Ok(mut state)) = blocking(move || opener.open(&path, start, volume, lang)).await
    else {
        return failure(StatusCode::INTERNAL_SERVER_ERROR, BROKEN);
    };
    // The subtitle kept with an offline film: selected for « VOSTFR » when French.
    if !download_id.is_empty()
        && let Some(subtitle) = s.offline.subtitles(&download_id)
    {
        let srt = cues_to_srt(&subtitle["cues"]);
        let lang: String = text(&subtitle["lang"]).chars().take(8).collect();
        if !srt.is_empty() {
            let select = pref_lang == crate::config::PrefLang::Vostfr && lang == "fr";
            let adder = native.clone();
            if let Some(Ok(())) =
                blocking(move || adder.add_subtitle(&srt, "gardé avec le film", &lang, select))
                    .await
            {
                let reader = native.clone();
                state = blocking(move || reader.state()).await.unwrap_or(state);
            }
        }
    }
    s.native_settings
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clear();
    ok(with_settings(&s, state))
}

async fn command(State(s): State<Shared>, request: Request) -> Response {
    let (native, request) = match player(&s, request).await {
        Ok(found) => found,
        Err(answer) => return answer,
    };
    let body = json_body(request).await;
    let (cmd, value) = (text(&body["cmd"]), body["value"].clone());
    let Ok(commands) = mpv_commands(&cmd, &value) else {
        return failure(StatusCode::BAD_REQUEST, INVALID);
    };
    let runner = native.clone();
    match blocking(move || runner.command(&commands)).await {
        Some(Ok(())) => {
            if cmd == "voice" || cmd == "clarity" {
                let mut settings = s.native_settings.lock().unwrap_or_else(|e| e.into_inner());
                crate::native::remember(&mut settings, &cmd, &value);
            }
            ok(with_settings(
                &s,
                blocking(move || native.state())
                    .await
                    .unwrap_or(json!({"open": false})),
            ))
        }
        _ => failure(StatusCode::INTERNAL_SERVER_ERROR, BROKEN),
    }
}

/// The page's « voix » and « clarté » choices in the state.
fn with_settings(s: &Shared, mut state: Value) -> Value {
    if state["open"] == true {
        let settings = s.native_settings.lock().unwrap_or_else(|e| e.into_inner());
        state["voice"] = settings.get("voice").cloned().unwrap_or(json!("normal"));
        state["clarity"] = settings.get("clarity").cloned().unwrap_or(json!(0));
    }
    state
}

async fn close(State(s): State<Shared>, request: Request) -> Response {
    let (native, _) = match player(&s, request).await {
        Ok(found) => found,
        Err(answer) => return answer,
    };
    s.native_settings
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clear();
    ok(blocking(move || native.close())
        .await
        .unwrap_or(json!({"open": false})))
}

/// The current frame in the download folder; FFmpeg reads the same moment
/// from the file when this mpv cannot convert a hardware-decoded frame.
async fn screenshot(State(s): State<Shared>, request: Request) -> Response {
    let (native, request) = match player(&s, request).await {
        Ok(found) => found,
        Err(answer) => return answer,
    };
    let body = json_body(request).await;
    let (title, time) = (text(&body["title"]), text(&body["time_str"]));
    let name = crate::routes::window::capture_name(&title, &time, 20);
    let folder = blocking(crate::routes::window::download_dir)
        .await
        .unwrap_or_default();
    let _ = std::fs::create_dir_all(&folder);
    let path = folder.join(&name);
    let target = path.clone();
    let shooter = native.clone();
    let saved = match blocking(move || shooter.screenshot(&target)).await {
        Some(Ok(())) if path.is_file() => true,
        _ => match native.source_and_time() {
            Some((source, at)) => extract_frame(&source, at, &path).await,
            None => false,
        },
    };
    if saved {
        Json(json!({"ok": true, "filename": name})).into_response()
    } else {
        failure(StatusCode::INTERNAL_SERVER_ERROR, BROKEN)
    }
}

/// One frame of the film at `seconds`, written by FFmpeg.
async fn extract_frame(source: &str, seconds: f64, path: &std::path::Path) -> bool {
    let remote = source.starts_with("https://") || source.starts_with("http://127.0.0.1:");
    let Some(ffmpeg) = crate::intro::find_tool("ffmpeg") else {
        return false;
    };
    if source.is_empty() || !(remote || std::path::Path::new(source).is_file()) {
        return false;
    }
    let run = tokio::process::Command::new(ffmpeg)
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            &format!("{:.3}", seconds.max(0.0)),
            "-i",
            source,
            "-frames:v",
            "1",
            "-y",
        ])
        .arg(path)
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true)
        .output();
    matches!(tokio::time::timeout(std::time::Duration::from_secs(30), run).await, Ok(Ok(out)) if out.status.success())
        && path.is_file()
}

/// A subtitle KINO listed, fetched here and handed to mpv as a file.
async fn subtitle(State(s): State<Shared>, request: Request) -> Response {
    let (native, request) = match player(&s, request).await {
        Ok(found) => found,
        Err(answer) => return answer,
    };
    let body = json_body(request).await;
    let url = text(&body["url"]);
    let Some(lang) = crate::subtitles::listed_lang(&url) else {
        return failure(StatusCode::BAD_REQUEST, "Sous-titre inconnu.");
    };
    let fetched = async {
        let answer = s
            .http
            .get(&url)
            .timeout(std::time::Duration::from_secs(8))
            .send()
            .await
            .ok()?;
        answer.bytes().await.ok()
    };
    let Some(raw) = fetched.await else {
        return failure(
            StatusCode::BAD_GATEWAY,
            "Sous-titre indisponible pour le moment.",
        );
    };
    if raw.len() > crate::native::SUBTITLE_MAX_BYTES {
        return failure(StatusCode::BAD_REQUEST, "Sous-titre trop volumineux.");
    }
    let srt = decode_subtitle(&raw);
    if crate::subtitles::parse_srt(&srt).is_empty() {
        return failure(StatusCode::BAD_REQUEST, "Sous-titre illisible.");
    }
    let label: String = body
        .get("label")
        .map(text)
        .filter(|l| !l.is_empty())
        .unwrap_or_else(|| "OpenSubtitles".into())
        .chars()
        .take(60)
        .collect();
    let adder = native.clone();
    match blocking(move || adder.add_subtitle(&srt, &label, &lang, true)).await {
        Some(Ok(())) => ok(with_settings(
            &s,
            blocking(move || native.state())
                .await
                .unwrap_or(json!({"open": false})),
        )),
        _ => failure(StatusCode::INTERNAL_SERVER_ERROR, BROKEN),
    }
}
