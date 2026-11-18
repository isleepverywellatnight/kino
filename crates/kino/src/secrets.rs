//! Service keys in the system's keychain: the debrid keys, the TMDB key and
//! Trakt's tokens, on Windows (Credential Manager) and Linux (Secret Service).
//! macOS keeps them in the settings file until KINO is signed with a Developer
//! ID: an unsigned app would be asked for keychain access at every update.
//!
//! For now every key is kept in both places (the file and the keychain) and
//! read from the file, so an older KINO still finds them. A stored value of
//! `IN_KEYCHAIN` means « in the keychain »: that is how the file will read once
//! the keys leave it (a later version), and this one already understands it.
//!
//! Only KINO's programs turn the keychain on (`use_system_keychain`); tests and
//! anything else using this library keep to the file.

use std::sync::OnceLock;

use serde_json::Value;

use crate::config::{AppConfig, DebridProvider};

/// A key's value in the file once the key lives in the keychain only.
pub const IN_KEYCHAIN: &str = "@keychain";

/// The entry name of Trakt's tokens (stored as their JSON text).
pub const TRAKT_TOKENS: &str = "trakt_tokens";
const TMDB_KEY: &str = "tmdb_key";

/// Where secrets are kept besides the file.
pub trait SecretStore: Send + Sync {
    fn get(&self, name: &str) -> Result<Option<String>, String>;
    fn set(&self, name: &str, value: &str) -> Result<(), String>;
    fn delete(&self, name: &str) -> Result<(), String>;
    /// What the Diagnostic calls it.
    fn label(&self) -> &'static str;
}

static STORE: OnceLock<Box<dyn SecretStore>> = OnceLock::new();

/// Turns the system's keychain on for this process, where KINO uses one.
/// Returns whether it is on.
pub fn use_system_keychain() -> bool {
    #[cfg(any(windows, target_os = "linux"))]
    {
        let _ = STORE.set(Box::new(SystemKeychain));
    }
    STORE.get().is_some()
}

/// The keychain, when this process turned it on.
pub fn store() -> Option<&'static dyn SecretStore> {
    STORE.get().map(|s| s.as_ref())
}

#[cfg(any(windows, target_os = "linux"))]
struct SystemKeychain;

#[cfg(any(windows, target_os = "linux"))]
impl SystemKeychain {
    const SERVICE: &'static str = "KINO";

    /// Entries belong to one settings file: a KINO started with another home
    /// (a test, a second account) never sees nor removes this one's keys.
    fn scope() -> &'static str {
        static SCOPE: OnceLock<String> = OnceLock::new();
        SCOPE.get_or_init(|| {
            use sha2::Digest;
            let path = crate::paths::config_file();
            let digest = sha2::Sha256::digest(path.to_string_lossy().as_bytes());
            hex::encode(&digest[..6])
        })
    }

    fn entry(name: &str) -> Result<keyring::Entry, String> {
        keyring::Entry::new(Self::SERVICE, &format!("{name}@{}", Self::scope()))
            .map_err(|e| e.to_string())
    }
}

#[cfg(any(windows, target_os = "linux"))]
impl SecretStore for SystemKeychain {
    fn get(&self, name: &str) -> Result<Option<String>, String> {
        match Self::entry(name)?.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(e.to_string()),
        }
    }

    fn set(&self, name: &str, value: &str) -> Result<(), String> {
        Self::entry(name)?
            .set_password(value)
            .map_err(|e| e.to_string())
    }

    fn delete(&self, name: &str) -> Result<(), String> {
        match Self::entry(name)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(e.to_string()),
        }
    }

    fn label(&self) -> &'static str {
        if cfg!(windows) {
            "Gestionnaire d'identification Windows"
        } else {
            "Secret Service"
        }
    }
}

fn provider_entry(provider: DebridProvider) -> String {
    format!("debrid:{provider}")
}

/// The keys of the settings, as (entry name, value); an empty value means none.
fn config_keys(cfg: &AppConfig) -> Vec<(String, String)> {
    let mut keys: Vec<(String, String)> = DebridProvider::ALL
        .iter()
        .map(|p| (provider_entry(*p), cfg.token(*p).to_string()))
        .collect();
    keys.push((TMDB_KEY.into(), cfg.tmdb_key().to_string()));
    keys
}

