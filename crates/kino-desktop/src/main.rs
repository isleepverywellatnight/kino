#![cfg_attr(windows, windows_subsystem = "windows")]
//! KINO: the window (the page in a web view and the bridge it calls,
//! window.pywebview.api), KINO's server in this same process, and what the
//! desktop gives the server: the players, notifications, media keys, the
//! menu bar, updates, one instance at a time.

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};

use anyhow::Result;
use serde_json::{Value, json};
use tao::dpi::{LogicalPosition, LogicalSize};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy};
use tao::window::{Fullscreen, ResizeDirection, Window, WindowBuilder};
use wry::WebViewBuilder;

#[cfg(target_os = "macos")]
use objc2::rc::Retained;

#[cfg(target_os = "macos")]
mod mac_media;
#[cfg(target_os = "macos")]
mod mac_mpv;
#[cfg(target_os = "macos")]
mod mac_notify;
#[cfg(target_os = "macos")]
mod mac_window;
#[cfg(windows)]
mod win_dock;
#[cfg(windows)]
mod win_frame;

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
    /// The answer to a bridge call finished off the main thread.
    Reply(u64, Result<Value, String>),
    /// A window action the page asked through the server (/api/window/action).
    Action(String),
    /// Windows: mpv's window (a handle) to hold inside KINO's, then let go.
    #[cfg_attr(not(windows), allow(dead_code))]
    Dock(isize),
    #[cfg_attr(not(windows), allow(dead_code))]
    Undock,
    /// A notification was clicked: KINO comes to the front and opens the series.
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    Notification(Value),
    /// The native player's work on the main thread (open, close).
    #[cfg(target_os = "macos")]
    Native(mac_mpv::MainCall),
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
    fn can_dock(&self) -> bool {
        cfg!(windows)
    }
    #[cfg(windows)]
    fn dock(&self, pid: u32) {
        let proxy = self.proxy.lock().ok().map(|p| (*p).clone());
        std::thread::spawn(move || {
            if let (Some(player), Some(p)) = (win_dock::find(pid), proxy) {
                let _ = p.send_event(UserEvent::Dock(player));
            }
        });
    }
    fn undock(&self) {
        if let Ok(p) = self.proxy.lock() {
            let _ = p.send_event(UserEvent::Undock);
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

/// Maximized: by KINO on Windows (within the work area), by the system elsewhere.
fn maximized(window: &Window) -> bool {
    #[cfg(windows)]
    {
        use tao::platform::windows::WindowExtWindows;
        win_frame::is_maximized(window.hwnd())
    }
    #[cfg(not(windows))]
    window.is_maximized()
}

fn set_maximized(window: &Window, on: bool) {
    #[cfg(windows)]
    {
        use tao::platform::windows::WindowExtWindows;
        win_frame::set_maximized(window.hwnd(), on);
    }
    #[cfg(not(windows))]
    window.set_maximized(on);
}

fn save_bounds(window: &Window) {
    if maximized(window) || window.fullscreen().is_some() {
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
    json!({"maximized": maximized(window), "fullscreen": window.fullscreen().is_some(), "width": size.width.round(),
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
            set_maximized(window, !maximized(window));
            json!(maximized(window))
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
        _ => return Err("Pas encore disponible dans cette fenêtre.".into()),
    })
}

/// At launch: the leftovers of finished updates (once the new version is
/// confirmed), then a quiet look at GitHub for a new version.
fn start_update_tasks(runtime: &tokio::runtime::Handle, updater: Arc<kino::update::Updater>) {
    let cleaner = updater.clone();
    runtime.spawn_blocking(move || {
        if !cleaner.is_installed() {
            return;
        }
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(180);
        while cleaner.state_dir.join("pending.json").exists()
            && std::time::Instant::now() < deadline
        {
            std::thread::sleep(std::time::Duration::from_secs(2));
        }
        kino::update::prune_leftovers(
            &cleaner.app_path,
            &cleaner.state_dir,
            kino::backup::app_version(),
            kino::db::now(),
        );
    });
    runtime.spawn(async move {
        tokio::time::sleep(std::time::Duration::from_secs(20)).await;
        let _ = updater.check_latest().await;
    });
}

/// The bridge's update calls. Quick ones answer at once (Some); the others
/// answer later through `replies` (None).
fn updates(
    name: &str,
    updater: &Arc<kino::update::Updater>,
    runtime: &tokio::runtime::Handle,
    remote: &Arc<kino::remote::Remote>,
    replies: &EventLoopProxy<UserEvent>,
    id: u64,
) -> Option<Result<Value, String>> {
    let later = |work: Box<dyn FnOnce() -> Result<Value, String> + Send>, close_after: bool| {
        let replies = replies.clone();
        runtime.spawn_blocking(move || {
            let result = work();
            let done = result.is_ok();
            let _ = replies.send_event(UserEvent::Reply(id, result));
            if done && close_after {
                std::thread::sleep(std::time::Duration::from_millis(400));
                let _ = replies.send_event(UserEvent::Action("close".into()));
            }
        });
        None
    };
    // An update never interrupts a film: the player must be closed first.
    let playing = || {
        let state = remote.state();
        state.get("playing").and_then(Value::as_bool) == Some(true)
            && kino::db::now()
                - state
                    .get("updated_at")
                    .and_then(Value::as_f64)
                    .unwrap_or(0.0)
                < 5.0
    };
    match name {
        "update_status" => Some(Ok(updater.status())),
        "open_release_page" => {
            let page = updater.status()["latest"]["page"]
                .as_str()
                .filter(|p| {
                    p.starts_with(&format!(
                        "https://github.com/{}/releases/",
                        kino::update::UPDATE_REPO
                    ))
                })
                .map(str::to_string)
                .unwrap_or_else(kino::update::releases_page);
            let program = if cfg!(windows) {
                "explorer.exe"
            } else if cfg!(target_os = "macos") {
                "open"
            } else {
                "xdg-open"
            };
            let _ = Command::new(program).arg(page).spawn();
            Some(Ok(json!({"opened": true})))
        }
        "check_for_updates" => {
            let (updater, replies) = (updater.clone(), replies.clone());
            runtime.spawn(async move {
                let result = updater.check_for_updates().await;
                let _ = replies.send_event(UserEvent::Reply(id, Ok(result)));
            });
            None
        }
        "choose_update_package" => {
            let Some(path) = rfd::FileDialog::new()
                .add_filter("Mise à jour KINO", &["kinoupdate"])
                .pick_file()
            else {
                return Some(Ok(json!({"cancelled": true})));
            };
            let updater = updater.clone();
            later(Box::new(move || updater.prepare(&path)), false)
        }
        "install_update" | "rollback_update" => {
            if playing() {
                let what = if name == "install_update" {
                    "installer la mise à jour"
                } else {
                    "revenir à l'ancienne version"
                };
                return Some(Ok(
                    json!({"deferred": true, "message": format!("Une lecture est en cours. Fermez le lecteur pour {what}.")}),
                ));
            }
            let updater = updater.clone();
            let install = name == "install_update";
            later(
                Box::new(move || {
                    if install {
                        updater.install()
                    } else {
                        updater.rollback()
                    }
                }),
                true,
            )
        }
        _ => Some(Err("Pas encore disponible dans cette fenêtre.".into())),
    }
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

/// Whether this program runs from an installed KINO (an app bundle or an
/// installation folder), not from a build in the repository.
fn packaged() -> bool {
    let exe = std::env::current_exe().unwrap_or_default();
    !exe.components().any(|c| c.as_os_str() == "target")
}

/// The interface, the update key, FFmpeg and the player, as shipped.
fn self_check() -> Result<Value, String> {
    let web = kino::paths::web_root();
    for file in [
        "index.html",
        "remote.html",
        "js/vendor/qrcode.min.js",
        "js/playback-help.js",
        "js/vendor/peerjs.min.js",
        "js/i18n.js",
        "js/i18n/en.js",
    ] {
        if !web.join(file).is_file() {
            return Err(format!("The interface is incomplete: {file} is missing"));
        }
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let contents = if cfg!(target_os = "macos") {
        exe.parent().and_then(std::path::Path::parent)
    } else {
        exe.parent()
    }
    .map(std::path::Path::to_path_buf)
    .unwrap_or_default();
    let inside = |path: &std::path::Path| {
        path.canonicalize()
            .is_ok_and(|p| contents.canonicalize().is_ok_and(|c| p.starts_with(c)))
    };
    let ffmpeg = kino::intro::find_tool("ffmpeg").ok_or("FFmpeg is missing")?;
    if packaged() && !inside(&ffmpeg) {
        return Err("FFmpeg is outside the application".into());
    }
    let runs = |program: &std::path::Path, flag: &str| {
        let mut command = Command::new(program);
        command
            .arg(flag)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        std::os::windows::process::CommandExt::creation_flags(&mut command, 0x0800_0000);
        command.status().is_ok_and(|s| s.success())
    };
    if !runs(&ffmpeg, "-version") {
        return Err("FFmpeg does not run".into());
    }
    // Completed below on macOS (libmpv) and Windows (mpv).
    #[cfg_attr(not(any(target_os = "macos", windows)), allow(unused_mut))]
    let mut result = json!({"ok": true, "version": kino::backup::app_version(), "packaged": packaged(), "platform": std::env::consts::OS,
                            "ffmpeg": ffmpeg.display().to_string(), "web": web.display().to_string()});
    let key = [
        exe.parent()
            .map(|d| d.join("../Resources/assets/update_public_key.hex")),
        exe.parent().map(|d| d.join("assets/update_public_key.hex")),
    ];
    if packaged() && !key.iter().flatten().any(|k| k.is_file()) {
        return Err("The update key is missing".into());
    }
    #[cfg(target_os = "macos")]
    {
        let mpv = mac_mpv::version();
        if packaged() && mpv.is_none() {
            return Err("libmpv is missing from the application".into());
        }
        result["libmpv"] = json!(mpv);
        let helper = contents.join("Resources/kino-update-helper");
        if packaged() && !helper.is_file() {
            return Err("The update helper is missing".into());
        }
    }
    #[cfg(windows)]
    {
        let mpv = contents.join("mpv").join("mpv.exe");
        if packaged() && !(mpv.is_file() && runs(&mpv, "--version")) {
            return Err("mpv is missing from the installation folder".into());
        }
        result["mpv"] = json!(mpv.display().to_string());
    }
    // KINO's data opens, in a throwaway folder.
    let scratch = std::env::temp_dir().join(format!("kino-self-check-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&scratch);
    let opened = kino::db::Db::open(&scratch.join("kino.db")).map(|_| ());
    let _ = std::fs::remove_dir_all(&scratch);
    opened.map_err(|e| format!("The database does not open: {e}"))?;
    Ok(result)
}

/// One KINO at a time: a second launch brings the first one forward and quits.
/// Returns the held lock (kept for the process's life), or None to quit.
#[cfg(unix)]
fn single_instance() -> Option<std::fs::File> {
    use std::io::{Read, Seek, Write};
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    let home = kino::paths::home();
    let dir = if cfg!(target_os = "macos") {
        home.join("Library/Application Support/KINO")
    } else {
        home.join(".local/share/kino")
    };
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join("instance.lock");
    let Ok(mut file) = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .custom_flags(libc::O_NOFOLLOW)
        .mode(0o600)
        .open(&path)
    else {
        return None;
    };
    let _ = file.set_permissions(std::fs::Permissions::from_mode(0o600));
    // SAFETY: flock on a file this process opened.
    if unsafe {
        libc::flock(
            std::os::fd::AsRawFd::as_raw_fd(&file),
            libc::LOCK_EX | libc::LOCK_NB,
        )
    } != 0
    {
        let mut pid = String::new();
        let _ = file.read_to_string(&mut pid);
        #[cfg(target_os = "macos")]
        if let Ok(pid) = pid.trim().parse::<i32>() {
            {
                if let Some(running) =
                    objc2_app_kit::NSRunningApplication::runningApplicationWithProcessIdentifier(
                        pid,
                    )
                {
                    running.activateWithOptions(
                        objc2_app_kit::NSApplicationActivationOptions::ActivateAllWindows,
                    );
                }
            }
        }
        return None;
    }
    let _ = file.set_len(0);
    let _ = file.rewind();
    let _ = write!(file, "{}", std::process::id());
    Some(file)
}

#[cfg(windows)]
fn single_instance() -> Option<()> {
    use windows_sys::Win32::Foundation::{ERROR_ALREADY_EXISTS, GetLastError};
    use windows_sys::Win32::System::Threading::CreateMutexW;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        FindWindowW, SW_RESTORE, SetForegroundWindow, ShowWindow,
    };
    let wide = |s: &str| {
        s.encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<u16>>()
    };
    let name = wide("Local\\KINO_SINGLE_INSTANCE_MUTEX");
    // SAFETY: Win32 calls with NUL-terminated wide strings; the mutex lives as long as KINO.
    unsafe {
        let _mutex = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
        if GetLastError() == ERROR_ALREADY_EXISTS {
            let window = FindWindowW(std::ptr::null(), wide("KINO").as_ptr());
            if !window.is_null() {
                ShowWindow(window, SW_RESTORE);
                SetForegroundWindow(window);
            }
            return None;
        }
    }
    Some(())
}

fn main() -> Result<()> {
    // The build's self-check: what KINO ships, checked before a release or an update.
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--self-check") {
        let report = args
            .iter()
            .position(|a| a == "--self-check-report")
            .and_then(|i| args.get(i + 1))
            .map(PathBuf::from);
        let result = self_check();
        let ok = result.is_ok();
        let answer = result.unwrap_or_else(|e| json!({"ok": false, "error": e}));
        println!("{answer}");
        if let Some(path) = report {
            let _ = std::fs::write(path, answer.to_string());
        }
        std::process::exit(if ok { 0 } else { 1 });
    }
    let Some(_instance) = single_instance() else {
        return Ok(());
    };
    let port = free_port(8080);
    let token = kino::launch::new_token();
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
    #[cfg(target_os = "macos")]
    let native: Option<Arc<dyn kino::native::NativePlayer>> = mac_mpv::available().then(|| {
        let calls = event_loop.create_proxy();
        Arc::new(mac_mpv::Controller::new(Box::new(move |call| {
            let _ = calls.send_event(UserEvent::Native(call));
        }))) as Arc<dyn kino::native::NativePlayer>
    });
    #[cfg(not(target_os = "macos"))]
    let native: Option<Arc<dyn kino::native::NativePlayer>> = None;
    let options = kino::launch::Options {
        port,
        pairing_token: token,
        legacy: None,
        native_player: false,
        notifications: false,
        startup_tasks: true,
        window: Some(hooks),
        notifier,
        remote: Some(remote.clone()),
        native,
    };
    let (ready_tx, ready_rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let runtime = tokio::runtime::Runtime::new().expect("runtime");
        let handle = runtime.handle().clone();
        runtime.block_on(async move {
            match kino::launch::bind(port).await {
                Ok(listener) => {
                    let _ = ready_tx.send(Some(handle));
                    if let Err(e) = kino::launch::serve(listener, options).await {
                        eprintln!("KINO server stopped: {e}");
                    }
                }
                Err(_) => {
                    let _ = ready_tx.send(None);
                }
            }
        });
    });
    let Some(runtime) = ready_rx.recv().ok().flatten() else {
        anyhow::bail!("KINO's port is taken");
    };
    let updater = Arc::new(kino::update::Updater::new(
        kino::update::default_app_path(),
        kino::update::default_state_dir(),
        None,
    ));
    start_update_tasks(&runtime, updater.clone());

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
        // Windows: no invisible resize borders around the page (KINO handles resizing).
        #[cfg(windows)]
        {
            use tao::platform::windows::WindowBuilderExtWindows;
            builder = builder.with_undecorated_shadow(false);
        }
    }
    let window = builder.build(&event_loop)?;
    *bounds.lock().unwrap_or_else(|e| e.into_inner()) = bounds_of(&window);
    #[cfg(windows)]
    {
        use tao::platform::windows::WindowExtWindows;
        win_frame::frameless(window.hwnd());
    }
    let builder = WebViewBuilder::new();
    // Windows: WebView2's DevTools port, for the window test (tests/windows_ui.py).
    #[cfg(windows)]
    let builder = match std::env::var("KINO_DEVTOOLS_PORT")
        .ok()
        .and_then(|p| p.parse::<u16>().ok())
    {
        Some(devtools) => {
            use wry::WebViewBuilderExtWindows;
            builder.with_additional_browser_args(format!(
                "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection --remote-debugging-port={devtools}"
            ))
        }
        None => builder,
    };
    let webview = builder
        .with_url(format!("http://127.0.0.1:{port}/"))
        .with_initialization_script(bridge_script())
        .with_background_color((9, 9, 11, 255))
        .with_on_page_load_handler({
            let updater = updater.clone();
            move |event, _url| {
                if matches!(event, wry::PageLoadEvent::Finished) {
                    let (app, state) = (updater.app_path.clone(), updater.state_dir.clone());
                    std::thread::spawn(move || kino::update::acknowledge_ready(&app, &state));
                }
            }
        })
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

    let replies = event_loop.create_proxy();
    // Windows: the mpv window held inside KINO's, if any.
    #[cfg(windows)]
    let mut docked: Option<isize> = None;
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
    #[cfg(target_os = "macos")]
    let mut next_tick = std::time::Instant::now();
    #[cfg(target_os = "macos")]
    let mut native_side = mac_mpv::MainSide::default();
    #[cfg(target_os = "macos")]
    let page_view: Retained<objc2_app_kit::NSView> = {
        use wry::WebViewExtMacOS;
        // SAFETY: the WKWebView is an NSView.
        unsafe { Retained::cast_unchecked(webview.webview()) }
    };
    #[cfg(target_os = "macos")]
    mac_window::setup(&window, &page_view);

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
            Event::UserEvent(UserEvent::Call(id, name, _))
                if name.contains("update") || name == "open_release_page" =>
            {
                let done = updates(&name, &updater, &runtime, &remote, &replies, id);
                if let Some(result) = done {
                    let _ = replies.send_event(UserEvent::Reply(id, result));
                }
            }
            Event::UserEvent(UserEvent::Reply(id, result)) => {
                let (ok, value) = match result {
                    Ok(v) => (true, v),
                    Err(e) => (false, json!(e)),
                };
                let _ =
                    webview.evaluate_script(&format!("window.__kinoReply({id}, {ok}, {value})"));
            }
            Event::UserEvent(UserEvent::Call(id, name, args)) => {
                let (ok, value) = match call(&window, &name, &args, &mut quit) {
                    Ok(v) => (true, v),
                    Err(e) => (false, json!(e)),
                };
                let _ =
                    webview.evaluate_script(&format!("window.__kinoReply({id}, {ok}, {value})"));
            }
            Event::UserEvent(UserEvent::Action(name)) => {
                if let Some(on) = match name.as_str() {
                    "maximized_on" => Some(true),
                    "maximized_off" => Some(false),
                    _ => None,
                } {
                    set_maximized(&window, on);
                }
                let name = match name.as_str() {
                    "maximize" => "toggle_maximize",
                    "maximized_on" | "maximized_off" => "",
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
            Event::UserEvent(UserEvent::Native(call)) => match call {
                mac_mpv::MainCall::Open(player, path, reply) => {
                    let _ = reply.send(native_side.open(&page_view, player, &path));
                }
                mac_mpv::MainCall::Close(reply) => {
                    native_side.close(&page_view);
                    let _ = reply.send(());
                }
            },
            #[cfg(target_os = "macos")]
            Event::UserEvent(UserEvent::Poster(url, bytes)) => {
                if let Some(center) = now_playing.as_mut() {
                    center.poster(url, bytes);
                }
            }
            #[cfg(windows)]
            Event::UserEvent(UserEvent::Dock(player)) => {
                use tao::platform::windows::WindowExtWindows;
                win_dock::dock(window.hwnd(), player);
                docked = Some(player);
            }
            Event::UserEvent(UserEvent::Undock) => {
                #[cfg(windows)]
                {
                    docked = None;
                }
                // The player's full screen ends with it.
                window.set_fullscreen(None);
                #[cfg(windows)]
                {
                    use tao::platform::windows::WindowExtWindows;
                    win_dock::refresh(window.hwnd());
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
            } => {
                *bounds.lock().unwrap_or_else(|e| e.into_inner()) = bounds_of(&window);
                #[cfg(windows)]
                if let Some(player) = docked {
                    use tao::platform::windows::WindowExtWindows;
                    win_dock::fit(window.hwnd(), player);
                }
            }
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => quit = true,
            _ => {}
        }
        if quit {
            save_bounds(&window);
            *control_flow = ControlFlow::Exit;
        }
    });
}
