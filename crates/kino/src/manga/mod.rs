//! Manga: the catalogue (AniList), the chapters of a series gathered from
//! several sites, the user's own files, and where the user stopped reading.
//!
//! For each chapter number KINO keeps one copy: in the reading language when a
//! site has it, else in English.

pub mod local;
pub mod sources;

use std::collections::HashMap;
use std::sync::LazyLock;
use std::time::Duration;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::db::Db;
use crate::net::{KINO_AGENT, Net, NetError, NetResult};
use sources::Sites;

pub const LANGUAGES: [&str; 10] = [
    "fr", "en", "es", "es-la", "de", "it", "pt-br", "ru", "ja", "zh",
];
const CONTENT: [&str; 2] = ["safe", "suggestive"];
const PROGRESS_KEY: &str = "manga_progress_v1";
const PROGRESS_KEEP: usize = 200;
const FIELDS: &str = "id title { romaji english native } synonyms coverImage { extraLarge large } startDate { year } \
                      status chapters volumes countryOfOrigin format genres";

static UUID: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$").unwrap()
});
static ANILIST_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{1,9}$").unwrap());
static TAGS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>]+>|\(Source:[^)]*\)").unwrap());
static AUTHOR_ROLE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)story|art").unwrap());
static LOCAL_KEY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[0-9a-f]{20}$").unwrap());

/// What goes wrong with a manga request: refused (400, its message shown as
/// is), a site down (502), or P2P not agreed to yet (409).
#[derive(Debug)]
pub enum MangaError {
    Refused(String),
    Down(String),
    Consent,
}

impl MangaError {
    pub fn refused(text: &str) -> Self {
        MangaError::Refused(text.to_string())
    }
    pub fn down(text: &str) -> Self {
        MangaError::Down(text.to_string())
    }
}

impl From<NetError> for MangaError {
    fn from(error: NetError) -> Self {
        MangaError::Down(error.0)
    }
}

pub type MangaResult<T> = Result<T, MangaError>;

pub struct Manga<'a> {
    pub net: &'a Net,
    pub sites: &'a Sites,
}

pub fn lang(value: &str) -> &str {
    if LANGUAGES.contains(&value) {
        value
    } else {
        "en"
    }
}

fn query_string(params: &[(&str, String)]) -> String {
    let mut serializer = form_urlencoded::Serializer::new(String::new());
    for (k, v) in params {
        serializer.append_pair(k, v);
    }
    serializer.finish()
}

fn titles_of(media: &Value) -> Vec<String> {
    let title = &media["title"];
    let latin = media["synonyms"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|t| !t.is_empty() && t.is_ascii());
    [title["english"].as_str(), title["romaji"].as_str()]
        .into_iter()
        .flatten()
        .chain(latin)
        .filter(|t| !t.is_empty())
        .map(str::to_string)
        .collect()
}

fn series_of(media: &Value) -> Value {
    let title = &media["title"];
    let name = title["english"]
        .as_str()
        .filter(|t| !t.is_empty())
        .or(title["romaji"].as_str())
        .unwrap_or("Sans titre");
    let cover = media["coverImage"]["extraLarge"]
        .as_str()
        .or(media["coverImage"]["large"].as_str())
        .unwrap_or("");
    let year = media["startDate"]["year"]
        .as_i64()
        .map_or(json!(""), |y| json!(y));
    json!({
        "id": media["id"].as_i64().map(|i| i.to_string()).unwrap_or_default(), "source": "anilist", "title": name,
        "cover": cover, "year": year, "status": media["status"].as_str().unwrap_or("").to_lowercase(),
        "kind": media["countryOfOrigin"].as_str().unwrap_or(""),
    })
}

