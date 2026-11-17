//! Trakt against a stand-in: the device code, its validation, and the
//! watchlist (films and shows) added to Ma Liste.

use std::sync::Arc;

use kino::db::Db;
use kino::trakt::Trakt;
use serde_json::json;
use wiremock::matchers::{body_partial_json, header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn sign_in_then_watchlist() {
    let server = MockServer::start().await;
    let ok = |v| ResponseTemplate::new(200).set_body_json(v);
    Mock::given(method("POST")).and(path("/oauth/device/code"))
        .respond_with(ok(json!({"device_code": "DEV", "user_code": "ABCD-EFGH", "verification_url": "https://trakt.tv/activate", "expires_in": 600, "interval": 5})))
        .mount(&server).await;
    Mock::given(method("POST"))
        .and(path("/oauth/device/token"))
        .and(body_partial_json(json!({"code": "WAIT"})))
        .respond_with(ResponseTemplate::new(400))
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/oauth/device/token"))
        .and(body_partial_json(json!({"code": "DEV"})))
        .respond_with(ok(json!({"access_token": "TOKEN", "refresh_token": "R"})))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/users/me"))
        .and(header("authorization", "Bearer TOKEN"))
        .respond_with(ok(json!({"username": "kino-user"})))
        .mount(&server)
        .await;
    Mock::given(method("GET")).and(path("/sync/watchlist/movies"))
        .respond_with(ok(json!([{"movie": {"title": "Heat", "year": 1995, "ids": {"imdb": "tt0113277"}}}, {"movie": {"title": "No id", "ids": {}}}])))
        .mount(&server).await;
    Mock::given(method("GET")).and(path("/sync/watchlist/shows"))
        .respond_with(ok(json!([{"show": {"title": "The Bear", "year": 2022, "ids": {"imdb": "tt14452776"}}}, {"show": {"title": "Only TMDB", "ids": {"tmdb": 42}}}])))
        .mount(&server).await;

    let dir = tempfile::tempdir().unwrap();
    let db = Arc::new(Db::open(&dir.path().join("kino.db")).unwrap());
    let client = reqwest::Client::new();
    let trakt = Trakt {
        db: &db,
        client: &client,
        base: server.uri(),
    };
    assert_eq!(
        trakt.sync().await["status"],
        "disabled",
        "nothing before signing in"
    );
    let started = trakt.start().await;
    assert_eq!(started["user_code"], "ABCD-EFGH");
    assert_eq!(trakt.poll("WAIT").await["status"], "pending");
    let done = trakt.poll("DEV").await;
    assert_eq!(done["status"], "connected");
    assert_eq!(trakt.status()["username"], "kino-user");
    assert_eq!(
        trakt.sync().await,
        json!({"status": "synced", "items_added": 3}),
        "films and shows; an entry without id is left out"
    );
    let ids: Vec<String> = kino::library::watchlist(&db)
        .iter()
        .map(|w| w["id"].as_str().unwrap().to_string())
        .collect();
    assert!(ids.contains(&"tt14452776".to_string()) && ids.contains(&"tmdb_42".to_string()));
    assert_eq!(trakt.sync().await["items_added"], 0, "already there");
    assert_eq!(trakt.disconnect(), json!({"status": "disconnected"}));
    assert_eq!(trakt.status()["connected"], false);
}
