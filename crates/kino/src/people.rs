//! Cast photos and the « Page acteur »: a person's films and series, each
//! confirmed by the title's own credits, with a photo and a short French bio.
//! Photo and bio come from Wikipedia, only from a page about a person of film
//! or TV, so a namesake never lends its picture.

use std::sync::LazyLock;
use std::time::Duration;

use futures_util::StreamExt;
use regex::Regex;
use serde_json::{Map, Value, json};
use unicode_normalization::UnicodeNormalization;
use unicode_normalization::char::is_combining_mark;

use crate::meta::Meta;

const AGENT: &str = "KINO/1.1 (lecteur de films local ; photos de la distribution)";
const PHOTO_TTL: f64 = 30.0 * 86400.0;
const MAX_PER_KIND: usize = 30;
const BIO_LIMIT: usize = 420;
static SPACES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s+").unwrap());
static NOT_WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^\w\s]").unwrap());
static FOUR_DIGITS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\d{4}").unwrap());
static SCREEN_PERSON: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(actor|actress|voice|comedian|composer|director|filmmaker|screenwriter|writer|producer|singer|musician|rapper|model|presenter|television|entertainer|performer|dancer|animator|youtuber)\b",
    )
    .unwrap()
});

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

fn or(a: &Value, b: &Value) -> Value {
    if truthy(a) { a.clone() } else { b.clone() }
}

/// urllib.parse.quote(text, safe="").
fn quote(text: &str) -> String {
    text.bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"_.-~".contains(&b) {
                (b as char).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect()
}

pub fn clean_name(name: &str) -> String {
    SPACES
        .replace_all(name, " ")
        .trim()
        .chars()
        .take(80)
        .collect()
}

/// « Zoë  Saldaña » and « zoe saldana » are the same person.
pub fn fold(name: &str) -> String {
    let bare: String = name
        .nfkd()
        .filter(|c| !is_combining_mark(*c))
        .collect::<String>()
        .to_lowercase();
    SPACES
        .replace_all(&NOT_WORD.replace_all(&bare, " "), " ")
        .trim()
        .to_string()
}

fn wikimedia(url: &str) -> bool {
    reqwest::Url::parse(url).is_ok_and(|u| {
        u.scheme() == "https" && u.host_str().is_some_and(|h| h.ends_with(".wikimedia.org"))
    })
}

impl Meta {
    /// The English Wikipedia summary of a person of film or TV: Some(Null) when
    /// there is none, None on network trouble.
    pub async fn screen_person_summary(&self, name: &str) -> Option<Value> {
        for title in [
            name.to_string(),
            format!("{name} (actor)"),
            format!("{name} (actress)"),
        ] {
            let url = format!(
                "https://en.wikipedia.org/api/rest_v1/page/summary/{}",
                quote(&title.replace(' ', "_"))
            );
            let data = match self
                .net
                .request_json(
                    reqwest::Method::GET,
                    &url,
                    &[("User-Agent", AGENT)],
                    None,
                    Duration::from_secs(6),
                    1,
                )
                .await
            {
                Ok(d) => d,
                Err(e) if e.0.contains("(404)") => continue,
                Err(_) => return None,
            };
            if data["type"] != "standard" || !SCREEN_PERSON.is_match(&text(&data["description"])) {
                continue;
            }
            return Some(data);
        }
        Some(Value::Null)
    }

    /// The Wikipedia thumbnail of a cast or crew member: '' when none, None on network trouble.
    pub async fn person_photo(&self, name: &str) -> Option<String> {
        let name = clean_name(name);
        if name.is_empty() {
            return Some(String::new());
        }
        let key = format!("person_photo_v1:{}", name.to_lowercase());
        let found = self
            .net
            .cached(&key, PHOTO_TTL, || async {
                let Some(data) = self.screen_person_summary(&name).await else {
                    return Ok(Value::Null);
                };
                let thumb = text(&data["thumbnail"]["source"]);
                Ok(json!(if wikimedia(&thumb) {
                    thumb
                } else {
                    String::new()
                }))
            })
            .await
            .ok()?;
        found.as_str().map(str::to_string)
    }
}

/// Photos for up to 12 names, six at a time: {name: url or null}.
pub async fn person_photos(meta: &Meta, names: &[String]) -> Map<String, Value> {
    let mut unique: Vec<String> = Vec::new();
    for name in names
        .iter()
        .map(|n| clean_name(n))
        .filter(|n| !n.is_empty())
    {
        if !unique.contains(&name) {
            unique.push(name);
        }
    }
    unique.truncate(12);
    let photos: Vec<Option<String>> =
        futures_util::stream::iter(unique.clone().into_iter().map(|name| {
            let meta = meta.clone();
            async move { meta.person_photo(&name).await }
        }))
        .buffered(6)
        .collect()
        .await;
    unique
        .into_iter()
        .zip(photos)
        .map(|(n, p)| {
            (
                n,
                p.filter(|p| !p.is_empty()).map_or(Value::Null, Value::from),
            )
        })
        .collect()
}

/// Where the name appears in a title's credits: « cast », « director », « writer ».
fn roles_in(meta: &Value, name: &str) -> Vec<&'static str> {
    let wanted = fold(name);
    ["cast", "director", "writer"]
        .into_iter()
        .filter(|role| {
            meta[*role]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .any(|c| fold(c) == wanted)
        })
        .collect()
}

