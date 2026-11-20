//! A Discover catalog (Films, Series, Anime): KINO's titles in a virtual grid,
//! by genre and in an order the user picks, posters through the image cache,
//! the next page asked for as the last rows come into view (phase G2).

use std::ops::Range;
use std::rc::Rc;

use gpui::{
    App, Context, Entity, EventEmitter, SharedString, UniformListScrollHandle, Window, div,
    prelude::*, px, uniform_list,
};

use crate::core::{Catalog, Core, Filter, Sort};
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

/// A genre chip: the catalog's value and its label (read when drawn, in the
/// interface's language).
pub struct Genre {
    pub value: &'static str,
    label: fn() -> &'static str,
}

const fn genre(value: &'static str, label: fn() -> &'static str) -> Genre {
    Genre { value, label }
}

/// The genre chips of `catalog`, as the web shows them; the first is "all".
pub fn genres(catalog: Catalog) -> &'static [Genre] {
    const MOVIES: &[Genre] = &[
        genre("", || t!("page.tous")),
        genre("Action", || t!("page.action")),
        genre("Sci-Fi", || t!("page.sci_fi")),
        genre("Thriller", || t!("page.thriller")),
        genre("Crime", || t!("page.policier")),
        genre("Adventure", || t!("page.aventure")),
        genre("Animation", || t!("page.animation")),
        genre("Comedy", || t!("page.comedie")),
        genre("Drama", || t!("page.drame")),
        genre("Fantasy", || t!("page.fantastique")),
        genre("Horror", || t!("page.horreur")),
        genre("War", || t!("page.guerre")),
        genre("Western", || t!("page.western")),
        genre("Documentary", || t!("page.documentaire")),
    ];
    const SERIES: &[Genre] = &[
        genre("", || t!("home.toutes")),
        genre("Drama", || t!("page.drame")),
        genre("Crime", || t!("page.policier")),
        genre("Thriller", || t!("page.thriller")),
        genre("Action", || t!("page.action")),
        genre("Sci-Fi", || t!("page.sci_fi")),
        genre("Comedy", || t!("page.comedie")),
        genre("Adventure", || t!("page.aventure")),
        genre("Fantasy", || t!("page.fantastique")),
        genre("Horror", || t!("page.horreur")),
        genre("Mystery", || t!("fiche.mystere")),
        genre("War", || t!("page.guerre")),
        genre("Western", || t!("page.western")),
        genre("Documentary", || t!("page.documentaire")),
    ];
    const ANIME: &[Genre] = &[
        genre("", || t!("page.tous")),
        genre("Action", || t!("home.action_combats")),
        genre("Fantasy", || t!("home.fantasy_magie")),
        genre("Isekai", || t!("home.isekai")),
        genre("Sci-Fi", || t!("home.sf_mecha")),
        genre("Slice of Life", || t!("anime.tranche_de_vie")),
        genre("Romance", || t!("fiche.romance")),
        genre("Comedy", || t!("page.comedie")),
        genre("Sport", || t!("anime.sport")),
        // The anime films ("Films d'Animation" on the web; the core takes both).
        genre("movie", || t!("home.films_anime")),
    ];
    match catalog {
        Catalog::Movies => MOVIES,
        Catalog::Series => SERIES,
        Catalog::Anime => ANIME,
    }
}

impl Sort {
    /// The sort menu's label, in the interface's language.
    pub fn label(self) -> &'static str {
        match self {
            Sort::Top => t!("page.tendances"),
            Sort::Rating => t!("page.mieux_notes"),
            Sort::Recent => t!("page.plus_recents"),
            Sort::Oldest => t!("page.plus_anciens"),
        }
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
    filter: Filter,
    /// Bumped when the filter changes: a page for an older one is dropped.
    generation: u64,
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
            filter: Filter::default(),
            generation: 0,
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

    /// The genre and order shown.
    pub fn filter(&self) -> Filter {
        self.filter
    }

    /// Whether a page is being asked for.
    pub fn loading(&self) -> bool {
        matches!(self.load, Load::Loading)
    }

