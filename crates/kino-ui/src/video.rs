//! Video under gpui, the path that runs everywhere: libmpv's software renderer
//! draws each frame into memory (BGRA), and gpui shows it as an image under
//! its own controls. The same code on macOS, Windows and Linux; the native
//! paths (mpv's window under a transparent gpui on Windows, its OpenGL view on
//! macOS) are measured against this one.
//!
//! libmpv is loaded at run time: `KINO_LIBMPV`, then the copy beside the
//! program, then the system's.

use std::ffi::{CString, c_char, c_int, c_void};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use gpui::RenderImage;

const FORMAT_INT64: c_int = 4;
const FORMAT_DOUBLE: c_int = 5;
const FORMAT_FLAG: c_int = 3;

const RENDER_INVALID: c_int = 0;
const RENDER_API_TYPE: c_int = 1;
const RENDER_SW_SIZE: c_int = 17;
const RENDER_SW_FORMAT: c_int = 18;
const RENDER_SW_STRIDE: c_int = 19;
const RENDER_SW_POINTER: c_int = 20;
const RENDER_UPDATE_FRAME: u64 = 1;

/// The largest frame drawn: beyond 1080p, memory traffic grows for nothing
/// a window of this size would show.
const MAX_PIXELS: i64 = 1920 * 1080;

#[repr(C)]
struct Param {
    kind: c_int,
    data: *mut c_void,
}

type UpdateCallback = unsafe extern "C" fn(*mut c_void);

struct Lib {
    create: unsafe extern "C" fn() -> *mut c_void,
    set_option_string: unsafe extern "C" fn(*mut c_void, *const c_char, *const c_char) -> c_int,
    initialize: unsafe extern "C" fn(*mut c_void) -> c_int,
    command: unsafe extern "C" fn(*mut c_void, *const *const c_char) -> c_int,
    get_property: unsafe extern "C" fn(*mut c_void, *const c_char, c_int, *mut c_void) -> c_int,
    set_property: unsafe extern "C" fn(*mut c_void, *const c_char, c_int, *mut c_void) -> c_int,
    terminate_destroy: unsafe extern "C" fn(*mut c_void),
    render_create: unsafe extern "C" fn(*mut *mut c_void, *mut c_void, *mut Param) -> c_int,
    render_set_update_callback: unsafe extern "C" fn(*mut c_void, UpdateCallback, *mut c_void),
    render_update: unsafe extern "C" fn(*mut c_void) -> u64,
    render_render: unsafe extern "C" fn(*mut c_void, *mut Param) -> c_int,
    render_free: unsafe extern "C" fn(*mut c_void),
    _lib: libloading::Library,
}

fn candidates() -> Vec<PathBuf> {
    let mut paths: Vec<PathBuf> = std::env::var_os("KINO_LIBMPV")
        .map(PathBuf::from)
        .into_iter()
        .collect();
    let beside = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(PathBuf::from));
    let names: &[&str] = if cfg!(windows) {
        &["libmpv-2.dll", "mpv-2.dll"]
    } else if cfg!(target_os = "macos") {
        &["libmpv.2.dylib", "libmpv.dylib"]
    } else {
        &["libmpv.so.2", "libmpv.so"]
    };
    for name in names {
        if let Some(dir) = &beside {
            paths.push(dir.join(name));
        }
        if cfg!(target_os = "macos") {
            paths.push(PathBuf::from("/opt/homebrew/lib").join(name));
            paths.push(PathBuf::from("/usr/local/lib").join(name));
        }
        // The system's search path (Linux, and Windows' PATH).
        paths.push(PathBuf::from(name));
    }
    paths
}

