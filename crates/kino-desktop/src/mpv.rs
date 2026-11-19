//! libmpv for KINO's native player (« Lecteur natif »), on macOS and Windows:
//! the library, one player, and the controller the server calls. Each window
//! shows the video its own way (`mac_mpv.rs`, `win_mpv.rs`) on its main
//! thread; mpv's client API is thread-safe, so the server's threads call it.

use std::ffi::{CStr, CString, c_char, c_int, c_void};
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock, mpsc};
use std::time::Duration;

use serde_json::{Value, json};

const FORMAT_FLAG: c_int = 3;
const FORMAT_DOUBLE: c_int = 5;

pub(crate) struct Lib {
    _lib: libloading::Library,
    create: unsafe extern "C" fn() -> *mut c_void,
    set_option_string: unsafe extern "C" fn(*mut c_void, *const c_char, *const c_char) -> c_int,
    initialize: unsafe extern "C" fn(*mut c_void) -> c_int,
    command: unsafe extern "C" fn(*mut c_void, *const *const c_char) -> c_int,
    get_property: unsafe extern "C" fn(*mut c_void, *const c_char, c_int, *mut c_void) -> c_int,
    get_property_string: unsafe extern "C" fn(*mut c_void, *const c_char) -> *mut c_char,
    free: unsafe extern "C" fn(*mut c_void),
    terminate_destroy: unsafe extern "C" fn(*mut c_void),
    error_string: unsafe extern "C" fn(c_int) -> *const c_char,
    #[cfg(target_os = "macos")]
    pub(crate) render: crate::mac_mpv::RenderLib,
}

/// KINO's libmpv: `KINO_LIBMPV` first, then the copy shipped with the app,
/// then the one fetched for development under vendor/.
fn libmpv_path() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = std::env::var_os("KINO_LIBMPV")
        .map(PathBuf::from)
        .into_iter()
        .collect();
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(PathBuf::from));
    let vendor = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../vendor");
    #[cfg(target_os = "macos")]
    {
        // Inside KINO.app, else tools/vendor_libmpv.sh's copy.
        candidates.extend(exe_dir.map(|d| d.join("../Frameworks/libmpv.2.dylib")));
        candidates.push(vendor.join("libmpv/libmpv.2.dylib"));
    }
    #[cfg(windows)]
    {
        // Beside KINO.exe, else tools/fetch_mpv_windows.ps1's copy.
        candidates.extend(exe_dir.map(|d| d.join("libmpv-2.dll")));
        candidates.push(vendor.join("libmpv-windows/libmpv-2.dll"));
    }
    candidates.into_iter().find(|p| p.is_file())
}

pub(crate) fn lib() -> Option<&'static Lib> {
    static LIB: OnceLock<Option<Lib>> = OnceLock::new();
    LIB.get_or_init(|| {
        let path = libmpv_path()?;
        // SAFETY: KINO's own libmpv; each symbol is read with its C signature.
        unsafe {
            let lib = libloading::Library::new(path).ok()?;
            macro_rules! sym {
                ($name:literal) => {
                    *lib.get($name).ok()?
                };
            }
            Some(Lib {
                create: sym!(b"mpv_create"),
                set_option_string: sym!(b"mpv_set_option_string"),
                initialize: sym!(b"mpv_initialize"),
                command: sym!(b"mpv_command"),
                get_property: sym!(b"mpv_get_property"),
                get_property_string: sym!(b"mpv_get_property_string"),
                free: sym!(b"mpv_free"),
                terminate_destroy: sym!(b"mpv_terminate_destroy"),
                error_string: sym!(b"mpv_error_string"),
                #[cfg(target_os = "macos")]
                render: crate::mac_mpv::RenderLib {
                    create: sym!(b"mpv_render_context_create"),
                    update: sym!(b"mpv_render_context_update"),
                    render: sym!(b"mpv_render_context_render"),
                    free: sym!(b"mpv_render_context_free"),
                },
                _lib: lib,
            })
        }
    })
    .as_ref()
}

pub fn available() -> bool {
    lib().is_some()
}

fn c(text: &str) -> CString {
    CString::new(text.replace('\0', "")).unwrap_or_default()
}

