"""KINO's Python modules use only names that exist (tools/backend_check.py),
and the AllDebrid and TorBox redirections that check found broken work again."""
import importlib.util
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch

import app
import debrid_engine

ROOT = Path(__file__).resolve().parent.parent


def load_checker():
    spec = importlib.util.spec_from_file_location("kino_backend_check", ROOT / "tools" / "backend_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def handler_for(path):
    handler = object.__new__(app.RequestHandler)
    handler.path = path
    handler._request_allowed = Mock(return_value=True)
    handler.send_json = Mock()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    return handler


class IntegrityTests(unittest.TestCase):
    def test_every_module_uses_only_names_that_exist(self):
        self.assertEqual(load_checker().check(ROOT), [])

    def test_the_check_finds_a_name_bound_nowhere(self):
        checker = load_checker()
        with self.subTest("missing"):
            import tempfile
            with tempfile.TemporaryDirectory() as folder:
                Path(folder, "src").mkdir()
                Path(folder, "src", "broken.py").write_text(
                    "import os\n\n\ndef run(value):\n    local = os.sep\n    return local + vanished(value)\n",
                    encoding="utf-8")
                Path(folder, "tests").mkdir()
                Path(folder, "tests", "test_ignored.py").write_text("gone()\n", encoding="utf-8")
                self.assertEqual(checker.check(Path(folder)),
                                 [{"file": "broken.py", "scope": "run", "name": "vanished"}])


class DebridRedirectionTests(unittest.TestCase):
    def test_alldebrid_link_redirects_to_the_unlocked_file(self):
        handler = handler_for("/api/ad-dl?link=" + urllib.parse.quote("https://alldebrid.com/f/abc"))
        with patch.object(debrid_engine, "_get_provider_and_token", return_value=("alldebrid", "SECRET")) as token, \
             patch.object(app, "http_json", return_value={"data": {"link": "https://cdn.example/film.mkv"}}) as fetch:
            handler.do_GET()
        token.assert_called_once_with(provider="alldebrid")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(query["link"], ["https://alldebrid.com/f/abc"])
        handler.send_response.assert_called_once_with(302)
        handler.send_header.assert_any_call("Location", "https://cdn.example/film.mkv")
        handler.send_json.assert_not_called()

    def test_torbox_file_redirects_with_an_encoded_request(self):
        handler = handler_for("/api/torbox-dl?torrent_id=" + urllib.parse.quote("12&x=1") + "&file_id=3")
        with patch.object(debrid_engine, "_get_provider_and_token", return_value=("torbox", "SECRET")), \
             patch.object(app, "http_json", return_value={"data": "https://cdn.example/ep.mkv"}) as fetch:
            handler.do_GET()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(query, {"token": ["SECRET"], "torrent_id": ["12&x=1"], "file_id": ["3"]})
        self.assertEqual(fetch.call_args.kwargs["headers"], {"Authorization": "Bearer SECRET"})
        handler.send_header.assert_any_call("Location", "https://cdn.example/ep.mkv")

    def test_a_refused_link_reports_an_error_without_the_key(self):
        handler = handler_for("/api/ad-dl?link=x")
        with patch.object(debrid_engine, "_get_provider_and_token", return_value=("alldebrid", "SECRET")), \
             patch.object(app, "http_json", return_value={"data": {}}):
            handler.do_GET()
        handler.send_response.assert_not_called()
        payload, = handler.send_json.call_args.args
        self.assertEqual(handler.send_json.call_args.kwargs, {"status": 500})
        self.assertIn("AllDebrid", payload["error"])
        self.assertNotIn("SECRET", payload["error"])


if __name__ == "__main__":
    unittest.main()
