//! Playback without a debrid service: the torrent is read peer to peer
//! (librqbit). KINO fetches the chosen file in order and serves it to its own
//! player at http://127.0.0.1:<port>/p2p/<info hash>/<file index>, as a debrid
//! service would serve a link. While it plays the computer also sends pieces
//! to other peers: the user agrees to it once, after being told what it means.

use std::collections::{HashMap, HashSet};
use std::num::NonZeroU32;
use std::path::PathBuf;
use std::sync::{Arc, LazyLock};
use std::time::{Duration, Instant};

use librqbit::{
    AddTorrent, AddTorrentOptions, AddTorrentResponse, ListenerOptions, ManagedTorrent, Session,
    SessionOptions, limits::LimitsConfig,
};
use regex::Regex;
use serde_json::{Value, json};
use tokio::sync::Mutex;

use crate::db::Db;

const CONSENT_KEY: &str = "p2p_consent_v1";
const SETTINGS_KEY: &str = "p2p_settings_v1";
/// Bytes per second sent to other peers: « minimum » keeps the exchange going.
const UPLOAD_LIMITS: [(&str, u32); 4] = [
    ("minimum", 64 * 1024),
    ("256k", 256 * 1024),
    ("1m", 1024 * 1024),
    ("normal", 0),
];
/// Bytes per second received, to leave room for the rest of the house.
const DOWNLOAD_LIMITS: [(&str, u32); 4] = [
    ("none", 0),
    ("10m", 10 * 1024 * 1024),
    ("5m", 5 * 1024 * 1024),
    ("2m", 2 * 1024 * 1024),
];
const METADATA_TIMEOUT: Duration = Duration::from_secs(30);
const KEEP_TORRENTS: usize = 2;
const VIDEO_EXTENSIONS: [&str; 8] = [
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".webm",
];
/// librqbit has no BitTorrent protocol encryption: with « chiffrées
/// uniquement » chosen, KINO does not connect rather than connect in clear.
pub const ENCRYPTION_UNSUPPORTED: &str = "Les connexions chiffrées uniquement ne sont pas disponibles dans cette version : désactivez cette option dans Paramètres pour lire en P2P.";
pub const CONSENT_NEEDED: &str =
    "Acceptez la lecture P2P dans Paramètres avant de lancer un torrent sans service de débridage.";

static HASH_IN_LINK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"btih:([0-9a-fA-F]{40})").unwrap());
static HASH_IN_PATH: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"/([0-9a-fA-F]{40})(?:/|$)").unwrap());

pub fn cache_dir() -> PathBuf {
    let home = crate::paths::home();
    if cfg!(target_os = "macos") {
        home.join("Library/Caches/KINO/P2P")
    } else if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or(home)
            .join("KINO")
            .join("P2P")
    } else {
        home.join(".cache/kino/p2p")
    }
}

/// The 40-hex info hash of a magnet link or of a stream address holding one.
pub fn info_hash_of(magnet: &str, resolve_url: &str) -> String {
    [magnet, resolve_url]
        .iter()
        .find_map(|t| {
            HASH_IN_LINK
                .captures(t)
                .or_else(|| HASH_IN_PATH.captures(t))
                .map(|c| c[1].to_lowercase())
        })
        .unwrap_or_default()
}

fn is_video(name: &str) -> bool {
    let lower = name.to_lowercase();
    VIDEO_EXTENSIONS.iter().any(|e| lower.ends_with(e))
}

pub fn content_type(name: &str) -> &'static str {
    let lower = name.to_lowercase();
    match lower.rsplit('.').next().unwrap_or("") {
        "mp4" | "m4v" => "video/mp4",
        "webm" => "video/webm",
        "mov" => "video/quicktime",
        "ts" => "video/mp2t",
        "avi" => "video/x-msvideo",
        _ => "video/x-matroska",
    }
}

