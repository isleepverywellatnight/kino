//! Titles from Cinemeta: the catalogue rows, the search, a title's page (with
//! its summary in French and similar titles), and a series' episodes.
//!
//! Cache keys are the Python version's, so both share what they fetched.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, LazyLock, RwLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use md5::{Digest, Md5};
use serde_json::{Map, Value, json};

use crate::catalog;
use crate::net::{Net, NetResult};

pub const CINEMETA: &str = "https://v3-cinemeta.strem.io";

struct Tables {
    genre_fr: HashMap<String, String>,
    imdb_aliases: HashMap<String, String>,
    anime_image_fixes: Map<String, Value>,
    classics: Vec<Value>,
}

static TABLES: LazyLock<Tables> = LazyLock::new(|| {
    let data: Value = serde_json::from_str(include_str!("data/meta.json")).expect("meta.json");
    let strings = |v: &Value| -> HashMap<String, String> {
        v.as_object()
            .into_iter()
            .flatten()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string()))
            .collect()
    };
    let classics = data["classics"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|c| {
            let id = c[0].as_str().unwrap_or("");
            json!({"id": id, "name": c[1], "type": "movie", "releaseInfo": c[2], "year": c[2], "imdbRating": c[3],
                   "genres": c[4], "description": c[5],
                   "poster": format!("https://images.metahub.space/poster/medium/{id}/img"),
                   "background": format!("https://images.metahub.space/background/medium/{id}/img")})
        })
        .collect();
    Tables {
        genre_fr: strings(&data["genre_fr"]),
        imdb_aliases: strings(&data["imdb_aliases"]),
        anime_image_fixes: data["anime_image_fixes"]
            .as_object()
            .cloned()
            .unwrap_or_default(),
        classics,
    }
});

/// Anime titles known to KINO; the anime catalogue adds to it as it is read.
pub static KNOWN_ANIME: LazyLock<RwLock<HashSet<String>>> = LazyLock::new(|| {
    let data: Value = serde_json::from_str(include_str!("data/meta.json")).expect("meta.json");
    RwLock::new(
        data["known_anime_ids"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect(),
    )
});

pub fn fallback_animes() -> Vec<Value> {
    let data: Value = serde_json::from_str(include_str!("data/meta.json")).expect("meta.json");
    data["fallback_animes"]
        .as_array()
        .cloned()
        .unwrap_or_default()
}

pub fn is_anime_item(item: &Value) -> bool {
    if item["is_anime"].as_bool() == Some(true) {
        return true;
    }
    if let Some(id) = item["id"].as_str()
        && KNOWN_ANIME.read().map(|k| k.contains(id)).unwrap_or(false)
    {
        return true;
    }
    item["genres"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|g| g.as_str().unwrap_or("").to_lowercase() == "anime")
}

fn text(value: &Value) -> String {
    match value {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        Value::Number(n) if n.as_f64() == Some(0.0) => String::new(),
        other => other.to_string(),
    }
}

fn first4(value: &str) -> String {
    value.chars().take(4).collect()
}

fn released(item: &Value) -> String {
    let r = text(&item["releaseInfo"]);
    if r.is_empty() { text(&item["year"]) } else { r }
}

/// Python's sort orders of a catalogue page.
fn sort_metas(metas: &mut [Value], sort: &str) {
    match sort {
        "imdbRating" => {
            let rating = |m: &Value| -> f64 {
                let r = text(&m["imdbRating"]);
                if !r.is_empty() && r.replacen('.', "", 1).chars().all(|c| c.is_ascii_digit()) {
                    r.parse().unwrap_or(0.0)
                } else {
                    0.0
                }
            };
            metas.sort_by(|a, b| rating(b).total_cmp(&rating(a)));
        }
        "recent" => metas.sort_by_key(|m| {
            let r = released(m);
            std::cmp::Reverse(first4(if r.is_empty() { "0" } else { &r }))
        }),
        "oldest" => metas.sort_by_key(|m| {
            let year = first4(&released(m));
            if year.len() == 4 && year.chars().all(|c| c.is_ascii_digit()) {
                year.parse().unwrap_or(9999)
            } else {
                9999
            }
        }),
        _ => {}
    }
}

pub fn classics(genre: &str, sort: &str) -> Vec<Value> {
    let genre = genre.trim().to_lowercase();
    let mut items: Vec<Value> = TABLES
        .classics
        .iter()
        .filter(|c| {
            genre.is_empty()
                || c["genres"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .any(|g| g.as_str().unwrap_or("").to_lowercase() == genre)
        })
        .cloned()
        .collect();
    match sort {
        "imdbRating" => items.sort_by(|a, b| {
            let r = |m: &Value| text(&m["imdbRating"]).parse::<f64>().unwrap_or(0.0);
            r(b).total_cmp(&r(a))
        }),
        "recent" => {
            items.sort_by_key(|m| std::cmp::Reverse(text(&m["year"]).parse::<i64>().unwrap_or(0)))
        }
        "oldest" => items.sort_by_key(|m| text(&m["year"]).parse::<i64>().unwrap_or(9999)),
        _ => {}
    }
    items
}

/// Today in UTC, as YYYY-MM-DD.
pub fn today_utc() -> String {
    let days = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64 / 86400)
        .unwrap_or(0);
    // Howard Hinnant's civil_from_days.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}")
}

fn number(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_f64().map(|f| f as i64))
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0)
}

