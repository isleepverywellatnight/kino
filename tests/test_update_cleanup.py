"""Finished updates no longer leave gigabytes behind, and rollback stays possible."""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

import kino_update

NOW = 2_000_000_000.0
TOKEN = "0123456789abcdef01234567"


def touch(path: Path, age: float, *, directory=False):
    if directory:
        path.mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"x" * 10)
    else:
        path.write_bytes(b"x")
    os.utime(path, (NOW - age, NOW - age))
    return path


def package(inbox: Path, version: str):
    path = inbox / f"KINO-{version}-arm64.kinoupdate"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"payload": {"version": version}, "signature": ""}))
    return path


class UpdateCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.apps = root / "Applications"
        self.state = root / "Updates"
        (self.apps / "KINO.app").mkdir(parents=True)
        (self.state / "inbox").mkdir(parents=True)

    def prune(self):
        return kino_update.prune_update_leftovers(app_path=self.apps / "KINO.app", state_dir=self.state,
                                                  current_version="1.1.16", now=NOW)

    def test_leftovers_are_removed_and_rollback_is_kept(self):
        old_stage = touch(self.state / "stage-aaaa", 3600, directory=True)
        fresh_stage = touch(self.state / "stage-bbbb", 60, directory=True)
        old_helper = touch(self.state / f"helper-{TOKEN}", 3 * 86400)
        fresh_ack = touch(self.state / f"ack-{TOKEN.replace('0', '1')}.json", 60)
        snapshots = [touch(self.state / f"data-{n:024x}", n * 3600, directory=True) for n in range(1, 6)]
        old_pkgs = [package(self.state / "inbox", v) for v in ("1.1.8", "1.1.16")]
        newer = package(self.state / "inbox", "1.1.17")
        backups = [touch(self.apps / f".KINO.backup-{n:024x}.app", n * 86400, directory=True) for n in range(1, 5)]
        rollback = backups[2]
        (self.state / "status.json").write_text(json.dumps({"backup_path": str(rollback)}), encoding="utf-8")

        removed = self.prune()

        self.assertEqual(removed, {"stages": 1, "work_files": 1, "packages": 2, "snapshots": 2, "app_backups": 2})
        self.assertFalse(old_stage.exists())
        self.assertTrue(fresh_stage.exists(), "a package prepared minutes ago may still be installed")
        self.assertFalse(old_helper.exists())
        self.assertTrue(fresh_ack.exists())
        self.assertEqual([s.exists() for s in snapshots], [True, True, True, False, False])
        self.assertTrue(all(not p.exists() for p in old_pkgs))
        self.assertTrue(newer.exists(), "a newer package stays installable")
        self.assertTrue(rollback.exists(), "the rollback bundle is kept")
        self.assertTrue(backups[0].exists(), "the newest other backup is kept")
        self.assertFalse(backups[1].exists() or backups[3].exists())
        self.assertTrue((self.apps / "KINO.app").exists())

    def test_nothing_is_touched_while_an_update_awaits_confirmation(self):
        stage = touch(self.state / "stage-aaaa", 3600, directory=True)
        backups = [touch(self.apps / f".KINO.backup-{n:024x}.app", n * 86400, directory=True) for n in range(1, 4)]
        (self.state / "pending.json").write_text("{}", encoding="utf-8")
        self.assertEqual(sum(self.prune().values()), 0)
        self.assertTrue(stage.exists() and all(b.exists() for b in backups))

    def test_cleanup_waits_for_the_update_confirmation_then_runs(self):
        import threading
        stage = touch(self.state / "stage-aaaa", 3600, directory=True)
        os.utime(stage, (time.time() - 3600, time.time() - 3600))
        pending = self.state / "pending.json"
        pending.write_text("{}", encoding="utf-8")
        threading.Timer(0.3, pending.unlink).start()  # the helper confirms the new window
        removed = kino_update.prune_installed_update_leftovers(
            app_path=self.apps / "KINO.app", state_dir=self.state, wait_sec=5, poll_sec=0.05)
        self.assertEqual(removed["stages"], 1)
        self.assertFalse(stage.exists())

    def test_only_the_installed_app_runs_the_startup_cleanup(self):
        self.assertNotEqual(kino_update.manager.app_path, Path("/Applications/KINO.app"))
        self.assertEqual(kino_update.prune_installed_update_leftovers(), {})


if __name__ == "__main__":
    unittest.main()
