//! /api/one-click-play with KINO's own player, and /api/auto-stream, which
//! the player calls for the next episodes. Without a debrid key (P2P) or with
//! mpv, only the KINO app answers (the bare server has neither).

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::play::{self, Episode, Found, text};
use crate::resolve::Resolver;
use crate::server::{Shared, needs_app};
use crate::sources::search::{self, Search};

const NO_SOURCE: &str = "Aucune autre source prête à lire. Vous pouvez réessayer les sources écartées ou ouvrir la fiche.";
const WRONG_FILE: &str = "La source préparée ne contient pas le fichier attendu. Choisissez une autre source dans la fiche.";
static DIGITS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\d+").unwrap());

pub fn router() -> Router<Shared> {
    Router::new()
        .route("/api/one-click-play", post(one_click_play))
        .route("/api/mpv", post(external_player))
        .route("/api/vlc", post(external_player))
        .route("/api/auto-stream", get(auto_stream))
        .route("/api/playback-feedback", post(feedback))
        .route("/api/subtitles", get(subtitles))
        .route("/api/intro-times", get(intro_times))
        .route("/api/trailer", get(trailer))
        .route("/api/subtitle-cues", get(subtitle_cues))
}

fn error(status: StatusCode, message: &str, code: Option<&str>) -> Response {
    let mut body = json!({"error": message});
    if let Some(code) = code {
        body["code"] = json!(code);
    }
    (status, Json(body)).into_response()
}

/// What the page asked for, with KINO's defaults.
struct Play {
    imdb_id: String,
    title: String,
    kind: String,
    is_series: bool,
    season: Value,
    episode: Value,
    series_name: String,
    year: String,
    poster: String,
    built_in: bool,
}

impl Play {
    fn new(body: &Map<String, Value>, cfg: &Map<String, Value>) -> Self {
        let get = |k: &str| body.get(k).cloned().unwrap_or(Value::Null);
        let title = body.get("title").map(text).unwrap_or_else(|| "KINO".into());
        let kind = body.get("type").map(text).unwrap_or_else(|| "movie".into());
        let default_player = if cfg!(target_os = "macos") {
            "integrated"
        } else {
            "kino"
        };
        let mode = [
            get("player_mode"),
            cfg.get("player_mode").cloned().unwrap_or(Value::Null),
        ]
        .into_iter()
        .map(|v| text(&v))
        .find(|v| !v.is_empty())
        .unwrap_or_else(|| default_player.into())
        .to_lowercase();
        let name = text(&get("name"));
        Self {
            title: title.clone(),
            imdb_id: text(&get("imdb_id")),
            is_series: ["series", "anime", "tv"].contains(&kind.as_str()),
            kind,
            season: body.get("season").cloned().unwrap_or(json!(1)),
            episode: body.get("episode").cloned().unwrap_or(json!(1)),
            series_name: if name.is_empty() {
                title.split(" — ").next().unwrap_or("").to_string()
            } else {
                name
            },
            year: text(&get("year")),
            poster: text(&get("poster")),
            built_in: mode == "integrated" || (cfg!(target_os = "macos") && mode != "external"),
        }
    }

    fn number(value: &Value) -> i64 {
        match value {
            Value::String(s) => s.trim().parse().unwrap_or(1),
            other => other
                .as_i64()
                .or_else(|| other.as_f64().map(|f| f as i64))
                .unwrap_or(1),
        }
    }

    fn episode(&self) -> Option<(i64, i64)> {
        self.is_series
            .then(|| (Self::number(&self.season), Self::number(&self.episode)))
    }
}

