//! The offline library: films and episodes kept on this computer, one download
//! at a time, resumable when the server can prove the file did not change,
//! and served back to the player from KINO's own address.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::db::{Db, now};
use crate::meta::Meta;
use crate::net::BROWSER;
use crate::play::{Episode, EpisodeLinks};

const VIDEO_EXTENSIONS: [&str; 8] = [
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".webm",
];
/// A download never fills the disk to the last byte.
const DISK_RESERVE: u64 = 1024 * 1024 * 1024;
static EPISODE_SOURCE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^kino-episode:(tt\d{1,12})/(\d{1,4})/(\d{1,5})$").unwrap());
static FORBIDDEN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"[<>:"/\\|?*\x00-\x1f]"#).unwrap());
static CONTENT_RANGE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^bytes (\d+)-(\d+)/(\d+|\*)$").unwrap());
static IMDB: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^tt\d{1,12}$").unwrap());
pub static DOWNLOAD_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[0-9a-f]{32}$").unwrap());

/// A refusal shown as is, with a code the page reads.
#[derive(Debug, PartialEq)]
pub struct OfflineError {
    pub message: String,
    pub code: &'static str,
}

fn refuse<T>(message: &str, code: &'static str) -> Result<T, OfflineError> {
    Err(OfflineError {
        message: message.into(),
        code,
    })
}

fn invalid<T>(message: &str) -> Result<T, OfflineError> {
    refuse(message, "invalid_download")
}

fn text(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false))
        && v != &json!(0)
        && v != &json!("")
        && v != &json!([])
        && v != &json!({})
}

fn cut(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// A plain video file name, without any folder.
fn safe_name(name: &str) -> Result<String, OfflineError> {
    let last = name
        .replace('\\', "/")
        .rsplit('/')
        .next()
        .unwrap_or("")
        .to_string();
    let cleaned: String = cut(
        FORBIDDEN
            .replace_all(&last, "_")
            .trim_matches(|c| c == ' ' || c == '.'),
        180,
    );
    let lower = cleaned.to_lowercase();
    let suffix = lower.rfind('.').map(|i| &lower[i..]).unwrap_or("");
    if cleaned.is_empty() || !VIDEO_EXTENSIONS.contains(&suffix) {
        return invalid("Choisissez un fichier vidéo pris en charge.");
    }
    Ok(cleaned)
}

/// What KINO keeps of the title: enough to show and play it.
fn media_snapshot(media: &Value) -> Map<String, Value> {
    let mut out = Map::new();
    let Some(media) = media.as_object() else {
        out.insert("season".into(), Value::Null);
        out.insert("episode".into(), Value::Null);
        return out;
    };
    for key in [
        "id", "name", "type", "year", "poster", "runtime", "season", "episode",
    ] {
        if let Some(v) = media.get(key) {
            let kept = if key == "season" || key == "episode" {
                v.clone()
            } else {
                json!(cut(&text(v), 500))
            };
            out.insert(key.into(), kept);
        }
    }
    for key in ["season", "episode"] {
        let value = out.get(key).cloned().unwrap_or(Value::Null);
        let number = if truthy(&value) {
            match &value {
                Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)),
                Value::String(s) => s.trim().parse::<i64>().ok(),
                Value::Bool(b) => Some(*b as i64),
                _ => None,
            }
            .map(|n| n.clamp(1, 10000))
        } else {
            None
        };
        out.insert(key.into(), number.map(Value::from).unwrap_or(Value::Null));
    }
    out
}

fn fingerprint(media: &Map<String, Value>, filename: &str) -> String {
    let part = |k: &str| {
        media
            .get(k)
            .filter(|v| truthy(v))
            .map(text)
            .unwrap_or_default()
    };
    let identity = format!(
        "{}|{}|{}|{}",
        media.get("id").map(text).unwrap_or_default(),
        part("season"),
        part("episode"),
        filename.to_lowercase()
    );
    hex::encode(Sha256::digest(identity.as_bytes()))
}

