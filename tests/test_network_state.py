"""Sans internet, on the server: KINO knows when the internet is down, says it
in plain words, stops retrying for nothing, and the interface can ask."""
import ast
import errno
import io
import json
import socket
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

import app
import config
import network_state

ROOT = Path(__file__).resolve().parent.parent
NO_DNS = urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StateTests(unittest.TestCase):
    def setUp(self):
        network_state.reset()
        self.addCleanup(network_state.reset)

    def test_only_a_missing_network_counts_as_offline(self):
        for error in (NO_DNS, socket.gaierror(8, "x"), OSError(errno.ENETUNREACH, "x"),
                      urllib.error.URLError(OSError(errno.EHOSTUNREACH, "x")), network_state.OfflineError()):
            self.assertTrue(network_state.is_network_error(error), error)
        for error in (urllib.error.URLError(ConnectionRefusedError(61, "x")), TimeoutError("x"), socket.timeout("x"),
                      urllib.error.HTTPError("https://x", 503, "x", {}, None), RuntimeError("x"), ValueError("x")):
            self.assertFalse(network_state.is_network_error(error), error)

    def test_the_check_opens_and_closes_a_connection_to_a_known_host(self):
        opened = []

        def connect(address, timeout):
            opened.append(address)
            if address[0] == "first":
                raise OSError("down")
            return Mock()

        self.assertTrue(network_state.probe((("first", 443), ("second", 443)), connect=connect))
        self.assertEqual(opened, [("first", 443), ("second", 443)])
        self.assertFalse(network_state.probe((("first", 443),), connect=connect))
        self.assertEqual({host for host, _ in network_state.PROBE_HOSTS}, {"v3-cinemeta.strem.io", "fr.wikipedia.org"},
                         "only hosts KINO already uses")

    def test_a_check_is_kept_a_while_and_done_again_on_request(self):
        check = Mock(return_value=False)
        self.assertEqual(network_state.status(check=check), {"online": False})
        self.assertEqual(network_state.status(check=check), {"online": False})
        self.assertEqual(check.call_count, 1)
        check.return_value = True
        self.assertEqual(network_state.status(refresh=True, check=check), {"online": True})
        self.assertEqual(check.call_count, 2)

    def test_requests_arriving_together_share_one_check(self):
        calls = []

        def slow_check():
            calls.append(1)
            time.sleep(0.15)
            return True

        answers = []
        threads = [threading.Thread(target=lambda: answers.append(network_state.status(refresh=True, check=slow_check)))
                   for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(answers, [{"online": True}] * 4)
        self.assertLess(len(calls), 4)

    def test_offline_lasts_only_a_few_seconds_without_a_new_failure(self):
        network_state.note_failure(NO_DNS)
        now = time.monotonic()
        self.assertTrue(network_state.known_offline(now))
        self.assertFalse(network_state.known_offline(now + network_state.FAST_FAIL_SECONDS + 1))
        network_state.note_success()
        self.assertFalse(network_state.known_offline())
        network_state.note_failure(RuntimeError("bad answer"))
        self.assertFalse(network_state.known_offline(), "a server error says nothing about the internet")


class HttpJsonTests(unittest.TestCase):
    def setUp(self):
        network_state.reset()
        self.addCleanup(network_state.reset)

    def test_no_network_fails_at_once_in_plain_words(self):
        with patch("config.urllib.request.urlopen", side_effect=NO_DNS) as urlopen, patch("config.time.sleep") as sleep:
            with self.assertRaises(network_state.OfflineError) as caught:
                config.http_json("https://v3-cinemeta.strem.io/catalog/movie/top.json")
            self.assertEqual(str(caught.exception), "Pas de connexion à internet.")
            self.assertEqual(urlopen.call_count, 1, "no retry when there is no network")
            sleep.assert_not_called()
            with self.assertRaises(network_state.OfflineError):
                config.http_json("https://api.real-debrid.com/rest/1.0/user")
            self.assertEqual(urlopen.call_count, 1, "the next request does not even try")

    def test_an_answer_ends_offline_and_server_errors_still_retry(self):
        network_state.note_failure(NO_DNS)
        network_state.note_success()
        with patch("config.urllib.request.urlopen", return_value=Response(b'{"ok": 1}')):
            self.assertEqual(config.http_json("https://example.org/a.json"), {"ok": 1})
        unavailable = urllib.error.HTTPError("https://example.org", 503, "busy", {}, io.BytesIO(b"busy"))
        with patch("config.urllib.request.urlopen", side_effect=unavailable) as urlopen, patch("config.time.sleep"):
            with self.assertRaises(RuntimeError):
                config.http_json("https://example.org/a.json", retries=2)
        self.assertEqual(urlopen.call_count, 3)
        self.assertFalse(network_state.known_offline(), "a server that answers means the internet works")


class RouteTests(unittest.TestCase):
    def setUp(self):
        network_state.reset()
        self.addCleanup(network_state.reset)

    def handler(self, path):
        request = object.__new__(app.RequestHandler)
        request.path = path
        request.headers = {}
        request._request_allowed = Mock(return_value=True)
        request.send_json = Mock()
        return request

    def test_the_interface_can_ask_and_ask_again(self):
        with patch.object(network_state, "status", return_value={"online": False}) as status:
            request = self.handler("/api/network")
            request.do_GET()
            request.send_json.assert_called_once_with({"online": False})
            self.handler("/api/network?refresh=1").do_GET()
        self.assertEqual([call.kwargs["refresh"] for call in status.call_args_list], [False, True])

    def sent(self, payload, status):
        request = object.__new__(app.RequestHandler)
        request.send_response = Mock()
        request.send_header = Mock()
        request.end_headers = Mock()
        request.wfile = io.BytesIO()
        request.send_json(payload, status=status)
        return json.loads(request.wfile.getvalue())

    def test_failures_while_offline_carry_the_offline_code(self):
        self.assertEqual(self.sent({"error": "Pas de connexion à internet."}, 500),
                         {"error": "Pas de connexion à internet.", "code": "offline"})
        self.assertEqual(self.sent({"error": "boom"}, 500), {"error": "boom"})
        network_state.note_failure(NO_DNS)
        self.assertEqual(self.sent({"error": "Aucun flux trouvé"}, 500), {"error": "Aucun flux trouvé", "code": "offline"})
        self.assertEqual(self.sent({"error": "Série invalide."}, 400), {"error": "Série invalide."}, "a wrong request stays one")
        self.assertEqual(self.sent({"error": "x", "code": "provider_auth"}, 500), {"error": "x", "code": "provider_auth"})

    def test_kino_checks_at_launch(self):
        source = (ROOT / "src" / "desktop.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        run = ast.get_source_segment(source, next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_server"))
        self.assertIn("threading.Thread(target=app.network_state.warm, daemon=True).start()", run)
        self.assertLess(run.index("network_state.warm"), run.index("serve_forever()"))


if __name__ == "__main__":
    unittest.main()