/// One libmpv player.
pub struct Mpv {
    pub(crate) handle: *mut c_void,
    /// macOS: the render context, created and used on the main thread.
    #[cfg(target_os = "macos")]
    pub(crate) render: Mutex<*mut c_void>,
}

// SAFETY: libmpv's client API may be called from any thread; the macOS render
// context pointer is only used on the main thread, behind its mutex.
unsafe impl Send for Mpv {}
unsafe impl Sync for Mpv {}

impl Mpv {
    /// A player with KINO's settings, then `options` (later ones win).
    pub fn new(options: &[(String, String)]) -> Result<Self, String> {
        let l = lib().ok_or("libmpv n'est pas présente dans KINO.")?;
        // SAFETY: calls into libmpv with valid C strings.
        unsafe {
            let handle = (l.create)();
            if handle.is_null() {
                return Err("mpv_create failed".into());
            }
            let me = Self {
                handle,
                #[cfg(target_os = "macos")]
                render: Mutex::new(std::ptr::null_mut()),
            };
            let defaults = [
                ("keep-open", "yes"),
                ("idle", "yes"),
                ("input-default-bindings", "no"),
                ("osc", "no"),
                ("terminal", "no"),
                ("config", "no"),
            ];
            let all = defaults
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .chain(options.iter().cloned());
            for (name, value) in all {
                me.check(
                    (l.set_option_string)(handle, c(&name).as_ptr(), c(&value).as_ptr()),
                    &name,
                )?;
            }
            me.check((l.initialize)(handle), "initialize")?;
            Ok(me)
        }
    }

    pub(crate) fn check(&self, code: c_int, what: &str) -> Result<(), String> {
        if code >= 0 {
            return Ok(());
        }
        // SAFETY: mpv_error_string returns a static C string.
        let message = lib()
            .map(|l| {
                unsafe { CStr::from_ptr((l.error_string)(code)) }
                    .to_string_lossy()
                    .into_owned()
            })
            .unwrap_or_default();
        Err(format!("{what}: {message}"))
    }

    pub fn command(&self, args: &[String]) -> Result<(), String> {
        let l = lib().ok_or("libmpv")?;
        let owned: Vec<CString> = args.iter().map(|a| c(a)).collect();
        let mut argv: Vec<*const c_char> = owned.iter().map(|a| a.as_ptr()).collect();
        argv.push(std::ptr::null());
        // SAFETY: a NULL-terminated array of C strings that outlive the call.
        self.check(
            unsafe { (l.command)(self.handle, argv.as_ptr()) },
            args.first().map_or("", String::as_str),
        )
    }

    pub fn get_string(&self, name: &str) -> Option<String> {
        let l = lib()?;
        // SAFETY: the returned string is freed with mpv_free.
        unsafe {
            let found = (l.get_property_string)(self.handle, c(name).as_ptr());
            if found.is_null() {
                return None;
            }
            let text = CStr::from_ptr(found).to_string_lossy().into_owned();
            (l.free)(found.cast());
            Some(text)
        }
    }

    pub fn get_double(&self, name: &str) -> Option<f64> {
        let l = lib()?;
        let mut value = 0.0f64;
        // SAFETY: MPV_FORMAT_DOUBLE writes one double.
        let code = unsafe {
            (l.get_property)(
                self.handle,
                c(name).as_ptr(),
                FORMAT_DOUBLE,
                (&raw mut value).cast(),
            )
        };
        (code >= 0).then_some(value)
    }

    pub fn get_flag(&self, name: &str) -> Option<bool> {
        let l = lib()?;
        let mut value: c_int = 0;
        // SAFETY: MPV_FORMAT_FLAG writes one int.
        let code = unsafe {
            (l.get_property)(
                self.handle,
                c(name).as_ptr(),
                FORMAT_FLAG,
                (&raw mut value).cast(),
            )
        };
        (code >= 0).then_some(value != 0)
    }
}

impl Drop for Mpv {
    fn drop(&mut self) {
        if let Some(l) = lib() {
            // SAFETY: the handle is destroyed once (on macOS, after its render context).
            unsafe { (l.terminate_destroy)(self.handle) };
        }
    }
}

// --- the controller the server calls ----------------------------------------

/// What the window's event loop does for the controller, on the main thread.
pub enum MainCall {
    Open(Arc<Mpv>, String, mpsc::Sender<Result<(), String>>),
    /// Windows: the player's video is up, the page may let it show through
    /// (macOS draws in its own view under the page from the start).
    #[cfg(windows)]
    Reveal(Arc<Mpv>),
    Close(mpsc::Sender<()>),
}

