//! Authenticated KINO updates, from GitHub Releases or a local package. The
//! public key ships with KINO; the release workflow signs with the private one.
//!
//! macOS: a signed .kinoupdate package (downloaded from the latest release, or
//! found locally) is verified, staged, then installed by the native helper
//! (packaging/update-helper), which keeps the previous version for a rollback.
//! Windows: the release's installer is downloaded with its signed description
//! (.sig), checked against it, then run silently; it relaunches KINO.

use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};

use base64::Engine;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use regex::Regex;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

pub const BUNDLE_ID: &str = "com.kino.desktop";
pub const DATA_SCHEMA: i64 = 1;
pub const UPDATE_FORMAT: i64 = 1;
pub const UPDATE_REPO: &str = "isleepverywellatnight/kino";
const MAX_PACKAGE_BYTES: u64 = 900 * 1024 * 1024;
const MAX_APP_BYTES: u64 = 1800 * 1024 * 1024;
const MAX_RELEASE_JSON: usize = 2_000_000;
const MAX_SIGNATURE_BYTES: usize = 32_768;
pub const WINDOWS_INSTALLER: &str = "KINO-Windows.exe";
const KEEP_DATA_SNAPSHOTS: usize = 3;
const STALE_STAGE_SEC: f64 = 10.0 * 60.0;
const STALE_WORK_FILE_SEC: f64 = 24.0 * 3600.0;

static VERSION: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$").unwrap());
static ARTIFACT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^KINO-[0-9]+\.[0-9]+\.[0-9]+-(arm64|x86_64)\.tar\.gz$").unwrap());
static SHA: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[a-f0-9]{64}$").unwrap());
static WORK_FILE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(?:helper|ack|job)-[0-9a-f]{24}(?:\.json)?$").unwrap());

pub fn releases_api() -> String {
    format!("https://api.github.com/repos/{UPDATE_REPO}/releases/latest")
}
pub fn releases_page() -> String {
    format!("https://github.com/{UPDATE_REPO}/releases/latest")
}
fn download_prefix() -> String {
    format!("https://github.com/{UPDATE_REPO}/releases/download/")
}

pub type Result<T> = std::result::Result<T, String>;

fn app_version() -> &'static str {
    crate::backup::app_version()
}

/// As Python's json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False).
pub fn canonical_json(value: &Value) -> Vec<u8> {
    fn sorted(value: &Value) -> Value {
        match value {
            Value::Object(map) => {
                let ordered: BTreeMap<&String, Value> =
                    map.iter().map(|(k, v)| (k, sorted(v))).collect();
                let mut out = Map::new();
                for (k, v) in ordered {
                    out.insert(k.clone(), v);
                }
                Value::Object(out)
            }
            Value::Array(items) => Value::Array(items.iter().map(sorted).collect()),
            other => other.clone(),
        }
    }
    serde_json::to_vec(&sorted(value)).unwrap_or_default()
}

pub fn version_tuple(version: &str) -> Result<(u64, u64, u64)> {
    let c = VERSION
        .captures(version)
        .ok_or("Version du paquet invalide.")?;
    let n = |i: usize| {
        c[i].parse::<u64>()
            .map_err(|_| "Version du paquet invalide.".to_string())
    };
    Ok((n(1)?, n(2)?, n(3)?))
}

fn newer(version: &str, than: &str) -> bool {
    matches!((version_tuple(version), version_tuple(than)), (Ok(a), Ok(b)) if a > b)
}

/// "arm64" or "x86_64", as macOS names it.
pub fn architecture() -> &'static str {
    match std::env::consts::ARCH {
        "aarch64" => "arm64",
        other => other,
    }
}

/// The payload of a signed envelope {"payload": …, "signature": base64}.
fn signed_payload(raw: &[u8], public_key_hex: &str) -> Result<Value> {
    if raw.len() > MAX_SIGNATURE_BYTES {
        return Err("Manifeste trop volumineux.".into());
    }
    let invalid = || "Signature du manifeste invalide.".to_string();
    let envelope: Value = serde_json::from_slice(raw).map_err(|_| invalid())?;
    let payload = envelope.get("payload").ok_or_else(invalid)?.clone();
    let signature = base64::engine::general_purpose::STANDARD
        .decode(envelope["signature"].as_str().ok_or_else(invalid)?)
        .map_err(|_| invalid())?;
    let key: [u8; 32] = hex::decode(public_key_hex.trim())
        .ok()
        .and_then(|k| k.try_into().ok())
        .ok_or_else(invalid)?;
    let key = VerifyingKey::from_bytes(&key).map_err(|_| invalid())?;
    let signature = Signature::from_slice(&signature).map_err(|_| invalid())?;
    key.verify(&canonical_json(&payload), &signature)
        .map_err(|_| invalid())?;
    Ok(payload)
}

fn keys_are(payload: &Value, wanted: &[&str]) -> bool {
    payload
        .as_object()
        .is_some_and(|o| o.len() == wanted.len() && wanted.iter().all(|k| o.contains_key(*k)))
}

fn check_artifact(artifact: &Value, kind: &str) -> Result<()> {
    if !keys_are(artifact, &["name", "sha256", "size"]) {
        return Err("Artefact invalide.".into());
    }
    if !artifact["sha256"].as_str().is_some_and(|s| SHA.is_match(s)) {
        return Err(format!("Empreinte d'{kind} invalide."));
    }
    if !artifact["size"]
        .as_u64()
        .is_some_and(|s| s > 0 && s <= MAX_PACKAGE_BYTES)
        || !artifact["size"].is_u64()
    {
        return Err(format!("Taille d'{kind} invalide."));
    }
    Ok(())
}

fn notes_ok(payload: &Value) -> bool {
    payload["notes"]
        .as_str()
        .is_some_and(|n| n.chars().count() <= 4000)
}

fn schema(payload: &Value) -> Option<i64> {
    payload["data_schema"]
        .as_i64()
        .filter(|_| payload["data_schema"].is_i64() || payload["data_schema"].is_u64())
}

