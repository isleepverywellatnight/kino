//! Signs KINO updates: a macOS .kinoupdate package, or the .sig of a Windows
//! installer or a Linux AppImage. The private key lives outside the project and the app; the release
//! workflow reads it from the KINO_UPDATE_SIGNING_KEY secret.
//!
//!   kino-release --init-key [--key K]                  create the key pair (once)
//!   kino-release --app dist/KINO.app --output X        macOS: signed package
//!   kino-release --installer dist/KINO-Windows.exe     Windows: writes KINO-Windows.exe.sig
//!   kino-release --appimage dist/KINO-Linux-x86_64.AppImage   Linux: writes its .sig
//!
//! Common: --key PATH (PKCS#8 PEM), --notes TEXT, --min-macos 14.0, --public-key PATH

use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use base64::Engine;
use ed25519_dalek::SigningKey;
use ed25519_dalek::pkcs8::{DecodePrivateKey, EncodePrivateKey, spki::der::pem::LineEnding};
use kino::update::{
    self, BUNDLE_ID, DATA_SCHEMA, LINUX_ARTIFACT, SignedArtifact, UPDATE_FORMAT, WINDOWS_ARTIFACT,
    canonical_json,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

type Result<T> = std::result::Result<T, String>;

fn project() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// The public key KINO ships (`--public-key` names another one, for tests).
fn public_key_file() -> PathBuf {
    let args: Vec<String> = std::env::args().collect();
    args.iter()
        .position(|a| a == "--public-key")
        .and_then(|i| args.get(i + 1))
        .map(PathBuf::from)
        .unwrap_or_else(|| project().join("assets/update_public_key.hex"))
}

fn default_key() -> PathBuf {
    kino::paths::home().join("Library/Application Support/KINO/UpdateSigning/private-key.pem")
}

fn init_key(path: &Path) -> Result<()> {
    if path.exists() {
        return Err("La clé locale existe déjà ; aucune rotation automatique.".into());
    }
    let parent = path.parent().ok_or("dossier")?;
    std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut seed = [0u8; 32];
    rand::fill(&mut seed);
    let key = SigningKey::from_bytes(&seed);
    let pem = key
        .to_pkcs8_pem(LineEnding::LF)
        .map_err(|e| e.to_string())?;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
    options
        .open(path)
        .and_then(|mut f| f.write_all(pem.as_bytes()))
        .map_err(|e| e.to_string())?;
    std::fs::write(
        public_key_file(),
        hex::encode(key.verifying_key().to_bytes()) + "\n",
    )
    .map_err(|e| e.to_string())?;
    println!("Clé locale créée. Seule sa partie publique est incluse dans KINO.");
    Ok(())
}

/// The private key, checked against the public key KINO ships.
fn signing_key(path: &Path) -> Result<SigningKey> {
    let pem =
        std::fs::read_to_string(path).map_err(|e| format!("Clé de signature illisible : {e}"))?;
    let key = SigningKey::from_pkcs8_pem(&pem)
        .map_err(|_| "La clé de signature doit être Ed25519.".to_string())?;
    let pinned = std::fs::read_to_string(public_key_file()).map_err(|e| e.to_string())?;
    if hex::encode(key.verifying_key().to_bytes()) != pinned.trim() {
        return Err("La clé privée ne correspond pas à la clé publique de l'app.".into());
    }
    Ok(key)
}

fn sha256_and_size(path: &Path) -> Result<(String, u64)> {
    let mut file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let (mut digest, mut size, mut buffer) = (Sha256::new(), 0u64, vec![0u8; 1 << 20]);
    loop {
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        digest.update(&buffer[..n]);
        size += n as u64;
    }
    Ok((hex::encode(digest.finalize()), size))
}

fn envelope(payload: Value, key: &SigningKey) -> Vec<u8> {
    use ed25519_dalek::Signer;
    let signature = base64::engine::general_purpose::STANDARD
        .encode(key.sign(&canonical_json(&payload)).to_bytes());
    canonical_json(&json!({"payload": payload, "signature": signature}))
}

fn notes(text: &str) -> String {
    text.chars().take(4000).collect()
}

/// The .sig of a Windows installer or a Linux AppImage, next to it.
fn sign_artifact(installer: &Path, key: &Path, text: &str, target: &SignedArtifact) -> Result<()> {
    if installer.file_name().and_then(|n| n.to_str()) != Some(target.name) {
        return Err(format!("Nom de fichier attendu : {}", target.name));
    }
    // Built by the same job, from the same version declaration.
    let version = kino::backup::app_version();
    update::version_tuple(version)?;
    let key = signing_key(key)?;
    let (sha256, size) = sha256_and_size(installer)?;
    let payload = json!({"format": UPDATE_FORMAT, "version": version, "platform": target.platform, "architecture": target.architecture, "bundle_id": BUNDLE_ID,
                         "data_schema": DATA_SCHEMA, "notes": notes(text), "artifact": {"name": target.name, "sha256": sha256, "size": size}});
    let output = installer.with_file_name(format!("{}.sig", target.name));
    std::fs::write(&output, envelope(payload, &key)).map_err(|e| e.to_string())?;
    println!("Signature : {}", output.display());
    Ok(())
}

fn package(app: &Path, output: &Path, key: &Path, text: &str, min_macos: &str) -> Result<()> {
    let info: plist::Dictionary =
        plist::from_file(app.join("Contents/Info.plist")).map_err(|e| e.to_string())?;
    let get = |k: &str| {
        info.get(k)
            .and_then(|v| v.as_string())
            .unwrap_or("")
            .to_string()
    };
    if get("CFBundleIdentifier") != BUNDLE_ID {
        return Err("Identifiant de bundle incorrect.".into());
    }
    let version = get("CFBundleShortVersionString");
    update::version_tuple(&version)?;
    let arch = update::architecture();
    let archive_name = format!("KINO-{version}-{arch}.tar.gz");
    let key = signing_key(key)?;
    let work = std::env::temp_dir().join(format!("kino-package-{}", std::process::id()));
    std::fs::create_dir_all(&work).map_err(|e| e.to_string())?;
    let archive_path = work.join(&archive_name);
    {
        let file = std::fs::File::create(&archive_path).map_err(|e| e.to_string())?;
        let mut tar = tar::Builder::new(flate2::write::GzEncoder::new(
            file,
            flate2::Compression::default(),
        ));
        // Links stay links (the bundle's own), as the updater checks them.
        tar.follow_symlinks(false);
        tar.append_dir_all("KINO.app", app)
            .map_err(|e| e.to_string())?;
        tar.into_inner()
            .and_then(|gz| gz.finish())
            .map_err(|e| e.to_string())?;
    }
    let (sha256, size) = sha256_and_size(&archive_path)?;
    let payload = json!({"format": UPDATE_FORMAT, "version": version, "platform": "darwin", "architecture": arch, "bundle_id": BUNDLE_ID,
                         "data_schema": DATA_SCHEMA, "min_macos": min_macos, "notes": notes(text),
                         "artifact": {"name": archive_name, "sha256": sha256, "size": size}});
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let mut zip = zip::ZipWriter::new(std::fs::File::create(output).map_err(|e| e.to_string())?);
    let stored = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Stored)
        .large_file(true);
    zip.start_file("manifest.json", stored)
        .map_err(|e| e.to_string())?;
    zip.write_all(&envelope(payload, &key))
        .map_err(|e| e.to_string())?;
    zip.start_file(archive_name, stored)
        .map_err(|e| e.to_string())?;
    std::io::copy(
        &mut std::fs::File::open(&archive_path).map_err(|e| e.to_string())?,
        &mut zip,
    )
    .map_err(|e| e.to_string())?;
    zip.finish().map_err(|e| e.to_string())?;
    let _ = std::fs::remove_dir_all(&work);
    println!("Paquet local signé : {}", output.display());
    Ok(())
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let value = |name: &str| {
        args.iter()
            .position(|a| a == name)
            .and_then(|i| args.get(i + 1))
            .cloned()
    };
    let key = value("--key")
        .map(PathBuf::from)
        .unwrap_or_else(default_key);
    let text = value("--notes").unwrap_or_else(|| "Améliorations et corrections de KINO.".into());
    let done = if args.iter().any(|a| a == "--init-key") {
        init_key(&key)
    } else if let Some(installer) = value("--installer") {
        sign_artifact(Path::new(&installer), &key, &text, &WINDOWS_ARTIFACT)
    } else if let Some(image) = value("--appimage") {
        sign_artifact(Path::new(&image), &key, &text, &LINUX_ARTIFACT)
    } else if let (Some(app), Some(output)) = (value("--app"), value("--output")) {
        package(
            Path::new(&app),
            Path::new(&output),
            &key,
            &text,
            &value("--min-macos").unwrap_or_else(|| "14.0".into()),
        )
    } else {
        Err(
            "--app et --output sont requis pour créer un paquet (ou --installer, --appimage, ou --init-key)."
                .into(),
        )
    };
    if let Err(e) = done {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
