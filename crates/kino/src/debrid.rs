//! The debrid services KINO plays through: the providers it knows, and their
//! accounts (who is connected, how long their subscription lasts).

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use reqwest::Method;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::db::now;
use crate::net::{Net, NetError, NetResult};

/// (key, name, short, badge, torrentio key, where to find the key).
pub const PROVIDERS: [(&str, &str, &str, &str, &str, &str); 6] = [
    (
        "realdebrid",
        "Real-Debrid",
        "RD",
        "RD+",
        "realdebrid",
        "https://real-debrid.com/apitoken",
    ),
    (
        "alldebrid",
        "AllDebrid",
        "AD",
        "AD+",
        "alldebrid",
        "https://alldebrid.fr/apikeys/",
    ),
    (
        "torbox",
        "TorBox",
        "TB",
        "TB+",
        "torbox",
        "https://torbox.app/settings",
    ),
    (
        "debridlink",
        "Debrid-Link",
        "DL",
        "DL+",
        "debridlink",
        "https://debrid-link.fr/webapp/apikey",
    ),
    (
        "premiumize",
        "Premiumize",
        "PM",
        "PM+",
        "premiumize",
        "https://www.premiumize.me/account",
    ),
    (
        "megadebrid",
        "Mega-Debrid",
        "MD",
        "MD+",
        "",
        "https://www.mega-debrid.eu/index.php?page=account",
    ),
];

pub fn is_provider(name: &str) -> bool {
    PROVIDERS.iter().any(|p| p.0 == name)
}

fn provider(name: &str) -> (&'static str, &'static str) {
    PROVIDERS
        .iter()
        .find(|p| p.0 == name)
        .map(|p| (p.1, p.2))
        .unwrap_or(("Real-Debrid", "RD"))
}

fn query(pairs: &[(&str, &str)]) -> String {
    let mut s = form_urlencoded::Serializer::new(String::new());
    for (k, v) in pairs {
        s.append_pair(k, v);
    }
    s.finish()
}

fn seconds_until(stamp: f64) -> i64 {
    (stamp - now()).max(0.0) as i64
}

fn number(value: &Value) -> f64 {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0.0)
}

