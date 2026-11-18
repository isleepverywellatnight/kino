//! Where the chapters and volumes of a manga come from.
//!
//! For one AniList series, KINO finds its chapters on several sites by its titles:
//! WeebCentral (English scans of nearly every series, within days of the
//! Japanese release) and MangaDex (volunteer translations in many languages,
//! the series whose AniList number matches). Whole volumes come from Nyaa by
//! torrent. A site that answers with a browser check is left out: KINO never
//! tries to pass one.

use std::collections::HashSet;
use std::sync::LazyLock;
use std::time::Duration;

use regex::Regex;
use scraper::{ElementRef, Html, Selector};
use serde_json::{Value, json};
use unicode_normalization::UnicodeNormalization;

use crate::net::{BROWSER, Net, NetError, NetResult};

pub const NYAA_TRACKERS: [&str; 4] = [
    "http://nyaa.tracker.wf:7777/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://exodus.desync.com:6969/announce",
];

static WC_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[0-9A-Z]{26}$").unwrap());
static NUMBER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\d+(?:\.\d+)?").unwrap());
static LINKS: LazyLock<Selector> = LazyLock::new(|| Selector::parse("a[href]").unwrap());
static COVERS: LazyLock<Selector> = LazyLock::new(|| Selector::parse("img[alt]").unwrap());
static SPANS: LazyLock<Selector> = LazyLock::new(|| Selector::parse("span").unwrap());
static TIMES: LazyLock<Selector> = LazyLock::new(|| Selector::parse("time[datetime]").unwrap());
static IMAGES: LazyLock<Selector> = LazyLock::new(|| Selector::parse("img").unwrap());
const IMAGE_TYPES: [&str; 6] = ["png", "jpg", "jpeg", "webp", "gif", "avif"];
static HASH40: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[0-9a-f]{40}$").unwrap());

/// Where the sites are; tests point them at a local server.
#[derive(Clone)]
pub struct Sites {
    pub anilist: String,
    pub mangadex: String,
    pub weebcentral: String,
    pub nyaa_rss: String,
}

impl Default for Sites {
    fn default() -> Self {
        Self {
            anilist: "https://graphql.anilist.co".into(),
            mangadex: "https://api.mangadex.org".into(),
            weebcentral: "https://weebcentral.com".into(),
            nyaa_rss: "https://nyaa.si/?page=rss".into(),
        }
    }
}

/// « Kaiju No. 8 » and « kaiju no 8 » are the same title.
pub fn normalize(title: &str) -> String {
    title
        .nfkd()
        .filter(char::is_ascii)
        .flat_map(char::to_lowercase)
        .filter(char::is_ascii_alphanumeric)
        .collect()
}

/// « 12 », « 12.0 » and « 012 » are one chapter; None when there is no number.
pub fn number_key(number: &str) -> Option<String> {
    let value: f64 = number.trim().parse().ok()?;
    if !value.is_finite() {
        return None;
    }
    // Python's « %g »: no trailing zero, six significant digits.
    let text = format!("{}", (value * 1e6).round() / 1e6);
    Some(text)
}

/// Python's difflib ratio (Ratcliff/Obershelp): 2 × matching characters / total length.
pub fn similarity(a: &str, b: &str) -> f64 {
    fn matches(a: &[char], b: &[char]) -> usize {
        let (mut best, mut at_a, mut at_b) = (0, 0, 0);
        let mut previous = vec![0usize; b.len() + 1];
        for (i, ca) in a.iter().enumerate() {
            let mut current = vec![0usize; b.len() + 1];
            for (j, cb) in b.iter().enumerate() {
                if ca == cb {
                    current[j + 1] = previous[j] + 1;
                    if current[j + 1] > best {
                        best = current[j + 1];
                        at_a = i + 1 - best;
                        at_b = j + 1 - best;
                    }
                }
            }
            previous = current;
        }
        if best == 0 {
            return 0;
        }
        best + matches(&a[..at_a], &b[..at_b]) + matches(&a[at_a + best..], &b[at_b + best..])
    }
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    if a.is_empty() && b.is_empty() {
        return 1.0;
    }
    2.0 * matches(&a, &b) as f64 / (a.len() + b.len()) as f64
}

