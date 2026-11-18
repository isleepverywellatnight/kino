//! Trakt.tv: sign-in with a device code (no password typed in KINO), progress
//! sent while a title plays, and the Trakt watchlist added to Ma Liste.

use std::time::Duration;

use serde_json::{Value, json};

use crate::db::Db;

pub const API: &str = "https://api.trakt.tv";
const DEFAULT_CLIENT_ID: &str = "6c4998782390886a07ec82956cf04627d3b2c6e61f2249e0b19d7d42eb6bf378";

fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false))
        && v != &json!(0)
        && v != &json!("")
        && v != &json!([])
        && v != &json!({})
}

pub struct Trakt<'a> {
    pub db: &'a Db,
    pub client: &'a reqwest::Client,
    pub base: String,
}

impl Trakt<'_> {
    fn client_id(&self) -> String {
        self.db
            .get_config("trakt_client_id")
            .filter(truthy)
            .and_then(|v| v.as_str().map(str::to_string))
            .unwrap_or_else(|| DEFAULT_CLIENT_ID.into())
    }

    pub fn access_token(&self) -> Option<String> {
        stored_tokens(self.db)
            .and_then(|t| t["access_token"].as_str().map(str::to_string))
            .filter(|t| !t.is_empty())
    }

    fn request(
        &self,
        method: reqwest::Method,
        path: &str,
        token: Option<&str>,
        timeout: u64,
    ) -> reqwest::RequestBuilder {
        let mut request = self
            .client
            .request(method, format!("{}{path}", self.base))
            .header("Content-Type", "application/json")
            .header("trakt-api-version", "2")
            .header("trakt-api-key", self.client_id())
            .header("User-Agent", "KINO-Desktop/1.0")
            .timeout(Duration::from_secs(timeout));
        if let Some(token) = token {
            request = request.header("Authorization", format!("Bearer {token}"));
        }
        request
    }

    pub fn status(&self) -> Value {
        let connected = self.access_token().is_some();
        let user = self.db.get_config("trakt_user").unwrap_or(Value::Null);
        let name = if user.is_object() {
            user["username"].clone()
        } else {
            Value::Null
        };
        json!({"status": "ok", "authenticated": connected, "connected": connected, "username": name, "user": user})
    }

    /// The code the user types on trakt.tv/activate.
    pub async fn start(&self) -> Value {
        let sent = self
            .request(reqwest::Method::POST, "/oauth/device/code", None, 10)
            .json(&json!({"client_id": self.client_id()}))
            .send()
            .await;
        let answer = match sent.and_then(reqwest::Response::error_for_status) {
            Ok(r) => r
                .json::<Value>()
                .await
                .map_err(|e| crate::net::describe(&e)),
            Err(e) => Err(crate::net::describe(&e)),
        };
        match answer {
            Ok(res) => {
                let _ = self.db.save_config("trakt_pending_device", &res);
                let or = |k: &str, d: Value| res.get(k).cloned().unwrap_or(d);
                json!({"status": "ok", "user_code": or("user_code", Value::Null), "verification_url": or("verification_url", json!("https://trakt.tv/activate")),
                       "device_code": or("device_code", Value::Null), "expires_in": or("expires_in", json!(600)), "interval": or("interval", json!(5))})
            }
            Err(e) => json!({"status": "error", "message": e}),
        }
    }

    /// Whether the user has validated the code; once done, the keys and the profile are kept.
    pub async fn poll(&self, device_code: &str) -> Value {
        let secret = self
            .db
            .get_config("trakt_client_secret")
            .and_then(|v| v.as_str().map(str::to_string))
            .unwrap_or_default();
        let body =
            json!({"code": device_code, "client_id": self.client_id(), "client_secret": secret});
        let response = match self
            .request(reqwest::Method::POST, "/oauth/device/token", None, 10)
            .json(&body)
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => return json!({"status": "error", "message": crate::net::describe(&e)}),
        };
        let code = response.status().as_u16();
        let (status, message) = match code {
            200..=299 => ("", ""),
            400 => ("pending", "En attente de validation sur trakt.tv/activate"),
            404 => ("not_found", "Code invalide"),
            409 => ("already_used", "Code déjà utilisé"),
            410 => ("expired", "Code expiré"),
            418 => ("denied", "Autorisation refusée"),
            _ => {
                return json!({"status": "error", "code": code, "message": format!("HTTP Error {code}")});
            }
        };
        if !status.is_empty() {
            return json!({"status": status, "message": message});
        }
        let tokens: Value = match response.json().await {
            Ok(t) => t,
            Err(e) => return json!({"status": "error", "message": crate::net::describe(&e)}),
        };
        let _ = self.db.save_config("trakt_tokens", &tokens);
        keep_in_keychain(Some(&tokens));
        let _ = self.db.delete_config("trakt_pending_device");
        let user = self
            .profile(tokens["access_token"].as_str().unwrap_or(""))
            .await;
        if let Some(user) = &user {
            let _ = self.db.save_config("trakt_user", user);
        }
        json!({"status": "connected", "tokens": tokens, "user": user})
    }

    async fn profile(&self, token: &str) -> Option<Value> {
        if token.is_empty() {
            return None;
        }
        let r = self
            .request(reqwest::Method::GET, "/users/me", Some(token), 10)
            .send()
            .await
            .ok()?
            .error_for_status()
            .ok()?;
        r.json().await.ok()
    }

    pub fn disconnect(&self) -> Value {
        for key in ["trakt_tokens", "trakt_user", "trakt_pending_device"] {
            let _ = self.db.delete_config(key);
        }
        keep_in_keychain(None);
        json!({"status": "disconnected"})
    }

    /// « start », « pause » or « stop » with the progress; at 80 % or more a stop marks it seen.
    pub async fn scrobble(&self, action: &str, media: &Value, progress: f64) -> Value {
        let Some(token) = self.access_token() else {
            return json!({"status": "disabled", "message": "Trakt non connecté"});
        };
        if !["start", "pause", "stop"].contains(&action) {
            return json!({"status": "error", "message": "Action invalide"});
        }
        let body = scrobble_body(media, progress);
        let sent = self
            .request(
                reqwest::Method::POST,
                &format!("/scrobble/{action}"),
                Some(&token),
                8,
            )
            .json(&body)
            .send()
            .await;
        match sent.and_then(reqwest::Response::error_for_status) {
            Ok(r) => match r.json::<Value>().await {
                Ok(res) => json!({"status": "ok", "action": action, "response": res}),
                Err(e) => json!({"status": "error", "message": crate::net::describe(&e)}),
            },
            Err(e) => json!({"status": "error", "message": crate::net::describe(&e)}),
        }
    }

    /// The Trakt watchlist added to Ma Liste (titles removed in KINO stay removed).
    pub async fn sync(&self) -> Value {
        let Some(token) = self.access_token() else {
            return json!({"status": "disabled", "message": "Trakt non connecté"});
        };
        let mut items = Vec::new();
        // Trakt names a series « show »: the Python version read « series » and never found one.
        for (path, field, kind) in [
            ("/sync/watchlist/movies", "movie", "movie"),
            ("/sync/watchlist/shows", "show", "series"),
        ] {
            let Ok(r) = self
                .request(reqwest::Method::GET, path, Some(&token), 12)
                .send()
                .await
            else {
                continue;
            };
            let Ok(list) = r.error_for_status().map(|r| r.json::<Value>()) else {
                continue;
            };
            let Ok(list) = list.await else { continue };
            for entry in list.as_array().into_iter().flatten() {
                let m = &entry[field];
                let ids = &m["ids"];
                let id = ids["imdb"]
                    .as_str()
                    .filter(|i| !i.is_empty())
                    .map(str::to_string)
                    .or_else(|| ids["tmdb"].as_i64().map(|t| format!("tmdb_{t}")));
                if let (Some(id), Some(title)) = (id, m["title"].as_str().filter(|t| !t.is_empty()))
                {
                    let year = m["year"]
                        .as_i64()
                        .map(|y| y.to_string())
                        .unwrap_or_default();
                    items.push(json!({"id": id, "name": title, "type": kind, "year": year, "poster": "", "imdbRating": ""}));
                }
            }
        }
        let added = crate::library::import_user_lists(self.db, &items, &[], false);
        json!({"status": "synced", "items_added": added["watchlist"].as_array().map(Vec::len).unwrap_or(0)})
    }
}

