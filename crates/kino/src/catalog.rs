//! KINO's local catalogue of titles (`media_catalog`, searched with FTS5), and
//! the French titles kept beside them (`media_aliases`).

use std::sync::LazyLock;

use regex::Regex;
use rusqlite::types::ValueRef;
use rusqlite::{Connection, Row, params, params_from_iter};
use serde_json::{Map, Value, json};
use unicode_normalization::UnicodeNormalization;

use crate::db::{Db, now};

static YEAR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(1[89]\d\d|20\d\d)\b").unwrap());

fn text(value: &Value) -> String {
    match value {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        Value::Number(n) if n.as_f64() == Some(0.0) => String::new(),
        other => other.to_string(),
    }
}

/// A row as JSON, each column by its name (NULL stays null).
fn row_json(row: &Row) -> rusqlite::Result<Value> {
    let mut map = Map::new();
    for (i, name) in row.as_ref().column_names().iter().enumerate() {
        let value = match row.get_ref(i)? {
            ValueRef::Null => Value::Null,
            ValueRef::Integer(n) => json!(n),
            ValueRef::Real(f) => json!(f),
            ValueRef::Text(t) => json!(String::from_utf8_lossy(t)),
            ValueRef::Blob(_) => Value::Null,
        };
        map.insert(name.to_string(), value);
    }
    Ok(Value::Object(map))
}

/// Indexes titles of a catalogue page or a title page, for the instant search.
pub fn index_media(db: &Db, items: &[Value]) {
    let at = now();
    let _ = db.with(|c| {
        let tx = c.unchecked_transaction()?;
        for item in items {
            let id = text(if item["imdb_id"].is_null() { &item["id"] } else { &item["imdb_id"] });
            if id.is_empty() {
                continue;
            }
            let pick = |a: &str, b: &str| {
                let first = text(&item[a]);
                if first.is_empty() { text(&item[b]) } else { first }
            };
            let kind = pick("media_type", "type");
            let genres = match &item["genres"] {
                Value::Array(list) => list.iter().map(|g| g.as_str().map(str::to_string).unwrap_or_else(|| g.to_string())).collect::<Vec<_>>().join(", "),
                other => text(other),
            };
            tx.execute(
                "INSERT INTO media_catalog (imdb_id, title, media_type, year, poster, rating, genres, overview, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(imdb_id) DO UPDATE SET
                    title = excluded.title, media_type = excluded.media_type, year = excluded.year,
                    poster = COALESCE(NULLIF(excluded.poster, ''), media_catalog.poster), rating = excluded.rating,
                    genres = excluded.genres, overview = COALESCE(NULLIF(excluded.overview, ''), media_catalog.overview),
                    updated_at = excluded.updated_at",
                params![id, pick("title", "name"), if kind.is_empty() { "movie".into() } else { kind }, text(&item["year"]),
                        text(&item["poster"]), pick("rating", "imdbRating"), genres, pick("overview", "description"), at],
            )?;
        }
        tx.commit()
    });
}

/// The kind (movie or series) the catalogue knows for a title.
pub fn media_type(db: &Db, imdb_id: &str) -> Option<String> {
    db.read(|c| {
        c.query_row(
            "SELECT media_type FROM media_catalog WHERE imdb_id = ? LIMIT 1",
            [imdb_id],
            |r| r.get::<_, Option<String>>(0),
        )
    })
    .ok()
    .flatten()
    .map(|t| t.trim().to_lowercase())
    .filter(|t| !t.is_empty())
}

