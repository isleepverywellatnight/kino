"""Bundled libmpv for the native player: location, self-check and headless playback."""
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import native_player

VENDORED = (Path(__file__).resolve().parent.parent / "vendor" / "libmpv" / "libmpv.2.dylib").is_file()


@unittest.skipUnless(sys.platform == "darwin" and VENDORED, "libmpv is vendored for macOS only")
class NativePlayerTests(unittest.TestCase):
    def test_libmpv_comes_from_kino_not_from_another_app(self):
        folder = native_player.libmpv_dir()
        self.assertEqual(folder, native_player.paths.VENDOR_DIR / "libmpv")
        result = native_player.check()
        self.assertGreaterEqual(int(result["api"].split(".")[0]), 2)
        self.assertTrue(result["mpv"].startswith("mpv "))

    def test_frozen_app_uses_its_own_bundle_first(self):
        with tempfile.TemporaryDirectory() as directory:
            bundled = Path(directory)
            (bundled / "libmpv.2.dylib").write_bytes(b"")
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", directory, create=True):
                self.assertEqual(native_player.libmpv_dir(), bundled)

    def test_missing_libmpv_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(native_player.paths, "VENDOR_DIR", Path(directory)), \
                patch.object(native_player, "_lib", None):
            self.assertIsNone(native_player.libmpv_dir())
            with self.assertRaises(native_player.LibmpvUnavailable):
                native_player.check()

    def test_headless_playback_reads_tracks_and_seeks(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.mkv"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-f", "lavfi", "-i", "sine=frequency=660", "-t", "6",
                            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac",
                            "-metadata:s:a:0", "language=fre", "-metadata:s:a:1", "language=eng", str(clip)], check=True)
            player = native_player.Mpv(vo="null", ao="null", alang="eng")
            try:
                player.command("loadfile", str(clip))
                deadline = time.time() + 10
                while time.time() < deadline and not (player.get_double("time-pos") or 0) > 0.2:
                    time.sleep(0.05)
                self.assertAlmostEqual(player.get_double("duration"), 6, delta=0.2)
                self.assertEqual(player.get_string("video-params/w"), "320")
                self.assertEqual(player.get_string("current-tracks/audio/lang"), "eng", "language preference applied")
                player.command("seek", 4, "absolute")
                deadline = time.time() + 5
                while time.time() < deadline and not (player.get_double("time-pos") or 0) >= 3.9:
                    time.sleep(0.05)
                self.assertGreaterEqual(player.get_double("time-pos"), 3.9)
            finally:
                player.close()


if __name__ == "__main__":
    unittest.main()
