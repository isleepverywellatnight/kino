//! « Parcours » of an anime franchise: its works in release order from
//! AniList's relations, each paired with the KINO fiche that plays it only
//! when title, year and kind agree without doubt. The user may reorder a saga.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;
use std::time::Duration;

use regex::Regex;
use serde_json::{Map, Value, json};
use unicode_normalization::UnicodeNormalization;
use unicode_normalization::char::is_combining_mark;

use crate::db::Db;
use crate::meta::Meta;
use crate::net::NetError;

pub const ANILIST: &str = "https://graphql.anilist.co";
pub const AGENT: &str = "KINO-Desktop/2.0";
const ORDER_KEY: &str = "anime_journey_order_v1";
static PLAYBACK_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^tt\d{5,12}$").unwrap());
static SAGA_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[1-9]\d{0,7}$").unwrap());
static ANILIST_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{1,8}$").unwrap());
static NOT_ALNUM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9]+").unwrap());
static YEAR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(19|20)\d{2}\b").unwrap());
const RELATIONS: [&str; 7] = [
    "SEQUEL",
    "PREQUEL",
    "SIDE_STORY",
    "PARENT",
    "SPIN_OFF",
    "ALTERNATIVE",
    "SUMMARY",
];
const FORMATS: [&str; 6] = ["TV", "TV_SHORT", "ONA", "OVA", "MOVIE", "SPECIAL"];
const SEARCH_QUERY: &str = "\nquery ($search: String) {\n  Page(page: 1, perPage: 8) {\n    media(search: $search, type: ANIME) {\n      id title { english romaji native } synonyms format startDate { year month day }\n    }\n  }\n}\n";
const WORK_FIELDS: &str =
    "\nid title { english romaji native } synonyms format episodes\nstartDate { year month day }\n";

fn work_query() -> String {
    format!(
        "query ($id: Int) {{ Media(id: $id, type: ANIME) {{ {WORK_FIELDS} relations {{ edges {{ relationType(version: 2) node {{ {WORK_FIELDS} }} }} }} }} }}"
    )
}

fn identity_query() -> String {
    format!("query ($id: Int) {{ Media(id: $id, type: ANIME) {{ {WORK_FIELDS} }} }}")
}

/// A refused request: the page gets a 400.
#[derive(Debug)]
pub struct Invalid;

pub fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false))
        && v != &json!(0)
        && v != &json!("")
        && v != &json!([])
        && v != &json!({})
}

/// Python's str(value).
pub fn py_str(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

pub fn normalize(value: &Value) -> String {
    let raw = if truthy(value) {
        py_str(value)
    } else {
        String::new()
    };
    let folded = raw.to_lowercase().replace('ß', "ss");
    let plain: String = folded.nfkd().filter(|c| !is_combining_mark(*c)).collect();
    NOT_ALNUM.replace_all(&plain, " ").trim().to_string()
}

fn year_of(value: &Value) -> Option<i64> {
    let raw = if truthy(value) {
        py_str(value)
    } else {
        String::new()
    };
    YEAR.find(&raw).and_then(|m| m.as_str().parse().ok())
}

fn titles(media: &Value) -> Vec<Value> {
    let t = &media["title"];
    let mut out = vec![
        t["english"].clone(),
        t["romaji"].clone(),
        t["native"].clone(),
    ];
    out.extend(media["synonyms"].as_array().cloned().unwrap_or_default());
    out
}

/// A whole number written as Python's str() would show it (no bool).
fn id_text(v: &Value) -> Option<String> {
    match v {
        Value::Bool(_) => None,
        Value::Number(n) if n.is_i64() || n.is_u64() => Some(n.to_string()),
        Value::Number(n) => Some(py_str(&json!(n))),
        Value::String(s) => Some(s.clone()),
        Value::Null => Some("None".into()),
        _ => None,
    }
}

fn int(v: &Value) -> i64 {
    v.as_i64()
        .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0)
}

impl Meta {
    pub async fn anilist(&self, query: &str, variables: Value) -> Result<Value, NetError> {
        let body = json!({"query": query, "variables": variables});
        let headers = [("Content-Type", "application/json"), ("User-Agent", AGENT)];
        let answer = self
            .net
            .request_json(
                reqwest::Method::POST,
                ANILIST,
                &headers,
                Some(&body),
                Duration::from_secs(8),
                0,
            )
            .await?;
        if truthy(&answer["errors"]) {
            return Err(NetError("AniList returned an error".into()));
        }
        Ok(if truthy(&answer["data"]) {
            answer["data"].clone()
        } else {
            json!({})
        })
    }

