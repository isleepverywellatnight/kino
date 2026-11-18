//! KINO's own suggestions: « Pour vous » and « Vous aimerez aussi », computed
//! on this computer from what the user watched, rated and set aside.
//!
//! The pool holds a few thousand public film records (genres, people, country,
//! decade, the words of their summary). Each title becomes a weighted vector
//! of those traits, rare traits weighing more; the taste profile adds up the
//! titles the user told something about. A suggestion is close to the profile
//! (or to one title), not seen, varied, and says why.
//!
//! Maps keep their insertion order, as Python's dicts: the sums and the ties
//! of the choice depend on it.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, LazyLock, Mutex};

use indexmap::IndexMap;
use regex::Regex;
use rusqlite::{Connection, params};
use serde_json::{Value, json};
use unicode_normalization::UnicodeNormalization;

use crate::db::Db;
use crate::sources::stream::py_round;

pub const POOL_READY: usize = 500;
const DESCRIPTION_WORDS: usize = 25;
const PER_PERSON: i64 = 3;
const CAST_KEPT: usize = 5;
const QUALITY_WEIGHT: f64 = 0.12;
const DIVERSITY: f64 = 0.25;
static IMDB: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^tt\d{1,12}$").unwrap());
static WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[a-z]{4,}").unwrap());
static STOPWORDS: LazyLock<HashSet<&'static str>> = LazyLock::new(|| {
    "about above after again against all also among another around away back because been before being below between \
     both came come could does doing down during each even ever every find finds first from further gets give goes \
     going have having into just last life like lives made make makes many more most much must must never new next \
     only other over own same seen should since some still such take takes than that their them then there these they \
     thing this those though through time together toward under until upon very want wants well were what when where \
     which while whose with without world would year years young your story film movie series season help helps begins \
     becomes find found discovers soon must must left right town city day days night home family friend friends"
        .split_whitespace()
        .collect()
});

type Vector = IndexMap<String, f64>;

