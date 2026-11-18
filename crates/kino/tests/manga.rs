//! The Manga tab against stand-ins for AniList, MangaDex, WeebCentral and Nyaa:
//! the same cases as the Python version's tests.

use std::sync::Arc;

use kino::db::Db;
use kino::manga::{self, Manga, MangaError, sources::Sites};
use kino::net::Net;
use serde_json::{Value, json};
use wiremock::matchers::{body_string_contains, method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

const SERIES: &str = "b0b721ff-c388-4486-aa0f-c2b0bb321512";
const CHAPTER: &str = "6e04e431-fc6b-4e38-b952-6232c72df112";

fn media(adult: bool) -> Value {
    json!({"id": 118586, "title": {"romaji": "Sousou no Frieren", "english": "Frieren: Beyond Journey's End", "native": "葬送のフリーレン"},
           "synonyms": ["Frieren at the Funeral", "フリーレン"], "coverImage": {"extraLarge": "https://s4.anilist.co/frieren.jpg"},
           "startDate": {"year": 2020}, "status": "RELEASING", "countryOfOrigin": "JP", "genres": ["Adventure", "Fantasy"],
           "isAdult": adult, "description": "An elf mage.<br>(Source: Shogakukan)", "chapters": null, "volumes": 13,
           "staff": {"edges": [{"role": "Story", "node": {"name": {"full": "Yamada Kanehito"}}},
                               {"role": "Translator", "node": {"name": {"full": "Someone"}}}]}})
}

fn mangadex_series() -> Value {
    json!({"id": SERIES, "type": "manga", "attributes": {"description": {"en": "An elf mage.", "fr": "Une mage elfe."},
           "links": {"al": "118586"}}})
}

fn chapter(number: &str, id: &str, group: &str, external: Option<&str>) -> Value {
    json!({"id": id, "attributes": {"chapter": number, "volume": null, "title": format!("Titre {number}"), "pages": 20, "externalUrl": external},
           "relationships": [{"type": "scanlation_group", "attributes": {"name": group}}]})
}

const WC_SEARCH: &str = r#"<a href="https://weebcentral.com/series/01J76XYDGDQERFSK333582BNBZ/Sousou-no-Frieren"><picture>
  <img src="https://temp.compsci88.com/cover/fallback/x.jpg" alt="Sousou no Frieren cover"></picture></a>"#;
const WC_CHAPTERS: &str = r#"
<div x-data="{ date: '2025-10-15' }"><a href="/chapters/01K7JP7T3V8ZVZJSGF99FFH55K" class="x"><span class="">Chapter 3</span>
 <time datetime="2025-10-15T10:00:00.000Z"></time></a></div>
<div><a href="/chapters/01K7JP7T3V8ZVZJSGF99FFH55J" class="x"><span class="">Chapter 2</span><time datetime="2025-10-08T10:00:00Z"></time></a></div>
<div><a href="/chapters/01K7JP7T3V8ZVZJSGF99FFH55I" class="x"><span class="">Chapter 1</span><time datetime="2025-10-01T10:00:00Z"></time></a></div>"#;
const WC_IMAGES: &str = r#"<img src="/static/images/x.svg"><img src="https://scans-hot.planeptune.us/manga/Frieren/0003-001.png" alt="Page 1">
<img src="https://scans-hot.planeptune.us/manga/Frieren/0003-002.png" alt="Page 2">"#;
const NYAA: &str = r#"<?xml version="1.0" encoding="utf-8"?><rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0"><channel>
<item><title>Frieren - Beyond Journey's End v01-13 (Digital) (1r0n)</title><nyaa:infoHash>AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</nyaa:infoHash>
 <nyaa:seeders>12</nyaa:seeders><nyaa:size>2.1 GiB</nyaa:size><pubDate>Wed, 01 Oct 2026 10:00:00 -0000</pubDate></item>
<item><title>Frieren v01 (Dead)</title><nyaa:infoHash>bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb</nyaa:infoHash><nyaa:seeders>0</nyaa:seeders></item>
<item><title>Another Mage Story v01</title><nyaa:infoHash>cccccccccccccccccccccccccccccccccccccccc</nyaa:infoHash><nyaa:seeders>50</nyaa:seeders></item>
</channel></rss>"#;

struct World {
    _dir: tempfile::TempDir,
    _server: MockServer,
    net: Net,
    sites: Sites,
}

impl World {
    fn manga(&self) -> Manga<'_> {
        Manga {
            net: &self.net,
            sites: &self.sites,
        }
    }
}

