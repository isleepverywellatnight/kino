"""« Noter en un geste »: the rating route, its checks, the weight of
a rating in the suggestions, and a Drive sync that keeps the newer rating.
Everything uses temporary databases; run with a temporary HOME."""
import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import app
import kino_db
import ratings
import recommender as rec


def request(path, body):
    handler = object.__new__(app.RequestHandler)
    handler.path = path
    handler._request_allowed = Mock(return_value=True)
    handler.read_json_body = Mock(return_value=body)
    handler.send_json = Mock()
    handler.do_POST()
    return handler.send_json.call_args.args[0], handler.send_json.call_args.kwargs.get("status", 200)


def entry(item_id):
    return next(h for h in kino_db.db_get_history() if h["id"] == item_id)


class RatingValueTests(unittest.TestCase):
    def test_letterboxd_values_by_halves(self):
        self.assertEqual([ratings.clean_rating(v) for v in (None, "", 4, "4.5", 5.0, "0.5")], ["", "", "4", "4.5", "5", "0.5"])
        for wrong in (0, 6, 4.3, "beaucoup", True, [4]):
            with self.assertRaises(ValueError, msg=wrong):
                ratings.clean_rating(wrong)
        self.assertEqual(ratings.GESTURES, {"disliked": "1", "liked": "4", "loved": "5"})

    def test_what_a_request_changes(self):
        self.assertEqual(ratings.patch_for({"rating": "5"}, 100),
                         {"user_rating": "5", "rated_at": 100.0, "rating_skipped": False})
        self.assertEqual(ratings.patch_for({"rating": ""}, 100)["user_rating"], "", "a second click takes it back")
        self.assertEqual(ratings.patch_for({"asked": "S2"}, 100), {"rating_asked": "S2"})
        self.assertEqual(ratings.patch_for({"asked": "film"}, 100), {"rating_asked": "film"})
        self.assertEqual(ratings.patch_for({"skip": True}, 100), {"rating_skipped": True})
        self.assertNotIn("rating_skipped", ratings.patch_for({"asked": "film"}, 100))
        for wrong in ({}, {"asked": "S"}, {"asked": "<b>"}, {"rating": 9}, None):
            with self.assertRaises(ValueError):
                ratings.patch_for(wrong, 100)

    def test_the_newer_rating_is_kept(self):
        played_later = {"id": "tt1", "progress_pct": 40}
        rated = {"id": "tt1", "user_rating": "5", "rated_at": 200}
        self.assertEqual(ratings.newer_rating(played_later, rated), {"id": "tt1", "progress_pct": 40, "user_rating": "5", "rated_at": 200})
        self.assertIs(ratings.newer_rating(rated, played_later), rated)
        taken_back = {"id": "tt1", "user_rating": "", "rated_at": 300}
        self.assertEqual(ratings.newer_rating(rated, taken_back)["user_rating"], "")


class RatingRouteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(kino_db, "DB_PATH", Path(temp.name) / "kino.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        kino_db.init_db()
        kino_db.db_record_history({"id": "tt0113277", "name": "Heat", "type": "movie", "position": 9800, "duration": 10000})
        kino_db.db_record_history({"id": "tt0000002", "name": "Autre", "type": "movie", "position": 60, "duration": 6000})

    def test_a_rating_is_saved_without_moving_the_title(self):
        order = [h["id"] for h in kino_db.db_get_history()]
        reply, status = request("/api/rating", {"id": "tt0113277", "rating": "5"})
        self.assertEqual(status, 200)
        self.assertEqual(reply["fields"]["user_rating"], "5")
        heat = entry("tt0113277")
        self.assertEqual((heat["user_rating"], heat["completed"]), ("5", True))
        self.assertGreater(heat["rated_at"], 0)
        self.assertEqual([h["id"] for h in kino_db.db_get_history()], order, "À continuer keeps its order")
        request("/api/rating", {"id": "tt0113277", "rating": ""})
        self.assertEqual(entry("tt0113277")["user_rating"], "")

    def test_asked_and_skipped(self):
        request("/api/rating", {"id": "tt0113277", "asked": "film"})
        request("/api/rating", {"id": "tt0113277", "skip": True})
        heat = entry("tt0113277")
        self.assertEqual((heat["rating_asked"], heat["rating_skipped"]), ("film", True))
        request("/api/rating", {"id": "tt0113277", "rating": "4"})
        self.assertFalse(entry("tt0113277")["rating_skipped"], "a rating brings it back")

    def test_refusals(self):
        self.assertEqual(request("/api/rating", {"id": "tt9999999", "rating": "5"})[1], 404)
        self.assertEqual(request("/api/rating", {"id": "", "rating": "5"})[1], 404)
        self.assertEqual(request("/api/rating", {"id": "tt0113277", "rating": "7"})[1], 400)
        self.assertEqual(request("/api/rating", {"id": "tt0113277"})[1], 400)
        self.assertNotIn("user_rating", entry("tt0113277"))

    def test_a_rating_changes_the_taste(self):
        request("/api/rating", {"id": "tt0113277", "rating": "1"})
        signals = rec.taste_signals(kino_db.db_get_history(), [], [], now=time.time())
        self.assertEqual(signals["tt0113277"], -1.0)


class RatingWeightTests(unittest.TestCase):
    def test_liked_says_more_than_seen(self):
        seen = rec.title_weight({"completed": True})
        self.assertEqual(seen, 0.6)
        self.assertEqual([rec.title_weight(rating=r) for r in (1, 2, 3, 4, 5)], [-1.0, -0.5, 0.2, 0.8, 1.0])
        self.assertGreater(rec.title_weight({"completed": True}, rating=4), seen)
        self.assertLess(rec.title_weight({"completed": True}, rating=3), seen, "a 3 is lukewarm")
        self.assertEqual(rec.title_weight(rating=4.5), 0.9)
        self.assertEqual(rec.title_weight(rating=0.5), -1.0)


class RatingDriveSyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.drive = self.root / "GoogleDrive" / "My Drive" / "KINO"
        self.drive.mkdir(parents=True)
        drive_patch = patch.object(kino_db, "find_gdrive_sync_dir", return_value=self.drive)
        drive_patch.start()
        self.addCleanup(drive_patch.stop)
        for name in ("mac", "other"):
            with self.device(name):
                kino_db.init_db()

    @contextmanager
    def device(self, name):
        with patch.object(kino_db, "DB_PATH", self.root / f"{name}.db"):
            yield

    def sync(self, name):
        with self.device(name):
            self.assertEqual(kino_db.db_sync_gdrive()["status"], "synced")

    def play(self, name, item_id, position):
        time.sleep(0.01)
        with self.device(name):
            kino_db.db_record_history({"id": item_id, "name": item_id, "type": "movie", "position": position, "duration": 6000})

    def rate(self, name, item_id, value):
        time.sleep(0.01)
        with self.device(name):
            self.assertTrue(kino_db.db_patch_history(item_id, ratings.patch_for({"rating": value}, time.time())))

    def on(self, name, item_id):
        with self.device(name):
            return entry(item_id)

    def test_a_rating_reaches_the_mac_that_played_later(self):
        self.play("mac", "tt1", 5900)
        self.sync("mac")
        self.sync("other")
        self.rate("other", "tt1", "5")
        self.play("mac", "tt1", 1200)  # watched again later on the Mac
        self.sync("other")
        self.sync("mac")
        mac = self.on("mac", "tt1")
        self.assertEqual(mac["user_rating"], "5", "the rating from the other Mac")
        self.assertFalse(mac["completed"], "and the Mac's newer playback")

    def test_a_newer_playback_elsewhere_keeps_the_rating(self):
        self.play("mac", "tt2", 5900)
        self.sync("mac")
        self.sync("other")
        self.rate("mac", "tt2", "4")       # rated on the Mac…
        self.play("other", "tt2", 300)     # …then played later elsewhere, without the rating
        self.sync("other")
        self.assertNotIn("user_rating", self.on("other", "tt2"))
        self.sync("mac")
        mac = self.on("mac", "tt2")
        self.assertEqual((mac["user_rating"], mac["completed"]), ("4", False), "the rating stays, the newer playback wins")
        with open(self.drive / "kino_sync.json", encoding="utf-8") as handle:
            shared = {e["id"]: e for e in json.load(handle)["history"]}
        self.assertEqual(shared["tt2"]["data"]["user_rating"], "4", "Drive holds it too")
        self.sync("other")
        self.assertEqual(self.on("other", "tt2")["user_rating"], "4", "and the other Mac gets it back")


if __name__ == "__main__":
    unittest.main()
