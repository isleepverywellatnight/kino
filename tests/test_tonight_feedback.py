"""Public Ce soir API behavior with an isolated local database."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import kino_db


def request(path, body):
    handler = object.__new__(app.RequestHandler)
    handler.path = path
    handler._request_allowed = Mock(return_value=True)
    handler.read_json_body = Mock(return_value=body)
    handler.send_json = Mock()
    handler.do_POST()
    return handler.send_json.call_args.args[0]


class TonightFeedbackAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_patch = patch.object(kino_db, "DB_PATH", Path(self.temp.name) / "kino.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        kino_db.init_db()
        for media_id in ("tt1000001", "tt1000002"):
            kino_db.db_toggle_watchlist({"id": media_id, "name": media_id, "type": "movie"})
        cache_patch = patch.object(app, "read_tonight_metadata", return_value={"runtime": "80 min", "genres": ["Comedy"]})
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        fetch_patch = patch.object(app, "fetch_tonight_metadata", return_value={})
        fetch_patch.start()
        self.addCleanup(fetch_patch.stop)
        downloads_patch = patch.object(app.offline_library.library, "list", return_value={})
        downloads_patch.start()
        self.addCleanup(downloads_patch.stop)

    def test_pas_ce_soir_hides_title_and_undo_restores_it(self):
        before = request("/api/tonight", {"minutes": 90, "mood": "discovery"})
        self.assertEqual(len(before["choices"]), 2)
        feedback = request("/api/tonight/feedback", {"id": "tt1000001", "action": "defer"})
        self.assertTrue(feedback["undo_token"])
        hidden = request("/api/tonight", {"minutes": 90, "mood": "discovery"})
        self.assertNotIn("tt1000001", [choice["item"]["id"] for choice in hidden["choices"]])
        request("/api/tonight/undo", {"id": "tt1000001", "undo_token": feedback["undo_token"]})
        restored = request("/api/tonight", {"minutes": 90, "mood": "discovery"})
        self.assertIn("tt1000001", [choice["item"]["id"] for choice in restored["choices"]])

    def test_durable_rejection_survives_new_requests_until_restored_or_reset(self):
        rejected = request("/api/tonight/feedback", {"id": "tt1000001", "action": "exclude"})
        self.assertTrue(rejected["undo_token"])
        self.assertNotIn("tt1000001", [choice["item"]["id"] for choice in
                          request("/api/tonight", {"minutes": 90, "mood": "discovery"})["choices"]])
        request("/api/tonight/feedback", {"id": "tt1000001", "action": "restore"})
        self.assertIn("tt1000001", [choice["item"]["id"] for choice in
                         request("/api/tonight", {"minutes": 90, "mood": "discovery"})["choices"]])
        request("/api/tonight/feedback", {"id": "tt1000001", "action": "exclude"})
        request("/api/tonight/reset", {})
        self.assertIn("tt1000001", [choice["item"]["id"] for choice in
                         request("/api/tonight", {"minutes": 90, "mood": "discovery"})["choices"]])

    def test_deja_vu_updates_history_and_undo_restores_partial_progress(self):
        kino_db.db_record_history({"id": "tt1000001", "name": "Partiellement vu", "type": "movie",
                                   "position": 600, "duration": 7200})
        before = next(entry for entry in kino_db.db_get_history() if entry["id"] == "tt1000001")
        feedback = request("/api/tonight/feedback", {"id": "tt1000001", "action": "seen"})
        self.assertTrue(feedback["undo_token"])
        watched = next(entry for entry in kino_db.db_get_history() if entry["id"] == "tt1000001")
        self.assertTrue(watched["completed"])
        self.assertNotIn("tt1000001", [choice["item"]["id"] for choice in
                          request("/api/tonight", {"minutes": 90, "mood": "discovery"})["choices"]])
        request("/api/tonight/undo", {"id": "tt1000001", "undo_token": feedback["undo_token"]})
        after = next(entry for entry in kino_db.db_get_history() if entry["id"] == "tt1000001")
        self.assertEqual(after, before)

    def test_repeated_search_rotates_when_watchlist_has_spare_titles(self):
        for media_id in ("tt1000003", "tt1000004"):
            kino_db.db_toggle_watchlist({"id": media_id, "name": media_id, "type": "movie"})
        first = request("/api/tonight", {"minutes": 90, "mood": "discovery"})
        second = request("/api/tonight", {"minutes": 90, "mood": "discovery"})
        first_ids = {choice["item"]["id"] for choice in first["choices"]}
        self.assertEqual(len(first_ids), 3)
        self.assertNotIn(second["choices"][0]["item"]["id"], first_ids)
        self.assertIn("Encore jamais proposé", second["choices"][0]["reasons"])

    def test_offline_request_uses_only_ready_media_without_metadata_network_fetch(self):
        downloads = {"local": {"id": "local", "available": True,
                               "media": {"id": "tt1000002", "type": "movie"}}}
        with patch.object(app, "read_tonight_metadata", return_value={}), \
             patch.object(app, "fetch_tonight_metadata") as fetch, \
             patch.object(app.offline_library.library, "list", return_value=downloads):
            result = request("/api/tonight", {"minutes": 90, "mood": "discovery", "offline_only": True})
        self.assertEqual([choice["item"]["id"] for choice in result["choices"]], ["tt1000002"])
        self.assertEqual(result["choices"][0]["offline_id"], "local")
        fetch.assert_not_called()

    def test_empty_offline_request_explains_that_no_file_is_ready(self):
        result = request("/api/tonight", {"minutes": 90, "mood": "discovery", "offline_only": True})
        self.assertEqual(result["choices"], [])
        self.assertEqual(result["empty_reason"], "no_offline")

    def test_feedback_accepts_a_valid_watchlist_item_without_imdb_id(self):
        kino_db.db_toggle_watchlist({"id": "anime:123", "name": "Anime de ma liste", "type": "anime"})
        result = request("/api/tonight/feedback", {"id": "anime:123", "action": "defer"})
        self.assertTrue(result["undo_token"])

    def test_series_episode_can_be_marked_whole_series_seen_and_stays_seen_after_reset(self):
        kino_db.db_toggle_watchlist({"id": "series:7", "name": "Série", "type": "series"})
        kino_db.db_record_history({"id": "series:7", "name": "Série", "type": "series",
                                   "season": 1, "episode": 1, "position": 2700, "duration": 2700})
        feedback = request("/api/tonight/feedback", {"id": "series:7", "action": "seen"})
        self.assertTrue(feedback["undo_token"])
        request("/api/tonight/reset", {})
        result = request("/api/tonight", {"minutes": 90, "mood": "discovery"})
        self.assertNotIn("series:7", [choice["item"]["id"] for choice in result["choices"]])
