//! A magnet's debrid link against stand-ins for Real-Debrid and AllDebrid.

use std::sync::Arc;

use kino::db::Db;
use kino::net::Net;
use kino::resolve::{Hosts, Resolver};
use serde_json::json;
use wiremock::matchers::{body_string_contains, header, method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

const MAGNET: &str = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567";

fn net() -> (tempfile::TempDir, Net) {
    let dir = tempfile::tempdir().unwrap();
    let db = Arc::new(Db::open(&dir.path().join("kino.db")).unwrap());
    (dir, Net::new(db))
}

fn hosts(base: &str) -> Hosts {
    Hosts {
        realdebrid: format!("{base}/rd"),
        alldebrid: format!("{base}/ad"),
        ..Hosts::default()
    }
}

#[tokio::test]
async fn real_debrid_adds_selects_waits_and_unrestricts_the_episode_first() {
    let server = MockServer::start().await;
    let ok = |v| ResponseTemplate::new(200).set_body_json(v);
    Mock::given(method("POST"))
        .and(path("/rd/torrents/addMagnet"))
        .and(header("authorization", "Bearer KEY"))
        .and(body_string_contains("magnet=magnet"))
        .respond_with(ok(json!({"id": "T1"})))
        .mount(&server)
        .await;
    Mock::given(method("GET")).and(path("/rd/torrents/info/T1"))
        .respond_with(ok(json!({"id": "T1", "status": "downloaded", "links": ["https://rd/l1", "https://rd/l2"],
            "files": [{"id": 1, "path": "/Show.S01E01.mkv", "bytes": 900_000_000}, {"id": 2, "path": "/Show.S01E02.mkv", "bytes": 900_000_000},
                      {"id": 3, "path": "/sample.txt", "bytes": 10}]})))
        .mount(&server).await;
    Mock::given(method("POST"))
        .and(path("/rd/torrents/selectFiles/T1"))
        .and(body_string_contains("files=1%2C2"))
        .respond_with(ResponseTemplate::new(204))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST")).and(path("/rd/unrestrict/link")).and(body_string_contains("l1"))
        .respond_with(ok(json!({"filename": "Show.S01E01.mkv", "filesize": 900000000, "download": "https://dl/1"}))).mount(&server).await;
    Mock::given(method("POST")).and(path("/rd/unrestrict/link")).and(body_string_contains("l2"))
        .respond_with(ok(json!({"filename": "Show.S01E02.mkv", "filesize": 900000000, "download": "https://dl/2"}))).mount(&server).await;
    let (_d, net) = net();
    let resolver = Resolver {
        net: &net,
        provider: "realdebrid".into(),
        token: "KEY".into(),
        hosts: hosts(&server.uri()),
    };
    let answer = resolver
        .magnet(MAGNET, Some(1), Some(2), None)
        .await
        .unwrap();
    assert_eq!(answer["ready"], true);
    assert_eq!(answer["torrent_id"], "T1");
    assert_eq!(
        answer["files"][0],
        json!({"filename": "Show.S01E02.mkv", "filesize": "858.31 MB", "filesize_bytes": 900000000,
                                          "download": "https://dl/2", "is_target_ep": true}),
        "the wanted episode first"
    );
    assert_eq!(answer["files"][1]["is_target_ep"], false);
}

#[tokio::test]
async fn real_debrid_refuses_a_dead_torrent_and_reports_one_in_progress() {
    let server = MockServer::start().await;
    let ok = |v| ResponseTemplate::new(200).set_body_json(v);
    Mock::given(method("POST"))
        .and(path("/rd/torrents/addMagnet"))
        .respond_with(ok(json!({"id": "T2"})))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/rd/torrents/info/T2"))
        .respond_with(ok(json!({"status": "dead", "files": []})))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/rd/torrents/info/T3"))
        .respond_with(ok(
            json!({"status": "downloading", "progress": 42, "speed": 2097152, "seeders": 7}),
        ))
        .mount(&server)
        .await;
    let (_d, net) = net();
    let resolver = Resolver {
        net: &net,
        provider: "realdebrid".into(),
        token: "KEY".into(),
        hosts: hosts(&server.uri()),
    };
    let error = resolver.magnet(MAGNET, None, None, None).await.unwrap_err();
    assert_eq!(error.0, "Real-Debrid a refusé ce torrent (statut : dead)");
    let status = resolver.check("T3", None, None).await.unwrap();
    assert_eq!(
        status,
        json!({"ready": false, "torrent_id": "T3", "status": "downloading", "progress": 42, "speed": "2.00 MB/s", "seeders": 7, "files": []})
    );
    let missing = Resolver {
        net: &net,
        provider: "torbox".into(),
        token: String::new(),
        hosts: hosts(&server.uri()),
    };
    assert_eq!(
        missing
            .magnet(MAGNET, None, None, None)
            .await
            .unwrap_err()
            .0,
        "Clé API TorBox manquante. Cliquez sur 'Config' en haut à droite."
    );
}

#[tokio::test]
async fn alldebrid_unlocks_the_files_once_ready() {
    let server = MockServer::start().await;
    let ok = |v| ResponseTemplate::new(200).set_body_json(v);
    Mock::given(method("POST"))
        .and(path("/ad/magnet/upload"))
        .and(query_param("apikey", "AD"))
        .respond_with(ok(json!({"data": {"magnets": [{"id": 99}]}})))
        .mount(&server)
        .await;
    Mock::given(method("GET")).and(path("/ad/magnet/status")).and(query_param("id", "99"))
        .respond_with(ok(json!({"data": {"magnets": {"id": 99, "statusCode": 4,
            "links": [{"filename": "Film.2024.mkv", "size": 4_000_000_000_i64, "link": "https://ad/raw"}, {"filename": "notes.nfo", "size": 1, "link": "https://ad/nfo"}]}}})))
        .mount(&server).await;
    Mock::given(method("GET"))
        .and(path("/ad/link/unlock"))
        .and(query_param("link", "https://ad/raw"))
        .respond_with(ok(json!({"data": {"link": "https://dl/film"}})))
        .mount(&server)
        .await;
    let (_d, net) = net();
    let resolver = Resolver {
        net: &net,
        provider: "alldebrid".into(),
        token: "AD".into(),
        hosts: hosts(&server.uri()),
    };
    let answer = resolver.magnet(MAGNET, None, None, None).await.unwrap();
    assert_eq!(answer["torrent_id"], "ad:99");
    assert_eq!(
        answer["files"],
        json!([{"filename": "Film.2024.mkv", "filesize": "3.73 GB", "filesize_bytes": 4000000000_i64,
                                        "download": "https://dl/film", "is_target_ep": false}]),
        "the .nfo is left out"
    );
}