fn load() -> Result<Lib, String> {
    let mut tried = Vec::new();
    for path in candidates() {
        // SAFETY: libmpv's symbols are read with their C signatures.
        let lib = match unsafe { libloading::Library::new(&path) } {
            Ok(lib) => lib,
            Err(_) => {
                tried.push(path.display().to_string());
                continue;
            }
        };
        // SAFETY: each symbol is read with its C signature from libmpv's
        // headers (client.h, render.h); `lib` lives in the returned `Lib`.
        unsafe {
            macro_rules! sym {
                ($name:literal) => {
                    *lib.get($name)
                        .map_err(|e| format!("{}: {e}", path.display()))?
                };
            }
            return Ok(Lib {
                create: sym!(b"mpv_create"),
                set_option_string: sym!(b"mpv_set_option_string"),
                initialize: sym!(b"mpv_initialize"),
                command: sym!(b"mpv_command"),
                get_property: sym!(b"mpv_get_property"),
                set_property: sym!(b"mpv_set_property"),
                terminate_destroy: sym!(b"mpv_terminate_destroy"),
                render_create: sym!(b"mpv_render_context_create"),
                render_set_update_callback: sym!(b"mpv_render_context_set_update_callback"),
                render_update: sym!(b"mpv_render_context_update"),
                render_render: sym!(b"mpv_render_context_render"),
                render_free: sym!(b"mpv_render_context_free"),
                _lib: lib,
            });
        }
    }
    Err(format!("libmpv not found (tried {})", tried.join(", ")))
}

fn c(text: &str) -> CString {
    CString::new(text.replace('\0', "")).unwrap_or_default()
}

/// mpv calls this from its own thread when a new frame is ready.
unsafe extern "C" fn frame_ready(flag: *mut c_void) {
    // SAFETY: `flag` is the `AtomicBool` the `Video` keeps alive.
    let flag = unsafe { &*(flag as *const AtomicBool) };
    flag.store(true, Ordering::Release);
}

/// One player and its software renderer.
pub struct Video {
    lib: Lib,
    handle: *mut c_void,
    render: *mut c_void,
    ready: Arc<AtomicBool>,
}

// SAFETY: `handle` and `render` are owned by this value and freed once, in
// `Drop`. mpv's client API is thread-safe, so the handle may move threads.
unsafe impl Send for Video {}
// SAFETY: the client API (`&self` methods) is thread-safe; the render API is
// not, and is only reached through `frame` (`&mut self`) or, on macOS, from the
// main thread behind the caller's `Mutex`, so never from two threads at once.
unsafe impl Sync for Video {}

impl Video {
    /// Opens `source`. With `window` (a native window's handle), mpv draws in
    /// it with the GPU (`native.rs`); without, frames come through `frame`.
    pub fn open(
        source: &str,
        options: &[(&str, &str)],
        window: Option<isize>,
    ) -> Result<Self, String> {
        let lib = load()?;
        // SAFETY: each call follows libmpv's client and render API.
        unsafe {
            let handle = (lib.create)();
            if handle.is_null() {
                return Err("mpv_create failed".into());
            }
            let wid = window.map(|w| w.to_string());
            let base: Vec<(&str, &str)> = match &wid {
                Some(wid) => vec![("wid", wid), ("hwdec", "auto-safe")],
                None => vec![("vo", "libmpv"), ("hwdec", "auto-copy-safe")],
            };
            for (name, value) in base.iter().chain(options) {
                (lib.set_option_string)(handle, c(name).as_ptr(), c(value).as_ptr());
            }
            if (lib.initialize)(handle) < 0 {
                (lib.terminate_destroy)(handle);
                return Err("mpv_initialize failed".into());
            }
            let ready = Arc::new(AtomicBool::new(false));
            if window.is_some() || source.is_empty() {
                let video = Self {
                    lib,
                    handle,
                    render: std::ptr::null_mut(),
                    ready,
                };
                if !source.is_empty() {
                    video.command(&["loadfile", source])?;
                }
                return Ok(video);
            }
            let api = c("sw");
            let mut params = [
                Param {
                    kind: RENDER_API_TYPE,
                    data: api.as_ptr() as *mut c_void,
                },
                Param {
                    kind: RENDER_INVALID,
                    data: std::ptr::null_mut(),
                },
            ];
            let mut render = std::ptr::null_mut();
            if (lib.render_create)(&mut render, handle, params.as_mut_ptr()) < 0 {
                (lib.terminate_destroy)(handle);
                return Err("this libmpv has no software renderer".into());
            }
            (lib.render_set_update_callback)(
                render,
                frame_ready,
                Arc::as_ptr(&ready) as *mut c_void,
            );
            let video = Self {
                lib,
                handle,
                render,
                ready,
            };
            video.command(&["loadfile", source])?;
            Ok(video)
        }
    }

