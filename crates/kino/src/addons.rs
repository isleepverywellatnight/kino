//! Add-ons: the Stremio add-ons the user adds by their address (more sources,
//! catalogues), and KINO's built-in Nyaa source that can be switched off.
//! Nothing of an add-on runs on this computer: KINO only reads its answers.

use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::db::Db;
use crate::net::Net;

const STORE_KEY: &str = "stremio_addons_v1";
const MAX_ADDONS: usize = 30;
const MAX_CATALOGS: usize = 8;
const TYPES: [&str; 2] = ["movie", "series"];
static LOCK: Mutex<()> = Mutex::new(());
static IMDB: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^tt\d{1,12}$").unwrap());

/// A refusal shown to the user as is.
#[derive(Debug, PartialEq)]
pub struct AddonError(pub String);

fn refuse<T>(text: &str) -> Result<T, AddonError> {
    Err(AddonError(text.into()))
}

fn text(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

fn truthy(v: &Value) -> bool {
    !matches!(v, Value::Null | Value::Bool(false))
        && v != &json!(0)
        && v != &json!("")
        && v != &json!([])
        && v != &json!({})
}

fn cut(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// The add-on's base address from what the user pasted (…/manifest.json, stremio://…).
pub fn base_of(address: &str) -> Result<String, AddonError> {
    let mut url = address.trim().to_string();
    if let Some(rest) = url.strip_prefix("stremio://") {
        url = format!("https://{rest}");
    }
    let parsed = reqwest::Url::parse(&url).ok().filter(|u| {
        ["http", "https"].contains(&u.scheme()) && u.host_str().is_some_and(|h| !h.is_empty())
    });
    let Some(parsed) = parsed else {
        return refuse("Collez l'adresse d'un addon : elle finit par manifest.json.");
    };
    let path = parsed.path();
    let path = path
        .strip_suffix("/manifest.json")
        .unwrap_or(path)
        .trim_end_matches('/');
    let port = parsed.port().map(|p| format!(":{p}")).unwrap_or_default();
    let auth = match (parsed.username(), parsed.password()) {
        ("", None) => String::new(),
        (user, None) => format!("{user}@"),
        (user, Some(password)) => format!("{user}:{password}@"),
    };
    Ok(format!(
        "{}://{auth}{}{port}{path}",
        parsed.scheme(),
        parsed.host_str().unwrap_or("")
    ))
}

/// What KINO keeps of a manifest: enough to query the add-on, nothing else.
pub fn entry(base: &str, manifest: &Value) -> Result<Value, AddonError> {
    if !manifest.is_object() || !truthy(&manifest["id"]) || !truthy(&manifest["name"]) {
        return refuse("Cette adresse ne répond pas comme un addon.");
    }
    let resources: Vec<String> = manifest["resources"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|r| {
            if r.is_object() {
                r["name"].as_str()
            } else {
                r.as_str()
            }
        })
        .filter(|n| *n == "stream" || *n == "catalog")
        .map(str::to_string)
        .collect();
    if resources.is_empty() {
        return refuse(
            "Cet addon ne propose ni sources ni catalogues : KINO ne peut pas s'en servir.",
        );
    }
    let declared: Vec<String> = manifest["types"]
        .as_array()
        .into_iter()
        .flatten()
        .map(text)
        .collect();
    let types: Vec<&str> = TYPES
        .iter()
        .copied()
        .filter(|t| declared.iter().any(|d| d == t))
        .collect();
    let mut catalogs = Vec::new();
    for catalog in manifest["catalogs"].as_array().into_iter().flatten() {
        let kind = catalog["type"].as_str().unwrap_or("");
        if !catalog.is_object() || !TYPES.contains(&kind) || !truthy(&catalog["id"]) {
            continue;
        }
        // A catalogue that cannot be read without a search or a genre is left out.
        let required = catalog["extra"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|e| e.is_object())
            .any(|e| truthy(&e["isRequired"]))
            || catalog["extraRequired"]
                .as_array()
                .is_some_and(|r| r.iter().any(|x| x == "search"));
        if required {
            continue;
        }
        let name = if truthy(&catalog["name"]) {
            text(&catalog["name"])
        } else {
            text(&manifest["name"])
        };
        catalogs.push(json!({"type": kind, "id": text(&catalog["id"]), "name": name}));
    }
    let prefixes = &manifest["idPrefixes"];
    let imdb = !truthy(prefixes)
        || prefixes
            .as_array()
            .is_some_and(|p| p.iter().any(|x| text(x).starts_with("tt")));
    let has = |r: &str| resources.iter().any(|x| x == r);
    Ok(json!({
        "id": cut(&text(&manifest["id"]), 120), "name": cut(&text(&manifest["name"]), 80),
        "version": cut(&if truthy(&manifest["version"]) { text(&manifest["version"]) } else { String::new() }, 20),
        "description": cut(&if truthy(&manifest["description"]) { text(&manifest["description"]) } else { String::new() }, 300),
        "base": base, "streams": has("stream"), "types": types,
        // Sources only for titles KINO knows by their IMDb number (tt…).
        "imdb": imdb,
        "catalogs": if has("catalog") { catalogs.into_iter().take(MAX_CATALOGS).collect::<Vec<_>>() } else { vec![] },
        "enabled": true,
    }))
}

pub fn installed(db: &Db) -> Vec<Value> {
    db.get_config(STORE_KEY)
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default()
        .into_iter()
        .filter(|a| a.is_object() && truthy(&a["id"]) && truthy(&a["base"]))
        .collect()
}

fn save(db: &Db, addons: Vec<Value>) {
    let _ = db.save_config(
        STORE_KEY,
        &Value::Array(addons.into_iter().take(MAX_ADDONS).collect()),
    );
}

/// Reads the manifest at this address and keeps the add-on; the same add-on is updated.
pub async fn add(db: &Db, net: &Net, address: &str) -> Result<Value, AddonError> {
    let base = base_of(address)?;
    let headers = [
        ("User-Agent", crate::net::BROWSER),
        ("Accept", "application/json, text/plain, */*"),
    ];
    let manifest = net
        .request_json(
            reqwest::Method::GET,
            &format!("{base}/manifest.json"),
            &headers,
            None,
            Duration::from_secs(10),
            1,
        )
        .await
        .map_err(|_| {
            AddonError("Cet addon ne répond pas. Vérifiez l'adresse, puis réessayez.".into())
        })?;
    let found = entry(&base, &manifest)?;
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let mut addons: Vec<Value> = installed(db)
        .into_iter()
        .filter(|a| a["id"] != found["id"] && a["base"] != base.as_str())
        .collect();
    if addons.len() >= MAX_ADDONS {
        return refuse(&format!(
            "KINO garde {MAX_ADDONS} addons au plus : retirez-en un d'abord."
        ));
    }
    addons.push(found.clone());
    save(db, addons);
    Ok(found)
}

pub fn remove(db: &Db, id: &str) -> Result<(), AddonError> {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let addons = installed(db);
    let kept: Vec<Value> = addons.iter().filter(|a| a["id"] != id).cloned().collect();
    if kept.len() == addons.len() {
        return refuse("Addon inconnu.");
    }
    save(db, kept);
    Ok(())
}

pub fn set_enabled(db: &Db, id: &str, enabled: bool) -> Result<(), AddonError> {
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let mut addons = installed(db);
    let Some(addon) = addons.iter_mut().find(|a| a["id"] == id) else {
        return refuse("Addon inconnu.");
    };
    addon["enabled"] = json!(enabled);
    save(db, addons);
    Ok(())
}

/// Every catalogue of the enabled add-ons: {addon, addon_name, type, id, name}.
pub fn catalogs(db: &Db) -> Vec<Value> {
    let mut found = Vec::new();
    for addon in installed(db).iter().filter(|a| truthy(&a["enabled"])) {
        for catalog in addon["catalogs"].as_array().into_iter().flatten() {
            let mut row = Map::new();
            row.insert("addon".into(), addon["id"].clone());
            row.insert("addon_name".into(), addon["name"].clone());
            row.extend(catalog.as_object().cloned().unwrap_or_default());
            found.push(Value::Object(row));
        }
    }
    found
}

pub enum CatalogError {
    Unknown,
    Down,
}

/// The titles of one catalogue that KINO can open (IMDb numbers), 60 at most.
pub async fn catalog_items(
    db: &Db,
    net: &Net,
    addon_id: &str,
    kind: &str,
    catalog_id: &str,
) -> Result<Vec<Value>, CatalogError> {
    let addon = installed(db)
        .into_iter()
        .find(|a| a["id"] == addon_id && truthy(&a["enabled"]))
        .ok_or(CatalogError::Unknown)?;
    if !addon["catalogs"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|c| c["type"] == kind && c["id"] == catalog_id)
    {
        return Err(CatalogError::Unknown);
    }
    let quoted: String = catalog_id
        .bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"_.-~".contains(&b) {
                (b as char).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect();
    let url = format!("{}/catalog/{kind}/{quoted}.json", text(&addon["base"]));
    let headers = [
        ("User-Agent", crate::net::BROWSER),
        ("Accept", "application/json, text/plain, */*"),
    ];
    let data = net
        .request_json(
            reqwest::Method::GET,
            &url,
            &headers,
            None,
            Duration::from_secs(10),
            1,
        )
        .await
        .map_err(|_| CatalogError::Down)?;
    let mut items = Vec::new();
    for meta in data["metas"].as_array().into_iter().flatten() {
        let id = if truthy(&meta["id"]) {
            text(&meta["id"])
        } else {
            String::new()
        };
        if !meta.is_object() || !IMDB.is_match(&id) {
            continue;
        }
        let field = |v: &Value| if truthy(v) { text(v) } else { String::new() };
        let year = [&meta["releaseInfo"], &meta["year"]]
            .into_iter()
            .find(|v| truthy(v))
            .map(text)
            .unwrap_or_default();
        items.push(json!({"id": meta["id"], "type": kind, "name": cut(&field(&meta["name"]), 200), "poster": field(&meta["poster"]),
                          "year": cut(&year, 12), "imdbRating": field(&meta["imdbRating"])}));
    }
    items.truncate(60);
    Ok(items)
}

/// KINO's built-in sources that can be switched off (the anime indexer).
pub fn builtin(db: &Db) -> Vec<Value> {
    let enabled = db
        .get_config("addons_enabled")
        .and_then(|v| v["nyaa_anime"].as_bool())
        .unwrap_or(true);
    vec![json!({
        "id": "nyaa_anime", "name": "Nyaa Anime (VOSTFR & Fansubs)", "version": "1.0.0",
        "description": "Indexeur spécialisé pour les animes japonais en haute qualité (VOSTFR, 1080p Fansubs, Nyaa.si)",
        "author": "KINO Community", "type": "scraper", "enabled": enabled, "entry_point": "", "path": "", "is_user": false,
    })]
}

pub fn toggle_builtin(db: &Db, id: &str, enabled: bool) {
    let mut flags = db
        .get_config("addons_enabled")
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default();
    flags.insert(id.to_string(), json!(enabled));
    let _ = db.save_config("addons_enabled", &Value::Object(flags));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn addresses_and_manifests() {
        assert_eq!(
            base_of("stremio://addon.example.com/abc/manifest.json").unwrap(),
            "https://addon.example.com/abc"
        );
        assert_eq!(
            base_of("https://a.b:7000/x/").unwrap(),
            "https://a.b:7000/x"
        );
        assert!(base_of("ftp://a.b/manifest.json").is_err());
        let manifest = json!({"id": "org.x", "name": "X", "resources": ["stream", {"name": "catalog"}], "types": ["movie", "tv"],
                              "catalogs": [{"type": "movie", "id": "top"}, {"type": "movie", "id": "search", "extra": [{"name": "search", "isRequired": true}]}],
                              "idPrefixes": ["kitsu"]});
        let kept = entry("https://a.b", &manifest).unwrap();
        assert_eq!(kept["types"], json!(["movie"]));
        assert_eq!(
            kept["catalogs"],
            json!([{"type": "movie", "id": "top", "name": "X"}]),
            "a catalogue that needs a search is left out"
        );
        assert_eq!(
            kept["imdb"], false,
            "no IMDb prefix: no sources for KINO's titles"
        );
        assert!(
            entry(
                "https://a.b",
                &json!({"id": "y", "name": "Y", "resources": ["meta"]})
            )
            .is_err()
        );
    }
}
