//! A film, a series or an anime as the screens show it: parsed once from the
//! catalogs' JSON (Cinemeta's shape, as `kino::meta` returns it), so the
//! screens never read raw JSON.

use gpui::SharedString;
use serde_json::Value;

#[derive(Clone, Debug, PartialEq)]
pub struct Title {
    /// IMDb id (`tt…`), or the catalog's own id.
    pub id: SharedString,
    pub name: SharedString,
    /// "2024", or "2019–2023" for a series.
    pub year: Option<SharedString>,
    /// The poster's address.
    pub poster: Option<SharedString>,
    /// IMDb's rating, 0–10.
    pub rating: Option<f32>,
    /// "movie", "series" or "anime": how KINO looks it up.
    pub kind: SharedString,
}

impl Title {
    /// A catalog item; `None` without an id or a name (it could not be opened).
    pub fn from_meta(meta: &Value) -> Option<Self> {
        let text = |key: &str| {
            meta.get(key)
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(|s| SharedString::from(s.to_string()))
        };
        let id = text("imdb_id").or_else(|| text("id"))?;
        let name = text("name")?;
        let year = text("releaseInfo").or_else(|| text("year")).or_else(|| {
            meta.get("year")
                .and_then(Value::as_u64)
                .map(|y| SharedString::from(y.to_string()))
        });
        // Cinemeta writes the rating as a string ("7.8"), sometimes a number.
        let rating = meta.get("imdbRating").and_then(|r| match r {
            Value::String(s) => s.trim().parse::<f32>().ok(),
            Value::Number(n) => n.as_f64().map(|n| n as f32),
            _ => None,
        });
        let rating = rating.filter(|r| (0.0..=10.0).contains(r) && *r > 0.0);
        let kind = text("type").unwrap_or_else(|| SharedString::from("movie"));
        Some(Self {
            id,
            kind,
            name,
            year,
            poster: text("poster").filter(|p| p.starts_with("https://")),
            rating,
        })
    }

    /// The title as Ma Liste keeps it (`kino::library::toggle_watchlist`).
    pub fn to_item(&self) -> Value {
        serde_json::json!({
            "id": self.id.as_ref(),
            "name": self.name.as_ref(),
            "type": if self.kind == "anime" { "series" } else { self.kind.as_ref() },
            "year": self.year.as_ref().map_or("", |y| y.as_ref()),
            "poster": self.poster.as_ref().map_or("", |p| p.as_ref()),
            "imdbRating": self.rating.map(|r| format!("{r:.1}")).unwrap_or_default(),
        })
    }

    /// "2024 • ★ 7.8", what the card shows under the title.
    pub fn subtitle(&self) -> SharedString {
        match (&self.year, self.rating) {
            (Some(year), Some(rating)) => format!("{year} • ★ {rating:.1}").into(),
            (Some(year), None) => year.clone(),
            (None, Some(rating)) => format!("★ {rating:.1}").into(),
            (None, None) => SharedString::default(),
        }
    }
}

/// One episode of a series, as KINO's metadata lists it.
#[derive(Clone, Debug, PartialEq)]
pub struct Episode {
    pub season: u32,
    pub episode: u32,
    pub name: SharedString,
    /// "2024-03-01".
    pub released: SharedString,
}

impl Episode {
    /// "S01E03".
    pub fn code(&self) -> SharedString {
        format!("S{:02}E{:02}", self.season, self.episode).into()
    }
}

/// A title's page: what KINO knows of it, in the interface's language where
/// KINO has it (`description_fr`, `genres_fr`).
#[derive(Clone, Debug)]
pub struct Details {
    pub title: Title,
    pub description: Option<SharedString>,
    pub genres: Vec<SharedString>,
    /// The wide picture behind the page.
    pub background: Option<SharedString>,
    /// "142 min".
    pub runtime: Option<SharedString>,
    pub director: Vec<SharedString>,
    pub cast: Vec<SharedString>,
    pub similar: Vec<Title>,
    /// A series' episodes in order (specials and undated ones left out).
    pub episodes: Vec<Episode>,
}

