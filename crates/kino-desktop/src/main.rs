//! KINO's window in Rust (opt-in while it grows): the page in a web view, the
//! bridge it calls (window.pywebview.api, as with the Python window), KINO's
//! server in this same process, and the Python server behind it, headless,
//! for what is not in Rust yet (players, P2P, TV, remote, updates).

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

use anyhow::Result;
use serde_json::{Value, json};
use tao::dpi::{LogicalPosition, LogicalSize};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy};
use tao::window::{Fullscreen, ResizeDirection, Window, WindowBuilder};
use wry::WebViewBuilder;

#[cfg(target_os = "macos")]
mod mac_media;
#[cfg(target_os = "macos")]
mod mac_notify;

/// What the page may call; each returns a promise, as pywebview's do.
const BRIDGE: [&str; 21] = [
    "minimize",
    "toggle_maximize",
    "start_window_drag",
    "start_resize",
    "is_fullscreen",
    "toggle_fullscreen",
    "enter_fullscreen",
    "exit_fullscreen",
    "close",
    "set_bounds",
    "resize",
    "get_state",
    "choose_manga_folder",
    "export_playback_diagnostic",
    "update_status",
    "check_for_updates",
    "open_release_page",
    "choose_update_package",
    "install_update",
    "rollback_update",
    "platform",
];

fn bridge_script() -> String {
    let names = serde_json::to_string(&BRIDGE).unwrap_or_default();
    format!(
        r#"(function () {{
  const pending = new Map(); let next = 0;
  window.__kinoReply = (id, ok, value) => {{
    const call = pending.get(id); if (!call) return; pending.delete(id);
    ok ? call.resolve(value) : call.reject(new Error(value));
  }};
  const api = {{}};
  for (const name of {names}) {{
    api[name] = (...args) => new Promise((resolve, reject) => {{
      const id = ++next; pending.set(id, {{ resolve, reject }});
      window.ipc.postMessage(JSON.stringify({{ id, name, args }}));
    }});
  }}
  window.pywebview = {{ api, platform: 'kino-desktop' }};
  const ready = () => window.dispatchEvent(new Event('pywebviewready'));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready); else ready();
}})();"#
    )
}

enum UserEvent {
    Call(u64, String, Vec<Value>),
    /// The external player started or ended: the window steps aside, then comes back.
    Hide,
    Show,
    /// A window action the page asked through the server (/api/window/action).
    Action(String),
    /// A notification was clicked: KINO comes to the front and opens the series.
    Notification(Value),
    /// A poster fetched for « À l'écoute » (its address, its bytes).
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    Poster(String, Vec<u8>),
}

type Bounds = Arc<Mutex<Option<(i32, i32, u32, u32)>>>;

/// What the external player asks of the window, from the server's threads.
struct Hooks {
    proxy: Mutex<EventLoopProxy<UserEvent>>,
    bounds: Bounds,
}

impl kino::player::WindowHooks for Hooks {
    fn hide(&self) {
        if let Ok(p) = self.proxy.lock() {
            let _ = p.send_event(UserEvent::Hide);
        }
    }
    fn show(&self) {
        if let Ok(p) = self.proxy.lock() {
            let _ = p.send_event(UserEvent::Show);
        }
    }
    fn geometry(&self) -> Option<(i32, i32, u32, u32)> {
        self.bounds.lock().ok().and_then(|b| *b)
    }
    fn action(&self, name: &str) {
        if let Ok(p) = self.proxy.lock() {
            let _ = p.send_event(UserEvent::Action(name.to_string()));
        }
    }
}

/// The window's place for mpv's --geometry: points on a Mac, pixels elsewhere.
fn bounds_of(window: &Window) -> Option<(i32, i32, u32, u32)> {
    let at = window.outer_position().ok()?;
    let size = window.inner_size();
    let scale = if cfg!(target_os = "macos") {
        window.scale_factor()
    } else {
        1.0
    };
    Some((
        (at.x as f64 / scale) as i32,
        (at.y as f64 / scale) as i32,
        (size.width as f64 / scale) as u32,
        (size.height as f64 / scale) as u32,
    ))
}

/// Remembered between launches, as the Python window does (`window_bounds`).
fn saved_bounds() -> (f64, f64, Option<(f64, f64)>) {
    let cfg = kino::config::load();
    let b = &cfg.get("window_bounds").cloned().unwrap_or(Value::Null);
    let n = |k: &str, d: f64| b[k].as_f64().unwrap_or(d);
    let at = (b["x"].is_number() && b["y"].is_number()).then(|| (n("x", 0.0), n("y", 0.0)));
    (
        n("width", 1280.0).max(640.0),
        n("height", 820.0).max(420.0),
        at,
    )
}

