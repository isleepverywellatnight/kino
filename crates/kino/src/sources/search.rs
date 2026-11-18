//! The source search: every indexer at once, the user's add-ons, APIBay and
//! Nyaa, then the releases that fit, ranked. Kept 3 minutes.

use std::collections::HashMap;
use std::sync::LazyLock;
use std::time::Duration;

use base64::Engine;
use regex::Regex;
use serde_json::{Map, Value, json};

use super::release::details;
use super::relevance::plausible_size;
use super::stream::{self, Wanted, py_quote, py_round};
use crate::config::{self, AppConfig, HdrMode, PrefLang, Quality, SourcePriority};
use crate::debrid;
use crate::meta::Meta;

static YEAR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(19\d\d|20\d\d)\b").unwrap());
static FIRST_NUMBER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(\d+)").unwrap());
static ADDON_TARGET: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^tt\d{1,12}(?::\d{1,4}:\d{1,5})?$").unwrap());
static NOT_ALNUM_SPACE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-zA-Z0-9\s]").unwrap());

/// What a page asks for, cleaned as the Python version does.
#[derive(Clone, Debug)]
pub struct Query {
    pub imdb_id: String,
    pub media_type: String,
    pub season: i64,
    pub episode: i64,
    pub title: String,
    pub release_year: String,
    pub runtime_minutes: Option<i64>,
    pub sort_by: String,
    pub pref_lang: PrefLang,
    pub pref_quality: Quality,
    pub hdr_mode: HdrMode,
    pub source_priority: SourcePriority,
}

fn int_param(params: &HashMap<String, String>, key: &str) -> i64 {
    params
        .get(key)
        .and_then(|v| v.trim().parse::<i64>().ok())
        .filter(|v| *v != 0)
        .unwrap_or(1)
}

impl Query {
    pub fn from_params(params: &HashMap<String, String>, cfg: &AppConfig) -> Self {
        let pick = |a: &str, b: &str| {
            params
                .get(a)
                .filter(|v| !v.is_empty())
                .or(params.get(b))
                .cloned()
                .unwrap_or_default()
        };
        let raw_id = pick("imdb_id", "id").trim().to_string();
        let imdb_id = crate::meta::canonical_id(&raw_id);
        let mut kind = pick("type", "media_type").trim().to_lowercase();
        if kind.is_empty() {
            kind = "movie".into();
        }
        if kind == "tv" {
            kind = "series".into();
        }
        if !["movie", "series", "anime"].contains(&kind.as_str()) {
            kind = "movie".into();
        }
        let raw_title = pick("title", "q").trim().to_string();
        let title = match raw_title.split_once(" — ") {
            Some((head, _)) => head.trim().to_string(),
            None => raw_title,
        };
        let raw_year = params
            .get("year")
            .map(|y| y.trim().to_string())
            .unwrap_or_default();
        let release_year = YEAR
            .captures(&raw_year)
            .map(|c| c[1].to_string())
            .unwrap_or(raw_year);
        let runtime_minutes = params
            .get("runtime")
            .filter(|r| !r.is_empty())
            .and_then(|r| FIRST_NUMBER.captures(r))
            .and_then(|c| c[1].parse().ok());
        Self {
            imdb_id,
            media_type: kind,
            season: int_param(params, "season"),
            episode: int_param(params, "episode"),
            title,
            release_year,
            runtime_minutes,
            sort_by: params
                .get("sort_by")
                .cloned()
                .unwrap_or_else(|| "score".into()),
            pref_lang: cfg.pref_lang,
            pref_quality: cfg.pref_quality,
            hdr_mode: cfg.hdr_mode,
            source_priority: cfg.priority(),
        }
    }

