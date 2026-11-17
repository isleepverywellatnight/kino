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
    let token: String = rand::rng()
        .sample_iter(&rand::distr::Alphanumeric)
        .take(43)
        .map(char::from)
        .collect();
    let db = Arc::new(Db::open(&paths::database())?);
    let net = Arc::new(kino::net::Net::new(db.clone()));
    let state = Arc::new(server::AppState {
        db: db.clone(),
        port,
        pairing_token: token,
        web_root: paths::web_root(),
        legacy: std::env::var("KINO_LEGACY").ok().filter(|s| !s.is_empty()),
        http: reqwest::Client::builder().build()?,
        net: net.clone(),
        meta: kino::meta::Meta {
            net: net.clone(),
            cinemeta: kino::meta::CINEMETA.into(),
            translate: true,
        },
        accounts: Default::default(),
        native_player: false,
        notifications: false,
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
