//! « Lire sur la TV » against a stand-in TV (UPnP AVTransport): the source
//! sent, the resume point sought once playing, the phone's commands applied,
//! progress saved, the end of the film, a refused file, and the relay's guard.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use kino::db::Db;
use kino::meta::Meta;
use kino::net::Net;
use kino::remote::Remote;
use kino::tv::{Cast, Timing, TvError};
use serde_json::{Map, Value, json};
use wiremock::{Mock, MockServer, Request, Respond, ResponseTemplate, matchers::method};

#[derive(Default)]
struct Screen {
    state: String,
    position: f64,
    uri: String,
    actions: Vec<String>,
    refuse: bool,
}

/// Plays a 2-hour film; each position read moves it on by 5 seconds.
#[derive(Clone, Default)]
struct Tv(Arc<Mutex<Screen>>);

impl Respond for Tv {
    fn respond(&self, request: &Request) -> ResponseTemplate {
        let action = request
            .headers
            .get("SOAPACTION")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .rsplit('#')
            .next()
            .unwrap_or("")
            .trim_matches('"')
            .to_string();
        let body = String::from_utf8_lossy(&request.body).into_owned();
        let between = |a: &str, b: &str| {
            body.split(a)
                .nth(1)
                .and_then(|r| r.split(b).next())
                .unwrap_or("")
                .to_string()
        };
        let mut tv = self.0.lock().unwrap();
        tv.actions.push(action.clone());
        let reply = |inner: String| {
            ResponseTemplate::new(200)
                .set_body_string(format!("<s:Envelope><s:Body>{inner}</s:Body></s:Envelope>"))
        };
        match action.as_str() {
            "SetAVTransportURI" if tv.refuse => {
                ResponseTemplate::new(500).set_body_string("<s:Envelope><s:Body><s:Fault><detail><UPnPError><errorCode>714</errorCode></UPnPError></detail></s:Fault></s:Body></s:Envelope>")
            }
            "SetAVTransportURI" => {
                tv.uri = between("<CurrentURI>", "</CurrentURI>");
                tv.state = "STOPPED".into();
                reply(String::new())
            }
            "Play" => {
                tv.state = "PLAYING".into();
                reply(String::new())
            }
            "Pause" => {
                tv.state = "PAUSED_PLAYBACK".into();
                reply(String::new())
            }
            "Stop" => {
                tv.state = "STOPPED".into();
                reply(String::new())
            }
            "Seek" => {
                let target = between("<Target>", "</Target>");
                let parts: Vec<f64> = target.split(':').map(|p| p.parse().unwrap_or(0.0)).collect();
                tv.position = parts.iter().fold(0.0, |acc, n| acc * 60.0 + n);
                reply(String::new())
            }
            "GetTransportInfo" => reply(format!("<CurrentTransportState>{}</CurrentTransportState>", tv.state)),
            "GetPositionInfo" => {
                if tv.state == "PLAYING" {
                    tv.position += 5.0;
                }
                let p = tv.position as i64;
                reply(format!("<RelTime>{}:{:02}:{:02}</RelTime><TrackDuration>2:00:00</TrackDuration>", p / 3600, p / 60 % 60, p % 60))
            }
            _ => ResponseTemplate::new(500),
        }
    }
}

