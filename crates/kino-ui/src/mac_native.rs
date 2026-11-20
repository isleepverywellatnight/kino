//! The native video path on macOS: mpv draws with OpenGL into a view placed
//! under gpui's own view, in the same window; gpui, its window transparent,
//! leaves the video's area clear so the picture shows under its controls.
//! gpui's view stays on top, so it receives every click. The same approach as
//! the web version's player (`crates/kino-desktop/src/mac_mpv.rs`).

// libmpv's renderer draws with OpenGL, which Apple marks deprecated.
#![allow(deprecated)]

use std::cell::RefCell;
use std::ffi::{c_char, c_int, c_void};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use block2::RcBlock;
use objc2::rc::Retained;
use objc2::{DefinedClass, MainThreadMarker, MainThreadOnly, define_class, msg_send};
use objc2_app_kit::{
    NSAutoresizingMaskOptions, NSOpenGLPFAAccelerated, NSOpenGLPFADoubleBuffer,
    NSOpenGLPFAOpenGLProfile, NSOpenGLPixelFormat, NSOpenGLProfileVersion3_2Core, NSOpenGLView,
    NSView, NSWindowOrderingMode,
};
use objc2_foundation::{NSRect, NSRunLoop, NSRunLoopCommonModes, NSTimer};

use crate::video::Video;

const GL_FRAMEBUFFER_BINDING: u32 = 0x8CA6;

/// The system's OpenGL, for mpv's renderer and the framebuffer in use.
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

pub struct ViewIvars {
    video: RefCell<Option<Arc<Mutex<Video>>>>,
    stats: Arc<Stats>,
}

/// What `--bench` reports of the native path on macOS.
#[derive(Default)]
pub struct Stats {
    /// Frames mpv has drawn.
    pub frames: AtomicUsize,
    /// Time spent drawing them, in µs (on the main thread).
    pub draw_us: AtomicUsize,
    /// Whether the OpenGL format is hardware-accelerated.
    pub accelerated: std::sync::atomic::AtomicBool,
}

define_class!(
    #[unsafe(super(NSOpenGLView, NSView))]
    #[thread_kind = MainThreadOnly]
    #[name = "KinoUiMpvView"]
    #[ivars = ViewIvars]
    pub struct MpvView;

    impl MpvView {
        #[unsafe(method(drawRect:))]
        fn draw_rect(&self, _rect: NSRect) {
            let started = std::time::Instant::now();
            let Some(video) = self.ivars().video.borrow().clone() else { return };
            let Some(context) = self.openGLContext() else { return };
            context.makeCurrentContext();
            let size = self.convertRectToBacking(self.bounds()).size;
            let mut binding: c_int = 0;
            // SAFETY: glGetIntegerv from the system's OpenGL, context current.
            unsafe {
                let get: Option<extern "C" fn(u32, *mut c_int)> =
                    std::mem::transmute(libc::dlsym(opengl(), c"glGetIntegerv".as_ptr()));
                if let Some(get) = get {
                    get(GL_FRAMEBUFFER_BINDING, &raw mut binding);
                }
            }
            if let Ok(video) = video.lock() {
                video.gl_render(binding, size.width as c_int, size.height as c_int);
            }
            context.flushBuffer();
            let stats = &self.ivars().stats;
            stats.frames.fetch_add(1, Ordering::Relaxed);
            stats
                .draw_us
                .fetch_add(started.elapsed().as_micros() as usize, Ordering::Relaxed);
        }
    }
);

