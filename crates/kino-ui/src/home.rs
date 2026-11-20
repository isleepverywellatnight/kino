//! The rows above a catalog (phase G2), from the user's own lists as the web's
//! home shows them: « À continuer » (what is in progress) and « Ma Liste »
//! (what is still to see), each for the catalog's kind only: films under
//! Films, series under Series, the anime titles under Anime; then « Pour
//! vous », KINO's own suggestions, under Films and Series.

use gpui::SharedString;
use serde_json::Value;

use crate::core::Catalog;
use crate::title::Title;

/// How many titles a row shows at most (`HOME_SHELF_SIZE` on the web).
pub const SHELF_SIZE: usize = 12;

/// A title in progress: where the user is in it.
#[derive(Clone, Debug, PartialEq)]
pub struct InProgress {
    pub title: Title,
    /// 0–100, how far into the film or the episode.
    pub percent: u8,
    /// The episode reached, for a series: (season, episode).
    pub episode: Option<(u32, u32)>,
}

impl InProgress {
    /// "S1 · É3 • 42 %", what the card shows under the title.
    pub fn note(&self) -> SharedString {
        let percent = format!("{} %", self.percent);
        match self.episode {
            Some((season, episode)) => {
                format!("{} • {percent}", t!("lists.s_x_e_x", season, episode)).into()
            }
            None => percent.into(),
        }
    }
}

/// A suggestion and why KINO makes it.
#[derive(Clone, Debug, PartialEq)]
pub struct Suggested {
    pub title: Title,
    /// "Parce que vous avez aimé …", as KINO's recommender writes it.
    pub why: SharedString,
}

/// Fewer suggestions than this and « Pour vous » is not shown (as on the web).
const FOR_YOU_MIN: usize = 4;

/// The rows of one catalog; an empty one is not shown.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Shelves {
    pub resume: Vec<InProgress>,
    pub list: Vec<Title>,
    pub for_you: Vec<Suggested>,
}