pub struct Meta {
    pub net: Arc<Net>,
    pub cinemeta: String,
    pub translate: bool,
}

impl Meta {
    async fn json(&self, url: &str) -> NetResult<Value> {
        self.net
            .get_json(url, "KINO-Desktop", Duration::from_secs(20))
            .await
    }

    fn index_later(&self, items: Vec<Value>) {
        if items.is_empty() {
            return;
        }
        let net = self.net.clone();
        tokio::task::spawn_blocking(move || catalog::index_media(net.db(), &items));
    }

    /// One page of a catalogue: films, series or the classics (anime: anime.rs).
    pub async fn catalog_top(
        &self,
        kind: &str,
        genre: &str,
        skip: u64,
        sort: &str,
    ) -> NetResult<Vec<Value>> {
        let (mut genre, mut sort) = (genre.trim().to_string(), sort.trim().to_string());
        if sort.is_empty() {
            sort = "top".into();
        }
        if kind == "classics" {
            return Ok(classics(&genre, &sort)
                .into_iter()
                .skip(skip as usize)
                .collect());
        }
        if genre == "imdbRating" {
            sort = "imdbRating".into();
            genre.clear();
        }
        let key = format!(
            "catalog:{kind}:{sort}:{}:{skip}",
            if genre.is_empty() { "all" } else { &genre }
        );
        let value = self
            .net
            .cached(&key, 900.0, || async {
                let mut parts = Vec::new();
                if !genre.is_empty() {
                    parts.push(format!("genre={}", urlencode(&genre)));
                }
                if skip > 0 {
                    parts.push(format!("skip={skip}"));
                }
                let extra = if parts.is_empty() {
                    String::new()
                } else {
                    format!("/{}", parts.join("&"))
                };
                let data = self
                    .json(&format!("{}/catalog/{kind}/top{extra}.json", self.cinemeta))
                    .await?;
                let mut metas: Vec<Value> = data["metas"].as_array().cloned().unwrap_or_default();
                if kind == "series"
                    && !["anime", "animation"].contains(&genre.to_lowercase().as_str())
                {
                    metas.retain(|m| !is_anime_item(m));
                    if metas.len() < 35
                        && let Ok(next) = self
                            .json(&format!(
                                "{}/catalog/{kind}/top/skip={}.json",
                                self.cinemeta,
                                skip + 50
                            ))
                            .await
                    {
                        for m in next["metas"].as_array().into_iter().flatten() {
                            if !is_anime_item(m) && !metas.iter().any(|x| x["id"] == m["id"]) {
                                metas.push(m.clone());
                            }
                        }
                    }
                }
                self.index_later(metas.clone());
                sort_metas(&mut metas, &sort);
                Ok(Value::Array(metas))
            })
            .await?;
        Ok(value.as_array().cloned().unwrap_or_default())
    }

