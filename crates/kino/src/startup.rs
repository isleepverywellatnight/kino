//! What KINO does once at launch, when the desktop app starts this server
//! (KINO_STARTUP_TASKS=1): the cache tidied, the Drive sync, the debrid account
//! and the connection checked, finished series looked at again, the suggestion
//! pool read again when a month old, and the daily backup.

use std::time::Duration;

use serde_json::{Value, json};

use crate::server::Shared;

pub fn run(state: Shared) {
    let s = state.clone();
    tokio::spawn(async move {
        let db = s.db.clone();
        let _ = crate::db::spawn_blocking(move || {
            db.purge_expired(true);
            crate::gdrive::sync(&db, crate::gdrive::find_dir());
        })
        .await;
    });
    let s = state.clone();
    tokio::spawn(async move {
        crate::network::online(true).await;
        let cfg = crate::db::spawn_blocking(crate::config::load)
            .await
            .unwrap_or_default();
        let (provider, token) = crate::sources::search::provider_and_token(&cfg);
        if !token.is_empty() {
            s.accounts.status(&s.net, &provider, &token).await;
        }
    });
    let s = state.clone();
    tokio::spawn(async move { revive_finished_series(&s).await });
    let s = state.clone();
    tokio::spawn(async move {
        // The pool read and measured ahead: the first « Pour vous » is quick.
        let warm = s.clone();
        let _ = crate::db::spawn_blocking(move || warm.suggestions.warm(&warm.db)).await;
        s.suggestions
            .refresh_pool(&s.db, &s.net, crate::meta::CINEMETA, false)
            .await;
    });
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(90)).await;
        loop {
            let made = crate::db::spawn_blocking(|| {
                let backups = crate::backup::Backups::standard();
                if backups.due() && backups.db_path.exists() {
                    Some(backups.make(false))
                } else {
                    None
                }
            })
            .await;
            if let Ok(Some(Err(e))) = made {
                crate::errorlog::LOG.record("sauvegarde du jour", "Erreur", &format!("{e:?}"), &[]);
            }
            tokio::time::sleep(Duration::from_secs(3600)).await;
        }
    });
}

/// A series watched to its last episode returns to « À continuer » once new
/// episodes are listed (40 at most, one light request each per day).
async fn revive_finished_series(s: &Shared) -> usize {
    let truthy = |v: &Value| {
        !matches!(v, Value::Null | Value::Bool(false)) && v != &json!(0) && v != &json!("")
    };
    let finished: Vec<Value> = crate::library::history(&s.db)
        .into_iter()
        .filter(|i| i["type"] == "series" && truthy(&i["series_finished"]))
        .take(40)
        .collect();
    let number = |v: &Value| {
        v.as_i64()
            .or_else(|| v.as_str().and_then(|t| t.parse().ok()))
            .unwrap_or(1)
    };
    let mut revived = 0;
    for item in finished {
        let id = item["id"].as_str().unwrap_or("").to_string();
        let (season, episode, done) = s
            .meta
            .next_episode(&id, number(&item["season"]), number(&item["episode"]), true)
            .await;
        let fields =
            json!({"series_finished": false, "next_season": season, "next_episode": episode});
        if !done
            && crate::library::patch_history(
                &s.db,
                &id,
                fields.as_object().unwrap_or(&serde_json::Map::new()),
            )
        {
            revived += 1;
        }
    }
    revived
}
