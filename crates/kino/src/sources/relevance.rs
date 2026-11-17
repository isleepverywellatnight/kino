//! Which releases belong to the work asked for: not a homonym, not a spin-off,
//! not another year, and a plausible size for what it claims to be.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;
use unicode_normalization::UnicodeNormalization;
use unicode_normalization::char::canonical_combining_class;

use super::release::{self, TRASH};

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect(pattern)
}

static GB: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)(\d+(?:\.\d+)?)\s*(?:GB|GiB)"));
static MB: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)(\d+(?:\.\d+)?)\s*(?:MB|MiB)"));
static YEARS: LazyLock<Regex> = LazyLock::new(|| re(r"\b(19[2-9]\d|20[0-3]\d)\b"));
static WORDS: LazyLock<Regex> = LazyLock::new(|| re(r"[a-z0-9]+"));
static BRACKETS: LazyLock<Regex> = LazyLock::new(|| re(r"\[.*?\]|\(.*?\)"));
static SPLIT: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)\b(s\d{1,2}(?:e\d{1,2})?|season\s*\d+|ep\s*\d+|e\d{2}|(19\d\d|20\d\d)|2160p|1080p|720p|480p|bluray|web-dl|webrip|hdtv|dvdrip|remux)\b",
    )
});
static SEQUEL: LazyLock<Regex> = LazyLock::new(|| re(r"^(?:\d{1,2}|ii|iii|iv|vi|vii|viii|ix)$"));
static SEPARATORS: LazyLock<Regex> = LazyLock::new(|| re(r"[\.\-_]"));
static NOT_ALNUM: LazyLock<Regex> = LazyLock::new(|| re(r"[^a-z0-9]"));
static LEAD_TAG: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*\[[^\]]+\]\s*"));
static LEAD_GROUP: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*\([^\)]+\)\s*"));
static DOTS: LazyLock<Regex> = LazyLock::new(|| re(r"[\._]"));
static YEAR_IN_BRACKETS: LazyLock<Regex> =
    LazyLock::new(|| re(r"[\(\[\{]\s*(?:19\d\d|20\d\d)\s*[\)\]\}]"));
static FROM_MARKER: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)\s+(?:s\d{1,2}\s*e\d{1,3}|s\d{1,2}|e\d{1,3}|\d{1,2}x\d{1,2}|-\s*s\d{1,2}|-\s*\d{1,4}|\b(?:19\d\d|20\d\d)\b).*$",
    )
});
static EDGES: LazyLock<Regex> = LazyLock::new(|| re(r"^[\s\-_:]+|[\s\-_:]+$"));
static SPACES: LazyLock<Regex> = LazyLock::new(|| re(r"\s+"));

const IGNORE_WORDS: &[&str] = &[
    "naoki",
    "urasawa",
    "urasawas",
    "anime",
    "series",
    "complete",
    "batch",
    "dual",
    "audio",
    "sub",
    "subs",
    "raw",
    "raws",
    "readnfo",
    "upscale",
    "original",
    "color",
    "uncut",
    "remastered",
    "edition",
    "rip",
    "the",
    "a",
    "an",
    "s",
    "season",
    "ep",
    "episode",
    "vol",
    "volume",
    "ova",
    "ona",
    "special",
    "specials",
    "movie",
    "film",
];
const OTHER_SHOW_WORDS: &[&str] = &[
    "re",
    "monarch",
    "legacy",
    "ed",
    "gein",
    "dahmer",
    "jeffrey",
    "work",
    "works",
    "high",
    "musume",
    "florence",
    "truck",
    "trucks",
    "hunter",
    "hunters",
    "university",
    "inc",
    "strike",
    "rancher",
    "chronicles",
    "garage",
    "legend",
    "legends",
    "island",
    "school",
    "academy",
    "quest",
    "wars",
    "war",
];

fn other_show_words() -> impl Iterator<Item = &'static str> {
    OTHER_SHOW_WORDS.iter().copied()
}