fn unique(titles: &[String], limit: usize) -> Vec<&String> {
    let mut seen = HashSet::new();
    titles
        .iter()
        .filter(|t| !t.is_empty() && seen.insert(t.as_str()))
        .take(limit)
        .collect()
}

fn source_error(text: &str) -> NetError {
    NetError(text.to_string())
}

// ---------- WeebCentral ----------

async fn fetch(net: &Net, url: &str, extra: &[(&str, &str)]) -> NetResult<String> {
    net.get_text(url, extra, BROWSER, Duration::from_secs(15))
        .await
}

/// [(series id, name)] for a title, best match first.
pub async fn weebcentral_search(
    net: &Net,
    sites: &Sites,
    query: &str,
) -> NetResult<Vec<(String, String)>> {
    let params: String = form_urlencoded::Serializer::new(String::new())
        .append_pair("text", query)
        .append_pair("sort", "Best Match")
        .append_pair("order", "Descending")
        .append_pair("official", "Any")
        .append_pair("anime", "Any")
        .append_pair("adult", "Any")
        .append_pair("display_mode", "Full Display")
        .finish();
    let page = fetch(
        net,
        &format!("{}/search/data?{params}", sites.weebcentral),
        &[("HX-Request", "true")],
    )
    .await?;
    Ok(parse_search(&page))
}

/// A WeebCentral address of this kind (« series », « chapters »): its id, and
/// the name part after it. Absolute or relative, attributes in any order.
fn weebcentral_link<'a>(href: &'a str, kind: &str) -> Option<(&'a str, &'a str)> {
    let path = [
        "https://weebcentral.com",
        "http://weebcentral.com",
        "//weebcentral.com",
    ]
    .iter()
    .find_map(|base| href.strip_prefix(base))
    .unwrap_or(href);
    let path = path.split(['?', '#']).next().unwrap_or("");
    let mut parts = path.strip_prefix('/')?.split('/');
    if parts.next()? != kind {
        return None;
    }
    let id = parts.next().filter(|id| WC_ID.is_match(id))?;
    Some((id, parts.next().unwrap_or("")))
}

/// The cover's title (« … cover ») in this element, if any.
fn cover_name(element: ElementRef) -> Option<String> {
    element.select(&COVERS).find_map(|img| {
        let alt = img.value().attr("alt")?.trim();
        alt.strip_suffix(" cover")
            .map(str::trim)
            .filter(|n| !n.is_empty())
            .map(str::to_string)
    })
}

/// [(series id, name)] of a search answer, in its order: the name on the cover,
/// else the one in the address.
pub fn parse_search(page: &str) -> Vec<(String, String)> {
    let document = Html::parse_fragment(page);
    let mut found = Vec::new();
    let mut seen = HashSet::new();
    for link in document.select(&LINKS) {
        let Some((id, slug)) = link
            .value()
            .attr("href")
            .and_then(|h| weebcentral_link(h, "series"))
        else {
            continue;
        };
        if !seen.insert(id.to_string()) {
            continue;
        }
        // The cover is in the link, else elsewhere in the same result: the
        // nearest enclosing element that names no other series.
        let card = link
            .ancestors()
            .filter_map(ElementRef::wrap)
            .take_while(|e| {
                e.select(&LINKS).all(|other| {
                    other
                        .value()
                        .attr("href")
                        .and_then(|h| weebcentral_link(h, "series"))
                        .is_none_or(|(other_id, _)| other_id == id)
                })
            })
            .find(|e| cover_name(*e).is_some());
        let name = cover_name(link)
            .or_else(|| card.and_then(cover_name))
            .unwrap_or_else(|| {
                form_urlencoded::parse(format!("x={slug}").as_bytes())
                    .next()
                    .map(|(_, v)| v.replace('-', " "))
                    .unwrap_or_default()
            });
        found.push((id.to_string(), name));
    }
    found
}

