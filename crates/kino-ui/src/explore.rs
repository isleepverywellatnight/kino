//! Explorer (phase G2): the user's imported Letterboxd lists, then KINO's own
//! selections, each as a row of posters with how many titles it holds, how
//! many were seen, who made it and why; « Tout voir » opens one in full.

use std::ops::Range;

use gpui::{
    Context, Entity, EventEmitter, SharedString, UniformListScrollHandle, Window, div, prelude::*,
    px, uniform_list,
};
use serde_json::Value;

use crate::core::Core;
use crate::discover::{OpenTitle, card, title_rows};
use crate::home::{SHELF_SIZE, is_watched};
use crate::image_cache::ImageCache;
use crate::theme;
use crate::title::Title;

/// A list of titles someone chose.
#[derive(Clone, Debug, PartialEq)]
pub struct Selection {
    pub id: SharedString,
    pub name: SharedString,
    /// Who made it; `None` for the user's own lists.
    pub author: Option<SharedString>,
    pub description: Option<SharedString>,
    /// Imported by the user from Letterboxd.
    pub imported: bool,
    pub titles: Vec<Title>,
    /// How many titles it holds, and how many of them were seen.
    pub total: usize,
    pub watched: usize,
}

impl Selection {
    /// A list as `kino::letterboxd` gives it (`imported`: one of the user's).
    pub fn new(list: &Value, imported: bool, history: &[Value]) -> Option<Self> {
        let text = |key: &str| {
            list[key]
                .as_str()
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(|s| SharedString::from(s.to_string()))
        };
        let items: &[Value] = list["items"].as_array().map_or(&[], Vec::as_slice);
        Some(Self {
            id: text("id")?,
            name: text("title").unwrap_or_else(|| {
                if imported {
                    t!("social.ma_liste_letterboxd").into()
                } else {
                    SharedString::default()
                }
            }),
            author: (!imported).then(|| text("author")).flatten(),
            description: text("description"),
            imported,
            titles: items.iter().filter_map(Title::from_meta).collect(),
            total: items.len(),
            watched: items.iter().filter(|m| is_watched(m, history)).count(),
        })
    }

    /// "20 titres · 3 vus · par Communauté Letterboxd".
    pub fn meta(&self) -> SharedString {
        let plural = |n: usize| if n > 1 { "s" } else { "" };
        let mut parts = vec![
            t!("social.x_titre_x", self.total, plural(self.total)),
            t!("settings.x_vu_x", self.watched, plural(self.watched)),
        ];
        if let Some(author) = &self.author {
            parts.push(t!("social.par_x_2", author.as_ref()));
        }
        parts.join(" · ").into()
    }
}

/// The user's lists first, then KINO's selections.
pub fn selections(imported: &[Value], curated: &[Value], history: &[Value]) -> Vec<Selection> {
    imported
        .iter()
        .filter_map(|l| Selection::new(l, true, history))
        .chain(
            curated
                .iter()
                .filter_map(|l| Selection::new(l, false, history)),
        )
        .collect()
}

enum State {
    Loading,
    Ready(Vec<Selection>),
}

pub struct Explore {
    core: Core,
    images: Entity<ImageCache>,
    state: State,
    /// The selection shown in full, by index.
    open: Option<usize>,
    list: UniformListScrollHandle,
}

impl EventEmitter<OpenTitle> for Explore {}

impl Explore {
    pub fn new(core: Core, images: Entity<ImageCache>, cx: &mut Context<Self>) -> Self {
        cx.observe(&images, |_, _, cx| cx.notify()).detach();
        Self {
            core,
            images,
            state: State::Loading,
            open: None,
            list: UniformListScrollHandle::new(),
        }
    }

    /// The selections, once read (`--shell-check`).
    pub fn selections(&self) -> Option<&[Selection]> {
        match &self.state {
            State::Loading => None,
            State::Ready(selections) => Some(selections),
        }
    }

