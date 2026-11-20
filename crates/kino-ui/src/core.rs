//! KINO's core inside the native interface (phase G2): the same database,
//! network client and metadata as the server (`kino::launch::serve`), on a
//! tokio runtime of their own. Screens ask it for data as typed values; gpui's
//! executors await the answers, the work runs on tokio.

use std::sync::Arc;
use std::time::Duration;

use gpui::SharedString;
use kino::db::Db;
use kino::meta::Meta;
use kino::recommender::Store;
use serde_json::Value;

use crate::image_cache::Loader;
use crate::title::{Details, Title};

/// The catalogs the Discover screens show.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Catalog {
    Movies,
    Series,
    Anime,
}

impl Catalog {
    fn kind(self) -> &'static str {
        match self {
            Catalog::Movies => "movie",
            Catalog::Series => "series",
            Catalog::Anime => "anime",
        }
    }
}

/// The order of a catalog, as the web's sort menu offers it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Sort {
    /// What is popular now.
    #[default]
    Top,
    /// Best rated first.
    Rating,
    Recent,
    Oldest,
}

impl Sort {
    pub const ALL: [Sort; 4] = [Sort::Top, Sort::Rating, Sort::Recent, Sort::Oldest];

    /// The value KINO's catalogs take (`CATALOG_SORTS` on the web).
    pub fn param(self) -> &'static str {
        match self {
            Sort::Top => "top",
            Sort::Rating => "imdbRating",
            Sort::Recent => "recent",
            Sort::Oldest => "oldest",
        }
    }
}

/// Which part of a catalog to show: a genre ("" for all) in an order.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Filter {
    /// The catalog's genre value ("Action", "Slice of Life"…), "" for all.
    pub genre: &'static str,
    pub sort: Sort,
}

#[derive(Clone)]
pub struct Core {
    runtime: Arc<tokio::runtime::Runtime>,
    /// KINO's server, running in this process: the phone remote, the TV and
    /// the routes this interface calls (`api`).
    server: kino::server::Shared,
    db: Arc<Db>,
    meta: Meta,
    /// KINO's suggestion pool (« Pour vous »), the server's own.
    suggestions: Arc<Store>,
}

/// What KINO's player asks of the window. The external player opens in its
/// own window; the native interface keeps showing.
struct Hooks;

impl kino::player::WindowHooks for Hooks {
    fn hide(&self) {}
    fn show(&self) {}
    fn geometry(&self) -> Option<(i32, i32, u32, u32)> {
        None
    }
}

/// The first free port from 8080, as the desktop app picks it.
fn free_port(start: u16) -> u16 {
    (start..start.saturating_add(10))
        .find(|p| std::net::TcpListener::bind(("127.0.0.1", *p)).is_ok())
        .unwrap_or(start)
}

