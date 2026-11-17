//! Ma Liste and the watch history, as the Python version stores them.
//!
//! A removal is remembered as {id: deleted_at} (`sync_deletions` in
//! `app_config`): Google Drive sync exports it, and it wins over any older
//! version of the item; a later addition or play wins over it.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Map, Value, json};

use crate::db::{Db, now};

const DELETIONS_KEY: &str = "sync_deletions";
const KINDS: [&str; 2] = ["history", "watchlist"];
const MAX_DELETIONS: usize = 2000;

static EPISODE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^s(\d{1,2})e(\d{1,3})$").unwrap());

pub fn normalize_episode_code(value: &str) -> String {
    match EPISODE.captures(value) {
        Some(c) => format!(
            "S{:02}E{:02}",
            c[1].parse::<u32>().unwrap_or(0),
            c[2].parse::<u32>().unwrap_or(0)
        ),
        None => value.to_string(),
    }
}

fn stamp(value: &Value) -> f64 {
    let v = value
        .as_f64()
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0.0);
    if v.is_finite() && v > 0.0 { v } else { 0.0 }
}

fn load_deletions(conn: &Connection) -> Map<String, Value> {
    let raw: Option<String> = conn
        .query_row(
            "SELECT value_json FROM app_config WHERE key = ?",
            [DELETIONS_KEY],
            |r| r.get(0),
        )
        .optional()
        .ok()
        .flatten()
        .flatten();
    let stored: Value = raw
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(json!({}));
    let mut clean = Map::new();
    for kind in KINDS {
        let entries: Map<String, Value> = stored[kind]
            .as_object()
            .map(|o| {
                o.iter()
                    .filter(|&(id, s)| !id.is_empty() && stamp(s) > 0.0)
                    .map(|(id, s)| (id.clone(), json!(stamp(s))))
                    .collect()
            })
            .unwrap_or_default();
        clean.insert(kind.into(), Value::Object(entries));
    }
    clean
}

fn save_deletions(conn: &Connection, deletions: &Map<String, Value>) -> rusqlite::Result<()> {
    let mut kept = Map::new();
    for kind in KINDS {
        let mut entries: Vec<(String, f64)> = deletions[kind]
            .as_object()
            .into_iter()
            .flatten()
            .map(|(k, v)| (k.clone(), stamp(v)))
            .collect();
        entries.sort_by(|a, b| b.1.total_cmp(&a.1));
        entries.truncate(MAX_DELETIONS);
        kept.insert(
            kind.into(),
            Value::Object(entries.into_iter().map(|(k, v)| (k, json!(v))).collect()),
        );
    }
    conn.execute(
        "INSERT INTO app_config (key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) \
         ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = CURRENT_TIMESTAMP",
        params![DELETIONS_KEY, Value::Object(kept).to_string()],
    )?;
    Ok(())
}

/// Remembers removals; each stamp is when the removed version was written.
fn mark_deleted(conn: &Connection, kind: &str, stamps: &[(String, f64)]) -> rusqlite::Result<()> {
    if stamps.is_empty() {
        return Ok(());
    }
    let mut deletions = load_deletions(conn);
    let at = now();
    if let Some(entries) = deletions.get_mut(kind).and_then(Value::as_object_mut) {
        for (id, written) in stamps {
            // Later than the removed version, even if written by a clock ahead of this one.
            let deleted_at = at.max(written + 0.001);
            let previous = entries.get(id).map(stamp).unwrap_or(0.0);
            entries.insert(id.clone(), json!(previous.max(deleted_at)));
        }
    }
    save_deletions(conn, &deletions)
}

/// An explicit local addition replaces an earlier removal.
fn forget_deletion(conn: &Connection, kind: &str, id: &str) -> rusqlite::Result<()> {
    let mut deletions = load_deletions(conn);
    if deletions
        .get_mut(kind)
        .and_then(Value::as_object_mut)
        .and_then(|e| e.remove(id))
        .is_some()
    {
        save_deletions(conn, &deletions)?;
    }
    Ok(())
}

