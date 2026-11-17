"""Playback without a debrid key: an add-on's direct link as it is, a torrent read
peer to peer once the user agreed, served to the player on this computer only."""
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import kino_db
import one_click
import p2p_engine
from test_playback_recovery import handler_for

HASH = "0123456789abcdef0123456789abcdef01234567"


class Database(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        database = patch.object(kino_db, "DB_PATH", self.folder / "kino.db")
        database.start()
        self.addCleanup(database.stop)
        kino_db.init_db()
        self.addCleanup(p2p_engine.shutdown)


class ConsentTests(Database):
    def test_asked_once_and_withdrawn(self):
        self.assertIsNone(p2p_engine.consent())
        agreed = p2p_engine.set_consent(True, "normal")
        self.assertEqual(p2p_engine.settings()["upload"], "normal")
        self.assertEqual(p2p_engine.consent(), agreed)
        p2p_engine.set_consent(True, "everything")
        self.assertEqual(p2p_engine.settings()["upload"], "minimum", "an unknown choice keeps the minimum")
        self.assertIsNone(p2p_engine.set_consent(False))
        self.assertIsNone(p2p_engine.consent())
        self.assertEqual(p2p_engine.settings()["upload"], "minimum", "the choices outlive the consent")

    def test_the_settings(self):
        self.assertEqual(p2p_engine.settings(), {"upload": "minimum", "download": "none", "encrypted": False, "port": 0})
        saved = p2p_engine.save_settings({"download": "5m", "encrypted": True, "port": "51413"})
        self.assertEqual(saved, {"upload": "minimum", "download": "5m", "encrypted": True, "port": 51413})
        self.assertEqual(p2p_engine.settings(), saved)
        self.assertEqual(p2p_engine.save_settings({"port": ""})["port"], 0, "empty: chosen by the system")
        for bad in ({"upload": "all"}, {"download": "fast"}, {"port": 80}, {"port": 70000}, {"port": "x"}):
            with self.assertRaises(p2p_engine.P2PSettingsError, msg=bad):
                p2p_engine.save_settings(bad)
        self.assertEqual(p2p_engine.settings()["download"], "5m", "a refused change keeps the rest")

    def test_an_upload_choice_from_before_the_settings(self):
        kino_db.save_config(p2p_engine.CONSENT_KEY, {"accepted_at": 1, "upload": "normal"})
        self.assertEqual(p2p_engine.settings()["upload"], "normal")
        p2p_engine.set_consent(True)
        self.assertEqual(p2p_engine.settings()["upload"], "normal", "kept when the consent is saved again")

    def test_what_libtorrent_is_told(self):
        import libtorrent as lt
        plain = p2p_engine.session_settings(p2p_engine.DEFAULTS)
        self.assertEqual((plain["upload_rate_limit"], plain["download_rate_limit"], plain["listen_interfaces"]),
                         (64 * 1024, 0, "0.0.0.0:0,[::]:0"))
        self.assertEqual(plain["out_enc_policy"], int(lt.enc_policy.pe_enabled))
        chosen = p2p_engine.session_settings({"upload": "normal", "download": "2m", "encrypted": True, "port": 51413})
        self.assertEqual((chosen["upload_rate_limit"], chosen["download_rate_limit"], chosen["listen_interfaces"]),
                         (0, 2 * 1024 * 1024, "0.0.0.0:51413,[::]:51413"))
        self.assertEqual((chosen["out_enc_policy"], chosen["in_enc_policy"], chosen["allowed_enc_level"]),
                         (int(lt.enc_policy.pe_forced), int(lt.enc_policy.pe_forced), int(lt.enc_level.pe_rc4)))

    def test_a_running_session_takes_the_new_choices(self):
        import libtorrent as lt
        p2p_engine._ENGINE = p2p_engine.Engine(folder=self.folder / "cache")
        p2p_engine.save_settings({"download": "2m", "encrypted": True})
        running = p2p_engine._ENGINE.session.get_settings()
        self.assertEqual((running["download_rate_limit"], running["out_enc_policy"]), (2 * 1024 * 1024, int(lt.enc_policy.pe_forced)))

    def test_clearing_the_temporary_files(self):
        cache = self.folder / "P2P"
        (cache / "Film").mkdir(parents=True)
        (cache / "Film" / "Film.mkv").write_bytes(b"x" * 1000)
        with patch.object(p2p_engine, "cache_dir", return_value=cache):
            self.assertEqual(p2p_engine.state()["cache_bytes"], 1000)
            p2p_engine.clear_cache()
            self.assertFalse(cache.exists())
            self.assertEqual(p2p_engine.state()["cache_bytes"], 0)

    def test_a_torrent_waits_for_consent_a_direct_link_does_not(self):
        with self.assertRaises(p2p_engine.P2PConsentNeeded):
            p2p_engine.prepare("", f"magnet:?xt=urn:btih:{HASH}", base_url="http://127.0.0.1:8080")
        link = "https://cdn.example/files/Film.2024.1080p.mkv?sig=x"
        self.assertEqual(p2p_engine.prepare("", "", resolve_url=link, base_url="http://127.0.0.1:8080"),
                         {"ready": True, "files": [{"download": link, "filename": "Film.2024.1080p.mkv"}]})

    def test_one_click_chooses_its_reader(self):
        torrent = {"info_hash": HASH, "magnet": f"magnet:?xt=urn:btih:{HASH}"}
        direct = {"resolve_url": "https://cdn.example/f.mkv"}
        self.assertIs(app.one_click_reader("key", [torrent]), app.rd_debrid_magnet)
        self.assertIsNone(app.one_click_reader("", [torrent]), "torrents only, no consent yet")
        self.assertTrue(callable(app.one_click_reader("", [torrent, direct])))
        p2p_engine.set_consent(True)
        self.assertTrue(callable(app.one_click_reader("", [torrent])))

    def test_without_a_key_play_asks_for_consent(self):
        handler = handler_for("/api/one-click-play", {"imdb_id": "tt1", "type": "movie"})
        torrent = {"info_hash": HASH, "magnet": f"magnet:?xt=urn:btih:{HASH}", "title": "Film.2024.1080p"}
        with patch.object(app, "load_config", return_value={}), patch.object(app, "get_media_meta", return_value={}), \
                patch.object(app, "get_available_streams", return_value=[torrent]), \
                patch.object(p2p_engine, "available", return_value=True):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[0]["code"], "p2p_consent")
        self.assertEqual(handler.send_json.call_args.kwargs, {"status": 409})

    def test_the_routes(self):
        handler = object.__new__(app.RequestHandler)
        handler.send_json = Mock()
        handler.post_p2p_consent(None, {}, {"accept": True, "upload": "normal"})
        self.assertEqual({k: v for k, v in handler.send_json.call_args.args[0].items() if k not in ("available", "cache_bytes")},
                         {"accepted": True, "upload": "normal", "download": "none", "encrypted": False, "port": 0})
        handler.post_p2p_consent(None, {}, {"accept": "yes"})
        self.assertFalse(handler.send_json.call_args.args[0]["accepted"], "only true agrees")
        handler.post_p2p_settings(None, {}, {"download": "10m", "accepted": True})
        self.assertEqual(handler.send_json.call_args.args[0]["download"], "10m")
        self.assertFalse(handler.send_json.call_args.args[0]["accepted"], "settings do not agree for the user")
        handler.post_p2p_settings(None, {}, {"port": 22})
        self.assertEqual(handler.send_json.call_args.kwargs, {"status": 400})


