//! The Anime tab: rows of what is on (AniList) and the week's planning, each
//! work paired with the fiche KINO plays. A public table of identifiers
//! (arm.haglund.dev) gives the fiche and the season of each AniList work; only
//! AniList numbers of public titles are sent, nothing about the user.

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use chrono::{Datelike, Local, NaiveDate, TimeZone};
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::anime_journey::{AGENT, Invalid, py_str, resolve_work, truthy};
use crate::db::{Db, now};
use crate::meta::Meta;

const IDS_URL: &str = "https://arm.haglund.dev/api/v2/ids";
const SEASONS: [&str; 4] = ["WINTER", "SPRING", "SUMMER", "FALL"];
const SERIES_FORMATS: [&str; 3] = ["TV", "TV_SHORT", "ONA"];
const MAP_KEY: &str = "anime_playback_map_v1";
const MAP_KEEP: usize = 4000;
const FOUND_FOR: f64 = 30.0 * 86400.0;
const MISSING_FOR: f64 = 86400.0;
static PLAYBACK_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^tt\d{5,12}$").unwrap());
static ANILIST_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[1-9]\d{0,7}$").unwrap());
/// One writer at a time for the pairing table.
static MAP_LOCK: Mutex<()> = Mutex::new(());

const CARD_FIELDS: &str = "\nid title { english romaji } format episodes status averageScore popularity genres\ncoverImage { large } startDate { year month } nextAiringEpisode { episode airingAt }\n";

fn home_query() -> String {
    let c = CARD_FIELDS;
    format!(
        "\nquery ($season: MediaSeason, $year: Int, $nextSeason: MediaSeason, $nextYear: Int) {{\n  \
         trending: Page(perPage: 20) {{ media(type: ANIME, sort: TRENDING_DESC, isAdult: false) {{ {c} }} }}\n  \
         season: Page(perPage: 20) {{ media(type: ANIME, season: $season, seasonYear: $year, sort: POPULARITY_DESC, isAdult: false) {{ {c} }} }}\n  \
         upcoming: Page(perPage: 20) {{ media(type: ANIME, season: $nextSeason, seasonYear: $nextYear, sort: POPULARITY_DESC, isAdult: false) {{ {c} }} }}\n  \
         top: Page(perPage: 20) {{ media(type: ANIME, sort: SCORE_DESC, isAdult: false, format_in: [TV, MOVIE, ONA]) {{ {c} }} }}\n  \
         movies: Page(perPage: 20) {{ media(type: ANIME, format: MOVIE, sort: POPULARITY_DESC, isAdult: false) {{ {c} }} }}\n}}\n"
    )
}

fn week_query() -> String {
    format!(
        "\nquery ($start: Int, $end: Int, $page: Int) {{\n  Page(page: $page, perPage: 50) {{\n    pageInfo {{ hasNextPage }}\n    \
         airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {{\n      airingAt episode\n      \
         media {{ isAdult {CARD_FIELDS} }}\n    }}\n  }}\n}}\n"
    )
}

const PREQUEL_QUERY: &str = "\nquery ($id: Int) {\n  Media(id: $id, type: ANIME) {\n    id title { english romaji } format startDate { year }\n    relations { edges { relationType(version: 2) node { id format } } }\n  }\n}\n";

fn label(season: &str) -> &'static str {
    match season {
        "WINTER" => "Hiver",
        "SPRING" => "Printemps",
        "SUMMER" => "Été",
        _ => "Automne",
    }
}

/// The anime season of a day and the one after.
fn seasons(today: NaiveDate) -> ((&'static str, i32), (&'static str, i32)) {
    let index = (today.month0() / 3) as usize;
    let following = (index + 1) % 4;
    (
        (SEASONS[index], today.year()),
        (SEASONS[following], today.year() + i32::from(following == 0)),
    )
}

fn anilist_id(v: &Value) -> Option<i64> {
    let shown = match v {
        Value::Bool(_) => return None,
        other => py_str(other),
    };
    ANILIST_ID
        .is_match(&shown)
        .then(|| shown.parse().ok())
        .flatten()
}

/// What KINO opens for one row of the identifier table.
fn playback(row: &Value, format: &str) -> Value {
    let imdb = if truthy(&row["imdb"]) {
        py_str(&row["imdb"])
    } else {
        String::new()
    };
    if !row.is_object() || !PLAYBACK_ID.is_match(&imdb) {
        return Value::Null;
    }
    if format == "MOVIE" {
        return json!({"id": imdb, "type": "movie", "season": null});
    }
    if !SERIES_FORMATS.contains(&format) {
        return Value::Null;
    }
    let season = [&row["thetvdb-season"], &row["themoviedb-season"]]
        .into_iter()
        .filter_map(Value::as_i64)
        .find(|s| (1..=99).contains(s));
    json!({"id": imdb, "type": "series", "season": season})
}

