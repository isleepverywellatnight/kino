"""Instant search finds titles whatever their punctuation."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import kino_db

TITLES = [
    ("tt0145487", "Spider-Man"), ("tt0075686", "L'Homme qui tua Liberty Valance"),
    ("tt0117060", "Mission: Impossible"), ("tt0211915", "Le Fabuleux Destin d'Amélie Poulain"),
    ("tt0910970", "WALL·E"), ("tt0110912", "Pulp Fiction"),
]


class FastSearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(kino_db, "DB_PATH", Path(temp.name) / "kino.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        kino_db.init_db()
        kino_db.db_index_media([{"id": i, "name": n, "type": "movie", "year": "2000"} for i, n in TITLES])

    def names(self, query, media_type=None):
        return [row["name"] for row in kino_db.db_search_fast(query, media_type=media_type)]

    def test_punctuated_titles_are_found_as_typed(self):
        cases = {
            "spider-man": "Spider-Man", "spiderman": "Spider-Man", "l'homme": "L'Homme qui tua Liberty Valance",
            "mission: impossible": "Mission: Impossible", "d'amélie": "Le Fabuleux Destin d'Amélie Poulain",
            "walle": "WALL·E", "wall-e": "WALL·E",
        }
        for query, title in cases.items():
            with self.subTest(query=query):
                self.assertIn(title, self.names(query))

    def test_odd_input_is_harmless(self):
        for query in ("", "   ", "%%%", "'\"", "a_b", "NEAR(", "pulp*"):
            with self.subTest(query=query):
                self.assertIsInstance(kino_db.db_search_fast(query), list)
        self.assertEqual(self.names("%%%"), [])
        self.assertEqual(self.names("pulp", media_type="series"), [])


if __name__ == "__main__":
    unittest.main()