    pub async fn search(&self, query: &str, kind: &str) -> NetResult<Vec<Value>> {
        let key = format!("search:{kind}:{}", query.trim().to_lowercase());
        let value = self
            .net
            .cached(&key, 600.0, || async {
                if kind == "anime" {
                    let (series, movies) = tokio::join!(
                        Box::pin(self.search(query, "series")),
                        Box::pin(self.search(query, "movie"))
                    );
                    let mut seen = HashSet::new();
                    let combined: Vec<Value> = series
                        .unwrap_or_default()
                        .into_iter()
                        .chain(movies.unwrap_or_default())
                        .filter(|m| {
                            m["id"]
                                .as_str()
                                .is_some_and(|id| !id.is_empty() && seen.insert(id.to_string()))
                        })
                        .collect();
                    return Ok(Value::Array(combined));
                }
                let data = self
                    .json(&format!(
                        "{}/catalog/{kind}/top/search={}.json",
                        self.cinemeta,
                        urlencode(query)
                    ))
                    .await?;
                let metas = data["metas"].as_array().cloned().unwrap_or_default();
                self.index_later(metas.clone());
                Ok(Value::Array(metas))
            })
            .await?;
        Ok(value.as_array().cloned().unwrap_or_default())
    }

    /// A summary in French: kept as it is when it already reads as French.
    pub async fn translate_fr(&self, original: &str) -> String {
        let text = original.trim().to_string();
        if !self.translate || text.is_empty() || is_already_french(&text) {
            return text;
        }
        let digest = hex::encode(Md5::digest(text.as_bytes()));
        let key = format!("tr_fr_v2:{}", &digest[..16]);
        let found = self
            .net
            .cached(&key, 604_800.0, || async {
                let quick = Duration::from_millis(1500);
                let google = format!("https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=fr&dt=t&q={}", urlencode(&text));
                if let Ok(answer) = self.net.get_json(&google, "Mozilla/5.0", quick).await {
                    let out: String = answer[0].as_array().into_iter().flatten().filter_map(|seg| seg[0].as_str()).collect();
                    if !out.trim().is_empty() {
                        return Ok(json!(out.trim()));
                    }
                }
                let short: String = text.chars().take(480).collect();
                let memory = format!("https://api.mymemory.translated.net/get?langpair=en|fr&q={}", urlencode(&short));
                if let Ok(answer) = self.net.get_json(&memory, "Mozilla/5.0", quick).await {
                    let out = answer["responseData"]["translatedText"].as_str().unwrap_or("").trim().to_string();
                    if !out.is_empty() && !out.to_uppercase().contains("MYMEMORY WARNING") {
                        return Ok(json!(out));
                    }
                }
                Ok(Value::Null) // not kept: a later call tries again
            })
            .await
            .ok()
            .and_then(|v| v.as_str().map(str::to_string));
        found.unwrap_or(text)
    }

    /// A title's page: Cinemeta's, with French genres and summary, similar titles,
    /// and for a series its latest aired episode.
    pub async fn media(&self, imdb_id: &str, kind: &str) -> NetResult<Value> {
        let id = TABLES
            .imdb_aliases
            .get(imdb_id)
            .cloned()
            .unwrap_or_else(|| imdb_id.to_string());
        if kind == "anime" {
            if let Some(known) = catalog::media_type(self.net.db(), &id)
                && (known == "movie" || known == "series")
                && let Ok(mut found) = Box::pin(self.media(&id, &known)).await
                && found["name"].as_str().is_some_and(|n| !n.is_empty())
            {
                found["is_anime"] = json!(true);
                return Ok(found);
            }
            let (series, movie) = tokio::join!(
                Box::pin(self.media(&id, "series")),
                Box::pin(self.media(&id, "movie"))
            );
            let (series, movie) = (series.unwrap_or_default(), movie.unwrap_or_default());
            let named = |m: &Value| m["name"].as_str().is_some_and(|n| !n.is_empty());
            if named(&series) && series["videos"].as_array().is_some_and(|v| !v.is_empty()) {
                let mut s = series;
                s["type"] = json!("series");
                s["is_anime"] = json!(true);
                return Ok(s);
            }
            if named(&movie) {
                let mut m = movie;
                m["type"] = json!("movie");
                m["is_anime"] = json!(true);
                return Ok(m);
            }
            let truthy = |m: &Value| m.as_object().is_some_and(|o| !o.is_empty());
            return Ok(if truthy(&series) {
                series
            } else if truthy(&movie) {
                movie
            } else {
                json!({})
            });
        }
        let key = format!("meta_fr_v2:{kind}:{id}");
        self.net
            .cached(&key, 3600.0, || async { self.fetch_media(&id, kind).await })
            .await
    }