/// Titles of the same kind sharing a genre, best rated first, from the local catalogue.
pub fn similar(db: &Db, imdb_id: &str, kind: &str, genres: &[String], limit: usize) -> Vec<Value> {
    let target = if kind == "series" { "series" } else { "movie" };
    let mut clauses = vec![
        "imdb_id != ?",
        "length(poster) > 0",
        "length(rating) > 0",
        "media_type = ?",
    ];
    let mut values: Vec<String> = vec![imdb_id.into(), target.into()];
    let valid: Vec<&String> = genres
        .iter()
        .filter(|g| g.trim().chars().count() >= 3)
        .take(3)
        .collect();
    let genre_clause = vec!["genres LIKE ?"; valid.len()].join(" OR ");
    let genre_clause = format!("({genre_clause})");
    if !valid.is_empty() {
        values.extend(valid.iter().map(|g| format!("%{}%", g.trim())));
        clauses.push(&genre_clause);
    }
    values.push(limit.to_string());
    let sql = format!(
        "SELECT imdb_id as id, title as name, media_type as type, year, poster, rating as imdbRating FROM media_catalog \
         WHERE {} ORDER BY CAST(rating AS FLOAT) DESC LIMIT ?",
        clauses.join(" AND ")
    );
    db.read(|c| {
        let mut statement = c.prepare(&sql)?;
        let rows = statement.query_map(params_from_iter(values.iter()), row_json)?;
        rows.collect()
    })
    .unwrap_or_default()
}

fn folded(text: &str) -> String {
    text.nfkd()
        .filter(char::is_ascii)
        .flat_map(char::to_lowercase)
        .filter(char::is_ascii_alphanumeric)
        .collect()
}

fn year_of(text: &str) -> Option<i32> {
    YEAR.captures(text).and_then(|c| c[1].parse().ok())
}

/// Two listings of one work: same year (±1), or a year unknown.
pub fn same_work_years(a: &str, b: &str) -> bool {
    match (year_of(a), year_of(b)) {
        (Some(a), Some(b)) => (a - b).abs() <= 1,
        _ => true,
    }
}

fn names(item: &Value) -> Vec<String> {
    [item["name"].as_str(), item["name_fr"].as_str()]
        .into_iter()
        .flatten()
        .map(folded)
        .filter(|n| !n.is_empty())
        .collect()
}

/// Rated works first, the exact title then the titles that start so; a listing
/// that repeats a better one (same name, kind and year, no rating) is left out.
fn rank(query: &str, items: Vec<Value>, limit: usize) -> Vec<Value> {
    let wanted = folded(query);
    let filled = |v: &Value| !text(v).trim().is_empty();
    let mut keyed: Vec<((bool, bool, bool, bool), Value)> = items
        .into_iter()
        .map(|item| {
            let own = names(&item);
            let key = (
                !filled(&item["imdbRating"]),
                !own.contains(&wanted),
                !own.iter().any(|n| n.starts_with(&wanted)),
                !filled(&item["year"]),
            );
            (key, item)
        })
        .collect();
    keyed.sort_by_key(|a| a.0);
    let mut kept: Vec<Value> = Vec::new();
    for (_, item) in keyed {
        let rated = filled(&item["imdbRating"]);
        let own = names(&item);
        let repeats = kept.iter().any(|better| {
            names(better).iter().any(|n| own.contains(n))
                && item["type"] == better["type"]
                && same_work_years(&text(&item["year"]), &text(&better["year"]))
        });
        if !rated && repeats {
            continue;
        }
        kept.push(item);
    }
    kept.truncate(limit);
    kept
}

fn compact_title_sql() -> String {
    let mut sql = "lower(c.title)".to_string();
    for separator in [" ", "-", "'", "’", ":", ".", "·", ","] {
        sql = format!("replace({sql}, '{}', '')", separator.replace('\'', "''"));
    }
    sql
}

fn query(
    c: &Connection,
    sql: &str,
    values: &[&dyn rusqlite::ToSql],
) -> rusqlite::Result<Vec<Value>> {
    let mut statement = c.prepare(sql)?;
    let rows = statement.query_map(values, row_json)?;
    rows.collect()
}

