//! KINO's native interface, phase G0 (docs/dev/plan-gpui.md): a go/no-go trial.
//!
//! - KINO's window: no system frame, its own bar (drag, double press to
//!   maximize, minimize, maximize, close).
//! - A grid of 300 posters in a virtual list: only the rows on screen are
//!   built; the posters arrive as they are made, off the main thread, like
//!   images from the network.
//! - `--bench`: the grid scrolls by itself (fast, down then up), each frame's
//!   time is measured, and the figures are printed as JSON before quitting.
//!   The CI runs it on macOS, Windows and Linux.
//! - `--video [source]`: the player screen instead, a video drawn by libmpv
//!   under gpui's own controls (`video.rs`). With `--bench`, it plays a 1080p
//!   test pattern and prints how long each frame took to reach the screen.

use std::sync::{Arc, Mutex};

#[cfg(windows)]
mod native;
mod video;

/// Processor time this process has used, in ms.
fn cpu_ms() -> f64 {
    #[cfg(windows)]
    {
        native::cpu_ms()
    }
    #[cfg(unix)]
    {
        // SAFETY: getrusage fills the struct it is given.
        let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
        unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut usage) };
        let ms = |t: libc::timeval| t.tv_sec as f64 * 1000.0 + t.tv_usec as f64 / 1000.0;
        ms(usage.ru_utime) + ms(usage.ru_stime)
    }
}
use std::time::{Duration, Instant};

use gpui::{
    App, Application, Bounds, Context, Image, ImageFormat, MouseButton, MouseDownEvent, ObjectFit,
    RenderImage, SharedString, StyledImage, UniformListScrollHandle, Window,
    WindowBackgroundAppearance, WindowBounds, WindowDecorations, WindowOptions, div, img, point,
    prelude::*, px, rgb, size, uniform_list,
};

const POSTERS: usize = 300;
const COLUMNS: usize = 6;
const POSTER_W: f32 = 150.0;
const POSTER_H: f32 = 225.0;
const ROW_H: f32 = 290.0;

// KINO's colours (web/css/design.css).
const BACKGROUND: u32 = 0x09090b;
const SURFACE: u32 = 0x111113;
const PLACEHOLDER: u32 = 0x1d1d1d;
const LINE: u32 = 0x27272a;
const TEXT: u32 = 0xfafafa;
const TEXT_2: u32 = 0xa1a1aa;

/// The automatic scroll of `--bench`: 4 s down, 4 s up, at 4,000 px/s.
const BENCH_LEG: Duration = Duration::from_secs(4);
const BENCH_SPEED: f32 = 4000.0;

struct Bench {
    started: Instant,
    last: Option<Instant>,
    frames: Vec<f64>,
    cpu_start: f64,
}

impl Bench {
    fn new() -> Self {
        Self {
            started: Instant::now(),
            last: None,
            frames: Vec::new(),
            cpu_start: cpu_ms(),
        }
    }

    /// Records this frame; the time since the start.
    fn tick(&mut self) -> Duration {
        let now = Instant::now();
        if let Some(last) = self.last {
            self.frames
                .push(now.duration_since(last).as_secs_f64() * 1000.0);
        }
        self.last = Some(now);
        now.duration_since(self.started)
    }

    fn report(&self) -> String {
        let mut frames = self.frames.clone();
        frames.sort_by(f64::total_cmp);
        let at = |q: f64| {
            frames
                .get(((frames.len() as f64) * q) as usize)
                .copied()
                .unwrap_or(0.0)
        };
        serde_json::json!({
            "frames": frames.len(),
            "p50_ms": (at(0.5) * 10.0).round() / 10.0,
            "p95_ms": (at(0.95) * 10.0).round() / 10.0,
            "longest_ms": (frames.last().copied().unwrap_or(0.0) * 10.0).round() / 10.0,
            "over_33ms": frames.iter().filter(|f| **f > 33.4).count(),
            // Processor use over the run, as a share of one core.
            "cpu_pct": ((cpu_ms() - self.cpu_start)
                / (self.started.elapsed().as_secs_f64() * 1000.0) * 1000.0).round() / 10.0,
        })
        .to_string()
    }
}

struct Kino {
    posters: Vec<Option<Arc<Image>>>,
    list: UniformListScrollHandle,
    bench: Option<Bench>,
}

