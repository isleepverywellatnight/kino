//! KINO's core inside the native interface (phase G2): the same database,
//! network client and metadata as the server (`kino::launch::serve`), on a
//! tokio runtime of their own. Screens ask it for data as typed values; gpui's
//! executors await the answers, the work runs on tokio.

use std::sync::Arc;
use std::time::Duration;

use kino::db::Db;
use kino::meta::Meta;
use kino::net::Net;
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
    db: Arc<Db>,
    meta: Meta,
}

impl Core {
    /// Opens KINO's database (the one the server uses: `~/.kino_data.db`) and
    /// starts the runtime.
    ///
    /// # Errors
    /// When the runtime cannot start or the database cannot open.
    pub fn start() -> Result<Self, String> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("kino-core")
            .enable_all()
            .build()
            .map_err(|e| format!("cannot start KINO's runtime: {e}"))?;
        let db = Arc::new(
            Db::open(&kino::paths::database())
                .map_err(|e| format!("cannot open KINO's database: {e:#}"))?,
        );
        let meta = Meta {
            net: Arc::new(Net::new(db.clone())),
            cinemeta: kino::meta::CINEMETA.into(),
            translate: true,
        };
        Ok(Self {
            runtime: Arc::new(runtime),
            db,
            meta,
        })
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