    /// Reads the lists again (the screen opened: titles may have been seen).
    pub fn refresh(&mut self, cx: &mut Context<Self>) {
        let lists = self.core.explore();
        cx.spawn(async move |this, cx| {
            let (imported, curated, history) = lists.await;
            let found = selections(&imported, &curated, &history);
            let _ = this.update(cx, |explore, cx| {
                if explore.open.is_some_and(|i| i >= found.len()) {
                    explore.open = None;
                }
                explore.state = State::Ready(found);
                cx.notify();
            });
        })
        .detach();
    }

    /// Back from a selection to the shelves; `false` when none was open.
    pub fn close(&mut self, cx: &mut Context<Self>) -> bool {
        let was_open = self.open.take().is_some();
        if was_open {
            cx.notify();
        }
        was_open
    }

    fn shelf(
        &self,
        index: usize,
        selection: &Selection,
        cx: &mut Context<Self>,
    ) -> gpui::AnyElement {
        let open = OpenTitle::emitter(cx.entity());
        let cards: Vec<_> = selection
            .titles
            .iter()
            .take(SHELF_SIZE)
            .map(|t| card(t, t.subtitle(), &self.images, open.clone(), cx).flex_none())
            .collect();
        let mut info = div()
            .flex()
            .flex_col()
            .gap(theme::SPACE_1)
            .child(
                div()
                    .text_size(theme::FS_LG)
                    .font_weight(gpui::FontWeight::BOLD)
                    .child(selection.name.clone()),
            )
            .child(
                div()
                    .text_size(theme::FS_SM)
                    .text_color(theme::text_3())
                    .child(selection.meta()),
            );
        if let Some(description) = &selection.description {
            info = info.child(
                div()
                    .max_w(px(720.0))
                    .text_size(theme::FS_SM)
                    .text_color(theme::text_2())
                    .child(description.clone()),
            );
        }
        let all = div()
            .id(SharedString::from(format!("explore-all-{}", selection.id)))
            .flex_none()
            .px(theme::SPACE_3)
            .py(theme::SPACE_1)
            .rounded_full()
            .border_1()
            .border_color(theme::line_strong())
            .text_size(theme::FS_SM)
            .cursor_pointer()
            .hover(|style| style.bg(theme::surface_2()))
            .child(t!("letterboxd.tout_voir"))
            .on_click(cx.listener(move |explore, _, _, cx| {
                explore.open = Some(index);
                cx.notify();
            }));
        let row = if cards.is_empty() {
            div()
                .text_size(theme::FS_SM)
                .text_color(theme::text_3())
                .child(t!("social.cette_selection_est_vide"))
                .into_any_element()
        } else {
            div()
                .id(SharedString::from(format!("explore-row-{}", selection.id)))
                .flex()
                .gap_4()
                .overflow_x_scroll()
                .children(cards)
                .into_any_element()
        };
        div()
            .flex()
            .flex_col()
            .gap(theme::SPACE_3)
            .child(
                div()
                    .flex()
                    .items_start()
                    .justify_between()
                    .gap(theme::SPACE_4)
                    .child(info)
                    .child(all),
            )
            .child(row)
            .into_any_element()
    }

    /// One selection in full, as a grid.
    fn full(&self, selection: &Selection, cx: &mut Context<Self>) -> gpui::AnyElement {
        let back = div()
            .id("explore-back")
            .px(theme::SPACE_3)
            .py(theme::SPACE_1)
            .rounded_full()
            .border_1()
            .border_color(theme::line_strong())
            .text_size(theme::FS_SM)
            .cursor_pointer()
            .hover(|style| style.bg(theme::surface_2()))
            .child(t!("page.explorer_2"))
            .on_click(cx.listener(|explore, _, _, cx| {
                explore.close(cx);
            }));
        let titles = selection.titles.clone();
        let rows = titles.len().div_ceil(crate::COLUMNS);
        div()
            .size_full()
            .flex()
            .flex_col()
            .child(
                div()
                    .px(theme::SPACE_6)
                    .pt(theme::SPACE_6)
                    .flex()
                    .flex_col()
                    .gap(theme::SPACE_2)
                    .child(div().flex().child(back))
                    .child(
                        div()
                            .text_size(theme::FS_XL)
                            .font_weight(gpui::FontWeight::BOLD)
                            .child(selection.name.clone()),
                    )
                    .child(
                        div()
                            .text_size(theme::FS_SM)
                            .text_color(theme::text_3())
                            .child(selection.meta()),
                    ),
            )
            .child(
                uniform_list(
                    "explore-grid",
                    rows,
                    cx.processor(move |explore, range: Range<usize>, _, cx| {
                        let open = OpenTitle::emitter(cx.entity());
                        title_rows(&titles, range, &explore.images, open, cx)
                    }),
                )
                .track_scroll(self.list.clone())
                .flex_grow(),
            )
            .into_any_element()
    }
}