impl Core {
    /// Starts KINO's runtime and its server in this process (database,
    /// network, metadata, the player, the phone remote and the launch work),
    /// as the desktop app does.
    ///
    /// # Errors
    /// When the runtime cannot start or the database cannot open.
    pub fn start() -> Result<Self, String> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(4)
            .thread_name("kino-core")
            .enable_all()
            .build()
            .map_err(|e| format!("cannot start KINO's runtime: {e}"))?;
        let port = free_port(8080);
        let options = kino::launch::Options {
            port,
            pairing_token: kino::launch::new_token(),
            native_player: false,
            notifications: false,
            startup_tasks: true,
            window: Some(Arc::new(Hooks)),
            notifier: None,
            remote: None,
            native: None,
        };
        // The launch work spawns tasks: inside the runtime.
        let server = {
            let _entered = runtime.enter();
            kino::launch::state(options)
                .map_err(|e| format!("cannot open KINO's database: {e:#}"))?
        };
        let serving = server.clone();
        runtime.spawn(async move {
            match kino::launch::bind(port).await {
                Ok(listener) => {
                    if let Err(e) = kino::launch::serve_state(listener, serving).await {
                        eprintln!("KINO server stopped: {e:#}");
                    }
                }
                Err(e) => eprintln!("KINO's port {port} is taken: {e:#}"),
            }
        });
        Ok(Self {
            db: server.db.clone(),
            meta: server.meta.clone(),
            suggestions: server.suggestions.clone(),
            server,
            runtime: Arc::new(runtime),
        })
    }

    /// Calls one of KINO's routes in this process, as the web page does over
    /// HTTP: `(status, JSON answer)`. The request is a local one (loopback),
    /// so the routes the page may call are allowed.
    pub fn api(
        &self,
        method: &'static str,
        path: String,
        body: Option<Value>,
    ) -> impl Future<Output = (u16, Value)> + use<> {
        let server = self.server.clone();
        let task = self.runtime.spawn(async move {
            use tower::ServiceExt;
            let local = std::net::SocketAddr::from(([127, 0, 0, 1], server.port));
            let mut request = axum::http::Request::builder()
                .method(method)
                .uri(&path)
                .header(
                    axum::http::header::HOST,
                    format!("127.0.0.1:{}", server.port),
                );
            let body = match body {
                Some(body) => {
                    request = request.header(axum::http::header::CONTENT_TYPE, "application/json");
                    axum::body::Body::from(body.to_string())
                }
                None => axum::body::Body::empty(),
            };
            let Ok(mut request) = request.body(body) else {
                return (400, Value::Null);
            };
            request
                .extensions_mut()
                .insert(axum::extract::ConnectInfo(local));
            let Ok(response) = kino::server::router(server).oneshot(request).await;
            let status = response.status().as_u16();
            let bytes = axum::body::to_bytes(response.into_body(), 64 << 20)
                .await
                .unwrap_or_default();
            (
                status,
                serde_json::from_slice(&bytes).unwrap_or(Value::Null),
            )
        });
        async move { task.await.unwrap_or((500, Value::Null)) }
    }

    /// A page of `catalog` in `filter`'s genre and order, `skip` items in. Runs on KINO's
    /// runtime; the returned future can be awaited from gpui.
    pub fn catalog(
        &self,
        catalog: Catalog,
        filter: Filter,
        skip: u64,
    ) -> impl Future<Output = Result<Vec<Title>, String>> + use<> {
        let meta = self.meta.clone();
        let (genre, sort) = (filter.genre, filter.sort.param());
        let task = self.runtime.spawn(async move {
            let page = match catalog {
                // KINO's own anime catalog (AniList and Kitsu), not Cinemeta's.
                Catalog::Anime => meta.anime_catalog_page(genre, skip, sort).await,
                _ => meta.catalog_top(catalog.kind(), genre, skip, sort).await,
            };
            page.map(|metas| {
                metas
                    .iter()
                    .filter_map(Title::from_meta)
                    .map(|mut title| {
                        // Anime pages come from KINO's anime sources.
                        if catalog == Catalog::Anime {
                            title.kind = "anime".into();
                        }
                        title
                    })
                    .collect::<Vec<_>>()
            })
            .map_err(|e| e.to_string())
        });
        async move { task.await.map_err(|e| format!("catalog task: {e}"))? }
    }

    /// The user's history (newest first) and Ma Liste, anime marked, as the
    /// web's `/api/user-lists` reads them: from KINO's database, off the UI.
    pub fn library(&self) -> impl Future<Output = (Vec<Value>, Vec<Value>)> + use<> {
        let db = self.db.clone();
        let task = self.runtime.spawn_blocking(move || {
            let (mut history, mut watchlist) =
                (kino::library::history(&db), kino::library::watchlist(&db));
            kino::anime_home::mark_anime(&db, &mut history);
            kino::anime_home::mark_anime(&db, &mut watchlist);
            (history, watchlist)
        });
        async move { task.await.unwrap_or_default() }
    }

    /// « Pour vous » for `catalog` (films or series; none for anime, as on the
    /// web): KINO's own suggestions, each with its reason, anime marked.
    pub fn for_you(&self, catalog: Catalog) -> impl Future<Output = Vec<Value>> + use<> {
        let (meta, db, store) = (self.meta.clone(), self.db.clone(), self.suggestions.clone());
        let task = self.runtime.spawn(async move {
            let kind = match catalog {
                Catalog::Movies => "movie",
                Catalog::Series => "series",
                Catalog::Anime => return Vec::new(),
            };
            kino::recommender::for_you_page(&meta, &db, &store, kind)
                .await
                .items
        });
        async move { task.await.unwrap_or_default() }
    }

    /// « Ce soir »: up to three titles of Ma Liste for `minutes` and `mood`
    /// (`kino::tonight::page`, as `/api/tonight` answers).
    pub fn tonight(
        &self,
        minutes: i64,
        mood: &'static str,
    ) -> impl Future<Output = Result<kino::tonight::Page, String>> + use<> {
        let (meta, db) = (self.meta.clone(), self.db.clone());
        let task = self.runtime.spawn(async move {
            kino::tonight::page(&meta, &db, minutes, mood, false)
                .await
                .map_err(|_| t!("server.choisissez_une_duree_et_une_ambiance").to_string())
        });
        async move { task.await.map_err(|e| format!("tonight task: {e}"))? }
    }

    /// The user's answer about a « Ce soir » idea: "defer", "seen" or
    /// "exclude" (`kino::tonight::apply_feedback`).
    pub fn tonight_feedback(
        &self,
        id: SharedString,
        action: &'static str,
    ) -> impl Future<Output = bool> + use<> {
        let db = self.db.clone();
        let task = self.runtime.spawn_blocking(move || {
            kino::tonight::apply_feedback(&db, &id, &Value::from(action)).is_ok()
        });
        async move { task.await.unwrap_or(false) }
    }

    /// Explorer's selections: the user's imported Letterboxd lists, KINO's
    /// own selections, and the history (to count what was seen), as
    /// `/api/letterboxd/custom-lists` and `/api/community/curated` give them.
    pub fn explore(&self) -> impl Future<Output = (Vec<Value>, Vec<Value>, Vec<Value>)> + use<> {
        let db = self.db.clone();
        let task = self.runtime.spawn_blocking(move || {
            let curated = kino::letterboxd::curated()
                .as_array()
                .cloned()
                .unwrap_or_default();
            (
                kino::letterboxd::lists(),
                curated,
                kino::library::history(&db),
            )
        });
        async move { task.await.unwrap_or_default() }
    }

    /// Plays `title` (an episode of it, for a series) with KINO's player, as
    /// the web's one-click play does (`/api/one-click-play`).
    pub fn play(&self, request: &PlayRequest) -> impl Future<Output = PlayOutcome> + use<> {
        let answer = self.api("POST", "/api/one-click-play".into(), Some(request.body()));
        async move {
            let (status, answer) = answer.await;
            PlayOutcome::from_answer(status, &answer)
        }
    }

    /// The user agreed to read torrents peer to peer (`/api/p2p/consent`).
    pub fn accept_p2p(&self) -> impl Future<Output = bool> + use<> {
        let answer = self.api(
            "POST",
            "/api/p2p/consent".into(),
            Some(serde_json::json!({"accept": true})),
        );
        async move { answer.await.0 == 200 }
    }

    /// Whether `id` is in Ma Liste.
    pub fn in_watchlist(&self, id: SharedString) -> impl Future<Output = bool> + use<> {
        let db = self.db.clone();
        let task = self.runtime.spawn_blocking(move || {
            kino::library::watchlist(&db)
                .iter()
                .any(|m| m["id"].as_str() == Some(id.as_ref()))
        });
        async move { task.await.unwrap_or(false) }
    }

    /// Adds `title` to Ma Liste, or removes it: whether it is there now.
    pub fn toggle_watchlist(&self, title: &Title) -> impl Future<Output = bool> + use<> {
        let (db, item) = (self.db.clone(), title.to_item());
        let id = title.id.clone();
        let task = self.runtime.spawn_blocking(move || {
            kino::library::toggle_watchlist(&db, &item)
                .iter()
                .any(|m| m["id"].as_str() == Some(id.as_ref()))
        });
        async move { task.await.unwrap_or(false) }
    }

    /// Where a series picks up: the episode last played, from the history.
    pub fn resume_episode(
        &self,
        id: SharedString,
    ) -> impl Future<Output = Option<(u32, u32)>> + use<> {
        let db = self.db.clone();
        let task = self.runtime.spawn_blocking(move || {
            let history = kino::library::history(&db);
            let entry = history
                .iter()
                .find(|h| h["id"].as_str() == Some(id.as_ref()))?;
            let number = |key: &str| entry[key].as_u64().and_then(|n| u32::try_from(n).ok());
            Some((number("season")?, number("episode")?))
        });
        async move { task.await.ok().flatten() }
    }

    /// Films and series matching `query`, films first, as the web's search
    /// shows them (`kino::catalog::search`, both kinds at once).
    pub fn search(&self, query: &str) -> impl Future<Output = Vec<Title>> + use<> {
        let (meta, db, query) = (self.meta.clone(), self.db.clone(), query.to_string());
        let task = self.runtime.spawn(async move {
            let (movies, series) = tokio::join!(
                kino::catalog::search(&meta, &db, &query, "movie"),
                kino::catalog::search(&meta, &db, &query, "series")
            );
            let mut seen = std::collections::HashSet::new();
            movies
                .iter()
                .chain(&series)
                .filter_map(Title::from_meta)
                .filter(|title| seen.insert(title.id.clone()))
                .collect::<Vec<_>>()
        });
        async move { task.await.unwrap_or_default() }
    }

    /// What KINO knows of `title` (its page), in French when `french`.
    pub fn details(
        &self,
        title: &Title,
        french: bool,
    ) -> impl Future<Output = Result<Details, String>> + use<> {
        let (meta, id, kind) = (
            self.meta.clone(),
            title.id.to_string(),
            title.kind.to_string(),
        );
        let task = self.runtime.spawn(async move {
            let page = meta.media(&id, &kind).await.map_err(|e| e.to_string())?;
            Details::from_meta(&page, french).ok_or_else(|| format!("{id}: no page"))
        });
        async move { task.await.map_err(|e| format!("details task: {e}"))? }
    }

    /// Images over HTTP(S) with KINO's client, for the image cache.
    pub fn image_loader(&self) -> Loader {
        let handle = self.runtime.handle().clone();
        let client = self.meta.net.client.clone();
        Arc::new(move |url: &str| {
            let request = client.get(url).timeout(Duration::from_secs(20));
            handle.block_on(async move {
                let response = request
                    .send()
                    .await
                    .and_then(reqwest::Response::error_for_status)
                    .map_err(|e| format!("image unavailable: {e}"))?;
                response
                    .bytes()
                    .await
                    .map(|b| b.to_vec())
                    .map_err(|e| format!("image cut short: {e}"))
            })
        })
    }
}