fn number_of(value: &Value) -> f64 {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0.0)
}

/// Drops cinema recordings and releases whose size cannot hold what they claim.
pub fn plausible_size(item: &Value, media_type: &str, runtime_minutes: Option<f64>) -> bool {
    let title = item["title"].as_str().unwrap_or("");
    if TRASH.is_match(title) {
        return false;
    }
    let mut size = number_of(&item["size_gb"]);
    let meta = item["meta"].as_str().unwrap_or("");
    if size == 0.0 {
        if let Some(c) = GB.captures(meta) {
            size = c[1].parse().unwrap_or(0.0);
        } else if let Some(c) = MB.captures(meta) {
            size = c[1].parse::<f64>().unwrap_or(0.0) / 1024.0;
        }
    }
    if size == 0.0 {
        return true;
    }
    let parsed = match &item["parsed_details"] {
        Value::Object(o) if !o.is_empty() => Value::Object(o.clone()),
        _ => release::details(title, meta),
    };
    let resolution = parsed["resolution"].as_str().unwrap_or("1080p");
    let source = parsed["source"].as_str().unwrap_or("WEB-DL");
    let rm = runtime_minutes.unwrap_or(0.0);
    if media_type != "series" {
        if size < 0.25 {
            return false;
        }
        match resolution {
            "4K" => {
                // A real 4K stream needs about 6.8 Mbit/s or more.
                let minimum = if rm > 75.0 {
                    5.2f64.max(rm * 60.0 * 6.8 / 8192.0)
                } else {
                    5.2
                };
                if size < minimum || (source == "REMUX" && size < 18.0 && (rm == 0.0 || rm > 70.0))
                {
                    return false;
                }
            }
            "1080p" => {
                let minimum = if rm > 75.0 {
                    0.9f64.max(rm * 60.0 * 1.5 / 8192.0)
                } else {
                    0.9
                };
                if size < minimum || (source == "REMUX" && size < 12.0 && (rm == 0.0 || rm > 70.0))
                {
                    return false;
                }
            }
            _ if size < 0.40 => return false,
            _ => {}
        }
        // One film does not weigh 120 GB: usually a whole saga pack.
        if size > 120.0 {
            return false;
        }
    } else if (resolution == "4K" && size < 1.4)
        || (resolution == "1080p" && size < 0.28)
        || size < 0.10
    {
        return false;
    }
    true
}

pub fn clean_title_for_comparison(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let s = LEAD_TAG.replace(text, "");
    let s = LEAD_GROUP.replace(&s, "");
    let s = DOTS.replace_all(&s, " ");
    let s = YEAR_IN_BRACKETS.replace_all(&s, " ");
    let s = FROM_MARKER.replace(&s, "");
    let s = EDGES.replace_all(&s, "");
    SPACES.replace_all(&s, " ").trim().to_lowercase()
}

pub fn release_years(text: &str) -> Vec<i64> {
    YEARS
        .captures_iter(text)
        .filter_map(|c| c[1].parse().ok())
        .collect()
}

/// Years in a release name, minus those of the work's own title (« 2001: A Space Odyssey »).
pub fn years_outside_title(release_title: &str, titles: &[&str]) -> Vec<i64> {
    let own: HashSet<i64> = titles.iter().flat_map(|t| release_years(t)).collect();
    release_years(release_title)
        .into_iter()
        .filter(|y| !own.contains(y))
        .collect()
}

fn is_word_char(c: Option<char>) -> bool {
    c.is_some_and(|c| c.is_alphanumeric() || c == '_')
}

/// Whether `needle` starts and ends on a word boundary at `at` (as `\b` would say).
fn bounded(text: &str, at: usize, needle: &str) -> bool {
    let before = text[..at].chars().next_back();
    let after = text[at + needle.len()..].chars().next();
    let first = needle.chars().next();
    let last = needle.chars().next_back();
    (is_word_char(before) != is_word_char(first) || needle.is_empty())
        && (is_word_char(last) != is_word_char(after) || needle.is_empty())
}

