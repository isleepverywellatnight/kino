"""The shared KINO Discord identity is the default for existing and new installs."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import api_service
import config
import discord_rpc
import kino_db


class DiscordPresenceConfigTests(unittest.TestCase):
    def test_existing_preset_migrates_to_kino_without_changing_other_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = root / ".rd_cinehub_config.json"
            settings.write_text(json.dumps({"discord_client_id": "631379801826918400",
                                            "discord_rpc": False, "pref_lang": "vostfr"}), encoding="utf-8")
            with patch.object(Path, "home", return_value=root), \
                 patch.object(config, "CONFIG_FILE", settings), \
                 patch.object(kino_db, "DB_PATH", root / "kino.db"):
                kino_db.init_db()
                loaded = config.load_config()
                self.assertEqual(loaded["discord_client_id"], "1556492442415398973")
                self.assertFalse(loaded["discord_rpc"])
                self.assertEqual(loaded["pref_lang"], "vostfr")
                config.save_config({"pref_lang": "vostfr"})
                self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["discord_client_id"],
                                 "1556492442415398973")

    def test_personal_application_id_is_preserved(self):
        custom_id = "123456789012345678"
        self.assertEqual(config.sanitize_config_payload({"discord_client_id": custom_id})["discord_client_id"],
                         custom_id)
        self.assertEqual(discord_rpc.DiscordRPCClient().client_id, "1556492442415398973")

    @patch("api_service.config.load_config", return_value={"discord_rpc": True, "provider_tokens": {}})
    def test_settings_api_reports_shared_identity_for_a_fresh_install(self, _load_config):
        self.assertEqual(api_service.build_config_response()["discord_client_id"], "1556492442415398973")


if __name__ == "__main__":
    unittest.main()