class HelperTests(unittest.TestCase):
    def test_the_info_hash(self):
        self.assertEqual(p2p_engine.info_hash_of(f"magnet:?xt=urn:btih:{HASH.upper()}&dn=x"), HASH)
        self.assertEqual(p2p_engine.info_hash_of("", f"https://torrentio.example/resolve/{HASH}/1/f.mkv"), HASH)
        self.assertEqual(p2p_engine.info_hash_of("magnet:?xt=urn:btih:short"), "")

    def test_byte_ranges(self):
        self.assertIsNone(p2p_engine._byte_range(None, 100))
        self.assertEqual(p2p_engine._byte_range("bytes=10-19", 100), (10, 19))
        self.assertEqual(p2p_engine._byte_range("bytes=90-", 100), (90, 99))
        self.assertEqual(p2p_engine._byte_range("bytes=-5", 100), (95, 99))
        self.assertEqual(p2p_engine._byte_range("bytes=150-", 100), (100, 100), "past the end: 416")
        self.assertIsNone(p2p_engine._byte_range("pages=1-2", 100))

    def test_the_file_route_is_for_this_computer_only(self):
        self.assertTrue(app._P2P_FILE_ROUTE.fullmatch(f"/p2p/{HASH}/3"))
        self.assertIsNone(app._P2P_FILE_ROUTE.fullmatch(f"/p2p/{HASH}/../x"))
        handler = object.__new__(app.RequestHandler)
        handler.client_address = ("192.168.1.20", 5000)
        handler.headers = {"Host": "192.168.1.10:8080"}
        self.assertFalse(handler._request_allowed(f"/p2p/{HASH}/0", {}))


