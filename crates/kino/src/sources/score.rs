//! The score of a release: eleven parts, each answering one question, with the
//! reasons shown in « Pourquoi cette source ».

use std::sync::LazyLock;

use fancy_regex::Regex as FancyRegex;
use regex::Regex;
use serde_json::{Map, Value, json};

use super::release::{FRENCH_AUDIO_LANGS, TRASH, details};
use super::relevance::{plausible_size, release_years};

pub const QUALITY_FIRST_POINTS: i64 = 10000;
const ANIME_GROUPS: [&str; 13] = [
    "subsplease",
    "erai-raws",
    "asw",
    "judas",
    "ember",
    "vostfree",
    "tsundere",
    "yameii",
    "anime rg",
    "kametsu",
    "dragsterps",
    "kayo",
    "chibiki",
];
pub const PARTS: [&str; 11] = [
    "instant_points",
    "quality_points",
    "language_points",
    "picture_points",
    "sound_points",
    "seeder_points",
    "fake_penalty",
    "size_points",
    "anime_group_points",
    "year_points",
    "priority_points",
];

static ANNOUNCED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:2160p|4k|uhd|1080p|fhd|1080i|720p|480p|576p|sd|dvdrip)\b").unwrap()
});
static ANNOUNCED_NO_SD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:2160p|4k|uhd|1080p|fhd|1080i|720p|480p|576p)\b").unwrap()
});

pub struct Profile {
    pub pref_lang: String,
    pub pref_quality: String,
    pub hdr_mode: String,
    pub media_type: String,
    pub runtime_minutes: Option<f64>,
    pub target_year: String,
    pub title_years: Vec<i64>,
    pub priority: String,
}

fn langs(d: &Value) -> Vec<&str> {
    d["langs"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect()
}

fn has(list: &[&str], tag: &str) -> bool {
    list.contains(&tag)
}

fn title(item: &Value) -> &str {
    item["title"].as_str().unwrap_or("")
}

fn number(value: &Value) -> f64 {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0.0)
}

fn raw_text(item: &Value) -> String {
    format!("{} {}", title(item), item["meta"].as_str().unwrap_or(""))
}

pub fn release_details(item: &Value) -> Value {
    match &item["parsed_details"] {
        Value::Object(o) if !o.is_empty() => Value::Object(o.clone()),
        _ => details(title(item), item["meta"].as_str().unwrap_or("")),
    }
}

fn instant_points(item: &Value, d: &Value) -> i64 {
    if item["is_instant"].as_bool() == Some(true) || d["is_instant"].as_bool() == Some(true) {
        3500
    } else {
        0
    }
}

fn quality_points(d: &Value, p: &Profile) -> i64 {
    let (res, src) = (
        d["resolution"].as_str().unwrap_or(""),
        d["source"].as_str().unwrap_or(""),
    );
    if p.pref_quality == "1080p" {
        if res == "1080p" {
            return 350
                + match src {
                    "REMUX" => 120,
                    "BluRay" => 80,
                    _ => 0,
                };
        }
        return match res {
            "4K" => 100,
            "720p" => 50,
            _ => 0,
        };
    }
    match res {
        "4K" => {
            300 + match src {
                "REMUX" => 180,
                "BluRay" => 100,
                _ => 0,
            }
        }
        "1080p" => {
            200 + match src {
                "REMUX" => 140,
                "BluRay" => 80,
                _ => 0,
            }
        }
        "720p" => 50,
        _ => 0,
    }
}

fn language_points(d: &Value, p: &Profile) -> i64 {
    let l = langs(d);
    if p.pref_lang == "vostfr" {
        if has(&l, "VOSTFR") {
            return 900;
        }
        if has(&l, "MULTI") {
            return 750;
        }
        if has(&l, "VO") || l.is_empty() {
            return 500;
        }
        if ["VFF", "VF", "FR"].iter().any(|t| has(&l, t)) {
            return 200;
        }
        return 0;
    }
    if has(&l, "VFF") && has(&l, "MULTI") {
        return 950;
    }
    for (tag, points) in [
        ("VFF", 850),
        ("MULTI", 750),
        ("VF", 650),
        ("VFQ", 450),
        ("FR", 400),
        ("VOSTFR", 250),
    ] {
        if has(&l, tag) {
            return points;
        }
    }
    50
}