fn int(v: &Value, default: i64) -> i64 {
    match v {
        Value::Null => default,
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f as i64))
            .unwrap_or(default),
        Value::String(s) => s.trim().parse().unwrap_or(default),
        Value::Bool(b) => *b as i64,
        _ => default,
    }
}

/// The body of a scrobble: the show and the episode, or the film, with their ids.
pub fn scrobble_body(media: &Value, progress: f64) -> Value {
    let series = ["series", "show", "episode"].contains(&media["type"].as_str().unwrap_or(""))
        || truthy(&media["season"]);
    let mut body = json!({"progress": progress.clamp(0.0, 100.0)});
    let mut ids = serde_json::Map::new();
    if truthy(&media["imdb_id"]) {
        ids.insert(
            "imdb".into(),
            json!(
                media["imdb_id"]
                    .as_str()
                    .map(str::to_string)
                    .unwrap_or_else(|| media["imdb_id"].to_string())
            ),
        );
    }
    if truthy(&media["tmdb_id"]) {
        let tmdb = int(&media["tmdb_id"], i64::MIN);
        if tmdb != i64::MIN {
            ids.insert("tmdb".into(), json!(tmdb));
        }
    }
    if series {
        let title = [&media["title"], &media["series_title"]]
            .into_iter()
            .find(|v| truthy(v))
            .cloned()
            .unwrap_or(json!(""));
        let mut show = json!({"title": title});
        if !ids.is_empty() {
            show["ids"] = json!(ids);
        }
        body["show"] = show;
        body["episode"] =
            json!({"season": int(&media["season"], 1), "number": int(&media["episode"], 1)});
    } else {
        let title = if truthy(&media["title"]) {
            media["title"].clone()
        } else {
            json!("")
        };
        let year = if truthy(&media["year"]) {
            json!(int(&media["year"], 0))
        } else {
            Value::Null
        };
        let mut movie = json!({"title": title, "year": year});
        if !ids.is_empty() {
            movie["ids"] = json!(ids);
        }
        body["movie"] = movie;
    }
    body
}

