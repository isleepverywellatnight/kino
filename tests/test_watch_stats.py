"""Mes statistiques: what KINO's history allows to count, and nothing more."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app
import kino_db
import watch_stats

TODAY = datetime(2026, 11, 10, tzinfo=timezone.utc)


def stamp(year, month, day=15):
    return datetime(year, month, day, tzinfo=timezone.utc).timestamp()


HISTORY = [
    {"id": "tt1", "type": "movie", "name": "Les Évadés", "year": "1994", "completed": True, "duration": 8520, "position": 8400,
     "last_watched": stamp(2026, 10)},
    {"id": "tt2", "type": "movie", "name": "Importé", "year": "2002", "imported_watched": True, "completed": True,
     "duration": 7200, "position": 7200, "last_watched": stamp(2026, 11)},
    {"id": "tt3", "type": "movie", "name": "En cours", "year": "2026", "duration": 6000, "position": 1200,
     "last_watched": stamp(2026, 11)},
    {"id": "tt4", "type": "series", "name": "Série finie", "year": "2008", "completed": True, "progress_pct": 100,
     "series_finished": True, "watched_episodes": ["S01E01", "s1e2", "s01e02"],
     "ep_positions": {"s1e2": {"position": 3000, "duration": 3000, "completed": True}}, "last_watched": stamp(2025, 12)},
    {"id": "tt5", "type": "series", "name": "Série en cours", "year": "2025", "watched_episodes": [],
     "ep_positions": {"s2e1": {"position": 600, "duration": 3300}, "s1e9": {"position": 3200, "duration": 3200, "completed": True}},
     "last_watched": stamp(2024, 1)},
    {"id": "", "type": "movie"}, "bad",
]
GENRES = {"tt1": ["Drama", "Crime"], "tt2": ["Drama"], "tt4": ["Crime", "Drama", "Drama"], "tt5": []}


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self.stats = watch_stats.compute(HISTORY, lambda entry: GENRES.get(entry["id"], []), today=TODAY)

    def test_totals(self):
        self.assertEqual(self.stats["films"], {"seen": 2, "started": 1, "imported": 1})
        self.assertEqual(self.stats["episodes"], {"seen": 3}, "two codes for the finished series, one played to the end")
        self.assertEqual(self.stats["series"], {"total": 2, "started": 1, "finished": 1})
        self.assertEqual(self.stats["titles_seen"], 4)

    def test_viewing_time_leaves_imported_films_out(self):
        expected = 8520 + 1200 + 3000 + 600 + 3200
        self.assertEqual(self.stats["watch_seconds"], expected)

    def test_genres_count_each_title_once(self):
        self.assertEqual(self.stats["genres"], [{"name": "Drama", "count": 3}, {"name": "Crime", "count": 2}])
        self.assertEqual(self.stats["genres_known"], 3)

    def test_months_cover_the_last_twelve_without_imports(self):
        months = self.stats["months"]
        self.assertEqual([m["month"] for m in months][:2] + [months[-1]["month"]], ["2025-12", "2026-01", "2026-11"])
        self.assertEqual(len(months), 12)
        counts = {m["month"]: m["count"] for m in months if m["count"]}
        self.assertEqual(counts, {"2025-12": 1, "2026-10": 1, "2026-11": 1}, "the import and January 2024 are left out")

    def test_decades_and_the_oldest_film(self):
        self.assertEqual(self.stats["decades"], [{"decade": 1990, "count": 1}, {"decade": 2000, "count": 2}, {"decade": 2020, "count": 1}])
        self.assertEqual(self.stats["oldest"], {"id": "tt1", "name": "Les Évadés", "year": 1994})

    def test_an_empty_history(self):
        stats = watch_stats.compute([], today=TODAY)
        self.assertEqual((stats["titles_seen"], stats["watch_seconds"], stats["oldest"], len(stats["months"])), (0, 0, None, 12))


class RouteTests(unittest.TestCase):
    def test_genres_from_the_catalogue_then_the_details(self):
        history = [{"id": "tt1", "type": "movie", "completed": True}, {"id": "tt2", "type": "series", "completed": True,
                                                                         "watched_episodes": ["s1e1"]},
                   {"id": "tt3", "type": "movie", "completed": True}, {"id": "kitsu:1", "type": "series"}]
        asked = []

        def details(imdb_id, kind):
            asked.append((imdb_id, kind))
            if imdb_id == "tt3":
                raise OSError("network down")
            return {"genres": ["Animation"]}

        with patch.object(kino_db, "db_media_genres", return_value={"tt1": ["Drama"]}), \
                patch.object(app, "get_media_meta", side_effect=details):
            stats = app.viewing_stats(history)
        self.assertEqual(sorted(asked), [("tt2", "series"), ("tt3", "movie")])
        self.assertEqual(stats["genres"], [{"name": "Animation", "count": 1}, {"name": "Drama", "count": 1}])
        self.assertEqual(stats["genres_known"], 2)

    def test_the_catalogue_genres_are_read_as_a_list(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(kino_db, "DB_PATH", Path(folder) / "kino.db"):
            kino_db.init_db()
            kino_db.db_index_media([{"id": "tt1", "name": "Heat", "type": "movie", "genres": ["Action", "Crime"], "poster": "p"},
                                    {"id": "tt2", "name": "Rien", "type": "movie"}])
            self.assertEqual(kino_db.db_media_genres(["tt1", "tt2", "tt9", "tt1"]), {"tt1": ["Action", "Crime"]})
            self.assertEqual(kino_db.db_media_genres([]), {})


if __name__ == "__main__":
    unittest.main()