    async fn fetch_media(&self, id: &str, kind: &str) -> NetResult<Value> {
        let data = self
            .json(&format!("{}/meta/{kind}/{id}.json", self.cinemeta))
            .await?;
        let Some(mut meta) = data["meta"].as_object().cloned().filter(|m| !m.is_empty()) else {
            return Ok(json!({}));
        };
        if let Some(fixes) = TABLES.anime_image_fixes.get(id).and_then(Value::as_object) {
            for (k, v) in fixes {
                let current = text(meta.get(k).unwrap_or(&Value::Null));
                if !text(v).is_empty() && (current.is_empty() || current.contains("metahub.space"))
                {
                    meta.insert(k.clone(), v.clone());
                }
            }
        }
        // Cinemeta sometimes keeps HTML entities in names (« Catherine O&apos;Hara »).
        for key in ["name", "description", "awards"] {
            if let Some(Value::String(s)) = meta.get(key) {
                let decoded = html_escape::decode_html_entities(s).into_owned();
                meta.insert(key.into(), json!(decoded));
            }
        }
        for key in ["cast", "director", "writer"] {
            if let Some(Value::Array(list)) = meta.get(key) {
                let decoded: Vec<Value> = list
                    .iter()
                    .map(|v| json!(html_escape::decode_html_entities(&text_or_str(v)).into_owned()))
                    .collect();
                meta.insert(key.into(), Value::Array(decoded));
            }
        }
        let raw_genres = [meta.get("genres"), meta.get("genre")]
            .into_iter()
            .flatten()
            .find(|g| !text(g).is_empty() && g != &&json!([]))
            .cloned()
            .unwrap_or(json!([]));
        let genre_names: Vec<String> = raw_genres
            .as_array()
            .into_iter()
            .flatten()
            .map(text_or_str)
            .collect();
        if raw_genres.is_array() {
            let fr: Vec<Value> = genre_names
                .iter()
                .map(|g| json!(TABLES.genre_fr.get(g).cloned().unwrap_or_else(|| g.clone())))
                .collect();
            meta.insert("genres_fr".into(), Value::Array(fr));
        }
        let mut similar = catalog::similar(self.net.db(), id, kind, &genre_names, 8);
        if similar.len() < 4
            && let Some(first) = genre_names.first()
            && let Ok(pool) = Box::pin(self.catalog_top(kind, first, 0, "top")).await
        {
            let mut seen: HashSet<String> = similar
                .iter()
                .filter_map(|s| s["id"].as_str().map(str::to_string))
                .collect();
            seen.insert(id.to_string());
            for candidate in pool {
                let cid = text(&candidate["id"]);
                if !cid.is_empty() && !seen.contains(&cid) && !text(&candidate["poster"]).is_empty()
                {
                    seen.insert(cid.clone());
                    let kind_of = text(&candidate["type"]);
                    similar.push(json!({"id": cid, "name": candidate.get("name").cloned().unwrap_or(json!("")),
                        "type": if kind_of.is_empty() { kind.to_string() } else { kind_of }, "year": released(&candidate),
                        "poster": candidate.get("poster").cloned().unwrap_or(json!("")), "imdbRating": text(&candidate["imdbRating"])}));
                    if similar.len() >= 8 {
                        break;
                    }
                }
            }
        }
        similar.truncate(8);
        meta.insert("similar".into(), Value::Array(similar));
        if let Some(description) = meta.get("description").map(text).filter(|d| !d.is_empty()) {
            meta.insert(
                "description_fr".into(),
                json!(self.translate_fr(&description).await),
            );
        }
        let meta_value = Value::Object(meta.clone());
        self.index_later(vec![meta_value]);
        if kind == "series"
            && let Some(Value::Array(videos)) = meta.get("videos")
        {
            let today = today_utc();
            let mut aired: Vec<(i64, i64, String, Value)> = videos
                .iter()
                .filter_map(|v| {
                    let (s, e) = (
                        number(&v["season"]),
                        if number(&v["episode"]) != 0 {
                            number(&v["episode"])
                        } else {
                            number(&v["number"])
                        },
                    );
                    let rel: String = text(&v["released"]).chars().take(10).collect();
                    let title = [&v["name"], &v["title"]]
                        .into_iter()
                        .find(|t| !text(t).is_empty())
                        .cloned()
                        .unwrap_or_else(|| json!(format!("Episode {e}")));
                    (s >= 1 && e >= 1 && rel.len() == 10 && rel <= today)
                        .then_some((s, e, rel, title))
                })
                .collect();
            aired.sort_by_key(|a| (a.0, a.1));
            if let Some((s, e, rel, title)) = aired.pop() {
                meta.insert("latest_aired".into(), json!({"season": s, "episode": e, "code": format!("S{s:02}E{e:02}"), "released": rel, "title": title}));
            }
        }
        Ok(Value::Object(meta))
    }

