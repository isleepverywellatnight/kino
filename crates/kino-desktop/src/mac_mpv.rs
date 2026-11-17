//! KINO's native player on macOS: the bundled libmpv draws into an OpenGL view
//! placed under the web view, whose background turns transparent while it
//! plays, so the page's own controls show over the video. mpv's client API is
//! thread-safe (the server's threads call it); AppKit and OpenGL work runs on
//! the main thread (`MainSide`, driven by the window's event loop).

// libmpv's renderer draws with OpenGL, which Apple marks deprecated.
#![allow(deprecated)]

use std::cell::RefCell;
use std::ffi::{CStr, CString, c_char, c_int, c_void};
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock, mpsc};
use std::time::Duration;

use block2::RcBlock;
use objc2::rc::Retained;
use objc2::{DefinedClass, MainThreadMarker, MainThreadOnly, define_class, msg_send};
use objc2_app_kit::{
    NSAutoresizingMaskOptions, NSOpenGLPFAAccelerated, NSOpenGLPFADoubleBuffer,
    NSOpenGLPFAOpenGLProfile, NSOpenGLPixelFormat, NSOpenGLProfileVersion3_2Core, NSOpenGLView,
    NSView, NSWindowOrderingMode,
};
use objc2_foundation::{NSNumber, NSRect, NSRunLoop, NSRunLoopCommonModes, NSString, NSTimer};
use serde_json::{Value, json};

// --- libmpv -------------------------------------------------------------

const FORMAT_FLAG: c_int = 3;
const FORMAT_DOUBLE: c_int = 5;
const RENDER_API_TYPE: c_int = 1;
const RENDER_OPENGL_INIT_PARAMS: c_int = 2;
const RENDER_OPENGL_FBO: c_int = 3;
const RENDER_FLIP_Y: c_int = 4;
const RENDER_UPDATE_FRAME: u64 = 1;
const GL_FRAMEBUFFER_BINDING: u32 = 0x8CA6;

#[repr(C)]
struct RenderParam {
    kind: c_int,
    data: *mut c_void,
}

#[repr(C)]
struct OpenGlInit {
    get_proc_address: extern "C" fn(*mut c_void, *const c_char) -> *mut c_void,
    ctx: *mut c_void,
}

#[repr(C)]
struct OpenGlFbo {
    fbo: c_int,
    w: c_int,
    h: c_int,
    internal_format: c_int,
}

struct Lib {
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
    render_create: unsafe extern "C" fn(*mut *mut c_void, *mut c_void, *mut RenderParam) -> c_int,
    render_update: unsafe extern "C" fn(*mut c_void) -> u64,
    render_render: unsafe extern "C" fn(*mut c_void, *mut RenderParam) -> c_int,
    render_free: unsafe extern "C" fn(*mut c_void),
}

/// libmpv.2.dylib and its libraries: inside KINO.app, else vendor/libmpv
/// (tools/vendor_libmpv.sh), else `KINO_LIBMPV`.
fn libmpv_path() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = std::env::var_os("KINO_LIBMPV")
        .map(PathBuf::from)
        .into_iter()
        .collect();
    if let Ok(exe) = std::env::current_exe()
        && let Some(dir) = exe.parent()
    {
        candidates.push(dir.join("../Frameworks/libmpv.2.dylib"));
    }
    candidates
        .push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../vendor/libmpv/libmpv.2.dylib"));
    candidates.into_iter().find(|p| p.is_file())
}

fn lib() -> Option<&'static Lib> {
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
                render_create: sym!(b"mpv_render_context_create"),
                render_update: sym!(b"mpv_render_context_update"),
                render_render: sym!(b"mpv_render_context_render"),
                render_free: sym!(b"mpv_render_context_free"),
                _lib: lib,
            })
        }
    })
    .as_ref()
}

pub fn available() -> bool {
    lib().is_some()
}

/// OpenGL's functions, for mpv's renderer and the framebuffer in use.
fn opengl() -> *mut c_void {
    static GL: OnceLock<usize> = OnceLock::new();
    *GL.get_or_init(|| {
        let path = c"/System/Library/Frameworks/OpenGL.framework/OpenGL";
        // SAFETY: the system's OpenGL framework, opened once for the process.
        unsafe { libc::dlopen(path.as_ptr(), libc::RTLD_LAZY) as usize }
    }) as *mut c_void
}

extern "C" fn get_proc_address(_ctx: *mut c_void, name: *const c_char) -> *mut c_void {
    // SAFETY: mpv passes a C string naming an OpenGL function.
    unsafe { libc::dlsym(opengl(), name) }
}

/// One libmpv player. The client API is thread-safe; the render context is
/// created, used and freed on the main thread with its GL context current.
pub struct Mpv {
    handle: *mut c_void,
    render: Mutex<*mut c_void>,
}

// SAFETY: libmpv's client API may be called from any thread; the render
// context pointer is only used on the main thread, behind its mutex.
unsafe impl Send for Mpv {}
unsafe impl Sync for Mpv {}

fn c(text: &str) -> CString {
    CString::new(text.replace('\0', "")).unwrap_or_default()
}

