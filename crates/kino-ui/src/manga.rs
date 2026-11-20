//! Manga (phase G5): the web's rows (trending, most popular, manhwa and
//! webtoons), a series' page with its chapters, and the reader (a chapter's
//! pages one under the other, the next chapter at the end), through KINO's
//! manga routes (`/api/manga/*`: AniList, MangaDex, WeebCentral).

use gpui::{Context, Entity, SharedString, Window, div, img, prelude::*, px};
use serde_json::Value;

use crate::core::Core;
use crate::image_cache::ImageCache;
use crate::{POSTER_H, POSTER_W, theme};

/// The rows of the manga home: (route name, label).
const ROWS: [&str; 3] = ["trending", "popular", "webtoon"];

fn row_label(name: &str) -> &'static str {
    match name {
        "trending" => t!("page.tendances"),
        "popular" => t!("manga.les_plus_populaires"),
        _ => t!("manga.manhwa_et_webtoons"),
    }
}

/// A manga series as KINO's manga routes list it.
#[derive(Clone, Debug, PartialEq)]
pub struct Series {
    pub id: SharedString,
    pub title: SharedString,
    pub cover: Option<SharedString>,
    pub year: Option<SharedString>,
}

impl Series {
    pub fn new(value: &Value) -> Option<Self> {
        let text = |key: &str| match &value[key] {
            Value::String(s) if !s.trim().is_empty() => {
                Some(SharedString::from(s.trim().to_string()))
            }
            Value::Number(n) => Some(SharedString::from(n.to_string())),
            _ => None,
        };
        Some(Self {
            id: text("id")?,
            title: text("title")?,
            cover: text("cover").filter(|c| c.starts_with("https://")),
            year: text("year"),
        })
    }
}

/// One chapter of a series.
#[derive(Clone, Debug, PartialEq)]
pub struct Chapter {
    pub id: SharedString,
    /// "Chapitre 12" or the chapter's own label.
    pub label: SharedString,
    pub date: SharedString,
}

impl Chapter {
    pub fn new(value: &Value) -> Option<Self> {
        let id = value["id"].as_str().filter(|s| !s.is_empty())?;
        let number = value["number"].as_str().unwrap_or_default();
        let label = value["label"]
            .as_str()
            .or_else(|| value["title"].as_str())
            .filter(|l| !l.trim().is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| {
                if number.is_empty() {
                    t!("manga.chapitre_unique").to_string()
                } else {
                    t!("manga.chapitre_x", number)
                }
            });
        Some(Self {
            id: id.to_string().into(),
            label: label.into(),
            date: value["date"]
                .as_str()
                .unwrap_or_default()
                .to_string()
                .into(),
        })
    }
}

fn list<T>(value: &Value, key: &str, parse: fn(&Value) -> Option<T>) -> Vec<T> {
    value[key]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(parse)
        .collect()
}

/// Where the screen is.
enum View {
    Home,
    /// A series' page: its description and chapters, once read.
    Series {
        series: Series,
        description: Option<SharedString>,
        chapters: Option<Result<Vec<Chapter>, SharedString>>,
    },
    /// A chapter being read: its pages, once read.
    Reader {
        series: Series,
        chapters: Vec<Chapter>,
        index: usize,
        pages: Option<Result<Vec<SharedString>, SharedString>>,
    },
}

pub struct Manga {
    core: Core,
    images: Entity<ImageCache>,
    rows: Vec<(&'static str, Option<Vec<Series>>)>,
    view: View,
    loaded: bool,
}

/// `/api/manga/...`'s error, or its JSON.
fn answer(status: u16, value: Value) -> Result<Value, SharedString> {
    if (200..300).contains(&status) {
        Ok(value)
    } else {
        Err(value["error"]
            .as_str()
            .map_or_else(|| format!("HTTP {status}"), str::to_string)
            .into())
    }
}

impl Manga {
    pub fn new(core: Core, images: Entity<ImageCache>, cx: &mut Context<Self>) -> Self {
        cx.observe(&images, |_, _, cx| cx.notify()).detach();
        Self {
            core,
            images,
            rows: ROWS.iter().map(|name| (*name, None)).collect(),
            view: View::Home,
            loaded: false,
        }
    }

    /// The rows' series counts, once read (`--shell-check`).
    pub fn row_counts(&self) -> Option<Vec<usize>> {
        self.rows
            .iter()
            .map(|(_, series)| series.as_ref().map(Vec::len))
            .collect()
    }