fn text(value: &Value) -> String {
    match value {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        Value::Number(n) if n.as_f64() == Some(0.0) => String::new(),
        other => other.to_string(),
    }
}

// ---------- Ma Liste ----------

pub fn watchlist(db: &Db) -> Vec<Value> {
    db.with(|c| {
        let mut statement = c.prepare("SELECT id, name, type, year, poster, imdb_rating, user_rating FROM watchlist ORDER BY added_at DESC LIMIT 500")?;
        let rows = statement.query_map([], |r| {
            let get = |i: usize| r.get::<_, Option<String>>(i).map(Option::unwrap_or_default);
            let kind = get(2)?;
            Ok(json!({"id": get(0)?, "name": get(1)?, "type": if kind.is_empty() { "movie".into() } else { kind },
                      "year": get(3)?, "poster": get(4)?, "imdbRating": get(5)?, "user_rating": get(6)?}))
        })?;
        rows.collect::<rusqlite::Result<Vec<Value>>>()
    })
    .unwrap_or_default()
}

/// Adds the title to Ma Liste, or removes it if it is there.
pub fn toggle_watchlist(db: &Db, item: &Value) -> Vec<Value> {
    let id = text(&item["id"]);
    if id.is_empty() {
        return watchlist(db);
    }
    let _ = db.with(|c| {
        let tx = c.unchecked_transaction()?;
        let added: Option<Option<f64>> = tx.query_row("SELECT added_at FROM watchlist WHERE id = ?", [&id], |r| r.get(0)).optional()?;
        match added {
            Some(added) => {
                tx.execute("DELETE FROM watchlist WHERE id = ?", [&id])?;
                mark_deleted(&tx, "watchlist", &[(id.clone(), added.unwrap_or(0.0))])?;
            }
            None => {
                forget_deletion(&tx, "watchlist", &id)?;
                let kind = text(&item["type"]);
                tx.execute(
                    "INSERT INTO watchlist (id, name, type, year, poster, imdb_rating, added_at, user_rating) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    params![id, text(&item["name"]), if kind.is_empty() { "movie".to_string() } else { kind }, text(&item["year"]),
                            text(&item["poster"]), text(&item["imdbRating"]), now(), text(&item["user_rating"])],
                )?;
            }
        }
        tx.commit()
    });
    watchlist(db)
}

// ---------- The history ----------

pub fn history(db: &Db) -> Vec<Value> {
    db.with(|c| {
        let mut statement =
            c.prepare("SELECT data_json FROM watch_history ORDER BY last_watched DESC LIMIT 300")?;
        let rows = statement.query_map([], |r| r.get::<_, Option<String>>(0))?;
        Ok(rows
            .filter_map(|r| r.ok().flatten())
            .filter_map(|s| serde_json::from_str(&s).ok())
            .collect())
    })
    .unwrap_or_default()
}

fn number(value: &Value) -> Option<f64> {
    value.as_f64().filter(|v| v.is_finite())
}