    pub fn command(&self, args: &[&str]) -> Result<(), String> {
        let owned: Vec<CString> = args.iter().map(|a| c(a)).collect();
        let mut pointers: Vec<*const c_char> = owned.iter().map(|a| a.as_ptr()).collect();
        pointers.push(std::ptr::null());
        // SAFETY: a null-terminated array of C strings that outlive the call.
        match unsafe { (self.lib.command)(self.handle, pointers.as_ptr()) } {
            0.. => Ok(()),
            code => Err(format!("mpv command {args:?}: {code}")),
        }
    }

    fn property<T: Default>(&self, name: &str, format: c_int) -> Option<T> {
        let mut value = T::default();
        let name = c(name);
        // SAFETY: `value` has the layout of `format`.
        let code = unsafe {
            (self.lib.get_property)(
                self.handle,
                name.as_ptr(),
                format,
                &mut value as *mut T as *mut c_void,
            )
        };
        (code >= 0).then_some(value)
    }

    pub fn seconds(&self, name: &str) -> Option<f64> {
        self.property::<f64>(name, FORMAT_DOUBLE)
    }

    pub fn count(&self, name: &str) -> Option<i64> {
        self.property::<i64>(name, FORMAT_INT64)
    }

    pub fn paused(&self) -> bool {
        self.property::<c_int>("pause", FORMAT_FLAG).unwrap_or(0) != 0
    }

    pub fn set_paused(&self, paused: bool) {
        let mut flag: c_int = paused.into();
        let name = c("pause");
        // SAFETY: a flag property set from a C int.
        unsafe {
            (self.lib.set_property)(
                self.handle,
                name.as_ptr(),
                FORMAT_FLAG,
                &mut flag as *mut c_int as *mut c_void,
            );
        }
    }

    /// The size mpv would show the video at, at most 1080p's number of
    /// pixels, in even numbers (what video scalers expect).
    fn frame_size(&self) -> Option<(u32, u32)> {
        let w = u32::try_from(self.property::<i64>("dwidth", FORMAT_INT64)?).ok()?;
        let h = u32::try_from(self.property::<i64>("dheight", FORMAT_INT64)?).ok()?;
        if w == 0 || h == 0 {
            return None;
        }
        let scale = (MAX_PIXELS as f64 / (f64::from(w) * f64::from(h)))
            .sqrt()
            .min(1.0);
        // `scale` <= 1, so each side stays within its u32 original.
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let fit = |side: u32| ((f64::from(side) * scale) as u32) & !1;
        Some((fit(w), fit(h))).filter(|&(w, h)| w > 0 && h > 0)
    }

    /// The next frame, if mpv has one: drawn into memory, ready for gpui.
    pub fn frame(&mut self) -> Option<RenderImage> {
        if self.render.is_null() || !self.ready.swap(false, Ordering::Acquire) {
            return None;
        }
        // SAFETY: called after mpv's update callback, as the render API asks.
        let flags = unsafe { (self.lib.render_update)(self.render) };
        if flags & RENDER_UPDATE_FRAME == 0 {
            return None;
        }
        let (w, h) = self.frame_size()?;
        let mut size: [c_int; 2] = [c_int::try_from(w).ok()?, c_int::try_from(h).ok()?];
        let mut stride = usize::try_from(w).ok()?.checked_mul(4)?;
        let mut pixels = vec![0u8; stride.checked_mul(usize::try_from(h).ok()?)?];
        let format = c("bgr0");
        let mut params = [
            Param {
                kind: RENDER_SW_SIZE,
                data: size.as_mut_ptr() as *mut c_void,
            },
            Param {
                kind: RENDER_SW_FORMAT,
                data: format.as_ptr() as *mut c_void,
            },
            Param {
                kind: RENDER_SW_STRIDE,
                data: &mut stride as *mut usize as *mut c_void,
            },
            Param {
                kind: RENDER_SW_POINTER,
                data: pixels.as_mut_ptr() as *mut c_void,
            },
            Param {
                kind: RENDER_INVALID,
                data: std::ptr::null_mut(),
            },
        ];
        // SAFETY: the buffer holds `stride * h` bytes and outlives the call.
        if unsafe { (self.lib.render_render)(self.render, params.as_mut_ptr()) } < 0 {
            return None;
        }
        // "bgr0" leaves the fourth byte at zero: gpui reads it as alpha.
        for pixel in pixels.chunks_exact_mut(4) {
            pixel[3] = 255;
        }
        let buffer = image::RgbaImage::from_raw(w, h, pixels)?;
        Some(RenderImage::new(vec![image::Frame::new(buffer)]))
    }
}