fn subtitle_snapshot(value: &Value) -> String {
    if !value.is_object() || value["lang"] == "off" {
        return "{}".into();
    }
    let Some(cues) = value["cues"].as_array().filter(|c| c.len() <= 10000) else {
        return "{}".into();
    };
    let number = |v: &Value| {
        v.as_f64()
            .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
    };
    let clean: Vec<Value> = cues
        .iter()
        .filter(|c| c.is_object())
        .filter_map(|c| {
            let (start, end) = (number(&c["start"])?, number(&c["end"])?);
            (0.0 <= start && start < end && end < 1_000_000.0)
                .then(|| json!({"start": start, "end": end, "text": cut(&if truthy(&c["text"]) { text(&c["text"]) } else { String::new() }, 1000)}))
        })
        .collect();
    if clean.is_empty() {
        return "{}".into();
    }
    let field = |k: &str, d: &str, n: usize| {
        cut(
            &if truthy(&value[k]) {
                text(&value[k])
            } else {
                d.to_string()
            },
            n,
        )
    };
    let result = json!({"label": field("label", "CC", 80), "lang": field("lang", "", 12), "kind": field("kind", "opensubtitles", 30),
                        "variant": field("variant", "standard", 30), "cues": clean})
    .to_string();
    if result.len() <= 2_000_000 {
        result
    } else {
        "{}".into()
    }
}

fn expand(dest: &str) -> PathBuf {
    match dest.strip_prefix("~") {
        Some(rest) => crate::paths::home().join(rest.trim_start_matches(['/', '\\'])),
        None => PathBuf::from(dest),
    }
}

/// The « KINO Offline » folder inside the download folder.
fn offline_root(dest: &str) -> Result<PathBuf, OfflineError> {
    let root = expand(dest).join("KINO Offline");
    std::fs::create_dir_all(&root)
        .and_then(|_| root.canonicalize())
        .or_else(|_| {
            refuse(
                "Le dossier de téléchargement est inaccessible.",
                "folder_unavailable",
            )
        })
}

type Row = Map<String, Value>;

fn read_rows(db: &Db, id: Option<&str>) -> Vec<Row> {
    db.with(|c| {
        let sql = if id.is_some() {
            "SELECT * FROM offline_downloads WHERE id=?"
        } else {
            "SELECT * FROM offline_downloads ORDER BY created_at DESC"
        };
        let mut s = c.prepare(sql)?;
        let names: Vec<String> = s.column_names().iter().map(|n| n.to_string()).collect();
        let map = |r: &rusqlite::Row| -> rusqlite::Result<Row> {
            let mut row = Map::new();
            for (i, name) in names.iter().enumerate() {
                let value = match r.get_ref(i)? {
                    rusqlite::types::ValueRef::Null => Value::Null,
                    rusqlite::types::ValueRef::Integer(n) => json!(n),
                    rusqlite::types::ValueRef::Real(f) => json!(f),
                    rusqlite::types::ValueRef::Text(t) => json!(String::from_utf8_lossy(t)),
                    rusqlite::types::ValueRef::Blob(_) => Value::Null,
                };
                row.insert(name.clone(), value);
            }
            Ok(row)
        };
        match id {
            Some(id) => s.query_map([id], map)?.collect(),
            None => s.query_map([], map)?.collect(),
        }
    })
    .unwrap_or_default()
}

fn update(db: &Db, id: &str, changes: &[(&str, Value)]) {
    let mut changes: Vec<(&str, Value)> = changes.to_vec();
    changes.push(("updated_at", json!(now())));
    let clauses = changes
        .iter()
        .map(|(k, _)| format!("{k}=?"))
        .collect::<Vec<_>>()
        .join(", ");
    let sql_value = |v: &Value| -> rusqlite::types::Value {
        match v {
            Value::Null => rusqlite::types::Value::Null,
            Value::Bool(b) => rusqlite::types::Value::Integer(*b as i64),
            Value::Number(n) => n
                .as_i64()
                .map(rusqlite::types::Value::Integer)
                .unwrap_or_else(|| rusqlite::types::Value::Real(n.as_f64().unwrap_or(0.0))),
            Value::String(s) => rusqlite::types::Value::Text(s.clone()),
            other => rusqlite::types::Value::Text(other.to_string()),
        }
    };
    let mut values: Vec<rusqlite::types::Value> =
        changes.iter().map(|(_, v)| sql_value(v)).collect();
    values.push(rusqlite::types::Value::Text(id.to_string()));
    let _ = db.with(|c| {
        c.execute(
            &format!("UPDATE offline_downloads SET {clauses} WHERE id=?"),
            rusqlite::params_from_iter(values),
        )
    });
}

