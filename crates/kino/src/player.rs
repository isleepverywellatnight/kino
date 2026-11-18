//! The external player (mpv, IINA or VLC): KINO's languages, sound, HDR and
//! resume point, an M3U playlist for the next episodes, the window hidden while
//! it plays, progress saved every 3 s, subtitles added and the Discord card.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{Map, Value, json};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

use crate::config::{AudioMode, HdrMode, PrefLang};
use crate::db::Db;
use crate::discord::{Discord, Playing};
use crate::meta::Meta;
use crate::play::text;

#[cfg(windows)]
const IPC: &str = r"\\.\pipe\kino_mpv";
#[cfg(not(windows))]
const IPC: &str = "/tmp/kino_mpv.sock";

/// The player's control socket (`KINO_MPV_IPC` moves it, for tests).
pub fn ipc() -> String {
    std::env::var("KINO_MPV_IPC")
        .ok()
        .filter(|p| !p.is_empty())
        .unwrap_or_else(|| IPC.into())
}

/// What the window does for the player (the desktop app provides it).
pub trait WindowHooks: Send + Sync {
    fn hide(&self);
    fn show(&self);
    /// (x, y, width, height) of the window, for the player to open at its place.
    fn geometry(&self) -> Option<(i32, i32, u32, u32)>;
    /// A window action asked by the page: minimize, maximize, fullscreen,
    /// enter_fullscreen, exit_fullscreen, close, hide, show.
    fn action(&self, _name: &str) {}
    /// Windows: whether KINO's window can hold the player inside it.
    fn can_dock(&self) -> bool {
        false
    }
    /// Holds the player's window (process `pid`) inside KINO's window.
    fn dock(&self, _pid: u32) {}
    /// The docked player closed: KINO's window as before.
    fn undock(&self) {}
}

/// What the history and the Discord card are told about the title.
#[derive(Clone, Debug, Default)]
pub struct Media {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub year: String,
    pub poster: String,
    pub season: Option<i64>,
    pub episode: Option<i64>,
    pub filename: String,
}

impl Media {
    pub fn entry(&self, episode: Option<i64>, position: f64, duration: f64) -> Map<String, Value> {
        let mut entry = Map::new();
        entry.insert("id".into(), json!(self.id));
        entry.insert(
            "name".into(),
            json!(if self.name.is_empty() {
                "KINO"
            } else {
                &self.name
            }),
        );
        entry.insert(
            "type".into(),
            json!(if self.kind.is_empty() {
                "movie"
            } else {
                &self.kind
            }),
        );
        entry.insert("year".into(), json!(self.year));
        entry.insert("poster".into(), json!(self.poster));
        let series = self.kind == "series";
        entry.insert(
            "season".into(),
            json!(series.then(|| self.season.unwrap_or(1))),
        );
        entry.insert("episode".into(), json!(episode));
        entry.insert("filename".into(), json!(self.filename));
        entry.insert("position".into(), json!(position as i64));
        entry.insert("duration".into(), json!(duration as i64));
        entry
    }

    /// The fields the history keeps when the player starts (no position yet).
    pub fn start_entry(&self) -> Map<String, Value> {
        let mut entry = Map::new();
        for (k, v) in [
            ("id", &self.id),
            ("name", &self.name),
            ("type", &self.kind),
            ("year", &self.year),
            ("poster", &self.poster),
            ("filename", &self.filename),
        ] {
            entry.insert(k.into(), json!(v));
        }
        entry.insert("season".into(), json!(self.season));
        entry.insert("episode".into(), json!(self.episode));
        entry
    }
}

pub struct Launch {
    pub url: String,
    pub title: String,
    /// (title, url) of each file when there are several episodes.
    pub playlist: Vec<(String, String)>,
    pub media: Option<Media>,
    pub start_sec: i64,
}

#[derive(Default)]
struct Last {
    at: Option<Instant>,
    url: String,
    answer: Option<Value>,
}

pub struct Player {
    db: Arc<Db>,
    meta: Meta,
    pub discord: Arc<Discord>,
    window: Option<Arc<dyn WindowHooks>>,
    last: Mutex<Last>,
    current: Arc<Mutex<u32>>,
}

static HDR: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| {
    Regex::new(r"(?i)\b(hdr|hdr10|hdr10\+|dv|dovi|dolby[\s.\-]*vision|hlg)\b").unwrap()
});

fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .flat_map(|dir| {
            let plain = dir.join(name);
            let exe = dir.join(format!("{name}.exe"));
            [plain, exe]
        })
        .find(|p| p.is_file())
}

/// The player to start: the one set in KINO's settings, the one shipped with
/// KINO on Windows, then mpv, IINA or VLC where they usually are. The
/// setting is read as written (`mpv_path` is not one Paramètres edits).
pub fn find() -> Option<PathBuf> {
    let home = crate::paths::home();
    let mut candidates: Vec<Option<PathBuf>> = vec![
        crate::config::load_raw()
            .get("mpv_path")
            .map(text)
            .filter(|p| !p.is_empty())
            .map(PathBuf::from),
    ];
    if let Ok(exe) = std::env::current_exe()
        && let Some(dir) = exe.parent()
    {
        candidates.push(Some(dir.join("mpv").join("mpv.exe")));
    }
    candidates.extend([which("mpv"), which("iina-cli"), which("iina")]);
    let standard = [
        "/opt/homebrew/bin/mpv",
        "/usr/local/bin/mpv",
        "/Applications/mpv.app/Contents/MacOS/mpv",
        "/Applications/IINA.app/Contents/MacOS/iina-cli",
        "/Applications/IINA.app/Contents/MacOS/IINA",
        "/Applications/VLC.app/Contents/MacOS/VLC",
        r"C:\Program Files\mpv\mpv.exe",
        r"C:\Program Files (x86)\mpv\mpv.exe",
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    ];
    candidates.extend(standard.iter().map(|p| Some(PathBuf::from(p))));
    candidates.push(Some(home.join("scoop").join("shims").join("mpv.exe")));
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        candidates.push(Some(
            PathBuf::from(local).join(r"Microsoft\WinGet\Links\mpv.exe"),
        ));
    }
    // An mpv unpacked in Downloads, the newest first.
    let mut unpacked: Vec<PathBuf> = std::fs::read_dir(home.join("Downloads"))
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.file_name().to_string_lossy().starts_with("mpv"))
        .map(|e| e.path().join("mpv.exe"))
        .collect();
    unpacked.sort();
    unpacked.reverse();
    candidates.extend(unpacked.into_iter().map(Some));
    candidates.into_iter().flatten().find(|p| p.exists())
}

struct Plan {
    alang: &'static str,
    slang: &'static str,
    hdr_mode: HdrMode,
    audio_mode: AudioMode,
    hdr_media: bool,
    input_conf: Option<String>,
    start_sec: i64,
    playlist: bool,
    title: String,
    target: String,
}

/// Sound, picture and resume: the same for mpv (« -- ») and IINA (« --mpv- »).
fn shared_options(prefix: &str, plan: &Plan) -> Vec<String> {
    let mut args = vec![];
    if plan.audio_mode == AudioMode::VoiceBoost {
        args.push(format!(
            "{prefix}af=lavfi=[dynaudnorm=f=180:g=13:p=0.92:m=4.5]"
        ));
    }
    if plan.hdr_mode != HdrMode::HdrNative {
        args.push(format!("{prefix}tone-mapping=bt.2446a"));
        args.push(format!("{prefix}hdr-compute-peak=yes"));
        if plan.hdr_mode == HdrMode::HdrBoost {
            args.extend([
                format!("{prefix}gamma=9"),
                format!("{prefix}brightness=3"),
                format!("{prefix}contrast=2"),
            ]);
        } else if plan.hdr_media {
            args.extend([format!("{prefix}gamma=6"), format!("{prefix}brightness=2")]);
        }
    }
    if plan.start_sec > 5 {
        args.push(format!("{prefix}start={}", plan.start_sec));
    }
    args
}

