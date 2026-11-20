//! A title's page (phases G2–G3, opened from any grid): its backdrop, poster,
//! year, rating, runtime, genres, synopsis, director and cast, from KINO's
//! metadata; « Regarder » (KINO's player through the one-click play, the
//! source prepared by the debrid service when it has to be), Ma Liste, a
//! series' seasons and episodes, and « Vous aimerez aussi ».

use std::time::Duration;

use gpui::{Context, Entity, EventEmitter, SharedString, Window, div, img, prelude::*, px};
use serde_json::Value;

use crate::core::{Core, PlayOutcome, PlayRequest};
use crate::discover::{OpenTitle, title_rows};
use crate::i18n::{self, Language};
use crate::image_cache::ImageCache;
use crate::title::{Details, Episode, Title};
use crate::{POSTER_H, POSTER_W, theme};

/// How often a source being prepared is asked about again.
const PREPARING_POLL: Duration = Duration::from_secs(5);

enum State {
    Loading,
    Ready(Box<Details>),
    Failed(SharedString),
}

/// Where playing stands, as the line under the buttons tells it.
#[derive(Clone, Debug, PartialEq)]
pub enum Playing {
    Idle,
    Launching,
    Preparing(u8),
    Started(SharedString),
    /// Peer-to-peer reading needs the user's agreement first.
    NeedsConsent(SharedString, Option<(u32, u32)>),
    Failed(SharedString),
}