async fn until(what: impl Fn() -> bool) {
    for _ in 0..200 {
        if what() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

#[tokio::test]
async fn a_film_on_the_tv() {
    // The TV must be on the local network: the stand-in listens on this computer's LAN address.
    let ip = kino::remote::local_ip();
    if ip == "127.0.0.1" {
        eprintln!("no local network address: skipped");
        return;
    }
    let listener = std::net::TcpListener::bind((ip.as_str(), 0)).unwrap();
    let server = MockServer::builder().listener(listener).start().await;
    let tv = Tv::default();
    Mock::given(method("POST"))
        .respond_with(tv.clone())
        .mount(&server)
        .await;
    let device = json!({"location": format!("{}/dmr", server.uri()), "control_url": format!("{}/control", server.uri()), "host": ip, "name": "[TV] Salon"});

    let home = tempfile::tempdir().unwrap();
    let db = Arc::new(Db::open(&home.path().join("kino.db")).unwrap());
    let meta = Meta {
        net: Arc::new(Net::new(db.clone())),
        cinemeta: "http://127.0.0.1:9".into(),
        translate: false,
    };
    let remote = Arc::new(Remote::default());
    let timing = Timing {
        poll: Duration::from_millis(40),
        start_timeout: 2.0,
        next_delay: 0.2,
    };
    let cast = Cast::new(
        db.clone(),
        meta,
        Default::default(),
        remote.clone(),
        None,
        8080,
        timing,
    );

    let media: Map<String, Value> = json!({"id": "tt0113277", "name": "Heat", "type": "movie", "year": "1995", "poster": "https://img/heat.jpg"}).as_object().cloned().unwrap();
    let status = cast
        .start(
            &device,
            "https://debrid.example/dl/Heat.1995.mp4",
            "Heat",
            media.clone(),
            "Heat (1995).mp4",
            1200.0,
        )
        .await
        .unwrap();
    assert_eq!(
        (
            status["active"].clone(),
            status["device"].clone(),
            status["starting"].clone()
        ),
        (json!(true), json!("[TV] Salon"), json!(true))
    );
    let uri = tv.0.lock().unwrap().uri.clone();
    assert!(
        uri.starts_with(&format!("http://{ip}:8080/tv/stream/"))
            && uri.ends_with("/Heat%201995.mp4"),
        "{uri}"
    );

    // Playing: the resume point is sought once, then the phone sees the TV.
    until(|| tv.0.lock().unwrap().actions.contains(&"Seek".to_string())).await;
    until(|| remote.state()["current_time"].as_f64().unwrap_or(0.0) >= 1200.0).await;
    let state = remote.state();
    assert_eq!(
        (
            state["playing"].clone(),
            state["title"].clone(),
            state["duration"].clone()
        ),
        (json!(true), json!("Heat"), json!(7200.0))
    );

    // The relay answers the TV and this computer only, with the session's token.
    let path = uri.split(":8080").nth(1).unwrap().to_string();
    assert_eq!(
        cast.relay_allowed(&ip, &path).map(|r| r.1),
        Some("video/mp4")
    );
    assert!(cast.relay_allowed("192.168.1.99", &path).is_none());
    assert!(cast.relay_allowed(&ip, "/tv/stream/wrong/x.mp4").is_none());

    // A tap on the phone pauses the TV; « +30 s » seeks.
    remote.push(&json!("toggle_play"), &Value::Null);
    until(|| tv.0.lock().unwrap().state == "PAUSED_PLAYBACK").await;
    until(|| cast.status()["paused"] == true).await;
    let before = tv.0.lock().unwrap().position;
    let status = cast.control("seek_rel", &json!(30)).await.unwrap();
    assert_eq!(tv.0.lock().unwrap().position, before + 30.0);
    assert_eq!(status["paused"], true);

    // The film ends on the TV: progress saved, the session over.
    {
        let mut screen = tv.0.lock().unwrap();
        screen.state = "PLAYING".into();
        screen.position = 7150.0;
    }
    until(|| cast.status()["position"].as_f64().unwrap_or(0.0) >= 7150.0).await;
    tv.0.lock().unwrap().state = "STOPPED".into();
    until(|| cast.status()["active"] == false).await;
    assert_eq!(cast.status(), json!({"active": false}));
    assert_eq!(remote.state()["playing"], false);
    let entry = kino::library::history(&db)
        .into_iter()
        .find(|e| e["id"] == "tt0113277")
        .unwrap();
    assert_eq!(
        (entry["position"].clone(), entry["duration"].clone()),
        (json!(7200), json!(7200))
    );

    // A file the TV cannot read: UPnP error 714.
    tv.0.lock().unwrap().refuse = true;
    match cast
        .start(
            &device,
            "https://debrid.example/dl/x.avi",
            "X",
            Map::new(),
            "x.avi",
            0.0,
        )
        .await
    {
        Err(TvError::Refused(message)) => {
            assert_eq!(message, "La TV ne lit pas ce type de fichier.")
        }
        other => panic!("{other:?}"),
    }
    assert_eq!(cast.status(), json!({"active": false}));
    // Nothing to send, or a TV outside the local network.
    assert!(matches!(
        cast.start(&device, "ftp://x", "X", Map::new(), "", 0.0)
            .await,
        Err(TvError::Refused(_))
    ));
    let far = json!({"location": "http://8.8.8.8/dmr", "control_url": "http://8.8.8.8/c"});
    assert!(matches!(
        cast.start(&far, "https://x/y.mkv", "X", Map::new(), "", 0.0)
            .await,
        Err(TvError::Refused(_))
    ));
}