class FakeRequest:
    def __init__(self, byte_range=None):
        self.headers = {"Range": byte_range} if byte_range else {}
        self.wfile = io.BytesIO()
        self.status, self.sent = None, {}

    def send_response(self, code):
        self.status = code

    def send_header(self, name, value):
        self.sent[name] = value

    def end_headers(self):
        pass

    def send_error(self, code):
        self.status = code


@unittest.skipUnless(p2p_engine.available(), "libtorrent is not installed")
class LoopbackTests(Database):
    """A file seeded on this computer, read back through KINO's engine: no internet."""

    def test_a_torrent_plays_byte_for_byte(self):
        import libtorrent as lt
        seed_dir = self.folder / "seed"
        seed_dir.mkdir()
        payload = os.urandom(3 * 1024 * 1024 + 4321)
        (seed_dir / "Film.Test.2026.mkv").write_bytes(payload)
        (seed_dir / "notes.txt").write_text("x")
        files = lt.file_storage()
        lt.add_files(files, str(seed_dir))
        torrent = lt.create_torrent(files, 64 * 1024)
        lt.set_piece_hashes(torrent, str(seed_dir.parent))
        info = lt.torrent_info(lt.bencode(torrent.generate()))
        seeder = lt.session({"listen_interfaces": "127.0.0.1:0", "enable_dht": False, "enable_lsd": False, "alert_mask": 0})
        params = lt.add_torrent_params()
        params.ti, params.save_path = info, str(seed_dir.parent)
        seeder.add_torrent(params)
        time.sleep(0.5)
        info_hash = str(info.info_hashes().v1)
        magnet = f"magnet:?xt=urn:btih:{info_hash}&x.pe=127.0.0.1:{seeder.listen_port()}"

        p2p_engine.set_consent(True, "minimum")
        p2p_engine._ENGINE = p2p_engine.Engine(folder=self.folder / "cache")
        p2p_engine._ENGINE.session.apply_settings({"enable_dht": False, "listen_interfaces": "127.0.0.1:0"})
        answer = p2p_engine.prepare("", magnet, base_url="http://127.0.0.1:8080")
        self.assertTrue(answer["ready"])
        self.assertEqual([(f["filename"], f["filesize_bytes"]) for f in answer["files"]], [("Film.Test.2026.mkv", len(payload))],
                         "videos only")
        index = int(answer["files"][0]["download"].rsplit("/", 1)[1])
        self.assertEqual(answer["files"][0]["download"], f"http://127.0.0.1:8080/p2p/{info_hash}/{index}")

        whole = FakeRequest()
        p2p_engine.serve(whole, info_hash, index)
        self.assertEqual((whole.status, whole.sent["Content-Length"], whole.sent["Accept-Ranges"]), (200, str(len(payload)), "bytes"))
        self.assertEqual(whole.wfile.getvalue(), payload)
        part = FakeRequest("bytes=1000000-1000999")
        p2p_engine.serve(part, info_hash, index)
        self.assertEqual((part.status, part.sent["Content-Range"]), (206, f"bytes 1000000-1000999/{len(payload)}"))
        self.assertEqual(part.wfile.getvalue(), payload[1000000:1001000])
        for bad in (("f" * 40, index), (info_hash, 99)):
            missing = FakeRequest()
            p2p_engine.serve(missing, *bad)
            self.assertEqual(missing.status, 404)

        p2p_engine.shutdown()
        self.assertEqual(list((self.folder / "cache").rglob("*.mkv")), [], "nothing left on disk")


if __name__ == "__main__":
    unittest.main()
