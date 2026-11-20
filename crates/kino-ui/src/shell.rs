//! KINO's shell (phase G1): the window bar, the sidebar with the web version's
//! sections, a header with Back and the search field, and the screen's content.
//! Films and Series show KINO's catalogs (`discover.rs`); the other screens
//! are still empty.
//!
//! Shortcuts: Alt+← goes back; Ctrl+1…8 (⌘ on macOS) opens a screen;
//! Ctrl/⌘+K goes to the search field, Escape empties it.

use gpui::{
    App, Context, Entity, FocusHandle, Focusable, KeyBinding, SharedString, Window, actions, div,
    prelude::*, px,
};

use crate::core::{Catalog, Core};
use crate::discover::Discover;
use crate::i18n::Language;
use crate::image_cache::{DEFAULT_BUDGET, ImageCache};
use crate::search::SearchView;
use crate::state::AppState;
use crate::text_input::{self, TextInput};
use crate::{bar, theme};

actions!(
    kino,
    [
        Back,
        FocusSearch,
        Screen1,
        Screen2,
        Screen3,
        Screen4,
        Screen5,
        Screen6,
        Screen7,
        Screen8,
    ]
);

/// The screens of the sidebar, in its order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Screen {
    Movies,
    Series,
    Anime,
    Manga,
    Tonight,
    Explore,
    Library,
    Settings,
}

impl Screen {
    pub const ALL: [Screen; 8] = [
        Screen::Movies,
        Screen::Series,
        Screen::Anime,
        Screen::Manga,
        Screen::Tonight,
        Screen::Explore,
        Screen::Library,
        Screen::Settings,
    ];

    pub fn title(self) -> &'static str {
        match self {
            Screen::Movies => t!("page.films"),
            Screen::Series => t!("page.series"),
            Screen::Anime => t!("page.anime"),
            Screen::Manga => t!("page.manga"),
            Screen::Tonight => t!("page.ce_soir"),
            Screen::Explore => t!("page.explorer"),
            Screen::Library => t!("page.bibliotheque"),
            Screen::Settings => t!("page.parametres"),
        }
    }

    fn id(self) -> &'static str {
        match self {
            Screen::Movies => "nav-movies",
            Screen::Series => "nav-series",
            Screen::Anime => "nav-anime",
            Screen::Manga => "nav-manga",
            Screen::Tonight => "nav-tonight",
            Screen::Explore => "nav-explore",
            Screen::Library => "nav-library",
            Screen::Settings => "nav-settings",
        }
    }
}

/// Where the user is and where Back leads: no GPU, no window, so it is tested
/// on its own.
#[derive(Debug)]
pub struct Navigation {
    current: Screen,
    history: Vec<Screen>,
}

/// Back remembers this many screens.
const HISTORY: usize = 50;

impl Navigation {
    pub fn new(start: Screen) -> Self {
        Self {
            current: start,
            history: Vec::new(),
        }
    }

    pub fn current(&self) -> Screen {
        self.current
    }

    pub fn can_go_back(&self) -> bool {
        !self.history.is_empty()
    }

    /// Opens `screen`; the screen left is where Back returns. Opening the
    /// screen already shown changes nothing.
    pub fn go(&mut self, screen: Screen) {
        if screen == self.current {
            return;
        }
        if self.history.len() == HISTORY {
            self.history.remove(0);
        }
        self.history.push(self.current);
        self.current = screen;
    }

    /// The screen before; false when there is none.
    pub fn back(&mut self) -> bool {
        match self.history.pop() {
            Some(previous) => {
                self.current = previous;
                true
            }
            None => false,
        }
    }
}

/// The keys of the shell, bound once for the application.
pub fn bind_keys(cx: &mut App) {
    cx.bind_keys([
        KeyBinding::new("alt-left", Back, Some("Shell")),
        KeyBinding::new("secondary-k", FocusSearch, Some("Shell")),
        KeyBinding::new("secondary-1", Screen1, Some("Shell")),
        KeyBinding::new("secondary-2", Screen2, Some("Shell")),
        KeyBinding::new("secondary-3", Screen3, Some("Shell")),
        KeyBinding::new("secondary-4", Screen4, Some("Shell")),
        KeyBinding::new("secondary-5", Screen5, Some("Shell")),
        KeyBinding::new("secondary-6", Screen6, Some("Shell")),
        KeyBinding::new("secondary-7", Screen7, Some("Shell")),
        KeyBinding::new("secondary-8", Screen8, Some("Shell")),
    ]);
}

