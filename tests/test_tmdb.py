"""Titles close to a film or a series, from TMDB. TMDB is
simulated with answers of its real shape; the network is never used."""
import unittest
from unittest.mock import patch

import tmdb


def fake_tmdb(calls, recommendations=None, similar=None):
    recommendations = recommendations if recommendations is not None else [
        {"id": 11324, "title": "Shutter Island", "release_date": "2010-02-14", "vote_average": 8.2, "vote_count": 25000, "poster_path": "/si.jpg"},
        {"id": 77, "title": "Memento", "release_date": "2000-10-11", "vote_average": 8.2, "vote_count": 15000, "poster_path": "/me.jpg"},
        {"id": 1124, "title": "The Prestige", "release_date": "2006-10-17", "vote_average": 8.2, "vote_count": 17000, "poster_path": None},
        {"id": 210577, "title": "Gone Girl", "release_date": "2014-10-01", "vote_average": 7.9, "vote_count": 19000, "poster_path": "/gg.jpg"},
        {"id": 999, "title": "Obscure", "release_date": "", "vote_average": 6.0, "vote_count": 3, "poster_path": "/o.jpg"},
        {"id": 5, "title": "Sans IMDb", "release_date": "2001-01-01", "vote_average": 7, "vote_count": 100},
    ]
    imdb_of = {11324: "tt1130884", 77: "tt0209144", 1124: "tt0482571", 210577: "tt2267998", 999: "tt0000999", 5: None}

    def get(path, key, params=None):
        calls.append(path)
        if path == "/find/tt0137523":
            return {"movie_results": [{"id": 550}], "tv_results": []}
        if path == "/find/tt0903747":
            return {"movie_results": [], "tv_results": [{"id": 1396}]}
        if path.endswith("/recommendations"):
            return {"results": recommendations}
        if path.endswith("/similar"):
            return {"results": similar or []}
        if path.endswith("/external_ids"):
            return {"imdb_id": imdb_of.get(int(path.split("/")[2]))}
        if path == "/configuration":
            if key == "bad":
                raise tmdb.TmdbError("Clé TMDB refusée.")
            return {}
        raise AssertionError(path)
    return get


class RelatedTests(unittest.TestCase):
    def test_a_film_gets_its_recommendations_as_kino_cards(self):
        calls = []
        items = tmdb.related("tt0137523", "movie", "k" * 32, get=fake_tmdb(calls))
        self.assertEqual([i["id"] for i in items], ["tt1130884", "tt0209144", "tt0482571", "tt2267998", "tt0000999"],
                         "a recommendation without IMDb id is left out")
        self.assertEqual(items[0], {"id": "tt1130884", "name": "Shutter Island", "type": "movie", "year": "2010",
                                    "poster": "https://image.tmdb.org/t/p/w342/si.jpg", "imdbRating": "8.2"})
        self.assertEqual(items[2]["poster"], "https://images.metahub.space/poster/medium/tt0482571/img", "no TMDB poster: KINO's usual one")
        self.assertEqual((items[4]["imdbRating"], items[4]["year"]), ("", ""), "a rating from 3 votes is not shown")
        self.assertIn("/movie/550/recommendations", calls)
        self.assertIn("/movie/550/similar", calls, "few recommendations: the similar titles complete them")

    def test_a_series_uses_tv(self):
        calls = []
        items = tmdb.related("tt0903747", "series", "k" * 32, get=fake_tmdb(calls))
        self.assertIn("/tv/1396/recommendations", calls)
        self.assertEqual({i["type"] for i in items}, {"series"})

    def test_ids_and_lists_are_cached(self):
        store, calls = {}, []

        def cache(key, ttl, fetch):
            if key not in store:
                store[key] = fetch()
            return store[key]
        tmdb.related("tt0137523", "movie", "k" * 32, get=fake_tmdb(calls), cache=cache)
        first = len(calls)
        tmdb.related("tt0137523", "movie", "k" * 32, get=fake_tmdb(calls), cache=cache)
        self.assertEqual(len(calls), first, "the second time, nothing is asked")
        self.assertIn("tmdb_related_v1:movie:tt0137523", store)
        self.assertEqual(store["tmdb_imdb:movie:77"], "tt0209144")

    def test_no_key_or_a_bad_id_asks_nothing(self):
        calls = []
        self.assertEqual(tmdb.related("tt0137523", "movie", "", get=fake_tmdb(calls)), [])
        self.assertEqual(tmdb.related("javascript:x", "movie", "k" * 32, get=fake_tmdb(calls)), [])
        self.assertEqual(calls, [])

    def test_the_key_check_and_its_form(self):
        self.assertTrue(tmdb.check_key("good", get=fake_tmdb([])))
        self.assertFalse(tmdb.check_key("bad", get=fake_tmdb([])))
        self.assertFalse(tmdb.check_key("", get=fake_tmdb([])))
        self.assertEqual(tmdb.key_headers_params("eyJhbGciOi.x.y"), ({"Authorization": "Bearer eyJhbGciOi.x.y"}, {}))
        self.assertEqual(tmdb.key_headers_params("abc123"), ({}, {"api_key": "abc123"}))


class KinoTests(unittest.TestCase):
    def test_without_a_key_kino_keeps_the_genre_match(self):
        import meta_engine
        with patch.object(meta_engine, "load_config", lambda: {}), \
             patch.object(meta_engine, "get_media_meta", lambda i, t: {"similar": [{"id": "tt1"}]}):
            self.assertEqual(meta_engine.related_titles("tt0137523", "movie"), {"source": "genres", "items": [{"id": "tt1"}]})

    def test_with_a_key_tmdb_comes_first_and_a_failure_falls_back(self):
        import meta_engine
        cards = [{"id": f"tt{i}"} for i in range(6)]
        with patch.object(meta_engine, "load_config", lambda: {"tmdb_key": "k" * 32}), \
             patch.object(meta_engine, "get_media_meta", lambda i, t: {"similar": [{"id": "tt9"}]}):
            with patch.object(tmdb, "related", lambda *a, **k: cards):
                self.assertEqual(meta_engine.related_titles("tt0137523", "movie"), {"source": "tmdb", "items": cards})
            with patch.object(tmdb, "related", side_effect=OSError("down")):
                self.assertEqual(meta_engine.related_titles("tt0137523", "movie")["source"], "genres")

    def test_the_key_is_kept_only_in_its_form(self):
        import config
        self.assertEqual(config.sanitize_config_payload({"tmdb_key": " a" * 3})["tmdb_key"], "")
        good = "0123456789abcdef0123456789abcdef"
        self.assertEqual(config.sanitize_config_payload({"tmdb_key": good})["tmdb_key"], good)
        self.assertEqual(config.sanitize_config_payload({"tmdb_key": good + "<script>"})["tmdb_key"], "")


if __name__ == "__main__":
    unittest.main()
