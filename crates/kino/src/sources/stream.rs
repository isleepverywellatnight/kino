//! From the indexers' answers to the ranked list: each Stremio stream read as a
//! release, one entry per torrent, the releases that fit, and their order.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use super::release::{INSTANT_BADGES, details};
use super::relevance::{is_valid_release_for_show, plausible_size, release_years};
use super::score::{self, Profile};

pub const MERGE_ORDER: [&str; 9] = [
    "fr",
    "anime",
    "main",
    "addons",
    "comet",
    "knightcrawler",
    "tpb",
    "peerflix",
    "apibay",
];

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect(pattern)
}

static SEEDER_ICON: LazyLock<Regex> = LazyLock::new(|| re(r"[👤👥]\s*(\d+)"));
static FLAGS: LazyLock<Regex> = LazyLock::new(|| re(r"[\x{1F1E6}-\x{1F1FF}]{2}"));
static DOUBLE_BULLET: LazyLock<Regex> = LazyLock::new(|| re(r"\s*•\s*•\s*"));
static SPACES: LazyLock<Regex> = LazyLock::new(|| re(r"\s+"));
static LEADING_EMOJI: LazyLock<Regex> =
    LazyLock::new(|| re(r"^[\s\x{1F300}-\x{1F9FF}\x{2600}-\x{26FF}\x{2700}-\x{27BF}]+\s*"));
static SEED_ICON: LazyLock<Regex> = LazyLock::new(|| re(r"👤\s*(\d+)"));
static SEED_WORD: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\b(\d+)\s*(?:seeders?|seeds?|pairs?)\b"));
static GB_ICON: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)💾\s*([\d\.]+)\s*GB"));
static GB: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)([\d\.]+)\s*(?:GB|GiB)"));
static MB_ICON: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)💾\s*([\d\.]+)\s*MB"));
static MB: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)([\d\.]+)\s*(?:MB|MiB)"));
static HASH_IN_URL: LazyLock<Regex> = LazyLock::new(|| re(r"/([a-fA-F0-9]{40})/"));

/// Python's round(x, n): the correctly rounded decimal.
pub fn py_round(x: f64, digits: usize) -> f64 {
    format!("{x:.digits$}").parse().unwrap_or(x)
}

/// Python's str() of a float (« 4.0 », « 3.53 »).
pub fn py_float(x: f64) -> String {
    if x.is_finite() && x.fract() == 0.0 && x.abs() < 1e16 {
        format!("{x:.1}")
    } else {
        format!("{x}")
    }
}

/// Python's urllib.parse.quote (« / » kept).
pub fn py_quote(text: &str) -> String {
    text.bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"_.-~/".contains(&b) {
                (b as char).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect()
}

pub fn clean_meta_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut t = SEEDER_ICON.replace_all(text, "$1 seeders").into_owned();
    for (from, to) in [
        ("💾", " • "),
        ("⚙️", " • "),
        ("📹", " • "),
        ("🔎", " • "),
        ("🏷️", " • "),
        ("⭐", " • "),
        ("📄", ""),
        ("🔊", " • "),
    ] {
        t = t.replace(from, to);
    }
    let t = FLAGS.replace_all(&t, "");
    let t = DOUBLE_BULLET.replace_all(&t, " • ");
    let t = SPACES.replace_all(&t, " ");
    t.trim_matches(|c| c == ' ' || c == '•' || c == '/')
        .to_string()
}

fn int_of(value: &Value) -> i64 {
    match value {
        Value::Number(n) => n
            .as_i64()
            .unwrap_or_else(|| n.as_f64().unwrap_or(0.0) as i64),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        Value::Bool(b) => i64::from(*b),
        _ => 0,
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

/// `a or b or c` in Python.
fn first_truthy<'a>(values: &[&'a Value]) -> Option<&'a Value> {
    values.iter().copied().find(|v| truthy(v))
}

