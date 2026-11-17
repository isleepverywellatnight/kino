//! « À l'écoute » and the playback keys, the part every system shares: what
//! to show for the player's state, when to tell the system again, and which
//! remote command a key or a Control Centre button stands for. Both players
//! already obey the phone remote's queue, so the keys go through it.

use serde_json::{Map, Value, json};

use crate::play::{text, truthy};

/// A player that has not reported for this long is closed (it reports every second).
const STALE_AFTER: f64 = 4.0;
/// Next / previous on a film: a jump in the film, as the remote's buttons.
const SKIP_SECONDS: i64 = 10;
/// Elapsed time drifts by itself (rate 1): sent again only when this far off.
const DRIFT: f64 = 2.0;

#[derive(Clone, Debug, PartialEq)]
pub struct Fields {
    pub title: String,
    pub artist: String,
    pub duration: f64,
    pub elapsed: f64,
    pub rate: f64,
    pub poster: String,
}

fn number(value: &Value) -> Option<f64> {
    match value {
        Value::Null => Some(0.0),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::String(s) if s.is_empty() => Some(0.0),
        Value::String(s) => s.trim().parse().ok(),
        other => other.as_f64(),
    }
}

/// What « À l'écoute » shows for this player state, or None when nothing plays.
pub fn fields(state: &Map<String, Value>, now: f64) -> Option<Fields> {
    let get = |k: &str| state.get(k).unwrap_or(&Value::Null);
    if !truthy(get("playing")) {
        return None;
    }
    if now - number(get("updated_at"))? > STALE_AFTER {
        return None;
    }
    let duration = number(get("duration"))?.max(0.0);
    let mut elapsed = number(get("current_time"))?.max(0.0);
    if duration > 0.0 {
        elapsed = elapsed.min(duration);
    }
    let or_kino = |k: &str| {
        Some(text(get(k)).trim().to_string())
            .filter(|t| !t.is_empty() && truthy(get(k)))
            .unwrap_or_else(|| "KINO".into())
    };
    let poster = text(get("poster"));
    Some(Fields {
        title: or_kino("title"),
        artist: or_kino("sub_title"),
        duration,
        elapsed,
        rate: if truthy(get("paused")) { 0.0 } else { 1.0 },
        poster: if poster.starts_with("https://") {
            poster
        } else {
            String::new()
        },
    })
}

/// Whether the system must be told again: something changed, or the time jumped.
pub fn needs_update(
    previous: Option<&Fields>,
    fields: Option<&Fields>,
    now: f64,
    sent_at: f64,
) -> bool {
    let (Some(p), Some(f)) = (previous, fields) else {
        return previous != fields;
    };
    if p.title != f.title
        || p.artist != f.artist
        || p.duration != f.duration
        || p.rate != f.rate
        || p.poster != f.poster
    {
        return true;
    }
    let predicted = p.elapsed + (now - sent_at).max(0.0) * p.rate;
    (predicted - f.elapsed).abs() > DRIFT
}

/// The remote command for a key or a button: toggle, play, pause, next,
/// previous, position. Play/pause is sent as an explicit play or pause from
/// the state before the press, so a web view that also toggles ends right.
pub fn command_for(
    kind: &str,
    state: &Map<String, Value>,
    now: f64,
    position: Option<f64>,
) -> Option<(String, Value)> {
    fields(state, now)?;
    let get = |k: &str| state.get(k).unwrap_or(&Value::Null);
    let paused = truthy(get("paused"));
    let series = get("media_type") == "series" || truthy(get("sub_title"));
    let command = |a: &str, v: Value| Some((a.to_string(), v));
    match kind {
        "toggle" => command(if paused { "play" } else { "pause" }, Value::Null),
        "play" | "pause" => command(kind, Value::Null),
        "next" if series => command("next_ep", Value::Null),
        "next" => command("seek_rel", json!(SKIP_SECONDS)),
        "previous" if series => command("prev_ep", Value::Null),
        "previous" => command("seek_rel", json!(-SKIP_SECONDS)),
        "position" => position.map(|p| ("seek".to_string(), json!(p.max(0.0)))),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state(v: Value) -> Map<String, Value> {
        v.as_object().cloned().unwrap()
    }

    #[test]
    fn shown_and_keys() {
        let s = state(
            json!({"playing": true, "paused": false, "current_time": 130.0, "duration": 120.0, "title": "", "sub_title": "S01E02",
                             "media_type": "series", "poster": "http://x", "updated_at": 100.0}),
        );
        let f = fields(&s, 102.0).unwrap();
        assert_eq!(
            (
                f.title.as_str(),
                f.artist.as_str(),
                f.elapsed,
                f.rate,
                f.poster.as_str()
            ),
            ("KINO", "S01E02", 120.0, 1.0, "")
        );
        assert!(fields(&s, 105.0).is_none(), "stale");
        assert_eq!(
            command_for("toggle", &s, 101.0, None),
            Some(("pause".into(), Value::Null))
        );
        assert_eq!(
            command_for("next", &s, 101.0, None),
            Some(("next_ep".into(), Value::Null))
        );
        let film = state(
            json!({"playing": true, "paused": true, "updated_at": 100.0, "media_type": "movie"}),
        );
        assert_eq!(
            command_for("previous", &film, 101.0, None),
            Some(("seek_rel".into(), json!(-10)))
        );
        assert_eq!(
            command_for("toggle", &film, 101.0, None),
            Some(("play".into(), Value::Null))
        );
        assert_eq!(
            command_for("position", &film, 101.0, Some(-3.0)),
            Some(("seek".into(), json!(0.0)))
        );
        let moved = Fields {
            elapsed: 125.0,
            ..f.clone()
        };
        assert!(!needs_update(
            Some(&f),
            Some(&Fields {
                elapsed: 121.5,
                ..f.clone()
            }),
            11.0,
            10.0
        ));
        assert!(needs_update(Some(&f), Some(&moved), 11.0, 10.0));
        assert!(needs_update(Some(&f), None, 0.0, 0.0) && !needs_update(None, None, 0.0, 0.0));
    }
}