impl Manga<'_> {
    async fn mangadex(&self, path: &str, params: &[(&str, String)], ttl: f64) -> NetResult<Value> {
        let query = query_string(params);
        let url = if query.is_empty() {
            format!("{}{path}", self.sites.mangadex)
        } else {
            format!("{}{path}?{query}", self.sites.mangadex)
        };
        let key = format!("mangadex:{url}");
        self.net
            .cached(&key, ttl, || async {
                self.net
                    .get_json(&url, KINO_AGENT, Duration::from_secs(12))
                    .await
            })
            .await
    }

    async fn anilist(&self, query: &str, variables: Value, ttl: f64) -> NetResult<Value> {
        let key = format!("rs:anilist-manga:{query}:{variables}");
        let body = json!({"query": query, "variables": variables});
        let data = self
            .net
            .cached(&key, ttl, || async {
                self.net
                    .post_json(
                        &self.sites.anilist,
                        &body,
                        KINO_AGENT,
                        Duration::from_secs(12),
                    )
                    .await
            })
            .await?;
        Ok(data["data"].clone())
    }

    // ---------- The catalogue ----------

    pub async fn search(&self, query: &str) -> MangaResult<Vec<Value>> {
        let query: String = query.trim().chars().take(100).collect();
        if query.is_empty() {
            return Ok(vec![]);
        }
        let gql = format!(
            "query ($q: String) {{ Page(perPage: 30) {{ media(search: $q, type: MANGA, isAdult: false, sort: [SEARCH_MATCH, POPULARITY_DESC]) {{ {FIELDS} }} }} }}"
        );
        let data = self.anilist(&gql, json!({"q": query}), 600.0).await?;
        Ok(data["Page"]["media"]
            .as_array()
            .into_iter()
            .flatten()
            .map(series_of)
            .collect())
    }

    /// One row of the tab: « trending », « popular » or « webtoon ».
    pub async fn row(&self, name: &str) -> MangaResult<Vec<Value>> {
        let filter = match name {
            // « Tendances »: what is being read now, among the series still coming out.
            "trending" => "sort: TRENDING_DESC, status: RELEASING",
            "popular" => "sort: POPULARITY_DESC",
            "webtoon" => "sort: TRENDING_DESC, countryOfOrigin: KR",
            _ => return Err(MangaError::refused("Rangée inconnue.")),
        };
        let gql = format!(
            "{{ Page(perPage: 30) {{ media(type: MANGA, isAdult: false, {filter}) {{ {FIELDS} }} }} }}"
        );
        let data = self.anilist(&gql, json!({}), 6.0 * 3600.0).await?;
        Ok(data["Page"]["media"]
            .as_array()
            .into_iter()
            .flatten()
            .map(series_of)
            .collect())
    }

    async fn media(&self, anilist_id: i64) -> MangaResult<Value> {
        let gql = format!(
            "query ($id: Int) {{ Media(id: $id, type: MANGA) {{ {FIELDS} isAdult description(asHtml: false) \
             staff(perPage: 6) {{ edges {{ role node {{ name {{ full }} }} }} }} }} }}"
        );
        let media = self
            .anilist(&gql, json!({"id": anilist_id}), 6.0 * 3600.0)
            .await?["Media"]
            .clone();
        if !media.is_object() || media["isAdult"].as_bool() == Some(true) {
            return Err(MangaError::refused(
                "Ce manga n'est pas disponible dans KINO.",
            ));
        }
        Ok(media)
    }

    /// A series opened before the catalogue moved to AniList: its AniList number.
    async fn anilist_of_mangadex(&self, manga_id: &str) -> MangaResult<i64> {
        let manga = self
            .mangadex(&format!("/manga/{manga_id}"), &[], 86400.0)
            .await?;
        manga["data"]["attributes"]["links"]["al"]
            .as_str()
            .and_then(|n| n.parse().ok())
            .ok_or_else(|| MangaError::refused("Ce manga n'est plus dans le catalogue de KINO."))
    }

    async fn anilist_number(&self, series_id: &str) -> MangaResult<i64> {
        if UUID.is_match(series_id) {
            return self.anilist_of_mangadex(series_id).await;
        }
        if !ANILIST_ID.is_match(series_id) {
            return Err(MangaError::refused("Manga inconnu."));
        }
        series_id
            .parse()
            .map_err(|_| MangaError::refused("Manga inconnu."))
    }

    async fn mangadex_series(&self, anilist_id: i64, titles: &[String]) -> Option<Value> {
        let mut seen = std::collections::HashSet::new();
        for title in titles
            .iter()
            .filter(|t| !t.is_empty() && seen.insert(t.as_str()))
            .take(3)
        {
            let params = [
                ("title", title.clone()),
                ("limit", "10".into()),
                ("contentRating[]", CONTENT[0].into()),
                ("contentRating[]", CONTENT[1].into()),
            ];
            let data = self.mangadex("/manga", &params, 86400.0).await.ok()?;
            if let Some(found) =
                data["data"].as_array().into_iter().flatten().find(|m| {
                    m["attributes"]["links"]["al"].as_str() == Some(&anilist_id.to_string())
                })
            {
                return Some(found.clone());
            }
        }
        None
    }

    pub async fn details(&self, series_id: &str, reading: &str) -> MangaResult<Value> {
        let anilist_id = self.anilist_number(series_id).await?;
        let media = self.media(anilist_id).await?;
        let reading = lang(reading);
        let mut authors: Vec<String> = Vec::new();
        for edge in media["staff"]["edges"].as_array().into_iter().flatten() {
            if AUTHOR_ROLE.is_match(edge["role"].as_str().unwrap_or(""))
                && let Some(name) = edge["node"]["name"]["full"].as_str()
                && !name.is_empty()
                && !authors.iter().any(|a| a == name)
            {
                authors.push(name.to_string());
            }
        }
        authors.truncate(3);
        let titles = titles_of(&media);
        let mut description = TAGS
            .replace_all(media["description"].as_str().unwrap_or(""), "")
            .trim()
            .to_string();
        if reading != "en"
            && let Some(found) = self.mangadex_series(anilist_id, &titles).await
            && let Some(text) = found["attributes"]["description"][reading]
                .as_str()
                .filter(|t| !t.is_empty())
        {
            description = text.to_string();
        }
        let mut info = series_of(&media);
        let extra = json!({
            "description": description, "authors": authors,
            "tags": media["genres"].as_array().map(|g| g.iter().take(6).cloned().collect::<Vec<_>>()).unwrap_or_default(),
            "chapters_total": media["chapters"].as_i64().unwrap_or(0), "volumes_total": media["volumes"].as_i64().unwrap_or(0),
            "titles": titles,
        });
        if let (Some(target), Some(more)) = (info.as_object_mut(), extra.as_object()) {
            target.extend(more.clone());
        }
        Ok(info)
    }

    // ---------- The chapters, from every site ----------

    async fn mangadex_chapters(&self, manga_id: &str, reading: &str) -> NetResult<Vec<Value>> {
        let mut found = Vec::new();
        let mut seen = std::collections::HashSet::new();
        let mut offset = 0;
        while offset < 3000 {
            let params = [
                ("translatedLanguage[]", reading.to_string()),
                ("limit", "500".into()),
                ("offset", offset.to_string()),
                ("order[volume]", "asc".into()),
                ("order[chapter]", "asc".into()),
                ("includes[]", "scanlation_group".into()),
                ("contentRating[]", CONTENT[0].into()),
                ("contentRating[]", CONTENT[1].into()),
            ];
            let page = self
                .mangadex(&format!("/manga/{manga_id}/feed"), &params, 1800.0)
                .await?;
            for chapter in page["data"].as_array().into_iter().flatten() {
                let a = &chapter["attributes"];
                let number = a["chapter"].as_str().unwrap_or("").to_string();
                if !number.is_empty() && !seen.insert(number.clone()) {
                    continue;
                }
                let group = chapter["relationships"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter(|r| r["type"] == "scanlation_group")
                    .find_map(|r| r["attributes"]["name"].as_str().filter(|n| !n.is_empty()))
                    .unwrap_or("");
                found.push(json!({
                    "id": format!("md:{}", chapter["id"].as_str().unwrap_or("")), "number": number,
                    "volume": a["volume"].as_str().unwrap_or(""), "title": a["title"].as_str().unwrap_or(""),
                    "pages": a["pages"].as_i64().unwrap_or(0), "lang": reading, "group": group, "source": "MangaDex",
                    // Hosted by its publisher (MangaPlus…): it opens there, not in KINO.
                    "external": a["externalUrl"].as_str().unwrap_or(""),
                }));
            }
            offset += 500;
            if offset as i64 >= page["total"].as_i64().unwrap_or(0) {
                break;
            }
        }
        Ok(found)
    }

    async fn weebcentral_chapters(&self, titles: &[String]) -> NetResult<Vec<Value>> {
        let key = format!("rs:wc:{:?}", &titles[..titles.len().min(3)]);
        let list = self
            .net
            .cached(&key, 1800.0, || async {
                let Some(id) = sources::weebcentral_match(self.net, self.sites, titles).await?
                else {
                    return Ok(json!([]));
                };
                Ok(Value::Array(
                    sources::weebcentral_chapters(self.net, self.sites, &id).await?,
                ))
            })
            .await?;
        Ok(list
            .as_array()
            .into_iter()
            .flatten()
            .map(|c| {
                let mut c = c.clone();
                if let Some(map) = c.as_object_mut() {
                    for (k, v) in [
                        ("title", json!("")),
                        ("volume", json!("")),
                        ("pages", json!(0)),
                        ("lang", json!("en")),
                        ("group", json!("")),
                        ("source", json!("WeebCentral")),
                        ("external", json!("")),
                    ] {
                        map.insert(k.into(), v);
                    }
                }
                c
            })
            .collect())
    }

    /// Every chapter once, in reading order: in the reading language when a site has it, else in English.
    pub async fn chapters(&self, series_id: &str, reading: &str) -> MangaResult<Vec<Value>> {
        let reading = lang(reading);
        let anilist_id = self.anilist_number(series_id).await?;
        let titles = titles_of(&self.media(anilist_id).await?);
        let md = self.mangadex_series(anilist_id, &titles).await;
        let md_id = md
            .as_ref()
            .and_then(|m| m["id"].as_str())
            .unwrap_or("")
            .to_string();
        let (wc, md_en, md_lang) = tokio::join!(
            self.weebcentral_chapters(&titles),
            async {
                if md_id.is_empty() {
                    Ok(vec![])
                } else {
                    self.mangadex_chapters(&md_id, "en").await
                }
            },
            async {
                if md_id.is_empty() || reading == "en" {
                    Ok(vec![])
                } else {
                    self.mangadex_chapters(&md_id, reading).await
                }
            },
        );
        let failed = wc.is_err() || md_en.is_err() || md_lang.is_err();
        let lists: HashMap<&str, Vec<Value>> = HashMap::from([
            ("wc", wc.unwrap_or_default()),
            ("md-en", md_en.unwrap_or_default()),
            ("md-lang", md_lang.unwrap_or_default()),
        ]);
        if failed && lists.values().all(Vec::is_empty) {
            return Err(MangaError::down(
                "Les sources de chapitres ne répondent pas.",
            ));
        }
        let order: &[&str] = if reading != "en" {
            &["md-lang", "wc", "md-en"]
        } else {
            &["wc", "md-en"]
        };
        let mut chosen: Map<String, Value> = Map::new();
        for name in order {
            for chapter in &lists[name] {
                let number = chapter["number"].as_str().unwrap_or("");
                let key = sources::number_key(number)
                    .unwrap_or_else(|| format!("x:{}", chapter["id"].as_str().unwrap_or("")));
                let external = |c: &Value| !c["external"].as_str().unwrap_or("").is_empty();
                // A chapter hosted elsewhere gives way to one KINO can show.
                let replace = match chosen.get(&key) {
                    None => true,
                    Some(current) => external(current) && !external(chapter),
                };
                if replace {
                    chosen.insert(key, chapter.clone());
                }
            }
        }
        let mut list: Vec<Value> = chosen.into_iter().map(|(_, v)| v).collect();
        let position = |c: &Value| {
            sources::number_key(c["number"].as_str().unwrap_or(""))
                .and_then(|n| n.parse::<f64>().ok())
                .unwrap_or(1e9)
        };
        list.sort_by(|a, b| {
            position(a).total_cmp(&position(b)).then_with(|| {
                a["id"]
                    .as_str()
                    .unwrap_or("")
                    .cmp(b["id"].as_str().unwrap_or(""))
            })
        });
        Ok(list)
    }

    /// The image addresses of a chapter, from the site it comes from.
    pub async fn pages(&self, chapter_id: &str) -> MangaResult<Vec<String>> {
        if let Some(id) = chapter_id.strip_prefix("wc:") {
            let found = sources::weebcentral_pages(self.net, self.sites, id)
                .await
                .map_err(|e| {
                    if e.0.contains("inconnu") {
                        MangaError::Refused(e.0)
                    } else {
                        MangaError::Down(e.0)
                    }
                })?;
            if found.is_empty() {
                return Err(MangaError::refused("Ce chapitre ne se lit pas dans KINO."));
            }
            return Ok(found);
        }
        let id = chapter_id.strip_prefix("md:").unwrap_or(chapter_id);
        if !UUID.is_match(id) {
            return Err(MangaError::refused("Chapitre inconnu."));
        }
        let data = self
            .mangadex(&format!("/at-home/server/{id}"), &[], 300.0)
            .await?;
        let base = data["baseUrl"].as_str().unwrap_or("");
        let digest = data["chapter"]["hash"].as_str().unwrap_or("");
        if base.is_empty() || digest.is_empty() || !base.starts_with("https://") {
            return Err(MangaError::refused("Ce chapitre ne se lit pas dans KINO."));
        }
        Ok(data["chapter"]["data"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(|n| format!("{base}/data/{digest}/{n}"))
            .collect())
    }

    /// The titles of a series, for the torrent search.
    pub async fn titles(&self, series_id: &str) -> MangaResult<Vec<String>> {
        let anilist_id = self.anilist_number(series_id).await?;
        Ok(titles_of(&self.media(anilist_id).await?))
    }
}

// ---------- Where the user stopped ----------

pub fn progress(db: &Db) -> Vec<Value> {
    let mut items: Vec<(usize, Value)> = db
        .get_config(PROGRESS_KEY)
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
        .into_values()
        .enumerate()
        .collect();
    // Most recent first; within one millisecond, the one written last.
    items.sort_by(|(i, a), (j, b)| {
        let updated = |v: &Value| v["updated"].as_f64().unwrap_or(0.0);
        updated(b).total_cmp(&updated(a)).then(j.cmp(i))
    });
    items.into_iter().map(|(_, v)| v).collect()
}

fn text(value: &Value, limit: usize) -> String {
    let raw = match value {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    };
    raw.chars().take(limit).collect()
}

fn whole(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0)
        .max(0)
}

