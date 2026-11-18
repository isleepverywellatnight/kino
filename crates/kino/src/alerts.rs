//! Which new episodes to announce, and how. An item of « Nouveaux épisodes »
//! is announced once per release (a later episode of the same series is a new
//! release). The first check only records what is already out, so turning the
//! feature on brings no burst of old episodes. Messages never name the
//! episode: its title can spoil.

use serde_json::{Value, json};

use crate::play::{text, truthy};

const MAX_SEEN: usize = 300;
const MAX_ALERTS: usize = 3;

fn kind(item: &Value) -> String {
    Some(text(&item["kind"]))
        .filter(|k| !k.is_empty() && truthy(&item["kind"]))
        .unwrap_or_else(|| "followed".into())
}

/// As Python's str() of a value that may be missing (« None »).
fn shown(value: &Value) -> String {
    if value.is_null() {
        "None".into()
    } else {
        text(value)
    }
}

pub fn alert_key(item: &Value) -> String {
    let kind = kind(item);
    if kind == "followed" {
        let release = [&item["newest"], &item["released"]]
            .into_iter()
            .find(|v| truthy(v))
            .map(text)
            .unwrap_or_default();
        return format!("{}:followed:{release}", shown(&item["id"]));
    }
    format!("{}:{kind}:s{}", shown(&item["id"]), shown(&item["season"]))
}

fn number(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_f64().map(|f| f as i64))
        .or_else(|| text(value).trim().parse().ok())
        .unwrap_or(0)
}

fn code(item: &Value) -> String {
    format!(
        "S{:02}E{:02}",
        number(&item["season"]),
        number(&item["episode"])
    )
}

fn name(item: &Value) -> String {
    let name = if truthy(&item["name"]) {
        text(&item["name"])
    } else {
        "Série".into()
    };
    name.trim().to_string()
}

/// (title, body), in French.
pub fn message(item: &Value) -> (String, String) {
    let name = name(item);
    match kind(item).as_str() {
        "premiere" => (
            format!("Nouvelle série · {name}"),
            format!("La saison {} est disponible.", shown(&item["season"])),
        ),
        "season" => (
            format!("Nouvelle saison · {name}"),
            format!("La saison {} est disponible.", shown(&item["season"])),
        ),
        _ => {
            let count = if truthy(&item["new_count"]) {
                number(&item["new_count"])
            } else {
                1
            };
            if count > 1 {
                (
                    format!("{count} nouveaux épisodes · {name}"),
                    format!("À partir de {}.", code(item)),
                )
            } else {
                (
                    format!("Nouvel épisode · {name}"),
                    format!("{} est disponible.", code(item)),
                )
            }
        }
    }
}

/// (alerts, seen): the items to announce now, and the keys to remember.
/// `seen` is None before the first check: everything recorded, nothing announced.
pub fn pending(items: &[Value], seen: Option<&[String]>) -> (Vec<Value>, Vec<String>) {
    let items: Vec<&Value> = items
        .iter()
        .filter(|i| i.is_object() && truthy(&i["id"]))
        .collect();
    let keys: Vec<String> = items.iter().map(|i| alert_key(i)).collect();
    let Some(seen) = seen else {
        return (vec![], keys.into_iter().take(MAX_SEEN).collect());
    };
    let alerts = items
        .iter()
        .zip(&keys)
        .filter(|(_, k)| !seen.contains(k))
        .map(|(i, _)| (*i).clone())
        .collect();
    let mut remembered: Vec<String> = vec![];
    for key in keys.iter().chain(seen.iter().filter(|k| keys.contains(k))) {
        if !remembered.contains(key) {
            remembered.push(key.clone());
        }
    }
    remembered.truncate(MAX_SEEN);
    (alerts, remembered)
}

/// What to send: one notification per release, a summary past three.
pub fn notifications(alerts: &[Value]) -> Vec<Value> {
    if alerts.len() <= MAX_ALERTS {
        return alerts
            .iter()
            .map(|item| {
                let (title, body) = message(item);
                json!({"title": title, "body": body, "item": item})
            })
            .collect();
    }
    let names: Vec<String> = alerts
        .iter()
        .take(3)
        .map(|i| {
            if truthy(&i["name"]) {
                text(&i["name"])
            } else {
                "Série".into()
            }
        })
        .collect();
    vec![
        json!({"title": format!("{} séries ont du nouveau", alerts.len()), "body": format!("{}…", names.join(", ")), "item": null}),
    ]
}