async fn world(adult: bool, weebcentral_up: bool) -> World {
    let server = MockServer::start().await;
    let ok = |body: Value| ResponseTemplate::new(200).set_body_json(body);
    Mock::given(method("POST"))
        .and(path("/anilist"))
        .and(body_string_contains("Media(id"))
        .respond_with(ok(json!({"data": {"Media": media(adult)}})))
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/anilist"))
        .respond_with(ok(json!({"data": {"Page": {"media": [media(false)]}}})))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/md/manga"))
        .respond_with(ok(json!({"data": [mangadex_series()]})))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path(format!("/md/manga/{SERIES}")))
        .respond_with(ok(json!({"data": mangadex_series()})))
        .mount(&server)
        .await;
    Mock::given(method("GET")).and(path(format!("/md/manga/{SERIES}/feed"))).and(query_param("translatedLanguage[]", "en"))
        .respond_with(ok(json!({"total": 1, "data": [chapter("4", "00000000-0000-0000-0000-000000000004", "EN team", None)]})))
        .mount(&server).await;
    Mock::given(method("GET")).and(path(format!("/md/manga/{SERIES}/feed")))
        .respond_with(ok(json!({"total": 4, "data": [
            chapter("1", "00000000-0000-0000-0000-000000000001", "Blue Solo", None),
            chapter("1", "00000000-0000-0000-0000-000000000002", "Autre équipe", None),
            chapter("2", "00000000-0000-0000-0000-000000000003", "Blue Solo", Some("https://mangaplus.example/2")),
            chapter("5", CHAPTER, "Blue Solo", None)]})))
        .mount(&server).await;
    Mock::given(method("GET")).and(path(format!("/md/at-home/server/{CHAPTER}")))
        .respond_with(ok(json!({"baseUrl": "https://abc.mangadex.network", "chapter": {"hash": "h1", "data": ["1.png", "2.png"]}})))
        .mount(&server).await;
    let wc_status = if weebcentral_up { 200 } else { 503 };
    Mock::given(path("/wc/search/data"))
        .respond_with(ResponseTemplate::new(wc_status).set_body_string(WC_SEARCH))
        .mount(&server)
        .await;
    Mock::given(path(
        "/wc/series/01J76XYDGDQERFSK333582BNBZ/full-chapter-list",
    ))
    .respond_with(ResponseTemplate::new(wc_status).set_body_string(WC_CHAPTERS))
    .mount(&server)
    .await;
    Mock::given(path("/wc/chapters/01K7JP7T3V8ZVZJSGF99FFH55K/images"))
        .respond_with(ResponseTemplate::new(200).set_body_string(WC_IMAGES))
        .mount(&server)
        .await;
    Mock::given(path("/nyaa"))
        .respond_with(ResponseTemplate::new(200).set_body_string(NYAA))
        .mount(&server)
        .await;

    let dir = tempfile::tempdir().unwrap();
    let db = Arc::new(Db::open(&dir.path().join("kino.db")).unwrap());
    let base = server.uri();
    let sites = Sites {
        anilist: format!("{base}/anilist"),
        mangadex: format!("{base}/md"),
        weebcentral: format!("{base}/wc"),
        nyaa_rss: format!("{base}/nyaa?page=rss"),
    };
    World {
        _dir: dir,
        _server: server,
        net: Net::new(db),
        sites,
    }
}

#[tokio::test]
async fn search_and_rows() {
    let w = world(false, true).await;
    let found = w.manga().search("frieren").await.unwrap();
    assert_eq!(
        found,
        vec![
            json!({"id": "118586", "source": "anilist", "title": "Frieren: Beyond Journey's End",
        "cover": "https://s4.anilist.co/frieren.jpg", "year": 2020, "status": "releasing", "kind": "JP"})
        ]
    );
    assert!(w.manga().search("  ").await.unwrap().is_empty());
    assert_eq!(w.manga().row("trending").await.unwrap().len(), 1);
    assert!(matches!(
        w.manga().row("everything").await,
        Err(MangaError::Refused(_))
    ));
}