/// (start, end) inclusive from a Range header, None for the whole file;
/// Some((size, size)) when the range cannot be satisfied.
pub fn byte_range(header: &str, size: u64) -> Option<(u64, u64)> {
    static RANGE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^bytes=(\d*)-(\d*)$").unwrap());
    let c = RANGE.captures(header.trim())?;
    let (a, b) = (c[1].to_string(), c[2].to_string());
    if a.is_empty() && b.is_empty() {
        return None;
    }
    if a.is_empty() {
        let length = b.parse::<u64>().ok()?.min(size);
        return Some((size - length, size.saturating_sub(1)));
    }
    let start = a.parse::<u64>().ok()?;
    let end = if b.is_empty() {
        size.saturating_sub(1)
    } else {
        b.parse::<u64>().ok()?.min(size.saturating_sub(1))
    };
    Some(if start <= end && end < size {
        (start, end)
    } else {
        (size, size)
    })
}

/// The user's choices, each checked, with the defaults for the others.
pub fn settings(db: &Db) -> Value {
    let stored = db
        .get_config(SETTINGS_KEY)
        .filter(Value::is_object)
        .unwrap_or(json!({}));
    let mut stored = stored.as_object().cloned().unwrap_or_default();
    // Before 1.8 the upload choice was kept with the consent.
    if !stored.contains_key("upload")
        && let Some(old) = db
            .get_config(CONSENT_KEY)
            .and_then(|c| c.get("upload").cloned())
            .filter(crate::play::truthy)
    {
        stored.insert("upload".into(), old);
    }
    let pick = |key: &str, table: &[(&str, u32)], default: &str| {
        stored
            .get(key)
            .and_then(Value::as_str)
            .filter(|v| table.iter().any(|(k, _)| k == v))
            .unwrap_or(default)
            .to_string()
    };
    let port = stored
        .get("port")
        .and_then(Value::as_i64)
        .filter(|p| *p == 0 || (1024..=65535).contains(p))
        .filter(|_| stored["port"].is_i64())
        .unwrap_or(0);
    json!({"upload": pick("upload", &UPLOAD_LIMITS, "minimum"), "download": pick("download", &DOWNLOAD_LIMITS, "none"),
           "encrypted": stored.get("encrypted") == Some(&json!(true)), "port": port})
}

fn limit(table: &[(&str, u32)], key: &str) -> Option<NonZeroU32> {
    NonZeroU32::new(table.iter().find(|(k, _)| *k == key).map_or(0, |(_, v)| *v))
}

fn limits(choices: &Value) -> LimitsConfig {
    LimitsConfig {
        upload_bps: limit(
            &UPLOAD_LIMITS,
            choices["upload"].as_str().unwrap_or("minimum"),
        ),
        download_bps: limit(
            &DOWNLOAD_LIMITS,
            choices["download"].as_str().unwrap_or("none"),
        ),
    }
}

struct Live {
    session: Arc<Session>,
    port: u16,
    /// info hash → (torrent, last use, file being served).
    torrents: HashMap<String, (Arc<ManagedTorrent>, Instant, Option<usize>)>,
}

/// A torrent file being read: it waits for each piece as the player reads.
pub trait Readable: tokio::io::AsyncRead + tokio::io::AsyncSeek + Send {}
impl<T: tokio::io::AsyncRead + tokio::io::AsyncSeek + Send> Readable for T {}

/// One BitTorrent session for KINO, started on first use.
pub struct P2p {
    db: Arc<Db>,
    live: Mutex<Option<Live>>,
    /// Peers asked first for every torrent (a seeder on this computer in tests).
    known_peers: std::sync::Mutex<Vec<std::net::SocketAddr>>,
}

impl P2p {
    pub fn new(db: Arc<Db>) -> Arc<Self> {
        Arc::new(Self {
            db,
            live: Mutex::new(None),
            known_peers: Default::default(),
        })
    }

    pub fn add_known_peer(&self, peer: std::net::SocketAddr) {
        self.known_peers
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push(peer);
    }

    pub fn consent(&self) -> Option<Value> {
        self.db
            .get_config(CONSENT_KEY)
            .filter(|c| crate::play::truthy(&c["accepted_at"]))
    }

