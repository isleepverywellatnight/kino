//! The anime catalogue: a pool of anime series and films read from Cinemeta's
//! « Anime » genre (kept an hour), filtered by the page's categories, which
//! also read the title and summary since Cinemeta's genres are coarse.

use std::collections::{HashMap, HashSet};
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use futures_util::future::join_all;
use regex::Regex;
use serde_json::{Value, json};

use crate::db::now;
use crate::meta::{KNOWN_ANIME, Meta};
use crate::net::NetResult;

const AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)";

/// Hand-checked genres of well-known anime, added to Cinemeta's.
const CURATED: &[(&str, &[&str])] = &[
    ("tt1910272", &["Sci-Fi", "Thriller", "Drama"]),
    ("tt12590266", &["Sci-Fi", "Action"]),
    ("tt0112159", &["Sci-Fi", "Mecha", "Drama"]),
    ("tt0213338", &["Sci-Fi", "Action", "Adventure"]),
    ("tt9679542", &["Sci-Fi", "Adventure", "Comedy"]),
    ("tt13718450", &["Sci-Fi", "Drama", "Action"]),
    ("tt26737616", &["Sci-Fi", "Mystery", "Drama"]),
    ("tt0096633", &["Sci-Fi", "Drama", "Action"]),
    ("tt0816398", &["Sci-Fi", "Drama", "Romance"]),
    ("tt21975436", &["Sci-Fi", "Action", "Adventure"]),
    ("tt4508902", &["Action", "Comedy", "Sci-Fi"]),
    ("tt5249462", &["Sci-Fi", "Mystery", "Drama"]),
    ("tt2379308", &["Sci-Fi", "Crime", "Action"]),
    ("tt11126994", &["Action", "Sci-Fi", "Fantasy"]),
    ("tt2560140", &["Fantasy", "Action", "Drama"]),
    ("tt12343534", &["Fantasy", "Action"]),
    ("tt9335498", &["Fantasy", "Action"]),
    ("tt21209876", &["Fantasy", "Action"]),
    ("tt22248376", &["Fantasy", "Adventure", "Drama"]),
    ("tt1355642", &["Fantasy", "Adventure", "Action"]),
    ("tt2098220", &["Fantasy", "Adventure", "Action"]),
    ("tt0434665", &["Fantasy", "Action", "Adventure"]),
    ("tt13293588", &["Fantasy", "Adventure", "Drama", "Isekai"]),
    ("tt5607616", &["Fantasy", "Drama", "Thriller", "Isekai"]),
    ("tt9054364", &["Fantasy", "Comedy", "Adventure", "Isekai"]),
    ("tt7441658", &["Fantasy", "Action", "Adventure"]),
    ("tt13616990", &["Fantasy", "Action", "Horror"]),
    ("tt0877057", &["Fantasy", "Crime", "Drama"]),
    ("tt21621494", &["Fantasy", "Comedy", "Adventure"]),
    ("tt3909224", &["Fantasy", "Action", "Adventure"]),
    ("tt0318871", &["Fantasy", "Action", "Adventure"]),
    ("tt5897304", &["Comedy", "Action", "Fantasy"]),
    ("tt2359704", &["Action", "Adventure", "Fantasy"]),
    ("tt14976292", &["Fantasy", "Mystery", "Drama"]),
    ("tt14115938", &["Fantasy", "Comedy", "Action", "Isekai"]),
    ("tt8788458", &["Fantasy", "Mystery", "Thriller"]),
    ("tt11147852", &["Fantasy", "Action", "Comedy"]),
    ("tt0810705", &["Drama", "Romance", "Music", "Slice of Life"]),
    ("tt3895150", &["Drama", "Romance", "Music", "Slice of Life"]),
    ("tt7078180", &["Drama", "Fantasy", "Romance"]),
    ("tt7263328", &["Drama", "Mystery"]),
    ("tt0434706", &["Mystery", "Drama", "Crime"]),
    ("tt26743760", &["Drama", "Mystery", "History"]),
    ("tt0131179", &["Mystery", "Crime", "Comedy"]),
    ("tt13706018", &["Comedy", "Action"]),
    ("tt0988818", &["Comedy", "Action", "Sci-Fi"]),
    ("tt8086718", &["Comedy", "Adventure", "Slice of Life"]),
    ("tt28919914", &["Comedy", "Romance"]),
    ("tt0423731", &["Action", "Adventure", "Comedy"]),
];

