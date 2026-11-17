//! Titles shown in French or in their original language: French titles from
//! English Wikipedia's links to French pages (checked by kind and year),
//! original titles from Wikidata by IMDb id, and French episode summaries.

use std::collections::HashMap;
use std::sync::LazyLock;
use std::time::Duration;

use futures_util::StreamExt;
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::meta::{Meta, is_already_french};

const WIKIPEDIA_AGENT: &str = "KINO/1.1 (lecteur de films local ; photos de la distribution)";
const WIKIDATA_AGENT: &str = "KINO (https://github.com/isleepverywellatnight/kino)";
const FRENCH_TTL: f64 = 30.0 * 86400.0;
const ORIGINAL_TTL: f64 = 180.0 * 86400.0;
static IMDB: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^tt\d{1,12}$").unwrap());
static SPACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s+").unwrap());
static YEAR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(1[89]\d\d|20\d\d)\b").unwrap());
static FR_SUFFIX: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\s*\((?:film|série télévisée|série d'animation|téléfilm|série|mini-série|feuilleton)[^)]*\)$").unwrap()
});
static SERIES_DESCRIPTION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)television|\bTV\b|anime|web series|sitcom|miniseries").unwrap()
});
static NOT_SCREEN_WORK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)manga|novel|comic|video game|book").unwrap());
static FILM_WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\bfilm\b").unwrap());
static ALIAS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(.+?)\s*\(([^()]{2,120})\)$").unwrap());
static YEARS_ONLY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[\d\s–-]+$").unwrap());
static FRENCH_WORDS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)(?:^|\s)(?:le|la|les|l'|l’|un|une|des|du|de|et|au|aux)(?:\s|$)|[àâçéèêëîïôûùüÿœ]",
    )
    .unwrap()
});

fn text(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false))
        && v != &json!(0)
        && v != &json!("")
        && v != &json!([])
        && v != &json!({})
}

fn or_text(v: &Value) -> String {
    if truthy(v) { text(v) } else { String::new() }
}

/// « Spirited Away (Le Voyage de Chihiro) » → (« Spirited Away », « Le Voyage de Chihiro »).
pub fn split_french_alias(name: &str) -> (String, String) {
    if let Some(c) = ALIAS.captures(name)
        && !YEARS_ONLY.is_match(&c[2])
        && FRENCH_WORDS.is_match(&c[2])
    {
        return (c[1].trim().to_string(), c[2].trim().to_string());
    }
    (name.to_string(), String::new())
}

struct Work {
    id: String,
    name: String,
    year: String,
    alias: String,
    kind: &'static str,
}

fn candidates(w: &Work) -> Vec<String> {
    let (name, year) = (&w.name, &w.year);
    if w.kind == "series" {
        vec![
            format!("{name} ({year} TV series)"),
            format!("{name} (TV series)"),
            format!("{name} (American TV series)"),
            name.clone(),
        ]
    } else {
        vec![
            format!("{name} ({year} film)"),
            format!("{name} (film)"),
            name.clone(),
        ]
    }
}

fn description_matches(description: &str, year: &str, kind: &str) -> bool {
    if NOT_SCREEN_WORK.is_match(description) {
        return false;
    }
    if kind == "series" {
        return SERIES_DESCRIPTION.is_match(description);
    }
    // Without its year a listing cannot be told from the famous film of the same name.
    let Ok(year) = year.parse::<i64>() else {
        return false;
    };
    FILM_WORD.is_match(description)
        && YEAR
            .captures_iter(description)
            .filter_map(|c| c[1].parse::<i64>().ok())
            .any(|f| (f - year).abs() <= 1)
}

/// {id: French title or ''} for up to 12 works in one request; None when Wikipedia does not answer.
async fn wikipedia_french_titles(meta: &Meta, works: &[&Work]) -> Option<HashMap<String, String>> {
    let titles: Vec<String> = works.iter().flat_map(|w| candidates(w)).collect();
    let joined = titles.join("|");
    let form = [
        ("action", "query"),
        ("titles", joined.as_str()),
        ("prop", "langlinks|description"),
        ("lllang", "fr"),
        ("lllimit", "max"),
        ("redirects", "1"),
        ("format", "json"),
        ("formatversion", "2"),
    ];
    let data = meta
        .net
        .request_form(
            "https://en.wikipedia.org/w/api.php",
            &[("User-Agent", WIKIPEDIA_AGENT)],
            &form,
            Duration::from_secs(10),
            1,
        )
        .await
        .ok()?;
    let query = &data["query"];
    let pairs = |key: &str| -> HashMap<String, String> {
        query[key]
            .as_array()
            .into_iter()
            .flatten()
            .map(|n| (text(&n["from"]), text(&n["to"])))
            .collect()
    };
    let (normalized, redirects) = (pairs("normalized"), pairs("redirects"));
    let pages: HashMap<String, &Value> = query["pages"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|p| (text(&p["title"]), p))
        .collect();
    let mut found = HashMap::new();
    for w in works {
        found.insert(w.id.clone(), String::new());
        for candidate in candidates(w) {
            let title = normalized.get(&candidate).cloned().unwrap_or(candidate);
            let Some(page) = pages.get(redirects.get(&title).unwrap_or(&title)) else {
                continue;
            };
            if truthy(&page["missing"])
                || !description_matches(&or_text(&page["description"]), &w.year, w.kind)
            {
                continue;
            }
            if let Some(link) = page["langlinks"]
                .as_array()
                .and_then(|l| l.first())
                .map(|l| or_text(&l["title"]))
                .filter(|t| !t.is_empty())
            {
                found.insert(
                    w.id.clone(),
                    FR_SUFFIX.replace(&link, "").trim().to_string(),
                );
            }
            break;
        }
    }
    Some(found)
}

