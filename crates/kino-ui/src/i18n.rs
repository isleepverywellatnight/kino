//! Translations (`t!`), from the web version's catalogs (`build.rs`).
//!
//! `t!("fiche.aucun_synopsis_disponible")` is the text in the current
//! language; `t!("tabs.x_ep", 12)` fills its `{}` (or `{1}`, `{2}`… when a
//! language reorders them). A key fr.js does not have does not compile. A text a
//! language lacks shows in English, and in French where English lacks it.

use std::fmt::Display;
use std::sync::atomic::{AtomicUsize, Ordering};

include!(concat!(env!("OUT_DIR"), "/i18n_table.rs"));

/// The interface's languages, in the table's order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Language {
    French,
    English,
    German,
    Spanish,
    Italian,
    Portuguese,
    Russian,
    Japanese,
    Chinese,
}

impl Language {
    pub const ALL: [Language; 9] = [
        Language::French,
        Language::English,
        Language::German,
        Language::Spanish,
        Language::Italian,
        Language::Portuguese,
        Language::Russian,
        Language::Japanese,
        Language::Chinese,
    ];

    /// The language of a code like "fr", "pt-BR" or "zh_CN.UTF-8".
    pub fn from_code(code: &str) -> Option<Self> {
        let short = code.get(..2)?.to_ascii_lowercase();
        let index = ["fr", "en", "de", "es", "it", "pt", "ru", "ja", "zh"]
            .iter()
            .position(|c| *c == short)?;
        Some(Self::ALL[index])
    }
}

static CURRENT: AtomicUsize = AtomicUsize::new(1);

pub fn set_language(language: Language) {
    CURRENT.store(language as usize, Ordering::Relaxed);
}

pub fn language() -> Language {
    Language::ALL[CURRENT.load(Ordering::Relaxed)]
}

/// The language of the system (`LC_ALL`, `LC_MESSAGES`, `LANG`), else English.
pub fn system_language() -> Language {
    ["LC_ALL", "LC_MESSAGES", "LANG"]
        .iter()
        .filter_map(|name| std::env::var(name).ok())
        .find_map(|value| Language::from_code(&value))
        .unwrap_or(Language::English)
}

/// The text of key number `index` in the current language.
pub fn text(index: usize) -> &'static str {
    text_in(index, language())
}

/// The text of key number `index` in `language`.
pub fn text_in(index: usize, language: Language) -> &'static str {
    let row = &TEXTS[index];
    row[language as usize]
        .or(row[Language::English as usize])
        .or(row[Language::French as usize])
        .unwrap_or("")
}

/// The text of key number `index` with its `{}` (in order) or `{1}`, `{2}`…
/// (by position) replaced by `args`.
// Used by `t!` with arguments; the G0 screens have none yet.
#[allow(dead_code)]
pub fn fill(index: usize, args: &[&dyn Display]) -> String {
    fill_template(text(index), args)
}

#[allow(dead_code)]
fn fill_template(template: &str, args: &[&dyn Display]) -> String {
    let mut out = String::with_capacity(template.len() + 16);
    let mut next = 0;
    let mut rest = template;
    while let Some(open) = rest.find('{') {
        out.push_str(&rest[..open]);
        let after = &rest[open + 1..];
        match after.find('}') {
            Some(close) if after[..close].chars().all(|c| c.is_ascii_digit()) => {
                let position = if close == 0 {
                    next += 1;
                    next - 1
                } else {
                    after[..close]
                        .parse::<usize>()
                        .unwrap_or(1)
                        .saturating_sub(1)
                };
                if let Some(arg) = args.get(position) {
                    out.push_str(&arg.to_string());
                }
                rest = &after[close + 1..];
            }
            _ => {
                out.push('{');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(name: &str) -> usize {
        let fr = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../web/js/i18n/fr.js"
        ))
        .unwrap();
        let start = fr.find("({").unwrap() + 1;
        let end = fr.rfind("})").unwrap() + 1;
        let keys: std::collections::BTreeMap<String, String> =
            serde_json::from_str(&fr[start..end]).unwrap();
        keys.keys().position(|k| k == name).unwrap()
    }

    #[test]
    fn t_reads_the_current_language() {
        assert_eq!(KEYS, TEXTS.len());
        set_language(Language::French);
        assert_eq!(t!("anime.a_voir"), "À voir");
        set_language(Language::English);
        assert_eq!(t!("anime.a_voir"), "To watch");
        assert_eq!(t!("tabs.x_vus", 40), "40 % watched");
    }

    #[test]
    fn each_language_has_its_text() {
        let index = key("anime.a_voir");
        assert_eq!(text_in(index, Language::French), "À voir");
        assert_eq!(text_in(index, Language::English), "To watch");
        assert_ne!(text_in(index, Language::Japanese), "To watch");
    }

    #[test]
    fn placeholders_fill_in_order_and_by_position() {
        assert_eq!(fill_template("{} of {}", &[&1, &3]), "1 of 3");
        assert_eq!(fill_template("{2} then {1}", &[&"a", &"b"]), "b then a");
        assert_eq!(fill_template("{not a slot}", &[&1]), "{not a slot}");
    }

    #[test]
    fn a_missing_text_falls_back_to_english_then_french() {
        for (index, row) in TEXTS.iter().enumerate() {
            let german = text_in(index, Language::German);
            match (row[2], row[1]) {
                (Some(own), _) => assert_eq!(german, own),
                (None, Some(english)) => assert_eq!(german, english),
                (None, None) => assert_eq!(german, row[0].unwrap()),
            }
        }
    }

    #[test]
    fn system_codes() {
        assert_eq!(
            Language::from_code("pt_BR.UTF-8"),
            Some(Language::Portuguese)
        );
        assert_eq!(Language::from_code("C"), None);
    }
}