/// The settings with each key the file marks `IN_KEYCHAIN` read from `store`.
/// Without a store, or when the keychain has nothing, the key is left empty.
pub fn resolve(cfg: &mut AppConfig, store: Option<&dyn SecretStore>) {
    let stored = |name: &str| {
        store
            .and_then(|s| s.get(name).ok().flatten())
            .unwrap_or_default()
    };
    for provider in DebridProvider::ALL {
        if cfg.token(*provider) == IN_KEYCHAIN {
            let value = stored(&provider_entry(*provider));
            cfg.provider_tokens.insert(*provider, value);
        }
    }
    cfg.provider_tokens.retain(|_, v| !v.is_empty());
    cfg.rd_token = cfg.token(cfg.debrid_provider).to_string();
    if cfg.tmdb_key() == IN_KEYCHAIN {
        cfg.tmdb_key = Some(stored(TMDB_KEY)).filter(|k| !k.is_empty());
    }
}

/// Puts `value` in the keychain under `name` (removed when empty), writing
/// only what differs. Marker values are left alone.
fn keep_one(store: &dyn SecretStore, name: &str, value: &str) -> Result<(), String> {
    if value == IN_KEYCHAIN {
        return Ok(());
    }
    let current = store.get(name)?;
    match (value.is_empty(), current.as_deref()) {
        (true, None) => Ok(()),
        (true, Some(_)) => store.delete(name),
        (false, Some(same)) if same == value => Ok(()),
        (false, _) => store.set(name, value),
    }
}

/// Copies the settings' keys to the keychain (and removes the ones gone).
/// Returns what failed, by entry name; the file still holds every key.
pub fn keep_config(store: &dyn SecretStore, cfg: &AppConfig) -> Vec<String> {
    config_keys(cfg)
        .into_iter()
        .filter_map(|(name, value)| {
            keep_one(store, &name, &value)
                .err()
                .map(|e| format!("{name}: {e}"))
        })
        .collect()
}

/// Copies Trakt's tokens (or their absence) to the keychain.
pub fn keep_trakt(store: &dyn SecretStore, tokens: Option<&Value>) -> Result<(), String> {
    let text = tokens
        .filter(|t| t.is_object())
        .map(Value::to_string)
        .unwrap_or_default();
    keep_one(store, TRAKT_TOKENS, &text)
}

/// Trakt's tokens as stored in the database, read from the keychain when the
/// database holds `IN_KEYCHAIN`.
pub fn resolve_trakt(stored: Option<Value>, store: Option<&dyn SecretStore>) -> Option<Value> {
    match stored {
        Some(Value::String(s)) if s == IN_KEYCHAIN => store?
            .get(TRAKT_TOKENS)
            .ok()
            .flatten()
            .and_then(|t| serde_json::from_str(&t).ok()),
        other => other,
    }
}

/// For the Diagnostic: where the keys are, without any key.
pub fn report(store: Option<&dyn SecretStore>, cfg: &AppConfig) -> Value {
    let Some(store) = store else {
        return serde_json::json!({"keychain": null, "note": "Clés dans le fichier de réglages."});
    };
    let present: Vec<(String, String)> = config_keys(cfg)
        .into_iter()
        .filter(|(_, v)| !v.is_empty())
        .collect();
    let kept = present
        .iter()
        .filter(|(name, value)| store.get(name).ok().flatten().as_deref() == Some(value.as_str()))
        .count();
    serde_json::json!({"keychain": store.label(), "keys": present.len(), "in_keychain": kept})
}

