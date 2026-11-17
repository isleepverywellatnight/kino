"""Page loads no longer prune the Debrid Cloud each time."""

import unittest
from unittest.mock import patch

import api_service
import debrid_engine


class AutoCleanupTests(unittest.TestCase):
    def setUp(self):
        debrid_engine._auto_cleanup_last.clear()
        self.addCleanup(debrid_engine._auto_cleanup_last.clear)

    def test_runs_at_most_every_six_hours_per_provider(self):
        with patch.object(debrid_engine, "rd_cleanup_cloud",
                          return_value={"deleted_downloads": 1, "deleted_torrents": 0}) as cleanup:
            first = debrid_engine.rd_auto_cleanup_cloud("key", 30, "realdebrid", now=1000.0)
            debrid_engine.rd_auto_cleanup_cloud("key", 30, "realdebrid", now=1000.0 + 3600)
            debrid_engine.rd_auto_cleanup_cloud("key", 30, "alldebrid", now=1000.0 + 3600)
            debrid_engine.rd_auto_cleanup_cloud("key", 30, "realdebrid", now=1000.0 + 6 * 3600 + 1)
        self.assertEqual(first["deleted_downloads"], 1)
        self.assertEqual([call.kwargs["provider"] for call in cleanup.call_args_list],
                         ["realdebrid", "alldebrid", "realdebrid"])

    def test_settings_page_uses_the_throttled_cleanup(self):
        started = []

        class FakeThread:
            def __init__(self, target, args, daemon):
                started.append(target)

            def start(self):
                pass

        cfg = {"debrid_provider": "realdebrid", "rd_token": "key", "rd_retention_days": 30, "provider_tokens": {}}
        with patch("api_service.config.load_config", return_value=cfg), \
             patch("api_service.debrid_engine.rd_get_user", return_value={"username": "x"}), \
             patch("api_service.threading.Thread", FakeThread):
            api_service.build_config_response()
        self.assertEqual(started, [debrid_engine.rd_auto_cleanup_cloud])


if __name__ == "__main__":
    unittest.main()