fn year(item: &Value) -> i64 {
    let shown = text(&or(&or(&item["releaseInfo"], &item["year"]), &json!("")));
    FOUR_DIGITS
        .find(&shown)
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0)
}

fn entry(found: &Value, meta: &Value, kind: &str, roles: Vec<&str>) -> Value {
    json!({
        "id": or(&found["id"], &found["imdb_id"]), "imdb_id": or(&found["imdb_id"], &found["id"]), "type": kind,
        "name": or(&or(&found["name"], &meta["name"]), &json!("")),
        "year": text(&or(&or(&or(&found["releaseInfo"], &found["year"]), &meta["year"]), &json!(""))),
        "releaseInfo": or(&found["releaseInfo"], &json!("")),
        "poster": or(&or(&found["poster"], &meta["poster"]), &json!("")),
        "imdbRating": or(&or(&meta["imdbRating"], &found["imdbRating"]), &json!("")),
        "roles": roles,
    })
}

/// {"movies", "series", "others"}: titles confirmed by their credits, newest first; the rest apart.
async fn person_works(meta: &Meta, name: &str) -> Map<String, Value> {
    let mut found: Vec<(bool, Value)> = Vec::new();
    for kind in ["movie", "series"] {
        let mut seen: Vec<Value> = Vec::new();
        for item in meta
            .search(name, kind)
            .await
            .unwrap_or_default()
            .into_iter()
            .take(MAX_PER_KIND)
        {
            let key = or(&item["id"], &item["imdb_id"]);
            if truthy(&key) && text(&key).starts_with("tt") && !seen.contains(&key) {
                seen.push(key);
                found.push((kind == "series", item));
            }
        }
    }
    let entries: Vec<Value> =
        futures_util::stream::iter(found.into_iter().map(|(is_series, item)| {
            let (meta, name) = (meta.clone(), name.to_string());
            async move {
                let kind = if is_series { "series" } else { "movie" };
                let id = text(&or(&item["id"], &item["imdb_id"]));
                let details = meta.media(&id, kind).await.unwrap_or(Value::Null);
                entry(&item, &details, kind, roles_in(&details, &name))
            }
        }))
        .buffered(8)
        .collect()
        .await;
    let newest = |list: Vec<&Value>| {
        let mut list: Vec<Value> = list.into_iter().cloned().collect();
        list.sort_by_key(|e| -year(e));
        Value::Array(list)
    };
    let confirmed = |kind: &str| {
        entries
            .iter()
            .filter(|e| truthy(&e["roles"]) && e["type"] == kind)
            .collect()
    };
    let mut out = Map::new();
    out.insert("movies".into(), newest(confirmed("movie")));
    out.insert("series".into(), newest(confirmed("series")));
    out.insert(
        "others".into(),
        newest(entries.iter().filter(|e| !truthy(&e["roles"])).collect()),
    );
    out
}

