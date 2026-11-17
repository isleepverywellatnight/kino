//! « Lire sur la TV »: a film or an episode on a TV of the local network
//! (DLNA / UPnP AVTransport, as Samsung TVs understand it). KINO finds the TV
//! (SSDP), relays the Debrid stream over plain HTTP (TV players often refuse
//! https), and reads the TV's position every two seconds for the history, the
//! phone remote, the media keys and Discord. Near the end of an episode the
//! next one is prepared; it starts ten seconds after the end.

use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use rand::Rng;
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::db::{Db, now};
use crate::discord::{Discord, Playing};
use crate::meta::Meta;
use crate::play::{Episode, EpisodeLinks, text, truthy};
use crate::remote::Remote;

const RENDERER: &str = "urn:schemas-upnp-org:device:MediaRenderer:1";
const AVTRANSPORT: &str = "urn:schemas-upnp-org:service:AVTransport:1";
const SAVE_EVERY: f64 = 15.0;
const PREFETCH_AT: f64 = 0.85;
/// DLNA: the stream can be sought by byte ranges (OP=01), streamed (FLAGS).
/// Without it a Samsung TV refuses to seek (error 701).
pub const DLNA_FEATURES: &str =
    "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000";

/// How often the TV is read, and how long it is waited for.
#[derive(Clone, Copy)]
pub struct Timing {
    pub poll: Duration,
    /// A TV that has not started after this long refused the source.
    pub start_timeout: f64,
    /// Seconds between the end of an episode and the next one.
    pub next_delay: f64,
}

impl Default for Timing {
    fn default() -> Self {
        Self {
            poll: Duration::from_secs(2),
            start_timeout: 25.0,
            next_delay: 10.0,
        }
    }
}

pub fn mime_for(filename: &str, url: &str) -> &'static str {
    static EXT: std::sync::LazyLock<Regex> =
        std::sync::LazyLock::new(|| Regex::new(r"\.[a-z0-9]{2,4}$").unwrap());
    let path = reqwest::Url::parse(url)
        .map(|u| u.path().to_string())
        .unwrap_or_default();
    for name in [filename.to_lowercase(), path.to_lowercase()] {
        let found = EXT.find(&name).map(|m| match m.as_str() {
            ".mkv" => "video/x-mkv",
            ".mp4" | ".m4v" => "video/mp4",
            ".mov" => "video/quicktime",
            ".avi" => "video/x-msvideo",
            ".webm" => "video/webm",
            ".ts" | ".m2ts" => "video/vnd.dlna.mpeg-tts",
            ".mpg" | ".mpeg" => "video/mpeg",
            ".wmv" => "video/x-ms-wmv",
            _ => "",
        });
        if let Some(mime) = found.filter(|m| !m.is_empty()) {
            return mime;
        }
    }
    "video/x-mkv"
}

pub fn hms(seconds: f64) -> String {
    let s = seconds.max(0.0) as i64;
    format!("{}:{:02}:{:02}", s / 3600, s / 60 % 60, s % 60)
}

/// « 1:02:03 » or « 0:00:04.372 » → seconds; anything else → 0.
pub fn seconds_of(value: &str) -> f64 {
    static TIME: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
        Regex::new(r"^\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.\d+)?\s*$").unwrap()
    });
    TIME.captures(value)
        .map(|c| {
            (1..=3)
                .map(|i| c[i].parse::<f64>().unwrap_or(0.0))
                .fold(0.0, |acc, n| acc * 60.0 + n)
        })
        .unwrap_or(0.0)
}

fn escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

/// Only addresses of the local network: a description could point anywhere.
pub fn is_local_url(url: &str) -> bool {
    let Some(host) = reqwest::Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(str::to_string))
    else {
        return false;
    };
    match host.trim_matches(['[', ']']).parse::<std::net::IpAddr>() {
        Ok(std::net::IpAddr::V4(ip)) => {
            (ip.is_private() || ip.is_link_local()) && !ip.is_loopback()
        }
        Ok(std::net::IpAddr::V6(ip)) => {
            (ip.segments()[0] & 0xfe00) == 0xfc00 || (ip.segments()[0] & 0xffc0) == 0xfe80
        }
        Err(_) => false,
    }
}

// --- Finding the TVs -----------------------------------------------------

