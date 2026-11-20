//! A Discover catalog (Films, Series): KINO's best titles in a virtual grid,
//! posters through the image cache, the next page asked for as the last rows
//! come into view (phase G2).

use std::ops::Range;
use std::rc::Rc;

use gpui::{
    App, Context, Entity, EventEmitter, SharedString, UniformListScrollHandle, Window, div,
    prelude::*, px, uniform_list,
};

use crate::core::{Catalog, Core};
use crate::image_cache::ImageCache;
use crate::title::Title;
use crate::{COLUMNS, ROW_H, poster_card, theme};

/// How many rows before the end the next page is asked for.
const PREFETCH_ROWS: usize = 2;

/// A card was clicked: its title's page should open.
#[derive(Clone, Debug)]
pub struct OpenTitle(pub Title);

/// What a card does when clicked: send `OpenTitle` from a view.
pub type Opener = Rc<dyn Fn(Title, &mut App)>;

impl OpenTitle {
    /// An `Opener` that makes `view` emit `OpenTitle`.
    pub fn emitter<T: EventEmitter<OpenTitle>>(view: Entity<T>) -> Opener {
        Rc::new(move |title, cx| view.update(cx, |_, cx| cx.emit(OpenTitle(title))))
    }
}

enum Load {
    Idle,
    Loading,
    /// Nothing more to show (an empty page).
    Done,
    Failed(SharedString),
}

pub struct Discover {
    catalog: Catalog,
    core: Core,
    images: Entity<ImageCache>,
    titles: Vec<Title>,
    load: Load,
    list: UniformListScrollHandle,
}

impl EventEmitter<OpenTitle> for Discover {}

impl Discover {
    pub fn new(
        catalog: Catalog,
        core: Core,
        images: Entity<ImageCache>,
        cx: &mut Context<Self>,
    ) -> Self {
        cx.observe(&images, |_, _, cx| cx.notify()).detach();
        let mut this = Self {
            catalog,
            core,
            images,
            titles: Vec::new(),
            load: Load::Idle,
            list: UniformListScrollHandle::new(),
        };
        this.load_more(cx);
        this
    }

    /// The titles loaded so far (`--shell-check`).
    pub fn titles(&self) -> &[Title] {
        &self.titles
    }

    /// Why the catalog could not load, if it could not.
    pub fn error(&self) -> Option<&SharedString> {
        match &self.load {
            Load::Failed(error) => Some(error),
            _ => None,
        }
    }

    fn load_more(&mut self, cx: &mut Context<Self>) {
        if !matches!(self.load, Load::Idle) {
            return;
        }
        self.load = Load::Loading;
        let page = self.core.catalog(self.catalog, self.titles.len() as u64);
        cx.spawn(async move |this, cx| {
            let page = page.await;
            let _ = this.update(cx, |discover, cx| {
                match page {
                    Ok(page) if page.is_empty() => discover.load = Load::Done,
                    Ok(page) => {
                        // A page can repeat the end of the last one.
                        let known: std::collections::HashSet<SharedString> =
                            discover.titles.iter().map(|t| t.id.clone()).collect();
                        discover
                            .titles
                            .extend(page.into_iter().filter(|t| !known.contains(&t.id)));
                        discover.load = Load::Idle;
                    }
                    Err(error) => discover.load = Load::Failed(error.into()),
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn rows(&mut self, range: Range<usize>, cx: &mut Context<Self>) -> Vec<gpui::AnyElement> {
        let rows = self.titles.len().div_ceil(COLUMNS);
        if range.end + PREFETCH_ROWS >= rows {
            self.load_more(cx);
        }
        let open = OpenTitle::emitter(cx.entity());
        title_rows(&self.titles, range, &self.images, open, cx)
    }
}

/// Rows of title cards, posters through the image cache: the grid of every
/// screen that lists titles (catalogs, search).
pub fn title_rows(
    titles: &[Title],
    range: Range<usize>,
    images: &Entity<ImageCache>,
    open: Opener,
    cx: &mut gpui::App,
) -> Vec<gpui::AnyElement> {
    range
        .map(|row| {
            let start = row * COLUMNS;
            let end = (start + COLUMNS).min(titles.len());
            let cards: Vec<_> = titles[start..end]
                .iter()
                .map(|title| {
                    let image = title
                        .poster
                        .as_ref()
                        .and_then(|url| images.update(cx, |images, cx| images.get(url, cx)));
                    let (open, clicked) = (open.clone(), title.clone());
                    div()
                        .id(SharedString::from(format!("card-{}", title.id)))
                        .cursor_pointer()
                        .on_click(move |_, _, cx| open(clicked.clone(), cx))
                        .child(poster_card(title.name.clone(), title.subtitle(), image))
                })
                .collect();
            div()
                .id(row)
                .h(px(ROW_H))
                .px_6()
                .pt_4()
                .flex()
                .gap_4()
                .children(cards)
                .into_any_element()
        })
        .collect()
}

impl Render for Discover {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        if self.titles.is_empty() {
            let message = match &self.load {
                Load::Failed(error) => error.clone(),
                _ => SharedString::from(t!("anime.chargement")),
            };
            return div()
                .size_full()
                .p(theme::SPACE_6)
                .text_color(theme::text_3())
                .child(message)
                .into_any_element();
        }
        let rows = self.titles.len().div_ceil(COLUMNS);
        uniform_list(
            "discover",
            rows,
            cx.processor(|discover, range: Range<usize>, _, cx| discover.rows(range, cx)),
        )
        .track_scroll(self.list.clone())
        .size_full()
        .into_any_element()
    }
}
