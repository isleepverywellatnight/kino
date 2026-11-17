"""After a season finale, the recorded next episode is S+1E1, even after a restart."""

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import config
import kino_db
import meta_engine

VIDEOS = [{"season": 1, "episode": n} for n in range(1, 9)] + [{"season": 2, "episode": 1}, {"season": 2, "episode": 2}]


class NextEpisodeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(kino_db, "DB_PATH", Path(temp.name) / "kino.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        kino_db.init_db()
        saved = dict(config.MEM_CACHE)
        config.MEM_CACHE.clear()
        self.addCleanup(lambda: (config.MEM_CACHE.clear(), config.MEM_CACHE.update(saved)))

    def test_finale_after_restart_fetches_the_episode_list_once(self):
        calls = []

        def fake_http(url, **kwargs):
            calls.append((url, kwargs.get("retries")))
            return {"meta": {"videos": VIDEOS}}

        with patch.object(meta_engine, "http_json", fake_http):
            self.assertEqual(meta_engine._compute_next_series_episode("tt0903747", 1, 8, allow_network=True), (2, 1))
            self.assertEqual(meta_engine._compute_next_series_episode("tt0903747", 1, 8, allow_network=True), (2, 1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 0, "no retry while a history entry is saved")

    def test_unfinished_episode_never_waits_for_the_network(self):
        with patch.object(meta_engine, "http_json", side_effect=AssertionError("network used")):
            self.assertEqual(meta_engine._compute_next_series_episode("tt0903747", 1, 3), (1, 4))

    def test_disk_cache_is_used_before_the_network(self):
        kino_db.db_cache_set("meta_fr_v2:series:tt0903747", {"videos": VIDEOS}, ttl_sec=3600)
        with patch.object(meta_engine, "http_json", side_effect=AssertionError("network used")):
            self.assertEqual(meta_engine._compute_next_series_episode("tt0903747", 1, 8, allow_network=True), (2, 1))

    def test_recorded_history_stores_the_real_next_episode(self):
        with patch.object(meta_engine, "http_json", return_value={"meta": {"videos": VIDEOS}}):
            config.record_history({"id": "tt0903747", "type": "series", "season": 1, "episode": 8,
                                   "progress_pct": 97, "name": "Série"})
        entry = next(item for item in kino_db.db_get_history() if item["id"] == "tt0903747")
        self.assertEqual((entry["next_season"], entry["next_episode"]), (2, 1))


    def test_last_episode_of_the_series_marks_it_finished(self):
        kino_db.db_cache_set("meta_fr_v2:series:tt0903747", {"videos": VIDEOS}, ttl_sec=3600)
        self.assertEqual(meta_engine.next_series_episode("tt0903747", 2, 2), (2, 3, True))
        self.assertEqual(meta_engine.next_series_episode("tt0903747", 1, 8), (2, 1, False))
        self.assertEqual(meta_engine.next_series_episode("tt9999999", 1, 8), (1, 9, False), "unknown list: not finished")
        config.record_history({"id": "tt0903747", "type": "series", "season": 2, "episode": 2, "progress_pct": 95})
        entry = next(item for item in kino_db.db_get_history() if item["id"] == "tt0903747")
        self.assertIs(entry["series_finished"], True)
        config.record_history({"id": "tt0903747", "type": "series", "season": 2, "episode": 2, "progress_pct": 40})
        entry = next(item for item in kino_db.db_get_history() if item["id"] == "tt0903747")
        self.assertIs(entry["series_finished"], False, "an episode in progress is not the end")


    def test_in_app_player_payload_marks_the_series_finished(self):
        # The in-app player sends position and duration only.
        with patch.object(meta_engine, "http_json", return_value={"meta": {"videos": VIDEOS}}) as fetch:
            config.record_history({"id": "tt0903747", "type": "series", "season": 2, "episode": 2,
                                   "position": 2580, "duration": 2700})
        entry = next(item for item in kino_db.db_get_history() if item["id"] == "tt0903747")
        self.assertIs(entry["series_finished"], True)
        self.assertEqual(fetch.call_count, 1, "the end of an episode may fetch the episode list")
        with patch.object(meta_engine, "http_json", side_effect=AssertionError("network used")):
            config.record_history({"id": "tt0903747", "type": "series", "season": 2, "episode": 2,
                                   "position": 600, "duration": 2700})

    def test_finished_series_return_when_a_new_season_is_listed(self):
        kino_db.db_cache_set("meta_fr_v2:series:tt0903747", {"videos": VIDEOS}, ttl_sec=3600)
        config.record_history({"id": "tt0903747", "type": "series", "season": 2, "episode": 2, "progress_pct": 99})
        before = next(item for item in kino_db.db_get_history() if item["id"] == "tt0903747")
        self.assertIs(before["series_finished"], True)
        self.assertEqual(config.refresh_finished_series(), 0, "nothing new: stays finished")
        config.MEM_CACHE.clear()
        kino_db.db_cache_set("meta_fr_v2:series:tt0903747",
                             {"videos": VIDEOS + [{"season": 3, "episode": 1}]}, ttl_sec=3600)
        self.assertEqual(config.refresh_finished_series(), 1)
        after = next(item for item in kino_db.db_get_history() if item["id"] == "tt0903747")
        self.assertIs(after["series_finished"], False)
        self.assertEqual((after["next_season"], after["next_episode"]), (3, 1))
        self.assertEqual(after["last_watched"], before["last_watched"], "the entry keeps its place")


    def test_absolute_episode_numbers_skip_specials(self):
        videos = ([{"season": 0, "episode": n} for n in (1, 2)] + [{"season": 1, "episode": n} for n in range(1, 25)]
                  + [{"season": 2, "episode": n} for n in range(1, 24)])
        kino_db.db_cache_set("meta_fr_v2:series:tt12343534", {"videos": videos}, ttl_sec=3600)
        self.assertEqual(meta_engine.absolute_episode_number("tt12343534", 2, 1), 25)
        self.assertEqual(meta_engine.absolute_episode_number("tt12343534", 1, 24), 24)
        self.assertIsNone(meta_engine.absolute_episode_number("tt12343534", 3, 1))
        self.assertIsNone(meta_engine.absolute_episode_number("tt0000000", 2, 1))


class MemoryCacheBoundTests(unittest.TestCase):
    def test_oldest_entries_are_dropped(self):
        saved = dict(config.MEM_CACHE)
        self.addCleanup(lambda: (config.MEM_CACHE.clear(), config.MEM_CACHE.update(saved)))
        config.MEM_CACHE.clear()
        start = time.time()
        for n in range(config.MEM_CACHE_MAX_ENTRIES + 1):
            config._remember(f"k{n}", n, start + n)
        self.assertLessEqual(len(config.MEM_CACHE), config.MEM_CACHE_MAX_ENTRIES)
        self.assertNotIn("k0", config.MEM_CACHE)
        self.assertIn(f"k{config.MEM_CACHE_MAX_ENTRIES}", config.MEM_CACHE)


if __name__ == "__main__":
    unittest.main()
