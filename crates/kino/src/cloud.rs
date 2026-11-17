//! The debrid Cloud: the files of the account, grouped by work from their
//! release names (« The.Bear.S03E05.1080p… » → The Bear, S3E5, 1080p), their
//! deletion and the retention cleanup.

use std::collections::{BTreeSet, HashMap};
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use regex::Regex;
use serde_json::{Map, Value, json};
use unicode_normalization::UnicodeNormalization;

use crate::net::{BROWSER, Net, NetResult};
use crate::resolve::{Hosts, format_size};
use crate::sources::stream::{py_quote, py_round};

const VIDEO: [&str; 12] = [
    "mkv", "mp4", "avi", "m4v", "mov", "ts", "m2ts", "wmv", "webm", "mpg", "mpeg", "iso",
];
const OTHER: [&str; 28] = [
    "zip", "rar", "7z", "tar", "gz", "srt", "ass", "ssa", "sub", "idx", "nfo", "txt", "jpg",
    "jpeg", "png", "gif", "pdf", "epub", "cbz", "cbr", "mp3", "flac", "m4a", "aac", "ogg", "exe",
    "dmg", "apk",
];
/// Words that only appear after the title in a release name.
const RELEASE_WORDS: [&str; 56] = [
    "4k",
    "uhd",
    "hdr",
    "hdr10",
    "hdr10+",
    "dv",
    "dovi",
    "bluray",
    "blu-ray",
    "bdrip",
    "brrip",
    "bdremux",
    "remux",
    "web-dl",
    "webdl",
    "webrip",
    "web-rip",
    "hdtv",
    "pdtv",
    "dvdrip",
    "dvdscr",
    "hdrip",
    "hdlight",
    "x264",
    "x265",
    "h264",
    "h265",
    "h-264",
    "h-265",
    "hevc",
    "avc",
    "xvid",
    "divx",
    "10bit",
    "8bit",
    "aac",
    "ac3",
    "eac3",
    "dts",
    "dts-hd",
    "ddp",
    "ddp5",
    "dd5",
    "truehd",
    "atmos",
    "multi",
    "multi-vf2",
    "vff",
    "vfq",
    "vfi",
    "vf2",
    "vostfr",
    "subfrench",
    "truefrench",
    "amzn",
    "dsnp",
];
const MORE_RELEASE_WORDS: [&str; 3] = ["atvp", "hmax", "nf"];
/// Words that may close a title when no year or marker cut it earlier.
const TRAILING: [&str; 18] = [
    "french",
    "truefrench",
    "multi",
    "vff",
    "vf",
    "vostfr",
    "english",
    "complete",
    "integrale",
    "intégrale",
    "extended",
    "unrated",
    "remastered",
    "proper",
    "repack",
    "internal",
    "custom",
    "-",
];
/// Names that say nothing about the work: kept apart rather than matched.
const GENERIC: [&str; 9] = [
    "video",
    "film",
    "movie",
    "sample",
    "fichier",
    "file",
    "untitled",
    "download",
    "telechargement",
];

static EXTENSION: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(?s)(.*)\.([A-Za-z0-9]{2,4})$").unwrap());
static GROUP_TAG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\s*\[[^\]]*\]").unwrap());
static GROUP_TAGS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*(?:\[[^\]]*\]\s*)+").unwrap());
static SITE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^\s*(?:www\.)?[\w-]+\.(?:com|org|net|to|cc|tv|io|me|ws|xyz|fr)\s+-\s+")
        .unwrap()
});
static DOTS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[._]+").unwrap());
static BRACKETS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\[\](){}]").unwrap());
static MARKER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^s(\d{1,2})[ -]?e(\d{1,4})(?:-?e?\d{1,4})?$").unwrap());
static CROSS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(\d{1,2})x(\d{2,3})$").unwrap());
static SEASON: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^s(\d{1,2})$").unwrap());
static EPISODE_E: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^e(\d{1,4})$").unwrap());
static EPISODE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^ep?(\d{2,4})$").unwrap());
static ABSOLUTE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{1,4}(?:v\d)?$").unwrap());
static VERSION: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"v\d$").unwrap());
static RESOLUTION: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{3,4}[pi]$").unwrap());
static YEAR_TOKEN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(19|20)\d\d$").unwrap());
static NOT_ALNUM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^0-9a-z]+").unwrap());
static YEARS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(1[89]\d\d|20\d\d)\b").unwrap());

fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false))
        && v != &json!(0)
        && v != &json!("")
        && v != &json!([])
        && v != &json!({})
}

fn text(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

fn or_text(v: &Value) -> String {
    if truthy(v) { text(v) } else { String::new() }
}

fn digits(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_digit())
}

/// Lower case, no accents, no punctuation.
pub fn comparable(value: &str) -> String {
    let folded: String = value
        .nfkd()
        .filter(char::is_ascii)
        .collect::<String>()
        .to_lowercase();
    NOT_ALNUM
        .replace_all(&folded.replace('&', " and "), " ")
        .trim()
        .to_string()
}

fn release_word(token: &str) -> bool {
    let low = token.to_lowercase();
    let low = low.trim_matches(|c| ".,;:!-+".contains(c));
    let head = low.split('-').next().unwrap_or("");
    let known = |w: &str| RELEASE_WORDS.contains(&w) || MORE_RELEASE_WORDS.contains(&w);
    known(low) || known(head) || RESOLUTION.is_match(head)
}

/// {"kind": movie | series | other, "title", "year", "season", "episode", "quality", "hdr"}.
pub fn parse_release(filename: &str, today: NaiveDate) -> Value {
    let mut season: Option<i64> = None;
    let mut episode: Option<i64> = None;
    let mut year = String::new();
    let other = |year: &str| json!({"kind": "other", "title": "", "year": year, "season": null, "episode": null, "quality": "", "hdr": false});
    let name = filename.trim();
    let (base, extension) = match EXTENSION.captures(name) {
        Some(c)
            if VIDEO.contains(&c[2].to_lowercase().as_str())
                || OTHER.contains(&c[2].to_lowercase().as_str()) =>
        {
            (c[1].to_string(), c[2].to_lowercase())
        }
        _ => (name.to_string(), String::new()),
    };
    if !extension.is_empty() && !VIDEO.contains(&extension.as_str()) {
        return other("");
    }
    let grouped = GROUP_TAG.is_match(&base);
    let base = GROUP_TAGS.replace(&base, "");
    let base = SITE.replace(&base, "");
    let spaced = DOTS.replace_all(&base, " ");
    let spaced = BRACKETS.replace_all(&spaced, " ");
    let tokens: Vec<&str> = spaced.split_whitespace().collect();
    let latest_year = i64::from(chrono::Datelike::year(&today)) + 1;
    let mut cut = tokens.len();
    for (index, token) in tokens.iter().enumerate() {
        let low = token.to_lowercase();
        let following = tokens
            .get(index + 1)
            .map(|t| t.to_lowercase())
            .unwrap_or_default();
        let n = |s: &str| s.parse::<i64>().ok();
        if let Some(c) = MARKER.captures(&low) {
            (season, episode) = (n(&c[1]), n(&c[2]));
        } else if let Some(c) = CROSS.captures(&low) {
            (season, episode) = (n(&c[1]), n(&c[2]));
        } else if let (Some(s), Some(e)) = (SEASON.captures(&low), EPISODE_E.captures(&following)) {
            (season, episode) = (n(&s[1]), n(&e[1]));
        } else if let Some(s) = SEASON.captures(&low).filter(|_| index > 0) {
            season = n(&s[1]);
        } else if (low == "season" || low == "saison") && digits(&following) && index > 0 {
            season = n(&following);
        } else if ["ep", "episode", "épisode"].contains(&low.as_str())
            && digits(&following)
            && index > 0
        {
            episode = n(&following);
        } else if EPISODE.is_match(&low) && index > 0 {
            episode = n(&low.chars().filter(char::is_ascii_digit).collect::<String>());
        } else if grouped && low == "-" && ABSOLUTE.is_match(&following) && index > 0 {
            episode = n(&VERSION.replace(&following, ""));
        } else if !(release_word(token) && index > 0) {
            continue;
        }
        cut = index;
        break;
    }
    let mut title_end = cut;
    for index in (1..cut).rev() {
        if YEAR_TOKEN.is_match(tokens[index])
            && let Ok(y) = tokens[index].parse::<i64>()
            && (1900..=latest_year).contains(&y)
        {
            year = tokens[index].to_string();
            title_end = index;
            break;
        }
    }
    let mut words: Vec<&str> = tokens[..title_end].to_vec();
    while words
        .last()
        .is_some_and(|w| TRAILING.contains(&w.to_lowercase().as_str()))
    {
        words.pop();
    }
    while words.first() == Some(&"-") {
        words.remove(0);
    }
    let title = words
        .join(" ")
        .trim_matches(|c| c == ' ' || c == '-' || c == '–')
        .to_string();
    let plain = comparable(&title);
    if plain.is_empty() || GENERIC.contains(&plain.as_str()) || (digits(&plain) && year.is_empty())
    {
        return other(&year);
    }
    let (mut quality, mut hdr) = (String::new(), false);
    for token in &tokens[cut..] {
        let lowered = token.to_lowercase();
        let low = lowered.split('-').next().unwrap_or("");
        if quality.is_empty() && RESOLUTION.is_match(low) {
            quality = format!("{}p", &low[..low.len() - 1]);
        } else if quality.is_empty() && (low == "4k" || low == "uhd") {
            quality = "2160p".into();
        }
        if low.starts_with("hdr") || low == "dv" || low == "dovi" {
            hdr = true;
        }
    }
    let kind = if season.is_some() || episode.is_some() {
        "series"
    } else {
        "movie"
    };
    json!({"kind": kind, "title": title, "year": year, "season": season, "episode": episode, "quality": quality, "hdr": hdr})
}