#[tokio::test]
async fn a_series_page() {
    let w = world(false, true).await;
    let info = w.manga().details("118586", "fr").await.unwrap();
    assert_eq!(
        info["description"], "Une mage elfe.",
        "the French summary from MangaDex"
    );
    assert_eq!(info["authors"], json!(["Yamada Kanehito"]));
    assert_eq!(
        (info["tags"].clone(), info["volumes_total"].clone()),
        (json!(["Adventure", "Fantasy"]), json!(13))
    );
    assert_eq!(
        info["titles"],
        json!([
            "Frieren: Beyond Journey's End",
            "Sousou no Frieren",
            "Frieren at the Funeral"
        ])
    );
    assert_eq!(
        w.manga().details("118586", "en").await.unwrap()["description"],
        "An elf mage.",
        "AniList's, without markup or source"
    );
    for bad in ["", "../../etc", "12a"] {
        assert!(
            matches!(
                w.manga().details(bad, "fr").await,
                Err(MangaError::Refused(_))
            ),
            "{bad}"
        );
    }
    assert_eq!(
        w.manga().details(SERIES, "en").await.unwrap()["id"],
        "118586",
        "a series opened from MangaDex before"
    );
    let adult = world(true, true).await;
    assert!(matches!(
        adult.manga().details("118586", "fr").await,
        Err(MangaError::Refused(_))
    ));
}

#[tokio::test]
async fn every_chapter_once_in_the_reading_language_first() {
    let w = world(false, true).await;
    let list = w.manga().chapters("118586", "fr").await.unwrap();
    let seen: Vec<(String, String, String)> = list
        .iter()
        .map(|c| {
            (
                c["number"].as_str().unwrap().into(),
                c["source"].as_str().unwrap().into(),
                c["lang"].as_str().unwrap().into(),
            )
        })
        .collect();
    let want = [
        ("1", "MangaDex", "fr"),
        ("2", "WeebCentral", "en"),
        ("3", "WeebCentral", "en"),
        ("4", "MangaDex", "en"),
        ("5", "MangaDex", "fr"),
    ];
    assert_eq!(
        seen,
        want.map(|(a, b, c)| (a.to_string(), b.to_string(), c.to_string()))
    );
    assert_eq!(list[0]["group"], "Blue Solo");
    assert_eq!(
        (list[2]["id"].clone(), list[2]["date"].clone()),
        (json!("wc:01K7JP7T3V8ZVZJSGF99FFH55K"), json!("2025-10-15"))
    );
    let english: Vec<(String, String)> = w
        .manga()
        .chapters("118586", "en")
        .await
        .unwrap()
        .iter()
        .map(|c| {
            (
                c["number"].as_str().unwrap().into(),
                c["source"].as_str().unwrap().into(),
            )
        })
        .collect();
    assert_eq!(
        english,
        [
            ("1", "WeebCentral"),
            ("2", "WeebCentral"),
            ("3", "WeebCentral"),
            ("4", "MangaDex")
        ]
        .map(|(a, b)| (a.to_string(), b.to_string()))
    );
}