    pub async fn set_consent(&self, accepted: bool, upload: &Value) -> Result<(), String> {
        if !accepted {
            self.db
                .save_config(CONSENT_KEY, &json!({}))
                .map_err(|e| e.to_string())?;
            self.shutdown().await;
            return Ok(());
        }
        let upload = upload.as_str().map(|u| {
            if UPLOAD_LIMITS.iter().any(|(k, _)| *k == u) {
                u
            } else {
                "minimum"
            }
        });
        self.save_settings(&upload.map_or(json!({}), |u| json!({"upload": u})))
            .await?;
        self.db
            .save_config(
                CONSENT_KEY,
                &json!({"accepted_at": crate::db::now() as i64}),
            )
            .map_err(|e| e.to_string())
    }

    /// Saves the choices the user changed and applies them to a running session.
    pub async fn save_settings(&self, changes: &Value) -> Result<Value, String> {
        let mut value = settings(&self.db);
        for (key, table, message) in [
            ("upload", &UPLOAD_LIMITS, "Limite d'envoi inconnue."),
            (
                "download",
                &DOWNLOAD_LIMITS,
                "Limite de téléchargement inconnue.",
            ),
        ] {
            if let Some(v) = changes.get(key) {
                if !v
                    .as_str()
                    .is_some_and(|v| table.iter().any(|(k, _)| *k == v))
                {
                    return Err(message.into());
                }
                value[key] = v.clone();
            }
        }
        if let Some(v) = changes.get("encrypted") {
            value["encrypted"] = json!(v == &json!(true));
        }
        if let Some(v) = changes.get("port") {
            let port = match v {
                Value::Null => Some(0),
                Value::String(s) if s.is_empty() => Some(0),
                Value::String(s) => s.trim().parse::<i64>().ok(),
                Value::Number(n) => n.as_i64(),
                Value::Bool(b) => Some(i64::from(*b)),
                _ => None,
            }
            .unwrap_or(-1);
            if port != 0 && !(1024..=65535).contains(&port) {
                return Err("Choisissez un port entre 1024 et 65535, ou laissez vide.".into());
            }
            value["port"] = json!(port);
        }
        self.db
            .save_config(SETTINGS_KEY, &value)
            .map_err(|e| e.to_string())?;
        let mut live = self.live.lock().await;
        if let Some(current) = live.as_ref() {
            if value["port"].as_u64() != Some(u64::from(current.port)) && value["port"] != 0 {
                // Another port: a new session (its torrents start again from the network).
                if let Some(old) = live.take() {
                    old.session.stop().await;
                }
            } else {
                let l = limits(&value);
                current.session.ratelimits.set_upload_bps(l.upload_bps);
                current.session.ratelimits.set_download_bps(l.download_bps);
            }
        }
        Ok(value)
    }

    /// Bytes the torrents being read take on this computer.
    pub fn cache_size() -> u64 {
        fn walk(dir: &std::path::Path) -> u64 {
            std::fs::read_dir(dir)
                .into_iter()
                .flatten()
                .flatten()
                .map(|e| match e.file_type() {
                    Ok(t) if t.is_dir() => walk(&e.path()),
                    Ok(t) if t.is_file() => e.metadata().map_or(0, |m| m.len()),
                    _ => 0,
                })
                .sum()
        }
        walk(&cache_dir())
    }

    /// What Paramètres shows: whether P2P can be used, is accepted, and how.
    pub fn state(&self) -> Value {
        let mut state = json!({"available": true, "accepted": self.consent().is_some()});
        if let (Some(s), Some(extra)) = (state.as_object_mut(), settings(&self.db).as_object()) {
            s.extend(extra.clone());
        }
        state["cache_bytes"] = json!(Self::cache_size());
        state["encryption_supported"] = json!(false);
        state
    }

    /// Stops what is being read and removes its files.
    pub async fn clear_cache(&self) {
        self.shutdown().await;
        let _ = std::fs::remove_dir_all(cache_dir());
    }

    pub async fn shutdown(&self) {
        if let Some(live) = self.live.lock().await.take() {
            for (torrent, _, _) in live.torrents.values() {
                let _ = live.session.delete(torrent.id().into(), true).await;
            }
            live.session.stop().await;
        }
    }