/// The WeebCentral series for these titles (the AniList ones), or None.
pub async fn weebcentral_match(
    net: &Net,
    sites: &Sites,
    titles: &[String],
) -> NetResult<Option<String>> {
    let wanted: HashSet<String> = titles
        .iter()
        .map(|t| normalize(t))
        .filter(|t| !t.is_empty())
        .collect();
    if wanted.is_empty() {
        return Ok(None);
    }
    let mut best: (f64, Option<String>) = (0.0, None);
    for title in unique(titles, 3) {
        for (id, name) in weebcentral_search(net, sites, title)
            .await?
            .into_iter()
            .take(6)
        {
            let key = normalize(&name);
            if wanted.contains(&key) {
                return Ok(Some(id));
            }
            let score = wanted
                .iter()
                .map(|w| similarity(&key, w))
                .fold(0.0, f64::max);
            if score > best.0 {
                best = (score, Some(id));
            }
        }
    }
    Ok(if best.0 >= 0.85 { best.1 } else { None })
}

/// [{id, number, label, date}] of a WeebCentral series, in reading order.
pub async fn weebcentral_chapters(
    net: &Net,
    sites: &Sites,
    series_id: &str,
) -> NetResult<Vec<Value>> {
    if !WC_ID.is_match(series_id) {
        return Err(source_error("Série WeebCentral inconnue."));
    }
    let page = fetch(
        net,
        &format!("{}/series/{series_id}/full-chapter-list", sites.weebcentral),
        &[],
    )
    .await?;
    Ok(parse_chapters(&page))
}

/// The chapter's name in its link: the first span holding text and no tag.
fn chapter_label(link: ElementRef) -> Option<String> {
    link.select(&SPANS).find_map(|span| {
        if span.children().any(|c| c.value().is_element()) {
            return None;
        }
        let text = span.text().collect::<String>().trim().to_string();
        (!text.is_empty()).then_some(text)
    })
}

/// [{id, number, label, date}] of a chapter list (newest first on the site),
/// in reading order. A link without a chapter name is not a chapter.
pub fn parse_chapters(page: &str) -> Vec<Value> {
    let document = Html::parse_fragment(page);
    let mut found = Vec::new();
    let mut seen = HashSet::new();
    for link in document.select(&LINKS) {
        let Some((id, _)) = link
            .value()
            .attr("href")
            .and_then(|h| weebcentral_link(h, "chapters"))
        else {
            continue;
        };
        let Some(label) = chapter_label(link) else {
            continue;
        };
        if !seen.insert(id.to_string()) {
            continue;
        }
        let number = NUMBER
            .find_iter(&label)
            .last()
            .map(|m| m.as_str().to_string())
            .unwrap_or_default();
        // The date in the link, else beside it in its row (an element naming
        // no other chapter).
        let row = link.parent().and_then(ElementRef::wrap).filter(|row| {
            row.select(&LINKS).all(|other| {
                other
                    .value()
                    .attr("href")
                    .and_then(|h| weebcentral_link(h, "chapters"))
                    .is_none_or(|(other_id, _)| other_id == id)
            })
        });
        let date = link
            .select(&TIMES)
            .next()
            .or_else(|| row.and_then(|r| r.select(&TIMES).next()))
            .and_then(|t| t.value().attr("datetime"))
            .map(|d| d.chars().take(10).collect::<String>())
            .unwrap_or_default();
        found.push(
            json!({"id": format!("wc:{id}"), "number": number, "label": label, "date": date}),
        );
    }
    found.reverse();
    found
}

pub async fn weebcentral_pages(
    net: &Net,
    sites: &Sites,
    chapter_id: &str,
) -> NetResult<Vec<String>> {
    if !WC_ID.is_match(chapter_id) {
        return Err(source_error("Chapitre WeebCentral inconnu."));
    }
    let page = fetch(
        net,
        &format!(
            "{}/chapters/{chapter_id}/images?is_prev=False&current_page=1&reading_style=long_strip",
            sites.weebcentral
        ),
        &[],
    )
    .await?;
    Ok(parse_pages(&page))
}

