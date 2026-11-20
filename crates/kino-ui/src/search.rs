//! Search results (phase G2): films and series for what the search field
//! holds, asked for 300 ms after the last keystroke; an answer to a query the
//! user has since changed is dropped.

use std::ops::Range;
use std::time::Duration;

use gpui::{
    Context, Entity, SharedString, UniformListScrollHandle, Window, div, prelude::*, uniform_list,
};

use crate::core::Core;
use crate::discover::title_rows;
use crate::image_cache::ImageCache;
use crate::title::Title;
use crate::{COLUMNS, theme};

/// How long typing must pause before a search starts.
const PAUSE: Duration = Duration::from_millis(300);

pub struct SearchView {
    core: Core,
    images: Entity<ImageCache>,
    query: SharedString,
    /// Bumped on each change: an answer for an older one is dropped.
    generation: u64,
    searching: bool,
    results: Vec<Title>,
    list: UniformListScrollHandle,
}

impl SearchView {
    pub fn new(core: Core, images: Entity<ImageCache>, cx: &mut Context<Self>) -> Self {
        cx.observe(&images, |_, _, cx| cx.notify()).detach();
        Self {
            core,
            images,
            query: SharedString::default(),
            generation: 0,
            searching: false,
            results: Vec::new(),
            list: UniformListScrollHandle::new(),
        }
    }

    /// The results so far, and whether a search is still running
    /// (`--shell-check`).
    pub fn results(&self) -> (&[Title], bool) {
        (&self.results, self.searching)
    }

    /// The field changed: search for `query` once typing pauses.
    pub fn set_query(&mut self, query: &str, cx: &mut Context<Self>) {
        let query = query.trim();
        if query == self.query.as_ref() {
            return;
        }
        self.generation += 1;
        self.query = SharedString::from(query.to_string());
        self.results.clear();
        self.searching = !query.is_empty();
        cx.notify();
        if query.is_empty() {
            return;
        }
        let generation = self.generation;
        let query = query.to_string();
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(PAUSE).await;
            let current = this.update(cx, |view, _| view.generation == generation);
            if !matches!(current, Ok(true)) {
                return;
            }
            let search = this.update(cx, |view, _| view.core.search(&query));
            let Ok(search) = search else { return };
            let results = search.await;
            let _ = this.update(cx, |view, cx| {
                if view.generation == generation {
                    view.results = results;
                    view.searching = false;
                    cx.notify();
                }
            });
        })
        .detach();
    }
}

impl Render for SearchView {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let heading = div()
            .px(theme::SPACE_6)
            .pt(theme::SPACE_6)
            .pb(theme::SPACE_2)
            .text_size(theme::FS_XL)
            .font_weight(gpui::FontWeight::BOLD)
            .child(SharedString::from(t!(
                "fiche.resultats_pour_x",
                self.query.as_ref()
            )));
        let body = if self.results.is_empty() {
            div()
                .px(theme::SPACE_6)
                .text_color(theme::text_3())
                .child(if self.searching {
                    t!("anime.chargement")
                } else {
                    t!("tabs.aucun_resultat")
                })
                .into_any_element()
        } else {
            let rows = self.results.len().div_ceil(COLUMNS);
            uniform_list(
                "search",
                rows,
                cx.processor(|view, range: Range<usize>, _, cx| {
                    title_rows(&view.results, range, &view.images, cx)
                }),
            )
            .track_scroll(self.list.clone())
            .flex_grow()
            .into_any_element()
        };
        div()
            .size_full()
            .flex()
            .flex_col()
            .child(heading)
            .child(body)
    }
}
