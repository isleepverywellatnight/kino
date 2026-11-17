import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import kino_db
import meta_engine
import series_playback


class SeriesPlaybackTests(unittest.TestCase):
    def test_two_series_persist_independently_and_reset_without_touching_global_setting(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(kino_db, "DB_PATH", Path(folder) / "kino.db"):
            kino_db.init_db()
            kino_db.save_config("pref_lang", "vf")
            french = {"audio": {"language": "fr", "role": "main", "original": False},
                      "subtitles": {"enabled": False, "kind": "", "language": "", "variant": ""}}
            japanese = {"audio": {"language": "ja", "role": "main", "original": True},
                        "subtitles": {"enabled": True, "kind": "opensubtitles", "language": "fr", "variant": "standard"}}
            series_playback.save("tt101", french)
            series_playback.save("tt202", japanese)
            self.assertEqual(series_playback.get("tt101"), french)
            self.assertEqual(series_playback.get("tt202"), japanese)
            series_playback.clear("tt101")
            self.assertIsNone(series_playback.get("tt101"))
            self.assertEqual(series_playback.get("tt202"), japanese)
            self.assertEqual(kino_db.get_config("pref_lang"), "vf")

    def test_rejects_urls_and_unstable_identifiers(self):
        with self.assertRaises(ValueError):
            series_playback.series_id("https://example.invalid/series")
        with self.assertRaises(ValueError):
            series_playback.normalize_preference({"audio": {"language": "https://secret.invalid"}})
        with self.assertRaises(ValueError):
            series_playback.normalize_preference({"subtitles": {"enabled": True, "kind": "file", "language": "fr"}})

    def test_subtitle_kind_is_read_from_available_metadata(self):
        payload = {"subtitles": [
            {"url": "https://example.invalid/normal.srt", "lang": "fra", "id": "regular"},
            {"url": "https://example.invalid/forced.srt", "lang": "fra", "title": "Français forcés"},
            {"url": "https://example.invalid/sdh.srt", "lang": "eng", "hearing_impaired": True},
        ]}
        with patch.object(meta_engine, "cached_get", side_effect=lambda _key, _ttl, fetch: fetch()), \
             patch.object(meta_engine, "http_json", return_value=payload):
            subtitles = meta_engine.fetch_opensubtitles("tt123", "series", 1, 2)
        self.assertEqual([item["variant"] for item in subtitles], ["standard", "forced", "sdh"])

    def test_http_save_get_and_clear(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(kino_db, "DB_PATH", Path(folder) / "kino.db"):
            kino_db.init_db()
            preference = {"audio": {"language": "en", "role": "main", "original": True},
                          "subtitles": {"enabled": True, "kind": "opensubtitles", "language": "fr", "variant": "standard"}}
            write = object.__new__(app.RequestHandler)
            write.path = "/api/series-playback-preference"
            write._request_allowed = Mock(return_value=True)
            write.read_json_body = Mock(return_value={"series_id": "tt303", "action": "save", "preference": preference})
            write.send_json = Mock()
            write.do_POST()
            self.assertEqual(write.send_json.call_args.args[0]["preference"], preference)

            read = object.__new__(app.RequestHandler)
            read.path = "/api/series-playback-preference?series_id=tt303"
            read._request_allowed = Mock(return_value=True)
            read.send_json = Mock()
            read.do_GET()
            self.assertEqual(read.send_json.call_args.args[0]["preference"], preference)

            write.read_json_body.return_value = {"series_id": "tt303", "action": "clear"}
            write.do_POST()
            self.assertIsNone(series_playback.get("tt303"))


if __name__ == "__main__":
    unittest.main()
