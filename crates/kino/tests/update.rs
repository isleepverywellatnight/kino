//! Signed update packages, built here with a test key: a valid one is staged,
//! a tampered, mismatched, truncated or escaping one is refused, and the
//! cleanup keeps what a rollback needs.

use std::io::Write;
use std::path::{Path, PathBuf};

use base64::Engine;
use ed25519_dalek::{Signer, SigningKey};
use kino::update::{self, Updater, canonical_json};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

fn key() -> SigningKey {
    SigningKey::from_bytes(&[7u8; 32])
}

fn public_hex() -> String {
    hex::encode(key().verifying_key().to_bytes())
}

fn envelope(payload: &Value, signer: &SigningKey) -> Vec<u8> {
    let signature = base64::engine::general_purpose::STANDARD
        .encode(signer.sign(&canonical_json(payload)).to_bytes());
    canonical_json(&json!({"payload": payload, "signature": signature}))
}

/// A KINO.app tree as a .tar.gz, plus optional extra entries (name, link target).
fn app_archive(
    version: &str,
    extra_link: Option<(&str, &str)>,
    extra_file: Option<&str>,
) -> Vec<u8> {
    let mut plist = vec![];
    let mut info = plist::Dictionary::new();
    info.insert("CFBundleIdentifier".into(), "com.kino.desktop".into());
    info.insert("CFBundleExecutable".into(), "KINO".into());
    info.insert("CFBundleShortVersionString".into(), version.into());
    plist::to_writer_xml(&mut plist, &info).unwrap();
    let gz = flate2::write::GzEncoder::new(vec![], flate2::Compression::fast());
    let mut tar = tar::Builder::new(gz);
    let mut add = |name: &str, data: &[u8], mode: u32| {
        let mut header = tar::Header::new_gnu();
        header.set_size(data.len() as u64);
        header.set_mode(mode);
        header.set_cksum();
        tar.append_data(&mut header, name, data).unwrap();
    };
    add("KINO.app/Contents/Info.plist", &plist, 0o644);
    add(
        "KINO.app/Contents/MacOS/KINO",
        b"#!/bin/sh\nexit 0\n",
        0o4755,
    );
    if let Some(name) = extra_file {
        let mut header = tar::Header::new_gnu();
        header.set_size(1);
        header.set_mode(0o644);
        header.set_path("placeholder").ok();
        let raw = header.as_old_mut();
        raw.name = [0; 100];
        raw.name[..name.len()].copy_from_slice(name.as_bytes());
        header.set_cksum();
        tar.append(&header, &b"x"[..]).unwrap();
    }
    if let Some((name, target)) = extra_link {
        let mut header = tar::Header::new_gnu();
        header.set_entry_type(tar::EntryType::Symlink);
        header.set_size(0);
        tar.append_link(&mut header, name, target).unwrap();
    }
    tar.into_inner().unwrap().finish().unwrap()
}

struct Built {
    path: PathBuf,
}

/// A .kinoupdate: manifest.json and the archive, the manifest signed by `signer`.
fn package(
    dir: &Path,
    version: &str,
    archive: &[u8],
    tweak: impl Fn(&mut Value),
    signer: &SigningKey,
) -> Built {
    let name = format!("KINO-{version}-{}.tar.gz", update::architecture());
    let mut payload = json!({
        "format": 1, "version": version, "platform": "darwin", "architecture": update::architecture(), "bundle_id": "com.kino.desktop",
        "data_schema": 1, "min_macos": "11.0", "notes": "Notes",
        "artifact": {"name": name, "sha256": hex::encode(Sha256::digest(archive)), "size": archive.len()},
    });
    tweak(&mut payload);
    let path = dir.join(format!(
        "KINO-{version}-{}.kinoupdate",
        update::architecture()
    ));
    let mut zip = zip::ZipWriter::new(std::fs::File::create(&path).unwrap());
    let stored =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    zip.start_file("manifest.json", stored).unwrap();
    zip.write_all(&envelope(&payload, signer)).unwrap();
    zip.start_file(name, stored).unwrap();
    zip.write_all(archive).unwrap();
    zip.finish().unwrap();
    Built { path }
}