/// The window's own options for every player (video output, decoding, where
/// to draw), asked for at each opening.
pub type WindowOptions = Box<dyn Fn() -> Vec<(String, String)> + Send + Sync>;

#[derive(Default)]
struct Playing {
    player: Option<Arc<Mpv>>,
    path: String,
    pending: Vec<(String, String, String, bool)>,
    subtitle_dir: Option<tempfile::TempDir>,
}

/// How the controller reaches the main thread.
type ToMain = Arc<Mutex<Box<dyn Fn(MainCall) + Send>>>;

pub struct Controller {
    to_main: ToMain,
    window_options: WindowOptions,
    playing: Mutex<Playing>,
}

impl Controller {
    pub fn new(to_main: Box<dyn Fn(MainCall) + Send>, window_options: WindowOptions) -> Self {
        Self {
            to_main: Arc::new(Mutex::new(to_main)),
            window_options,
            playing: Mutex::default(),
        }
    }

    fn main(&self, call: MainCall) {
        (self.to_main.lock().unwrap_or_else(|e| e.into_inner()))(call);
    }

    /// Once mpv shows the video (its output is configured), the main thread
    /// reveals it; a player closed before, or a file without video, never is.
    #[cfg(windows)]
    fn reveal_when_shown(&self, player: &Arc<Mpv>) {
        let to_main = self.to_main.clone();
        let player = Arc::downgrade(player);
        std::thread::spawn(move || {
            let start = std::time::Instant::now();
            while start.elapsed() < Duration::from_secs(30) {
                std::thread::sleep(Duration::from_millis(40));
                let Some(player) = player.upgrade() else {
                    return;
                };
                if player.get_flag("vo-configured") == Some(true) {
                    (to_main.lock().unwrap_or_else(|e| e.into_inner()))(MainCall::Reveal(player));
                    return;
                }
            }
        });
    }

    fn close_playing(&self, playing: &mut Playing) {
        if playing.player.take().is_some() {
            let (tx, rx) = mpsc::channel();
            self.main(MainCall::Close(tx));
            let _ = rx.recv_timeout(Duration::from_secs(10));
        }
        *playing = Playing::default();
    }

    /// `sub-add` needs the file loaded; loadfile is asynchronous.
    fn flush_subtitles(playing: &mut Playing) {
        let Some(player) = playing.player.clone() else {
            return;
        };
        if playing.pending.is_empty() || player.get_double("duration").is_none() {
            return;
        }
        for (path, title, lang, select) in std::mem::take(&mut playing.pending) {
            let mode = if select { "select" } else { "auto" };
            let _ = player.command(&["sub-add".into(), path, mode.into(), title, lang]);
        }
    }

    fn state_of(playing: &mut Playing) -> Value {
        Self::flush_subtitles(playing);
        let Some(p) = playing.player.clone() else {
            return json!({"open": false});
        };
        let count = p.get_double("track-list/count").unwrap_or(0.0) as usize;
        let mut tracks = vec![];
        for i in 0..count.min(64) {
            let get = |k: &str| p.get_string(&format!("track-list/{i}/{k}"));
            let kind = get("type").unwrap_or_default();
            if kind != "audio" && kind != "sub" {
                continue;
            }
            let flag = |k: &str| p.get_flag(&format!("track-list/{i}/{k}")).unwrap_or(false);
            tracks.push(json!({"type": kind, "id": get("id"), "lang": get("lang").unwrap_or_default(), "title": get("title").unwrap_or_default(),
                               "codec": get("codec").unwrap_or_default(), "channels": get("demux-channel-count").unwrap_or_default(),
                               "selected": flag("selected"), "external": flag("external")}));
        }
        let duration = p.get_double("duration");
        json!({
            "open": true, "time": p.get_double("time-pos").unwrap_or(0.0), "duration": duration.unwrap_or(0.0),
            "paused": p.get_flag("pause").unwrap_or(false), "ended": p.get_flag("eof-reached").unwrap_or(false),
            // A source that could not be opened leaves mpv idle with no duration.
            "idle": p.get_flag("idle-active").unwrap_or(false),
            "buffering": p.get_flag("paused-for-cache").unwrap_or(false) || duration.is_none(),
            "volume": p.get_double("volume"), "muted": p.get_flag("mute").unwrap_or(false),
            "width": p.get_string("video-params/w"), "height": p.get_string("video-params/h"), "hwdec": p.get_string("hwdec-current"),
            "speed": p.get_double("speed").filter(|s| *s != 0.0).unwrap_or(1.0), "sub_scale": p.get_double("sub-scale").filter(|s| *s != 0.0).unwrap_or(1.0),
            "tracks": tracks,
        })
    }
}

