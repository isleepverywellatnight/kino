"""Process-level check for the desktop launch guard on macOS."""

import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_LAUNCH_PROBE = """
import pathlib
import sys
import time

pathlib.Path.home = classmethod(lambda cls: pathlib.Path(sys.argv[1]))
import desktop

try:
    desktop.check_single_instance()
except SystemExit:
    print('duplicate', flush=True)
else:
    print('primary', flush=True)
    time.sleep(float(sys.argv[2]))
"""


@unittest.skipUnless(sys.platform == "darwin", "macOS launch guard")
class SingleInstanceTests(unittest.TestCase):
    def test_second_mac_process_cannot_pass_desktop_launch_guard(self):
        with tempfile.TemporaryDirectory(prefix="kino-instance-", dir="/private/tmp") as temp_home:
            first = subprocess.Popen(
                [sys.executable, "-c", _LAUNCH_PROBE, temp_home, "8"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                cwd=Path(__file__).resolve().parent.parent,
            )
            try:
                readable, _, _ = select.select([first.stdout], [], [], 20)
                self.assertTrue(readable, "La première instance n'a pas répondu.")
                self.assertEqual(first.stdout.readline().strip(), "primary")
                second = subprocess.run(
                    [sys.executable, "-c", _LAUNCH_PROBE, temp_home, "0"],
                    capture_output=True, text=True, timeout=20,
                    cwd=Path(__file__).resolve().parent.parent,
                )
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(second.stdout.strip(), "duplicate")
            finally:
                first.terminate()
                try:
                    first.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    first.kill()
                    first.communicate(timeout=3)
            third = subprocess.run(
                [sys.executable, "-c", _LAUNCH_PROBE, temp_home, "0"],
                capture_output=True, text=True, timeout=20,
                cwd=Path(__file__).resolve().parent.parent,
            )
            self.assertEqual(third.returncode, 0, third.stderr)
            self.assertEqual(third.stdout.strip(), "primary")


if __name__ == "__main__":
    unittest.main()