    async fn candidate(
        &self,
        title: &str,
        year: Option<i64>,
        kind: &str,
    ) -> Result<Option<Value>, NetError> {
        let search: String = title.chars().take(120).collect();
        let data = self
            .anilist(SEARCH_QUERY, json!({"search": search}))
            .await?;
        let requested = normalize(&json!(title));
        let mut matches: Vec<Value> = Vec::new();
        for media in data["Page"]["media"].as_array().into_iter().flatten() {
            let format = media["format"].as_str().unwrap_or("");
            let expected = if kind == "movie" {
                format == "MOVIE"
            } else {
                FORMATS.contains(&format) && format != "MOVIE"
            };
            if !expected || !titles(media).iter().any(|t| normalize(t) == requested) {
                continue;
            }
            if let Some(y) = year
                && media["startDate"]["year"].as_i64() != Some(y)
            {
                continue;
            }
            matches.push(media.clone());
        }
        let ids: HashSet<String> = matches.iter().map(|m| m["id"].to_string()).collect();
        Ok((ids.len() == 1).then(|| matches.remove(0)))
    }
}

fn work(media: &Value, relation: &str, playback: Value) -> Value {
    let date = &media["startDate"];
    let year = date["year"].clone();
    let title = [&media["title"]["english"], &media["title"]["romaji"]]
        .into_iter()
        .find(|v| truthy(v))
        .cloned()
        .unwrap_or(json!("Œuvre sans titre"));
    let release = truthy(&year).then(|| {
        [("year", 4), ("month", 2), ("day", 2)]
            .iter()
            .map(|(part, width)| {
                let shown = if truthy(&date[*part]) {
                    py_str(&date[*part])
                } else {
                    "0".into()
                };
                format!("{shown:0>width$}")
            })
            .collect::<Vec<_>>()
            .join("-")
    });
    let id = int(&media["id"]);
    json!({
        "id": id, "title": title, "format": if truthy(&media["format"]) { media["format"].clone() } else { json!("UNKNOWN") },
        "relation": relation, "year": year, "release": release, "episodes": media["episodes"], "playback": playback,
        "source_url": format!("https://anilist.co/anime/{id}"),
    })
}

fn remember_work(db: &Db, media: &Value) {
    if truthy(&media["id"]) {
        let _ = db.cache_set(
            &format!("anime_journey_identity_v1:{}", int(&media["id"])),
            media,
            86400.0,
        );
    }
}

fn release_key(work: &Value) -> (String, i64) {
    (
        work["release"].as_str().unwrap_or("9999-99-99").to_string(),
        work["id"].as_i64().unwrap_or(0),
    )
}