pub struct Shell {
    navigation: Navigation,
    /// The Discover catalogs, or why KINO's core could not start.
    movies: Result<Entity<Discover>, SharedString>,
    series: Result<Entity<Discover>, SharedString>,
    anime: Result<Entity<Discover>, SharedString>,
    /// Search results; `None` when KINO's core could not start.
    results: Option<Entity<SearchView>>,
    search: Entity<TextInput>,
    /// What the search field holds, as of its last change.
    query: gpui::SharedString,
    focus: FocusHandle,
    /// `--shell-check`: each screen drawn, in order.
    drawn: Option<Vec<Screen>>,
}

impl Shell {
    pub fn new(check: bool, window: &mut Window, cx: &mut Context<Self>) -> Self {
        // KINO's core: its database, network and metadata, as the server's.
        let (movies, series, anime, results) = match Core::start() {
            Ok(core) => {
                let images = cx.new(|_| ImageCache::new(core.image_loader(), DEFAULT_BUDGET));
                let results = {
                    let (core, images) = (core.clone(), images.clone());
                    Some(cx.new(|cx| SearchView::new(core, images, cx)))
                };
                let open = |catalog, cx: &mut Context<Self>| {
                    let (core, images) = (core.clone(), images.clone());
                    Ok(cx.new(|cx| Discover::new(catalog, core, images, cx)))
                };
                (
                    open(Catalog::Movies, cx),
                    open(Catalog::Series, cx),
                    open(Catalog::Anime, cx),
                    results,
                )
            }
            Err(error) => (
                Err(error.clone().into()),
                Err(error.clone().into()),
                Err(error.into()),
                None,
            ),
        };
        let focus = cx.focus_handle();
        window.focus(&focus);
        let search = cx.new(|cx| TextInput::new(t!("page.rechercher_un_film_une_serie"), cx));
        cx.subscribe(&search, |shell, search, _: &text_input::Changed, cx| {
            shell.query = search.read(cx).text().clone();
            if let Some(results) = &shell.results {
                let query = shell.query.clone();
                results.update(cx, |results, cx| results.set_query(&query, cx));
            }
            cx.notify();
        })
        .detach();
        if check {
            run_check(window, cx);
        }
        Self {
            navigation: Navigation::new(Screen::Movies),
            movies,
            series,
            anime,
            results,
            search,
            query: gpui::SharedString::default(),
            focus,
            drawn: check.then(Vec::new),
        }
    }

    fn go(&mut self, screen: Screen, cx: &mut Context<Self>) {
        self.navigation.go(screen);
        cx.notify();
    }

    fn back(&mut self, cx: &mut Context<Self>) {
        if self.navigation.back() {
            cx.notify();
        }
    }

    fn nav_item(&self, screen: Screen, cx: &mut Context<Self>) -> impl IntoElement {
        let active = self.navigation.current() == screen;
        let item = div()
            .id(screen.id())
            .px_3()
            .py_2()
            .rounded(theme::RADIUS_SM)
            .text_size(theme::FS_MD)
            .child(screen.title())
            .on_click(cx.listener(move |shell, _, _, cx| shell.go(screen, cx)));
        if active {
            item.bg(theme::surface_3()).text_color(theme::text())
        } else {
            item.text_color(theme::text_2())
                .hover(|style| style.bg(theme::surface_2()).text_color(theme::text()))
        }
    }

