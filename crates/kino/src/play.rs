//! « Lecture en un clic » with KINO's own player: the sources tried in order,
//! the right file in each, the episode's playlist, the resume point; and the
//! next episodes found on demand (/api/auto-stream), kept 30 minutes.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::Duration;

use regex::Regex;
use rusqlite::params;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::db::{Db, now};
use crate::meta::Meta;
use crate::net::NetError;
use crate::resolve::Resolver;
use crate::sources::relevance::{clean_title_for_comparison, is_valid_release_for_show};
use crate::sources::score::episode_status;
use crate::sources::search::{Query, Search};

pub const NO_STREAM: &str = "Aucun flux valide trouvé pour ce titre.";
static SOURCE_KEY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[0-9a-f]{32}$").unwrap());
static TORRENT_ID: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[A-Za-z0-9:_-]{1,80}$").unwrap());
static FEEDBACK_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[\w:-]{1,160}$").unwrap());
static VIDEO_SUFFIX: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\.(?:mkv|mp4|avi|mov|m4v|wmv|ts|webm)$").unwrap());
static NOT_ALNUM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9]+").unwrap());
static HEX: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[0-9a-f]{8,}$").unwrap());
static GENERIC: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?:video|movie|film|stream|download|file|output|feature|main)(?:\s*(?:\d+|hd|1080p|2160p|720p|4k))*$").unwrap()
});

