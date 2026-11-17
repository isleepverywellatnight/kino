import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
import media_probe
import player_engine


class TestInternalPlayer(unittest.TestCase):
    def test_next_episode_skips_contradictory_file(self):
        streams = [
            {"title": "Example S01E02", "magnet": "magnet:first", "score": 2000},
            {"title": "Example S01E02", "magnet": "magnet:second", "score": 1000},
        ]
        def resolve(_token, magnet, **_kwargs):
            episode = 3 if magnet == "magnet:first" else 2
            return {"ready": True, "files": [{"filename": f"Example.S01E0{episode}.mkv",
                    "download": f"https://example.invalid/episode-{episode}", "is_target_ep": True}]}
        with patch.object(player_engine, "AUTO_STREAM_CACHE", {}), \
             patch.object(player_engine, "load_config", return_value={"rd_token": "test-token"}), \
             patch.object(player_engine.stream_engine, "get_available_streams", return_value=streams), \
             patch.object(player_engine, "rd_debrid_magnet", side_effect=resolve) as debrid, \
             patch.object(player_engine, "record_history"):
            url = player_engine.resolve_auto_stream_episode({
                "imdb_id": "tt0000001", "season": 1, "episode": 2, "name": "Example",
            })
        self.assertEqual(url, "https://example.invalid/episode-2")
        self.assertEqual(debrid.call_count, 2)

    def test_prefetched_episode_enters_history_only_when_requested(self):
        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        params = {"imdb_id": "tt0000001", "season": 1, "episode": 2, "name": "Example"}
        streams = [{"title": "Example S01E02", "magnet": "magnet:next", "score": 1000}]
        ready = {"ready": True, "files": [{
            "download": "https://example.invalid/episode-2", "filename": "Example.S01E02.mkv",
            "is_target_ep": True,
        }]}
        with patch.object(player_engine, "AUTO_STREAM_CACHE", {}) as cache, \
             patch.object(player_engine, "load_config", return_value={"rd_token": "test-token"}), \
             patch.object(player_engine.stream_engine, "get_available_streams", return_value=streams), \
             patch.object(player_engine, "rd_debrid_magnet", return_value=ready) as debrid, \
             patch.object(player_engine, "record_history") as history, \
             patch.object(player_engine.threading, "Thread", ImmediateThread), \
             patch.object(player_engine.time, "sleep"):
            player_engine.prefetch_next_episode("tt0000001", season=1, next_ep=2, name="Example")
            self.assertIn(("tt0000001", 1, 2), cache)
            history.assert_not_called()

            url = player_engine.resolve_auto_stream_episode(params)

        self.assertEqual(url, "https://example.invalid/episode-2")
        debrid.assert_called_once()
        history.assert_called_once()
        self.assertEqual(history.call_args.args[0]["episode"], 2)

    def test_next_episode_uses_server_order_and_saved_preferences(self):
        streams = [
            {"title": "Preferred by server", "magnet": "magnet:first", "info_hash": "first", "score": 3000},
            {"title": "Previous source", "magnet": "magnet:second", "info_hash": "second", "score": 1000},
        ]
        cfg = {"rd_token": "test-token", "pref_lang": "vostfr", "pref_quality": "1080p", "hdr_mode": "sdr_pref"}
        result = {"ready": True, "files": [{"download": "https://example.invalid/video", "filename": "episode.mkv"}]}
        with patch.object(player_engine, "AUTO_STREAM_CACHE", {}), \
             patch.object(player_engine, "load_config", return_value=cfg), \
             patch.object(player_engine.stream_engine, "get_available_streams", return_value=streams) as search, \
             patch.object(player_engine, "rd_debrid_magnet", return_value=result) as resolve, \
             patch.object(player_engine, "record_history"):
            url = player_engine.resolve_auto_stream_episode({
                "imdb_id": "tt0000001", "season": 1, "episode": 2, "name": "Example", "pref_hash": "second",
            })
        self.assertEqual(url, "https://example.invalid/video")
        self.assertEqual(resolve.call_args.args[1], "magnet:first")
        self.assertEqual(search.call_args.args[0].pref_lang, "vostfr")
        self.assertEqual(search.call_args.args[0].pref_quality, "1080p")

    def test_icon_sprite_is_served_by_app(self):
        handler = object.__new__(app.RequestHandler)
        handler.path = "/icons.svg"
        handler.wfile = io.BytesIO()
        handler._request_allowed = Mock(return_value=True)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()

        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "image/svg+xml")
        self.assertIn(b'<symbol id="film"', handler.wfile.getvalue())

    def test_compatible_stream_always_converts_audio_to_aac(self):
        cmd = player_engine.build_compatible_stream_command(
            "/bin/ffmpeg", "https://example.invalid/movie.mkv", 42, False)
        self.assertIn(["-c:a", "aac"], [cmd[i:i + 2] for i in range(len(cmd) - 1)])
        self.assertIn("42.00", cmd)

    def test_seeking_copies_a_playable_video_instead_of_reencoding_it(self):
        # Re-encoding a 4K 60 fps film on every seek or resume took 1.8 GB and
        # every core: the 8 GB Mac froze (2026-11-09).
        cmd = player_engine.build_compatible_stream_command(
            "/bin/ffmpeg", "https://example.invalid/movie.mkv", 600, False, hevc_tag=True)
        pairs = [cmd[i:i + 2] for i in range(len(cmd) - 1)]
        self.assertIn(["-c:v", "copy"], pairs)
        self.assertIn(["-tag:v", "hvc1"], pairs)
        self.assertNotIn("libx264", cmd)
        self.assertLess(cmd.index("-ss"), cmd.index("-i"), "fast input seek")

        start_cmd = player_engine.build_compatible_stream_command(
            "/bin/ffmpeg", "https://example.invalid/movie.mkv")
        self.assertIn(["-c:v", "copy"], [start_cmd[i:i + 2] for i in range(len(start_cmd) - 1)])

    def test_hevc_fallback_converts_video_too(self):
        cmd = player_engine.build_compatible_stream_command(
            "/bin/ffmpeg", "https://example.invalid/movie.mkv", 0, True)
        self.assertIn(["-c:v", "libx264"], [cmd[i:i + 2] for i in range(len(cmd) - 1)])

    def test_required_conversion_is_bounded_to_1080p_and_four_threads(self):
        with patch.object(player_engine.sys, "platform", "darwin"):
            cmd = player_engine.build_compatible_stream_command(
                "/bin/ffmpeg", "https://example.invalid/movie.mkv", 600, True)
        pairs = [cmd[i:i + 2] for i in range(len(cmd) - 1)]
        self.assertIn(["-vf", "scale=-2:'min(1080,ih)'"], pairs)
        self.assertIn(["-threads", "4"], pairs)
        self.assertIn(["-hwaccel", "videotoolbox"], pairs)
        self.assertLess(cmd.index("-hwaccel"), cmd.index("-i"))
        with patch.object(player_engine.sys, "platform", "win32"):
            self.assertNotIn("-hwaccel", player_engine.build_compatible_stream_command(
                "/bin/ffmpeg", "https://example.invalid/movie.mkv", 0, True))

    def test_compatible_stream_selects_audio_without_reencoding_when_possible(self):
        cmd = player_engine.build_compatible_stream_command(
            "/bin/ffmpeg", "https://example.invalid/movie.mkv", audio_index=3, transcode_audio=False)
        pairs = [cmd[i:i + 2] for i in range(len(cmd) - 1)]
        self.assertIn(["-map", "0:3?"], pairs)
        self.assertIn(["-c:a", "copy"], pairs)
        self.assertIn(["-c:v", "copy"], pairs)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not available")
    def test_real_selected_audio_is_converted_to_aac(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mkv"
            output = Path(directory) / "output.mp4"
            subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
                "-map", "0:v", "-map", "1:a", "-map", "2:a",
                "-c:v", "libx264", "-c:a:0", "aac", "-c:a:1", "ac3",
                "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fra", str(source),
            ], check=True, capture_output=True)
            command = player_engine.build_compatible_stream_command(ffmpeg, str(source), audio_index=2)
            with output.open("wb") as output_file:
                subprocess.run(command, stdout=output_file, stderr=subprocess.PIPE, check=True)
            result = media_probe.inspect_media(str(output), ffmpeg, shutil.which("ffprobe"))
            self.assertEqual(len(result["audio"]), 1)
            self.assertEqual(result["audio"][0]["language"], "fra")
            self.assertEqual(result["audio"][0]["codec"], "aac")

    def test_hevc_copy_is_labelled_hvc1_only_when_the_video_is_copied(self):
        def pairs(cmd):
            return [cmd[i:i + 2] for i in range(len(cmd) - 1)]
        copied = player_engine.build_compatible_stream_command(
            "/bin/ffmpeg", "https://example.invalid/movie.mkv", hevc_tag=True)
        self.assertIn(["-tag:v", "hvc1"], pairs(copied))
        for converted in (
                player_engine.build_compatible_stream_command(
                    "/bin/ffmpeg", "https://example.invalid/movie.mkv", 0, True, hevc_tag=True),
                player_engine.build_compatible_stream_command(
                    "/bin/ffmpeg", "https://example.invalid/movie.mkv", 600, True, hevc_tag=True)):
            self.assertNotIn("-tag:v", converted, "H.264 output must keep its own label")
        self.assertNotIn("-tag:v", player_engine.build_compatible_stream_command(
            "/bin/ffmpeg", "https://example.invalid/movie.mkv"))

    def _get(self, path):
        handler = object.__new__(app.RequestHandler)
        handler.path = path
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler._request_allowed = Mock(return_value=True)
        handler.send_json = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        return handler

    def test_offline_film_is_converted_from_its_downloaded_file(self):
        download_id = "8562009d895e48baa64b51e301d49556"
        with tempfile.TemporaryDirectory() as directory:
            film = Path(directory) / "film.mkv"
            film.write_bytes(b"\x1aE\xdf\xa3")
            process = Mock()
            process.stdout.read.side_effect = [b"\x00\x00\x00\x18ftyp", b""]
            with patch.object(app.offline_library.library, "media_path",
                              side_effect=lambda dl_id: film if dl_id == download_id else None), \
                    patch.object(app, "find_ffmpeg", return_value="/bin/ffmpeg"), \
                    patch.object(app.subprocess, "Popen", return_value=process) as popen:
                handler = self._get(f"/api/remux?url=%2Fapi%2Foffline%2Fmedia%2F{download_id}&ss=0&hvc1=1&acopy=1")
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("-i") + 1], str(film))
            self.assertIn("hvc1", command)
            handler.send_response.assert_called_once_with(200)

    def test_remux_refuses_local_paths_other_protocols_and_unknown_downloads(self):
        with patch.object(app.offline_library.library, "media_path", return_value=None), \
                patch.object(app, "find_ffmpeg", return_value="/bin/ffmpeg"), \
                patch.object(app.subprocess, "Popen") as popen:
            for source in ("%2Fetc%2Fpasswd", "file%3A%2F%2F%2Fetc%2Fpasswd", "concat%3Aa%7Cb",
                           "%2Fapi%2Foffline%2Fmedia%2F00000000000000000000000000000000"):
                with self.subTest(source=source):
                    handler = self._get(f"/api/remux?url={source}&ss=0")
                    self.assertEqual(handler.send_json.call_args.kwargs.get("status"), 400)
            popen.assert_not_called()

    def test_offline_film_can_be_analysed_and_sent_to_the_external_player(self):
        download_id = "8562009d895e48baa64b51e301d49556"
        with tempfile.TemporaryDirectory() as directory:
            film = Path(directory) / "film.mkv"
            film.write_bytes(b"\x1aE\xdf\xa3")

            def post(path, body):
                handler = object.__new__(app.RequestHandler)
                handler.path = path
                handler._request_allowed = Mock(return_value=True)
                handler.read_json_body = Mock(return_value=body)
                handler.send_json = Mock()
                handler.do_POST()
                return handler

            with patch.object(app.offline_library.library, "media_path", return_value=film), \
                    patch.object(app.media_probe, "inspect_media",
                                 return_value={"audio": [], "video": [{"codec": "hevc"}]}) as inspect, \
                    patch.object(app, "launch_mpv", return_value={"mode": "kino"}) as mpv, \
                    patch.object(app, "get_resume_position", return_value=0):
                probed = post("/api/probe-media", {"url": f"/api/offline/media/{download_id}"})
                post("/api/mpv", {"url": f"/api/offline/media/{download_id}", "filename": "film.mkv"})
            self.assertEqual(inspect.call_args.args[0], str(film))
            self.assertTrue(probed.send_json.call_args.args[0]["ok"])
            self.assertEqual(mpv.call_args.args[0], str(film))

    def test_lan_cannot_access_private_api_without_pairing(self):
        handler = object.__new__(app.RequestHandler)
        handler.client_address = ("192.168.1.4", 12345)
        handler.headers = {"Host": "127.0.0.1:8080"}
        handler.server = SimpleNamespace(server_port=8080)
        self.assertFalse(handler._request_allowed("/api/config", {}))
        self.assertFalse(handler._request_allowed("/api/remote/control", {}))
        self.assertTrue(handler._request_allowed(
            "/api/remote/control", {"token": app.REMOTE_PAIRING_TOKEN}))

        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {"Origin": "https://untrusted.example"}
        self.assertFalse(handler._request_allowed("/api/config", {}))
        handler.headers = {"Host": "127.0.0.1:8080", "Origin": "http://127.0.0.1:8080"}
        self.assertTrue(handler._request_allowed("/api/config", {}))
        handler.headers = {"Host": "attacker.example"}
        self.assertFalse(handler._request_allowed("/api/config", {}))


if __name__ == "__main__":
    unittest.main()