    /// Reads the rows, once (the screen opened).
    pub fn open(&mut self, cx: &mut Context<Self>) {
        if self.loaded {
            return;
        }
        self.loaded = true;
        for name in ROWS {
            let found = self
                .core
                .api("GET", format!("/api/manga/row?name={name}"), None);
            cx.spawn(async move |this, cx| {
                let (status, value) = found.await;
                let series = answer(status, value)
                    .map(|v| list(&v, "series", Series::new))
                    .unwrap_or_default();
                let _ = this.update(cx, |manga, cx| {
                    if let Some(row) = manga.rows.iter_mut().find(|(n, _)| *n == name) {
                        row.1 = Some(series);
                    }
                    cx.notify();
                });
            })
            .detach();
        }
    }

    /// Back from the reader to the series, from the series to the rows;
    /// `false` at the rows.
    pub fn back(&mut self, cx: &mut Context<Self>) -> bool {
        let view = std::mem::replace(&mut self.view, View::Home);
        self.view = match view {
            View::Home => return false,
            View::Series { .. } => View::Home,
            View::Reader {
                series, chapters, ..
            } => View::Series {
                series,
                description: None,
                chapters: Some(Ok(chapters)),
            },
        };
        cx.notify();
        true
    }

    fn open_series(&mut self, series: Series, cx: &mut Context<Self>) {
        let id = series.id.clone();
        self.view = View::Series {
            series,
            description: None,
            chapters: None,
        };
        cx.notify();
        let lang = if crate::i18n::language() == crate::i18n::Language::French {
            "fr"
        } else {
            "en"
        };
        let details = self.core.api(
            "GET",
            format!("/api/manga/details?lang={lang}&id={id}"),
            None,
        );
        let chapters = self.core.api(
            "GET",
            format!("/api/manga/chapters?lang={lang}&id={id}"),
            None,
        );
        cx.spawn(async move |this, cx| {
            let (details, chapters) = (details.await, chapters.await);
            let description = answer(details.0, details.1)
                .ok()
                .and_then(|d| d["description"].as_str().map(str::to_string))
                .filter(|d| !d.trim().is_empty())
                .map(SharedString::from);
            let chapters =
                answer(chapters.0, chapters.1).map(|v| list(&v, "chapters", Chapter::new));
            let _ = this.update(cx, |manga, cx| {
                if let View::Series {
                    series,
                    description: d,
                    chapters: c,
                } = &mut manga.view
                    && series.id == id
                {
                    *d = description;
                    *c = Some(chapters);
                    cx.notify();
                }
            });
        })
        .detach();
    }