/// `\bword\b` without building a regular expression for each word.
fn has_word(text: &str, word: &str) -> bool {
    text.match_indices(word)
        .any(|(at, _)| bounded(text, at, word))
}

/// `\bre[\:\s\.\-_]?{title}\b` without building a regular expression each time.
fn re_prefixed(text: &str, title: &str) -> bool {
    text.match_indices("re").any(|(at, _)| {
        if is_word_char(text[..at].chars().next_back()) {
            return false;
        }
        let rest = &text[at + 2..];
        let mut starts = vec![at + 2];
        if let Some(c) = rest.chars().next()
            && (c == ':' || c.is_whitespace() || c == '.' || c == '-' || c == '_')
        {
            starts.push(at + 2 + c.len_utf8());
        }
        starts.into_iter().any(|start| {
            text[start..].starts_with(title) && {
                let end = start + title.len();
                is_word_char(title.chars().next_back()) != is_word_char(text[end..].chars().next())
            }
        })
    })
}

fn words(text: &str) -> Vec<String> {
    WORDS
        .find_iter(text)
        .map(|m| m.as_str().to_string())
        .collect()
}

fn leading_year(expected_year: &str) -> Option<i64> {
    let first: String = expected_year.chars().take(4).collect();
    first.trim().parse().ok()
}

/// Whether a release's name is the work asked for (and not a homonym or spin-off).
pub fn is_release_relevant(filename: &str, expected_title: &str, expected_year: &str) -> bool {
    if expected_title.is_empty() || filename.is_empty() {
        return true;
    }
    let low_file = filename.to_lowercase();
    let cleaned = BRACKETS.replace_all(filename, " ");
    let title_years: HashSet<i64> = release_years(expected_title).into_iter().collect();
    let split_at = SPLIT
        .captures_iter(&cleaned)
        .find(|c| {
            !c.get(2).is_some_and(|y| {
                y.as_str()
                    .parse::<i64>()
                    .is_ok_and(|y| title_years.contains(&y))
            })
        })
        .map(|c| c.get(0).map_or(0, |m| m.start()));
    let title_part = &cleaned[..split_at.unwrap_or(cleaned.len())];
    let norm_title = SEPARATORS
        .replace_all(title_part, " ")
        .trim()
        .to_lowercase();

    // A numbered sequel is another film: « Toy.Story.2 » is not « Toy Story ».
    let title_tokens = words(&norm_title);
    let expected_tokens = words(&expected_title.to_lowercase());
    if !expected_tokens.is_empty()
        && title_tokens.len() > expected_tokens.len()
        && title_tokens[..expected_tokens.len()] == expected_tokens[..]
        && SEQUEL.is_match(&title_tokens[expected_tokens.len()])
    {
        return false;
    }
    let primary = expected_title
        .split(':')
        .next()
        .unwrap_or("")
        .split(" - ")
        .next()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    let full = expected_title.trim().to_lowercase();
    let norm_clean = NOT_ALNUM.replace_all(&norm_title, "").into_owned();

    // « Re:Monster » is not « Monster », unless the title asked for starts with « Re ».
    if !primary.starts_with("re") && re_prefixed(&low_file, &primary) {
        return false;
    }
    let words_title: HashSet<String> = words(&norm_title).into_iter().collect();
    let words_expected: HashSet<String> = words(&primary).into_iter().chain(words(&full)).collect();
    let extra: HashSet<&String> = words_title
        .iter()
        .filter(|w| !words_expected.contains(*w) && !IGNORE_WORDS.contains(&w.as_str()))
        .collect();
    if extra
        .iter()
        .any(|w| other_show_words().any(|o| o == w.as_str()))
    {
        return false;
    }
    if words_expected.len() == 1 && !extra.is_empty() {
        return false;
    }
    if words_expected.len() <= 2 {
        for word in other_show_words() {
            let present = has_word(&low_file, word);
            if present
                && (["monarch", "gein", "dahmer", "florence"].contains(&word)
                    || low_file.contains(&format!("{primary} {word}"))
                    || low_file.contains(&format!("{word} {primary}"))
                    || low_file.contains(&format!("{primary}.{word}")))
            {
                return false;
            }
        }
        if primary == "monster"
            && (norm_clean == "monsters"
                || norm_clean == "monsterland"
                || low_file.contains("monsters.")
                || low_file.contains("monsters ")
                || low_file.contains("monsterland"))
        {
            return false;
        }
        // Compound words built on a one-word title (« monsterland », « darkness »).
        if words_expected.len() == 1
            && words_title
                .iter()
                .any(|w| w.starts_with(&primary) && w.len() > primary.len())
        {
            return false;
        }
    }
    if let Some(year) = leading_year(expected_year) {
        for y in years_outside_title(filename, &[expected_title]) {
            if ![1080, 2160, 720, 480, 576].contains(&y) && (y - year).abs() > 2 {
                return false;
            }
        }
    }
    true
}