fn text(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
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

/// float(value or 0); None for what Python refuses.
fn float(value: &Value) -> Option<f64> {
    match value {
        v if !truthy(v) => Some(0.0),
        Value::Bool(b) => Some(*b as i64 as f64),
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

fn is_digits(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_digit())
}

fn fold(text: &str) -> String {
    text.nfkd()
        .filter(char::is_ascii)
        .collect::<String>()
        .to_lowercase()
}

fn names(value: &Value, limit: Option<usize>) -> Vec<String> {
    let list: Vec<String> = match value {
        Value::String(s) => s.split(',').map(|p| p.trim().to_string()).collect(),
        Value::Array(a) => a.iter().map(text).collect(),
        v if !truthy(v) => vec![],
        _ => vec![],
    };
    let found: Vec<String> = list
        .into_iter()
        .map(|n| n.trim().to_string())
        .filter(|n| !n.is_empty())
        .collect();
    match limit {
        Some(l) => found.into_iter().take(l).collect(),
        None => found,
    }
}

/// The telling words of a summary (no small or common words), each once.
pub fn description_words(summary: &str) -> Vec<String> {
    let mut words: Vec<String> = Vec::new();
    for word in WORD.find_iter(&fold(summary)).map(|m| m.as_str()) {
        if STOPWORDS.contains(word) {
            continue;
        }
        let stem = if word.ends_with('s') && !word.ends_with("ss") {
            &word[..word.len() - 1]
        } else {
            word
        };
        if !words.iter().any(|w| w == stem) {
            words.push(stem.to_string());
        }
        if words.len() >= DESCRIPTION_WORDS {
            break;
        }
    }
    words
}

/// What the suggestions need of a public record, or None.
pub fn compact(meta: &Value, kind: &str) -> Option<Value> {
    let id = [&meta["imdb_id"], &meta["id"]]
        .into_iter()
        .find(|v| truthy(v))
        .map(text)
        .unwrap_or_default();
    if !IMDB.is_match(&id) || !truthy(&meta["name"]) {
        return None;
    }
    let year_raw = [&meta["year"], &meta["releaseInfo"]]
        .into_iter()
        .find(|v| truthy(v))
        .map(text)
        .unwrap_or_default();
    let year: String = year_raw.chars().take(4).collect();
    let genres = if truthy(&meta["genres"]) {
        &meta["genres"]
    } else {
        &meta["genre"]
    };
    let poster = if truthy(&meta["poster"]) {
        text(&meta["poster"])
    } else {
        String::new()
    };
    let summary = if truthy(&meta["description"]) {
        text(&meta["description"])
    } else {
        String::new()
    };
    Some(json!({
        "id": id, "type": if kind == "series" { "series" } else { "movie" }, "name": text(&meta["name"]).chars().take(200).collect::<String>(),
        "year": if is_digits(&year) { year } else { String::new() }, "poster": poster.chars().take(300).collect::<String>(),
        "rating": float(&meta["imdbRating"]).unwrap_or(0.0), "popularity": float(&meta["popularity"]).unwrap_or(0.0),
        "genres": names(genres, None).into_iter().take(6).collect::<Vec<_>>(),
        "director": names(&meta["director"], Some(3)), "writer": names(&meta["writer"], Some(3)),
        "cast": names(&meta["cast"], Some(CAST_KEPT)), "country": names(&meta["country"], Some(2)),
        "words": description_words(&summary),
    }))
}

fn strings(value: &Value) -> Vec<String> {
    value.as_array().into_iter().flatten().map(text).collect()
}

/// Every trait of a title, as « group:value ».
fn traits(entry: &Value) -> Vec<String> {
    let mut out: Vec<String> = strings(&entry["genres"])
        .iter()
        .map(|g| format!("g:{}", g.to_lowercase()))
        .collect();
    out.extend(
        strings(&entry["director"])
            .iter()
            .map(|n| format!("d:{}", fold(n))),
    );
    out.extend(
        strings(&entry["writer"])
            .iter()
            .map(|n| format!("w:{}", fold(n))),
    );
    out.extend(
        strings(&entry["cast"])
            .iter()
            .take(CAST_KEPT)
            .map(|n| format!("c:{}", fold(n))),
    );
    out.extend(
        strings(&entry["country"])
            .iter()
            .map(|n| format!("co:{}", fold(n))),
    );
    out.extend(strings(&entry["words"]).iter().map(|w| format!("k:{w}")));
    let year = if truthy(&entry["year"]) {
        text(&entry["year"])
    } else {
        String::new()
    };
    if is_digits(&year)
        && let Ok(y) = year.parse::<i64>()
    {
        out.push(format!("dec:{}", y / 10 * 10));
    }
    let mut unique: Vec<String> = Vec::with_capacity(out.len());
    let mut seen = HashSet::new();
    for t in out {
        if seen.insert(t.clone()) {
            unique.push(t);
        }
    }
    unique
}

fn group_weight(group: &str) -> f64 {
    match group {
        "d" => 2.2,
        "w" => 1.6,
        "c" => 1.0,
        "g" => 1.6,
        "k" => 1.4,
        "dec" => 0.9,
        "co" => 0.8,
        _ => 1.0,
    }
}

fn normalized(mut vec: Vector) -> Vector {
    let norm = vec.values().map(|v| v * v).sum::<f64>().sqrt();
    let norm = if norm == 0.0 { 1.0 } else { norm };
    for v in vec.values_mut() {
        *v /= norm;
    }
    vec
}

/// The pool's traits with their weight: a trait shared by few titles weighs more.
pub struct Space {
    pub entries: IndexMap<String, Value>,
    idf: HashMap<String, f64>,
    default_idf: f64,
    vectors: Mutex<HashMap<String, Arc<Vector>>>,
}

impl Space {
    pub fn new(list: Vec<Value>) -> Self {
        let mut counts: HashMap<String, f64> = HashMap::new();
        for entry in &list {
            for t in traits(entry) {
                *counts.entry(t).or_default() += 1.0;
            }
        }
        let total = list.len().max(1) as f64;
        let idf = counts
            .into_iter()
            .map(|(t, n)| (t, ((total + 1.0) / (n + 1.0)).ln() + 1.0))
            .collect();
        let mut entries = IndexMap::new();
        for entry in list {
            let id = text(&entry["id"]);
            // A later duplicate replaces the value but keeps the first place, as a dict.
            entries.insert(id, entry);
        }
        Self {
            entries,
            idf,
            default_idf: (total + 1.0).ln() + 1.0,
            vectors: Mutex::new(HashMap::new()),
        }
    }

    pub fn vector(&self, entry: &Value) -> Arc<Vector> {
        let key = text(&entry["id"]);
        let pooled = self.entries.contains_key(&key);
        if pooled
            && let Some(v) = self
                .vectors
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(&key)
        {
            return v.clone();
        }
        let mut vec = Vector::new();
        for t in traits(entry) {
            let group = t.split(':').next().unwrap_or("");
            let weight =
                group_weight(group) * self.idf.get(&t).copied().unwrap_or(self.default_idf);
            vec.insert(t, weight);
        }
        let vec = Arc::new(normalized(vec));
        if pooled {
            self.vectors
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .insert(key, vec.clone());
        }
        vec
    }
}

fn cosine(a: &Vector, b: &Vector) -> f64 {
    let (a, b) = if a.len() > b.len() { (b, a) } else { (a, b) };
    a.iter()
        .map(|(t, v)| v * b.get(t).copied().unwrap_or(0.0))
        .sum()
}

fn quality(entry: &Value) -> f64 {
    let rating = float(&entry["rating"]).unwrap_or(0.0);
    if rating == 0.0 {
        0.0
    } else {
        ((rating - 6.5) / 2.5).clamp(-1.0, 1.0)
    }
}

// ---------- The user's taste ----------

const RATING_WEIGHTS: [(f64, f64); 5] =
    [(1.0, -1.0), (2.0, -0.5), (3.0, 0.2), (4.0, 0.8), (5.0, 1.0)];

pub fn rating_weight(rating: f64) -> f64 {
    let value = rating.clamp(1.0, 5.0);
    for pair in RATING_WEIGHTS.windows(2) {
        let ((low, low_weight), (high, high_weight)) = (pair[0], pair[1]);
        if value <= high {
            return py_round(
                low_weight + (high_weight - low_weight) * (value - low) / (high - low),
                4,
            );
        }
    }
    RATING_WEIGHTS[4].1
}

fn history_weight(entry: &Value, now: f64) -> f64 {
    let pct = float(&entry["progress_pct"]).unwrap_or(0.0);
    if truthy(&entry["completed"]) || truthy(&entry["imported_watched"]) || pct >= 85.0 {
        return 0.6;
    }
    let last = float(&entry["last_watched"]).unwrap_or(0.0);
    if pct < 25.0 && last != 0.0 && now - last > 21.0 * 86400.0 {
        return -0.3; // started, then left a long time ago
    }
    0.2
}

fn user_rating(value: &Value) -> Option<f64> {
    match value {
        Value::Null => None,
        Value::String(s) if s.is_empty() => None,
        v => float(v),
    }
}

fn id_of(item: &Value) -> String {
    if truthy(&item["id"]) {
        text(&item["id"])
    } else {
        String::new()
    }
}

/// {id: weight} of every title the user told something about.
pub fn taste_signals(
    history: &[Value],
    watchlist: &[Value],
    not_for_me: &[String],
    now: f64,
) -> IndexMap<String, f64> {
    let mut signals: IndexMap<String, f64> = IndexMap::new();
    for entry in history {
        let id = id_of(entry);
        if IMDB.is_match(&id) {
            let weight = match user_rating(&entry["user_rating"]) {
                Some(r) if r != 0.0 => rating_weight(r),
                _ if entry.as_object().is_some_and(|o| !o.is_empty()) => history_weight(entry, now),
                _ => 0.0,
            };
            signals.insert(id, weight);
        }
    }
    for item in watchlist {
        let id = id_of(item);
        if IMDB.is_match(&id) && !signals.contains_key(&id) {
            let weight = match user_rating(&item["user_rating"]) {
                Some(r) if r != 0.0 => rating_weight(r),
                _ => 0.3,
            };
            signals.insert(id, weight);
        }
    }
    for id in not_for_me {
        signals.insert(id.clone(), -1.0);
    }
    signals.retain(|_, w| *w != 0.0);
    signals
}

/// (liked vector, disliked vector) from the titles the user told something about.
fn profile(
    space: &Space,
    signals: &IndexMap<String, f64>,
    known: &IndexMap<String, Value>,
) -> (Vector, Vector) {
    let (mut liked, mut disliked) = (Vector::new(), Vector::new());
    for (id, weight) in signals {
        let Some(entry) = known.get(id).filter(|e| truthy(e)) else {
            continue;
        };
        let target = if *weight > 0.0 {
            &mut liked
        } else {
            &mut disliked
        };
        for (t, v) in space.vector(entry).iter() {
            *target.entry(t.clone()).or_insert(0.0) += weight.abs() * v;
        }
    }
    (normalized(liked), normalized(disliked))
}

// ---------- Suggestions ----------

fn shared(a: &Value, b: &Value, role: &str) -> Vec<String> {
    let other: HashSet<String> = strings(&b[role]).iter().map(|n| fold(n)).collect();
    strings(&a[role])
        .into_iter()
        .filter(|n| other.contains(&fold(n)))
        .collect()
}

fn genre_fr(genre: &str) -> Option<&'static str> {
    Some(match genre {
        "Action" => "action",
        "Adventure" => "aventure",
        "Animation" => "animation",
        "Biography" => "biopic",
        "Comedy" => "comédie",
        "Crime" => "policier",
        "Documentary" => "documentaire",
        "Drama" => "drame",
        "Family" => "familial",
        "Fantasy" => "fantastique",
        "History" => "histoire",
        "Horror" => "horreur",
        "Mystery" => "mystère",
        "Romance" => "romance",
        "Sci-Fi" => "science-fiction",
        "Sport" => "sport",
        "Thriller" => "thriller",
        "War" => "guerre",
        "Western" => "western",
        "Music" => "musique",
        _ => return None,
    })
}

/// str.capitalize(): the first letter up, the rest down.
fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    chars
        .next()
        .map(|c| {
            c.to_uppercase()
                .chain(chars.flat_map(char::to_lowercase))
                .collect()
        })
        .unwrap_or_default()
}

