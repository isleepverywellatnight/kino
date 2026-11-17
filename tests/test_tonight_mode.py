import unittest

from tonight_mode import choose_tonight, enrich_watchlist, runtime_minutes


class TonightModeTests(unittest.TestCase):
    def test_runtime_parses_common_formats(self):
        self.assertEqual(runtime_minutes("1h 35 min"), 95)
        self.assertEqual(runtime_minutes("90 min"), 90)
        self.assertIsNone(runtime_minutes("Durée inconnue"))

    def test_time_mood_and_watched_items_are_respected(self):
        items = [
            {"id": "watched", "name": "Vu", "runtime": "90 min", "genres": ["Comedy"]},
            {"id": "long", "name": "Trop long", "runtime": "130 min", "genres": ["Comedy"]},
            {"id": "dark", "name": "Sombre", "runtime": "80 min", "genres": ["Horror"]},
            {"id": "light", "name": "Léger", "runtime": "85 min", "genres": ["Comedy"]},
            {"id": "unknown", "name": "Inconnu", "genres": []},
        ]
        choices = choose_tonight(items, [{"id": "watched", "completed": True}], 100, "light")
        self.assertEqual([choice["item"]["id"] for choice in choices], ["light", "unknown"])
        self.assertIn("85 min", choices[0]["reasons"])
        self.assertEqual(choices[1]["reasons"], ["Durée inconnue", "Ambiance non renseignée"])

    def test_no_more_than_three_unique_titles(self):
        items = [{"id": str(i), "name": str(i), "runtime": 60} for i in range(5)]
        choices = choose_tonight(items + items, [], 90)
        self.assertEqual([choice["item"]["id"] for choice in choices], ["0", "1", "2"])

    def test_metadata_enrichment_enables_duration_and_mood_filters(self):
        items = [{"id": "tt1", "name": "A"}, {"id": "tt2", "name": "B"}]
        def cached(item):
            return {"runtime": "80 min", "genres": ["Comedy"]} if item["id"] == "tt1" else None
        def fetch(item):
            return {"runtime": "140 min", "genres": ["Comedy"]}
        enriched, coverage = enrich_watchlist(items, cached, fetch)
        choices = choose_tonight(enriched, [], 100, "light")
        self.assertEqual([choice["item"]["id"] for choice in choices], ["tt1"])
        self.assertEqual(coverage, {"enriched": 2, "total": 2, "partial": False})

    def test_temporary_and_durable_refusals_filter_differently(self):
        items = [{"id": item_id, "name": item_id, "runtime": 80} for item_id in ("later", "never", "free")]
        feedback = {
            "later": {"decision": "deferred", "until_ts": 200},
            "never": {"decision": "excluded", "until_ts": 0},
        }
        current = choose_tonight(items, [], 90, feedback=feedback, now=100)
        after = choose_tonight(items, [], 90, feedback=feedback, now=201)
        self.assertEqual([choice["item"]["id"] for choice in current], ["free"])
        self.assertEqual({choice["item"]["id"] for choice in after}, {"later", "free"})

    def test_frequently_shown_title_gives_way_to_fresh_choice(self):
        items = [{"id": str(i), "name": str(i), "runtime": 80} for i in range(4)]
        feedback = {str(i): {"shown_count": 2, "last_shown": 100} for i in range(3)}
        choices = choose_tonight(items, [], 90, limit=3, feedback=feedback, now=101)
        self.assertEqual(choices[0]["item"]["id"], "3")
        self.assertIn("Encore jamais proposé", choices[0]["reasons"])

    def test_new_profile_gets_genre_diversity(self):
        items = [
            {"id": "a", "name": "Comédie A", "runtime": 80, "genres": ["Comedy"]},
            {"id": "b", "name": "Comédie B", "runtime": 80, "genres": ["Comedy"]},
            {"id": "c", "name": "Thriller", "runtime": 80, "genres": ["Thriller"]},
        ]
        choices = choose_tonight(items, [], 90, limit=2)
        self.assertEqual([choice["item"]["id"] for choice in choices], ["a", "c"])
        self.assertIn("Genre différent des autres idées", choices[1]["reasons"])

    def test_offline_mode_only_offers_files_ready_on_this_mac(self):
        items = [{"id": "online", "name": "En ligne", "runtime": 80},
                 {"id": "offline", "name": "Sur ce Mac", "runtime": 80}]
        downloads = {
            "ready": {"id": "ready", "available": True, "media": {"id": "offline"}},
            "partial": {"id": "partial", "available": False, "media": {"id": "online"}},
        }
        choices = choose_tonight(items, [], 90, offline_downloads=downloads, offline_only=True)
        self.assertEqual([choice["item"]["id"] for choice in choices], ["offline"])
        self.assertEqual(choices[0]["offline_id"], "ready")
        self.assertIn("Disponible hors ligne", choices[0]["reasons"])

    def test_two_explicit_rejections_reduce_similar_genres(self):
        items = [
            {"id": "comedy", "name": "Comédie", "runtime": 80, "genres": ["Comedy"]},
            {"id": "drama", "name": "Drame", "runtime": 80, "genres": ["Drama"]},
        ]
        feedback = {
            "old1": {"decision": "excluded", "genres": ["Comedy"]},
            "old2": {"decision": "excluded", "genres": ["Comedy"]},
        }
        choices = choose_tonight(items, [], 90, limit=1, feedback=feedback)
        self.assertEqual(choices[0]["item"]["id"], "drama")
        self.assertIn("Évite les genres souvent écartés", choices[0]["reasons"])

    def test_duration_ranking_prefers_a_good_fit_within_available_time(self):
        items = [{"id": "short", "name": "Court", "runtime": 45},
                 {"id": "fit", "name": "Adapté", "runtime": 105}]
        choices = choose_tonight(items, [], 120, limit=2)
        self.assertEqual([choice["item"]["id"] for choice in choices], ["fit", "short"])

    def test_finishing_one_series_episode_does_not_hide_the_whole_series(self):
        items = [{"id": "series", "name": "La série", "type": "series", "runtime": 45}]
        history = [{"id": "series", "type": "series", "season": 1, "episode": 1,
                    "watched_episodes": ["S01E01"], "completed": True, "progress_pct": 100}]
        choices = choose_tonight(items, history, 90)
        self.assertEqual([choice["item"]["id"] for choice in choices], ["series"])


if __name__ == "__main__":
    unittest.main()
