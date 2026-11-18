//! The settings code as it was before typing (maps of JSON values), kept for
//! the tests: the typed settings must read, check and merge exactly as it did.
#![allow(dead_code)]

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::{debrid, paths};

pub const UI_LANGUAGES: [&str; 9] = ["fr", "en", "de", "es", "it", "pt", "ru", "zh", "ja"];
const PLAYER_MODES: [&str; 3] = ["integrated", "kino", "external"];
const PREF_LANGS: [&str; 4] = ["vf", "vostfr", "vo", "multi"];
const QUALITIES: [&str; 6] = ["2160p", "1080p", "720p", "4k", "480p", "sd"];
const HDR_MODES: [&str; 6] = [
    "sdr_pref",
    "hdr_native",
    "hdr_boost",
    "hdr_only",
    "sdr_only",
    "auto",
];
const SOURCE_PRIORITIES: [&str; 2] = ["speed", "quality"];
const AUDIO_MODES: [&str; 6] = [
    "voice_boost",
    "standard",
    "normal",
    "default",
    "cinema",
    "none",
];
const LIST_ITEM_KEYS: [&str; 7] = [
    "id",
    "name",
    "type",
    "year",
    "poster",
    "imdbRating",
    "user_rating",
];
const MAX_CUSTOM_LISTS: usize = 20;
const MAX_CUSTOM_LIST_ITEMS: usize = 120;
pub const DISCORD_CLIENT_ID: &str = "1556492442415398973";
const LEGACY_DISCORD_IDS: [&str; 6] = [
    "631379801826918400",
    "645028677033132033",
    "968880591003082783",
    "938732156346314795",
    "608065709741965327",
    "926541425682829352",
];

static ALNUM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z0-9]+$").unwrap());
static LETTERBOXD_USER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[A-Za-z0-9_\-]+$").unwrap());
static TMDB_KEY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[A-Za-z0-9._\-]{20,400}$").unwrap());

pub fn default_download_dir() -> String {
    paths::home()
        .join("Downloads")
        .to_string_lossy()
        .into_owned()
}

/// Python's str() of a JSON value, trimmed and cut to `max` characters.
fn safe(value: &Value, max: usize) -> String {
    let raw = match value {
        Value::Null => return String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".into()
            } else {
                "False".into()
            }
        }
        other => other.to_string(),
    };
    raw.trim().chars().take(max).collect()
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

pub fn normalize_discord_id(id: &str) -> String {
    let clean = id.trim();
    if clean.is_empty() || LEGACY_DISCORD_IDS.contains(&clean) {
        DISCORD_CLIENT_ID.into()
    } else {
        clean.into()
    }
}

fn provider_tokens(raw: &Value) -> Map<String, Value> {
    let mut tokens = Map::new();
    for (provider, token) in raw.as_object().into_iter().flatten() {
        let name = provider
            .trim()
            .chars()
            .take(64)
            .collect::<String>()
            .to_lowercase();
        let value = safe(token, 2048);
        if debrid::is_provider(&name) && !value.is_empty() {
            tokens.insert(name, json!(value));
        }
    }
    tokens
}

fn download_dir(raw: &Value) -> String {
    let path = match raw {
        Value::Null | Value::Bool(false) => String::new(),
        other => safe(other, usize::MAX),
    };
    if path.is_empty() {
        return default_download_dir();
    }
    if path == "~" {
        return paths::home().to_string_lossy().into_owned();
    }
    match path.strip_prefix("~/") {
        Some(rest) => paths::home().join(rest).to_string_lossy().into_owned(),
        None => path,
    }
}

