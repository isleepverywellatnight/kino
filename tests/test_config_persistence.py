"""Settings written through save_config must survive the next load_config."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
import kino_db
import meta_engine


def _letterboxd_list(list_id, items=2):
    return {
        "id": list_id, "title": "Films de chevet", "description": "", "url": f"https://letterboxd.com/u/list/{list_id}/",
        "items": [{"id": f"tt{n:07d}", "name": f"Film {n}", "type": "movie", "year": "2016",
                   "poster": "https://example.test/p.jpg", "imdbRating": "7.5", "user_rating": "4"}
                  for n in range(items)],
        "count": items, "updated_at": 1794000000,
    }


class ConfigPersistenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = self.root / ".rd_cinehub_config.json"
        for patcher in (patch.object(Path, "home", return_value=self.root),
                        patch.object(config, "CONFIG_FILE", self.settings),
                        patch.object(kino_db, "DB_PATH", self.root / "kino.db")):
            patcher.start()
            self.addCleanup(patcher.stop)
        kino_db.init_db()

    def test_letterboxd_user_lists_and_window_are_kept_between_launches(self):
        bounds = {"x": -1440, "y": 25, "width": 1280, "height": 820}
        config.save_config({"letterboxd_user": "@cinephile_42", "custom_lists": [_letterboxd_list("u-list-a")],
                            "window_bounds": bounds})
        loaded = config.load_config()
        self.assertEqual(loaded["letterboxd_user"], "cinephile_42")
        self.assertEqual([item["id"] for item in loaded["custom_lists"]], ["u-list-a"])
        self.assertEqual(loaded["custom_lists"][0]["items"][1]["name"], "Film 1")
        self.assertEqual(loaded["window_bounds"], bounds)
        on_disk = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertNotIn("watchlist", on_disk)
        self.assertNotIn("history", on_disk)

    def test_deleting_the_last_custom_list_is_saved(self):
        config.save_config({"custom_lists": [_letterboxd_list("u-list-a")]})
        with patch.object(meta_engine, "load_config", config.load_config), \
             patch.object(meta_engine, "save_config", config.save_config):
            self.assertEqual(meta_engine.delete_letterboxd_custom_list("u-list-a"), [])
        self.assertEqual(config.load_config()["custom_lists"], [])

    def test_invalid_values_are_rejected_and_lists_are_bounded(self):
        cleaned = config.sanitize_config_payload({
            "letterboxd_user": "bad user/../x",
            "window_bounds": {"x": 0, "y": 0, "width": 10, "height": 10},
            "custom_lists": [_letterboxd_list(f"l{n}", items=150) for n in range(25)]
                            + [{"id": "insecure", "url": "http://letterboxd.com/x/"}, "not a list"],
        })
        self.assertNotIn("letterboxd_user", cleaned)
        self.assertNotIn("window_bounds", cleaned)
        self.assertEqual(len(cleaned["custom_lists"]), config.MAX_CUSTOM_LISTS)
        self.assertEqual(cleaned["custom_lists"][0]["count"], config.MAX_CUSTOM_LIST_ITEMS)
        self.assertNotIn("insecure", [item["id"] for item in cleaned["custom_lists"]])
        self.assertEqual(config.sanitize_config_payload({"letterboxd_user": ""}), {"letterboxd_user": ""})


if __name__ == "__main__":
    unittest.main()
