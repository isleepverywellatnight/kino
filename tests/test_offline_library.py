"""End-to-end offline download checks with an isolated DB and local HTTP source."""

from contextlib import closing
import base64
import atexit
import errno
import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

_TEST_HOME = tempfile.TemporaryDirectory()
atexit.register(_TEST_HOME.cleanup)
with patch.object(Path, "home", return_value=Path(_TEST_HOME.name)):
    import app
    import kino_db
    import offline_library


VIDEO = b"KINO-VIDEO-" * 350_000
POSTER = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/v6kAAAAASUVORK5CYII=")


class SourceHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/expired.mp4":
            self.send_error(403)
            return
        if self.path == "/poster":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(POSTER)))
            self.end_headers()
            self.wfile.write(POSTER)
            return
        supports_range = self.path != "/norange.mp4"
        use_range = supports_range and self.headers.get("Range", "").startswith("bytes=")
        offset = int(self.headers["Range"].split("=", 1)[1].split("-", 1)[0]) if use_range else 0
        if use_range and self.headers.get("If-Range") != '"stable-v1"':
            offset = 0
            use_range = False
        self.send_response(206 if use_range else 200)
        self.send_header("Content-Length", str(len(VIDEO) - offset))
        self.send_header("Content-Type", "video/mp4")
        self.send_header("ETag", '"stable-v1"')
        self.send_header("Last-Modified", "Wed, 21 Oct 2015 07:28:00 GMT")
        if supports_range:
            self.send_header("Accept-Ranges", "bytes")
        if use_range:
            self.send_header("Content-Range", f"bytes {offset}-{len(VIDEO)-1}/{len(VIDEO)}")
        self.end_headers()
        try:
            for position in range(offset, len(VIDEO), 64 * 1024):
                self.wfile.write(VIDEO[position:position + 64 * 1024])
                if self.path in ("/slow.mp4", "/norange.mp4"):
                    time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass


class OfflineLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(kino_db, "DB_PATH", self.root / "kino.db")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        kino_db.init_db()
        self.library = offline_library.OfflineLibrary()
        self.source = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
        self.source_thread = threading.Thread(target=self.source.serve_forever, daemon=True)
        self.source_thread.start()
        self.addCleanup(self.source.server_close)
        self.addCleanup(self.source.shutdown)
        self.base = f"http://127.0.0.1:{self.source.server_port}"

    def wait_for(self, download_id, statuses, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.library.list().get(download_id)
            if item and item["status"] in statuses and download_id not in self.library._active:
                return item
            time.sleep(0.02)
        self.fail(f"Download did not reach {statuses}: {self.library.list().get(download_id)}")

    def create(self, path="/file.mp4", filename="film.mp4", media=None, subtitles=None):
        return self.library.create(self.base + path, filename, self.root / "downloads", media, subtitles)

    def test_free_space_is_measured_on_the_download_volume_even_before_the_folder_exists(self):
        missing = self.root / "not" / "created" / "yet"
        with patch.object(offline_library.shutil, "disk_usage",
                          return_value=shutil._ntuple_diskusage(100, 40, 60)) as usage:
            self.assertEqual(self.library.free_bytes(missing), 60)
        self.assertEqual(Path(usage.call_args[0][0]), self.root)

    def test_download_stops_before_writing_when_the_disk_would_be_nearly_full(self):
        free = offline_library.DISK_RESERVE_BYTES + len(VIDEO) - 1
        with patch.object(offline_library.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(0, 0, free)):
            download_id, _ = self.create()
            item = self.wait_for(download_id, {"failed"})
        self.assertEqual(item["error_code"], "disk_full")
        self.assertEqual(list((self.root / "downloads" / "KINO Offline").glob("*.part")), [])
        with patch.object(offline_library.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(0, 0, free + 1)):
            self.library.resume(download_id)
            self.assertTrue(self.wait_for(download_id, {"completed"})["available"])

    def test_completed_download_survives_restart_and_serves_ranges_with_local_poster_and_subtitles(self):
        media = {"id": "tt123", "name": "Film exemple", "type": "movie", "poster": self.base + "/poster"}
        subtitle = {"label": "CC : Français", "lang": "fr", "kind": "opensubtitles",
                    "cues": [{"start": 1, "end": 2, "text": "Bonjour"}]}
        download_id, duplicate = self.create(media=media, subtitles=subtitle)
        self.assertFalse(duplicate)
        item = self.wait_for(download_id, {"completed"})
        self.assertTrue(item["available"])
        self.assertEqual(item["bytes_done"], len(VIDEO))
        self.assertTrue(item["has_subtitles"])
        self.assertEqual(self.library.media_path(download_id).read_bytes(), VIDEO)
        self.assertEqual(self.library.poster_path(download_id).read_bytes(), POSTER)
        self.assertEqual(self.library.subtitles(download_id)["cues"][0]["text"], "Bonjour")
        self.assertEqual(self.create(media=media)[0:2], (download_id, True))

        reloaded = offline_library.OfflineLibrary()
        reloaded.reconcile()
        self.assertTrue(reloaded.list()[download_id]["available"])
        with patch.object(app.offline_library, "library", reloaded):
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}"
                request = urllib.request.Request(url + f"/api/offline/media/{download_id}",
                                                 headers={"Range": "bytes=10-19"})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers["Content-Range"], f"bytes 10-19/{len(VIDEO)}")
                    self.assertEqual(response.read(), VIDEO[10:20])
                with urllib.request.urlopen(url + f"/api/offline/subtitles/{download_id}") as response:
                    self.assertEqual(json.load(response)["subtitle"]["lang"], "fr")
                with urllib.request.urlopen(url + f"/api/offline/poster/{download_id}") as response:
                    self.assertEqual(response.headers["Content-Type"], "image/png")
                    self.assertEqual(response.read(), POSTER)
            finally:
                server.shutdown()
                server.server_close()

    def test_paused_download_resumes_only_with_validated_range(self):
        download_id, _ = self.create("/slow.mp4")
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and self.library.list()[download_id]["bytes_done"] < 1024 * 1024:
            time.sleep(0.02)
        self.library.pause(download_id)
        paused = self.wait_for(download_id, {"paused"})
        self.assertGreater(paused["bytes_done"], 0)
        self.assertFalse(paused["available"])
        self.assertIsNone(self.library.media_path(download_id))
        self.library.resume(download_id)
        completed = self.wait_for(download_id, {"completed"})
        self.assertTrue(completed["available"])
        self.assertEqual(self.library.media_path(download_id).read_bytes(), VIDEO)

    def test_server_without_range_requires_explicit_restart(self):
        download_id, _ = self.create("/norange.mp4")
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and self.library.list()[download_id]["bytes_done"] < 1024 * 1024:
            time.sleep(0.02)
        self.library.pause(download_id)
        self.wait_for(download_id, {"paused"})
        with self.assertRaises(offline_library.OfflineError) as failure:
            self.library.resume(download_id)
        self.assertEqual(failure.exception.code, "restart_required")
        self.library.resume(download_id, restart=True)
        self.assertTrue(self.wait_for(download_id, {"completed"})["available"])

    def test_expired_url_disk_full_and_app_restart_never_report_complete(self):
        expired, _ = self.create("/expired.mp4")
        self.assertEqual(self.wait_for(expired, {"failed"})["error_code"], "url_expired")
        self.assertIsNone(self.library.media_path(expired))

        with patch.object(offline_library.os, "open", side_effect=OSError(errno.ENOSPC, "full")):
            full, _ = self.create(filename="another.mp4")
            self.assertEqual(self.wait_for(full, {"failed"})["error_code"], "disk_full")
        self.assertIsNone(self.library.media_path(full))

        interrupted, _ = self.create(filename="third.mp4")
        self.wait_for(interrupted, {"completed"})
        offline_library._update(interrupted, status="downloading")
        new_process = offline_library.OfflineLibrary()
        new_process.reconcile()
        self.assertEqual(new_process.list()[interrupted]["status"], "interrupted")
        self.assertFalse(new_process.list()[interrupted]["available"])

    def test_missing_file_and_remove_preserve_history_and_watchlist(self):
        download_id, _ = self.create(media={"id": "tt123", "name": "Film exemple"})
        self.wait_for(download_id, {"completed"})
        with closing(kino_db.get_connection()) as conn, conn:
            conn.execute("INSERT INTO watchlist (id, name) VALUES ('tt123', 'Film exemple')")
            conn.execute("INSERT INTO watch_history (id, data_json, last_watched) VALUES ('tt123', '{}', 1)")
        file_path = self.library.media_path(download_id)
        file_path.rename(file_path.with_name("moved.mp4"))
        self.assertEqual(self.library.list()[download_id]["error_code"], "missing_file")
        self.assertIsNone(self.library.media_path(download_id))
        self.library.remove(download_id)
        with closing(kino_db.get_connection()) as conn, conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM watchlist WHERE id='tt123'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM watch_history WHERE id='tt123'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