fn group_key(parsed: &Value) -> String {
    let kind = parsed["kind"].as_str().unwrap_or("other");
    if kind == "other" {
        return "other".into();
    }
    let key = format!(
        "{kind}:{}",
        comparable(parsed["title"].as_str().unwrap_or(""))
    );
    let year = parsed["year"].as_str().unwrap_or("");
    if kind == "movie" && !year.is_empty() {
        format!("{key}:{year}")
    } else {
        key
    }
}

fn bytes(v: &Value) -> i64 {
    match v {
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f as i64))
            .unwrap_or(0),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        _ => 0,
    }
}

/// Cloud files gathered by work, newest work first, « other » files last.
pub fn group_items(items: &[Value], today: NaiveDate) -> Vec<Value> {
    let mut groups: indexmap::IndexMap<String, Map<String, Value>> = indexmap::IndexMap::new();
    for item in items {
        let Some(filename) = item.get("filename").filter(|f| truthy(f)) else {
            continue;
        };
        let parsed = parse_release(&text(filename), today);
        let key = group_key(&parsed);
        let group = groups.entry(key.clone()).or_insert_with(|| {
            json!({"key": key, "kind": parsed["kind"], "title": parsed["title"], "year": parsed["year"], "files": []}).as_object().cloned().unwrap_or_default()
        });
        if !truthy(&group["year"]) && truthy(&parsed["year"]) {
            group.insert("year".into(), parsed["year"].clone());
        }
        if let Some(files) = group.get_mut("files").and_then(Value::as_array_mut)
            && files.len() < 200
        {
            let mut file = item.as_object().cloned().unwrap_or_default();
            for k in ["season", "episode", "quality", "hdr"] {
                file.insert(k.into(), parsed[k].clone());
            }
            files.push(Value::Object(file));
        }
    }
    let (works, others): (Vec<_>, Vec<_>) =
        groups.into_values().partition(|g| g["kind"] != "other");
    works
        .into_iter()
        .chain(others)
        .map(|mut group| {
            let kind = group["kind"].as_str().unwrap_or("other").to_string();
            let mut files = group["files"].as_array().cloned().unwrap_or_default();
            let lower = |f: &Value| {
                f["filename"]
                    .as_str()
                    .map(str::to_lowercase)
                    .unwrap_or_else(|| text(&f["filename"]).to_lowercase())
            };
            match kind.as_str() {
                "series" => files.sort_by_key(|f| {
                    (
                        f["season"].as_i64().unwrap_or(0),
                        f["episode"].as_i64().unwrap_or(0),
                        lower(f),
                    )
                }),
                "movie" => files.sort_by_key(|f| -bytes(&f["bytes"])),
                _ => files.sort_by_key(lower),
            }
            let seasons: BTreeSet<i64> =
                files.iter().filter_map(|f| f["season"].as_i64()).collect();
            let latest = files
                .iter()
                .map(|f| or_text(&f["generated"]))
                .max()
                .unwrap_or_default();
            group.insert("count".into(), json!(files.len()));
            group.insert(
                "bytes".into(),
                json!(files.iter().map(|f| bytes(&f["bytes"])).sum::<i64>()),
            );
            group.insert("files".into(), Value::Array(files));
            group.insert(
                "seasons".into(),
                json!(seasons.into_iter().collect::<Vec<_>>()),
            );
            group.insert("latest".into(), json!(latest));
            Value::Object(group)
        })
        .collect()
}

