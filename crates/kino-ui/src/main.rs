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

use std::sync::Arc;
use std::time::{Duration, Instant};

use gpui::{
    App, Application, Bounds, Context, Image, ImageFormat, MouseButton, MouseDownEvent,
    SharedString, UniformListScrollHandle, Window, WindowBounds, WindowDecorations, WindowOptions,
    div, img, point, prelude::*, px, rgb, size, uniform_list,
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
}

impl Bench {
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
            bench: bench.then(|| Bench {
                started: Instant::now(),
                last: None,
                frames: Vec::new(),
            }),
        }
    }

    /// `--bench`: where the grid is now, this frame's time, and the end.
    fn bench_frame(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(bench) = self.bench.as_mut() else {
            return;
        };
        let now = Instant::now();
        if let Some(last) = bench.last {
            bench
                .frames
                .push(now.duration_since(last).as_secs_f64() * 1000.0);
        }
        bench.last = Some(now);
        let elapsed = now.duration_since(bench.started);
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

    fn bar(&self, cx: &mut Context<Self>) -> impl IntoElement {
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
                    .on_click(cx.listener(|_, _, _, cx| cx.quit())),
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
            .child(self.bar(cx))
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

fn main() {
    let bench = std::env::args().any(|a| a == "--bench");
    Application::new().run(move |cx: &mut App| {
        let bounds = Bounds::centered(None, size(px(1180.0), px(760.0)), cx);
        cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                titlebar: None,
                window_decorations: Some(WindowDecorations::Client),
                app_id: Some("kino".into()),
                window_min_size: Some(size(px(640.0), px(420.0))),
                ..Default::default()
            },
            |_, cx| cx.new(|cx| Kino::new(bench, cx)),
        )
        .expect("KINO's window");
        cx.activate(true);
    });
}
