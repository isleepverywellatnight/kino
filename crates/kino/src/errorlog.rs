//! KINO's error log: what failed, where and when, on this computer only, in a
//! few small files the Diagnostic reads. Nothing private is written: links
//! keep their host only, magnets, keys and e-mail addresses are masked, the
//! home folder becomes « ~ ». The same error within a minute is written once.

use std::collections::HashMap;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{LazyLock, Mutex};

use regex::Regex;
use serde_json::{Value, json};

use crate::sources::stream::py_round;

const MAX_BYTES: u64 = 512 * 1024;
const BACKUPS: usize = 2;
const REPEAT_WINDOW: f64 = 60.0;

static URL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"(?i)\b([a-z][a-z0-9+.-]*)://([^/\s:?#]+)[^\s"'<>)]*"#).unwrap());
static MAGNET: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)magnet:\?\S+").unwrap());
static EMAIL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\w.+-]+@[\w-]+\.[\w.-]+").unwrap());
static TOKEN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b[A-Za-z0-9_\-]{28,}\b").unwrap());

pub fn default_dir() -> PathBuf {
    let home = crate::paths::home();
    if cfg!(target_os = "macos") {
        home.join("Library")
            .join("Application Support")
            .join("KINO")
            .join("Logs")
    } else if cfg!(windows) {
        std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or(home)
            .join("KINO")
            .join("Logs")
    } else {
        home.join(".local").join("share").join("kino").join("logs")
    }
}

/// The text without keys, links' paths, magnets, e-mails or the home folder.
pub fn redact(text: &str, secrets: &[String]) -> String {
    let mut text = text.to_string();
    for secret in secrets.iter().filter(|s| s.chars().count() >= 6) {
        text = text.replace(secret.as_str(), "[clé]");
    }
    let text = MAGNET.replace_all(&text, "magnet:[…]");
    let text = URL.replace_all(&text, "$1://$2/…");
    let text = EMAIL.replace_all(&text, "[e-mail]");
    let mut text = TOKEN.replace_all(&text, "[…]").into_owned();
    let home = crate::paths::home().to_string_lossy().into_owned();
    if home.chars().count() > 1 {
        text = text.replace(&home, "~");
    }
    text
}

fn cut(text: &str, n: usize) -> String {
    text.chars().take(n).collect()
}

/// The keys KINO holds, so that they never reach the log, even inside a message.
fn secrets() -> Vec<String> {
    static CACHE: LazyLock<Mutex<(f64, Vec<String>)>> = LazyLock::new(|| Mutex::new((0.0, vec![])));
    let at = crate::db::now();
    let mut cache = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    if at - cache.0 > 60.0 {
        let cfg = crate::config::load();
        let mut found: Vec<String> = vec![
            cfg.get("rd_token")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        ];
        found.extend(
            cfg.get("provider_tokens")
                .and_then(Value::as_object)
                .into_iter()
                .flatten()
                .filter_map(|(_, v)| v.as_str().map(str::to_string)),
        );
        found.push(
            cfg.get("tmdb_key")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        );
        *cache = (at, found.into_iter().filter(|s| !s.is_empty()).collect());
    }
    cache.1.clone()
}

pub struct ErrorLog {
    pub dir: PathBuf,
    last: Mutex<HashMap<(String, String, String), f64>>,
}

pub static LOG: LazyLock<ErrorLog> = LazyLock::new(|| ErrorLog::new(default_dir()));

impl ErrorLog {
    pub fn new(dir: PathBuf) -> Self {
        Self {
            dir,
            last: Mutex::new(HashMap::new()),
        }
    }

    pub fn path(&self) -> PathBuf {
        self.dir.join("errors.log")
    }

    /// One line for one failure. Never fails: the log must not break KINO.
    pub fn record(&self, place: &str, kind: &str, message: &str, trace: &[String]) -> bool {
        let secrets = secrets();
        let entry = json!({
            "t": py_round(crate::db::now(), 3),
            "where": cut(&redact(place, &secrets), 120),
            "kind": cut(&redact(kind, &secrets), 60),
            "message": cut(&redact(message, &secrets), 500),
            "trace": trace.iter().take(8).map(|f| cut(&redact(f, &secrets), 200)).collect::<Vec<_>>(),
        });
        let signature = (
            entry["where"].to_string(),
            entry["kind"].to_string(),
            entry["message"].to_string(),
        );
        let now = entry["t"].as_f64().unwrap_or(0.0);
        let mut last = self.last.lock().unwrap_or_else(|e| e.into_inner());
        if last
            .get(&signature)
            .is_some_and(|t| now - t < REPEAT_WINDOW)
        {
            return false;
        }
        last.insert(signature, now);
        if last.len() > 200 {
            let mut kept: Vec<_> = last.drain().collect();
            kept.sort_by(|a, b| a.1.total_cmp(&b.1));
            last.extend(kept.into_iter().rev().take(100));
        }
        let write = || -> std::io::Result<()> {
            std::fs::create_dir_all(&self.dir)?;
            self.rotate()?;
            let mut out = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(self.path())?;
            writeln!(out, "{entry}")
        };
        write().is_ok()
    }

    fn rotate(&self) -> std::io::Result<()> {
        match std::fs::metadata(self.path()) {
            Ok(m) if m.len() >= MAX_BYTES => {}
            _ => return Ok(()),
        }
        for index in (1..=BACKUPS).rev() {
            let older = self.dir.join(format!("errors.log.{index}"));
            let newer = if index > 1 {
                self.dir.join(format!("errors.log.{}", index - 1))
            } else {
                self.path()
            };
            if newer.exists() {
                std::fs::rename(newer, older)?;
            }
        }
        Ok(())
    }

    /// The latest entries, newest first; with `since`, only the later ones.
    pub fn recent(&self, limit: usize, since: Option<f64>) -> Vec<Value> {
        let mut entries = Vec::new();
        let names = std::iter::once("errors.log".to_string())
            .chain((1..=BACKUPS).map(|i| format!("errors.log.{i}")));
        for name in names {
            let Ok(content) = std::fs::read_to_string(self.dir.join(name)) else {
                continue;
            };
            for line in content.lines().rev() {
                let Ok(entry) = serde_json::from_str::<Value>(line) else {
                    continue;
                };
                if since.is_some_and(|s| entry["t"].as_f64().unwrap_or(0.0) < s) {
                    return entries;
                }
                entries.push(entry);
                if entries.len() >= limit {
                    return entries;
                }
            }
        }
        entries
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn private_details_are_masked_and_repeats_skipped() {
        let secrets = vec!["SECRETKEY123".to_string()];
        let masked = redact(
            "GET https://api.real-debrid.com/rest/1.0/torrents?token=abc for me@example.com with SECRETKEY123 and magnet:?xt=urn:btih:abc and ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            &secrets,
        );
        assert_eq!(
            masked,
            "GET https://api.real-debrid.com/… for [e-mail] with [clé] and magnet:[…] and […]"
        );
        let dir = tempfile::tempdir().unwrap();
        let log = ErrorLog::new(dir.path().to_path_buf());
        assert!(log.record(
            "page accueil",
            "TypeError",
            "x is undefined",
            &["app.js:1".into()]
        ));
        assert!(
            !log.record("page accueil", "TypeError", "x is undefined", &[]),
            "the same error within a minute"
        );
        assert!(log.record("page accueil", "TypeError", "y is undefined", &[]));
        let recent = log.recent(10, None);
        assert_eq!(recent.len(), 2);
        assert_eq!(recent[0]["message"], "y is undefined", "newest first");
    }
}
