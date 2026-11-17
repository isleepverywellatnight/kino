//! Google Drive sync: KINO's lists, history and settings shared between
//! computers through a file in the user's Drive folder (kino_sync.json).
//! Removals travel as dated markers: a removed title does not come back from
//! an older version on the other computer, but does if it was seen or added
//! there since.

use std::path::{Path, PathBuf};

use rusqlite::{OptionalExtension, params};
use serde_json::{Map, Value, json};

use crate::db::{Db, now};
use crate::library::{KINDS, load_deletions, save_deletions, stamp};

/// The KINO folder of Google Drive on this computer, if there is one.
pub fn find_dir() -> Option<PathBuf> {
    if let Ok(exe) = std::env::current_exe() {
        let shown = exe.to_string_lossy().to_lowercase();
        if ["googledrive", "google drive", "mon drive", "my drive"]
            .iter()
            .any(|k| shown.contains(k))
        {
            return exe.parent().map(Path::to_path_buf);
        }
    }
    let home = crate::paths::home();
    if let Ok(entries) = std::fs::read_dir(home.join("Library").join("CloudStorage")) {
        for entry in entries.filter_map(Result::ok) {
            if entry
                .file_name()
                .to_string_lossy()
                .starts_with("GoogleDrive-")
                && entry.path().is_dir()
            {
                for sub in ["Mon Drive", "My Drive"] {
                    let candidate = entry.path().join(sub).join("KINO");
                    if candidate.is_dir() {
                        return Some(candidate);
                    }
                }
            }
        }
    }
    if cfg!(windows) {
        for drive in ["G:", "H:", "F:"] {
            for sub in ["Mon Drive", "My Drive"] {
                let candidate = PathBuf::from(format!("{drive}\\{sub}\\KINO"));
                if candidate.is_dir() {
                    return Some(candidate);
                }
            }
        }
    }
    [
        "Google Drive/Mon Drive/KINO",
        "Google Drive/My Drive/KINO",
        "Google Drive/KINO",
    ]
    .iter()
    .map(|s| home.join(s))
    .find(|c| c.is_dir())
}

/// When the user last wrote this history entry: a play, or a re-import.
fn history_version(last_watched: &Value, data: &Value) -> f64 {
    stamp(last_watched).max(if data.is_object() {
        stamp(&data["reimported_at"])
    } else {
        0.0
    })
}

/// `kept`, with the rating of `other` when `other` was rated later; None when `kept` stays as it is.
fn newer_rating(kept: &Value, other: &Value) -> Option<Value> {
    if !kept.is_object()
        || !other.is_object()
        || stamp(&other["rated_at"]) <= stamp(&kept["rated_at"])
    {
        return None;
    }
    let mut merged = kept.clone();
    let rating = match &other["user_rating"] {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        v if v == &json!(0) => String::new(),
        v => v.to_string(),
    };
    merged["user_rating"] = json!(rating);
    merged["rated_at"] = other["rated_at"].clone();
    Some(merged)
}

fn text(v: &Value) -> String {
    match v {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        v if v == &json!(0) => String::new(),
        v => v.to_string(),
    }
}

fn device() -> &'static str {
    if cfg!(target_os = "macos") {
        "Darwin"
    } else if cfg!(windows) {
        "Windows"
    } else {
        "Linux"
    }
}