fn save_bounds(window: &Window) {
    if window.is_maximized() || window.fullscreen().is_some() {
        return;
    }
    let scale = window.scale_factor();
    let size = window.inner_size().to_logical::<f64>(scale);
    let at = window
        .outer_position()
        .map(|p| p.to_logical::<f64>(scale))
        .ok();
    let mut bounds = json!({"width": size.width.round(), "height": size.height.round()});
    if let Some(p) = at {
        bounds["x"] = json!(p.x.round());
        bounds["y"] = json!(p.y.round());
    }
    let _ = kino::config::save(&json!({"window_bounds": bounds}));
}

/// A free port from `start`, as the Python app looks for one.
fn free_port(start: u16) -> u16 {
    (start..start + 10)
        .find(|p| std::net::TcpListener::bind(("127.0.0.1", *p)).is_ok())
        .unwrap_or(start)
}

/// The Python server, headless, behind this one (KINO_PYTHON and src/app.py in
/// a checkout). It ends when its input closes, with this process.
fn start_python(private_port: u16, front_port: u16, token: &str) -> Option<Child> {
    let python = std::env::var_os("KINO_PYTHON").map(PathBuf::from)?;
    let script = std::env::var_os("KINO_PYTHON_APP")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("src/app.py"));
    Command::new(python)
        .arg(script)
        .env("KINO_SIDECAR_PORT", private_port.to_string())
        .env("KINO_PORT", front_port.to_string())
        .env("KINO_PAIRING_TOKEN", token)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()
        .ok()
}

fn direction(name: &str) -> Option<ResizeDirection> {
    Some(match name.replace('_', "-").as_str() {
        "left" => ResizeDirection::West,
        "right" => ResizeDirection::East,
        "top" => ResizeDirection::North,
        "bottom" => ResizeDirection::South,
        "topleft" | "top-left" => ResizeDirection::NorthWest,
        "topright" | "top-right" => ResizeDirection::NorthEast,
        "bottomleft" | "bottom-left" => ResizeDirection::SouthWest,
        "bottomright" | "bottom-right" => ResizeDirection::SouthEast,
        _ => return None,
    })
}

fn state(window: &Window) -> Value {
    let scale = window.scale_factor();
    let size = window.inner_size().to_logical::<f64>(scale);
    let at = window
        .outer_position()
        .map(|p| p.to_logical::<f64>(scale))
        .unwrap_or(LogicalPosition::new(0.0, 0.0));
    json!({"maximized": window.is_maximized(), "fullscreen": window.fullscreen().is_some(), "width": size.width.round(),
           "height": size.height.round(), "x": at.x.round(), "y": at.y.round(), "platform": std::env::consts::OS})
}

