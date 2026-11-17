"""A whole season for offline: the episodes wait their turn, one download at a
time; each asks for its Debrid link when its turn comes; the queue goes on
after a missing source and after KINO restarts."""
import atexit
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

_TEST_HOME = tempfile.TemporaryDirectory()
atexit.register(_TEST_HOME.cleanup)
with patch.object(Path, "home", return_value=Path(_TEST_HOME.name)):
    import app
    import kino_db
    import offline_library

from test_offline_library import SourceHandler, VIDEO

SHOW = {"name": "Série test", "year": "2024", "poster": ""}


class SeasonQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        db = patch.object(kino_db, "DB_PATH", self.root / "kino.db")
        db.start()
        self.addCleanup(db.stop)
        kino_db.init_db()
        self.source = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
        threading.Thread(target=self.source.serve_forever, daemon=True).start()
        self.addCleanup(self.source.server_close)
        self.addCleanup(self.source.shutdown)
        self.base = f"http://127.0.0.1:{self.source.server_port}"
        self.asked = []
        self.library = offline_library.OfflineLibrary()
        self.library.resolve_episode = self.resolve

    def resolve(self, imdb_id, season, episode, media):
        self.asked.append((imdb_id, season, episode, media.get("name")))
        if episode == 99:
            raise RuntimeError("Aucun flux trouvé")
        return f"{self.base}/slow.mp4"  # slowed down by the test server, so the order shows

    def queue(self, episode, imdb_id="tt1"):
        return self.library.queue_episode(imdb_id, 1, episode, self.root / "downloads", SHOW)

    def statuses(self):
        return {item["media"]["episode"]: item["status"] for item in self.library.list().values()}

    def wait_until(self, test, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if test():
                return
            time.sleep(0.03)
        self.fail(f"timed out: {self.statuses()}")

    def test_episodes_download_one_after_another(self):
        ids = [self.queue(episode)[0] for episode in (1, 2, 3)]
        statuses = self.statuses()
        self.assertIn(statuses[1], ("queued", "downloading"))
        self.assertEqual((statuses[2], statuses[3]), ("waiting", "waiting"), "one download at a time")
        self.wait_until(lambda: all(status == "completed" for status in self.statuses().values()))
        self.assertEqual([entry[2] for entry in self.asked], [1, 2, 3], "in episode order, each link asked at its turn")
        self.assertEqual(self.asked[0], ("tt1", 1, 1, "Série test"))
        for download_id in ids:
            item = self.library.list()[download_id]
            self.assertTrue(item["available"])
            self.assertEqual(Path(self.library.media_path(download_id)).read_bytes(), VIDEO)
        self.assertEqual(self.library.list()[ids[1]]["filename"], "Série test S01E02.mkv")

    def test_the_same_episode_is_queued_once_whatever_its_file_name(self):
        first, already = self.queue(1)
        self.assertFalse(already)
        self.assertEqual(self.queue(1), (first, True))
        self.wait_until(lambda: self.statuses().get(1) == "completed")
        self.assertEqual(self.queue(1), (first, True), "a kept episode is not downloaded again")

    def test_a_missing_source_does_not_stop_the_queue(self):
        missing, _ = self.queue(99)
        self.queue(2)
        self.wait_until(lambda: self.statuses().get(2) == "completed")
        failed = self.library.list()[missing]
        self.assertEqual((failed["status"], failed["error_code"]), ("failed", "source_unavailable"))

    def test_a_paused_episode_waits_and_others_go_on(self):
        self.queue(1)
        second, _ = self.queue(2)
        self.library.pause(second)
        third, _ = self.queue(3)
        self.wait_until(lambda: self.statuses().get(3) == "completed" and self.statuses().get(1) == "completed")
        self.assertEqual(self.library.list()[second]["status"], "paused")
        self.library.resume(second)
        self.wait_until(lambda: self.statuses().get(2) == "completed")

    def test_the_queue_starts_again_when_kino_starts(self):
        with patch.object(self.library, "start_next"):
            waiting, _ = self.queue(1)
        self.assertEqual(self.statuses()[1], "waiting")
        self.library.reconcile()
        self.wait_until(lambda: self.statuses().get(1) == "completed")

    def test_wrong_episodes_are_refused(self):
        for imdb_id, season, episode in (("kitsu:1", 1, 1), ("tt1", 0, 1), ("tt1", 1, "x")):
            with self.assertRaises(offline_library.OfflineError):
                self.library.queue_episode(imdb_id, season, episode, self.root / "downloads", SHOW)
        self.assertEqual(self.library.list(), {})


class RouteTests(unittest.TestCase):
    def test_the_route_queues_the_episodes_and_counts_them(self):
        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/download-season"
        handler.headers = {}
        handler._request_allowed = Mock(return_value=True)
        handler.read_json_body = Mock(return_value={"imdb_id": "tt1", "season": 2, "episodes": [1, 2, 3], "name": "Série"})
        handler.send_json = Mock()
        answers = iter([("a", False), ("b", True), ("c", False)])
        with patch.object(app, "load_config", return_value={"download_dir": "/tmp/kino-test"}), \
                patch.object(app.offline_library.library, "queue_episode", side_effect=lambda *a: next(answers)) as queue:
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[0], {"queued": 2, "duplicates": 1})
        self.assertEqual(queue.call_args_list[0].args, ("tt1", 2, 1, "/tmp/kino-test", {"name": "Série"}))

    def test_the_link_is_found_like_the_next_episode_without_touching_the_history(self):
        with patch.object(app, "resolve_auto_stream_episode", return_value="https://debrid.example/ep.mkv") as resolve:
            self.assertEqual(app.resolve_offline_episode("tt1", 1, 2, {"name": "Série", "year": "2024"}), "https://debrid.example/ep.mkv")
        resolve.assert_called_once_with({"imdb_id": "tt1", "season": 1, "episode": 2, "name": "Série", "year": "2024"}, update_history=False)
        self.assertIs(app.offline_library.library.resolve_episode, app.resolve_offline_episode)


if __name__ == "__main__":
    unittest.main()