async fn one_click_play(State(s): State<Shared>, raw: Bytes) -> Response {
    let Ok(Value::Object(body)) =
        serde_json::from_slice::<Value>(if raw.is_empty() { b"{}" } else { &raw })
    else {
        return error(
            StatusCode::BAD_REQUEST,
            "Requête invalide : un objet JSON est attendu.",
            None,
        );
    };
    let cfg = tokio::task::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let (provider, token) = search::provider_and_token(&cfg);
    let play = Play::new(&body, &cfg);
    if (token.is_empty() && s.p2p.is_none()) || (!play.built_in && s.player.is_none()) {
        return needs_app();
    }
    if !play.built_in && crate::player::find().is_none() {
        return error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Aucun lecteur externe compatible (mpv, IINA ou VLC) n'a été trouvé.",
            None,
        );
    }
    let mut film = Value::Null;
    let mut runtime = 0;
    if !play.imdb_id.is_empty()
        && play.kind == "movie"
        && let Ok(found) = s.meta.media(&play.imdb_id, "movie").await
    {
        runtime = DIGITS
            .find(&text(&found["runtime"]))
            .and_then(|m| m.as_str().parse().ok())
            .unwrap_or(0);
        film = found;
    }
    let params: HashMap<String, String> = body.iter().map(|(k, v)| (k.clone(), text(v))).collect();
    let mut query = search::Query::from_params(&params, &cfg);
    if runtime != 0 && query.runtime_minutes.is_none() {
        query.runtime_minutes = Some(runtime);
    }
    let mut sources = Search { meta: &s.meta }
        .streams(&query, &provider, &token, false)
        .await;
    if sources.is_empty() {
        return error(StatusCode::INTERNAL_SERVER_ERROR, play::NO_STREAM, None);
    }
    let quality_first = cfg.get("source_priority").and_then(Value::as_str) == Some("quality");
    if quality_first {
        sources.sort_by_key(|s| s["is_instant"].as_bool() != Some(true));
    }
    let body = Value::Object(body);
    let asked_aside = play::set_aside(&body);
    let mut skip = asked_aside.clone();
    if !play.imdb_id.is_empty() {
        skip.extend(play::excluded(&s.db, &body));
    }
    // No debrid key: the torrents are read peer to peer, once the user agreed.
    let p2p_reader = s
        .p2p
        .clone()
        .filter(|_| token.is_empty())
        .map(|p2p| play::P2pReader {
            p2p,
            base_url: format!("http://127.0.0.1:{}", s.port),
        });
    if let Some(reader) = &p2p_reader
        && reader.p2p.consent().is_none()
        && !sources.iter().any(direct_link)
    {
        return error(StatusCode::CONFLICT, P2P_CONSENT, Some("p2p_consent"));
    }
    let resolver = Resolver {
        net: &s.net,
        provider,
        token,
        hosts: Default::default(),
    };
    let aliases_for = |c: &Value| play::file_aliases(play.is_series, &film, c);
    let tried = |keys: &[String]| {
        let mut all: Vec<String> = asked_aside
            .iter()
            .cloned()
            .chain(keys.iter().cloned())
            .collect::<HashSet<_>>()
            .into_iter()
            .collect();
        all.sort();
        all
    };

    // « La qualité choisie d'abord »: the source the page waited for, or the one to prepare.
    if let Some((torrent_id, key)) = play::prepared(&body).filter(|_| p2p_reader.is_none()) {
        let empty = json!({});
        let candidate = sources
            .iter()
            .find(|c| play::candidate_key(c) == key)
            .unwrap_or(&empty);
        let episode = play.episode();
        let answer = match resolver
            .check(&torrent_id, episode.map(|e| e.0), episode.map(|e| e.1))
            .await
        {
            Ok(a) => a,
            Err(e) => return error(StatusCode::INTERNAL_SERVER_ERROR, &e.0, None),
        };
        if answer["ready"] != json!(true) || answer["files"].as_array().is_none_or(|f| f.is_empty())
        {
            return (
                StatusCode::ACCEPTED,
                Json(play::preparing(&answer, candidate, &key, &sources)),
            )
                .into_response();
        }
        let files = answer["files"].as_array().cloned().unwrap_or_default();
        let Some(file) = play::select_file(&s.meta, &query, &files, &aliases_for(candidate)).await
        else {
            return error(
                StatusCode::UNPROCESSABLE_ENTITY,
                WRONG_FILE,
                Some("prepared_wrong_file"),
            );
        };
        let found = Found {
            candidate: candidate.clone(),
            answer,
            file,
            key: key.clone(),
        };
        return Json(start(&s, &play, &cfg, found, vec![key]).await).into_response();
    }
    if quality_first && !truthy(&body["prefer_ready"]) && p2p_reader.is_none() {
        let chosen: Vec<&Value> = sources
            .iter()
            .filter(|c| truthy(&c["score_parts"]["priority_points"]))
            .collect();
        if !chosen.is_empty()
            && !chosen
                .iter()
                .any(|c| c["is_instant"].as_bool() == Some(true))
            && let Some(best) = chosen
                .into_iter()
                .find(|c| !skip.contains(&play::candidate_key(c)))
        {
            let attempts = play::try_sources(
                &s.meta,
                &resolver,
                std::slice::from_ref(best),
                &query,
                play.episode(),
                aliases_for,
                &HashSet::new(),
            )
            .await;
            if let Some(found) = attempts.found {
                let keys = tried(&attempts.tried);
                return Json(start(&s, &play, &cfg, found, keys).await).into_response();
            }
            if let Some(pending) = attempts.pending.filter(|p| truthy(&p["torrent_id"])) {
                let preparing =
                    play::preparing(&pending, best, &play::candidate_key(best), &sources);
                return (StatusCode::ACCEPTED, Json(preparing)).into_response();
            }
        }
    }
    let attempts = match &p2p_reader {
        Some(reader) => {
            let a = play::try_sources(
                &s.meta,
                reader,
                &sources,
                &query,
                play.episode(),
                aliases_for,
                &skip,
            )
            .await;
            if a.found.is_none()
                && let Some(e) = a
                    .last_error
                    .as_ref()
                    .filter(|e| e.0 == crate::p2p::ENCRYPTION_UNSUPPORTED)
            {
                return error(StatusCode::CONFLICT, &e.0, Some("p2p_encryption"));
            }
            a
        }
        None => {
            play::try_sources(
                &s.meta,
                &resolver,
                &sources,
                &query,
                play.episode(),
                aliases_for,
                &skip,
            )
            .await
        }
    };
    match attempts.found {
        Some(found) => {
            let keys = tried(&attempts.tried);
            Json(start(&s, &play, &cfg, found, keys).await).into_response()
        }
        None => error(
            StatusCode::UNPROCESSABLE_ENTITY,
            NO_SOURCE,
            Some("no_source"),
        ),
    }
}