/// The description addresses (LOCATION) of the media renderers that answer.
pub async fn ssdp_search(timeout: Duration) -> Vec<String> {
    let ip: std::net::Ipv4Addr = crate::remote::local_ip()
        .parse()
        .unwrap_or(std::net::Ipv4Addr::UNSPECIFIED);
    let socket = (|| -> std::io::Result<tokio::net::UdpSocket> {
        let s = socket2::Socket::new(
            socket2::Domain::IPV4,
            socket2::Type::DGRAM,
            Some(socket2::Protocol::UDP),
        )?;
        s.set_multicast_ttl_v4(2)?;
        if !ip.is_loopback() && !ip.is_unspecified() {
            let _ = s.set_multicast_if_v4(&ip);
        }
        if s.bind(&std::net::SocketAddr::from((ip, 0)).into()).is_err() {
            s.bind(&std::net::SocketAddr::from(([0, 0, 0, 0], 0)).into())?;
        }
        s.set_nonblocking(true)?;
        tokio::net::UdpSocket::from_std(s.into())
    })();
    let Ok(socket) = socket else { return vec![] };
    let message = format!(
        "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: {RENDERER}\r\n\r\n"
    );
    for _ in 0..2 {
        let _ = socket
            .send_to(message.as_bytes(), "239.255.255.250:1900")
            .await;
    }
    static LOCATION: std::sync::LazyLock<Regex> =
        std::sync::LazyLock::new(|| Regex::new(r"(?im)^location:\s*(\S+)").unwrap());
    let mut locations: Vec<String> = vec![];
    let end = tokio::time::Instant::now() + timeout;
    let mut buffer = vec![0u8; 65507];
    while let Ok(Ok((n, _))) = tokio::time::timeout_at(end, socket.recv_from(&mut buffer)).await {
        let reply = String::from_utf8_lossy(&buffer[..n]);
        if let Some(found) = LOCATION.captures(&reply).map(|c| c[1].to_string())
            && !locations.contains(&found)
        {
            locations.push(found);
        }
    }
    locations
}

/// A renderer from its UPnP description, or None if it cannot play (no AVTransport).
pub fn parse_description(location: &str, xml: &str) -> Option<Value> {
    let doc = roxmltree::Document::parse(xml).ok()?;
    let device = doc
        .descendants()
        .find(|n| n.tag_name().name() == "device")?;
    let child = |parent: roxmltree::Node, name: &str| {
        parent
            .children()
            .find(|c| c.tag_name().name() == name)
            .and_then(|c| c.text())
            .unwrap_or("")
            .trim()
            .to_string()
    };
    let base = reqwest::Url::parse(location).ok()?;
    let mut control = String::new();
    for service in doc
        .descendants()
        .filter(|n| n.tag_name().name() == "service")
    {
        if child(service, "serviceType").starts_with("urn:schemas-upnp-org:service:AVTransport:")
            && let Ok(url) = base.join(&child(service, "controlURL"))
        {
            control = url.to_string();
        }
    }
    if control.is_empty() {
        return None;
    }
    let host = base.host_str().unwrap_or("").to_string();
    let name = child(device, "friendlyName");
    Some(
        json!({"location": location, "control_url": control, "host": host, "name": if name.is_empty() { host.clone() } else { name },
                "maker": child(device, "manufacturer"), "model": child(device, "modelName")}),
    )
}

/// The TVs of the local network, Samsung ones first.
pub async fn discover(http: &reqwest::Client, locations: Vec<String>) -> Vec<Value> {
    let mut found: Vec<Value> = vec![];
    for location in locations.into_iter().filter(|l| is_local_url(l)) {
        let description = async {
            let answer = http
                .get(&location)
                .header("User-Agent", "KINO")
                .timeout(Duration::from_secs(5))
                .send()
                .await
                .ok()?;
            let raw = answer.bytes().await.ok()?;
            Some(String::from_utf8_lossy(&raw[..raw.len().min(512 * 1024)]).into_owned())
        };
        if let Some(renderer) = description
            .await
            .and_then(|xml| parse_description(&location, &xml))
            && found
                .iter()
                .all(|o| o["control_url"] != renderer["control_url"])
        {
            found.push(renderer);
        }
    }
    found.sort_by_key(|r| {
        (
            !text(&r["maker"]).to_lowercase().contains("samsung"),
            text(&r["name"]).to_lowercase(),
        )
    });
    found
}

