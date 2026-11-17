"""Searching by French title: local aliases and French Wikipedia."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import config
import kino_db
import meta_engine

SHAWSHANK = {"id": "tt0111161", "name": "The Shawshank Redemption", "type": "movie", "releaseInfo": "1994"}


class FrenchSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(kino_db, "DB_PATH", Path(self.temp.name) / "kino.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        kino_db.init_db()
        config.MEM_CACHE.clear()
        self.addCleanup(config.MEM_CACHE.clear)

    def test_known_french_titles_are_suggested_without_accents_and_first(self):
        kino_db.db_index_media([{"id": "tt0111161", "name": "The Shawshank Redemption", "type": "movie", "year": "1994"},
                                {"id": "tt5797184", "name": "Escape from Pretoria", "type": "movie", "year": "2020"}])
        kino_db.db_set_aliases([{"id": "tt0111161", "alias": "Les Évadés", "name": "The Shawshank Redemption", "year": "1994"},
                                {"id": "tt9999999", "alias": "Les Évadés de nulle part", "name": "Nowhere", "year": "2026"}])
        found = kino_db.db_search_fast("evades")
        self.assertEqual([r["id"] for r in found][:2], ["tt0111161", "tt9999999"])
        self.assertEqual(found[0]["name"], "The Shawshank Redemption", "the catalogue name stays for source searches")
        self.assertEqual(found[0]["name_fr"], "Les Évadés")
        self.assertTrue(found[1]["poster"].endswith("/tt9999999/img"), "alias without catalogue entry still has a poster")
        self.assertEqual(kino_db.db_search_fast("evades", media_type="series"), [])
        self.assertEqual([r["id"] for r in kino_db.db_search_fast("shawshank")], ["tt0111161"])

    def test_suggestions_put_the_rated_exact_title_first_and_drop_its_bare_repeats(self):
        kino_db.db_index_media([
            {"id": "tt11387646", "name": "GoodFellas... Where Loyalty Matters!", "type": "movie"},
            {"id": "tt2180383", "name": "Goodfellas", "type": "movie", "year": "1990"},
            {"id": "tt13585564", "name": "Paulie & Henry: The End of a Goodfellas Era", "type": "movie"},
            {"id": "tt0099685", "name": "GoodFellas", "type": "movie", "year": "1990", "imdbRating": "8.7"},
            {"id": "tt7777777", "name": "Goodfellas", "type": "movie", "year": "2031"},
        ])
        found = [r["id"] for r in kino_db.db_search_fast("goodfellas", limit=8)]
        self.assertEqual(found[0], "tt0099685")
        self.assertNotIn("tt2180383", found, "the same name, kind and year without a rating: a repeat")
        self.assertIn("tt7777777", found, "a work of another year stays")
        self.assertEqual(found[1], "tt7777777", "then the exact title, before the titles that only contain the word")

    def test_french_titles_resolved_for_display_become_searchable(self):
        with patch.object(meta_engine, "http_json", return_value={"query": {"pages": [
                {"title": "Ghostbusters (1984 film)", "description": "1984 film by Ivan Reitman",
                 "langlinks": [{"title": "SOS Fantômes"}]}]}}):
            meta_engine.french_titles([{"id": "tt0087332", "name": "Ghostbusters", "year": "1984"},
                                       {"id": "tt1375666", "name": "Inception", "year": "2010"}])
        self.assertEqual([r["id"] for r in kino_db.db_search_fast("sos fant")], ["tt0087332"])

    def test_french_wikipedia_leads_to_the_catalogue_entry_checked_by_name_and_year(self):
        wiki = {"query": {"pages": [
            {"index": 1, "title": "Les Évadés (film, 1994)", "description": "film sorti en 1994",
             "langlinks": [{"title": "The Shawshank Redemption"}]},
            {"index": 2, "title": "Les Évadés (série de films)", "description": "série de films", "langlinks": [{"title": "X"}]},
            {"index": 3, "title": "Évasion", "description": "fait de s'échapper", "langlinks": [{"title": "Escape"}]},
        ]}}
        catalogue = {"The Shawshank Redemption": [dict(SHAWSHANK, name="The Shawshank Redemption", releaseInfo="1994")]}
        with patch.object(meta_engine, "http_json", return_value=wiki), \
                patch.object(meta_engine, "search_cinemeta", side_effect=lambda q, t: catalogue.get(q, [])) as search:
            results = meta_engine.search_by_french_title("Les Évadés", "movie")
        self.assertEqual([(r["id"], r["name_fr"]) for r in results], [("tt0111161", "Les Évadés")])
        search.assert_called_once_with("The Shawshank Redemption", "movie")
        self.assertEqual(kino_db.db_cache_get("title_fr_v1:tt0111161"), "Les Évadés")
        self.assertEqual([r["id"] for r in kino_db.db_search_fast("évadés")], ["tt0111161"])

    def test_wrong_year_short_queries_and_network_trouble_give_nothing(self):
        wiki = {"query": {"pages": [{"index": 1, "title": "Les Évadés", "description": "film sorti en 1955",
                                     "langlinks": [{"title": "The Shawshank Redemption"}]}]}}
        with patch.object(meta_engine, "http_json", return_value=wiki), \
                patch.object(meta_engine, "search_cinemeta", return_value=[SHAWSHANK]):
            self.assertEqual(meta_engine.search_by_french_title("Les Évadés", "movie"), [])
        self.assertEqual(meta_engine.search_by_french_title("Ça", "movie"), [])
        config.MEM_CACHE.clear()
        with patch.object(meta_engine, "http_json", side_effect=RuntimeError("Erreur API (503)")):
            self.assertEqual(meta_engine.search_by_french_title("Les Bronzés", "movie"), [])
        self.assertIsNone(kino_db.db_cache_get("search_fr_v1:les bronzes"), "asked again next time")

    def test_search_route_puts_french_matches_first_without_duplicates(self):
        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/search?type=movie&q=Les%20%C3%89vad%C3%A9s"
        handler.headers = {}
        handler._request_allowed = Mock(return_value=True)
        handler.send_json = Mock()
        french = [dict(SHAWSHANK, name_fr="Les Évadés")]
        with patch.object(app, "search_cinemeta", return_value=[{"id": "tt5797184", "name": "Escape from Pretoria"}, SHAWSHANK]), \
                patch.object(meta_engine, "search_by_french_title", return_value=french):
            handler.do_GET()
        metas = handler.send_json.call_args.args[0]["metas"]
        self.assertEqual([m["id"] for m in metas], ["tt0111161", "tt5797184"])
        self.assertEqual(metas[0]["name_fr"], "Les Évadés")


if __name__ == "__main__":
    unittest.main()
