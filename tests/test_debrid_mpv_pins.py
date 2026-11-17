"""Debrid and the external player, pinned: what rd_debrid_magnet asks each
service and answers, and the exact command launch_mpv starts for mpv, IINA and
VLC. Written on KINO 1.1.81 before these functions were split;
the code behind them may be rearranged, the answers may not change."""
import pathlib
import sys
import unittest
from unittest.mock import patch

import debrid_engine
import player_engine


class Debrid(unittest.TestCase):
    def run_magnet(self, prov, answers, *, token="tok", magnet="magnet:?xt=urn:btih:abc", resolve_url=None, season=None, episode=None):
        calls = []

        def http(url, method="GET", data=None, json_data=None, headers=None, timeout=20, retries=3):
            calls.append((url.split("?")[0], method, data))
            for key, answer in answers.items():
                if key in url:
                    value = answer.pop(0) if isinstance(answer, list) else answer
                    if isinstance(value, Exception):
                        raise value
                    return value
            return {}

        self.calls = calls
        with patch.object(debrid_engine, "_get_provider_and_token", lambda t, p: (prov, token)), \
                patch.object(debrid_engine, "http_json", side_effect=http), patch.object(debrid_engine.time, "sleep") as sleep, \
                patch.object(debrid_engine, "resolve_torrentio_rd_url", return_value={"ready": True, "via": "resolve"}) as resolve, \
                patch.object(debrid_engine, "_alldebrid_unlock_magnet", return_value={"ready": True, "via": "ad"}) as unlock, \
                patch.object(debrid_engine, "rd_check_torrent", return_value={"ready": True, "via": "check"}) as check, \
                patch.object(debrid_engine, "unrestrict_torrent_links", return_value={"ready": True, "via": "rd"}) as unrestrict:
            self.helpers = dict(sleep=sleep, resolve=resolve, unlock=unlock, check=check, unrestrict=unrestrict)
            return debrid_engine.rd_debrid_magnet("tok", magnet, season=season, episode=episode, resolve_url=resolve_url, provider=prov)

    def test_a_missing_key_and_a_resolve_link(self):
        with self.assertRaisesRegex(RuntimeError, r"^Clé API AllDebrid manquante\. Cliquez sur 'Config' en haut à droite\.$"):
            self.run_magnet("alldebrid", {}, token="")
        self.assertEqual(self.run_magnet("realdebrid", {}, resolve_url="https://torrentio/r"), {"ready": True, "via": "resolve"})
        self.assertEqual(self.run_magnet("realdebrid", {}, magnet="https://host/f"), {"ready": True, "via": "resolve"})
        self.assertEqual(self.calls, [])

    def test_real_debrid_adds_selects_waits_then_unrestricts(self):
        info = {"files": [{"id": 1, "path": "/a.mkv", "bytes": 4_000_000_000}, {"id": 2, "path": "/b.txt", "bytes": 10},
                          {"id": 3, "path": "/c.mp4", "bytes": 30 * 1024 * 1024}]}
        result = self.run_magnet("realdebrid", {"addMagnet": {"id": "RDX"}, "torrents/info": [dict(info), dict(info, status="downloading"),
                                                                                                 dict(info, status="downloaded")]},
                                 season=1, episode=2)
        self.assertEqual(result, {"ready": True, "via": "rd"})
        self.assertEqual([c[0].rsplit("/", 2)[-2:] for c in self.calls],
                         [["torrents", "addMagnet"], ["info", "RDX"], ["selectFiles", "RDX"], ["info", "RDX"], ["info", "RDX"]])
        self.assertEqual(self.calls[2][2], {"files": "1,3"})
        self.assertEqual(self.helpers["sleep"].call_count, 1)
        args = self.helpers["unrestrict"].call_args.args
        self.assertEqual((args[0], args[1]["status"], args[2], args[3]), ("tok", "downloaded", 1, 2))

    def test_real_debrid_not_ready_after_eight_checks_and_refused(self):
        stuck = {"files": [], "status": "downloading", "progress": 12, "speed": 2_000_000, "seeders": 7}
        result = self.run_magnet("realdebrid", {"addMagnet": {"id": "RDX"}, "torrents/info": stuck})
        self.assertEqual(result, {"ready": False, "torrent_id": "RDX", "status": "downloading", "progress": 12,
                                  "speed": "1.91 MB/s", "seeders": 7, "files": []})
        self.assertEqual(self.helpers["sleep"].call_count, 8)
        self.assertNotIn("selectFiles", " ".join(c[0] for c in self.calls), "no file list, nothing to select")
        with self.assertRaisesRegex(RuntimeError, r"Real-Debrid a refusé ce torrent \(statut : dead\)"):
            self.run_magnet("realdebrid", {"addMagnet": {"id": "RDX"}, "torrents/info": [{"files": []}, {"status": "dead"}]})

    def test_alldebrid(self):
        ready = self.run_magnet("alldebrid", {"upload": {"data": {"magnets": [{"id": 7}]}},
                                              "status": {"data": {"magnets": [{"statusCode": 4, "id": 7}]}}}, season=1, episode=2)
        self.assertEqual(ready, {"ready": True, "via": "ad"})
        self.assertEqual(self.helpers["unlock"].call_args.args, ("tok", {"statusCode": 4, "id": 7}, 1, 2))
        pending = self.run_magnet("alldebrid", {"upload": {"data": {"magnets": [{"id": 7}]}},
                                                "status": {"data": {"magnets": {"statusCode": 1, "status": "Downloading", "downloaded": 250,
                                                                                "size": 1000, "downloadSpeed": 1024, "seeders": 3}}}})
        self.assertEqual(pending, {"ready": False, "torrent_id": "ad:7", "status": "Downloading", "progress": 25, "speed": "1.00 KB/s",
                                   "seeders": 3, "files": []})
        self.assertEqual(self.helpers["sleep"].call_count, 6)
        with self.assertRaisesRegex(RuntimeError, "AllDebrid erreur magnet : Error"):
            self.run_magnet("alldebrid", {"upload": {"data": {"magnets": [{"id": 7}]}}, "status": {"data": {"magnets": [{"statusCode": 6, "status": "Error"}]}}})
        with self.assertRaisesRegex(RuntimeError, "AllDebrid n'a pas pu ajouter ce magnet."):
            self.run_magnet("alldebrid", {"upload": {"data": {"magnets": []}}})

    def test_debrid_link_and_premiumize(self):
        files = [{"name": "Show.S01E03.mkv", "downloadUrl": "https://dl/3", "downloadPercent": 100, "size": 2048},
                 {"name": "Show.S01E02.mkv", "downloadUrl": "https://dl/2", "downloadPercent": 100, "size": 1024},
                 {"name": "Show.S01E04.mkv", "downloadUrl": "https://dl/4", "downloadPercent": 50, "size": 1024}]
        ready = self.run_magnet("debridlink", {"seedbox/add": {"value": {"id": "S1", "files": files}}}, season=1, episode=2)
        self.assertEqual(ready, {"ready": True, "torrent_id": "dl:S1", "status": "downloaded", "progress": 100, "files": [
            {"filename": "Show.S01E02.mkv", "filesize": "1.00 KB", "filesize_bytes": 1024, "download": "https://dl/2", "is_target_ep": True},
            {"filename": "Show.S01E03.mkv", "filesize": "2.00 KB", "filesize_bytes": 2048, "download": "https://dl/3", "is_target_ep": False}]})
        pending = self.run_magnet("debridlink", {"seedbox/add": {"value": {"id": "S1", "files": [], "downloadPercent": 40,
                                                                           "downloadSpeed": 2048, "peersConnected": 5}}})
        self.assertEqual(pending, {"ready": False, "torrent_id": "dl:S1", "status": "Mise en cache Debrid-Link", "progress": 40,
                                   "speed": "2.00 KB/s", "seeders": 5, "files": []})
        content = [{"path": "f/Film.mkv", "link": "https://pm/l", "size": 4096}, {"path": "f/readme.txt", "stream_link": "https://pm/t"}]
        self.assertEqual(self.run_magnet("premiumize", {"directdl": {"content": content}}), {
            "ready": True, "torrent_id": "pm_direct", "status": "downloaded", "progress": 100,
            "files": [{"filename": "Film.mkv", "filesize": "4.00 KB", "filesize_bytes": 4096, "download": "https://pm/l", "is_target_ep": False}]})
        with self.assertRaisesRegex(RuntimeError, "pas encore en cache instantané sur Premiumize"):
            self.run_magnet("premiumize", {"directdl": {"content": []}})

    def test_torbox_and_mega_debrid(self):
        self.assertEqual(self.run_magnet("torbox", {"createtorrent": {"data": {"torrent_id": 99}}}, season=1, episode=2), {"ready": True, "via": "check"})
        self.assertEqual(self.helpers["check"].call_args.args, ("tok", "tb:99", 1, 2))
        self.assertEqual(self.helpers["check"].call_args.kwargs, {"provider": "torbox"})
        with self.assertRaisesRegex(RuntimeError, "^quota$"):
            self.run_magnet("torbox", {"createtorrent": {"data": {}, "detail": "quota"}})
        result = self.run_magnet("megadebrid", {"form=connexion": {"token": "MDT"}, "form=debrid": {"debridLink": "https://md/d/Film%20x.mkv?k=1"}},
                                 token="me:pw", magnet="hoster.example/f")  # an http link goes to the resolve step first
        self.assertEqual(result["files"], [{"filename": "Film x.mkv", "filesize": "Prêt (Mega-Debrid)", "filesize_bytes": 0,
                                            "download": "https://md/d/Film%20x.mkv?k=1", "is_target_ep": True}])
        with self.assertRaisesRegex(RuntimeError, "Mega-Debrid débride les liens hébergeurs directs"):
            self.run_magnet("megadebrid", {}, token="MDT")


