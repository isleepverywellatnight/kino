//! KINO's core inside the native interface (phase G2): the same database,
//! network client and metadata as the server (`kino::launch::serve`), on a
//! tokio runtime of their own. Screens ask it for data as typed values; gpui's
//! executors await the answers, the work runs on tokio.

use std::sync::Arc;
use std::time::Duration;

use kino::db::Db;
use kino::meta::Meta;
use kino::net::Net;

use crate::image_cache::Loader;
use crate::title::Title;

/// The catalogs the Discover screens show.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Catalog {
    Movies,
    Series,
}

impl Catalog {
    fn kind(self) -> &'static str {
        match self {
            Catalog::Movies => "movie",
            Catalog::Series => "series",
        }
    }
}

#[derive(Clone)]
pub struct Core {
    runtime: Arc<tokio::runtime::Runtime>,
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
            net: Arc::new(Net::new(db)),
            cinemeta: kino::meta::CINEMETA.into(),
            translate: true,
        };
        Ok(Self {
            runtime: Arc::new(runtime),
            meta,
        })
    }

    /// A page of `catalog`, `skip` items in, best first. Runs on KINO's
    /// runtime; the returned future can be awaited from gpui.
    pub fn catalog(
        &self,
        catalog: Catalog,
        skip: u64,
    ) -> impl Future<Output = Result<Vec<Title>, String>> + use<> {
        let meta = self.meta.clone();
        let task = self.runtime.spawn(async move {
            meta.catalog_top(catalog.kind(), "", skip, "top")
                .await
                .map(|metas| {
                    metas
                        .iter()
                        .filter_map(Title::from_meta)
                        .collect::<Vec<_>>()
                })
                .map_err(|e| e.to_string())
        });
        async move { task.await.map_err(|e| format!("catalog task: {e}"))? }
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