/// The system's notifications (Notification Centre on a Mac), given by the window.
pub trait Notifier: Send + Sync {
    /// « authorized », « denied », « not_determined »… or « unknown ».
    fn status(&self) -> String;
    /// Asks the user once; true once answered.
    fn request_permission(&self) -> bool;
    /// One notification; a click opens what `info` names (a series).
    fn send(&self, id: &str, title: &str, body: &str, info: &serde_json::Map<String, Value>);
}

pub const GRANTED: [&str; 3] = ["authorized", "provisional", "ephemeral"];
const WATCH_FIRST: u64 = 90;
const WATCH_EVERY: u64 = 30 * 60;

/// Announces releases not announced yet; returns them. Nothing is sent when
/// the setting is off; the first check only records what is already out.
pub async fn check(
    db: &crate::db::Db,
    meta: &crate::meta::Meta,
    notifier: &dyn Notifier,
) -> Vec<Value> {
    let cfg = tokio::task::spawn_blocking(crate::config::load)
        .await
        .unwrap_or_default();
    if cfg.episode_notifications == Some(false) {
        return vec![];
    }
    let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let (history, watchlist) = (crate::library::history(db), crate::library::watchlist(db));
    let items = crate::series::new_episodes(meta, &history, &watchlist, &today).await;
    let seen: Option<Vec<String>> = cfg.notified_episodes.clone();
    let (alerts, remembered) = pending(&items, seen.as_deref());
    if Some(&remembered) != seen.as_ref() {
        let change = json!({"notified_episodes": remembered});
        let _ = tokio::task::spawn_blocking(move || crate::config::save(&change)).await;
    }
    for note in notifications(&alerts) {
        let item = &note["item"];
        let info: serde_json::Map<String, Value> = ["id", "name", "kind", "season", "episode"]
            .iter()
            .filter(|k| !item[**k].is_null() && item[**k] != json!(""))
            .map(|k| (k.to_string(), json!(text(&item[*k]))))
            .collect();
        let id = if item.is_null() {
            format!("kino-summary-{}", crate::db::now() as i64)
        } else {
            alert_key(item)
        };
        notifier.send(&id, &text(&note["title"]), &text(&note["body"]), &info);
    }
    alerts
}

/// While KINO is open: permission asked once, then a look every 30 minutes.
pub fn watch(
    db: std::sync::Arc<crate::db::Db>,
    meta: crate::meta::Meta,
    notifier: std::sync::Arc<dyn Notifier>,
) {
    tokio::spawn(async move {
        let on = tokio::task::spawn_blocking(crate::config::load)
            .await
            .ok()
            .and_then(|c| c.episode_notifications)
            != Some(false);
        if on {
            let asking = notifier.clone();
            let _ = tokio::task::spawn_blocking(move || asking.request_permission()).await;
        }
        let mut wait = WATCH_FIRST;
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(wait)).await;
            wait = WATCH_EVERY;
            check(&db, &meta, notifier.as_ref()).await;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn announce_once() {
        let a = json!({"id": "tt1", "name": "Severance", "newest": "2026-11-14", "season": 2, "episode": 6, "new_count": 2});
        let b = json!({"id": "tt2", "name": "Andor", "kind": "season", "season": 3});
        assert_eq!(alert_key(&a), "tt1:followed:2026-11-14");
        assert_eq!(alert_key(&b), "tt2:season:s3");
        assert_eq!(
            message(&a),
            (
                "2 nouveaux épisodes · Severance".into(),
                "À partir de S02E06.".into()
            )
        );
        assert_eq!(message(&b).1, "La saison 3 est disponible.");
        let items = vec![a.clone(), b.clone()];
        let (none, seen) = pending(&items, None);
        assert!(none.is_empty() && seen.len() == 2);
        let c = json!({"id": "tt3", "name": "Dark", "kind": "premiere", "season": 1});
        let (alerts, remembered) = pending(&[a, b, c.clone()], Some(&seen));
        assert_eq!(alerts, vec![c]);
        assert_eq!(remembered.len(), 3);
        let many: Vec<Value> = (0..4)
            .map(|i| json!({"id": format!("tt{i}"), "name": format!("S{i}")}))
            .collect();
        assert_eq!(
            notifications(&many),
            vec![json!({"title": "4 séries ont du nouveau", "body": "S0, S1, S2…", "item": null})]
        );
    }
}
