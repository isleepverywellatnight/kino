import tempfile
from contextlib import closing
import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch

import app
import config
import kino_db
import spoiler_protection


class SpoilerProtectionTests(unittest.TestCase):
    def test_reveal_is_scoped_and_does_not_touch_watch_history(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(kino_db, "DB_PATH", Path(folder) / "kino.db"):
            kino_db.init_db()
            kino_db.db_record_history({"id": "tt12345", "name": "Fixture", "type": "series",
                                       "season": 1, "episode": 1, "position": 1200, "duration": 1200})
            history = kino_db.db_get_history()
            self.assertFalse(spoiler_protection.get()["enabled"])
            spoiler_protection.update("global", enabled=True)
            spoiler_protection.update("series", identifier="tt12345", enabled=False)
            spoiler_protection.update("series", identifier="tt99999", enabled=True)
            spoiler_protection.update("reveal", identifier="tt12345", season=1, episode=2)
            state = spoiler_protection.get()
            self.assertEqual(state["overrides"], {"tt12345": False, "tt99999": True})
            self.assertEqual(state["revealed"], {"tt12345": ["S01E02"]})
            self.assertEqual(kino_db.db_get_history(), history)
            spoiler_protection.update("hide", identifier="tt12345", season=1, episode=2)
            spoiler_protection.update("series", identifier="tt12345", enabled=None)
            self.assertNotIn("tt12345", spoiler_protection.get()["overrides"])
            self.assertNotIn("tt12345", spoiler_protection.get()["revealed"])

    def test_rejects_invalid_actions_identifiers_and_episodes(self):
        for kwargs in [
            {"action": "global", "enabled": "on"},
            {"action": "series", "identifier": "https://example.test", "enabled": True},
            {"action": "reveal", "identifier": "tt123", "season": 1.5, "episode": 2},
            {"action": "reveal", "identifier": "tt123", "season": 1, "episode": 0},
            {"action": "clear-all"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                spoiler_protection.update(**kwargs)

    def test_http_round_trip_uses_isolated_storage(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(kino_db, "DB_PATH", Path(folder) / "kino.db"):
            kino_db.init_db()
            write = object.__new__(app.RequestHandler)
            write.path = "/api/spoiler-protection"
            write._request_allowed = Mock(return_value=True)
            write.read_json_body = Mock(return_value={"action": "global", "enabled": True})
            write.send_json = Mock()
            write.do_POST()
            self.assertTrue(write.send_json.call_args.args[0]["enabled"])
            read = object.__new__(app.RequestHandler)
            read.path = "/api/spoiler-protection"
            read._request_allowed = Mock(return_value=True)
            read.send_json = Mock()
            read.do_GET()
            self.assertTrue(read.send_json.call_args.args[0]["enabled"])

    def test_playback_mark_can_be_unmarked_even_with_legacy_episode_code(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(kino_db, "DB_PATH", Path(folder) / "kino.db"), \
             patch.object(config, "CONFIG_FILE", Path(folder) / "config.json"):
            kino_db.init_db()
            kino_db.db_record_history({"id": "tt12345", "name": "Fixture", "type": "series",
                                       "season": 1, "episode": 2, "position": 1200, "duration": 1200})
            with closing(kino_db.get_connection()) as conn, conn:
                row = conn.execute("SELECT data_json FROM watch_history WHERE id = ?", ("tt12345",)).fetchone()
                old = json.loads(row["data_json"])
                old["watched_episodes"] = ["s1e2"]
                old["ep_positions"] = {"s1e2": old["ep_positions"]["S01E02"]}
                conn.execute("UPDATE watch_history SET data_json = ? WHERE id = ?", (json.dumps(old), "tt12345"))
            config.toggle_watched_status({"id": "tt12345", "name": "Fixture", "type": "series",
                                          "season": 1, "episode": 2})
            updated = kino_db.db_get_history()[0]
            self.assertEqual(updated["watched_episodes"], [])
            self.assertEqual(updated["ep_positions"], {})


if __name__ == "__main__":
    unittest.main()