/// The signed description of the Windows installer (KINO-Windows.exe.sig).
pub fn verify_installer_signature(
    raw: &[u8],
    public_key_hex: &str,
    current: &str,
) -> Result<Value> {
    let p = signed_payload(raw, public_key_hex)?;
    if !keys_are(
        &p,
        &[
            "format",
            "version",
            "platform",
            "architecture",
            "bundle_id",
            "data_schema",
            "notes",
            "artifact",
        ],
    ) {
        return Err("Champs de la signature invalides.".into());
    }
    if p["format"] != json!(UPDATE_FORMAT)
        || p["platform"] != "win32"
        || p["architecture"] != "x64"
        || p["bundle_id"] != BUNDLE_ID
    {
        return Err("Cet installateur ne cible pas KINO sur Windows.".into());
    }
    if version_tuple(p["version"].as_str().unwrap_or(""))? <= version_tuple(current)? {
        return Err("Cet installateur n'est pas plus récent que KINO.".into());
    }
    if schema(&p).is_none_or(|s| s < DATA_SCHEMA) {
        return Err("La base de données de cette version est plus ancienne.".into());
    }
    if !notes_ok(&p) {
        return Err("Notes de version invalides.".into());
    }
    if !keys_are(&p["artifact"], &["name", "sha256", "size"]) {
        return Err("Artefact invalide.".into());
    }
    if p["artifact"]["name"] != WINDOWS_INSTALLER {
        return Err("Nom d'installateur incohérent.".into());
    }
    check_artifact(&p["artifact"], "installateur")?;
    Ok(p)
}

/// The macOS package's signed manifest.
pub fn verify_manifest(
    raw: &[u8],
    public_key_hex: &str,
    current: &str,
    arch: &str,
    os_version: Option<&str>,
) -> Result<Value> {
    let p = signed_payload(raw, public_key_hex)?;
    if !keys_are(
        &p,
        &[
            "format",
            "version",
            "platform",
            "architecture",
            "bundle_id",
            "data_schema",
            "min_macos",
            "notes",
            "artifact",
        ],
    ) {
        return Err("Champs du manifeste invalides.".into());
    }
    if p["format"] != json!(UPDATE_FORMAT)
        || p["platform"] != "darwin"
        || p["bundle_id"] != BUNDLE_ID
    {
        return Err("Ce paquet ne cible pas KINO sur macOS.".into());
    }
    if p["architecture"] != arch {
        return Err(format!(
            "Paquet pour {}, Mac actuel : {arch}.",
            crate::play::text(&p["architecture"])
        ));
    }
    let version = p["version"].as_str().unwrap_or("").to_string();
    if version_tuple(&version)? <= version_tuple(current)? {
        return Err("Ce paquet n'est pas plus récent que KINO.".into());
    }
    if schema(&p) != Some(DATA_SCHEMA) {
        return Err(
            "La base de données de cette version n'est pas compatible avec le retour arrière."
                .into(),
        );
    }
    if !notes_ok(&p) {
        return Err("Notes de version invalides.".into());
    }
    let min_os = p["min_macos"]
        .as_str()
        .filter(|m| Regex::new(r"^\d+\.\d+$").unwrap().is_match(m))
        .ok_or("Version macOS minimale invalide.")?;
    let actual = os_version.map(str::to_string).unwrap_or_else(macos_version);
    let pair = |v: &str| -> Vec<u64> {
        v.split('.')
            .take(2)
            .map(|n| n.parse().unwrap_or(0))
            .collect()
    };
    if !actual.is_empty() && pair(&actual) < pair(min_os) {
        return Err(format!("Ce paquet exige macOS {min_os} ou plus récent."));
    }
    let artifact = &p["artifact"];
    if !keys_are(artifact, &["name", "sha256", "size"]) {
        return Err("Artefact invalide.".into());
    }
    let name = artifact["name"]
        .as_str()
        .filter(|n| ARTIFACT.is_match(n))
        .ok_or("Nom d'artefact invalide.")?;
    if name != format!("KINO-{version}-{arch}.tar.gz") {
        return Err("Nom d'artefact incohérent.".into());
    }
    check_artifact(artifact, "artefact")?;
    Ok(p)
}

fn macos_version() -> String {
    if !cfg!(target_os = "macos") {
        return String::new();
    }
    std::process::Command::new("sw_vers")
        .arg("-productVersion")
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// The parts of a GitHub release KINO uses: version, notes, page, assets.
pub fn parse_release(data: &Value) -> Result<Value> {
    if !data.is_object()
        || crate::play::truthy(&data["draft"])
        || crate::play::truthy(&data["prerelease"])
    {
        return Err("Aucune version publiée.".into());
    }
    let tag = crate::play::text(&data["tag_name"]);
    let version = tag.strip_prefix('v').unwrap_or(&tag).to_string();
    version_tuple(&version)?;
    let mut assets = Map::new();
    for asset in data["assets"].as_array().into_iter().flatten() {
        let (Some(name), Some(url), Some(size)) = (
            asset["name"].as_str(),
            asset["browser_download_url"].as_str(),
            asset["size"].as_u64(),
        ) else {
            continue;
        };
        if url.starts_with(&download_prefix()) && size > 0 && size <= MAX_PACKAGE_BYTES {
            assets.insert(name.into(), json!({"url": url, "size": size}));
        }
    }
    let page = data["html_url"]
        .as_str()
        .filter(|p| p.starts_with(&format!("https://github.com/{UPDATE_REPO}/releases/")))
        .map(str::to_string)
        .unwrap_or_else(releases_page);
    let notes: String = data["body"]
        .as_str()
        .unwrap_or("")
        .chars()
        .take(4000)
        .collect();
    Ok(json!({"version": version, "notes": notes, "page": page, "assets": assets}))
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .user_agent(format!("KINO/{}", app_version()))
        .build()
        .unwrap_or_default()
}

pub async fn fetch_latest_release() -> Result<Value> {
    let answer = client()
        .get(releases_api())
        .header("Accept", "application/vnd.github+json")
        .timeout(std::time::Duration::from_secs(20))
        .send()
        .await
        .map_err(|_| "Impossible de joindre GitHub.".to_string())?;
    let raw = answer
        .bytes()
        .await
        .map_err(|_| "Impossible de joindre GitHub.".to_string())?;
    if raw.len() > MAX_RELEASE_JSON {
        return Err("Réponse de GitHub trop volumineuse.".into());
    }
    let data: Value =
        serde_json::from_slice(&raw).map_err(|_| "Réponse de GitHub illisible.".to_string())?;
    parse_release(&data)
}

/// Downloads a release asset, refusing anything but its announced size.
pub async fn download_asset(
    asset: &Value,
    target: &Path,
    progress: &(dyn Fn(f64) + Send + Sync),
) -> Result<PathBuf> {
    use futures_util::StreamExt;
    use std::io::Write;
    let (url, size) = (
        asset["url"].as_str().unwrap_or(""),
        asset["size"].as_u64().unwrap_or(0),
    );
    if !url.starts_with(&download_prefix()) || size == 0 || size > MAX_PACKAGE_BYTES {
        return Err("Adresse de téléchargement refusée.".into());
    }
    let part = target.with_file_name(format!(
        "{}.part",
        target
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("download")
    ));
    let result = async {
        let answer = client()
            .get(url)
            .header("Accept", "application/octet-stream")
            .send()
            .await
            .map_err(|e| e.to_string())?;
        let mut out = std::fs::File::create(&part).map_err(|e| e.to_string())?;
        let mut copied: u64 = 0;
        let mut stream = answer.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|e| e.to_string())?;
            copied += chunk.len() as u64;
            if copied > size {
                return Err("Le fichier téléchargé est plus gros qu'annoncé.".to_string());
            }
            out.write_all(&chunk).map_err(|e| e.to_string())?;
            progress(copied as f64 / size as f64);
        }
        if copied != size {
            return Err("Téléchargement incomplet.".to_string());
        }
        std::fs::rename(&part, target).map_err(|e| e.to_string())
    }
    .await;
    if result.is_err() {
        let _ = std::fs::remove_file(&part);
    }
    result.map(|_| target.to_path_buf())
}