    /// Episode numbers of a series: from the cached title page, else (if allowed) a light fetch.
    pub async fn episode_list(&self, imdb_id: &str, allow_network: bool) -> Option<Vec<Value>> {
        for key in [
            format!("meta_fr_v2:series:{imdb_id}"),
            format!("meta_fr:series:{imdb_id}"),
            format!("meta:series:{imdb_id}"),
        ] {
            if let Some(videos) = self
                .net
                .db()
                .cache_get(&key)
                .and_then(|m| m["videos"].as_array().cloned())
                .filter(|v| !v.is_empty())
            {
                return Some(videos);
            }
        }
        if !allow_network || !is_imdb_id(imdb_id) {
            return None;
        }
        let key = format!("series_episodes_v1:{imdb_id}");
        self.net
            .cached(&key, 86400.0, || async {
                let data = self.net.get_json(&format!("{}/meta/series/{imdb_id}.json", self.cinemeta), "KINO-Desktop", Duration::from_secs(4)).await?;
                let list: Vec<Value> = data["meta"]["videos"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|v| json!({"season": v["season"], "episode": if text(&v["episode"]).is_empty() { v["number"].clone() } else { v["episode"].clone() }}))
                    .collect();
                Ok(Value::Array(list))
            })
            .await
            .ok()
            .and_then(|v| v.as_array().cloned())
    }

    /// (next season, next episode, finished): finished only when the episode list is
    /// known and nothing follows the last episode watched.
    pub async fn next_episode(
        &self,
        imdb_id: &str,
        season: i64,
        episode: i64,
        allow_network: bool,
    ) -> (i64, i64, bool) {
        let (s, e) = (
            if season == 0 { 1 } else { season },
            if episode == 0 { 1 } else { episode },
        );
        if imdb_id.is_empty() {
            return (s, e + 1, false);
        }
        let Some(videos) = self
            .episode_list(imdb_id, allow_network)
            .await
            .filter(|v| !v.is_empty())
        else {
            return (s, e + 1, false);
        };
        let numbered: Vec<(i64, i64)> = videos
            .iter()
            .map(|v| {
                (
                    number(&v["season"]),
                    if number(&v["episode"]) != 0 {
                        number(&v["episode"])
                    } else {
                        number(&v["number"])
                    },
                )
            })
            .collect();
        if let Some(next) = numbered
            .iter()
            .filter(|(se, ep)| *se == s && *ep > e)
            .map(|(_, ep)| *ep)
            .min()
        {
            return (s, next, false);
        }
        if let Some(next) = numbered
            .iter()
            .filter(|(se, ep)| *se == s + 1 && *ep > 0)
            .map(|(_, ep)| *ep)
            .min()
        {
            return (s + 1, next, false);
        }
        if let Some(&(se, ep)) = numbered
            .iter()
            .filter(|(se, ep)| *se > s + 1 && *ep > 0)
            .min()
        {
            return (se, ep, false);
        }
        (s, e + 1, true)
    }
}

