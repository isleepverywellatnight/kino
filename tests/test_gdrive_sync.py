"""Google Drive sync between two devices, with isolated databases and a fake KINO Drive folder.

Run the suite with a temporary HOME: these tests never use the real database
or the real kino_sync.json, but importing kino_db starts a startup sync.
"""

import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import config
import kino_db


def old_format_file(history=(), watchlist=()):
    """A kino_sync.json written by KINO 1.1.8 or older (no deletion markers)."""
    return {
        "version": 1,
        "updated_at": 100.0,
        "device": "Windows",
        "config": {},
        "watchlist": list(watchlist),
        "history": list(history),
    }


class DriveSyncDeletionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.drive = self.root / "GoogleDrive" / "My Drive" / "KINO"
        self.drive.mkdir(parents=True)
        drive_patch = patch.object(kino_db, "find_gdrive_sync_dir", return_value=self.drive)
        drive_patch.start()
        self.addCleanup(drive_patch.stop)
        for name in ("mac", "windows"):
            with self.device(name):
                kino_db.init_db()

    @contextmanager
    def device(self, name):
        with patch.object(kino_db, "DB_PATH", self.root / f"{name}.db"):
            yield

    def sync(self, name):
        with self.device(name):
            result = kino_db.db_sync_gdrive()
        self.assertEqual(result["status"], "synced", result)
        return result

    def record(self, name, item_id):
        with self.device(name):
            kino_db.db_record_history({"id": item_id, "name": item_id, "type": "movie",
                                       "position": 600, "duration": 6000})

    def remove(self, name, item_id):
        with self.device(name):
            config.remove_history(item_id)

    def toggle(self, name, item_id):
        with self.device(name):
            kino_db.db_toggle_watchlist({"id": item_id, "name": item_id, "type": "movie"})

    def history_ids(self, name):
        with self.device(name):
            return {entry["id"] for entry in kino_db.db_get_history()}

    def watchlist_ids(self, name):
        with self.device(name):
            return {item["id"] for item in kino_db.db_get_watchlist()}

    def sync_file(self):
        return json.loads((self.drive / "kino_sync.json").read_text(encoding="utf-8"))

    def write_sync_file(self, data):
        (self.drive / "kino_sync.json").write_text(json.dumps(data), encoding="utf-8")

    def test_history_removed_locally_stays_removed_after_launch_sync(self):
        self.record("mac", "tt0000001")
        self.record("mac", "tt0000002")
        self.sync("mac")

        self.remove("mac", "tt0000001")
        self.sync("mac")  # next launch

        self.assertEqual(self.history_ids("mac"), {"tt0000002"})
        self.assertEqual({entry["id"] for entry in self.sync_file()["history"]}, {"tt0000002"})

    def test_history_removal_reaches_other_device_without_losing_its_additions(self):
        self.record("mac", "tt0000001")
        self.record("mac", "tt0000002")
        self.sync("mac")
        self.sync("windows")
        self.assertEqual(self.history_ids("windows"), {"tt0000001", "tt0000002"})

        self.record("windows", "tt0000003")  # not synced yet
        self.remove("mac", "tt0000001")
        self.sync("mac")
        self.sync("windows")
        self.sync("mac")

        self.assertEqual(self.history_ids("windows"), {"tt0000002", "tt0000003"})
        self.assertEqual(self.history_ids("mac"), {"tt0000002", "tt0000003"})
        self.assertEqual({entry["id"] for entry in self.sync_file()["history"]}, {"tt0000002", "tt0000003"})

    def test_rewatch_on_other_device_after_removal_comes_back(self):
        self.record("mac", "tt0000001")
        self.sync("mac")
        self.sync("windows")

        self.remove("mac", "tt0000001")
        self.sync("mac")
        self.record("windows", "tt0000001")  # watched again after the removal
        self.sync("windows")
        self.sync("mac")

        self.assertEqual(self.history_ids("windows"), {"tt0000001"})
        self.assertEqual(self.history_ids("mac"), {"tt0000001"})

    def test_clear_all_history_reaches_other_device(self):
        self.record("mac", "tt0000001")
        self.record("mac", "tt0000002")
        self.sync("mac")
        self.sync("windows")

        self.remove("mac", "__all__")
        self.sync("mac")
        self.sync("windows")

        self.assertEqual(self.history_ids("mac"), set())
        self.assertEqual(self.history_ids("windows"), set())

    def test_watchlist_removal_reaches_other_device_without_losing_its_additions(self):
        self.toggle("mac", "tt0000005")
        self.toggle("mac", "tt0000006")
        self.sync("mac")
        self.sync("windows")

        self.toggle("windows", "tt0000007")  # added on Windows, not synced yet
        self.toggle("mac", "tt0000005")  # removed on the Mac
        self.sync("mac")
        self.sync("windows")
        self.sync("mac")

        self.assertEqual(self.watchlist_ids("mac"), {"tt0000006", "tt0000007"})
        self.assertEqual(self.watchlist_ids("windows"), {"tt0000006", "tt0000007"})

    def test_old_format_sync_file_is_merged_but_cannot_restore_removed_items(self):
        old = old_format_file(
            history=[{"id": "tt0000008", "data": {"id": "tt0000008", "name": "Ancien"}, "last_watched": 100.0}],
            watchlist=[{"id": "tt0000009", "name": "Ancien", "type": "movie", "added_at": 100.0},
                       {"id": "tt0000010", "name": "Sans date", "type": "movie"}],
        )
        self.write_sync_file(old)
        self.sync("mac")
        self.assertEqual(self.history_ids("mac"), {"tt0000008"})
        self.assertEqual(self.watchlist_ids("mac"), {"tt0000009", "tt0000010"})

        self.remove("mac", "tt0000008")
        self.toggle("mac", "tt0000009")
        self.toggle("mac", "tt0000010")
        self.write_sync_file(old)  # an older KINO re-exports its unchanged state
        self.sync("mac")

        self.assertEqual(self.history_ids("mac"), set())
        self.assertEqual(self.watchlist_ids("mac"), set())
        exported = self.sync_file()
        # Fields read by older versions keep their shape.
        self.assertEqual(exported["history"], [])
        self.assertEqual(exported["watchlist"], [])
        self.assertIsInstance(exported["config"], dict)
        self.assertEqual(set(exported["deleted"]["history"]), {"tt0000008"})
        self.assertEqual(set(exported["deleted"]["watchlist"]), {"tt0000009", "tt0000010"})

    def test_removal_wins_over_entry_stamped_by_a_clock_running_ahead(self):
        ahead = time.time() + 3600
        old = old_format_file(history=[{"id": "tt0000004", "data": {"id": "tt0000004"}, "last_watched": ahead}])
        self.write_sync_file(old)
        self.sync("mac")
        self.assertEqual(self.history_ids("mac"), {"tt0000004"})

        self.remove("mac", "tt0000004")
        self.write_sync_file(old)
        self.sync("mac")

        self.assertEqual(self.history_ids("mac"), set())

    def test_deletion_markers_are_not_shared_as_settings(self):
        self.record("mac", "tt0000001")
        self.remove("mac", "tt0000001")
        self.sync("mac")
        self.assertNotIn(kino_db.SYNC_DELETIONS_KEY, self.sync_file()["config"])

        # An older KINO copies every app_config row into "config"; that copy is ignored.
        self.record("windows", "tt0000002")
        old = old_format_file()
        old["config"] = {kino_db.SYNC_DELETIONS_KEY: {"history": {"tt0000002": time.time() + 3600}}}
        self.write_sync_file(old)
        self.sync("windows")
        self.assertEqual(self.history_ids("windows"), {"tt0000002"})

    def test_unreadable_sync_file_is_left_untouched(self):
        self.record("mac", "tt0000001")
        partial = '{"version": 2, "history": [{"id": "tt0000003"'
        (self.drive / "kino_sync.json").write_text(partial, encoding="utf-8")

        with self.device("mac"):
            result = kino_db.db_sync_gdrive()

        self.assertEqual(result["status"], "error")
        self.assertEqual((self.drive / "kino_sync.json").read_text(encoding="utf-8"), partial)
        self.assertEqual(self.history_ids("mac"), {"tt0000001"})

    def test_legacy_json_migration_does_not_restore_removed_items(self):
        legacy = self.root / "legacy_config.json"
        legacy.write_text(json.dumps({
            "history": [{"id": "tt0000001", "name": "Ancien"}],
            "watchlist": [{"id": "tt0000002", "name": "Ancien", "type": "movie"}],
        }), encoding="utf-8")
        with self.device("mac"):
            kino_db.migrate_from_json(legacy)
        self.assertEqual(self.history_ids("mac"), {"tt0000001"})
        self.assertEqual(self.watchlist_ids("mac"), {"tt0000002"})

        self.remove("mac", "tt0000001")
        self.toggle("mac", "tt0000002")
        with self.device("mac"):
            kino_db.migrate_from_json(legacy)  # next launch

        self.assertEqual(self.history_ids("mac"), set())
        self.assertEqual(self.watchlist_ids("mac"), set())


if __name__ == "__main__":
    unittest.main()
