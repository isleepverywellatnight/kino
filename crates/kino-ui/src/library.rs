//! The library (phase G4): Ma Liste, what is in progress, the user's series
//! (where each stands, what comes next), the calendar of their episodes,
//! what was seen, and the statistics, from KINO's database and routes
//! (`/api/my-series`, `/api/calendar`, `/api/stats`) as the web shows them.

use std::ops::Range;

use gpui::{
    Context, Entity, EventEmitter, SharedString, UniformListScrollHandle, Window, div, prelude::*,
    px, uniform_list,
};
use serde_json::Value;

use crate::core::Core;
use crate::discover::{OpenTitle, card, title_rows};
use crate::home::is_watched;
use crate::image_cache::ImageCache;
use crate::theme;
use crate::title::Title;

/// The library's tabs, in the web's order.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Tab {
    #[default]
    MyList,
    InProgress,
    MySeries,
    Calendar,
    Watched,
    Stats,
}

impl Tab {
    pub const ALL: [Tab; 6] = [
        Tab::MyList,
        Tab::InProgress,
        Tab::MySeries,
        Tab::Calendar,
        Tab::Watched,
        Tab::Stats,
    ];

    fn label(self) -> &'static str {
        match self {
            Tab::MyList => t!("page.ma_liste_2"),
            Tab::InProgress => t!("page.en_cours"),
            Tab::MySeries => t!("page.mes_series"),
            Tab::Calendar => t!("page.calendrier"),
            Tab::Watched => t!("page.deja_vus"),
            Tab::Stats => t!("page.statistiques"),
        }
    }

    fn id(self) -> &'static str {
        match self {
            Tab::MyList => "lib-mylist",
            Tab::InProgress => "lib-inprogress",
            Tab::MySeries => "lib-myseries",
            Tab::Calendar => "lib-calendar",
            Tab::Watched => "lib-watched",
            Tab::Stats => "lib-stats",
        }
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|n| n != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Null => false,
        _ => true,
    }
}