/// A poster (171×256 JPEG): a gradient of its own, like a real one, so that
/// no two images are the same.
fn poster(index: usize) -> Vec<u8> {
    let hue = (index * 37 % 360) as f32;
    let picture = image::RgbImage::from_fn(171, 256, |x, y| {
        let t = y as f32 / 256.0;
        let shade = |offset: f32| {
            let h = ((hue + offset) % 360.0) / 60.0;
            let c = 1.0 - (h % 2.0 - 1.0).abs();
            (c * 180.0 * (1.0 - t) + 20.0 + (x % 32) as f32) as u8
        };
        image::Rgb([shade(0.0), shade(120.0), shade(240.0)])
    });
    let mut bytes = Vec::new();
    let encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut bytes, 80);
    let _ = picture.write_with_encoder(encoder);
    bytes
}

impl Kino {
    fn new(bench: bool, cx: &mut Context<Self>) -> Self {
        // The posters arrive in batches, made off the main thread.
        cx.spawn(async move |this, cx| {
            for start in (0..POSTERS).step_by(25) {
                let batch = cx
                    .background_executor()
                    .spawn(async move {
                        (start..(start + 25).min(POSTERS))
                            .map(|i| Arc::new(Image::from_bytes(ImageFormat::Jpeg, poster(i))))
                            .collect::<Vec<_>>()
                    })
                    .await;
                let done = this.update(cx, |kino, cx| {
                    for (offset, image) in batch.into_iter().enumerate() {
                        kino.posters[start + offset] = Some(image);
                    }
                    cx.notify();
                });
                if done.is_err() {
                    return;
                }
            }
        })
        .detach();
        Self {
            posters: vec![None; POSTERS],
            list: UniformListScrollHandle::new(),
            bench: bench.then(Bench::new),
        }
    }

    /// `--bench`: where the grid is now, this frame's time, and the end.
    fn bench_frame(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(bench) = self.bench.as_mut() else {
            return;
        };
        let elapsed = bench.tick();
        if elapsed >= BENCH_LEG * 2 {
            println!("KINO-UI BENCH {}", bench.report());
            cx.quit();
            return;
        }
        let leg = BENCH_LEG.as_secs_f32();
        let t = elapsed.as_secs_f32();
        let travelled = if t < leg { t } else { 2.0 * leg - t };
        let content = (POSTERS.div_ceil(COLUMNS) as f32) * ROW_H;
        let y = (travelled * BENCH_SPEED).min(content);
        self.list
            .0
            .borrow()
            .base_handle
            .set_offset(point(px(0.0), px(-y)));
        window.request_animation_frame();
    }
}

/// KINO's bar: a press moves the window, a double press maximizes it.
fn bar() -> impl IntoElement {
    {
        let button = |id: &'static str, label: &'static str| {
            div()
                .id(id)
                .w(px(46.0))
                .h_full()
                .flex()
                .items_center()
                .justify_center()
                .text_color(rgb(TEXT_2))
                .hover(|style| style.bg(rgb(LINE)).text_color(rgb(TEXT)))
                // A press on a button never moves the window.
                .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
                .child(label)
        };
        div()
            .id("bar")
            .h(px(44.0))
            .flex()
            .flex_none()
            .items_center()
            .bg(rgb(SURFACE))
            .border_b_1()
            .border_color(rgb(LINE))
            // A press on the bar moves the window; a double press maximizes it.
            .on_mouse_down(MouseButton::Left, |event: &MouseDownEvent, window, _| {
                if event.click_count >= 2 {
                    window.zoom_window();
                } else {
                    window.start_window_move();
                }
            })
            .child(
                div()
                    .pl_4()
                    .font_weight(gpui::FontWeight::BOLD)
                    .child("KINO"),
            )
            .child(div().flex_grow())
            .child(button("minimize", "—").on_click(|_, window, _| window.minimize_window()))
            .child(button("maximize", "▢").on_click(|_, window, _| window.zoom_window()))
            .child(
                button("close", "✕")
                    .hover(|style| style.bg(rgb(0xc42b1c)).text_color(rgb(TEXT)))
                    .on_click(|_, _, cx| cx.quit()),
            )
    }
}