/// Merges the Drive file into KINO, then writes the merged state back.
pub fn sync(db: &Db, dir: Option<PathBuf>) -> Value {
    let Some(dir) = dir else {
        return json!({"status": "disabled", "message": "Google Drive KINO non détecté"});
    };
    let file = dir.join("kino_sync.json");
    let at = now();
    let remote: Option<Value> = if file.exists() {
        match std::fs::read_to_string(&file)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .filter(Value::is_object)
        {
            Some(r) => Some(r),
            // Never overwrite a file being copied by Drive: it may hold the other computer's additions.
            None => {
                return json!({"status": "error", "message": "Fichier de synchronisation Google Drive illisible"});
            }
        }
    } else {
        None
    };
    let done = db.with(|c| {
        let tx = c.unchecked_transaction()?;
        let mut deletions = load_deletions(&tx);
        if let Some(remote) = &remote {
            for kind in KINDS {
                for (id, when) in remote["deleted"][kind].as_object().into_iter().flatten() {
                    let when = stamp(when);
                    if !id.is_empty() && when > 0.0 {
                        let marks = deletions.get_mut(kind).and_then(Value::as_object_mut).expect("kinds");
                        if when > marks.get(id).map(stamp).unwrap_or(0.0) {
                            marks.insert(id.clone(), json!(when));
                        }
                    }
                }
            }
            let marks = |kind: &str| deletions[kind].as_object().cloned().unwrap_or_default();
            let (deleted_wl, deleted_hist) = (marks("watchlist"), marks("history"));
            for (id, when) in &deleted_wl {
                tx.execute("DELETE FROM watchlist WHERE id = ? AND COALESCE(added_at, 0) <= ?", params![id, stamp(when)])?;
            }
            for (id, when) in &deleted_hist {
                let row: Option<(Option<f64>, Option<String>)> =
                    tx.query_row("SELECT last_watched, data_json FROM watch_history WHERE id = ?", [id], |r| Ok((r.get(0).ok(), r.get(1)?))).optional()?;
                let Some((last, data)) = row else { continue };
                let data: Value = data.as_deref().and_then(|d| serde_json::from_str(d).ok()).unwrap_or(json!({}));
                if history_version(&last.map(Value::from).unwrap_or(Value::Null), &data) <= stamp(when) {
                    tx.execute("DELETE FROM watch_history WHERE id = ?", [id])?;
                }
            }
            for item in remote["watchlist"].as_array().into_iter().flatten().filter(|i| i.is_object()) {
                let id = text(&item["id"]);
                if id.is_empty() {
                    continue;
                }
                let added = stamp(&item["added_at"]);
                if deleted_wl.get(&id).is_some_and(|d| added <= stamp(d)) {
                    continue;
                }
                let kind = match text(&item["type"]) {
                    k if k.is_empty() => "movie".to_string(),
                    k => k,
                };
                tx.execute(
                    "INSERT OR IGNORE INTO watchlist (id, name, type, year, poster, imdb_rating, added_at, user_rating) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    params![id, text(&item["name"]), kind, text(&item["year"]), text(&item["poster"]), text(&item["imdbRating"]),
                            if added > 0.0 { added } else { at }, text(&item["user_rating"])],
                )?;
            }
            for entry in remote["history"].as_array().into_iter().flatten().filter(|e| e.is_object()) {
                let id = text(&entry["id"]);
                if id.is_empty() {
                    continue;
                }
                let remote_last = stamp(&entry["last_watched"]);
                if deleted_hist.get(&id).is_some_and(|d| history_version(&json!(remote_last), &entry["data"]) <= stamp(d)) {
                    continue;
                }
                let remote_entry = if entry["data"].is_object() || entry["data"].is_array() { entry["data"].clone() } else { json!({}) };
                let row: Option<(Option<f64>, Option<String>)> =
                    tx.query_row("SELECT last_watched, data_json FROM watch_history WHERE id = ?", [&id], |r| Ok((r.get(0).ok(), r.get(1)?))).optional()?;
                let Some((local_last, local)) = row else {
                    tx.execute("INSERT INTO watch_history (id, data_json, last_watched) VALUES (?, ?, ?)", params![id, remote_entry.to_string(), remote_last])?;
                    continue;
                };
                let local: Value = local.as_deref().and_then(|d| serde_json::from_str(d).ok()).unwrap_or(json!({}));
                // The entry played last wins, but the newer rating stays.
                if remote_last > local_last.unwrap_or(0.0) {
                    let merged = newer_rating(&remote_entry, &local).unwrap_or(remote_entry);
                    tx.execute("UPDATE watch_history SET data_json = ?, last_watched = ? WHERE id = ?", params![merged.to_string(), remote_last, id])?;
                } else if let Some(merged) = newer_rating(&local, &remote_entry) {
                    tx.execute("UPDATE watch_history SET data_json = ? WHERE id = ?", params![merged.to_string(), id])?;
                }
            }
            for (key, value) in remote["config"].as_object().into_iter().flatten() {
                if key == "sync_deletions" {
                    continue;
                }
                let current: Option<Option<String>> = tx.query_row("SELECT value_json FROM app_config WHERE key = ?", [key], |r| r.get(0)).optional()?;
                if current.flatten().is_none_or(|v| v.is_empty()) {
                    tx.execute("INSERT OR REPLACE INTO app_config (key, value_json) VALUES (?, ?)", params![key, value.to_string()])?;
                }
            }
        }
        // A title still present was seen or added again after its removal.
        for (kind, table) in [("history", "watch_history"), ("watchlist", "watchlist")] {
            let present: Vec<String> = {
                let mut s = tx.prepare(&format!("SELECT id FROM {table}"))?;
                s.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?
            };
            if let Some(marks) = deletions.get_mut(kind).and_then(Value::as_object_mut) {
                marks.retain(|id, _| !present.contains(id));
            }
        }
        save_deletions(&tx, &deletions)?;
        let deletions = load_deletions(&tx);
        tx.commit()?;

        let watchlist: Vec<Value> = {
            let mut s = c.prepare("SELECT id, name, type, year, poster, imdb_rating, added_at, user_rating FROM watchlist")?;
            s.query_map([], |r| {
                let col = |i: usize| r.get::<_, Option<String>>(i).ok().flatten().unwrap_or_default();
                let kind = match col(2) {
                    k if k.is_empty() => "movie".to_string(),
                    k => k,
                };
                let added: Value = r.get::<_, Option<f64>>(6).ok().flatten().map(Value::from).unwrap_or(Value::Null);
                Ok(json!({"id": col(0), "name": col(1), "type": kind, "year": col(3), "poster": col(4), "imdbRating": col(5), "user_rating": col(7), "added_at": added}))
            })?
            .collect::<rusqlite::Result<_>>()?
        };
        let history: Vec<Value> = {
            let mut s = c.prepare("SELECT id, data_json, last_watched FROM watch_history ORDER BY last_watched DESC LIMIT 200")?;
            s.query_map([], |r| {
                let data: Value = r.get::<_, Option<String>>(1)?.as_deref().and_then(|d| serde_json::from_str(d).ok()).unwrap_or(json!({}));
                let last: Value = r.get::<_, Option<f64>>(2).ok().flatten().map(Value::from).unwrap_or(Value::Null);
                Ok(json!({"id": r.get::<_, String>(0)?, "data": data, "last_watched": last}))
            })?
            .collect::<rusqlite::Result<_>>()?
        };
        let config: Map<String, Value> = {
            let mut s = c.prepare("SELECT key, value_json FROM app_config WHERE key != 'sync_deletions'")?;
            s.query_map([], |r| {
                let raw: Option<String> = r.get(1)?;
                let value = raw.as_deref().and_then(|v| serde_json::from_str(v).ok()).unwrap_or_else(|| raw.map(Value::from).unwrap_or(Value::Null));
                Ok((r.get::<_, String>(0)?, value))
            })?
            .collect::<rusqlite::Result<_>>()?
        };
        Ok((watchlist, history, config, deletions))
    });
    let (watchlist, history, config, deletions) = match done {
        Ok(d) => d,
        Err(e) => return json!({"status": "error", "message": e.to_string()}),
    };
    let consolidated = json!({"version": 2, "updated_at": at, "device": device(), "config": config, "watchlist": watchlist, "history": history, "deleted": deletions});
    let temporary = dir.join("kino_sync.tmp");
    let written = serde_json::to_string_pretty(&consolidated)
        .map_err(|e| e.to_string())
        .and_then(|t| std::fs::write(&temporary, t).map_err(|e| e.to_string()))
        .and_then(|_| std::fs::rename(&temporary, &file).map_err(|e| e.to_string()));
    if let Err(e) = written {
        return json!({"status": "error", "message": e});
    }
    json!({"status": "synced", "path": file.to_string_lossy(), "device": device(), "updated_at": at,
           "watchlist_count": watchlist.len(), "history_count": history.len()})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn two_computers_share_lists_and_removals() {
        let drive = tempfile::tempdir().unwrap();
        let (a_dir, b_dir) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
        let a = Db::open(&a_dir.path().join("k.db")).unwrap();
        let b = Db::open(&b_dir.path().join("k.db")).unwrap();
        crate::library::toggle_watchlist(&a, &json!({"id": "tt1", "name": "One", "type": "movie"}));
        crate::library::toggle_watchlist(&a, &json!({"id": "tt2", "name": "Two", "type": "movie"}));
        assert_eq!(
            sync(&a, Some(drive.path().to_path_buf()))["watchlist_count"],
            2
        );
        assert_eq!(
            sync(&b, Some(drive.path().to_path_buf()))["watchlist_count"],
            2,
            "the other computer gets them"
        );
        crate::library::toggle_watchlist(&b, &json!({"id": "tt1"}));
        sync(&b, Some(drive.path().to_path_buf()));
        let after = sync(&a, Some(drive.path().to_path_buf()));
        assert_eq!(
            after["watchlist_count"], 1,
            "the removal travels, the older version does not bring it back"
        );
        std::fs::write(drive.path().join("kino_sync.json"), "{half a file").unwrap();
        assert_eq!(
            sync(&a, Some(drive.path().to_path_buf()))["status"],
            "error",
            "an unreadable file is never overwritten"
        );
        assert_eq!(sync(&a, None)["status"], "disabled");
    }
}
