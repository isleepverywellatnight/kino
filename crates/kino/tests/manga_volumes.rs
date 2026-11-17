//! A manga volume pack read peer to peer from a seeder on this computer: its
//! archives listed in volume order, one fetched alone, checked, then copied
//! into the user's manga folder.

use std::io::Write;
use std::sync::Arc;
use std::time::Duration;

use kino::db::Db;
use kino::p2p::P2p;
use librqbit::{
    AddTorrent, AddTorrentOptions, CreateTorrentOptions, ListenerOptions, Session, SessionOptions,
};
use serde_json::json;

fn cbz(pages: usize) -> Vec<u8> {
    let mut zip = zip::ZipWriter::new(std::io::Cursor::new(vec![]));
    let stored =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for page in 0..pages {
        zip.start_file(format!("{page:03}.jpg"), stored).unwrap();
        zip.write_all(&vec![page as u8; 200_000]).unwrap();
    }
    zip.finish().unwrap().into_inner()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_volume_lands_in_the_manga_folder() {
    let home = tempfile::tempdir().unwrap();
    // SAFETY: this test binary runs one test, before anything reads HOME.
    unsafe { std::env::set_var("HOME", home.path()) };
    let pack = home.path().join("share/Berserk");
    std::fs::create_dir_all(&pack).unwrap();
    std::fs::write(pack.join("Berserk v10.cbz"), cbz(4)).unwrap();
    std::fs::write(pack.join("Berserk v2.cbz"), cbz(3)).unwrap();
    std::fs::write(pack.join("readme.txt"), b"x").unwrap();
    let spawner = librqbit::spawn_utils::BlockingSpawner::new(2);
    let created = librqbit::create_torrent(
        &pack,
        CreateTorrentOptions {
            name: Some("Berserk"),
            trackers: vec![],
            piece_length: Some(65536),
        },
        &spawner,
    )
    .await
    .unwrap();
    let hash = created.info_hash().as_string();
    let seeder = Session::new_with_opts(
        home.path().join("share"),
        SessionOptions {
            dht: None,
            disable_local_service_discovery: true,
            listen: Some(ListenerOptions {
                listen_addr: (std::net::Ipv4Addr::LOCALHOST, 0).into(),
                ..Default::default()
            }),
            ..Default::default()
        },
    )
    .await
    .unwrap();
    seeder
        .add_torrent(
            AddTorrent::TorrentFileBytes(created.as_bytes().unwrap()),
            Some(AddTorrentOptions {
                overwrite: true,
                ..Default::default()
            }),
        )
        .await
        .unwrap();

    let db = Arc::new(Db::open(&home.path().join("kino.db")).unwrap());
    let p2p = P2p::new(db.clone());
    p2p.add_known_peer(seeder.listen_addr().unwrap());
    // Without the consent, nothing is opened.
    assert!(matches!(
        kino::manga::volumes::files(&p2p, &hash, "Berserk").await,
        Err(kino::manga::MangaError::Consent)
    ));
    p2p.set_consent(true, &json!("minimum")).await.unwrap();

    let listed = tokio::time::timeout(
        Duration::from_secs(60),
        kino::manga::volumes::files(&p2p, &hash, "Berserk"),
    )
    .await
    .unwrap()
    .unwrap_or_else(|e| panic!("{e:?}"));
    let names: Vec<&str> = listed["files"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| f["name"].as_str().unwrap())
        .collect();
    assert_eq!(
        names,
        ["Berserk v2.cbz", "Berserk v10.cbz"],
        "archives only, in volume order"
    );
    let wanted = listed["files"][1]["index"].clone();

    let mut status = kino::manga::volumes::start(&p2p, &db, &hash, &wanted, "Berserk: Deluxe")
        .await
        .unwrap_or_else(|e| panic!("{e:?}"));
    for _ in 0..200 {
        if status["done"] == true {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
        status = kino::manga::volumes::status(&p2p, &db, &hash, &wanted)
            .await
            .unwrap_or_else(|e| panic!("{e:?}"));
    }
    assert_eq!(status["done"], true, "{status}");
    assert_eq!(status["series"], "Berserk  Deluxe");
    let copied = home
        .path()
        .join("Documents/KINO Manga/Berserk  Deluxe/Berserk v10.cbz");
    assert_eq!(std::fs::read(&copied).unwrap(), cbz(4));
    assert_eq!(status["local"].as_str().unwrap().len(), 20);
    // The other volume was not fetched.
    assert!(
        !home
            .path()
            .join("Documents/KINO Manga/Berserk  Deluxe/Berserk v2.cbz")
            .exists()
    );
}
