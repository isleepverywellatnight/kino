"""Faster start-up: the server starts first and the window opens as soon as it
holds its port; the Debrid account check is shared and kept five minutes; the
automatic Cloud clean-up runs at most once an hour."""
import ast
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import api_service

ROOT = Path(__file__).resolve().parent.parent


class AccountTests(unittest.TestCase):
    def setUp(self):
        api_service._ACCOUNT_CACHE.clear()
        api_service._LAST_CLEANUP["at"] = 0.0

    def test_the_answer_is_kept_five_minutes_per_key(self):
        clock = [1000.0]
        with patch.object(api_service.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(api_service.debrid_engine, "rd_get_user", return_value={"username": "u"}) as check:
            self.assertEqual(api_service.account_status("KEY", "realdebrid"), ({"username": "u"}, "connected"))
            clock[0] += 299
            api_service.account_status("KEY", "realdebrid")
            self.assertEqual(check.call_count, 1)
            api_service.account_status("OTHER", "realdebrid")
            self.assertEqual(check.call_count, 2, "a new key is checked")
            clock[0] += 301
            api_service.account_status("OTHER", "realdebrid")
            self.assertEqual(check.call_count, 3, "checked again after five minutes")
        self.assertNotIn("KEY", repr(api_service._ACCOUNT_CACHE), "the key itself is never kept")

    def test_a_network_failure_is_not_remembered_but_a_refused_key_is(self):
        with patch.object(api_service.debrid_engine, "rd_get_user", side_effect=OSError("network down")) as check:
            self.assertEqual(api_service.account_status("KEY", "realdebrid"), (None, "unavailable"))
            api_service.account_status("KEY", "realdebrid")
        self.assertEqual(check.call_count, 2)
        with patch.object(api_service.debrid_engine, "rd_get_user", side_effect=RuntimeError("Erreur API (401) : bad token")) as check:
            self.assertEqual(api_service.account_status("KEY", "realdebrid"), (None, "invalid"))
            api_service.account_status("KEY", "realdebrid")
        self.assertEqual(check.call_count, 1)

    def test_the_start_up_check_and_the_first_request_share_one_call(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def slow(token, provider=None):
            calls.append(token)
            started.set()
            release.wait(2)
            return {"username": "u"}

        results = []
        with patch.object(api_service.debrid_engine, "rd_get_user", side_effect=slow):
            first = threading.Thread(target=lambda: results.append(api_service.account_status("KEY", "realdebrid")))
            first.start()
            started.wait(2)
            second = threading.Thread(target=lambda: results.append(api_service.account_status("KEY", "realdebrid")))
            second.start()
            release.set()
            first.join(2)
            second.join(2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [({"username": "u"}, "connected")] * 2)

    def test_warm_up_checks_the_saved_key_only(self):
        with patch.object(api_service.config, "load_config", return_value={"rd_token": "KEY", "debrid_provider": "torbox"}), \
                patch.object(api_service, "account_status") as status:
            api_service.warm_account()
        status.assert_called_once_with("KEY", "torbox")
        with patch.object(api_service.config, "load_config", return_value={}), patch.object(api_service, "account_status") as status:
            api_service.warm_account()
        status.assert_not_called()

    def test_the_cloud_clean_up_runs_at_most_once_an_hour(self):
        clock = [5000.0]
        cfg = {"rd_token": "KEY", "debrid_provider": "realdebrid", "rd_retention_days": 7}
        with patch.object(api_service.config, "load_config", return_value=cfg), \
                patch.object(api_service.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(api_service.debrid_engine, "rd_get_user", return_value={"username": "u"}), \
                patch.object(api_service.threading, "Thread") as thread:
            for step in (0, 600, 2400, 700):
                clock[0] += step
                response = api_service.build_config_response()
        self.assertEqual(response["user_status"], "connected")
        self.assertEqual(thread.call_count, 2, "at 0 and after an hour, not in between")


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "src" / "desktop.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def function(self, name):
        node = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(self.source, node)

    def test_the_server_holds_its_port_before_anything_else(self):
        run = self.function("run_server")
        order = [run.index(part) for part in ("app.ThreadingHTTPServer((\"0.0.0.0\"", "SERVER_READY.set()",
                                               "library.reconcile()", "start_background_maintenance()",
                                               "api_service.warm_account", "serve_forever()")]
        self.assertEqual(order, sorted(order))
        self.assertIn("finally:\n        SERVER_READY.set()", run, "the window never waits on a failed start")

    def test_the_window_waits_for_the_server_not_a_fixed_pause(self):
        main = self.function("main")
        self.assertNotIn("time.sleep(0.3)", main)
        self.assertLess(main.index("t.start()"), main.index("install_macos_hooks()"), "the server starts while the hooks load")
        self.assertLess(main.index("install_macos_hooks()"), main.index("SERVER_READY.wait(2.0)"))
        self.assertLess(main.index("SERVER_READY.wait(2.0)"), main.index("webview.create_window("))


if __name__ == "__main__":
    unittest.main()


class WebDataTests(unittest.TestCase):
    def test_the_window_keeps_its_web_data(self):
        # pywebview's private mode (its default) erased all web data at each
        # launch: posters downloaded again (12 in 105-511 ms instead of 5-28 ms from
        # WebKit's disk cache) and the page forgot volume, mute and catalogue view.
        import ast
        from pathlib import Path
        tree = ast.parse((Path(__file__).resolve().parent.parent / "src" / "desktop.py").read_text(encoding="utf-8"))
        starts = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "start"
                  and any(k.arg == "gui" and getattr(k.value, "value", "") == "cocoa" for k in node.keywords)]
        self.assertEqual(len(starts), 1)
        private = next((k.value.value for k in starts[0].keywords if k.arg == "private_mode"), None)
        self.assertIs(private, False)