/// (category, genres that say it, words of the title or summary that say it).
const CATEGORIES: &[(&[&str], &[&str], &[&str])] = &[
    (
        &["action"],
        &["action"],
        &[
            "combat",
            "bataille",
            "arts martiaux",
            "guerre",
            "fight",
            "samourai",
            "ninja",
        ],
    ),
    (
        &["adventure", "aventure"],
        &["adventure"],
        &[
            "aventure",
            "voyage",
            "quete",
            "exploration",
            "peril",
            "journey",
        ],
    ),
    (
        &["fantasy", "fantastique"],
        &["fantasy"],
        &[
            "demon",
            "demons",
            "magic",
            "magie",
            "curse",
            "malediction",
            "fleau",
            "sorcier",
            "witch",
            "dragon",
            "titan",
            "spirit",
            "esprit",
            "monster",
            "monstre",
            "isekai",
            "shinigami",
            "supernatural",
            "surnaturel",
            "alchimie",
            "vampire",
            "elfe",
            "elf",
        ],
    ),
    (
        &["sci-fi", "scifi", "science-fiction"],
        &["sci-fi"],
        &[
            "sci-fi",
            "scifi",
            "science-fiction",
            "cyberpunk",
            "mecha",
            "cyborg",
            "extraterrestre",
            "alien",
            "aliens",
            "time travel",
            "voyage dans le temps",
            "post-apocalyptic",
            "post-apocalyptique",
            "dystopie",
            "dystopian",
            "spaceship",
            "vaisseau spatial",
            "galaxy",
            "galaxie",
            "android",
        ],
    ),
    (
        &["comedy", "comedie"],
        &["comedy"],
        &[
            "humour",
            "parodie",
            "gag",
            "tranche de vie",
            "slice of life",
            "comique",
        ],
    ),
    (
        &["drama", "drame"],
        &["drama"],
        &["drame", "tragedie", "emotion", "deuil", "maladie"],
    ),
    (
        &["romance"],
        &["romance"],
        &["romance", "amour", "love", "sentiment", "lycee", "amoureux"],
    ),
    (
        &["mystery", "mystere", "thriller"],
        &["mystery", "thriller", "crime"],
        &["mystere", "enquete", "meurtre", "psychologique"],
    ),
    (
        &["isekai"],
        &["isekai"],
        &[
            "isekai",
            "réincarné",
            "réincarnée",
            "réincarnation",
            "reincarnated",
            "autre monde",
            "another world",
            "parallel world",
            "monde parallèle",
        ],
    ),
    (
        &["slice of life", "tranche de vie"],
        &["slice of life", "school life"],
        &[
            "tranche de vie",
            "slice of life",
            "vie quotidienne",
            "quotidien",
            "school life",
        ],
    ),
    (
        &["sport", "sports"],
        &["sport", "sports"],
        &[
            "sport", "volley", "basket", "football", "baseball", "tennis", "natation", "boxe",
            "cyclisme",
        ],
    ),
];

static WORDS: LazyLock<HashMap<&'static str, Regex>> = LazyLock::new(|| {
    CATEGORIES
        .iter()
        .flat_map(|(_, _, words)| words.iter())
        .filter(|w| w.chars().count() <= 4)
        .map(|w| {
            (
                *w,
                Regex::new(&format!(r"(?i)\b{}\b", regex::escape(w))).unwrap(),
            )
        })
        .collect()
});

/// (when it was read, the pool).
type Recent = (f64, Vec<Value>);

/// Pools read in the last five minutes, by kind.
static RECENT: LazyLock<Mutex<HashMap<&'static str, Recent>>> = LazyLock::new(Default::default);

/// Python's str() of a field, with its default when the field is missing.
fn field(m: &Value, key: &str, default: &str) -> String {
    match m.get(key) {
        None => default.into(),
        Some(Value::Null) => "None".into(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(b)) => if *b { "True" } else { "False" }.into(),
        Some(other) => other.to_string(),
    }
}

fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false))
        && v != &json!(0)
        && v != &json!("")
        && v != &json!([])
        && v != &json!({})
}