/// French display titles of works by IMDb id: {id: title, or '' when none is known}.
pub async fn french_titles(meta: &Meta, items: &[Value]) -> Map<String, Value> {
    let mut clean: Vec<Work> = Vec::new();
    for item in items.iter().take(60).filter(|i| i.is_object()) {
        let id = or_text(&item["id"]);
        let name: String = SPACES
            .replace_all(&or_text(&item["name"]), " ")
            .trim()
            .chars()
            .take(200)
            .collect();
        let year = YEAR
            .captures(&or_text(&item["year"]))
            .map(|c| c[1].to_string())
            .unwrap_or_default();
        if IMDB.is_match(&id) && !name.is_empty() {
            let (name, alias) = split_french_alias(&name);
            let kind = if ["series", "anime"].contains(&item["type"].as_str().unwrap_or("")) {
                "series"
            } else {
                "movie"
            };
            clean.push(Work {
                id,
                name,
                year,
                alias,
                kind,
            });
        }
    }
    let db = meta.net.db();
    let mut result: Map<String, Value> = Map::new();
    let mut missing: Vec<&Work> = Vec::new();
    for w in &clean {
        if !w.alias.is_empty() {
            result.insert(w.id.clone(), json!(w.alias));
            continue;
        }
        match db.cache_get(&format!("title_fr_v1:{}", w.id)) {
            Some(cached) if !cached.is_null() => {
                result.insert(w.id.clone(), cached);
            }
            _ if !missing.iter().any(|m| m.id == w.id) => missing.push(w),
            _ => {}
        }
    }
    for batch in missing.chunks(12) {
        let Some(found) = wikipedia_french_titles(meta, batch).await else {
            continue;
        };
        for w in batch {
            let title = found.get(&w.id).cloned().unwrap_or_default();
            let _ = db.cache_set(&format!("title_fr_v1:{}", w.id), &json!(title), FRENCH_TTL);
            result.insert(w.id.clone(), json!(title));
        }
    }
    // Known French titles also feed the local search (« Les Évadés » → The Shawshank Redemption).
    let rows: Vec<Value> = clean
        .iter()
        .filter_map(|w| {
            let shown = result.get(&w.id).filter(|t| truthy(t)).map(text)?;
            (shown.to_lowercase() != w.name.to_lowercase())
                .then(|| json!({"id": w.id, "alias": shown, "name": w.name, "year": w.year, "type": w.kind}))
        })
        .collect();
    // Another listing of the same work holds that title: this one keeps its own name.
    for id in crate::french::set_aliases(db, &rows) {
        result.insert(id.clone(), json!(""));
        let _ = db.cache_set(&format!("title_fr_v1:{id}"), &json!(""), FRENCH_TTL);
    }
    result
}

/// A title in Latin script (with its accents); another alphabet is not shown.
pub fn is_latin_title(title: &str) -> bool {
    title
        .chars()
        .all(|c| !c.is_alphabetic() || c <= '\u{024f}' || ('\u{1e00}'..='\u{1eff}').contains(&c))
}