fn poster_card(index: usize, image: Option<Arc<Image>>) -> impl IntoElement {
    let art = div()
        .w(px(POSTER_W))
        .h(px(POSTER_H))
        .rounded_md()
        .overflow_hidden()
        .bg(rgb(PLACEHOLDER));
    let art = match image {
        Some(image) => art.child(img(image).w(px(POSTER_W)).h(px(POSTER_H))),
        None => art,
    };
    div()
        .w(px(POSTER_W))
        .flex()
        .flex_col()
        .gap_2()
        .child(art)
        .child(
            div()
                .text_sm()
                .text_color(rgb(TEXT))
                .child(SharedString::from(format!("Film {}", index + 1))),
        )
        .child(
            div()
                .text_xs()
                .text_color(rgb(TEXT_2))
                .child("2026 • ★ 7.5"),
        )
}

impl Render for Kino {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.bench_frame(window, cx);
        let rows = POSTERS.div_ceil(COLUMNS);
        div()
            .size_full()
            .flex()
            .flex_col()
            .bg(rgb(BACKGROUND))
            .text_color(rgb(TEXT))
            .child(bar())
            .child(
                uniform_list(
                    "posters",
                    rows,
                    cx.processor(|kino, range: std::ops::Range<usize>, _, _| {
                        range
                            .map(|row| {
                                div()
                                    .id(row)
                                    .h(px(ROW_H))
                                    .px_6()
                                    .pt_4()
                                    .flex()
                                    .gap_4()
                                    .children(
                                        (row * COLUMNS..((row + 1) * COLUMNS).min(POSTERS))
                                            .map(|i| poster_card(i, kino.posters[i].clone())),
                                    )
                            })
                            .collect::<Vec<_>>()
                    }),
                )
                .track_scroll(self.list.clone())
                .flex_grow(),
            )
    }
}

/// The 1080p test pattern `--video --bench` plays: moving, with a clock.
const TEST_PATTERN: &str = "av://lavfi:testsrc2=size=1920x1080:rate=30";

/// The player screen: libmpv's frames as an image, gpui's controls over them.
struct Player {
    video: Arc<Mutex<video::Video>>,
    frame: Option<Arc<RenderImage>>,
    /// Time to draw each frame in memory, in ms (`--bench`).
    draws: Vec<f64>,
    bench: Option<Bench>,
    /// The native path (Windows): KINO's window and the child mpv draws in.
    host: Option<(isize, isize)>,
    /// `--bench --native`: whether a real click on the play button, over
    /// mpv's window, reached gpui (pause state before the click).
    click: Option<(bool, Option<bool>)>,
    /// `--bench --native`: what Windows found under the click, and whether
    /// gpui saw the button pressed.
    probe: Option<String>,
    pressed: bool,
}

impl Player {
    fn new(
        video: video::Video,
        host: Option<(isize, isize)>,
        bench: bool,
        cx: &mut Context<Self>,
    ) -> Self {
        let video = Arc::new(Mutex::new(video));
        let shared = video.clone();
        if host.is_some() {
            // mpv draws by itself: gpui only refreshes its controls.
            cx.spawn(async move |this, cx| {
                loop {
                    cx.background_executor()
                        .timer(Duration::from_millis(250))
                        .await;
                    if this.update(cx, |_, cx| cx.notify()).is_err() {
                        return;
                    }
                }
            })
            .detach();
            return Self {
                video,
                frame: None,
                draws: Vec::new(),
                bench: bench.then(Bench::new),
                host,
                click: None,
                probe: None,
                pressed: false,
            };
        }
        // mpv says when a frame is ready; it is drawn off the main thread.
        cx.spawn(async move |this, cx| {
            loop {
                cx.background_executor()
                    .timer(Duration::from_millis(4))
                    .await;
                let video = shared.clone();
                let drawn = cx
                    .background_executor()
                    .spawn(async move {
                        let started = Instant::now();
                        let frame = video.lock().ok()?.frame()?;
                        Some((frame, started.elapsed().as_secs_f64() * 1000.0))
                    })
                    .await;
                let alive = this.update(cx, |player, cx| {
                    if let Some((frame, ms)) = drawn {
                        player.draws.push(ms);
                        if let Some(old) = player.frame.replace(Arc::new(frame)) {
                            // The last frame leaves the GPU's atlas.
                            cx.drop_image(old, None);
                        }
                        cx.notify();
                    }
                });
                if alive.is_err() {
                    return;
                }
            }
        })
        .detach();
        Self {
            video,
            frame: None,
            draws: Vec::new(),
            bench: bench.then(Bench::new),
            host: None,
            click: None,
            probe: None,
            pressed: false,
        }
    }

