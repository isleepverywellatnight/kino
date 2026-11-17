//! Is the internet reachable? A connection is opened and closed to hosts KINO
//! already uses (the catalogue, Wikipedia); nothing is sent. A check is
//! trusted 20 seconds, and one runs at a time.

use std::sync::Mutex;
use std::time::{Duration, Instant};

const HOSTS: [(&str, u16); 2] = [("v3-cinemeta.strem.io", 443), ("fr.wikipedia.org", 443)];
const FRESH: Duration = Duration::from_secs(20);

static STATE: Mutex<Option<(bool, Instant)>> = Mutex::new(None);
static PROBING: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

async fn probe() -> bool {
    for (host, port) in HOSTS {
        let connect = tokio::net::TcpStream::connect((host, port));
        if let Ok(Ok(_)) = tokio::time::timeout(Duration::from_millis(1500), connect).await {
            return true;
        }
    }
    false
}

fn recent() -> Option<bool> {
    STATE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .filter(|(_, at)| at.elapsed() < FRESH)
        .map(|(online, _)| online)
}

/// Whether the internet answers: checked again when asked or when the last check is old.
pub async fn online(refresh: bool) -> bool {
    if !refresh && let Some(known) = recent() {
        return known;
    }
    let before = STATE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .map(|(_, at)| at);
    let _probing = PROBING.lock().await;
    // Another request checked while this one waited: its answer is fresh.
    let after = *STATE.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((known, at)) = after
        && Some(at) != before
    {
        return known;
    }
    let found = probe().await;
    *STATE.lock().unwrap_or_else(|e| e.into_inner()) = Some((found, Instant::now()));
    found
}
