//! Discord Rich Presence: « Regarde KINO », the title, the episode, the
//! progress bar and the poster on the user's Discord profile, through the
//! Discord app's local socket (a named pipe on Windows).

use std::sync::Mutex;

use serde_json::{Value, json};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::mpsc;

/// KINO's public Discord application, shared by every installation.
pub const DEFAULT_CLIENT_ID: &str = "1556492442415398973";
/// Ids offered by old versions (other apps' names): back to KINO's own.
const LEGACY_IDS: [&str; 6] = [
    "631379801826918400",
    "645028677033132033",
    "968880591003082783",
    "938732156346314795",
    "608065709741965327",
    "926541425682829352",
];
const NEWER_FIELDS: [&str; 2] = ["type", "status_display_type"];

pub fn normalize_client_id(id: &str) -> String {
    let clean = id.trim();
    if clean.is_empty() || LEGACY_IDS.contains(&clean) {
        DEFAULT_CLIENT_ID.into()
    } else {
        clean.into()
    }
}

pub struct Playing<'a> {
    pub title: &'a str,
    pub kind: &'a str,
    pub season: Option<i64>,
    pub episode: Option<i64>,
    pub episode_title: Option<&'a str>,
    pub position: f64,
    pub duration: f64,
    pub poster: Option<&'a str>,
    pub paused: bool,
    pub year: Option<&'a str>,
}

/// The card: « Regarde KINO », the title large, « En lecture · S01E02 », the bar.
pub fn build_activity(p: &Playing, now: i64) -> Value {
    let title = p.title.trim();
    let series =
        ["series", "anime", "tv"].contains(&p.kind) || p.season.is_some() || p.episode.is_some();
    let mut parts = vec![if p.paused {
        "En pause".to_string()
    } else {
        "En lecture".to_string()
    }];
    if series {
        let code = format!(
            "{}{}",
            p.season
                .filter(|s| *s != 0)
                .map(|s| format!("S{s:02}"))
                .unwrap_or_default(),
            p.episode
                .filter(|e| *e != 0)
                .map(|e| format!("E{e:02}"))
                .unwrap_or_default()
        );
        if !code.is_empty() {
            parts.push(code);
        }
        if let Some(t) = p.episode_title.filter(|t| !t.trim().is_empty()) {
            parts.push(t.trim().to_string());
        }
    }
    let cut = |s: &str| s.chars().take(128).collect::<String>();
    let mut activity = json!({"type": 3, "status_display_type": 2, "details": cut(title), "state": cut(&parts.join(" · "))});
    let year: String = p.year.unwrap_or("").trim().chars().take(4).collect();
    if let Some(poster) = p
        .poster
        .filter(|u| u.starts_with("https://") || u.starts_with("http://"))
    {
        let text = if year.len() == 4 && year.chars().all(|c| c.is_ascii_digit()) {
            format!("{title} ({year})")
        } else {
            title.to_string()
        };
        activity["assets"] = json!({"large_image": poster, "large_text": cut(&text)});
    }
    if !p.paused {
        let (position, length) = (p.position.max(0.0), p.duration.max(0.0));
        activity["timestamps"] = if length > position {
            let start = now - position as i64;
            json!({"start": start, "end": start + length as i64})
        } else {
            json!({"start": now})
        };
    }
    activity
}

enum Order {
    Show(Value),
    Clear,
}

