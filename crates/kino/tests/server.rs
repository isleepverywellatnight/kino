//! The bare server (kino-server, no window): what only the KINO app can do is
//! refused with a clear message, and an unknown route is not found.

use kino::launch::{self, Options};

#[tokio::test]
async fn the_bare_server_says_what_needs_the_app() {
    let home = tempfile::tempdir().unwrap();
    // SAFETY: this test binary runs one test, before the server reads HOME.
    unsafe { std::env::set_var("HOME", home.path()) };
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let port = listener.local_addr().unwrap().port();
    let options = Options {
        port,
        pairing_token: String::new(),
        native_player: false,
        notifications: false,
        startup_tasks: false,
        window: None,
        notifier: None,
        remote: None,
        native: None,
    };
    tokio::spawn(launch::serve(listener, options));
    let base = format!("http://127.0.0.1:{port}");
    let http = reqwest::Client::new();
    let mut ready = false;
    for _ in 0..100 {
        if http.get(format!("{base}/api/p2p")).send().await.is_ok() {
            ready = true;
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    assert!(ready, "the server answers");

    for path in ["/api/p2p", "/api/tv/devices", "/api/remote/state"] {
        let answer = http.get(format!("{base}{path}")).send().await.unwrap();
        assert_eq!(answer.status().as_u16(), 501, "{path}");
        let body: serde_json::Value = answer.json().await.unwrap();
        assert_eq!(
            body["error"], "Disponible dans l'application KINO.",
            "{path}"
        );
    }
    let answer = http
        .get(format!("{base}/api/nothing-here"))
        .send()
        .await
        .unwrap();
    assert_eq!(answer.status().as_u16(), 404);
}
