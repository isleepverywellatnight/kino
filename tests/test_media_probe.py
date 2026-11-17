import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import media_probe


class MediaProbeTests(unittest.TestCase):
    def test_track_language_and_original_selection(self):
        data = {"format": {"format_name": "matroska,webm", "duration": "90"}, "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 640, "height": 360},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "tags": {"language": "fra"}, "disposition": {"default": 1}},
            {"index": 2, "codec_type": "audio", "codec_name": "ac3", "tags": {"language": "jpn"}, "disposition": {"original": 1}},
        ]}
        media = media_probe.parse_probe_json(json.dumps(data))
        self.assertEqual(media_probe.select_audio_track(media["audio"], "vf")["index"], 1)
        self.assertEqual(media_probe.select_audio_track(media["audio"], "vostfr")["index"], 2)
        self.assertEqual(media["video"][0]["codec"], "h264")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not available")
    def test_real_multitrack_file_is_detected_with_both_backends(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracks.mkv"
            subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
                "-map", "0:v", "-map", "1:a", "-map", "2:a",
                "-c:v", "libx264", "-c:a", "aac", "-metadata:s:a:0", "language=eng",
                "-metadata:s:a:1", "language=fra", str(path),
            ], check=True, capture_output=True)
            for ffprobe in (shutil.which("ffprobe"), None):
                with self.subTest(ffprobe=ffprobe):
                    result = media_probe.inspect_media(str(path), ffmpeg, ffprobe)
                    self.assertEqual(result["video"][0]["codec"], "h264")
                    self.assertEqual(len(result["audio"]), 2)
                    self.assertEqual(media_probe.select_audio_track(result["audio"], "vf")["language"], "fra")


if __name__ == "__main__":
    unittest.main()