fn stored_map(db: &Db) -> Map<String, Value> {
    db.cache_get(MAP_KEY)
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

fn save_map(db: &Db, stored: Map<String, Value>) {
    let stored = if stored.len() > MAP_KEEP {
        let mut newest: Vec<(String, Value)> = stored.into_iter().collect();
        let at = |v: &Value| v["at"].as_f64().unwrap_or(0.0);
        newest.sort_by(|a, b| at(&b.1).total_cmp(&at(&a.1)));
        newest.truncate(MAP_KEEP);
        newest.into_iter().collect()
    } else {
        stored
    };
    let _ = db.cache_set(MAP_KEY, &Value::Object(stored), 0.0);
}

impl Meta {
    /// {AniList id: fiche or null}: kept a month when found, a day when not; a
    /// failed request keeps nothing.
    pub async fn playback_ids(&self, works: &[(Value, Value)]) -> HashMap<i64, Value> {
        let mut formats: indexmap::IndexMap<i64, String> = indexmap::IndexMap::new();
        for (id, format) in works {
            if let Some(id) = anilist_id(id) {
                formats
                    .entry(id)
                    .or_insert_with(|| format.as_str().unwrap_or("").to_string());
            }
        }
        let at = now();
        let db = self.net.db();
        let stored = stored_map(db);
        let mut result: HashMap<i64, Value> = HashMap::new();
        let mut asked: Vec<i64> = Vec::new();
        for id in formats.keys() {
            match stored.get(&id.to_string()).filter(|e| e.is_object()) {
                Some(entry)
                    if at - entry["at"].as_f64().unwrap_or(0.0)
                        < if truthy(&entry["kino"]) {
                            FOUND_FOR
                        } else {
                            MISSING_FOR
                        } =>
                {
                    result.insert(*id, entry["kino"].clone());
                }
                _ => asked.push(*id),
            }
        }
        let mut updates: Map<String, Value> = Map::new();
        for chunk in asked.chunks(100) {
            let body = Value::Array(chunk.iter().map(|i| json!({"anilist": i})).collect());
            let headers = [("Content-Type", "application/json"), ("User-Agent", AGENT)];
            let rows = self
                .net
                .request_json(
                    reqwest::Method::POST,
                    IDS_URL,
                    &headers,
                    Some(&body),
                    Duration::from_secs(8),
                    0,
                )
                .await;
            let Some(rows) = rows
                .ok()
                .and_then(|r| r.as_array().cloned())
                .filter(|r| r.len() == chunk.len())
            else {
                for id in chunk {
                    result.insert(
                        *id,
                        stored
                            .get(&id.to_string())
                            .map(|e| e["kino"].clone())
                            .unwrap_or(Value::Null),
                    );
                }
                continue;
            };
            for (id, row) in chunk.iter().zip(rows) {
                let kino = playback(&row, &formats[id]);
                updates.insert(id.to_string(), json!({"kino": kino, "at": at}));
                result.insert(*id, kino);
            }
        }
        if !updates.is_empty() {
            let _guard = MAP_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            // Re-read: another request may have paired other works meanwhile.
            let mut latest = stored_map(db);
            latest.extend(updates);
            save_map(db, latest);
        }
        result
    }
}

fn remember_playback(db: &Db, id: i64, kino: &Value) {
    let _guard = MAP_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let mut stored = stored_map(db);
    stored.insert(id.to_string(), json!({"kino": kino, "at": now()}));
    save_map(db, stored);
}

fn or(v: &Value, default: Value) -> Value {
    if truthy(v) { v.clone() } else { default }
}

/// One AniList work as the page shows it.
pub fn card(media: &Value, kino: Value) -> Value {
    let titles = &media["title"];
    let title = [&titles["english"], &titles["romaji"]]
        .into_iter()
        .find(|v| truthy(v))
        .cloned()
        .unwrap_or(json!("Sans titre"));
    let romaji = or(&titles["romaji"], json!(""));
    let upcoming = &media["nextAiringEpisode"];
    let genres: Vec<Value> = media["genres"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|g| g.is_string())
        .take(3)
        .cloned()
        .collect();
    json!({
        "id": media["id"].as_i64().unwrap_or(0),
        "title": title,
        "romaji": if truthy(&romaji) && romaji != title { romaji } else { json!("") },
        "format": or(&media["format"], json!("")),
        "episodes": or(&media["episodes"], json!(0)),
        "status": or(&media["status"], json!("")),
        "score": or(&media["averageScore"], json!(0)),
        "popularity": or(&media["popularity"], json!(0)),
        "genres": genres,
        "poster": or(&media["coverImage"]["large"], json!("")),
        "year": or(&media["startDate"]["year"], json!(0)),
        "month": or(&media["startDate"]["month"], json!(0)),
        "next": if truthy(&upcoming["airingAt"]) {
            json!({"episode": or(&upcoming["episode"], json!(0)), "airing_at": or(&upcoming["airingAt"], json!(0))})
        } else {
            Value::Null
        },
        "kino": kino,
    })
}

async fn cards(meta: &Meta, medias: &[Value]) -> Vec<Value> {
    let medias: Vec<&Value> = medias
        .iter()
        .filter(|m| m.is_object() && truthy(&m["id"]))
        .collect();
    let works: Vec<(Value, Value)> = medias
        .iter()
        .map(|m| (m["id"].clone(), m["format"].clone()))
        .collect();
    let paired = meta.playback_ids(&works).await;
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for media in medias {
        let id = media["id"].as_i64().unwrap_or(0);
        if seen.insert(id) {
            out.push(card(media, paired.get(&id).cloned().unwrap_or(Value::Null)));
        }
    }
    out
}

/// The user's titles (history, Ma Liste) KINO knows to be anime, from what is
/// already on this computer.
fn library_ids(db: &Db) -> Vec<String> {
    let mut known: std::collections::HashSet<String> = stored_map(db)
        .values()
        .filter(|e| e["kino"].is_object())
        .map(|e| py_str(&e["kino"]["id"]))
        .collect();
    for kind in ["series", "movie"] {
        if let Some(Value::Array(pool)) = db.cache_get(&format!("anime_engine:pool:{kind}:v3")) {
            known.extend(
                pool.iter()
                    .filter(|m| m.is_object())
                    .map(|m| m.get("id").map(py_str).unwrap_or_else(|| "None".into())),
            );
        }
    }
    let mut mine: Vec<String> = Vec::new();
    for item in crate::library::history(db)
        .iter()
        .chain(crate::library::watchlist(db).iter())
    {
        let id = if truthy(&item["id"]) {
            py_str(&item["id"])
        } else {
            String::new()
        };
        if !id.is_empty()
            && (known.contains(&id) || truthy(&item["is_anime"]) || item["type"] == "anime")
            && !mine.contains(&id)
        {
            mine.push(id);
        }
    }
    mine
}

/// « À la une »: the rows, with the user's own anime titles.
pub async fn home(meta: &Meta, force: bool) -> Value {
    let today = Local::now().date_naive();
    let (current, following) = seasons(today);
    let db = meta.net.db();
    let key = format!("anime_home_v1:{}:{}", current.0, current.1);
    let mut result = if force {
        None
    } else {
        db.cache_get(&key).filter(Value::is_object)
    };
    if result.is_none() {
        let variables = json!({"season": current.0, "year": current.1, "nextSeason": following.0, "nextYear": following.1});
        result = Some(match meta.anilist(&home_query(), variables).await {
            Ok(data) => {
                let plan = [
                    (
                        "trending",
                        "Tendances".to_string(),
                        "Ce qui se regarde le plus en ce moment",
                    ),
                    (
                        "season",
                        format!("Cette saison · {} {}", label(current.0), current.1),
                        "Les plus suivis",
                    ),
                    (
                        "upcoming",
                        format!(
                            "La saison prochaine · {} {}",
                            label(following.0),
                            following.1
                        ),
                        "Les plus attendus",
                    ),
                    ("top", "Les mieux notés".to_string(), ""),
                    ("movies", "Films".to_string(), "Les plus populaires"),
                ];
                let mut rows = Vec::new();
                for (key, title, note) in plan {
                    let items = cards(
                        meta,
                        data[key]["media"]
                            .as_array()
                            .map(Vec::as_slice)
                            .unwrap_or(&[]),
                    )
                    .await;
                    if !items.is_empty() {
                        rows.push(
                            json!({"key": key, "title": title, "note": note, "items": items}),
                        );
                    }
                }
                let fresh = json!({"ok": true, "rows": rows, "updated_at": now() as i64});
                if !rows_empty(&fresh) {
                    let _ = db.cache_set(&key, &fresh, 1800.0);
                    let _ = db.cache_set("anime_home_last_v1", &fresh, 7.0 * 86400.0);
                }
                fresh
            }
            Err(_) => match db.cache_get("anime_home_last_v1").filter(Value::is_object) {
                Some(mut last) => {
                    last["stale"] = json!(true);
                    last
                }
                None => {
                    json!({"ok": false, "rows": [], "message": "Les rangées anime sont indisponibles pour le moment."})
                }
            },
        });
    }
    let mut out = result.unwrap_or_default();
    out["library"] = json!(library_ids(db));
    out
}

fn rows_empty(result: &Value) -> bool {
    result["rows"].as_array().is_none_or(Vec::is_empty)
}

/// Midnight today and seven days later, on this computer's clock.
fn week_bounds(at: f64) -> (i64, i64) {
    let day = Local
        .timestamp_opt(at as i64, 0)
        .single()
        .map(|d| d.date_naive())
        .unwrap_or_default();
    let midnight = |d: NaiveDate| {
        Local
            .from_local_datetime(&d.and_hms_opt(0, 0, 0).unwrap_or_default())
            .earliest()
            .map(|t| t.timestamp())
            .unwrap_or(0)
    };
    (midnight(day), midnight(day + chrono::Duration::days(7)))
}

/// Every episode airing from today to six days ahead, in airing order.
pub async fn week(meta: &Meta, force: bool) -> Value {
    let at = now();
    let (start, end) = week_bounds(at);
    let db = meta.net.db();
    let key = format!("anime_week_v1:{start}");
    if !force && let Some(cached) = db.cache_get(&key).filter(Value::is_object) {
        return cached;
    }
    let mut airings: Vec<Value> = Vec::new();
    let mut failed = false;
    for page in 1..=8 {
        match meta
            .anilist(
                &week_query(),
                json!({"start": start - 1, "end": end, "page": page}),
            )
            .await
        {
            Ok(data) => {
                let page = &data["Page"];
                airings.extend(
                    page["airingSchedules"]
                        .as_array()
                        .cloned()
                        .unwrap_or_default(),
                );
                if !truthy(&page["pageInfo"]["hasNextPage"]) {
                    break;
                }
            }
            Err(_) => {
                failed = true;
                break;
            }
        }
    }
    if failed {
        return match db
            .cache_get("anime_week_last_v1")
            .filter(|l| l.is_object() && l["to"].as_f64().unwrap_or(0.0) > at)
        {
            Some(mut last) => {
                last["stale"] = json!(true);
                last
            }
            None => {
                json!({"ok": false, "episodes": [], "message": "Planning indisponible pour le moment."})
            }
        };
    }
    airings.retain(|a| {
        let m = &a["media"];
        m.is_object()
            && truthy(&m["id"])
            && SERIES_FORMATS.contains(&m["format"].as_str().unwrap_or(""))
            && !truthy(&m["isAdult"])
    });
    let works: Vec<(Value, Value)> = airings
        .iter()
        .map(|a| (a["media"]["id"].clone(), a["media"]["format"].clone()))
        .collect();
    let paired = meta.playback_ids(&works).await;
    let mut episodes: Vec<Value> = airings
        .iter()
        .map(|a| {
            let id = a["media"]["id"].as_i64().unwrap_or(0);
            let mut work = card(&a["media"], paired.get(&id).cloned().unwrap_or(Value::Null));
            if let Some(w) = work.as_object_mut() {
                w.shift_remove("next");
                w.insert("episode".into(), or(&a["episode"], json!(1)));
                w.insert("airing_at".into(), or(&a["airingAt"], json!(0)));
            }
            work
        })
        .collect();
    let num = |v: &Value| v.as_f64().unwrap_or(0.0);
    episodes.sort_by(|a, b| {
        num(&a["airing_at"])
            .total_cmp(&num(&b["airing_at"]))
            .then(num(&b["popularity"]).total_cmp(&num(&a["popularity"])))
    });
    let result = json!({"ok": true, "from": start, "to": end, "episodes": episodes, "updated_at": at as i64});
    let _ = db.cache_set(&key, &result, 1800.0);
    let _ = db.cache_set("anime_week_last_v1", &result, 2.0 * 86400.0);
    result
}

/// The fiche of a work that came without one: a new season opens on its series
/// (found through its prequels), anything else by title and year.
pub async fn open_work(meta: &Meta, id: &Value) -> Result<Value, Invalid> {
    let id = anilist_id(id).ok_or(Invalid)?;
    let db = meta.net.db();
    let media = meta
        .anilist(PREQUEL_QUERY, json!({"id": id}))
        .await
        .map(|d| d["Media"].clone())
        .ok()
        .filter(truthy)
        .unwrap_or(json!({}));
    let title = [&media["title"]["english"], &media["title"]["romaji"]]
        .into_iter()
        .find(|v| truthy(v))
        .map(py_str)
        .unwrap_or_default();
    let format = media["format"].as_str().unwrap_or("").to_string();
    let year = if truthy(&media["startDate"]["year"]) {
        py_str(&media["startDate"]["year"])
    } else {
        String::new()
    };
    let answer = |kino: &Value, name: &str, poster: &Value| {
        json!({"ok": true, "media": {"id": kino["id"], "name": name, "type": kino["type"], "is_anime": true, "year": year,
                                      "poster": poster, "open_season": or(&kino["season"], json!(0))}})
    };
    if truthy(&media) {
        let kino = meta
            .playback_ids(&[(json!(id), media["format"].clone())])
            .await
            .remove(&id)
            .unwrap_or(Value::Null);
        if truthy(&kino) {
            return Ok(answer(&kino, &title, &json!("")));
        }
    }
    // A sequel shares the fiche of the season before it.
    let (mut current, mut steps) = (media.clone(), 0);
    while truthy(&current) && SERIES_FORMATS.contains(&format.as_str()) && steps < 4 {
        steps += 1;
        let prequel = current["relations"]["edges"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|e| {
                e["relationType"] == "PREQUEL"
                    && SERIES_FORMATS.contains(&e["node"]["format"].as_str().unwrap_or(""))
            });
        let Some(prequel) = prequel
            .map(|e| e["node"].clone())
            .filter(|n| truthy(&n["id"]))
        else {
            break;
        };
        let prequel_id = prequel["id"].as_i64().unwrap_or(0);
        let found = meta
            .playback_ids(&[(prequel["id"].clone(), prequel["format"].clone())])
            .await
            .remove(&prequel_id)
            .unwrap_or(Value::Null);
        if truthy(&found) {
            let season = found["season"]
                .as_i64()
                .filter(|s| *s != 0)
                .map(|s| json!(s + steps))
                .unwrap_or(Value::Null);
            let kino = json!({"id": found["id"], "type": "series", "season": season});
            remember_playback(db, id, &kino);
            return Ok(answer(&kino, &title, &json!("")));
        }
        match meta.anilist(PREQUEL_QUERY, json!({"id": prequel_id})).await {
            Ok(data) => {
                current = if truthy(&data["Media"]) {
                    data["Media"].clone()
                } else {
                    json!({})
                }
            }
            Err(_) => break,
        }
    }
    let matched = resolve_work(meta, &json!(id), None)
        .await
        .unwrap_or(json!({"ok": false}));
    if truthy(&matched["ok"]) {
        let found = &matched["media"];
        let kino = json!({"id": found["id"], "type": found["type"], "season": null});
        remember_playback(db, id, &kino);
        let name = if truthy(&found["name"]) {
            py_str(&found["name"])
        } else {
            title.clone()
        };
        return Ok(answer(&kino, &name, &or(&found["poster"], json!(""))));
    }
    Ok(json!({"ok": false, "title": title, "message": "Aucune fiche KINO vérifiée pour ce titre."}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seasons_pairing_and_cards() {
        let day = |m| NaiveDate::from_ymd_opt(2025, m, 15).unwrap();
        assert_eq!(seasons(day(1)), (("WINTER", 2025), ("SPRING", 2025)));
        assert_eq!(seasons(day(11)), (("FALL", 2025), ("WINTER", 2026)));
        let row = json!({"imdb": "tt2560140", "thetvdb-season": 3, "themoviedb-season": 2});
        assert_eq!(
            playback(&row, "TV"),
            json!({"id": "tt2560140", "type": "series", "season": 3})
        );
        assert_eq!(
            playback(&row, "MOVIE"),
            json!({"id": "tt2560140", "type": "movie", "season": null})
        );
        assert_eq!(playback(&row, "OVA"), Value::Null);
        assert_eq!(playback(&json!({"imdb": "tt12"}), "TV"), Value::Null);
        assert_eq!(anilist_id(&json!(true)), None);
        assert_eq!(anilist_id(&json!("0")), None);
        let c = card(
            &json!({"id": 5, "title": {"romaji": "Shingeki"}, "genres": ["A", 3, "B", "C", "D"]}),
            Value::Null,
        );
        assert_eq!(c["title"], "Shingeki");
        assert_eq!(c["romaji"], "", "the same title is not shown twice");
        assert_eq!(c["genres"], json!(["A", "B", "C"]));
    }
}