    fn read(
        &mut self,
        series: Series,
        chapters: Vec<Chapter>,
        index: usize,
        cx: &mut Context<Self>,
    ) {
        let Some(chapter) = chapters.get(index) else {
            return;
        };
        let id = chapter.id.clone();
        self.view = View::Reader {
            series,
            chapters,
            index,
            pages: None,
        };
        cx.notify();
        let pages = self
            .core
            .api("GET", format!("/api/manga/pages?chapter={id}"), None);
        cx.spawn(async move |this, cx| {
            let (status, value) = pages.await;
            let pages = answer(status, value).map(|v| {
                v["pages"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .filter(|p| p.starts_with("https://"))
                    .map(|p| SharedString::from(p.to_string()))
                    .collect()
            });
            let _ = this.update(cx, |manga, cx| {
                if let View::Reader {
                    chapters,
                    index,
                    pages: p,
                    ..
                } = &mut manga.view
                    && chapters.get(*index).is_some_and(|c| c.id == id)
                {
                    *p = Some(pages);
                    cx.notify();
                }
            });
        })
        .detach();
    }

    fn cover(&self, series: &Series, scale: f32, cx: &mut Context<Self>) -> gpui::Div {
        let image = series
            .cover
            .as_ref()
            .and_then(|url| self.images.update(cx, |images, cx| images.get(url, cx)));
        let art = div()
            .w(px(POSTER_W * scale))
            .h(px(POSTER_H * scale))
            .flex_none()
            .rounded(theme::RADIUS_SM)
            .overflow_hidden()
            .bg(theme::surface_2());
        match image {
            Some(image) => art.child(img(image).size_full()),
            None => art,
        }
    }

    fn home(&self, cx: &mut Context<Self>) -> gpui::AnyElement {
        let mut page = div()
            .id("manga-home")
            .size_full()
            .overflow_y_scroll()
            .p(theme::SPACE_6)
            .flex()
            .flex_col()
            .gap(theme::SPACE_5)
            .child(
                div()
                    .text_size(px(28.0))
                    .font_weight(gpui::FontWeight::BOLD)
                    .child(t!("page.manga")),
            );
        for (name, series) in &self.rows {
            let body = match series {
                None => div()
                    .text_color(theme::text_3())
                    .child(t!("anime.chargement"))
                    .into_any_element(),
                Some(series) if series.is_empty() => div()
                    .text_color(theme::text_3())
                    .child(t!("manga.aucune_serie_pour_l_instant"))
                    .into_any_element(),
                Some(series) => {
                    let cards: Vec<_> = series
                        .iter()
                        .map(|s| {
                            let open = s.clone();
                            div()
                                .id(SharedString::from(format!("manga-{name}-{}", s.id)))
                                .flex_none()
                                .w(px(POSTER_W))
                                .flex()
                                .flex_col()
                                .gap_2()
                                .cursor_pointer()
                                .child(self.cover(s, 1.0, cx))
                                .child(div().text_sm().truncate().child(s.title.clone()))
                                .on_click(cx.listener(move |manga, _, _, cx| {
                                    manga.open_series(open.clone(), cx)
                                }))
                        })
                        .collect();
                    div()
                        .id(SharedString::from(format!("manga-row-{name}")))
                        .flex()
                        .gap_4()
                        .overflow_x_scroll()
                        .children(cards)
                        .into_any_element()
                }
            };
            page = page
                .child(
                    div()
                        .text_size(theme::FS_LG)
                        .font_weight(gpui::FontWeight::BOLD)
                        .child(row_label(name)),
                )
                .child(body);
        }
        page.into_any_element()
    }

    fn back_button(label: &'static str, cx: &mut Context<Self>) -> gpui::Stateful<gpui::Div> {
        div()
            .id("manga-back")
            .flex_none()
            .px(theme::SPACE_3)
            .py(theme::SPACE_1)
            .rounded_full()
            .border_1()
            .border_color(theme::line_strong())
            .text_size(theme::FS_SM)
            .cursor_pointer()
            .hover(|style| style.bg(theme::surface_2()))
            .child(label)
            .on_click(cx.listener(|manga, _, _, cx| {
                manga.back(cx);
            }))
    }

    fn series_page(
        &self,
        series: &Series,
        description: Option<&SharedString>,
        chapters: Option<&Result<Vec<Chapter>, SharedString>>,
        cx: &mut Context<Self>,
    ) -> gpui::AnyElement {
        let mut info = div()
            .flex()
            .flex_col()
            .gap(theme::SPACE_3)
            .max_w(px(720.0))
            .child(
                div()
                    .text_size(px(28.0))
                    .font_weight(gpui::FontWeight::BOLD)
                    .child(series.title.clone()),
            );
        if let Some(year) = &series.year {
            info = info.child(div().text_color(theme::text_2()).child(year.clone()));
        }
        if let Some(description) = description {
            info = info.child(
                div()
                    .text_size(theme::FS_BASE)
                    .line_height(px(24.0))
                    .child(description.clone()),
            );
        }
        let chapters_view = match chapters {
            None => div()
                .text_color(theme::text_3())
                .child(t!("anime.chargement"))
                .into_any_element(),
            Some(Err(error)) => div()
                .text_color(theme::text_3())
                .child(error.clone())
                .into_any_element(),
            Some(Ok(list)) if list.is_empty() => div()
                .text_color(theme::text_3())
                .child(t!("manga.aucun_chapitre_trouve_pour_cette_serie"))
                .into_any_element(),
            Some(Ok(list)) => div()
                .flex()
                .flex_col()
                .children(list.iter().enumerate().map(|(index, chapter)| {
                    let (series, chapters) = (series.clone(), list.clone());
                    div()
                        .id(SharedString::from(format!("chapter-{}", chapter.id)))
                        .flex()
                        .justify_between()
                        .px(theme::SPACE_3)
                        .py(theme::SPACE_2)
                        .rounded(theme::RADIUS_SM)
                        .cursor_pointer()
                        .hover(|style| style.bg(theme::surface_2()))
                        .child(div().truncate().child(chapter.label.clone()))
                        .child(
                            div()
                                .flex_none()
                                .text_size(theme::FS_SM)
                                .text_color(theme::text_3())
                                .child(chapter.date.clone()),
                        )
                        .on_click(cx.listener(move |manga, _, _, cx| {
                            manga.read(series.clone(), chapters.clone(), index, cx)
                        }))
                }))
                .into_any_element(),
        };
        div()
            .id("manga-series")
            .size_full()
            .overflow_y_scroll()
            .p(theme::SPACE_6)
            .flex()
            .flex_col()
            .gap(theme::SPACE_5)
            .child(div().flex().child(Self::back_button(t!("manga.manga"), cx)))
            .child(
                div()
                    .flex()
                    .gap(theme::SPACE_6)
                    .child(self.cover(series, 1.4, cx))
                    .child(info),
            )
            .child(chapters_view)
            .into_any_element()
    }

    fn reader(
        &self,
        series: &Series,
        chapters: &[Chapter],
        index: usize,
        pages: Option<&Result<Vec<SharedString>, SharedString>>,
        cx: &mut Context<Self>,
    ) -> gpui::AnyElement {
        let chapter = &chapters[index];
        let mut page = div()
            .id("manga-reader")
            .size_full()
            .overflow_y_scroll()
            .p(theme::SPACE_6)
            .flex()
            .flex_col()
            .items_center()
            .gap(theme::SPACE_3)
            .child(
                div()
                    .w_full()
                    .flex()
                    .items_center()
                    .gap(theme::SPACE_3)
                    .child(Self::back_button(t!("manga.manga"), cx))
                    .child(
                        div()
                            .text_size(theme::FS_LG)
                            .font_weight(gpui::FontWeight::BOLD)
                            .child(SharedString::from(format!(
                                "{} · {}",
                                series.title, chapter.label
                            ))),
                    ),
            );
        match pages {
            None => {
                page = page.child(
                    div()
                        .text_color(theme::text_3())
                        .child(t!("manga.chargement_du_chapitre")),
                );
            }
            Some(Err(error)) => {
                page = page.child(div().text_color(theme::text_3()).child(error.clone()))
            }
            Some(Ok(pages)) if pages.is_empty() => {
                page = page.child(
                    div()
                        .text_color(theme::text_3())
                        .child(t!("manga.ce_chapitre_n_a_pas_de_pages")),
                );
            }
            Some(Ok(pages)) => {
                for url in pages {
                    let image = self.images.update(cx, |images, cx| images.get(url, cx));
                    let frame = div().w(px(760.0)).min_h(px(400.0)).bg(theme::surface_1());
                    page = page.child(match image {
                        Some(image) => {
                            frame.child(img(image).w_full().object_fit(gpui::ObjectFit::Contain))
                        }
                        None => frame,
                    });
                }
                if index + 1 < chapters.len() {
                    let (series, list) = (series.clone(), chapters.to_vec());
                    page = page.child(
                        div()
                            .id("manga-next")
                            .px(theme::SPACE_4)
                            .py(theme::SPACE_2)
                            .rounded_full()
                            .bg(theme::text())
                            .text_color(theme::on_light())
                            .cursor_pointer()
                            .child(SharedString::from(t!(
                                "manga.chapitre_suivant_x",
                                chapters[index + 1].label.as_ref()
                            )))
                            .on_click(cx.listener(move |manga, _, _, cx| {
                                manga.read(series.clone(), list.clone(), index + 1, cx)
                            })),
                    );
                } else {
                    page = page.child(
                        div()
                            .text_color(theme::text_3())
                            .child(t!("manga.dernier_chapitre_disponible")),
                    );
                }
            }
        }
        page.into_any_element()
    }
}

impl Render for Manga {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        match &self.view {
            View::Home => self.home(cx),
            View::Series {
                series,
                description,
                chapters,
            } => {
                let (series, description, chapters) =
                    (series.clone(), description.clone(), chapters.clone());
                self.series_page(&series, description.as_ref(), chapters.as_ref(), cx)
            }
            View::Reader {
                series,
                chapters,
                index,
                pages,
            } => {
                let (series, chapters, index, pages) =
                    (series.clone(), chapters.clone(), *index, pages.clone());
                self.reader(&series, &chapters, index, pages.as_ref(), cx)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_series_needs_an_id_and_a_title() {
        let series = Series::new(&json!({"id": "30013", "title": "One Piece",
            "cover": "https://s4.anilist.co/x.jpg", "year": 1997}))
        .unwrap();
        assert_eq!(series.year.as_ref().map(|y| y.as_ref()), Some("1997"));
        assert!(series.cover.is_some());
        assert!(Series::new(&json!({"title": "No id"})).is_none());
        let insecure = Series::new(&json!({"id": "1", "title": "X", "cover": "http://x"})).unwrap();
        assert!(insecure.cover.is_none());
    }

    #[test]
    fn a_chapter_without_a_label_is_named_by_its_number() {
        let named =
            Chapter::new(&json!({"id": "wc:1", "number": "3", "label": "Episode 3 & bonus"}))
                .unwrap();
        assert_eq!(named.label.as_ref(), "Episode 3 & bonus");
        let numbered = Chapter::new(&json!({"id": "md:2", "number": "12"})).unwrap();
        assert!(numbered.label.contains("12"));
        assert!(Chapter::new(&json!({"number": "1"})).is_none());
    }

    #[test]
    fn a_route_error_keeps_its_message() {
        assert_eq!(
            answer(503, json!({"error": "MangaDex ne répond pas"})),
            Err("MangaDex ne répond pas".into())
        );
        assert!(answer(200, json!({"series": []})).is_ok());
    }
}