fn picture_points(d: &Value, p: &Profile) -> i64 {
    let hdr = d["hdr_type"].as_str().unwrap_or("");
    let mut points = 0;
    match p.hdr_mode.as_str() {
        "hdr_native" => {
            if hdr.contains("DV") {
                points += 60;
            }
            if hdr.contains("HDR") {
                points += 50;
            }
        }
        "hdr_boost" => {
            if hdr.contains("SDR") {
                points += 100;
            }
            if hdr.contains("DV") && !hdr.contains("HDR") {
                points -= 150;
            }
        }
        // The player converts HDR10 well for a standard screen; Dolby Vision alone can show wrong colours.
        _ => {
            if hdr == "DV" {
                points -= 350;
            } else if hdr == "SDR" {
                points += 40;
            }
        }
    }
    points
}

fn sound_points(d: &Value) -> i64 {
    let a: Vec<&str> = d["audio_formats"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    if a.contains(&"Atmos") {
        80
    } else if a.contains(&"TrueHD") || a.contains(&"DTS-HD") {
        60
    } else if a.contains(&"DDP") || a.contains(&"5.1") {
        35
    } else {
        0
    }
}

fn seeder_points(item: &Value) -> i64 {
    (number(&item["seeders"]) as i64 * 2).min(80)
}

fn fake_penalty(item: &Value, p: &Profile) -> i64 {
    let mut points = 0;
    if !plausible_size(item, &p.media_type, p.runtime_minutes) {
        points -= 10000;
    }
    if TRASH.is_match(&title(item).to_uppercase()) {
        points -= 20000;
    }
    points
}

fn size_points(item: &Value, p: &Profile) -> i64 {
    let size = number(&item["size_gb"]);
    if p.pref_quality == "1080p" {
        if (1.5..=14.0).contains(&size) {
            return 60;
        }
        return if size > 25.0 { -80 } else { 0 };
    }
    if (6.0..=35.0).contains(&size) {
        60
    } else if size > 35.0 && size <= 65.0 {
        30
    } else {
        0
    }
}

fn anime_group_points(item: &Value) -> i64 {
    let lower = title(item).to_lowercase();
    if !ANIME_GROUPS.iter().any(|g| lower.contains(g)) {
        return 0;
    }
    let from_nyaa = item["source"]
        .as_str()
        .unwrap_or("")
        .to_lowercase()
        .contains("nyaa")
        || lower.contains("nyaasi");
    260 + if from_nyaa { 80 } else { 0 }
}

fn target_year(p: &Profile) -> Option<i64> {
    let first: String = p.target_year.chars().take(4).collect();
    first.trim().parse().ok()
}

fn year_points(item: &Value, p: &Profile) -> i64 {
    if p.target_year.is_empty() {
        return 0;
    }
    let Some(year) = target_year(p) else { return 0 };
    let years: Vec<i64> = release_years(title(item))
        .into_iter()
        .filter(|y| !p.title_years.contains(y))
        .collect();
    if years.contains(&year) {
        500
    } else if years.iter().any(|y| (y - year).abs() > 1) {
        -10000
    } else {
        0
    }
}

fn priority_points(item: &Value, d: &Value, p: &Profile) -> i64 {
    if p.priority != "quality" {
        return 0;
    }
    let wanted = if p.pref_quality == "1080p" {
        "1080p"
    } else {
        "4K"
    };
    if d["resolution"].as_str() != Some(wanted) || !ANNOUNCED.is_match(&raw_text(item)) {
        return 0;
    }
    let hdr = d["hdr_type"].as_str().unwrap_or("");
    if hdr.contains("DV") && !hdr.contains("HDR") {
        return 0;
    }
    if fake_penalty(item, p) < 0 || year_points(item, p) < 0 {
        return 0;
    }
    if language_points(d, p) < (if p.pref_lang == "vostfr" { 500 } else { 400 }) {
        return 0;
    }
    QUALITY_FIRST_POINTS
}

/// [(part, points)] in the order of PARTS.
pub fn parts(item: &Value, d: &Value, p: &Profile) -> Vec<(&'static str, i64)> {
    vec![
        ("instant_points", instant_points(item, d)),
        ("quality_points", quality_points(d, p)),
        ("language_points", language_points(d, p)),
        ("picture_points", picture_points(d, p)),
        ("sound_points", sound_points(d)),
        ("seeder_points", seeder_points(item)),
        ("fake_penalty", fake_penalty(item, p)),
        ("size_points", size_points(item, p)),
        ("anime_group_points", anime_group_points(item)),
        ("year_points", year_points(item, p)),
        ("priority_points", priority_points(item, d, p)),
    ]
}

fn quality_reason(points: i64, item: &Value, d: &Value, p: &Profile) -> (String, &'static str) {
    if !ANNOUNCED.is_match(&raw_text(item)) {
        return ("Qualité non indiquée".into(), "neutral");
    }
    let resolution = d["resolution"].as_str().unwrap_or("");
    let label = format!(
        "{resolution}{}",
        match d["source"].as_str() {
            Some("REMUX") => " REMUX",
            Some("BluRay") => " Blu-ray",
            _ => "",
        }
    );
    let wanted = if p.pref_quality != "1080p" {
        "4K"
    } else {
        "1080p"
    };
    if resolution == wanted {
        (format!("{label}, la qualité choisie"), "good")
    } else if resolution == "4K" {
        (
            format!("{label}, au-dessus de la qualité choisie"),
            "neutral",
        )
    } else if resolution == "1080p" {
        (label, if points >= 200 { "good" } else { "neutral" })
    } else {
        (format!("{label}, en dessous de la qualité choisie"), "bad")
    }
}

fn language_reason(d: &Value, p: &Profile) -> (&'static str, &'static str) {
    let l = langs(d);
    if p.pref_lang == "vostfr" {
        if has(&l, "VOSTFR") {
            return ("VOSTFR, la langue choisie", "good");
        }
        if has(&l, "MULTI") {
            return ("Plusieurs langues", "good");
        }
        if has(&l, "VO") || l.is_empty() {
            return ("Version originale, sous-titres non indiqués", "neutral");
        }
        return ("Doublée en français", "bad");
    }
    if has(&l, "VFF") && has(&l, "MULTI") {
        return ("VF + VO", "good");
    }
    for (tag, text, tone) in [
        ("VFF", "VF (France)", "good"),
        ("MULTI", "Plusieurs langues", "good"),
        ("VF", "VF", "good"),
        ("VFQ", "VF québécoise", "neutral"),
        ("FR", "Français", "neutral"),
        ("VOSTFR", "VOSTFR seulement", "bad"),
    ] {
        if has(&l, tag) {
            return (text, tone);
        }
    }
    ("Pas de VF annoncée", "bad")
}

fn picture_reason(points: i64, d: &Value, p: &Profile) -> Option<(&'static str, &'static str)> {
    let hdr = d["hdr_type"].as_str().unwrap_or("");
    match p.hdr_mode.as_str() {
        "hdr_native" => (points > 0).then_some(("HDR / Dolby Vision, pour votre écran", "good")),
        "hdr_boost" => {
            if points > 0 {
                Some(("Image SDR, idéale pour l’amélioration HDR", "good"))
            } else {
                (points < 0).then_some(("Dolby Vision seul : couleurs faussées possibles", "bad"))
            }
        }
        _ => {
            if hdr.contains("DV") && !hdr.contains("HDR") {
                Some(("Dolby Vision seul : couleurs faussées possibles", "bad"))
            } else if hdr.contains("HDR") || hdr.contains("DV") {
                Some(("HDR, converti pour votre écran standard", "neutral"))
            } else {
                (points > 0).then_some(("Image SDR, adaptée à votre écran", "good"))
            }
        }
    }
}

fn reason(
    name: &str,
    points: i64,
    item: &Value,
    d: &Value,
    p: &Profile,
) -> Option<(String, &'static str)> {
    let fixed = |pair: Option<(&str, &'static str)>| pair.map(|(t, tone)| (t.to_string(), tone));
    match name {
        "instant_points" => fixed(Some(if points != 0 {
            ("Prête tout de suite", "good")
        } else {
            ("Disponibilité immédiate non signalée", "bad")
        })),
        "quality_points" => Some(quality_reason(points, item, d, p)),
        "language_points" => fixed(Some(language_reason(d, p))),
        "picture_points" => fixed(picture_reason(points, d, p)),
        "sound_points" => fixed(match points {
            80 => Some(("Son Dolby Atmos", "good")),
            60 => Some(("Son haute fidélité (TrueHD, DTS-HD)", "good")),
            35 => Some(("Son 5.1", "good")),
            _ => None,
        }),
        "seeder_points" => (points >= 40).then(|| {
            (
                format!("Bien partagée ({} pairs)", number(&item["seeders"]) as i64),
                "good",
            )
        }),
        "fake_penalty" => fixed(if points <= -20000 {
            Some(("Copie filmée en salle (CAM, TS)", "bad"))
        } else {
            (points < 0).then_some(("Taille improbable pour la durée", "bad"))
        }),
        "size_points" => fixed(match points {
            60 => Some(("Taille adaptée à la qualité", "good")),
            30 => Some(("Fichier lourd (35 à 65 Go)", "neutral")),
            p if p < 0 => Some(("Fichier très lourd", "bad")),
            _ => None,
        }),
        "anime_group_points" => fixed(match points {
            0 => None,
            p if p > 260 => Some(("Équipe anime reconnue, via Nyaa", "good")),
            _ => Some(("Équipe anime reconnue", "good")),
        }),
        "priority_points" => fixed((points > 0).then_some(("La qualité choisie d’abord", "good"))),
        "year_points" => {
            if points > 0 {
                Some((
                    format!(
                        "Année {} confirmée",
                        p.target_year.chars().take(4).collect::<String>()
                    ),
                    "good",
                ))
            } else {
                fixed((points < 0).then_some(("Autre année : sans doute une autre œuvre", "bad")))
            }
        }
        _ => None,
    }
}

/// The reasons behind a score, in the order of its parts.
pub fn reasons(item: &Value, d: &Value, parts: &[(&str, i64)], p: &Profile) -> Vec<Value> {
    parts
        .iter()
        .filter_map(|(name, points)| {
            reason(name, *points, item, d, p).map(
                |(text, tone)| json!({"part": name, "text": text, "tone": tone, "points": points}),
            )
        })
        .collect()
}

pub fn parts_map(parts: &[(&str, i64)]) -> Map<String, Value> {
    parts
        .iter()
        .map(|(k, v)| (k.to_string(), json!(v)))
        .collect()
}

static SXXEYY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\bs(\d{1,2})[. _-]*e(\d{1,3})(?:[. _]*(-)[. _]*e?(\d{1,3})|[. _]*e(\d{1,3}))?")
        .unwrap()
});
static NXN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\b(\d{1,2})x(\d{1,3})\b").unwrap());
static ABSOLUTE: LazyLock<FancyRegex> = LazyLock::new(|| {
    FancyRegex::new(r"\s-\s(\d{1,3})(?:v\d)?(?=[\s\[\(.]|$)(?!\s*[-~]\s*\d)").unwrap()
});
static NAMED_SEASON: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)\s+season\b|\bseason\s*(\d{1,2})\b|\bs(\d{1,2})\b")
        .unwrap()
});
static RENUMBERED: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(?:part|cour)\s*\d|\b(?:ii|iii|iv)\b").unwrap());
static SEASON_PACK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(?:season|s)[. _]*0?(\d{1,2})\b").unwrap());

