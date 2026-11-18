//! KINO's native player on macOS: the bundled libmpv draws into an OpenGL view
//! placed under the web view, whose background turns transparent while it
//! plays, so the page's own controls show over the video. mpv's client API is
//! thread-safe (the server's threads call it); AppKit and OpenGL work runs on
//! the main thread (`MainSide`, driven by the window's event loop).

// libmpv's renderer draws with OpenGL, which Apple marks deprecated.
#![allow(deprecated)]

use std::cell::RefCell;
use std::ffi::{c_char, c_int, c_void};
use std::sync::{Arc, OnceLock};

use block2::RcBlock;
use objc2::rc::Retained;
use objc2::{DefinedClass, MainThreadMarker, MainThreadOnly, define_class, msg_send};
use objc2_app_kit::{
    NSAutoresizingMaskOptions, NSOpenGLPFAAccelerated, NSOpenGLPFADoubleBuffer,
    NSOpenGLPFAOpenGLProfile, NSOpenGLPixelFormat, NSOpenGLProfileVersion3_2Core, NSOpenGLView,
    NSView, NSWindowOrderingMode,
};
use objc2_foundation::{NSNumber, NSRect, NSRunLoop, NSRunLoopCommonModes, NSString, NSTimer};

use crate::mpv::{Mpv, lib};

// --- libmpv's OpenGL renderer ------------------------------------------------

const RENDER_API_TYPE: c_int = 1;
const RENDER_OPENGL_INIT_PARAMS: c_int = 2;
const RENDER_OPENGL_FBO: c_int = 3;
const RENDER_FLIP_Y: c_int = 4;
const RENDER_UPDATE_FRAME: u64 = 1;
const GL_FRAMEBUFFER_BINDING: u32 = 0x8CA6;

#[repr(C)]
pub(crate) struct RenderParam {
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

/// libmpv's render API, read with the rest of the library (`mpv::lib`).
pub(crate) struct RenderLib {
    pub(crate) create:
        unsafe extern "C" fn(*mut *mut c_void, *mut c_void, *mut RenderParam) -> c_int,
    pub(crate) update: unsafe extern "C" fn(*mut c_void) -> u64,
    pub(crate) render: unsafe extern "C" fn(*mut c_void, *mut RenderParam) -> c_int,
    pub(crate) free: unsafe extern "C" fn(*mut c_void),
}

/// KINO's settings for every player on macOS.
pub fn window_options() -> Vec<(String, String)> {
    [("vo", "libmpv"), ("hwdec", "videotoolbox")]
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
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

impl Mpv {
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
            unsafe { (l.render.create)(&raw mut ctx, self.handle, params.as_mut_ptr()) },
            "render context",
        )?;
        *self.render.lock().unwrap_or_else(|e| e.into_inner()) = ctx;
        Ok(())
    }

    fn frame_ready(&self) -> bool {
        let ctx = *self.render.lock().unwrap_or_else(|e| e.into_inner());
        // SAFETY: a live render context, on the main thread.
        !ctx.is_null()
            && lib().is_some_and(|l| unsafe { (l.render.update)(ctx) } & RENDER_UPDATE_FRAME != 0)
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
        unsafe { (l.render.render)(ctx, params.as_mut_ptr()) };
    }

    /// On the main thread, with the view's context current when it rendered.
    fn close(&self) {
        let Some(l) = lib() else { return };
        let mut render = self.render.lock().unwrap_or_else(|e| e.into_inner());
        // SAFETY: each pointer is freed once.
        unsafe {
            if !render.is_null() {
                (l.render.free)(*render);
                *render = std::ptr::null_mut();
            }
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
