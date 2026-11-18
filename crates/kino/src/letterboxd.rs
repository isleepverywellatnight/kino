//! Letterboxd: a watchlist and the films seen imported from a public profile
//! or an exported CSV file, custom lists, and KINO's own selections. Each
//! entry is matched to the catalogue without guessing: a wrong film is worse
//! than none.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;
use std::time::Duration;

use futures_util::StreamExt;
use regex::Regex;
use serde_json::{Value, json};
use unicode_normalization::UnicodeNormalization;

use crate::db::Db;
use crate::meta::Meta;
use crate::net::NetError;

const AGENT: &str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
const SAFARI: &str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15";
static TITLE_YEAR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)^(.*?)\s*\((\d{4})\)\s*$").unwrap());
static NOT_ALNUM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9]+").unwrap());
static CONTAINER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?s)<li[^>]*class="[^"]*poster-container[^"]*"[^>]*>(.*?)</li>"#).unwrap()
});
static FILM_NAME: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"data-film-name="([^"]+)""#).unwrap());
static FILM_YEAR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"data-film-release-year="(\d{4})""#).unwrap());
static ITEM_NAME: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"data-item-name="([^"]+)""#).unwrap());
static RATED_CLASS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"class="[^"]*rating\s+rated-(\d+)[^"]*""#).unwrap());
static RATED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"rated-(\d+)").unwrap());
static DATA_RATING: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"data-rating="([0-9.]+)""#).unwrap());
static FILM_PAIR: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"data-film-name="([^"]+)"[^>]*?(?:data-film-release-year="(\d{4})")?"#).unwrap()
});
static RSS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"<letterboxd:filmTitle>([^<]+)</letterboxd:filmTitle>\s*(?:<letterboxd:filmYear>(\d{4})</letterboxd:filmYear>)?(?:\s*<letterboxd:memberRating>([0-9.]+)</letterboxd:memberRating>)?").unwrap()
});
static OG_TITLE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"<meta property="og:title" content="([^"]+)""#).unwrap());
static H1: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<h1[^>]*>([^<]+)</h1>").unwrap());
static OG_DESCRIPTION: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"<meta property="og:description" content="([^"]+)""#).unwrap());
static LIST_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-zA-Z0-9_\-]").unwrap());

pub fn curated() -> Value {
    serde_json::from_str(include_str!("data/community.json")).unwrap_or(json!([]))
}

fn unescape(text: &str) -> String {
    html_escape::decode_html_entities(text).into_owned()
}

fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false))
        && v != &json!(0)
        && v != &json!("")
        && v != &json!([])
        && v != &json!({})
}

