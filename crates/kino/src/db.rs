//! KINO's SQLite database (`~/.kino_data.db`): the same tables as the Python
//! version (schema.sql is what it creates), so the two share one file.

use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, MutexGuard, TryLockError};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::Result;
use rusqlite::{Connection, OpenFlags, OptionalExtension, params};
use serde_json::Value;

const SCHEMA: &str = include_str!("schema.sql");

/// One connection writes; a few read-only ones answer reads meanwhile (WAL
/// lets readers and the writer work at the same time).
pub struct Db {
    conn: Mutex<Connection>,
    readers: Vec<Mutex<Connection>>,
    next_reader: AtomicUsize,
    /// When expired cache rows were last removed.
    last_purge: Mutex<f64>,
}

/// Expired cache rows go every six hours: they were only removed when their exact
/// key was read again, so every past search stayed in the database.
const PURGE_EVERY: f64 = 6.0 * 3600.0;

/// Read-only connections beside the writer.
const READERS: usize = 4;

/// Applied to every connection.
const PRAGMAS: &str = "PRAGMA synchronous = NORMAL;
     PRAGMA cache_size = -32000;
     PRAGMA temp_store = MEMORY;
     PRAGMA busy_timeout = 10000;";

fn reader(path: &Path) -> rusqlite::Result<Connection> {
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.execute_batch(PRAGMAS)?;
    Ok(conn)
}

fn locked(mutex: &Mutex<Connection>) -> MutexGuard<'_, Connection> {
    mutex.lock().unwrap_or_else(|e| e.into_inner())
}

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

impl Db {
    pub fn open(path: &Path) -> Result<Self> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode = WAL;")?;
        conn.execute_batch(PRAGMAS)?;
        conn.execute_batch(SCHEMA)?;
        // Opened once the tables exist; without them, reads go to the writer.
        let readers = (0..READERS)
            .map_while(|_| reader(path).ok())
            .map(Mutex::new)
            .collect();
        Ok(Self {
            conn: Mutex::new(conn),
            readers,
            next_reader: AtomicUsize::new(0),
            last_purge: Mutex::new(0.0),
        })
    }

    /// Runs `f` on the writing connection (also for reads that must follow a write).
    pub fn with<T>(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T> {
        Ok(f(&locked(&self.conn))?)
    }

    /// Runs `f` on a read-only connection: a free one, else the next in turn.
    /// It sees every committed write; a statement that writes fails there.
    pub fn read<T>(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T> {
        let count = self.readers.len();
        if count == 0 {
            return self.with(f);
        }
        let start = self.next_reader.fetch_add(1, Ordering::Relaxed);
        for i in 0..count {
            match self.readers[(start + i) % count].try_lock() {
                Ok(conn) => return Ok(f(&conn)?),
                Err(TryLockError::Poisoned(p)) => return Ok(f(&p.into_inner())?),
                Err(TryLockError::WouldBlock) => {}
            }
        }
        Ok(f(&locked(&self.readers[start % count]))?)
    }

    /// A value of `app_config`, or None when missing or unreadable.
    pub fn get_config(&self, key: &str) -> Option<Value> {
        let raw: Option<String> = self
            .read(|c| {
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
            .read(|c| {
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
    fn reads_go_on_while_a_write_is_under_way() {
        let dir = tempfile::tempdir().unwrap();
        let db = std::sync::Arc::new(Db::open(&dir.path().join("kino.db")).unwrap());
        assert_eq!(db.readers.len(), READERS);
        db.save_config("seen", &json!(1)).unwrap();
        assert_eq!(
            db.get_config("seen"),
            Some(json!(1)),
            "a read sees the write"
        );
        let writer = db.clone();
        let (started, wait) = std::sync::mpsc::channel();
        let long = std::thread::spawn(move || {
            writer
                .with(|c| {
                    c.execute_batch("BEGIN IMMEDIATE")?;
                    c.execute(
                        "INSERT INTO app_config (key, value_json) VALUES ('later', '2')",
                        [],
                    )?;
                    started.send(()).unwrap();
                    std::thread::sleep(std::time::Duration::from_millis(400));
                    c.execute_batch("COMMIT")
                })
                .unwrap();
        });
        wait.recv().unwrap();
        let at = std::time::Instant::now();
        assert_eq!(db.get_config("seen"), Some(json!(1)));
        assert_eq!(db.get_config("later"), None, "not committed yet");
        assert!(
            at.elapsed() < std::time::Duration::from_millis(200),
            "reads did not wait for the writer: {:?}",
            at.elapsed()
        );
        long.join().unwrap();
        assert_eq!(
            db.get_config("later"),
            Some(json!(2)),
            "committed, then seen"
        );
        let refused = db.read(|c| c.execute("DELETE FROM app_config", []));
        assert!(refused.is_err(), "a reader cannot write");
    }

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
