//! Diagnostic: what works in KINO, and what to do about the rest, in plain
//! words. `gather` only reads (its one network use is the connection and
//! Debrid account check that Paramètres already makes); `checks` turns the
//! facts into lines, `report` into a text to copy without any key, link,
//! e-mail address or user name.

use std::path::{Path, PathBuf};

use regex::Regex;
use serde_json::{Value, json};

use crate::play::{text, truthy};

const LOW_SPACE: f64 = 5e9; // under this, a download will not fit
const SHORT_SPACE: f64 = 25e9; // a 4K film often weighs 20 Go or more
const SHORT_PREMIUM: f64 = 7.0 * 86400.0;

fn item(key: &str, label: &str, state: &str, summary: &str, advice: &str, section: &str) -> Value {
    let mut item =
        json!({"id": key, "label": label, "state": state, "summary": summary, "advice": advice});
    if !section.is_empty() {
        item["section"] = json!(section);
    }
    item
}

fn plural(n: i64) -> &'static str {
    if n > 1 { "s" } else { "" }
}

fn days(seconds: f64) -> String {
    let days = ((seconds / 86400.0).round_ties_even() as i64).max(1);
    format!("{days} jour{}", plural(days))
}

fn gigabytes(size: f64) -> String {
    let value = size / 1e9;
    if value >= 10.0 {
        format!("{value:.0} Go")
    } else {
        format!("{value:.1} Go").replace('.', ",")
    }
}

fn ago(seconds: f64) -> String {
    let minutes = (seconds / 60.0).floor() as i64;
    match minutes {
        m if m < 1 => "à l’instant".into(),
        m if m < 60 => format!("il y a {m} min"),
        m if m / 60 < 48 => format!("il y a {} h", m / 60),
        m => format!("il y a {} jours", m / 60 / 24),
    }
}

fn internet(f: &Value) -> Value {
    if truthy(&f["online"]) {
        return item("internet", "Internet", "ok", "Internet répond.", "", "");
    }
    item(
        "internet",
        "Internet",
        "problem",
        "Pas de connexion à internet.",
        "Vérifiez le Wi-Fi. Vos fichiers hors ligne restent disponibles.",
        "",
    )
}

fn debrid(f: &Value) -> Value {
    let d = &f["debrid"];
    let name = text(&d["provider"]);
    let label = format!("Compte {name}");
    if !truthy(&d["has_token"]) {
        return item(
            "debrid",
            &label,
            "problem",
            &format!("Aucune clé {name} enregistrée."),
            "Collez votre clé dans Compte Debrid : sans elle, KINO ne peut pas lire les films et les séries.",
            "settings-account",
        );
    }
    match d["status"].as_str().unwrap_or("") {
        "skipped" => {
            return item(
                "debrid",
                &label,
                "warning",
                "Compte non vérifié : pas de connexion à internet.",
                "Relancez le diagnostic quand internet répond.",
                "",
            );
        }
        "invalid" => {
            return item(
                "debrid",
                &label,
                "problem",
                &format!("{name} refuse la clé enregistrée."),
                &format!(
                    "Elle a peut-être changé : copiez-la de nouveau depuis votre compte {name} et collez-la dans Compte Debrid."
                ),
                "settings-account",
            );
        }
        "connected" => {}
        _ => {
            return item(
                "debrid",
                &label,
                "warning",
                &format!("{name} ne répond pas pour le moment."),
                "Réessayez dans quelques minutes ; si cela dure, le service a peut-être une panne.",
                "",
            );
        }
    }
    let premium = d["premium"].as_f64();
    match premium {
        Some(p) if p <= 0.0 => item(
            "debrid",
            &label,
            "problem",
            &format!("Votre abonnement {name} est terminé."),
            &format!(
                "Renouvelez-le sur le site de {name} : les sources ne se lancent plus sans lui."
            ),
            "",
        ),
        Some(p) if p < SHORT_PREMIUM => item(
            "debrid",
            &label,
            "warning",
            &format!("Connecté, mais l’abonnement se termine dans {}.", days(p)),
            &format!("Pensez à le renouveler sur le site de {name}."),
            "",
        ),
        Some(p) => item(
            "debrid",
            &label,
            "ok",
            &format!("Connecté, abonnement encore {}.", days(p)),
            "",
            "",
        ),
        None => item("debrid", &label, "ok", "Connecté.", "", ""),
    }
}