fn iina_command(bin: &Path, plan: &Plan) -> (PathBuf, Vec<String>) {
    let mut bin = bin.to_path_buf();
    if bin.ends_with("IINA") {
        let cli = bin.with_file_name("iina-cli");
        if cli.exists() {
            bin = cli;
        }
    }
    if cfg!(target_os = "macos") && matches!(plan.hdr_mode, HdrMode::SdrPref | HdrMode::HdrBoost) {
        let _ = std::process::Command::new("defaults")
            .args([
                "write",
                "com.colliderli.iina",
                "enableToneMapping",
                "-bool",
                "true",
            ])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
    let mut args = vec![
        "--no-stdin".to_string(),
        "--keep-running".into(),
        "--mpv-hwdec=videotoolbox".into(),
        format!("--mpv-input-ipc-server={}", ipc()),
        format!("--mpv-alang={}", plan.alang),
        format!("--mpv-slang={}", plan.slang),
    ];
    if let Some(conf) = &plan.input_conf {
        args.push(format!("--mpv-input-conf={conf}"));
    }
    args.extend(shared_options("--mpv-", plan));
    if !plan.playlist {
        args.push(format!("--mpv-force-media-title={}", plan.title));
    }
    args.push(plan.target.clone());
    (bin, args)
}

fn mpv_command(bin: &Path, plan: &Plan, geometry: Option<(i32, i32, u32, u32)>) -> Vec<String> {
    let mut args = vec![];
    let config_dir = bin.with_file_name("portable_config");
    if config_dir.exists() {
        args.push(format!("--config-dir={}", config_dir.display()));
    }
    if cfg!(target_os = "macos") {
        args.push("--hwdec=videotoolbox".into());
    }
    args.push(format!("--input-ipc-server={}", ipc()));
    if let Some(conf) = &plan.input_conf {
        args.push(format!("--input-conf={conf}"));
    }
    args.extend([
        format!("--alang={}", plan.alang),
        format!("--slang={}", plan.slang),
        "--no-border".into(),
        "--border=no".into(),
        "--no-window-dragging".into(),
        "--window-dragging=no".into(),
        "--title=KINO".into(),
        "--force-window=immediate".into(),
    ]);
    args.extend(shared_options("--", plan));
    if let Some((x, y, w, h)) = geometry {
        args.push(format!("--geometry={w}x{h}+{x}+{y}"));
    }
    if !plan.playlist {
        args.push(format!("--force-media-title={}", plan.title));
    }
    args.push(plan.target.clone());
    args
}

/// The file, or an M3U playlist when there are several episodes.
fn playback_target(url: &str, playlist: &[(String, String)]) -> (String, bool) {
    if playlist.len() <= 1 {
        return (url.to_string(), false);
    }
    let mut lines = vec!["#EXTM3U".to_string()];
    for (title, url) in playlist {
        let title = title.replace(['\n', '\r'], " ");
        let title = if title.trim().is_empty() {
            "KINO"
        } else {
            title.trim()
        };
        if !url.trim().is_empty() {
            lines.push(format!("#EXTINF:-1,{title}"));
            lines.push(url.trim().to_string());
        }
    }
    let file = crate::paths::home().join(".kino_playlist.m3u");
    match std::fs::write(&file, lines.join("\n") + "\n") {
        Ok(()) => (file.display().to_string(), true),
        Err(_) => (url.to_string(), false),
    }
}

/// « s » skips the intro (+85 s) in mpv and IINA.
fn input_conf() -> Option<String> {
    if cfg!(windows) {
        return None;
    }
    let path = "/tmp/kino_mpv_input.conf";
    let line = |k: &str| format!("{k} seek 85 exact ; show-text \"Intro passée (+85s)\"\n");
    std::fs::write(path, line("s") + &line("S"))
        .ok()
        .map(|_| path.to_string())
}

#[cfg(unix)]
async fn connect() -> Option<tokio::net::UnixStream> {
    tokio::net::UnixStream::connect(ipc()).await.ok()
}

#[cfg(windows)]
async fn connect() -> Option<tokio::net::windows::named_pipe::NamedPipeClient> {
    tokio::net::windows::named_pipe::ClientOptions::new()
        .open(ipc())
        .ok()
}

/// mpv's bridge script for a docked player (Windows), shipped in assets/.
fn bridge_script() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    [
        dir.join("assets/kino_bridge.lua"),
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../assets/kino_bridge.lua"),
    ]
    .into_iter()
    .find(|p| p.is_file())
}

