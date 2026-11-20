//! Settings (phase G5): the interface's language (kept in KINO's settings),
//! the debrid service and its key (with the account's state), the audio
//! language and the quality wanted, read and saved through `/api/config`
//! as the web's settings page does.

use gpui::{Context, Entity, SharedString, Window, div, prelude::*};
use serde_json::{Value, json};

use crate::core::Core;
use crate::i18n::Language;
use crate::state::AppState;
use crate::text_input::TextInput;
use crate::theme;

/// The debrid services KINO knows: (setting value, name).
pub const PROVIDERS: [(&str, &str); 6] = [
    ("realdebrid", "Real-Debrid"),
    ("alldebrid", "AllDebrid"),
    ("torbox", "TorBox"),
    ("premiumize", "Premiumize"),
    ("debridlink", "Debrid-Link"),
    ("megadebrid", "Mega-Debrid"),
];

/// The audio languages: (setting value, label).
pub const AUDIO: [(&str, &str); 4] = [
    ("vf", "VF"),
    ("vostfr", "VOSTFR"),
    ("vo", "VO"),
    ("multi", "MULTI"),
];

/// The qualities offered: (setting value, label).
pub const QUALITIES: [(&str, &str); 4] = [
    ("4k", "4K"),
    ("1080p", "1080p"),
    ("720p", "720p"),
    ("480p", "480p"),
];

/// What `/api/config` says, as this screen shows it.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Config {
    pub provider: SharedString,
    pub has_token: bool,
    /// "premium", "free", "invalid", "unconfigured"…
    pub user_status: SharedString,
    /// The account's name, when the service gives it.
    pub user: Option<SharedString>,
    pub pref_lang: SharedString,
    pub pref_quality: SharedString,
}

impl Config {
    pub fn new(config: &Value) -> Self {
        let text = |key: &str| -> SharedString {
            config[key].as_str().unwrap_or_default().to_string().into()
        };
        Self {
            provider: text("debrid_provider"),
            has_token: config["has_token"] == true,
            user_status: text("user_status"),
            user: config["user"]["username"]
                .as_str()
                .filter(|u| !u.is_empty())
                .map(|u| u.to_string().into()),
            pref_lang: text("pref_lang"),
            pref_quality: text("pref_quality"),
        }
    }

    /// The account line: "Compte : name (premium)".
    pub fn account(&self) -> Option<SharedString> {
        if !self.has_token {
            return None;
        }
        let who = self.user.as_ref().map_or("", |u| u.as_ref());
        Some(format!("{} : {who} ({})", t!("page.compte"), self.user_status).into())
    }
}

pub struct Settings {
    core: Core,
    config: Option<Config>,
    key: Entity<TextInput>,
    /// The line under « Enregistrer ».
    saved: Option<SharedString>,
}

impl Settings {
    pub fn new(core: Core, cx: &mut Context<Self>) -> Self {
        let key = cx.new(|cx| TextInput::new(t!("page.cle_api"), cx));
        Self {
            core,
            config: None,
            key,
            saved: None,
        }
    }

    /// What `/api/config` said, once read (`--shell-check`).
    pub fn config(&self) -> Option<&Config> {
        self.config.as_ref()
    }

    /// Reads the settings again (the screen opened, a choice saved).
    pub fn refresh(&mut self, cx: &mut Context<Self>) {
        let answer = self.core.api("GET", "/api/config".into(), None);
        cx.spawn(async move |this, cx| {
            let (_, config) = answer.await;
            let _ = this.update(cx, |settings, cx| {
                settings.config = Some(Config::new(&config));
                cx.notify();
            });
        })
        .detach();
    }

    /// Saves `changes` (`/api/config`), then reads the settings again.
    fn save(&mut self, changes: Value, cx: &mut Context<Self>) {
        let saved = self.core.api("POST", "/api/config".into(), Some(changes));
        cx.spawn(async move |this, cx| {
            let (status, _) = saved.await;
            let _ = this.update(cx, |settings, cx| {
                settings.saved = Some(if status == 200 {
                    t!("settings.enregistre").into()
                } else {
                    format!("HTTP {status}").into()
                });
                settings.refresh(cx);
            });
        })
        .detach();
    }

    fn save_key(&mut self, cx: &mut Context<Self>) {
        let key = self.key.read(cx).text().trim().to_string();
        if key.is_empty() {
            return;
        }
        let provider = self
            .config
            .as_ref()
            .map_or("realdebrid".to_string(), |c| c.provider.to_string());
        self.save(json!({"debrid_provider": provider, "rd_token": key}), cx);
    }
}

fn pill(id: SharedString, label: &'static str, active: bool) -> gpui::Stateful<gpui::Div> {
    let pill = div()
        .id(id)
        .px(theme::SPACE_4)
        .py(theme::SPACE_2)
        .rounded(theme::RADIUS_SM)
        .border_1()
        .text_size(theme::FS_MD)
        .cursor_pointer()
        .child(label);
    if active {
        pill.bg(theme::text())
            .text_color(theme::on_light())
            .border_color(theme::text())
    } else {
        pill.border_color(theme::line_strong())
            .text_color(theme::text_2())
            .hover(|style| style.bg(theme::surface_2()))
    }
}