    fn bench_frame(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(bench) = self.bench.as_mut() else {
            return;
        };
        let elapsed = bench.tick();
        #[cfg(windows)]
        if let Some((parent, _)) = self.host {
            // At 3 s, a real click on the play button; at 4.5 s, its effect.
            let paused = self.video.lock().map(|v| v.paused()).unwrap_or(false);
            match self.click {
                None if elapsed >= Duration::from_secs(3) => {
                    let scale = window.scale_factor();
                    let height = f32::from(window.viewport_size().height);
                    let (x, y) = (44.0 * scale, (height - 36.0) * scale);
                    self.click = Some((paused, None));
                    if let Some((_, host)) = self.host {
                        self.probe = Some(native::probe(parent, host, x as i32, y as i32));
                    }
                    native::click(parent, x as i32, y as i32);
                }
                Some((before, None)) if elapsed >= Duration::from_millis(4500) => {
                    self.click = Some((before, Some(paused != before)));
                    // The run goes on playing.
                    if paused {
                        if let Ok(video) = self.video.lock() {
                            video.set_paused(false);
                        }
                    }
                }
                _ => {}
            }
        }
        if elapsed >= BENCH_LEG * 2 {
            let mut draws = self.draws.clone();
            draws.sort_by(f64::total_cmp);
            let at = |q: f64| {
                draws
                    .get(((draws.len() as f64) * q) as usize)
                    .map_or(0.0, |d| (d * 10.0).round() / 10.0)
            };
            let mut report: serde_json::Value =
                serde_json::from_str(&bench.report()).unwrap_or_default();
            report["video_frames"] = self.draws.len().into();
            report["draw_p50_ms"] = at(0.5).into();
            report["draw_p95_ms"] = at(0.95).into();
            if self.host.is_some() {
                let video = self.video.lock().ok();
                let count = |name| video.as_ref().and_then(|v| v.count(name));
                report["path"] = "native".into();
                report["video_dropped"] = count("frame-drop-count").into();
                report["decoder_dropped"] = count("decoder-frame-drop-count").into();
                report["click_reached_controls"] =
                    self.click.and_then(|(_, reached)| reached).into();
                report["click_probe"] = self.probe.clone().into();
                report["press_seen"] = self.pressed.into();
            } else {
                report["path"] = "software".into();
            }
            println!("KINO-UI VIDEO {report}");
            cx.quit();
            return;
        }
        window.request_animation_frame();
    }

    fn controls(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let (position, duration, paused) = match self.video.lock() {
            Ok(video) => (
                video.seconds("time-pos").unwrap_or(0.0),
                video.seconds("duration").unwrap_or(0.0),
                video.paused(),
            ),
            Err(_) => (0.0, 0.0, false),
        };
        let clock = |s: f64| format!("{}:{:02}", (s as u64) / 60, (s as u64) % 60);
        let done = if duration > 0.0 {
            (position / duration).clamp(0.0, 1.0) as f32
        } else {
            0.0
        };
        div()
            .absolute()
            .bottom_0()
            .left_0()
            .right_0()
            .px_6()
            .py_4()
            .flex()
            .flex_col()
            .gap_3()
            .bg(gpui::rgba(0x000000b0))
            .child(
                // The progress bar.
                div()
                    .h(px(4.0))
                    .w_full()
                    .rounded_full()
                    .bg(gpui::rgba(0xffffff40))
                    .child(
                        div()
                            .h_full()
                            .w(gpui::relative(done))
                            .rounded_full()
                            .bg(rgb(0xe50914)),
                    ),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap_4()
                    .child(
                        div()
                            .id("play")
                            .w(px(40.0))
                            .h(px(40.0))
                            .rounded_full()
                            .flex()
                            .items_center()
                            .justify_center()
                            .bg(gpui::rgba(0xffffff26))
                            .hover(|style| style.bg(gpui::rgba(0xffffff40)))
                            .child(if paused { "▶" } else { "❚❚" })
                            .on_mouse_down(
                                MouseButton::Left,
                                cx.listener(|player, _, _, _| player.pressed = true),
                            )
                            .on_click(cx.listener(|player, _, _, cx| {
                                if let Ok(video) = player.video.lock() {
                                    video.set_paused(!video.paused());
                                }
                                cx.notify();
                            })),
                    )
                    .child(
                        div()
                            .text_sm()
                            .text_color(rgb(TEXT_2))
                            .child(SharedString::from(format!(
                                "{} / {}",
                                clock(position),
                                clock(duration)
                            ))),
                    ),
            )
    }
}