impl MpvView {
    fn create(mtm: MainThreadMarker, frame: NSRect, stats: Arc<Stats>) -> Option<Retained<Self>> {
        // A hardware-accelerated format first; the system's software
        // renderer if none (virtual machines without a GPU).
        let (format, accelerated) = [true, false].into_iter().find_map(|accelerated| {
            let mut attributes = vec![
                NSOpenGLPFADoubleBuffer,
                NSOpenGLPFAOpenGLProfile,
                NSOpenGLProfileVersion3_2Core,
            ];
            if accelerated {
                attributes.push(NSOpenGLPFAAccelerated);
            }
            attributes.push(0);
            // SAFETY: a zero-terminated attribute list.
            unsafe {
                NSOpenGLPixelFormat::initWithAttributes(
                    <NSOpenGLPixelFormat as objc2::AllocAnyThread>::alloc(),
                    std::ptr::NonNull::from(&attributes[0]),
                )
            }
            .map(|format| (format, accelerated))
        })?;
        stats.accelerated.store(accelerated, Ordering::Relaxed);
        let this = Self::alloc(mtm).set_ivars(ViewIvars {
            video: RefCell::new(None),
            stats,
        });
        // SAFETY: NSOpenGLView's designated initializer, on a fresh instance
        // of this class, with a live pixel format.
        let view: Option<Retained<Self>> =
            unsafe { msg_send![super(this), initWithFrame: frame, pixelFormat: &*format] };
        let view = view?;
        view.setWantsBestResolutionOpenGLSurface(true);
        Some(view)
    }
}

/// mpv's view under gpui's, and the timer that asks it to draw new frames.
pub struct Host {
    view: Retained<MpvView>,
    timer: Retained<NSTimer>,
    pub stats: Arc<Stats>,
}

/// Puts mpv's view under `gpui_view` (an `NSView` pointer) and opens `source`.
pub fn attach(
    gpui_view: *mut c_void,
    video: Arc<Mutex<Video>>,
    source: &str,
) -> Result<Host, String> {
    let mtm = MainThreadMarker::new().ok_or("main thread")?;
    // SAFETY: gpui's live view, from its window handle.
    let gpui_view: &NSView = unsafe { &*(gpui_view as *const NSView) };
    // SAFETY: a plain AppKit query on the main thread (checked above).
    let superview = unsafe { gpui_view.superview() }.ok_or("gpui's view has no superview")?;
    let stats = Arc::new(Stats::default());
    let view = MpvView::create(mtm, gpui_view.frame(), stats.clone())
        .ok_or("no OpenGL pixel format or view")?;
    view.setAutoresizingMask(
        NSAutoresizingMaskOptions::ViewWidthSizable | NSAutoresizingMaskOptions::ViewHeightSizable,
    );
    view.setWantsLayer(true);
    superview.addSubview_positioned_relativeTo(&view, NSWindowOrderingMode::Below, Some(gpui_view));
    // The render context must exist before mpv opens its video output.
    view.openGLContext()
        .ok_or("no OpenGL context for mpv's view")?
        .makeCurrentContext();
    {
        let mut player = video.lock().map_err(|_| "player")?;
        player.create_gl_render(get_proc_address)?;
        player.command(&["loadfile", source])?;
    }
    *view.ivars().video.borrow_mut() = Some(video);
    let pumped = view.clone();
    let block = RcBlock::new(move |_timer: std::ptr::NonNull<NSTimer>| {
        let ready = pumped
            .ivars()
            .video
            .borrow()
            .as_ref()
            .and_then(|v| v.lock().ok().map(|v| v.gl_frame_ready()))
            .unwrap_or(false);
        if ready {
            pumped.setNeedsDisplay(true);
        }
    });
    // SAFETY: the block lives as long as the timer, which `Host` invalidates
    // on drop; both run on the main thread.
    let timer = unsafe { NSTimer::timerWithTimeInterval_repeats_block(1.0 / 120.0, true, &block) };
    // SAFETY: the main run loop, on the main thread.
    unsafe { NSRunLoop::mainRunLoop().addTimer_forMode(&timer, NSRunLoopCommonModes) };
    Ok(Host { view, timer, stats })
}

impl Drop for Host {
    fn drop(&mut self) {
        self.timer.invalidate();
        if let Some(context) = self.view.openGLContext() {
            context.makeCurrentContext();
        }
        self.view.ivars().video.borrow_mut().take();
        self.view.removeFromSuperview();
    }
}
