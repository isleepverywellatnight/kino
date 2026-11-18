//! KINO's SQLite database (`~/.kino_data.db`): the same tables as the Python
//! version (schema.sql is what it creates), so the two share one file.

use std::cell::Cell;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, MutexGuard, TryLockError};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::Result;
use rusqlite::{Connection, OpenFlags, OptionalExtension, params};
use serde_json::Value;

const SCHEMA: &str = include_str!("schema.sql");

/// The version of the tables, kept in `PRAGMA user_version`. A database from
/// before versions were counted reads 0: it has schema.sql's tables already.
pub const SCHEMA_VERSION: i64 = 1;

/// The steps between versions: the step at index `i` takes version `i` to
/// `i + 1`, in one transaction with the new version number.
const MIGRATIONS: [&str; SCHEMA_VERSION as usize] = [
    // 0 → 1: nothing changes; versions start being counted.
    "",
];

/// Brings the database up to `SCHEMA_VERSION`. A database already at a later
/// version (made by a newer KINO) is left as it is.
fn migrate(conn: &mut Connection) -> Result<()> {
    let found: i64 = conn.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    for (from, step) in MIGRATIONS.iter().enumerate().skip(found.max(0) as usize) {
        let tx = conn.transaction()?;
        tx.execute_batch(step)?;
        tx.pragma_update(None, "user_version", from as i64 + 1)?;
        tx.commit()?;
    }
    Ok(())
}

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

thread_local! {
    /// Set on threads started by `spawn_blocking` below: waiting there is expected.
    static BLOCKING: Cell<bool> = const { Cell::new(false) };
    /// Set while a slow access is being written to the error log, which may
    /// itself read the settings.
    static REPORTING: Cell<bool> = const { Cell::new(false) };
}

/// A database access longer than this, on a thread that runs async tasks,
/// holds up every request waiting on that thread: it goes to the error log.
const SLOW: Duration = Duration::from_millis(25);

/// `tokio::task::spawn_blocking`, marking the thread as one where waiting on
/// SQLite is expected. KINO's code uses this one, so that the slow accesses the
/// error log reports are only those made from async tasks.
pub fn spawn_blocking<F, T>(work: F) -> tokio::task::JoinHandle<T>
where
    F: FnOnce() -> T + Send + 'static,
    T: Send + 'static,
{
    tokio::task::spawn_blocking(move || blocking(work))
}

/// Runs `work` with the current thread marked as one where waiting on SQLite
/// is expected: for threads a runtime handle starts with its own
/// `spawn_blocking` (the desktop app's).
pub fn blocking<T>(work: impl FnOnce() -> T) -> T {
    struct Unmark(bool);
    impl Drop for Unmark {
        fn drop(&mut self) {
            BLOCKING.set(self.0);
        }
    }
    let _unmark = Unmark(BLOCKING.replace(true));
    work()
}

/// Whether the current thread runs async tasks (a Tokio worker, or a thread
/// inside `block_on`), rather than a blocking or plain thread.
fn on_async_thread() -> bool {
    tokio::runtime::Handle::try_current().is_ok() && !BLOCKING.get()
}

/// Runs `access` and reports it when it was slow on an async thread, with the
/// place in KINO's code that asked for it.
#[track_caller]
fn timed<T>(access: impl FnOnce() -> T) -> T {
    let caller = std::panic::Location::caller();
    let start = Instant::now();
    let out = access();
    let took = start.elapsed();
    if took >= SLOW && on_async_thread() && !REPORTING.get() {
        REPORTING.set(true);
        crate::errorlog::LOG.record(
            &format!("{}:{}", caller.file(), caller.line()),
            "Base lente sur un thread async",
            &format!("{} ms", took.as_millis()),
            &[],
        );
        REPORTING.set(false);
    }
    out
}

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

impl Db {
    pub fn open(path: &Path) -> Result<Self> {
        let mut conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode = WAL;")?;
        conn.execute_batch(PRAGMAS)?;
        conn.execute_batch(SCHEMA)?;
        migrate(&mut conn)?;
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
    #[track_caller]
    pub fn with<T>(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T> {
        timed(|| Ok(f(&locked(&self.conn))?))
    }

    /// Runs `f` on a read-only connection: a free one, else the next in turn.
    /// It sees every committed write; a statement that writes fails there.
    #[track_caller]
    pub fn read<T>(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T> {
        let count = self.readers.len();
        if count == 0 {
            return self.with(f);
        }
        timed(|| {
            let start = self.next_reader.fetch_add(1, Ordering::Relaxed);
            for i in 0..count {
                match self.readers[(start + i) % count].try_lock() {
                    Ok(conn) => return Ok(f(&conn)?),
                    Err(TryLockError::Poisoned(p)) => return Ok(f(&p.into_inner())?),
                    Err(TryLockError::WouldBlock) => {}
                }
            }
            Ok(f(&locked(&self.readers[start % count]))?)
        })
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

    fn user_version(path: &Path) -> i64 {
        Connection::open(path)
            .unwrap()
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap()
    }

    #[test]
    fn a_new_database_is_at_the_current_schema_version() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("kino.db");
        Db::open(&path).unwrap();
        assert_eq!(user_version(&path), SCHEMA_VERSION);
    }

    #[test]
    fn an_uncounted_database_keeps_its_rows_and_gets_a_version() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("kino.db");
        {
            let old = Connection::open(&path).unwrap();
            old.execute_batch(SCHEMA).unwrap();
            old.execute(
                "INSERT INTO watchlist (id, name, added_at) VALUES ('tt0099685', 'Les Affranchis', 1.0)",
                [],
            )
            .unwrap();
        }
        assert_eq!(user_version(&path), 0);
        let db = Db::open(&path).unwrap();
        assert_eq!(user_version(&path), SCHEMA_VERSION);
        let name: String = db
            .read(|c| {
                c.query_row(
                    "SELECT name FROM watchlist WHERE id = 'tt0099685'",
                    [],
                    |r| r.get(0),
                )
            })
            .unwrap();
        assert_eq!(name, "Les Affranchis");
        drop(db);
        Db::open(&path).unwrap();
        assert_eq!(
            user_version(&path),
            SCHEMA_VERSION,
            "opening again changes nothing"
        );
    }

    #[test]
    fn a_database_from_a_newer_kino_opens_unchanged() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("kino.db");
        Db::open(&path).unwrap();
        Connection::open(&path)
            .unwrap()
            .pragma_update(None, "user_version", SCHEMA_VERSION + 5)
            .unwrap();
        Db::open(&path).unwrap();
        assert_eq!(user_version(&path), SCHEMA_VERSION + 5);
    }

    #[test]
    fn only_async_threads_count_as_async() {
        assert!(!on_async_thread(), "a plain thread");
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .build()
            .unwrap();
        runtime.block_on(async {
            assert!(on_async_thread(), "inside the runtime");
            assert!(
                !spawn_blocking(on_async_thread).await.unwrap(),
                "KINO's blocking threads"
            );
            assert!(
                tokio::spawn(async { on_async_thread() }).await.unwrap(),
                "a worker"
            );
            assert!(blocking(|| !on_async_thread()), "a marked stretch");
            assert!(on_async_thread(), "the mark ends with it");
        });
    }

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