/// Accents and ligatures removed: release names rarely carry them (« Amélie » → « Amelie »).
pub fn fold_title(text: &str) -> String {
    text.replace('œ', "oe")
        .replace('Œ', "Oe")
        .replace('æ', "ae")
        .replace('Æ', "Ae")
        .nfkd()
        .filter(|c| canonical_combining_class(*c) == 0)
        .collect()
}

/// An apostrophe is dropped (« Ch'tis » → « Chtis ») or becomes a separator (« d.Amelie »).
fn title_variants(title: &str) -> Vec<String> {
    let folded = fold_title(title);
    let mut variants = vec![folded.clone()];
    for apostrophe in ['\'', '\u{2019}'] {
        if folded.contains(apostrophe) {
            variants.push(folded.replace(apostrophe, ""));
            variants.push(folded.replace(apostrophe, " "));
        }
    }
    let mut unique = Vec::new();
    for v in variants {
        if !unique.contains(&v) {
            unique.push(v);
        }
    }
    unique
}

pub fn is_valid_release_for_show(
    release_title: &str,
    target_title: &str,
    release_year: &str,
) -> bool {
    if target_title.trim().is_empty() {
        return true;
    }
    let release_title = fold_title(release_title);
    title_variants(target_title)
        .iter()
        .any(|variant| matches_work(&release_title, variant, release_year))
}

fn matches_work(release_title: &str, target_title: &str, release_year: &str) -> bool {
    if !is_release_relevant(release_title, target_title, release_year) {
        return false;
    }
    // A long title must really be in the release: the year and size alone do not
    // identify the work (« A Monster Calls »).
    const SMALL: [&str; 10] = [
        "a", "an", "the", "le", "la", "les", "de", "des", "un", "une",
    ];
    let target_words: Vec<String> = words(&clean_title_for_comparison(target_title))
        .into_iter()
        .filter(|w| !SMALL.contains(&w.as_str()))
        .collect();
    let one_word_before_colon = target_title.contains(':')
        && target_title
            .split_once(':')
            .map_or(0, |(head, _)| head.split_whitespace().count())
            == 1;
    if target_words.len() >= 2 && !one_word_before_colon {
        let release_words = words(&clean_title_for_comparison(release_title));
        let distinctive = &target_words[..target_words.len().min(3)];
        let window = &release_words[..release_words.len().min(distinctive.len() + 4)];
        if !distinctive.iter().all(|w| window.contains(w)) {
            return false;
        }
    }
    if let Some(year) = leading_year(release_year)
        && years_outside_title(release_title, &[target_title])
            .iter()
            .any(|y| (y - year).abs() > 1)
    {
        return false;
    }
    true
}
