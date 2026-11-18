//! The settings file (`~/.rd_cinehub_config.json`), typed. Values are read
//! leniently, as KINO always did: defaults, then each file, each value checked
//! (a wrong one is left out, `"30"` reads as 30, `"off"` as false). Keys KINO
//! does not know, set by hand in the file (`mpv_path`), are kept on save.

use std::collections::BTreeMap;
use std::sync::LazyLock;

use regex::Regex;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::{Map, Value};

use crate::paths;

pub const UI_LANGUAGES: [&str; 9] = ["fr", "en", "de", "es", "it", "pt", "ru", "zh", "ja"];
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

/// A setting with a fixed set of values, each written as KINO always wrote it
/// (the stream cache keys and the page use these words).
macro_rules! choice {
    ($(#[$doc:meta])* $name:ident (default $default:ident) { $($body:tt)+ }) => {
        choice!($(#[$doc])* $name { $($body)+ });

        impl Default for $name {
            fn default() -> Self {
                Self::$default
            }
        }
    };
    ($(#[$doc:meta])* $name:ident { $($variant:ident = $text:literal),+ $(,)? }) => {
        $(#[$doc])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
        pub enum $name {
            $(#[serde(rename = $text)] $variant),+
        }

        impl $name {
            pub const ALL: &'static [$name] = &[$($name::$variant),+];

            pub fn as_str(self) -> &'static str {
                match self {
                    $($name::$variant => $text),+
                }
            }

            /// The value for its word, exactly as stored.
            pub fn parse(text: &str) -> Option<Self> {
                match text {
                    $($text => Some($name::$variant),)+
                    _ => None,
                }
            }
        }

        impl std::fmt::Display for $name {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str(self.as_str())
            }
        }
    };
}

choice!(
    /// The debrid service whose key is used.
    DebridProvider (default RealDebrid) {
        RealDebrid = "realdebrid",
        AllDebrid = "alldebrid",
        TorBox = "torbox",
        Premiumize = "premiumize",
        DebridLink = "debridlink",
        MegaDebrid = "megadebrid",
    }
);
choice!(
    /// Where a film plays: the page's player, KINO's own window, or mpv/VLC.
    PlayerMode {
        Integrated = "integrated",
        Kino = "kino",
        External = "external",
    }
);
choice!(
    /// The audio language wanted: French, original with French subtitles, original, several.
    PrefLang (default Vf) {
        Vf = "vf",
        Vostfr = "vostfr",
        Vo = "vo",
        Multi = "multi",
    }
);
choice!(
    /// The resolution wanted (« 4k » and « 2160p » are both kept as written).
    Quality (default Q4k) {
        Q2160p = "2160p",
        Q1080p = "1080p",
        Q720p = "720p",
        Q4k = "4k",
        Q480p = "480p",
        Sd = "sd",
    }
);
choice!(
    /// How HDR sources are weighed and shown.
    HdrMode (default SdrPref) {
        SdrPref = "sdr_pref",
        HdrNative = "hdr_native",
        HdrBoost = "hdr_boost",
        HdrOnly = "hdr_only",
        SdrOnly = "sdr_only",
        Auto = "auto",
    }
);
choice!(
    /// What sorts sources first: the quickest to start, or the best picture.
    SourcePriority (default Speed) {
        Speed = "speed",
        Quality = "quality",
    }
);
choice!(
    /// The external player's audio treatment.
    AudioMode (default VoiceBoost) {
        VoiceBoost = "voice_boost",
        Standard = "standard",
        Normal = "normal",
        SystemDefault = "default",
        Cinema = "cinema",
        Plain = "none",
    }
);

impl Default for PlayerMode {
    /// KINO's player on macOS (the page's), its own window elsewhere.
    fn default() -> Self {
        if cfg!(target_os = "macos") {
            Self::Integrated
        } else {
            Self::Kino
        }
    }
}

/// A title kept in a list imported from Letterboxd.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct ListItem {
    pub id: String,
    pub name: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub year: String,
    pub poster: String,
    #[serde(rename = "imdbRating")]
    pub imdb_rating: String,
    pub user_rating: String,
}

/// A list imported from Letterboxd.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct CustomList {
    pub id: String,
    pub title: String,
    pub description: String,
    pub url: String,
    pub items: Vec<ListItem>,
    pub count: usize,
    pub updated_at: i64,
}

/// Where the window was, to open there again.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct WindowBounds {
    pub x: i64,
    pub y: i64,
    pub width: i64,
    pub height: i64,
}

/// The TV chosen for « Lire sur la TV »; empty once forgotten.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TvDevice {
    pub location: String,
    pub control_url: String,
    pub host: String,
    pub name: String,
}

