"""French episode summaries for the series fiche (/api/episodes-fr)."""
import unittest
from unittest.mock import Mock, patch

import app
import config
import meta_engine


def get(path):
    handler = object.__new__(app.RequestHandler)
    handler.path = path
    handler._request_allowed = Mock(return_value=True)
    handler.send_json = Mock()
    handler.do_GET()
    return handler.send_json.call_args


META = {"videos": [
    {"season": 2, "episode": 1, "overview": "He runs."},
    {"season": 2, "episode": 2, "overview": "Elle part avec les clés dans la voiture."},
    {"season": 2, "number": 3, "description": "Rock &amp; roll."},
    {"season": 2, "episode": 4, "overview": ""},
    {"season": 1, "episode": 1, "overview": "Old season."},
]}


class OverviewTests(unittest.TestCase):
    def test_a_season_is_translated_episode_by_episode(self):
        def translate(text):
            return {"He runs.": "Il court.", "Rock & roll.": "Du rock."}.get(text, text)

        with patch.object(meta_engine, "get_series_meta", return_value=META), \
                patch.object(meta_engine, "translate_text_fr", side_effect=translate) as tr:
            result = meta_engine.episode_overviews_fr("tt1", 2)
        self.assertEqual(result, {"1": "Il court.", "2": "Elle part avec les clés dans la voiture.", "3": "Du rock."})
        self.assertNotIn("Old season.", [call.args[0] for call in tr.call_args_list])

    def test_failed_translations_are_left_out(self):
        with patch.object(meta_engine, "get_series_meta", return_value=META), \
                patch.object(meta_engine, "translate_text_fr", side_effect=lambda text: text):
            result = meta_engine.episode_overviews_fr("tt1", 2)
        self.assertEqual(result, {"2": "Elle part avec les clés dans la voiture."}, "only text already in French")

    def test_bad_input_or_no_episodes(self):
        with patch.object(meta_engine, "get_series_meta", return_value=META):
            self.assertEqual(meta_engine.episode_overviews_fr("tt1", "x"), {})
            self.assertEqual(meta_engine.episode_overviews_fr("tt1", 9), {})
        with patch.object(meta_engine, "get_series_meta", return_value=None):
            self.assertEqual(meta_engine.episode_overviews_fr("tt1", 2), {})

    def test_a_failed_translation_is_not_cached(self):
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            raise OSError("offline")

        text = "A summary that is not in French at all, translated later."
        with patch.object(meta_engine.urllib.request, "urlopen", side_effect=failing), \
                patch.object(meta_engine, "cached_get", side_effect=lambda key, ttl, fetch: fetch()):
            self.assertEqual(meta_engine.translate_text_fr(text), text)
        stored = []
        with patch.object(meta_engine.urllib.request, "urlopen", side_effect=failing), \
                patch.object(config.kino_db, "db_cache_set", side_effect=lambda *a, **k: stored.append(a)), \
                patch.object(config.kino_db, "db_cache_get", return_value=None):
            meta_engine.translate_text_fr(text + " Again.")
        self.assertEqual(stored, [], "nothing stored: the next call tries again")
        self.assertTrue(calls)


class RouteTests(unittest.TestCase):
    def test_route_returns_the_summaries(self):
        with patch.object(meta_engine, "episode_overviews_fr", return_value={"1": "Il court."}) as overviews:
            result = get("/api/episodes-fr?imdb_id=tt27995113&season=2")
        self.assertEqual(result.args[0], {"overviews": {"1": "Il court."}})
        overviews.assert_called_once_with("tt27995113", 2)

    def test_route_refuses_bad_ids(self):
        with patch.object(meta_engine, "episode_overviews_fr") as overviews:
            for query in ("imdb_id=../x&season=2", "imdb_id=tt1&season=abc", "imdb_id=tt1&season=1234", "season=1"):
                self.assertEqual(get(f"/api/episodes-fr?{query}").kwargs["status"], 400, query)
        overviews.assert_not_called()


if __name__ == "__main__":
    unittest.main()