/// What to play: a film, or one episode of a series.
#[derive(Clone, Debug, PartialEq)]
pub struct PlayRequest {
    pub title: Title,
    /// (season, episode) for a series.
    pub episode: Option<(u32, u32)>,
    /// The source a debrid service was preparing, once it is ready.
    pub prepared: Option<Value>,
}

impl PlayRequest {
    /// The web page's request to `/api/one-click-play`, KINO's own player
    /// window chosen (the native interface has no web player).
    pub fn body(&self) -> Value {
        let title = &self.title;
        let mut body = serde_json::json!({
            "imdb_id": title.id.as_ref(),
            "name": title.name.as_ref(),
            "poster": title.poster.as_ref().map_or("", |p| p.as_ref()),
            "year": title.year.as_ref().map_or("", |y| y.as_ref()),
            "player_mode": "kino",
        });
        match self.episode {
            Some((season, episode)) => {
                body["type"] = "series".into();
                body["season"] = season.into();
                body["episode"] = episode.into();
                body["title"] = t!(
                    "fiche.x_s_x_e_x",
                    title.name.as_ref(),
                    format!("{season:02}"),
                    format!("{episode:02}")
                )
                .into();
            }
            None => {
                body["type"] = "movie".into();
                body["title"] = match &title.year {
                    Some(year) => format!("{} ({year})", title.name),
                    None => title.name.to_string(),
                }
                .into();
            }
        }
        if let Some(prepared) = &self.prepared {
            body["prepared"] = prepared.clone();
        }
        body
    }
}