fn sha256_file(path: &Path) -> Result<String> {
    let mut file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    loop {
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        digest.update(&buffer[..n]);
    }
    Ok(hex::encode(digest.finalize()))
}

pub fn default_state_dir() -> PathBuf {
    if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(crate::paths::home)
            .join("KINO")
            .join("Updates")
    } else {
        crate::paths::home().join("Library/Application Support/KINO/Updates")
    }
}

/// KINO.app on macOS, the installation folder (holding KINO.exe) on Windows.
pub fn default_app_path() -> PathBuf {
    let exe = std::env::current_exe()
        .and_then(|e| e.canonicalize())
        .unwrap_or_default();
    let mut path = exe.parent().map(Path::to_path_buf).unwrap_or_default();
    if !cfg!(windows) {
        path = path
            .parent()
            .and_then(Path::parent)
            .map(Path::to_path_buf)
            .unwrap_or_default();
    }
    path
}

fn regular_package(path: &Path) -> Result<()> {
    let info = std::fs::symlink_metadata(path).map_err(|_| "Paquet introuvable.".to_string())?;
    if !info.file_type().is_file() || info.len() == 0 || info.len() > MAX_PACKAGE_BYTES {
        return Err("Paquet invalide ou trop volumineux.".into());
    }
    Ok(())
}

type Package = zip::ZipArchive<std::fs::File>;

/// (manifest payload, artifact size) of an open package.
fn signed_contents(
    package: &mut Package,
    public_key_hex: &str,
    current: &str,
    arch: &str,
) -> Result<Value> {
    let names: Vec<String> = package.file_names().map(str::to_string).collect();
    if names.len() != 2 || names.iter().filter(|n| *n == "manifest.json").count() != 1 {
        return Err("Structure du paquet invalide.".into());
    }
    let mut manifest = package
        .by_name("manifest.json")
        .map_err(|_| "Structure du paquet invalide.".to_string())?;
    if manifest.size() > 32_768 {
        return Err("Manifeste trop volumineux.".into());
    }
    let mut raw = vec![];
    manifest
        .by_ref()
        .take(32_769)
        .read_to_end(&mut raw)
        .map_err(|_| "Paquet de mise à jour illisible.".to_string())?;
    drop(manifest);
    let payload = verify_manifest(&raw, public_key_hex, current, arch, None)?;
    let name = payload["artifact"]["name"]
        .as_str()
        .unwrap_or("")
        .to_string();
    if names.iter().filter(|n| **n == name).count() != 1 {
        return Err("Artefact absent du paquet.".into());
    }
    let size = package
        .by_name(&name)
        .map_err(|_| "Artefact absent du paquet.".to_string())?
        .size();
    if Some(size) != payload["artifact"]["size"].as_u64() {
        return Err("Taille de l'artefact incorrecte.".into());
    }
    Ok(payload)
}

fn open_package(path: &Path) -> Result<Package> {
    regular_package(path)?;
    let file =
        std::fs::File::open(path).map_err(|_| "Paquet de mise à jour illisible.".to_string())?;
    zip::ZipArchive::new(file).map_err(|_| "Paquet de mise à jour illisible.".to_string())
}

/// The signed metadata only, for the local search; nothing is staged.
pub fn inspect_package(
    path: &Path,
    public_key_hex: &str,
    current: &str,
    arch: &str,
) -> Result<Value> {
    let mut package = open_package(path)?;
    signed_contents(&mut package, public_key_hex, current, arch)
}

/// A package found, its payload, its folder's name, its version and date.
type Found = (
    PathBuf,
    Value,
    String,
    (u64, u64, u64),
    std::time::SystemTime,
);

/// Compatible signed packages in shallow folders, newest first: (path, payload, label).
pub fn local_candidates(
    directories: &[(PathBuf, String)],
    public_key_hex: &str,
    current: &str,
    arch: &str,
) -> Vec<(PathBuf, Value, String)> {
    let mut found: Vec<Found> = vec![];
    for (dir, label) in directories {
        let Ok(entries) = std::fs::read_dir(dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            if !(name.starts_with("KINO-") && name.ends_with(".kinoupdate") && kind.is_file()) {
                continue;
            }
            let path = entry.path();
            if let Ok(payload) = inspect_package(&path, public_key_hex, current, arch) {
                let version =
                    version_tuple(payload["version"].as_str().unwrap_or("")).unwrap_or_default();
                let modified = entry
                    .metadata()
                    .and_then(|m| m.modified())
                    .unwrap_or(std::time::UNIX_EPOCH);
                found.push((path, payload, label.clone(), version, modified));
            }
        }
    }
    found.sort_by_key(|b| std::cmp::Reverse((b.3, b.4)));
    found
        .into_iter()
        .map(|(p, payload, l, _, _)| (p, payload, l))
        .collect()
}

/// Every entry under KINO.app/, no traversal, files, folders and inner links only.
fn check_archive(archive_path: &Path) -> Result<()> {
    let file = std::fs::File::open(archive_path).map_err(|e| e.to_string())?;
    let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(file));
    let (mut total, mut seen) = (0u64, std::collections::HashSet::new());
    let forbidden = || "L'archive contient un chemin interdit.".to_string();
    for entry in archive
        .entries()
        .map_err(|_| "Archive illisible.".to_string())?
    {
        let entry = entry.map_err(|_| "Archive illisible.".to_string())?;
        if seen.len() >= 100_000 {
            return Err("L'archive contient trop de fichiers.".into());
        }
        let raw = entry
            .path()
            .map_err(|_| forbidden())?
            .to_string_lossy()
            .into_owned();
        let name = raw
            .strip_prefix("./")
            .unwrap_or(&raw)
            .trim_end_matches('/')
            .to_string();
        let parts: Vec<&str> = name.split('/').collect();
        if parts.first() != Some(&"KINO.app")
            || parts
                .iter()
                .any(|p| p.is_empty() || *p == "." || *p == "..")
        {
            return Err(forbidden());
        }
        if !seen.insert(name.clone()) {
            return Err("L'archive contient des chemins dupliqués.".into());
        }
        let kind = entry.header().entry_type();
        if !(kind.is_file() || kind.is_dir() || kind.is_symlink()) {
            return Err("Type de fichier interdit dans l'archive.".into());
        }
        if kind.is_symlink() {
            let link = entry
                .link_name()
                .ok()
                .flatten()
                .map(|l| l.to_string_lossy().into_owned())
                .unwrap_or_default();
            if link.starts_with('/') {
                return Err("Lien symbolique hors de l'application.".into());
            }
            let mut stack: Vec<&str> = parts[..parts.len() - 1].to_vec();
            for piece in link.split('/') {
                match piece {
                    "" | "." => {}
                    ".." => {
                        stack.pop();
                    }
                    other => stack.push(other),
                }
            }
            if stack.first() != Some(&"KINO.app") || stack.len() < 2 {
                return Err("Lien symbolique hors de l'application.".into());
            }
        }
        total += entry.header().size().unwrap_or(0);
        if total > MAX_APP_BYTES {
            return Err("Application décompressée trop volumineuse.".into());
        }
    }
    Ok(())
}