impl TvDevice {
    pub fn is_empty(&self) -> bool {
        self.location.is_empty() && self.control_url.is_empty()
    }
}

impl Serialize for TvDevice {
    /// Forgotten, it is written `{}` (as before), else with its four fields.
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut map = serializer.serialize_map(None)?;
        if !self.is_empty() {
            map.serialize_entry("location", &self.location)?;
            map.serialize_entry("control_url", &self.control_url)?;
            map.serialize_entry("host", &self.host)?;
            map.serialize_entry("name", &self.name)?;
        }
        map.end()
    }
}

/// The settings. `rd_token` is the active provider's key.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct AppConfig {
    pub debrid_provider: DebridProvider,
    pub provider_tokens: BTreeMap<DebridProvider, String>,
    pub rd_token: String,
    pub download_dir: String,
    pub player_mode: PlayerMode,
    pub pref_lang: PrefLang,
    pub pref_quality: Quality,
    pub hdr_mode: HdrMode,
    pub audio_mode: AudioMode,
    pub rd_retention_days: i64,
    pub discord_rpc: bool,
    pub discord_client_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ui_language: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_priority: Option<SourcePriority>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub native_player: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub auto_fr_subs: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub episode_notifications: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub notified_episodes: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub letterboxd_user: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub custom_lists: Option<Vec<CustomList>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub window_bounds: Option<WindowBounds>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tv_device: Option<TvDevice>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tmdb_key: Option<String>,
    /// Keys of the file KINO does not know, kept as they are.
    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

/// The names of the settings KINO checks: anything else in the file is `extra`.
const KNOWN: [&str; 23] = [
    "debrid_provider",
    "provider_tokens",
    "rd_token",
    "download_dir",
    "player_mode",
    "pref_lang",
    "pref_quality",
    "hdr_mode",
    "audio_mode",
    "rd_retention_days",
    "discord_rpc",
    "discord_client_id",
    "ui_language",
    "source_priority",
    "native_player",
    "auto_fr_subs",
    "episode_notifications",
    "notified_episodes",
    "letterboxd_user",
    "custom_lists",
    "window_bounds",
    "tv_device",
    "tmdb_key",
];

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            debrid_provider: DebridProvider::default(),
            provider_tokens: BTreeMap::new(),
            rd_token: String::new(),
            download_dir: default_download_dir(),
            player_mode: PlayerMode::default(),
            pref_lang: PrefLang::default(),
            pref_quality: Quality::default(),
            hdr_mode: HdrMode::default(),
            audio_mode: AudioMode::default(),
            rd_retention_days: 0,
            discord_rpc: true,
            discord_client_id: DISCORD_CLIENT_ID.into(),
            ui_language: None,
            source_priority: None,
            native_player: None,
            auto_fr_subs: None,
            episode_notifications: None,
            notified_episodes: None,
            letterboxd_user: None,
            custom_lists: None,
            window_bounds: None,
            tv_device: None,
            tmdb_key: None,
            extra: Map::new(),
        }
    }
}

impl<'de> Deserialize<'de> for AppConfig {
    /// Read as a settings file is: defaults, then each value checked.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Ok(Self::from_files(&[Value::deserialize(deserializer)?], None))
    }
}