/// A path of a download, only inside its own folder and named after it.
fn managed(row: &Row, key: &str) -> Result<PathBuf, OfflineError> {
    let path = PathBuf::from(row.get(key).and_then(Value::as_str).unwrap_or(""));
    let root = PathBuf::from(row.get("root_path").and_then(Value::as_str).unwrap_or(""));
    let prefix = format!("{}_", row.get("id").and_then(Value::as_str).unwrap_or("\0"));
    let link = |p: &Path| std::fs::symlink_metadata(p).is_ok_and(|m| m.file_type().is_symlink());
    let named = path
        .file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|n| n.starts_with(&prefix));
    if !named || link(&path) || link(&root) {
        return invalid("Chemin du téléchargement invalide.");
    }
    let parent = path
        .parent()
        .map(|p| p.canonicalize().unwrap_or_else(|_| p.to_path_buf()));
    let root_real = root.canonicalize().unwrap_or(root);
    if parent.as_deref() != Some(root_real.as_path()) {
        return invalid("Chemin du téléchargement invalide.");
    }
    Ok(path)
}

fn number(row: &Row, key: &str) -> i64 {
    row.get(key)
        .and_then(|v| v.as_i64().or_else(|| v.as_f64().map(|f| f as i64)))
        .unwrap_or(0)
}

fn field<'a>(row: &'a Row, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}

pub struct Library {
    pub db: Arc<Db>,
    active: Mutex<HashMap<String, Arc<AtomicBool>>>,
    /// One change of the queue at a time.
    lock: Mutex<()>,
    client: reqwest::Client,
    /// What finds an episode's link when its turn comes.
    pub episodes: Option<(Meta, Arc<EpisodeLinks>)>,
}