/// One call of the page: Ok(answer) or Err(message). `quit` ends the app.
fn call(window: &Window, name: &str, args: &[Value], quit: &mut bool) -> Result<Value, String> {
    let number = |i: usize| args.get(i).and_then(Value::as_f64);
    Ok(match name {
        "minimize" => {
            window.set_minimized(true);
            json!(true)
        }
        "toggle_maximize" => {
            window.set_maximized(!window.is_maximized());
            json!(window.is_maximized())
        }
        "start_window_drag" => json!(window.drag_window().is_ok()),
        "start_resize" => {
            let d = args
                .first()
                .and_then(Value::as_str)
                .and_then(direction)
                .ok_or("Bord inconnu.")?;
            json!(window.drag_resize_window(d).is_ok())
        }
        "is_fullscreen" => json!(window.fullscreen().is_some()),
        "toggle_fullscreen" => {
            let on = window.fullscreen().is_none();
            window.set_fullscreen(on.then_some(Fullscreen::Borderless(None)));
            json!(on)
        }
        "enter_fullscreen" => {
            window.set_fullscreen(Some(Fullscreen::Borderless(None)));
            json!(true)
        }
        "exit_fullscreen" => {
            window.set_fullscreen(None);
            json!(false)
        }
        "close" => {
            *quit = true;
            json!(true)
        }
        "set_bounds" | "resize" => {
            let (x, y, w, h) = if name == "resize" {
                (None, None, number(0), number(1))
            } else {
                (number(0), number(1), number(2), number(3))
            };
            if let (Some(x), Some(y)) = (x, y) {
                window.set_outer_position(LogicalPosition::new(x, y));
            }
            if let (Some(w), Some(h)) = (w, h) {
                window.set_inner_size(LogicalSize::new(w.max(640.0), h.max(420.0)));
            }
            state(window)
        }
        "get_state" => state(window),
        "platform" => json!(std::env::consts::OS),
        "choose_manga_folder" => match rfd::FileDialog::new().set_parent(window).pick_folder() {
            None => json!({"cancelled": true}),
            Some(path) => {
                let db = kino::db::Db::open(&kino::paths::database()).map_err(|e| e.to_string())?;
                kino::manga::local::set_folder(&db, &path.to_string_lossy())
                    .map_err(|e| format!("{e:?}"))?
            }
        },
        "export_playback_diagnostic" => {
            // Numbers only: nothing about the title or the source leaves.
            let allowed = [
                "schema",
                "position",
                "duration",
                "paused",
                "muted",
                "volume",
                "readyState",
                "networkState",
                "mediaError",
                "compatibleStream",
                "audioTrackCount",
                "selectedAudioIndex",
                "subtitleCueCount",
                "subtitleDelay",
                "sourceAttempts",
            ];
            let report = args
                .first()
                .and_then(Value::as_object)
                .ok_or("Rapport invalide")?;
            let mut safe: serde_json::Map<String, Value> = report
                .iter()
                .filter(|(k, v)| {
                    allowed.contains(&k.as_str())
                        && (v.is_null() || v.is_boolean() || v.as_f64().is_some_and(f64::is_finite))
                })
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            safe.insert("player".into(), json!("KINO internal"));
            match rfd::FileDialog::new()
                .set_parent(window)
                .set_file_name("KINO-diagnostic.json")
                .add_filter("JSON", &["json"])
                .save_file()
            {
                None => json!(false),
                Some(path) => json!(
                    std::fs::write(
                        path,
                        serde_json::to_string_pretty(&Value::Object(safe)).unwrap_or_default()
                    )
                    .is_ok()
                ),
            }
        }
        // The updates stay with the Python window for now.
        "update_status" | "check_for_updates" => {
            json!({"state": "unavailable", "message": "Mises à jour : passez par l'app KINO habituelle."})
        }
        _ => return Err("Pas encore disponible dans cette fenêtre.".into()),
    })
}

/// A poster for « À l'écoute »: https only, 3 MB at most.
#[cfg(target_os = "macos")]
fn fetch_poster(url: &str) -> Option<Vec<u8>> {
    if !url.starts_with("https://") {
        return None;
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .ok()?;
    runtime.block_on(async {
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(6))
            .build()
            .ok()?;
        let answer = client
            .get(url)
            .header("User-Agent", "KINO")
            .send()
            .await
            .ok()?;
        let bytes = answer.bytes().await.ok()?;
        (bytes.len() <= 3 * 1024 * 1024).then(|| bytes.to_vec())
    })
}