impl kino::native::NativePlayer for Controller {
    fn open(
        &self,
        path: &str,
        start: f64,
        volume: Option<f64>,
        pref_lang: kino::config::PrefLang,
    ) -> Result<Value, String> {
        let mut playing = self.playing.lock().unwrap_or_else(|e| e.into_inner());
        self.close_playing(&mut playing);
        let mut options = (self.window_options)();
        options.extend(kino::native::options(pref_lang, start, volume));
        let player = Arc::new(Mpv::new(&options)?);
        let (tx, rx) = mpsc::channel();
        self.main(MainCall::Open(player.clone(), path.to_string(), tx));
        rx.recv_timeout(Duration::from_secs(10))
            .map_err(|_| "main thread".to_string())??;
        #[cfg(windows)]
        self.reveal_when_shown(&player);
        playing.player = Some(player);
        playing.path = path.to_string();
        Ok(Self::state_of(&mut playing))
    }

    fn command(&self, commands: &[Vec<String>]) -> Result<(), String> {
        let playing = self.playing.lock().unwrap_or_else(|e| e.into_inner());
        let player = playing
            .player
            .as_ref()
            .ok_or("Aucune lecture native en cours.")?;
        commands.iter().try_for_each(|c| player.command(c))
    }

    fn screenshot(&self, path: &std::path::Path) -> Result<(), String> {
        let playing = self.playing.lock().unwrap_or_else(|e| e.into_inner());
        let player = playing
            .player
            .as_ref()
            .ok_or("Aucune lecture native en cours.")?;
        player.command(&[
            "screenshot-to-file".into(),
            path.display().to_string(),
            "video".into(),
        ])
    }

    fn add_subtitle(&self, srt: &str, title: &str, lang: &str, select: bool) -> Result<(), String> {
        let mut playing = self.playing.lock().unwrap_or_else(|e| e.into_inner());
        if playing.player.is_none() {
            return Err("Aucune lecture native en cours.".into());
        }
        if playing.subtitle_dir.is_none() {
            playing.subtitle_dir = tempfile::Builder::new()
                .prefix("kino-native-subs-")
                .tempdir()
                .ok();
        }
        let dir = playing
            .subtitle_dir
            .as_ref()
            .ok_or("dossier")?
            .path()
            .to_path_buf();
        let path = dir.join(format!(
            "{}.srt",
            std::fs::read_dir(&dir).map(|d| d.count()).unwrap_or(0) + 1
        ));
        std::fs::write(&path, srt).map_err(|e| e.to_string())?;
        let cut = |s: &str, n: usize| s.chars().take(n).collect::<String>();
        playing.pending.push((
            path.display().to_string(),
            cut(title, 80),
            cut(lang, 8),
            select,
        ));
        Self::flush_subtitles(&mut playing);
        Ok(())
    }

    fn state(&self) -> Value {
        Self::state_of(&mut self.playing.lock().unwrap_or_else(|e| e.into_inner()))
    }

    fn close(&self) -> Value {
        let mut playing = self.playing.lock().unwrap_or_else(|e| e.into_inner());
        self.close_playing(&mut playing);
        json!({"open": false})
    }

    fn source_and_time(&self) -> Option<(String, f64)> {
        let playing = self.playing.lock().unwrap_or_else(|e| e.into_inner());
        let player = playing.player.as_ref()?;
        Some((
            playing.path.clone(),
            player.get_double("time-pos").unwrap_or(0.0),
        ))
    }
}

/// The bundled mpv's version (self-check), when libmpv loads.
pub fn version() -> Option<String> {
    let player = Mpv::new(&[("vo".into(), "null".into()), ("ao".into(), "null".into())]).ok()?;
    player.get_string("mpv-version")
}