/// The page images of a chapter, each once, in order: https pictures only
/// (`src`, else a lazy `data-src`), not the site's own icons.
pub fn parse_pages(page: &str) -> Vec<String> {
    let document = Html::parse_fragment(page);
    let mut seen = HashSet::new();
    document
        .select(&IMAGES)
        .filter_map(|img| {
            let attrs = img.value();
            ["src", "data-src"]
                .iter()
                .filter_map(|k| attrs.attr(k))
                .map(str::trim)
                .find(|u| is_page_image(u))
        })
        .map(str::to_string)
        .filter(|u| seen.insert(u.clone()))
        .collect()
}

fn is_page_image(url: &str) -> bool {
    let path = url.split(['?', '#']).next().unwrap_or("");
    let kind = path
        .rsplit_once('.')
        .map_or("", |(_, ext)| ext)
        .to_ascii_lowercase();
    url.starts_with("https://")
        && !path.contains("/static/")
        && !path.contains('"')
        && IMAGE_TYPES.contains(&kind.as_str())
}

// ---------- Nyaa: whole volumes by torrent ----------

pub fn magnet(info_hash: &str, name: &str) -> String {
    let trackers: String = NYAA_TRACKERS
        .iter()
        .map(|t| {
            format!(
                "&tr={}",
                form_urlencoded::byte_serialize(t.as_bytes()).collect::<String>()
            )
        })
        .collect();
    let name: String = form_urlencoded::byte_serialize(name.as_bytes())
        .collect::<String>()
        .replace('+', "%20");
    format!("magnet:?xt=urn:btih:{info_hash}&dn={name}{trackers}")
}