pub fn text(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
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

fn int(value: &Value, default: i64) -> i64 {
    match value {
        Value::Null => default,
        Value::String(s) => s.trim().parse().unwrap_or(default),
        other => other
            .as_i64()
            .or_else(|| other.as_f64().map(|f| f as i64))
            .unwrap_or(default),
    }
}

/// A stable, opaque name for a source, so that a failed one is not tried again.
pub fn candidate_key(candidate: &Value) -> String {
    let identity = ["info_hash", "magnet", "resolve_url"]
        .iter()
        .map(|k| &candidate[*k])
        .find(|v| truthy(v))
        .map(text)
        .unwrap_or_else(|| {
            format!(
                "{}:{}",
                text(&candidate["source"]),
                text(&candidate["title"])
            )
        });
    hex::encode(Sha256::digest(identity.as_bytes()))[..32].to_string()
}

// ---------- Sources the page or earlier plays set aside ----------

fn feedback_scope(context: &Value) -> Option<String> {
    let identity = [&context["imdb_id"], &context["id"]]
        .into_iter()
        .find(|v| truthy(v))
        .map(text)
        .unwrap_or_default();
    if !FEEDBACK_ID.is_match(&identity) {
        return None;
    }
    let series = ["series", "anime", "tv"].contains(&context["type"].as_str().unwrap_or(""));
    let (season, episode) = if series {
        (
            Some(int(&context["season"], 1)),
            Some(int(&context["episode"], 1)),
        )
    } else {
        (None, None)
    };
    if series && (season < Some(0) || episode < Some(1)) {
        return None;
    }
    Some(
        json!([
            identity,
            if series { "series" } else { "movie" },
            season,
            episode
        ])
        .to_string()
        .replace(",", ", "),
    )
}

/// Sources that failed for this title before (wrong content for good, the others an hour).
pub fn excluded(db: &Db, context: &Value) -> HashSet<String> {
    let Some(scope) = feedback_scope(context) else {
        return HashSet::new();
    };
    db.with(|c| {
        c.execute(
            "CREATE TABLE IF NOT EXISTS playback_source_feedback (scope TEXT NOT NULL, source_key TEXT NOT NULL, reason TEXT NOT NULL, \
             expires_at REAL, PRIMARY KEY (scope, source_key))",
            [],
        )?;
        c.execute("DELETE FROM playback_source_feedback WHERE expires_at <= ?", params![now()])?;
        let mut s = c.prepare("SELECT source_key FROM playback_source_feedback WHERE scope = ?")?;
        s.query_map([&scope], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<HashSet<_>>>()
    })
    .unwrap_or_default()
}

/// The keys the page asks to skip: at most 30, well formed only.
pub fn set_aside(body: &Value) -> HashSet<String> {
    body["exclude_source_keys"]
        .as_array()
        .into_iter()
        .flatten()
        .take(30)
        .filter_map(Value::as_str)
        .filter(|k| SOURCE_KEY.is_match(k))
        .map(str::to_string)
        .collect()
}

// ---------- The right file of a source ----------

fn size(item: &Value) -> i64 {
    int(&item["filesize_bytes"], 0)
}

/// A file that does not contradict what was asked for: the episode, or the film's title.
pub async fn select_file(
    meta: &Meta,
    query: &Query,
    files: &[Value],
    aliases: &[String],
) -> Option<Value> {
    let available: Vec<&Value> = files
        .iter()
        .filter(|f| {
            f["download"]
                .as_str()
                .is_some_and(|d| d.starts_with("http://") || d.starts_with("https://"))
        })
        .collect();
    if available.is_empty() {
        return None;
    }
    let biggest = |items: Vec<&Value>| items.into_iter().rev().max_by_key(|i| size(i)).cloned();
    if ["series", "anime"].contains(&query.media_type.as_str()) {
        let absolute = if query.season > 1 && !query.imdb_id.is_empty() {
            meta.absolute_episode(&query.imdb_id, query.season, query.episode, false)
                .await
        } else {
            None
        };
        let eligible: Vec<(&Value, &str)> = available
            .iter()
            .map(|f| {
                (
                    *f,
                    episode_status(
                        f["filename"].as_str().unwrap_or(""),
                        query.season,
                        query.episode,
                        absolute,
                    ),
                )
            })
            .filter(|(_, s)| *s != "mismatch")
            .collect();
        let explicit: Vec<&Value> = eligible
            .iter()
            .filter(|(_, s)| *s == "match")
            .map(|(f, _)| *f)
            .collect();
        if !explicit.is_empty() {
            return biggest(explicit);
        }
        let flagged: Vec<&Value> = eligible
            .iter()
            .filter(|(f, _)| f["is_target_ep"].as_bool() == Some(true))
            .map(|(f, _)| *f)
            .collect();
        if !flagged.is_empty() {
            return biggest(flagged);
        }
        return (available.len() == 1 && !eligible.is_empty()).then(|| eligible[0].0.clone());
    }
    let mut names: Vec<String> = Vec::new();
    for name in std::iter::once(&query.title)
        .chain(aliases)
        .filter(|n| !n.is_empty())
        .map(|n| n.trim().to_string())
    {
        if !names.contains(&name) {
            names.push(name);
        }
    }
    let matching: Vec<&Value> = available
        .iter()
        .copied()
        .filter(|f| {
            names.iter().any(|n| {
                is_valid_release_for_show(
                    f["filename"].as_str().unwrap_or(""),
                    n,
                    &query.release_year,
                )
            })
        })
        .collect();
    if !matching.is_empty() {
        return biggest(matching);
    }
    if available.len() != 1 {
        return None;
    }
    let filename = available[0]["filename"].as_str().unwrap_or("");
    let base = filename.rsplit(['/', '\\']).next().unwrap_or(filename);
    let stem = VIDEO_SUFFIX.replace(base, "");
    let normalized = NOT_ALNUM
        .replace_all(&clean_title_for_comparison(&stem), " ")
        .trim()
        .to_string();
    // A unique generic filename says nothing; trust the source already checked.
    let generic =
        normalized.is_empty() || HEX.is_match(&normalized) || GENERIC.is_match(&normalized);
    (names.is_empty() || generic).then(|| available[0].clone())
}

pub struct Found {
    pub candidate: Value,
    pub answer: Value,
    pub file: Value,
    pub key: String,
}

pub struct Attempts {
    pub found: Option<Found>,
    /// The last answer of a source the debrid service is still preparing.
    pub pending: Option<Value>,
    pub tried: Vec<String>,
    pub last_error: Option<NetError>,
}

/// The first ten sources in their order: the debrid link, then the right file in it.
pub async fn try_sources(
    meta: &Meta,
    resolver: &Resolver<'_>,
    candidates: &[Value],
    query: &Query,
    episode: Option<(i64, i64)>,
    aliases_for: impl Fn(&Value) -> Vec<String>,
    skip: &HashSet<String>,
) -> Attempts {
    let mut attempts = Attempts {
        found: None,
        pending: None,
        tried: vec![],
        last_error: None,
    };
    for candidate in candidates.iter().take(10) {
        let key = candidate_key(candidate);
        if skip.contains(&key) {
            continue;
        }
        attempts.tried.push(key.clone());
        let magnet = candidate["magnet"].as_str().unwrap_or("");
        let link = candidate["resolve_url"].as_str();
        match resolver
            .magnet(magnet, episode.map(|e| e.0), episode.map(|e| e.1), link)
            .await
        {
            Ok(answer) => {
                let files = answer["files"].as_array().cloned().unwrap_or_default();
                if answer["ready"] != json!(true) || files.is_empty() {
                    if answer["ready"] != json!(true) {
                        attempts.pending = Some(answer);
                    }
                    continue;
                }
                if let Some(file) = select_file(meta, query, &files, &aliases_for(candidate)).await
                {
                    attempts.found = Some(Found {
                        candidate: candidate.clone(),
                        answer,
                        file,
                        key,
                    });
                    return attempts;
                }
            }
            Err(e) => attempts.last_error = Some(e),
        }
    }
    attempts
}

// ---------- Next episodes, found on demand ----------

/// (imdb id, season, episode) → (link, file name, when it was found).
type Links = HashMap<(String, i64, i64), (String, String, f64)>;

#[derive(Default)]
pub struct EpisodeLinks {
    links: Mutex<Links>,
}

impl EpisodeLinks {
    pub fn remember(&self, key: (String, i64, i64), download: &str, filename: &str) {
        self.links
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(key, (download.into(), filename.into(), now()));
    }

    fn recent(&self, key: &(String, i64, i64)) -> Option<(String, String)> {
        let links = self.links.lock().unwrap_or_else(|e| e.into_inner());
        links
            .get(key)
            .filter(|(_, _, at)| now() - at < 1800.0)
            .map(|(d, f, _)| (d.clone(), f.clone()))
    }
}

pub struct Episode {
    pub imdb_id: String,
    pub season: i64,
    pub episode: i64,
    pub name: String,
    pub year: String,
    pub poster: String,
}

impl Episode {
    pub fn from_params(p: &HashMap<String, String>) -> Self {
        let get = |k: &str| p.get(k).cloned().unwrap_or_default();
        let number = |k: &str| {
            p.get(k)
                .and_then(|v| v.trim().parse::<i64>().ok())
                .filter(|v| *v != 0)
                .unwrap_or(1)
        };
        Self {
            imdb_id: get("imdb_id").trim().to_string(),
            season: number("season"),
            episode: number("episode"),
            name: Some(get("name"))
                .filter(|n| !n.is_empty())
                .unwrap_or_else(|| "Série".into()),
            year: get("year"),
            poster: get("poster"),
        }
    }

    pub fn history_entry(&self, filename: &str) -> Map<String, Value> {
        json!({"id": self.imdb_id, "name": self.name, "type": "series", "year": self.year, "poster": self.poster,
               "season": self.season, "episode": self.episode, "filename": filename})
        .as_object()
        .cloned()
        .unwrap_or_default()
    }
}

/// The debrid link of an episode, reused for 30 minutes; only the player's own
/// call records it in the history.
pub async fn resolve_episode(
    meta: &Meta,
    links: &EpisodeLinks,
    ep: &Episode,
    pref_hash: &str,
    cfg: &Map<String, Value>,
    update_history: bool,
) -> Result<String, NetError> {
    let (provider, token) = crate::sources::search::provider_and_token(cfg);
    if token.is_empty() {
        return Err(NetError("Token API Real-Debrid manquant.".into()));
    }
    let label = format!("{} S{:02}E{:02}", ep.name, ep.season, ep.episode);
    let key = (ep.imdb_id.clone(), ep.season, ep.episode);
    if let Some((download, filename)) = links.recent(&key) {
        if !ep.imdb_id.is_empty() && update_history {
            crate::library::record_history(
                meta.net.db(),
                &ep.history_entry(if filename.is_empty() {
                    &label
                } else {
                    &filename
                }),
            );
        }
        return Ok(download);
    }
    let params: HashMap<String, String> = [
        ("imdb_id", ep.imdb_id.clone()),
        ("type", "series".into()),
        ("season", ep.season.to_string()),
        ("episode", ep.episode.to_string()),
        ("title", ep.name.clone()),
        ("year", ep.year.clone()),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect();
    let query = Query::from_params(&params, cfg);
    let mut torrents = if ep.imdb_id.is_empty() {
        vec![]
    } else {
        Search { meta }
            .streams(&query, &provider, &token, false)
            .await
    };
    if torrents.is_empty() {
        return Err(NetError(format!("Aucun flux trouvé pour {label}.")));
    }
    if !pref_hash.is_empty() {
        // The server's ranking stays; the previous episode's source only wins a tie.
        let score = |c: &Value| c["score"].as_f64().unwrap_or(f64::NEG_INFINITY);
        torrents.sort_by(|a, b| {
            let same =
                |c: &Value| c["info_hash"].as_str().unwrap_or("").to_lowercase() == pref_hash;
            score(b).total_cmp(&score(a)).then(same(b).cmp(&same(a)))
        });
    }
    if cfg.get("source_priority").and_then(Value::as_str) == Some("quality") {
        torrents.sort_by_key(|s| s["is_instant"].as_bool() != Some(true));
    }
    let resolver = Resolver {
        net: &meta.net,
        provider,
        token,
        hosts: Default::default(),
    };
    let attempts = try_sources(
        meta,
        &resolver,
        &torrents,
        &query,
        Some((ep.season, ep.episode)),
        |_| vec![],
        &HashSet::new(),
    )
    .await;
    match attempts.found {
        Some(found) => {
            let download = found.file["download"].as_str().unwrap_or("").to_string();
            let filename = found.file["filename"]
                .as_str()
                .map(str::to_string)
                .unwrap_or(label);
            links.remember(key, &download, &filename);
            if !ep.imdb_id.is_empty() && update_history {
                crate::library::record_history(meta.net.db(), &ep.history_entry(&filename));
            }
            Ok(download)
        }
        None => {
            let why = attempts
                .last_error
                .map(|e| e.0)
                .unwrap_or_else(|| "aucun flux RD+ prêt".into());
            Err(NetError(format!(
                "Impossible de résoudre S{:02}E{:02} ({why}).",
                ep.season, ep.episode
            )))
        }
    }
}

/// The next episode's link, found in the background while the current one plays.
pub fn prefetch_next(
    meta: Meta,
    links: Arc<EpisodeLinks>,
    ep: Episode,
    pref_hash: String,
    cfg: Map<String, Value>,
) {
    if ep.imdb_id.is_empty() || ep.season == 0 || ep.episode <= 0 {
        return;
    }
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(4)).await;
        let _ = resolve_episode(&meta, &links, &ep, &pref_hash, &cfg, false).await;
    });
}