/// Unpacks a checked archive; modes keep at most rwxr-xr-x (no setuid, no group write).
fn unpack(archive_path: &Path, stage: &Path) -> Result<()> {
    let file = std::fs::File::open(archive_path).map_err(|e| e.to_string())?;
    let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(file));
    archive.set_preserve_permissions(false);
    archive.set_overwrite(false);
    for entry in archive.entries().map_err(|e| e.to_string())? {
        let mut entry = entry.map_err(|e| e.to_string())?;
        let mode = entry.header().mode().unwrap_or(0o644) & 0o755;
        let is_file = entry.header().entry_type().is_file();
        if !entry.unpack_in(stage).map_err(|e| e.to_string())? {
            return Err("L'archive contient un chemin interdit.".into());
        }
        #[cfg(unix)]
        if is_file {
            use std::os::unix::fs::PermissionsExt;
            let path = stage.join(entry.path().map_err(|e| e.to_string())?);
            let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode));
        }
        #[cfg(not(unix))]
        let _ = (mode, is_file);
    }
    Ok(())
}

fn bundle_version(app: &Path) -> Result<String> {
    let info: plist::Dictionary = plist::from_file(app.join("Contents/Info.plist"))
        .map_err(|_| "Info.plist du paquet illisible.".to_string())?;
    let get = |k: &str| {
        info.get(k)
            .and_then(|v| v.as_string())
            .unwrap_or("")
            .to_string()
    };
    if get("CFBundleIdentifier") != BUNDLE_ID || get("CFBundleExecutable") != "KINO" {
        return Err("Identité de l'application incorrecte.".into());
    }
    Ok(get("CFBundleShortVersionString"))
}

fn run_checked(program: &Path, args: &[&str], timeout: u64) -> Result<()> {
    let name = program
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let failed = || format!("Vérification de l'application échouée : {name}.");
    let mut child = std::process::Command::new(program)
        .args(args)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|_| failed())?;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(timeout);
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(()),
            Ok(Some(_)) | Err(_) => return Err(failed()),
            Ok(None) if std::time::Instant::now() > deadline => {
                let _ = child.kill();
                return Err(failed());
            }
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(100)),
        }
    }
}

/// The staged KINO.app: its version, executable, architecture, signature, self-check.
fn verify_candidate(app: &Path, payload: &Value, system_checks: bool) -> Result<()> {
    if bundle_version(app)? != crate::play::text(&payload["version"]) {
        return Err("La version de l'application diffère du manifeste.".into());
    }
    let executable = app.join("Contents/MacOS/KINO");
    #[cfg(unix)]
    let runnable = {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(&executable)
            .is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
    };
    #[cfg(not(unix))]
    let runnable = executable.is_file();
    if !runnable {
        return Err("Exécutable KINO manquant.".into());
    }
    if !system_checks {
        return Ok(());
    }
    let arches = std::process::Command::new("/usr/bin/lipo")
        .args(["-archs"])
        .arg(&executable)
        .output()
        .map_err(|_| "Architecture de l'exécutable illisible.".to_string())?;
    let arches = String::from_utf8_lossy(&arches.stdout).to_string();
    if !arches
        .split_whitespace()
        .any(|a| payload["architecture"] == a)
    {
        return Err("L'exécutable n'a pas la bonne architecture.".into());
    }
    let app_text = app.display().to_string();
    run_checked(
        Path::new("/usr/bin/codesign"),
        &["--verify", "--deep", "--strict", &app_text],
        30,
    )?;
    run_checked(&executable, &["--self-check"], 45)
}

/// Checks a package, then stages its KINO.app in `stage` (created here).
pub fn verify_and_extract(
    path: &Path,
    stage: &Path,
    public_key_hex: &str,
    current: &str,
    arch: &str,
    progress: &dyn Fn(u8),
    system_checks: bool,
) -> Result<Value> {
    let mut package = open_package(path)?;
    std::fs::create_dir(stage).map_err(|e| e.to_string())?;
    let mut work = || -> Result<Value> {
        let payload = signed_contents(&mut package, public_key_hex, current, arch)?;
        let artifact = &payload["artifact"];
        let name = crate::play::text(&artifact["name"]);
        let size = artifact["size"].as_u64().unwrap_or(0);
        let archive_path = stage.join(&name);
        {
            let mut source = package
                .by_name(&name)
                .map_err(|_| "Artefact absent du paquet.".to_string())?;
            let mut target = std::fs::File::create(&archive_path).map_err(|e| e.to_string())?;
            let (mut digest, mut copied, mut buffer) = (Sha256::new(), 0u64, vec![0u8; 1 << 20]);
            loop {
                let n = source
                    .read(&mut buffer)
                    .map_err(|_| "L'artefact est tronqué ou modifié.".to_string())?;
                if n == 0 {
                    break;
                }
                std::io::Write::write_all(&mut target, &buffer[..n]).map_err(|e| e.to_string())?;
                digest.update(&buffer[..n]);
                copied += n as u64;
                progress(
                    ((copied as f64 / size.max(1) as f64) * 70.0)
                        .round()
                        .min(70.0) as u8,
                );
            }
            if copied != size
                || hex::encode(digest.finalize()) != artifact["sha256"].as_str().unwrap_or("")
            {
                return Err("L'artefact est tronqué ou modifié.".into());
            }
        }
        check_archive(&archive_path)?;
        unpack(&archive_path, stage)?;
        let _ = std::fs::remove_file(&archive_path);
        progress(85);
        let candidate = stage.join("KINO.app");
        verify_candidate(&candidate, &payload, system_checks)?;
        progress(100);
        Ok(json!({"payload": payload, "candidate": candidate.display().to_string()}))
    };
    let result = work();
    if result.is_err() {
        let _ = std::fs::remove_dir_all(stage);
    }
    result
}