fn orders(db: &Db) -> Map<String, Value> {
    db.get_config(ORDER_KEY)
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

/// The saga in the order the user chose, when they chose one.
fn with_order(db: &Db, result: Value) -> Value {
    if !truthy(&result["ok"]) {
        return result;
    }
    let Some(order) = orders(db)
        .get(&py_str(&result["saga_id"]))
        .and_then(Value::as_array)
        .cloned()
    else {
        return result;
    };
    let mut positions: HashMap<i64, usize> = HashMap::new();
    for (index, id) in order.iter().enumerate() {
        if let Some(id) = id.as_i64() {
            positions.insert(id, index);
        } else if let Value::Bool(b) = id {
            positions.insert(*b as i64, index);
        }
    }
    let mut works = result["works"].as_array().cloned().unwrap_or_default();
    if !works
        .iter()
        .any(|w| w["id"].as_i64().is_some_and(|i| positions.contains_key(&i)))
    {
        return result;
    }
    let rank = |w: &Value| {
        (
            positions
                .get(&w["id"].as_i64().unwrap_or(-1))
                .copied()
                .unwrap_or(positions.len()),
            release_key(w),
        )
    };
    works.sort_by_key(rank);
    let mut out = result;
    out["works"] = Value::Array(works);
    out["order_label"] = json!("Ordre personnalisé");
    out
}

fn saga_id(v: &Value) -> Result<i64, Invalid> {
    let shown = id_text(v).ok_or(Invalid)?;
    if !SAGA_ID.is_match(&shown) {
        return Err(Invalid);
    }
    shown.parse().map_err(|_| Invalid)
}

fn playback_identity(imdb_id: &Value, kind: &Value) -> Result<(String, String), Invalid> {
    let id = py_str(imdb_id);
    let kind = kind.as_str().unwrap_or("");
    if !PLAYBACK_ID.is_match(&id) || !["movie", "series"].contains(&kind) {
        return Err(Invalid);
    }
    Ok((id, kind.to_string()))
}

/// The saga as the user ordered it; every work exactly once.
pub fn save_order(
    db: &Db,
    imdb_id: &Value,
    kind: &Value,
    saga: &Value,
    ids: &Value,
) -> Result<Value, Invalid> {
    let saga = saga_id(saga)?;
    let (id, kind) = playback_identity(imdb_id, kind)?;
    let cached = db
        .cache_get(&format!("anime_journey_v1:{kind}:{id}"))
        .filter(Value::is_object)
        .ok_or(Invalid)?;
    if cached["saga_id"].as_i64() != Some(saga) {
        return Err(Invalid);
    }
    let expected: Vec<Value> = cached["works"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|w| w["id"].clone())
        .collect();
    let list = ids.as_array().ok_or(Invalid)?;
    let all_ints = list.iter().all(|i| i.is_i64() || i.is_u64());
    let same = list.iter().map(|i| i.to_string()).collect::<HashSet<_>>()
        == expected
            .iter()
            .map(|i| i.to_string())
            .collect::<HashSet<_>>();
    if list.len() != expected.len() || !all_ints || !same {
        return Err(Invalid);
    }
    let mut all = orders(db);
    all.insert(saga.to_string(), ids.clone());
    let skip = all.len().saturating_sub(100);
    let kept: Map<String, Value> = all.into_iter().skip(skip).collect();
    db.save_config(ORDER_KEY, &Value::Object(kept))
        .map_err(|_| Invalid)?;
    Ok(with_order(db, cached))
}

pub fn reset_order(db: &Db, saga: &Value) -> Result<(), Invalid> {
    let saga = saga_id(saga)?;
    let mut all = orders(db);
    all.shift_remove(&saga.to_string());
    db.save_config(ORDER_KEY, &Value::Object(all))
        .map_err(|_| Invalid)
}

pub fn cached_journey(db: &Db, imdb_id: &Value, kind: &Value) -> Result<Value, Invalid> {
    let (id, kind) = playback_identity(imdb_id, kind)?;
    let cached = db
        .cache_get(&format!("anime_journey_v1:{kind}:{id}"))
        .filter(|c| c.is_object() && truthy(&c["ok"]))
        .ok_or(Invalid)?;
    Ok(with_order(db, cached))
}

/// The fiche of one work of a loaded saga.
pub async fn resolve_member(
    meta: &Meta,
    imdb_id: &Value,
    kind: &Value,
    anilist_id: &Value,
) -> Result<Value, Invalid> {
    let result = cached_journey(meta.net.db(), imdb_id, kind)?;
    let shown = id_text(anilist_id)
        .filter(|s| ANILIST_ID.is_match(s))
        .ok_or(Invalid)?;
    let wanted: i64 = shown.parse().map_err(|_| Invalid)?;
    let work = result["works"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|w| w["id"].as_i64() == Some(wanted))
        .cloned()
        .ok_or(Invalid)?;
    if truthy(&work["playback"]) {
        let year = if truthy(&work["year"]) {
            py_str(&work["year"])
        } else {
            String::new()
        };
        return Ok(
            json!({"ok": true, "media": {"id": work["playback"]["id"], "name": work["title"], "type": work["playback"]["type"],
                                               "is_anime": true, "year": year, "poster": ""}}),
        );
    }
    resolve_work(meta, &json!(wanted), Some(&py_str(imdb_id))).await
}

/// The release-order path of the franchise a fiche belongs to.
pub async fn get_journey(
    meta: &Meta,
    imdb_id: &Value,
    title: &Value,
    release_year: &Value,
    kind: &Value,
    force: bool,
) -> Result<Value, Invalid> {
    let playback = if truthy(imdb_id) {
        py_str(imdb_id)
    } else {
        String::new()
    };
    let kind = if truthy(kind) {
        py_str(kind)
    } else {
        String::new()
    };
    if !PLAYBACK_ID.is_match(&playback) || !["movie", "series"].contains(&kind.as_str()) {
        return Err(Invalid);
    }
    let title = title
        .as_str()
        .map(str::trim)
        .filter(|t| (1..=160).contains(&t.chars().count()))
        .ok_or(Invalid)?;
    let year = year_of(release_year);
    let db = meta.net.db();
    let key = format!("anime_journey_v1:{kind}:{playback}");
    if !force && let Some(cached) = db.cache_get(&key).filter(Value::is_object) {
        return Ok(with_order(db, cached));
    }
    let unavailable = json!({"ok": false, "reason": "unavailable", "message": "Parcours indisponible pour le moment."});
    let candidate = match meta.candidate(title, year, &kind).await {
        Ok(Some(c)) => c,
        Ok(None) => {
            return Ok(
                json!({"ok": false, "reason": "unmatched", "message": "Aucune œuvre AniList ne correspond avec certitude à ce titre et cette année."}),
            );
        }
        Err(_) => return Ok(unavailable),
    };
    let root = match meta
        .anilist(&work_query(), json!({"id": int(&candidate["id"])}))
        .await
    {
        Ok(data) => data["Media"].clone(),
        Err(_) => return Ok(unavailable),
    };
    if !truthy(&root) {
        return Ok(
            json!({"ok": false, "reason": "unavailable", "message": "Relations AniList indisponibles."}),
        );
    }
    remember_work(db, &root);
    let root_id = int(&root["id"]);
    let mut works: indexmap::IndexMap<i64, Value> = indexmap::IndexMap::new();
    works.insert(
        root_id,
        work(&root, "SELF", json!({"id": playback, "type": kind})),
    );
    let mut mainline: Vec<i64> = vec![root_id];
    let mut queue: Vec<i64> = vec![root_id];
    let mut expanded: HashSet<i64> = HashSet::new();
    let (mut partial, mut relations_known) = (false, false);
    while !queue.is_empty() && expanded.len() < 8 && works.len() < 32 {
        let current_id = queue.remove(0);
        if !expanded.insert(current_id) {
            continue;
        }
        let current = if current_id == root_id {
            root.clone()
        } else {
            match meta.anilist(&work_query(), json!({"id": current_id})).await {
                Ok(data) => data["Media"].clone(),
                Err(_) => {
                    partial = true;
                    continue;
                }
            }
        };
        let edges = current["relations"]["edges"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        relations_known = relations_known || !edges.is_empty();
        for edge in &edges {
            let node = &edge["node"];
            let relation = edge["relationType"].as_str().unwrap_or("");
            if !RELATIONS.contains(&relation)
                || !FORMATS.contains(&node["format"].as_str().unwrap_or(""))
                || !truthy(&node["id"])
            {
                continue;
            }
            let id = int(&node["id"]);
            remember_work(db, node);
            if id != root_id && !works.contains_key(&id) {
                works.insert(id, work(node, relation, Value::Null));
            }
            if relation == "SEQUEL"
                || relation == "PREQUEL"
                || (relation == "PARENT" && current_id == root_id)
            {
                if !mainline.contains(&id) {
                    mainline.push(id);
                }
                if !expanded.contains(&id) && !queue.contains(&id) {
                    queue.push(id);
                }
            }
            if works.len() >= 32 {
                partial = true;
                break;
            }
        }
    }
    partial = partial || !queue.is_empty();
    let saga = mainline
        .iter()
        .min_by_key(|i| release_key(&works[*i]))
        .copied()
        .unwrap_or(root_id);
    let mut sorted: Vec<Value> = works.into_values().collect();
    sorted.sort_by_key(release_key);
    let result = json!({"ok": true, "saga_id": saga, "order_label": "Ordre de sortie", "source": "AniList",
                        "relations_known": relations_known, "partial": partial, "works": sorted});
    let _ = db.cache_set(&key, &result, 86400.0);
    Ok(with_order(db, result))
}

async fn cinemeta_results(meta: &Meta, title: &str, kind: &str) -> Result<Vec<Value>, NetError> {
    let url = format!(
        "{}/catalog/{kind}/top/search={}.json",
        meta.cinemeta,
        crate::sources::stream::py_quote(title)
    );
    let data = meta
        .net
        .request_json(
            reqwest::Method::GET,
            &url,
            &[("User-Agent", AGENT)],
            None,
            Duration::from_secs(8),
            0,
        )
        .await?;
    Ok(data["metas"]
        .as_array()
        .into_iter()
        .flatten()
        .take(20)
        .cloned()
        .collect())
}

fn check_current(result: Value, current: Option<&str>) -> Value {
    if truthy(&result["ok"]) && current.is_some_and(|c| !c.is_empty() && result["media"]["id"] == c)
    {
        return json!({"ok": false, "reason": "shared_series", "message": "Cette œuvre partage une fiche avec la série ; sa saison exacte reste à vérifier."});
    }
    result
}

/// The fiche of one work, only when title, year and kind agree with a single Cinemeta listing.
pub async fn resolve_work(
    meta: &Meta,
    anilist_id: &Value,
    current: Option<&str>,
) -> Result<Value, Invalid> {
    let shown = id_text(anilist_id)
        .filter(|s| ANILIST_ID.is_match(s))
        .ok_or(Invalid)?;
    if current.is_some_and(|c| !PLAYBACK_ID.is_match(c)) {
        return Err(Invalid);
    }
    let id: i64 = shown.parse().map_err(|_| Invalid)?;
    let db = meta.net.db();
    let match_key = format!("anime_journey_match_v1:{id}");
    if let Some(cached) = db.cache_get(&match_key).filter(Value::is_object) {
        return Ok(check_current(cached, current));
    }
    let unavailable = json!({"ok": false, "reason": "unavailable", "message": "Vérification de lecture indisponible."});
    let media = match db
        .cache_get(&format!("anime_journey_identity_v1:{id}"))
        .filter(Value::is_object)
    {
        Some(m) => m,
        None => match meta.anilist(&identity_query(), json!({"id": id})).await {
            Ok(data) => data["Media"].clone(),
            Err(_) => return Ok(unavailable),
        },
    };
    let format = media["format"].as_str().unwrap_or("");
    if !truthy(&media) || !FORMATS.contains(&format) {
        return Ok(
            json!({"ok": false, "reason": "unmatched", "message": "Œuvre indisponible dans KINO."}),
        );
    }
    let mut search: Vec<String> = Vec::new();
    for t in titles(&media) {
        if let Some(s) = t.as_str().filter(|s| !s.trim().is_empty())
            && !search.iter().any(|x| x == s)
        {
            search.push(s.to_string());
        }
    }
    search.truncate(3);
    let acceptable: HashSet<String> = titles(&media).iter().map(normalize).collect();
    let expected_year = media["startDate"]["year"].as_i64();
    let kinds: &[&str] = match format {
        "MOVIE" => &["movie"],
        "TV" | "TV_SHORT" | "ONA" => &["series"],
        _ => &["movie", "series"],
    };
    let mut matches: indexmap::IndexMap<String, Value> = indexmap::IndexMap::new();
    for kind in kinds {
        for title in &search {
            let Ok(found) = cinemeta_results(meta, title, kind).await else {
                return Ok(unavailable);
            };
            for candidate in found {
                let cid = if truthy(&candidate["id"]) {
                    py_str(&candidate["id"])
                } else {
                    String::new()
                };
                if !PLAYBACK_ID.is_match(&cid)
                    || candidate["type"] != *kind
                    || !acceptable.contains(&normalize(&candidate["name"]))
                {
                    continue;
                }
                let shown = if truthy(&candidate["releaseInfo"]) {
                    &candidate["releaseInfo"]
                } else {
                    &candidate["year"]
                };
                if expected_year.is_some()
                    && expected_year != Some(0)
                    && year_of(shown) != expected_year
                {
                    continue;
                }
                matches.insert(cid, candidate);
            }
            if !matches.is_empty() {
                break;
            }
        }
    }
    if matches.len() != 1 {
        let reason = if matches.is_empty() {
            "unmatched"
        } else {
            "ambiguous"
        };
        let result = json!({"ok": false, "reason": reason, "message": "Correspondance de lecture non vérifiée pour cette œuvre."});
        let _ = db.cache_set(&match_key, &result, 3600.0);
        return Ok(result);
    }
    let candidate = matches.into_values().next().unwrap_or_default();
    if candidate.get("name").is_none() || candidate.get("type").is_none() {
        return Ok(unavailable);
    }
    let shown = [&candidate["releaseInfo"], &candidate["year"]]
        .into_iter()
        .find(|v| truthy(v))
        .map(py_str)
        .unwrap_or_default();
    let poster = if truthy(&candidate["poster"]) {
        candidate["poster"].clone()
    } else {
        json!("")
    };
    let result = json!({"ok": true, "media": {"id": candidate["id"], "name": candidate["name"], "type": candidate["type"],
                                              "is_anime": true, "year": shown, "poster": poster}});
    let _ = db.cache_set(&match_key, &result, 86400.0);
    Ok(check_current(result, current))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn titles_years_and_works() {
        assert_eq!(
            normalize(&json!("Shingeki no Kyojin: The Final Season")),
            "shingeki no kyojin the final season"
        );
        assert_eq!(normalize(&json!("Pokémon")), "pokemon");
        assert_eq!(year_of(&json!("2013–2023")), Some(2013));
        let w = work(
            &json!({"id": 16498, "title": {"romaji": "Shingeki"}, "format": "TV", "startDate": {"year": 2013, "month": 4}}),
            "SELF",
            Value::Null,
        );
        assert_eq!(w["release"], "2013-04-00");
        assert_eq!(w["title"], "Shingeki");
        assert!(saga_id(&json!(true)).is_err());
        assert_eq!(saga_id(&json!("12")).unwrap(), 12);
        assert!(saga_id(&json!(12.0)).is_err(), "« 12.0 » is not an id");
    }
}