impl AppConfig {
    /// Defaults, then each file in turn (the old one, then the current one),
    /// then the provider's key put in place. `env_token` is a Real-Debrid key
    /// given outside the files.
    pub fn from_files(files: &[Value], env_token: Option<String>) -> Self {
        let mut cfg = Self::default();
        for file in files {
            cfg.apply(&ConfigUpdate::from_value(file));
        }
        if let Some(last) = files.last().and_then(Value::as_object) {
            cfg.extra = last
                .iter()
                .filter(|(k, _)| !KNOWN.contains(&k.as_str()))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
        }
        let raw = Some(cfg.rd_token.clone())
            .filter(|t| !t.is_empty())
            .or(env_token)
            .unwrap_or_default();
        if !raw.is_empty() && cfg.token(DebridProvider::RealDebrid).is_empty() {
            cfg.provider_tokens.insert(DebridProvider::RealDebrid, raw);
        }
        cfg.rd_token = cfg.token(cfg.debrid_provider).to_string();
        cfg
    }

    /// A provider's key; empty when there is none.
    pub fn token(&self, provider: DebridProvider) -> &str {
        self.provider_tokens
            .get(&provider)
            .map_or("", String::as_str)
    }

    /// The checked values replace the current ones.
    fn apply(&mut self, u: &ConfigUpdate) {
        macro_rules! set {
            ($($field:ident),+) => {
                $(if let Some(v) = &u.$field { self.$field = v.clone(); })+
            };
        }
        macro_rules! set_option {
            ($($field:ident),+) => {
                $(if let Some(v) = &u.$field { self.$field = Some(v.clone()); })+
            };
        }
        set!(
            debrid_provider,
            provider_tokens,
            rd_token,
            download_dir,
            player_mode,
            pref_lang,
            pref_quality,
            hdr_mode,
            audio_mode,
            rd_retention_days,
            discord_rpc,
            discord_client_id
        );
        set_option!(
            ui_language,
            source_priority,
            native_player,
            auto_fr_subs,
            episode_notifications,
            notified_episodes,
            letterboxd_user,
            custom_lists,
            window_bounds,
            tv_device,
            tmdb_key
        );
    }

    /// The settings with the checked changes: a new key goes to the provider
    /// chosen (the new one, or the current one), and `rd_token` follows it.
    pub fn merged(mut self, updates: &ConfigUpdate) -> Self {
        if updates.is_empty() {
            return self;
        }
        let mut tokens = self.provider_tokens.clone();
        let target = updates.debrid_provider.unwrap_or(self.debrid_provider);
        if let Some(token) = updates
            .rd_token
            .as_deref()
            .map(str::trim)
            .filter(|t| !t.is_empty())
        {
            tokens.insert(target, token.to_string());
        }
        self.apply(updates);
        self.debrid_provider = target;
        if let Some(more) = &updates.provider_tokens {
            tokens.extend(more.iter().map(|(k, v)| (*k, v.clone())));
        }
        self.provider_tokens = tokens;
        self.rd_token = self.token(target).to_string();
        self
    }

    /// Sources sorted by picture first, when chosen so.
    pub fn quality_first(&self) -> bool {
        self.source_priority == Some(SourcePriority::Quality)
    }

    /// The source priority, « speed » unless « quality » was chosen.
    pub fn priority(&self) -> SourcePriority {
        self.source_priority.unwrap_or_default()
    }

    /// The user's TMDB key; empty when none.
    pub fn tmdb_key(&self) -> &str {
        self.tmdb_key.as_deref().unwrap_or("")
    }

    /// The TV chosen, unless forgotten.
    pub fn tv(&self) -> Option<&TvDevice> {
        self.tv_device.as_ref().filter(|d| !d.is_empty())
    }
}

/// Changes to the settings, each checked: only those present and valid are set.
/// A forgotten TV is `Some` with an empty device.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct ConfigUpdate {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub debrid_provider: Option<DebridProvider>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rd_token: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub download_dir: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub player_mode: Option<PlayerMode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ui_language: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pref_lang: Option<PrefLang>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pref_quality: Option<Quality>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hdr_mode: Option<HdrMode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_priority: Option<SourcePriority>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub audio_mode: Option<AudioMode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rd_retention_days: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub discord_rpc: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub native_player: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub auto_fr_subs: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub episode_notifications: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub notified_episodes: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub discord_client_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider_tokens: Option<BTreeMap<DebridProvider, String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub letterboxd_user: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub custom_lists: Option<Vec<CustomList>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub window_bounds: Option<WindowBounds>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tv_device: Option<TvDevice>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tmdb_key: Option<String>,
}