// --- Talking to the TV -----------------------------------------------------

#[derive(Debug)]
pub enum TvError {
    /// The TV answered with an error, or the request is not possible.
    Refused(String),
    /// The TV did not answer.
    Unreachable,
}

impl TvError {
    fn upnp(code: i64) -> Self {
        Self::Refused(match code {
            701 => "La TV n'a pas accepté la commande.".into(),
            714 => "La TV ne lit pas ce type de fichier.".into(),
            716 => "La TV ne trouve pas la vidéo.".into(),
            _ => format!("La TV a répondu par une erreur ({code})."),
        })
    }
}

fn tag(reply: &str, name: &str) -> String {
    let start = format!("<{name}>");
    let end = format!("</{name}>");
    reply
        .find(&start)
        .and_then(|i| {
            reply[i + start.len()..]
                .find(&end)
                .map(|j| &reply[i + start.len()..i + start.len() + j])
        })
        .map(|t| html_escape::decode_html_entities(t.trim()).into_owned())
        .unwrap_or_default()
}

pub fn didl(title: &str, url: &str, mime: &str) -> String {
    format!(
        "<DIDL-Lite xmlns=\"urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/\" xmlns:dc=\"http://purl.org/dc/elements/1.1/\" \
         xmlns:upnp=\"urn:schemas-upnp-org:metadata-1-0/upnp/\"><item id=\"0\" parentID=\"-1\" restricted=\"1\">\
         <dc:title>{}</dc:title><upnp:class>object.item.videoItem</upnp:class>\
         <res protocolInfo=\"http-get:*:{mime}:{DLNA_FEATURES}\">{}</res></item></DIDL-Lite>",
        escape(title),
        escape(url)
    )
}

#[derive(Clone)]
struct Renderer {
    http: reqwest::Client,
    control_url: String,
}

impl Renderer {
    async fn call(&self, action: &str, args: &str) -> Result<String, TvError> {
        let body = format!(
            "<?xml version=\"1.0\" encoding=\"utf-8\"?><s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" \
             s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body>\
             <u:{action} xmlns:u=\"{AVTRANSPORT}\"><InstanceID>0</InstanceID>{args}</u:{action}></s:Body></s:Envelope>"
        );
        let answer = self
            .http
            .post(&self.control_url)
            .header("Content-Type", "text/xml; charset=\"utf-8\"")
            .header("SOAPACTION", format!("\"{AVTRANSPORT}#{action}\""))
            .header("User-Agent", "KINO")
            .body(body)
            .timeout(Duration::from_secs(8))
            .send()
            .await
            .map_err(|_| TvError::Unreachable)?;
        let status = answer.status();
        let reply = answer.text().await.map_err(|_| TvError::Unreachable)?;
        if status.is_success() {
            return Ok(reply);
        }
        let code = tag(&reply, "errorCode")
            .parse()
            .unwrap_or(status.as_u16() as i64);
        Err(TvError::upnp(code))
    }

    async fn load(&self, url: &str, title: &str, mime: &str) -> Result<(), TvError> {
        let args = format!(
            "<CurrentURI>{}</CurrentURI><CurrentURIMetaData>{}</CurrentURIMetaData>",
            escape(url),
            escape(&didl(title, url, mime))
        );
        self.call("SetAVTransportURI", &args).await.map(drop)
    }
    async fn play(&self) -> Result<(), TvError> {
        self.call("Play", "<Speed>1</Speed>").await.map(drop)
    }
    async fn pause(&self) -> Result<(), TvError> {
        self.call("Pause", "").await.map(drop)
    }
    async fn stop(&self) -> Result<(), TvError> {
        self.call("Stop", "").await.map(drop)
    }
    async fn seek(&self, seconds: f64) -> Result<(), TvError> {
        self.call(
            "Seek",
            &format!("<Unit>REL_TIME</Unit><Target>{}</Target>", hms(seconds)),
        )
        .await
        .map(drop)
    }
    /// (state, position, duration): PLAYING, PAUSED_PLAYBACK, STOPPED, TRANSITIONING…
    async fn status(&self) -> Result<(String, f64, f64), TvError> {
        let state = tag(
            &self.call("GetTransportInfo", "").await?,
            "CurrentTransportState",
        );
        let position = self.call("GetPositionInfo", "").await?;
        Ok((
            state,
            seconds_of(&tag(&position, "RelTime")),
            seconds_of(&tag(&position, "TrackDuration")),
        ))
    }
}