/// The strongest reason two titles go together, in French.
pub fn why_close(candidate: &Value, source: &Value) -> String {
    if let Some(d) = shared(candidate, source, "director").first() {
        return format!("Même réalisation ({d})");
    }
    if let Some(w) = shared(candidate, source, "writer").first() {
        return format!("Même scénario ({w})");
    }
    let cast = shared(candidate, source, "cast");
    if let Some(first) = cast.first() {
        return match cast.get(1) {
            Some(second) => format!("Avec {first} et {second}"),
            None => format!("Avec {first}"),
        };
    }
    let theirs = strings(&source["genres"]);
    let genres: Vec<String> = strings(&candidate["genres"])
        .into_iter()
        .filter(|g| theirs.contains(g))
        .collect();
    let year_of = |e: &Value| {
        if truthy(&e["year"]) {
            text(&e["year"])
        } else {
            String::new()
        }
    };
    let (year, other) = (year_of(candidate), year_of(source));
    if let Some(genre) = genres.first() {
        let label = capitalize(
            &genre_fr(genre)
                .map(str::to_string)
                .unwrap_or_else(|| genre.to_lowercase()),
        );
        if is_digits(&year)
            && is_digits(&other)
            && let (Ok(y), Ok(o)) = (year.parse::<i64>(), other.parse::<i64>())
            && y / 10 == o / 10
        {
            return if y < 2000 {
                format!("{label} des années {:02}", y / 10 * 10 % 100)
            } else {
                format!("{label} des années {}", y / 10 * 10)
            };
        }
        return format!("{label} aussi");
    }
    "Thèmes proches".into()
}