fn years_in(v: &Value) -> Vec<i64> {
    YEARS
        .captures_iter(&or_text(v))
        .filter_map(|c| c[1].parse().ok())
        .collect()
}

/// The catalogue entry named like the file, or None: a wrong poster is worse than none.
pub fn best_match<'a>(results: &'a [Value], title: &str, year: &str) -> Option<&'a Value> {
    let wanted = comparable(title);
    let mut best: Option<(&Value, f64)> = None;
    for meta in results {
        if !or_text(&meta["id"]).starts_with("tt") {
            continue;
        }
        let names: Vec<String> = [&meta["name"], &meta["name_fr"]]
            .iter()
            .map(|n| comparable(&or_text(n)))
            .filter(|n| !n.is_empty())
            .collect();
        if names.is_empty() {
            continue;
        }
        let ratio = names
            .iter()
            .map(|n| {
                if *n == wanted {
                    1.0
                } else {
                    crate::manga::sources::similarity(n, &wanted)
                }
            })
            .fold(f64::MIN, f64::max);
        let shown = if truthy(&meta["releaseInfo"]) {
            &meta["releaseInfo"]
        } else {
            &meta["year"]
        };
        let found = years_in(shown);
        if let (Ok(y), Some(first)) = (year.parse::<i64>(), found.first())
            && (first - y).abs() > 1
        {
            continue;
        }
        let same_year = !year.is_empty() && !found.is_empty();
        if ratio < 0.95 && !(same_year && ratio >= 0.9) {
            continue;
        }
        let rank = ratio + if same_year { 0.1 } else { 0.0 };
        if best.is_none_or(|(_, b)| rank > b) && rank > 0.0 {
            best = Some((meta, rank));
        }
    }
    best.map(|(m, _)| m)
}

/// The catalogue media of a Cloud work, {} when the catalogue has none; Err when a search failed.
pub async fn match_title(
    meta: &crate::meta::Meta,
    title: &str,
    year: &str,
    kind: &str,
) -> NetResult<Value> {
    if !["movie", "series"].contains(&kind) || comparable(title).chars().count() < 2 {
        return Ok(json!({}));
    }
    let results = meta.search(title, kind).await?;
    let mut found = best_match(&results, title, year).cloned();
    if found.is_none() {
        let french = crate::french::search(meta, title, kind).await;
        found = best_match(&french, title, year).cloned();
    }
    let Some(found) = found else {
        return Ok(json!({}));
    };
    let id = text(&found["id"]);
    let shown = if truthy(&found["releaseInfo"]) {
        &found["releaseInfo"]
    } else {
        &found["year"]
    };
    let years = years_in(shown);
    let mut media = json!({
        "id": id, "name": if truthy(&found["name"]) { found["name"].clone() } else { json!(title) }, "type": kind,
        "year": years.first().map(|y| y.to_string()).unwrap_or_else(|| year.to_string()),
        "poster": if truthy(&found["poster"]) { found["poster"].clone() } else { json!(format!("https://images.metahub.space/poster/medium/{id}/img")) },
    });
    if truthy(&found["name_fr"]) {
        media["name_fr"] = found["name_fr"].clone();
    }
    Ok(media)
}

