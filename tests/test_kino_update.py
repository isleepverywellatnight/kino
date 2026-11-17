import base64
import hashlib
import json
import os
import plistlib
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import kino_update


def signed_manifest(payload, key):
    return kino_update.canonical_json({
        "payload": payload,
        "signature": base64.b64encode(key.sign(kino_update.canonical_json(payload))).decode("ascii"),
    })


def fake_bundle(root, version, pending_path=None, self_check_exit=0):
    app = root / "KINO.app"
    executable = app / "Contents/MacOS/KINO"
    executable.parent.mkdir(parents=True)
    with (app / "Contents/Info.plist").open("wb") as fh:
        plistlib.dump({
            "CFBundleIdentifier": "com.kino.desktop",
            "CFBundleExecutable": "KINO",
            "CFBundleShortVersionString": version,
        }, fh)
    script = f"#!/bin/sh\nif [ \"$1\" = '--self-check' ]; then exit {self_check_exit}; fi\n"
    if pending_path:
        script += (
            f"'{sys.executable}' - '{pending_path}' <<'PY'\n"
            "import json, pathlib, sys\n"
            "data = json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
            "pathlib.Path(data['ack_path']).write_text(json.dumps({'nonce': data['nonce'], 'ready': True}))\n"
            "PY\n"
            "sleep 1\n"
        )
    executable.write_text(script, encoding="utf-8")
    executable.chmod(0o755)
    return app