/// `str(a or b or default)` as Python writes it.
fn first_of(m: &Value, keys: &[&str], default: &str) -> String {
    keys.iter()
        .map(|k| &m[*k])
        .find(|v| truthy(v))
        .map(|v| field(&json!({"v": v}), "v", default))
        .unwrap_or_else(|| default.into())
}

impl Meta {
    async fn anime_page(&self, kind: &str, skip: u64) -> Vec<Value> {
        let extra = if skip > 0 {
            format!("genre=Anime&skip={skip}")
        } else {
            "genre=Anime".into()
        };
        let url = format!("{}/catalog/{kind}/top/{extra}.json", self.cinemeta);
        let Ok(data) = self
            .net
            .request_json(
                reqwest::Method::GET,
                &url,
                &[("User-Agent", AGENT)],
                None,
                Duration::from_secs(6),
                0,
            )
            .await
        else {
            return vec![];
        };
        let mut metas = data["metas"].as_array().cloned().unwrap_or_default();
        for m in metas.iter_mut().filter_map(Value::as_object_mut) {
            if !m.get("type").is_some_and(truthy) {
                m.insert("type".into(), json!(kind));
            }
            m.insert("is_anime".into(), json!(true));
            let id = m
                .get("id")
                .filter(|v| truthy(v))
                .map(|v| field(&json!({"v": v}), "v", ""));
            if let Some(id) = id {
                if !m.get("background").is_some_and(truthy) {
                    m.insert(
                        "background".into(),
                        json!(format!(
                            "https://images.metahub.space/background/medium/{id}/img"
                        )),
                    );
                }
                if !m.get("poster").is_some_and(truthy) {
                    m.insert(
                        "poster".into(),
                        json!(format!(
                            "https://images.metahub.space/poster/medium/{id}/img"
                        )),
                    );
                }
            }
        }
        metas
    }

    /// 400+ series or 200+ films of Cinemeta's « Anime » genre, kept an hour.
    pub async fn anime_pool(&self, kind: &str) -> Vec<Value> {
        let kind: &'static str = if kind == "movie" { "movie" } else { "series" };
        let at = now();
        if let Some((stamp, pool)) = RECENT.lock().unwrap_or_else(|e| e.into_inner()).get(kind)
            && !pool.is_empty()
            && at - stamp < 300.0
        {
            return pool.clone();
        }
        let key = format!("anime_engine:pool:{kind}:v3");
        if let Some(Value::Array(pool)) = self.net.db().cache_get(&key)
            && pool.len() > 20
        {
            RECENT
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .insert(kind, (at, pool.clone()));
            return pool;
        }
        let skips: &[u64] = if kind == "series" {
            &[0, 50, 100, 150, 200, 250, 300, 350, 400]
        } else {
            &[0, 50, 100, 150]
        };
        let pages = join_all(skips.iter().map(|s| self.anime_page(kind, *s))).await;
        let mut seen = HashSet::new();
        let mut pool: Vec<Value> = pages
            .into_iter()
            .flatten()
            .filter(|m| truthy(&m["id"]) && seen.insert(m["id"].to_string()))
            .collect();
        if pool.is_empty() && kind == "series" {
            pool = crate::meta::fallback_animes();
        }
        if !pool.is_empty() {
            let _ = self
                .net
                .db()
                .cache_set(&key, &Value::Array(pool.clone()), 3600.0);
            RECENT
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .insert(kind, (at, pool.clone()));
        }
        pool
    }