    fn sidebar(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let label = |text: &'static str| {
            div()
                .px_3()
                .pt_4()
                .pb_1()
                .text_size(theme::FS_XS)
                .text_color(theme::text_3())
                .child(text)
        };
        let mut sidebar = div()
            .w(px(220.0))
            .flex_none()
            .h_full()
            .flex()
            .flex_col()
            .gap_1()
            .p_3()
            .border_r_1()
            .border_color(theme::line())
            .child(label(t!("page.decouvrir")));
        for screen in &Screen::ALL[..6] {
            sidebar = sidebar.child(self.nav_item(*screen, cx));
        }
        sidebar
            .child(label(t!("page.personnel")))
            .child(self.nav_item(Screen::Library, cx))
            .child(div().flex_grow())
            .child(self.nav_item(Screen::Settings, cx))
    }

    fn header(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let can_go_back = self.navigation.can_go_back();
        let back = div()
            .id("back")
            .w(px(36.0))
            .h(px(36.0))
            .flex()
            .items_center()
            .justify_center()
            .rounded_full()
            .child("←");
        let back = if can_go_back {
            back.text_color(theme::text())
                .hover(|style| style.bg(theme::surface_2()))
                .on_click(cx.listener(|shell, _, _, cx| shell.back(cx)))
        } else {
            back.text_color(theme::text_3())
        };
        div()
            .h(px(60.0))
            .flex_none()
            .flex()
            .items_center()
            .gap(theme::SPACE_3)
            .px(theme::SPACE_5)
            .border_b_1()
            .border_color(theme::line())
            .child(back)
            .child(
                div()
                    .id("search")
                    .h(px(38.0))
                    .w(px(460.0))
                    .px(theme::SPACE_4)
                    .flex()
                    .items_center()
                    .rounded_full()
                    .bg(theme::surface_2())
                    .border_1()
                    .border_color(theme::line())
                    .child(self.search.clone()),
            )
    }

    fn content(&self, cx: &App) -> gpui::AnyElement {
        // A query takes the content: its results.
        if !self.query.trim().is_empty() {
            if let Some(results) = &self.results {
                return div().size_full().child(results.clone()).into_any_element();
            }
            return div()
                .size_full()
                .p(theme::SPACE_6)
                .text_size(theme::FS_XL)
                .font_weight(gpui::FontWeight::BOLD)
                .child(SharedString::from(t!(
                    "fiche.resultats_pour_x",
                    self.query.trim()
                )))
                .into_any_element();
        }
        match self.navigation.current() {
            Screen::Movies => catalog_view(&self.movies),
            Screen::Series => catalog_view(&self.series),
            Screen::Anime => catalog_view(&self.anime),
            Screen::Settings => settings(cx),
            screen => div()
                .size_full()
                .p(theme::SPACE_6)
                .flex()
                .flex_col()
                .gap(theme::SPACE_2)
                .child(
                    div()
                        .text_size(theme::FS_XL)
                        .font_weight(gpui::FontWeight::BOLD)
                        .child(SharedString::from(screen.title())),
                )
                .into_any_element(),
        }
    }
}

/// Settings: for now the interface's language, the first of KINO's settings
/// in the native interface.
fn settings(cx: &App) -> gpui::AnyElement {
    let current = AppState::language(cx);
    let mut languages = div().flex().flex_wrap().gap(theme::SPACE_2);
    for language in Language::ALL {
        let item = div()
            .id(language.native_name())
            .px(theme::SPACE_4)
            .py(theme::SPACE_2)
            .rounded(theme::RADIUS_SM)
            .border_1()
            .text_size(theme::FS_MD)
            .child(language.native_name())
            .on_click(move |_, _, cx| AppState::set_language(language, cx));
        languages = languages.child(if language == current {
            item.bg(theme::text())
                .text_color(theme::on_light())
                .border_color(theme::text())
        } else {
            item.border_color(theme::line_strong())
                .text_color(theme::text_2())
                .hover(|style| style.bg(theme::surface_2()).text_color(theme::text()))
        });
    }
    div()
        .size_full()
        .p(theme::SPACE_6)
        .flex()
        .flex_col()
        .gap(theme::SPACE_4)
        .child(
            div()
                .text_size(theme::FS_XL)
                .font_weight(gpui::FontWeight::BOLD)
                .child(t!("page.parametres")),
        )
        .child(
            div()
                .text_size(theme::FS_SM)
                .text_color(theme::text_3())
                .child(t!("page.langue")),
        )
        .child(languages)
        .into_any_element()
}