fn custom_lists(raw: &Value) -> Option<Vec<Value>> {
    let raw = raw.as_array()?;
    let mut lists = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for list in raw.iter().filter(|l| l.is_object()) {
        let id = safe(&list["id"], 160);
        let url = safe(&list["url"], 512);
        if id.is_empty() || seen.contains(&id) || !url.starts_with("https://") {
            continue;
        }
        let mut items = Vec::new();
        for item in list["items"].as_array().into_iter().flatten() {
            if !item.is_object() || safe(&item["id"], 32).is_empty() {
                continue;
            }
            let kept: Map<String, Value> = LIST_ITEM_KEYS
                .iter()
                .map(|k| (k.to_string(), json!(safe(&item[*k], 512))))
                .collect();
            items.push(Value::Object(kept));
            if items.len() >= MAX_CUSTOM_LIST_ITEMS {
                break;
            }
        }
        let updated = list["updated_at"]
            .as_i64()
            .or_else(|| list["updated_at"].as_f64().map(|f| f as i64))
            .unwrap_or(0)
            .max(0);
        seen.insert(id.clone());
        lists.push(json!({"id": id, "title": safe(&list["title"], 200), "description": safe(&list["description"], 1000), "url": url,
                          "count": items.len(), "items": items, "updated_at": updated}));
        if lists.len() >= MAX_CUSTOM_LISTS {
            break;
        }
    }
    // Same order of keys as the Python version.
    Some(
        lists
            .into_iter()
            .map(|l| {
                [
                    "id",
                    "title",
                    "description",
                    "url",
                    "items",
                    "count",
                    "updated_at",
                ]
                .iter()
                .map(|k| (k.to_string(), l[*k].clone()))
                .collect::<Map<_, _>>()
                .into()
            })
            .collect(),
    )
}

fn window_bounds(raw: &Value) -> Option<Value> {
    let get = |k: &str| -> Option<i64> {
        let v = raw.get(k)?;
        v.as_i64()
            .or_else(|| v.as_f64().map(|f| f as i64))
            .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
    };
    let (x, y, w, h) = (get("x")?, get("y")?, get("width")?, get("height")?);
    let ok = (640..=20000).contains(&w)
        && (420..=20000).contains(&h)
        && (-100_000..=100_000).contains(&x)
        && (-100_000..=100_000).contains(&y);
    ok.then(|| json!({"x": x, "y": y, "width": w, "height": h}))
}

fn is_local_url(url: &str) -> bool {
    let host = url
        .split("://")
        .nth(1)
        .unwrap_or("")
        .split(['/', ':'])
        .next()
        .unwrap_or("");
    match host.parse::<std::net::IpAddr>() {
        Ok(std::net::IpAddr::V4(ip)) => ip.is_private(),
        Ok(std::net::IpAddr::V6(ip)) => (ip.segments()[0] & 0xfe00) == 0xfc00,
        Err(_) => false,
    }
}

fn tv_device(value: &Value) -> Option<Value> {
    let location = safe(&value["location"], usize::MAX);
    let control = safe(&value["control_url"], usize::MAX);
    if !value.is_object() || !is_local_url(&location) || !is_local_url(&control) {
        return None;
    }
    let host = control
        .split("://")
        .nth(1)
        .unwrap_or("")
        .split(['/', ':'])
        .next()
        .unwrap_or("")
        .to_string();
    let name = safe(&value["name"], 120);
    Some(
        json!({"location": location.chars().take(300).collect::<String>(), "control_url": control.chars().take(300).collect::<String>(),
                "host": host, "name": if name.is_empty() { host.clone() } else { name }}),
    )
}

fn choice(data: &Map<String, Value>, key: &str, max: usize, allowed: &[&str]) -> Option<String> {
    let value = data.get(key).filter(|v| !v.is_null())?;
    let text = safe(value, max).to_lowercase();
    allowed.contains(&text.as_str()).then_some(text)
}