/// {key: catalogue media or null} for up to 40 Cloud works; a match or a miss
/// is kept a week, a network failure is not.
pub async fn match_titles(meta: &crate::meta::Meta, titles: &[Value]) -> Map<String, Value> {
    static YEAR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(19|20)\d\d$").unwrap());
    let mut wanted: Vec<(String, String, String, String)> = Vec::new();
    for entry in titles.iter().take(40).filter(|e| e.is_object()) {
        let key: String = or_text(&entry["key"]).chars().take(200).collect();
        let title: String = or_text(&entry["title"]).chars().take(200).collect();
        let year = or_text(&entry["year"]);
        let kind = entry["kind"].as_str().unwrap_or("");
        if !key.is_empty() && !title.is_empty() && (kind == "movie" || kind == "series") {
            wanted.push((
                key,
                title,
                if YEAR.is_match(&year) {
                    year
                } else {
                    String::new()
                },
                kind.to_string(),
            ));
        }
    }
    let found: Vec<(String, Value)> =
        futures_util::StreamExt::collect(futures_util::StreamExt::buffered(
            futures_util::stream::iter(wanted.into_iter().map(|(key, title, year, kind)| {
                let meta = meta.clone();
                async move {
                    let cache = format!("cloud_match_v2:{kind}:{}:{year}", comparable(&title));
                    let media = meta
                        .net
                        .cached(&cache, 7.0 * 86400.0, || async {
                            match_title(&meta, &title, &year, &kind).await
                        })
                        .await
                        .ok();
                    (key, media.filter(truthy).unwrap_or(Value::Null))
                }
            })),
            6,
        ))
        .await;
    found.into_iter().collect()
}

// ---------- The account ----------

/// Age in days of an ISO date or a timestamp (0 when unknown).
fn age_days(value: &Value, at: DateTime<Utc>) -> f64 {
    let when = match value {
        Value::Number(n) => n
            .as_f64()
            .filter(|s| *s != 0.0)
            .and_then(|s| DateTime::from_timestamp(s as i64, 0)),
        Value::String(s) if !s.is_empty() => {
            let s = s.replace('Z', "+00:00");
            DateTime::parse_from_rfc3339(&s)
                .map(|d| d.with_timezone(&Utc))
                .ok()
                .or_else(|| {
                    DateTime::parse_from_str(&s, "%Y-%m-%dT%H:%M:%S%.f%:z")
                        .ok()
                        .map(|d| d.with_timezone(&Utc))
                })
                .or_else(|| {
                    DateTime::parse_from_str(&s, "%Y-%m-%d %H:%M:%S%.f%:z")
                        .ok()
                        .map(|d| d.with_timezone(&Utc))
                })
                .or_else(|| {
                    ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%d %H:%M:%S%.f"]
                        .iter()
                        .find_map(|f| NaiveDateTime::parse_from_str(&s, f).ok())
                        .map(|d| d.and_utc())
                })
                .or_else(|| {
                    NaiveDate::parse_from_str(&s, "%Y-%m-%d")
                        .ok()
                        .and_then(|d| d.and_hms_opt(0, 0, 0))
                        .map(|d| d.and_utc())
                })
        }
        _ => None,
    };
    when.map(|w| ((at - w).num_milliseconds() as f64 / 86_400_000.0).max(0.0))
        .unwrap_or(0.0)
}

fn day_of(ts: &Value) -> String {
    ts.as_f64()
        .filter(|t| *t != 0.0)
        .and_then(|t| DateTime::from_timestamp(t as i64, 0))
        .map(|d| d.format("%Y-%m-%d").to_string())
        .unwrap_or_default()
}

pub struct Account<'a> {
    pub net: &'a Net,
    pub provider: String,
    pub token: String,
    pub hosts: Hosts,
    pub port: u16,
}