/// The instant search: the local catalogue and the French titles, ranked.
/// The search the interfaces show (the web's `/api/search`, the native
/// interface's field): titles in French first, then Cinemeta's, and KINO's own
/// index when neither finds anything.
pub async fn search(meta: &crate::meta::Meta, db: &Db, query: &str, kind: &str) -> Vec<Value> {
    let (catalogue, french) = tokio::join!(
        meta.search(query, kind),
        crate::french::search(meta, query, kind)
    );
    let known: Vec<Value> = french.iter().map(|m| m["id"].clone()).collect();
    let mut metas = french;
    metas.extend(
        catalogue
            .unwrap_or_default()
            .into_iter()
            .filter(|m| !known.contains(&m["id"])),
    );
    if metas.is_empty() {
        let only = ["movie", "series"].contains(&kind).then_some(kind);
        metas = search_fast(db, query, only, 20);
    }
    metas
}

pub fn search_fast(db: &Db, query_text: &str, kind: Option<&str>, limit: usize) -> Vec<Value> {
    // Punctuation separates words, as in the FTS index: « Spider-Man » is « spider » + « man ».
    let clean: String = query_text
        .trim()
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { ' ' })
        .collect();
    let tokens: Vec<&str> = clean.split_whitespace().collect();
    if tokens.is_empty() {
        return vec![];
    }
    let fts = tokens
        .iter()
        .map(|t| format!("\"{t}\"*"))
        .collect::<Vec<_>>()
        .join(" AND ");
    let wanted = limit;
    let limit = (limit * 3).max(24) as i64;
    let kind = kind.filter(|k| *k == "movie" || *k == "series");
    db.read(|c| {
        let base = "SELECT c.imdb_id as id, c.title as name, c.media_type as type, c.year, c.poster, c.rating as imdbRating, c.overview \
                    FROM media_catalog c JOIN media_fts s ON c.id = s.rowid WHERE media_fts MATCH ?";
        let mut rows = match kind {
            Some(k) => query(c, &format!("{base} AND c.media_type = ? ORDER BY rank LIMIT ?"), &[&fts, &k, &limit])?,
            None => query(c, &format!("{base} ORDER BY rank LIMIT ?"), &[&fts, &limit])?,
        };
        if rows.is_empty() {
            // Titles typed without their separators: « spiderman », « walle ».
            let compact = tokens.concat().to_lowercase();
            if compact.chars().count() >= 4 {
                let pattern = format!("%{}%", compact.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_"));
                let sql = format!(
                    "SELECT c.imdb_id as id, c.title as name, c.media_type as type, c.year, c.poster, c.rating as imdbRating, c.overview \
                     FROM media_catalog c WHERE {} LIKE ? ESCAPE '\\'",
                    compact_title_sql()
                );
                rows = match kind {
                    Some(k) => query(c, &format!("{sql} AND c.media_type = ? ORDER BY length(c.title) LIMIT ?"), &[&pattern, &k, &limit])?,
                    None => query(c, &format!("{sql} ORDER BY length(c.title) LIMIT ?"), &[&pattern, &limit])?,
                };
            }
        }
        let alias_sql = "SELECT a.imdb_id as id, COALESCE(NULLIF(c.title, ''), a.name) as name, \
                         COALESCE(c.media_type, a.media_type) as type, COALESCE(NULLIF(c.year, ''), a.year) as year, \
                         COALESCE(NULLIF(c.poster, ''), 'https://images.metahub.space/poster/medium/' || a.imdb_id || '/img') as poster, \
                         c.rating as imdbRating, c.overview, a.alias as name_fr \
                         FROM media_aliases a JOIN media_alias_fts f ON a.rowid = f.rowid \
                         LEFT JOIN media_catalog c ON c.imdb_id = a.imdb_id WHERE media_alias_fts MATCH ?";
        let aliases = match kind {
            Some(k) => query(c, &format!("{alias_sql} AND COALESCE(c.media_type, a.media_type) = ? ORDER BY rank LIMIT ?"), &[&fts, &k, &limit]),
            None => query(c, &format!("{alias_sql} ORDER BY rank LIMIT ?"), &[&fts, &limit]),
        }
        .unwrap_or_default();
        // French titles first: someone typing « Les Évadés » means the French title.
        let mut results = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for item in aliases.into_iter().chain(rows) {
            if let Some(id) = item["id"].as_str()
                && !id.is_empty()
                && seen.insert(id.to_string())
            {
                results.push(item);
            }
        }
        Ok(rank(query_text, results, wanted))
    })
    .unwrap_or_default()
}

