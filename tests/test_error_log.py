"""KINO's error log: nothing private written, a few small files,
the same error once a minute, the threads' errors caught, the page's errors
received, and the Diagnostic's line. Run with a temporary HOME."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import diagnostic
import error_log


class RedactTests(unittest.TestCase):
    def test_nothing_private(self):
        home = str(Path.home())
        text = (f"GET https://xyz.download.real-debrid.com/d/ABCDEF123/Film.mkv?token=abc failed for me@example.com "
                f"in {home}/Movies with magnet:?xt=urn:btih:0123456789abcdef&dn=Film key=KZ8sDf73Hf9sk2LmQpW0aZx1VbN4rT6y")
        out = error_log.redact(text, secrets=["SECRETKEY42"])
        self.assertIn("https://xyz.download.real-debrid.com/…", out, "a link keeps its host only")
        for leaked in ("ABCDEF123", "Film.mkv?token", "me@example.com", home, "urn:btih", "KZ8sDf73Hf9sk2LmQpW0aZx1VbN4rT6y"):
            self.assertNotIn(leaked, out, leaked)
        self.assertEqual(error_log.redact("token SECRETKEY42 refused", secrets=["SECRETKEY42"]), "token [clé] refused")


class LogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.now = [1000.0]
        self.log = error_log.ErrorLog(Path(temp.name) / "Logs", clock=lambda: self.now[0])

    def boom(self):
        try:
            {}["missing"]
        except KeyError as error:
            return error

    def test_one_line_per_error_with_where_and_frames(self):
        self.assertTrue(self.log.record("serveur GET /api/x", self.boom()))
        entry = self.log.recent()[0]
        self.assertEqual((entry["where"], entry["kind"], entry["message"]), ("serveur GET /api/x", "KeyError", "'missing'"))
        self.assertTrue(entry["trace"][-1].startswith("test_error_log.py:"), entry["trace"])
        self.assertEqual(error_log.line_for(entry)[-35:], "serveur GET /api/x · KeyError : 'missing'"[-35:])

    def test_the_same_error_once_a_minute(self):
        self.log.record("page films", message="x is undefined")
        self.now[0] += 30
        self.assertFalse(self.log.record("page films", message="x is undefined"))
        self.now[0] += 31
        self.assertTrue(self.log.record("page films", message="x is undefined"))
        self.assertEqual(len(self.log.recent()), 2)

    def test_a_few_small_files(self):
        with patch.object(error_log, "MAX_BYTES", 2000):
            for index in range(200):
                self.now[0] += 61
                self.log.record("tâche", message=f"erreur {index}")
        files = sorted(p.name for p in self.log.dir.iterdir())
        self.assertEqual(files, ["errors.log", "errors.log.1", "errors.log.2"])
        self.assertLess(sum(p.stat().st_size for p in self.log.dir.iterdir()), 3 * 2000 + 600)
        self.assertEqual(self.log.recent(limit=1)[0]["message"], "erreur 199", "the newest first")

    def test_recent_and_since(self):
        for index in range(3):
            self.now[0] += 100
            self.log.record("tâche", message=f"e{index}")
        self.assertEqual([e["message"] for e in self.log.recent()], ["e2", "e1", "e0"])
        self.assertEqual(self.log.count_since(1150), 2)

    def test_a_threads_error_is_written(self):
        previous = threading.excepthook
        self.addCleanup(setattr, threading, "excepthook", previous)
        with patch.object(threading, "excepthook", lambda args: None):
            self.log.install()
            worker = threading.Thread(target=lambda: 1 / 0, name="sync")
            worker.start()
            worker.join()
        self.addCleanup(setattr, __import__("sys"), "excepthook", __import__("sys").__excepthook__)
        entry = self.log.recent()[0]
        self.assertEqual((entry["where"], entry["kind"]), ("tâche sync", "ZeroDivisionError"))

    def test_never_raises(self):
        blocker = Path(tempfile.mkdtemp()) / "a-file"
        blocker.write_text("x", encoding="utf-8")
        broken = error_log.ErrorLog(blocker / "not-a-folder")
        self.assertFalse(broken.record("x", message="y"))


class ServerTests(unittest.TestCase):
    def test_the_page_errors_reach_the_log(self):
        import app
        recorded = []
        handler = object.__new__(app.RequestHandler)
        handler.send_json = lambda data, status=200: recorded.append(("answer", data))
        with patch.object(app.error_log.log, "record", lambda where, **k: recorded.append((where, k))):
            handler.post_client_error(None, {}, {"kind": "Promesse rejetée", "message": "x is undefined", "source": "app-home.js:12",
                                                 "stack": "at renderHome", "tab": "movies"})
        where, fields = recorded[0]
        self.assertEqual((where, fields["kind"], fields["message"]), ("page movies", "Promesse rejetée", "x is undefined"))
        self.assertIn("app-home.js:12", fields["stack"])
        self.assertEqual(recorded[1], ("answer", {"ok": True}))

    def test_a_failing_route_is_written_down(self):
        import app
        source = Path(app.__file__).read_text(encoding="utf-8")
        self.assertIn('error_log.log.record(f"serveur GET {parsed.path}", e)', source)
        self.assertIn('error_log.log.record(f"serveur POST {parsed.path}", e)', source)


class HistoryTests(unittest.TestCase):
    def test_a_strange_progress_does_not_stop_the_history(self):
        # Found by the error log on its first run: a duration sent as an object crashed the route.
        import config
        self.assertEqual(config._clean_progress({"id": "tt1", "position": "pas un nombre", "duration": {"x": 1}, "progress_pct": float("nan")}),
                         {"id": "tt1"})
        self.assertEqual(config._clean_progress({"id": "tt1", "position": "120", "duration": 6000}), {"id": "tt1", "position": 120.0, "duration": 6000.0})


class DiagnosticTests(unittest.TestCase):
    def test_the_errors_line_and_the_report(self):
        item = diagnostic.errors_check({"errors": {"day": 0}})
        self.assertEqual((item["state"], item["summary"]), ("ok", "Aucune erreur ces dernières 24 heures."))
        item = diagnostic.errors_check({"errors": {"day": 3}})
        self.assertEqual((item["state"], item["summary"]), ("warning", "3 erreurs ces dernières 24 heures."))
        facts = {"now": 1000.0, "version": "1.1.97", "system": "macOS",
                 "errors": {"day": 1, "recent": [{"t": 990.0, "where": "serveur GET /api/x", "kind": "KeyError", "message": "'a'"}]}}
        text = diagnostic.report(facts, [item])
        self.assertIn("Dernières erreurs :", text)
        self.assertIn("serveur GET /api/x · KeyError : 'a'", text)


if __name__ == "__main__":
    unittest.main()