fn card(entry: &Value, why: &str) -> Value {
    let id = text(&entry["id"]);
    let rating = float(&entry["rating"]).unwrap_or(0.0);
    let mut out = json!({
        "id": entry["id"], "name": entry["name"], "type": entry["type"],
        "year": if truthy(&entry["year"]) { entry["year"].clone() } else { json!("") },
        "poster": if truthy(&entry["poster"]) { entry["poster"].clone() } else { json!(format!("https://images.metahub.space/poster/medium/{id}/img")) },
        "imdbRating": if rating != 0.0 { format!("{rating:.1}") } else { String::new() },
    });
    if !why.is_empty() {
        out["why"] = json!(why);
    }
    out
}

fn people(entry: &Value) -> Vec<String> {
    let mut out: Vec<String> = strings(&entry["director"])
        .iter()
        .take(1)
        .map(|n| format!("d:{}", fold(n)))
        .collect();
    out.extend(
        strings(&entry["cast"])
            .iter()
            .take(2)
            .map(|n| format!("c:{}", fold(n))),
    );
    out
}

/// Best first, each next one also different from those already chosen (MMR),
/// and no director or lead actor in more than three of them.
fn pick<'a>(
    space: &Space,
    mut scored: Vec<(f64, &'a Value)>,
    limit: usize,
) -> Vec<(f64, &'a Value)> {
    struct Candidate<'a> {
        score: f64,
        entry: &'a Value,
        vector: Arc<Vector>,
        people: Vec<String>,
        /// The highest likeness to a title already chosen.
        closest: Option<f64>,
    }
    scored.sort_by(|a, b| b.0.total_cmp(&a.0));
    let mut remaining: Vec<Candidate> = scored
        .into_iter()
        .take(limit * 8)
        .map(|(score, entry)| Candidate {
            score,
            entry,
            vector: space.vector(entry),
            people: people(entry),
            closest: None,
        })
        .collect();
    let mut chosen: Vec<(f64, &Value)> = Vec::new();
    let mut counts: HashMap<String, i64> = HashMap::new();
    while !remaining.is_empty() && chosen.len() < limit {
        remaining.retain(|c| {
            c.people
                .iter()
                .all(|p| counts.get(p).copied().unwrap_or(0) < PER_PERSON)
        });
        if remaining.is_empty() {
            break;
        }
        let mut best: Option<(usize, f64)> = None;
        for (index, c) in remaining.iter().enumerate() {
            let value = (1.0 - DIVERSITY) * c.score - DIVERSITY * c.closest.unwrap_or(0.0);
            if best.is_none_or(|(_, b)| value > b) {
                best = Some((index, value));
            }
        }
        let picked = remaining.remove(best.map(|b| b.0).unwrap_or(0));
        // Each likeness is measured once, against the title just chosen.
        for c in &mut remaining {
            let likeness = cosine(&c.vector, &picked.vector);
            c.closest = Some(
                c.closest
                    .map_or(likeness, |m| if likeness > m { likeness } else { m }),
            );
        }
        for person in picked.people {
            *counts.entry(person).or_default() += 1;
        }
        chosen.push((picked.score, picked.entry));
    }
    chosen
}

