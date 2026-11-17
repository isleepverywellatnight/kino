"""« Tout »: films, series and French titles searched at once, merged without
duplicates, the closest names first."""
import unittest
from unittest.mock import Mock, patch

import app
import kino_db
import meta_engine
import unified_search


def meta(id_, name, kind=None, **extra):
    item = {"id": id_, "name": name, **extra}
    if kind:
        item["type"] = kind
    return item


class ScoreTests(unittest.TestCase):
    def test_closeness(self):
        score = unified_search.match_score
        self.assertEqual(score("dune", meta("tt1", "Dune")), 3)
        self.assertEqual(score("Les évadés", meta("tt2", "The Shawshank Redemption", name_fr="Les Évadés")), 3)
        self.assertEqual(score("dune", meta("tt3", "Dune: Prophecy")), 2)
        self.assertEqual(score("part two", meta("tt4", "Dune: Part Two")), 1)
        self.assertEqual(score("dune", meta("tt5", "Arrakis")), 0)
        self.assertEqual(score("", meta("tt1", "Dune")), 0)


class MergeTests(unittest.TestCase):
    def test_closest_first_then_sources_alternate(self):
        movies = [meta("tt2", "Dune: Part Two", "movie"), meta("tt1", "Dune", "movie"), meta("tt9", "Arrakis", "movie")]
        series = [meta("tt3", "Dune: Prophecy", "series"), meta("tt8", "Frank Herbert's Dune", "series")]
        merged = unified_search.merge_results("Dune", movies, series)
        self.assertEqual([m["id"] for m in merged], ["tt1", "tt2", "tt3", "tt8", "tt9"])
        self.assertEqual([m["type"] for m in merged], ["movie", "movie", "series", "series", "movie"])

    def test_french_matches_first_without_duplicates(self):
        french = [meta("tt0111161", "The Shawshank Redemption", "movie", name_fr="Les Évadés")]
        movies = [meta("tt5797184", "Escape from Pretoria", "movie"), meta("tt0111161", "The Shawshank Redemption", "movie")]
        merged = unified_search.merge_results("Les Évadés", movies, [], french)
        self.assertEqual([m["id"] for m in merged], ["tt0111161", "tt5797184"])
        self.assertEqual(merged[0]["name_fr"], "Les Évadés")

    def test_types_bad_entries_and_limit(self):
        merged = unified_search.merge_results("x", [meta("tt1", "X")], [meta("tt2", "X 2"), None, {"name": "no id"}])
        self.assertEqual([(m["id"], m["type"]) for m in merged], [("tt1", "movie"), ("tt2", "series")])
        many = [meta(f"tt{i}", f"Film {i}", "movie") for i in range(100)]
        self.assertEqual(len(unified_search.merge_results("film", many)), 60)


class RouteTests(unittest.TestCase):
    def handler(self, path):
        handler = object.__new__(app.RequestHandler)
        handler.path = path
        handler.headers = {}
        handler._request_allowed = Mock(return_value=True)
        handler.send_json = Mock()
        return handler

    def test_all_searches_both_catalogues_and_french_titles(self):
        def catalogue(query, kind):
            return [meta("tt1", "Dune", "movie")] if kind == "movie" else [meta("tt3", "Dune: Prophecy", "series")]
        handler = self.handler("/api/search?type=all&q=Dune")
        with patch.object(app, "search_cinemeta", side_effect=catalogue) as search, \
                patch.object(meta_engine, "search_by_french_title", return_value=[]) as french:
            handler.do_GET()
        self.assertEqual(sorted(call.args for call in search.call_args_list), [("Dune", "movie"), ("Dune", "series")])
        french.assert_called_once_with("Dune", "all")
        metas = handler.send_json.call_args.args[0]["metas"]
        self.assertEqual([(m["id"], m["type"]) for m in metas], [("tt1", "movie"), ("tt3", "series")])

    def test_a_failing_source_leaves_the_others(self):
        def catalogue(query, kind):
            if kind == "movie":
                raise RuntimeError("Erreur API (503)")
            return [meta("tt3", "Dune: Prophecy", "series")]
        with patch.object(app, "search_cinemeta", side_effect=catalogue), \
                patch.object(meta_engine, "search_by_french_title", side_effect=OSError("down")):
            self.assertEqual([m["id"] for m in app.search_everything("Dune")], ["tt3"])

    def test_known_titles_when_nothing_is_found_online(self):
        local = [meta("tt0111161", "The Shawshank Redemption", "movie")]
        with patch.object(app, "search_cinemeta", return_value=[]), \
                patch.object(meta_engine, "search_by_french_title", return_value=[]), \
                patch.object(kino_db, "db_search_fast", return_value=local) as fast:
            self.assertEqual([m["id"] for m in app.search_everything("shawshank")], ["tt0111161"])
        fast.assert_called_once_with("shawshank", media_type=None, limit=20)
        self.assertEqual(app.search_everything("   "), [])


if __name__ == "__main__":
    unittest.main()