use play::truthy;

const P2P_CONSENT: &str = "Sans service de débridage, KINO lit les torrents en P2P : acceptez-le une fois pour continuer.";

/// A source an add-on serves as a plain web file: it plays without debrid nor P2P.
fn direct_link(source: &Value) -> bool {
    let url = text(&source["resolve_url"]);
    (url.starts_with("http://") || url.starts_with("https://")) && !truthy(&source["info_hash"])
}

/// Once a source and its file are found: the season's playlist, the next
/// episode in the background, the resume point.
async fn start(
    s: &Shared,
    play: &Play,
    cfg: &Map<String, Value>,
    found: Found,
    tried: Vec<String>,
) -> Value {
    let Found {
        candidate,
        answer,
        file,
        key,
    } = found;
    let download = text(&file["download"]);
    let pref_hash = text(&candidate["info_hash"]);
    let episode = play.episode();
    let mut playlist = vec![];
    if let Some((season, number)) = episode
        && !play.imdb_id.is_empty()
    {
        playlist = play::season_playlist(
            &s.meta,
            &s.links,
            s.port,
            &play.imdb_id,
            season,
            number,
            &download,
            &play.series_name,
            &play.year,
            &play.poster,
            &pref_hash,
            &text(&file["filename"]),
        )
        .await;
        let next = Episode {
            imdb_id: play.imdb_id.clone(),
            season,
            episode: number + 1,
            name: "Série".into(),
            year: String::new(),
            poster: String::new(),
        };
        play::prefetch_next(
            s.meta.clone(),
            s.links.clone(),
            next,
            pref_hash.clone(),
            cfg.clone(),
        );
    }
    let resume = if play.imdb_id.is_empty() {
        0
    } else {
        play::resume_position(
            &crate::library::history(&s.db),
            &play.imdb_id,
            episode.map(|e| e.0),
            episode.map(|e| e.1),
        )
    };
    let count = if playlist.is_empty() {
        1
    } else {
        playlist.len()
    };
    let mut mpv = json!({"mode": "integrated", "playlist_count": count});
    if !play.built_in
        && let Some(player) = &s.player
    {
        let media = (!play.imdb_id.is_empty()).then(|| crate::player::Media {
            id: play.imdb_id.clone(),
            name: play.series_name.clone(),
            kind: play.kind.clone(),
            year: play.year.clone(),
            poster: play.poster.clone(),
            season: episode.map(|e| e.0),
            episode: episode.map(|e| e.1),
            filename: text(&file["filename"]),
        });
        let request = crate::player::Launch {
            url: download.clone(),
            title: play.title.clone(),
            playlist: playlist
                .iter()
                .map(|i| (text(&i["title"]), text(&i["url"])))
                .collect(),
            media,
            start_sec: resume,
        };
        mpv = player
            .launch(request)
            .unwrap_or_else(|e| json!({"error": e, "playlist_count": count}));
    }
    json!({"ok": true, "source_key": key, "attempted_source_keys": tried, "chosen_torrent": play::chosen_label(&candidate),
           "debrid": answer, "file": file, "stream_url": download, "playlist": playlist,
           "mpv": mpv, "resume_sec": resume})
}

