//! A Discover catalog (Films, Series, Anime): KINO's titles in a virtual grid,
//! by genre and in an order the user picks, posters through the image cache,
//! the next page asked for as the last rows come into view (phase G2).

use std::ops::Range;
use std::rc::Rc;

use gpui::{
    App, Context, Entity, EventEmitter, ListAlignment, ListState, SharedString, Window, div, list,
    prelude::*, px,
};

use crate::core::{Catalog, Core, Filter, Sort};
use crate::home::Shelves;
use crate::image_cache::ImageCache;
use crate::title::Title;
use crate::{COLUMNS, ROW_H, poster_card, theme};

/// How many items before the end the next page is asked for.
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
    /// The user's rows above the catalog, once read.
    shelves: Shelves,
    titles: Vec<Title>,
    load: Load,
    /// The rows, then the filters, then the grid: one list of varied heights.
    list: ListState,
}

impl EventEmitter<OpenTitle> for Discover {}

/// What each item of the list is.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Item {
    Resume,
    List,
    Filters,
    /// The message shown while the grid is empty.
    Empty,
    Row(usize),
}

/// The list's shape: the user's rows above (if any), the filters, then the
/// grid's rows (or its message while empty).
#[derive(Clone, Copy, Debug)]
struct Layout {
    resume: bool,
    list: bool,
    titles: usize,
}

impl Layout {
    /// Where the grid starts in the list.
    fn grid_start(self) -> usize {
        usize::from(self.resume) + usize::from(self.list) + 1
    }

    fn count(self) -> usize {
        self.grid_start() + self.titles.div_ceil(COLUMNS).max(1)
    }

    fn item(self, index: usize) -> Option<Item> {
        let above = [
            self.resume.then_some(Item::Resume),
            self.list.then_some(Item::List),
            Some(Item::Filters),
        ];
        if let Some(item) = above.into_iter().flatten().nth(index) {
            return Some(item);
        }
        let row = index.checked_sub(self.grid_start())?;
        match self.titles.div_ceil(COLUMNS) {
            0 if row == 0 => Some(Item::Empty),
            rows if row < rows => Some(Item::Row(row)),
            _ => None,
        }
    }
}

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
            shelves: Shelves::default(),
            titles: Vec::new(),
            load: Load::Idle,
            list: ListState::new(2, ListAlignment::Top, px(ROW_H)),
        };
        this.load_shelves(cx);
        this.load_more(cx);
        this
    }

    /// The titles loaded so far (`--shell-check`).
    pub fn titles(&self) -> &[Title] {
        &self.titles
    }

    /// The user's rows above the catalog (`--shell-check`).
    pub fn shelves(&self) -> &Shelves {
        &self.shelves
    }

    /// The genre and order shown.
    pub fn filter(&self) -> Filter {
        self.filter
    }

    /// Whether a page is being asked for.
    pub fn loading(&self) -> bool {
        matches!(self.load, Load::Loading)
    }

    /// Why the catalog could not load, if it could not.
    pub fn error(&self) -> Option<&SharedString> {
        match &self.load {
            Load::Failed(error) => Some(error),
            _ => None,
        }
    }

    fn layout(&self) -> Layout {
        Layout {
            resume: !self.shelves.resume.is_empty(),
            list: !self.shelves.list.is_empty(),
            titles: self.titles.len(),
        }
    }

    /// The grid changed from its `kept` first titles on: those rows are
    /// measured again, the rows before keep their place.
    fn grid_changed(&mut self, kept: usize) {
        let layout = self.layout();
        let start = layout.grid_start() + kept / COLUMNS;
        let old = self.list.item_count();
        let new = layout.count();
        self.list
            .splice(start.min(old)..old, new.saturating_sub(start));
    }

    /// Re-reads the user's lists (a title watched or added elsewhere).
    pub fn load_shelves(&mut self, cx: &mut Context<Self>) {
        let (catalog, library) = (self.catalog, self.core.library());
        cx.spawn(async move |this, cx| {
            let (history, watchlist) = library.await;
            let shelves = Shelves::new(catalog, &history, &watchlist);
            let _ = this.update(cx, |discover, cx| {
                if discover.shelves != shelves {
                    discover.shelves = shelves;
                    discover.list.reset(discover.layout().count());
                    cx.notify();
                }
            });
        })
        .detach();
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
        self.grid_changed(0);
        self.load_more(cx);
        cx.notify();
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
                let kept = discover.titles.len();
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
                discover.grid_changed(kept);
                cx.notify();
            });
        })
        .detach();
    }

    fn item(&mut self, index: usize, cx: &mut Context<Self>) -> gpui::AnyElement {
        let layout = self.layout();
        if index + PREFETCH_ROWS >= layout.count() {
            self.load_more(cx);
        }
        let open = OpenTitle::emitter(cx.entity());
        match layout.item(index) {
            Some(Item::Resume) => {
                let cards: Vec<_> = self
                    .shelves
                    .resume
                    .iter()
                    .map(|r| card(&r.title, r.note(), &self.images, open.clone(), cx))
                    .collect();
                shelf("resume", t!("page.a_continuer"), cards)
            }
            Some(Item::List) => {
                let cards: Vec<_> = self
                    .shelves
                    .list
                    .iter()
                    .map(|t| card(t, t.subtitle(), &self.images, open.clone(), cx))
                    .collect();
                shelf("my-list", t!("page.ma_liste_2"), cards)
            }
            Some(Item::Filters) => self.filters(cx).into_any_element(),
            Some(Item::Empty) => {
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
            }
            Some(Item::Row(row)) => title_rows(&self.titles, row..row + 1, &self.images, open, cx)
                .pop()
                .unwrap_or_else(|| div().into_any_element()),
            None => div().into_any_element(),
        }
    }
}