impl Render for Explore {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let selections = match &self.state {
            State::Loading => {
                return div()
                    .p(theme::SPACE_6)
                    .text_color(theme::text_3())
                    .child(t!("social.chargement_des_selections"))
                    .into_any_element();
            }
            State::Ready(selections) => selections.clone(),
        };
        if let Some(selection) = self.open.and_then(|i| selections.get(i)) {
            return self.full(selection, cx);
        }
        let heading = |text: &'static str| {
            div()
                .pt(theme::SPACE_2)
                .text_size(theme::FS_XL)
                .font_weight(gpui::FontWeight::BOLD)
                .child(text)
        };
        let mut page = div()
            .id("explore")
            .size_full()
            .overflow_y_scroll()
            .p(theme::SPACE_6)
            .flex()
            .flex_col()
            .gap(theme::SPACE_6)
            .child(
                div()
                    .flex()
                    .flex_col()
                    .gap(theme::SPACE_2)
                    .child(
                        div()
                            .text_size(px(28.0))
                            .font_weight(gpui::FontWeight::BOLD)
                            .child(t!("page.explorer")),
                    )
                    .child(
                        div()
                            .text_size(theme::FS_SM)
                            .text_color(theme::text_2())
                            .child(t!("page.collections_cultes_palmares_letterboxd")),
                    ),
            );
        if selections.is_empty() {
            return page
                .child(
                    div()
                        .text_color(theme::text_3())
                        .child(t!("social.aucune_selection_disponible")),
                )
                .into_any_element();
        }
        let imported = selections.iter().any(|s| s.imported);
        let mut curated_heading = true;
        if imported {
            page = page.child(heading(t!("social.vos_listes_letterboxd")));
        }
        for (index, selection) in selections.iter().enumerate() {
            if !selection.imported && curated_heading {
                curated_heading = false;
                page = page.child(heading(t!("social.selections")));
            }
            page = page.child(self.shelf(index, selection, cx));
        }
        page.into_any_element()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn curated() -> Value {
        json!({"id": "top", "title": "Top 250", "author": "Letterboxd", "description": "The best.",
               "items": [{"id": "tt1", "name": "A", "type": "movie"},
                         {"id": "tt2", "name": "B", "type": "movie"},
                         {"name": "No id"}]})
    }

    #[test]
    fn a_selection_counts_what_was_seen() {
        let history = vec![json!({"id": "tt1", "type": "movie", "completed": true})];
        let selection = Selection::new(&curated(), false, &history).unwrap();
        assert_eq!((selection.total, selection.watched), (3, 1));
        assert_eq!(selection.titles.len(), 2);
        assert_eq!(
            selection.author.as_ref().map(|a| a.as_ref()),
            Some("Letterboxd")
        );
        assert!(selection.meta().contains("Letterboxd"));
    }

    #[test]
    fn the_user_s_lists_come_first_without_an_author() {
        let mine = json!({"id": "lb1", "author": "someone", "items": []});
        let found = selections(&[mine], &[curated()], &[]);
        assert_eq!(found.len(), 2);
        assert!(found[0].imported && found[0].author.is_none());
        assert!(!found[0].name.is_empty());
        assert!(!found[1].imported);
    }

    #[test]
    fn kino_s_own_selections_are_read() {
        let curated = kino::letterboxd::curated();
        let found = selections(&[], curated.as_array().unwrap(), &[]);
        assert!(found.len() >= 6);
        assert!(found.iter().all(|s| !s.titles.is_empty()));
    }
}