/// The season's playlist: this episode, then the next ones through /api/auto-stream.
#[allow(clippy::too_many_arguments)]
pub async fn season_playlist(
    meta: &Meta,
    links: &EpisodeLinks,
    port: u16,
    imdb_id: &str,
    season: i64,
    start: i64,
    first_url: &str,
    series_name: &str,
    year: &str,
    poster: &str,
    pref_hash: &str,
    first_filename: &str,
) -> Vec<Value> {
    let (s, ep0) = (
        if season == 0 { 1 } else { season },
        if start == 0 { 1 } else { start },
    );
    let mut label = if series_name.trim().is_empty() {
        "Série".to_string()
    } else {
        series_name.trim().to_string()
    };
    if !imdb_id.is_empty() && !first_url.is_empty() {
        let name = if first_filename.is_empty() {
            format!("{label}.S{s:02}E{ep0:02}")
        } else {
            first_filename.to_string()
        };
        links.remember((imdb_id.into(), s, ep0), first_url, &name);
    }
    let mut videos: Vec<(i64, String)> = Vec::new();
    if !imdb_id.is_empty()
        && let Ok(series) = meta.media(imdb_id, "series").await
    {
        if series_name.is_empty()
            && let Some(name) = series["name"].as_str().filter(|n| !n.is_empty())
        {
            label = name.to_string();
        }
        for v in series["videos"].as_array().into_iter().flatten() {
            if int(&v["season"], 0) == s {
                let number = if int(&v["episode"], 0) != 0 {
                    int(&v["episode"], 0)
                } else {
                    int(&v["number"], 0)
                };
                if number > 0 {
                    let name = [&v["name"], &v["title"]]
                        .into_iter()
                        .find(|n| truthy(n))
                        .map(text)
                        .unwrap_or_else(|| format!("Épisode {number}"));
                    videos.push((number, name));
                }
            }
        }
    }
    videos.sort_by_key(|v| v.0);
    let first_name = videos
        .iter()
        .find(|v| v.0 == ep0)
        .map(|v| v.1.clone())
        .unwrap_or_default();
    let mut future: Vec<(i64, String)> = videos.into_iter().filter(|v| v.0 > ep0).collect();
    if future.is_empty() && !imdb_id.is_empty() {
        future = (ep0 + 1..(ep0 + 15).min(25))
            .map(|n| (n, format!("Épisode {n}")))
            .collect();
    }
    let first_title = format!(
        "{label} — S{s:02}E{ep0:02}{}",
        if first_name.is_empty() {
            String::new()
        } else {
            format!(" — {first_name}")
        }
    );
    let mut items = vec![json!({"title": first_title, "url": first_url})];
    for (number, name) in future {
        let query = form_urlencoded::Serializer::new(String::new())
            .append_pair("imdb_id", imdb_id)
            .append_pair("season", &s.to_string())
            .append_pair("episode", &number.to_string())
            .append_pair("name", &label)
            .append_pair("year", year)
            .append_pair("poster", poster)
            .append_pair("pref_hash", pref_hash)
            .finish();
        items.push(json!({"title": format!("{label} — S{s:02}E{number:02} — {name}"), "url": format!("http://127.0.0.1:{port}/api/auto-stream?{query}")}));
    }
    items
}