fn main() -> Result<()> {
    let port = free_port(8080);
    let private_port = free_port(port + 1);
    let token = kino::launch::new_token();
    let mut python = start_python(private_port, port, &token);
    let legacy = python
        .as_ref()
        .map(|_| format!("http://127.0.0.1:{private_port}"));
    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy: EventLoopProxy<UserEvent> = event_loop.create_proxy();
    let bounds: Bounds = Default::default();
    let hooks: Arc<dyn kino::player::WindowHooks> = Arc::new(Hooks {
        proxy: Mutex::new(event_loop.create_proxy()),
        bounds: bounds.clone(),
    });
    #[cfg(target_os = "macos")]
    let notifier: Option<Arc<dyn kino::alerts::Notifier>> = {
        let clicks = Mutex::new(event_loop.create_proxy());
        mac_notify::create(Box::new(move |info| {
            if let Ok(p) = clicks.lock() {
                let _ = p.send_event(UserEvent::Notification(Value::Object(info)));
            }
        }))
        .map(|n| Arc::new(n) as Arc<dyn kino::alerts::Notifier>)
    };
    #[cfg(not(target_os = "macos"))]
    let notifier: Option<Arc<dyn kino::alerts::Notifier>> = None;
    let remote: Arc<kino::remote::Remote> = Default::default();
    let options = kino::launch::Options {
        port,
        pairing_token: token,
        legacy,
        native_player: false,
        notifications: false,
        startup_tasks: true,
        window: Some(hooks),
        notifier,
        remote: Some(remote.clone()),
    };
    let (ready_tx, ready_rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let runtime = tokio::runtime::Runtime::new().expect("runtime");
        runtime.block_on(async move {
            match kino::launch::bind(port).await {
                Ok(listener) => {
                    let _ = ready_tx.send(true);
                    if let Err(e) = kino::launch::serve(listener, options).await {
                        eprintln!("KINO server stopped: {e}");
                    }
                }
                Err(_) => {
                    let _ = ready_tx.send(false);
                }
            }
        });
    });
    if !ready_rx.recv().unwrap_or(false) {
        anyhow::bail!("KINO's port is taken");
    }

    let (width, height, at) = saved_bounds();
    let mut builder = WindowBuilder::new()
        .with_title("KINO")
        .with_inner_size(LogicalSize::new(width, height))
        .with_min_inner_size(LogicalSize::new(640.0, 420.0));
    if let Some((x, y)) = at {
        builder = builder.with_position(LogicalPosition::new(x, y));
    }
    #[cfg(target_os = "macos")]
    {
        // Native buttons over KINO's own bar, as the Python window shows them.
        use tao::platform::macos::WindowBuilderExtMacOS;
        builder = builder
            .with_titlebar_transparent(true)
            .with_title_hidden(true)
            .with_fullsize_content_view(true);
    }
    #[cfg(not(target_os = "macos"))]
    {
        builder = builder.with_decorations(false);
    }
    let window = builder.build(&event_loop)?;
    *bounds.lock().unwrap_or_else(|e| e.into_inner()) = bounds_of(&window);
    let webview = WebViewBuilder::new()
        .with_url(format!("http://127.0.0.1:{port}/"))
        .with_initialization_script(bridge_script())
        .with_background_color((9, 9, 11, 255))
        .with_ipc_handler(move |request| {
            let Ok(message) = serde_json::from_str::<Value>(request.body()) else {
                return;
            };
            let id = message["id"].as_u64().unwrap_or(0);
            let name = message["name"].as_str().unwrap_or("").to_string();
            let args = message["args"].as_array().cloned().unwrap_or_default();
            let _ = proxy.send_event(UserEvent::Call(id, name, args));
        })
        .build(&window)?;

    #[cfg(target_os = "macos")]
    let mut now_playing = {
        let posters = Mutex::new(event_loop.create_proxy());
        objc2::MainThreadMarker::new().and_then(|main| {
            mac_media::NowPlaying::create(
                main,
                remote.clone(),
                Box::new(move |url: String| {
                    let proxy = posters.lock().ok().map(|p| (*p).clone());
                    std::thread::spawn(move || {
                        let bytes = fetch_poster(&url).unwrap_or_default();
                        if let Some(p) = proxy {
                            let _ = p.send_event(UserEvent::Poster(url, bytes));
                        }
                    });
                }),
            )
        })
    };
    let mut next_tick = std::time::Instant::now();

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        #[cfg(target_os = "macos")]
        if let Some(center) = now_playing.as_mut() {
            if std::time::Instant::now() >= next_tick {
                center.tick();
                next_tick = std::time::Instant::now() + std::time::Duration::from_secs(1);
            }
            *control_flow = ControlFlow::WaitUntil(next_tick);
        }
        let mut quit = false;
        match event {
            Event::UserEvent(UserEvent::Call(id, name, args)) => {
                let (ok, value) = match call(&window, &name, &args, &mut quit) {
                    Ok(v) => (true, v),
                    Err(e) => (false, json!(e)),
                };
                let _ =
                    webview.evaluate_script(&format!("window.__kinoReply({id}, {ok}, {value})"));
            }
            Event::UserEvent(UserEvent::Action(name)) => {
                let name = match name.as_str() {
                    "maximize" => "toggle_maximize",
                    "fullscreen" => "toggle_fullscreen",
                    "hide" | "show" => {
                        window.set_visible(name == "show");
                        ""
                    }
                    other => other,
                };
                if !name.is_empty() {
                    let _ = call(&window, name, &[], &mut quit);
                }
            }
            Event::UserEvent(UserEvent::Notification(info)) => {
                window.set_visible(true);
                window.set_focus();
                if info["id"].is_string() {
                    let _ = webview.evaluate_script(&format!("openFromNotification({info})"));
                }
            }
            #[cfg(target_os = "macos")]
            Event::UserEvent(UserEvent::Poster(url, bytes)) => {
                if let Some(center) = now_playing.as_mut() {
                    center.poster(url, bytes);
                }
            }
            Event::UserEvent(UserEvent::Hide) => window.set_visible(false),
            Event::UserEvent(UserEvent::Show) => {
                window.set_visible(true);
                window.set_focus();
            }
            Event::WindowEvent {
                event: WindowEvent::Moved(_) | WindowEvent::Resized(_),
                ..
            } => *bounds.lock().unwrap_or_else(|e| e.into_inner()) = bounds_of(&window),
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => quit = true,
            _ => {}
        }
        if quit {
            save_bounds(&window);
            if let Some(mut child) = python.take() {
                drop(child.stdin.take());
                let _ = child.wait();
            }
            *control_flow = ControlFlow::Exit;
        }
    });
}
