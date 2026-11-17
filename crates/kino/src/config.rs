//! The settings file (`~/.rd_cinehub_config.json`), read as the Python version
//! writes it.

use serde_json::{Map, Value};

use crate::paths;

pub const UI_LANGUAGES: [&str; 9] = ["fr", "en", "de", "es", "it", "pt", "ru", "zh", "ja"];

/// The settings as stored; an unreadable file reads as empty.
pub fn load_raw() -> Map<String, Value> {
    std::fs::read_to_string(paths::config_file())
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

/// The interface language chosen in Paramètres, when it is one KINO speaks.
pub fn ui_language() -> Option<String> {
    let lang = load_raw().get("ui_language")?.as_str()?.to_string();
    UI_LANGUAGES.contains(&lang.as_str()).then_some(lang)
}