class FakeProc:
    pid = 4242

    def wait(self):
        return 0


class ExternalPlayer(unittest.TestCase):
    def launch(self, binary, *, platform="darwin", cfg=None, exists=(), playlist=None, media=None, title="Film", start=0,
               url="https://debrid.example/film.mkv", callbacks=None, now=1000.0):
        log = []
        exists = set(exists)
        callbacks = callbacks or {}
        playlist_file = pathlib.Path("/kino-test/playlist.m3u")

        def thread(target=None, args=(), daemon=None):
            class T:
                def start(self):
                    log.append(("thread", target.__name__))
            return T()

        with patch.object(player_engine, "find_mpv", return_value=binary), \
                patch.object(player_engine, "load_config", return_value=cfg or {"pref_lang": "vf", "hdr_mode": "sdr_pref", "audio_mode": "voice_boost"}), \
                patch.object(player_engine.sys, "platform", platform), patch.object(player_engine, "PLAYLIST_FILE", playlist_file), \
                patch.object(pathlib.Path, "exists", lambda p: p.name in exists), \
                patch.object(pathlib.Path, "write_text", lambda p, text, encoding=None: log.append(("write", str(p), text))), \
                patch.object(player_engine.os.path, "exists", return_value=False), \
                patch.object(player_engine.subprocess, "run", side_effect=lambda *a, **k: log.append(("run", a[0]))), \
                patch.object(player_engine.subprocess, "Popen", side_effect=lambda args, **k: log.append(("popen", args, sorted(k))) or FakeProc()), \
                patch.object(player_engine.threading, "Thread", side_effect=thread), \
                patch.object(player_engine, "_get_window_cb", side_effect=callbacks.get), \
                patch.object(player_engine.discord_rpc.discord_rpc, "set_activity", side_effect=lambda **k: log.append(("discord", k["title"], k["current_time"]))), \
                patch.object(player_engine, "background_inject_subtitles", side_effect=lambda m: log.append(("subs", m["name"]))), \
                patch.object(player_engine, "spawn_on_user_desktop", side_effect=lambda a: log.append(("spawn", a)) or 777), \
                patch.object(player_engine.time, "time", return_value=now):
            player_engine._LAST_MPV_LAUNCH_TIME, player_engine._LAST_MPV_LAUNCH_URL, player_engine._LAST_MPV_LAUNCH_RES = 0.0, "", None
            result = player_engine.launch_mpv(url, title, playlist_items=playlist, media_ctx=media, start_sec=start)
        return result, log

    @unittest.skipIf(sys.platform == "win32", "simulates a Mac or Linux: their paths read with « / »")
    def test_mpv_on_a_mac_with_an_hdr_film_and_a_resume_point(self):
        media = {"name": "Film", "type": "movie", "filename": "Film.2160p.HDR.mkv"}
        result, log = self.launch("/opt/homebrew/bin/mpv", media=media, start=120.7, title='Le "Film"')
        popen = next(entry for entry in log if entry[0] == "popen")
        self.assertEqual(popen[1], [
            "/opt/homebrew/bin/mpv", "--hwdec=videotoolbox", f"--input-ipc-server={player_engine.IPC_SOCK_PATH}",
            "--input-conf=/tmp/kino_mpv_input.conf", "--alang=fre,fra,fr,eng", "--slang=fre,fra,fr,eng", "--no-border", "--border=no",
            "--no-window-dragging", "--window-dragging=no", "--title=KINO", "--force-window=immediate",
            "--af=lavfi=[dynaudnorm=f=180:g=13:p=0.92:m=4.5]", "--tone-mapping=bt.2446a", "--hdr-compute-peak=yes", "--gamma=6",
            "--brightness=2", "--start=120", "--force-media-title=Le 'Film'", "https://debrid.example/film.mkv"])
        self.assertEqual(popen[2], ["start_new_session", "stderr", "stdin", "stdout"])
        self.assertEqual(result, {"mpv": "/opt/homebrew/bin/mpv", "pid": 4242, "playlist_count": 1})
        self.assertIn(("discord", "Film", 120.7), log)
        self.assertEqual([entry[1] for entry in log if entry[0] == "thread"], ["monitor_ipc_playback", "_wait"])
        self.assertIn(("subs", "Film"), log)
        self.assertEqual(log[0], ("write", "/tmp/kino_mpv_input.conf",
                                  's seek 85 exact ; show-text "Intro passée (+85s)"\nS seek 85 exact ; show-text "Intro passée (+85s)"\n'))

    @unittest.skipIf(sys.platform == "win32", "simulates a Mac or Linux: their paths read with « / »")
    def test_iina_with_its_command_line_tool_and_a_playlist(self):
        playlist = [{"title": "Ep\n1", "url": " https://a/1 "}, {"title": None, "url": ""}, {"title": "Ep 3", "url": "https://a/3"}]
        cfg = {"pref_lang": "vostfr", "hdr_mode": "hdr_boost", "audio_mode": "standard"}
        result, log = self.launch("/Applications/IINA.app/Contents/MacOS/IINA", exists={"iina-cli"}, playlist=playlist, cfg=cfg, start=3)
        self.assertIn(("run", ["defaults", "write", "com.colliderli.iina", "enableToneMapping", "-bool", "true"]), log)
        self.assertIn(("write", "/kino-test/playlist.m3u", "#EXTM3U\n#EXTINF:-1,Ep 1\nhttps://a/1\n#EXTINF:-1,Ep 3\nhttps://a/3\n"), log)
        popen = next(entry for entry in log if entry[0] == "popen")
        self.assertEqual(popen[1], [
            "/Applications/IINA.app/Contents/MacOS/iina-cli", "--no-stdin", "--keep-running", "--mpv-hwdec=videotoolbox",
            f"--mpv-input-ipc-server={player_engine.IPC_SOCK_PATH}", "--mpv-alang=eng,ja,jpn,fre,fra,fr", "--mpv-slang=fre,fra,fr,eng",
            "--mpv-input-conf=/tmp/kino_mpv_input.conf", "--mpv-tone-mapping=bt.2446a", "--mpv-hdr-compute-peak=yes", "--mpv-gamma=9",
            "--mpv-brightness=3", "--mpv-contrast=2", "/kino-test/playlist.m3u"])
        self.assertEqual((result["mpv"], result["playlist_count"]), ("/Applications/IINA.app/Contents/MacOS/iina-cli", 3))

    @unittest.skipIf(sys.platform == "win32", "simulates a Mac or Linux: their paths read with « / »")
    def test_vlc_gets_only_the_media_and_hdr_native_adds_nothing(self):
        result, log = self.launch("/Applications/VLC.app/Contents/MacOS/VLC", cfg={"hdr_mode": "hdr_native"})
        self.assertEqual(next(entry for entry in log if entry[0] == "popen")[1], ["/Applications/VLC.app/Contents/MacOS/VLC", "https://debrid.example/film.mkv"])
        result, log = self.launch("/usr/bin/mpv", platform="linux", cfg={"hdr_mode": "hdr_native", "audio_mode": "standard"},
                                  exists={"portable_config"}, callbacks={"GET_WINDOW_GEOMETRY": lambda: {"width": 800, "height": 450, "x": 10, "y": 20}})
        args = next(entry for entry in log if entry[0] == "popen")[1]
        self.assertEqual(args[:2], ["/usr/bin/mpv", "--config-dir=/usr/bin/portable_config"])
        self.assertNotIn("--hwdec=videotoolbox", args)
        self.assertFalse(any(a.startswith(("--tone-mapping", "--af=")) for a in args))
        self.assertEqual(args[-3:], ["--geometry=800x450+10+20", "--force-media-title=Film", "https://debrid.example/film.mkv"])

    def test_windows_docked_or_on_the_user_desktop(self):
        docks = []
        callbacks = {"GET_FORM_HWND": lambda: 123, "DOCK_MPV_WINDOW": lambda pid: docks.append(pid)}
        result, log = self.launch("C:/mpv/mpv.exe", platform="win32", callbacks=callbacks, exists={"kino_bridge.lua"}, now=1700000000.123)
        self.assertEqual(log[0][0], "run")
        popen = next(entry for entry in log if entry[0] == "popen")
        self.assertIn("--title=KINO_PLAYER_1700000000123", popen[1])
        self.assertTrue(any(a.startswith("--scripts=") and a.endswith("kino_bridge.lua") for a in popen[1]))
        self.assertEqual(popen[2], ["creationflags", "stderr", "stdin", "stdout"])
        self.assertEqual([entry[1] for entry in log if entry[0] == "thread"], ["<lambda>", "monitor_mpv_lifecycle"])
        self.assertEqual(result, {"mpv": "C:/mpv/mpv.exe", "pid": 4242, "playlist_count": 1})
        result, log = self.launch("C:/mpv/mpv.exe", platform="win32", callbacks={"WINDOW_ACTION_CALLBACK": lambda action: None})
        self.assertEqual(result["pid"], 777)
        self.assertTrue(any(entry[0] == "spawn" for entry in log))

    def test_a_second_launch_within_seconds_returns_the_first_and_no_player_fails(self):
        first, _ = self.launch("/opt/homebrew/bin/mpv")
        with patch.object(player_engine.time, "time", return_value=1002.0), \
                patch.object(player_engine.subprocess, "Popen") as popen:
            again = player_engine.launch_mpv("https://debrid.example/film.mkv", "Film")
        popen.assert_not_called()
        self.assertEqual(again, first)
        with self.assertRaisesRegex(RuntimeError, r"^Aucun lecteur externe compatible \(mpv, IINA ou VLC\) n'a été trouvé\.$"):
            self.launch(None)


class Structure(unittest.TestCase):
    def test_short_functions_one_per_service_and_per_player(self):
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        limits = {("debrid_engine.py", "rd_debrid_magnet"): 25, ("player_engine.py", "launch_mpv"): 60}
        for (path, name), limit in limits.items():
            node = next(n for n in ast.walk(ast.parse((root / "src" / path).read_text(encoding="utf-8")))
                        if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertLessEqual(node.end_lineno - node.lineno + 1, limit, name)
        self.assertEqual(sorted(debrid_engine.MAGNET_SERVICES), ["alldebrid", "debridlink", "megadebrid", "premiumize", "torbox"])
        doc = (root / "docs" / "dev" / "playback.md").read_text(encoding="utf-8")
        for step in ("_realdebrid_magnet", "_alldebrid_magnet", "_claim_mpv_launch", "_shared_mpv_options", "_start_elsewhere"):
            self.assertIn(f"`{step}`", doc)


if __name__ == "__main__":
    unittest.main()