/// Records a play: merges it into the title's entry, keeps each episode's
/// position, and marks an episode watched once 88 % in or 3 minutes from the end.
pub fn record_history(db: &Db, entry: &Map<String, Value>) -> Vec<Value> {
    let id = [&entry.get("id"), &entry.get("filename")]
        .into_iter()
        .flatten()
        .map(|v| text(v))
        .find(|t| !t.is_empty());
    let Some(id) = id else {
        return history(db);
    };
    let at = now();
    let _ = db.with(|c| {
        let existing: Option<Option<String>> = c.query_row("SELECT data_json FROM watch_history WHERE id = ?", [&id], |r| r.get(0)).optional()?;
        let mut merged: Map<String, Value> = existing.flatten().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default();
        merged.remove("imported_watched");
        for (k, v) in entry {
            if !v.is_null() && v != &json!("") {
                merged.insert(k.clone(), v.clone());
            }
        }
        let mut watched: Vec<String> = Vec::new();
        for code in merged.get("watched_episodes").and_then(Value::as_array).into_iter().flatten() {
            let code = normalize_episode_code(&text(code));
            if !watched.contains(&code) {
                watched.push(code);
            }
        }
        let mut positions: Map<String, Value> = merged
            .get("ep_positions")
            .and_then(Value::as_object)
            .map(|o| o.iter().map(|(k, v)| (normalize_episode_code(k), v.clone())).collect())
            .unwrap_or_default();
        let key = match (merged.get("season"), merged.get("episode")) {
            (Some(s), Some(e)) if !s.is_null() && !e.is_null() => Some(normalize_episode_code(&format!("s{}e{}", python_str(s), python_str(e)))),
            _ => None,
        };
        if let (Some(pos), Some(dur)) = (entry.get("position").and_then(number), entry.get("duration").and_then(number))
            && dur > 0.0
        {
            let done = pos / dur >= 0.88 || dur - pos <= 180.0;
            merged.insert("completed".into(), json!(done));
            if let Some(key) = &key {
                if pos <= 0.0 && !done {
                    positions.remove(key);
                } else {
                    positions.insert(key.clone(), json!({"position": entry["position"], "duration": entry["duration"], "completed": done}));
                }
                if done && !watched.contains(key) {
                    watched.push(key.clone());
                } else if !done {
                    watched.retain(|c| c != key);
                }
            }
        }
        merged.insert("watched_episodes".into(), json!(watched));
        merged.insert("ep_positions".into(), Value::Object(positions));
        merged.insert("last_watched".into(), json!(at));
        merged.insert("id".into(), json!(id));
        let tx = c.unchecked_transaction()?;
        forget_deletion(&tx, "history", &id)?;
        tx.execute(
            "INSERT INTO watch_history (id, data_json, last_watched) VALUES (?, ?, ?) \
             ON CONFLICT(id) DO UPDATE SET data_json = excluded.data_json, last_watched = excluded.last_watched",
            params![id, Value::Object(merged).to_string(), at],
        )?;
        tx.commit()
    });
    history(db)
}

/// Python's str() of a season or episode number (« 1 », or « 1.0 » for a float).
fn python_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Number(n) if n.is_f64() => {
            let f = n.as_f64().unwrap_or(0.0);
            if f.fract() == 0.0 {
                format!("{f:.1}")
            } else {
                f.to_string()
            }
        }
        Value::Bool(b) => {
            if *b {
                "True".into()
            } else {
                "False".into()
            }
        }
        other => other.to_string(),
    }
}

/// Updates fields of an entry without moving it in « Reprendre ».
pub fn patch_history(db: &Db, id: &str, fields: &Map<String, Value>) -> bool {
    db.with(|c| {
        let raw: Option<Option<String>> = c
            .query_row(
                "SELECT data_json FROM watch_history WHERE id = ?",
                [id],
                |r| r.get(0),
            )
            .optional()?;
        let Some(mut data) = raw.and_then(|s| {
            serde_json::from_str::<Map<String, Value>>(s.as_deref().unwrap_or("{}")).ok()
        }) else {
            return Ok(false);
        };
        data.extend(fields.clone());
        c.execute(
            "UPDATE watch_history SET data_json = ? WHERE id = ?",
            params![Value::Object(data).to_string(), id],
        )?;
        Ok(true)
    })
    .unwrap_or(false)
}