/// Seconds left before an ISO date (« 2027-01-01T00:00:00Z »).
fn seconds_until_iso(text: &str) -> Option<i64> {
    let text = text.trim().replace('Z', "+00:00");
    let (date, rest) = text.split_once('T')?;
    let mut d = date.split('-').map(|p| p.parse::<i64>());
    let (y, m, day) = (d.next()?.ok()?, d.next()?.ok()?, d.next()?.ok()?);
    let time = rest.split(['+', '-']).next()?;
    let mut t = time
        .split(':')
        .map(|p| p.split('.').next().unwrap_or("0").parse::<i64>());
    let (h, min, s) = (
        t.next()?.ok()?,
        t.next().unwrap_or(Ok(0)).ok()?,
        t.next().unwrap_or(Ok(0)).ok()?,
    );
    // Days from the civil date (Howard Hinnant).
    let (y2, m2) = if m <= 2 { (y - 1, m + 9) } else { (y, m - 3) };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let doy = (153 * m2 + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    let stamp = days * 86400 + h * 3600 + min * 60 + s;
    Some((stamp as f64 - now()).max(0.0) as i64)
}

/// The account behind a key: {username, premium (seconds left), provider, short}.
pub async fn user(net: &Net, provider_name: &str, token: &str) -> NetResult<Option<Value>> {
    let token = token.trim();
    if token.is_empty() {
        return Ok(None);
    }
    let (name, short) = provider(provider_name);
    let timeout = Duration::from_secs(20);
    let account = |username: Value, premium: i64| json!({"username": username, "premium": premium, "provider": name, "short": short});
    let get = |url: String, headers: Vec<(&'static str, String)>| async move {
        let pairs: Vec<(&str, &str)> = headers.iter().map(|(k, v)| (*k, v.as_str())).collect();
        net.request_json(Method::GET, &url, &pairs, None, timeout, 3)
            .await
    };
    let bearer = vec![("Authorization", format!("Bearer {token}"))];
    match provider_name {
        "alldebrid" => {
            let res = get(
                format!(
                    "https://api.alldebrid.com/v4/user?{}",
                    query(&[("agent", "KINO"), ("apikey", token)])
                ),
                vec![],
            )
            .await?;
            if res["status"] != "success" {
                return Err(NetError(
                    res["error"]["message"]
                        .as_str()
                        .unwrap_or("Clé AllDebrid invalide")
                        .into(),
                ));
            }
            let u = &res["data"]["user"];
            let left = if u["isPremium"].as_bool() == Some(true) {
                seconds_until(number(&u["premiumUntil"]))
            } else {
                0
            };
            Ok(Some(account(
                u.get("username").cloned().unwrap_or(json!("AllDebrid")),
                left,
            )))
        }
        "debridlink" => {
            let res = get(
                "https://debrid-link.com/api/v2/account/infos".into(),
                bearer,
            )
            .await?;
            let u = &res["value"];
            let name = [&u["username"], &u["email"]]
                .into_iter()
                .find(|v| v.as_str().is_some_and(|s| !s.is_empty()))
                .cloned()
                .unwrap_or(json!("Debrid-Link"));
            Ok(Some(account(name, number(&u["premiumLeft"]) as i64)))
        }
        "torbox" => {
            let res = get("https://api.torbox.app/v1/api/user/me".into(), bearer).await?;
            let u = &res["data"];
            let paying = number(&u["plan"]) > 0.0;
            let expires = u["premium_expires_at"].as_str().unwrap_or("");
            let left = if !expires.is_empty() {
                seconds_until_iso(expires).unwrap_or(if paying { 86400 * 30 } else { 0 })
            } else if paying {
                86400 * 30
            } else {
                0
            };
            let email = u["email"]
                .as_str()
                .filter(|e| !e.is_empty())
                .unwrap_or("TorBox");
            Ok(Some(account(
                json!(email.split('@').next().unwrap_or(email)),
                left,
            )))
        }
        "premiumize" => {
            let res = get(
                format!(
                    "https://www.premiumize.me/api/account/info?{}",
                    query(&[("apikey", token)])
                ),
                vec![],
            )
            .await?;
            if res["status"] != "success" {
                return Err(NetError(
                    res["message"]
                        .as_str()
                        .unwrap_or("Clé Premiumize invalide")
                        .into(),
                ));
            }
            let customer = match &res["customer_id"] {
                Value::Null => "Premiumize".to_string(),
                Value::String(s) if s.is_empty() => "Premiumize".to_string(),
                Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            Ok(Some(account(
                json!(customer),
                seconds_until(number(&res["premium_until"])),
            )))
        }
        "megadebrid" => match token.split_once(':') {
            Some((login, password)) => {
                let url = format!(
                    "https://www.mega-debrid.eu/index.php?{}",
                    query(&[
                        ("form", "connexion"),
                        ("login", login),
                        ("password", password)
                    ])
                );
                let res = get(url, vec![]).await?;
                if res["response_code"] != "ok" {
                    return Err(NetError(
                        res["response_text"]
                            .as_str()
                            .unwrap_or("Identifiants Mega-Debrid invalides")
                            .into(),
                    ));
                }
                let left = if number(&res["vip_end"]) > 0.0 {
                    (number(&res["vip_end"]) as i64 - now() as i64).max(0)
                } else {
                    86400
                };
                Ok(Some(account(json!(login), left)))
            }
            None => Ok(Some(account(json!("Mega-Debrid"), 86400 * 30))),
        },
        _ => {
            let mut u = get("https://api.real-debrid.com/rest/1.0/user".into(), bearer).await?;
            if let Some(map) = u.as_object_mut() {
                map.insert("provider".into(), json!(name));
                map.insert("short".into(), json!(short));
            }
            Ok(Some(u))
        }
    }
}

fn is_authentication_error(message: &str) -> bool {
    let m = message.to_lowercase();
    [
        "erreur api (401)",
        "unauthorized",
        "invalid api key",
        "invalid api token",
        "invalid token",
        "clé alldebrid invalide",
        "clé premiumize invalide",
        "identifiants mega-debrid invalides",
    ]
    .iter()
    .any(|needle| m.contains(needle))
}

/// (provider, SHA-256 of the key).
type AccountKey = (String, String);

/// The account's answer is kept 5 minutes, so that opening Settings or the
/// start-up does not wait for it again; a network failure is not remembered.
pub struct Accounts {
    cache: Mutex<HashMap<AccountKey, (Instant, Value, String)>>,
    check: tokio::sync::Mutex<()>,
}

impl Default for Accounts {
    fn default() -> Self {
        Self {
            cache: Mutex::new(HashMap::new()),
            check: tokio::sync::Mutex::new(()),
        }
    }
}

const ACCOUNT_TTL: Duration = Duration::from_secs(300);

impl Accounts {
    fn cached(&self, key: &(String, String)) -> Option<(Value, String)> {
        let cache = self.cache.lock().unwrap_or_else(|e| e.into_inner());
        cache
            .get(key)
            .filter(|(at, _, _)| at.elapsed() < ACCOUNT_TTL)
            .map(|(_, user, status)| (user.clone(), status.clone()))
    }

    /// (user, status): « connected », « invalid » (the key is refused) or « unavailable ».
    pub async fn status(&self, net: &Net, provider: &str, token: &str) -> (Value, String) {
        let key = (
            provider.to_string(),
            hex::encode(Sha256::digest(token.as_bytes())),
        );
        if let Some(hit) = self.cached(&key) {
            return hit;
        }
        // One check at a time: the start-up check and the page's first request share it.
        let _one = self.check.lock().await;
        if let Some(hit) = self.cached(&key) {
            return hit;
        }
        let (user, status, remember) = match user(net, provider, token).await {
            Ok(Some(u)) if !u.is_null() && u.as_object().is_some_and(|o| !o.is_empty()) => {
                (u, "connected", true)
            }
            Ok(_) => (Value::Null, "unavailable", true),
            Err(e) if is_authentication_error(&e.0) => (Value::Null, "invalid", true),
            Err(_) => (Value::Null, "unavailable", false),
        };
        if remember {
            self.cache
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .insert(key, (Instant::now(), user.clone(), status.to_string()));
        }
        (user, status.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn providers_and_errors() {
        assert!(is_provider("torbox") && !is_provider("x"));
        assert!(is_authentication_error(
            "Erreur API (401) de api.real-debrid.com : bad_token"
        ));
        assert!(!is_authentication_error(
            "Erreur API (503) de api.real-debrid.com"
        ));
        assert!(seconds_until_iso("2999-01-01T00:00:00Z").unwrap() > 0);
        assert_eq!(seconds_until_iso("2001-01-01T00:00:00.000Z"), Some(0));
        assert_eq!(seconds_until_iso("nonsense"), None);
    }
}