/// A docked player's full screen, maximize and minimize, passed on to KINO's window.
async fn window_events(window: Arc<dyn WindowHooks>) {
    tokio::time::sleep(Duration::from_millis(350)).await;
    let Some(stream) = connect().await else {
        return;
    };
    let mut stream = BufReader::new(stream);
    for (id, name) in [
        (1, "fullscreen"),
        (2, "window-maximized"),
        (3, "window-minimized"),
    ] {
        let line = json!({"command": ["observe_property", id, name]}).to_string() + "\n";
        if stream.get_mut().write_all(line.as_bytes()).await.is_err() {
            return;
        }
    }
    // The first three changes are the current values, not the user's.
    let mut initial = 3;
    let mut line = String::new();
    while stream.read_line(&mut line).await.is_ok_and(|n| n > 0) {
        if let Ok(message) = serde_json::from_str::<Value>(&line)
            && message["event"] == "property-change"
        {
            if initial > 0 {
                initial -= 1;
            } else {
                match (message["name"].as_str(), message["data"].as_bool()) {
                    (Some("fullscreen"), Some(on)) => window.action(if on {
                        "enter_fullscreen"
                    } else {
                        "exit_fullscreen"
                    }),
                    (Some("window-maximized"), Some(on)) => {
                        window.action(if on { "maximized_on" } else { "maximized_off" })
                    }
                    (Some("window-minimized"), Some(true)) => window.action("minimize"),
                    _ => {}
                }
            }
        }
        line.clear();
    }
}

/// One property of the running player, through its IPC socket.
pub async fn query(property: &str) -> Option<Value> {
    let work = async {
        let mut stream = BufReader::new(connect().await?);
        let line = json!({"command": ["get_property", property]}).to_string() + "\n";
        stream.get_mut().write_all(line.as_bytes()).await.ok()?;
        let mut reply = String::new();
        loop {
            reply.clear();
            if stream.read_line(&mut reply).await.ok()? == 0 {
                return None;
            }
            // Events come on the same socket: the answer is the line with « error ».
            if let Ok(msg) = serde_json::from_str::<Value>(&reply)
                && msg.get("error").is_some()
            {
                return (msg["error"] == "success").then(|| msg["data"].clone());
            }
        }
    };
    tokio::time::timeout(Duration::from_millis(400), work)
        .await
        .ok()
        .flatten()
}

/// A command to the running player (« sub-add », « seek »…).
pub async fn send(command: Value) -> bool {
    let work = async {
        let mut stream = connect().await?;
        let line = json!({"command": command}).to_string() + "\n";
        stream.write_all(line.as_bytes()).await.ok()
    };
    tokio::time::timeout(Duration::from_millis(400), work)
        .await
        .ok()
        .flatten()
        .is_some()
}

impl Player {
    pub fn new(db: Arc<Db>, meta: Meta, window: Option<Arc<dyn WindowHooks>>) -> Arc<Self> {
        let cfg = crate::sources::search::load_settings();
        let enabled = cfg.discord_rpc;
        let client = cfg.discord_client_id.clone();
        Arc::new(Self {
            db,
            meta,
            discord: Arc::new(Discord::start(&client, enabled)),
            window,
            last: Default::default(),
            current: Default::default(),
        })
    }

    pub fn window(&self) -> Option<&Arc<dyn WindowHooks>> {
        self.window.as_ref()
    }