fn percent(entry: &Value) -> f64 {
    match &entry["progress_pct"] {
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(s) => s.trim().parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

/// The three lists of titles: Ma Liste (newest first), in progress, seen.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Lists {
    pub my_list: Vec<Title>,
    pub in_progress: Vec<Title>,
    pub watched: Vec<Title>,
}

impl Lists {
    /// As the web's `getInProgressHistory` and `getWatchedHistory`.
    pub fn new(history: &[Value], watchlist: &[Value]) -> Self {
        let done = |h: &Value| truthy(&h["completed"]) || percent(h) >= 85.0;
        let in_progress = history
            .iter()
            .filter(|h| !truthy(&h["imported_watched"]))
            .filter(|h| {
                if h["type"] == "series" {
                    !(truthy(&h["series_finished"]) && done(h))
                } else {
                    !done(h)
                }
            })
            .filter_map(Title::from_meta)
            .collect();
        let watched = history
            .iter()
            .filter(|h| done(h) || truthy(&h["imported_watched"]))
            .filter_map(Title::from_meta)
            .collect();
        let my_list = watchlist
            .iter()
            .filter(|m| !is_watched(m, history))
            .filter_map(Title::from_meta)
            .collect();
        Self {
            my_list,
            in_progress,
            watched,
        }
    }
}

/// One of the user's series and where it stands (`/api/my-series`).
#[derive(Clone, Debug, PartialEq)]
pub struct Followed {
    pub title: Title,
    /// "continue", "uptodate" or "start".
    pub group: SharedString,
    /// The next episode to watch.
    pub next: Option<(u32, u32)>,
    pub seen: u64,
    pub aired: u64,
    /// The next one to come out: (season, episode, day).
    pub upcoming: Option<(u32, u32, SharedString)>,
}

fn episode_at(value: &Value) -> Option<(u32, u32)> {
    let number = |key: &str| value[key].as_u64().and_then(|n| u32::try_from(n).ok());
    Some((number("season")?, number("episode")?))
}

impl Followed {
    pub fn new(series: &Value) -> Option<Self> {
        let mut title = Title::from_meta(series)?;
        title.kind = "series".into();
        Some(Self {
            title,
            group: series["group"]
                .as_str()
                .unwrap_or("continue")
                .to_string()
                .into(),
            next: episode_at(&series["next"]),
            seen: series["seen"].as_u64().unwrap_or(0),
            aired: series["aired"].as_u64().unwrap_or(0),
            upcoming: episode_at(&series["upcoming"]).map(|(s, e)| {
                (
                    s,
                    e,
                    series["upcoming"]["released"]
                        .as_str()
                        .unwrap_or_default()
                        .to_string()
                        .into(),
                )
            }),
        })
    }

    /// "S02E04 · 13 épisodes vus sur 20".
    pub fn note(&self) -> SharedString {
        let mut parts = Vec::new();
        if let Some((season, episode)) = self.next {
            parts.push(format!("S{season:02}E{episode:02}"));
        }
        if self.aired > 0 {
            parts.push(t!("lists.x_episodes_vus_sur_x", self.seen, self.aired));
        }
        if let Some((season, episode, day)) = &self.upcoming {
            parts.push(t!(
                "lists.prochain_a_sortir_x_le_x",
                format!("S{season:02}E{episode:02}"),
                day.as_ref()
            ));
        }
        parts.join(" · ").into()
    }
}

/// A day of the calendar and its episodes: (series, "S01E03").
#[derive(Clone, Debug, PartialEq)]
pub struct Day {
    pub date: SharedString,
    pub items: Vec<(Title, SharedString)>,
}

/// The calendar's days (`/api/calendar`).
pub fn calendar_days(calendar: &Value) -> Vec<Day> {
    calendar["days"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|day| {
            let items: Vec<(Title, SharedString)> = day["items"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|item| {
                    let mut title = Title::from_meta(item)?;
                    title.kind = "series".into();
                    let (season, episode) = episode_at(item)?;
                    let last = item["last_episode"].as_u64().unwrap_or(u64::from(episode));
                    let code = if last > u64::from(episode) {
                        format!("S{season:02}E{episode:02}–E{last:02}")
                    } else {
                        format!("S{season:02}E{episode:02}")
                    };
                    Some((title, code.into()))
                })
                .collect();
            (!items.is_empty()).then(|| Day {
                date: day["date"].as_str().unwrap_or_default().to_string().into(),
                items,
            })
        })
        .collect()
}

/// The statistics' main figures (`/api/stats`): (label, figure).
pub fn stats_figures(stats: &Value) -> Vec<(&'static str, SharedString)> {
    let number = |v: &Value| v.as_u64().unwrap_or(0);
    let hours = number(&stats["watch_seconds"]) / 3600;
    let mut figures = vec![
        (
            t!("stats.films_vus"),
            number(&stats["films"]["seen"]).to_string().into(),
        ),
        (
            t!("stats.episodes_vus"),
            number(&stats["episodes"]["seen"]).to_string().into(),
        ),
        (
            t!("page.mes_series"),
            number(&stats["series"]["total"]).to_string().into(),
        ),
        (
            t!("stats.de_lecture_dans_kino"),
            format!("{hours} h").into(),
        ),
    ];
    let genres: Vec<String> = stats["genres"]
        .as_array()
        .into_iter()
        .flatten()
        .take(5)
        .filter_map(|g| Some(format!("{} ({})", g["name"].as_str()?, number(&g["count"]))))
        .collect();
    if !genres.is_empty() {
        figures.push((t!("stats.genres_preferes"), genres.join(", ").into()));
    }
    figures
}

enum Loaded<T> {
    Loading,
    Ready(T),
}

pub struct Library {
    core: Core,
    images: Entity<ImageCache>,
    tab: Tab,
    lists: Loaded<Lists>,
    series: Loaded<Vec<Followed>>,
    calendar: Loaded<Vec<Day>>,
    stats: Loaded<Vec<(&'static str, SharedString)>>,
    grid: UniformListScrollHandle,
}

impl EventEmitter<OpenTitle> for Library {}

impl Library {
    pub fn new(core: Core, images: Entity<ImageCache>, cx: &mut Context<Self>) -> Self {
        cx.observe(&images, |_, _, cx| cx.notify()).detach();
        Self {
            core,
            images,
            tab: Tab::default(),
            lists: Loaded::Loading,
            series: Loaded::Loading,
            calendar: Loaded::Loading,
            stats: Loaded::Loading,
            grid: UniformListScrollHandle::new(),
        }
    }

    /// The lists, once read (`--shell-check`).
    pub fn lists(&self) -> Option<&Lists> {
        match &self.lists {
            Loaded::Ready(lists) => Some(lists),
            Loaded::Loading => None,
        }
    }

    /// The statistics, once read (`--shell-check`).
    pub fn stats(&self) -> Option<&[(&'static str, SharedString)]> {
        match &self.stats {
            Loaded::Ready(stats) => Some(stats),
            Loaded::Loading => None,
        }
    }

    /// Shows `tab`.
    pub fn show(&mut self, tab: Tab, cx: &mut Context<Self>) {
        self.tab = tab;
        self.refresh(cx);
    }

    /// Reads what the shown tab needs again (the screen opened, a tab chosen).
    pub fn refresh(&mut self, cx: &mut Context<Self>) {
        match self.tab {
            Tab::MyList | Tab::InProgress | Tab::Watched => {
                let library = self.core.library();
                cx.spawn(async move |this, cx| {
                    let (history, watchlist) = library.await;
                    let lists = Lists::new(&history, &watchlist);
                    let _ = this.update(cx, |library, cx| {
                        library.lists = Loaded::Ready(lists);
                        cx.notify();
                    });
                })
                .detach();
            }
            Tab::MySeries => {
                let answer = self.core.api("GET", "/api/my-series".into(), None);
                cx.spawn(async move |this, cx| {
                    let (_, answer) = answer.await;
                    let series = answer["series"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Followed::new)
                        .collect();
                    let _ = this.update(cx, |library, cx| {
                        library.series = Loaded::Ready(series);
                        cx.notify();
                    });
                })
                .detach();
            }
            Tab::Calendar => {
                let answer = self.core.api("GET", "/api/calendar".into(), None);
                cx.spawn(async move |this, cx| {
                    let (_, answer) = answer.await;
                    let days = calendar_days(&answer);
                    let _ = this.update(cx, |library, cx| {
                        library.calendar = Loaded::Ready(days);
                        cx.notify();
                    });
                })
                .detach();
            }
            Tab::Stats => {
                let answer = self.core.api("GET", "/api/stats".into(), None);
                cx.spawn(async move |this, cx| {
                    let (_, answer) = answer.await;
                    let figures = stats_figures(&answer);
                    let _ = this.update(cx, |library, cx| {
                        library.stats = Loaded::Ready(figures);
                        cx.notify();
                    });
                })
                .detach();
            }
        }
    }

    fn grid(&self, titles: Vec<Title>, cx: &mut Context<Self>) -> gpui::AnyElement {
        if titles.is_empty() {
            return message(t!("letterboxd.cette_liste_est_vide"));
        }
        let rows = titles.len().div_ceil(crate::COLUMNS);
        uniform_list(
            "library-grid",
            rows,
            cx.processor(move |library, range: Range<usize>, _, cx| {
                let open = OpenTitle::emitter(cx.entity());
                title_rows(&titles, range, &library.images, open, cx)
            }),
        )
        .track_scroll(self.grid.clone())
        .flex_grow()
        .into_any_element()
    }

    fn series(&self, series: &[Followed], cx: &mut Context<Self>) -> gpui::AnyElement {
        if series.is_empty() {
            return message(t!("letterboxd.cette_liste_est_vide"));
        }
        let open = OpenTitle::emitter(cx.entity());
        let mut page = div()
            .id("library-series")
            .flex_grow()
            .overflow_y_scroll()
            .px(theme::SPACE_6)
            .pb(theme::SPACE_6)
            .flex()
            .flex_col()
            .gap(theme::SPACE_3);
        for (group, heading) in [
            ("continue", t!("page.a_continuer")),
            ("uptodate", t!("lists.a_jour")),
            ("start", t!("lists.a_commencer")),
        ] {
            let members: Vec<&Followed> = series.iter().filter(|s| s.group == group).collect();
            if members.is_empty() {
                continue;
            }
            page = page.child(
                div()
                    .pt(theme::SPACE_4)
                    .text_size(theme::FS_LG)
                    .font_weight(gpui::FontWeight::BOLD)
                    .child(heading),
            );
            let cards: Vec<_> = members
                .iter()
                .map(|s| card(&s.title, s.note(), &self.images, open.clone(), cx).flex_none())
                .collect();
            page = page.child(div().flex().flex_wrap().gap_4().children(cards));
        }
        page.into_any_element()
    }

    fn calendar(&self, days: &[Day], cx: &mut Context<Self>) -> gpui::AnyElement {
        if days.is_empty() {
            return message(t!("letterboxd.cette_liste_est_vide"));
        }
        let open = OpenTitle::emitter(cx.entity());
        let mut page = div()
            .id("library-calendar")
            .flex_grow()
            .overflow_y_scroll()
            .px(theme::SPACE_6)
            .pb(theme::SPACE_6)
            .flex()
            .flex_col()
            .gap(theme::SPACE_4);
        for day in days {
            let cards: Vec<_> = day
                .items
                .iter()
                .map(|(title, code)| {
                    card(title, code.clone(), &self.images, open.clone(), cx).flex_none()
                })
                .collect();
            page = page
                .child(
                    div()
                        .pt(theme::SPACE_2)
                        .text_size(theme::FS_LG)
                        .font_weight(gpui::FontWeight::BOLD)
                        .child(day.date.clone()),
                )
                .child(div().flex().flex_wrap().gap_4().children(cards));
        }
        page.into_any_element()
    }
}

fn message(text: &'static str) -> gpui::AnyElement {
    div()
        .px(theme::SPACE_6)
        .pt(theme::SPACE_4)
        .text_color(theme::text_3())
        .child(text)
        .into_any_element()
}

fn loading() -> gpui::AnyElement {
    message(t!("anime.chargement"))
}

impl Render for Library {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let tabs: Vec<_> = Tab::ALL
            .into_iter()
            .map(|tab| {
                let active = tab == self.tab;
                let pill = div()
                    .id(tab.id())
                    .px(theme::SPACE_3)
                    .py(theme::SPACE_1)
                    .rounded_full()
                    .border_1()
                    .text_size(theme::FS_SM)
                    .cursor_pointer()
                    .child(tab.label())
                    .on_click(cx.listener(move |library, _, _, cx| library.show(tab, cx)));
                if active {
                    pill.bg(theme::text())
                        .border_color(theme::text())
                        .text_color(theme::on_light())
                } else {
                    pill.border_color(theme::line_strong())
                        .text_color(theme::text_2())
                        .hover(|style| style.bg(theme::surface_2()))
                }
            })
            .collect();
        let body = match self.tab {
            Tab::MyList | Tab::InProgress | Tab::Watched => match &self.lists {
                Loaded::Loading => loading(),
                Loaded::Ready(lists) => {
                    let titles = match self.tab {
                        Tab::MyList => lists.my_list.clone(),
                        Tab::InProgress => lists.in_progress.clone(),
                        _ => lists.watched.clone(),
                    };
                    self.grid(titles, cx)
                }
            },
            Tab::MySeries => match &self.series {
                Loaded::Loading => loading(),
                Loaded::Ready(series) => {
                    let series = series.clone();
                    self.series(&series, cx)
                }
            },
            Tab::Calendar => match &self.calendar {
                Loaded::Loading => loading(),
                Loaded::Ready(days) => {
                    let days = days.clone();
                    self.calendar(&days, cx)
                }
            },
            Tab::Stats => match &self.stats {
                Loaded::Loading => loading(),
                Loaded::Ready(figures) => div()
                    .px(theme::SPACE_6)
                    .pt(theme::SPACE_4)
                    .flex()
                    .flex_wrap()
                    .gap(theme::SPACE_4)
                    .children(figures.iter().map(|(label, figure)| {
                        div()
                            .min_w(px(180.0))
                            .p(theme::SPACE_4)
                            .rounded(theme::RADIUS)
                            .bg(theme::surface_1())
                            .border_1()
                            .border_color(theme::line())
                            .flex()
                            .flex_col()
                            .gap(theme::SPACE_1)
                            .child(
                                div()
                                    .text_size(px(26.0))
                                    .font_weight(gpui::FontWeight::BOLD)
                                    .child(figure.clone()),
                            )
                            .child(
                                div()
                                    .text_size(theme::FS_SM)
                                    .text_color(theme::text_3())
                                    .child(*label),
                            )
                    }))
                    .into_any_element(),
            },
        };
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
                    .gap(theme::SPACE_3)
                    .child(
                        div()
                            .text_size(px(28.0))
                            .font_weight(gpui::FontWeight::BOLD)
                            .child(t!("page.bibliotheque")),
                    )
                    .child(div().flex().flex_wrap().gap(theme::SPACE_2).children(tabs)),
            )
            .child(body)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_lists_follow_the_web_s_rules() {
        let history = vec![
            json!({"id": "tt1", "name": "Half", "type": "movie", "progress_pct": 40}),
            json!({"id": "tt2", "name": "Seen", "type": "movie", "completed": true}),
            json!({"id": "tt3", "name": "Imported", "type": "movie", "imported_watched": true}),
        ];
        let watchlist = vec![
            json!({"id": "tt2", "name": "Seen", "type": "movie"}),
            json!({"id": "tt4", "name": "To see", "type": "movie"}),
        ];
        let lists = Lists::new(&history, &watchlist);
        let names = |t: &[Title]| t.iter().map(|t| t.name.to_string()).collect::<Vec<_>>();
        assert_eq!(names(&lists.in_progress), ["Half"]);
        assert_eq!(names(&lists.watched), ["Seen", "Imported"]);
        assert_eq!(names(&lists.my_list), ["To see"]);
    }

    #[test]
    fn a_followed_series_says_what_comes_next() {
        let series = Followed::new(&json!({
            "id": "tt9", "name": "Show", "group": "continue",
            "next": {"season": 2, "episode": 4}, "seen": 13, "aired": 20,
            "upcoming": {"season": 3, "episode": 1, "released": "2027-01-05"}
        }))
        .unwrap();
        assert_eq!(series.next, Some((2, 4)));
        let note = series.note();
        assert!(note.starts_with("S02E04 · "), "{note}");
        assert!(note.contains("2027-01-05"));
    }

    #[test]
    fn the_calendar_groups_episodes_by_day() {
        let days = calendar_days(&json!({"days": [
            {"date": "2026-11-20", "items": [
                {"id": "tt9", "name": "Show", "season": 1, "episode": 3, "last_episode": 4}]},
            {"date": "2026-11-21", "items": []}
        ]}));
        assert_eq!(days.len(), 1);
        assert_eq!(days[0].items[0].1.as_ref(), "S01E03–E04");
    }

    #[test]
    fn the_statistics_show_their_figures() {
        let figures = stats_figures(&json!({
            "films": {"seen": 12}, "episodes": {"seen": 40}, "series": {"total": 3},
            "watch_seconds": 7200, "genres": [{"name": "Drama", "count": 5}]
        }));
        assert_eq!(figures[0].1.as_ref(), "12");
        assert_eq!(figures[3].1.as_ref(), "2 h");
        assert!(figures.last().unwrap().1.contains("Drama (5)"));
    }
}