    /// The same key as the Python version (« stream_v14:… »).
    pub fn cache_key(&self, provider: &str, has_token: bool) -> String {
        let runtime = self
            .runtime_minutes
            .map_or("None".to_string(), |r| r.to_string());
        format!(
            "stream_v14:{provider}:{}:{}:{}:{}:{}:{}:{}:{}:{runtime}:{}:{}:{}:{}",
            self.media_type,
            self.imdb_id,
            self.season,
            self.episode,
            if has_token { "True" } else { "False" },
            self.sort_by,
            self.title.to_lowercase(),
            self.release_year,
            self.pref_lang,
            self.pref_quality,
            self.hdr_mode,
            self.source_priority
        )
    }
}

pub fn stream_target(imdb_id: &str, media_type: &str, season: i64, episode: i64) -> String {
    if ["series", "anime", "tv"].contains(&media_type) {
        format!("series/{imdb_id}:{season}:{episode}")
    } else {
        format!("movie/{imdb_id}")
    }
}

/// Comet, with the debrid service when a key is known.
pub fn comet_url(target: &str, token: &str, provider: &str) -> String {
    if token.is_empty() {
        return format!("https://comet.feels.legal/stream/{target}.json");
    }
    let service = if ["alldebrid", "torbox", "premiumize", "debridlink"].contains(&provider) {
        provider
    } else {
        "realdebrid"
    };
    // Python's json.dumps spacing, so the address is the same.
    let config = format!(
        "{{\"indexers\": [\"all\"], \"maxResultsPerResolution\": 0, \"maxSize\": 107374182400, \"cachedOnly\": false, \"removeTrash\": true, \
         \"debridServices\": [{{\"service\": \"{service}\", \"apiKey\": {}}}], \"enableTorrent\": true}}",
        serde_json::to_string(token).unwrap_or_default()
    );
    let encoded = base64::engine::general_purpose::STANDARD.encode(config);
    format!("https://comet.feels.legal/{encoded}/stream/{target}.json")
}

/// (bucket, address, label, seconds) of the indexers, in launch order.
pub fn indexer_sources(
    target: &str,
    token: &str,
    tio_key: &str,
    provider: &str,
) -> Vec<(String, String, String, u64)> {
    let tio = "https://torrentio.strem.fun";
    let (main, fr, anime, kc) = if !token.is_empty() && !tio_key.is_empty() {
        (
            format!("{tio}/{tio_key}={token}/stream/{target}.json"),
            format!(
                "{tio}/providers=torrent9,c411,nyaasi|language=french|{tio_key}={token}/stream/{target}.json"
            ),
            format!(
                "{tio}/providers=nyaasi,tokyotosho,anidex,torrent9|language=japanese,french|{tio_key}={token}/stream/{target}.json"
            ),
            format!("https://knightcrawler.elfhosted.com/{tio_key}={token}/stream/{target}.json"),
        )
    } else {
        (
            format!("{tio}/stream/{target}.json"),
            format!("{tio}/providers=torrent9,c411,nyaasi|language=french/stream/{target}.json"),
            format!(
                "{tio}/providers=nyaasi,tokyotosho,anidex,torrent9|language=japanese,french/stream/{target}.json"
            ),
            format!("https://knightcrawler.elfhosted.com/stream/{target}.json"),
        )
    };
    vec![
        ("main".into(), main, "Torrentio".into(), 20),
        ("fr".into(), fr, "Torrentio FR".into(), 10),
        ("anime".into(), anime, "Torrentio Anime (Nyaa)".into(), 10),
        (
            "comet".into(),
            comet_url(target, token, provider),
            "Comet".into(),
            10,
        ),
        ("knightcrawler".into(), kc, "KnightCrawler".into(), 4),
        (
            "tpb".into(),
            format!("https://thepiratebay-plus.strem.fun/stream/{target}.json"),
            "TPB+".into(),
            6,
        ),
        (
            "peerflix".into(),
            format!("https://peerflix.mov/stream/{target}.json"),
            "Peerflix".into(),
            6,
        ),
    ]
}