/// One Stremio stream as a release; None when it has neither a torrent nor a link.
pub fn parse_stremio_stream(s: &Value, default_source: &str) -> Option<Value> {
    let hints = &s["behaviorHints"];
    let raw_title = first_truthy(&[&s["title"], &s["description"]])
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let source_tag = first_truthy(&[&s["name"]])
        .and_then(Value::as_str)
        .unwrap_or(default_source)
        .replace('\n', " ")
        .trim()
        .to_string();
    let lines: Vec<&str> = raw_title
        .split('\n')
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    let mut release_name = lines.first().copied().unwrap_or("Stream").to_string();
    match hints["filename"].as_str().filter(|f| !f.is_empty()) {
        Some(file) => release_name = file.to_string(),
        None => release_name = LEADING_EMOJI.replace(&release_name, "").trim().to_string(),
    }
    // Some indexers send HTML entities (« Les Mis&eacute;rables »).
    let release_name = html_escape::decode_html_entities(&release_name).into_owned();
    let meta_info = if lines.len() > 1 {
        clean_meta_text(&lines[1..].join(" • "))
    } else {
        String::new()
    };

    let mut seeders = int_of(first_truthy(&[&s["seed"], &s["seeders"]]).unwrap_or(&Value::Null));
    if seeders == 0 {
        let found = SEED_ICON
            .captures(&raw_title)
            .or_else(|| SEED_WORD.captures(&raw_title));
        seeders = found.and_then(|c| c[1].parse().ok()).unwrap_or(0);
    }
    let size_bytes = int_of(
        first_truthy(&[&s["sizebytes"], &s["size"], &hints["videoSize"]]).unwrap_or(&Value::Null),
    );
    let (mut size_gb, mut size_str) = (0.0, String::new());
    if size_bytes > 0 {
        size_gb = py_round(size_bytes as f64 / (1024.0 * 1024.0 * 1024.0), 2);
        size_str = format!("{} GB", py_float(size_gb));
    } else {
        let gb = GB_ICON
            .captures(&raw_title)
            .or_else(|| GB.captures(&raw_title));
        let mb = MB_ICON
            .captures(&raw_title)
            .or_else(|| MB.captures(&raw_title));
        if let Some(c) = gb
            .as_ref()
            .and_then(|c| c[1].parse::<f64>().ok().map(|v| (c, v)))
        {
            size_gb = py_round(c.1, 2);
            size_str = format!("{} GB", py_float(size_gb));
        } else if let Some((c, v)) = mb
            .as_ref()
            .and_then(|c| c[1].parse::<f64>().ok().map(|v| (c, v)))
        {
            size_gb = py_round(v / 1024.0, 3);
            size_str = format!("{} MB", &c[1]);
        }
    }
    let resolve_url = s["url"].as_str().unwrap_or("").to_string();
    let mut info_hash = s["infoHash"].as_str().unwrap_or("").to_lowercase();
    if info_hash.is_empty()
        && !resolve_url.is_empty()
        && let Some(c) = HASH_IN_URL.captures(&resolve_url)
    {
        info_hash = c[1].to_lowercase();
    }
    if info_hash.is_empty() && resolve_url.is_empty() {
        return None;
    }
    // The indexer's name (« Torrentio FR ») does not describe the file's audio.
    let parsed = details(&release_name, &meta_info);
    let instant = parsed["is_instant"].as_bool() == Some(true)
        || details(&source_tag, "")["is_instant"].as_bool() == Some(true)
        || INSTANT_BADGES.iter().any(|b| {
            source_tag
                .to_lowercase()
                .contains(&format!("[{}]", b.to_lowercase()))
        });
    let magnet = if info_hash.is_empty() {
        resolve_url.clone()
    } else {
        format!(
            "magnet:?xt=urn:btih:{info_hash}&dn={}",
            py_quote(&release_name)
        )
    };
    let prefix = format!("{default_source} • ");
    let display_meta = if let Some(rest) = meta_info.strip_prefix(&prefix) {
        rest.to_string()
    } else if meta_info == default_source {
        String::new()
    } else {
        meta_info.clone()
    };
    Some(json!({
        "source": default_source, "title": release_name, "meta": display_meta, "qualities": parsed["display_badges"],
        "langs": parsed["langs"], "magnet": magnet, "resolve_url": resolve_url, "is_instant": instant, "info_hash": info_hash,
        "file_idx": s.get("fileIdx").cloned().unwrap_or(Value::Null), "seeders": seeders, "size_gb": size_gb, "size_str": size_str,
        "parsed_details": parsed,
    }))
}

/// One entry per info hash. A duplicate adds its languages, and its instant
/// availability (with its link and qualities) when the first copy had none.
pub fn merge(buckets: &Map<String, Value>) -> Vec<Value> {
    let mut order: Vec<String> = MERGE_ORDER.iter().map(|s| s.to_string()).collect();
    order.extend(
        buckets
            .keys()
            .filter(|k| !MERGE_ORDER.contains(&k.as_str()))
            .cloned(),
    );
    let mut merged: Vec<Value> = Vec::new();
    let mut seen: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    for key in order {
        for item in buckets
            .get(&key)
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let hash = item["info_hash"].as_str().unwrap_or("").to_lowercase();
            if hash.is_empty() {
                merged.push(item.clone());
                continue;
            }
            if let Some(&at) = seen.get(&hash) {
                let existing = &mut merged[at];
                if item["is_instant"].as_bool() == Some(true)
                    && existing["is_instant"].as_bool() != Some(true)
                {
                    existing["is_instant"] = json!(true);
                    let link = first_truthy(&[&item["resolve_url"], &existing["resolve_url"]])
                        .cloned()
                        .unwrap_or(Value::Null);
                    existing["resolve_url"] = link;
                    for q in item["qualities"].as_array().cloned().unwrap_or_default() {
                        if let Some(list) = existing["qualities"].as_array_mut()
                            && !list.contains(&q)
                        {
                            list.insert(0, q);
                        }
                    }
                }
                for lang in item["langs"].as_array().cloned().unwrap_or_default() {
                    if let Some(list) = existing["langs"].as_array_mut()
                        && !list.contains(&lang)
                    {
                        list.push(lang);
                    }
                }
                continue;
            }
            seen.insert(hash, merged.len());
            merged.push(item.clone());
        }
    }
    merged
}