pub fn save_progress(db: &Db, entry: &Value) -> MangaResult<Value> {
    let source = entry["source"].as_str().unwrap_or("");
    let series = text(&entry["series"], 200);
    let series = match source {
        "mangadex" if UUID.is_match(&series) => series,
        "anilist" if ANILIST_ID.is_match(&series) => series
            .parse::<i64>()
            .map(|n| n.to_string())
            .unwrap_or(series),
        "local" if LOCAL_KEY.is_match(&series) => series,
        "mangadex" | "anilist" => return Err(MangaError::refused("Manga inconnu.")),
        "local" => return Err(MangaError::refused("Série inconnue.")),
        _ => return Err(MangaError::refused("Source inconnue.")),
    };
    let record = json!({
        "source": source, "series": series, "title": text(&entry["title"], 200), "cover": text(&entry["cover"], 500),
        "chapter": text(&entry["chapter"], 60), "number": text(&entry["number"], 20), "lang": lang(entry["lang"].as_str().unwrap_or("")),
        "page": whole(&entry["page"]), "pages": whole(&entry["pages"]), "updated": (crate::db::now() * 1000.0).round() / 1000.0,
    });
    let mut stored = db
        .get_config(PROGRESS_KEY)
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default();
    stored.insert(format!("{source}:{series}"), record.clone());
    if stored.len() > PROGRESS_KEEP {
        let mut by_age: Vec<(String, f64)> = stored
            .iter()
            .map(|(k, v)| (k.clone(), v["updated"].as_f64().unwrap_or(0.0)))
            .collect();
        by_age.sort_by(|a, b| a.1.total_cmp(&b.1));
        for (key, _) in by_age.into_iter().take(stored.len() - PROGRESS_KEEP) {
            stored.remove(&key);
        }
    }
    db.save_config(PROGRESS_KEY, &Value::Object(stored))
        .map_err(|e| MangaError::down(&e.to_string()))?;
    Ok(record)
}

pub fn forget(db: &Db, source: &str, series: &str) {
    if let Some(mut stored) = db
        .get_config(PROGRESS_KEY)
        .and_then(|v| v.as_object().cloned())
        && stored.remove(&format!("{source}:{series}")).is_some()
    {
        let _ = db.save_config(PROGRESS_KEY, &Value::Object(stored));
    }
}
