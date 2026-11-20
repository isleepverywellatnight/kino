//! A title's page (phase G2, opened from any grid): its backdrop, poster,
//! year, rating, runtime, genres, synopsis, director and cast, from KINO's
//! metadata. Seasons, sources and playing come with G3.

use std::ops::Range;

use gpui::{
    Context, Entity, EventEmitter, SharedString, UniformListScrollHandle, Window, div, img,
    prelude::*, px, uniform_list,
};

use crate::core::Core;
use crate::discover::{OpenTitle, title_rows};
use crate::i18n::{self, Language};
use crate::image_cache::ImageCache;
use crate::title::{Details, Title};
use crate::{POSTER_H, POSTER_W, theme};

enum State {
    Loading,
    Ready(Box<Details>),
    Failed(SharedString),
}

pub struct TitlePage {
    title: Title,
    images: Entity<ImageCache>,
    state: State,
    list: UniformListScrollHandle,
}

impl EventEmitter<OpenTitle> for TitlePage {}

impl TitlePage {
    pub fn new(
        core: &Core,
        images: Entity<ImageCache>,
        title: Title,
        cx: &mut Context<Self>,
    ) -> Self {
        cx.observe(&images, |_, _, cx| cx.notify()).detach();
        let details = core.details(&title, i18n::language() == Language::French);
        cx.spawn(async move |this, cx| {
            let details = details.await;
            let _ = this.update(cx, |page, cx| {
                page.state = match details {
                    Ok(details) => State::Ready(Box::new(details)),
                    Err(error) => State::Failed(error.into()),
                };
                cx.notify();
            });
        })
        .detach();
        Self {
            title,
            images,
            state: State::Loading,
            list: UniformListScrollHandle::new(),
        }
    }

    /// The page as loaded so far (`--shell-check`): `None` while loading.
    pub fn details(&self) -> Option<Result<&Details, &SharedString>> {
        match &self.state {
            State::Loading => None,
            State::Ready(details) => Some(Ok(details)),
            State::Failed(error) => Some(Err(error)),
        }
    }

    fn image(
        &self,
        url: Option<&SharedString>,
        cx: &mut Context<Self>,
    ) -> Option<std::sync::Arc<gpui::RenderImage>> {
        url.and_then(|url| self.images.update(cx, |images, cx| images.get(url, cx)))
    }
}

fn line(label: &'static str, names: &[SharedString]) -> Option<gpui::Div> {
    (!names.is_empty()).then(|| {
        div()
            .flex()
            .gap(theme::SPACE_2)
            .text_size(theme::FS_SM)
            .child(div().text_color(theme::text_3()).child(label))
            .child(
                div()
                    .text_color(theme::text_2())
                    .child(SharedString::from(names.join(", "))),
            )
    })
}

impl Render for TitlePage {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let details = match &self.state {
            State::Ready(details) => Some(details.as_ref().clone()),
            _ => None,
        };
        let title = details.as_ref().map_or(&self.title, |d| &d.title).clone();
        let poster = self.image(title.poster.as_ref(), cx);
        let backdrop = self.image(details.as_ref().and_then(|d| d.background.as_ref()), cx);

        let mut facts = vec![title.subtitle()];
        if let Some(runtime) = details.as_ref().and_then(|d| d.runtime.clone()) {
            facts.push(runtime);
        }
        let mut info = div()
            .flex()
            .flex_col()
            .gap(theme::SPACE_3)
            .max_w(px(720.0))
            .child(
                div()
                    .text_size(px(32.0))
                    .font_weight(gpui::FontWeight::BOLD)
                    .child(title.name.clone()),
            )
            .child(
                div()
                    .text_size(theme::FS_MD)
                    .text_color(theme::text_2())
                    .child(SharedString::from(
                        facts
                            .iter()
                            .filter(|f| !f.is_empty())
                            .map(|f| f.as_ref())
                            .collect::<Vec<_>>()
                            .join(" • "),
                    )),
            );
        match (&self.state, &details) {
            (_, Some(details)) => {
                if !details.genres.is_empty() {
                    info = info.child(div().flex().flex_wrap().gap(theme::SPACE_2).children(
                        details.genres.iter().map(|g| {
                            div()
                                .px(theme::SPACE_3)
                                .py(theme::SPACE_1)
                                .rounded_full()
                                .border_1()
                                .border_color(theme::line_strong())
                                .text_size(theme::FS_SM)
                                .text_color(theme::text_2())
                                .child(g.clone())
                        }),
                    ));
                }
                if let Some(description) = &details.description {
                    info = info.child(
                        div()
                            .text_size(theme::FS_BASE)
                            .text_color(theme::text())
                            .line_height(px(24.0))
                            .child(description.clone()),
                    );
                }
                info = info
                    .children(line(t!("fiche.realisation"), &details.director))
                    .children(line(t!("fiche.distribution"), &details.cast));
            }
            (State::Failed(error), _) => {
                info = info.child(div().text_color(theme::text_3()).child(error.clone()));
            }
            _ => {
                info = info.child(
                    div()
                        .text_color(theme::text_3())
                        .child(t!("anime.chargement")),
                );
            }
        }

        let art = div()
            .w(px(POSTER_W * 1.4))
            .h(px(POSTER_H * 1.4))
            .flex_none()
            .rounded(theme::RADIUS)
            .overflow_hidden()
            .bg(theme::surface_2());
        let art = match poster {
            Some(poster) => art.child(img(poster).size_full()),
            None => art,
        };
        let hero = div()
            .relative()
            .w_full()
            .h(px(POSTER_H * 1.4 + 96.0))
            .overflow_hidden();
        let hero = match backdrop {
            Some(backdrop) => hero.child(
                img(backdrop)
                    .absolute()
                    .size_full()
                    .object_fit(gpui::ObjectFit::Cover)
                    .opacity(0.35),
            ),
            None => hero,
        };
        let hero = hero.child(
            div()
                .absolute()
                .size_full()
                .p(theme::SPACE_6)
                .flex()
                .gap(theme::SPACE_6)
                .child(art)
                .child(info),
        );

        let similar = details.map(|d| d.similar).unwrap_or_default();
        let mut page = div().size_full().flex().flex_col().child(hero);
        if !similar.is_empty() {
            let rows = similar.len().div_ceil(crate::COLUMNS);
            page = page
                .child(
                    div()
                        .px(theme::SPACE_6)
                        .pt(theme::SPACE_4)
                        .text_size(theme::FS_LG)
                        .font_weight(gpui::FontWeight::BOLD)
                        .child(t!("fiche.vous_aimerez_aussi")),
                )
                .child(
                    uniform_list(
                        "similar",
                        rows,
                        cx.processor(move |page, range: Range<usize>, _, cx| {
                            let open = OpenTitle::emitter(cx.entity());
                            title_rows(&similar, range, &page.images, open, cx)
                        }),
                    )
                    .track_scroll(self.list.clone())
                    .flex_grow(),
                );
        }
        page
    }
}