fn stage(dir: &Path, built: &Built) -> Result<Value, String> {
    update::verify_and_extract(
        &built.path,
        &dir.join(format!("stage-{}", rand_name())),
        &public_hex(),
        "1.0.0",
        update::architecture(),
        &|_| {},
        false,
    )
}

fn rand_name() -> String {
    format!(
        "{:x}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    )
}

#[test]
fn a_valid_package_is_staged() {
    let dir = tempfile::tempdir().unwrap();
    let built = package(
        dir.path(),
        "2.0.0",
        &app_archive("2.0.0", None, None),
        |_| {},
        &key(),
    );
    let staged = stage(dir.path(), &built).unwrap();
    let app = PathBuf::from(staged["candidate"].as_str().unwrap());
    assert_eq!(staged["payload"]["version"], "2.0.0");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        // Executable kept, setuid dropped.
        assert_eq!(
            std::fs::metadata(app.join("Contents/MacOS/KINO"))
                .unwrap()
                .permissions()
                .mode()
                & 0o7777,
            0o755
        );
    }
    // The inspection reads the same metadata, nothing staged.
    assert_eq!(
        update::inspect_package(&built.path, &public_hex(), "1.0.0", update::architecture())
            .unwrap()["version"],
        "2.0.0"
    );
}

/// (label, change to the manifest, signing key, expected refusal).
type Case = (
    &'static str,
    Box<dyn Fn(&mut Value)>,
    SigningKey,
    &'static str,
);

#[test]
fn refused_packages() {
    let dir = tempfile::tempdir().unwrap();
    let archive = app_archive("2.0.0", None, None);
    let cases: Vec<Case> = vec![
        (
            "tampered",
            Box::new(|p: &mut Value| p["notes"] = json!("x")),
            SigningKey::from_bytes(&[9u8; 32]),
            "Signature du manifeste invalide.",
        ),
        (
            "arch",
            Box::new(|p: &mut Value| p["architecture"] = json!("ppc")),
            key(),
            "Paquet pour ppc",
        ),
        (
            "schema",
            Box::new(|p: &mut Value| p["data_schema"] = json!(2)),
            key(),
            "retour arrière",
        ),
        (
            "older",
            Box::new(|p: &mut Value| p["version"] = json!("0.9.0")),
            key(),
            "plus récent",
        ),
        (
            "macos",
            Box::new(|p: &mut Value| p["min_macos"] = json!("99.0")),
            key(),
            "exige macOS 99.0",
        ),
        (
            "hash",
            Box::new(|p: &mut Value| p["artifact"]["sha256"] = json!("0".repeat(64))),
            key(),
            "tronqué ou modifié",
        ),
    ];
    for (label, tweak, signer, expected) in cases {
        let sub = dir.path().join(label);
        std::fs::create_dir(&sub).unwrap();
        let built = package(&sub, "2.0.0", &archive, tweak, &signer);
        let error = stage(&sub, &built).unwrap_err();
        if !cfg!(target_os = "macos") && label == "macos" {
            continue;
        }
        assert!(error.contains(expected), "{label}: {error}");
        assert!(
            std::fs::read_dir(&sub).unwrap().all(|e| !e
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with("stage-")),
            "{label}: stage left behind"
        );
    }
    // An archive escaping KINO.app, by a path or a link.
    for (label, archive) in [
        (
            "traversal",
            app_archive("2.0.0", None, Some("KINO.app/../evil")),
        ),
        ("outside", app_archive("2.0.0", None, Some("Other.app/x"))),
        (
            "link",
            app_archive(
                "2.0.0",
                Some(("KINO.app/Contents/link", "../../../etc/passwd")),
                None,
            ),
        ),
        (
            "absolute",
            app_archive(
                "2.0.0",
                Some(("KINO.app/Contents/abs", "/etc/passwd")),
                None,
            ),
        ),
    ] {
        let sub = dir.path().join(label);
        std::fs::create_dir(&sub).unwrap();
        let built = package(&sub, "2.0.0", &archive, |_| {}, &key());
        let error = stage(&sub, &built).unwrap_err();
        assert!(
            error.contains("interdit") || error.contains("hors de l'application"),
            "{label}: {error}"
        );
    }
    // A Plist naming another app.
    let sub = dir.path().join("identity");
    std::fs::create_dir(&sub).unwrap();
    let built = package(
        &sub,
        "2.0.0",
        &app_archive("2.1.0", None, None),
        |_| {},
        &key(),
    );
    assert!(
        stage(&sub, &built)
            .unwrap_err()
            .contains("diffère du manifeste")
    );
}