fn trakt(f: &Value) -> Value {
    let t = &f["trakt"];
    if !truthy(&t["connected"]) {
        return item(
            "trakt",
            "Trakt",
            "off",
            "Non connecté.",
            "Facultatif : reliez Trakt dans Synchronisation pour suivre vos visionnages.",
            "settings-sync",
        );
    }
    if let Some(expires) = t["expires_at"].as_f64().filter(|e| *e != 0.0)
        && expires <= f["now"].as_f64().unwrap_or(0.0)
    {
        return item(
            "trakt",
            "Trakt",
            "warning",
            "La connexion Trakt a expiré.",
            "Déconnectez puis reconnectez Trakt dans Synchronisation.",
            "settings-sync",
        );
    }
    item("trakt", "Trakt", "ok", "Connecté.", "", "")
}

fn drive(f: &Value) -> Value {
    let d = &f["drive"];
    if !truthy(&d["found"]) {
        return item(
            "drive",
            "Google Drive",
            "off",
            "Aucun dossier KINO dans Google Drive.",
            "Facultatif : avec Google Drive pour ordinateur et un dossier « KINO » dans Mon Drive, vos listes et votre historique suivent sur vos autres appareils.",
            "",
        );
    }
    match d["sync_file"].as_str().unwrap_or("") {
        "unreadable" => item(
            "drive",
            "Google Drive",
            "problem",
            "Le fichier de synchronisation est illisible.",
            "Google Drive est peut-être en train de le copier : attendez la fin, puis relancez KINO.",
            "settings-sync",
        ),
        "missing" => item(
            "drive",
            "Google Drive",
            "warning",
            "Dossier trouvé, pas encore synchronisé.",
            "Lancez une synchronisation dans Synchronisation.",
            "settings-sync",
        ),
        _ => {
            let since = f["now"].as_f64().unwrap_or(0.0) - d["modified"].as_f64().unwrap_or(0.0);
            item(
                "drive",
                "Google Drive",
                "ok",
                &format!("Dernière synchronisation {}.", ago(since)),
                "",
                "",
            )
        }
    }
}

fn player(f: &Value) -> Value {
    let p = &f["player"];
    if !truthy(&p["ffmpeg"]) {
        return item(
            "player",
            "Lecteur",
            "problem",
            "Un composant de lecture manque (FFmpeg).",
            "Réinstallez KINO depuis Mises à jour, ou revenez à la version précédente.",
            "settings-updates",
        );
    }
    match (truthy(&p["native_available"]), truthy(&p["native_on"])) {
        (true, true) => item("player", "Lecteur", "ok", "Lecteur natif prêt.", "", ""),
        (true, false) => item(
            "player",
            "Lecteur",
            "ok",
            "Lecteur intégré de KINO.",
            "Le lecteur natif lit plus de formats : activez « Lecteur natif » dans Lecture.",
            "settings-player",
        ),
        _ => item(
            "player",
            "Lecteur",
            "ok",
            "Lecteur intégré de KINO (le lecteur natif n’existe que dans l’app pour Mac).",
            "",
            "",
        ),
    }
}

fn notifications(f: &Value) -> Value {
    let n = &f["notifications"];
    let label = "Notifications";
    if !truthy(&n["available"]) {
        return item(
            "notifications",
            label,
            "off",
            "Disponibles seulement dans l’app KINO pour Mac.",
            "",
            "",
        );
    }
    if !truthy(&n["enabled"]) {
        return item(
            "notifications",
            label,
            "off",
            "Désactivées dans Paramètres.",
            "Activez « Notifications des nouveaux épisodes » dans Lecture pour être prévenu.",
            "settings-player",
        );
    }
    match n["status"].as_str().unwrap_or("") {
        "authorized" | "provisional" | "ephemeral" => item(
            "notifications",
            label,
            "ok",
            "Autorisées pour les nouveaux épisodes.",
            "",
            "",
        ),
        "denied" => item(
            "notifications",
            label,
            "warning",
            "macOS bloque les notifications de KINO.",
            "Autorisez-les dans Réglages Système → Notifications → KINO.",
            "",
        ),
        "not_determined" => item(
            "notifications",
            label,
            "warning",
            "macOS n’a pas encore reçu de réponse.",
            "Acceptez la demande d’autorisation de KINO quand elle apparaît.",
            "",
        ),
        _ => item(
            "notifications",
            label,
            "warning",
            "État des notifications inconnu.",
            "",
            "",
        ),
    }
}