pub struct Wanted<'a> {
    pub media_type: &'a str,
    pub season: i64,
    pub episode: i64,
    pub absolute_episode: Option<i64>,
    pub runtime_minutes: Option<f64>,
    pub titles: &'a [String],
    pub release_year: &'a str,
    pub pref_lang: &'a str,
    pub pref_quality: &'a str,
}

/// Drops fakes, CAMs, other shows and other episodes; explains each release kept.
pub fn keep_relevant(items: Vec<Value>, w: &Wanted) -> Vec<Value> {
    let episodic = ["series", "anime", "tv"].contains(&w.media_type);
    let mut kept = Vec::new();
    for mut t in items {
        if !plausible_size(&t, w.media_type, w.runtime_minutes) {
            continue;
        }
        let title = t["title"].as_str().unwrap_or("").to_string();
        if !w.titles.is_empty()
            && !w
                .titles
                .iter()
                .filter(|n| !n.is_empty())
                .any(|name| is_valid_release_for_show(&title, name, w.release_year))
        {
            continue;
        }
        let status = if episodic {
            score::episode_status(&title, w.season, w.episode, w.absolute_episode)
        } else {
            ""
        };
        if status == "mismatch" {
            continue;
        }
        t["recommendation"] = score::explain(
            &t,
            w.pref_lang,
            w.pref_quality,
            !w.titles.is_empty(),
            status,
        );
        kept.push(t);
    }
    kept
}

/// Scores each release (its parts and their reasons), then sorts: « score »,
/// « seeders », « size » or « quality ».
pub fn rank(
    mut items: Vec<Value>,
    sort_by: &str,
    w: &Wanted,
    hdr_mode: &str,
    source_priority: &str,
) -> Vec<Value> {
    let mut title_years: Vec<i64> = w.titles.iter().flat_map(|n| release_years(n)).collect();
    title_years.sort_unstable();
    title_years.dedup();
    let profile = Profile {
        pref_lang: w.pref_lang.into(),
        pref_quality: w.pref_quality.into(),
        hdr_mode: hdr_mode.into(),
        media_type: w.media_type.into(),
        runtime_minutes: w.runtime_minutes,
        target_year: w.release_year.into(),
        title_years,
        priority: if source_priority == "quality" {
            "quality".into()
        } else {
            "speed".into()
        },
    };
    for t in items.iter_mut() {
        let d = score::release_details(t);
        let parts = score::parts(t, &d, &profile);
        t["score"] = json!(parts.iter().map(|(_, p)| p).sum::<i64>());
        t["score_parts"] = Value::Object(score::parts_map(&parts));
        t["why"] = Value::Array(score::reasons(t, &d, &parts, &profile));
    }
    let instant = |x: &Value| x["is_instant"].as_bool() == Some(true);
    let num = |v: &Value| {
        v.as_f64()
            .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            .unwrap_or(0.0)
    };
    // Python's sort is stable, and reverse=True keeps equal items in their order.
    match sort_by {
        "seeders" => items.sort_by(|a, b| {
            (instant(b), int_of(&b["seeders"])).cmp(&(instant(a), int_of(&a["seeders"])))
        }),
        "size" => items.sort_by(|a, b| {
            instant(b)
                .cmp(&instant(a))
                .then(num(&b["size_gb"]).total_cmp(&num(&a["size_gb"])))
        }),
        "quality" => {
            let rank = |x: &Value| match x["parsed_details"]["resolution"].as_str() {
                Some("4K") => 4,
                Some("1080p") => 3,
                Some("720p") => 2,
                Some("SD") => 1,
                _ => 0,
            };
            items.sort_by(|a, b| {
                (instant(b), rank(b), int_of(&b["score"])).cmp(&(
                    instant(a),
                    rank(a),
                    int_of(&a["score"]),
                ))
            });
        }
        _ => items.sort_by_key(|x| std::cmp::Reverse(int_of(&x["score"]))),
    }
    items
}
