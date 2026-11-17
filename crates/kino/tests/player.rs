//! The external player against a stand-in mpv: its arguments, the season's
//! playlist, the progress it reports into the history, the window stepping
//! aside and coming back, and a second click that starts nothing new.
#![cfg(unix)]

use std::sync::{Arc, Mutex};
use std::time::Duration;

use kino::db::Db;
use kino::meta::Meta;
use kino::net::Net;
use kino::player::{Launch, Media, Player, WindowHooks};

#[derive(Default)]
struct Window(Mutex<Vec<&'static str>>);

impl WindowHooks for Window {
    fn hide(&self) {
        self.0.lock().unwrap().push("hide");
    }
    fn show(&self) {
        self.0.lock().unwrap().push("show");
    }
    fn geometry(&self) -> Option<(i32, i32, u32, u32)> {
        Some((40, 30, 1280, 720))
    }
}

/// Answers mpv's IPC socket as mpv does, then quits after a few seconds.
const STAND_IN: &str = r#"#!/usr/bin/env python3
import json, os, socket, sys, threading, time
args = sys.argv[1:]
open(os.environ["KINO_TEST_ARGS"], "w").write(json.dumps(args))
sock = next(a.split("=", 1)[1] for a in args if a.startswith("--input-ipc-server="))
values = {"time-pos": 125.5, "duration": 1500.0, "playlist-pos": 1, "pause": False}
server = socket.socket(socket.AF_UNIX)
server.bind(sock)
server.listen()
def serve():
    while True:
        conn, _ = server.accept()
        line = conn.makefile().readline()
        command = json.loads(line)["command"]
        conn.sendall(b'{"event":"playback-restart"}\n')
        if command[0] == "get_property":
            conn.sendall((json.dumps({"data": values[command[1]], "error": "success"}) + "\n").encode())
        conn.close()
threading.Thread(target=serve, daemon=True).start()
time.sleep(float(os.environ.get("KINO_TEST_RUN", "6")))
"#;

#[tokio::test]
async fn plays_follows_and_comes_back() {
    let home = tempfile::tempdir().unwrap();
    let program = home.path().join("mpv");
    std::fs::write(&program, STAND_IN).unwrap();
    std::fs::set_permissions(
        &program,
        std::os::unix::fs::PermissionsExt::from_mode(0o755),
    )
    .unwrap();
    let args_file = home.path().join("args.json");
    std::fs::write(
        home.path().join(".rd_cinehub_config.json"),
        serde_json::json!({"mpv_path": program, "discord_rpc": false}).to_string(),
    )
    .unwrap();
    // SAFETY: this test binary runs one test, before any thread reads them.
    unsafe {
        std::env::set_var("HOME", home.path());
        std::env::set_var("KINO_TEST_ARGS", &args_file);
        std::env::set_var("KINO_MPV_IPC", home.path().join("mpv.sock"));
    }

    let db = Arc::new(Db::open(&home.path().join("kino.db")).unwrap());
    let meta = Meta {
        net: Arc::new(Net::new(db.clone())),
        cinemeta: "http://127.0.0.1:9".into(),
        translate: false,
    };
    let window = Arc::new(Window::default());
    let player = Player::new(db.clone(), meta, Some(window.clone()));
    let request = || Launch {
        url: "https://cdn.example/ep4.mkv".into(),
        title: "Severance — S02E04".into(),
        playlist: vec![
            ("S02E04".into(), "https://cdn.example/ep4.mkv".into()),
            (
                "S02E05".into(),
                "http://127.0.0.1:8080/api/auto-stream?e=5".into(),
            ),
        ],
        // Not an IMDb id: no subtitles are looked up.
        media: Some(Media {
            id: "kino-test".into(),
            name: "Severance".into(),
            kind: "series".into(),
            season: Some(2),
            episode: Some(4),
            ..Default::default()
        }),
        start_sec: 600,
    };
    assert_eq!(
        kino::player::find(),
        Some(program.clone()),
        "the stand-in, never a real player"
    );
    let answer = player.launch(request()).unwrap();
    assert_eq!(answer["playlist_count"], 2);
    assert!(answer["pid"].as_u64().unwrap() > 0);
    // A second click on the same file starts nothing new.
    assert_eq!(player.launch(request()).unwrap(), answer);

    let mut entry = serde_json::Value::Null;
    for _ in 0..80 {
        tokio::time::sleep(Duration::from_millis(100)).await;
        if let Some(e) = kino::library::history(&db)
            .into_iter()
            .find(|e| e["id"] == "kino-test")
        {
            entry = e;
            break;
        }
    }
    assert_eq!(entry["position"], 125);
    assert_eq!(entry["duration"], 1500);
    assert_eq!(
        entry["episode"], 5,
        "the playlist moved on to the next episode"
    );

    let args: Vec<String> =
        serde_json::from_str(&std::fs::read_to_string(&args_file).unwrap()).unwrap();
    let playlist = home.path().join(".kino_playlist.m3u");
    for wanted in [
        "--start=600",
        "--geometry=1280x720+40+30",
        "--alang=fre,fra,fr,eng",
        "--title=KINO",
    ] {
        assert!(args.iter().any(|a| a == wanted), "{wanted} in {args:?}");
    }
    assert_eq!(args.last().unwrap(), &playlist.display().to_string());
    assert!(!args.iter().any(|a| a.starts_with("--force-media-title")));
    let m3u = std::fs::read_to_string(&playlist).unwrap();
    assert!(m3u.starts_with("#EXTM3U\n#EXTINF:-1,S02E04\nhttps://cdn.example/ep4.mkv\n"));

    for _ in 0..100 {
        if window.0.lock().unwrap().len() == 2 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert_eq!(*window.0.lock().unwrap(), vec!["hide", "show"]);
}
