//! « Nouveaux épisodes », « Mes séries » and the « Calendrier » give what the
//! Python version gave for the same histories and episode lists
//! (tools/make_series_fixtures.py), the lists served by a stand-in for Cinemeta.

use std::io::Read;
use std::sync::Arc;

use kino::db::Db;
use kino::meta::Meta;
use kino::net::Net;
use kino::series;
use serde_json::{Value, json};
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn fixture() -> Value {
    let raw = std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/series.json.gz"
    ))
    .unwrap();
    let mut text = String::new();
    flate2::read::GzDecoder::new(&raw[..])
        .read_to_string(&mut text)
        .unwrap();
    serde_json::from_str(&text).unwrap()
}

#[tokio::test]
async fn followed_series_match_the_python_version() {
    let data = fixture();
    let server = MockServer::start().await;
    let cases = data["cases"].as_array().unwrap();
    for (index, case) in cases.iter().enumerate() {
        for (id, videos) in data["videos"].as_object().unwrap() {
            let missing = case["missing"].as_array().unwrap().iter().any(|m| m == id);
            let answer = if missing {
                ResponseTemplate::new(404)
            } else {
                ResponseTemplate::new(200).set_body_json(json!({"meta": {"videos": videos}}))
            };
            Mock::given(method("GET"))
                .and(path(format!("/c{index}/meta/series/{id}.json")))
                .respond_with(answer)
                .mount(&server)
                .await;
        }
    }
    for (index, case) in cases.iter().enumerate() {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(Db::open(&dir.path().join("kino.db")).unwrap());
        let meta = Meta {
            net: Arc::new(Net::new(db)),
            cinemeta: format!("{}/c{index}", server.uri()),
            translate: false,
        };
        let (history, watchlist) = (
            case["history"].as_array().unwrap(),
            case["watchlist"].as_array().unwrap(),
        );
        let today = case["today"].as_str().unwrap();
        let new = series::new_episodes(&meta, history, watchlist, today).await;
        assert_eq!(Value::Array(new), case["new"], "new episodes, case {index}");
        let mine = series::my_series(&meta, history, watchlist, today).await;
        assert_eq!(Value::Array(mine), case["my"], "my series, case {index}");
        let calendar = series::calendar(
            &meta,
            history,
            watchlist,
            today,
            case["local_today"].as_str().unwrap(),
        )
        .await;
        assert_eq!(calendar, case["calendar"], "calendar, case {index}");
    }
}