impl Library {
    pub fn new(db: Arc<Db>, episodes: Option<(Meta, Arc<EpisodeLinks>)>) -> Arc<Self> {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(30))
            .read_timeout(Duration::from_secs(30))
            .build()
            .unwrap_or_default();
        Arc::new(Self {
            db,
            active: Mutex::new(HashMap::new()),
            lock: Mutex::new(()),
            client,
            episodes,
        })
    }

    fn get(&self, id: &str) -> Option<Row> {
        read_rows(&self.db, Some(id)).into_iter().next()
    }

    fn running(&self, id: &str) -> bool {
        self.active
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .contains_key(id)
    }

    /// A completed download whose file is still there, of the size recorded.
    fn available(&self, row: &mut Row) -> bool {
        if field(row, "status") != "completed" {
            return false;
        }
        let ready = managed(row, "file_path")
            .ok()
            .and_then(|p| std::fs::metadata(&p).ok().filter(|m| m.is_file()))
            .is_some_and(|m| {
                let size = m.len() as i64;
                let total = number(row, "bytes_total");
                size > 0 && size == number(row, "bytes_done") && (total == 0 || size == total)
            });
        if !ready {
            update(
                &self.db,
                field(row, "id"),
                &[
                    ("status", json!("interrupted")),
                    ("error_code", json!("missing_file")),
                ],
            );
            row.insert("status".into(), json!("interrupted"));
        }
        ready
    }

    fn public(&self, mut row: Row) -> Value {
        let available = self.available(&mut row);
        let (total, done) = (number(&row, "bytes_total"), number(&row, "bytes_done"));
        let id = field(&row, "id").to_string();
        let mut media: Value = serde_json::from_str(if field(&row, "media_json").is_empty() {
            "{}"
        } else {
            field(&row, "media_json")
        })
        .unwrap_or(json!({}));
        if available {
            media["offline_id"] = json!(id);
            let poster = PathBuf::from(field(&row, "poster_path")).is_file();
            media["poster"] = json!(if poster {
                format!("/api/offline/poster/{id}")
            } else {
                String::new()
            });
        }
        let progress = if total != 0 {
            json!(
                crate::sources::stream::py_round(done as f64 * 100.0 / total as f64, 1).min(100.0)
            )
        } else {
            json!(0)
        };
        let subtitles: Value = serde_json::from_str(if field(&row, "subtitles_json").is_empty() {
            "{}"
        } else {
            field(&row, "subtitles_json")
        })
        .unwrap_or(json!({}));
        json!({
            "id": id, "filename": row["filename"], "title": row["title"], "status": row["status"], "error_code": row["error_code"],
            "bytes_done": done, "bytes_total": total, "progress": progress, "available": available, "media": media.clone(),
            "poster": if available { media.get("poster").cloned().unwrap_or(json!("")) } else { json!("") },
            "has_subtitles": truthy(&subtitles), "url": if available { format!("/api/offline/media/{id}") } else { String::new() },
            "created_at": row["created_at"],
        })
    }

    /// Every download, newest first, by id.
    pub fn list(&self) -> Map<String, Value> {
        read_rows(&self.db, None)
            .into_iter()
            .map(|r| (field(&r, "id").to_string(), self.public(r)))
            .collect()
    }

    pub fn usage_bytes(&self) -> u64 {
        read_rows(&self.db, None)
            .iter()
            .flat_map(|r| ["file_path", "part_path", "poster_path"].map(|k| managed(r, k).ok()))
            .flatten()
            .filter_map(|p| std::fs::metadata(p).ok().filter(|m| m.is_file()))
            .map(|m| m.len())
            .sum()
    }

    /// Free space on the volume that receives downloads (the folder may not exist yet).
    pub fn free_bytes(dest: &str) -> Option<u64> {
        let mut path = expand(dest);
        while !path.exists() {
            path = path.parent()?.to_path_buf();
        }
        fs4::available_space(&path).ok()
    }

    /// A first start after the app closed while writing: those parts stay resumable.
    pub fn reconcile(self: &Arc<Self>) {
        let _ = self.db.with(|c| {
            c.execute(
                "UPDATE offline_downloads SET status='interrupted', error_code='app_closed', updated_at=? WHERE status IN ('queued', 'downloading')",
                [now()],
            )
        });
        self.start_next();
    }

    fn new_row(
        id: &str,
        media: &Map<String, Value>,
        name: &str,
        root: &Path,
        status: &str,
        source: &str,
        subtitles: String,
    ) -> Value {
        let poster = media.get("poster").map(text).unwrap_or_default();
        let at = now();
        json!({
            "id": id, "fingerprint": fingerprint(media, name), "status": status, "source_url": source, "filename": name,
            "title": cut(&media.get("name").filter(|v| truthy(v)).map(text).unwrap_or_else(|| name.to_string()), 240),
            "media_json": Value::Object(media.clone()).to_string(), "subtitles_json": subtitles,
            "poster_url": if poster.starts_with("http://") || poster.starts_with("https://") { poster } else { String::new() },
            "root_path": root.to_string_lossy(), "file_path": root.join(format!("{id}_{name}")).to_string_lossy(),
            "part_path": root.join(format!("{id}_{name}.part")).to_string_lossy(), "poster_path": root.join(format!("{id}_poster")).to_string_lossy(),
            "bytes_done": 0, "bytes_total": 0, "etag": "", "last_modified": "", "accept_ranges": 0, "error_code": "", "created_at": at, "updated_at": at,
        })
    }

    fn insert(&self, row: &Value) {
        let Some(map) = row.as_object() else { return };
        let columns = map.keys().cloned().collect::<Vec<_>>().join(", ");
        let marks = vec!["?"; map.len()].join(", ");
        let values: Vec<rusqlite::types::Value> = map
            .values()
            .map(|v| match v {
                Value::Number(n) => n
                    .as_i64()
                    .map(rusqlite::types::Value::Integer)
                    .unwrap_or_else(|| rusqlite::types::Value::Real(n.as_f64().unwrap_or(0.0))),
                Value::String(s) => rusqlite::types::Value::Text(s.clone()),
                _ => rusqlite::types::Value::Null,
            })
            .collect();
        let _ = self.db.with(|c| {
            c.execute(
                &format!("INSERT INTO offline_downloads ({columns}) VALUES ({marks})"),
                rusqlite::params_from_iter(values),
            )
        });
    }

    /// Keeps a film or an episode from a playable link; (id, already there).
    pub fn create(
        self: &Arc<Self>,
        url: &str,
        filename: &str,
        dest: &str,
        media: &Value,
        subtitles: &Value,
    ) -> Result<(String, bool), OfflineError> {
        let parsed = reqwest::Url::parse(url).ok().filter(|u| {
            ["http", "https"].contains(&u.scheme()) && u.host_str().is_some_and(|h| !h.is_empty())
        });
        if parsed.is_none() {
            return invalid("Lien vidéo invalide.");
        }
        let name = safe_name(filename)?;
        let clean = media_snapshot(media);
        let root = offline_root(dest)?;
        let print = fingerprint(&clean, &name);
        let guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        // The same title and file already known: only the first one counts.
        if let Some(mut row) = read_rows(&self.db, None)
            .into_iter()
            .find(|r| field(r, "fingerprint") == print)
        {
            let id = field(&row, "id").to_string();
            let status = field(&row, "status").to_string();
            if status == "completed" && self.available(&mut row)
                || status == "queued"
                || status == "downloading"
            {
                return Ok((id, true));
            }
            // A fresh link chosen in the sources is an explicit restart when the old one expired.
            if field(&row, "source_url") != url {
                drop(guard);
                self.resume(&id, true, Some(url))?;
                return Ok((id, false));
            }
            return Ok((id, true));
        }
        let id = hex::encode(rand::random::<[u8; 16]>());
        self.insert(&Self::new_row(
            &id,
            &clean,
            &name,
            &root,
            "queued",
            url,
            subtitle_snapshot(subtitles),
        ));
        self.launch(&id);
        Ok((id, false))
    }

    /// Adds one episode of a season to the queue; its link is asked for when its turn comes.
    pub fn queue_episode(
        self: &Arc<Self>,
        imdb_id: &Value,
        season: &Value,
        episode: &Value,
        dest: &str,
        media: &Map<String, Value>,
    ) -> Result<(String, bool), OfflineError> {
        let imdb = text(imdb_id);
        if !IMDB.is_match(&imdb) {
            return invalid("Série inconnue.");
        }
        let whole = |v: &Value| match v {
            Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)),
            Value::String(s) => s.trim().parse().ok(),
            Value::Bool(b) => Some(*b as i64),
            _ => None,
        };
        let (Some(season), Some(episode)) = (whole(season), whole(episode)) else {
            return invalid("Épisode inconnu.");
        };
        if !(1..=9999).contains(&season) || !(1..=99999).contains(&episode) {
            return invalid("Épisode inconnu.");
        }
        let mut full = media.clone();
        full.extend([
            ("id".into(), json!(imdb)),
            ("type".into(), json!("series")),
            ("season".into(), json!(season)),
            ("episode".into(), json!(episode)),
        ]);
        let clean = media_snapshot(&Value::Object(full));
        let series = clean
            .get("name")
            .filter(|v| truthy(v))
            .map(text)
            .unwrap_or_else(|| "Série".into());
        let name = safe_name(&format!("{series} S{season:02}E{episode:02}.mkv"))?;
        let root = offline_root(dest)?;
        {
            let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
            for row in read_rows(&self.db, None) {
                let other: Value =
                    serde_json::from_str(field(&row, "media_json")).unwrap_or(json!({}));
                if other["id"] == imdb.as_str()
                    && other["season"] == season
                    && other["episode"] == episode
                    && field(&row, "status") != "failed"
                {
                    return Ok((field(&row, "id").to_string(), true));
                }
            }
            let id = hex::encode(rand::random::<[u8; 16]>());
            let mut row = Self::new_row(
                &id,
                &clean,
                &name,
                &root,
                "waiting",
                &format!("kino-episode:{imdb}/{season}/{episode}"),
                String::new(),
            );
            row["subtitles_json"] = json!("");
            self.insert(&row);
            drop(_guard);
            self.start_next();
            Ok((id, false))
        }
    }

    /// Starts the oldest waiting episode when nothing else downloads.
    pub fn start_next(self: &Arc<Self>) {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        if !self
            .active
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .is_empty()
        {
            return;
        }
        let waiting = read_rows(&self.db, None)
            .into_iter()
            .filter(|r| field(r, "status") == "waiting")
            .min_by(|a, b| {
                let at = |r: &Row| r.get("created_at").and_then(Value::as_f64).unwrap_or(0.0);
                at(a)
                    .total_cmp(&at(b))
                    .then(field(a, "id").cmp(field(b, "id")))
            });
        if let Some(row) = waiting {
            update(&self.db, field(&row, "id"), &[("status", json!("queued"))]);
            self.launch(field(&row, "id"));
        }
    }

    fn launch(self: &Arc<Self>, id: &str) {
        let stop = Arc::new(AtomicBool::new(false));
        self.active
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(id.to_string(), stop.clone());
        let (me, id) = (self.clone(), id.to_string());
        tokio::spawn(async move {
            let outcome = me.work(&id, &stop).await;
            if let Err((status, code)) = outcome {
                update(
                    &me.db,
                    &id,
                    &[("status", json!(status)), ("error_code", json!(code))],
                );
            }
            me.active
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .remove(&id);
            me.start_next();
        });
    }

    pub fn pause(&self, id: &str) -> Result<(), OfflineError> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let Some(row) = self.get(id) else {
            return refuse("Téléchargement introuvable.", "not_found");
        };
        let status = field(&row, "status");
        if (status == "queued" || status == "downloading")
            && let Some(stop) = self
                .active
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(id)
        {
            stop.store(true, Ordering::SeqCst);
            return Ok(());
        }
        if status == "queued" || status == "waiting" {
            update(&self.db, id, &[("status", json!("paused"))]);
        }
        Ok(())
    }

    pub fn resume(
        self: &Arc<Self>,
        id: &str,
        restart: bool,
        source: Option<&str>,
    ) -> Result<(), OfflineError> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let Some(mut row) = self.get(id) else {
            return refuse("Téléchargement introuvable.", "not_found");
        };
        let status = field(&row, "status").to_string();
        if status == "queued" || status == "downloading" || self.running(id) {
            return invalid("Téléchargement déjà en cours.");
        }
        if status == "completed" && self.available(&mut row) {
            return invalid("Fichier déjà disponible hors ligne.");
        }
        let part = managed(&row, "part_path")?;
        if restart {
            let _ = std::fs::remove_file(&part);
            let mut changes = vec![
                ("bytes_done", json!(0)),
                ("bytes_total", json!(0)),
                ("etag", json!("")),
                ("last_modified", json!("")),
                ("accept_ranges", json!(0)),
                ("error_code", json!("")),
                ("status", json!("queued")),
            ];
            if let Some(url) = source {
                if !reqwest::Url::parse(url).is_ok_and(|u| {
                    ["http", "https"].contains(&u.scheme())
                        && u.host_str().is_some_and(|h| !h.is_empty())
                }) {
                    return invalid("Lien vidéo invalide.");
                }
                changes.push(("source_url", json!(url)));
            }
            update(&self.db, id, &changes);
        } else {
            let size = std::fs::metadata(&part)
                .ok()
                .filter(|m| m.is_file())
                .map(|m| m.len())
                .unwrap_or(0);
            if number(&row, "bytes_done") != 0 && size == 0 {
                return refuse(
                    "Fichier partiel introuvable. Recommencez le téléchargement.",
                    "restart_required",
                );
            }
            if part.is_file() && size == 0 {
                let _ = std::fs::remove_file(&part);
            }
            let etag = field(&row, "etag");
            let validated = (!etag.is_empty() && !etag.starts_with("W/"))
                || !field(&row, "last_modified").is_empty();
            if size != 0
                && (number(&row, "accept_ranges") == 0
                    || number(&row, "bytes_total") == 0
                    || !validated)
            {
                return refuse(
                    "Reprise non vérifiable. Recommencez le téléchargement.",
                    "restart_required",
                );
            }
            update(
                &self.db,
                id,
                &[
                    ("bytes_done", json!(size)),
                    ("status", json!("queued")),
                    ("error_code", json!("")),
                ],
            );
        }
        self.launch(id);
        Ok(())
    }

    pub fn remove(&self, id: &str) -> Result<(), OfflineError> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let Some(row) = self.get(id) else {
            return refuse("Téléchargement introuvable.", "not_found");
        };
        let status = field(&row, "status");
        if status == "queued" || status == "downloading" || self.running(id) {
            return invalid("Mettez le téléchargement en pause avant de le retirer.");
        }
        for key in ["file_path", "part_path", "poster_path"] {
            let _ = std::fs::remove_file(managed(&row, key)?);
        }
        let _ = self
            .db
            .with(|c| c.execute("DELETE FROM offline_downloads WHERE id=?", [id]));
        Ok(())
    }

    /// The file of a completed download, or its poster.
    pub fn asset(&self, id: &str, kind: &str) -> Option<PathBuf> {
        let mut row = self.get(id)?;
        if !self.available(&mut row) {
            return None;
        }
        let path = managed(
            &row,
            if kind == "media" {
                "file_path"
            } else {
                "poster_path"
            },
        )
        .ok()?;
        (kind == "media" || path.is_file()).then_some(path)
    }

    pub fn subtitles(&self, id: &str) -> Option<Value> {
        let mut row = self.get(id)?;
        if !self.available(&mut row) {
            return None;
        }
        Some(
            serde_json::from_str(if field(&row, "subtitles_json").is_empty() {
                "{}"
            } else {
                field(&row, "subtitles_json")
            })
            .unwrap_or(json!({})),
        )
    }

    /// The link to download: a queued episode asks for its link now, so it has not expired.
    async fn source(&self, row: &Row) -> Result<String, (&'static str, &'static str)> {
        let source = field(row, "source_url").to_string();
        let Some(c) = EPISODE_SOURCE.captures(&source) else {
            return Ok(source);
        };
        let unavailable = ("failed", "source_unavailable");
        let (meta, links) = self.episodes.as_ref().ok_or(unavailable)?;
        let media: Value = serde_json::from_str(field(row, "media_json")).unwrap_or(json!({}));
        let ep = Episode {
            imdb_id: c[1].to_string(),
            season: c[2].parse().unwrap_or(1),
            episode: c[3].parse().unwrap_or(1),
            name: media["name"]
                .as_str()
                .filter(|n| !n.is_empty())
                .unwrap_or("Série")
                .to_string(),
            year: media["year"].as_str().unwrap_or("").to_string(),
            poster: String::new(),
        };
        let cfg = tokio::task::spawn_blocking(crate::config::load)
            .await
            .unwrap_or_default();
        let url = crate::play::resolve_episode(meta, links, &ep, "", &cfg, false)
            .await
            .map_err(|_| unavailable)?;
        reqwest::Url::parse(&url)
            .ok()
            .filter(|u| ["http", "https"].contains(&u.scheme()) && u.host_str().is_some())
            .map(|_| url)
            .ok_or(unavailable)
    }

    async fn work(&self, id: &str, stop: &AtomicBool) -> Result<(), (&'static str, &'static str)> {
        let row = self.get(id).ok_or(("failed", "unknown_error"))?;
        let failed = |code| ("failed", code);
        let part = managed(&row, "part_path").map_err(|e| failed(e.code))?;
        let final_path = managed(&row, "file_path").map_err(|e| failed(e.code))?;
        let offset = std::fs::metadata(&part)
            .ok()
            .filter(|m| m.is_file())
            .map(|m| m.len())
            .unwrap_or(0);
        if stop.load(Ordering::SeqCst) {
            update(
                &self.db,
                id,
                &[("status", json!("paused")), ("bytes_done", json!(offset))],
            );
            return Ok(());
        }
        let url = self.source(&row).await?;
        let mut request = self
            .client
            .get(&url)
            .header("User-Agent", BROWSER)
            .header("Accept", "application/json, text/plain, */*");
        if offset != 0 {
            let etag = field(&row, "etag");
            let validator = if !etag.is_empty() && !etag.starts_with("W/") {
                etag
            } else {
                field(&row, "last_modified")
            };
            request = request
                .header("Range", format!("bytes={offset}-"))
                .header("If-Range", validator);
        }
        let mut response = request
            .send()
            .await
            .map_err(|_| ("interrupted", "network_interrupted"))?;
        let status = response.status().as_u16();
        if status >= 400 {
            return Err(failed(if [401, 403, 404, 410].contains(&status) {
                "url_expired"
            } else {
                "network_interrupted"
            }));
        }
        if offset != 0 && status != 206 {
            return Err(failed("resume_unavailable"));
        }
        if offset == 0 && status != 200 {
            return Err(failed("invalid_response"));
        }
        let header = |name: &str| {
            response
                .headers()
                .get(name)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_string()
        };
        if header("content-type").to_lowercase().contains("text/html") {
            return Err(failed("url_expired"));
        }
        let (etag, last_modified) = (header("etag"), header("last-modified"));
        let length: u64 = header("content-length").trim().parse().unwrap_or(0);
        let total = if offset != 0 {
            let range = header("content-range");
            let c = CONTENT_RANGE
                .captures(range.trim())
                .ok_or(failed("resume_unavailable"))?;
            if c[1].parse::<u64>().ok() != Some(offset) || &c[3] == "*" {
                return Err(failed("resume_unavailable"));
            }
            let total: u64 = c[3].parse().unwrap_or(0);
            let (old_etag, old_modified) = (field(&row, "etag"), field(&row, "last_modified"));
            if total as i64 != number(&row, "bytes_total")
                || (!old_etag.is_empty() && !etag.is_empty() && etag != old_etag)
                || (!old_modified.is_empty()
                    && !last_modified.is_empty()
                    && last_modified != old_modified)
            {
                return Err(failed("restart_required"));
            }
            if length != 0 && length != total - offset {
                return Err(failed("resume_unavailable"));
            }
            total
        } else {
            length
        };
        let remaining = if total != 0 { total - offset } else { 0 };
        if remaining != 0
            && fs4::available_space(part.parent().unwrap_or(Path::new(".")))
                .unwrap_or(u64::MAX)
                .saturating_sub(remaining)
                < DISK_RESERVE
        {
            return Err(failed("disk_full"));
        }
        let ranges = if header("accept-ranges").eq_ignore_ascii_case("bytes") {
            1
        } else {
            number(&row, "accept_ranges")
        };
        update(
            &self.db,
            id,
            &[
                ("status", json!("downloading")),
                ("bytes_total", json!(total)),
                (
                    "etag",
                    json!(if etag.is_empty() {
                        field(&row, "etag").to_string()
                    } else {
                        etag.clone()
                    }),
                ),
                (
                    "last_modified",
                    json!(if last_modified.is_empty() {
                        field(&row, "last_modified").to_string()
                    } else {
                        last_modified.clone()
                    }),
                ),
                ("accept_ranges", json!(ranges)),
            ],
        );
        let file_error = |e: std::io::Error| {
            failed(if e.raw_os_error() == Some(28) {
                "disk_full"
            } else {
                "file_error"
            })
        };
        let mut options = std::fs::OpenOptions::new();
        if offset != 0 {
            options.append(true);
        } else {
            options.write(true).create_new(true);
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            // Never write through a link planted in place of the part file.
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let mut file = options.open(&part).map_err(file_error)?;
        let mut done = offset;
        let mut saved = Instant::now();
        while !stop.load(Ordering::SeqCst) {
            match response.chunk().await {
                Ok(Some(chunk)) => {
                    std::io::Write::write_all(&mut file, &chunk).map_err(file_error)?;
                    done += chunk.len() as u64;
                    if saved.elapsed() > Duration::from_millis(500) {
                        update(&self.db, id, &[("bytes_done", json!(done))]);
                        saved = Instant::now();
                    }
                }
                Ok(None) => break,
                Err(_) => {
                    let _ = file.sync_all();
                    update(&self.db, id, &[("bytes_done", json!(done))]);
                    return Err(("interrupted", "network_interrupted"));
                }
            }
        }
        file.sync_all().map_err(file_error)?;
        drop(file);
        if stop.load(Ordering::SeqCst) {
            update(
                &self.db,
                id,
                &[("status", json!("paused")), ("bytes_done", json!(done))],
            );
            return Ok(());
        }
        if done == 0 || (total != 0 && done != total) {
            return Err(failed("network_interrupted"));
        }
        std::fs::rename(&part, &final_path).map_err(file_error)?;
        self.fetch_poster(&row).await;
        update(
            &self.db,
            id,
            &[
                ("status", json!("completed")),
                ("bytes_done", json!(done)),
                ("bytes_total", json!(if total != 0 { total } else { done })),
                ("error_code", json!("")),
            ],
        );
        Ok(())
    }

    async fn fetch_poster(&self, row: &Row) {
        let url = field(row, "poster_url");
        let Ok(path) = managed(row, "poster_path") else {
            return;
        };
        if url.is_empty() || path.is_file() {
            return;
        }
        let Ok(r) = self
            .client
            .get(url)
            .header("User-Agent", BROWSER)
            .timeout(Duration::from_secs(8))
            .send()
            .await
        else {
            return;
        };
        let Ok(data) = r.bytes().await else { return };
        let image = data.starts_with(b"\xff\xd8")
            || data.starts_with(b"\x89PNG")
            || (data.starts_with(b"RIFF") && data.get(8..12) == Some(b"WEBP"));
        if data.len() > 4_000_000 || !image {
            return;
        }
        let temp = path.with_file_name(format!(
            "{}.part",
            path.file_name()
                .map(|n| n.to_string_lossy())
                .unwrap_or_default()
        ));
        if std::fs::write(&temp, &data).is_ok() {
            let _ = std::fs::rename(&temp, &path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_media_and_subtitles() {
        assert_eq!(
            safe_name("../../x/Le Film: 2.mkv").unwrap(),
            "Le Film_ 2.mkv"
        );
        assert!(safe_name("notes.txt").is_err());
        let media = media_snapshot(
            &json!({"id": "tt1", "name": "Show", "season": "2", "episode": 0, "extra": "x"}),
        );
        assert_eq!(
            Value::Object(media.clone()),
            json!({"id": "tt1", "name": "Show", "season": 2, "episode": null})
        );
        assert_eq!(fingerprint(&media, "A.mkv").len(), 64);
        assert_eq!(subtitle_snapshot(&json!({"lang": "off"})), "{}");
        let kept: Value = serde_json::from_str(&subtitle_snapshot(&json!({"lang": "fr", "cues": [{"start": 1, "end": 2, "text": "Bonjour"}, {"start": 3, "end": 2}]}))).unwrap();
        assert_eq!(
            kept["cues"].as_array().unwrap().len(),
            1,
            "an impossible cue is left out"
        );
    }
}