fn text(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// « A Ghost Story (2017) » → (« A Ghost Story », « 2017 »).
pub fn title_year(raw: &str) -> (String, String) {
    let s = unescape(raw.trim());
    if s.is_empty() {
        return (String::new(), String::new());
    }
    match TITLE_YEAR.captures(&s) {
        Some(c) => (c[1].trim().to_string(), c[2].trim().to_string()),
        None => (s, String::new()),
    }
}

/// A note out of 5 as text: « 4 », « 4.5 ».
fn note(value: f64) -> String {
    format!("{value:.1}")
        .trim_end_matches('0')
        .trim_end_matches('.')
        .to_string()
}

#[derive(Clone, Debug, PartialEq)]
pub struct Entry {
    pub title: String,
    pub year: String,
    pub imdb_id: String,
    pub user_rating: String,
}

/// The films of a Letterboxd export (or of an IMDb export, or plain lines).
pub fn parse_csv(raw: &str) -> Vec<Entry> {
    let text = raw.trim_start_matches('\u{feff}').trim();
    if text.is_empty() {
        return vec![];
    }
    let rows: Vec<Vec<String>> = csv::ReaderBuilder::new()
        .has_headers(false)
        .flexible(true)
        .from_reader(text.as_bytes())
        .records()
        .filter_map(Result::ok)
        .map(|r| r.iter().map(str::to_string).collect::<Vec<_>>())
        .filter(|r| r.iter().any(|c| !c.trim().is_empty()))
        .collect();
    let Some(first) = rows.first() else {
        return vec![];
    };
    let header: Vec<String> = first.iter().map(|c| c.trim().to_lowercase()).collect();
    let find = |names: &[&str]| header.iter().position(|h| names.contains(&h.as_str()));
    let name_at = find(&["name", "title", "film", "movie", "original title"]);
    let year_at = find(&["year", "release year", "année"]);
    let imdb_at = find(&["const", "imdb", "imdb_id", "tconst"]);
    let rating_at = find(&[
        "rating",
        "your rating",
        "user rating",
        "note",
        "user_rating",
        "my rating",
        "member rating",
    ]);
    let mut entries: Vec<Entry> = Vec::new();
    let mut seen: HashMap<String, usize> = HashMap::new();
    if let Some(name_at) = name_at {
        for r in &rows[1..] {
            let Some(raw_title) = r.get(name_at).map(|t| t.trim()).filter(|t| !t.is_empty()) else {
                continue;
            };
            let (title, parsed_year) = title_year(raw_title);
            let year = year_at
                .and_then(|i| r.get(i))
                .map(|y| y.trim().to_string())
                .unwrap_or(parsed_year);
            let imdb_id = imdb_at
                .and_then(|i| r.get(i))
                .map(|y| y.trim().to_string())
                .unwrap_or_default();
            let mut user_rating = String::new();
            if let Some(raw) = rating_at
                .and_then(|i| r.get(i))
                .map(|v| v.trim())
                .filter(|v| !v.is_empty())
                && let Ok(mut value) = raw.replace(',', ".").parse::<f64>()
                && value > 0.0
            {
                if value > 5.0 {
                    value = crate::sources::stream::py_round(value / 2.0, 1);
                }
                user_rating = note(value);
            }
            let key = format!(
                "{}|{year}",
                if imdb_id.is_empty() {
                    title.to_lowercase()
                } else {
                    imdb_id.clone()
                }
            );
            match seen.get(&key) {
                None => {
                    seen.insert(key, entries.len());
                    entries.push(Entry {
                        title,
                        year,
                        imdb_id,
                        user_rating,
                    });
                }
                Some(i) if !user_rating.is_empty() && entries[*i].user_rating.is_empty() => {
                    entries[*i].user_rating = user_rating
                }
                _ => {}
            }
        }
        return entries;
    }
    for r in &rows {
        let line = r
            .iter()
            .map(|c| c.trim())
            .filter(|c| !c.is_empty())
            .collect::<Vec<_>>()
            .join(" ");
        if line.is_empty() || line.to_lowercase().starts_with("date,name") {
            continue;
        }
        let (title, year) = title_year(&line);
        if !title.is_empty() {
            let key = format!("{}|{year}", title.to_lowercase());
            if let std::collections::hash_map::Entry::Vacant(slot) = seen.entry(key) {
                slot.insert(entries.len());
                entries.push(Entry {
                    title,
                    year,
                    imdb_id: String::new(),
                    user_rating: String::new(),
                });
            }
        }
    }
    entries
}

/// The films of one page of a public profile (grid, list, or RSS).
pub fn parse_page(body: &str, seen: &mut HashSet<String>) -> Vec<Entry> {
    let mut found = Vec::new();
    let mut add = |title: String, year: String, rating: String, found: &mut Vec<Entry>| {
        if !title.is_empty() && seen.insert(format!("{}|{year}", title.to_lowercase())) {
            found.push(Entry {
                title,
                year,
                imdb_id: String::new(),
                user_rating: rating,
            });
        }
    };
    for c in CONTAINER.captures_iter(body) {
        let block = &c[1];
        let (mut title, mut year, mut rating) = (String::new(), String::new(), String::new());
        if let Some(m) = FILM_NAME.captures(block) {
            title = unescape(m[1].trim());
            if let Some(y) = FILM_YEAR.captures(block) {
                year = y[1].trim().to_string();
            }
        }
        if title.is_empty()
            && let Some(m) = ITEM_NAME.captures(block)
        {
            (title, year) = title_year(&unescape(m[1].trim()));
        }
        if !title.is_empty() {
            if let Some(r) = RATED_CLASS
                .captures(block)
                .or_else(|| RATED.captures(block))
            {
                if let Ok(v) = r[1].parse::<i64>() {
                    rating = note(v as f64 / 2.0);
                }
            } else if let Some(r) = DATA_RATING.captures(block)
                && let Ok(v) = r[1].parse::<f64>()
            {
                rating = note(v);
            }
        }
        add(title, year, rating, &mut found);
    }
    if found.is_empty() {
        for m in ITEM_NAME.captures_iter(body) {
            let (title, year) = title_year(&m[1]);
            add(title, year, String::new(), &mut found);
        }
    }
    if found.is_empty() {
        for m in FILM_PAIR.captures_iter(body) {
            let year = m
                .get(2)
                .map(|y| y.as_str().trim().to_string())
                .unwrap_or_default();
            add(unescape(m[1].trim()), year, String::new(), &mut found);
        }
    }
    if found.is_empty() && body.contains("<letterboxd:filmTitle>") {
        for m in RSS.captures_iter(body) {
            let year = m
                .get(2)
                .map(|y| y.as_str().trim().to_string())
                .unwrap_or_default();
            let rating = m
                .get(3)
                .and_then(|r| r.as_str().trim().parse::<f64>().ok())
                .map(note)
                .unwrap_or_default();
            add(unescape(m[1].trim()), year, rating, &mut found);
        }
    }
    found
}

/// The page of a profile's watchlist or films seen, from a name or a link.
fn profile_url(raw: &str, section: &str) -> String {
    let sec = if section == "watched" {
        "films"
    } else {
        "watchlist"
    };
    if !raw.starts_with("http://") && !raw.starts_with("https://") {
        let user = raw
            .trim_start_matches('@')
            .trim_matches('/')
            .split('/')
            .next()
            .unwrap_or("")
            .trim();
        return format!("https://letterboxd.com/{user}/{sec}/");
    }
    let base = format!(
        "{}/",
        raw.split('?').next().unwrap_or("").trim_end_matches('/')
    );
    let parts: Vec<String> = reqwest::Url::parse(&base)
        .map(|u| {
            u.path()
                .split('/')
                .filter(|p| !p.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    if parts.len() == 1 && base.contains("letterboxd.com") {
        return format!("https://letterboxd.com/{}/{sec}/", parts[0]);
    }
    if parts.len() == 2 && (parts[1] == "watchlist" || parts[1] == "films") {
        return format!("https://letterboxd.com/{}/{sec}/", parts[0]);
    }
    base
}

async fn page(meta: &Meta, url: &str, agent: &str, language: &str) -> Result<String, NetError> {
    let headers = [
        (
            "Accept",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        ),
        ("Accept-Language", language),
    ];
    meta.net
        .get_text(url, &headers, agent, Duration::from_secs(10))
        .await
}

async fn profile_entries(
    meta: &Meta,
    raw: &str,
    pages: usize,
    section: &str,
) -> Result<Vec<Entry>, NetError> {
    let base = profile_url(raw.trim(), section);
    let mut entries = Vec::new();
    let mut seen = HashSet::new();
    for n in 1..=pages {
        let url = if n == 1 {
            base.clone()
        } else {
            format!("{base}page/{n}/")
        };
        let body = match page(meta, &url, AGENT, "en-US,en;q=0.9").await {
            Ok(b) => b,
            Err(_) if n == 1 => {
                return Err(NetError(format!(
                    "Impossible de lire la page Letterboxd ({base}) : vérifiez que le profil est public."
                )));
            }
            Err(_) => break,
        };
        let found = parse_page(&body, &mut seen);
        let more = !found.is_empty() && body.contains(&format!("/page/{}/", n + 1));
        entries.extend(found);
        if !more {
            break;
        }
    }
    Ok(entries)
}

fn norm(text: &str) -> String {
    let plain: String = text
        .nfkd()
        .filter(char::is_ascii)
        .collect::<String>()
        .to_lowercase();
    NOT_ALNUM
        .replace_all(&plain, " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn result_year(item: &Value) -> Option<i64> {
    let shown = [&item["releaseInfo"], &item["year"]]
        .into_iter()
        .find(|v| truthy(v))
        .map(text)
        .unwrap_or_default();
    let year: String = shown.chars().take(4).collect();
    (!year.is_empty() && year.chars().all(|c| c.is_ascii_digit()))
        .then(|| year.parse().ok())
        .flatten()
}

/// The catalogue result that is this film, or None. With a year a result must
/// be within three years (same title), or within one (title contained, or
/// another title); without a year, the same title.
pub fn pick<'a>(results: &'a [Value], title: &str, year: &str, imdb_id: &str) -> Option<&'a Value> {
    if imdb_id.starts_with("tt")
        && let Some(exact) = results.iter().find(|m| m["id"] == imdb_id)
    {
        return Some(exact);
    }
    let wanted = norm(title);
    let target = (!year.is_empty() && year.chars().all(|c| c.is_ascii_digit()))
        .then(|| year.parse::<i64>().ok())
        .flatten();
    let name = |m: &Value| norm(&text(&m["name"]));
    let close = |m: &Value, gap: i64| matches!((target, result_year(m)), (Some(t), Some(f)) if (f - t).abs() <= gap);
    let Some(_) = target else {
        return results.iter().find(|m| name(m) == wanted);
    };
    results
        .iter()
        .find(|m| name(m) == wanted && close(m, 3))
        .or_else(|| {
            results.iter().find(|m| {
                !wanted.is_empty()
                    && (name(m).contains(&wanted) || wanted.contains(&name(m)))
                    && close(m, 1)
            })
        })
        .or_else(|| results.iter().find(|m| close(m, 1)))
}

/// What KINO 1.1.90 and before chose (films only): the same tests, then the first result.
fn old_pick<'a>(results: &'a [Value], title: &str, year: &str, imdb_id: &str) -> Option<&'a Value> {
    let numeric = !year.is_empty() && year.chars().all(|c| c.is_ascii_digit());
    if (numeric || !imdb_id.is_empty())
        && let Some(found) = pick(results, title, year, imdb_id)
    {
        return Some(found);
    }
    let low = title.to_lowercase();
    results
        .iter()
        .find(|m| text(&m["name"]).trim().to_lowercase() == low)
        .or(results.first())
}

struct Resolved {
    item: Option<Value>,
    old_id: Option<String>,
}

async fn resolve_one(meta: Meta, entry: Entry) -> Resolved {
    let none = Resolved {
        item: None,
        old_id: None,
    };
    let (title, year, imdb_id) = (
        entry.title.trim().to_string(),
        entry.year.trim().to_string(),
        entry.imdb_id.trim().to_string(),
    );
    if title.is_empty() && imdb_id.is_empty() {
        return none;
    }
    let query = if title.is_empty() {
        imdb_id.clone()
    } else {
        title.clone()
    };
    let Ok(movies) = meta.search(&query, "movie").await else {
        return none;
    };
    let mut chosen = pick(&movies, &title, &year, &imdb_id)
        .cloned()
        .map(|c| (c, "movie"));
    if chosen.is_none() {
        let Ok(series) = meta.search(&query, "series").await else {
            return none;
        };
        chosen = pick(&series, &title, &year, &imdb_id)
            .cloned()
            .map(|c| (c, "series"));
    }
    let old = if movies.is_empty() {
        None
    } else {
        old_pick(&movies, &title, &year, &imdb_id).cloned()
    };
    let mut old_id = None;
    // Only a clear mistake is taken back: years far apart, as « Lupin » (2021) → 1979.
    if let (Some(old), Ok(target)) = (&old, year.parse::<i64>())
        && truthy(&old["id"])
        && chosen.as_ref().is_none_or(|(c, _)| c["id"] != old["id"])
        && year.chars().all(|c| c.is_ascii_digit())
        && result_year(old).is_some_and(|y| (y - target).abs() > 3)
    {
        old_id = Some(text(&old["id"]));
    }
    let Some((chosen, kind)) = chosen.filter(|(c, _)| truthy(&c["id"])) else {
        return Resolved { item: None, old_id };
    };
    let id = text(&chosen["id"]);
    let mut rating = text(&chosen["imdbRating"]).trim().to_string();
    if rating.is_empty() && id.starts_with("tt") {
        let url = format!("{}/meta/{kind}/{id}.json", meta.cinemeta);
        let net = meta.net.clone();
        let data = meta
            .net
            .cached(
                &format!("cinemeta_lite:{kind}:{id}"),
                86400.0,
                || async move {
                    net.request_json(
                        reqwest::Method::GET,
                        &url,
                        &[("User-Agent", crate::net::BROWSER)],
                        None,
                        Duration::from_secs(4),
                        3,
                    )
                    .await
                },
            )
            .await
            .unwrap_or_default();
        rating = text(&data["meta"]["imdbRating"]).trim().to_string();
    }
    let shown = [&chosen["releaseInfo"], &chosen["year"]]
        .into_iter()
        .find(|v| truthy(v))
        .map(text)
        .unwrap_or(year.clone());
    Resolved {
        item: Some(json!({
            "id": id, "name": if truthy(&chosen["name"]) { chosen["name"].clone() } else { json!(title) }, "type": kind,
            "year": shown.chars().take(4).collect::<String>(),
            "poster": if truthy(&chosen["poster"]) { chosen["poster"].clone() } else { json!(format!("https://images.metahub.space/poster/medium/{id}/img")) },
            "imdbRating": rating, "user_rating": entry.user_rating.trim(), "letterboxd": {"title": title, "year": year},
        })),
        old_id,
    }
}

/// Entries → catalogue items, twelve at a time; `wrong` receives the films the
/// old matching chose instead, so an import can take them back.
async fn resolve(
    meta: &Meta,
    entries: &[Entry],
    max: usize,
    wrong: Option<&mut Vec<String>>,
) -> Vec<Value> {
    let results: Vec<Resolved> = futures_util::stream::iter(
        entries
            .iter()
            .take(max)
            .cloned()
            .map(|e| resolve_one(meta.clone(), e)),
    )
    .buffered(12)
    .collect()
    .await;
    let items: Vec<Value> = results.iter().filter_map(|r| r.item.clone()).collect();
    if let Some(wrong) = wrong {
        let good: HashSet<String> = items.iter().map(|i| text(&i["id"])).collect();
        for id in results.iter().filter_map(|r| r.old_id.clone()) {
            if !good.contains(&id) && !wrong.contains(&id) {
                wrong.push(id);
            }
        }
    }
    items
}

/// A watchlist and/or the films seen, from a profile or an export, notes kept.
pub async fn import(meta: &Meta, db: &Db, payload: &Value) -> Result<Value, NetError> {
    let field = |k: &str| {
        if truthy(&payload[k]) {
            text(&payload[k]).trim().to_string()
        } else {
            String::new()
        }
    };
    let source = field("url_or_user");
    let csv_text = field("csv_text");
    let csv_name = field("csv_filename").to_lowercase();
    let restore = payload["restore_removed"] == true;
    let mut mode = field("mode").to_lowercase();
    if !["watchlist", "watched", "both"].contains(&mode.as_str()) {
        mode = "both".into();
    }
    if source.to_lowercase().contains("/films") && mode == "watchlist" {
        mode = "watched".into();
    }
    let (mut wanted, mut seen) = (Vec::new(), Vec::new());
    if !source.is_empty() {
        if mode == "watchlist" || mode == "both" {
            match profile_entries(meta, &source, 4, "watchlist").await {
                Ok(found) => wanted.extend(found),
                Err(e) if mode == "watchlist" => return Err(e),
                Err(_) => {}
            }
        }
        if mode == "watched" || mode == "both" {
            match profile_entries(meta, &source, 5, "watched").await {
                Ok(found) => seen.extend(found),
                Err(e) if mode == "watched" => return Err(e),
                Err(_) => {}
            }
        }
    }
    if !csv_text.is_empty() {
        let parsed = parse_csv(&csv_text);
        if mode == "watched"
            || ["watched", "rating", "diary", "history"]
                .iter()
                .any(|k| csv_name.contains(k))
        {
            seen.extend(parsed);
        } else {
            wanted.extend(parsed);
        }
    }
    if wanted.is_empty() && seen.is_empty() {
        return Err(NetError(
            "Aucun film trouvé. Vérifiez le pseudo/lien Letterboxd ou le fichier CSV.".into(),
        ));
    }
    let resolved_wanted = if wanted.is_empty() {
        vec![]
    } else {
        resolve(meta, &wanted, 120, None).await
    };
    let mut wrong = Vec::new();
    let resolved_seen = if seen.is_empty() {
        vec![]
    } else {
        resolve(meta, &seen, 180, Some(&mut wrong)).await
    };
    let at = crate::db::now() as i64;
    let watched: Vec<Value> = resolved_seen
        .iter()
        .map(|item| {
            let mut entry = json!({
                "id": item["id"], "name": item["name"], "type": if truthy(&item["type"]) { item["type"].clone() } else { json!("movie") },
                "year": item["year"], "poster": item["poster"], "imdbRating": item["imdbRating"],
                "user_rating": if truthy(&item["user_rating"]) { item["user_rating"].clone() } else { json!("") },
                "position": 7200, "duration": 7200, "progress_pct": 100.0, "completed": true, "imported_watched": true,
            });
            // A series seen on Letterboxd: seen to the end (« Revoir »), never « À continuer ».
            if item["type"] == "series" {
                entry["series_finished"] = json!(true);
            }
            entry["letterboxd"] = if truthy(&item["letterboxd"]) { item["letterboxd"].clone() } else { json!({}) };
            entry["updated_at"] = json!(at);
            entry
        })
        .collect();
    let corrected = crate::library::drop_wrong_imports(db, &wrong);
    let added = crate::library::import_user_lists(db, &resolved_wanted, &watched, restore);
    if !source.is_empty() && !source.starts_with("http") && !source.contains('/') {
        let user = source.trim().trim_start_matches('@').to_string();
        let _ = tokio::task::spawn_blocking(move || {
            crate::config::save(&json!({"letterboxd_user": user}))
        })
        .await;
    }
    let count = |v: &Value| v.as_array().map(Vec::len).unwrap_or(0);
    Ok(json!({
        "watchlist": crate::library::watchlist(db), "history": crate::library::history(db),
        "found_count": wanted.len() + seen.len(), "added_count": count(&added["watchlist"]),
        "found_wl_count": wanted.len(), "added_wl_count": count(&added["watchlist"]),
        "found_watched_count": seen.len(), "added_watched_count": count(&added["history"]),
        "removed_wl_count": count(&added["removed"]["watchlist"]), "removed_watched_count": count(&added["removed"]["history"]),
        "corrected_watched_count": corrected.len(),
        "unmatched_count": (wanted.len() - resolved_wanted.len()) + (seen.len() - resolved_seen.len()),
    }))
}

fn custom_lists() -> Vec<Value> {
    crate::config::load()
        .custom_lists
        .and_then(|lists| serde_json::to_value(lists).ok())
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default()
}

async fn save_lists(lists: Vec<Value>) {
    let _ =
        tokio::task::spawn_blocking(move || crate::config::save(&json!({"custom_lists": lists})))
            .await;
}

/// A public Letterboxd list (or a boxd.it short link), kept with the user's lists.
pub async fn import_list(meta: &Meta, url: &str) -> Result<Value, NetError> {
    let mut raw = url.trim().to_string();
    if raw.is_empty() {
        return Err(NetError("URL Letterboxd requise.".into()));
    }
    if raw.contains("boxd.it")
        && let Ok(r) = reqwest::Client::new()
            .get(&raw)
            .header("User-Agent", SAFARI)
            .timeout(Duration::from_secs(10))
            .send()
            .await
    {
        raw = r.url().to_string();
    }
    let clean = format!(
        "{}/",
        raw.split('?').next().unwrap_or("").trim_end_matches('/')
    );
    let (mut title, mut description) = (String::new(), String::new());
    let mut movies: Vec<Entry> = Vec::new();
    let mut seen = HashSet::new();
    for n in 1..=3 {
        let url = if n == 1 {
            clean.clone()
        } else {
            format!("{clean}page/{n}/")
        };
        let body = match page(meta, &url, SAFARI, "fr-FR,fr;q=0.9,en-US;q=0.8").await {
            Ok(b) => b,
            Err(e) if n == 1 => {
                return Err(NetError(format!(
                    "Impossible de lire la page Letterboxd ({clean}) : {}",
                    e.0
                )));
            }
            Err(_) => break,
        };
        if n == 1 {
            let clean_text = |s: &str| {
                unescape(s)
                    .replace("&#039;", "'")
                    .replace("&amp;", "&")
                    .trim()
                    .to_string()
            };
            if let Some(m) = OG_TITLE.captures(&body).or_else(|| H1.captures(&body)) {
                title = clean_text(&m[1]);
            }
            if let Some(m) = OG_DESCRIPTION.captures(&body) {
                description = clean_text(&m[1]);
            }
        }
        let mut found = 0;
        for m in ITEM_NAME.captures_iter(&body) {
            let (t, y) = title_year(&m[1]);
            if !t.is_empty() && seen.insert(format!("{}|{y}", t.to_lowercase())) {
                movies.push(Entry {
                    title: t,
                    year: y,
                    imdb_id: String::new(),
                    user_rating: String::new(),
                });
                found += 1;
            }
        }
        if found == 0 {
            for m in FILM_PAIR.captures_iter(&body) {
                let t = unescape(m[1].trim());
                let y = m
                    .get(2)
                    .map(|y| y.as_str().trim().to_string())
                    .unwrap_or_default();
                if !t.is_empty() && seen.insert(format!("{}|{y}", t.to_lowercase())) {
                    movies.push(Entry {
                        title: t,
                        year: y,
                        imdb_id: String::new(),
                        user_rating: String::new(),
                    });
                    found += 1;
                }
            }
        }
        if found == 0 || !body.contains(&format!("/page/{}/", n + 1)) {
            break;
        }
    }
    if movies.is_empty() {
        return Err(NetError(format!(
            "Aucun film n'a pu être extrait de cette liste Letterboxd ({clean})."
        )));
    }
    let path = reqwest::Url::parse(&clean)
        .map(|u| u.path().to_string())
        .unwrap_or_default();
    if title.is_empty() {
        let last = path
            .split('/')
            .rfind(|p| !p.is_empty())
            .unwrap_or("Liste Letterboxd")
            .replace('-', " ");
        title = last
            .split(' ')
            .map(title_case)
            .collect::<Vec<_>>()
            .join(" ");
    }
    let items = resolve(meta, &movies, 120, None).await;
    let mut id = LIST_ID
        .replace_all(path.trim_matches('/'), "_")
        .into_owned();
    if id.is_empty() {
        id = format!("list_{}", crate::db::now() as i64);
    }
    let entry = json!({"id": id, "title": title, "description": description, "url": clean, "items": items, "count": items.len(),
                       "updated_at": crate::db::now() as i64});
    let mut lists: Vec<Value> = custom_lists()
        .into_iter()
        .filter(|l| l["id"] != id.as_str() && l["url"] != clean.as_str())
        .collect();
    lists.insert(0, entry.clone());
    lists.truncate(20);
    save_lists(lists.clone()).await;
    Ok(
        json!({"status": "ok", "list": entry, "custom_lists": lists, "total_extracted": movies.len(), "total_resolved": items.len()}),
    )
}

/// str.title() for one word: the first letter of each alphabetic run up.
fn title_case(word: &str) -> String {
    let mut out = String::new();
    let mut start = true;
    for c in word.chars() {
        if c.is_alphabetic() {
            if start {
                out.extend(c.to_uppercase());
            } else {
                out.extend(c.to_lowercase());
            }
            start = false;
        } else {
            out.push(c);
            start = true;
        }
    }
    out
}

pub async fn delete_list(id: &Value) -> Vec<Value> {
    let lists: Vec<Value> = custom_lists()
        .into_iter()
        .filter(|l| &l["id"] != id)
        .collect();
    save_lists(lists.clone()).await;
    lists
}

pub fn lists() -> Vec<Value> {
    custom_lists()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exports_pages_and_matches() {
        let csv = "\u{feff}Date,Name,Year,Letterboxd URI,Rating\n2024-01-02,\"Lupin, the Thief\",2021,https://boxd.it/x,4.5\n2024-01-03,Heat,1995,,\n2024-01-04,Heat,1995,,5\n";
        let entries = parse_csv(csv);
        assert_eq!(entries.len(), 2);
        assert_eq!(
            entries[0],
            Entry {
                title: "Lupin, the Thief".into(),
                year: "2021".into(),
                imdb_id: String::new(),
                user_rating: "4.5".into()
            }
        );
        assert_eq!(
            entries[1].user_rating, "5",
            "a later note fills an empty one"
        );
        assert_eq!(
            parse_csv("Const,Your Rating,Title,Year\ntt0113277,8,Heat,1995\n")[0].user_rating,
            "4"
        );
        assert_eq!(parse_csv("A Ghost Story (2017)\nHeat\n").len(), 2);
        let page = r#"<li class="poster-container"><div data-film-name="Heat" data-film-release-year="1995"></div><span class="rating rated-9"></span></li>"#;
        let found = parse_page(page, &mut HashSet::new());
        assert_eq!(
            (
                found[0].title.as_str(),
                found[0].year.as_str(),
                found[0].user_rating.as_str()
            ),
            ("Heat", "1995", "4.5")
        );
        assert_eq!(
            profile_url("@kino/", "watched"),
            "https://letterboxd.com/kino/films/"
        );
        assert_eq!(
            profile_url("https://letterboxd.com/kino/watchlist/?x=1", "watchlist"),
            "https://letterboxd.com/kino/watchlist/"
        );
        let results = vec![
            json!({"id": "tt0079833", "name": "The Castle of Cagliostro", "releaseInfo": "1979"}),
            json!({"id": "tt2531336", "name": "Lupin", "releaseInfo": "2021–"}),
        ];
        assert_eq!(
            pick(&results, "Lupin", "2021", "").unwrap()["id"],
            "tt2531336"
        );
        assert!(pick(&results, "Lupin", "1950", "").is_none(), "no guess");
        assert_eq!(
            old_pick(&results, "Arsène Lupin", "1950", "").unwrap()["id"],
            "tt0079833",
            "what old versions chose: the first result"
        );
        assert_eq!(title_case("my top films"), "My Top Films");
    }
}
