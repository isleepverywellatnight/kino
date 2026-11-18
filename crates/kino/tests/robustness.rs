//! Every route of the bare server, fed odd input: empty, wrong types, broken
//! JSON, huge or non-ASCII text, odd path parts. Each request must get an
//! answer (a panic in a handler drops the connection) within the time limit,
//! and an error answer says what went wrong in words, never a raw transport error.
//! The network is a dead proxy: answers come from KINO alone.

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::Duration;

use kino::launch::{self, Options};
use regex::Regex;
use tokio::sync::Semaphore;

const PATH_PARTS: &[&str] = &[
    "tt0111161",
    "é😀'\"<>",
    "..%2F..%2Fetc",
    "0",
    "-1",
    "%FF%FE",
];
const QUERY_VALUES: &[&str] = &[
    "",
    "-1",
    "%C3%A9%F0%9F%98%80",
    "99999999999999999999999",
    "%FF",
    "%5B%5D",
    "tt0111161",
];
const QUERY_KEYS: &[&str] = &[
    "id", "imdb", "q", "query", "type", "season", "episode", "page", "hash", "index", "url",
    "title", "year", "lang", "limit", "token", "magnet", "file", "start", "audio",
];

fn bodies() -> Vec<String> {
    // Large, yet small enough to be sent whole before a route that ignores it answers.
    let big = "é".repeat(8_000);
    vec![
        String::new(),
        "{}".into(),
        "[]".into(),
        "null".into(),
        "\"x\"".into(),
        "{".into(),
        "\u{0}\u{1}".into(),
        r#"{"id":null,"imdb":5,"items":"x","url":{},"magnet":[],"season":-1,"episode":1e308,"title":"é😀","type":"series","ids":[null,1,"",{}],"index":-3,"enabled":"oui","name":[]}"#.into(),
        r#"{"items":[{"id":"tt0111161"},{"id":null},5,"x"],"ids":["tt0111161","",null],"type":"movie","id":"tt0111161","season":1,"episode":1}"#.into(),
        format!(r#"{{"id":"{big}","q":"{big}","title":"{big}"}}"#),
    ]
}

/// The routes declared in the server's sources: `.route("/path", …)`.
fn routes() -> BTreeSet<String> {
    let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let pattern = Regex::new(r#"\.route\(\s*"([^"]+)""#).unwrap();
    let mut found = BTreeSet::new();
    let mut files = vec![src.join("server.rs")];
    for entry in std::fs::read_dir(src.join("routes")).unwrap() {
        files.push(entry.unwrap().path());
    }
    for file in files {
        let text = std::fs::read_to_string(&file).unwrap();
        for capture in pattern.captures_iter(&text) {
            found.insert(capture[1].to_string());
        }
    }
    found
}

/// The route with each `{part}` replaced by `value`.
fn fill(route: &str, value: &str) -> String {
    Regex::new(r"\{\*?[a-z_]+\}")
        .unwrap()
        .replace_all(route, value)
        .into_owned()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn every_route_answers_odd_input() {
    let home = tempfile::tempdir().unwrap();
    // SAFETY: this test binary runs one test, before the server reads its environment.
    unsafe {
        std::env::set_var("HOME", home.path());
        std::env::set_var("USERPROFILE", home.path());
        for proxy in [
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ] {
            std::env::set_var(proxy, "http://127.0.0.1:9");
        }
        std::env::remove_var("NO_PROXY");
        std::env::remove_var("no_proxy");
    }
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
    // The test's own client talks to KINO directly.
    let http = reqwest::Client::builder()
        .no_proxy()
        // A fresh connection each time: a reused one the server just closed
        // (it answered before reading the body) is not KINO failing.
        .pool_max_idle_per_host(0)
        .timeout(Duration::from_secs(30))
        .build()
        .unwrap();
    for _ in 0..200 {
        if http.get(format!("{base}/icons.svg")).send().await.is_ok() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }

    let routes = routes();
    assert!(routes.len() > 80, "routes found: {}", routes.len());
    let mut requests: Vec<(reqwest::Method, String, Option<String>)> = Vec::new();
    for route in &routes {
        let paths: Vec<String> = if route.contains('{') {
            PATH_PARTS.iter().map(|v| fill(route, v)).collect()
        } else {
            vec![route.clone()]
        };
        for path in &paths {
            requests.push((reqwest::Method::GET, path.clone(), None));
            for value in QUERY_VALUES {
                let query: Vec<String> =
                    QUERY_KEYS.iter().map(|k| format!("{k}={value}")).collect();
                requests.push((
                    reqwest::Method::GET,
                    format!("{path}?{}", query.join("&")),
                    None,
                ));
            }
            for body in bodies() {
                requests.push((reqwest::Method::POST, path.clone(), Some(body)));
            }
        }
    }

    let limit = Arc::new(Semaphore::new(16));
    let mut tasks = tokio::task::JoinSet::new();
    for (method, path, body) in requests {
        let http = http.clone();
        let url = format!("{base}{path}");
        let limit = limit.clone();
        tasks.spawn(async move {
            let _slot = limit.acquire_owned().await.unwrap();
            let mut request = http
                .request(method.clone(), &url)
                .header("content-type", "application/json");
            if let Some(body) = body.clone() {
                request = request.body(body);
            }
            let started = std::time::Instant::now();
            let answer = request.send().await;
            let what = format!(
                "{method} {path} {}",
                body.map(|b| b.chars().take(60).collect::<String>())
                    .unwrap_or_default()
            );
            match answer {
                // Read the whole body: a handler may fail while streaming it.
                Ok(answer) => {
                    let status = answer.status().as_u16();
                    match answer.bytes().await {
                        Ok(body) => {
                            let full = String::from_utf8_lossy(&body);
                            let text: String = full.chars().take(120).collect();
                            // An error answer explains itself, in words for the user.
                            let explained = serde_json::from_slice::<serde_json::Value>(&body)
                                .ok()
                                .and_then(|v| {
                                    v["error"]
                                        .as_str()
                                        .or(v["message"].as_str())
                                        .map(str::to_string)
                                })
                                .is_some_and(|m| !m.trim().is_empty());
                            let raw = full.contains("error sending request")
                                || full.contains("for url (");
                            Ok((
                                status,
                                explained,
                                raw,
                                format!("{what} -> {text}"),
                                started.elapsed(),
                            ))
                        }
                        Err(error) => Err(format!("{what}: body cut ({error})")),
                    }
                }
                Err(error) => Err(format!("{what}: no answer ({error})")),
            }
        });
    }
    let mut failures = Vec::new();
    let mut unexplained = BTreeSet::new();
    let mut slowest = (Duration::ZERO, String::new());
    let mut count = 0;
    while let Some(result) = tasks.join_next().await {
        count += 1;
        match result.unwrap() {
            Ok((status, explained, raw, what, took)) => {
                if (status >= 500 && !explained) || raw {
                    unexplained.insert(format!("{status} {what}"));
                }
                if took > slowest.0 {
                    slowest = (took, what);
                }
            }
            Err(failure) => failures.push(failure),
        }
    }
    eprintln!(
        "{count} requests on {} routes; slowest {:?}: {}",
        routes.len(),
        slowest.0,
        slowest.1
    );
    for answer in &unexplained {
        eprintln!("unexplained: {answer}");
    }
    assert!(
        failures.is_empty(),
        "{} requests without an answer:\n{}",
        failures.len(),
        failures.join("\n")
    );
    assert!(
        unexplained.is_empty(),
        "{} error answers without a message for the user",
        unexplained.len()
    );
}