    /// Shows `filter`'s titles instead, from the first page.
    pub fn set_filter(&mut self, filter: Filter, cx: &mut Context<Self>) {
        if filter == self.filter {
            return;
        }
        self.filter = filter;
        self.generation += 1;
        self.titles.clear();
        self.load = Load::Idle;
        self.list.scroll_to_item(0, gpui::ScrollStrategy::Top);
        self.load_more(cx);
        cx.notify();
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
        let generation = self.generation;
        let page = self
            .core
            .catalog(self.catalog, self.filter, self.titles.len() as u64);
        cx.spawn(async move |this, cx| {
            let page = page.await;
            let _ = this.update(cx, |discover, cx| {
                if discover.generation != generation {
                    return;
                }
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

impl Discover {
    /// The genre chips and the sort menu above the grid.
    fn filters(&self, cx: &mut Context<Self>) -> gpui::Div {
        let chip = |id: SharedString, label: &'static str, active: bool| {
            let chip = div()
                .id(id)
                .flex_none()
                .px(theme::SPACE_3)
                .py(theme::SPACE_1)
                .rounded_full()
                .border_1()
                .text_size(theme::FS_SM)
                .cursor_pointer()
                .child(label);
            if active {
                chip.bg(theme::text())
                    .border_color(theme::text())
                    .text_color(theme::on_light())
            } else {
                chip.border_color(theme::line_strong())
                    .text_color(theme::text_2())
                    .hover(|style| style.bg(theme::surface_2()).text_color(theme::text()))
            }
        };
        let filter = self.filter;
        let genres = genres(self.catalog).iter().map(|genre| {
            let value = genre.value;
            chip(
                SharedString::from(format!("genre-{value}")),
                (genre.label)(),
                value == filter.genre,
            )
            .on_click(cx.listener(move |discover, _, _, cx| {
                discover.set_filter(
                    Filter {
                        genre: value,
                        ..discover.filter
                    },
                    cx,
                )
            }))
        });
        let sorts = Sort::ALL.into_iter().map(|sort| {
            chip(
                SharedString::from(format!("sort-{}", sort.param())),
                sort.label(),
                sort == filter.sort,
            )
            .on_click(cx.listener(move |discover, _, _, cx| {
                discover.set_filter(
                    Filter {
                        sort,
                        ..discover.filter
                    },
                    cx,
                )
            }))
        });
        div()
            .flex_none()
            .px(theme::SPACE_6)
            .pt(theme::SPACE_4)
            .flex()
            .flex_col()
            .gap(theme::SPACE_3)
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(theme::SPACE_2)
                    .child(
                        div()
                            .text_size(theme::FS_SM)
                            .text_color(theme::text_3())
                            .child(t!("page.trier_le_catalogue")),
                    )
                    .children(sorts),
            )
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .gap(theme::SPACE_2)
                    .children(genres),
            )
    }
}

impl Render for Discover {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let filters = self.filters(cx);
        let body = if self.titles.is_empty() {
            let message = match &self.load {
                Load::Failed(error) => error.clone(),
                Load::Done => SharedString::from(t!("tabs.aucun_resultat")),
                _ => SharedString::from(t!("anime.chargement")),
            };
            div()
                .p(theme::SPACE_6)
                .text_color(theme::text_3())
                .child(message)
                .into_any_element()
        } else {
            let rows = self.titles.len().div_ceil(COLUMNS);
            uniform_list(
                "discover",
                rows,
                cx.processor(|discover, range: Range<usize>, _, cx| discover.rows(range, cx)),
            )
            .track_scroll(self.list.clone())
            .flex_grow()
            .into_any_element()
        };
        div()
            .size_full()
            .flex()
            .flex_col()
            .child(filters)
            .child(body)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_catalog_starts_with_all_and_names_each_genre_once() {
        for catalog in [Catalog::Movies, Catalog::Series, Catalog::Anime] {
            let values: Vec<_> = genres(catalog).iter().map(|g| g.value).collect();
            assert_eq!(values[0], "", "{catalog:?}");
            let unique: std::collections::HashSet<_> = values.iter().collect();
            assert_eq!(unique.len(), values.len(), "{catalog:?}");
            assert!(genres(catalog).iter().all(|g| !(g.label)().is_empty()));
        }
    }

    #[test]
    fn sorts_are_the_web_s_catalog_sorts() {
        let params: Vec<_> = Sort::ALL.iter().map(|s| s.param()).collect();
        assert_eq!(params, ["top", "imdbRating", "recent", "oldest"]);
        assert_eq!(Filter::default().sort, Sort::Top);
        assert!(Sort::ALL.iter().all(|s| !s.label().is_empty()));
    }
}
