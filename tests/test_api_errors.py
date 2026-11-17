"""Invalid requests get a 400, and upstream errors never echo URLs or HTML pages."""

import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app
import config


class UpstreamErrorTests(unittest.TestCase):
    def test_error_messages_hide_debrid_keys_and_html(self):
        url = "https://torrentio.strem.fun/providers=x|realdebrid=SECRETKEY/stream/movie/tt1.json?token=SECRET2"
        page = io.BytesIO(b"<!DOCTYPE html><html><body>Cannot GET</body></html>")
        error = urllib.error.HTTPError(url, 404, "Not Found", {}, page)
        with patch("config.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                config.http_json(url, retries=0)
        message = str(caught.exception)
        self.assertNotIn("SECRET", message)
        self.assertNotIn("<", message)
        self.assertIn("torrentio.strem.fun", message)

    def test_invalid_json_from_a_service_names_only_its_host(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        url = "https://api.torbox.app/v1/api/torrents/requestdl?token=SECRETKEY"
        with patch("config.urllib.request.urlopen", return_value=Response(b"not json")):
            with self.assertRaises(RuntimeError) as caught:
                config.http_json(url, retries=0)
        self.assertNotIn("SECRET", str(caught.exception))


class RequestValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, data=None):
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_malformed_bodies_are_rejected(self):
        for raw in (b"{not json", b'["a", "list"]', b'"text"', b"\xff\xfe"):
            with self.subTest(raw=raw):
                status, payload = self.request("/api/watchlist", raw)
                self.assertEqual(status, 400)
                self.assertIn("JSON", payload["error"])

    def test_unknown_catalog_is_rejected_before_any_network_call(self):
        self.assertEqual(self.request("/api/catalog?type=weird")[0], 400)

    def test_metadata_needs_a_title_identifier(self):
        for path in ("/api/meta", "/api/meta?imdb_id=../x", "/api/series-meta?imdb_id="):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 400)


if __name__ == "__main__":
    unittest.main()
