"""Stremio add-ons added by their address: kept, queried for sources and catalogues."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import kino_db
import stremio_addons
import torrent_engine

MANIFEST = {
    "id": "org.example.addon", "name": "Exemple", "version": "1.2.0", "description": "Des sources",
    "types": ["movie", "series", "tv"],
    "resources": ["stream", {"name": "catalog", "types": ["movie"]}, "meta"],
    "idPrefixes": ["tt"],
    "catalogs": [
        {"type": "movie", "id": "top", "name": "Populaires"},
        {"type": "movie", "id": "search", "name": "Recherche", "extra": [{"name": "search", "isRequired": True}]},
        {"type": "channel", "id": "tv", "name": "Chaînes"},
    ],
}


class AddonTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        database = patch.object(kino_db, "DB_PATH", Path(folder.name) / "kino.db")
        database.start()
        self.addCleanup(database.stop)
        kino_db.init_db()
        self.fetch = Mock(return_value=MANIFEST)

    def add(self, address="https://addon.example/abc/manifest.json"):
        return stremio_addons.add(address, fetch=self.fetch)

    def test_the_address_the_user_pastes(self):
        for address, base in (("https://addon.example/abc/manifest.json", "https://addon.example/abc"),
                              ("stremio://addon.example/abc/manifest.json", "https://addon.example/abc"),
                              ("  https://addon.example/  ", "https://addon.example")):
            self.assertEqual(stremio_addons._base_of(address), base)
        for bad in ("", "addon.example/manifest.json", "file:///etc/passwd", "javascript:alert(1)"):
            with self.assertRaises(stremio_addons.AddonError):
                stremio_addons._base_of(bad)

    def test_kept_with_what_it_offers(self):
        entry = self.add()
        self.fetch.assert_called_once_with("https://addon.example/abc/manifest.json", timeout=10, retries=1)
        self.assertEqual(entry, {
            "id": "org.example.addon", "name": "Exemple", "version": "1.2.0", "description": "Des sources",
            "base": "https://addon.example/abc", "streams": True, "types": ["movie", "series"], "imdb": True,
            "catalogs": [{"type": "movie", "id": "top", "name": "Populaires"}], "enabled": True})
        self.assertEqual(stremio_addons.installed(), [entry], "kept on this computer")
        self.add("https://addon.example/abc/manifest.json")
        self.assertEqual(len(stremio_addons.installed()), 1, "the same add-on is updated, not doubled")

    def test_an_address_that_is_not_an_addon(self):
        self.fetch.side_effect = OSError("down")
        with self.assertRaisesRegex(stremio_addons.AddonError, "ne répond pas"):
            self.add()
        self.fetch.side_effect = None
        self.fetch.return_value = {"html": True}
        with self.assertRaisesRegex(stremio_addons.AddonError, "ne répond pas comme un addon"):
            self.add()
        self.fetch.return_value = {**MANIFEST, "resources": ["meta", "subtitles"]}
        with self.assertRaisesRegex(stremio_addons.AddonError, "ni sources ni catalogues"):
            self.add()
        self.assertEqual(stremio_addons.installed(), [])

    def test_sources_for_a_title_from_the_enabled_addons_only(self):
        self.add()
        self.assertEqual(stremio_addons.stream_sources("tt0111161", "movie"), [
            ("stremio:org.example.addon", "https://addon.example/abc/stream/movie/tt0111161.json", "Exemple", 8)])
        self.assertEqual(stremio_addons.stream_sources("tt0903747:2:3", "anime")[0][1],
                         "https://addon.example/abc/stream/series/tt0903747:2:3.json", "anime is a series")
        self.assertEqual(stremio_addons.stream_sources("../../x", "movie"), [], "only IMDb targets are sent")
        stremio_addons.set_enabled("org.example.addon", False)
        self.assertEqual(stremio_addons.stream_sources("tt0111161", "movie"), [])
        stremio_addons.set_enabled("org.example.addon", True)
        self.fetch.return_value = {**MANIFEST, "id": "kitsu.only", "idPrefixes": ["kitsu:"]}
        self.add("https://other.example/manifest.json")
        self.assertEqual([s[0] for s in stremio_addons.stream_sources("tt0111161", "movie")], ["stremio:org.example.addon"],
                         "an add-on that knows no IMDb number is not asked")

    def test_catalogues_list_the_titles_kino_can_open(self):
        self.add()
        self.assertEqual(stremio_addons.catalogs(), [
            {"addon": "org.example.addon", "addon_name": "Exemple", "type": "movie", "id": "top", "name": "Populaires"}])
        fetch = Mock(return_value={"metas": [
            {"id": "tt1", "name": "Film", "poster": "p.jpg", "releaseInfo": "2024", "imdbRating": "7.1"},
            {"id": "kitsu:1", "name": "Ailleurs"}, "nope"]})
        items = stremio_addons.catalog_items("org.example.addon", "movie", "top", fetch=fetch)
        fetch.assert_called_once_with("https://addon.example/abc/catalog/movie/top.json", timeout=10, retries=1)
        self.assertEqual(items, [{"id": "tt1", "type": "movie", "name": "Film", "poster": "p.jpg", "year": "2024", "imdbRating": "7.1"}])
        with self.assertRaises(stremio_addons.AddonError):
            stremio_addons.catalog_items("org.example.addon", "movie", "search", fetch=fetch)

    def test_removed(self):
        self.add()
        stremio_addons.remove("org.example.addon")
        self.assertEqual(stremio_addons.installed(), [])
        with self.assertRaises(stremio_addons.AddonError):
            stremio_addons.remove("org.example.addon")

    def test_the_routes(self):
        handler = object.__new__(app.RequestHandler)
        handler.send_json = Mock()
        with patch.object(stremio_addons, "http_json", Mock(return_value=MANIFEST)):
            handler.post_stremio_addons(None, {}, {"action": "add", "url": "https://addon.example/manifest.json"})
        self.assertEqual(handler.send_json.call_args.args[0]["addons"][0]["name"], "Exemple")
        handler.post_stremio_addons(None, {}, {"action": "add", "url": "nope"})
        self.assertEqual(handler.send_json.call_args.kwargs, {"status": 400})
        handler.post_stremio_addons(None, {}, {"action": "explode"})
        self.assertEqual(handler.send_json.call_args.kwargs, {"status": 400})


class SearchTests(unittest.TestCase):
    def test_an_addon_bucket_joins_the_merge(self):
        answers = {"https://addon.example/stream/movie/tt1.json": {"streams": [
            {"name": "Exemple\n1080p", "title": "Film.2024.1080p.WEB.mkv\n👤 12 💾 2.1 GB", "infoHash": "a" * 40}]}}
        sources = [("stremio:org.example.addon", "https://addon.example/stream/movie/tt1.json", "Exemple", 8)]
        buckets = torrent_engine.fetch_streams(sources, "tt1", "tt1", "movie", 1, 1,
                                               http_json_fn=lambda url, timeout=None: answers.get(url, {}))
        self.assertEqual(len(buckets["stremio:org.example.addon"]), 1)
        merged = torrent_engine.merge_streams(buckets)
        self.assertEqual([item["info_hash"] for item in merged], ["a" * 40])


if __name__ == "__main__":
    unittest.main()