    /// Starts the player; a second launch within seconds gets the first one's answer.
    pub fn launch(self: &Arc<Self>, request: Launch) -> Result<Value, String> {
        let count = request.playlist.len().max(1);
        {
            let mut last = self.last.lock().unwrap_or_else(|e| e.into_inner());
            let since = last
                .at
                .map(|t| t.elapsed().as_secs_f64())
                .unwrap_or(f64::MAX);
            if (since < 3.5 && last.url == request.url || since < 1.2)
                && let Some(answer) = &last.answer
            {
                return Ok(answer.clone());
            }
            last.at = Some(Instant::now());
            last.url = request.url.clone();
            last.answer
                .get_or_insert_with(|| json!({"mpv": "mpv", "pid": 0, "playlist_count": count}));
        }
        let cfg = crate::sources::search::load_settings();
        let bin =
            find().ok_or("Aucun lecteur externe compatible (mpv, IINA ou VLC) n'a été trouvé.")?;
        let title = if request.title.is_empty() {
            "KINO".to_string()
        } else {
            request.title.replace('"', "'")
        };
        if cfg!(unix) {
            let _ = std::fs::remove_file(ipc());
        }
        let (target, playlist) = playback_target(&request.url, &request.playlist);
        let filename = request
            .media
            .as_ref()
            .map(|m| m.filename.as_str())
            .unwrap_or("");
        let plan = Plan {
            alang: if cfg.pref_lang == PrefLang::Vostfr {
                "eng,ja,jpn,fre,fra,fr"
            } else {
                "fre,fra,fr,eng"
            },
            slang: "fre,fra,fr,eng",
            hdr_mode: cfg.hdr_mode,
            audio_mode: cfg.audio_mode,
            hdr_media: HDR.is_match(&format!("{} {filename} {}", request.title, request.url)),
            input_conf: input_conf(),
            start_sec: request.start_sec,
            playlist,
            title: title.clone(),
            target: target.clone(),
        };
        let name = bin.to_string_lossy().to_lowercase();
        let is_mpv = !name.contains("iina") && !name.contains("vlc");
        let docked = cfg!(windows) && is_mpv && self.window.as_ref().is_some_and(|w| w.can_dock());
        let (program, args) = if name.contains("iina") {
            iina_command(&bin, &plan)
        } else if name.contains("vlc") {
            (bin.clone(), vec![target])
        } else if docked {
            // Inside KINO's window: a title to find it by, and the bridge that
            // passes full screen and resizing on to KINO's window.
            let mut args = mpv_command(&bin, &plan, None);
            let at = args.len() - 1;
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_millis());
            args.insert(at, format!("--title=KINO_PLAYER_{stamp}"));
            if let Some(bridge) = bridge_script() {
                args.insert(at, format!("--scripts={}", bridge.display()));
            }
            (bin.clone(), args)
        } else {
            let geometry = self.window.as_ref().and_then(|w| w.geometry());
            (bin.clone(), mpv_command(&bin, &plan, geometry))
        };
        if let Some(window) = self.window.as_ref().filter(|_| !docked) {
            window.hide();
        }
        if let Some(media) = &request.media {
            self.discord.show(&Playing {
                title: if media.name.is_empty() {
                    &title
                } else {
                    &media.name
                },
                kind: &media.kind,
                season: media.season,
                episode: media.episode,
                episode_title: None,
                position: request.start_sec as f64,
                duration: 0.0,
                poster: Some(&media.poster),
                paused: false,
                year: None,
            });
        }
        let pid = match self.start(&program, &args, request.media, docked) {
            Ok(pid) => pid,
            Err(e) => {
                if let Some(window) = &self.window {
                    window.show();
                }
                return Err(format!("Le lecteur n'a pas pu démarrer : {e}"));
            }
        };
        let answer = json!({"mpv": bin.display().to_string(), "pid": pid, "playlist_count": count});
        let mut last = self.last.lock().unwrap_or_else(|e| e.into_inner());
        *last = Last {
            at: Some(Instant::now()),
            url: request.url,
            answer: Some(answer.clone()),
        };
        Ok(answer)
    }

    fn start(
        self: &Arc<Self>,
        program: &Path,
        args: &[String],
        media: Option<Media>,
        docked: bool,
    ) -> std::io::Result<u32> {
        if cfg!(windows) {
            // One player at a time.
            let mut kill = std::process::Command::new("taskkill");
            kill.args(["/F", "/IM", "mpv.exe"])
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null());
            #[cfg(windows)]
            std::os::windows::process::CommandExt::creation_flags(&mut kill, 0x0800_0000);
            let _ = kill.status();
        }
        let mut command = tokio::process::Command::new(program);
        command
            .args(args)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        #[cfg(unix)]
        command.process_group(0);
        let mut child = command.spawn()?;
        let pid = child.id().unwrap_or(0);
        *self.current.lock().unwrap_or_else(|e| e.into_inner()) = pid;
        if let Some(media) = media.clone().filter(|m| !m.id.is_empty()) {
            self.add_subtitles(media);
        }
        if docked && let Some(window) = self.window.clone() {
            window.dock(pid);
            tokio::spawn(window_events(window));
        }
        let me = self.clone();
        tokio::spawn(async move {
            me.follow(&mut child, media).await;
            me.discord.clear();
            if *me.current.lock().unwrap_or_else(|e| e.into_inner()) == pid
                && let Some(window) = &me.window
            {
                if docked {
                    window.undock()
                } else {
                    window.show()
                }
            }
        });
        Ok(pid)
    }

    /// Every 3 s while the player runs: the position into the history (the
    /// episode follows the playlist) and the Discord card.
    async fn follow(&self, child: &mut tokio::process::Child, media: Option<Media>) {
        let media = media.filter(|m| !m.id.is_empty());
        let mut wait = Duration::from_secs(2);
        loop {
            tokio::select! {
                _ = child.wait() => return,
                _ = tokio::time::sleep(wait) => {}
            }
            wait = Duration::from_secs(3);
            let Some(media) = &media else { continue };
            let (Some(pos), Some(dur)) = (
                query("time-pos").await.and_then(|v| v.as_f64()),
                query("duration").await.and_then(|v| v.as_f64()),
            ) else {
                continue;
            };
            if dur <= 30.0 || pos <= 3.0 {
                continue;
            }
            let series = media.kind == "series";
            let base = media.episode.unwrap_or(1);
            let episode = match query("playlist-pos").await.and_then(|v| v.as_i64()) {
                Some(p) if series && p >= 0 => Some(base + p),
                _ => series.then_some(base),
            };
            let entry = media.entry(episode, pos, dur);
            let db = self.db.clone();
            let _ =
                tokio::task::spawn_blocking(move || crate::library::record_history(&db, &entry))
                    .await;
            let paused = query("pause")
                .await
                .is_some_and(|v| crate::play::truthy(&v));
            self.discord.show(&Playing {
                title: if media.name.is_empty() {
                    "KINO"
                } else {
                    &media.name
                },
                kind: &media.kind,
                season: series.then(|| media.season.unwrap_or(1)),
                episode,
                episode_title: None,
                position: pos,
                duration: dur,
                poster: Some(&media.poster),
                paused,
                year: None,
            });
        }
    }

    /// The best complete French and English subtitles, added to the player.
    fn add_subtitles(&self, media: Media) {
        let meta = self.meta.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(1200)).await;
            let release = if media.filename.is_empty() {
                &media.name
            } else {
                &media.filename
            };
            let subs = crate::subtitles::opensubtitles(
                &meta,
                &media.id,
                &media.kind,
                media.season.unwrap_or(1),
                media.episode.unwrap_or(1),
                release,
                None,
            )
            .await;
            let dir = std::env::temp_dir();
            let dir = if cfg!(windows) {
                dir
            } else {
                PathBuf::from("/tmp")
            };
            for lang in ["fr", "en"] {
                let mut best: Vec<u8> = vec![];
                for item in subs.iter().filter(|s| s["lang"] == lang).take(3) {
                    let Ok(answer) = meta
                        .net
                        .client
                        .get(text(&item["url"]))
                        .timeout(Duration::from_millis(3500))
                        .send()
                        .await
                    else {
                        continue;
                    };
                    let Ok(raw) = answer.bytes().await else {
                        continue;
                    };
                    if raw.len() > best.len() || raw.len() >= 6000 {
                        best = raw.to_vec();
                    }
                    if raw.len() >= 6000 {
                        break;
                    }
                }
                let file = dir.join(format!("kino_sub_{lang}.srt"));
                if best.len() > 64 && std::fs::write(&file, &best).is_ok() {
                    send(json!(["sub-add", file.display().to_string(), "auto"])).await;
                }
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan(hdr_mode: HdrMode, start_sec: i64) -> Plan {
        Plan {
            alang: "fre,fra,fr,eng",
            slang: "fre,fra,fr,eng",
            hdr_mode,
            audio_mode: AudioMode::VoiceBoost,
            hdr_media: true,
            input_conf: None,
            start_sec,
            playlist: false,
            title: "Dune".into(),
            target: "https://x/dune.mkv".into(),
        }
    }

    #[test]
    fn mpv_arguments() {
        let args = mpv_command(
            Path::new("/nowhere/mpv"),
            &plan(HdrMode::SdrPref, 600),
            Some((10, 20, 1280, 720)),
        );
        for wanted in [
            "--title=KINO",
            "--start=600",
            "--gamma=6",
            "--geometry=1280x720+10+20",
            "--force-media-title=Dune",
        ] {
            assert!(args.iter().any(|a| a == wanted), "{wanted} in {args:?}");
        }
        assert_eq!(args.last().map(String::as_str), Some("https://x/dune.mkv"));
        let native = shared_options("--mpv-", &plan(HdrMode::HdrNative, 3));
        assert_eq!(
            native,
            vec!["--mpv-af=lavfi=[dynaudnorm=f=180:g=13:p=0.92:m=4.5]"]
        );
    }

    #[test]
    fn episode_follows_the_playlist() {
        let m = Media {
            id: "tt1".into(),
            kind: "series".into(),
            season: None,
            episode: Some(4),
            ..Default::default()
        };
        let e = m.entry(Some(5), 120.9, 1500.0);
        assert_eq!(
            (
                e["season"].clone(),
                e["episode"].clone(),
                e["position"].clone()
            ),
            (json!(1), json!(5), json!(120))
        );
        assert_eq!(m.entry(None, 1.0, 2.0)["name"], "KINO");
    }
}