impl Mpv {
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
                render: Mutex::new(std::ptr::null_mut()),
            };
            let defaults = [
                ("vo", "libmpv"),
                ("hwdec", "videotoolbox"),
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

    fn check(&self, code: c_int, what: &str) -> Result<(), String> {
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

    /// With the view's GL context current, before the file opens its video output.
    fn create_render_context(&self) -> Result<(), String> {
        let l = lib().ok_or("libmpv")?;
        let mut init = OpenGlInit {
            get_proc_address,
            ctx: std::ptr::null_mut(),
        };
        let api = c"opengl";
        let mut params = [
            RenderParam {
                kind: RENDER_API_TYPE,
                data: api.as_ptr() as *mut c_void,
            },
            RenderParam {
                kind: RENDER_OPENGL_INIT_PARAMS,
                data: (&raw mut init).cast(),
            },
            RenderParam {
                kind: 0,
                data: std::ptr::null_mut(),
            },
        ];
        let mut ctx: *mut c_void = std::ptr::null_mut();
        // SAFETY: the parameters live for the call; mpv copies what it keeps.
        self.check(
            unsafe { (l.render_create)(&raw mut ctx, self.handle, params.as_mut_ptr()) },
            "render context",
        )?;
        *self.render.lock().unwrap_or_else(|e| e.into_inner()) = ctx;
        Ok(())
    }

    fn frame_ready(&self) -> bool {
        let ctx = *self.render.lock().unwrap_or_else(|e| e.into_inner());
        // SAFETY: a live render context, on the main thread.
        !ctx.is_null()
            && lib().is_some_and(|l| unsafe { (l.render_update)(ctx) } & RENDER_UPDATE_FRAME != 0)
    }

    fn render(&self, width: i32, height: i32) {
        let ctx = *self.render.lock().unwrap_or_else(|e| e.into_inner());
        let Some(l) = lib().filter(|_| !ctx.is_null()) else {
            return;
        };
        let mut binding: c_int = 0;
        // SAFETY: glGetIntegerv from the system's OpenGL, with the view's context current.
        unsafe {
            let get: Option<extern "C" fn(u32, *mut c_int)> =
                std::mem::transmute(libc::dlsym(opengl(), c"glGetIntegerv".as_ptr()));
            if let Some(get) = get {
                get(GL_FRAMEBUFFER_BINDING, &raw mut binding);
            }
        }
        let mut fbo = OpenGlFbo {
            fbo: binding,
            w: width,
            h: height,
            internal_format: 0,
        };
        let mut flip: c_int = 1;
        let mut params = [
            RenderParam {
                kind: RENDER_OPENGL_FBO,
                data: (&raw mut fbo).cast(),
            },
            RenderParam {
                kind: RENDER_FLIP_Y,
                data: (&raw mut flip).cast(),
            },
            RenderParam {
                kind: 0,
                data: std::ptr::null_mut(),
            },
        ];
        // SAFETY: a live render context and parameters that outlive the call.
        unsafe { (l.render_render)(ctx, params.as_mut_ptr()) };
    }

    /// On the main thread, with the view's context current when it rendered.
    fn close(&self) {
        let Some(l) = lib() else { return };
        let mut render = self.render.lock().unwrap_or_else(|e| e.into_inner());
        // SAFETY: each pointer is freed once.
        unsafe {
            if !render.is_null() {
                (l.render_free)(*render);
                *render = std::ptr::null_mut();
            }
        }
    }
}

impl Drop for Mpv {
    fn drop(&mut self) {
        if let Some(l) = lib() {
            // SAFETY: the handle is destroyed once, after its render context.
            unsafe { (l.terminate_destroy)(self.handle) };
        }
    }
}

// --- the view under the page -----------------------------------------------

pub struct ViewIvars {
    player: RefCell<Option<Arc<Mpv>>>,
}

define_class!(
    #[unsafe(super(NSOpenGLView, NSView))]
    #[thread_kind = MainThreadOnly]
    #[name = "KinoMpvView"]
    #[ivars = ViewIvars]
    pub struct MpvView;

    impl MpvView {
        #[unsafe(method(drawRect:))]
        fn draw_rect(&self, _rect: NSRect) {
            let Some(player) = self.ivars().player.borrow().clone() else { return };
            let Some(context) = self.openGLContext() else { return };
            context.makeCurrentContext();
            let size = self.convertRectToBacking(self.bounds()).size;
            player.render(size.width as i32, size.height as i32);
            context.flushBuffer();
        }
    }
);

impl MpvView {
    fn create(mtm: MainThreadMarker, frame: NSRect) -> Option<Retained<Self>> {
        let attributes = [
            NSOpenGLPFADoubleBuffer,
            NSOpenGLPFAAccelerated,
            NSOpenGLPFAOpenGLProfile,
            NSOpenGLProfileVersion3_2Core,
            0,
        ];
        // SAFETY: a zero-terminated attribute list.
        let format = unsafe {
            NSOpenGLPixelFormat::initWithAttributes(
                <NSOpenGLPixelFormat as objc2::AllocAnyThread>::alloc(),
                std::ptr::NonNull::from(&attributes[0]),
            )
        }?;
        let this = Self::alloc(mtm).set_ivars(ViewIvars {
            player: RefCell::new(None),
        });
        let view: Option<Retained<Self>> =
            unsafe { msg_send![super(this), initWithFrame: frame, pixelFormat: &*format] };
        let view = view?;
        view.setWantsBestResolutionOpenGLSurface(true);
        Some(view)
    }
}

/// What lives on the main thread while a film plays.
#[derive(Default)]
pub struct MainSide {
    view: Option<Retained<MpvView>>,
    timer: Option<Retained<NSTimer>>,
}

fn set_draws_background(webview: &NSView, on: bool) {
    let key = NSString::from_str("drawsBackground");
    let value = NSNumber::new_bool(on);
    // SAFETY: WKWebView's documented drawsBackground key, set to a boolean.
    unsafe {
        let _: () = msg_send![webview, setValue: &*value, forKey: &*key];
    }
    webview.setNeedsDisplay(true);
}

impl MainSide {
    /// Places the view under the web view and opens `path` in `player`.
    pub fn open(&mut self, webview: &NSView, player: Arc<Mpv>, path: &str) -> Result<(), String> {
        let mtm = MainThreadMarker::new().ok_or("main thread")?;
        self.close(webview);
        // SAFETY: the web view is in KINO's window.
        let superview = unsafe { webview.superview() }.ok_or("Fenêtre KINO introuvable.")?;
        let view = MpvView::create(mtm, webview.frame()).ok_or("OpenGL")?;
        view.setAutoresizingMask(
            NSAutoresizingMaskOptions::ViewWidthSizable
                | NSAutoresizingMaskOptions::ViewHeightSizable,
        );
        view.setWantsLayer(true);
        superview.addSubview_positioned_relativeTo(
            &view,
            NSWindowOrderingMode::Below,
            Some(webview),
        );
        // The render context must exist before mpv opens its video output.
        view.openGLContext().ok_or("OpenGL")?.makeCurrentContext();
        let opened = player
            .create_render_context()
            .and_then(|_| player.command(&["loadfile".into(), path.into()]));
        if let Err(e) = opened {
            player.close();
            view.removeFromSuperview();
            return Err(e);
        }
        *view.ivars().player.borrow_mut() = Some(player);
        set_draws_background(webview, false);
        let pumped = view.clone();
        let block = RcBlock::new(move |_timer: std::ptr::NonNull<NSTimer>| {
            let ready = pumped
                .ivars()
                .player
                .borrow()
                .as_ref()
                .is_some_and(|p| p.frame_ready());
            if ready {
                pumped.setNeedsDisplay(true);
            }
        });
        let timer =
            unsafe { NSTimer::timerWithTimeInterval_repeats_block(1.0 / 120.0, true, &block) };
        unsafe { NSRunLoop::mainRunLoop().addTimer_forMode(&timer, NSRunLoopCommonModes) };
        self.view = Some(view);
        self.timer = Some(timer);
        Ok(())
    }

    pub fn close(&mut self, webview: &NSView) {
        if let Some(timer) = self.timer.take() {
            timer.invalidate();
        }
        let Some(view) = self.view.take() else { return };
        if let Some(context) = view.openGLContext() {
            context.makeCurrentContext();
        }
        if let Some(player) = view.ivars().player.borrow_mut().take() {
            player.close();
        }
        view.removeFromSuperview();
        set_draws_background(webview, true);
    }
}

// --- the controller the server calls ----------------------------------------

/// What the window's event loop does for the controller, on the main thread.
pub enum MainCall {
    Open(Arc<Mpv>, String, mpsc::Sender<Result<(), String>>),
    Close(mpsc::Sender<()>),
}

#[derive(Default)]
struct Playing {
    player: Option<Arc<Mpv>>,
    path: String,
    pending: Vec<(String, String, String, bool)>,
    subtitle_dir: Option<tempfile::TempDir>,
}

pub struct Controller {
    to_main: Mutex<Box<dyn Fn(MainCall) + Send>>,
    playing: Mutex<Playing>,
}

impl Controller {
    pub fn new(to_main: Box<dyn Fn(MainCall) + Send>) -> Self {
        Self {
            to_main: Mutex::new(to_main),
            playing: Mutex::default(),
        }
    }

    fn main(&self, call: MainCall) {
        (self.to_main.lock().unwrap_or_else(|e| e.into_inner()))(call);
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
        pref_lang: &str,
    ) -> Result<Value, String> {
        let mut playing = self.playing.lock().unwrap_or_else(|e| e.into_inner());
        self.close_playing(&mut playing);
        let player = Arc::new(Mpv::new(&kino::native::options(pref_lang, start, volume))?);
        let (tx, rx) = mpsc::channel();
        self.main(MainCall::Open(player.clone(), path.to_string(), tx));
        rx.recv_timeout(Duration::from_secs(10))
            .map_err(|_| "main thread".to_string())??;
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
