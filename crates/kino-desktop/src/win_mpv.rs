//! KINO's native player on Windows: libmpv draws in KINO's own window (its
//! `wid`), under the page, whose background turns transparent while it plays
//! so the page's controls show over the video, as on macOS. No other process
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
    playing: bool,
}

impl MainSide {
    /// Opens `path` in `player` and clears the page's background over it.
    pub fn open(&mut self, webview: &WebView, player: Arc<Mpv>, path: &str) -> Result<(), String> {
        self.close(webview);
        player.command(&["loadfile".into(), path.into()])?;
        webview
            .set_background_color((0, 0, 0, 0))
            .map_err(|e| e.to_string())?;
        self.playing = true;
        Ok(())
    }

    /// The page's own background again; the controller then ends the player.
    pub fn close(&mut self, webview: &WebView) {
        if std::mem::take(&mut self.playing) {
            let _ = webview.set_background_color(PAGE_BACKGROUND);
        }
    }
}