/// The connection to the Discord app, run in the background.
pub struct Discord {
    orders: mpsc::UnboundedSender<Order>,
    state: Mutex<(bool, String)>,
    connected: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

trait Stream: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> Stream for T {}

async fn open() -> Option<Box<dyn Stream>> {
    #[cfg(windows)]
    {
        for i in 0..10 {
            if let Ok(pipe) = tokio::net::windows::named_pipe::ClientOptions::new()
                .open(format!(r"\\.\pipe\discord-ipc-{i}"))
            {
                return Some(Box::new(pipe));
            }
        }
        None
    }
    #[cfg(unix)]
    {
        let dirs: Vec<String> = ["XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"]
            .iter()
            .filter_map(|k| std::env::var(k).ok())
            .chain(["/tmp".to_string()])
            .collect();
        for dir in dirs.iter().filter(|d| !d.is_empty()) {
            for i in 0..10 {
                let path = std::path::Path::new(dir).join(format!("discord-ipc-{i}"));
                if path.exists()
                    && let Ok(socket) = tokio::net::UnixStream::connect(&path).await
                {
                    return Some(Box::new(socket));
                }
            }
        }
        None
    }
}

async fn frame(stream: &mut Box<dyn Stream>, op: u32, body: &Value) -> Option<Value> {
    let raw = body.to_string().into_bytes();
    let mut packet = Vec::with_capacity(8 + raw.len());
    packet.extend_from_slice(&op.to_le_bytes());
    packet.extend_from_slice(&(raw.len() as u32).to_le_bytes());
    packet.extend_from_slice(&raw);
    stream.write_all(&packet).await.ok()?;
    let mut header = [0u8; 8];
    tokio::time::timeout(
        std::time::Duration::from_millis(2000),
        stream.read_exact(&mut header),
    )
    .await
    .ok()?
    .ok()?;
    let length = u32::from_le_bytes([header[4], header[5], header[6], header[7]]) as usize;
    let mut reply = vec![0u8; length.min(1 << 20)];
    tokio::time::timeout(
        std::time::Duration::from_millis(2000),
        stream.read_exact(&mut reply),
    )
    .await
    .ok()?
    .ok()?;
    serde_json::from_slice(&reply).ok().or(Some(Value::Null))
}

impl Discord {
    pub fn start(client_id: &str, enabled: bool) -> Self {
        let (orders, mut inbox) = mpsc::unbounded_channel::<Order>();
        let connected = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let me = Self {
            orders,
            state: Mutex::new((enabled, normalize_client_id(client_id))),
            connected: connected.clone(),
        };
        let client = me.state.lock().map(|s| s.1.clone()).unwrap_or_default();
        tokio::spawn(async move {
            let mut stream: Option<Box<dyn Stream>> = None;
            while let Some(order) = inbox.recv().await {
                let activity = match order {
                    Order::Show(a) => Some(a),
                    Order::Clear if stream.is_none() => continue,
                    Order::Clear => None,
                };
                if stream.is_none()
                    && let Some(mut s) = open().await
                {
                    let ready = frame(&mut s, 0, &json!({"v": 1, "client_id": client})).await;
                    if ready.is_some_and(|r| r["evt"] == "READY" || r["data"]["user"].is_object()) {
                        stream = Some(s);
                    }
                }
                connected.store(stream.is_some(), std::sync::atomic::Ordering::SeqCst);
                let Some(s) = stream.as_mut() else { continue };
                let payload = |a: &Option<Value>| json!({"cmd": "SET_ACTIVITY", "args": {"pid": std::process::id(), "activity": a}, "nonce": crate::db::now().to_string()});
                match frame(s, 1, &payload(&activity)).await {
                    None => {
                        stream = None;
                        connected.store(false, std::sync::atomic::Ordering::SeqCst);
                    }
                    // An older Discord refuses the newer fields: sent again without them.
                    Some(reply) if reply["evt"] == "ERROR" => {
                        let basic = activity.map(|mut a| {
                            if let Some(o) = a.as_object_mut() {
                                NEWER_FIELDS.iter().for_each(|k| {
                                    o.remove(*k);
                                });
                            }
                            a
                        });
                        let _ = frame(s, 1, &payload(&basic)).await;
                    }
                    Some(_) => {}
                }
            }
        });
        me
    }

    pub fn enabled(&self) -> bool {
        self.state.lock().map(|s| s.0).unwrap_or(false)
    }

    pub fn connected(&self) -> bool {
        self.connected.load(std::sync::atomic::Ordering::SeqCst)
    }

    pub fn set_enabled(&self, on: bool) {
        if let Ok(mut s) = self.state.lock() {
            s.0 = on;
        }
        if !on {
            self.clear();
        }
    }

    pub fn show(&self, playing: &Playing) {
        if self.enabled() && !playing.title.trim().is_empty() {
            let _ = self.orders.send(Order::Show(build_activity(
                playing,
                crate::db::now() as i64,
            )));
        }
    }

    pub fn clear(&self) {
        let _ = self.orders.send(Order::Clear);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_card() {
        let p = Playing {
            title: "The Bear",
            kind: "series",
            season: Some(3),
            episode: Some(5),
            episode_title: None,
            position: 60.0,
            duration: 1800.0,
            poster: Some("https://p/x.jpg"),
            paused: false,
            year: Some("2022"),
        };
        let a = build_activity(&p, 10_000);
        assert_eq!(a["state"], "En lecture · S03E05");
        assert_eq!(a["timestamps"], json!({"start": 9940, "end": 11740}));
        assert_eq!(a["assets"]["large_text"], "The Bear (2022)");
        let paused = build_activity(&Playing { paused: true, ..p }, 10_000);
        assert!(paused.get("timestamps").is_none());
        assert_eq!(normalize_client_id("645028677033132033"), DEFAULT_CLIENT_ID);
    }
}
