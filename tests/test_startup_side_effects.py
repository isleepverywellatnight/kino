"""Importing KINO modules must not synchronise Google Drive; app startup does."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parent.parent


class StartupSideEffectTests(unittest.TestCase):
    def run_in_fake_home(self, code):
        with tempfile.TemporaryDirectory() as home:
            drive = Path(home) / "Library/CloudStorage/GoogleDrive-test/Mon Drive/KINO"
            drive.mkdir(parents=True)
            env = {**os.environ, "HOME": home, "USERPROFILE": home}
            subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, env=env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            return (drive / "kino_sync.json").exists()

    def test_importing_the_app_modules_leaves_google_drive_alone(self):
        self.assertFalse(self.run_in_fake_home("""
            import time
            import kino_db, config, app
            time.sleep(1.5)
        """))

    def test_startup_maintenance_synchronises_google_drive(self):
        self.assertTrue(self.run_in_fake_home("""
            import time
            import kino_db
            kino_db.start_background_maintenance()
            time.sleep(1.5)
        """))


if __name__ == "__main__":
    unittest.main()