/// Where to resume: the episode's own point, or the film's, between 1 % and 85 %.
pub fn resume_position(
    history: &[Value],
    imdb_id: &str,
    season: Option<i64>,
    episode: Option<i64>,
) -> i64 {
    let Some(item) = history.iter().find(|x| x["id"].as_str() == Some(imdb_id)) else {
        return 0;
    };
    if let (Some(s), Some(e)) = (season.filter(|s| *s != 0), episode.filter(|e| *e != 0))
        && let Some(position) = item["ep_positions"]
            .get(format!("S{s:02}E{e:02}"))
            .filter(|p| truthy(p))
    {
        let pct = position["pct"].as_f64().unwrap_or(0.0);
        return if (1.0..85.0).contains(&pct) {
            position["pos"].as_f64().unwrap_or(0.0) as i64
        } else {
            0
        };
    }
    let pct = item["progress_pct"].as_f64().unwrap_or(0.0);
    if (1.0..85.0).contains(&pct) {
        let same = match (season.filter(|s| *s != 0), episode) {
            (None, _) => true,
            (Some(s), e) => item["season"].as_i64() == Some(s) && item["episode"].as_i64() == e,
        };
        if same {
            return item["position"].as_f64().unwrap_or(0.0) as i64;
        }
    }
    0
}