/// Titles close to one title: the same people first, then genres, era and themes.
pub fn related(
    space: &Space,
    source: Option<&Value>,
    kind: &str,
    exclude: &HashSet<String>,
    limit: usize,
) -> Vec<Value> {
    let Some(source) = source.filter(|s| truthy(s)) else {
        return vec![];
    };
    let vec = space.vector(source);
    let own = text(&source["id"]);
    let mut scored = Vec::new();
    for (id, entry) in &space.entries {
        if entry["type"] != kind || exclude.contains(id) || *id == own {
            continue;
        }
        let closeness = cosine(&vec, &space.vector(entry));
        if closeness > 0.05 {
            scored.push((closeness + QUALITY_WEIGHT * quality(entry), entry));
        }
    }
    pick(space, scored, limit)
        .into_iter()
        .map(|(_, e)| card(e, &why_close(e, source)))
        .collect()
}

/// Titles for this user: close to what they liked, far from what they did not.
pub fn for_you(
    space: &Space,
    signals: &IndexMap<String, f64>,
    known: &IndexMap<String, Value>,
    kind: &str,
    limit: usize,
) -> Vec<Value> {
    let (liked, disliked) = profile(space, signals, known);
    if liked.is_empty() {
        return vec![];
    }
    let mut ranked: Vec<(&String, &f64)> = signals.iter().collect();
    ranked.sort_by(|a, b| b.1.total_cmp(a.1));
    let fans: Vec<&Value> = ranked
        .into_iter()
        .filter(|(i, w)| **w > 0.0 && known.contains_key(*i))
        .map(|(i, _)| &known[i])
        .collect();
    let mut scored = Vec::new();
    for (id, entry) in &space.entries {
        if entry["type"] != kind || signals.contains_key(id) {
            continue;
        }
        let vec = space.vector(entry);
        let score =
            cosine(&liked, &vec) - 0.6 * cosine(&disliked, &vec) + QUALITY_WEIGHT * quality(entry);
        if score > 0.05 {
            scored.push((score, entry));
        }
    }
    let mut cards = Vec::new();
    for (_, entry) in pick(space, scored, limit * 2) {
        let vec = space.vector(entry);
        let mut reasons: Vec<(f64, &Value)> = fans
            .iter()
            .take(60)
            .filter(|f| f["type"] == kind)
            .map(|f| (cosine(&vec, &space.vector(f)), *f))
            .collect();
        reasons.sort_by(|a, b| b.0.total_cmp(&a.0));
        reasons.truncate(2);
        reasons.retain(|r| r.0 > 0.08);
        let Some(first) = reasons.first() else {
            continue;
        };
        let titles: Vec<String> = reasons.iter().map(|(_, f)| text(&f["name"])).collect();
        cards.push(card(
            entry,
            &format!(
                "Parce que vous avez aimé {} · {}",
                titles.join(" et "),
                why_close(entry, first.1)
            ),
        ));
        if cards.len() >= limit {
            break;
        }
    }
    cards
}

