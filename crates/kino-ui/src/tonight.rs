//! « Ce soir » (phase G2): up to three titles of Ma Liste for the time and
//! mood the user has, each with KINO's reasons, as the web's dialog shows
//! them (`kino::tonight`). « Pas ce soir », « Déjà vu » and « Ça ne
//! m'intéresse pas » are recorded and the ideas asked for again.

use gpui::{Context, Entity, EventEmitter, SharedString, Window, div, img, prelude::*, px};
use serde_json::Value;

use crate::core::Core;
use crate::discover::OpenTitle;
use crate::image_cache::ImageCache;
use crate::theme;
use crate::title::Title;

/// The durations offered, in minutes, and how they read (the web's menu).
pub const DURATIONS: [(i64, &str); 5] = [
    (45, "45 min"),
    (90, "1 h 30"),
    (120, "2 h"),
    (150, "2 h 30"),
    (180, "3 h"),
];

/// What the user feels like watching.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Mood {
    #[default]
    Discovery,
    Light,
    Intense,
}

impl Mood {
    pub const ALL: [Mood; 3] = [Mood::Discovery, Mood::Light, Mood::Intense];

    /// The value `kino::tonight` takes.
    pub fn param(self) -> &'static str {
        match self {
            Mood::Discovery => "discovery",
            Mood::Light => "light",
            Mood::Intense => "intense",
        }
    }

    fn label(self) -> &'static str {
        match self {
            Mood::Discovery => t!("page.decouverte"),
            Mood::Light => t!("page.leger"),
            Mood::Intense => t!("page.intense"),
        }
    }
}

/// One idea: the title and why KINO proposes it.
#[derive(Clone, Debug, PartialEq)]
pub struct Choice {
    pub title: Title,
    /// "Dure 1 h 52 · Comédie · …", as `kino::tonight` writes them.
    pub reasons: SharedString,
}

/// What the page shows once KINO answered.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Answer {
    pub choices: Vec<Choice>,
    /// Why nothing came, when nothing did (`kino::tonight::empty_reason`).
    pub empty_reason: &'static str,
    /// Some durations or moods were unknown.
    pub partial: bool,
}

impl Answer {
    pub fn new(page: &kino::tonight::Page) -> Self {
        let choices = page
            .choices
            .iter()
            .filter_map(|choice| {
                let reasons: Vec<&str> = choice["reasons"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .collect();
                Some(Choice {
                    title: Title::from_meta(&choice["item"])?,
                    reasons: reasons.join(" · ").into(),
                })
            })
            .take(3)
            .collect();
        Self {
            choices,
            empty_reason: page.empty_reason,
            partial: page.coverage["partial"] == true,
        }
    }

    /// The line above the ideas: how many, or why there are none.
    pub fn status(&self) -> SharedString {
        let count = self.choices.len();
        if count > 0 {
            return if self.partial {
                t!("core.certaines_durees_ou_ambiances_restent").into()
            } else {
                t!(
                    "core.x_idee_x_dans_ma_liste_selon_vos_choix",
                    count,
                    if count > 1 { "s" } else { "" }
                )
                .into()
            };
        }
        match self.empty_reason {
            "empty_watchlist" => t!("core.ajoutez_des_titres_a_ma_liste_pour"),
            "no_offline" => t!("core.aucun_titre_de_ma_liste_n_est_pret_hors"),
            "all_watched" => t!("core.tous_les_titres_de_ma_liste_sont_deja"),
            "all_deferred" => t!("core.tous_les_titres_disponibles_sont"),
            _ => t!("core.aucun_titre_ne_correspond_essayez_une"),
        }
        .into()
    }
}

enum State {
    Loading,
    Ready(Answer),
    Failed(SharedString),
}

pub struct Tonight {
    core: Core,
    images: Entity<ImageCache>,
    minutes: i64,
    mood: Mood,
    /// Bumped on each request: an older answer is dropped.
    generation: u64,
    state: State,
}

impl EventEmitter<OpenTitle> for Tonight {}

impl Tonight {
    pub fn new(core: Core, images: Entity<ImageCache>, cx: &mut Context<Self>) -> Self {
        cx.observe(&images, |_, _, cx| cx.notify()).detach();
        Self {
            core,
            images,
            minutes: 120,
            mood: Mood::default(),
            generation: 0,
            state: State::Loading,
        }
    }

    /// The answer, once there is one (`--shell-check`).
    pub fn answer(&self) -> Option<Result<&Answer, &SharedString>> {
        match &self.state {
            State::Loading => None,
            State::Ready(answer) => Some(Ok(answer)),
            State::Failed(error) => Some(Err(error)),
        }
    }