/// The source the page waited for, now that the debrid service has it.
pub fn prepared(body: &Value) -> Option<(String, String)> {
    let p = body["prepared"].as_object()?;
    let id = p.get("torrent_id").map(text).unwrap_or_default();
    let key = p.get("source_key").map(text).unwrap_or_default();
    (TORRENT_ID.is_match(&id) && SOURCE_KEY.is_match(&key)).then_some((id, key))
}

/// What the page shows while the debrid service prepares the source.
pub fn preparing(answer: &Value, candidate: &Value, key: &str, sources: &[Value]) -> Value {
    let progress = answer["progress"]
        .as_f64()
        .or_else(|| answer["progress"].as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0.0) as i64;
    json!({
        "preparing": true, "torrent_id": text(&answer["torrent_id"]), "progress": progress.clamp(0, 100), "status": text(&answer["status"]),
        "speed": text(&answer["speed"]), "seeders": int(&answer["seeders"], 0), "source_key": key, "chosen_torrent": chosen_label(candidate),
        "ready_alternative": sources.iter().any(|s| s["is_instant"].as_bool() == Some(true)),
    })
}

pub fn chosen_label(candidate: &Value) -> String {
    format!(
        "{} {}",
        text(&candidate["source"]),
        text(&candidate["title"])
    )
    .trim()
    .to_string()
}

/// Names a film's file may carry; an episode is found by its number.
pub fn file_aliases(is_series: bool, film: &Value, candidate: &Value) -> Vec<String> {
    if is_series {
        return vec![];
    }
    vec![
        text(&film["name"]),
        clean_title_for_comparison(&text(&candidate["title"])),
    ]
}
