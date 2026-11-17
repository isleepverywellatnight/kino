//! A copy a day of what the user made in KINO, the 7 newest kept, and
//! « Restaurer ». A copy holds Ma Liste, the history, « Ce soir » and « Pas
//! pour moi » choices, KINO's settings in the database and the settings file;
//! never the caches (KINO finds them again) nor a key. Restoring first copies
//! the present state, then puts back the user's tables and settings; the
//! caches, the keys and what belongs to this computer stay as they are.

use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};

use regex::Regex;
use rusqlite::{Connection, OpenFlags, params_from_iter};
use serde_json::{Map, Value, json};

pub const KEEP_DAILY: usize = 7;
const KEEP_BEFORE_RESTORE: usize = 2;
pub const DATA_SCHEMA: i64 = 1;
const USER_TABLES: [&str; 5] = [
    "watchlist",
    "watch_history",
    "app_config",
    "tonight_feedback",
    "taste_feedback",
];
const CACHE_TABLES: [&str; 3] = ["persistent_cache", "taste_pool", "playback_source_feedback"];
/// Settings that stay those of this computer when restoring.
const THIS_COMPUTER: [&str; 4] = [
    "window_bounds",
    "download_dir",
    "tv_device",
    "debrid_provider",
];
static SECRET: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)token|key|secret|password|auth").unwrap());
static DAILY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{4}-\d{2}-\d{2}$").unwrap());
static BEFORE_RESTORE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^avant-restauration-\d{4}-\d{2}-\d{2}-\d{6}$").unwrap());
/// One copy at a time.
static BUSY: Mutex<()> = Mutex::new(());

/// KINO's version (the Cargo workspace's).
pub fn app_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

pub fn default_dir() -> PathBuf {
    let home = crate::paths::home();
    if cfg!(target_os = "macos") {
        home.join("Library")
            .join("Application Support")
            .join("KINO")
            .join("Backups")
    } else if cfg!(windows) {
        std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or(home)
            .join("KINO")
            .join("Backups")
    } else {
        home.join(".local")
            .join("share")
            .join("kino")
            .join("backups")
    }
}

fn is_secret(name: &str) -> bool {
    SECRET.is_match(name)
}

/// The saved settings, with this computer's keys and own settings kept.
pub fn restored_settings(
    saved: &Map<String, Value>,
    current: &Map<String, Value>,
) -> Map<String, Value> {
    let mut out: Map<String, Value> = saved
        .iter()
        .filter(|(k, _)| !is_secret(k) && !THIS_COMPUTER.contains(&k.as_str()))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    out.extend(
        current
            .iter()
            .filter(|(k, _)| is_secret(k) || THIS_COMPUTER.contains(&k.as_str()))
            .map(|(k, v)| (k.clone(), v.clone())),
    );
    out
}

fn private(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = if path.is_dir() { 0o700 } else { 0o600 };
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode));
    }
    #[cfg(not(unix))]
    let _ = path;
}

fn tables(c: &Connection) -> rusqlite::Result<Vec<String>> {
    let mut s = c.prepare("SELECT name FROM sqlite_master WHERE type = 'table'")?;
    s.query_map([], |r| r.get(0))?.collect()
}

fn columns(c: &Connection, table: &str) -> rusqlite::Result<Vec<String>> {
    let mut s = c.prepare(&format!("PRAGMA table_info(\"{table}\")"))?;
    s.query_map([], |r| r.get(1))?.collect()
}

/// How many titles: in Ma Liste, seen, in progress, rated.
fn counts(c: &Connection) -> rusqlite::Result<Value> {
    let present = tables(c)?;
    let mut found = json!({"watchlist": 0, "seen": 0, "in_progress": 0, "rated": 0});
    if present.iter().any(|t| t == "watchlist") {
        found["watchlist"] =
            json!(c.query_row("SELECT COUNT(*) FROM watchlist", [], |r| r.get::<_, i64>(0))?);
    }
    if present.iter().any(|t| t == "watch_history") {
        let mut s = c.prepare("SELECT data_json FROM watch_history")?;
        let rows: Vec<Option<String>> = s
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        let truthy = |v: &Value| {
            !matches!(v, Value::Null | Value::Bool(false)) && v != &json!(0) && v != &json!("")
        };
        for raw in rows {
            let Ok(entry) = serde_json::from_str::<Value>(raw.as_deref().unwrap_or("{}")) else {
                continue;
            };
            let slot = if truthy(&entry["completed"]) || truthy(&entry["imported_watched"]) {
                "seen"
            } else {
                "in_progress"
            };
            found[slot] = json!(found[slot].as_i64().unwrap_or(0) + 1);
            let rating = match &entry["user_rating"] {
                Value::String(s) => s.trim().to_string(),
                Value::Null | Value::Bool(false) => String::new(),
                other => other.to_string(),
            };
            if !rating.is_empty() && rating != "0" {
                found["rated"] = json!(found["rated"].as_i64().unwrap_or(0) + 1);
            }
        }
    }
    Ok(found)
}

