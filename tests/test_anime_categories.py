"""Checks for the anime-specific catalogue filters."""

import unittest
from unittest.mock import patch

import anime_engine


class AnimeCategoryTests(unittest.TestCase):
    def setUp(self):
        self.series = [
            {"id": "tt13293588", "name": "Mushoku Tensei", "genres": ["Animation"], "description": ""},
            {"id": "slice-1", "name": "Everyday Days", "genres": ["Animation", "Slice of Life"], "description": ""},
            {"id": "sport-1", "name": "Volley Club", "genres": ["Animation", "Sport"], "description": ""},
            {"id": "scifi-1", "name": "Space Robots", "genres": ["Animation", "Sci-Fi", "Mecha"], "description": ""},
        ]
        self.movies = [{"id": "movie-1", "name": "Anime Film", "genres": ["Animation"]}]
        self.pool = patch.object(
            anime_engine, "get_anime_pool",
            side_effect=lambda media_type="series": self.movies if media_type == "movie" else self.series,
        )
        self.pool.start()
        self.addCleanup(self.pool.stop)

    def test_anime_specific_categories_select_the_matching_series(self):
        for category, expected_id in (
            ("Isekai", "tt13293588"),
            ("Slice of Life", "slice-1"),
            ("Sport", "sport-1"),
        ):
            with self.subTest(category=category):
                items = anime_engine.get_anime_catalog(genre=category)
                self.assertEqual([item["id"] for item in items], [expected_id])

    def test_anime_film_category_uses_the_movie_pool(self):
        items = anime_engine.get_anime_catalog(genre="Films d'Animation")
        self.assertEqual([item["id"] for item in items], ["movie-1"])

    def test_episode_without_verifiable_filler_source_is_unknown(self):
        for title in ("Naruto", "Steins;Gate", "A New Anime"):
            with self.subTest(title=title):
                result = anime_engine.is_episode_filler(title, 1)
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["is_filler"])
                self.assertIsNone(result["source"])


if __name__ == "__main__":
    unittest.main()