/// The first sentences of a summary, at most 420 characters.
fn short_bio(extract: &str) -> String {
    let text = SPACES.replace_all(extract, " ").trim().to_string();
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= BIO_LIMIT {
        return text;
    }
    let cut = &chars[..BIO_LIMIT];
    let end = cut
        .windows(2)
        .rposition(|w| w[0] == '.' && (w[1] == ' ' || w[1] == '\u{a0}'));
    match end {
        Some(end) if end > 80 => cut[..=end].iter().collect(),
        _ => {
            let cut: String = cut.iter().collect();
            format!(
                "{}…",
                cut.rsplit_once(' ').map_or(cut.as_str(), |(head, _)| head)
            )
        }
    }
}

/// Photo, French description and bio, cached a month; empty texts when Wikipedia
/// has no page on this person of film or TV, None on network trouble.
async fn person_profile(meta: &Meta, name: &str) -> Option<Value> {
    let name = clean_name(name);
    let key = format!("person_profile_v1:{}", fold(&name));
    let found = meta
        .net
        .cached(&key, PHOTO_TTL, || async {
            let Some(summary) = meta.screen_person_summary(&name).await else { return Ok(Value::Null) };
            if summary.is_null() {
                return Ok(json!({"photo": "", "description": "", "bio": ""}));
            }
            let photo = text(&summary["thumbnail"]["source"]);
            let mut profile = json!({"photo": if wikimedia(&photo) { photo } else { String::new() }, "description": "", "bio": ""});
            let en_title = if truthy(&summary["title"]) { text(&summary["title"]) } else { name.clone() };
            let form = [("action", "query"), ("prop", "langlinks"), ("lllang", "fr"), ("titles", en_title.as_str()), ("format", "json"), ("formatversion", "2")];
            let headers = [("User-Agent", AGENT)];
            if let Ok(links) = meta.net.request_form("https://en.wikipedia.org/w/api.php", &headers, &form, Duration::from_secs(6), 1).await {
                let fr_title = links["query"]["pages"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .flat_map(|p| p["langlinks"].as_array().into_iter().flatten())
                    .map(|l| &l["title"])
                    .find(|t| truthy(t))
                    .map(text)
                    .unwrap_or_default();
                if !fr_title.is_empty() {
                    let url = format!("https://fr.wikipedia.org/api/rest_v1/page/summary/{}", quote(&fr_title.replace(' ', "_")));
                    if let Ok(fr) = meta.net.request_json(reqwest::Method::GET, &url, &headers, None, Duration::from_secs(6), 1).await
                        && fr["type"] == "standard"
                    {
                        profile["description"] = json!(text(&fr["description"]).chars().take(120).collect::<String>());
                        profile["bio"] = json!(short_bio(&text(&fr["extract"])));
                    }
                }
            }
            Ok(profile)
        })
        .await
        .ok()?;
    (!found.is_null()).then_some(found)
}

/// Everything the « Page acteur » shows, gathered side by side.
pub async fn page(meta: &Meta, name: &str) -> Value {
    let name = clean_name(name);
    let (profile, works) = tokio::join!(person_profile(meta, &name), person_works(meta, &name));
    let mut out = Map::new();
    out.insert("name".into(), json!(name));
    let profile = profile.unwrap_or_else(|| json!({"photo": "", "description": "", "bio": ""}));
    out.extend(profile.as_object().cloned().unwrap_or_default());
    out.extend(works);
    Value::Object(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_and_bios() {
        assert_eq!(fold("Zoë  Saldaña"), "zoe saldana");
        assert_eq!(fold("Jean-Paul Belmondo"), "jean paul belmondo");
        assert_eq!(clean_name("  Tom \n Hanks "), "Tom Hanks");
        assert_eq!(quote("Tom_Hanks_(actor)"), "Tom_Hanks_%28actor%29");
        let long = format!("{} Fin. {}", "a".repeat(100), "b ".repeat(200));
        assert_eq!(short_bio(&long), format!("{} Fin.", "a".repeat(100)));
        let words = "mot ".repeat(150);
        assert!(short_bio(&words).ends_with("mot…"));
        assert_eq!(short_bio("  Court.  "), "Court.");
    }
}