/// A row of the user's titles under its heading, scrolled sideways.
fn shelf(
    id: &'static str,
    heading: &'static str,
    cards: Vec<gpui::Stateful<gpui::Div>>,
) -> gpui::AnyElement {
    div()
        .pt(theme::SPACE_4)
        .flex()
        .flex_col()
        .gap(theme::SPACE_3)
        .child(
            div()
                .px(theme::SPACE_6)
                .text_size(theme::FS_LG)
                .font_weight(gpui::FontWeight::BOLD)
                .child(heading),
        )
        .child(
            div()
                .id(id)
                .px(theme::SPACE_6)
                .flex()
                .gap_4()
                .overflow_x_scroll()
                .children(cards.into_iter().map(|c| c.flex_none())),
        )
        .into_any_element()
}

/// A title's card, its poster through the image cache; a click opens it.
fn card(
    title: &Title,
    subtitle: SharedString,
    images: &Entity<ImageCache>,
    open: Opener,
    cx: &mut App,
) -> gpui::Stateful<gpui::Div> {
    let image = title
        .poster
        .as_ref()
        .and_then(|url| images.update(cx, |images, cx| images.get(url, cx)));
    let clicked = title.clone();
    div()
        .id(SharedString::from(format!("card-{}", title.id)))
        .cursor_pointer()
        .on_click(move |_, _, cx| open(clicked.clone(), cx))
        .child(poster_card(title.name.clone(), subtitle, image))
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
                .map(|title| card(title, title.subtitle(), images, open.clone(), cx))
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
        list(
            self.list.clone(),
            cx.processor(|discover, index: usize, _, cx| discover.item(index, cx)),
        )
        .size_full()
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
    fn the_user_s_rows_come_first_then_the_filters_then_the_grid() {
        let layout = Layout {
            resume: true,
            list: false,
            titles: COLUMNS + 1,
        };
        let items: Vec<_> = (0..layout.count()).map(|i| layout.item(i)).collect();
        assert_eq!(
            items,
            [
                Some(Item::Resume),
                Some(Item::Filters),
                Some(Item::Row(0)),
                Some(Item::Row(1))
            ]
        );
        assert_eq!(layout.item(layout.count()), None);
    }

    #[test]
    fn an_empty_grid_shows_its_message() {
        let layout = Layout {
            resume: false,
            list: false,
            titles: 0,
        };
        assert_eq!(layout.count(), 2);
        assert_eq!(layout.item(1), Some(Item::Empty));
        let layout = Layout {
            resume: true,
            list: true,
            titles: 0,
        };
        assert_eq!(layout.item(2), Some(Item::Filters));
        assert_eq!(layout.item(3), Some(Item::Empty));
    }

    #[test]
    fn sorts_are_the_web_s_catalog_sorts() {
        let params: Vec<_> = Sort::ALL.iter().map(|s| s.param()).collect();
        assert_eq!(params, ["top", "imdbRating", "recent", "oldest"]);
        assert_eq!(Filter::default().sort, Sort::Top);
        assert!(Sort::ALL.iter().all(|s| !s.label().is_empty()));
    }
}