    /// One page (50) of the anime catalogue for a category, sorted as asked.
    pub async fn anime_catalog(&self, genre: &str, skip: u64, sort: &str) -> Vec<Value> {
        let norm = genre.trim().to_lowercase();
        let is_movie = ["films", "films d'animation", "movie", "film"].contains(&norm.as_str());
        let pool = self
            .anime_pool(if is_movie { "movie" } else { "series" })
            .await;
        let mut items: Vec<Value> = if is_movie
            || norm.is_empty()
            || ["tous", "tendances", "populaires", "all"].contains(&norm.as_str())
        {
            pool
        } else {
            pool.into_iter().filter(|m| in_category(m, &norm)).collect()
        };
        let year_text = |m: &Value, default: &str| {
            first_of(m, &["releaseInfo", "year"], default)
                .chars()
                .take(4)
                .collect::<String>()
        };
        match sort {
            "imdbRating" => {
                let rating = |m: &Value| {
                    let shown = first_of(m, &["imdbRating"], "");
                    let digits = shown.replacen('.', "", 1);
                    if !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()) {
                        shown.parse::<f64>().unwrap_or(0.0)
                    } else {
                        0.0
                    }
                };
                items.sort_by(|a, b| rating(b).total_cmp(&rating(a)));
            }
            "recent" => items.sort_by_key(|m| std::cmp::Reverse(year_text(m, "0"))),
            "oldest" => items.sort_by_key(|m| {
                let year = year_text(m, "");
                if !year.is_empty() && year.chars().all(|c| c.is_ascii_digit()) {
                    year.parse::<i64>().unwrap_or(9999)
                } else {
                    9999
                }
            }),
            _ => {}
        }
        items.into_iter().skip(skip as usize).take(50).collect()
    }

    /// GET /api/catalog?type=anime, cached ten minutes; the titles join the local catalogue.
    pub async fn anime_catalog_page(
        &self,
        genre: &str,
        skip: u64,
        sort: &str,
    ) -> NetResult<Vec<Value>> {
        let (genre, sort) = (
            genre.trim(),
            if sort.trim().is_empty() {
                "top"
            } else {
                sort.trim()
            },
        );
        let key = format!(
            "catalog:anime_v4:{sort}:{}:{skip}",
            if genre.is_empty() { "all" } else { genre }
        );
        let found = self
            .net
            .cached(&key, 600.0, || async {
                let metas = self.anime_catalog(genre, skip, sort).await;
                if let Ok(mut known) = KNOWN_ANIME.write() {
                    known.extend(
                        metas
                            .iter()
                            .filter(|m| truthy(&m["id"]))
                            .map(|m| field(m, "id", "")),
                    );
                }
                self.index_later(metas.clone());
                Ok(Value::Array(metas))
            })
            .await?;
        Ok(found.as_array().cloned().unwrap_or_default())
    }
}

/// Whether a title belongs to a category: its genres (Cinemeta's and the
/// hand-checked ones), else words of its title or summary.
fn in_category(m: &Value, norm: &str) -> bool {
    let mut genres: HashSet<String> = m["genres"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_lowercase)
        .collect();
    let id = m["id"].as_str().unwrap_or("");
    if let Some((_, extra)) = CURATED.iter().find(|(i, _)| *i == id) {
        genres.extend(extra.iter().map(|g| g.to_lowercase()));
    }
    if genres.contains("sci-fi & fantasy") {
        genres.extend(["sci-fi".to_string(), "fantasy".to_string()]);
    }
    if genres.contains("action & adventure") {
        genres.extend(["action".to_string(), "adventure".to_string()]);
    }
    let text = format!("{} {}", field(m, "name", ""), field(m, "description", "")).to_lowercase();
    let has = |word: &str| match WORDS.get(word) {
        Some(pattern) => pattern.is_match(&text),
        None => text.contains(&word.to_lowercase()),
    };
    match CATEGORIES
        .iter()
        .find(|(names, _, _)| names.contains(&norm))
    {
        Some((_, tags, words)) => {
            tags.iter().any(|t| genres.contains(*t)) || words.iter().any(|w| has(w))
        }
        None => genres.contains(norm),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn categories_read_genres_then_words() {
        let m = json!({"id": "tt1", "name": "Blade Gate", "description": "A ninja story", "genres": ["Animation"]});
        assert!(in_category(&m, "action"), "« ninja » as a whole word");
        let m = json!({"id": "tt1", "name": "Gags", "description": "", "genres": []});
        assert!(
            !in_category(&m, "comedie"),
            "short words only as whole words"
        );
        let m =
            json!({"id": "tt1", "name": "X", "description": "a reincarnated hero", "genres": []});
        assert!(in_category(&m, "isekai"));
        assert!(
            in_category(&json!({"id": "tt5607616", "name": "Re:Zero"}), "isekai"),
            "hand-checked genres"
        );
        assert!(in_category(
            &json!({"id": "tt9", "genres": ["Sci-Fi & Fantasy"]}),
            "fantastique"
        ));
        assert!(
            in_category(&json!({"id": "tt9", "genres": ["Music"]}), "music"),
            "other names: the genre itself"
        );
    }
}
