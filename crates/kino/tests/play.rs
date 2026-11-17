//! « Lecture en un clic »: the source keys, the right file, the sources tried
//! in order against a stand-in for Real-Debrid, the playlist and the resume point.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use kino::db::Db;
use kino::meta::Meta;
use kino::net::Net;
use kino::play::{self, Episode, EpisodeLinks};
use kino::resolve::{Hosts, Resolver};
use kino::sources::search::Query;
use serde_json::{Map, Value, json};
use wiremock::matchers::{body_string_contains, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn meta(dir: &tempfile::TempDir, cinemeta: &str) -> Meta {
    let db = Arc::new(Db::open(&dir.path().join("kino.db")).unwrap());
    Meta {
        net: Arc::new(Net::new(db)),
        cinemeta: cinemeta.into(),
        translate: false,
    }
}

fn query(pairs: &[(&str, &str)]) -> Query {
    let params: HashMap<String, String> = pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
    Query::from_params(&params, &Map::new())
}

fn file(name: &str, bytes: i64) -> Value {
    json!({"filename": name, "filesize_bytes": bytes, "download": format!("https://dl/{name}"), "is_target_ep": false})
}

#[test]
fn source_keys_match_the_python_server() {
    assert_eq!(
        play::candidate_key(&json!({"info_hash": "abcdef", "magnet": "m"})),
        "bef57ec7f53a6d40beb640a780a639c8"
    );
    assert_eq!(
        play::candidate_key(&json!({"info_hash": "", "magnet": "magnet:?xt=1"})),
        "f0869f1fef95c43d810161ef27943657"
    );
    assert_eq!(
        play::candidate_key(&json!({"source": "Torrentio", "title": "Film 2024"})),
        "31298eb8e517942fecfa95f15ecaa5b1"
    );
    let body = json!({"exclude_source_keys": ["bef57ec7f53a6d40beb640a780a639c8", "nope", 3]});
    assert_eq!(
        play::set_aside(&body),
        HashSet::from(["bef57ec7f53a6d40beb640a780a639c8".to_string()])
    );
}

#[tokio::test]
async fn the_wanted_episode_or_the_film_and_nothing_else() {
    let dir = tempfile::tempdir().unwrap();
    let meta = meta(&dir, "http://127.0.0.1:9");
    let series = query(&[
        ("imdb_id", "tt1"),
        ("type", "series"),
        ("season", "1"),
        ("episode", "2"),
        ("title", "Show"),
    ]);
    let files = [
        file("Show.S01E01.mkv", 9),
        file("Show.S01E02.mkv", 5),
        file("Show.S01E02.sample.mkv", 1),
    ];
    let chosen = play::select_file(&meta, &series, &files, &[])
        .await
        .unwrap();
    assert_eq!(
        chosen["filename"], "Show.S01E02.mkv",
        "the biggest file of the episode"
    );
    assert!(
        play::select_file(&meta, &series, &[file("Show.S01E05.mkv", 9)], &[])
            .await
            .is_none()
    );

    let film = query(&[
        ("imdb_id", "tt2"),
        ("type", "movie"),
        ("title", "Dune"),
        ("year", "2021"),
    ]);
    let chosen = play::select_file(
        &meta,
        &film,
        &[file("Dune.2021.2160p.mkv", 9), file("Extras.mkv", 99)],
        &[],
    )
    .await
    .unwrap();
    assert_eq!(chosen["filename"], "Dune.2021.2160p.mkv");
    assert!(
        play::select_file(&meta, &film, &[file("Other.Film.1999.mkv", 9)], &[])
            .await
            .is_none(),
        "another title"
    );
    let generic = play::select_file(&meta, &film, &[file("video.mp4", 9)], &[]).await;
    assert_eq!(
        generic.unwrap()["filename"],
        "video.mp4",
        "a generic name says nothing"
    );
    let link = json!({"filename": "Dune.2021.mkv", "download": "magnet:x"});
    assert!(
        play::select_file(&meta, &film, &[link], &[])
            .await
            .is_none(),
        "only web links play"
    );
}

#[tokio::test]
async fn sources_are_tried_in_order_past_the_set_aside_and_the_failed() {
    let server = MockServer::start().await;
    let ok = |v| ResponseTemplate::new(200).set_body_json(v);
    Mock::given(method("POST"))
        .and(path("/rd/torrents/addMagnet"))
        .and(body_string_contains("bbbb"))
        .respond_with(ResponseTemplate::new(503).set_body_string("down"))
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/rd/torrents/addMagnet"))
        .and(body_string_contains("cccc"))
        .respond_with(ok(json!({"id": "T3"})))
        .mount(&server)
        .await;
    Mock::given(method("GET")).and(path("/rd/torrents/info/T3"))
        .respond_with(ok(json!({"status": "downloaded", "links": ["https://rd/l1"], "files": [{"id": 1, "path": "/Show.S01E02.mkv", "bytes": 900_000_000}]})))
        .mount(&server).await;
    Mock::given(method("POST"))
        .and(path("/rd/torrents/selectFiles/T3"))
        .respond_with(ResponseTemplate::new(204))
        .mount(&server)
        .await;
    Mock::given(method("POST")).and(path("/rd/unrestrict/link"))
        .respond_with(ok(json!({"filename": "Show.S01E02.mkv", "filesize": 900000000, "download": "https://dl/e2"}))).mount(&server).await;

    let dir = tempfile::tempdir().unwrap();
    let meta = meta(&dir, "http://127.0.0.1:9");
    let resolver = Resolver {
        net: &meta.net,
        provider: "realdebrid".into(),
        token: "FAKE".into(),
        hosts: Hosts {
            realdebrid: format!("{}/rd", server.uri()),
            ..Hosts::default()
        },
    };
    let candidate = |h: &str| json!({"info_hash": h.repeat(10), "magnet": format!("magnet:?xt=urn:btih:{}", h.repeat(10)), "title": "Show S01E02"});
    let sources = [candidate("aaaa"), candidate("bbbb"), candidate("cccc")];
    let skip = HashSet::from([play::candidate_key(&sources[0])]);
    let series = query(&[
        ("imdb_id", "tt1"),
        ("type", "series"),
        ("season", "1"),
        ("episode", "2"),
        ("title", "Show"),
    ]);
    let attempts = play::try_sources(
        &meta,
        &resolver,
        &sources,
        &series,
        Some((1, 2)),
        |_| vec![],
        &skip,
    )
    .await;
    let found = attempts.found.expect("the third source plays");
    assert_eq!(found.file["download"], "https://dl/e2");
    assert_eq!(found.key, play::candidate_key(&sources[2]));
    assert_eq!(
        attempts.tried,
        vec![
            play::candidate_key(&sources[1]),
            play::candidate_key(&sources[2])
        ]
    );
    assert!(
        attempts.last_error.is_some(),
        "the failed source is reported"
    );
}

#[tokio::test]
async fn the_next_episodes_go_through_auto_stream_and_reuse_found_links() {
    let dir = tempfile::tempdir().unwrap();
    let meta = meta(&dir, "http://127.0.0.1:9");
    let links = EpisodeLinks::default();
    let items = play::season_playlist(
        &meta,
        &links,
        8080,
        "tt9",
        1,
        3,
        "https://dl/e3",
        "Show",
        "2020",
        "",
        "abc",
        "Show.S01E03.mkv",
    )
    .await;
    assert_eq!(
        items[0],
        json!({"title": "Show — S01E03", "url": "https://dl/e3"})
    );
    assert_eq!(
        items.len(),
        1 + 14,
        "without the episode list, the next fourteen"
    );
    assert_eq!(
        items[1]["url"],
        "http://127.0.0.1:8080/api/auto-stream?imdb_id=tt9&season=1&episode=4&name=Show&year=2020&poster=&pref_hash=abc"
    );

    let mut cfg = Map::new();
    cfg.insert("rd_token".into(), json!("FAKE"));
    let params: HashMap<String, String> = [
        ("imdb_id", "tt9"),
        ("season", "1"),
        ("episode", "3"),
        ("name", "Show"),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v.to_string()))
    .collect();
    let link = play::resolve_episode(
        &meta,
        &links,
        &Episode::from_params(&params),
        "",
        &cfg,
        true,
    )
    .await
    .unwrap();
    assert_eq!(
        link, "https://dl/e3",
        "the link found for the first episode"
    );
    let history = kino::library::history(meta.net.db());
    assert_eq!(history[0]["id"], "tt9");
    assert_eq!(history[0]["episode"], 3);
}

#[test]
fn resume_point_between_one_and_85_percent() {
    let history = [
        json!({"id": "tt1", "progress_pct": 40.0, "position": 900, "season": 1, "episode": 2,
                          "ep_positions": {"S01E03": {"pct": 90.0, "pos": 2000}, "S01E04": {"pct": 30.0, "pos": 600}}}),
    ];
    assert_eq!(
        play::resume_position(&history, "tt1", Some(1), Some(4)),
        600
    );
    assert_eq!(
        play::resume_position(&history, "tt1", Some(1), Some(3)),
        0,
        "nearly finished: from the start"
    );
    assert_eq!(
        play::resume_position(&history, "tt1", Some(1), Some(2)),
        900
    );
    assert_eq!(play::resume_position(&history, "tt1", Some(1), Some(5)), 0);
    assert_eq!(play::resume_position(&history, "tt2", None, None), 0);
}