pub struct TitlePage {
    core: Core,
    title: Title,
    images: Entity<ImageCache>,
    state: State,
    /// Whether the title is in Ma Liste, once known.
    in_list: Option<bool>,
    /// The episode a series picks up at, from the history.
    resume: Option<(u32, u32)>,
    /// The season shown, for a series.
    season: Option<u32>,
    playing: Playing,
    /// Bumped on each play: an older attempt's answer is dropped.
    attempt: u64,
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
                    Ok(details) => {
                        if page.season.is_none() {
                            page.season = details.episodes.first().map(|e| e.season);
                        }
                        State::Ready(Box::new(details))
                    }
                    Err(error) => State::Failed(error.into()),
                };
                cx.notify();
            });
        })
        .detach();
        let (in_list, resume) = (
            core.in_watchlist(title.id.clone()),
            core.resume_episode(title.id.clone()),
        );
        cx.spawn(async move |this, cx| {
            let (in_list, resume) = (in_list.await, resume.await);
            let _ = this.update(cx, |page, cx| {
                page.in_list = Some(in_list);
                page.resume = resume;
                if let Some((season, _)) = resume {
                    page.season = Some(season);
                }
                cx.notify();
            });
        })
        .detach();
        Self {
            core: core.clone(),
            title,
            images,
            state: State::Loading,
            in_list: None,
            resume: None,
            season: None,
            playing: Playing::Idle,
            attempt: 0,
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

    /// Whether the title is in Ma Liste, once known (`--shell-check`).
    pub fn in_list(&self) -> Option<bool> {
        self.in_list
    }

    /// Where playing stands (`--shell-check`).
    pub fn playing(&self) -> &Playing {
        &self.playing
    }

    fn image(
        &self,
        url: Option<&SharedString>,
        cx: &mut Context<Self>,
    ) -> Option<std::sync::Arc<gpui::RenderImage>> {
        url.and_then(|url| self.images.update(cx, |images, cx| images.get(url, cx)))
    }

    /// Adds the title to Ma Liste, or takes it out.
    pub fn toggle_list(&mut self, cx: &mut Context<Self>) {
        let title = match &self.state {
            State::Ready(details) => details.title.clone(),
            _ => self.title.clone(),
        };
        let toggled = self.core.toggle_watchlist(&title);
        cx.spawn(async move |this, cx| {
            let in_list = toggled.await;
            let _ = this.update(cx, |page, cx| {
                page.in_list = Some(in_list);
                cx.notify();
            });
        })
        .detach();
    }

    /// Plays the film, or `episode` of the series, with KINO's player.
    pub fn play(&mut self, episode: Option<(u32, u32)>, cx: &mut Context<Self>) {
        self.attempt += 1;
        self.ask(episode, None, self.attempt, cx);
    }

    fn ask(
        &mut self,
        episode: Option<(u32, u32)>,
        prepared: Option<Value>,
        attempt: u64,
        cx: &mut Context<Self>,
    ) {
        if prepared.is_none() {
            self.playing = Playing::Launching;
            cx.notify();
        }
        let title = match &self.state {
            State::Ready(details) => details.title.clone(),
            _ => self.title.clone(),
        };
        let outcome = self.core.play(&PlayRequest {
            title,
            episode,
            prepared,
        });
        cx.spawn(async move |this, cx| {
            let outcome = outcome.await;
            let wait = matches!(outcome, PlayOutcome::Preparing { .. });
            if wait {
                cx.background_executor().timer(PREPARING_POLL).await;
            }
            let _ = this.update(cx, |page, cx| {
                if page.attempt != attempt {
                    return;
                }
                match outcome {
                    PlayOutcome::Started { source } => {
                        page.playing = Playing::Started(source);
                        if episode.is_some() {
                            page.resume = episode;
                        }
                    }
                    PlayOutcome::Preparing { progress, prepared } => {
                        page.playing = Playing::Preparing(progress);
                        page.ask(episode, Some(prepared), attempt, cx);
                    }
                    PlayOutcome::NeedsConsent(message) => {
                        page.playing = Playing::NeedsConsent(message, episode);
                    }
                    PlayOutcome::Failed(error) => page.playing = Playing::Failed(error),
                }
                cx.notify();
            });
        })
        .detach();
    }

    /// The user agreed to peer-to-peer reading: play again.
    fn accept_p2p(&mut self, episode: Option<(u32, u32)>, cx: &mut Context<Self>) {
        let accepted = self.core.accept_p2p();
        cx.spawn(async move |this, cx| {
            let accepted = accepted.await;
            let _ = this.update(cx, |page, cx| {
                if accepted {
                    page.play(episode, cx);
                }
            });
        })
        .detach();
    }

    /// The episode « Regarder » plays, for a series: where the user is, else
    /// the first one.
    fn next_episode(&self, details: &Details) -> Option<(u32, u32)> {
        self.resume
            .or_else(|| details.episodes.first().map(|e| (e.season, e.episode)))
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

/// A rounded button; `primary` stands out.
fn button(
    id: impl Into<SharedString>,
    label: SharedString,
    primary: bool,
) -> gpui::Stateful<gpui::Div> {
    let button = div()
        .id(id.into())
        .flex_none()
        .px(theme::SPACE_4)
        .py(theme::SPACE_2)
        .rounded_full()
        .border_1()
        .text_size(theme::FS_MD)
        .cursor_pointer()
        .child(label);
    if primary {
        button
            .bg(theme::text())
            .border_color(theme::text())
            .text_color(theme::on_light())
    } else {
        button
            .border_color(theme::line_strong())
            .text_color(theme::text())
            .hover(|style| style.bg(theme::surface_2()))
    }
}

impl TitlePage {
    fn actions(&self, details: Option<&Details>, cx: &mut Context<Self>) -> gpui::Div {
        let series = details.is_some_and(|d| !d.episodes.is_empty());
        let episode = details
            .and_then(|d| self.next_episode(d))
            .filter(|_| series);
        let label: SharedString = match episode {
            Some((season, number)) => {
                format!("▶ {} S{season:02}E{number:02}", t!("home.regarder")).into()
            }
            None => format!("▶ {}", t!("home.regarder")).into(),
        };
        let launching = matches!(self.playing, Playing::Launching | Playing::Preparing(_));
        let mut play = button("title-play", label, true);
        // Once the page has loaded, or failed to: the title alone can be played.
        if !launching && !matches!(self.state, State::Loading) {
            play = play.on_click(cx.listener(move |page, _, _, cx| page.play(episode, cx)));
        } else {
            play = play.opacity(0.6);
        }
        let list_label: SharedString = match self.in_list {
            Some(true) => t!("core.dans_ma_liste").into(),
            _ => format!("+ {}", t!("core.ajouter_a_ma_liste")).into(),
        };
        let list = button("title-list", list_label, false)
            .on_click(cx.listener(|page, _, _, cx| page.toggle_list(cx)));
        let mut row = div()
            .flex()
            .flex_wrap()
            .items_center()
            .gap(theme::SPACE_3)
            .child(play)
            .child(list);
        let status: Option<SharedString> = match &self.playing {
            Playing::Idle => None,
            Playing::Launching => Some(t!("fiche.lancement_du_flux").into()),
            Playing::Preparing(progress) => {
                Some(t!("fiche.preparation_de_la_qualite_choisie_x", progress).into())
            }
            Playing::Started(source) if source.is_empty() => Some(t!("fiche.lance").into()),
            Playing::Started(source) => Some(format!("{} · {source}", t!("fiche.lance")).into()),
            Playing::NeedsConsent(message, _) | Playing::Failed(message) => Some(message.clone()),
        };
        if let Playing::NeedsConsent(_, episode) = &self.playing {
            let episode = *episode;
            row = row.child(
                button("title-p2p", t!("page.j_accepte_lire_en_p2p").into(), false)
                    .on_click(cx.listener(move |page, _, _, cx| page.accept_p2p(episode, cx))),
            );
        }
        let mut actions = div().flex().flex_col().gap(theme::SPACE_2).child(row);
        if let Some(status) = status {
            actions = actions.child(
                div()
                    .text_size(theme::FS_SM)
                    .text_color(theme::text_2())
                    .child(status),
            );
        }
        actions
    }

    fn episodes(&self, episodes: &[Episode], cx: &mut Context<Self>) -> gpui::Div {
        let mut seasons: Vec<u32> = episodes.iter().map(|e| e.season).collect();
        seasons.dedup();
        let shown = self
            .season
            .filter(|s| seasons.contains(s))
            .or_else(|| seasons.first().copied());
        let pills = seasons.iter().map(|&season| {
            let active = Some(season) == shown;
            let pill = div()
                .id(SharedString::from(format!("season-{season}")))
                .px(theme::SPACE_3)
                .py(theme::SPACE_1)
                .rounded_full()
                .border_1()
                .text_size(theme::FS_SM)
                .cursor_pointer()
                .child(SharedString::from(format!(
                    "{} {season}",
                    t!("page.saison")
                )))
                .on_click(cx.listener(move |page, _, _, cx| {
                    page.season = Some(season);
                    cx.notify();
                }));
            if active {
                pill.bg(theme::text())
                    .border_color(theme::text())
                    .text_color(theme::on_light())
            } else {
                pill.border_color(theme::line_strong())
                    .text_color(theme::text_2())
            }
        });
        let rows = episodes
            .iter()
            .filter(|e| Some(e.season) == shown)
            .map(|e| {
                let at = (e.season, e.episode);
                let current = self.resume == Some(at);
                div()
                    .id(SharedString::from(format!("episode-{}", e.code())))
                    .flex()
                    .items_center()
                    .gap(theme::SPACE_3)
                    .px(theme::SPACE_3)
                    .py(theme::SPACE_2)
                    .rounded(theme::RADIUS_SM)
                    .cursor_pointer()
                    .when(current, |row| row.bg(theme::surface_2()))
                    .hover(|style| style.bg(theme::surface_2()))
                    .child(div().w(px(24.0)).text_color(theme::text_3()).child("▶"))
                    .child(
                        div()
                            .w(px(72.0))
                            .flex_none()
                            .text_size(theme::FS_SM)
                            .text_color(theme::text_3())
                            .child(e.code()),
                    )
                    .child(
                        div()
                            .flex_grow()
                            .min_w_0()
                            .truncate()
                            .text_size(theme::FS_MD)
                            .child(e.name.clone()),
                    )
                    .child(
                        div()
                            .flex_none()
                            .text_size(theme::FS_SM)
                            .text_color(theme::text_3())
                            .child(e.released.clone()),
                    )
                    .on_click(cx.listener(move |page, _, _, cx| page.play(Some(at), cx)))
            })
            .collect::<Vec<_>>();
        div()
            .px(theme::SPACE_6)
            .pt(theme::SPACE_4)
            .flex()
            .flex_col()
            .gap(theme::SPACE_3)
            .child(
                div()
                    .text_size(theme::FS_LG)
                    .font_weight(gpui::FontWeight::BOLD)
                    .child(t!("fiche.episodes")),
            )
            .child(div().flex().flex_wrap().gap(theme::SPACE_2).children(pills))
            .child(div().flex().flex_col().children(rows))
    }
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
            )
            .child(self.actions(details.as_ref(), cx));
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
        let hero = div().relative().w_full().min_h(px(POSTER_H * 1.4 + 96.0));
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
                .relative()
                .p(theme::SPACE_6)
                .flex()
                .gap(theme::SPACE_6)
                .child(art)
                .child(info),
        );

        let mut page = div()
            .id("title-page")
            .size_full()
            .overflow_y_scroll()
            .flex()
            .flex_col()
            .pb(theme::SPACE_6)
            .child(hero);
        if let Some(details) = &details
            && !details.episodes.is_empty()
        {
            page = page.child(self.episodes(&details.episodes, cx));
        }
        let similar = details.map(|d| d.similar).unwrap_or_default();
        if !similar.is_empty() {
            let rows = similar.len().div_ceil(crate::COLUMNS);
            let open = OpenTitle::emitter(cx.entity());
            page = page
                .child(
                    div()
                        .px(theme::SPACE_6)
                        .pt(theme::SPACE_4)
                        .text_size(theme::FS_LG)
                        .font_weight(gpui::FontWeight::BOLD)
                        .child(t!("fiche.vous_aimerez_aussi")),
                )
                .children(title_rows(&similar, 0..rows, &self.images, open, cx));
        }
        page
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn title() -> Title {
        Title::from_meta(&json!({"id": "tt1", "name": "Heat", "type": "movie", "year": "1995"}))
            .unwrap()
    }

    #[test]
    fn a_film_is_asked_for_with_kino_s_player() {
        let body = PlayRequest {
            title: title(),
            episode: None,
            prepared: None,
        }
        .body();
        assert_eq!(body["type"], "movie");
        assert_eq!(body["player_mode"], "kino");
        assert_eq!(body["title"], "Heat (1995)");
        assert!(body.get("season").is_none());
    }

    #[test]
    fn an_episode_carries_its_numbers_and_what_was_prepared() {
        let prepared = json!({"torrent_id": "ABC", "source_key": "k"});
        let body = PlayRequest {
            title: title(),
            episode: Some((2, 5)),
            prepared: Some(prepared.clone()),
        }
        .body();
        assert_eq!(
            (
                body["type"].as_str(),
                body["season"].as_u64(),
                body["episode"].as_u64()
            ),
            (Some("series"), Some(2), Some(5))
        );
        assert_eq!(body["prepared"], prepared);
    }

    #[test]
    fn the_route_s_answers_become_outcomes() {
        assert_eq!(
            PlayOutcome::from_answer(
                200,
                &json!({"ok": true, "chosen_torrent": " RD Heat 1080p "})
            ),
            PlayOutcome::Started {
                source: "RD Heat 1080p".into()
            }
        );
        assert!(matches!(
            PlayOutcome::from_answer(
                202,
                &json!({"preparing": true, "progress": 140,
                "torrent_id": "T", "source_key": "K"})
            ),
            PlayOutcome::Preparing { progress: 100, .. }
        ));
        assert!(matches!(
            PlayOutcome::from_answer(409, &json!({"error": "P2P ?", "code": "p2p_consent"})),
            PlayOutcome::NeedsConsent(_)
        ));
        assert_eq!(
            PlayOutcome::from_answer(500, &json!({"error": "Aucun flux"})),
            PlayOutcome::Failed("Aucun flux".into())
        );
        assert_eq!(
            PlayOutcome::from_answer(502, &json!(null)),
            PlayOutcome::Failed("HTTP 502".into())
        );
    }
}