impl Shelves {
    /// `history` newest first, as `kino::library::history` reads it, and
    /// `watchlist` as `kino::library::watchlist` does, anime marked
    /// (`kino::anime_home::mark_anime`); `for_you` as
    /// `kino::recommender::for_you_page` gives it.
    pub fn new(
        catalog: Catalog,
        history: &[Value],
        watchlist: &[Value],
        for_you: &[Value],
    ) -> Self {
        let in_progress: Vec<&Value> = history.iter().filter(|h| is_in_progress(h)).collect();
        let resume = in_progress
            .iter()
            .filter(|h| belongs(catalog, h))
            .filter_map(|h| {
                Some(InProgress {
                    title: Title::from_meta(h)?,
                    percent: percent(h).clamp(0.0, 100.0).round() as u8,
                    episode: episode(h),
                })
            })
            .take(SHELF_SIZE)
            .collect();
        let started: std::collections::HashSet<&str> = in_progress
            .iter()
            .filter_map(|h| h["id"].as_str())
            .collect();
        let list = watchlist
            .iter()
            .filter(|m| belongs(catalog, m))
            .filter(|m| !m["id"].as_str().is_some_and(|id| started.contains(id)))
            .filter(|m| !is_watched(m, history))
            .filter_map(Title::from_meta)
            .take(SHELF_SIZE)
            .collect();
        // Not what was seen already, nor anime (the anime page has its own).
        let seen: std::collections::HashSet<&str> = history
            .iter()
            .filter(|h| is_done(h) || truthy(&h["imported_watched"]))
            .filter_map(|h| h["id"].as_str())
            .collect();
        let for_you: Vec<Suggested> = for_you
            .iter()
            .filter(|m| !is_anime(m))
            .filter(|m| !m["id"].as_str().is_some_and(|id| seen.contains(id)))
            .filter_map(|m| {
                Some(Suggested {
                    title: Title::from_meta(m)?,
                    why: m["why"].as_str().unwrap_or_default().to_string().into(),
                })
            })
            .take(SHELF_SIZE)
            .collect();
        let for_you = if for_you.len() < FOR_YOU_MIN {
            Vec::new()
        } else {
            for_you
        };
        Self {
            resume,
            list,
            for_you,
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

fn is_done(entry: &Value) -> bool {
    truthy(&entry["completed"]) || percent(entry) >= 85.0
}

fn is_anime(entry: &Value) -> bool {
    truthy(&entry["is_anime"]) || entry["type"] == "anime"
}

/// "series" or "movie", as the web's `listItemKind`.
fn kind(entry: &Value) -> &'static str {
    let kind = entry["type"].as_str().unwrap_or("");
    if ["series", "anime", "tv"].contains(&kind) || (is_anime(entry) && kind != "movie") {
        "series"
    } else {
        "movie"
    }
}

/// Whether `entry` is shown under `catalog`.
fn belongs(catalog: Catalog, entry: &Value) -> bool {
    match catalog {
        Catalog::Anime => is_anime(entry),
        Catalog::Movies => !is_anime(entry) && kind(entry) == "movie",
        Catalog::Series => !is_anime(entry) && kind(entry) == "series",
    }
}

/// As the web's `getInProgressHistory`: started, not finished; a series whose
/// last known episode was seen has nothing left to continue.
fn is_in_progress(entry: &Value) -> bool {
    if truthy(&entry["imported_watched"]) {
        return false;
    }
    if entry["type"] == "series" {
        !(truthy(&entry["series_finished"]) && is_done(entry))
    } else {
        !is_done(entry)
    }
}

/// As the web's `listItemState(…) === 'watched'`.
pub(crate) fn is_watched(item: &Value, history: &[Value]) -> bool {
    let Some(entry) = history.iter().find(|h| h["id"] == item["id"]) else {
        return false;
    };
    let done = is_done(entry) || truthy(&entry["imported_watched"]);
    if kind(item) == "series" || entry["type"] == "series" {
        done && truthy(&entry["series_finished"])
    } else {
        done
    }
}

fn episode(entry: &Value) -> Option<(u32, u32)> {
    let number = |key: &str| match &entry[key] {
        Value::Number(n) => n.as_u64().and_then(|n| u32::try_from(n).ok()),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    };
    (kind(entry) == "series")
        .then(|| Some((number("season")?, number("episode")?)))
        .flatten()
        .filter(|&(season, episode)| episode > 0 && season < 1000)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn history() -> Vec<Value> {
        vec![
            json!({"id": "tt1", "name": "Half Seen", "type": "movie", "progress_pct": 42}),
            json!({"id": "tt2", "name": "Seen", "type": "movie", "progress_pct": 97}),
            json!({"id": "tt3", "name": "A Show", "type": "series", "season": 2, "episode": 5,
                   "progress_pct": 10}),
            json!({"id": "tt4", "name": "Done Show", "type": "series", "completed": true,
                   "series_finished": true}),
            json!({"id": "tt5", "name": "Imported", "type": "movie", "imported_watched": true}),
            json!({"id": "tt6", "name": "An Anime", "type": "series", "is_anime": true,
                   "season": 1, "episode": 3, "progress_pct": 50}),
        ]
    }

    fn watchlist() -> Vec<Value> {
        vec![
            json!({"id": "tt7", "name": "To See", "type": "movie"}),
            json!({"id": "tt1", "name": "Half Seen", "type": "movie"}),
            json!({"id": "tt2", "name": "Seen", "type": "movie"}),
            json!({"id": "tt8", "name": "Show To See", "type": "series"}),
            json!({"id": "tt9", "name": "Anime To See", "type": "anime"}),
        ]
    }

    fn names(titles: impl IntoIterator<Item = Title>) -> Vec<String> {
        titles.into_iter().map(|t| t.name.to_string()).collect()
    }

    #[test]
    fn films_continue_films_and_list_what_is_left() {
        let shelves = Shelves::new(Catalog::Movies, &history(), &watchlist(), &[]);
        let resume: Vec<_> = shelves.resume.iter().map(|r| r.title.clone()).collect();
        assert_eq!(names(resume), ["Half Seen"]);
        assert_eq!(shelves.resume[0].note().as_ref(), "42 %");
        // In progress or seen: not « to see » any more.
        assert_eq!(names(shelves.list), ["To See"]);
    }

    #[test]
    fn series_show_the_episode_reached() {
        let shelves = Shelves::new(Catalog::Series, &history(), &watchlist(), &[]);
        assert_eq!(shelves.resume.len(), 1);
        assert_eq!(shelves.resume[0].episode, Some((2, 5)));
        assert!(shelves.resume[0].note().contains("10 %"));
        assert_eq!(names(shelves.list), ["Show To See"]);
    }

    #[test]
    fn anime_stay_under_anime() {
        let shelves = Shelves::new(Catalog::Anime, &history(), &watchlist(), &[]);
        let resume: Vec<_> = shelves.resume.iter().map(|r| r.title.clone()).collect();
        assert_eq!(names(resume), ["An Anime"]);
        assert_eq!(names(shelves.list), ["Anime To See"]);
    }

    #[test]
    fn nothing_saved_shows_no_row() {
        assert_eq!(
            Shelves::new(Catalog::Movies, &[], &[], &[]),
            Shelves::default()
        );
    }

    #[test]
    fn for_you_leaves_out_what_was_seen_and_anime() {
        let suggestion = |id: &str, name: &str| json!({"id": id, "name": name, "type": "movie", "why": format!("close to {name}")});
        let mut items: Vec<Value> = (10..15)
            .map(|i| suggestion(&format!("tt{i}"), &format!("Film {i}")))
            .collect();
        items.push(suggestion("tt2", "Seen"));
        items.push(json!({"id": "tt99", "name": "An Anime", "type": "movie", "is_anime": true}));
        let shelves = Shelves::new(Catalog::Movies, &history(), &[], &items);
        assert_eq!(shelves.for_you.len(), 5);
        assert_eq!(shelves.for_you[0].why.as_ref(), "close to Film 10");
        assert!(shelves.for_you.iter().all(|s| s.title.id.as_ref() != "tt2"));
        // Fewer than four: no row.
        let shelves = Shelves::new(Catalog::Movies, &[], &[], &items[..3]);
        assert!(shelves.for_you.is_empty());
    }
}
