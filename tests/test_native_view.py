"""« Lecteur natif »: commands, routes, setting (no window needed)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import api_service
import app
import config
import native_player
import native_view

DOWNLOAD_ID = "8562009d895e48baa64b51e301d49556"


def post(path, body):
    handler = object.__new__(app.RequestHandler)
    handler.path = path
    handler._request_allowed = Mock(return_value=True)
    handler.read_json_body = Mock(return_value=body)
    handler.send_json = Mock()
    handler.do_POST()
    return handler.send_json.call_args


class CommandTests(unittest.TestCase):
    def test_overlay_commands_map_to_mpv(self):
        self.assertEqual(native_view.mpv_command("toggle"), ("cycle", "pause"))
        self.assertEqual(native_view.mpv_command("seek", -10), ("seek", -10.0, "relative"))
        self.assertEqual(native_view.mpv_command("goto", 1800.5), ("seek", 1800.5, "absolute"))
        self.assertEqual(native_view.mpv_command("volume", 80), ("set", "volume", 80.0))
        self.assertEqual(native_view.mpv_command("audio", "2"), ("set", "aid", "2"))
        self.assertEqual(native_view.mpv_command("subtitles", "no"), ("set", "sid", "no"))
        self.assertEqual(native_view.mpv_command("subtitles", 3), ("set", "sid", "3"))

    def test_anything_else_is_refused(self):
        for cmd, value in (("loadfile", "/etc/passwd"), ("run", "sh"), ("volume", 500), ("goto", -5),
                           ("audio", "no"), ("audio", "1; quit"), ("seek", "abc")):
            with self.subTest(cmd=cmd, value=value), self.assertRaises((ValueError, TypeError)):
                native_view.mpv_command(cmd, value)

    def test_language_follows_the_one_click_setting(self):
        self.assertEqual(native_view.language_options("vf"), {"alang": "fr,fre,fra,french"})
        self.assertEqual(native_view.language_options("vostfr"), {"slang": "fr,fre,fra,french"})


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.controller = Mock()
        self.controller.open.return_value = {"open": True}
        self.controller.command.return_value = {"open": True}
        self.controller.close.return_value = {"open": False}
        patcher = patch.object(config, "NATIVE_PLAYER", self.controller)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_offline_film_opens_from_its_file_with_resume_and_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            film = Path(directory) / "film.mkv"
            film.write_bytes(b"\x1aE\xdf\xa3")
            with patch.object(app.offline_library.library, "media_path", return_value=film), \
                    patch.object(app, "load_config", return_value={"pref_lang": "vostfr"}):
                result = post("/api/native-player/open", {"offline_id": DOWNLOAD_ID, "start": 611.4, "volume": 80})
        self.assertTrue(result.args[0]["ok"])
        self.controller.open.assert_called_once_with(str(film), start=611.4, volume=80, pref_lang="vostfr")

    def test_unknown_film_or_bad_values_never_reach_mpv(self):
        with patch.object(app.offline_library.library, "media_path", return_value=None):
            self.assertEqual(post("/api/native-player/open", {"offline_id": DOWNLOAD_ID}).kwargs["status"], 404)
        for body in ({"offline_id": "../../etc"}, {"offline_id": "/etc/passwd"}, {}):
            self.assertEqual(post("/api/native-player/open", body).kwargs["status"], 404)
        self.controller.open.assert_not_called()
        self.controller.command.side_effect = ValueError("unknown")
        self.assertEqual(post("/api/native-player/command", {"cmd": "run", "value": "sh"}).kwargs["status"], 400)

    def test_commands_and_close_are_forwarded(self):
        self.assertTrue(post("/api/native-player/command", {"cmd": "seek", "value": 10}).args[0]["ok"])
        self.controller.command.assert_called_once_with("seek", 10)
        self.assertEqual(post("/api/native-player/close", {}).args[0]["state"], {"open": False})

    def test_without_native_player_the_routes_say_so(self):
        with patch.object(config, "NATIVE_PLAYER", None):
            self.assertEqual(post("/api/native-player/open", {"offline_id": DOWNLOAD_ID}).kwargs["status"], 503)


class SettingTests(unittest.TestCase):
    def test_setting_is_a_strict_boolean(self):
        self.assertEqual(config.sanitize_config_payload({"native_player": True}), {"native_player": True})
        self.assertEqual(config.sanitize_config_payload({"native_player": "yes"}), {})

    def test_config_reports_setting_and_availability(self):
        with patch.object(config, "load_config", return_value={"native_player": True}), \
                patch.object(config, "NATIVE_PLAYER", object()):
            response = api_service.build_config_response()
        self.assertTrue(response["native_player"])
        self.assertTrue(response["native_player_available"])
        with patch.object(config, "load_config", return_value={}), patch.object(config, "NATIVE_PLAYER", None):
            response = api_service.build_config_response()
        self.assertFalse(response["native_player"])
        self.assertFalse(response["native_player_available"])



class OptionCommandTests(unittest.TestCase):
    def test_settings_map_to_mpv_properties(self):
        self.assertEqual(native_view.mpv_command("speed", 1.25), ("set", "speed", 1.25))
        self.assertEqual(native_view.mpv_command("sub_scale", "1.5"), ("set", "sub-scale", 1.5))
        self.assertEqual(native_view.mpv_command("voice", "boost"), ("set", "af", native_view.VOICE_FILTER))
        self.assertEqual(native_view.mpv_command("voice", "normal"), ("set", "af", ""))
        self.assertEqual(native_view.mpv_command("clarity", 0), [("set", "gamma", 0), ("set", "brightness", 0), ("set", "contrast", 0)])
        self.assertEqual(native_view.mpv_command("clarity", "3"), [("set", "gamma", 32), ("set", "brightness", 9), ("set", "contrast", 4)])

    def test_out_of_range_settings_are_refused(self):
        for cmd, value in (("speed", 10), ("speed", 0), ("sub_scale", 9), ("voice", "lavfi=[x]"), ("voice", None),
                           ("clarity", 1.5), ("clarity", 4), ("clarity", -1)):
            with self.subTest(cmd=cmd, value=value), self.assertRaises((ValueError, TypeError)):
                native_view.mpv_command(cmd, value)

    def test_cues_become_srt(self):
        srt = native_view.cues_to_srt([{"start": 1.5, "end": 3, "text": "Bonjour"}, {"start": 3661.25, "end": 3662, "text": "Ça va ?"},
                                       {"start": 5, "end": 4, "text": "à l'envers"}, {"start": 7, "text": "incomplet"}, {"start": 8, "end": 9, "text": "  "}])
        self.assertEqual(srt, "1\n00:00:01,500 --> 00:00:03,000\nBonjour\n\n2\n01:01:01,250 --> 01:01:02,000\nÇa va ?\n")
        self.assertEqual(native_view.cues_to_srt(None), "")

    def test_subtitle_bytes_are_decoded(self):
        self.assertEqual(native_view.decode_subtitle("é".encode("utf-8")), "é")
        self.assertEqual(native_view.decode_subtitle("﻿é".encode("utf-8")), "é")
        self.assertEqual(native_view.decode_subtitle("é".encode("latin-1")), "é")

    def test_frame_capture_uses_ffmpeg_at_the_position(self):
        with tempfile.TemporaryDirectory() as directory:
            film = Path(directory) / "film.mkv"
            film.write_bytes(b"x")
            out = Path(directory) / "shot.png"
            calls = []

            def run(argv, **kwargs):
                calls.append((argv, kwargs))
                out.write_bytes(b"png")

            self.assertEqual(native_view.extract_frame(film, 83.5, out, ffmpeg="/bin/ffmpeg", run=run), out)
            argv, kwargs = calls[0]
            self.assertEqual(argv, ["/bin/ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "83.500", "-i", str(film),
                                    "-frames:v", "1", "-y", str(out)])
            self.assertTrue(kwargs["check"])
            with self.assertRaises(RuntimeError):
                native_view.extract_frame(Path(directory) / "missing.mkv", 1, out, ffmpeg="/bin/ffmpeg", run=run)


class FakeMpv:
    def __init__(self, loaded=True, screenshot_fails=False):
        self.commands, self.loaded, self.screenshot_fails = [], loaded, screenshot_fails
        self.props = {"speed": 1.25, "sub-scale": 1.0, "time-pos": 42.0}

    def command(self, *args):
        if args[0] == "screenshot-to-file" and self.screenshot_fails:
            raise native_player.MpvError("screenshot-to-file", -12)
        self.commands.append(args)

    def get_double(self, name):
        if name == "duration":
            return 120.0 if self.loaded else None
        if name == "track-list/count":
            return 0
        return self.props.get(name)

    def get_flag(self, name):
        return False

    def get_string(self, name):
        return None


class ControllerTests(unittest.TestCase):
    def controller(self, player):
        controller = native_view.NativePlayerController(lambda: (None, None))
        controller._player = player
        return controller

    def test_clarity_runs_every_step_and_settings_show_in_state(self):
        player = FakeMpv()
        controller = self.controller(player)
        state = controller.command("clarity", 2)
        self.assertEqual(player.commands, [("set", "gamma", 22), ("set", "brightness", 6), ("set", "contrast", 3)])
        state = controller.command("voice", "boost")
        self.assertEqual((state["clarity"], state["voice"], state["speed"], state["sub_scale"]), (2, "boost", 1.25, 1.0))

    def test_subtitles_wait_for_the_film_then_go_to_mpv(self):
        player = FakeMpv(loaded=False)
        controller = self.controller(player)
        controller.add_subtitle("1\n00:00:01,000 --> 00:00:02,000\nSalut\n", "OpenSubtitles", "fr")
        self.assertEqual(player.commands, [], "sub-add needs the loaded film")
        folder = Path(controller._subtitle_dir)
        written = sorted(folder.iterdir())
        self.assertEqual([path.name for path in written], ["1.srt"])
        self.assertIn("Salut", written[0].read_text(encoding="utf-8"))
        player.loaded = True
        controller.state()
        self.assertEqual(player.commands, [("sub-add", str(written[0]), "select", "OpenSubtitles", "fr")])
        controller.add_subtitle("x", "gardé avec le film", "fr", select=False)
        self.assertEqual(player.commands[-1][2], "auto")
        controller._player = None
        controller._close_locked()
        self.assertFalse(folder.exists(), "the temporary subtitles are deleted when playback ends")
        self.assertIsNone(controller._subtitle_dir)

    def test_screenshot_falls_back_to_ffmpeg_for_hardware_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "shot.png"
            controller = self.controller(FakeMpv())
            self.assertEqual(controller.screenshot(out), out)
            failing = self.controller(FakeMpv(screenshot_fails=True))
            failing._path = "/films/film.mkv"
            with patch.object(native_view, "extract_frame", return_value=out) as extract:
                self.assertEqual(failing.screenshot(out), out)
            extract.assert_called_once_with("/films/film.mkv", 42.0, out)

    def test_nothing_without_playback(self):
        controller = native_view.NativePlayerController(lambda: (None, None))
        for call in (lambda: controller.command("speed", 1), lambda: controller.screenshot("/tmp/x.png"),
                     lambda: controller.add_subtitle("x", "t", "fr")):
            with self.assertRaises(RuntimeError):
                call()


class OptionRouteTests(unittest.TestCase):
    def setUp(self):
        self.controller = Mock()
        self.controller.add_subtitle.return_value = {"open": True}
        self.controller.open.return_value = {"open": True}
        patcher = patch.object(config, "NATIVE_PLAYER", self.controller)
        patcher.start()
        self.addCleanup(patcher.stop)
        app._LISTED_SUBTITLES.clear()

    def response(self, raw):
        body = Mock()
        body.read.return_value = raw
        body.__enter__ = Mock(return_value=body)
        body.__exit__ = Mock(return_value=False)
        return body

    def test_only_subtitles_kino_listed_are_downloaded(self):
        url = "https://subs5.strem.io/fr/download/file/1"
        with patch.object(app.urllib.request, "urlopen") as urlopen:
            self.assertEqual(post("/api/native-player/subtitle", {"url": url}).kwargs["status"], 400)
            urlopen.assert_not_called()
            app.remember_listed_subtitles([{"url": url, "lang": "fr"}, {"url": "http://insecure/x", "lang": "en"}])
            self.assertIsNone(app.listed_subtitle_lang("http://insecure/x"), "plain http is never kept")
            urlopen.return_value = self.response("1\n00:00:01,000 --> 00:00:02,000\nÉté\n".encode("latin-1"))
            result = post("/api/native-player/subtitle", {"url": url, "label": "OpenSubtitles · SDH"})
        self.assertTrue(result.args[0]["ok"])
        text, label, lang = self.controller.add_subtitle.call_args.args
        self.assertIn("Été", text)
        self.assertEqual((label, lang, self.controller.add_subtitle.call_args.kwargs), ("OpenSubtitles · SDH", "fr", {"select": True}))

    def test_bad_downloads_are_reported(self):
        url = "https://subs5.strem.io/fr/download/file/2"
        app.remember_listed_subtitles([{"url": url, "lang": "fr"}])
        with patch.object(app.urllib.request, "urlopen", side_effect=app.urllib.error.URLError("down")):
            self.assertEqual(post("/api/native-player/subtitle", {"url": url}).kwargs["status"], 502)
        with patch.object(app.urllib.request, "urlopen", return_value=self.response(b"x" * (native_view.SUBTITLE_MAX_BYTES + 1))):
            self.assertEqual(post("/api/native-player/subtitle", {"url": url}).kwargs["status"], 400)
        with patch.object(app.urllib.request, "urlopen", return_value=self.response(b"<html>not a subtitle</html>")):
            self.assertEqual(post("/api/native-player/subtitle", {"url": url}).kwargs["status"], 400)
        self.controller.add_subtitle.assert_not_called()

    def test_the_list_is_bounded(self):
        app.remember_listed_subtitles([{"url": f"https://subs/{n}", "lang": "fr"} for n in range(400)])
        self.assertEqual(len(app._LISTED_SUBTITLES), 300)
        self.assertIsNone(app.listed_subtitle_lang("https://subs/0"))
        self.assertEqual(app.listed_subtitle_lang("https://subs/399"), "fr")

    def test_screenshot_goes_to_the_download_folder_with_a_safe_name(self):
        with tempfile.TemporaryDirectory() as directory:
            self.controller.screenshot.side_effect = lambda path: Path(path)
            with patch.object(app, "load_config", return_value={"download_dir": directory}):
                result = post("/api/native-player/screenshot", {"title": "../../Le Film : épisode", "time_str": "1m02s/../"})
            saved = self.controller.screenshot.call_args.args[0]
        self.assertEqual(saved.parent, Path(directory))
        self.assertEqual(saved.name, "KINO_Capture_Le_Film_pisode_1m02s.png")
        self.assertEqual(result.args[0], {"ok": True, "filename": saved.name})

    def test_offline_subtitle_follows_the_language_setting(self):
        cues = {"lang": "fr", "label": "CC : Français", "cues": [{"start": 1, "end": 2, "text": "Salut"}]}
        with tempfile.TemporaryDirectory() as directory:
            film = Path(directory) / "film.mkv"
            film.write_bytes(b"\x1aE\xdf\xa3")
            for pref, selected in (("vostfr", True), ("vf", False)):
                self.controller.add_subtitle.reset_mock()
                with patch.object(app.offline_library.library, "media_path", return_value=film), \
                        patch.object(app.offline_library.library, "subtitles", return_value=cues), \
                        patch.object(app, "load_config", return_value={"pref_lang": pref}):
                    self.assertTrue(post("/api/native-player/open", {"offline_id": DOWNLOAD_ID}).args[0]["ok"])
                text, title, lang = self.controller.add_subtitle.call_args.args
                self.assertEqual((title, lang, self.controller.add_subtitle.call_args.kwargs["select"]), ("gardé avec le film", "fr", selected))
                self.assertIn("Salut", text)
            self.controller.add_subtitle.reset_mock()
            with patch.object(app.offline_library.library, "media_path", return_value=film), \
                    patch.object(app.offline_library.library, "subtitles", return_value={}), \
                    patch.object(app, "load_config", return_value={}):
                post("/api/native-player/open", {"offline_id": DOWNLOAD_ID})
            self.controller.add_subtitle.assert_not_called()

    def test_listing_subtitles_remembers_their_addresses(self):
        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/subtitles?imdb_id=tt0111161&type=movie"
        handler._request_allowed = Mock(return_value=True)
        handler.send_json = Mock()
        with patch.object(app, "fetch_opensubtitles", return_value=[{"url": "https://subs/listed", "lang": "en"}]):
            handler.do_GET()
        self.assertEqual(app.listed_subtitle_lang("https://subs/listed"), "en")



class StreamTests(unittest.TestCase):
    def setUp(self):
        self.controller = Mock()
        self.controller.open.return_value = {"open": True}
        patcher = patch.object(config, "NATIVE_PLAYER", self.controller)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_https_or_kino_own_redirections(self):
        allowed = ["https://abc.download.real-debrid.com/d/XYZ/film.mkv",
                   f"http://127.0.0.1:{config.PORT}/api/auto-stream?imdb_id=tt1&season=1&episode=2",
                   f"http://127.0.0.1:{config.PORT}/api/ad-dl?link=https%3A%2F%2Falldebrid.com%2Ff%2Fx",
                   f"http://127.0.0.1:{config.PORT}/api/torbox-dl?torrent_id=1&file_id=2"]
        refused = ["file:///etc/passwd", "/Users/x/film.mkv", "http://example.com/x.mkv", "https://user:pw@host/x",
                   "edl://x", "lavfi://testsrc", "av://lavfi:x", "https://host/a b", "https://host/\nx", "",
                   f"http://127.0.0.1:{config.PORT}/api/config", "http://127.0.0.1:1/api/auto-stream",
                   f"http://localhost:{config.PORT}/api/auto-stream", "https://" + "a" * 4000, None, 42]
        for url in allowed:
            self.assertTrue(app.native_stream_allowed(url), url)
        for url in refused:
            self.assertFalse(app.native_stream_allowed(url), str(url)[:40])

    def test_a_debrid_source_opens_in_mpv_without_offline_subtitle(self):
        url = "https://abc.download.real-debrid.com/d/XYZ/film.mkv"
        with patch.object(app, "load_config", return_value={"pref_lang": "vostfr"}), \
                patch.object(app.RequestHandler, "add_offline_subtitle") as offline:
            result = post("/api/native-player/open", {"stream_url": url, "start": 120, "volume": 70})
        self.assertTrue(result.args[0]["ok"])
        self.controller.open.assert_called_once_with(url, start=120.0, volume=70, pref_lang="vostfr")
        offline.assert_not_called()

    def test_refused_addresses_never_reach_mpv(self):
        for url in ("file:///etc/hosts", "lavfi://testsrc", "http://example.com/x.mkv"):
            result = post("/api/native-player/open", {"stream_url": url})
            self.assertEqual(result.kwargs["status"], 400)
            self.assertEqual(result.args[0]["error"], "Adresse de lecture refusée.")
        self.controller.open.assert_not_called()

    def test_mpv_takes_direct_media_only(self):
        self.assertEqual(native_view.OPTIONS["ytdl"], "no")
        self.assertEqual(native_view.OPTIONS["load-scripts"], "no")

    def test_capture_reads_a_remote_source_too(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "shot.png"
            run = Mock(side_effect=lambda argv, **kwargs: out.write_bytes(b"png"))
            native_view.extract_frame("https://host/film.mkv", 10, out, ffmpeg="/bin/ffmpeg", run=run)
            self.assertIn("https://host/film.mkv", run.call_args.args[0])
            with self.assertRaises(RuntimeError):
                native_view.extract_frame("ftp://host/film.mkv", 10, out, ffmpeg="/bin/ffmpeg", run=run)

    def test_state_says_when_mpv_is_idle(self):
        player = FakeMpv()
        player.get_flag = lambda name: name == "idle-active"
        controller = native_view.NativePlayerController(lambda: (None, None))
        controller._player = player
        self.assertTrue(controller.state()["idle"])



class DefaultTests(unittest.TestCase):
    def test_native_player_is_on_by_default_where_it_exists(self):
        with patch.object(config, "NATIVE_PLAYER", object()):
            for saved, expected in (({}, True), ({"native_player": False}, False), ({"native_player": True}, True)):
                with patch.object(config, "load_config", return_value=saved):
                    self.assertIs(api_service.build_config_response()["native_player"], expected, saved)
        with patch.object(config, "NATIVE_PLAYER", None), patch.object(config, "load_config", return_value={}):
            self.assertFalse(api_service.build_config_response()["native_player"], "never on without the native player")


if __name__ == "__main__":
    unittest.main()