@unittest.skipUnless(sys.platform == "darwin", "macOS update helper")
class KINOUpdateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kino-update-test-", dir="/private/tmp"))
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def payload(self, archive_name, digest, size, **overrides):
        value = {
            "format": 1,
            "version": "9.2.0",
            "platform": "darwin",
            "architecture": "arm64",
            "bundle_id": "com.kino.desktop",
            "data_schema": 1,
            "min_macos": "13.0",
            "notes": "Mise à jour locale de test.",
            "artifact": {"name": archive_name, "sha256": digest, "size": size},
        }
        value.update(overrides)
        return value

    def make_package(self, payload_override=None, mutate_artifact=False, *,
                     version="9.2.0", folder=None):
        folder = folder or self.root
        folder.mkdir(parents=True, exist_ok=True)
        app = fake_bundle(folder / "source", version)
        archive = folder / f"KINO-{version}-arm64.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(app, arcname="KINO.app")
        raw = archive.read_bytes()
        payload = self.payload(archive.name, hashlib.sha256(raw).hexdigest(), len(raw),
                               version=version)
        if payload_override:
            payload.update(payload_override)
        package = folder / f"KINO-{version}-arm64.kinoupdate"
        with zipfile.ZipFile(package, "w") as outer:
            outer.writestr("manifest.json", signed_manifest(payload, self.key))
            outer.writestr(archive.name, raw[:-1] if mutate_artifact else raw)
        return package, payload

    def test_signed_manifest_rejects_tamper_wrong_arch_and_schema(self):
        package, payload = self.make_package()
        manifest = signed_manifest(payload, self.key)
        verified = kino_update.verify_manifest(manifest, self.public, architecture="arm64", os_version="15.0")
        self.assertEqual(verified["version"], "9.2.0")
        forged = json.loads(manifest)
        forged["payload"]["notes"] = "Texte falsifié"
        with self.assertRaisesRegex(kino_update.UpdateError, "Signature"):
            kino_update.verify_manifest(kino_update.canonical_json(forged), self.public, architecture="arm64")
        with self.assertRaisesRegex(kino_update.UpdateError, "Paquet pour"):
            kino_update.verify_manifest(manifest, self.public, architecture="x86_64")
        incompatible = self.payload(payload["artifact"]["name"], payload["artifact"]["sha256"],
                                    payload["artifact"]["size"], data_schema=2)
        with self.assertRaisesRegex(kino_update.UpdateError, "compatible"):
            kino_update.verify_manifest(signed_manifest(incompatible, self.key), self.public,
                                        architecture="arm64", os_version="15.0")

    def test_truncated_artifact_is_rejected_before_install(self):
        package, _ = self.make_package(mutate_artifact=True)
        with self.assertRaisesRegex(kino_update.UpdateError, "Taille"):
            kino_update.verify_and_extract(package, self.root / "stage", self.public,
                                           architecture="arm64", run_self_check=False)
        self.assertFalse((self.root / "stage").exists())

    def test_local_package_extracts_to_isolated_stage(self):
        package, _ = self.make_package()
        with patch.object(kino_update, "verify_candidate") as verify:
            result = kino_update.verify_and_extract(package, self.root / "stage", self.public,
                                                    architecture="arm64", run_self_check=False)
        self.assertEqual(result["payload"]["version"], "9.2.0")
        self.assertTrue((self.root / "stage/KINO.app/Contents/Info.plist").exists())
        verify.assert_called_once()

    def test_local_search_ignores_tamper_and_bad_artifact_then_prepares_valid_version(self):
        downloads = self.root / "Downloads"
        inbox = self.root / "state/inbox"
        good, _ = self.make_package(version="9.2.0", folder=downloads)
        self.make_package(version="9.3.0", folder=inbox, mutate_artifact=True)
        forged, _ = self.make_package(version="9.4.0", folder=self.root / "forged")
        with zipfile.ZipFile(forged) as package:
            envelope = json.loads(package.read("manifest.json"))
            artifact = package.read(envelope["payload"]["artifact"]["name"])
        envelope["payload"]["notes"] = "Falsifié"
        forged_in_downloads = downloads / forged.name
        with zipfile.ZipFile(forged_in_downloads, "w") as package:
            package.writestr("manifest.json", kino_update.canonical_json(envelope))
            package.writestr(envelope["payload"]["artifact"]["name"], artifact)
        manager = kino_update.UpdateManager(app_path=Path("/Applications/KINO.app"),
                                            state_dir=self.root / "state",
                                            public_key_hex=self.public)
        with patch.object(Path, "home", return_value=self.root), \
             patch.object(manager, "_require_installed"), \
             patch.object(kino_update, "verify_candidate"):
            result = manager.check_local_updates()
        self.assertTrue(result["available"])
        self.assertEqual(result["candidate"]["version"], "9.2.0")
        self.assertIn("Téléchargements", result["message"])
        self.assertEqual(Path(manager._candidate["candidate"]).name, "KINO.app")
        self.assertTrue(good.is_file())
        with patch.object(Path, "home", return_value=self.root), \
             patch.object(manager, "_require_installed"), \
             patch.object(kino_update, "verify_candidate"):
            manager.check_local_updates()
        self.assertEqual(len(list((self.root / "state").glob("stage-*"))), 1)

    def test_empty_local_search_keeps_app_unchanged(self):
        package, _ = self.make_package(folder=self.root / "elsewhere")
        manager = kino_update.UpdateManager(app_path=Path("/Applications/KINO.app"),
                                            state_dir=self.root / "state",
                                            public_key_hex=self.public)
        with patch.object(Path, "home", return_value=self.root), \
             patch.object(manager, "_require_installed"), \
             patch.object(kino_update, "verify_candidate"):
            manager.prepare(package)
            result = manager.check_local_updates()
        self.assertFalse(result["available"])
        self.assertIsNone(result["candidate"])
        self.assertIn("Aucun nouveau paquet", result["message"])
        self.assertEqual(list((self.root / "state").glob("stage-*")), [])

    def write_job(self, *, action, app, candidate, backup, status_path, pending_path, version, backup_version):
        nonce = secrets.token_hex(12)
        state = status_path.parent
        state.mkdir(parents=True, exist_ok=True)
        job = {
            "action": action,
            "pid": 10_000_000 + os.getpid(),
            "app_path": str(app),
            "candidate_path": str(candidate),
            "backup_path": str(backup),
            "backup_version": backup_version,
            "version": version,
            "data_schema": 1,
            "status_path": str(status_path),
            "pending_path": str(pending_path),
            "ack_path": str(state / f"ack-{nonce}.json"),
            "nonce": nonce,
        }
        path = state / f"job-{nonce}.json"
        path.write_text(json.dumps(job), encoding="utf-8")
        path.chmod(0o600)
        return path

    def run_helper(self, job_path):
        helper = Path(__file__).resolve().parent.parent / "packaging/update-helper/kino-update-helper"
        return subprocess.run([str(helper), str(job_path)], capture_output=True, text=True, timeout=20)

    def test_atomic_install_and_rollback_preserve_user_data(self):
        state = self.root / "state"
        status_path = state / "status.json"
        pending_path = state / "pending.json"
        old = fake_bundle(self.root, "9.1.0", pending_path)
        new = fake_bundle(self.root / "stage", "9.2.0", pending_path)
        db = self.root / "kino_data.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE watchlist (id TEXT)")
            conn.execute("CREATE TABLE watch_history (id TEXT)")
            conn.execute("CREATE TABLE app_config (key TEXT, value TEXT)")
            conn.execute("INSERT INTO watchlist VALUES ('film')")
            conn.execute("INSERT INTO watch_history VALUES ('episode')")
            conn.execute("INSERT INTO app_config VALUES ('language','fr')")
        kino_update.snapshot_database(db, state / "data.sqlite")
        backup = self.root / ".KINO.backup-a.app"
        job = self.write_job(action="install", app=old, candidate=new, backup=backup,
                             status_path=status_path, pending_path=pending_path,
                             version="9.2.0", backup_version="9.1.0")
        result = self.run_helper(job)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(kino_update._bundle_version(old), "9.2.0")
        self.assertEqual(kino_update._bundle_version(backup), "9.1.0")
        rollback = self.write_job(action="rollback", app=old, candidate=new, backup=backup,
                                  status_path=status_path, pending_path=pending_path,
                                  version="9.1.0", backup_version="9.1.0")
        result = self.run_helper(rollback)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(kino_update._bundle_version(old), "9.1.0")
        for path in (db, state / "data.sqlite"):
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute("SELECT id FROM watchlist").fetchone()[0], "film")
                self.assertEqual(conn.execute("SELECT id FROM watch_history").fetchone()[0], "episode")
                self.assertEqual(conn.execute("SELECT value FROM app_config").fetchone()[0], "fr")

    def test_failed_self_check_restores_original_bundle(self):
        state = self.root / "state"
        pending_path = state / "pending.json"
        old = fake_bundle(self.root, "9.1.0", pending_path)
        new = fake_bundle(self.root / "stage", "9.2.0", pending_path, self_check_exit=1)
        backup = self.root / ".KINO.backup-b.app"
        job = self.write_job(action="install", app=old, candidate=new, backup=backup,
                             status_path=state / "status.json", pending_path=pending_path,
                             version="9.2.0", backup_version="9.1.0")
        result = self.run_helper(job)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(kino_update._bundle_version(old), "9.1.0")
        self.assertIn("annulée", (state / "status.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
