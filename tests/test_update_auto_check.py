"""KINO asks GitHub for a new version at startup and every six hours, unless the
user turned it off in Settings → Updates."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kino_update


class AutoCheckTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.manager = kino_update.UpdateManager(app_path=Path(folder.name) / "KINO.app", state_dir=Path(folder.name) / "state")
        self.asked = 0

        def check(*args, **kwargs):
            self.asked += 1
            return {}
        self.manager.check_latest = check

    def test_on_by_default_and_every_six_hours(self):
        self.assertTrue(self.manager.auto_check())
        self.assertTrue(self.manager.status()["auto_check"])
        self.assertEqual(kino_update.AUTO_CHECK_EVERY, 6 * 3600)
        with patch.object(kino_update.time, "sleep") as sleep:
            self.manager.check_latest_quietly(rounds=3)
        self.assertEqual(self.asked, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [20.0, 6 * 3600, 6 * 3600])

    def test_turned_off_it_asks_nothing(self):
        state = self.manager.set_auto_check(False)
        self.assertFalse(state["auto_check"])
        if sys.platform != "win32":
            self.assertEqual((self.manager.state_dir / "settings.json").stat().st_mode & 0o777, 0o600)
        with patch.object(kino_update.time, "sleep"):
            self.manager.check_latest_quietly(rounds=2)
        self.assertEqual(self.asked, 0)
        self.assertTrue(self.manager.set_auto_check(True)["auto_check"])

    def test_a_failure_stays_silent(self):
        def down(*args, **kwargs):
            raise OSError("no network")
        self.manager.check_latest = down
        with patch.object(kino_update.time, "sleep"):
            self.manager.check_latest_quietly(rounds=1)


if __name__ == "__main__":
    unittest.main()