/// A read-only copy of the database, then the settings and the playlist.
pub fn snapshot_user_data(destination: &Path) -> Result<()> {
    std::fs::create_dir(destination).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(destination, std::fs::Permissions::from_mode(0o700));
    }
    let home = crate::paths::home();
    let db = home.join(".kino_data.db");
    if db.exists() {
        let target = destination.join("kino_data.db");
        let source =
            rusqlite::Connection::open_with_flags(&db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
                .map_err(|e| e.to_string())?;
        source
            .busy_timeout(std::time::Duration::from_secs(15))
            .map_err(|e| e.to_string())?;
        source
            .execute("VACUUM INTO ?1", [target.display().to_string()])
            .map_err(|e| e.to_string())?;
        private(&target);
    }
    for name in [".rd_cinehub_config.json", ".kino_playlist.m3u"] {
        let source = home.join(name);
        if source.is_file() {
            let target = destination.join(name);
            std::fs::copy(&source, &target).map_err(|e| e.to_string())?;
            private(&target);
        }
    }
    Ok(())
}

fn private(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    }
    #[cfg(not(unix))]
    let _ = path;
}

fn private_dir(path: &Path) -> Result<()> {
    std::fs::create_dir_all(path).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700));
    }
    Ok(())
}

fn token() -> String {
    use rand::Rng;
    let bytes: [u8; 12] = rand::rng().random();
    hex::encode(bytes)
}

#[derive(Default)]
struct Inner {
    progress: u8,
    progress_label: String,
    message: String,
    candidate: Option<Value>,
    latest: Option<Value>,
}

/// The update state of this copy of KINO (the bridge's update_* calls).
pub struct Updater {
    pub app_path: PathBuf,
    pub state_dir: PathBuf,
    public_key: Option<String>,
    inner: Mutex<Inner>,
    /// Whether the staged app is checked by lipo, codesign and its self-check.
    system_checks: bool,
}

impl Updater {
    pub fn new(app_path: PathBuf, state_dir: PathBuf, public_key: Option<String>) -> Self {
        Self {
            app_path,
            state_dir,
            public_key,
            inner: Mutex::default(),
            system_checks: true,
        }
    }

    /// For tests: the staged app is checked without lipo, codesign or a self-check.
    pub fn without_system_checks(mut self) -> Self {
        self.system_checks = false;
        self
    }

    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// The copy updates may replace.
    pub fn is_installed(&self) -> bool {
        if cfg!(target_os = "macos") {
            return self.app_path == Path::new("/Applications/KINO.app") && self.app_path.is_dir();
        }
        if cfg!(windows) {
            // Installed by packaging/kino.iss, which leaves its uninstaller next to KINO.exe.
            return self.app_path.join("unins000.exe").is_file();
        }
        false
    }

    fn require_installed(&self) -> Result<()> {
        if cfg!(target_os = "macos") {
            if !self.is_installed() {
                return Err("Ouvrez d'abord KINO depuis /Applications/KINO.app.".into());
            }
        } else if cfg!(windows) {
            if !self.is_installed() {
                return Err(
                    "Installez KINO avec son installateur pour recevoir les mises à jour.".into(),
                );
            }
        } else {
            return Err(
                "Les mises à jour automatiques ne sont pas disponibles sur ce système.".into(),
            );
        }
        Ok(())
    }

    fn key(&self) -> Result<String> {
        if let Some(k) = &self.public_key {
            return Ok(k.clone());
        }
        let candidates = [
            std::env::current_exe().ok().and_then(|e| {
                e.parent()
                    .map(|d| d.join("../Resources/assets/update_public_key.hex"))
            }),
            std::env::current_exe()
                .ok()
                .and_then(|e| e.parent().map(|d| d.join("assets/update_public_key.hex"))),
            Some(
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../../assets/update_public_key.hex"),
            ),
        ];
        candidates
            .into_iter()
            .flatten()
            .find_map(|p| std::fs::read_to_string(p).ok())
            .ok_or_else(|| "Clé de mise à jour absente.".to_string())
    }

    fn read_state(&self) -> Value {
        std::fs::read_to_string(self.state_dir.join("status.json"))
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or(json!({}))
    }

    fn newer_latest(inner: &Inner) -> Option<Value> {
        inner
            .latest
            .clone()
            .filter(|l| newer(l["version"].as_str().unwrap_or(""), app_version()))
    }

    fn discard_candidate(&self, inner: &mut Inner) {
        if let Some(candidate) = inner.candidate.take()
            && let Some(stage) = Path::new(candidate["candidate"].as_str().unwrap_or("")).parent()
            && stage.parent() == Some(self.state_dir.as_path())
            && stage
                .file_name()
                .is_some_and(|n| n.to_string_lossy().starts_with("stage-"))
        {
            let _ = std::fs::remove_dir_all(stage);
        }
    }

    pub fn status(&self) -> Value {
        let inner = self.inner();
        let state = self.read_state();
        let backup =
            Path::new(state["backup_path"].as_str().unwrap_or("/nonexistent")).to_path_buf();
        let latest = Self::newer_latest(&inner)
            .map(|l| json!({"version": l["version"], "notes": l["notes"], "page": l["page"]}));
        let mac = cfg!(target_os = "macos");
        let platform = if mac {
            "darwin"
        } else if cfg!(windows) {
            "win32"
        } else {
            "linux"
        };
        let message = if inner.message.is_empty() {
            crate::play::text(&state["message"])
        } else {
            inner.message.clone()
        };
        json!({
            "version": app_version(), "platform": platform, "channel": "github", "remote_configured": true,
            "installed": self.is_installed(), "local_packages": mac, "rollback_supported": mac,
            "latest": latest, "candidate": inner.candidate.as_ref().map(|c| c["payload"].clone()),
            "progress": inner.progress, "progress_label": inner.progress_label, "message": message,
            "rollback_available": mac && backup.is_dir() && state["data_schema"] == json!(DATA_SCHEMA),
            "rollback_version": crate::play::text(&state["backup_version"]),
        })
    }

    pub async fn check_latest(&self) -> Result<Value> {
        let release = fetch_latest_release().await?;
        self.inner().latest = Some(release.clone());
        Ok(release)
    }