/// The IMDb number Cinemeta uses for a title KINO knows under another one.
pub fn canonical_id(id: &str) -> String {
    TABLES
        .imdb_aliases
        .get(id)
        .cloned()
        .unwrap_or_else(|| id.to_string())
}

impl Meta {
    /// Position of SxxEyy among all regular episodes (specials left out), as anime
    /// groups number them: S02E01 after a 24-episode season 1 is « 25 ».
    pub async fn absolute_episode(
        &self,
        imdb_id: &str,
        season: i64,
        episode: i64,
        allow_network: bool,
    ) -> Option<i64> {
        let videos = self
            .episode_list(imdb_id, allow_network)
            .await
            .unwrap_or_default();
        let mut numbered: Vec<(i64, i64)> = videos
            .iter()
            .map(|v| {
                (
                    number(&v["season"]),
                    if number(&v["episode"]) != 0 {
                        number(&v["episode"])
                    } else {
                        number(&v["number"])
                    },
                )
            })
            .filter(|(s, e)| *s > 0 && *e > 0)
            .collect();
        numbered.sort_unstable();
        numbered.dedup();
        numbered
            .iter()
            .position(|p| *p == (season, episode))
            .map(|i| i as i64 + 1)
    }
}

fn text_or_str(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_string)
        .unwrap_or_else(|| text(value))
}

pub fn is_imdb_id(value: &str) -> bool {
    value.len() > 2
        && value.len() <= 14
        && value.starts_with("tt")
        && value[2..].chars().all(|c| c.is_ascii_digit())
}

pub fn urlencode(text: &str) -> String {
    form_urlencoded::byte_serialize(text.as_bytes())
        .collect::<String>()
        .replace('+', "%20")
}

/// Quick check that a summary is already in French, to spare a translation.
pub fn is_already_french(text: &str) -> bool {
    if text.is_empty() {
        return true;
    }
    let sample: String = format!(" {} ", text.to_lowercase())
        .chars()
        .take(350)
        .collect();
    const WORDS: [&str; 20] = [
        " le ", " la ", " les ", " des ", " un ", " une ", " dans ", " pour ", " avec ", " et ",
        " qui ", " son ", " sa ", " ses ", " plus ", " mais ", " sur ", " par ", " est ", " une ",
    ];
    WORDS.iter().filter(|w| sample.contains(*w)).count() >= 3
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tables_and_helpers() {
        assert_eq!(classics("", "top").len(), 62);
        let drama = classics("drama", "imdbRating");
        assert!(
            drama.len() > 5
                && drama.iter().all(|c| c["genres"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|g| g == "Drama"))
        );
        assert!(is_anime_item(&json!({"id": "tt0388629"})));
        assert!(is_anime_item(&json!({"id": "x", "genres": ["Anime"]})));
        assert!(!is_anime_item(
            &json!({"id": "tt0111161", "genres": ["Drama"]})
        ));
        assert!(is_already_french(
            "Dans un monde où les humains vivent avec des Titans et une peur"
        ));
        assert!(!is_already_french(
            "An elf mage goes on a journey with her friends."
        ));
        assert_eq!(today_utc().len(), 10);
        assert!(is_imdb_id("tt0111161") && !is_imdb_id("tt") && !is_imdb_id("kitsu:1"));
        let mut metas = vec![
            json!({"imdbRating": "7.1"}),
            json!({"imdbRating": "8.5"}),
            json!({"imdbRating": "n/a"}),
        ];
        sort_metas(&mut metas, "imdbRating");
        assert_eq!(metas[0]["imdbRating"], "8.5");
    }
}
