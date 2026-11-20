//! What every screen shares, typed (phase G1): one gpui global. Screens read
//! it, and those that keep derived data (the grid's titles) observe it.
//!
//! For now, the interface's language; KINO's library, settings and session
//! join it when the screens are built on the `kino` crate (G2–G4).

use gpui::{App, Global};

use crate::i18n::{self, Language};

pub struct AppState {
    language: Language,
}

impl Global for AppState {}

impl AppState {
    /// Sets the state up once, at start.
    pub fn init(language: Language, cx: &mut App) {
        i18n::set_language(language);
        cx.set_global(Self { language });
    }

    pub fn language(cx: &App) -> Language {
        cx.global::<Self>().language
    }

    /// Switches the interface's language: every window is drawn again, and
    /// the global's observers rebuild what they derived from it.
    pub fn set_language(language: Language, cx: &mut App) {
        if Self::language(cx) == language {
            return;
        }
        i18n::set_language(language);
        cx.global_mut::<Self>().language = language;
        cx.refresh_windows();
    }
}