    /// The « Rechercher une mise à jour » button: GitHub first, then (macOS) local packages.
    pub async fn check_for_updates(&self) -> Value {
        {
            let mut inner = self.inner();
            self.discard_candidate(&mut inner);
            inner.progress = 0;
            inner.progress_label.clear();
            inner.message = "Recherche d'une nouvelle version…".into();
        }
        let release = self.check_latest().await.ok();
        let is_newer = release
            .as_ref()
            .is_some_and(|r| newer(r["version"].as_str().unwrap_or(""), app_version()));
        if cfg!(windows) {
            return self.check_windows(release, is_newer).await;
        }
        let mut note = String::new();
        if let Some(release) = release
            .as_ref()
            .filter(|_| is_newer && cfg!(target_os = "macos"))
            && let Err(e) = self.download_mac_package(release).await
        {
            note = e;
        }
        let result = match self.check_local_updates() {
            Ok(r) => r,
            Err(e) => {
                if note.is_empty() {
                    note = e;
                }
                with(self.status(), json!({"available": false}))
            }
        };
        if result["available"] == true {
            return result;
        }
        let mut inner = self.inner();
        inner.message = match &release {
            Some(r) if is_newer => format!(
                "{} Vous pouvez aussi le télécharger depuis la page des versions.",
                format!(
                    "KINO {} est disponible. {note}",
                    crate::play::text(&r["version"])
                )
                .trim()
            ),
            Some(_) => format!("KINO est à jour (version {}).", app_version()),
            None => format!("Impossible de joindre GitHub. {}", inner.message),
        };
        let message = inner.message.clone();
        drop(inner);
        with(
            self.status(),
            json!({"available": false, "message": message}),
        )
    }

