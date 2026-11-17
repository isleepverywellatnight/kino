#!/usr/bin/env python3
import unittest
from unittest.mock import patch

import api_routes


class TestApiRoutes(unittest.TestCase):
    @patch("api_routes.api_service.build_config_response", return_value={"debrid_provider": "realdebrid", "configured_providers": {}})
    def test_config_get_route_uses_service_response(self, _mock_response):
        payload = api_routes.handle_config_get()
        self.assertIsInstance(payload, dict)
        self.assertIn("debrid_provider", payload)
        self.assertIn("configured_providers", payload)

    @patch("api_routes.api_service.apply_config_updates", return_value={"ok": True})
    def test_config_post_route_returns_ok(self, mock_apply):
        payload = api_routes.handle_config_post({"debrid_provider": "realdebrid", "rd_token": "abc123", "discord_rpc": False})
        self.assertEqual(payload["ok"], True)
        mock_apply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