/// A Discover catalog, or why it cannot show.
fn catalog_view(catalog: &Result<Entity<Discover>, SharedString>) -> gpui::AnyElement {
    match catalog {
        Ok(discover) => div().size_full().child(discover.clone()).into_any_element(),
        Err(error) => div()
            .size_full()
            .p(theme::SPACE_6)
            .text_color(theme::text_3())
            .child(error.clone())
            .into_any_element(),
    }
}

impl Focusable for Shell {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus.clone()
    }
}

impl Render for Shell {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let current = self.navigation.current();
        if let Some(drawn) = self.drawn.as_mut()
            && drawn.last() != Some(&current)
        {
            drawn.push(current);
        }
        let mut root = div()
            .id("shell")
            .key_context("Shell")
            .track_focus(&self.focus)
            .on_action(cx.listener(|shell, _: &Back, _, cx| shell.back(cx)))
            .on_action(cx.listener(|shell, _: &FocusSearch, window, cx| {
                window.focus(&shell.search.focus_handle(cx));
            }));
        macro_rules! screen_keys {
            ($($action:ident => $index:expr),*) => {
                $(root = root.on_action(cx.listener(|shell, _: &$action, _, cx| {
                    shell.go(Screen::ALL[$index], cx)
                }));)*
            };
        }
        screen_keys!(Screen1 => 0, Screen2 => 1, Screen3 => 2, Screen4 => 3,
            Screen5 => 4, Screen6 => 5, Screen7 => 6, Screen8 => 7);
        root.size_full()
            .flex()
            .flex_col()
            .bg(theme::bg())
            .text_color(theme::text())
            .child(bar())
            .child(
                div()
                    .flex()
                    .flex_grow()
                    .min_h_0()
                    .child(self.sidebar(cx))
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .flex_grow()
                            .min_w_0()
                            .child(self.header(cx))
                            .child(div().flex_grow().min_h_0().child(self.content(cx))),
                    ),
            )
    }
}