impl Render for Player {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.bench_frame(window, cx);
        #[cfg(windows)]
        if let Some((parent, host)) = self.host {
            // mpv's window sits under the area below the bar.
            native::place_host(parent, host, (44.0 * window.scale_factor()).round() as i32);
        }
        // On the native path the picture's area stays transparent: mpv's
        // window shows through it.
        let picture = div().size_full();
        let picture = if self.host.is_some() {
            picture
        } else {
            picture.bg(gpui::black())
        };
        let picture = match &self.frame {
            Some(frame) => picture.child(
                img(frame.clone())
                    .size_full()
                    .object_fit(ObjectFit::Contain),
            ),
            None => picture,
        };
        let root = div().size_full().flex().flex_col().text_color(rgb(TEXT));
        let root = if self.host.is_some() {
            root
        } else {
            root.bg(rgb(BACKGROUND))
        };
        root.child(bar()).child(
            div()
                .relative()
                .flex_grow()
                .child(picture)
                .child(
                    div()
                        .absolute()
                        .top_4()
                        .left_6()
                        .px_3()
                        .py_1()
                        .rounded_md()
                        .bg(gpui::rgba(0x000000b0))
                        .child("KINO test pattern"),
                )
                .child(self.controls(cx)),
        )
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let bench = args.iter().any(|a| a == "--bench");
    let video = args.iter().position(|a| a == "--video").map(|at| {
        args.get(at + 1)
            .filter(|a| !a.starts_with("--"))
            .cloned()
            .unwrap_or_else(|| TEST_PATTERN.to_string())
    });
    let native = args.iter().any(|a| a == "--native");
    if native && !cfg!(windows) {
        eprintln!("KINO-UI VIDEO ERROR --native is for Windows");
        std::process::exit(2);
    }
    Application::new().run(move |cx: &mut App| {
        let bounds = Bounds::centered(None, size(px(1180.0), px(760.0)), cx);
        let options = WindowOptions {
            window_bounds: Some(WindowBounds::Windowed(bounds)),
            titlebar: None,
            window_decorations: Some(WindowDecorations::Client),
            app_id: Some("kino".into()),
            window_min_size: Some(size(px(640.0), px(420.0))),
            // The native path shows mpv's window through gpui's surface.
            window_background: if native {
                WindowBackgroundAppearance::Transparent
            } else {
                WindowBackgroundAppearance::Opaque
            },
            ..Default::default()
        };
        let opened = match video.clone() {
            Some(source) => cx
                .open_window(options, |window, cx| {
                    window.set_window_title("KINO");
                    let host = if native { native_host(window) } else { None };
                    // The test pattern loops; a bench run makes no sound.
                    let mut settings = vec![("loop-file", "inf")];
                    if bench {
                        settings.push(("ao", "null"));
                    }
                    let video = video::Video::open(&source, &settings, host.map(|(_, h)| h))
                        .unwrap_or_else(|error| {
                            eprintln!("KINO-UI VIDEO ERROR {error}");
                            std::process::exit(2);
                        });
                    cx.new(|cx| Player::new(video, host, bench, cx))
                })
                .map(|_| ()),
            None => cx
                .open_window(options, |window, cx| {
                    window.set_window_title("KINO");
                    cx.new(|cx| Kino::new(bench, cx))
                })
                .map(|_| ()),
        };
        opened.expect("KINO's window");
        cx.activate(true);
    });
}

/// KINO's window and the child window mpv draws in (Windows).
#[cfg(windows)]
fn native_host(window: &Window) -> Option<(isize, isize)> {
    use raw_window_handle::{HasWindowHandle, RawWindowHandle};
    let RawWindowHandle::Win32(handle) = HasWindowHandle::window_handle(window).ok()?.as_raw()
    else {
        return None;
    };
    let parent = handle.hwnd.get();
    native::create_host(parent).map(|host| (parent, host))
}

#[cfg(not(windows))]
fn native_host(_: &Window) -> Option<(isize, isize)> {
    None
}