/// What asking to play led to.
#[derive(Clone, Debug, PartialEq)]
pub enum PlayOutcome {
    /// KINO's player opened, with this source.
    Started {
        source: SharedString,
    },
    /// A debrid service prepares the chosen source: ask again with
    /// `prepared` (`PlayRequest::prepared`) once it is ready.
    Preparing {
        progress: u8,
        prepared: Value,
    },
    /// No debrid key: torrents are read peer to peer once the user agrees.
    NeedsConsent(SharedString),
    Failed(SharedString),
}

impl PlayOutcome {
    /// The route's answer: its status and JSON.
    pub fn from_answer(status: u16, answer: &Value) -> Self {
        let text = |key: &str| answer[key].as_str().unwrap_or_default().to_string();
        if answer["preparing"] == true {
            return Self::Preparing {
                progress: answer["progress"]
                    .as_u64()
                    .map_or(0, |p| u8::try_from(p.min(100)).unwrap_or(100)),
                prepared: serde_json::json!({
                    "torrent_id": text("torrent_id"),
                    "source_key": text("source_key"),
                }),
            };
        }
        if answer["code"] == "p2p_consent" {
            return Self::NeedsConsent(text("error").into());
        }
        if (200..300).contains(&status) && answer["error"].is_null() {
            return Self::Started {
                source: text("chosen_torrent").trim().to_string().into(),
            };
        }
        let error = text("error");
        Self::Failed(if error.is_empty() {
            format!("HTTP {status}").into()
        } else {
            error.into()
        })
    }
}