#[test]
fn the_local_search_skips_bad_packages() {
    let dir = tempfile::tempdir().unwrap();
    let downloads = dir.path().join("Downloads");
    std::fs::create_dir(&downloads).unwrap();
    package(
        &downloads,
        "3.0.0",
        &app_archive("3.0.0", None, None),
        |p| p["notes"] = json!("tampered"),
        &SigningKey::from_bytes(&[1u8; 32]),
    );
    package(
        &downloads,
        "2.5.0",
        &app_archive("2.5.0", None, None),
        |_| {},
        &key(),
    );
    let found = update::local_candidates(
        &[(downloads, "Téléchargements".into())],
        &public_hex(),
        "1.0.0",
        update::architecture(),
    );
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].1["version"], "2.5.0");
    let updater = Updater::new(
        dir.path().join("KINO.app"),
        dir.path().join("state"),
        Some(public_hex()),
    )
    .without_system_checks();
    let status = updater
        .prepare(&found[0].0)
        .unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(status["candidate"]["version"], "2.5.0");
}

#[test]
fn cleanup_keeps_the_rollback() {
    let dir = tempfile::tempdir().unwrap();
    let (apps, state) = (dir.path().join("Applications"), dir.path().join("state"));
    let app = apps.join("KINO.app");
    std::fs::create_dir_all(&app).unwrap();
    std::fs::create_dir_all(state.join("inbox")).unwrap();
    for name in ["stage-old", "data-1", "data-2", "data-3", "data-4"] {
        std::fs::create_dir(state.join(name)).unwrap();
    }
    for name in [
        ".KINO.backup-a.app",
        ".KINO.backup-b.app",
        ".KINO.backup-c.app",
    ] {
        std::fs::create_dir(apps.join(name)).unwrap();
    }
    std::fs::write(
        state.join("status.json"),
        json!({"backup_path": apps.join(".KINO.backup-a.app")}).to_string(),
    )
    .unwrap();
    std::fs::write(state.join(format!("job-{}.json", "a".repeat(24))), "{}").unwrap();
    std::fs::write(
        state.join("inbox").join("KINO-1.0.0-arm64.kinoupdate"),
        "old",
    )
    .unwrap();
    let later = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs_f64()
        + 2.0 * 86400.0;
    // Waiting for the new version's confirmation: nothing is touched.
    std::fs::write(state.join("pending.json"), "{}").unwrap();
    assert_eq!(
        update::prune_leftovers(&app, &state, "1.5.0", later)["stages"],
        0
    );
    std::fs::remove_file(state.join("pending.json")).unwrap();
    let removed = update::prune_leftovers(&app, &state, "1.5.0", later);
    assert_eq!(
        removed,
        json!({"stages": 1, "work_files": 1, "packages": 1, "snapshots": 1, "app_backups": 1})
    );
    assert!(
        apps.join(".KINO.backup-a.app").is_dir(),
        "the rollback bundle stays"
    );
}

/// A description signed by the Python release tooling (the same test key)
/// checks here: both sides sign the same canonical JSON, accents included.
#[test]
fn python_signatures_verify() {
    let raw = include_bytes!("fixtures/python_signed_installer.json");
    let raw = raw.trim_ascii_end();
    let payload = update::verify_installer_signature(raw, &public_hex(), "1.0.0").unwrap();
    assert_eq!(payload["notes"], "Nouveautés — été « test »\n");
    let mut tampered = raw.to_vec();
    let at = tampered.windows(5).position(|w| w == b"9.0.0").unwrap();
    tampered[at] = b'8';
    assert!(update::verify_installer_signature(&tampered, &public_hex(), "1.0.0").is_err());
}