// --- One play on the TV -----------------------------------------------------

struct Pending {
    at: f64,
    media: Map<String, Value>,
    finding: bool,
}

struct Live {
    state: String,
    position: f64,
    duration: f64,
    played: bool,
    saved_at: f64,
    error: String,
    ended: bool,
    next: Option<Pending>,
    prefetched: bool,
}

struct Session {
    device: Value,
    source: String,
    token: String,
    mime: &'static str,
    title: String,
    media: Map<String, Value>,
    filename: String,
    resume: f64,
    started_at: f64,
    renderer: Renderer,
    live: Mutex<Live>,
}

impl Session {
    fn live(&self) -> MutexGuard<'_, Live> {
        self.live.lock().unwrap_or_else(|e| e.into_inner())
    }
}

fn episode_code(media: &Map<String, Value>) -> String {
    let number = |k: &str| {
        media
            .get(k)
            .filter(|v| truthy(v))
            .and_then(|v| v.as_i64().or_else(|| text(v).trim().parse().ok()))
    };
    match (number("season"), number("episode")) {
        (Some(s), Some(e)) => format!("S{s:02}E{e:02}"),
        _ => String::new(),
    }
}

fn number(media: &Map<String, Value>, key: &str) -> i64 {
    media
        .get(key)
        .and_then(|v| v.as_i64().or_else(|| text(v).trim().parse().ok()))
        .unwrap_or(0)
}

/// The play under way on a TV (one at a time), its relay and its follow-up.
pub struct Cast {
    http: reqwest::Client,
    db: Arc<Db>,
    meta: Meta,
    links: Arc<EpisodeLinks>,
    remote: Arc<Remote>,
    discord: Option<Arc<Discord>>,
    port: u16,
    timing: Timing,
    session: Mutex<Option<Arc<Session>>>,
    presence: Mutex<(Option<bool>, f64)>,
    devices: tokio::sync::Mutex<(f64, Vec<Value>)>,
}