fn storage(f: &Value) -> Value {
    let s = &f["storage"];
    let (label, folder) = ("Espace disque", text(&s["folder"]));
    if !truthy(&s["exists"]) {
        return item(
            "storage",
            label,
            "problem",
            &format!("Le dossier de téléchargement {folder} n’existe pas."),
            "Choisissez un autre dossier dans Stockage.",
            "settings-storage",
        );
    }
    if !truthy(&s["writable"]) {
        return item(
            "storage",
            label,
            "problem",
            &format!("KINO ne peut pas écrire dans {folder}."),
            "Choisissez un autre dossier dans Stockage.",
            "settings-storage",
        );
    }
    let free = s["free_bytes"].as_f64();
    let missing = s["missing_files"].as_i64().unwrap_or(0);
    let lost = if missing > 0 {
        format!(
            " {missing} fichier{} hors ligne introuvable{}.",
            plural(missing),
            plural(missing)
        )
    } else {
        String::new()
    };
    match free {
        Some(free) if free < LOW_SPACE => item(
            "storage",
            label,
            "problem",
            &format!("Plus que {} libres.{lost}", gigabytes(free)),
            "Libérez de la place ou retirez des fichiers dans Bibliothèque → Hors ligne.",
            "",
        ),
        Some(free) if free < SHORT_SPACE => item(
            "storage",
            label,
            "warning",
            &format!("{} libres.{lost}", gigabytes(free)),
            "Un film 4K pèse souvent 20 Go ou plus : libérez de la place avant d’en garder un hors ligne.",
            "",
        ),
        _ if missing > 0 => item(
            "storage",
            label,
            "warning",
            &format!("{} libres.{lost}", gigabytes(free.unwrap_or(0.0))),
            "Le fichier a été déplacé ou supprimé : retirez-le de Bibliothèque → Hors ligne, puis gardez-le de nouveau.",
            "",
        ),
        _ => item(
            "storage",
            label,
            "ok",
            &format!("{} libres dans {folder}.", gigabytes(free.unwrap_or(0.0))),
            "",
            "",
        ),
    }
}

fn database(f: &Value) -> Value {
    let b = &f["database"];
    if !truthy(&b["ok"]) {
        return item(
            "database",
            "Données",
            "problem",
            "La base de données de KINO a un problème.",
            "Fermez et rouvrez KINO. Si cela continue, revenez à la version précédente dans Mises à jour : une copie de vos données est faite avant chaque installation.",
            "settings-updates",
        );
    }
    item(
        "database",
        "Données",
        "ok",
        &format!(
            "Base saine : {} dans Ma Liste, {} dans l’historique.",
            b["watchlist"], b["history"]
        ),
        "",
        "",
    )
}

fn errors(f: &Value) -> Value {
    let day = f["errors"]["day"].as_i64().unwrap_or(0);
    if day == 0 {
        return item(
            "errors",
            "Erreurs",
            "ok",
            "Aucune erreur ces dernières 24 heures.",
            "",
            "",
        );
    }
    item(
        "errors",
        "Erreurs",
        "warning",
        &format!("{day} erreur{} ces dernières 24 heures.", plural(day)),
        "Copiez le rapport : il liste les dernières, sans clé ni lien.",
        "",
    )
}

pub fn checks(facts: &Value) -> Vec<Value> {
    [
        internet,
        debrid,
        trakt,
        drive,
        player,
        notifications,
        storage,
        database,
        errors,
    ]
    .iter()
    .map(|check| check(facts))
    .collect()
}

fn home() -> String {
    crate::paths::home().display().to_string()
}

