//! The macOS update helper shipped in KINO.app (packaging/update-helper), run
//! for real on stand-in bundles: an install swaps the new KINO in and keeps the
//! old one for a rollback; a new KINO that fails its self-check is undone; a
//! rollback brings the old one back. The stand-ins are shell scripts: nothing
//! with a window starts. And the job KINO writes has the fields the helper reads.

const HELPER_SOURCE: &str =
    include_str!("../../../packaging/update-helper/kino_update_helper.swift");
const UPDATE_SOURCE: &str = include_str!("../src/update.rs");

/// The fields of the helper's `Job` and of the job `handoff` writes are the same.
#[test]
fn the_job_has_the_fields_the_helper_reads() {
    let start = HELPER_SOURCE.find("struct Job: Decodable {").unwrap();
    let end = start + HELPER_SOURCE[start..].find('}').unwrap();
    let mut read: Vec<&str> = HELPER_SOURCE[start..end]
        .lines()
        .filter_map(|l| l.trim().strip_prefix("let "))
        .map(|l| l.split(':').next().unwrap().trim())
        .collect();
    let start = UPDATE_SOURCE.find("let job = json!({").unwrap();
    let end = start + UPDATE_SOURCE[start..].find("});").unwrap();
    let pattern = regex::Regex::new(r#""([a-z_]+)":"#).unwrap();
    let mut written: Vec<&str> = pattern
        .captures_iter(&UPDATE_SOURCE[start..end])
        .map(|c| c.get(1).unwrap().as_str())
        .collect();
    read.sort();
    written.sort();
    assert_eq!(read.len(), 12);
    assert_eq!(read, written);
}

#[cfg(target_os = "macos")]
mod macos {
    use std::os::unix::fs::PermissionsExt;
    use std::path::{Path, PathBuf};
    use std::process::Command;
    use std::time::{Duration, Instant};

    /// A stand-in KINO.app: its program passes or fails the self-check, and when
    /// launched notes it ran and stays a few seconds, as a window would.
    fn bundle(dir: &Path, version: &str, passes_check: bool, launched: &Path) -> PathBuf {
        let app = dir.join("KINO.app");
        std::fs::create_dir_all(app.join("Contents/MacOS")).unwrap();
        std::fs::write(
            app.join("Contents/Info.plist"),
            format!(
                r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.kino.desktop</string>
<key>CFBundleExecutable</key><string>KINO</string>
<key>CFBundleShortVersionString</key><string>{version}</string>
</dict></plist>"#
            ),
        )
        .unwrap();
        let script = format!(
            r#"#!/bin/sh
if [ "$1" = "--self-check" ]; then exit {check}; fi
echo {version} >> "{launched}"
sleep 5
"#,
            check = if passes_check { 0 } else { 1 },
            launched = launched.display(),
        );
        let program = app.join("Contents/MacOS/KINO");
        std::fs::write(&program, script).unwrap();
        std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o755)).unwrap();
        app
    }

    fn version(app: &Path) -> String {
        let plist = std::fs::read_to_string(app.join("Contents/Info.plist")).unwrap_or_default();
        regex::Regex::new(r"CFBundleShortVersionString</key><string>([^<]+)")
            .unwrap()
            .captures(&plist)
            .map(|c| c[1].to_string())
            .unwrap_or_default()
    }

    /// The pid of a process that has already ended (the KINO that quit).
    fn gone_pid() -> u32 {
        let mut child = Command::new("/usr/bin/true").spawn().unwrap();
        let pid = child.id();
        child.wait().unwrap();
        pid
    }

    struct Setup {
        _root: tempfile::TempDir,
        dir: PathBuf,
        state: PathBuf,
        launched: PathBuf,
        helper: PathBuf,
    }

    fn setup() -> Setup {
        // The helper only touches /Applications/KINO.app or this test root.
        let root = tempfile::Builder::new()
            .prefix("kino-update-test-")
            .tempdir_in("/private/tmp")
            .unwrap();
        let dir = root.path().join("Applications");
        let state = root.path().join("state");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::create_dir_all(&state).unwrap();
        let helper = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../packaging/update-helper/kino-update-helper");
        assert!(helper.is_file(), "the helper shipped in KINO.app");
        Setup {
            launched: root.path().join("launched.txt"),
            dir,
            state,
            helper,
            _root: root,
        }
    }

    /// Writes the job as KINO does (private file), runs the helper, waits for it.
    /// Meanwhile KINO's own confirmation runs, as the new window does once its
    /// page has loaded.
    fn run(s: &Setup, job: serde_json::Value) -> bool {
        let app = PathBuf::from(job["app_path"].as_str().unwrap());
        let path = s
            .state
            .join(format!("job-{}.json", job["nonce"].as_str().unwrap()));
        std::fs::write(&path, job.to_string()).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        let mut child = Command::new(&s.helper).arg(&path).spawn().unwrap();
        let deadline = Instant::now() + Duration::from_secs(60);
        loop {
            if let Some(status) = child.try_wait().unwrap() {
                return status.success();
            }
            assert!(Instant::now() < deadline, "the helper finishes");
            kino::update::acknowledge_ready(&app, &s.state);
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn job(
        s: &Setup,
        action: &str,
        nonce: &str,
        app: &Path,
        candidate: &str,
        backup: &Path,
        backup_version: &str,
        version: &str,
    ) -> serde_json::Value {
        serde_json::json!({
            "action": action, "pid": gone_pid(), "app_path": app.display().to_string(),
            "candidate_path": candidate, "backup_path": backup.display().to_string(),
            "backup_version": backup_version, "version": version, "data_schema": 1,
            "status_path": s.state.join("status.json").display().to_string(),
            "pending_path": s.state.join("pending.json").display().to_string(),
            "ack_path": s.state.join(format!("ack-{nonce}.json")).display().to_string(),
            "nonce": nonce,
        })
    }

    fn status(s: &Setup) -> serde_json::Value {
        serde_json::from_str(&std::fs::read_to_string(s.state.join("status.json")).unwrap())
            .unwrap()
    }

    #[test]
    fn installs_then_rolls_back() {
        let s = setup();
        let app = bundle(&s.dir, "1.0.0", true, &s.launched);
        let staged = s.state.join("stage");
        let candidate = bundle(&staged, "1.1.0", true, &s.launched);
        let nonce = "0123456789abcdef01234567";
        let backup = s.dir.join(format!(".KINO.backup-{nonce}.app"));
        let task = job(
            &s,
            "install",
            nonce,
            &app,
            &candidate.display().to_string(),
            &backup,
            "1.0.0",
            "1.1.0",
        );
        assert!(run(&s, task), "install succeeds");
        assert_eq!(version(&app), "1.1.0", "the new KINO is in place");
        assert_eq!(version(&backup), "1.0.0", "the old one is kept");
        let done = status(&s);
        assert_eq!(done["message"], "Mise à jour installée.");
        assert_eq!(done["backup_version"], "1.0.0");
        assert!(!s.state.join("pending.json").exists());
        assert!(
            !s.state.join(format!("job-{nonce}.json")).exists(),
            "the job is removed"
        );
        assert_eq!(
            std::fs::read_to_string(&s.launched).unwrap(),
            "1.1.0\n",
            "the new KINO was started"
        );

        let nonce = "fedcba9876543210fedcba98";
        let task = job(&s, "rollback", nonce, &app, "", &backup, "1.0.0", "1.0.0");
        assert!(run(&s, task), "rollback succeeds");
        assert_eq!(version(&app), "1.0.0", "the old KINO is back");
        assert_eq!(version(&backup), "1.1.0");
        assert_eq!(status(&s)["message"], "Version précédente restaurée.");
    }

    #[test]
    fn a_new_version_that_fails_its_check_is_undone() {
        let s = setup();
        let app = bundle(&s.dir, "1.0.0", true, &s.launched);
        let staged = s.state.join("stage");
        let candidate = bundle(&staged, "1.1.0", false, &s.launched);
        let nonce = "00112233445566778899aabb";
        let backup = s.dir.join(format!(".KINO.backup-{nonce}.app"));
        let task = job(
            &s,
            "install",
            nonce,
            &app,
            &candidate.display().to_string(),
            &backup,
            "1.0.0",
            "1.1.0",
        );
        assert!(!run(&s, task), "the helper reports the failure");
        assert_eq!(version(&app), "1.0.0", "the old KINO stays");
        assert!(!backup.exists());
        assert!(
            !s.dir.join(format!(".KINO.swap-{nonce}.app")).exists(),
            "nothing left behind"
        );
        let done = status(&s);
        assert!(
            done["message"]
                .as_str()
                .unwrap()
                .starts_with("Mise à jour annulée"),
            "{done}"
        );
        // The old KINO is started again.
        let deadline = Instant::now() + Duration::from_secs(5);
        while !s.launched.exists() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(50));
        }
        assert_eq!(std::fs::read_to_string(&s.launched).unwrap(), "1.0.0\n");
    }

    #[test]
    fn refuses_a_job_outside_the_allowed_places() {
        let s = setup();
        let elsewhere = tempfile::tempdir().unwrap();
        let app = bundle(elsewhere.path(), "1.0.0", true, &s.launched);
        let candidate = bundle(&s.state.join("stage"), "1.1.0", true, &s.launched);
        let nonce = "aabbccddeeff001122334455";
        let backup = elsewhere.path().join(format!(".KINO.backup-{nonce}.app"));
        let task = job(
            &s,
            "install",
            nonce,
            &app,
            &candidate.display().to_string(),
            &backup,
            "1.0.0",
            "1.1.0",
        );
        assert!(!run(&s, task));
        assert_eq!(version(&app), "1.0.0", "untouched");
        assert!(!s.launched.exists(), "nothing started");
    }
}
