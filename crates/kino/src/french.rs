//! Works found by their French title (« SOS Fantômes »), through French
//! Wikipedia: the French page links to the English one, whose title is looked
//! up in Cinemeta and checked by name and year. Matches are remembered for
//! display and search.

use std::collections::HashSet;
use std::sync::LazyLock;
use std::time::Duration;

use futures_util::StreamExt;
use regex::Regex;
use serde_json::{Value, json};
use unicode_normalization::UnicodeNormalization;

use crate::catalog::same_work_years;
use crate::db::{Db, now};
use crate::meta::Meta;

const WIKIPEDIA_AGENT: &str = "KINO/1.1 (lecteur de films local ; photos de la distribution)";
const TITLE_TTL: f64 = 30.0 * 86400.0;
static SPACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s+").unwrap());
static NOT_ALNUM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^0-9a-z]+").unwrap());
static WORK: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\bfilm\b|série télévisée|série d'animation|téléfilm|anime|mini-série").unwrap()
});
static FRANCHISE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)série de films|série cinématographique|franchise|saga").unwrap()
});
static EN_SUFFIX: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\s*\((?:\d{4} )?(?:film|TV series|American TV series|miniseries|franchise|film series)\)$").unwrap()
});
static FR_SUFFIX: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\s*\((?:film|série télévisée|série d'animation|téléfilm|série|mini-série|feuilleton)[^)]*\)$").unwrap()
});
static YEAR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(1[89]\d\d|20\d\d)\b").unwrap());
static SERIES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)série|anime").unwrap());