/// Removes an entry, also at the next syncs; « __all__ » empties the history.
pub fn remove_history(db: &Db, id: &str) -> Vec<Value> {
    if id.is_empty() {
        return history(db);
    }
    let _ = db.with(|c| {
        let tx = c.unchecked_transaction()?;
        let rows: Vec<(String, f64)> = if id == "__all__" {
            let mut s = tx.prepare("SELECT id, last_watched FROM watch_history")?;
            let all = s
                .query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, Option<f64>>(1)?.unwrap_or(0.0),
                    ))
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            drop(s);
            tx.execute("DELETE FROM watch_history", [])?;
            all
        } else {
            let written: Option<Option<f64>> = tx
                .query_row(
                    "SELECT last_watched FROM watch_history WHERE id = ?",
                    [id],
                    |r| r.get(0),
                )
                .optional()?;
            tx.execute("DELETE FROM watch_history WHERE id = ?", [id])?;
            vec![(id.to_string(), written.flatten().unwrap_or(0.0))]
        };
        mark_deleted(&tx, "history", &rows)?;
        tx.commit()
    });
    history(db)
}

/// « Noter en un geste »: '' or a note from 0.5 to 5 by halves, as text ('4', '4.5').
pub fn clean_rating(value: &Value) -> Result<String, &'static str> {
    let number = match value {
        Value::Null => return Ok(String::new()),
        Value::String(s) if s.is_empty() => return Ok(String::new()),
        Value::String(s) => s.trim().parse::<f64>().map_err(|_| "Note invalide.")?,
        Value::Number(n) => n.as_f64().unwrap_or(f64::NAN),
        _ => return Err("Note invalide."),
    };
    if !(0.5..=5.0).contains(&number) || number * 2.0 != (number * 2.0).trunc() {
        return Err("Note invalide.");
    }
    let shown = format!("{number:.1}");
    Ok(shown
        .trim_end_matches('0')
        .trim_end_matches('.')
        .to_string())
}

/// The history fields a POST /api/rating body changes: the note, the moment
/// that asked for it (« film », « S2 »), and « Passer ».
pub fn rating_patch(
    body: &Map<String, Value>,
    now: f64,
) -> Result<Map<String, Value>, &'static str> {
    static ASKED: std::sync::LazyLock<regex::Regex> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"^(film|S\d{1,3})$").unwrap());
    let mut fields = Map::new();
    if let Some(rating) = body.get("rating") {
        fields.insert("user_rating".into(), clean_rating(rating)?.into());
        fields.insert("rated_at".into(), json!(now));
        fields.insert("rating_skipped".into(), false.into());
    }
    if let Some(asked) = body.get("asked") {
        let asked = match asked {
            Value::String(s) => s.clone(),
            Value::Null | Value::Bool(false) => String::new(),
            other => other.to_string(),
        };
        if !ASKED.is_match(&asked) {
            return Err("Moment de la question inconnu.");
        }
        fields.insert("rating_asked".into(), asked.into());
    }
    if let Some(skip) = body.get("skip")
        && !body.contains_key("rating")
    {
        let on = !matches!(skip, Value::Null | Value::Bool(false))
            && skip != &json!(0)
            && skip != &json!("");
        fields.insert("rating_skipped".into(), on.into());
    }
    if fields.is_empty() {
        return Err("Rien à enregistrer.");
    }
    Ok(fields)
}

/// Films a Letterboxd import added by mistake (a wrong match), removed while
/// still as imported, never played; the removal reaches the other devices.
pub fn drop_wrong_imports(db: &Db, ids: &[String]) -> Vec<String> {
    let ids: Vec<&String> = ids.iter().filter(|i| !i.is_empty()).collect();
    if ids.is_empty() {
        return vec![];
    }
    db.with(|c| {
        let tx = c.unchecked_transaction()?;
        let mut dropped: Vec<(String, f64)> = Vec::new();
        for id in ids {
            let row: Option<(Option<String>, Value)> = tx
                .query_row(
                    "SELECT data_json, last_watched FROM watch_history WHERE id = ?",
                    [id],
                    |r| {
                        let written: Option<f64> = r.get(1).ok();
                        Ok((r.get(0)?, written.map(Value::from).unwrap_or(Value::Null)))
                    },
                )
                .optional()?;
            let Some((data, written)) = row else { continue };
            let Ok(data) = serde_json::from_str::<Value>(data.as_deref().unwrap_or("{}")) else {
                continue;
            };
            let imported = &data["imported_watched"];
            if matches!(imported, Value::Null | Value::Bool(false))
                || imported == &json!(0)
                || imported == &json!("")
            {
                continue;
            }
            tx.execute("DELETE FROM watch_history WHERE id = ?", [id])?;
            dropped.push((id.clone(), stamp(&written)));
        }
        mark_deleted(&tx, "history", &dropped)?;
        tx.commit()?;
        Ok(dropped.into_iter().map(|(id, _)| id).collect())
    })
    .unwrap_or_default()
}