    /// Asks KINO for ideas again (the screen opened, a choice changed).
    pub fn refresh(&mut self, cx: &mut Context<Self>) {
        self.generation += 1;
        let generation = self.generation;
        self.state = State::Loading;
        cx.notify();
        let page = self.core.tonight(self.minutes, self.mood.param());
        cx.spawn(async move |this, cx| {
            let page = page.await;
            let _ = this.update(cx, |tonight, cx| {
                if tonight.generation != generation {
                    return;
                }
                tonight.state = match page {
                    Ok(page) => State::Ready(Answer::new(&page)),
                    Err(error) => State::Failed(error.into()),
                };
                cx.notify();
            });
        })
        .detach();
    }

    /// The user's answer about an idea, then new ideas.
    fn feedback(&mut self, title: &Title, action: &'static str, cx: &mut Context<Self>) {
        let sent = self.core.tonight_feedback(title.id.clone(), action);
        cx.spawn(async move |this, cx| {
            sent.await;
            let _ = this.update(cx, |tonight, cx| tonight.refresh(cx));
        })
        .detach();
    }

    fn choice(&self, index: usize, choice: &Choice, cx: &mut Context<Self>) -> impl IntoElement {
        let title = choice.title.clone();
        let poster = title
            .poster
            .as_ref()
            .and_then(|url| self.images.update(cx, |images, cx| images.get(url, cx)));
        let art = div()
            .w(px(100.0))
            .h(px(150.0))
            .flex_none()
            .rounded(theme::RADIUS_SM)
            .overflow_hidden()
            .bg(theme::surface_2());
        let art = match poster {
            Some(poster) => art.child(img(poster).size_full()),
            None => art,
        };
        let button = |id: String, label: &'static str| {
            div()
                .id(SharedString::from(id))
                .px(theme::SPACE_3)
                .py(theme::SPACE_1)
                .rounded_full()
                .border_1()
                .border_color(theme::line_strong())
                .text_size(theme::FS_SM)
                .text_color(theme::text_2())
                .cursor_pointer()
                .hover(|style| style.bg(theme::surface_2()).text_color(theme::text()))
                .child(label)
        };
        let id = title.id.clone();
        let open = title.clone();
        let mut actions = div().flex().flex_wrap().gap(theme::SPACE_2).child(
            button(format!("tonight-open-{id}"), t!("core.voir_la_fiche"))
                .on_click(cx.listener(move |_, _, _, cx| cx.emit(OpenTitle(open.clone())))),
        );
        for (action, label) in [
            ("defer", t!("core.pas_ce_soir")),
            ("seen", t!("core.deja_vu")),
            ("exclude", t!("core.ca_ne_m_interesse_pas")),
        ] {
            let title = title.clone();
            actions = actions.child(button(format!("tonight-{action}-{id}"), label).on_click(
                cx.listener(move |tonight, _, _, cx| tonight.feedback(&title, action, cx)),
            ));
        }
        div()
            .flex()
            .gap(theme::SPACE_4)
            .p(theme::SPACE_4)
            .rounded(theme::RADIUS)
            .bg(theme::surface_1())
            .border_1()
            .border_color(theme::line())
            .child(art)
            .child(
                div()
                    .flex()
                    .flex_col()
                    .gap(theme::SPACE_2)
                    .min_w_0()
                    .child(
                        div()
                            .text_size(theme::FS_XS)
                            .text_color(theme::text_3())
                            .child(SharedString::from(t!(
                                "core.idee_x",
                                format!("{:02}", index + 1)
                            ))),
                    )
                    .child(
                        div()
                            .text_size(theme::FS_LG)
                            .font_weight(gpui::FontWeight::BOLD)
                            .child(title.name.clone()),
                    )
                    .child(
                        div()
                            .text_size(theme::FS_SM)
                            .text_color(theme::text_2())
                            .child(choice.reasons.clone()),
                    )
                    .child(actions),
            )
    }
}

/// A choice among a few, as a pill.
fn pill(id: SharedString, label: &'static str, active: bool) -> gpui::Stateful<gpui::Div> {
    let pill = div()
        .id(id)
        .px(theme::SPACE_3)
        .py(theme::SPACE_1)
        .rounded_full()
        .border_1()
        .text_size(theme::FS_SM)
        .cursor_pointer()
        .child(label);
    if active {
        pill.bg(theme::text())
            .border_color(theme::text())
            .text_color(theme::on_light())
    } else {
        pill.border_color(theme::line_strong())
            .text_color(theme::text_2())
            .hover(|style| style.bg(theme::surface_2()).text_color(theme::text()))
    }
}