    async fn session<'a>(&self, live: &'a mut Option<Live>) -> Result<&'a mut Live, String> {
        if live.is_none() {
            let choices = settings(&self.db);
            let port = choices["port"].as_u64().unwrap_or(0) as u16;
            let folder = cache_dir();
            // What a previous session left is not needed: playback restarts from the network.
            let _ = std::fs::remove_dir_all(&folder);
            std::fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
            let options = SessionOptions {
                disable_local_service_discovery: true,
                ratelimits: limits(&choices),
                listen: Some(ListenerOptions {
                    listen_addr: (std::net::Ipv6Addr::UNSPECIFIED, port).into(),
                    ..Default::default()
                }),
                client_name_and_version: Some(format!("KINO {}", crate::backup::app_version())),
                ..Default::default()
            };
            let session = Session::new_with_opts(folder, options)
                .await
                .map_err(|e| e.to_string())?;
            *live = Some(Live {
                session,
                port,
                torrents: HashMap::new(),
            });
        }
        live.as_mut().ok_or_else(|| "P2P".to_string())
    }

    /// Like a debrid service: {"ready", "files": [{download, filename, filesize_bytes}]}.
    /// A direct web link plays as it is; a torrent is added, its file list read,
    /// and each video gets a local address.
    pub async fn prepare(
        &self,
        magnet: &str,
        resolve_url: &str,
        base_url: &str,
    ) -> Result<Value, String> {
        if (resolve_url.starts_with("http://") || resolve_url.starts_with("https://"))
            && !magnet.starts_with("magnet:")
            && info_hash_of("", resolve_url).is_empty()
        {
            let name = resolve_url
                .split(['?', '#'])
                .next()
                .unwrap_or("")
                .rsplit('/')
                .next()
                .unwrap_or("");
            let name = percent_encoding::percent_decode_str(name)
                .decode_utf8_lossy()
                .into_owned();
            return Ok(
                json!({"ready": true, "files": [{"download": resolve_url, "filename": if name.is_empty() { "video".to_string() } else { name }}]}),
            );
        }
        let hash = info_hash_of(magnet, resolve_url);
        if hash.is_empty() {
            return Ok(json!({"ready": false, "files": []}));
        }
        if self.consent().is_none() {
            return Err(CONSENT_NEEDED.into());
        }
        if settings(&self.db)["encrypted"] == true {
            return Err(ENCRYPTION_UNSUPPORTED.into());
        }
        let magnet = if magnet.starts_with("magnet:") {
            magnet.to_string()
        } else {
            format!("magnet:?xt=urn:btih:{hash}")
        };
        let Some(torrent) = self.torrent(&hash, &magnet, false).await? else {
            return Ok(json!({"ready": false, "files": []}));
        };
        let files: Vec<Value> = torrent
            .with_metadata(|m| {
                m.file_infos
                    .iter()
                    .enumerate()
                    .filter(|(_, f)| is_video(&f.relative_filename.to_string_lossy()))
                    .map(|(i, f)| {
                        let path = f.relative_filename.to_string_lossy().replace('\\', "/");
                        json!({"download": format!("{base_url}/p2p/{hash}/{i}"), "filename": path.rsplit('/').next().unwrap_or(&path), "filesize_bytes": f.len, "p2p": true})
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(json!({"ready": !files.is_empty(), "files": files, "p2p": true}))
    }

    /// The torrent of `hash`, added if needed: its file list once the peers
    /// sent it (None after 30 s). New ones fetch only their videos, or nothing
    /// (`idle`, a volume pack) until a file is asked for.
    async fn torrent(
        &self,
        hash: &str,
        magnet: &str,
        idle: bool,
    ) -> Result<Option<Arc<ManagedTorrent>>, String> {
        let mut guard = self.live.lock().await;
        let live = self.session(&mut guard).await?;
        if let Some(entry) = live.torrents.get_mut(hash) {
            entry.1 = Instant::now();
            return Ok(Some(entry.0.clone()));
        }
        let peers = self
            .known_peers
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        let options = AddTorrentOptions {
            overwrite: true,
            paused: idle,
            initial_peers: (!peers.is_empty()).then_some(peers),
            ..Default::default()
        };
        let added = tokio::time::timeout(
            METADATA_TIMEOUT,
            live.session
                .add_torrent(AddTorrent::from_url(magnet), Some(options)),
        )
        .await;
        let torrent = match added {
            Ok(Ok(AddTorrentResponse::Added(_, t) | AddTorrentResponse::AlreadyManaged(_, t))) => t,
            _ => return Ok(None),
        };
        if !idle {
            // Until the player asks for one, only the videos are wanted.
            let videos: HashSet<usize> = torrent
                .with_metadata(|m| {
                    m.file_infos
                        .iter()
                        .enumerate()
                        .filter(|(_, f)| is_video(&f.relative_filename.to_string_lossy()))
                        .map(|(i, _)| i)
                        .collect()
                })
                .unwrap_or_default();
            if !videos.is_empty() {
                let _ = live.session.update_only_files(&torrent, &videos).await;
            }
        }
        live.torrents
            .insert(hash.to_string(), (torrent.clone(), Instant::now(), None));
        // Only the latest torrents stay, with their files.
        let mut by_age: Vec<(String, Instant)> = live
            .torrents
            .iter()
            .map(|(k, v)| (k.clone(), v.1))
            .collect();
        by_age.sort_by_key(|(_, at)| *at);
        for (old, _) in by_age
            .iter()
            .take(by_age.len().saturating_sub(KEEP_TORRENTS))
        {
            if let Some((t, _, _)) = live.torrents.remove(old) {
                let _ = live.session.delete(t.id().into(), true).await;
            }
        }
        Ok(Some(torrent))
    }

    /// Every file of a torrent: (index, path inside it, size). Nothing is fetched yet.
    pub async fn files(
        &self,
        hash: &str,
        magnet: &str,
    ) -> Result<Option<Vec<(usize, String, u64)>>, String> {
        let Some(torrent) = self.torrent(hash, magnet, true).await? else {
            return Ok(None);
        };
        Ok(torrent
            .with_metadata(|m| {
                m.file_infos
                    .iter()
                    .enumerate()
                    .map(|(i, f)| {
                        (
                            i,
                            f.relative_filename.to_string_lossy().replace('\\', "/"),
                            f.len,
                        )
                    })
                    .collect()
            })
            .ok())
    }

    /// Fetches these files of a torrent already added, and only them.
    pub async fn fetch(&self, hash: &str, files: &HashSet<usize>) -> Result<(), String> {
        // A torrent just added may still be checking its files: its choice of
        // files can only change once that is done (lock released meanwhile).
        let starting = {
            let guard = self.live.lock().await;
            let live = guard.as_ref().ok_or("Le téléchargement s'est arrêté.")?;
            live.torrents
                .get(hash)
                .ok_or("Le téléchargement s'est arrêté.")?
                .0
                .clone()
        };
        tokio::time::timeout(Duration::from_secs(30), starting.wait_until_initialized())
            .await
            .map_err(|_| "Le torrent met trop de temps à se préparer.".to_string())?
            .map_err(|e| e.to_string())?;
        let guard = self.live.lock().await;
        let live = guard.as_ref().ok_or("Le téléchargement s'est arrêté.")?;
        let (torrent, _, _) = live
            .torrents
            .get(hash)
            .ok_or("Le téléchargement s'est arrêté.")?;
        live.session
            .update_only_files(torrent, files)
            .await
            .map_err(|e| e.to_string())?;
        if torrent.is_paused() {
            live.session
                .unpause(torrent)
                .await
                .map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    /// (bytes done, where the file is written) of one file being fetched.
    pub async fn progress(&self, hash: &str, index: usize) -> Option<(u64, PathBuf)> {
        let guard = self.live.lock().await;
        let (torrent, _, _) = guard.as_ref()?.torrents.get(hash)?;
        let done = *torrent.stats().file_progress.get(index)?;
        let relative = torrent
            .with_metadata(|m| m.file_infos.get(index).map(|f| f.relative_filename.clone()))
            .ok()??;
        Some((done, torrent.output_folder().join(relative)))
    }

    /// A file of a torrent being read, its size and type, and a stream that
    /// waits for each piece (the file becomes the only one fetched).
    pub async fn open(
        &self,
        hash: &str,
        index: usize,
    ) -> Option<(std::pin::Pin<Box<dyn Readable>>, u64, &'static str)> {
        let mut guard = self.live.lock().await;
        let live = guard.as_mut()?;
        let session = live.session.clone();
        let entry = live.torrents.get_mut(hash)?;
        entry.1 = Instant::now();
        let torrent = entry.0.clone();
        let (len, name) = torrent
            .with_metadata(|m| {
                m.file_infos
                    .get(index)
                    .map(|f| (f.len, f.relative_filename.to_string_lossy().into_owned()))
            })
            .ok()??;
        if entry.2 != Some(index) {
            let _ = session
                .update_only_files(&torrent, &HashSet::from([index]))
                .await;
            entry.2 = Some(index);
        }
        drop(guard);
        let stream = torrent.stream(index).await.ok()?;
        Some((Box::pin(stream), len, content_type(&name)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn small_parts() {
        let hash = "0123456789abcdef0123456789abcdef01234567";
        assert_eq!(
            info_hash_of(&format!("magnet:?xt=urn:btih:{}", hash.to_uppercase()), ""),
            hash
        );
        assert_eq!(
            info_hash_of("", &format!("https://x/resolve/{hash}/1")),
            hash
        );
        assert_eq!(info_hash_of("", "https://x/y.mkv"), "");
        assert_eq!(byte_range("bytes=0-99", 1000), Some((0, 99)));
        assert_eq!(byte_range("bytes=-100", 1000), Some((900, 999)));
        assert_eq!(byte_range("bytes=900-", 1000), Some((900, 999)));
        assert_eq!(byte_range("bytes=2000-", 1000), Some((1000, 1000)));
        assert_eq!(byte_range("items=1-2", 1000), None);
        assert_eq!(content_type("A.MP4"), "video/mp4");
        assert!(is_video("Show/S01E01.MKV") && !is_video("sample.nfo"));
    }

    #[test]
    fn choices() {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(Db::open(&dir.path().join("k.db")).unwrap());
        assert_eq!(
            settings(&db),
            json!({"upload": "minimum", "download": "none", "encrypted": false, "port": 0})
        );
        db.save_config(CONSENT_KEY, &json!({"accepted_at": 1, "upload": "1m"}))
            .unwrap();
        assert_eq!(settings(&db)["upload"], "1m");
        let p2p = P2p::new(db.clone());
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            assert_eq!(
                p2p.save_settings(&json!({"port": 80})).await.unwrap_err(),
                "Choisissez un port entre 1024 et 65535, ou laissez vide."
            );
            assert!(p2p.save_settings(&json!({"upload": "huge"})).await.is_err());
            let saved = p2p
                .save_settings(&json!({"port": "6881", "download": "5m", "encrypted": true}))
                .await
                .unwrap();
            assert_eq!(
                saved,
                json!({"upload": "1m", "download": "5m", "encrypted": true, "port": 6881})
            );
            assert_eq!(
                p2p.prepare(
                    &format!("magnet:?xt=urn:btih:{}", "a".repeat(40)),
                    "",
                    "http://127.0.0.1:1"
                )
                .await
                .unwrap_err(),
                ENCRYPTION_UNSUPPORTED
            );
            assert_eq!(
                p2p.prepare("", "https://cdn.x/My%20Film.mkv?t=1", "")
                    .await
                    .unwrap()["files"][0]["filename"],
                "My Film.mkv"
            );
            p2p.set_consent(false, &Value::Null).await.unwrap();
            assert_eq!(
                p2p.prepare(&format!("magnet:?xt=urn:btih:{}", "a".repeat(40)), "", "")
                    .await
                    .unwrap_err(),
                CONSENT_NEEDED
            );
        });
    }
}