/// The settings a page may send, each checked; anything else is left out.
pub fn sanitize(data: &Value) -> Map<String, Value> {
    let Some(data) = data.as_object() else {
        return Map::new();
    };
    let mut out = Map::new();
    let providers: Vec<&str> = debrid::PROVIDERS.iter().map(|p| p.0).collect();
    if let Some(v) = choice(data, "debrid_provider", 64, &providers) {
        out.insert("debrid_provider".into(), json!(v));
    }
    if let Some(token) = data
        .get("rd_token")
        .filter(|v| !v.is_null())
        .map(|v| safe(v, 2048))
        .filter(|t| !t.is_empty())
    {
        out.insert("rd_token".into(), json!(token));
    }
    if data.contains_key("download_dir") {
        out.insert(
            "download_dir".into(),
            json!(download_dir(&data["download_dir"])),
        );
    }
    if let Some(v) = choice(data, "player_mode", 32, &PLAYER_MODES) {
        out.insert("player_mode".into(), json!(v));
    }
    let mut languages: Vec<&str> = UI_LANGUAGES.to_vec();
    languages.push("");
    if let Some(v) = choice(data, "ui_language", 8, &languages) {
        out.insert("ui_language".into(), json!(v));
    }
    if let Some(value) = data.get("pref_lang").filter(|v| !v.is_null()) {
        let mut lang = safe(value, 16).to_lowercase();
        if lang == "vost" {
            lang = "vostfr".into();
        }
        if PREF_LANGS.contains(&lang.as_str()) {
            out.insert("pref_lang".into(), json!(lang));
        }
    }
    for (key, max, allowed) in [
        ("pref_quality", 16, &QUALITIES[..]),
        ("hdr_mode", 32, &HDR_MODES[..]),
        ("source_priority", 16, &SOURCE_PRIORITIES[..]),
        ("audio_mode", 32, &AUDIO_MODES[..]),
    ] {
        if let Some(v) = choice(data, key, max, allowed) {
            out.insert(key.into(), json!(v));
        }
    }
    if data.contains_key("rd_retention_days") {
        let v = &data["rd_retention_days"];
        let days = if truthy(v) {
            v.as_i64()
                .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
                .unwrap_or(0)
        } else {
            0
        };
        if (0..=3650).contains(&days) {
            out.insert("rd_retention_days".into(), json!(days));
        }
    }
    if let Some(value) = data.get("discord_rpc") {
        match value {
            Value::String(s) => match s.trim().to_lowercase().as_str() {
                "1" | "true" | "yes" | "on" => _ = out.insert("discord_rpc".into(), json!(true)),
                "0" | "false" | "no" | "off" => _ = out.insert("discord_rpc".into(), json!(false)),
                _ => {}
            },
            other => _ = out.insert("discord_rpc".into(), json!(truthy(other))),
        }
    }
    for key in ["native_player", "auto_fr_subs", "episode_notifications"] {
        if let Some(Value::Bool(b)) = data.get(key) {
            out.insert(key.into(), json!(b));
        }
    }
    // Releases already announced by a notification.
    if let Some(Value::Array(list)) = data.get("notified_episodes") {
        let kept: Vec<Value> = list
            .iter()
            .filter_map(|v| v.as_str())
            .filter(|s| !s.trim().is_empty())
            .map(|s| json!(safe(&json!(s), 120)))
            .take(300)
            .collect();
        out.insert("notified_episodes".into(), Value::Array(kept));
    }
    if data.contains_key("discord_client_id") {
        let id = safe(&data["discord_client_id"], 128);
        if !id.is_empty() && ALNUM.is_match(&id) {
            out.insert("discord_client_id".into(), json!(normalize_discord_id(&id)));
        }
    }
    if data.contains_key("provider_tokens") {
        let tokens = provider_tokens(&data["provider_tokens"]);
        if !tokens.is_empty() {
            out.insert("provider_tokens".into(), Value::Object(tokens));
        }
    }
    if data.contains_key("letterboxd_user") {
        let user = safe(&data["letterboxd_user"], 64)
            .trim_start_matches('@')
            .to_string();
        if user.is_empty() || LETTERBOXD_USER.is_match(&user) {
            out.insert("letterboxd_user".into(), json!(user));
        }
    }
    if let Some(lists) = data.get("custom_lists").and_then(custom_lists) {
        out.insert("custom_lists".into(), Value::Array(lists));
    }
    if let Some(bounds) = data.get("window_bounds").and_then(window_bounds) {
        out.insert("window_bounds".into(), bounds);
    }
    if let Some(device) = data.get("tv_device") {
        out.insert("tv_device".into(), tv_device(device).unwrap_or(json!({})));
    }
    if data.contains_key("tmdb_key") {
        let key = safe(&data["tmdb_key"], 400);
        out.insert(
            "tmdb_key".into(),
            json!(if TMDB_KEY.is_match(&key) {
                key
            } else {
                String::new()
            }),
        );
    }
    out
}