/// `--shell-check`: every screen in turn, then Back to the start, one step
/// every 150 ms; then real keystrokes: the search shortcut, "matrix" typed into
/// the field, Escape; then Spanish from Settings (shared state) and back;
/// then KINO's catalogs. Prints what was drawn, typed, translated and loaded,
/// then quits.
fn run_check(window: &mut Window, cx: &mut Context<Shell>) {
    cx.spawn_in(window, async move |shell, cx| {
        let executor = cx.background_executor().clone();
        let step = || executor.timer(std::time::Duration::from_millis(150));
        step().await;
        for screen in Screen::ALL.iter().skip(1) {
            if shell.update(cx, |s, cx| s.go(*screen, cx)).is_err() {
                return;
            }
            step().await;
        }
        for _ in 1..Screen::ALL.len() {
            if shell.update(cx, |s, cx| s.back(cx)).is_err() {
                return;
            }
            step().await;
        }
        // Keys as a user presses them: bindings first, then the field.
        let shortcut = if cfg!(target_os = "macos") {
            "cmd-k"
        } else {
            "ctrl-k"
        };
        let keys = [shortcut, "m", "a", "t", "r", "i", "x"];
        for key in keys {
            // Through the window, not the shell: its actions update it.
            let pressed = cx.update(|window, cx| {
                if let Ok(keystroke) = gpui::Keystroke::parse(key) {
                    window.dispatch_keystroke(keystroke, cx);
                }
            });
            if pressed.is_err() {
                return;
            }
            step().await;
        }
        let typed = shell
            .update(cx, |s, _| s.query.to_string())
            .unwrap_or_default();
        // The search those keys started: up to 30 s for KINO's answer.
        for _ in 0..200 {
            let searching = shell
                .update(cx, |s, cx| {
                    s.results.as_ref().is_some_and(|r| r.read(cx).results().1)
                })
                .unwrap_or(false);
            if !searching {
                break;
            }
            step().await;
        }
        let found = shell
            .update(cx, |s, cx| {
                s.results.as_ref().map(|r| {
                    let (titles, _) = r.read(cx).results();
                    serde_json::json!({
                        "count": titles.len(),
                        "first": titles.first().map(|t| t.name.to_string()),
                    })
                })
            })
            .ok()
            .flatten();
        let _ = cx.update(|window, cx| {
            if let Ok(escape) = gpui::Keystroke::parse("escape") {
                window.dispatch_keystroke(escape, cx);
            }
        });
        step().await;
        // The shared state: Spanish from Settings, then back.
        let _ = shell.update(cx, |s, cx| s.go(Screen::Settings, cx));
        step().await;
        let start = cx.update(|_, cx| AppState::language(cx)).ok();
        let _ = cx.update(|_, cx| AppState::set_language(Language::Spanish, cx));
        step().await;
        let switched = (
            Screen::Settings.title().to_string(),
            t!("anime.chargement").to_string(),
        );
        if let Some(start) = start {
            let _ = cx.update(|_, cx| AppState::set_language(start, cx));
        }
        step().await;
        // KINO's catalogs, from the network: up to 30 s for the first page.
        for _ in 0..200 {
            let loaded = shell
                .update(cx, |s, cx| {
                    [&s.movies, &s.series, &s.anime].iter().all(|c| match c {
                        Ok(d) => !d.read(cx).titles().is_empty() || d.read(cx).error().is_some(),
                        Err(_) => true,
                    })
                })
                .unwrap_or(true);
            if loaded {
                break;
            }
            step().await;
        }
        let _ = shell.update(cx, |s, cx| {
            let catalog = |c: &Result<Entity<Discover>, SharedString>| match c {
                Ok(d) => {
                    let d = d.read(cx);
                    serde_json::json!({
                        "count": d.titles().len(),
                        "first": d.titles().first().map(|t| t.name.to_string()),
                        "with_poster": d.titles().iter().filter(|t| t.poster.is_some()).count(),
                        "error": d.error().map(|e| e.to_string()),
                    })
                }
                Err(e) => serde_json::json!({ "count": 0, "error": e.to_string() }),
            };
            let (movies, series, anime) =
                (catalog(&s.movies), catalog(&s.series), catalog(&s.anime));
            let drawn: Vec<String> = s
                .drawn
                .iter()
                .flatten()
                .map(|screen| format!("{screen:?}"))
                .collect();
            println!(
                "KINO-UI SHELL {}",
                serde_json::json!({
                    "drawn": drawn,
                    "can_go_back": s.navigation.can_go_back(),
                    "typed": typed,
                    "search": found,
                    "cleared": s.query.is_empty(),
                    "spanish_settings": switched.0,
                    "spanish_loading": switched.1,
                    "back_loading": t!("anime.chargement"),
                    "movies": movies,
                    "series": series,
                    "anime": anime,
                })
            );
            cx.quit();
        });
    })
    .detach();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn back_returns_through_the_screens_opened() {
        let mut nav = Navigation::new(Screen::Movies);
        assert!(!nav.can_go_back());
        nav.go(Screen::Series);
        nav.go(Screen::Library);
        assert_eq!(nav.current(), Screen::Library);
        assert!(nav.back());
        assert_eq!(nav.current(), Screen::Series);
        assert!(nav.back());
        assert_eq!(nav.current(), Screen::Movies);
        assert!(!nav.back());
        assert_eq!(nav.current(), Screen::Movies);
    }

    #[test]
    fn opening_the_current_screen_adds_no_step() {
        let mut nav = Navigation::new(Screen::Movies);
        nav.go(Screen::Movies);
        assert!(!nav.can_go_back());
    }

    #[test]
    fn history_keeps_the_latest_screens() {
        let mut nav = Navigation::new(Screen::Movies);
        for i in 0..(HISTORY * 2) {
            nav.go(Screen::ALL[i % 2 + 1]);
        }
        let mut steps = 0;
        while nav.back() {
            steps += 1;
        }
        assert_eq!(steps, HISTORY);
    }

    #[test]
    fn every_screen_has_a_title() {
        for screen in Screen::ALL {
            assert!(!screen.title().is_empty());
        }
    }
}