/// Last guard for the copied text: no key, link, e-mail or home folder.
fn scrub(text: &str, secrets: &[String]) -> String {
    static LINK: std::sync::LazyLock<Regex> =
        std::sync::LazyLock::new(|| Regex::new(r"(?i)\b[a-z][a-z0-9+.-]*://\S+").unwrap());
    static MAIL: std::sync::LazyLock<Regex> =
        std::sync::LazyLock::new(|| Regex::new(r"[\w.+-]+@[\w-]+\.[\w.-]+").unwrap());
    let mut out = text.to_string();
    for secret in secrets.iter().filter(|s| s.chars().count() >= 6) {
        out = out.replace(secret.as_str(), "[masqué]");
    }
    out = LINK.replace_all(&out, "[lien masqué]").into_owned();
    out = MAIL.replace_all(&out, "[adresse masquée]").into_owned();
    let home = home();
    if home.len() > 1 {
        out = out.replace(&home, "~");
    }
    out
}

fn local_time(at: f64, pattern: &str) -> String {
    chrono::DateTime::from_timestamp(at as i64, 0)
        .map(|t| t.with_timezone(&chrono::Local).format(pattern).to_string())
        .unwrap_or_default()
}

pub fn report(facts: &Value, items: &[Value], secrets: &[String]) -> String {
    let now = facts["now"].as_f64().unwrap_or(0.0);
    let mut lines = vec![
        format!("Diagnostic KINO · {}", local_time(now, "%d/%m/%Y à %H:%M")),
        format!(
            "KINO {} · {}",
            text(&facts["version"]),
            text(&facts["system"])
        ),
        String::new(),
    ];
    for item in items {
        let state = item["state"].as_str().unwrap_or("");
        let mark = match state {
            "ok" => "OK",
            "warning" => "À VÉRIFIER",
            "problem" => "PROBLÈME",
            _ => "DÉSACTIVÉ",
        };
        lines.push(format!(
            "[{mark}] {} : {}",
            text(&item["label"]),
            text(&item["summary"])
        ));
        if truthy(&item["advice"]) && (state == "warning" || state == "problem") {
            lines.push(format!("    → {}", text(&item["advice"])));
        }
    }
    let recent = facts["errors"]["recent"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    if !recent.is_empty() {
        lines.extend([String::new(), "Dernières erreurs :".into()]);
        for entry in recent {
            let when = local_time(entry["t"].as_f64().unwrap_or(0.0), "%d/%m %H:%M");
            lines.push(format!(
                "  {when} · {} · {} : {}",
                text(&entry["where"]),
                text(&entry["kind"]),
                text(&entry["message"])
            ));
        }
    }
    lines.extend([
        String::new(),
        "Ce rapport ne contient ni clé, ni lien, ni adresse e-mail, ni nom de compte.".into(),
    ]);
    scrub(&lines.join("\n"), secrets)
}

fn short_path(path: &Path) -> String {
    let text = path.display().to_string();
    let home = home();
    match text.strip_prefix(&home) {
        Some(rest) if home.len() > 1 => format!("~{rest}"),
        _ => text,
    }
}

fn drive_facts(folder: Option<PathBuf>) -> Value {
    let Some(folder) = folder else {
        return json!({"found": false});
    };
    let file = folder.join("kino_sync.json");
    if !file.exists() {
        return json!({"found": true, "sync_file": "missing"});
    }
    let readable = std::fs::read_to_string(&file)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .is_some_and(|v| v.is_object());
    if !readable {
        return json!({"found": true, "sync_file": "unreadable"});
    }
    let modified = std::fs::metadata(&file)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    json!({"found": true, "sync_file": "ok", "modified": modified})
}

#[cfg(unix)]
fn can_write(folder: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let Ok(path) = std::ffi::CString::new(folder.as_os_str().as_bytes()) else {
        return false;
    };
    // SAFETY: a valid C string; access() only reads the permissions.
    unsafe { libc::access(path.as_ptr(), libc::W_OK) == 0 }
}

#[cfg(not(unix))]
fn can_write(folder: &Path) -> bool {
    std::fs::metadata(folder).is_ok_and(|m| !m.permissions().readonly())
}

fn storage_facts(folder: &str, offline: &[Value]) -> Value {
    let folder = PathBuf::from(if let Some(rest) = folder.strip_prefix("~/") {
        format!("{}/{rest}", home())
    } else {
        folder.to_string()
    });
    let exists = folder.is_dir();
    let probe = folder.ancestors().find(|p| p.is_dir());
    let free = probe.and_then(|p| fs4::available_space(p).ok());
    let writable = exists && can_write(&folder);
    let missing = offline
        .iter()
        .filter(|i| i["status"] == "completed" && !truthy(&i["available"]))
        .count();
    json!({"folder": short_path(&folder), "exists": exists, "writable": writable, "free_bytes": free, "missing_files": missing})
}

fn database_facts(db: &crate::db::Db) -> Value {
    let read = db.read(|c| {
        let ok: String = c.query_row("PRAGMA quick_check", [], |r| r.get(0))?;
        let watchlist: i64 = c.query_row("SELECT COUNT(*) FROM watchlist", [], |r| r.get(0))?;
        let history: i64 = c.query_row("SELECT COUNT(*) FROM watch_history", [], |r| r.get(0))?;
        Ok((ok == "ok", watchlist, history))
    });
    match read {
        Ok((ok, watchlist, history)) => {
            json!({"ok": ok, "watchlist": watchlist, "history": history})
        }
        Err(_) => json!({"ok": false, "watchlist": 0, "history": 0}),
    }
}

fn system() -> String {
    let name = if cfg!(target_os = "macos") {
        std::process::Command::new("sw_vers")
            .arg("-productVersion")
            .output()
            .ok()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .filter(|v| !v.is_empty())
            .map(|v| format!("macOS {v}"))
            .unwrap_or_else(|| "Darwin".into())
    } else if cfg!(windows) {
        "Windows".into()
    } else {
        "Linux".into()
    };
    let machine = match std::env::consts::ARCH {
        "aarch64" if cfg!(target_os = "macos") => "arm64",
        "x86_64" if cfg!(windows) => "AMD64",
        other => other,
    };
    format!("{name} · {machine}")
}

/// What the program that runs the window offers.
pub struct Window {
    pub native_player: bool,
    /// macOS's answer for KINO's notifications, when the window can ask.
    pub notifications: Option<String>,
}

/// The facts, read now, and the secrets the report must not show.
pub async fn gather(state: &crate::server::AppState, window: &Window) -> (Value, Vec<String>) {
    let cfg = tokio::task::spawn_blocking(crate::config::load)
        .await
        .unwrap_or_default();
    let provider = cfg.debrid_provider.as_str().to_string();
    let name = crate::debrid::PROVIDERS
        .iter()
        .find(|p| p.0 == provider)
        .map_or("Real-Debrid", |p| p.1);
    let token = cfg.rd_token.clone();
    let online = crate::network::online(true).await;
    let mut debrid = json!({"provider": name, "has_token": !token.is_empty(), "status": "skipped", "premium": null});
    if !token.is_empty() && online {
        let (user, status) = state.accounts.status(&state.net, &provider, &token).await;
        debrid["status"] = json!(status);
        if let Some(premium) = user.get("premium").filter(|p| !p.is_null()).and_then(|p| {
            p.as_i64()
                .or_else(|| p.as_f64().map(|f| f as i64))
                .or_else(|| text(p).trim().parse().ok())
        }) {
            debrid["premium"] = json!(premium);
        }
    }
    let tokens = state
        .db
        .get_config("trakt_tokens")
        .filter(Value::is_object)
        .unwrap_or(json!({}));
    let number = |v: &Value| v.as_f64().or_else(|| text(v).trim().parse().ok());
    let expires = match (number(&tokens["created_at"]), number(&tokens["expires_in"])) {
        (Some(c), Some(e)) if truthy(&tokens["created_at"]) && truthy(&tokens["expires_in"]) => {
            json!(c + e)
        }
        _ => Value::Null,
    };
    let folder = Some(cfg.download_dir.clone())
        .filter(|d| !d.is_empty())
        .unwrap_or_else(crate::config::default_download_dir);
    let offline: Vec<Value> = state.offline.list().into_iter().map(|(_, v)| v).collect();
    let db = state.db.clone();
    let (database, drive) = tokio::task::spawn_blocking(move || {
        (database_facts(&db), drive_facts(crate::gdrive::find_dir()))
    })
    .await
    .unwrap_or((
        json!({"ok": false, "watchlist": 0, "history": 0}),
        json!({"found": false}),
    ));
    let day_ago = crate::db::now() - 86400.0;
    let notifications_on = cfg.episode_notifications != Some(false);
    let facts = json!({
        "now": crate::db::now(),
        "version": crate::backup::app_version(),
        "system": system(),
        "online": online,
        "debrid": debrid,
        "trakt": {"connected": truthy(&tokens["access_token"]), "expires_at": expires},
        "drive": drive,
        "player": {"ffmpeg": crate::intro::find_tool("ffmpeg").is_some(), "native_available": window.native_player,
                   "native_on": cfg.native_player.unwrap_or(window.native_player)},
        "notifications": {"available": window.notifications.is_some(), "enabled": notifications_on,
                          "status": window.notifications.clone().unwrap_or_else(|| "unavailable".into())},
        "storage": storage_facts(&folder, &offline),
        "database": database,
        "errors": {"day": crate::errorlog::LOG.recent(10_000, Some(day_ago)).len(), "recent": crate::errorlog::LOG.recent(10, None)},
    });
    let mut secrets = vec![token, cfg.tmdb_key().to_string()];
    secrets.extend(cfg.provider_tokens.values().cloned());
    secrets.extend(
        tokens
            .as_object()
            .into_iter()
            .flatten()
            .filter(|(k, _)| k.contains("token"))
            .map(|(_, v)| text(v)),
    );
    (facts, secrets)
}

pub async fn run(state: &crate::server::AppState, window: &Window) -> Value {
    let (facts, secrets) = gather(state, window).await;
    let items = checks(&facts);
    let report = report(&facts, &items, &secrets);
    json!({"version": facts["version"], "system": facts["system"], "items": items, "report": report})
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts() -> Value {
        json!({"now": 1_700_000_000.0, "version": "9.9.9", "system": "macOS 26.1 · arm64", "online": true,
               "debrid": {"provider": "Real-Debrid", "has_token": true, "status": "connected", "premium": 3 * 86400},
               "trakt": {"connected": true, "expires_at": 1_600_000_000.0},
               "drive": {"found": true, "sync_file": "ok", "modified": 1_700_000_000.0 - 7300.0},
               "player": {"ffmpeg": true, "native_available": false, "native_on": false},
               "notifications": {"available": false, "enabled": true, "status": "unavailable"},
               "storage": {"folder": "~/Movies/KINO", "exists": true, "writable": true, "free_bytes": 12.5e9, "missing_files": 2},
               "database": {"ok": true, "watchlist": 12, "history": 40},
               "errors": {"day": 1, "recent": [{"t": 1_700_000_000.0, "where": "serveur GET /api/x", "kind": "ValueError", "message": "https://x/y?k=s3cr3tkey1"}]}})
    }

    #[test]
    fn lines_and_report() {
        let items = checks(&facts());
        let summary = |id: &str| {
            items
                .iter()
                .find(|i| i["id"] == id)
                .map(|i| (text(&i["state"]), text(&i["summary"])))
                .unwrap()
        };
        assert_eq!(
            summary("debrid"),
            (
                "warning".into(),
                "Connecté, mais l’abonnement se termine dans 3 jours.".into()
            )
        );
        assert_eq!(summary("trakt").0, "warning");
        assert_eq!(summary("drive").1, "Dernière synchronisation il y a 2 h.");
        assert_eq!(
            summary("storage"),
            (
                "warning".into(),
                "12 Go libres. 2 fichiers hors ligne introuvables.".into()
            )
        );
        assert_eq!(
            summary("database").1,
            "Base saine : 12 dans Ma Liste, 40 dans l’historique."
        );
        let text = report(&facts(), &items, &["s3cr3tkey1".into()]);
        assert!(text.contains("[À VÉRIFIER] Erreurs : 1 erreur ces dernières 24 heures."));
        assert!(text.contains("ValueError : [lien masqué]") && !text.contains("s3cr3t"));
        assert_eq!(gigabytes(55e9), "55 Go");
        assert_eq!(days(86400.0 * 0.2), "1 jour");
    }
}
