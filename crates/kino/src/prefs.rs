//! Small preferences kept in `app_config`: spoiler protection, and the audio and
//! subtitle choices remembered for each series.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::db::Db;

static SERIES_ID: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[a-z0-9][a-z0-9:_-]{1,95}$").unwrap());
static CODE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^S\d{2}E\d{2,3}$").unwrap());
static SEASON: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{1,2}$").unwrap());
static EPISODE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{1,3}$").unwrap());
static LANGUAGE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[a-z]{2,3}$").unwrap());

const SPOILERS_KEY: &str = "spoiler_protection_v1";
const PLAYBACK_PREFIX: &str = "series_playback_v1:";

/// A refused value; the route answers 400 with its own message.
#[derive(Debug)]
pub struct Invalid;

/// Python's str() of a JSON value, for the checks written against it.
fn as_text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
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

fn text_or_empty(value: &Value) -> String {
    match value {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        other => as_text(other),
    }
}

pub fn series_id(value: &Value) -> Result<String, Invalid> {
    let id = text_or_empty(value).trim().to_lowercase();
    if SERIES_ID.is_match(&id) {
        Ok(id)
    } else {
        Err(Invalid)
    }
}

pub fn episode_code(season: &Value, episode: &Value) -> Result<String, Invalid> {
    if season.is_boolean() || episode.is_boolean() {
        return Err(Invalid);
    }
    let (s, e) = (as_text(season), as_text(episode));
    if !SEASON.is_match(&s) || !EPISODE.is_match(&e) {
        return Err(Invalid);
    }
    let (s, e): (u32, u32) = (
        s.parse().map_err(|_| Invalid)?,
        e.parse().map_err(|_| Invalid)?,
    );
    if s > 99 || !(1..=999).contains(&e) {
        return Err(Invalid);
    }
    Ok(format!("S{s:02}E{e:02}"))
}

// ---------- Spoiler protection ----------

pub fn spoilers(db: &Db) -> Value {
    let stored = db
        .get_config(SPOILERS_KEY)
        .filter(Value::is_object)
        .unwrap_or(json!({}));
    let overrides: Map<String, Value> = stored["overrides"]
        .as_object()
        .map(|o| {
            o.iter()
                .filter(|(k, v)| SERIES_ID.is_match(k) && v.is_boolean())
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect()
        })
        .unwrap_or_default();
    let revealed: Map<String, Value> = stored["revealed"]
        .as_object()
        .map(|o| {
            o.iter()
                .filter(|(k, v)| SERIES_ID.is_match(k) && v.is_array())
                .map(|(k, v)| {
                    let codes: Vec<Value> = v
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter(|c| c.as_str().is_some_and(|c| CODE.is_match(c)))
                        .cloned()
                        .collect();
                    (k.clone(), Value::Array(codes))
                })
                .collect()
        })
        .unwrap_or_default();
    json!({"enabled": stored["enabled"] == json!(true), "overrides": overrides, "revealed": revealed})
}

pub fn update_spoilers(
    db: &Db,
    action: &str,
    enabled: &Value,
    identifier: &Value,
    season: &Value,
    episode: &Value,
) -> Result<Value, Invalid> {
    if !["global", "series", "reveal", "hide"].contains(&action) {
        return Err(Invalid);
    }
    if action == "global" && !enabled.is_boolean() {
        return Err(Invalid);
    }
    if action == "series" && !enabled.is_null() && !enabled.is_boolean() {
        return Err(Invalid);
    }
    let id = if action != "global" {
        series_id(identifier)?
    } else {
        String::new()
    };
    let code = if action == "reveal" || action == "hide" {
        episode_code(season, episode)?
    } else {
        String::new()
    };
    let mut state = spoilers(db);
    match action {
        "global" => state["enabled"] = enabled.clone(),
        "series" => {
            let overrides = state["overrides"].as_object_mut().ok_or(Invalid)?;
            if enabled.is_null() {
                overrides.remove(&id);
            } else {
                overrides.insert(id, enabled.clone());
            }
        }
        _ => {
            let revealed = state["revealed"].as_object_mut().ok_or(Invalid)?;
            let mut codes: Vec<String> = revealed
                .get(&id)
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|c| c.as_str().map(str::to_string))
                .collect();
            codes.retain(|c| *c != code);
            if action == "reveal" {
                codes.push(code);
            }
            codes.sort();
            codes.dedup();
            codes.truncate(500);
            if codes.is_empty() {
                revealed.remove(&id);
            } else {
                revealed.insert(id, json!(codes));
            }
        }
    }
    db.save_config(SPOILERS_KEY, &state).map_err(|_| Invalid)?;
    Ok(state)
}

// ---------- Playback choices of a series ----------

fn lower_text(value: &Value, default: &str) -> String {
    let text = text_or_empty(value);
    if text.is_empty() {
        default.to_string()
    } else {
        text.to_lowercase()
    }
}

