use std::net::SocketAddr;
use std::sync::Arc;

use anyhow::Result;
use kino::{db::Db, paths, server};
use rand::Rng;

#[tokio::main]
async fn main() -> Result<()> {
    let port: u16 = std::env::var("KINO_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8080);
    if std::env::args().any(|a| a == "--version") {
        println!("kino-server {}", kino::backup::app_version());
        return Ok(());
    }
    // The desktop app shares its pairing token so the phone remote's link works here.
    let token: String = std::env::var("KINO_PAIRING_TOKEN")
        .ok()
        .filter(|t| t.len() >= 16)
        .unwrap_or_else(|| {
            rand::rng()
                .sample_iter(&rand::distr::Alphanumeric)
                .take(43)
                .map(char::from)
                .collect()
        });
    let flag = |name: &str| std::env::var(name).is_ok_and(|v| v == "1");
    if flag("KINO_EXIT_WITH_STDIN") {
        // Started by the desktop app: its end closes our input, and we end too.
        std::thread::spawn(|| {
            let _ = std::io::copy(&mut std::io::stdin(), &mut std::io::sink());
            std::process::exit(0);
        });
    }
    let db = Arc::new(Db::open(&paths::database())?);
    let net = Arc::new(kino::net::Net::new(db.clone()));
    let meta = kino::meta::Meta {
        net: net.clone(),
        cinemeta: kino::meta::CINEMETA.into(),
        translate: true,
    };
    let links: Arc<kino::play::EpisodeLinks> = Default::default();
    let offline = kino::offline::Library::new(db.clone(), Some((meta.clone(), links.clone())));
    // A download cut by the app closing stays resumable; a waiting episode starts.
    offline.reconcile();
    let state = Arc::new(server::AppState {
        db: db.clone(),
        port,
        pairing_token: token,
        web_root: paths::web_root(),
        legacy: std::env::var("KINO_LEGACY").ok().filter(|s| !s.is_empty()),
        http: reqwest::Client::builder().no_gzip().build()?,
        net: net.clone(),
        meta,
        accounts: Default::default(),
        links,
        offline,
        suggestions: Default::default(),
        native_player: flag("KINO_NATIVE_PLAYER"),
        notifications: flag("KINO_NOTIFICATIONS"),
        sites: kino::manga::sources::Sites::default(),
    });
    // Every interface: the phone remote reaches KINO from the local network.
    let listener = match tokio::net::TcpListener::bind(("0.0.0.0", port)).await {
        Ok(l) => l,
        Err(_) => tokio::net::TcpListener::bind(("127.0.0.1", port)).await?,
    };
    eprintln!("KINO listening on {}", listener.local_addr()?);
    axum::serve(
        listener,
        server::router(state).into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await?;
    Ok(())
}
