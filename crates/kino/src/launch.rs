//! Starting KINO's server: from kino-server (its own process) or from the
//! desktop app (the same process as the window).

use std::net::SocketAddr;
use std::sync::Arc;

use anyhow::Result;
use rand::Rng;

use crate::db::Db;
use crate::{paths, server};

pub struct Options {
    pub port: u16,
    /// The phone remote's token; a new one when empty.
    pub pairing_token: String,
    /// The Python server behind this one, for the routes not ported yet.
    pub legacy: Option<String>,
    pub native_player: bool,
    pub notifications: bool,
    /// The launch work (startup.rs): only for the app's own server.
    pub startup_tasks: bool,
    /// The window, when it runs in this process: the external player is then started here.
    pub window: Option<Arc<dyn crate::player::WindowHooks>>,
    /// The system's notifications, when the window can send them.
    pub notifier: Option<Arc<dyn crate::alerts::Notifier>>,
    /// The player's state and commands, shared with the window (media keys).
    pub remote: Option<Arc<crate::remote::Remote>>,
}

/// A new random pairing token, as the Python server makes them.
pub fn new_token() -> String {
    rand::rng()
        .sample_iter(&rand::distr::Alphanumeric)
        .take(43)
        .map(char::from)
        .collect()
}

/// The server's listener: every interface (the phone remote reaches KINO from
/// the local network), else this computer only.
pub async fn bind(port: u16) -> Result<tokio::net::TcpListener> {
    match tokio::net::TcpListener::bind(("0.0.0.0", port)).await {
        Ok(l) => Ok(l),
        Err(_) => Ok(tokio::net::TcpListener::bind(("127.0.0.1", port)).await?),
    }
}

/// Serves KINO on `listener` until the process ends.
pub async fn serve(listener: tokio::net::TcpListener, options: Options) -> Result<()> {
    let db = Arc::new(Db::open(&paths::database())?);
    let net = Arc::new(crate::net::Net::new(db.clone()));
    let meta = crate::meta::Meta {
        net: net.clone(),
        cinemeta: crate::meta::CINEMETA.into(),
        translate: true,
    };
    let links: Arc<crate::play::EpisodeLinks> = Default::default();
    let offline = crate::offline::Library::new(db.clone(), Some((meta.clone(), links.clone())));
    // A download cut by the app closing stays resumable; a waiting episode starts.
    offline.reconcile();
    let token = if options.pairing_token.len() >= 16 {
        options.pairing_token
    } else {
        new_token()
    };
    let player = options
        .window
        .map(|w| crate::player::Player::new(db.clone(), meta.clone(), Some(w)));
    let remote = options.remote.clone().unwrap_or_default();
    let tv = player.as_ref().map(|p| {
        crate::tv::Cast::new(
            db.clone(),
            meta.clone(),
            links.clone(),
            remote.clone(),
            Some(p.discord.clone()),
            options.port,
            Default::default(),
        )
    });
    let state = Arc::new(server::AppState {
        db: db.clone(),
        port: options.port,
        pairing_token: token,
        web_root: paths::web_root(),
        legacy: options.legacy.filter(|s| !s.is_empty()),
        http: reqwest::Client::builder().no_gzip().build()?,
        net,
        meta,
        accounts: Default::default(),
        links,
        offline,
        suggestions: Default::default(),
        native_player: options.native_player,
        notifications: options.notifications || options.notifier.is_some(),
        notifier: options.notifier.clone(),
        player,
        remote,
        tv,
        sites: crate::manga::sources::Sites::default(),
    });
    if let Some(notifier) = options.notifier {
        crate::alerts::watch(db.clone(), state.meta.clone(), notifier);
    }
    if options.startup_tasks {
        crate::startup::run(state.clone());
    }
    eprintln!("KINO listening on {}", listener.local_addr()?);
    axum::serve(
        listener,
        server::router(state).into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await?;
    Ok(())
}