/// The enabled Stremio add-ons that give sources for this kind of title.
pub fn addon_sources(
    db: &crate::db::Db,
    target: &str,
    media_type: &str,
) -> Vec<(String, String, String, u64)> {
    let kind = if media_type == "movie" {
        "movie"
    } else {
        "series"
    };
    let id_part = target.split_once('/').map_or(target, |(_, rest)| rest);
    if !ADDON_TARGET.is_match(id_part) {
        return vec![];
    }
    let stored = db
        .get_config("stremio_addons_v1")
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default();
    stored
        .iter()
        .filter(|a| {
            a["id"].as_str().is_some_and(|s| !s.is_empty())
                && a["base"].as_str().is_some_and(|s| !s.is_empty())
        })
        .filter(|a| a["enabled"] == json!(true) && truthy(&a["streams"]) && truthy(&a["imdb"]))
        .filter(|a| {
            a["types"]
                .as_array()
                .is_some_and(|t| t.iter().any(|x| x == kind))
        })
        .map(|a| {
            let quoted: String = id_part
                .split(':')
                .map(py_quote)
                .collect::<Vec<_>>()
                .join(":");
            (
                format!("stremio:{}", a["id"].as_str().unwrap_or("")),
                format!(
                    "{}/stream/{kind}/{quoted}.json",
                    a["base"].as_str().unwrap_or("")
                ),
                a["name"].as_str().unwrap_or("").to_string(),
                8,
            )
        })
        .collect()
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

fn format_size(bytes: f64) -> String {
    if bytes == 0.0 {
        return "0 B".into();
    }
    let mut value = bytes;
    for unit in ["B", "KB", "MB", "GB", "TB"] {
        if value < 1024.0 {
            return format!("{value:.2} {unit}");
        }
        value /= 1024.0;
    }
    format!("{value:.2} PB")
}

pub struct Search<'a> {
    pub meta: &'a Meta,
}

