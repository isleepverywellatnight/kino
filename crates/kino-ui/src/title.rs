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
        Some(Self {
            id,
            name,
            year,
            poster: text("poster").filter(|p| p.starts_with("https://")),
            rating,
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

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
