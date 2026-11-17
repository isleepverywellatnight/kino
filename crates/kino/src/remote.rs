//! The phone remote: the player's state (written by the page's player, read by
//! the phone, the TV and the media keys) and the commands waiting for it.

use std::sync::Mutex;

use serde_json::{Map, Value, json};

/// A tap older than this is dropped: one made before playback must not fire later.
const COMMAND_MAX_AGE: f64 = 5.0;
const KEEP: usize = 20;

pub struct Remote {
    state: Mutex<Map<String, Value>>,
    commands: Mutex<Vec<Value>>,
}

impl Default for Remote {
    fn default() -> Self {
        let state = json!({
            "playing": false, "paused": true, "current_time": 0.0, "duration": 0.0, "volume": 1.0,
            "title": "Aucune lecture en cours", "sub_title": "", "media_type": "movie",
            // The title's poster, for « À l'écoute » on macOS.
            "poster": "", "updated_at": crate::db::now(),
        });
        Self {
            state: Mutex::new(state.as_object().cloned().unwrap_or_default()),
            commands: Mutex::new(vec![]),
        }
    }
}

fn clean(key: &str, value: &Value) -> Option<Value> {
    match key {
        "playing" | "paused" => Some(json!(crate::play::truthy(value))),
        "current_time" | "duration" | "volume" => {
            let number = match value {
                Value::Number(n) => n.as_f64(),
                Value::Bool(b) => Some(f64::from(u8::from(*b))),
                Value::String(s) => s.trim().parse::<f64>().ok(),
                _ => None,
            }?;
            (number.is_finite() && number >= 0.0).then(|| json!(number))
        }
        "poster" => {
            let text = crate::play::text(value);
            Some(json!(
                if text.starts_with("https://") && text.chars().count() <= 1000 {
                    text
                } else {
                    String::new()
                }
            ))
        }
        _ => {
            let text = if crate::play::truthy(value) {
                crate::play::text(value)
            } else {
                String::new()
            };
            Some(json!(text.chars().take(200).collect::<String>()))
        }
    }
}

impl Remote {
    /// The player's state alone.
    pub fn state(&self) -> Map<String, Value> {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    /// The state with the address the phone opens.
    pub fn state_for_phone(&self, port: u16) -> Map<String, Value> {
        let mut state = self.state();
        let ip = local_ip();
        state.insert("url".into(), json!(format!("http://{ip}:{port}/remote")));
        state.insert("local_ip".into(), json!(ip));
        state
    }

    /// Records the page player's state; `playing` means a playback session is open.
    pub fn update(&self, new: &Value) {
        let Some(new) = new.as_object() else { return };
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        for (raw, value) in new {
            let key = match raw.as_str() {
                "currentTime" => "current_time",
                "subTitle" => "sub_title",
                "mediaType" => "media_type",
                other => other,
            };
            if key == "updated_at" || !state.contains_key(key) {
                continue;
            }
            if let Some(value) = clean(key, value) {
                state.insert(key.into(), value);
            }
        }
        state.insert("updated_at".into(), json!(crate::db::now()));
    }

    pub fn push(&self, action: &Value, value: &Value) {
        let mut commands = self.commands.lock().unwrap_or_else(|e| e.into_inner());
        commands.push(json!({"action": action, "value": value, "timestamp": crate::db::now()}));
        if commands.len() > KEEP {
            commands.remove(0);
        }
    }

    /// The fresh commands, taken from the queue.
    pub fn pop(&self) -> Vec<Value> {
        let now = crate::db::now();
        std::mem::take(&mut *self.commands.lock().unwrap_or_else(|e| e.into_inner()))
            .into_iter()
            .filter(|c| now - c["timestamp"].as_f64().unwrap_or(0.0) <= COMMAND_MAX_AGE)
            .collect()
    }
}

/// This computer's address on the local network (Wi-Fi or Ethernet).
pub fn local_ip() -> String {
    // No packet is sent: connecting a UDP socket only picks the outgoing interface.
    std::net::UdpSocket::bind("0.0.0.0:0")
        .and_then(|s| s.connect("8.8.8.8:80").map(|_| s))
        .and_then(|s| s.local_addr())
        .map(|a| a.ip().to_string())
        .ok()
        .filter(|ip| !ip.starts_with("127.") && ip != "0.0.0.0")
        .unwrap_or_else(|| "127.0.0.1".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_and_commands() {
        let remote = Remote::default();
        remote.update(&json!({"currentTime": "12.5", "title": "Dune", "poster": "file:///etc/passwd", "duration": -1, "volume": "x", "other": 1, "playing": 1}));
        let s = remote.state();
        assert_eq!(
            (
                s["current_time"].clone(),
                s["title"].clone(),
                s["poster"].clone()
            ),
            (json!(12.5), json!("Dune"), json!(""))
        );
        assert_eq!(
            (
                s["duration"].clone(),
                s["volume"].clone(),
                s["playing"].clone()
            ),
            (json!(0.0), json!(1.0), json!(true))
        );
        assert!(!s.contains_key("other"));
        for i in 0..25 {
            remote.push(&json!("seek"), &json!(i));
        }
        let popped = remote.pop();
        assert_eq!(popped.len(), 20);
        assert_eq!(popped[0]["value"], 5);
        assert!(remote.pop().is_empty());
    }
}
