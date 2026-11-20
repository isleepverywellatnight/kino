//! KINO's native interface (docs/dev/plan-gpui.md).
//!
//! Launched without options, it opens KINO's shell (`shell.rs`, phase G1).
//! The options run the trials of phase G0 and the CI's checks:
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
//! - `--idle`: the grid at rest; prints the processor's use.
//! - `--shell-check`: the shell visits every screen and comes back.

use std::sync::{Arc, Mutex};

#[macro_use]
mod i18n;
#[cfg(target_os = "macos")]
mod mac_native;
#[cfg(windows)]
mod native;
mod shell;
mod theme;

/// Where mpv draws on the native path: on Windows, KINO's window and the
/// child window inside it; on macOS, mpv's OpenGL view under gpui's.
#[cfg(windows)]
type Host = (isize, isize);
#[cfg(target_os = "macos")]
type Host = mac_native::Host;
#[cfg(not(any(windows, target_os = "macos")))]
type Host = ();
mod video;

/// Processor time this process has used, in ms.
fn cpu_ms() -> f64 {
    #[cfg(windows)]
    {
        native::cpu_ms()
    }
    #[cfg(unix)]
    {
        // SAFETY: `rusage` is plain integers, for which all-zero is valid.
        let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
        // SAFETY: getrusage fills the struct it is given and keeps no pointer.
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
    prelude::*, px, size, uniform_list,
};

const POSTERS: usize = 300;
const COLUMNS: usize = 6;
const POSTER_W: f32 = 150.0;
const POSTER_H: f32 = 225.0;
const ROW_H: f32 = 290.0;

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
    /// Each poster's title, made once: a scroll redraws many cards a second.
    titles: Vec<SharedString>,
    list: UniformListScrollHandle,
    bench: Option<Bench>,
    /// Alone in its window, with KINO's bar; else inside the shell.
    framed: bool,
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
            titles: (1..=POSTERS)
                .map(|n| SharedString::from(format!("{} {n}", t!("anime.film"))))
                .collect(),
            list: UniformListScrollHandle::new(),
            bench: bench.then(Bench::new),
            framed: true,
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
pub(crate) fn bar() -> impl IntoElement {
    {
        let button = |id: &'static str, label: &'static str| {
            div()
                .id(id)
                .w(px(46.0))
                .h_full()
                .flex()
                .items_center()
                .justify_center()
                .text_color(theme::text_2())
                .hover(|style| style.bg(theme::line_strong()).text_color(theme::text()))
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
            .bg(theme::surface_1())
            .border_b_1()
            .border_color(theme::line_strong())
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
                    .hover(|style| style.bg(theme::danger()).text_color(theme::text()))
                    .on_click(|_, _, cx| cx.quit()),
            )
    }
}

fn poster_card(title: SharedString, image: Option<Arc<Image>>) -> impl IntoElement {
    let art = div()
        .w(px(POSTER_W))
        .h(px(POSTER_H))
        .rounded_md()
        .overflow_hidden()
        .bg(theme::surface_2());
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
        .child(div().text_sm().text_color(theme::text()).child(title))
        .child(
            div()
                .text_xs()
                .text_color(theme::text_2())
                .child("2026 • ★ 7.5"),
        )
}

impl Render for Kino {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.bench_frame(window, cx);
        let rows = POSTERS.div_ceil(COLUMNS);
        let root = div()
            .size_full()
            .flex()
            .flex_col()
            .text_color(theme::text());
        let root = if self.framed {
            root.bg(theme::bg()).child(bar())
        } else {
            root
        };
        root.child(
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
                                .children((row * COLUMNS..((row + 1) * COLUMNS).min(POSTERS)).map(
                                    |i| {
                                        poster_card(kino.titles[i].clone(), kino.posters[i].clone())
                                    },
                                ))
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
    host: Option<Host>,
    /// `--bench --native`: whether a real click on the play button, over
    /// mpv's window, reached gpui (pause state before the click).
    click: Option<(bool, Option<bool>)>,
    /// `--bench --native`: what Windows found under the click, and whether
    /// gpui saw the button pressed.
    probe: Option<String>,
    pressed: bool,
    /// KINO's window (Windows), for the bench's click on either path.
    parent: Option<isize>,
    /// Where gpui saw the last press anywhere in the window (`--bench`).
    root_press: Option<(f32, f32)>,
}

impl Player {
    fn new(
        video: Arc<Mutex<video::Video>>,
        host: Option<Host>,
        bench: bool,
        cx: &mut Context<Self>,
    ) -> Self {
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
                parent: None,
                root_press: None,
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
            parent: None,
            root_press: None,
        }
    }

    fn bench_frame(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(bench) = self.bench.as_mut() else {
            return;
        };
        let elapsed = bench.tick();
        #[cfg(windows)]
        if let Some(parent) = self.parent {
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
                    self.probe = Some(format!(
                        "{} target=({x},{y}) viewport={:?} scale={scale}",
                        self.probe.take().unwrap_or_default(),
                        window.viewport_size()
                    ));
                    native::click(parent, x as i32, y as i32);
                }
                Some((before, None)) if elapsed >= Duration::from_millis(4500) => {
                    self.click = Some((before, Some(paused != before)));
                    // The run goes on playing.
                    if paused && let Ok(video) = self.video.lock() {
                        video.set_paused(false);
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
            if self.parent.is_some() {
                report["click_reached_controls"] =
                    self.click.and_then(|(_, reached)| reached).into();
                report["click_probe"] = self.probe.clone().into();
                report["press_seen"] = self.pressed.into();
                report["root_press"] = format!("{:?}", self.root_press).into();
            }
            if self.host.is_some() {
                let video = self.video.lock().ok();
                let count = |name| video.as_ref().and_then(|v| v.count(name));
                report["path"] = "native".into();
                report["video_dropped"] = count("frame-drop-count").into();
                report["decoder_dropped"] = count("decoder-frame-drop-count").into();
                #[cfg(target_os = "macos")]
                if let Some(host) = &self.host {
                    use std::sync::atomic::Ordering::Relaxed;
                    let frames = host.stats.frames.load(Relaxed);
                    report["video_frames"] = frames.into();
                    report["gl_accelerated"] = host.stats.accelerated.load(Relaxed).into();
                    // Mean time to draw a frame, in ms to one decimal.
                    let mean_us = host.stats.draw_us.load(Relaxed) as f64 / frames.max(1) as f64;
                    report["draw_avg_ms"] = ((mean_us / 100.0).round() / 10.0).into();
                }
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
                            .bg(theme::progress()),
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
                            .text_color(theme::text_2())
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
        let root = div()
            .size_full()
            .flex()
            .flex_col()
            .text_color(theme::text())
            .capture_any_mouse_down(cx.listener(|player, event: &MouseDownEvent, _, _| {
                player.root_press =
                    Some((f32::from(event.position.x), f32::from(event.position.y)));
            }));
        let root = if self.host.is_some() {
            root
        } else {
            root.bg(theme::bg())
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
    // KINO_LANG (fr, en, …) wins over the system's language.
    i18n::set_language(
        std::env::var("KINO_LANG")
            .ok()
            .and_then(|code| i18n::Language::from_code(&code))
            .unwrap_or_else(i18n::system_language),
    );
    let bench = args.iter().any(|a| a == "--bench");
    let idle = args.iter().any(|a| a == "--idle");
    let shell_check = args.iter().any(|a| a == "--shell-check");
    let video = args.iter().position(|a| a == "--video").map(|at| {
        args.get(at + 1)
            .filter(|a| !a.starts_with("--"))
            .cloned()
            .unwrap_or_else(|| TEST_PATTERN.to_string())
    });
    let native = args.iter().any(|a| a == "--native");
    if native && !cfg!(any(windows, target_os = "macos")) {
        eprintln!("KINO-UI VIDEO ERROR --native is for Windows and macOS");
        std::process::exit(2);
    }
    Application::new().run(move |cx: &mut App| {
        shell::bind_keys(cx);
        // 1180×760, or less on a smaller screen: the whole window stays on it.
        let screen = cx
            .primary_display()
            .map(|display| display.bounds().size)
            .unwrap_or(size(px(1280.0), px(800.0)));
        let fit = |wanted: f32, room: gpui::Pixels| px(wanted.min(f32::from(room) - 48.0));
        let bounds = Bounds::centered(
            None,
            size(fit(1180.0, screen.width), fit(760.0, screen.height)),
            cx,
        );
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
                    // The test pattern loops; a bench run makes no sound.
                    let mut settings = vec![("loop-file", "inf")];
                    if bench {
                        settings.push(("ao", "null"));
                    }
                    let (video, host) = open_player(window, &source, &settings, native)
                        .unwrap_or_else(|error| {
                            eprintln!("KINO-UI VIDEO ERROR {error}");
                            std::process::exit(2);
                        });
                    let parent = window_hwnd(window);
                    cx.new(|cx| {
                        let mut player = Player::new(video, host, bench, cx);
                        player.parent = parent;
                        player
                    })
                })
                .map(|_| ()),
            None if !(bench || idle) => cx
                .open_window(options, |window, cx| {
                    window.set_window_title("KINO");
                    cx.new(|cx| shell::Shell::new(shell_check, window, cx))
                })
                .map(|_| ()),
            None => cx
                .open_window(options, |window, cx| {
                    window.set_window_title("KINO");
                    if idle {
                        measure_idle(cx);
                    }
                    cx.new(|cx| Kino::new(bench, cx))
                })
                .map(|_| ()),
        };
        if let Err(error) = opened {
            // No display, or the GPU refused the window: say so and stop.
            eprintln!("KINO-UI ERROR cannot open KINO's window: {error:#}");
            std::process::exit(1);
        }
        cx.activate(true);
    });
}

/// KINO's window handle (Windows).
#[cfg(windows)]
fn window_hwnd(window: &Window) -> Option<isize> {
    use raw_window_handle::{HasWindowHandle, RawWindowHandle};
    match HasWindowHandle::window_handle(window).ok()?.as_raw() {
        RawWindowHandle::Win32(handle) => Some(handle.hwnd.get()),
        _ => None,
    }
}

#[cfg(not(windows))]
fn window_hwnd(_: &Window) -> Option<isize> {
    None
}

type Opened = (Arc<Mutex<video::Video>>, Option<Host>);

/// The player for `source`: on the native path, mpv draws itself under
/// gpui (Windows: a child window; macOS: an OpenGL view); otherwise its frames
/// come through memory (`video.rs`).
fn open_player(
    window: &Window,
    source: &str,
    settings: &[(&str, &str)],
    native: bool,
) -> Result<Opened, String> {
    if !native {
        let video = video::Video::open(source, settings, None)?;
        return Ok((Arc::new(Mutex::new(video)), None));
    }
    #[cfg(windows)]
    {
        let parent = window_hwnd(window).ok_or("KINO's window")?;
        let child = native::create_host(parent).ok_or("mpv's window")?;
        let video = video::Video::open(source, settings, Some(child))?;
        Ok((Arc::new(Mutex::new(video)), Some((parent, child))))
    }
    #[cfg(target_os = "macos")]
    {
        use raw_window_handle::{HasWindowHandle, RawWindowHandle};
        let RawWindowHandle::AppKit(handle) = HasWindowHandle::window_handle(window)
            .map_err(|e| e.to_string())?
            .as_raw()
        else {
            return Err("KINO's view".into());
        };
        let mut settings = settings.to_vec();
        settings.push(("hwdec", "videotoolbox"));
        // Opened without a file: mpv's OpenGL renderer comes first.
        let video = Arc::new(Mutex::new(video::Video::open("", &settings, None)?));
        let host = mac_native::attach(handle.ns_view.as_ptr(), video.clone(), source)?;
        Ok((video, Some(host)))
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        let _ = window;
        Err("no native video path on this system".into())
    }
}

/// `--idle`: the grid on screen, nothing moving. After 5 s (posters loaded),
/// the processor time used over 10 s, as a share of one core, then quit.
fn measure_idle(cx: &mut App) {
    cx.spawn(async move |cx| {
        let timer = |s| cx.background_executor().timer(Duration::from_secs(s));
        timer(5).await;
        let (start, at) = (cpu_ms(), Instant::now());
        timer(10).await;
        let pct = (cpu_ms() - start) / (at.elapsed().as_secs_f64() * 1000.0) * 100.0;
        println!(
            "KINO-UI IDLE {}",
            serde_json::json!({ "cpu_pct": (pct * 10.0).round() / 10.0 })
        );
        let _ = cx.update(|cx| cx.quit());
    })
    .detach();
}