    fn progress_setter(&self, label: &str) -> impl Fn(f64) + Send + Sync + '_ {
        let label = label.to_string();
        move |fraction: f64| {
            let mut inner = self.inner();
            inner.progress_label = label.clone();
            inner.progress = (fraction * 100.0).round().clamp(1.0, 99.0) as u8;
        }
    }

    async fn download_mac_package(&self, release: &Value) -> Result<()> {
        self.require_installed()?;
        let version = crate::play::text(&release["version"]);
        let name = format!("KINO-{version}-{}.kinoupdate", architecture());
        let asset = release["assets"]
            .get(&name)
            .cloned()
            .ok_or("Cette version n'a pas de paquet de mise à jour pour ce Mac.")?;
        let inbox = self.state_dir.join("inbox");
        private_dir(&inbox)?;
        let target = inbox.join(&name);
        if std::fs::metadata(&target)
            .is_ok_and(|m| m.is_file() && Some(m.len()) == asset["size"].as_u64())
        {
            return Ok(());
        }
        self.inner().message = format!("Téléchargement de KINO {version}…");
        download_asset(&asset, &target, &self.progress_setter("Téléchargement"))
            .await
            .map(drop)
    }

    async fn check_windows(&self, release: Option<Value>, is_newer: bool) -> Value {
        let message = match &release {
            None => "Impossible de joindre GitHub. Vérifiez la connexion Internet.".to_string(),
            Some(_) if !is_newer => format!("KINO est à jour (version {}).", app_version()),
            Some(r) => {
                let version = crate::play::text(&r["version"]);
                let prepared = match self.require_installed() {
                    Ok(()) => self.prepare_windows(r).await,
                    Err(e) => Err(e),
                };
                match prepared {
                    Ok(()) => {
                        let mut inner = self.inner();
                        inner.message =
                            format!("KINO {version} téléchargé et vérifié. Installation prête.");
                        inner.progress = 100;
                        inner.progress_label.clear();
                        drop(inner);
                        return with(self.status(), json!({"available": true}));
                    }
                    Err(e) => format!(
                        "KINO {version} est disponible. {e} Vous pouvez aussi le télécharger depuis la page des versions."
                    ),
                }
            }
        };
        let mut inner = self.inner();
        inner.message = message.clone();
        inner.progress = 0;
        inner.progress_label.clear();
        drop(inner);
        with(
            self.status(),
            json!({"available": false, "message": message}),
        )
    }

    async fn prepare_windows(&self, release: &Value) -> Result<()> {
        let signature_name = format!("{WINDOWS_INSTALLER}.sig");
        let (Some(asset), Some(signature)) = (
            release["assets"].get(WINDOWS_INSTALLER).cloned(),
            release["assets"].get(&signature_name).cloned(),
        ) else {
            return Err(
                "Cette version n'est pas signée pour les mises à jour automatiques.".into(),
            );
        };
        if signature["size"].as_u64().unwrap_or(u64::MAX) > MAX_SIGNATURE_BYTES as u64 {
            return Err("Signature trop volumineuse.".into());
        }
        std::fs::create_dir_all(&self.state_dir).map_err(|e| e.to_string())?;
        let stage = self.state_dir.join(format!("stage-{}", &token()[..16]));
        std::fs::create_dir(&stage).map_err(|e| e.to_string())?;
        let work = async {
            let signature_path =
                download_asset(&signature, &stage.join(&signature_name), &|_| {}).await?;
            let raw = std::fs::read(&signature_path).map_err(|e| e.to_string())?;
            let payload = verify_installer_signature(&raw, &self.key()?, app_version())?;
            if payload["version"] != release["version"]
                || payload["artifact"]["name"] != WINDOWS_INSTALLER
                || payload["artifact"]["size"] != asset["size"]
            {
                return Err("La signature ne correspond pas à cette version.".to_string());
            }
            self.inner().message = format!(
                "Téléchargement de KINO {}…",
                crate::play::text(&release["version"])
            );
            let installer = download_asset(
                &asset,
                &stage.join(WINDOWS_INSTALLER),
                &self.progress_setter("Téléchargement"),
            )
            .await?;
            if sha256_file(&installer)? != payload["artifact"]["sha256"].as_str().unwrap_or("") {
                return Err("L'installateur téléchargé est modifié ou incomplet.".to_string());
            }
            Ok((payload, installer))
        };
        match work.await {
            Ok((payload, installer)) => {
                let mut inner = self.inner();
                self.discard_candidate(&mut inner);
                let path = installer.display().to_string();
                inner.candidate =
                    Some(json!({"payload": payload, "candidate": path, "installer": path}));
                Ok(())
            }
            Err(e) => {
                let _ = std::fs::remove_dir_all(&stage);
                Err(e)
            }
        }
    }

    fn install_windows(&self) -> Result<Value> {
        let mut inner = self.inner();
        let candidate = inner
            .candidate
            .clone()
            .filter(|c| c.get("installer").is_some())
            .ok_or("Recherchez d'abord une mise à jour.")?;
        let installer = PathBuf::from(crate::play::text(&candidate["installer"]));
        if sha256_file(&installer)?
            != candidate["payload"]["artifact"]["sha256"]
                .as_str()
                .unwrap_or("")
        {
            return Err("L'installateur a changé depuis sa vérification.".into());
        }
        // Detached: the installer waits for KINO to quit (/UPDATE=1), installs, then relaunches it.
        let mut command = std::process::Command::new(&installer);
        command
            .args([
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/UPDATE=1",
            ])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        #[cfg(windows)]
        std::os::windows::process::CommandExt::creation_flags(
            &mut command,
            0x0000_0008 | 0x0000_0200,
        );
        command.spawn().map_err(|e| e.to_string())?;
        inner.message = "KINO va fermer, s'installer puis redémarrer automatiquement.".into();
        Ok(json!({"scheduled": true, "message": inner.message}))
    }

    /// A local package, checked and staged.
    pub fn prepare(&self, package: &Path) -> Result<Value> {
        if !cfg!(target_os = "macos") && self.system_checks {
            return Err("Les paquets locaux ne concernent que KINO sur macOS.".into());
        }
        if self.system_checks {
            self.require_installed()?;
        }
        {
            let mut inner = self.inner();
            self.discard_candidate(&mut inner);
            inner.progress = 0;
            inner.progress_label = "Vérification du paquet".into();
            inner.message = "Vérification de la signature et de l'application…".into();
        }
        private_dir(&self.state_dir)?;
        let key = self.key()?;
        let stage = self.state_dir.join(format!("stage-{}", &token()[..16]));
        let progress = |value: u8| self.inner().progress = value;
        match verify_and_extract(
            package,
            &stage,
            &key,
            app_version(),
            architecture(),
            &progress,
            self.system_checks,
        ) {
            Ok(result) => {
                let mut inner = self.inner();
                inner.candidate = Some(result);
                inner.message = "Paquet vérifié. Installation prête.".into();
                drop(inner);
                Ok(self.status())
            }
            Err(e) => {
                self.inner().message = e.clone();
                Err(e)
            }
        }
    }

    /// Signed packages in Downloads and KINO's inbox, newest first.
    pub fn check_local_updates(&self) -> Result<Value> {
        if !cfg!(target_os = "macos") && self.system_checks {
            return Err("Les paquets locaux ne concernent que KINO sur macOS.".into());
        }
        if self.system_checks {
            self.require_installed()?;
        }
        {
            let mut inner = self.inner();
            self.discard_candidate(&mut inner);
            inner.progress = 0;
            inner.message = "Recherche des mises à jour locales signées…".into();
        }
        let inbox = self.state_dir.join("inbox");
        private_dir(&inbox)?;
        let key = self.key()?;
        let directories = [
            (
                crate::paths::home().join("Downloads"),
                "Téléchargements".to_string(),
            ),
            (inbox, "dossier KINO".to_string()),
        ];
        for (path, payload, label) in
            local_candidates(&directories, &key, app_version(), architecture())
        {
            if let Ok(result) = self.prepare(&path) {
                let message = format!(
                    "KINO {} trouvé dans {label}. Paquet vérifié.",
                    crate::play::text(&payload["version"])
                );
                self.inner().message = message.clone();
                return Ok(with(result, json!({"available": true, "message": message})));
            }
        }
        let message = "Aucun nouveau paquet signé trouvé dans Téléchargements ou le dossier KINO. Vous pouvez aussi choisir un paquet ailleurs sur ce Mac.";
        self.inner().message = message.into();
        Ok(with(
            self.status(),
            json!({"available": false, "message": message}),
        ))
    }

    /// Hands the install (or the rollback) to the native helper, which waits for KINO to quit.
    fn handoff(&self, action: &str) -> Result<Value> {
        let inner = self.inner();
        if action == "install" && inner.candidate.is_none() {
            return Err("Choisissez d'abord un paquet de mise à jour.".into());
        }
        private_dir(&self.state_dir)?;
        let helper_source = self.app_path.join("Contents/Resources/kino-update-helper");
        if !helper_source.is_file() {
            return Err("Assistant de mise à jour absent de l'application.".into());
        }
        let token = token();
        let helper = self.state_dir.join(format!("helper-{token}"));
        std::fs::copy(&helper_source, &helper).map_err(|e| e.to_string())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&helper, std::fs::Permissions::from_mode(0o700));
        }
        if action == "install" {
            snapshot_user_data(&self.state_dir.join(format!("data-{token}")))?;
        }
        let state = self.read_state();
        let backup = if action == "install" {
            self.app_path
                .parent()
                .unwrap_or(Path::new("/"))
                .join(format!(".KINO.backup-{token}.app"))
        } else {
            PathBuf::from(crate::play::text(&state["backup_path"]))
        };
        if action == "rollback" && (!backup.is_dir() || state["data_schema"] != json!(DATA_SCHEMA))
        {
            return Err("Aucune version précédente compatible n'est disponible.".into());
        }
        let candidate = inner.candidate.clone();
        let job = json!({
            "action": action, "pid": std::process::id(), "app_path": self.app_path.display().to_string(),
            "candidate_path": candidate.as_ref().map(|c| c["candidate"].clone()).unwrap_or(json!("")),
            "backup_path": backup.display().to_string(),
            "backup_version": if action == "install" { json!(bundle_version(&self.app_path)?) } else { json!(crate::play::text(&state["backup_version"])) },
            "version": candidate.as_ref().map(|c| c["payload"]["version"].clone()).unwrap_or_else(|| json!(crate::play::text(&state["backup_version"]))),
            "data_schema": DATA_SCHEMA,
            "status_path": self.state_dir.join("status.json").display().to_string(),
            "pending_path": self.state_dir.join("pending.json").display().to_string(),
            "ack_path": self.state_dir.join(format!("ack-{token}.json")).display().to_string(),
            "nonce": token,
        });
        let job_path = self.state_dir.join(format!("job-{token}.json"));
        std::fs::write(&job_path, job.to_string()).map_err(|e| e.to_string())?;
        private(&job_path);
        let mut command = std::process::Command::new(&helper);
        command
            .arg(&job_path)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        #[cfg(unix)]
        std::os::unix::process::CommandExt::process_group(&mut command, 0);
        command.spawn().map_err(|e| e.to_string())?;
        Ok(json!({"scheduled": true, "message": "KINO va fermer puis redémarrer automatiquement."}))
    }

    pub fn install(&self) -> Result<Value> {
        if cfg!(windows) {
            return self.install_windows();
        }
        self.handoff("install")
    }

    pub fn rollback(&self) -> Result<Value> {
        if !cfg!(target_os = "macos") {
            return Err("Le retour à la version précédente n'est disponible que sur macOS.".into());
        }
        self.handoff("rollback")
    }
}

fn with(mut base: Value, extra: Value) -> Value {
    if let (Some(b), Some(e)) = (base.as_object_mut(), extra.as_object()) {
        for (k, v) in e {
            b.insert(k.clone(), v.clone());
        }
    }
    base
}

