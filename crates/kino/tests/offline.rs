//! The offline library against a stand-in file server: a download, its
//! listing, the file served back with ranges, refusals, removal, and a queued
//! episode that finds no source.

use std::sync::Arc;
use std::time::Duration;

use kino::db::Db;
use kino::offline::Library;
use serde_json::json;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

async fn settle(library: &Library, id: &str, wanted: &str) -> serde_json::Value {
    for _ in 0..100 {
        let entry = library.list()[id].clone();
        if entry["status"] == wanted {
            return entry;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    library.list()[id].clone()
}

#[tokio::test]
async fn download_list_serve_and_remove() {
    let server = MockServer::start().await;
    let body: Vec<u8> = (0..300_000u32).map(|i| (i % 251) as u8).collect();
    Mock::given(method("GET"))
        .and(path("/film.mkv"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_raw(body.clone(), "video/x-matroska")
                .insert_header("ETag", "\"v1\"")
                .insert_header("Accept-Ranges", "bytes"),
        )
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/expired.mkv"))
        .respond_with(ResponseTemplate::new(403))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/login.mkv"))
        .respond_with(ResponseTemplate::new(200).set_body_raw("<html>", "text/html"))
        .mount(&server)
        .await;

    let home = tempfile::tempdir().unwrap();
    let db = Arc::new(Db::open(&home.path().join("kino.db")).unwrap());
    let library = Library::new(db, None);
    let dest = home.path().join("Downloads").to_string_lossy().into_owned();
    let media = json!({"id": "tt0113277", "name": "Heat", "type": "movie", "year": "1995"});

    assert_eq!(
        library
            .create("ftp://x/film.mkv", "Heat.mkv", &dest, &media, &json!(null))
            .unwrap_err()
            .code,
        "invalid_download"
    );
    assert!(
        library
            .create(
                &format!("{}/film.mkv", server.uri()),
                "notes.txt",
                &dest,
                &media,
                &json!(null)
            )
            .is_err()
    );

    let (id, duplicate) = library
        .create(
            &format!("{}/film.mkv", server.uri()),
            "Heat.1995.mkv",
            &dest,
            &media,
            &json!(null),
        )
        .unwrap();
    assert!(!duplicate);
    let done = settle(&library, &id, "completed").await;
    assert_eq!(done["bytes_done"], 300_000);
    assert_eq!(done["available"], true);
    assert_eq!(done["url"], format!("/api/offline/media/{id}"));
    assert_eq!(done["media"]["offline_id"], id.as_str());
    let file = library.asset(&id, "media").unwrap();
    assert_eq!(std::fs::read(&file).unwrap(), body);
    assert!(file.parent().unwrap().ends_with("KINO Offline"));
    assert!(library.usage_bytes() >= 300_000);

    let (again, duplicate) = library
        .create(
            &format!("{}/film.mkv", server.uri()),
            "Heat.1995.mkv",
            &dest,
            &media,
            &json!(null),
        )
        .unwrap();
    assert_eq!(
        (again.as_str(), duplicate),
        (id.as_str(), true),
        "the same title and file is not downloaded twice"
    );
    assert_eq!(
        library.resume(&id, false, None).unwrap_err().message,
        "Fichier déjà disponible hors ligne."
    );

    let (expired, _) = library
        .create(
            &format!("{}/expired.mkv", server.uri()),
            "Other.mkv",
            &dest,
            &json!({"id": "tt1"}),
            &json!(null),
        )
        .unwrap();
    assert_eq!(
        settle(&library, &expired, "failed").await["error_code"],
        "url_expired"
    );
    let (login, _) = library
        .create(
            &format!("{}/login.mkv", server.uri()),
            "Login.mkv",
            &dest,
            &json!({"id": "tt2"}),
            &json!(null),
        )
        .unwrap();
    assert_eq!(
        settle(&library, &login, "failed").await["error_code"],
        "url_expired",
        "a login page is not a video"
    );

    let (episode, already) = library
        .queue_episode(
            &json!("tt0903747"),
            &json!(1),
            &json!(3),
            &dest,
            &serde_json::Map::new(),
        )
        .unwrap();
    assert!(!already);
    assert_eq!(
        settle(&library, &episode, "failed").await["error_code"],
        "source_unavailable",
        "no source finder here"
    );
    let (_, already) = library
        .queue_episode(
            &json!("tt0903747"),
            &json!(1),
            &json!(3),
            &dest,
            &serde_json::Map::new(),
        )
        .unwrap();
    assert!(!already, "a failed one may be queued again");

    std::fs::remove_file(&file).unwrap();
    assert_eq!(
        library.list()[&id]["available"],
        false,
        "a missing file is noticed"
    );
    library.remove(&id).unwrap();
    assert!(library.list().get(&id).is_none());
}