impl Cast {
    #[allow(
        clippy::too_many_arguments,
        reason = "the services a TV play reports to"
    )]
    pub fn new(
        db: Arc<Db>,
        meta: Meta,
        links: Arc<EpisodeLinks>,
        remote: Arc<Remote>,
        discord: Option<Arc<Discord>>,
        port: u16,
        timing: Timing,
    ) -> Arc<Self> {
        Arc::new(Self {
            http: reqwest::Client::new(),
            db,
            meta,
            links,
            remote,
            discord,
            port,
            timing,
            session: Mutex::new(None),
            presence: Mutex::new((None, 0.0)),
            devices: tokio::sync::Mutex::new((0.0, vec![])),
        })
    }

    fn current(&self) -> Option<Arc<Session>> {
        self.session
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    /// The TVs of the local network, looked for again after 30 seconds.
    pub async fn devices(&self, refresh: bool) -> Vec<Value> {
        let mut cache = self.devices.lock().await;
        if refresh || now() - cache.0 > 30.0 || cache.1.is_empty() {
            let locations = ssdp_search(Duration::from_secs(3)).await;
            *cache = (now(), discover(&self.http, locations).await);
        }
        cache.1.clone()
    }

    /// The TV saved in the settings, else the first one found (saved for next time).
    pub async fn chosen(&self) -> Option<Value> {
        let saved = tokio::task::spawn_blocking(crate::config::load)
            .await
            .ok()?
            .get("tv_device")
            .cloned()
            .unwrap_or(Value::Null);
        if truthy(&saved["control_url"]) {
            return Some(saved);
        }
        let first = self.devices(false).await.into_iter().next()?;
        let change = json!({"tv_device": first});
        let _ = tokio::task::spawn_blocking(move || crate::config::save(&change)).await;
        Some(first)
    }

    /// Whether `peer` may read `path`: the session's token, from its TV (or this computer).
    pub fn relay_allowed(&self, peer: &str, path: &str) -> Option<(String, &'static str)> {
        let session = self.current()?;
        let token = path.strip_prefix("/tv/stream/")?.split('/').next()?;
        let host = text(&session.device["host"]);
        (crate::access::same_secret(token, &session.token)
            && (peer == host || peer == "127.0.0.1" || peer == "::1"))
            .then(|| (session.source.clone(), session.mime))
    }

    /// Sends the source to the TV and starts following it.
    pub async fn start(
        self: &Arc<Self>,
        device: &Value,
        source: &str,
        title: &str,
        media: Map<String, Value>,
        filename: &str,
        resume: f64,
    ) -> Result<Value, TvError> {
        if !source.starts_with("https://") && !source.starts_with("http://") {
            return Err(TvError::Refused("Aucune source à envoyer à la TV.".into()));
        }
        if !truthy(&device["control_url"]) || !is_local_url(&text(&device["location"])) {
            return Err(TvError::Refused("Aucune TV choisie.".into()));
        }
        self.stop().await;
        let token: String = rand::rng()
            .sample_iter(&rand::distr::Alphanumeric)
            .take(32)
            .map(char::from)
            .collect();
        let mime = mime_for(filename, source);
        let title: String = (if title.is_empty() { "KINO" } else { title })
            .chars()
            .take(200)
            .collect();
        let safe: String = filename
            .chars()
            .filter(|c| c.is_alphanumeric() || "_.- ".contains(*c))
            .take(80)
            .collect();
        let safe = if safe.is_empty() {
            "video".to_string()
        } else {
            safe
        };
        let relay = format!(
            "http://{}:{}/tv/stream/{token}/{}",
            crate::remote::local_ip(),
            self.port,
            crate::sources::stream::py_quote(&safe)
        );
        let session = Arc::new(Session {
            device: device.clone(),
            source: source.into(),
            token,
            mime,
            title: title.clone(),
            media,
            filename: filename.into(),
            resume: resume.max(0.0),
            started_at: now(),
            renderer: Renderer {
                http: self.http.clone(),
                control_url: text(&device["control_url"]),
            },
            live: Mutex::new(Live {
                state: "TRANSITIONING".into(),
                position: 0.0,
                duration: 0.0,
                played: false,
                saved_at: 0.0,
                error: String::new(),
                ended: false,
                next: None,
                prefetched: false,
            }),
        });
        *self.session.lock().unwrap_or_else(|e| e.into_inner()) = Some(session.clone());
        let started = async {
            session.renderer.load(&relay, &title, mime).await?;
            session.renderer.play().await
        };
        if let Err(e) = started.await {
            *self.session.lock().unwrap_or_else(|e| e.into_inner()) = None;
            return Err(e);
        }
        tokio::spawn(self.clone().follow(session));
        Ok(self.status())
    }

    /// Reads the TV until the play ends. Boxed: the end of an episode starts the next.
    fn follow(
        self: Arc<Self>,
        session: Arc<Session>,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send>> {
        Box::pin(async move {
            loop {
                tokio::time::sleep(self.timing.poll).await;
                if session.live().ended {
                    break;
                }
                self.tick(&session).await;
            }
        })
    }

    pub async fn control(self: &Arc<Self>, action: &str, value: &Value) -> Result<Value, TvError> {
        let session = self
            .current()
            .ok_or_else(|| TvError::Refused("Rien n'est lu sur la TV.".into()))?;
        let between = session.live().next.is_some();
        if between {
            // Between two episodes: start the next one now, or stop there.
            if ["play", "toggle_play", "next_now"].contains(&action) {
                if let Some(next) = session.live().next.as_mut().filter(|n| !n.finding) {
                    next.at = now();
                }
            } else if ["stop", "cancel_next"].contains(&action) {
                self.stop().await;
            }
            return Ok(self.status());
        }
        let renderer = &session.renderer;
        let amount = || {
            value
                .as_f64()
                .or_else(|| text(value).trim().parse().ok())
                .unwrap_or(0.0)
        };
        match action {
            "play" => renderer.play().await?,
            "pause" => renderer.pause().await?,
            "toggle_play" => {
                if session.live().state == "PAUSED_PLAYBACK" {
                    renderer.play().await?
                } else {
                    renderer.pause().await?
                }
            }
            "seek_rel" | "seek" => {
                let (position, duration) = {
                    let live = session.live();
                    (live.position, live.duration)
                };
                let mut target = if action == "seek_rel" {
                    position + amount()
                } else {
                    amount()
                };
                if duration > 0.0 {
                    target = target.min(duration - 1.0);
                }
                renderer.seek(target.max(0.0)).await?;
                session.live().position = target.max(0.0);
            }
            "stop" => self.stop().await,
            _ => {}
        }
        Ok(self.status())
    }

    pub async fn stop(&self) {
        let Some(session) = self
            .session
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
        else {
            return;
        };
        self.save(&session, true).await;
        session.live().ended = true;
        let _ = session.renderer.stop().await;
        self.remote
            .update(&json!({"playing": false, "paused": true}));
        self.presence(None);
    }

    pub fn status(&self) -> Value {
        let Some(session) = self.current() else {
            return json!({"active": false});
        };
        let live = session.live();
        let mut status = json!({
            "active": true, "device": session.device["name"], "title": session.title, "state": live.state,
            "paused": live.state == "PAUSED_PLAYBACK", "position": live.position, "duration": live.duration,
            "error": live.error, "starting": !live.played,
        });
        if let Some(next) = &live.next {
            status["next"] = json!({"code": episode_code(&next.media), "in": (next.at - now()).round_ties_even().max(0.0) as i64, "finding": next.finding});
        }
        status
    }

    /// One look at the TV: its state for KINO, the progress saved, the commands applied.
    async fn tick(self: &Arc<Self>, session: &Arc<Session>) {
        if session.live().next.is_some() {
            self.wait_next(session).await;
            return;
        }
        let Ok((state, mut position, duration)) = session.renderer.status().await else {
            return;
        };
        let playing = state == "PLAYING" || state == "PAUSED_PLAYBACK";
        let (first_play, resume) = {
            let mut live = session.live();
            live.state = state.clone();
            if duration > 0.0 {
                live.duration = duration;
            }
            let first = playing && !live.played;
            if first {
                live.played = true;
            }
            (first, session.resume)
        };
        if first_play
            && resume > 30.0
            && position < resume - 10.0
            && session.renderer.seek(resume).await.is_ok()
        {
            position = resume;
        }
        if playing {
            let prefetch = {
                let mut live = session.live();
                live.position = position;
                let due = !live.prefetched
                    && truthy(session.media.get("season").unwrap_or(&Value::Null))
                    && live.duration > 0.0
                    && position >= PREFETCH_AT * live.duration;
                if due {
                    live.prefetched = true;
                }
                due
            };
            if prefetch {
                let me = self.clone();
                let media = session.media.clone();
                tokio::spawn(async move {
                    if let Some(next) = me.next_of(&media).await {
                        let _ = me.resolve(&next, false).await;
                    }
                });
            }
        } else if state == "STOPPED" || state == "NO_MEDIA_PRESENT" {
            let (played, near_end) = {
                let live = session.live();
                (
                    live.played,
                    live.duration > 0.0 && live.position >= live.duration - 90.0,
                )
            };
            if played {
                // Watched to the end, or stopped from the TV's own remote.
                if near_end {
                    {
                        let mut live = session.live();
                        live.position = live.duration;
                    }
                    if let Some(upcoming) = self.upcoming(session).await {
                        self.save(session, true).await;
                        session.live().next = Some(Pending {
                            at: now() + self.timing.next_delay,
                            media: upcoming,
                            finding: false,
                        });
                        self.share(session); // paused: the play key starts the next one now
                        return;
                    }
                }
                self.end(session, false).await;
                return;
            }
            if now() - session.started_at > self.timing.start_timeout {
                session.live().error = "La TV n'a pas pu lire cette source.".into();
                self.end(session, true).await;
                return;
            }
        }
        self.share(session);
        self.save(session, false).await;
        for command in self.remote.pop() {
            let _ = self
                .control(&text(&command["action"]), &command["value"])
                .await;
        }
    }

    async fn next_of(&self, media: &Map<String, Value>) -> Option<Map<String, Value>> {
        let (season, episode, finished) = self
            .meta
            .next_episode(
                &text(media.get("id")?),
                number(media, "season"),
                number(media, "episode"),
                true,
            )
            .await;
        (!finished).then(|| {
            let mut next = media.clone();
            next.insert("season".into(), json!(season));
            next.insert("episode".into(), json!(episode));
            next
        })
    }

    /// (Debrid link, file name) of an episode, found like the players' « épisode suivant ».
    async fn resolve(
        &self,
        media: &Map<String, Value>,
        update_history: bool,
    ) -> Option<(String, String)> {
        let get = |k: &str| media.get(k).map(text).filter(|v| !v.is_empty());
        let episode = Episode {
            imdb_id: get("id").unwrap_or_default(),
            season: number(media, "season"),
            episode: number(media, "episode"),
            name: get("name").unwrap_or_else(|| "Série".into()),
            year: get("year").unwrap_or_default(),
            poster: get("poster").unwrap_or_default(),
        };
        let cfg = tokio::task::spawn_blocking(crate::config::load)
            .await
            .ok()?;
        let url = crate::play::resolve_episode(
            &self.meta,
            &self.links,
            &episode,
            "",
            &cfg,
            update_history,
        )
        .await
        .ok()?;
        let filename = self
            .links
            .recent(&(episode.imdb_id.clone(), episode.season, episode.episode))
            .map(|l| l.1)
            .unwrap_or_default();
        Some((url, filename))
    }

    /// The episode after this one, or None: a film, the series over.
    async fn upcoming(&self, session: &Session) -> Option<Map<String, Value>> {
        let media = &session.media;
        if !truthy(media.get("season")?) || !truthy(media.get("episode")?) {
            return None;
        }
        self.next_of(media).await
    }

    /// The countdown after an episode, then the next one on the same TV.
    async fn wait_next(self: &Arc<Self>, session: &Arc<Session>) {
        for command in self.remote.pop() {
            let _ = self
                .control(&text(&command["action"]), &command["value"])
                .await;
        }
        let media = {
            let mut live = session.live();
            let ended = live.ended;
            match live.next.as_mut() {
                Some(next) if !ended && now() >= next.at && !next.finding => {
                    next.finding = true;
                    next.media.clone()
                }
                _ => return,
            }
        };
        let found = Box::pin(self.resolve(&media, true)).await;
        let still = self.current().is_some_and(|c| Arc::ptr_eq(&c, session));
        if session.live().ended || !still {
            return; // stopped during the search
        }
        let code = episode_code(&media);
        let Some((url, filename)) = found.filter(|f| !f.0.is_empty()) else {
            {
                let mut live = session.live();
                live.next = None;
                live.error = format!("{code} introuvable ou pas encore sorti.");
            }
            self.end(session, true).await;
            return;
        };
        let name = media
            .get("name")
            .map(text)
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| "Série".into());
        let title = format!("{name} — {code}");
        if Box::pin(self.start(&session.device, &url, &title, media, &filename, 0.0))
            .await
            .is_err()
        {
            {
                let mut live = session.live();
                live.next = None;
                live.error = "La TV n'a pas pu lancer l'épisode suivant.".into();
            }
            self.end(session, true).await;
        }
    }

    async fn end(&self, session: &Arc<Session>, keep_error: bool) {
        self.save(session, true).await;
        session.live().ended = true;
        if !keep_error {
            let mut current = self.session.lock().unwrap_or_else(|e| e.into_inner());
            if current.as_ref().is_some_and(|c| Arc::ptr_eq(c, session)) {
                *current = None;
            }
        }
        self.remote
            .update(&json!({"playing": false, "paused": true}));
        self.presence(None);
    }

    fn share(&self, session: &Session) {
        let live = session.live();
        let media = &session.media;
        let get = |k: &str| media.get(k).map(text).filter(|v| !v.is_empty());
        self.remote.update(&json!({
            "playing": true, "paused": live.state != "PLAYING", "title": session.title, "sub_title": episode_code(media),
            "media_type": get("type").unwrap_or_else(|| "movie".into()), "poster": get("poster").unwrap_or_default(),
            "current_time": live.position, "duration": live.duration,
        }));
        drop(live);
        self.presence(Some(session));
    }

    /// Discord while the TV plays: on pause or resume, then every 15 seconds.
    fn presence(&self, session: Option<&Session>) {
        let Some(discord) = &self.discord else { return };
        let mut last = self.presence.lock().unwrap_or_else(|e| e.into_inner());
        let Some(session) = session else {
            *last = (None, 0.0);
            discord.clear();
            return;
        };
        let live = session.live();
        let paused = live.state != "PLAYING";
        if Some(paused) == last.0 && now() - last.1 < 15.0 {
            return;
        }
        *last = (Some(paused), now());
        let media = &session.media;
        let get = |k: &str| media.get(k).map(text).filter(|v| !v.is_empty());
        let (name, kind, poster, year) = (
            get("name"),
            get("type").unwrap_or_else(|| "movie".into()),
            get("poster").unwrap_or_default(),
            get("year").unwrap_or_default(),
        );
        let positive = |k: &str| Some(number(media, k)).filter(|n| *n != 0);
        discord.show(&Playing {
            title: name.as_deref().unwrap_or(&session.title),
            kind: &kind,
            season: positive("season"),
            episode: positive("episode"),
            episode_title: None,
            position: live.position,
            duration: live.duration,
            poster: Some(&poster),
            paused,
            year: Some(&year),
        });
    }

    async fn save(&self, session: &Session, force: bool) {
        let entry = {
            let mut live = session.live();
            let media = &session.media;
            if !truthy(media.get("id").unwrap_or(&Value::Null))
                || live.duration == 0.0
                || !live.played
            {
                return;
            }
            if !force && now() - live.saved_at < SAVE_EVERY {
                return;
            }
            live.saved_at = now();
            let get = |k: &str| media.get(k).map(text).filter(|v| !v.is_empty());
            let mut entry = Map::new();
            entry.insert("id".into(), media["id"].clone());
            entry.insert(
                "name".into(),
                json!(get("name").unwrap_or_else(|| session.title.clone())),
            );
            entry.insert(
                "type".into(),
                json!(get("type").unwrap_or_else(|| "movie".into())),
            );
            entry.insert("year".into(), json!(get("year").unwrap_or_default()));
            entry.insert("poster".into(), json!(get("poster").unwrap_or_default()));
            entry.insert(
                "season".into(),
                media.get("season").cloned().unwrap_or(Value::Null),
            );
            entry.insert(
                "episode".into(),
                media.get("episode").cloned().unwrap_or(Value::Null),
            );
            entry.insert(
                "filename".into(),
                json!(if session.filename.is_empty() {
                    &session.title
                } else {
                    &session.filename
                }),
            );
            entry.insert(
                "position".into(),
                json!(live.position.round_ties_even() as i64),
            );
            entry.insert(
                "duration".into(),
                json!(live.duration.round_ties_even() as i64),
            );
            entry
        };
        let db = self.db.clone();
        let _ =
            tokio::task::spawn_blocking(move || crate::library::record_history(&db, &entry)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn small_parts() {
        assert_eq!(mime_for("Dune.MP4", ""), "video/mp4");
        assert_eq!(mime_for("", "https://x/a/b.webm?x=1"), "video/webm");
        assert_eq!(mime_for("noext", "https://x/"), "video/x-mkv");
        assert_eq!(hms(3725.9), "1:02:05");
        assert_eq!(seconds_of(" 0:00:04.372 "), 4.0);
        assert_eq!(seconds_of("1:02:03"), 3723.0);
        assert_eq!(seconds_of("NOT_IMPLEMENTED"), 0.0);
        assert!(is_local_url("http://192.168.1.20:9197/dmr"));
        assert!(!is_local_url("http://127.0.0.1:9197/dmr"));
        assert!(!is_local_url("http://example.com/dmr"));
        assert_eq!(
            tag("<a><RelTime> 0:01:02 </RelTime></a>", "RelTime"),
            "0:01:02"
        );
        assert_eq!(escape("a&b<'\">"), "a&amp;b&lt;&#x27;&quot;&gt;");
    }

    #[test]
    fn description() {
        let xml = r#"<root xmlns="urn:schemas-upnp-org:device-1-0"><device><friendlyName>[TV] Salon</friendlyName><manufacturer>Samsung Electronics</manufacturer><modelName>QE55</modelName>
            <serviceList><service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType><controlURL>/rc</controlURL></service>
            <service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType><controlURL>/upnp/control/AVTransport1</controlURL></service></serviceList></device></root>"#;
        let tv = parse_description("http://192.168.1.20:9197/dmr", xml).unwrap();
        assert_eq!(
            tv["control_url"],
            "http://192.168.1.20:9197/upnp/control/AVTransport1"
        );
        assert_eq!(
            (tv["name"].as_str(), tv["host"].as_str()),
            (Some("[TV] Salon"), Some("192.168.1.20"))
        );
        assert!(parse_description("http://192.168.1.20/", "<root><device/></root>").is_none());
    }
}