fn section(title: &'static str) -> gpui::Div {
    div()
        .pt(theme::SPACE_4)
        .text_size(theme::FS_SM)
        .text_color(theme::text_3())
        .child(title)
}

impl Render for Settings {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let current = AppState::language(cx);
        let languages: Vec<_> = Language::ALL
            .into_iter()
            .map(|language| {
                pill(
                    language.native_name().into(),
                    language.native_name(),
                    language == current,
                )
                .on_click(cx.listener(move |settings, _, _, cx| {
                    AppState::set_language(language, cx);
                    settings.save(json!({"ui_language": language.code()}), cx);
                }))
            })
            .collect();
        let config = self.config.clone().unwrap_or_default();
        let choices = |values: &'static [(&'static str, &'static str)],
                       chosen: &SharedString,
                       field: &'static str,
                       cx: &mut Context<Self>| {
            values
                .iter()
                .map(|&(value, label)| {
                    pill(
                        format!("{field}-{value}").into(),
                        label,
                        chosen.as_ref() == value,
                    )
                    .on_click(cx.listener(move |settings, _, _, cx| {
                        settings.save(json!({ field: value }), cx)
                    }))
                })
                .collect::<Vec<_>>()
        };
        let providers = choices(&PROVIDERS, &config.provider, "debrid_provider", cx);
        let audio = choices(&AUDIO, &config.pref_lang, "pref_lang", cx);
        let qualities = choices(&QUALITIES, &config.pref_quality, "pref_quality", cx);
        let save = pill(
            "settings-save-key".into(),
            t!("settings.enregistrer_la_cle"),
            true,
        )
        .on_click(cx.listener(|settings, _, _, cx| settings.save_key(cx)));
        let mut page = div()
            .id("settings")
            .size_full()
            .overflow_y_scroll()
            .p(theme::SPACE_6)
            .flex()
            .flex_col()
            .gap(theme::SPACE_3)
            .child(
                div()
                    .text_size(theme::FS_XL)
                    .font_weight(gpui::FontWeight::BOLD)
                    .child(t!("page.parametres")),
            )
            .child(section(t!("page.langue")))
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .gap(theme::SPACE_2)
                    .children(languages),
            )
            .child(section(t!("page.debridage_et_lecture_instantanee_via")))
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .gap(theme::SPACE_2)
                    .children(providers),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(theme::SPACE_3)
                    .child(
                        div()
                            .w(gpui::px(420.0))
                            .h(gpui::px(38.0))
                            .px(theme::SPACE_4)
                            .flex()
                            .items_center()
                            .rounded(theme::RADIUS_SM)
                            .bg(theme::surface_2())
                            .border_1()
                            .border_color(theme::line())
                            .child(self.key.clone()),
                    )
                    .child(save),
            );
        if let Some(account) = config.account() {
            page = page.child(
                div()
                    .text_size(theme::FS_SM)
                    .text_color(theme::text_2())
                    .child(account),
            );
        }
        page = page
            .child(section(t!("page.vf_multi_vostfr")))
            .child(div().flex().flex_wrap().gap(theme::SPACE_2).children(audio))
            .child(section(t!("page.qualite")))
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .gap(theme::SPACE_2)
                    .children(qualities),
            );
        if let Some(saved) = &self.saved {
            page = page.child(
                div()
                    .text_size(theme::FS_SM)
                    .text_color(theme::text_3())
                    .child(saved.clone()),
            );
        }
        page
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_config_view_reads_the_route_s_answer() {
        let config = Config::new(&json!({
            "debrid_provider": "torbox", "has_token": true, "user_status": "premium",
            "user": {"username": "neo"}, "pref_lang": "vostfr", "pref_quality": "1080p"
        }));
        assert_eq!(config.provider.as_ref(), "torbox");
        let account = config.account().unwrap();
        assert!(
            account.contains("neo") && account.contains("premium"),
            "{account}"
        );
        assert!(Config::new(&json!({})).account().is_none());
    }

    #[test]
    fn the_choices_are_kino_s_setting_values() {
        for (value, _) in PROVIDERS {
            assert!(
                [
                    "realdebrid",
                    "alldebrid",
                    "torbox",
                    "premiumize",
                    "debridlink",
                    "megadebrid"
                ]
                .contains(&value)
            );
        }
        assert!(
            AUDIO
                .iter()
                .all(|(v, _)| ["vf", "vostfr", "vo", "multi"].contains(v))
        );
        assert!(QUALITIES.iter().all(|(v, _)| !v.is_empty()));
    }

    #[test]
    fn every_language_has_its_code() {
        for language in Language::ALL {
            assert_eq!(Language::from_code(language.code()), Some(language));
        }
    }
}