#[derive(Debug)]
pub enum BackupError {
    /// Shown as is to the user.
    Refused(String),
    Failed(String),
}

impl<E: std::fmt::Display> From<E> for BackupError
where
    E: std::error::Error,
{
    fn from(e: E) -> Self {
        BackupError::Failed(e.to_string())
    }
}

pub struct Backups {
    pub directory: PathBuf,
    pub db_path: PathBuf,
    pub config_path: PathBuf,
}

impl Backups {
    pub fn standard() -> Self {
        Self {
            directory: default_dir(),
            db_path: crate::paths::database(),
            config_path: crate::paths::config_file(),
        }
    }

    fn day(at: chrono::DateTime<chrono::Local>) -> String {
        at.format("%Y-%m-%d").to_string()
    }

    /// One copy of the present data; today's copy is replaced.
    pub fn make(&self, before_restore: bool) -> Result<Value, BackupError> {
        let _busy = BUSY.lock().unwrap_or_else(|e| e.into_inner());
        let now = chrono::Local::now();
        let name = if before_restore {
            format!(
                "avant-restauration-{}-{}",
                Self::day(now),
                now.format("%H%M%S")
            )
        } else {
            Self::day(now)
        };
        std::fs::create_dir_all(&self.directory)?;
        private(&self.directory);
        let work = self
            .directory
            .join(format!(".work-{}", hex::encode(rand::random::<[u8; 6]>())));
        std::fs::create_dir(&work)?;
        private(&work);
        let result = (|| -> Result<PathBuf, BackupError> {
            let found = self.copy_database(&work.join("kino_data.db"))?;
            let settings: Map<String, Value> = std::fs::read_to_string(&self.config_path)
                .ok()
                .and_then(|t| serde_json::from_str(&t).ok())
                .unwrap_or_default();
            let shareable: Map<String, Value> = settings
                .into_iter()
                .filter(|(k, _)| !is_secret(k))
                .collect();
            std::fs::write(
                work.join("config.json"),
                serde_json::to_string_pretty(&shareable)?,
            )?;
            private(&work.join("config.json"));
            let manifest = json!({"created_at": crate::db::now(), "kind": if before_restore { "before_restore" } else { "daily" },
                                  "version": app_version(), "data_schema": DATA_SCHEMA, "counts": found});
            std::fs::write(
                work.join("backup.json"),
                serde_json::to_string_pretty(&manifest)?,
            )?;
            let target = self.directory.join(&name);
            if target.exists() {
                std::fs::remove_dir_all(&target)?;
            }
            std::fs::rename(&work, &target)?;
            Ok(target)
        })();
        let target = match result {
            Ok(t) => t,
            Err(e) => {
                let _ = std::fs::remove_dir_all(&work);
                return Err(e);
            }
        };
        self.prune();
        self.entry(&target)
            .ok_or_else(|| BackupError::Failed("copie illisible".into()))
    }

