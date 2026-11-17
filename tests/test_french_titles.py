"""French display titles (Wikipedia language links, KINO's own aliases)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import config
import kino_db
import meta_engine


def wiki_answer(pages, redirects=(), normalized=()):
    return {"query": {"pages": pages, "redirects": [{"from": a, "to": b} for a, b in redirects],
                      "normalized": [{"from": a, "to": b} for a, b in normalized]}}


class FrenchTitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(kino_db, "DB_PATH", Path(self.temp.name) / "kino.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        kino_db.init_db()

    def test_film_page_checked_by_year_gives_the_french_page_title(self):
        answer = wiki_answer([
            {"title": "The Nightmare Before Christmas (1993 film)", "missing": True},
            {"title": "The Nightmare Before Christmas (film)", "missing": True},
            {"title": "The Nightmare Before Christmas", "description": "1993 film by Henry Selick",
             "langlinks": [{"lang": "fr", "title": "L'Étrange Noël de monsieur Jack (film)"}]},
        ])
        with patch.object(meta_engine, "http_json", return_value=answer) as http:
            titles = meta_engine.french_titles([{"id": "tt0107688", "name": "The Nightmare Before Christmas",
                                                 "year": "1993", "type": "movie"}])
        self.assertEqual(titles, {"tt0107688": "L'Étrange Noël de monsieur Jack"})
        self.assertEqual(http.call_count, 1)
        with patch.object(meta_engine, "http_json") as http:
            again = meta_engine.french_titles([{"id": "tt0107688", "name": "The Nightmare Before Christmas", "year": "1993"}])
        http.assert_not_called()
        self.assertEqual(again, titles)

    def test_wrong_year_manga_or_unknown_page_gives_no_title(self):
        answer = wiki_answer([
            {"title": "Dune", "description": "1965 novel by Frank Herbert", "langlinks": [{"title": "Dune (roman)"}]},
            {"title": "Jujutsu Kaisen", "description": "Japanese manga series", "langlinks": [{"title": "Jujutsu Kaisen"}]},
            {"title": "Solaris", "description": "2002 film by Steven Soderbergh", "langlinks": [{"title": "Solaris (film, 2002)"}]},
        ])
        with patch.object(meta_engine, "http_json", return_value=answer):
            titles = meta_engine.french_titles([
                {"id": "tt0087182", "name": "Dune", "year": "1984", "type": "movie"},
                {"id": "tt12343534", "name": "Jujutsu Kaisen", "year": "2020", "type": "anime"},
                {"id": "tt0069293", "name": "Solaris", "year": "1972", "type": "movie"},
            ])
        self.assertEqual(titles, {"tt0087182": "", "tt12343534": "", "tt0069293": ""})

    def test_series_page_and_redirects(self):
        answer = wiki_answer(
            [{"title": "Rick and Morty", "description": "American adult animated sitcom",
              "langlinks": [{"title": "Rick et Morty"}]}],
            redirects=[("Rick and Morty (TV series)", "Rick and Morty")])
        with patch.object(meta_engine, "http_json", return_value=answer):
            titles = meta_engine.french_titles([{"id": "tt2861424", "name": "Rick and Morty", "year": "2013–", "type": "series"}])
        self.assertEqual(titles["tt2861424"], "Rick et Morty")

    def test_kino_aliases_need_no_request_and_network_trouble_is_not_cached(self):
        with patch.object(meta_engine, "http_json", side_effect=RuntimeError("Erreur API (503)")) as http:
            titles = meta_engine.french_titles([
                {"id": "tt0060196", "name": "The Good, the Bad and the Ugly (Le Bon, la Brute et le Truand)", "year": "1966"},
                {"id": "tt0816692", "name": "Interstellar", "year": "2014"},
            ])
        self.assertEqual(titles, {"tt0060196": "Le Bon, la Brute et le Truand"})
        self.assertEqual(http.call_count, 1)
        self.assertIsNone(kino_db.db_cache_get("title_fr_v1:tt0816692"), "asked again next time")

    def test_aliases_only_when_the_parenthesis_is_french(self):
        split = meta_engine.split_french_alias
        self.assertEqual(split("Spirited Away (Le Voyage de Chihiro)"), ("Spirited Away", "Le Voyage de Chihiro"))
        self.assertEqual(split("Leon: The Professional (Léon)"), ("Leon: The Professional", "Léon"))
        self.assertEqual(split("Birdman (or The Unexpected Virtue of Ignorance)")[1], "")
        self.assertEqual(split("Mission: Impossible (1996)")[1], "")

    def test_one_french_title_per_work(self):
        """« Les Affranchis » showed three times, on GoodFellas and two homonym listings."""
        kino_db.db_index_media([{"id": "tt0099685", "name": "GoodFellas", "type": "movie", "year": "1990", "imdbRating": "8.7"},
                                {"id": "tt2180383", "name": "Goodfellas", "type": "movie", "year": "1990"}])
        page = {"title": "Goodfellas", "description": "1990 film by Martin Scorsese", "langlinks": [{"title": "Les Affranchis"}]}
        with patch.object(meta_engine, "http_json", return_value=wiki_answer([page], redirects=[("GoodFellas", "Goodfellas")])):
            titles = meta_engine.french_titles([
                {"id": "tt0099685", "name": "GoodFellas", "year": "1990", "type": "movie"},
                {"id": "tt5679188", "name": "Good Fellas", "type": "movie"},  # no year: cannot be told apart
                {"id": "tt2180383", "name": "Goodfellas", "year": "1990", "type": "movie"},  # the same work listed twice
            ])
        self.assertEqual(titles, {"tt0099685": "Les Affranchis", "tt5679188": "", "tt2180383": ""})
        self.assertEqual(kino_db.db_cache_get("title_fr_v1:tt2180383"), "", "not asked again for a month")
        self.assertEqual([r["id"] for r in kino_db.db_search_fast("affranchis")], ["tt0099685"])

    def test_a_remake_keeps_its_own_french_title(self):
        kino_db.db_set_aliases([{"id": "tt0054215", "alias": "Psychose", "name": "Psycho", "year": "1960"}])
        self.assertEqual(kino_db.db_set_aliases([{"id": "tt0155975", "alias": "Psychose", "name": "Psycho", "year": "1998"}]), set())
        self.assertEqual({r["id"] for r in kino_db.db_search_fast("psychose")}, {"tt0054215", "tt0155975"})

    def test_titles_given_twice_before_are_taken_back(self):
        kino_db.db_index_media([{"id": "tt0021749", "name": "City Lights", "type": "movie", "year": "1931", "imdbRating": "8.5"}])
        conn = kino_db.get_connection()
        with conn:
            for imdb_id, year, at in (("tt33483889", "", 1.0), ("tt0021749", "1931", 2.0), ("tt10168078", "", 3.0), ("tt0099999", "1985", 4.0)):
                conn.execute("INSERT INTO media_aliases (imdb_id, alias, name, year, media_type, updated_at) VALUES (?, ?, ?, ?, 'movie', ?)",
                             (imdb_id, "Les Lumières de la ville", "City Lights", year, at))
        conn.close()
        kino_db.db_cache_set("title_fr_v1:tt33483889", "Les Lumières de la ville", ttl_sec=3600)
        self.assertEqual(kino_db.db_drop_shared_aliases(), 2, "the rated listing keeps it, a work of another year too")
        self.assertEqual(sorted(r["id"] for r in kino_db.db_search_fast("lumieres ville")), ["tt0021749", "tt0099999"])
        self.assertIsNone(kino_db.db_cache_get("title_fr_v1:tt33483889"))
        self.assertEqual(kino_db.db_drop_shared_aliases(), 0)

    def test_a_film_page_needs_the_listing_year(self):
        self.assertFalse(meta_engine._description_matches("1990 film by Martin Scorsese", "", "movie"))
        self.assertTrue(meta_engine._description_matches("1990 film by Martin Scorsese", "1991", "movie"))

    def test_bad_items_are_ignored_and_route_answers(self):
        self.assertEqual(meta_engine.french_titles([{"id": "kitsu:1", "name": "x"}, {"id": "tt1"}, "tt2", None]), {})
        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/french-titles"
        handler._request_allowed = Mock(return_value=True)
        handler.read_json_body = Mock(return_value={"items": [{"id": "tt1", "name": "A"}]})
        handler.send_json = Mock()
        with patch.object(meta_engine, "french_titles", return_value={"tt1": "Un"}) as titles:
            handler.do_POST()
        titles.assert_called_once_with([{"id": "tt1", "name": "A"}])
        self.assertEqual(handler.send_json.call_args.args[0], {"titles": {"tt1": "Un"}})


if __name__ == "__main__":
    unittest.main()