pub fn normalize_playback(value: &Value) -> Result<Value, Invalid> {
    if !value.is_object() {
        return Err(Invalid);
    }
    let mut audio = value["audio"].clone();
    if !audio.is_null() {
        if !audio.is_object() {
            return Err(Invalid);
        }
        let language = lower_text(&audio["language"], "");
        let role = lower_text(&audio["role"], "main");
        let original = if audio.get("original").is_none() {
            json!(false)
        } else {
            audio["original"].clone()
        };
        if !LANGUAGE.is_match(&language)
            || !["main", "commentary", "description"].contains(&role.as_str())
            || !original.is_boolean()
        {
            return Err(Invalid);
        }
        audio = json!({"language": language, "role": role, "original": original});
    }
    let mut subtitles = value["subtitles"].clone();
    if !subtitles.is_null() {
        if !subtitles.is_object() || !subtitles["enabled"].is_boolean() {
            return Err(Invalid);
        }
        let enabled = subtitles["enabled"].as_bool().unwrap_or(false);
        let language = lower_text(&subtitles["language"], "");
        let kind = lower_text(&subtitles["kind"], "");
        let variant = lower_text(&subtitles["variant"], "standard");
        if enabled
            && (!["fr", "en"].contains(&language.as_str())
                || kind != "opensubtitles"
                || !["standard", "forced", "sdh"].contains(&variant.as_str()))
        {
            return Err(Invalid);
        }
        let keep = |v: String| if enabled { v } else { String::new() };
        subtitles = json!({"enabled": enabled, "language": keep(language), "kind": keep(kind), "variant": keep(variant)});
    }
    if audio.is_null() && subtitles.is_null() {
        return Err(Invalid);
    }
    Ok(json!({"audio": audio, "subtitles": subtitles}))
}

pub fn playback(db: &Db, series: &Value) -> Result<Value, Invalid> {
    let key = format!("{PLAYBACK_PREFIX}{}", series_id(series)?);
    Ok(db
        .get_config(&key)
        .and_then(|stored| normalize_playback(&stored).ok())
        .unwrap_or(Value::Null))
}

pub fn save_playback(db: &Db, series: &Value, preference: &Value) -> Result<Value, Invalid> {
    let key = format!("{PLAYBACK_PREFIX}{}", series_id(series)?);
    let normalized = normalize_playback(preference)?;
    db.save_config(&key, &normalized).map_err(|_| Invalid)?;
    Ok(normalized)
}

pub fn clear_playback(db: &Db, series: &Value) -> Result<(), Invalid> {
    let key = format!("{PLAYBACK_PREFIX}{}", series_id(series)?);
    db.delete_config(&key).map_err(|_| Invalid)
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
    fn spoiler_choices() {
        let (_d, db) = db();
        assert_eq!(
            spoilers(&db),
            json!({"enabled": false, "overrides": {}, "revealed": {}})
        );
        update_spoilers(
            &db,
            "global",
            &json!(true),
            &Value::Null,
            &Value::Null,
            &Value::Null,
        )
        .unwrap();
        update_spoilers(
            &db,
            "series",
            &json!(false),
            &json!("TT0903747"),
            &Value::Null,
            &Value::Null,
        )
        .unwrap();
        update_spoilers(
            &db,
            "reveal",
            &Value::Null,
            &json!("tt0903747"),
            &json!(2),
            &json!("3"),
        )
        .unwrap();
        let state = update_spoilers(
            &db,
            "reveal",
            &Value::Null,
            &json!("tt0903747"),
            &json!(1),
            &json!(10),
        )
        .unwrap();
        assert_eq!(
            state,
            json!({"enabled": true, "overrides": {"tt0903747": false}, "revealed": {"tt0903747": ["S01E10", "S02E03"]}})
        );
        let state = update_spoilers(
            &db,
            "hide",
            &Value::Null,
            &json!("tt0903747"),
            &json!(1),
            &json!(10),
        )
        .unwrap();
        assert_eq!(state["revealed"], json!({"tt0903747": ["S02E03"]}));
        for (action, enabled, id, s, e) in [
            (
                "global",
                json!("yes"),
                Value::Null,
                Value::Null,
                Value::Null,
            ),
            ("x", json!(true), Value::Null, Value::Null, Value::Null),
            ("reveal", Value::Null, json!("tt1"), json!(true), json!(1)),
            ("reveal", Value::Null, json!("tt1"), json!(1), json!(0)),
            (
                "series",
                json!(true),
                json!("../x"),
                Value::Null,
                Value::Null,
            ),
        ] {
            assert!(
                update_spoilers(&db, action, &enabled, &id, &s, &e).is_err(),
                "{action}"
            );
        }
    }

    #[test]
    fn playback_choices() {
        let (_d, db) = db();
        assert_eq!(playback(&db, &json!("tt0903747")).unwrap(), Value::Null);
        let saved = save_playback(&db, &json!("tt0903747"), &json!({"audio": {"language": "JPN"}, "subtitles": {"enabled": true, "language": "fr", "kind": "opensubtitles"}})).unwrap();
        assert_eq!(
            saved,
            json!({"audio": {"language": "jpn", "role": "main", "original": false},
                                 "subtitles": {"enabled": true, "language": "fr", "kind": "opensubtitles", "variant": "standard"}})
        );
        assert_eq!(playback(&db, &json!("TT0903747")).unwrap(), saved);
        assert!(save_playback(&db, &json!("tt0903747"), &json!({})).is_err());
        assert!(
            save_playback(
                &db,
                &json!("tt0903747"),
                &json!({"subtitles": {"enabled": true, "language": "de", "kind": "opensubtitles"}})
            )
            .is_err()
        );
        clear_playback(&db, &json!("tt0903747")).unwrap();
        assert_eq!(playback(&db, &json!("tt0903747")).unwrap(), Value::Null);
        assert!(playback(&db, &json!("x")).is_err());
    }
}
