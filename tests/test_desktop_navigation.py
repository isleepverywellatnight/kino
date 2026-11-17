"""Regression checks for the macOS settings navigation and bundled app."""

import ast
import unittest
from pathlib import Path

from tools.interface_source import app_source


ROOT = Path(__file__).resolve().parent.parent


class DesktopNavigationTests(unittest.TestCase):
    def test_missing_debrid_token_does_not_force_settings_open(self):
        script = app_source(ROOT)
        config_check = script.split("async function checkConfig()", 1)[1].split(
            "async function ", 1
        )[0]
        self.assertNotIn("openConfig();", config_check)




if __name__ == "__main__":
    unittest.main()