/// « Signaler un problème » on a source, or every source of a title given back.
async fn feedback(
    State(s): State<Shared>,
    crate::body::JsonBody(body): crate::body::JsonBody,
) -> Response {
    let context = if play::truthy(&body["context"]) {
        body["context"].clone()
    } else {
        json!({})
    };
    let done = match body.get("action").map(text).as_deref() {
        None | Some("report") => {
            play::report_source(&s.db, &context, &body["source_key"], &body["reason"])
        }
        Some("clear") => play::clear_sources(&s.db, &context),
        Some(_) => Err("action"),
    };
    match done {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(_) => error(
            StatusCode::BAD_REQUEST,
            "Contenu, source ou action invalide.",
            None,
        ),
    }
}

async fn auto_stream(
    State(s): State<Shared>,
    Query(p): Query<HashMap<String, String>>,
) -> Response {
    let cfg = tokio::task::spawn_blocking(search::load_settings)
        .await
        .unwrap_or_default();
    let ep = Episode::from_params(&p);
    let pref_hash = p
        .get("pref_hash")
        .map(|h| h.trim().to_lowercase())
        .unwrap_or_default();
    match play::resolve_episode(&s.meta, &s.links, &ep, &pref_hash, &cfg, true).await {
        Ok(link) => (
            StatusCode::FOUND,
            [
                (header::LOCATION, link),
                (header::CACHE_CONTROL, "no-store".to_string()),
            ],
        )
            .into_response(),
        Err(e) => error(StatusCode::INTERNAL_SERVER_ERROR, &e.0, None),
    }
}