/// macOS: mpv draws with OpenGL into a view under gpui's (`mac_native.rs`).
#[cfg(target_os = "macos")]
impl Video {
    /// With the view's OpenGL context current, before the file opens.
    pub fn create_gl_render(
        &mut self,
        get_proc_address: extern "C" fn(*mut c_void, *const c_char) -> *mut c_void,
    ) -> Result<(), String> {
        #[repr(C)]
        struct Init {
            get_proc_address: extern "C" fn(*mut c_void, *const c_char) -> *mut c_void,
            ctx: *mut c_void,
        }
        let mut init = Init {
            get_proc_address,
            ctx: std::ptr::null_mut(),
        };
        let api = c("opengl");
        let mut params = [
            Param {
                kind: RENDER_API_TYPE,
                data: api.as_ptr() as *mut c_void,
            },
            Param {
                kind: 2, // MPV_RENDER_PARAM_OPENGL_INIT_PARAMS
                data: (&raw mut init).cast(),
            },
            Param {
                kind: RENDER_INVALID,
                data: std::ptr::null_mut(),
            },
        ];
        let mut render = std::ptr::null_mut();
        // SAFETY: the parameters outlive the call; mpv copies what it keeps.
        if unsafe { (self.lib.render_create)(&mut render, self.handle, params.as_mut_ptr()) } < 0 {
            return Err("mpv's OpenGL renderer".into());
        }
        self.render = render;
        Ok(())
    }

    /// Whether mpv has a new frame to draw (on the main thread).
    pub fn gl_frame_ready(&self) -> bool {
        // SAFETY: a live render context.
        !self.render.is_null()
            && unsafe { (self.lib.render_update)(self.render) } & RENDER_UPDATE_FRAME != 0
    }

    /// Draws the current frame into framebuffer `fbo` of `width`×`height`.
    pub fn gl_render(&self, fbo: c_int, width: c_int, height: c_int) {
        #[repr(C)]
        struct Fbo {
            fbo: c_int,
            w: c_int,
            h: c_int,
            internal_format: c_int,
        }
        if self.render.is_null() {
            return;
        }
        let mut target = Fbo {
            fbo,
            w: width,
            h: height,
            internal_format: 0,
        };
        let mut flip: c_int = 1;
        let mut params = [
            Param {
                kind: 3, // MPV_RENDER_PARAM_OPENGL_FBO
                data: (&raw mut target).cast(),
            },
            Param {
                kind: 4, // MPV_RENDER_PARAM_FLIP_Y
                data: (&raw mut flip).cast(),
            },
            Param {
                kind: RENDER_INVALID,
                data: std::ptr::null_mut(),
            },
        ];
        // SAFETY: a live render context and parameters that outlive the call.
        unsafe { (self.lib.render_render)(self.render, params.as_mut_ptr()) };
    }
}

impl Drop for Video {
    fn drop(&mut self) {
        // SAFETY: the render context goes before the player, as libmpv asks.
        unsafe {
            if !self.render.is_null() {
                (self.lib.render_free)(self.render);
            }
            (self.lib.terminate_destroy)(self.handle);
        }
    }
}
