//! The Cloud: release names parsed and grouped as the Python version does
//! (tools/make_cloud_fixtures.py), and the account's list, deletion and
//! cleanup against a stand-in for Real-Debrid.

use std::io::Read;
use std::sync::Arc;

use chrono::NaiveDate;
use kino::cloud::{Account, best_match, group_items, parse_release};
use kino::db::Db;
use kino::net::Net;
use kino::resolve::Hosts;
use serde_json::{Value, json};
use wiremock::matchers::{header, method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn fixture() -> Value {
    let raw = std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/cloud.json.gz"
    ))
    .unwrap();
    let mut text = String::new();
    flate2::read::GzDecoder::new(&raw[..])
        .read_to_string(&mut text)
        .unwrap();
    serde_json::from_str(&text).unwrap()
}

#[test]
fn release_names_groups_and_matches_as_python() {
    let data = fixture();
    let today = NaiveDate::parse_from_str(data["today"].as_str().unwrap(), "%Y-%m-%d").unwrap();
    let mut wrong = Vec::new();
    for case in data["parsed"].as_array().unwrap() {
        let name = case[0].as_str().unwrap();
        if parse_release(name, today) != case[1] {
            wrong.push(format!(
                "{name}\n  python {}\n  rust   {}",
                case[1],
                parse_release(name, today)
            ));
        }
    }
    assert!(
        wrong.is_empty(),
        "{} of {} names differ:\n{}",
        wrong.len(),
        data["parsed"].as_array().unwrap().len(),
        wrong[..wrong.len().min(8)].join("\n")
    );
    for (index, case) in data["groups"].as_array().unwrap().iter().enumerate() {
        assert_eq!(
            Value::Array(group_items(case[0].as_array().unwrap(), today)),
            case[1],
            "grouping {index}"
        );
    }
    let metas = data["metas"].as_array().unwrap();
    for case in data["matches"].as_array().unwrap() {
        let found = best_match(metas, case[0].as_str().unwrap(), case[1].as_str().unwrap())
            .cloned()
            .unwrap_or(Value::Null);
        assert_eq!(found, case[2], "match for {}", case[0]);
    }
}

fn account<'a>(net: &'a Net, server: &MockServer) -> Account<'a> {
    Account {
        net,
        provider: "realdebrid".into(),
        token: "FAKE".into(),
        hosts: Hosts {
            realdebrid: format!("{}/rd", server.uri()),
            ..Hosts::default()
        },
        port: 8080,
    }
}

#[tokio::test]
async fn real_debrid_list_delete_and_cleanup() {
    let server = MockServer::start().await;
    let ok = |v| ResponseTemplate::new(200).set_body_json(v);
    let recent = chrono::Utc::now().to_rfc3339();
    Mock::given(method("GET"))
        .and(path("/rd/downloads"))
        .and(header("authorization", "Bearer FAKE"))
        .and(query_param("limit", "100"))
        .respond_with(ok(json!([
            {"id": "A", "filename": "The.Bear.S03E05.1080p.WEB.h264-ETHEL.mkv", "filesize": 900000000, "download": "https://dl/a", "generated": "2026-01-01T10:00:00.000Z"},
            {"id": "B", "filename": "The.Bear.S03E05.1080p.WEB.h264-ETHEL.mkv", "filesize": 900000000, "download": "https://dl/b", "generated": recent},
            {"id": "C", "filename": "Dune.2021.2160p.mkv", "filesize": 30000000000_i64, "download": "https://dl/c", "generated": recent},
            {"id": "", "filename": "nameless.mkv", "download": "https://dl/x"}
        ])))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/rd/torrents"))
        .respond_with(ok(json!([{"id": "T1", "added": "2025-12-01T00:00:00.000Z"}, {"id": "T2", "added": recent}])))
        .mount(&server)
        .await;
    for id in ["A", "B", "C"] {
        Mock::given(method("DELETE"))
            .and(path(format!("/rd/downloads/delete/{id}")))
            .respond_with(ResponseTemplate::new(204))
            .mount(&server)
            .await;
    }
    Mock::given(method("DELETE"))
        .and(path("/rd/torrents/delete/T1"))
        .respond_with(ResponseTemplate::new(204))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("DELETE"))
        .and(path("/rd/torrents/delete/T2"))
        .respond_with(ResponseTemplate::new(204))
        .expect(0)
        .mount(&server)
        .await;

    let dir = tempfile::tempdir().unwrap();
    let net = Net::new(Arc::new(Db::open(&dir.path().join("kino.db")).unwrap()));
    let cloud = account(&net, &server);
    let items = cloud.downloads(100).await.unwrap();
    assert_eq!(
        items.len(),
        2,
        "one entry per file name, nameless ones left out"
    );
    assert_eq!(items[0]["ids"], json!(["A", "B"]));
    assert_eq!(items[1]["filesize"], "27.94 GB");
    let groups = group_items(&items, NaiveDate::from_ymd_opt(2026, 11, 17).unwrap());
    assert_eq!(groups[0]["title"], "The Bear");
    assert_eq!(groups[0]["seasons"], json!([3]));
    assert_eq!(groups[1]["key"], "movie:dune:2021");

    assert_eq!(
        cloud.delete(&[json!("A"), json!(""), json!("C")]).await,
        2,
        "an empty id is skipped"
    );
    let cleaned = cloud.cleanup(30).await;
    assert_eq!(
        cleaned,
        json!({"deleted_downloads": 1, "deleted_torrents": 1}),
        "only what is older than 30 days"
    );
    assert_eq!(
        cloud.cleanup(0).await,
        json!({"deleted_downloads": 0, "deleted_torrents": 0}),
        "0 keeps everything"
    );
    let none = Account {
        token: String::new(),
        ..account(&net, &server)
    };
    assert!(none.downloads(100).await.unwrap().is_empty());
}
