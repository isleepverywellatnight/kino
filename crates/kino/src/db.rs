//! KINO's SQLite database (`~/.kino_data.db`): the same tables as the Python
//! version (schema.sql is what it creates), so the two share one file.

use std::path::Path;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::Result;
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::Value;

const SCHEMA: &str = include_str!("schema.sql");

pub struct Db {
    conn: Mutex<Connection>,
    /// When expired cache rows were last removed.
    last_purge: Mutex<f64>,
}

/// Expired cache rows go every six hours: they were only removed when their exact
/// key was read again, so every past search stayed in the database.
const PURGE_EVERY: f64 = 6.0 * 3600.0;

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

impl Db {
    pub fn open(path: &Path) -> Result<Self> {
        let conn = Connection::open(path)?;
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA cache_size = -32000;
             PRAGMA temp_store = MEMORY;
             PRAGMA busy_timeout = 10000;",
        )?;
        conn.execute_batch(SCHEMA)?;
        Ok(Self {
            conn: Mutex::new(conn),
            last_purge: Mutex::new(0.0),
        })
    }

    pub fn with<T>(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T> {
        let conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        Ok(f(&conn)?)
    }

    /// A value of `app_config`, or None when missing or unreadable.
    pub fn get_config(&self, key: &str) -> Option<Value> {
        let raw: Option<String> = self
            .with(|c| {
                c.query_row(
                    "SELECT value_json FROM app_config WHERE key = ?",
                    [key],
                    |r| r.get(0),
                )
                .optional()
                .map(Option::flatten)
            })
            .ok()
            .flatten();
        raw.and_then(|s| serde_json::from_str(&s).ok())
    }

    pub fn save_config(&self, key: &str, value: &Value) -> Result<()> {
        let json = serde_json::to_string(value)?;
        self.with(|c| {
            c.execute(
                "INSERT INTO app_config (key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) \
                 ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = CURRENT_TIMESTAMP",
                params![key, json],
            )
        })?;
        Ok(())
    }

    pub fn delete_config(&self, key: &str) -> Result<()> {
        self.with(|c| c.execute("DELETE FROM app_config WHERE key = ?", [key]))?;
        Ok(())
    }

    /// A cached value that has not expired.
    pub fn cache_get(&self, key: &str) -> Option<Value> {
        let row: Option<(Option<String>, Option<f64>)> = self
            .with(|c| {
                c.query_row(
                    "SELECT value_json, expires_at FROM persistent_cache WHERE cache_key = ?",
                    [key],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()
            })
            .ok()
            .flatten();
        let (json, expires) = row?;
        if expires.is_some_and(|e| e < now()) {
            let _ =
                self.with(|c| c.execute("DELETE FROM persistent_cache WHERE cache_key = ?", [key]));
            return None;
        }
        json.and_then(|s| serde_json::from_str(&s).ok())
    }

    /// Removes the expired cache rows; with `compact`, gives back the space when a
    /// quarter of the file is free. Returns how many rows went.
    pub fn purge_expired(&self, compact: bool) -> usize {
        let at = now();
        *self.last_purge.lock().unwrap_or_else(|e| e.into_inner()) = at;
        self.with(|c| {
            let removed = c.execute(
                "DELETE FROM persistent_cache WHERE expires_at IS NOT NULL AND expires_at < ?",
                [at],
            )?;
            if compact && removed > 0 {
                let pages: f64 = c.query_row("PRAGMA page_count", [], |r| r.get(0))?;
                let free: f64 = c.query_row("PRAGMA freelist_count", [], |r| r.get(0))?;
                if pages > 0.0 && free / pages > 0.25 {
                    c.execute_batch("VACUUM")?;
                }
            }
            Ok(removed)
        })
        .unwrap_or(0)
    }

    pub fn cache_set(&self, key: &str, value: &Value, ttl_secs: f64) -> Result<()> {
        if now() - *self.last_purge.lock().unwrap_or_else(|e| e.into_inner()) > PURGE_EVERY {
            self.purge_expired(false);
        }
        let json = serde_json::to_string(value)?;
        let expires = (ttl_secs > 0.0).then(|| now() + ttl_secs);
        self.with(|c| {
            c.execute(
                "INSERT INTO persistent_cache (cache_key, value_json, expires_at) VALUES (?, ?, ?) \
                 ON CONFLICT(cache_key) DO UPDATE SET value_json = excluded.value_json, expires_at = excluded.expires_at",
                params![key, json, expires],
            )
        })?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn config_and_cache_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let db = Db::open(&dir.path().join("kino.db")).unwrap();
        assert_eq!(db.get_config("missing"), None);
        db.save_config("manga_folder_v1", &json!("/tmp/Mangas"))
            .unwrap();
        db.save_config("manga_folder_v1", &json!("/tmp/Other"))
            .unwrap();
        assert_eq!(db.get_config("manga_folder_v1"), Some(json!("/tmp/Other")));
        db.cache_set("k", &json!({"a": [1, "é"]}), 60.0).unwrap();
        assert_eq!(db.cache_get("k"), Some(json!({"a": [1, "é"]})));
        db.cache_set("old", &json!(1), 0.0).unwrap();
        assert_eq!(
            db.cache_get("old"),
            Some(json!(1)),
            "no expiry when the ttl is 0"
        );
        db.with(|c| {
            c.execute(
                "UPDATE persistent_cache SET expires_at = 1 WHERE cache_key = 'k'",
                [],
            )
        })
        .unwrap();
        assert_eq!(db.cache_get("k"), None, "expired");
    }

    #[test]
    fn opens_a_database_the_python_version_made_and_creates_every_table() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("kino.db");
        Db::open(&path).unwrap();
        let db = Db::open(&path).unwrap();
        let tables: Vec<String> = db
            .with(|c| {
                let mut s = c.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '%fts_%' ORDER BY name")?;
                let names: Vec<String> = s.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
                Ok(names)
            })
            .unwrap();
        for t in [
            "app_config",
            "persistent_cache",
            "watchlist",
            "watch_history",
            "offline_downloads",
            "tonight_feedback",
            "media_catalog",
            "media_aliases",
        ] {
            assert!(tables.contains(&t.to_string()), "{t} in {tables:?}");
        }
    }
}