/// « match », « mismatch » or « unknown » for the episode asked for.
pub fn episode_status(
    title: &str,
    season: i64,
    episode: i64,
    absolute_episode: Option<i64>,
) -> &'static str {
    let num = |m: Option<regex::Match>| m.and_then(|m| m.as_str().parse::<i64>().ok());
    if let Some(c) = SXXEYY.captures(title) {
        let (found_season, first) = (num(c.get(1)).unwrap_or(-1), num(c.get(2)).unwrap_or(-1));
        if found_season != season {
            return "mismatch";
        }
        if c.get(3).is_some()
            && let Some(last) = num(c.get(4))
        {
            return if first <= episode && episode <= last {
                "match"
            } else {
                "mismatch"
            };
        }
        let mut episodes = vec![first];
        if let Some(second) = num(c.get(5)) {
            episodes.push(second);
        }
        return if episodes.contains(&episode) {
            "match"
        } else {
            "mismatch"
        };
    }
    if let Some(c) = NXN.captures(title) {
        return if (num(c.get(1)), num(c.get(2))) == (Some(season), Some(episode)) {
            "match"
        } else {
            "mismatch"
        };
    }
    // Anime releases number episodes absolutely: « [SubsPlease] Frieren - 12 (1080p) ».
    if let Ok(Some(c)) = ABSOLUTE.captures(title) {
        let start = c.get(0).map_or(0, |m| m.start());
        let before = &title[..start];
        let number: i64 = c.get(1).and_then(|m| m.as_str().parse().ok()).unwrap_or(-1);
        if let Some(named) = NAMED_SEASON.captures(before) {
            let named_season = [named.get(1), named.get(2), named.get(3)]
                .into_iter()
                .flatten()
                .find_map(|m| m.as_str().parse::<i64>().ok())
                .unwrap_or(-1);
            if named_season != season {
                return "mismatch";
            }
            return if number == episode {
                "match"
            } else {
                "mismatch"
            };
        }
        let renumbered = RENUMBERED.is_match(before);
        if !renumbered && season == 1 {
            return if number == episode {
                "match"
            } else {
                "mismatch"
            };
        }
        if !renumbered && let Some(absolute) = absolute_episode.filter(|a| *a != 0) {
            if number == absolute {
                return "match";
            }
            return if number == episode {
                "unknown"
            } else {
                "mismatch"
            };
        }
    }
    if let Some(c) = SEASON_PACK.captures(title)
        && num(c.get(1)) != Some(season)
    {
        return "mismatch";
    }
    "unknown"
}

