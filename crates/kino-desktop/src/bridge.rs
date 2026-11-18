//! The page's calls to the window (`window.pywebview.api.*`), typed: each one
//! read and checked once, as it arrives, then carried as a `BridgeCall`.

use serde::Deserialize;
use serde_json::{Map, Value, json};

/// What the page posts: `{id, name, args}`.
#[derive(Debug, Default, Deserialize, PartialEq)]
pub struct Message {
    #[serde(default)]
    pub id: u64,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub args: Vec<Value>,
}

impl Message {
    /// The page's message; None when it is not one.
    pub fn read(body: &str) -> Option<Self> {
        serde_json::from_str(body).ok()
    }
}

/// An edge or a corner the window is resized from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Edge {
    Left,
    Right,
    Top,
    Bottom,
    TopLeft,
    TopRight,
    BottomLeft,
    BottomRight,
}

impl Edge {
    fn parse(name: &str) -> Option<Self> {
        Some(match name.replace('_', "-").as_str() {
            "left" => Self::Left,
            "right" => Self::Right,
            "top" => Self::Top,
            "bottom" => Self::Bottom,
            "topleft" | "top-left" => Self::TopLeft,
            "topright" | "top-right" => Self::TopRight,
            "bottomleft" | "bottom-left" => Self::BottomLeft,
            "bottomright" | "bottom-right" => Self::BottomRight,
            _ => return None,
        })
    }
}

/// The measures of a playback report that may leave the computer: numbers and
/// switches only, nothing about the title or the source.
const DIAGNOSTIC_FIELDS: [&str; 15] = [
    "schema",
    "position",
    "duration",
    "paused",
    "muted",
    "volume",
    "readyState",
    "networkState",
    "mediaError",
    "compatibleStream",
    "audioTrackCount",
    "selectedAudioIndex",
    "subtitleCueCount",
    "subtitleDelay",
    "sourceAttempts",
];

pub const UNKNOWN: &str = "Pas encore disponible dans cette fenêtre.";

/// One call of the page, its arguments checked.
#[derive(Debug, Clone, PartialEq)]
pub enum BridgeCall {
    Minimize,
    ToggleMaximize,
    StartWindowDrag,
    StartResize(Edge),
    IsFullscreen,
    ToggleFullscreen,
    EnterFullscreen,
    ExitFullscreen,
    Close,
    /// Where and how large (logical points); what is missing stays.
    SetBounds {
        x: Option<f64>,
        y: Option<f64>,
        width: Option<f64>,
        height: Option<f64>,
    },
    GetState,
    Platform,
    ChooseMangaFolder,
    /// The playback report, already reduced to its allowed measures.
    ExportPlaybackDiagnostic(Map<String, Value>),
    UpdateStatus,
    SetUpdateAutoCheck(bool),
    OpenReleasePage,
    CheckForUpdates,
    ChooseUpdatePackage,
    InstallUpdate,
    RollbackUpdate,
}

impl BridgeCall {
    /// The call for a name and its arguments, or the message for the page.
    pub fn parse(name: &str, args: &[Value]) -> Result<Self, String> {
        let number = |i: usize| args.get(i).and_then(Value::as_f64);
        Ok(match name {
            "minimize" => Self::Minimize,
            "toggle_maximize" => Self::ToggleMaximize,
            "start_window_drag" => Self::StartWindowDrag,
            "start_resize" => Self::StartResize(
                args.first()
                    .and_then(Value::as_str)
                    .and_then(Edge::parse)
                    .ok_or("Bord inconnu.")?,
            ),
            "is_fullscreen" => Self::IsFullscreen,
            "toggle_fullscreen" => Self::ToggleFullscreen,
            "enter_fullscreen" => Self::EnterFullscreen,
            "exit_fullscreen" => Self::ExitFullscreen,
            "close" => Self::Close,
            "set_bounds" => Self::SetBounds {
                x: number(0),
                y: number(1),
                width: number(2),
                height: number(3),
            },
            "resize" => Self::SetBounds {
                x: None,
                y: None,
                width: number(0),
                height: number(1),
            },
            "get_state" => Self::GetState,
            "platform" => Self::Platform,
            "choose_manga_folder" => Self::ChooseMangaFolder,
            "export_playback_diagnostic" => {
                let report = args
                    .first()
                    .and_then(Value::as_object)
                    .ok_or("Rapport invalide")?;
                let mut safe: Map<String, Value> = report
                    .iter()
                    .filter(|(k, v)| {
                        DIAGNOSTIC_FIELDS.contains(&k.as_str())
                            && (v.is_null()
                                || v.is_boolean()
                                || v.as_f64().is_some_and(f64::is_finite))
                    })
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect();
                safe.insert("player".into(), json!("KINO internal"));
                Self::ExportPlaybackDiagnostic(safe)
            }
            "update_status" => Self::UpdateStatus,
            "set_update_auto_check" => Self::SetUpdateAutoCheck(args.first() == Some(&json!(true))),
            "open_release_page" => Self::OpenReleasePage,
            "check_for_updates" => Self::CheckForUpdates,
            "choose_update_package" => Self::ChooseUpdatePackage,
            "install_update" => Self::InstallUpdate,
            "rollback_update" => Self::RollbackUpdate,
            _ => return Err(UNKNOWN.into()),
        })
    }