/// Adds the imported titles still missing from Ma Liste and the history; a
/// title already there is left as it is. A title removed in KINO is skipped
/// unless `restore`. Imported films seen are dated before any real viewing.
/// Returns {"watchlist": [ids], "history": [ids], "removed": {...}}.
pub fn import_user_lists(
    db: &Db,
    watchlist_items: &[Value],
    history_entries: &[Value],
    restore: bool,
) -> Value {
    const IMPORTED_BEFORE: f64 = 946_684_800.0;
    let at = now();
    db.with(|c| {
        let tx = c.unchecked_transaction()?;
        let mut deletions = load_deletions(&tx);
        let mut restored = false;
        let (mut added_wl, mut added_hist, mut removed_wl, mut removed_hist) = (vec![], vec![], vec![], vec![]);
        let id_of = |v: &Value| match &v["id"] {
            Value::Null | Value::Bool(false) => String::new(),
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        let field = |v: &Value| match v {
            Value::Null | Value::Bool(false) => String::new(),
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        let mut present: HashSet<String> = {
            let mut s = tx.prepare("SELECT id FROM watchlist")?;
            s.query_map([], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<_>>()?
        };
        for item in watchlist_items.iter().filter(|i| i.is_object()) {
            let id = id_of(item);
            if id.is_empty() || present.contains(&id) {
                continue;
            }
            if let Some(marks) = deletions.get_mut("watchlist").and_then(Value::as_object_mut)
                && marks.contains_key(&id)
            {
                if !restore {
                    removed_wl.push(id);
                    continue;
                }
                marks.remove(&id);
                restored = true;
            }
            present.insert(id.clone());
            let kind = match field(&item["type"]) {
                k if k.is_empty() => "movie".to_string(),
                k => k,
            };
            tx.execute(
                "INSERT INTO watchlist (id, name, type, year, poster, imdb_rating, added_at, user_rating) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                params![id, field(&item["name"]), kind, field(&item["year"]), field(&item["poster"]), field(&item["imdbRating"]),
                        at - 0.001 * added_wl.len() as f64, field(&item["user_rating"])],
            )?;
            added_wl.push(id);
        }
        let rows: Vec<(String, Value)> = {
            let mut s = tx.prepare("SELECT id, last_watched FROM watch_history")?;
            s.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<f64>>(1)?.map(Value::from).unwrap_or(Value::Null))))?
                .collect::<rusqlite::Result<_>>()?
        };
        let mut present: HashSet<String> = rows.iter().map(|r| r.0.clone()).collect();
        let oldest = rows.iter().map(|r| stamp(&r.1)).filter(|s| *s > 0.0).fold(IMPORTED_BEFORE, f64::min);
        for entry in history_entries.iter().filter(|e| e.is_object()) {
            let id = id_of(entry);
            if id.is_empty() || present.contains(&id) {
                continue;
            }
            let mut data = entry.as_object().cloned().unwrap_or_default();
            if let Some(marks) = deletions.get_mut("history").and_then(Value::as_object_mut)
                && marks.contains_key(&id)
            {
                if !restore {
                    removed_hist.push(id);
                    continue;
                }
                marks.remove(&id);
                restored = true;
                data.insert("id".into(), json!(id));
                data.insert("last_watched".into(), Value::Null);
                data.insert("reimported_at".into(), json!(at));
            }
            present.insert(id.clone());
            let written = (oldest - 1.0 - 0.001 * added_hist.len() as f64).max(1.0);
            data.insert("id".into(), json!(id));
            data.insert("last_watched".into(), json!(written));
            tx.execute(
                "INSERT INTO watch_history (id, data_json, last_watched) VALUES (?, ?, ?)",
                params![id, Value::Object(data).to_string(), written],
            )?;
            added_hist.push(id);
        }
        if restored {
            save_deletions(&tx, &deletions)?;
        }
        tx.commit()?;
        Ok(json!({"watchlist": added_wl, "history": added_hist, "removed": {"watchlist": removed_wl, "history": removed_hist}}))
    })
    .unwrap_or_else(|_| json!({"watchlist": [], "history": [], "removed": {"watchlist": [], "history": []}}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn db() -> (tempfile::TempDir, Db) {
        let dir = tempfile::tempdir().unwrap();
        let db = Db::open(&dir.path().join("kino.db")).unwrap();
        (dir, db)
    }

    #[test]
    fn ma_liste_toggles_and_remembers_removals() {
        let (_d, db) = db();
        let list = toggle_watchlist(
            &db,
            &json!({"id": "tt1", "name": "Heat", "year": 1995, "imdbRating": 8.3}),
        );
        assert_eq!(
            list,
            vec![
                json!({"id": "tt1", "name": "Heat", "type": "movie", "year": "1995", "poster": "", "imdbRating": "8.3", "user_rating": ""})
            ]
        );
        assert!(toggle_watchlist(&db, &json!({"id": "tt1"})).is_empty());
        let deletions = db.get_config(DELETIONS_KEY).unwrap();
        assert!(deletions["watchlist"]["tt1"].as_f64().unwrap() > 0.0);
        toggle_watchlist(&db, &json!({"id": "tt1"}));
        assert!(
            db.get_config(DELETIONS_KEY).unwrap()["watchlist"]
                .as_object()
                .unwrap()
                .is_empty(),
            "added again: the removal is forgotten"
        );
    }

    #[test]
    fn a_play_is_merged_into_the_entry() {
        let (_d, db) = db();
        let entry = |v: Value| v.as_object().unwrap().clone();
        record_history(
            &db,
            &entry(
                json!({"id": "tt2", "name": "Breaking Bad", "type": "series", "season": 1, "episode": 2, "position": 100, "duration": 3000, "watched_episodes": ["s1e1"]}),
            ),
        );
        let h = record_history(
            &db,
            &entry(
                json!({"id": "tt2", "season": 1, "episode": 2, "position": 2900, "duration": 3000, "name": ""}),
            ),
        );
        assert_eq!(
            h[0]["name"], "Breaking Bad",
            "an empty value does not erase"
        );
        assert_eq!(h[0]["watched_episodes"], json!(["S01E01", "S01E02"]));
        assert_eq!(
            h[0]["ep_positions"]["S01E02"],
            json!({"position": 2900, "duration": 3000, "completed": true})
        );
        assert_eq!(h[0]["completed"], true);
        assert!(patch_history(
            &db,
            "tt2",
            &entry(json!({"series_finished": true}))
        ));
        assert_eq!(history(&db)[0]["series_finished"], true);
        assert!(remove_history(&db, "tt2").is_empty());
        record_history(&db, &entry(json!({"id": "tt3"})));
        record_history(&db, &entry(json!({"filename": "Film.mkv"})));
        assert_eq!(history(&db).len(), 2);
        assert!(remove_history(&db, "__all__").is_empty());
        assert_eq!(
            db.get_config(DELETIONS_KEY).unwrap()["history"]
                .as_object()
                .unwrap()
                .len(),
            3
        );
    }

    #[test]
    fn episode_codes() {
        assert_eq!(normalize_episode_code("s1e2"), "S01E02");
        assert_eq!(normalize_episode_code("S10E100"), "S10E100");
        assert_eq!(normalize_episode_code("bonus"), "bonus");
    }
}