/// The signals behind a recommendation, from the same details as the ranking.
pub fn explain(
    item: &Value,
    pref_lang: &str,
    pref_quality: &str,
    title_checked: bool,
    status: &str,
) -> Value {
    let d = release_details(item);
    let (mut reasons, mut warnings): (Vec<String>, Vec<String>) = (vec![], vec![]);
    if title_checked {
        reasons.push("Titre cohérent".into());
    } else {
        warnings.push("Titre non vérifié".into());
    }
    match status {
        "match" => reasons.push("Épisode indiqué".into()),
        "unknown" => warnings.push("Épisode à vérifier dans le fichier".into()),
        _ => {}
    }
    if item["is_instant"].as_bool() == Some(true) || d["is_instant"].as_bool() == Some(true) {
        reasons.push("Disponibilité immédiate signalée".into());
    }
    let l = langs(&d);
    let preferred: &[&str] = if pref_lang == "vostfr" {
        &["VOSTFR"]
    } else {
        &["VFF", "VF", "MULTI", "FR"]
    };
    if let Some(chosen) = preferred.iter().find(|p| has(&l, p)) {
        reasons.push(format!("Langue souhaitée : {chosen}"));
    } else if l.is_empty() {
        warnings.push("Langue non indiquée".into());
    } else if pref_lang == "vostfr" {
        if has(&l, "VO") {
            warnings.push("Sous-titres français non indiqués".into());
        }
    } else if !FRENCH_AUDIO_LANGS.iter().any(|f| has(&l, f)) {
        warnings.push("Audio français non indiqué".into());
    }
    let resolution = d["resolution"].as_str().unwrap_or("");
    if !resolution.is_empty() && ANNOUNCED_NO_SD.is_match(&raw_text(item)) {
        let label = if resolution.to_lowercase() == pref_quality.to_lowercase() {
            "Qualité souhaitée"
        } else {
            "Qualité annoncée"
        };
        reasons.push(format!("{label} : {resolution}"));
    }
    let audio: Vec<&str> = d["audio_formats"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    if audio
        .iter()
        .any(|a| ["DTS", "DTS-HD", "TrueHD"].contains(a))
    {
        warnings.push("Compatibilité audio à vérifier".into());
    }
    json!({"reasons": reasons, "warnings": warnings})
}