fn default_player() -> &'static str {
    if cfg!(target_os = "macos") {
        "integrated"
    } else {
        "kino"
    }
}

/// The settings: defaults, then the old and the current file, each value checked.
/// `rd_token` is the active provider's key.
pub fn load_from(files: &[Value], env_token: Option<String>) -> Map<String, Value> {
    let mut cfg: Map<String, Value> = json!({
        "debrid_provider": "realdebrid", "provider_tokens": {}, "rd_token": "", "download_dir": default_download_dir(),
        "player_mode": default_player(), "pref_lang": "vf", "pref_quality": "4k", "hdr_mode": "sdr_pref", "audio_mode": "voice_boost",
        "rd_retention_days": 0, "discord_rpc": true, "discord_client_id": DISCORD_CLIENT_ID,
    })
    .as_object()
    .cloned()
    .unwrap_or_default();
    for loaded in files {
        cfg.extend(sanitize(loaded));
    }
    if !cfg["provider_tokens"].is_object() {
        cfg.insert("provider_tokens".into(), json!({}));
    }
    let active = cfg["debrid_provider"]
        .as_str()
        .filter(|p| debrid::is_provider(p))
        .unwrap_or("realdebrid")
        .to_string();
    cfg.insert("debrid_provider".into(), json!(active));
    let raw = cfg["rd_token"]
        .as_str()
        .filter(|t| !t.is_empty())
        .map(str::to_string)
        .or(env_token)
        .unwrap_or_default();
    if !raw.is_empty()
        && cfg["provider_tokens"]["realdebrid"]
            .as_str()
            .unwrap_or("")
            .is_empty()
    {
        cfg["provider_tokens"]
            .as_object_mut()
            .map(|t| t.insert("realdebrid".into(), json!(raw)));
    }
    let token = cfg["provider_tokens"][&active]
        .as_str()
        .unwrap_or("")
        .to_string();
    cfg.insert("rd_token".into(), json!(token));
    if !truthy(cfg.get("player_mode").unwrap_or(&Value::Null)) {
        cfg.insert("player_mode".into(), json!(default_player()));
    }
    cfg
}

/// What `save` wrote: the loaded settings with the checked changes (no file).
pub fn save_merge(mut cfg: Map<String, Value>, changes: &Value) -> Map<String, Value> {
    let updates = sanitize(changes);
    if updates.is_empty() {
        return cfg;
    }
    let mut tokens = cfg["provider_tokens"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    let target = updates
        .get("debrid_provider")
        .or(cfg.get("debrid_provider"))
        .and_then(Value::as_str)
        .filter(|p| debrid::is_provider(p))
        .unwrap_or("realdebrid")
        .to_string();
    if let Some(token) = updates
        .get("rd_token")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|t| !t.is_empty())
    {
        tokens.insert(target.clone(), json!(token));
    }
    cfg.extend(updates.clone());
    if let Some(mode) = updates
        .get("player_mode")
        .and_then(Value::as_str)
        .filter(|m| !m.is_empty())
    {
        cfg.insert("player_mode".into(), json!(mode.trim().to_lowercase()));
    } else if !truthy(cfg.get("player_mode").unwrap_or(&Value::Null)) {
        cfg.insert("player_mode".into(), json!(default_player()));
    }
    cfg.insert("debrid_provider".into(), json!(target));
    if let Some(Value::Object(more)) = updates.get("provider_tokens") {
        tokens.extend(more.clone());
    }
    let token = tokens
        .get(&target)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    cfg.insert("provider_tokens".into(), Value::Object(tokens));
    cfg.insert("rd_token".into(), json!(token));
    cfg
}