/// French and English subtitles for the release being played.
async fn subtitles(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let get = |k: &str| p.get(k).cloned().unwrap_or_default();
    // int(value or 1): a missing or empty number is 1, season 0 (specials) stays.
    let number = |k: &str| {
        p.get(k)
            .filter(|v| !v.is_empty())
            .and_then(|v| v.trim().parse::<i64>().ok())
            .unwrap_or(1)
    };
    let release: String = get("release").chars().take(300).collect();
    let fps = p
        .get("fps")
        .and_then(|f| f.trim().parse::<f64>().ok())
        .filter(|f| *f > 1.0 && *f < 200.0);
    let kind = p.get("type").cloned().unwrap_or_else(|| "movie".into());
    let id = get("imdb_id");
    let found = if id.is_empty() {
        vec![]
    } else {
        crate::subtitles::opensubtitles(
            &s.meta,
            &id,
            &kind,
            number("season"),
            number("episode"),
            &release,
            fps,
        )
        .await
    };
    Json(json!({"subtitles": found})).into_response()
}

/// The lines of a subtitle file KINO listed, for its own player.
async fn subtitle_cues(
    State(s): State<Shared>,
    Query(p): Query<HashMap<String, String>>,
) -> Response {
    let url = p
        .get("url")
        .map(|u| u.trim().to_string())
        .unwrap_or_default();
    if url.is_empty() || !url.starts_with("http") {
        return Json(json!({"cues": []})).into_response();
    }
    if !crate::subtitles::may_fetch(&url) {
        return error(
            StatusCode::BAD_REQUEST,
            "Adresse de sous-titres refusée.",
            None,
        );
    }
    let fetched = s
        .http
        .get(&url)
        .header("User-Agent", crate::net::BROWSER)
        .timeout(std::time::Duration::from_secs(6))
        .send()
        .await
        .and_then(reqwest::Response::error_for_status);
    let raw = match fetched {
        Ok(r) => r.bytes().await.ok(),
        Err(_) => None,
    };
    match raw {
        Some(raw) => {
            Json(json!({"cues": crate::subtitles::parse_srt(&crate::subtitles::decode(&raw))}))
                .into_response()
        }
        None => error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Sous-titres indisponibles pour le moment.",
            None,
        ),
    }
}

/// Opening and ending times of an episode, for « Passer le générique ».
async fn intro_times(
    State(s): State<Shared>,
    Query(p): Query<HashMap<String, String>>,
) -> Response {
    let get = |k: &str| p.get(k).map(|v| v.trim().to_string()).unwrap_or_default();
    let number = |k: &str| {
        p.get(k)
            .filter(|v| !v.is_empty())
            .and_then(|v| v.trim().parse::<i64>().ok())
            .filter(|v| *v != 0)
            .unwrap_or(1)
    };
    let mut title = get("title");
    let kind = p
        .get("type")
        .map(|v| v.trim().to_string())
        .unwrap_or_else(|| "series".into());
    let imdb_id = get("imdb_id");
    if title.is_empty()
        && !imdb_id.is_empty()
        && let Ok(found) = s.meta.media(&imdb_id, "movie").await
    {
        // Cinemeta names a title « name »; the Python version read « title » and found nothing.
        title = found["name"].as_str().unwrap_or("").to_string();
    }
    let url = get("url");
    let found = crate::intro::intro_times(
        &s.meta,
        &title,
        &kind,
        number("season"),
        number("episode"),
        Some(url.as_str()).filter(|u| !u.is_empty()),
        &imdb_id,
    )
    .await;
    Json(found).into_response()
}

async fn trailer(State(s): State<Shared>, Query(p): Query<HashMap<String, String>>) -> Response {
    let get = |k: &str| p.get(k).cloned().unwrap_or_default();
    let lang = p.get("lang").cloned().unwrap_or_else(|| "vf".into());
    Json(crate::trailer::trailer(&s.meta, &get("title"), &get("year"), &get("yt_id"), &lang).await)
        .into_response()
}

static OFFLINE_MEDIA: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^/api/offline/media/([0-9a-f]{32})$").unwrap());

/// The file of a completed offline download addressed by its KINO route
/// (« /api/offline/media/<id> »): FFmpeg, ffprobe and mpv need the file itself.
pub fn offline_media_file(s: &Shared, source: &str) -> Option<String> {
    let id = OFFLINE_MEDIA.captures(source.trim())?[1].to_string();
    let path = s.offline.asset(&id, "media").filter(|p| p.is_file())?;
    Some(path.display().to_string())
}