impl Render for Tonight {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let label = |text: &'static str| {
            div()
                .w(px(140.0))
                .flex_none()
                .text_size(theme::FS_SM)
                .text_color(theme::text_3())
                .child(text)
        };
        let durations = DURATIONS
            .iter()
            .map(|&(minutes, text)| {
                pill(
                    SharedString::from(format!("tonight-min-{minutes}")),
                    text,
                    minutes == self.minutes,
                )
                .on_click(cx.listener(move |tonight, _, _, cx| {
                    tonight.minutes = minutes;
                    tonight.refresh(cx);
                }))
            })
            .collect::<Vec<_>>();
        let moods = Mood::ALL
            .into_iter()
            .map(|mood| {
                pill(
                    SharedString::from(format!("tonight-mood-{}", mood.param())),
                    mood.label(),
                    mood == self.mood,
                )
                .on_click(cx.listener(move |tonight, _, _, cx| {
                    tonight.mood = mood;
                    tonight.refresh(cx);
                }))
            })
            .collect::<Vec<_>>();
        let again = pill("tonight-again".into(), t!("page.d_autres_idees"), true)
            .on_click(cx.listener(|tonight, _, _, cx| tonight.refresh(cx)));

        let (status, choices): (SharedString, Vec<gpui::AnyElement>) = match &self.state {
            State::Loading => (t!("core.recherche_dans_ma_liste").into(), Vec::new()),
            State::Failed(error) => (error.clone(), Vec::new()),
            State::Ready(answer) => {
                let answer = answer.clone();
                (
                    answer.status(),
                    answer
                        .choices
                        .iter()
                        .enumerate()
                        .map(|(i, c)| self.choice(i, c, cx).into_any_element())
                        .collect(),
                )
            }
        };

        div()
            .id("tonight")
            .size_full()
            .overflow_y_scroll()
            .p(theme::SPACE_6)
            .flex()
            .flex_col()
            .gap(theme::SPACE_4)
            .max_w(px(860.0))
            .child(
                div()
                    .text_size(theme::FS_XL)
                    .font_weight(gpui::FontWeight::BOLD)
                    .child(t!("page.ce_soir")),
            )
            .child(
                div()
                    .text_size(theme::FS_SM)
                    .text_color(theme::text_2())
                    .child(t!("page.jusqu_a_trois_idees_selon_votre_temps")),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(theme::SPACE_2)
                    .child(label(t!("page.temps_disponible")))
                    .children(durations),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(theme::SPACE_2)
                    .child(label(t!("page.envie")))
                    .children(moods)
                    .child(div().w(theme::SPACE_4))
                    .child(again),
            )
            .child(
                div()
                    .text_size(theme::FS_SM)
                    .text_color(theme::text_3())
                    .child(status),
            )
            .children(choices)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn page(choices: Vec<Value>, empty_reason: &'static str) -> kino::tonight::Page {
        kino::tonight::Page {
            choices,
            coverage: json!({"partial": false}),
            empty_reason,
            review: Vec::new(),
        }
    }

    #[test]
    fn an_idea_keeps_its_title_and_its_reasons() {
        let answer = Answer::new(&page(
            vec![
                json!({"item": {"id": "tt1", "name": "Heat", "type": "movie"},
                        "reasons": ["Dure 2 h 50", "Policier"]}),
            ],
            "",
        ));
        assert_eq!(answer.choices.len(), 1);
        assert_eq!(answer.choices[0].title.name.as_ref(), "Heat");
        assert_eq!(answer.choices[0].reasons.as_ref(), "Dure 2 h 50 · Policier");
        assert!(!answer.status().is_empty());
    }

    #[test]
    fn no_idea_says_why() {
        let empty = Answer::new(&page(Vec::new(), "empty_watchlist"));
        let other = Answer::new(&page(Vec::new(), "no_match"));
        assert!(empty.choices.is_empty());
        assert_ne!(empty.status(), other.status());
    }

    #[test]
    fn moods_and_durations_are_the_web_s() {
        let moods: Vec<_> = Mood::ALL.iter().map(|m| m.param()).collect();
        assert!(moods.iter().all(|m| kino::tonight::MOODS.contains(m)));
        assert!(DURATIONS.iter().all(|(m, _)| (15..=600).contains(m)));
    }
}