/// The version a package announces, read without trusting it (only to discard it).
fn package_version(path: &Path) -> String {
    let read = || -> Option<String> {
        let mut package = zip::ZipArchive::new(std::fs::File::open(path).ok()?).ok()?;
        let mut raw = String::new();
        package
            .by_name("manifest.json")
            .ok()?
            .read_to_string(&mut raw)
            .ok()?;
        let data: Value = serde_json::from_str(&raw).ok()?;
        data["payload"]["version"].as_str().map(str::to_string)
    };
    read().unwrap_or_else(|| {
        Regex::new(r"^KINO-(\d+\.\d+\.\d+)-[A-Za-z0-9_]+\.kinoupdate$")
            .unwrap()
            .captures(
                &path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default(),
            )
            .map(|c| c[1].to_string())
            .unwrap_or_default()
    })
}

/// Removes what finished updates leave behind. Kept: the rollback bundle and
/// the newest other backup, the newest data snapshots, and everything while
/// an update awaits confirmation.
pub fn prune_leftovers(app_path: &Path, state_dir: &Path, current: &str, now: f64) -> Value {
    let mut removed =
        json!({"stages": 0, "work_files": 0, "packages": 0, "snapshots": 0, "app_backups": 0});
    if !state_dir.is_dir() || state_dir.join("pending.json").exists() {
        return removed;
    }
    let age = |p: &Path| {
        std::fs::symlink_metadata(p)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(0.0, |d| now - d.as_secs_f64())
    };
    let plain_dir = |p: &Path| std::fs::symlink_metadata(p).is_ok_and(|m| m.is_dir());
    let mut bump = |key: &str| removed[key] = json!(removed[key].as_i64().unwrap_or(0) + 1);
    for entry in std::fs::read_dir(state_dir).into_iter().flatten().flatten() {
        let (path, name) = (
            entry.path(),
            entry.file_name().to_string_lossy().into_owned(),
        );
        let is_file = std::fs::symlink_metadata(&path).is_ok_and(|m| m.is_file());
        if name.starts_with("stage-") && plain_dir(&path) && age(&path) > STALE_STAGE_SEC {
            let _ = std::fs::remove_dir_all(&path);
            bump("stages");
        } else if WORK_FILE.is_match(&name) && is_file && age(&path) > STALE_WORK_FILE_SEC {
            let _ = std::fs::remove_file(&path);
            bump("work_files");
        }
    }
    let inbox = state_dir.join("inbox");
    if plain_dir(&inbox) {
        for entry in std::fs::read_dir(&inbox).into_iter().flatten().flatten() {
            let (path, name) = (
                entry.path(),
                entry.file_name().to_string_lossy().into_owned(),
            );
            if !(name.starts_with("KINO-")
                && name.ends_with(".kinoupdate")
                && std::fs::symlink_metadata(&path).is_ok_and(|m| m.is_file()))
            {
                continue;
            }
            if let (Ok(v), Ok(c)) = (
                version_tuple(&package_version(&path)),
                version_tuple(current),
            ) && v <= c
            {
                let _ = std::fs::remove_file(&path);
                bump("packages");
            }
        }
    }
    let mut snapshots: Vec<PathBuf> = std::fs::read_dir(state_dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .is_some_and(|n| n.to_string_lossy().starts_with("data-"))
                && plain_dir(p)
        })
        .collect();
    snapshots.sort_by(|a, b| age(a).total_cmp(&age(b)));
    for snapshot in snapshots.iter().skip(KEEP_DATA_SNAPSHOTS) {
        let _ = std::fs::remove_dir_all(snapshot);
        bump("snapshots");
    }
    let rollback = std::fs::read_to_string(state_dir.join("status.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .map(|s| PathBuf::from(crate::play::text(&s["backup_path"])))
        .unwrap_or_default();
    let parent = app_path.parent().unwrap_or(Path::new("/"));
    let mut backups: Vec<PathBuf> = std::fs::read_dir(parent)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name().is_some_and(|n| {
                let n = n.to_string_lossy();
                n.starts_with(".KINO.backup-") && n.ends_with(".app")
            }) && plain_dir(p)
        })
        .collect();
    backups.sort_by(|a, b| age(a).total_cmp(&age(b)));
    let others: Vec<&PathBuf> = backups
        .iter()
        .filter(|b| **b != rollback && b.as_path() != app_path)
        .collect();
    for backup in others.iter().skip(1) {
        let _ = std::fs::remove_dir_all(backup);
        bump("app_backups");
    }
    removed
}

/// Called once the page has loaded after a helper-launched update: tells the helper KINO started.
pub fn acknowledge_ready(app_path: &Path, state_dir: &Path) {
    if !cfg!(target_os = "macos") {
        return;
    }
    let read = || -> Option<()> {
        let data: Value =
            serde_json::from_str(&std::fs::read_to_string(state_dir.join("pending.json")).ok()?)
                .ok()?;
        if data["version"] != json!(bundle_version(app_path).ok()?) {
            return None;
        }
        let ack = PathBuf::from(data["ack_path"].as_str()?);
        if ack.parent() != Some(state_dir)
            || !ack.file_name()?.to_string_lossy().starts_with("ack-")
        {
            return None;
        }
        std::fs::write(
            ack,
            json!({"nonce": data["nonce"], "ready": true}).to_string(),
        )
        .ok()
    };
    let _ = read();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constants_follow_the_python_declaration() {
        let declared = include_str!("../../../src/kino_version.py");
        for (name, value) in [
            ("BUNDLE_ID", format!("\"{BUNDLE_ID}\"")),
            ("DATA_SCHEMA", DATA_SCHEMA.to_string()),
            ("UPDATE_FORMAT", UPDATE_FORMAT.to_string()),
            ("UPDATE_REPO", format!("\"{UPDATE_REPO}\"")),
        ] {
            assert!(declared.contains(&format!("{name} = {value}")), "{name}");
        }
    }

    #[test]
    fn canonical_and_versions() {
        let v = json!({"b": 1, "a": {"z": "é", "c": [2, {"y": null, "x": true}]}});
        assert_eq!(
            String::from_utf8(canonical_json(&v)).unwrap(),
            r#"{"a":{"c":[2,{"x":true,"y":null}],"z":"é"},"b":1}"#
        );
        assert!(version_tuple("1.10.0").unwrap() > version_tuple("1.9.9").unwrap());
        assert!(version_tuple("01.2.3").is_err() && version_tuple("1.2").is_err());
        let release = json!({"tag_name": "v2.0.0", "body": "Notes", "html_url": "https://evil.example/x", "assets": [
            {"name": "KINO-Windows.exe", "browser_download_url": format!("{}v2.0.0/KINO-Windows.exe", download_prefix()), "size": 10},
            {"name": "x", "browser_download_url": "https://evil.example/x", "size": 10}]});
        let parsed = parse_release(&release).unwrap();
        assert_eq!(parsed["page"], releases_page());
        assert_eq!(parsed["assets"].as_object().unwrap().len(), 1);
        assert!(parse_release(&json!({"tag_name": "v2.0.0", "draft": true})).is_err());
    }
}