/// {imdb id: original title} from Wikidata, or None when it cannot be asked.
async fn wikidata_original_titles(meta: &Meta, ids: &[String]) -> Option<HashMap<String, String>> {
    let values: Vec<String> = ids.iter().map(|i| format!("\"{i}\"")).collect();
    let query = format!(
        "SELECT ?imdb ?title WHERE {{ VALUES ?imdb {{ {} }} ?work wdt:P345 ?imdb; wdt:P1476 ?title. }}",
        values.join(" ")
    );
    let encoded = form_urlencoded::Serializer::new(String::new())
        .append_pair("query", &query)
        .append_pair("format", "json")
        .finish();
    let url = format!("https://query.wikidata.org/sparql?{encoded}");
    let headers = [
        ("Accept", "application/sparql-results+json"),
        ("User-Agent", WIKIDATA_AGENT),
    ];
    let data = meta
        .net
        .request_json(
            reqwest::Method::GET,
            &url,
            &headers,
            None,
            Duration::from_secs(12),
            1,
        )
        .await
        .ok()?;
    let mut found = HashMap::new();
    for row in data["results"]["bindings"].as_array().into_iter().flatten() {
        let id = text(&row["imdb"]["value"]);
        let title = SPACES
            .replace_all(&text(&row["title"]["value"]), " ")
            .trim()
            .to_string();
        if !id.is_empty() && !title.is_empty() && !found.contains_key(&id) {
            found.insert(id, title);
        }
    }
    Some(found)
}

/// Original titles by IMDb id: {id: title, or '' to keep the catalogue's name}.
pub async fn original_titles(meta: &Meta, items: &[Value]) -> Map<String, Value> {
    let mut ids: Vec<String> = Vec::new();
    for item in items.iter().take(60) {
        let id = if item.is_object() {
            or_text(&item["id"])
        } else {
            String::new()
        };
        if IMDB.is_match(&id) && !ids.contains(&id) {
            ids.push(id);
        }
    }
    let db = meta.net.db();
    let mut result = Map::new();
    let mut missing = Vec::new();
    for id in ids {
        match db.cache_get(&format!("title_orig_v1:{id}")) {
            Some(cached) if !cached.is_null() => {
                result.insert(id, cached);
            }
            _ => missing.push(id),
        }
    }
    for batch in missing.chunks(60) {
        let Some(found) = wikidata_original_titles(meta, batch).await else {
            continue;
        };
        for id in batch {
            let title = found
                .get(id)
                .filter(|t| is_latin_title(t))
                .cloned()
                .unwrap_or_default();
            let _ = db.cache_set(&format!("title_orig_v1:{id}"), &json!(title), ORIGINAL_TTL);
            result.insert(id.clone(), json!(title));
        }
    }
    result
}

/// French summaries of one season's episodes: {episode number: text}; an
/// episode whose translation failed is left out (the page keeps the original).
pub async fn episode_overviews_fr(meta: &Meta, imdb_id: &str, season: i64) -> Map<String, Value> {
    let Ok(series) = meta.media(imdb_id, "series").await else {
        return Map::new();
    };
    let number = |v: &Value| {
        v.as_i64()
            .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            .unwrap_or(0)
    };
    let videos: Vec<(i64, String)> = series["videos"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|v| {
            (if truthy(&v["season"]) {
                number(&v["season"])
            } else {
                0
            }) == season
        })
        .filter_map(|v| {
            let summary = [&v["overview"], &v["description"]]
                .into_iter()
                .find(|s| truthy(s))
                .map(text)?;
            let episode = [&v["episode"], &v["number"]]
                .into_iter()
                .find(|s| truthy(s))
                .map(number)
                .unwrap_or(0);
            Some((
                episode,
                html_escape::decode_html_entities(&summary)
                    .trim()
                    .to_string(),
            ))
        })
        .take(60)
        .collect();
    let results: Vec<Option<(i64, String)>> =
        futures_util::stream::iter(videos.into_iter().map(|(episode, original)| {
            let meta = meta.clone();
            async move {
                let translated = meta.translate_fr(&original).await;
                (!translated.is_empty() && (translated != original || is_already_french(&original)))
                    .then_some((episode, translated))
            }
        }))
        .buffered(6)
        .collect()
        .await;
    let mut out = Map::new();
    for (episode, summary) in results.into_iter().flatten().filter(|(e, _)| *e > 0) {
        out.insert(episode.to_string(), json!(summary));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aliases_descriptions_and_scripts() {
        assert_eq!(
            split_french_alias("Spirited Away (Le Voyage de Chihiro)"),
            ("Spirited Away".into(), "Le Voyage de Chihiro".into())
        );
        assert_eq!(split_french_alias("Heat (1995)").1, "");
        assert_eq!(
            split_french_alias("Birdman (or The Unexpected Virtue of Ignorance)").1,
            ""
        );
        assert!(description_matches(
            "1995 film by Michael Mann",
            "1995",
            "movie"
        ));
        assert!(
            !description_matches("1995 film by Michael Mann", "", "movie"),
            "no year, no match"
        );
        assert!(!description_matches("2004 manga series", "", "series"));
        assert!(description_matches("American TV series", "", "series"));
        assert!(is_latin_title("Der Untergang"));
        assert!(is_latin_title("Amélie"));
        assert!(!is_latin_title("千と千尋の神隠し"));
    }
}
