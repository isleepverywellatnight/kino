"""The « Sous-titres français automatiques » setting: on unless turned off,
saved only as a yes/no value."""
import unittest
from unittest.mock import patch

import api_service
import config


class SettingTests(unittest.TestCase):
    def test_only_a_yes_or_no_is_kept(self):
        self.assertEqual(config.sanitize_config_payload({"auto_fr_subs": False}), {"auto_fr_subs": False})
        self.assertEqual(config.sanitize_config_payload({"auto_fr_subs": True}), {"auto_fr_subs": True})
        self.assertNotIn("auto_fr_subs", config.sanitize_config_payload({"auto_fr_subs": "non"}))

    def test_on_by_default_and_sent_to_the_page(self):
        with patch.object(api_service.config, "load_config", return_value={}):
            self.assertIs(api_service.build_config_response()["auto_fr_subs"], True)
        with patch.object(api_service.config, "load_config", return_value={"auto_fr_subs": False}):
            self.assertIs(api_service.build_config_response()["auto_fr_subs"], False)


if __name__ == "__main__":
    unittest.main()