    fn copy_database(&self, destination: &Path) -> Result<Value, BackupError> {
        if !self.db_path.exists() {
            return Err(BackupError::Failed(
                "Aucune donnée KINO à sauvegarder.".into(),
            ));
        }
        {
            let source =
                Connection::open_with_flags(&self.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
            source.busy_timeout(std::time::Duration::from_secs(15))?;
            source.execute("VACUUM INTO ?1", [destination.to_string_lossy()])?;
        }
        let copy = Connection::open(destination)?;
        let present = tables(&copy)?;
        for table in CACHE_TABLES
            .iter()
            .filter(|t| present.iter().any(|p| p == *t))
        {
            copy.execute(&format!("DELETE FROM \"{table}\""), [])?;
        }
        copy.execute_batch("VACUUM")?;
        let check: String = copy.query_row("PRAGMA quick_check", [], |r| r.get(0))?;
        if check != "ok" {
            return Err(BackupError::Failed(
                "La copie de la base est abîmée.".into(),
            ));
        }
        let found = counts(&copy)?;
        drop(copy);
        private(destination);
        Ok(found)
    }

    fn entry(&self, folder: &Path) -> Option<Value> {
        let manifest: Value =
            serde_json::from_str(&std::fs::read_to_string(folder.join("backup.json")).ok()?)
                .ok()?;
        if !folder.join("kino_data.db").is_file() {
            return None;
        }
        let size: u64 = std::fs::read_dir(folder)
            .ok()?
            .filter_map(Result::ok)
            .filter_map(|e| e.metadata().ok())
            .filter(|m| m.is_file())
            .map(|m| m.len())
            .sum();
        let text = |v: &Value| match v {
            Value::String(s) => s.clone(),
            Value::Null | Value::Bool(false) => String::new(),
            other => other.to_string(),
        };
        Some(json!({
            "id": folder.file_name()?.to_string_lossy(), "created_at": manifest["created_at"].as_f64().unwrap_or(0.0),
            "kind": manifest["kind"].as_str().filter(|k| !k.is_empty()).unwrap_or("daily"), "version": text(&manifest["version"]),
            "counts": if manifest["counts"].is_object() { manifest["counts"].clone() } else { json!({}) }, "bytes": size,
            "restorable": manifest["data_schema"] == DATA_SCHEMA,
        }))
    }

    fn folders(&self) -> Vec<PathBuf> {
        std::fs::read_dir(&self.directory)
            .map(|d| {
                d.filter_map(Result::ok)
                    .map(|e| e.path())
                    .filter(|p| {
                        p.is_dir()
                            && p.file_name()
                                .and_then(|n| n.to_str())
                                .is_some_and(|n| DAILY.is_match(n) || BEFORE_RESTORE.is_match(n))
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// The copies, newest first.
    pub fn list(&self) -> Vec<Value> {
        let mut entries: Vec<Value> = self
            .folders()
            .iter()
            .filter_map(|f| self.entry(f))
            .collect();
        entries.sort_by(|a, b| {
            b["created_at"]
                .as_f64()
                .unwrap_or(0.0)
                .total_cmp(&a["created_at"].as_f64().unwrap_or(0.0))
        });
        entries
    }

    /// The 7 newest daily copies and the 2 newest « Avant restauration » stay.
    pub fn prune(&self) {
        let entries = self.list();
        for (kind, keep) in [
            ("daily", KEEP_DAILY),
            ("before_restore", KEEP_BEFORE_RESTORE),
        ] {
            for entry in entries.iter().filter(|e| e["kind"] == kind).skip(keep) {
                let _ = std::fs::remove_dir_all(
                    self.directory.join(entry["id"].as_str().unwrap_or("_")),
                );
            }
        }
        let now = std::time::SystemTime::now();
        for work in std::fs::read_dir(&self.directory)
            .into_iter()
            .flatten()
            .filter_map(Result::ok)
        {
            let old = work
                .metadata()
                .and_then(|m| m.modified())
                .ok()
                .and_then(|m| now.duration_since(m).ok())
                .is_some_and(|age| age.as_secs() > 3600);
            if work.file_name().to_string_lossy().starts_with(".work-") && old {
                let _ = std::fs::remove_dir_all(work.path());
            }
        }
    }

    /// Whether today's copy is still missing.
    pub fn due(&self) -> bool {
        !self
            .directory
            .join(Self::day(chrono::Local::now()))
            .join("backup.json")
            .is_file()
    }

    /// Puts back the copy `id`, after copying the present state.
    pub fn restore(&self, id: &str) -> Result<Value, BackupError> {
        if !DAILY.is_match(id) && !BEFORE_RESTORE.is_match(id) {
            return Err(BackupError::Refused("Sauvegarde inconnue.".into()));
        }
        let folder = self.directory.join(id);
        let entry = self
            .entry(&folder)
            .ok_or_else(|| BackupError::Refused("Sauvegarde introuvable.".into()))?;
        if entry["restorable"] != true {
            return Err(BackupError::Refused(
                "Cette sauvegarde vient d'une version de KINO trop différente.".into(),
            ));
        }
        let mut rows: Vec<(String, Vec<String>, Vec<Vec<rusqlite::types::Value>>)> = Vec::new();
        let before;
        {
            let saved = Connection::open_with_flags(
                folder.join("kino_data.db"),
                OpenFlags::SQLITE_OPEN_READ_ONLY,
            )?;
            let check: String = saved.query_row("PRAGMA quick_check", [], |r| r.get(0))?;
            if check != "ok" {
                return Err(BackupError::Refused("Cette sauvegarde est abîmée.".into()));
            }
            before = self.make(true)?;
            let present = tables(&saved)?;
            for table in USER_TABLES
                .iter()
                .filter(|t| present.iter().any(|p| p == *t))
            {
                let cols = columns(&saved, table)?;
                let names = cols
                    .iter()
                    .map(|c| format!("\"{c}\""))
                    .collect::<Vec<_>>()
                    .join(", ");
                let mut s = saved.prepare(&format!("SELECT {names} FROM \"{table}\""))?;
                let n = cols.len();
                let values = s
                    .query_map([], |r| {
                        (0..n)
                            .map(|i| r.get::<_, rusqlite::types::Value>(i))
                            .collect()
                    })?
                    .collect::<rusqlite::Result<_>>()?;
                rows.push((table.to_string(), cols, values));
            }
        }
        let mut live = Connection::open(&self.db_path)?;
        live.busy_timeout(std::time::Duration::from_secs(10))?;
        let present = tables(&live)?;
        let tx = live.transaction()?;
        for (table, cols, values) in &rows {
            if !present.contains(table) {
                continue;
            }
            let live_cols = columns(&tx, table)?;
            let shared: Vec<(usize, &String)> = cols
                .iter()
                .enumerate()
                .filter(|(_, c)| live_cols.contains(c))
                .collect();
            tx.execute(&format!("DELETE FROM \"{table}\""), [])?;
            let names = shared
                .iter()
                .map(|(_, c)| format!("\"{c}\""))
                .collect::<Vec<_>>()
                .join(", ");
            let marks = vec!["?"; shared.len()].join(", ");
            let mut insert = tx.prepare(&format!(
                "INSERT OR REPLACE INTO \"{table}\" ({names}) VALUES ({marks})"
            ))?;
            for row in values {
                insert.execute(params_from_iter(shared.iter().map(|(i, _)| &row[*i])))?;
            }
        }
        tx.commit()?;
        self.restore_settings(&folder.join("config.json"));
        Ok(json!({"restored": entry, "before": before}))
    }

    fn restore_settings(&self, saved: &Path) {
        let Some(saved) = std::fs::read_to_string(saved)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        else {
            return;
        };
        let current: Map<String, Value> = std::fs::read_to_string(&self.config_path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_default();
        let merged = restored_settings(saved.as_object().unwrap_or(&Map::new()), &current);
        let temporary = self.config_path.with_file_name(format!(
            "{}.restoring",
            self.config_path
                .file_name()
                .map(|n| n.to_string_lossy())
                .unwrap_or_default()
        ));
        if std::fs::write(
            &temporary,
            serde_json::to_string_pretty(&Value::Object(merged)).unwrap_or_default(),
        )
        .is_ok()
        {
            private(&temporary);
            let _ = std::fs::rename(&temporary, &self.config_path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copy_list_and_restore_keep_keys_out() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("kino.db");
        let db = crate::db::Db::open(&db_path).unwrap();
        db.with(|c| {
            c.execute("INSERT INTO watchlist (id, name, type, year, poster, imdb_rating, added_at, user_rating) VALUES ('tt1', 'A', 'movie', '', '', '', 1, '')", [])?;
            c.execute("INSERT INTO persistent_cache (cache_key, value_json, expires_at) VALUES ('k', '1', NULL)", [])
        })
        .unwrap();
        let config = dir.path().join("config.json");
        std::fs::write(
            &config,
            r#"{"rd_token": "SECRET", "pref_lang": "vo", "download_dir": "/here"}"#,
        )
        .unwrap();
        let backups = Backups {
            directory: dir.path().join("Backups"),
            db_path: db_path.clone(),
            config_path: config.clone(),
        };
        assert!(backups.due());
        let made = backups.make(false).unwrap();
        assert_eq!(made["counts"]["watchlist"], 1);
        assert!(!backups.due());
        let folder = backups.directory.join(made["id"].as_str().unwrap());
        assert!(
            !std::fs::read_to_string(folder.join("config.json"))
                .unwrap()
                .contains("SECRET"),
            "no key in a copy"
        );
        let copy = Connection::open(folder.join("kino_data.db")).unwrap();
        assert_eq!(
            copy.query_row("SELECT COUNT(*) FROM persistent_cache", [], |r| r
                .get::<_, i64>(0))
                .unwrap(),
            0,
            "caches emptied"
        );
        db.with(|c| c.execute("DELETE FROM watchlist", [])).unwrap();
        std::fs::write(
            &config,
            r#"{"rd_token": "NEW", "pref_lang": "vf", "download_dir": "/there"}"#,
        )
        .unwrap();
        let restored = backups.restore(made["id"].as_str().unwrap()).unwrap();
        assert_eq!(restored["before"]["kind"], "before_restore");
        assert_eq!(crate::library::watchlist(&db).len(), 1);
        let settings: Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(
            settings,
            json!({"pref_lang": "vo", "rd_token": "NEW", "download_dir": "/there"}),
            "keys and this computer's settings stay"
        );
        assert!(matches!(
            backups.restore("../x"),
            Err(BackupError::Refused(_))
        ));
        assert_eq!(backups.list().len(), 2);
        assert!(!app_version().is_empty());
    }
}