/// At launch: every key of the settings and Trakt's tokens copied to the
/// keychain; failures go to the error log (the file still holds the keys).
pub fn sync_at_launch(db: &crate::db::Db) {
    let Some(store) = store() else { return };
    let mut failed = keep_config(store, &crate::config::load());
    if let Err(e) = keep_trakt(store, db.get_config("trakt_tokens").as_ref()) {
        failed.push(format!("{TRAKT_TOKENS}: {e}"));
    }
    for failure in failed {
        crate::errorlog::LOG.record("trousseau", "Erreur", &failure, &[]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::collections::HashMap;
    use std::sync::Mutex;

    /// The keychain in memory: tests never touch the computer's own.
    #[derive(Default)]
    struct Memory(Mutex<HashMap<String, String>>);

    impl SecretStore for Memory {
        fn get(&self, name: &str) -> Result<Option<String>, String> {
            Ok(self.0.lock().unwrap().get(name).cloned())
        }
        fn set(&self, name: &str, value: &str) -> Result<(), String> {
            self.0.lock().unwrap().insert(name.into(), value.into());
            Ok(())
        }
        fn delete(&self, name: &str) -> Result<(), String> {
            self.0.lock().unwrap().remove(name);
            Ok(())
        }
        fn label(&self) -> &'static str {
            "mémoire"
        }
    }

    fn config(file: Value) -> AppConfig {
        AppConfig::from_files(&[file], None)
    }

    #[test]
    fn keys_are_copied_and_removed_ones_leave() {
        let store = Memory::default();
        let cfg = config(json!({"debrid_provider": "alldebrid",
            "provider_tokens": {"realdebrid": "RD-KEY", "alldebrid": "AD-KEY"}, "tmdb_key": "0123456789abcdef0123456789abcdef"}));
        assert!(keep_config(&store, &cfg).is_empty());
        assert_eq!(
            store.get("debrid:realdebrid").unwrap().as_deref(),
            Some("RD-KEY")
        );
        assert_eq!(
            store.get("debrid:alldebrid").unwrap().as_deref(),
            Some("AD-KEY")
        );
        assert_eq!(
            store.get(TMDB_KEY).unwrap().as_deref(),
            Some("0123456789abcdef0123456789abcdef")
        );
        let cfg = config(json!({"provider_tokens": {"alldebrid": "AD-KEY"}}));
        keep_config(&store, &cfg);
        assert_eq!(
            store.get("debrid:realdebrid").unwrap(),
            None,
            "a key removed from KINO leaves the keychain"
        );
        assert_eq!(store.get(TMDB_KEY).unwrap(), None);
    }

    #[test]
    fn a_file_that_points_to_the_keychain_reads_the_keys_back() {
        let store = Memory::default();
        store.set("debrid:realdebrid", "RD-KEY").unwrap();
        store.set(TMDB_KEY, "TMDB").unwrap();
        let mut cfg = config(
            json!({"provider_tokens": {"realdebrid": IN_KEYCHAIN}, "tmdb_key": IN_KEYCHAIN}),
        );
        resolve(&mut cfg, Some(&store));
        assert_eq!(cfg.token(DebridProvider::RealDebrid), "RD-KEY");
        assert_eq!(cfg.rd_token, "RD-KEY");
        assert_eq!(cfg.tmdb_key(), "TMDB");
        // The markers are never copied over the stored keys.
        let marked = config(json!({"provider_tokens": {"realdebrid": IN_KEYCHAIN}}));
        keep_config(&store, &marked);
        assert_eq!(
            store.get("debrid:realdebrid").unwrap().as_deref(),
            Some("RD-KEY")
        );
    }

    #[test]
    fn without_a_keychain_a_marked_key_is_simply_missing() {
        let mut cfg = config(json!({"provider_tokens": {"realdebrid": IN_KEYCHAIN}}));
        resolve(&mut cfg, None);
        assert_eq!(cfg.token(DebridProvider::RealDebrid), "");
        assert_eq!(cfg.rd_token, "");
    }

    #[test]
    fn a_file_with_keys_reads_them_from_the_file() {
        let store = Memory::default();
        store.set("debrid:realdebrid", "OLDER").unwrap();
        let mut cfg = config(json!({"provider_tokens": {"realdebrid": "RD-KEY"}}));
        resolve(&mut cfg, Some(&store));
        assert_eq!(cfg.token(DebridProvider::RealDebrid), "RD-KEY");
    }

    #[test]
    fn trakt_tokens_go_to_the_keychain_and_come_back() {
        let store = Memory::default();
        let tokens = json!({"access_token": "A", "refresh_token": "R"});
        keep_trakt(&store, Some(&tokens)).unwrap();
        assert_eq!(
            resolve_trakt(Some(json!(IN_KEYCHAIN)), Some(&store)),
            Some(tokens.clone())
        );
        assert_eq!(
            resolve_trakt(Some(tokens.clone()), Some(&store)),
            Some(tokens)
        );
        keep_trakt(&store, None).unwrap();
        assert_eq!(
            store.get(TRAKT_TOKENS).unwrap(),
            None,
            "disconnected: gone from the keychain"
        );
    }

    /// The computer's own keychain, written then emptied (one probe entry).
    /// Run by hand: cargo test -p kino --lib system_keychain -- --ignored
    #[cfg(any(windows, target_os = "linux"))]
    #[test]
    #[ignore]
    fn system_keychain_round_trip() {
        let store = SystemKeychain;
        let name = "kino-test-probe";
        store.delete(name).unwrap();
        assert_eq!(store.get(name).unwrap(), None);
        store.set(name, "probe-value").unwrap();
        assert_eq!(store.get(name).unwrap().as_deref(), Some("probe-value"));
        store.delete(name).unwrap();
        assert_eq!(store.get(name).unwrap(), None);
    }

    #[test]
    fn the_report_names_no_key() {
        let store = Memory::default();
        let cfg = config(json!({"provider_tokens": {"realdebrid": "RD-KEY"}}));
        keep_config(&store, &cfg);
        let report = report(Some(&store), &cfg);
        assert_eq!(report["keys"], 1);
        assert_eq!(report["in_keychain"], 1);
        assert!(!report.to_string().contains("RD-KEY"));
    }
}