// ---------- Storage (the tables the Python version uses) ----------

fn tables(c: &Connection) -> rusqlite::Result<()> {
    c.execute("CREATE TABLE IF NOT EXISTS taste_pool (id TEXT PRIMARY KEY, type TEXT, data_json TEXT, updated_at REAL)", [])?;
    c.execute("CREATE TABLE IF NOT EXISTS taste_feedback (media_id TEXT PRIMARY KEY, decision TEXT, at REAL)", [])?;
    Ok(())
}

/// (titles in the pool, when it was last written).
type PoolStamp = (i64, Option<f64>);

/// The pool read from the database, kept until another process changes it.
#[derive(Default)]
pub struct Store {
    cached: Mutex<Option<(PoolStamp, Arc<Space>)>>,
}

impl Store {
    /// (titles in the pool, seconds since it was last written).
    pub fn pool_age(&self, db: &Db, now: f64) -> (i64, Option<f64>) {
        let (count, at) = Self::stamp(db);
        (count, at.map(|a| now - a))
    }

    fn stamp(db: &Db) -> (i64, Option<f64>) {
        db.with(|c| {
            tables(c)?;
            c.query_row(
                "SELECT COUNT(*), MAX(updated_at) FROM taste_pool",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
        })
        .unwrap_or((0, None))
    }

    pub fn space(&self, db: &Db) -> Arc<Space> {
        let stamp = Self::stamp(db);
        let mut cached = self.cached.lock().unwrap_or_else(|e| e.into_inner());
        if let Some((at, space)) = cached.as_ref()
            && *at == stamp
        {
            return space.clone();
        }
        let rows: Vec<String> = db
            .with(|c| {
                let mut s = c.prepare("SELECT data_json FROM taste_pool")?;
                s.query_map([], |r| r.get::<_, Option<String>>(0))?
                    .map(|r| r.map(Option::unwrap_or_default))
                    .collect()
            })
            .unwrap_or_default();
        let space = Arc::new(Space::new(
            rows.iter()
                .filter_map(|r| serde_json::from_str(r).ok())
                .collect(),
        ));
        *cached = Some((stamp, space.clone()));
        space
    }

    /// Reads the pool and measures each title's traits, ahead of the first ask.
    pub fn warm(&self, db: &Db) {
        let space = self.space(db);
        for entry in space.entries.values() {
            space.vector(entry);
        }
    }

    /// Titles fetched one by one (watched titles missing from the pool).
    pub fn add_known(&self, db: &Db, entries: &[Value], now: f64) {
        let _ = db.with(|c| {
            tables(c)?;
            let tx = c.unchecked_transaction()?;
            for e in entries {
                tx.execute(
                    "INSERT OR REPLACE INTO taste_pool (id, type, data_json, updated_at) VALUES (?, ?, ?, ?)",
                    params![text(&e["id"]), text(&e["type"]), e.to_string(), now],
                )?;
            }
            tx.commit()
        });
    }

    pub fn not_for_me(db: &Db) -> Vec<String> {
        db.with(|c| {
            tables(c)?;
            let mut s =
                c.prepare("SELECT media_id FROM taste_feedback WHERE decision = 'not_for_me'")?;
            s.query_map([], |r| r.get(0))?.collect()
        })
        .unwrap_or_default()
    }

    pub fn set_not_for_me(db: &Db, id: &str, now: f64) {
        let _ = db.with(|c| {
            tables(c)?;
            c.execute("INSERT OR REPLACE INTO taste_feedback (media_id, decision, at) VALUES (?, 'not_for_me', ?)", params![id, now])
        });
    }

    pub fn reset_feedback(db: &Db) {
        let _ = db.with(|c| {
            tables(c)?;
            c.execute("DELETE FROM taste_feedback", [])
        });
    }
}

// ---------- The pool, read from Cinemeta's public catalogues ----------

const MOVIE_GENRES: [&str; 19] = [
    "Action",
    "Adventure",
    "Animation",
    "Biography",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Family",
    "Fantasy",
    "History",
    "Horror",
    "Mystery",
    "Romance",
    "Sci-Fi",
    "Sport",
    "Thriller",
    "War",
    "Western",
];
const POOL_MAX_AGE: f64 = 30.0 * 86400.0;
static BUILDING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// (kind, catalogue, genre, skip) of the pages read: about 200 requests.
fn pool_pages(year: i32) -> Vec<(&'static str, &'static str, String, u32)> {
    let mut pages = Vec::new();
    pages.extend(
        (0..1000)
            .step_by(50)
            .map(|s| ("movie", "top", String::new(), s)),
    );
    pages.extend(
        (0..500)
            .step_by(50)
            .map(|s| ("movie", "imdbRating", String::new(), s)),
    );
    for genre in MOVIE_GENRES {
        pages.extend([0, 50, 100].map(|s| ("movie", "top", genre.to_string(), s)));
    }
    for y in (2000..=year).rev() {
        pages.extend([0, 50].map(|s| ("movie", "year", y.to_string(), s)));
    }
    pages.extend(
        (0..500)
            .step_by(50)
            .map(|s| ("series", "top", String::new(), s)),
    );
    pages.extend(
        (0..250)
            .step_by(50)
            .map(|s| ("series", "imdbRating", String::new(), s)),
    );
    for genre in MOVIE_GENRES.iter().chain(["Reality-TV"].iter()) {
        pages.extend([0, 50].map(|s| ("series", "top", genre.to_string(), s)));
    }
    pages
}

fn page_url(base: &str, kind: &str, catalog: &str, genre: &str, skip: u32) -> String {
    let extra: Vec<String> = [
        (!genre.is_empty()).then(|| format!("genre={genre}")),
        (skip != 0).then(|| format!("skip={skip}")),
    ]
    .into_iter()
    .flatten()
    .collect();
    let extra = if extra.is_empty() {
        String::new()
    } else {
        format!("/{}", extra.join("&"))
    };
    format!("{base}/catalog/{kind}/{catalog}{extra}.json")
}

/// Whether the pool is being read again now.
pub fn building() -> bool {
    BUILDING.load(std::sync::atomic::Ordering::SeqCst)
}

impl Store {
    /// Reads the public catalogues again when the pool is empty or a month old.
    pub async fn refresh_pool(
        &self,
        db: &Db,
        net: &crate::net::Net,
        base: &str,
        force: bool,
    ) -> usize {
        let (count, age) = self.pool_age(db, crate::db::now());
        if !force && count as usize >= POOL_READY && age.is_some_and(|a| a < POOL_MAX_AGE) {
            return count as usize;
        }
        if BUILDING.swap(true, std::sync::atomic::Ordering::SeqCst) {
            return count as usize;
        }
        let year = chrono::Datelike::year(&chrono::Utc::now());
        let headers = [
            ("User-Agent", crate::net::BROWSER),
            ("Accept", "application/json, text/plain, */*"),
        ];
        let pages: Vec<(String, bool)> = pool_pages(year)
            .into_iter()
            .map(|(kind, catalog, genre, skip)| {
                (
                    page_url(base, kind, catalog, &genre, skip),
                    kind == "series",
                )
            })
            .collect();
        let mut found: IndexMap<String, Value> = IndexMap::new();
        let mut reads = futures_util::StreamExt::buffered(
            futures_util::stream::iter(pages.into_iter().map(|(url, series)| async move {
                let metas = net
                    .request_json(
                        reqwest::Method::GET,
                        &url,
                        &headers,
                        None,
                        std::time::Duration::from_secs(12),
                        1,
                    )
                    .await
                    .map(|d| d["metas"].as_array().cloned().unwrap_or_default())
                    .unwrap_or_default();
                tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                (metas, series)
            })),
            4,
        );
        while let Some((metas, series)) = futures_util::StreamExt::next(&mut reads).await {
            for meta in metas {
                if let Some(entry) = compact(&meta, if series { "series" } else { "movie" }) {
                    found.entry(text(&entry["id"])).or_insert(entry);
                }
            }
        }
        let entries: Vec<Value> = found.into_values().collect();
        let saved = if entries.len() >= POOL_READY {
            let at = crate::db::now();
            let _ = db.with(|c| {
                tables(c)?;
                let tx = c.unchecked_transaction()?;
                tx.execute("DELETE FROM taste_pool", [])?;
                for e in &entries {
                    tx.execute(
                        "INSERT INTO taste_pool (id, type, data_json, updated_at) VALUES (?, ?, ?, ?)",
                        params![text(&e["id"]), text(&e["type"]), e.to_string(), at],
                    )?;
                }
                tx.commit()
            });
            entries.len()
        } else {
            count as usize
        };
        BUILDING.store(false, std::sync::atomic::Ordering::SeqCst);
        saved
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weights_reasons_and_words() {
        assert_eq!(rating_weight(4.0), 0.8);
        assert_eq!(rating_weight(4.5), 0.9);
        assert_eq!(rating_weight(2.5), -0.15);
        assert_eq!(rating_weight(9.0), 1.0);
        assert_eq!(capitalize("science-fiction"), "Science-fiction");
        assert_eq!(
            description_words("The Mummies hunt lost cities across Egypt; mummies again!"),
            vec!["mummie", "hunt", "lost", "citie", "across", "egypt"]
        );
        let a = json!({"genres": ["Drama", "Sci-Fi"], "year": "1994", "director": ["X"], "cast": ["A", "B"]});
        let b = json!({"genres": ["Sci-Fi"], "year": "1999", "director": ["Y"], "cast": ["C"]});
        assert_eq!(why_close(&a, &b), "Science-fiction des années 90");
        assert_eq!(format!("{:.1}", 7.25), "7.2", "ties to even, as Python");
    }

    /// The choice as first written: every likeness measured again each round.
    fn pick_reference<'a>(
        space: &Space,
        mut scored: Vec<(f64, &'a Value)>,
        limit: usize,
    ) -> Vec<(f64, &'a Value)> {
        scored.sort_by(|a, b| b.0.total_cmp(&a.0));
        let mut remaining: Vec<(f64, &Value)> = scored.into_iter().take(limit * 8).collect();
        let mut chosen: Vec<(f64, &Value)> = Vec::new();
        let mut counts: HashMap<String, i64> = HashMap::new();
        while !remaining.is_empty() && chosen.len() < limit {
            remaining.retain(|(_, e)| {
                people(e)
                    .iter()
                    .all(|p| counts.get(p).copied().unwrap_or(0) < PER_PERSON)
            });
            if remaining.is_empty() {
                break;
            }
            let mut best: Option<(usize, f64)> = None;
            for (index, (score, entry)) in remaining.iter().enumerate() {
                let vec = space.vector(entry);
                let closest = chosen
                    .iter()
                    .map(|(_, other)| cosine(&vec, &space.vector(other)))
                    .fold(None, |m: Option<f64>, c| {
                        Some(m.map_or(c, |m| if c > m { c } else { m }))
                    })
                    .unwrap_or(0.0);
                let value = (1.0 - DIVERSITY) * score - DIVERSITY * closest;
                if best.is_none_or(|(_, b)| value > b) {
                    best = Some((index, value));
                }
            }
            let picked = remaining.remove(best.map(|b| b.0).unwrap_or(0));
            for person in people(picked.1) {
                *counts.entry(person).or_default() += 1;
            }
            chosen.push(picked);
        }
        chosen
    }

    #[test]
    fn pick_chooses_as_the_reference() {
        let genres = [
            "Drama",
            "Comedy",
            "Sci-Fi",
            "Horror",
            "Romance",
            "Thriller",
            "Animation",
        ];
        let people = [
            "Ana", "Ben", "Chloé", "Dario", "Emi", "Farid", "Gus", "Hana",
        ];
        let mut seed: u64 = 7;
        let mut next = |n: usize| {
            seed = seed
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            (seed >> 33) as usize % n
        };
        let titles: Vec<Value> = (0..600)
            .map(|i| {
                json!({
                    "id": format!("tt{i:07}"),
                    "type": "movie",
                    "genres": [genres[next(genres.len())], genres[next(genres.len())]],
                    "director": [people[next(people.len())]],
                    "cast": [people[next(people.len())], people[next(people.len())], people[next(people.len())]],
                    "year": format!("{}", 1960 + next(60)),
                    "rating": format!("{}.{}", 5 + next(4), next(10)),
                })
            })
            .collect();
        let space = Space::new(titles.clone());
        for limit in [1, 5, 20, 40] {
            let scored: Vec<(f64, &Value)> = space
                .entries
                .values()
                .enumerate()
                .map(|(i, e)| (((i * 37) % 101) as f64 / 101.0, e))
                .collect();
            let ids = |chosen: Vec<(f64, &Value)>| -> Vec<String> {
                chosen
                    .iter()
                    .map(|(s, e)| format!("{s} {}", e["id"]))
                    .collect()
            };
            assert_eq!(
                ids(pick(&space, scored.clone(), limit)),
                ids(pick_reference(&space, scored, limit)),
                "limit {limit}"
            );
        }
    }
}