impl Search<'_> {
    async fn stremio(&self, url: &str, label: &str, seconds: u64) -> Vec<Value> {
        match self
            .meta
            .net
            .get_json(url, "KINO/2.0", Duration::from_secs(seconds))
            .await
        {
            Ok(data) => data["streams"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|s| stream::parse_stremio_stream(s, label))
                .collect(),
            Err(_) => vec![],
        }
    }

    async fn apibay(&self, query: &str) -> Vec<Value> {
        let url = format!("https://apibay.org/q.php?q={}&cat=200", py_quote(query));
        let Ok(Value::Array(items)) = self
            .meta
            .net
            .request_json(
                reqwest::Method::GET,
                &url,
                &[],
                None,
                Duration::from_secs(4),
                0,
            )
            .await
        else {
            return vec![];
        };
        let mut found = Vec::new();
        for item in items {
            let hash = item["info_hash"].as_str().unwrap_or("");
            let name = item["name"].as_str().unwrap_or("");
            if hash.is_empty() || hash == "0".repeat(40) {
                continue;
            }
            let number = |v: &Value| {
                v.as_f64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
                    .unwrap_or(0.0)
            };
            let seeders = number(&item["seeders"]) as i64;
            let size = number(&item["size"]);
            let size_str = format_size(size);
            let d = details(name, "");
            let entry = json!({
                "source": "APIBay", "title": name, "meta": format!("{seeders} seeders • {size_str}"), "qualities": d["display_badges"],
                "langs": d["langs"], "magnet": format!("magnet:?xt=urn:btih:{hash}&dn={}", py_quote(name)), "resolve_url": "",
                "is_instant": false, "info_hash": hash, "file_idx": null, "seeders": seeders,
                "size_gb": py_round(size / (1024.0 * 1024.0 * 1024.0), 2), "size_str": size_str, "parsed_details": d,
            });
            if plausible_size(&entry, "movie", None) {
                found.push(entry);
            }
        }
        found.truncate(25);
        found
    }

    /// The Nyaa add-on (anime, VOSTFR and fansubs), when it is enabled.
    async fn nyaa(&self, query: &str, season: i64, episode: i64) -> Vec<Value> {
        let enabled = self
            .meta
            .net
            .db()
            .get_config("addons_enabled")
            .and_then(|v| v.get("nyaa_anime").cloned());
        if enabled.as_ref().is_some_and(|e| !truthy(e)) {
            return vec![];
        }
        let clean = NOT_ALNUM_SPACE.replace_all(query, " ").trim().to_string();
        if clean.is_empty() {
            return vec![];
        }
        let term = if season != 0 && episode != 0 {
            format!("{clean} E{episode:02}")
        } else {
            clean
        };
        let url = format!(
            "https://nyaa.si/?page=rss&q={}&c=1_0&f=0",
            form_urlencoded::byte_serialize(term.as_bytes()).collect::<String>()
        );
        let agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KINO/1.0";
        let Ok(feed) = self
            .meta
            .net
            .get_text(&url, &[], agent, Duration::from_millis(3500))
            .await
        else {
            return vec![];
        };
        let Ok(document) = roxmltree::Document::parse(&feed) else {
            return vec![];
        };
        let mut found = Vec::new();
        for item in document.descendants().filter(|n| n.has_tag_name("item")) {
            let child = |name: &str| {
                item.children()
                    .find(|n| n.tag_name().name() == name)
                    .and_then(|n| n.text())
                    .unwrap_or("")
                    .to_string()
            };
            let title = child("title");
            let hash = child("infoHash").to_lowercase();
            if title.is_empty() || hash.is_empty() {
                continue;
            }
            let mut entry = json!({"name": title, "title": title, "magnet": format!("magnet:?xt=urn:btih:{hash}&dn={}", py_quote(&title)),
                                   "info_hash": hash, "seeds": child("seeders").parse::<i64>().unwrap_or(0), "size": child("size"),
                                   "source": "Nyaa Anime", "addon_id": "nyaa_anime"});
            // As the Python version did: the release's details join the entry.
            if let (Some(map), Some(more)) =
                (entry.as_object_mut(), details(&title, "").as_object())
            {
                map.extend(more.clone());
            }
            found.push(entry);
        }
        found
    }

    /// The ranked sources for a film or an episode.
    pub async fn streams(
        &self,
        query: &Query,
        provider: &str,
        token: &str,
        force: bool,
    ) -> Vec<Value> {
        let key = query.cache_key(provider, !token.is_empty());
        if !force && let Some(Value::Array(hit)) = self.meta.net.db().cache_get(&key) {
            return hit;
        }
        let found = self.fetch(query, provider, token).await;
        let _ = self
            .meta
            .net
            .db()
            .cache_set(&key, &Value::Array(found.clone()), 180.0);
        found
    }

    async fn fetch(&self, query: &Query, provider: &str, token: &str) -> Vec<Value> {
        let tio_key = debrid::PROVIDERS
            .iter()
            .find(|p| p.0 == provider)
            .map_or("realdebrid", |p| p.4);
        let mut aliases: Vec<String> = Vec::new();
        if !query.title.is_empty()
            && query.media_type == "movie"
            && let Ok(meta) = self.meta.media(&query.imdb_id, "movie").await
            && let Some(name) = meta["name"].as_str().filter(|n| !n.is_empty())
            && name.to_lowercase() != query.title.to_lowercase()
        {
            aliases.push(name.to_string());
        }
        let absolute =
            if ["series", "anime"].contains(&query.media_type.as_str()) && query.season > 1 {
                self.meta
                    .absolute_episode(&query.imdb_id, query.season, query.episode, true)
                    .await
            } else {
                None
            };
        let target = stream_target(
            &query.imdb_id,
            &query.media_type,
            query.season,
            query.episode,
        );
        let mut sources = indexer_sources(&target, token, tio_key, provider);
        sources.extend(addon_sources(
            self.meta.net.db(),
            &target,
            &query.media_type,
        ));
        let waits: Vec<u64> = sources
            .iter()
            .enumerate()
            .map(|(i, s)| match i {
                0 => 20,
                1 | 2 => 8,
                _ if s.0.starts_with("stremio:") => s.3,
                _ => 4,
            })
            .collect();
        let stremio = futures_util::future::join_all(
            sources
                .iter()
                .zip(&waits)
                .map(|(s, wait)| self.stremio(&s.1, &s.2, (*wait).min(s.3))),
        );
        let addon_query = if !query.title.is_empty() {
            query.title.trim().to_string()
        } else {
            target.split(':').next().unwrap_or(&target).to_string()
        };
        let apibay_query = if query.title.is_empty() {
            query.imdb_id.clone()
        } else {
            query.title.clone()
        };
        let (answers, apibay, addons) = tokio::join!(
            stremio,
            self.apibay(&apibay_query),
            self.nyaa(&addon_query, query.season, query.episode)
        );
        let mut buckets = Map::new();
        for bucket in [
            "main",
            "fr",
            "anime",
            "comet",
            "knightcrawler",
            "tpb",
            "peerflix",
            "apibay",
            "addons",
        ] {
            buckets.insert(bucket.into(), json!([]));
        }
        for (source, answer) in sources.iter().zip(answers) {
            buckets.insert(source.0.clone(), Value::Array(answer));
        }
        buckets.insert("apibay".into(), Value::Array(apibay));
        buckets.insert("addons".into(), Value::Array(addons));
        let titles: Vec<String> = std::iter::once(query.title.clone())
            .chain(aliases)
            .filter(|t| !t.is_empty())
            .collect();
        let wanted = Wanted {
            media_type: &query.media_type,
            season: query.season,
            episode: query.episode,
            absolute_episode: absolute,
            runtime_minutes: query.runtime_minutes.map(|r| r as f64),
            titles: &titles,
            release_year: &query.release_year,
            pref_lang: query.pref_lang,
            pref_quality: query.pref_quality,
        };
        let kept = stream::keep_relevant(stream::merge(&buckets), &wanted);
        stream::rank(
            kept,
            &query.sort_by,
            &wanted,
            query.hdr_mode,
            query.source_priority,
        )
    }
}