fn text(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

fn truthy(value: &Value) -> bool {
    !matches!(value, Value::Null | Value::Bool(false))
        && value != &json!(0)
        && value != &json!("")
        && value != &json!([])
        && value != &json!({})
}

/// Lower case, no accents, no punctuation: « Spider-Man: No Way Home » ≈ « spider man no way home ».
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

struct Work {
    french: String,
    english: String,
    year: String,
    kind: &'static str,
}

/// The works a French Wikipedia search names, at most four.
fn works(data: &Value) -> Vec<Work> {
    let mut pages: Vec<&Value> = data["query"]["pages"]
        .as_array()
        .into_iter()
        .flatten()
        .collect();
    pages.sort_by_key(|p| p["index"].as_i64().unwrap_or(99));
    let mut found = Vec::new();
    for page in pages {
        let description = text(&page["description"]);
        let Some(link) = page["langlinks"].as_array().and_then(|l| l.first()) else {
            continue;
        };
        if !WORK.is_match(&description) || FRANCHISE.is_match(&description) {
            continue;
        }
        let link_title = text(&link["title"]);
        let english = EN_SUFFIX.replace(&link_title, "").trim().to_string();
        let year = YEAR
            .captures(&format!("{description} {link_title}"))
            .map(|c| c[1].to_string())
            .unwrap_or_default();
        let kind = if SERIES.is_match(&description) {
            "series"
        } else {
            "movie"
        };
        if !english.is_empty() {
            let french = FR_SUFFIX
                .replace(&text(&page["title"]), "")
                .trim()
                .to_string();
            found.push(Work {
                french,
                english,
                year,
                kind,
            });
        }
        if found.len() >= 4 {
            break;
        }
    }
    found
}

/// The Cinemeta listing of a work: same name, a year at most one apart.
async fn matching(meta: &Meta, work: &Work) -> Option<Value> {
    let metas = meta.search(&work.english, work.kind).await.ok()?;
    let wanted = comparable(&work.english);
    for item in metas {
        if comparable(&text(&item["name"])) != wanted {
            continue;
        }
        let shown = if truthy(&item["releaseInfo"]) {
            &item["releaseInfo"]
        } else {
            &item["year"]
        };
        let found = YEAR
            .captures(&text(shown))
            .and_then(|c| c[1].parse::<i64>().ok());
        if let (Some(found), Ok(year)) = (found, work.year.parse::<i64>())
            && (found - year).abs() > 1
        {
            continue;
        }
        let mut out = item.as_object().cloned().unwrap_or_default();
        let kind = if truthy(&item["type"]) {
            item["type"].clone()
        } else {
            json!(work.kind)
        };
        out.insert("name_fr".into(), json!(work.french));
        out.insert("type".into(), kind);
        return Some(Value::Object(out));
    }
    None
}

/// Remembers French titles; returns the ids left without theirs because another
/// listing of the same work (kind and year) already holds it.
pub fn set_aliases(db: &Db, rows: &[Value]) -> HashSet<String> {
    let mut refused = HashSet::new();
    let rows: Vec<&Value> = rows
        .iter()
        .filter(|r| truthy(&r["id"]) && truthy(&r["alias"]))
        .collect();
    if rows.is_empty() {
        return refused;
    }
    let at = now();
    let _ = db.with(|c| {
        let tx = c.unchecked_transaction()?;
        for r in rows {
            let id = text(&r["id"]);
            let alias = text(&r["alias"]);
            let kind = if ["series", "anime"].contains(&r["type"].as_str().unwrap_or("")) { "series" } else { "movie" };
            let year = if truthy(&r["year"]) {
                text(&r["year"])
            } else {
                tx.query_row("SELECT year FROM media_catalog WHERE imdb_id = ?", [&id], |row| row.get::<_, Option<String>>(0))
                    .ok()
                    .flatten()
                    .unwrap_or_default()
            };
            let mut others = tx.prepare(
                "SELECT a.imdb_id, COALESCE(NULLIF(c.year, ''), a.year) FROM media_aliases a \
                 LEFT JOIN media_catalog c ON c.imdb_id = a.imdb_id \
                 WHERE lower(a.alias) = lower(?) AND a.media_type = ? AND a.imdb_id != ?",
            )?;
            let held = others
                .query_map(rusqlite::params![alias, kind, id], |row| row.get::<_, Option<String>>(1))?
                .filter_map(Result::ok)
                .any(|other| same_work_years(&year, &other.unwrap_or_default()));
            drop(others);
            if held {
                refused.insert(id);
                continue;
            }
            let cut = |s: String, n: usize| s.chars().take(n).collect::<String>();
            tx.execute(
                "INSERT INTO media_aliases (imdb_id, alias, name, year, media_type, updated_at) VALUES (?, ?, ?, ?, ?, ?) \
                 ON CONFLICT(imdb_id) DO UPDATE SET alias = excluded.alias, \
                 name = COALESCE(NULLIF(excluded.name, ''), media_aliases.name), \
                 year = COALESCE(NULLIF(excluded.year, ''), media_aliases.year), \
                 media_type = excluded.media_type, updated_at = excluded.updated_at \
                 WHERE media_aliases.alias != excluded.alias OR media_aliases.name = ''",
                rusqlite::params![id, cut(alias, 300), cut(text(&r["name"]), 300), cut(text(&r["year"]), 12), kind, at],
            )?;
        }
        tx.commit()
    });
    refused
}

/// Works whose French title matches the query, cached a day.
pub async fn search(meta: &Meta, query: &str, kind: &str) -> Vec<Value> {
    let query: String = SPACES
        .replace_all(query, " ")
        .trim()
        .chars()
        .take(120)
        .collect();
    if query.chars().count() < 3 {
        return vec![];
    }
    let key = format!("search_fr_v1:{}", comparable(&query));
    let found = meta
        .net
        .cached(&key, 86400.0, || async {
            let form = [
                ("action", "query"), ("generator", "search"), ("gsrsearch", query.as_str()), ("gsrlimit", "6"), ("gsrnamespace", "0"),
                ("prop", "langlinks|description"), ("lllang", "en"), ("lllimit", "max"), ("format", "json"), ("formatversion", "2"),
            ];
            let Ok(data) = meta
                .net
                .request_form("https://fr.wikipedia.org/w/api.php", &[("User-Agent", WIKIPEDIA_AGENT)], &form, Duration::from_secs(8), 1)
                .await
            else {
                return Ok(Value::Null);
            };
            let works = works(&data);
            let mut matches: Vec<Value> = Vec::new();
            let mut each = futures_util::stream::iter(works.into_iter().map(|w| {
                let meta = meta.clone();
                async move { matching(&meta, &w).await }
            }))
            .buffered(4);
            while let Some(found) = each.next().await {
                matches.extend(found.filter(|m| truthy(&m["id"])));
            }
            let rows: Vec<Value> = matches
                .iter()
                .filter(|m| text(&m["name_fr"]).to_lowercase() != text(&m["name"]).to_lowercase())
                .map(|m| {
                    let year = if truthy(&m["releaseInfo"]) { &m["releaseInfo"] } else { &m["year"] };
                    json!({"id": m["id"], "alias": m["name_fr"], "name": m["name"], "year": text(year), "type": m["type"]})
                })
                .collect();
            let refused = set_aliases(meta.net.db(), &rows);
            matches.retain(|m| !refused.contains(&text(&m["id"])));
            for m in &matches {
                let _ = meta.net.db().cache_set(&format!("title_fr_v1:{}", text(&m["id"])), &m["name_fr"], TITLE_TTL);
            }
            Ok(Value::Array(matches))
        })
        .await
        .ok()
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default();
    if kind == "movie" || kind == "series" {
        return found.into_iter().filter(|m| m["type"] == kind).collect();
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn works_named_by_a_french_search() {
        let page = |index: i64, title: &str, description: &str, english: &str| json!({"index": index, "title": title, "description": description, "langlinks": [{"lang": "en", "title": english}]});
        let data = json!({"query": {"pages": [
            page(5, "Psychose 2", "film de Richard Franklin sorti en 1983", "Psycho II (film)"),
            page(1, "Psychose", "trouble psychiatrique", "Psychosis"),
            page(2, "Psychose (film)", "film d'Alfred Hitchcock, sorti en 1960", "Psycho (1960 film)"),
            page(4, "Psychose 4", "téléfilm américain de Mick Garris, diffusé en 1990", "Psycho IV: The Beginning"),
            page(6, "Psychose (série de films)", "série de films", "Psycho (franchise)"),
            page(3, "Les Simpson", "série télévisée d'animation américaine", "The Simpsons"),
        ]}});
        let found: Vec<(String, String, String, &str)> = works(&data)
            .into_iter()
            .map(|w| (w.french, w.english, w.year, w.kind))
            .collect();
        let row = |f: &str, e: &str, y: &str, k| (f.to_string(), e.to_string(), y.to_string(), k);
        assert_eq!(
            found,
            vec![
                row("Psychose", "Psycho", "1960", "movie"),
                row("Les Simpson", "The Simpsons", "", "series"),
                row("Psychose 4", "Psycho IV: The Beginning", "1990", "movie"),
                row("Psychose 2", "Psycho II", "1983", "movie"),
            ],
            "in Wikipedia's order, four at most, franchises left out"
        );
        assert_eq!(
            comparable("Spider-Man: No Way Home"),
            "spider man no way home"
        );
        assert_eq!(comparable("Fast & Furious Ⅸ"), "fast and furious ix");
    }
}