#[tokio::test]
async fn one_site_down_the_others_still_answer() {
    let w = world(false, false).await;
    let numbers: Vec<String> = w
        .manga()
        .chapters("118586", "fr")
        .await
        .unwrap()
        .iter()
        .map(|c| c["number"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(numbers, ["1", "2", "4", "5"]);
}

#[tokio::test]
async fn the_pages_of_a_chapter_and_the_volumes() {
    let w = world(false, true).await;
    assert_eq!(
        w.manga().pages(&format!("md:{CHAPTER}")).await.unwrap(),
        [
            "https://abc.mangadex.network/data/h1/1.png",
            "https://abc.mangadex.network/data/h1/2.png"
        ]
    );
    assert_eq!(
        w.manga()
            .pages("wc:01K7JP7T3V8ZVZJSGF99FFH55K")
            .await
            .unwrap(),
        [
            "https://scans-hot.planeptune.us/manga/Frieren/0003-001.png",
            "https://scans-hot.planeptune.us/manga/Frieren/0003-002.png"
        ]
    );
    for bad in ["wc:../x", "md:nope", "zz:1"] {
        assert!(
            matches!(w.manga().pages(bad).await, Err(MangaError::Refused(_))),
            "{bad}"
        );
    }
    let torrents = kino::manga::sources::nyaa_volumes(
        &w.net,
        &w.sites,
        &["Frieren: Beyond Journey's End".into(), "Frieren".into()],
        12,
    )
    .await
    .unwrap();
    assert_eq!(
        torrents,
        vec![
            json!({"hash": "a".repeat(40), "name": "Frieren - Beyond Journey's End v01-13 (Digital) (1r0n)",
        "size": "2.1 GiB", "seeders": 12, "date": "01 Oct 2026"})
        ],
        "no dead torrent, no other series"
    );
    assert_eq!(
        kino::manga::sources::weebcentral_match(
            &w.net,
            &w.sites,
            &["Completely Different Title".into()]
        )
        .await
        .unwrap(),
        None
    );
}

#[tokio::test]
async fn where_the_reader_stopped() {
    let w = world(false, true).await;
    let db = w.net.db();
    manga::save_progress(db, &json!({"source": "mangadex", "series": SERIES, "title": "Frieren", "chapter": CHAPTER, "number": "3", "page": 7, "pages": 20, "lang": "fr"})).unwrap();
    manga::save_progress(db, &json!({"source": "local", "series": "a".repeat(20), "title": "One Piece", "chapter": "1", "page": "2"})).unwrap();
    manga::save_progress(db, &json!({"source": "anilist", "series": "118586", "title": "Frieren", "chapter": "wc:01K7JP7T3V8ZVZJSGF99FFH55K", "page": 3})).unwrap();
    let seen: Vec<(String, i64)> = manga::progress(db)
        .iter()
        .map(|p| {
            (
                p["source"].as_str().unwrap().into(),
                p["page"].as_i64().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        seen,
        [("anilist", 3), ("local", 2), ("mangadex", 7)].map(|(a, b)| (a.to_string(), b))
    );
    for bad in [
        json!({"source": "web", "series": SERIES}),
        json!({"source": "mangadex", "series": "x"}),
        json!({"source": "local", "series": "../x"}),
        json!({"source": "anilist", "series": "1; drop"}),
    ] {
        assert!(manga::save_progress(db, &bad).is_err(), "{bad}");
    }
    manga::forget(db, "local", &"a".repeat(20));
    let left: Vec<String> = manga::progress(db)
        .iter()
        .map(|p| p["source"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(left, ["anilist", "mangadex"]);
}

/// WeebCentral's own answers (extracts kept in tests/fixtures/weebcentral):
/// the series found by its title, its chapters in reading order, a chapter's pages.
#[tokio::test]
async fn weebcentral_as_it_answers() {
    let server = MockServer::start().await;
    let fixture = |name: &str| {
        std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join(format!("tests/fixtures/weebcentral/{name}")),
        )
        .unwrap()
    };
    Mock::given(path("/wc/search/data"))
        .and(query_param("text", "Sousou no Frieren"))
        .respond_with(ResponseTemplate::new(200).set_body_string(fixture("search.html")))
        .mount(&server)
        .await;
    Mock::given(path(
        "/wc/series/01J76XYDGDQERFSK333582BNBZ/full-chapter-list",
    ))
    .respond_with(ResponseTemplate::new(200).set_body_string(fixture("chapters.html")))
    .mount(&server)
    .await;
    Mock::given(path("/wc/chapters/01K7JP7T3V8ZVZJSGF99FFH55K/images"))
        .respond_with(ResponseTemplate::new(200).set_body_string(fixture("images.html")))
        .mount(&server)
        .await;
    let dir = tempfile::tempdir().unwrap();
    let net = Net::new(Arc::new(Db::open(&dir.path().join("kino.db")).unwrap()));
    let base = server.uri();
    let sites = Sites {
        anilist: format!("{base}/anilist"),
        mangadex: format!("{base}/md"),
        weebcentral: format!("{base}/wc"),
        nyaa_rss: format!("{base}/nyaa?page=rss"),
    };
    use kino::manga::sources::{weebcentral_chapters, weebcentral_match, weebcentral_pages};
    let titles = vec![
        "Sousou no Frieren".to_string(),
        "Frieren: Beyond Journey's End".to_string(),
    ];
    let series = weebcentral_match(&net, &sites, &titles).await.unwrap();
    assert_eq!(series.as_deref(), Some("01J76XYDGDQERFSK333582BNBZ"));
    let chapters = weebcentral_chapters(&net, &sites, "01J76XYDGDQERFSK333582BNBZ")
        .await
        .unwrap();
    let numbers: Vec<&str> = chapters
        .iter()
        .map(|c| c["number"].as_str().unwrap())
        .collect();
    let newest = chapters.last().unwrap();
    assert_eq!(
        numbers.first(),
        Some(&"1"),
        "reading order: the oldest first"
    );
    assert_eq!(numbers[numbers.len() - 3..], ["145", "146", "147"]);
    assert_eq!(newest["id"], "wc:01K7JP7T3V8ZVZJSGF99FFH55K");
    assert_eq!(newest["date"], "2025-10-15");
    let pages = weebcentral_pages(&net, &sites, "01K7JP7T3V8ZVZJSGF99FFH55K")
        .await
        .unwrap();
    assert_eq!(pages.len(), 17);
    assert!(
        pages
            .iter()
            .all(|p| p.starts_with("https://") && p.ends_with(".png"))
    );
    assert!(
        weebcentral_chapters(&net, &sites, "../etc").await.is_err(),
        "not a series id"
    );
}