impl Details {
    /// `french`: the interface is in French, so KINO's French texts come first.
    pub fn from_meta(meta: &Value, french: bool) -> Option<Self> {
        let title = Title::from_meta(meta)?;
        let text = |key: &str| {
            meta.get(key)
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(|s| SharedString::from(s.to_string()))
        };
        let list = |key: &str| -> Vec<SharedString> {
            meta.get(key)
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(|s| SharedString::from(s.to_string()))
                .collect()
        };
        let (description, genres) = if french {
            (
                text("description_fr").or_else(|| text("description")),
                Some(list("genres_fr"))
                    .filter(|g| !g.is_empty())
                    .unwrap_or_else(|| list("genres")),
            )
        } else {
            (text("description"), list("genres"))
        };
        let similar = meta
            .get("similar")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Title::from_meta)
            .filter(|t| t.id != title.id)
            .collect();
        Some(Self {
            description,
            genres,
            background: text("background").filter(|b| b.starts_with("https://")),
            runtime: text("runtime"),
            director: list("director"),
            cast: list("cast").into_iter().take(8).collect(),
            similar,
            episodes: meta["videos"]
                .as_array()
                .map(|videos| {
                    kino::series::aired_episodes(videos)
                        .into_iter()
                        .filter_map(|aired| {
                            Some(Episode {
                                season: u32::try_from(aired.season).ok()?,
                                episode: u32::try_from(aired.episode).ok()?,
                                name: aired.name.into(),
                                released: aired.released.into(),
                            })
                        })
                        .collect()
                })
                .unwrap_or_default(),
            title,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn page() -> Value {
        json!({
            "id": "tt0133093", "type": "movie", "name": "The Matrix", "releaseInfo": "1999",
            "imdbRating": "8.7", "runtime": "136 min",
            "description": "A hacker learns the truth.", "description_fr": "Un pirate découvre la vérité.",
            "genres": ["Action", "Sci-Fi"], "genres_fr": ["Action", "Science-fiction"],
            "background": "https://images.metahub.space/background/medium/tt0133093/img",
            "director": ["Lana Wachowski", "Lilly Wachowski"], "cast": ["Keanu Reeves"],
            "similar": [{"id": "tt0234215", "name": "The Matrix Reloaded", "type": "movie"},
                        {"id": "tt0133093", "name": "The Matrix"}]
        })
    }

    #[test]
    fn a_series_lists_its_dated_episodes_in_order() {
        let mut meta = page();
        meta["videos"] = json!([
            {"season": 1, "episode": 2, "name": "Two", "released": "2020-01-08T00:00:00.000Z"},
            {"season": 1, "episode": 1, "name": "One", "released": "2020-01-01T00:00:00.000Z"},
            {"season": 0, "episode": 1, "name": "Special", "released": "2019-12-01"},
            {"season": 2, "episode": 1, "name": "Undated"}
        ]);
        let details = Details::from_meta(&meta, false).unwrap();
        let codes: Vec<_> = details
            .episodes
            .iter()
            .map(|e| e.code().to_string())
            .collect();
        assert_eq!(codes, ["S01E01", "S01E02"]);
        assert_eq!(details.episodes[0].released.as_ref(), "2020-01-01");
    }

    #[test]
    fn a_page_in_french_uses_kino_s_french_texts() {
        let details = Details::from_meta(&page(), true).unwrap();
        assert_eq!(
            details.description.as_ref().map(|d| d.as_ref()),
            Some("Un pirate découvre la vérité.")
        );
        assert_eq!(details.genres[1].as_ref(), "Science-fiction");
        assert_eq!(details.title.kind.as_ref(), "movie");
    }

    #[test]
    fn a_page_in_another_language_keeps_the_originals() {
        let details = Details::from_meta(&page(), false).unwrap();
        assert_eq!(
            details.description.as_ref().map(|d| d.as_ref()),
            Some("A hacker learns the truth.")
        );
        assert_eq!(details.genres[1].as_ref(), "Sci-Fi");
    }

    #[test]
    fn the_title_itself_is_not_among_the_similar_ones() {
        let details = Details::from_meta(&page(), true).unwrap();
        let names: Vec<_> = details.similar.iter().map(|t| t.name.to_string()).collect();
        assert_eq!(names, ["The Matrix Reloaded"]);
    }

    #[test]
    fn a_cinemeta_film_becomes_a_title() {
        let meta = json!({
            "id": "tt0111161", "imdb_id": "tt0111161", "type": "movie",
            "name": "The Shawshank Redemption", "releaseInfo": "1994",
            "poster": "https://images.metahub.space/poster/medium/tt0111161/img",
            "imdbRating": "9.3"
        });
        let title = Title::from_meta(&meta).unwrap();
        assert_eq!(title.id.as_ref(), "tt0111161");
        assert_eq!(title.year.as_ref().map(|y| y.as_ref()), Some("1994"));
        assert_eq!(title.rating, Some(9.3));
        assert_eq!(title.subtitle().as_ref(), "1994 • ★ 9.3");
    }

    #[test]
    fn a_series_keeps_its_years() {
        let meta = json!({"id": "tt0903747", "name": "Breaking Bad", "releaseInfo": "2008–2013", "imdbRating": 9.5});
        let title = Title::from_meta(&meta).unwrap();
        assert_eq!(title.subtitle().as_ref(), "2008–2013 • ★ 9.5");
    }

    #[test]
    fn items_without_an_id_or_a_name_are_left_out() {
        assert!(Title::from_meta(&json!({"name": "No id"})).is_none());
        assert!(Title::from_meta(&json!({"id": "tt1", "name": "  "})).is_none());
    }

    #[test]
    fn odd_values_are_dropped_not_shown() {
        let meta = json!({
            "id": "tt1", "name": "X", "imdbRating": "n/a",
            "poster": "http://insecure.example/p.jpg", "year": 1999
        });
        let title = Title::from_meta(&meta).unwrap();
        assert_eq!((title.rating, title.poster), (None, None));
        assert_eq!(title.year.as_ref().map(|y| y.as_ref()), Some("1999"));
    }
}
