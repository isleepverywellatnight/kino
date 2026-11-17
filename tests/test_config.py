#!/usr/bin/env python3
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import api_service
import config


class TestConfigSanitization(unittest.TestCase):
    def test_player_preferences_exposed_in_settings_are_accepted(self):
        cleaned = config.sanitize_config_payload({
            "pref_lang": "vostfr", "hdr_mode": "hdr_native", "audio_mode": "standard",
        })
        self.assertEqual(cleaned, {
            "pref_lang": "vostfr", "hdr_mode": "hdr_native", "audio_mode": "standard",
        })
        self.assertEqual(config.sanitize_config_payload({"pref_lang": "vost"})["pref_lang"], "vostfr")

    def test_sanitize_config_payload_normalizes_known_values(self):
        payload = {
            "debrid_provider": "REALDEBRID",
            "rd_token": "  abc123  ",
            "download_dir": "/tmp/test-downloads",
            "player_mode": "KINO",
            "pref_lang": "VF",
            "pref_quality": "4K",
            "hdr_mode": "SDR_PREF",
            "audio_mode": "voice_boost",
            "rd_retention_days": "30",
            "discord_rpc": "false",
            "discord_client_id": "abc123",
            "provider_tokens": {"realdebrid": "x", "bad": "y"},
        }

        cleaned = config.sanitize_config_payload(payload)

        self.assertEqual(cleaned["debrid_provider"], "realdebrid")
        self.assertEqual(cleaned["rd_token"], "abc123")
        self.assertEqual(cleaned["download_dir"], str(Path("/tmp/test-downloads")))
        self.assertEqual(cleaned["player_mode"], "kino")
        self.assertEqual(cleaned["pref_lang"], "vf")
        self.assertEqual(cleaned["pref_quality"], "4k")
        self.assertEqual(cleaned["hdr_mode"], "sdr_pref")
        self.assertEqual(cleaned["audio_mode"], "voice_boost")
        self.assertEqual(cleaned["rd_retention_days"], 30)
        self.assertIs(cleaned["discord_rpc"], False)
        self.assertEqual(cleaned["discord_client_id"], "abc123")
        self.assertEqual(cleaned["provider_tokens"], {"realdebrid": "x"})

    def test_sanitize_config_payload_rejects_unknown_values(self):
        payload = {
            "debrid_provider": "ghost_provider",
            "player_mode": "totally-invalid",
            "pref_lang": "unknown",
            "hdr_mode": "weird",
            "provider_tokens": {"realdebrid": "ok", "unknown": "x"},
        }

        cleaned = config.sanitize_config_payload(payload)

        self.assertNotIn("debrid_provider", cleaned)
        self.assertNotIn("player_mode", cleaned)
        self.assertNotIn("pref_lang", cleaned)
        self.assertNotIn("hdr_mode", cleaned)
        self.assertEqual(cleaned["provider_tokens"], {"realdebrid": "ok"})

    @patch("api_service.config.save_config")
    def test_api_service_config_helpers(self, mock_save_config):
        payload = {
            "debrid_provider": "realdebrid",
            "rd_token": "abc123",
            "player_mode": "kino",
            "pref_lang": "vf",
            "pref_quality": "4k",
            "hdr_mode": "sdr_pref",
            "discord_rpc": False,
        }

        self.assertEqual(api_service.normalize_config_updates(payload)["rd_token"], "abc123")
        self.assertEqual(api_service.apply_config_updates(payload)["ok"], True)
        mock_save_config.assert_called_once()

    @patch("api_service.debrid_engine.rd_get_user", side_effect=TimeoutError("provider timed out"))
    @patch("api_service.config.load_config")
    def test_config_response_does_not_report_provider_outage_as_invalid_key(self, mock_load_config, _mock_user):
        mock_load_config.return_value = {
            "debrid_provider": "realdebrid",
            "rd_token": "test-token",
            "provider_tokens": {"realdebrid": "test-token"},
        }

        response = api_service.build_config_response()

        self.assertIsNone(response["user"])
        self.assertEqual(response["user_status"], "unavailable")

    @patch("api_service.debrid_engine.rd_get_user", side_effect=RuntimeError("Erreur API (401): token rejected"))
    @patch("api_service.config.load_config")
    def test_config_response_marks_explicit_auth_rejection_as_invalid(self, mock_load_config, _mock_user):
        mock_load_config.return_value = {
            "debrid_provider": "realdebrid",
            "rd_token": "test-token",
            "provider_tokens": {"realdebrid": "test-token"},
        }

        response = api_service.build_config_response()

        self.assertEqual(response["user_status"], "invalid")

    @patch("config.urllib.request.urlopen")
    def test_http_json_retries_and_parses_valid_json(self, mock_urlopen):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        call_count = {"value": 0}

        def fake_urlopen(*args, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                raise Exception("temporary failure")
            return FakeResponse({"status": "ok", "value": 42})

        mock_urlopen.side_effect = fake_urlopen

        result = config.http_json("https://example.test/api", retries=1)

        self.assertEqual(result, {"status": "ok", "value": 42})
        self.assertGreaterEqual(mock_urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