/// Trakt's tokens: from the database, or the keychain when the database says so.
pub fn stored_tokens(db: &crate::db::Db) -> Option<Value> {
    crate::secrets::resolve_trakt(db.get_config("trakt_tokens"), crate::secrets::store())
}

/// Trakt's tokens (or their absence) copied to the keychain, when KINO uses one.
fn keep_in_keychain(tokens: Option<&Value>) {
    if let Some(store) = crate::secrets::store()
        && let Err(e) = crate::secrets::keep_trakt(store, tokens)
    {
        crate::errorlog::LOG.record("trousseau", "Erreur", &format!("trakt: {e}"), &[]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scrobble_bodies() {
        let episode = scrobble_body(
            &json!({"type": "series", "title": "The Bear", "imdb_id": "tt14452776", "season": "3", "episode": 5}),
            120.0,
        );
        assert_eq!(
            episode,
            json!({"progress": 100.0, "show": {"title": "The Bear", "ids": {"imdb": "tt14452776"}}, "episode": {"season": 3, "number": 5}})
        );
        let film = scrobble_body(
            &json!({"type": "movie", "title": "Heat", "year": "1995", "tmdb_id": "949"}),
            42.5,
        );
        assert_eq!(
            film,
            json!({"progress": 42.5, "movie": {"title": "Heat", "year": 1995, "ids": {"tmdb": 949}}})
        );
    }
}