impl<'de> Deserialize<'de> for ConfigUpdate {
    /// Whatever a page sends: each value checked, anything else left out.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Ok(Self::from_value(&Value::deserialize(deserializer)?))
    }
}

pub fn default_download_dir() -> String {
    paths::home()
        .join("Downloads")
        .to_string_lossy()
        .into_owned()
}

/// A JSON value as text (true as « True », as KINO always read it), trimmed
/// and cut to `max` characters.
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

fn provider_tokens(raw: &Value) -> BTreeMap<DebridProvider, String> {
    let mut tokens = BTreeMap::new();
    for (provider, token) in raw.as_object().into_iter().flatten() {
        let name = provider
            .trim()
            .chars()
            .take(64)
            .collect::<String>()
            .to_lowercase();
        let value = safe(token, 2048);
        if let Some(provider) = DebridProvider::parse(&name)
            && !value.is_empty()
        {
            tokens.insert(provider, value);
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

fn custom_lists(raw: &Value) -> Option<Vec<CustomList>> {
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
            let field = |k: &str| safe(&item[k], 512);
            items.push(ListItem {
                id: field("id"),
                name: field("name"),
                kind: field("type"),
                year: field("year"),
                poster: field("poster"),
                imdb_rating: field("imdbRating"),
                user_rating: field("user_rating"),
            });
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
        lists.push(CustomList {
            id,
            title: safe(&list["title"], 200),
            description: safe(&list["description"], 1000),
            url,
            count: items.len(),
            items,
            updated_at: updated,
        });
        if lists.len() >= MAX_CUSTOM_LISTS {
            break;
        }
    }
    Some(lists)
}

fn window_bounds(raw: &Value) -> Option<WindowBounds> {
    let get = |k: &str| -> Option<i64> {
        let v = raw.get(k)?;
        v.as_i64()
            .or_else(|| v.as_f64().map(|f| f as i64))
            .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
    };
    let (x, y, width, height) = (get("x")?, get("y")?, get("width")?, get("height")?);
    let ok = (640..=20000).contains(&width)
        && (420..=20000).contains(&height)
        && (-100_000..=100_000).contains(&x)
        && (-100_000..=100_000).contains(&y);
    ok.then_some(WindowBounds {
        x,
        y,
        width,
        height,
    })
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

fn tv_device(value: &Value) -> Option<TvDevice> {
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
    Some(TvDevice {
        location: location.chars().take(300).collect(),
        control_url: control.chars().take(300).collect(),
        name: if name.is_empty() { host.clone() } else { name },
        host,
    })
}

/// A value present (not null), as lowercase text cut to `max`.
fn word(data: &Map<String, Value>, key: &str, max: usize) -> Option<String> {
    let value = data.get(key).filter(|v| !v.is_null())?;
    Some(safe(value, max).to_lowercase())
}

impl ConfigUpdate {
    /// The settings a page (or a file) gives, each checked.
    pub fn from_value(data: &Value) -> Self {
        let Some(data) = data.as_object() else {
            return Self::default();
        };
        let mut out = Self {
            debrid_provider: word(data, "debrid_provider", 64)
                .and_then(|w| DebridProvider::parse(&w)),
            rd_token: data
                .get("rd_token")
                .filter(|v| !v.is_null())
                .map(|v| safe(v, 2048))
                .filter(|t| !t.is_empty()),
            download_dir: data.get("download_dir").map(download_dir),
            player_mode: word(data, "player_mode", 32).and_then(|w| PlayerMode::parse(&w)),
            ui_language: word(data, "ui_language", 8)
                .filter(|w| w.is_empty() || UI_LANGUAGES.contains(&w.as_str())),
            pref_lang: word(data, "pref_lang", 16)
                .map(|w| if w == "vost" { "vostfr".into() } else { w })
                .and_then(|w| PrefLang::parse(&w)),
            pref_quality: word(data, "pref_quality", 16).and_then(|w| Quality::parse(&w)),
            hdr_mode: word(data, "hdr_mode", 32).and_then(|w| HdrMode::parse(&w)),
            source_priority: word(data, "source_priority", 16)
                .and_then(|w| SourcePriority::parse(&w)),
            audio_mode: word(data, "audio_mode", 32).and_then(|w| AudioMode::parse(&w)),
            ..Self::default()
        };
        if let Some(v) = data.get("rd_retention_days") {
            let days = if truthy(v) {
                v.as_i64()
                    .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
                    .unwrap_or(0)
            } else {
                0
            };
            out.rd_retention_days = (0..=3650).contains(&days).then_some(days);
        }
        out.discord_rpc = match data.get("discord_rpc") {
            Some(Value::String(s)) => match s.trim().to_lowercase().as_str() {
                "1" | "true" | "yes" | "on" => Some(true),
                "0" | "false" | "no" | "off" => Some(false),
                _ => None,
            },
            Some(other) => Some(truthy(other)),
            None => None,
        };
        let flag = |k: &str| data.get(k).and_then(Value::as_bool);
        out.native_player = flag("native_player");
        out.auto_fr_subs = flag("auto_fr_subs");
        out.episode_notifications = flag("episode_notifications");
        // Releases already announced by a notification.
        if let Some(Value::Array(list)) = data.get("notified_episodes") {
            out.notified_episodes = Some(
                list.iter()
                    .filter_map(Value::as_str)
                    .filter(|s| !s.trim().is_empty())
                    .map(|s| safe(&Value::from(s), 120))
                    .take(300)
                    .collect(),
            );
        }
        if let Some(v) = data.get("discord_client_id") {
            let id = safe(v, 128);
            if !id.is_empty() && ALNUM.is_match(&id) {
                out.discord_client_id = Some(normalize_discord_id(&id));
            }
        }
        if let Some(v) = data.get("provider_tokens") {
            out.provider_tokens = Some(provider_tokens(v)).filter(|t| !t.is_empty());
        }
        if let Some(v) = data.get("letterboxd_user") {
            let user = safe(v, 64).trim_start_matches('@').to_string();
            if user.is_empty() || LETTERBOXD_USER.is_match(&user) {
                out.letterboxd_user = Some(user);
            }
        }
        out.custom_lists = data.get("custom_lists").and_then(custom_lists);
        out.window_bounds = data.get("window_bounds").and_then(window_bounds);
        out.tv_device = data
            .get("tv_device")
            .map(|d| tv_device(d).unwrap_or_default());
        if let Some(v) = data.get("tmdb_key") {
            let key = safe(v, 400);
            out.tmdb_key = Some(if TMDB_KEY.is_match(&key) {
                key
            } else {
                String::new()
            });
        }
        out
    }

    pub fn is_empty(&self) -> bool {
        *self == Self::default()
    }
}

fn read_file(path: &std::path::Path) -> Option<Value> {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .filter(Value::is_object)
}

/// The settings: defaults, then the old and the current file, each value checked.
pub fn load() -> AppConfig {
    let files: Vec<Value> = [
        paths::home().join(".rd_cli_config.json"),
        paths::config_file(),
    ]
    .iter()
    .map(|p| read_file(p).unwrap_or(Value::Object(Map::new())))
    .collect();
    AppConfig::from_files(&files, std::env::var("RD_API_TOKEN").ok())
}

/// Saves the checked changes; the file stays readable by the user only.
pub fn save(changes: &Value) -> std::io::Result<AppConfig> {
    save_update(&ConfigUpdate::from_value(changes))
}

/// Saves changes already checked.
pub fn save_update(updates: &ConfigUpdate) -> std::io::Result<AppConfig> {
    let current = load();
    if updates.is_empty() {
        return Ok(current);
    }
    let cfg = current.merged(updates);
    let path = paths::config_file();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(
        &path,
        serde_json::to_string_pretty(&cfg).unwrap_or_default(),
    )?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(cfg)
}

/// The settings as stored; an unreadable file reads as empty.
pub fn load_raw() -> Map<String, Value> {
    read_file(&paths::config_file())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

/// The interface language chosen in Paramètres, when it is one KINO speaks.
pub fn ui_language() -> Option<String> {
    let lang = load_raw().get("ui_language")?.as_str()?.to_string();
    UI_LANGUAGES.contains(&lang.as_str()).then_some(lang)
}

#[cfg(test)]
#[path = "config_legacy.rs"]
mod legacy;

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn values_are_checked() {
        let clean = ConfigUpdate::from_value(&json!({
            "debrid_provider": " TorBox ", "rd_token": "  abc  ", "player_mode": "nope", "pref_lang": "VOST", "pref_quality": "4K",
            "rd_retention_days": "30", "discord_rpc": "off", "native_player": "yes", "discord_client_id": "645028677033132033",
            "provider_tokens": {"alldebrid": "k", "evil": "x", "torbox": ""}, "letterboxd_user": "@kaiser_1", "tmdb_key": "short",
            "window_bounds": {"x": 10, "y": 20, "width": 300, "height": 900}, "tv_device": {"location": "http://8.8.8.8/", "control_url": "http://8.8.8.8/c"},
            "custom_lists": [{"id": "l1", "url": "https://letterboxd.com/x/list/y/", "items": [{"id": "tt1", "name": "A", "extra": 1}]}, {"id": "l2", "url": "http://x"}],
            "unknown": true,
        }));
        assert_eq!(clean.debrid_provider, Some(DebridProvider::TorBox));
        assert_eq!(clean.rd_token.as_deref(), Some("abc"));
        assert_eq!(clean.player_mode, None);
        assert_eq!(clean.native_player, None);
        assert_eq!(clean.pref_lang, Some(PrefLang::Vostfr));
        assert_eq!(clean.pref_quality, Some(Quality::Q4k));
        assert_eq!(clean.rd_retention_days, Some(30));
        assert_eq!(clean.discord_rpc, Some(false));
        assert_eq!(
            clean.discord_client_id.as_deref(),
            Some(DISCORD_CLIENT_ID),
            "an old preset becomes KINO's own"
        );
        assert_eq!(
            clean.provider_tokens,
            Some(BTreeMap::from([(DebridProvider::AllDebrid, "k".into())]))
        );
        assert_eq!(clean.letterboxd_user.as_deref(), Some("kaiser_1"));
        assert_eq!(clean.tmdb_key.as_deref(), Some(""));
        assert_eq!(clean.window_bounds, None, "too narrow");
        assert_eq!(
            clean.tv_device,
            Some(TvDevice::default()),
            "a TV outside the local network is forgotten"
        );
        let lists = clean.custom_lists.unwrap();
        assert_eq!(lists.len(), 1);
        assert_eq!(
            serde_json::to_value(&lists[0].items[0]).unwrap(),
            json!({"id": "tt1", "name": "A", "type": "", "year": "", "poster": "", "imdbRating": "", "user_rating": ""})
        );
        let device = ConfigUpdate::from_value(
            &json!({"tv_device": {"location": "http://192.168.1.20:9197/d.xml", "control_url": "http://192.168.1.20:9197/c"}}),
        );
        assert_eq!(device.tv_device.unwrap().name, "192.168.1.20");
    }

    #[test]
    fn every_provider_is_a_debrid_service() {
        let mut typed: Vec<&str> = DebridProvider::ALL.iter().map(|p| p.as_str()).collect();
        let mut known: Vec<&str> = crate::debrid::PROVIDERS.iter().map(|p| p.0).collect();
        typed.sort();
        known.sort();
        assert_eq!(typed, known);
    }

    #[test]
    fn unknown_keys_of_the_file_are_kept() {
        let cfg = AppConfig::from_files(
            &[
                json!({}),
                json!({"mpv_path": "/opt/mpv", "pref_lang": "vo"}),
            ],
            None,
        );
        assert_eq!(cfg.extra.get("mpv_path"), Some(&json!("/opt/mpv")));
        assert_eq!(cfg.pref_lang, PrefLang::Vo);
        let written = serde_json::to_value(
            cfg.merged(&ConfigUpdate::from_value(&json!({"pref_lang": "vf"}))),
        )
        .unwrap();
        assert_eq!(written["mpv_path"], "/opt/mpv", "a save keeps it");
        assert_eq!(written["pref_lang"], "vf");
        let from_page = ConfigUpdate::from_value(&json!({"mpv_path": "/tmp/evil"}));
        assert!(from_page.is_empty(), "a page cannot set it");
    }

    /// Values of every kind, valid and not, for each setting.
    fn samples(key: &str) -> Vec<Value> {
        let mut values = vec![
            Value::Null,
            json!(true),
            json!(false),
            json!(0),
            json!(1),
            json!(-5),
            json!(30),
            json!(3.5),
            json!(""),
            json!(" "),
            json!("abc"),
            json!("30"),
            json!("4000"),
            json!(" Off "),
            json!("yes"),
            json!([]),
            json!({}),
            json!([1, "a"]),
            json!({"x": 1}),
        ];
        let words: &[&str] = match key {
            "debrid_provider" => &["realdebrid", " TorBox ", "ALLDEBRID", "megadebrid", "evil"],
            "player_mode" => &["kino", "External", "integrated", " kino "],
            "ui_language" => &["fr", "EN", "ja", "xx"],
            "pref_lang" => &["vf", "VOST", "vostfr", "Vo", "multi"],
            "pref_quality" => &["4K", "2160p", "1080P", "sd", "720p", "480p"],
            "hdr_mode" => &[
                "sdr_pref",
                "HDR_NATIVE",
                "hdr_boost",
                "auto",
                "hdr_only",
                "sdr_only",
            ],
            "source_priority" => &["speed", "Quality"],
            "audio_mode" => &[
                "voice_boost",
                "none",
                "default",
                "cinema",
                "Standard",
                "normal",
            ],
            "discord_client_id" => &[
                "645028677033132033",
                "12345",
                "abc def",
                "1556492442415398973",
            ],
            "letterboxd_user" => &["@kaiser_1", "bad user", "ok-name"],
            "tmdb_key" => &[
                "0123456789abcdef0123456789",
                "short",
                "eyJhbGciOiJIUzI1NiJ9.payload.sig",
            ],
            "download_dir" => &["~", "~/Films", "/Volumes/Disk", "  "],
            "rd_token" => &["  abc  ", "XYZ"],
            _ => &[],
        };
        values.extend(words.iter().map(|w| json!(w)));
        match key {
            "provider_tokens" => {
                values.push(json!({"alldebrid": "k", "evil": "x", "torbox": "", " TorBox ": "t", "realdebrid": 5}));
                values.push(json!({"realdebrid": "rd", "premiumize": true}));
            }
            "notified_episodes" => values.push(json!(["tt1:1:2", "", " ", 5, "tt2:1:1"])),
            "window_bounds" => {
                values.push(json!({"x": 10, "y": 20, "width": 1200, "height": 900}));
                values.push(json!({"x": "10", "y": 20.7, "width": "800", "height": 500}));
                values.push(json!({"x": 10, "y": 20, "width": 300, "height": 900}));
            }
            "tv_device" => {
                values.push(json!({"location": "http://192.168.1.20:9197/d.xml", "control_url": "http://192.168.1.20:9197/c", "name": "Salon"}));
                values.push(
                    json!({"location": "http://8.8.8.8/", "control_url": "http://8.8.8.8/c"}),
                );
                values.push(
                    json!({"location": "http://[fd00::1]/d", "control_url": "http://[fd00::1]/c"}),
                );
            }
            "custom_lists" => {
                values.push(json!([{"id": "l1", "title": "T", "url": "https://letterboxd.com/x/list/y/", "updated_at": 12.9,
                    "items": [{"id": "tt1", "name": "A", "extra": 1}, {"id": ""}, 5, {"id": "tt2", "year": 1999}]},
                    {"id": "l1", "url": "https://dup"}, {"id": "l2", "url": "http://x"}, "x"]));
            }
            _ => {}
        }
        values
    }

    /// Settings objects mixing keys and values, the same each run.
    fn corpus() -> Vec<Value> {
        let keys = [
            "debrid_provider",
            "rd_token",
            "download_dir",
            "player_mode",
            "ui_language",
            "pref_lang",
            "pref_quality",
            "hdr_mode",
            "source_priority",
            "audio_mode",
            "rd_retention_days",
            "discord_rpc",
            "native_player",
            "auto_fr_subs",
            "episode_notifications",
            "notified_episodes",
            "discord_client_id",
            "provider_tokens",
            "letterboxd_user",
            "custom_lists",
            "window_bounds",
            "tv_device",
            "tmdb_key",
            "unknown_key",
        ];
        let mut out = vec![json!({}), json!(null), json!([1]), json!("x")];
        for key in keys {
            for value in samples(key) {
                out.push(json!({ key: value }));
            }
        }
        let mut seed: u64 = 42;
        let mut next = |n: usize| {
            seed = seed
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            (seed >> 33) as usize % n
        };
        for _ in 0..3000 {
            let mut object = Map::new();
            for _ in 0..(1 + next(8)) {
                let key = keys[next(keys.len())];
                let pool = samples(key);
                object.insert(key.into(), pool[next(pool.len())].clone());
            }
            out.push(Value::Object(object));
        }
        out
    }

    /// The typed checks keep what the former ones kept, written the same.
    #[test]
    fn checks_as_before() {
        for input in corpus() {
            let typed = serde_json::to_value(ConfigUpdate::from_value(&input)).unwrap();
            let former = Value::Object(legacy::sanitize(&input));
            assert_eq!(typed, former, "input {input}");
        }
    }

    fn without_extra(cfg: &AppConfig) -> Value {
        let mut value = serde_json::to_value(cfg).unwrap();
        for key in cfg.extra.keys() {
            value.as_object_mut().unwrap().remove(key);
        }
        value
    }

    /// Loading two files, then saving changes, gives what it gave before.
    #[test]
    fn loads_and_saves_as_before() {
        let corpus = corpus();
        let mut seed: u64 = 7;
        let mut next = |n: usize| {
            seed = seed
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            (seed >> 33) as usize % n
        };
        for round in 0..2000 {
            let files = [
                corpus[next(corpus.len())].clone(),
                corpus[next(corpus.len())].clone(),
            ];
            let env = (round % 3 == 0).then(|| "ENVKEY".to_string());
            let typed = AppConfig::from_files(&files, env.clone());
            let former = legacy::load_from(&files, env);
            assert_eq!(
                without_extra(&typed),
                Value::Object(former.clone()),
                "files {files:?}"
            );
            let change = corpus[next(corpus.len())].clone();
            let saved = typed.clone().merged(&ConfigUpdate::from_value(&change));
            let former_saved = legacy::save_merge(former, &change);
            assert_eq!(
                without_extra(&saved),
                Value::Object(former_saved),
                "files {files:?} change {change}"
            );
        }
    }

    #[test]
    fn words_are_written_as_before() {
        assert_eq!(serde_json::to_value(Quality::Q4k).unwrap(), "4k");
        assert_eq!(serde_json::to_value(AudioMode::Plain).unwrap(), "none");
        assert_eq!(
            serde_json::to_value(AudioMode::SystemDefault).unwrap(),
            "default"
        );
        assert_eq!(HdrMode::parse("sdr_pref"), Some(HdrMode::SdrPref));
        assert_eq!(PrefLang::parse("VF"), None, "words are compared as stored");
        let cfg: AppConfig =
            serde_json::from_value(json!({"pref_quality": "1080P", "player_mode": 5})).unwrap();
        assert_eq!(cfg.pref_quality, Quality::Q1080p);
        assert_eq!(
            cfg.player_mode,
            PlayerMode::default(),
            "a wrong value is left out"
        );
    }
}