/// {imdb_id: [genres]} for the titles the catalogue already knows.
pub fn media_genres(db: &Db, ids: &[String]) -> Map<String, Value> {
    let mut unique: Vec<&String> = Vec::new();
    for id in ids.iter().filter(|i| !i.is_empty()) {
        if !unique.contains(&id) {
            unique.push(id);
        }
    }
    unique.truncate(500);
    if unique.is_empty() {
        return Map::new();
    }
    let sql = format!(
        "SELECT imdb_id, genres FROM media_catalog WHERE imdb_id IN ({})",
        vec!["?"; unique.len()].join(",")
    );
    db.read(|c| {
        let mut statement = c.prepare(&sql)?;
        let rows = statement.query_map(params_from_iter(unique.iter()), |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
        })?;
        let mut found = Map::new();
        for (id, genres) in rows.flatten() {
            let names: Vec<Value> = genres
                .unwrap_or_default()
                .split(',')
                .map(str::trim)
                .filter(|g| !g.is_empty())
                .map(|g| json!(g))
                .collect();
            if !names.is_empty() {
                found.insert(id, Value::Array(names));
            }
        }
        Ok(found)
    })
    .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn index_search_and_similar() {
        let dir = tempfile::tempdir().unwrap();
        let db = Db::open(&dir.path().join("kino.db")).unwrap();
        index_media(
            &db,
            &[
                json!({"id": "tt0145487", "name": "Spider-Man", "type": "movie", "year": "2002", "poster": "p1", "imdbRating": "7.4", "genres": ["Action", "Adventure"]}),
                json!({"id": "tt0316654", "name": "Spider-Man 2", "type": "movie", "year": 2004, "poster": "p2", "imdbRating": 7.5, "genres": ["Action"]}),
                json!({"id": "tt0903747", "name": "Breaking Bad", "type": "series", "year": "2008", "poster": "p3", "imdbRating": "9.5", "genres": "Crime, Drama"}),
            ],
        );
        let found: Vec<String> = search_fast(&db, "spider man", None, 8)
            .iter()
            .map(|m| m["id"].as_str().unwrap().to_string())
            .collect();
        assert_eq!(found, ["tt0145487", "tt0316654"], "the exact title first");
        assert_eq!(
            search_fast(&db, "spiderman", Some("movie"), 8).len(),
            2,
            "typed without its hyphen"
        );
        assert!(search_fast(&db, "breaking", Some("movie"), 8).is_empty());
        assert!(search_fast(&db, "  -- ", None, 8).is_empty());
        assert_eq!(media_type(&db, "tt0903747").as_deref(), Some("series"));
        let similar: Vec<String> = similar(&db, "tt0145487", "movie", &["Action".into()], 8)
            .iter()
            .map(|m| m["id"].as_str().unwrap().to_string())
            .collect();
        assert_eq!(similar, ["tt0316654"]);
        assert_eq!(
            media_genres(&db, &["tt0903747".into(), "tt0903747".into(), "x".into()]),
            json!({"tt0903747": ["Crime", "Drama"]})
                .as_object()
                .unwrap()
                .clone()
        );
        index_media(
            &db,
            &[json!({"id": "tt0145487", "name": "Spider-Man", "poster": ""})],
        );
        let again = search_fast(&db, "spider-man", None, 8);
        let first = again.iter().find(|m| m["id"] == "tt0145487").unwrap();
        assert_eq!(first["poster"], "p1", "an empty poster keeps the known one");
    }

    #[test]
    fn same_work() {
        assert!(same_work_years("2002", "2003"));
        assert!(!same_work_years("2002", "2010"));
        assert!(same_work_years("", "2010"));
    }
}