/// The settings a search needs: provider and key.
/// The active provider's name and key (trimmed).
pub fn provider_and_token(cfg: &AppConfig) -> (String, String) {
    (
        cfg.debrid_provider.as_str().to_string(),
        cfg.rd_token.trim().to_string(),
    )
}

pub fn load_settings() -> AppConfig {
    config::load()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn addresses_and_keys() {
        assert_eq!(stream_target("tt1", "series", 2, 5), "series/tt1:2:5");
        assert_eq!(stream_target("tt1", "movie", 2, 5), "movie/tt1");
        // The same address as Python's base64(json.dumps(config)).
        assert_eq!(
            comet_url("movie/tt1", "KEY", "torbox"),
            "https://comet.feels.legal/eyJpbmRleGVycyI6IFsiYWxsIl0sICJtYXhSZXN1bHRzUGVyUmVzb2x1dGlvbiI6IDAsICJtYXhTaXplIjogMTA3Mzc0MTgyNDAwLCAiY2FjaGVkT25seSI6IGZhbHNlLCAicmVtb3ZlVHJhc2giOiB0cnVlLCAiZGVicmlkU2VydmljZXMiOiBbeyJzZXJ2aWNlIjogInRvcmJveCIsICJhcGlLZXkiOiAiS0VZIn1dLCAiZW5hYmxlVG9ycmVudCI6IHRydWV9/stream/movie/tt1.json"
        );
        let params: HashMap<String, String> = [
            ("imdb_id", "tt1"),
            ("type", "tv"),
            ("season", "0"),
            ("title", "Dark — Saison 1"),
            ("year", "(2017)"),
            ("runtime", "60 min"),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
        let q = Query::from_params(&params, &AppConfig::default());
        assert_eq!(
            (
                q.media_type.as_str(),
                q.season,
                q.title.as_str(),
                q.release_year.as_str(),
                q.runtime_minutes
            ),
            ("series", 1, "Dark", "2017", Some(60))
        );
        assert_eq!(
            q.cache_key("realdebrid", false),
            "stream_v14:realdebrid:series:tt1:1:1:False:score:dark:2017:60:vf:4k:sdr_pref:speed"
        );
        assert_eq!(format_size(1_610_612_736.0), "1.50 GB");
    }
}