    /// Handled with the updater (its answers may come later).
    pub fn is_update(&self) -> bool {
        matches!(
            self,
            Self::UpdateStatus
                | Self::SetUpdateAutoCheck(_)
                | Self::OpenReleasePage
                | Self::CheckForUpdates
                | Self::ChooseUpdatePackage
                | Self::InstallUpdate
                | Self::RollbackUpdate
        )
    }
}

/// The script that hands the answer to call `id` back to the page.
pub fn reply_script(id: u64, result: &Result<Value, String>) -> String {
    let (ok, value) = match result {
        Ok(v) => (true, v.clone()),
        Err(e) => (false, json!(e)),
    };
    format!("window.__kinoReply({id}, {ok}, {value})")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_pages_messages() {
        assert_eq!(
            Message::read(r#"{"id": 7, "name": "set_bounds", "args": [20, 20, 900, 620]}"#),
            Some(Message {
                id: 7,
                name: "set_bounds".into(),
                args: vec![json!(20), json!(20), json!(900), json!(620)]
            })
        );
        assert_eq!(Message::read(r#"{"name": "close"}"#).unwrap().id, 0);
        assert_eq!(Message::read("not json"), None);
        assert_eq!(Message::read(r#"{"id": "x"}"#), None);
    }

    #[test]
    fn each_call_with_its_arguments() {
        let call = |name: &str, args: Value| BridgeCall::parse(name, args.as_array().unwrap());
        assert_eq!(call("minimize", json!([])), Ok(BridgeCall::Minimize));
        assert_eq!(
            call("start_resize", json!(["top_left"])),
            Ok(BridgeCall::StartResize(Edge::TopLeft))
        );
        assert_eq!(
            call("start_resize", json!(["bottom-right"])),
            Ok(BridgeCall::StartResize(Edge::BottomRight))
        );
        assert_eq!(
            call("start_resize", json!(["middle"])),
            Err("Bord inconnu.".into())
        );
        assert_eq!(call("start_resize", json!([])), Err("Bord inconnu.".into()));
        assert_eq!(
            call("set_bounds", json!([20, 20.5, 900, "620"])),
            Ok(BridgeCall::SetBounds {
                x: Some(20.0),
                y: Some(20.5),
                width: Some(900.0),
                height: None
            }),
            "a number as text is not a number"
        );
        assert_eq!(
            call("resize", json!([800, 600])),
            Ok(BridgeCall::SetBounds {
                x: None,
                y: None,
                width: Some(800.0),
                height: Some(600.0)
            })
        );
        assert_eq!(
            call("set_update_auto_check", json!([true])),
            Ok(BridgeCall::SetUpdateAutoCheck(true))
        );
        assert_eq!(
            call("set_update_auto_check", json!(["true"])),
            Ok(BridgeCall::SetUpdateAutoCheck(false))
        );
        assert_eq!(call("eval", json!(["x"])), Err(UNKNOWN.into()));
        assert!(call("install_update", json!([])).unwrap().is_update());
        assert!(!call("close", json!([])).unwrap().is_update());
    }

    #[test]
    fn a_diagnostic_keeps_numbers_only() {
        let call = BridgeCall::parse(
            "export_playback_diagnostic",
            &[
                json!({"position": 12.5, "paused": true, "title": "Dune", "url": "https://x",
                     "volume": "80", "mediaError": null}),
            ],
        );
        assert_eq!(
            call,
            Ok(BridgeCall::ExportPlaybackDiagnostic(
                json!({"position": 12.5, "paused": true, "mediaError": null, "player": "KINO internal"})
                    .as_object()
                    .unwrap()
                    .clone()
            ))
        );
        assert_eq!(
            BridgeCall::parse("export_playback_diagnostic", &[json!([1])]),
            Err("Rapport invalide".into())
        );
    }

    #[test]
    fn answers_as_a_script() {
        assert_eq!(
            reply_script(3, &Ok(json!({"a": "é\"<"}))),
            r#"window.__kinoReply(3, true, {"a":"é\"<"})"#
        );
        assert_eq!(
            reply_script(4, &Err("Bord inconnu.".into())),
            r#"window.__kinoReply(4, false, "Bord inconnu.")"#
        );
    }
}