impl Account<'_> {
    async fn call(
        &self,
        method: reqwest::Method,
        url: &str,
        extra: &[(&str, &str)],
        body: Option<&Value>,
    ) -> NetResult<Value> {
        let mut headers = vec![
            ("User-Agent", BROWSER),
            ("Accept", "application/json, text/plain, */*"),
        ];
        headers.extend_from_slice(extra);
        self.net
            .request_json(method, url, &headers, body, Duration::from_secs(20), 3)
            .await
    }

    fn bearer(&self) -> String {
        format!("Bearer {}", self.token)
    }

    /// The account's files, newest first, as the Cloud tab lists them.
    pub async fn downloads(&self, limit: usize) -> NetResult<Vec<Value>> {
        if self.token.is_empty() {
            return Ok(vec![]);
        }
        let at = Utc::now();
        let h = &self.hosts;
        let bearer = self.bearer();
        let auth = [("Authorization", bearer.as_str())];
        let entry = |id: String,
                     name: Value,
                     size: i64,
                     download: Value,
                     generated: String,
                     age: f64| {
            json!({"id": id, "ids": [id], "filename": name, "filesize": format_size(size as f64), "bytes": size, "download": download,
                   "generated": generated, "age_days": py_round(age, 1)})
        };
        let mut items = Vec::new();
        match self.provider.as_str() {
            "alldebrid" => {
                let query = form_urlencoded::Serializer::new(String::new())
                    .append_pair("agent", "KINO")
                    .append_pair("apikey", &self.token)
                    .finish();
                let res = self
                    .call(
                        reqwest::Method::GET,
                        &format!("{}/magnet/status?{query}", h.alldebrid),
                        &[],
                        None,
                    )
                    .await?;
                let magnets: Vec<Value> = match &res["data"]["magnets"] {
                    Value::Object(m) => m.values().cloned().collect(),
                    Value::Array(a) => a.clone(),
                    _ => vec![],
                };
                for m in magnets.iter().take(limit) {
                    if m["statusCode"] != 4 {
                        continue;
                    }
                    let id = m.get("id").map(text).unwrap_or_default();
                    if let Some(first) = m["links"].as_array().and_then(|l| l.first()) {
                        let name = [&first["filename"], &m["filename"]]
                            .into_iter()
                            .find(|v| truthy(v))
                            .cloned()
                            .unwrap_or(json!("Fichier AllDebrid"));
                        let size = bytes(
                            [&first["size"], &m["size"]]
                                .into_iter()
                                .find(|v| truthy(v))
                                .unwrap_or(&json!(0)),
                        );
                        let raw = first["link"].as_str().unwrap_or("");
                        let download = if raw.is_empty() {
                            String::new()
                        } else {
                            format!(
                                "http://127.0.0.1:{}/api/ad-dl?link={}",
                                self.port,
                                py_quote(raw)
                            )
                        };
                        items.push(entry(
                            id,
                            name,
                            size,
                            json!(download),
                            day_of(&m["uploadDate"]),
                            age_days(&m["uploadDate"], at),
                        ));
                    }
                }
            }
            "debridlink" => {
                let res = self
                    .call(
                        reqwest::Method::GET,
                        &format!("{}/seedbox/list?perPage={limit}", h.debridlink),
                        &auth,
                        None,
                    )
                    .await?;
                for t in res["value"].as_array().into_iter().flatten() {
                    let files = t["files"].as_array().cloned().unwrap_or_default();
                    let target = files
                        .iter()
                        .find(|f| truthy(&f["downloadUrl"]))
                        .or(files.first());
                    let Some(target) = target.filter(|t| truthy(&t["downloadUrl"])) else {
                        continue;
                    };
                    let name = [&target["name"], &t["name"]]
                        .into_iter()
                        .find(|v| truthy(v))
                        .cloned()
                        .unwrap_or(json!("Fichier Debrid-Link"));
                    let size = bytes(
                        [&target["size"], &t["totalSize"]]
                            .into_iter()
                            .find(|v| truthy(v))
                            .unwrap_or(&json!(0)),
                    );
                    items.push(entry(
                        t.get("id").map(text).unwrap_or_default(),
                        name,
                        size,
                        target["downloadUrl"].clone(),
                        day_of(&t["created"]),
                        age_days(&t["created"], at),
                    ));
                }
            }
            "torbox" => {
                let res = self
                    .call(
                        reqwest::Method::GET,
                        &format!("{}/torrents/mylist?bypass_cache=true", h.torbox),
                        &auth,
                        None,
                    )
                    .await?;
                for t in res["data"].as_array().into_iter().flatten().take(limit) {
                    let files = t["files"].as_array().cloned().unwrap_or_default();
                    if !truthy(&t["download_present"]) || files.is_empty() {
                        continue;
                    }
                    let largest = files
                        .iter()
                        .rev()
                        .max_by_key(|f| bytes(&f["size"]))
                        .cloned()
                        .unwrap_or_default();
                    let id = t.get("id").map(text).unwrap_or_default();
                    let file_id = largest.get("id").map(text).unwrap_or_else(|| "0".into());
                    let name = [&largest["short_name"], &largest["name"], &t["name"]]
                        .into_iter()
                        .find(|v| truthy(v))
                        .cloned()
                        .unwrap_or(json!("Fichier TorBox"));
                    let size = bytes(
                        [&largest["size"], &t["size"]]
                            .into_iter()
                            .find(|v| truthy(v))
                            .unwrap_or(&json!(0)),
                    );
                    let created: String = or_text(&t["created_at"]).chars().take(10).collect();
                    let download = format!(
                        "http://127.0.0.1:{}/api/torbox-dl?torrent_id={id}&file_id={file_id}",
                        self.port
                    );
                    items.push(entry(
                        id,
                        name,
                        size,
                        json!(download),
                        created,
                        age_days(&t["created_at"], at),
                    ));
                }
            }
            "premiumize" => {
                let query = form_urlencoded::Serializer::new(String::new())
                    .append_pair("apikey", &self.token)
                    .finish();
                let res = self
                    .call(
                        reqwest::Method::GET,
                        &format!("{}/transfer/list?{query}", h.premiumize),
                        &[],
                        None,
                    )
                    .await?;
                for t in res["transfers"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .take(limit)
                {
                    let link = or_text(&t["link"]);
                    if link.is_empty() {
                        continue;
                    }
                    let id = t.get("id").map(text).unwrap_or_default();
                    let name = if truthy(&t["name"]) {
                        t["name"].clone()
                    } else {
                        json!("Fichier Premiumize")
                    };
                    items.push(json!({"id": id, "ids": [id], "filename": name, "filesize": "Cloud PM", "download": link, "generated": "", "age_days": 0.0}));
                }
            }
            "realdebrid" => {
                let data = self
                    .call(
                        reqwest::Method::GET,
                        &format!("{}/downloads?limit={limit}", h.realdebrid),
                        &auth,
                        None,
                    )
                    .await?;
                let Some(list) = data.as_array() else {
                    return Ok(vec![]);
                };
                let mut by_name: HashMap<String, usize> = HashMap::new();
                for d in list {
                    let (name, download, id) = (
                        or_text(&d["filename"]),
                        or_text(&d["download"]),
                        d["id"].clone(),
                    );
                    if name.is_empty() || download.is_empty() || !truthy(&id) {
                        continue;
                    }
                    if let Some(index) = by_name.get(&name) {
                        if let Some(ids) = items[*index]["ids"].as_array_mut() {
                            ids.push(id);
                        }
                        continue;
                    }
                    let generated: String = or_text(&d["generated"]).chars().take(10).collect();
                    let size = bytes(&d["filesize"]);
                    by_name.insert(name.clone(), items.len());
                    items.push(json!({"id": id, "ids": [id], "filename": name, "filesize": format_size(size as f64), "bytes": size, "download": download,
                                      "generated": generated, "age_days": py_round(age_days(&d["generated"], at), 1)}));
                }
            }
            _ => {}
        }
        Ok(items)
    }

    /// Deletes these files; returns how many the service accepted.
    pub async fn delete(&self, ids: &[Value]) -> usize {
        if self.token.is_empty() {
            return 0;
        }
        let h = &self.hosts;
        let bearer = self.bearer();
        let auth = [("Authorization", bearer.as_str())];
        let mut deleted = 0;
        for id in ids {
            let shown = text(id);
            let done = match self.provider.as_str() {
                "alldebrid" => {
                    let q = form_urlencoded::Serializer::new(String::new())
                        .append_pair("agent", "KINO")
                        .append_pair("apikey", &self.token)
                        .append_pair("id", &shown)
                        .finish();
                    self.call(
                        reqwest::Method::GET,
                        &format!("{}/magnet/delete?{q}", h.alldebrid),
                        &[],
                        None,
                    )
                    .await
                    .is_ok()
                }
                "debridlink" => self
                    .call(
                        reqwest::Method::DELETE,
                        &format!("{}/seedbox/{shown}/remove", h.debridlink),
                        &auth,
                        None,
                    )
                    .await
                    .is_ok(),
                "torbox" => {
                    let torrent = if digits(&shown) {
                        json!(shown.parse::<i64>().unwrap_or(0))
                    } else {
                        id.clone()
                    };
                    let body = json!({"torrent_id": torrent, "operation": "delete"});
                    self.call(
                        reqwest::Method::POST,
                        &format!("{}/torrents/controltorrent", h.torbox),
                        &auth,
                        Some(&body),
                    )
                    .await
                    .is_ok()
                }
                "premiumize" => {
                    let q = form_urlencoded::Serializer::new(String::new())
                        .append_pair("apikey", &self.token)
                        .finish();
                    let headers = [
                        ("User-Agent", BROWSER),
                        ("Accept", "application/json, text/plain, */*"),
                    ];
                    self.net
                        .request_form(
                            &format!("{}/transfer/delete?{q}", h.premiumize),
                            &headers,
                            &[("id", shown.as_str())],
                            Duration::from_secs(20),
                            3,
                        )
                        .await
                        .is_ok()
                }
                _ => {
                    if !truthy(id) {
                        continue;
                    }
                    self.call(
                        reqwest::Method::DELETE,
                        &format!("{}/downloads/delete/{shown}", h.realdebrid),
                        &auth,
                        None,
                    )
                    .await
                    .is_ok()
                }
            };
            if done {
                deleted += 1;
            }
        }
        deleted
    }

    /// Deletes what is older than `days` (-1: everything); Real-Debrid's torrents too.
    pub async fn cleanup(&self, days: i64) -> Value {
        let none = json!({"deleted_downloads": 0, "deleted_torrents": 0});
        if self.token.is_empty() || days == 0 {
            return none;
        }
        let old = |age: f64| days == -1 || age >= days as f64;
        if self.provider != "realdebrid" {
            let Ok(items) = self.downloads(100).await else {
                return json!({"deleted_downloads": 0, "deleted_torrents": 0});
            };
            let ids: Vec<Value> = items
                .iter()
                .filter(|i| old(i["age_days"].as_f64().unwrap_or(0.0)))
                .map(|i| i["id"].clone())
                .collect();
            let count = if ids.is_empty() {
                0
            } else {
                self.delete(&ids).await
            };
            return json!({"deleted_downloads": count, "deleted_torrents": 0});
        }
        let at = Utc::now();
        let bearer = self.bearer();
        let auth = [("Authorization", bearer.as_str())];
        let mut counts = [0usize; 2];
        for (slot, (list, date, delete)) in [
            ("downloads", "generated", "downloads/delete"),
            ("torrents", "added", "torrents/delete"),
        ]
        .into_iter()
        .enumerate()
        {
            let Ok(Value::Array(rows)) = self
                .call(
                    reqwest::Method::GET,
                    &format!("{}/{list}?limit=100", self.hosts.realdebrid),
                    &auth,
                    None,
                )
                .await
            else {
                continue;
            };
            for row in rows {
                if !truthy(&row["id"]) || !old(age_days(&row[date], at)) {
                    continue;
                }
                let url = format!("{}/{delete}/{}", self.hosts.realdebrid, text(&row["id"]));
                if self
                    .call(reqwest::Method::DELETE, &url, &auth, None)
                    .await
                    .is_ok()
                {
                    counts[slot] += 1;
                }
            }
        }
        json!({"deleted_downloads": counts[0], "deleted_torrents": counts[1]})
    }
}

/// The retention cleanup that opening the Cloud starts: once every six hours per service.
pub async fn auto_cleanup(account: &Account<'_>, days: i64) -> Value {
    static LAST: LazyLock<Mutex<HashMap<String, f64>>> = LazyLock::new(Default::default);
    let at = crate::db::now();
    {
        let mut last = LAST.lock().unwrap_or_else(|e| e.into_inner());
        if last
            .get(&account.provider)
            .is_some_and(|l| at - l < 6.0 * 3600.0)
        {
            return json!({"deleted_downloads": 0, "deleted_torrents": 0});
        }
        last.insert(account.provider.clone(), at);
    }
    account.cleanup(days).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ages() {
        let at = DateTime::parse_from_rfc3339("2026-11-17T12:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        assert_eq!(age_days(&json!("2026-11-16T12:00:00.000Z"), at), 1.0);
        assert_eq!(age_days(&json!("2026-11-07"), at), 10.5);
        assert_eq!(age_days(&json!(""), at), 0.0);
        assert_eq!(
            age_days(&json!("2030-01-01T00:00:00Z"), at),
            0.0,
            "never negative"
        );
    }
}
