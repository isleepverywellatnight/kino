//! KINO's native player on Windows: libmpv draws in KINO's own window (its
//! `wid`), under the page, whose background turns transparent while it plays
//! so the page's controls show over the video, as on macOS. It turns
//! transparent only once mpv shows the video: before, nothing is under the
//! page, and the player looked gone for a moment. No other process
//! and no window moved between processes (`win_dock.rs` does that for mpv.exe).

use std::sync::{Arc, OnceLock};

use wry::WebView;

use crate::mpv::Mpv;

/// The page's background outside playback (KINO's near-black).
pub const PAGE_BACKGROUND: (u8, u8, u8, u8) = (9, 9, 11, 255);

/// KINO's settings for every player on Windows: drawn in KINO's window, once
/// that window exists, with hardware decoding where it is known to be safe.
pub fn window_options(window: Arc<OnceLock<isize>>) -> impl Fn() -> Vec<(String, String)> {
    move || {
        let mut options = vec![("hwdec".to_string(), "auto-safe".to_string())];
        if let Some(hwnd) = window.get() {
            options.push(("wid".into(), hwnd.to_string()));
        }
        options
    }
}

/// What lives on the main thread while a film plays.
#[derive(Default)]
pub struct MainSide {
    playing: Option<Arc<Mpv>>,
    revealed: bool,
}

impl MainSide {
    /// Opens `path` in `player`; the page keeps its background (« Chargement »)
    /// until `reveal`.
    pub fn open(&mut self, webview: &WebView, player: Arc<Mpv>, path: &str) -> Result<(), String> {
        self.close(webview);
        player.command(&["loadfile".into(), path.into()])?;
        self.playing = Some(player);
        Ok(())
    }

    /// mpv shows `player`'s video: the page's background clears over it.
    pub fn reveal(&mut self, webview: &WebView, player: &Arc<Mpv>) {
        let current = self
            .playing
            .as_ref()
            .is_some_and(|p| Arc::ptr_eq(p, player));
        if current && !self.revealed && webview.set_background_color((0, 0, 0, 0)).is_ok() {
            self.revealed = true;
        }
    }

    /// The page's own background again; the controller then ends the player.
    pub fn close(&mut self, webview: &WebView) {
        self.playing = None;
        if std::mem::take(&mut self.revealed) {
            let _ = webview.set_background_color(PAGE_BACKGROUND);
        }
    }
}