/// « Lire dans mpv / VLC » from a source list: the file (or a pack's files, or
/// the season from this episode on) in the external player, from the resume point.
async fn external_player(State(s): State<Shared>, raw: Bytes) -> Response {
    let Some(player) = s.player.clone() else {
        return needs_app();
    };
    let body: Value = serde_json::from_slice(&raw).unwrap_or_else(|_| json!({}));
    let url = text(&body["url"]);
    let url = offline_media_file(&s, &url).unwrap_or(url);
    let filename = text(&body["filename"]);
    let media = body["media"].as_object().cloned().unwrap_or_default();
    let get = |k: &str| media.get(k).map(text).unwrap_or_default();
    let number = |k: &str| {
        media
            .get(k)
            .filter(|v| truthy(v))
            .and_then(|v| v.as_i64().or_else(|| text(v).trim().parse().ok()))
    };
    let mut playlist = vec![];
    let pack: Vec<&Value> = body["pack_files"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|f| truthy(&f["download"]))
        .collect();
    if body["pack_files"].as_array().is_some_and(|p| p.len() > 1) {
        playlist = pack
            .iter()
            .map(|f| {
                let name = text(&f["filename"]);
                (
                    if name.is_empty() {
                        "Épisode".into()
                    } else {
                        name
                    },
                    text(&f["download"]),
                )
            })
            .collect();
    } else if get("type") == "series" && !get("id").is_empty() {
        let cfg = tokio::task::spawn_blocking(search::load_settings)
            .await
            .unwrap_or_default();
        let (season, episode) = (
            number("season").unwrap_or(1),
            number("episode").unwrap_or(1),
        );
        playlist = play::season_playlist(
            &s.meta,
            &s.links,
            s.port,
            &get("id"),
            season,
            episode,
            &url,
            &get("name"),
            &get("year"),
            &get("poster"),
            "",
            &filename,
        )
        .await
        .iter()
        .map(|i| (text(&i["title"]), text(&i["url"])))
        .collect();
        let next = Episode {
            imdb_id: get("id"),
            season,
            episode: episode + 1,
            name: String::new(),
            year: String::new(),
            poster: String::new(),
        };
        play::prefetch_next(s.meta.clone(), s.links.clone(), next, String::new(), cfg);
    }
    let mut title = filename.clone();
    if !get("name").is_empty() {
        title = get("name");
        if let (Some(season), Some(episode)) = (number("season"), number("episode")) {
            title = format!("{} — S{season:02}E{episode:02}", get("name"));
        }
    }
    let mut resume = 0;
    let context = (!get("id").is_empty()).then(|| crate::player::Media {
        id: get("id"),
        name: media
            .get("name")
            .map(text)
            .unwrap_or_else(|| filename.clone()),
        kind: media
            .get("type")
            .map(text)
            .unwrap_or_else(|| "movie".into()),
        year: get("year"),
        poster: get("poster"),
        season: number("season"),
        episode: number("episode"),
        filename: filename.clone(),
    });
    if let Some(m) = &context {
        resume = play::resume_position(&crate::library::history(&s.db), &m.id, m.season, m.episode);
    }
    let request = crate::player::Launch {
        url,
        title,
        playlist,
        media: context.clone(),
        start_sec: resume,
    };
    match player.launch(request) {
        Ok(answer) => {
            if let Some(m) = context {
                let db = s.db.clone();
                let _ = tokio::task::spawn_blocking(move || {
                    crate::library::record_history(&db, &m.start_entry())
                })
                .await;
            }
            Json(json!({"ok": true, "mpv": answer, "resume_sec": resume})).into_response()
        }
        Err(e) => error(StatusCode::INTERNAL_SERVER_ERROR, &e, None),
    }
}