/// Torrents of volumes for these titles: [{hash, name, size, seeders, date}], most seeded first.
pub async fn nyaa_volumes(
    net: &Net,
    sites: &Sites,
    titles: &[String],
    limit: usize,
) -> NetResult<Vec<Value>> {
    let wanted: Vec<String> = titles
        .iter()
        .map(|t| normalize(t))
        .filter(|t| t.len() >= 3)
        .collect();
    // In the order found, so that torrents with as many seeders keep Nyaa's order.
    let mut found: Vec<Value> = Vec::new();
    for title in unique(titles, 2) {
        let query: String = form_urlencoded::byte_serialize(title.as_bytes())
            .collect::<String>()
            .replace('+', "%20");
        // 3_1: literature translated into English, where the digital volumes are.
        let feed = fetch(
            net,
            &format!("{}&c=3_1&s=seeders&o=desc&q={query}", sites.nyaa_rss),
            &[],
        )
        .await?;
        let document = roxmltree::Document::parse(&feed)
            .map_err(|_| source_error("Nyaa ne répond pas comme prévu."))?;
        for item in document.descendants().filter(|n| n.has_tag_name("item")) {
            let child = |name: &str| {
                item.children()
                    .find(|n| n.tag_name().name() == name)
                    .and_then(|n| n.text())
                    .unwrap_or("")
                    .to_string()
            };
            let name = child("title");
            let hash = child("infoHash").to_lowercase();
            let seeders: i64 = child("seeders").trim().parse().unwrap_or(0);
            if !HASH40.is_match(&hash) || seeders <= 0 {
                continue;
            }
            let key = normalize(&name);
            if !wanted.iter().any(|w| key.contains(w.as_str())) {
                continue; // another series that shares a word
            }
            let date: String = child("pubDate").chars().skip(5).take(11).collect();
            let torrent = json!({"hash": hash, "name": name, "size": child("size"), "seeders": seeders, "date": date});
            match found.iter_mut().find(|t| t["hash"] == torrent["hash"]) {
                Some(existing) => *existing = torrent,
                None => found.push(torrent),
            }
        }
    }
    let mut list = found;
    list.sort_by_key(|t| -t["seeders"].as_i64().unwrap_or(0));
    list.truncate(limit);
    Ok(list)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn titles_and_numbers() {
        assert_eq!(normalize("Kaiju No. 8"), normalize("kaiju no 8"));
        assert_eq!(
            normalize("Frieren: Beyond Journey’s End"),
            "frierenbeyondjourneysend"
        );
        assert_eq!(number_key("012").as_deref(), Some("12"));
        assert_eq!(number_key("12.50").as_deref(), Some("12.5"));
        assert_eq!(number_key(""), None);
        assert!((similarity("abcd", "bcde") - 0.75).abs() < 1e-9);
        assert_eq!(similarity("", ""), 1.0);
    }

    /// The regular expressions WeebCentral was read with before, for comparison.
    mod former {
        use super::super::*;

        static WC_SERIES: LazyLock<Regex> = LazyLock::new(|| {
            Regex::new(r#"https://weebcentral\.com/series/([0-9A-Z]{26})/([^"'<> ]+)"#).unwrap()
        });
        static WC_ALT: LazyLock<Regex> =
            LazyLock::new(|| Regex::new(r#"alt="([^"]+?) cover"[^>]*>\s*</picture>"#).unwrap());
        static WC_ROW: LazyLock<Regex> = LazyLock::new(|| {
            Regex::new(r#"<a href="(?:https://weebcentral\.com)?/chapters/([0-9A-Z]{26})""#)
                .unwrap()
        });
        static WC_LABEL: LazyLock<Regex> =
            LazyLock::new(|| Regex::new(r#"<span class="">([^<]+)</span>"#).unwrap());
        static WC_DATE: LazyLock<Regex> =
            LazyLock::new(|| Regex::new(r#"datetime="([^"]+)""#).unwrap());
        static WC_IMAGE: LazyLock<Regex> = LazyLock::new(|| {
            Regex::new(r#"<img[^>]+src="(https://[^"]+\.(?:png|jpe?g|webp|gif|avif))""#).unwrap()
        });

        pub fn search(page: &str) -> Vec<(String, String)> {
            let mut found = Vec::new();
            let mut seen = HashSet::new();
            for capture in WC_SERIES.captures_iter(page) {
                let id = capture[1].to_string();
                if !seen.insert(id.clone()) {
                    continue;
                }
                let at = page.find(&id).unwrap_or(0);
                let window: String = page[at..].chars().take(2000).collect();
                let name = match WC_ALT.captures(&window) {
                    Some(alt) => html_escape::decode_html_entities(&alt[1]).into_owned(),
                    None => form_urlencoded::parse(format!("x={}", &capture[2]).as_bytes())
                        .next()
                        .map(|(_, v)| v.replace('-', " "))
                        .unwrap_or_default(),
                };
                found.push((id, name));
            }
            found
        }

        pub fn chapters(page: &str) -> Vec<Value> {
            let starts: Vec<usize> = WC_ROW.find_iter(page).map(|m| m.start()).collect();
            let mut found = Vec::new();
            for (index, start) in starts.iter().enumerate() {
                let block = &page[*start..starts.get(index + 1).copied().unwrap_or(page.len())];
                let (Some(id), Some(label)) = (WC_ROW.captures(block), WC_LABEL.captures(block))
                else {
                    continue;
                };
                let label = html_escape::decode_html_entities(&label[1])
                    .trim()
                    .to_string();
                let number = NUMBER
                    .find_iter(&label)
                    .last()
                    .map(|m| m.as_str().to_string())
                    .unwrap_or_default();
                let date = WC_DATE
                    .captures(block)
                    .map(|d| d[1].chars().take(10).collect::<String>())
                    .unwrap_or_default();
                found.push(json!({"id": format!("wc:{}", &id[1]), "number": number, "label": label, "date": date}));
            }
            found.reverse();
            found
        }

        pub fn pages(page: &str) -> Vec<String> {
            let mut seen = HashSet::new();
            WC_IMAGE
                .captures_iter(page)
                .map(|c| c[1].to_string())
                .filter(|u| !u.contains("/static/") && seen.insert(u.clone()))
                .collect()
        }
    }

    const SEARCH: &str = include_str!("../../tests/fixtures/weebcentral/search.html");
    const CHAPTERS: &str = include_str!("../../tests/fixtures/weebcentral/chapters.html");
    const IMAGES: &str = include_str!("../../tests/fixtures/weebcentral/images.html");

    /// On WeebCentral's own pages, the selectors read what the expressions read.
    #[test]
    fn weebcentral_pages_read_as_before() {
        let search = parse_search(SEARCH);
        assert_eq!(search, former::search(SEARCH));
        assert_eq!(
            search,
            vec![(
                "01J76XYDGDQERFSK333582BNBZ".to_string(),
                "Frieren - Beyond Journey's End".to_string()
            )]
        );
        let chapters = parse_chapters(CHAPTERS);
        assert_eq!(chapters, former::chapters(CHAPTERS));
        assert_eq!(chapters.len(), 4);
        assert_eq!(
            chapters.last().unwrap(),
            &json!({"id": "wc:01K7JP7T3V8ZVZJSGF99FFH55K", "number": "147", "label": "Chapter 147", "date": "2025-10-15"})
        );
        let pages = parse_pages(IMAGES);
        assert_eq!(pages, former::pages(IMAGES));
        assert_eq!(pages.len(), 17);
        assert!(pages[0].ends_with("/0147-001.png"));
    }

    /// The same content written differently: what the expressions missed.
    #[test]
    fn weebcentral_pages_read_whatever_the_markup() {
        let search = r#"<div><a class='card' href='/series/01J76XYDGDQERFSK333582BNBZ/Sousou-no-Frieren'>
            <picture><img decoding=async alt='Frieren &amp; Co cover' src=x.jpg><source></picture></a></div>
            <article><a href="https://weebcentral.com/series/01J76XY7E9FNDZ1DBBM6PBJPFK/One-Piece?ref=1">One Piece</a>
            <span><img alt="One Piece cover"></span></article>
            <a href="https://weebcentral.com/series/01J76XYAQSGEJPXCSCVPQ3MHZM/Two%20Words-Here">x</a>
            <a href="https://elsewhere.example/series/01J76XYAQSGEJPXCSCVPQ3MHZN/X">x</a>"#;
        assert_eq!(
            parse_search(search),
            vec![
                ("01J76XYDGDQERFSK333582BNBZ".into(), "Frieren & Co".into()),
                ("01J76XY7E9FNDZ1DBBM6PBJPFK".into(), "One Piece".into()),
                ("01J76XYAQSGEJPXCSCVPQ3MHZM".into(), "Two Words Here".into()),
            ]
        );
        let chapters = r##"<a class="row" href="https://weebcentral.com/chapters/01K7JP7T3V8ZVZJSGF99FFH55K">
              <span class="label font-bold"> Chapter 12.5 </span></a><time datetime="2025-10-15T01:02:34Z"></time>
            <div><a data-x="1" href="/chapters/01K6EFK2XZRN17SPNVFWJYCG1A"><span><img src="/static/b.svg"></span>
              <span class="grow"><span class="title">Episode 3 &amp; bonus</span></span><time datetime='2025-09-30T23:33:43Z'>x</time></a></div>
            <a href="/chapters/01K6EFK2XZRN17SPNVFWJYCG1A"><span>Chapter 3</span></a>
            <a href="#top">top</a><a href="/chapters/not-an-id"><span>Chapter 9</span></a>"##;
        assert_eq!(
            parse_chapters(chapters),
            vec![
                json!({"id": "wc:01K6EFK2XZRN17SPNVFWJYCG1A", "number": "3", "label": "Episode 3 & bonus", "date": "2025-09-30"}),
                json!({"id": "wc:01K7JP7T3V8ZVZJSGF99FFH55K", "number": "12.5", "label": "Chapter 12.5", "date": ""}),
            ]
        );
        let images = r#"<img alt='Page 1' src='https://scans.example/m/001.PNG'>
            <img loading="lazy" data-src="https://scans.example/m/002.webp?v=2" src="data:image/gif;base64,R0">
            <img src="https://weebcentral.com/static/images/x.png"><img src="https://scans.example/m/001.PNG">
            <img src="http://insecure.example/003.png"><img src="https://scans.example/page.html">"#;
        assert_eq!(
            parse_pages(images),
            vec![
                "https://scans.example/m/001.PNG",
                "https://scans.example/m/002.webp?v=2"
            ]
        );
    }

    #[test]
    fn a_magnet_with_the_trackers() {
        let link = magnet(&"a".repeat(40), "Frieren v01");
        assert!(link.starts_with(&format!(
            "magnet:?xt=urn:btih:{}&dn=Frieren%20v01&tr=",
            "a".repeat(40)
        )));
        assert_eq!(link.matches("&tr=").count(), 4);
    }
}
