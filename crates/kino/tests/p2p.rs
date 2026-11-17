//! Peer-to-peer playback on this computer only: a seeder holds a generated
//! film, KINO finds its file list through the magnet link and reads the film
//! from that peer, from the start and from the middle.

use std::sync::Arc;
use std::time::Duration;

use kino::db::Db;
use kino::p2p::P2p;
use librqbit::{
    AddTorrent, AddTorrentOptions, CreateTorrentOptions, ListenerOptions, Session, SessionOptions,
};
use tokio::io::{AsyncReadExt, AsyncSeekExt};

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn reads_a_film_from_a_peer() {
    let home = tempfile::tempdir().unwrap();
    // SAFETY: this test binary runs one test, before the engine reads HOME.
    unsafe { std::env::set_var("HOME", home.path()) };

    // The seeder: a 3 MB « film » and its torrent, on this computer only.
    let share = home.path().join("share");
    std::fs::create_dir_all(share.join("Film")).unwrap();
    let film: Vec<u8> = (0..3_000_000u32)
        .map(|i| (i.wrapping_mul(2_654_435_761) >> 24) as u8)
        .collect();
    std::fs::write(share.join("Film/Film.Test.2024.mkv"), &film).unwrap();
    std::fs::write(share.join("Film/notes.nfo"), b"nfo").unwrap();
    let spawner = librqbit::spawn_utils::BlockingSpawner::new(2);
    let created = librqbit::create_torrent(
        &share.join("Film"),
        CreateTorrentOptions {
            name: Some("Film"),
            trackers: vec![],
            piece_length: Some(65536),
        },
        &spawner,
    )
    .await
    .unwrap();
    let hash = created.info_hash().as_string();
    let seeder_options = SessionOptions {
        dht: None,
        disable_local_service_discovery: true,
        listen: Some(ListenerOptions {
            listen_addr: (std::net::Ipv4Addr::LOCALHOST, 0).into(),
            ..Default::default()
        }),
        ..Default::default()
    };
    let seeder = Session::new_with_opts(share.clone(), seeder_options)
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
    let seeder_addr = seeder.listen_addr().unwrap();

    // KINO, after the user's consent.
    let db = Arc::new(Db::open(&home.path().join("kino.db")).unwrap());
    let p2p = P2p::new(db);
    p2p.set_consent(true, &serde_json::json!("minimum"))
        .await
        .unwrap();
    p2p.add_known_peer(seeder_addr);
    let prepared = tokio::time::timeout(
        Duration::from_secs(60),
        p2p.prepare(
            &format!("magnet:?xt=urn:btih:{hash}"),
            "",
            "http://127.0.0.1:8080",
        ),
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(prepared["ready"], true, "{prepared}");
    let files = prepared["files"].as_array().unwrap();
    assert_eq!(files.len(), 1, "only the video is offered");
    assert_eq!(files[0]["filename"], "Film.Test.2024.mkv");
    assert_eq!(files[0]["filesize_bytes"], film.len());
    let index: usize = files[0]["download"]
        .as_str()
        .unwrap()
        .rsplit('/')
        .next()
        .unwrap()
        .parse()
        .unwrap();
    assert!(
        files[0]["download"]
            .as_str()
            .unwrap()
            .starts_with(&format!("http://127.0.0.1:8080/p2p/{hash}/"))
    );

    let (mut stream, size, kind) = p2p.open(&hash, index).await.unwrap();
    assert_eq!((size, kind), (film.len() as u64, "video/x-matroska"));
    let mut head = vec![0u8; 100_000];
    tokio::time::timeout(Duration::from_secs(60), stream.read_exact(&mut head))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(head, film[..100_000]);
    // A jump into the film, as a player seeking.
    stream
        .seek(std::io::SeekFrom::Start(2_500_000))
        .await
        .unwrap();
    let mut tail = vec![];
    tokio::time::timeout(Duration::from_secs(60), stream.read_to_end(&mut tail))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(tail, film[2_500_000..]);
    assert!(P2p::cache_size() > 0);
    p2p.clear_cache().await;
    assert_eq!(P2p::cache_size(), 0);
}
