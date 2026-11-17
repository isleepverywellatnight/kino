"""KINO's own suggestions, on a small made-up pool whose right
answers are known: the same people first, genres and themes next, varied lists,
nothing seen or set aside, a reason for each suggestion."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import recommender as rec


def film(i, name, director="", cast=(), genres=("Drama",), year="2010", rating=7.5, words="", kind="movie", writer=""):
    return rec.compact({"imdb_id": f"tt{i:07d}", "name": name, "director": [director] if director else [],
                        "writer": [writer] if writer else [], "cast": list(cast), "genres": list(genres),
                        "year": year, "imdbRating": str(rating), "description": words, "country": "United States"}, kind)


POOL = [
    film(1, "Memento", "Christopher Nolan", ["Guy Pearce"], ["Mystery", "Thriller"], "2000", 8.4, "memory loss revenge tattoo"),
    film(2, "Inception", "Christopher Nolan", ["Leonardo DiCaprio"], ["Action", "Sci-Fi"], "2010", 8.8, "dream heist subconscious"),
    film(3, "Interstellar", "Christopher Nolan", ["Matthew McConaughey"], ["Sci-Fi", "Drama"], "2014", 8.7, "space wormhole farmer"),
    film(4, "The Prestige", "Christopher Nolan", ["Hugh Jackman"], ["Mystery", "Drama"], "2006", 8.5, "magician rivalry illusion"),
    film(5, "Tenet", "Christopher Nolan", ["John David Washington"], ["Action", "Sci-Fi"], "2020", 7.3, "time inversion spy"),
    film(6, "Shutter Island", "Martin Scorsese", ["Leonardo DiCaprio"], ["Mystery", "Thriller"], "2010", 8.2, "island asylum marshal memory"),
    film(7, "The Departed", "Martin Scorsese", ["Leonardo DiCaprio", "Matt Damon"], ["Crime", "Thriller"], "2006", 8.5, "undercover police mole"),
    film(8, "Gone Girl", "David Fincher", ["Ben Affleck"], ["Mystery", "Thriller"], "2014", 8.1, "missing wife marriage mystery"),
    film(9, "Mamma Mia", "Phyllida Lloyd", ["Meryl Streep"], ["Comedy", "Musical"], "2008", 6.5, "wedding island singing"),
    film(10, "Toy Story", "John Lasseter", ["Tom Hanks"], ["Animation", "Comedy"], "1995", 8.3, "toys cowboy friendship"),
    film(11, "Cars", "John Lasseter", ["Owen Wilson"], ["Animation", "Comedy"], "2006", 7.2, "race car town"),
    film(12, "Breaking Bad", "", ["Bryan Cranston"], ["Crime", "Drama"], "2008", 9.5, "chemistry teacher drug", "series", "Vince Gilligan"),
    film(13, "Better Call Saul", "", ["Bob Odenkirk"], ["Crime", "Drama"], "2015", 9.0, "lawyer con", "series", "Vince Gilligan"),
    film(14, "Malcolm in the Middle", "", ["Bryan Cranston"], ["Comedy"], "2000", 8.2, "family genius kid", "series"),
]


class TraitTests(unittest.TestCase):
    def test_a_record_becomes_its_traits(self):
        entry = POOL[0]
        self.assertEqual((entry["id"], entry["type"], entry["year"], entry["rating"]), ("tt0000001", "movie", "2000", 8.4))
        traits = rec.traits(entry)
        for trait in ("g:mystery", "d:christopher nolan", "c:guy pearce", "dec:2000", "k:memory", "co:united states"):
            self.assertIn(trait, traits)
        self.assertEqual(rec.description_words("The Matrix: a hacker discovers the world is a simulation and fights back"),
                         ["matrix", "hacker", "simulation", "fight"], "no small or common words, plurals folded")

    def test_rare_traits_weigh_more(self):
        space = rec.Space(POOL)
        self.assertGreater(space.idf["d:christopher nolan"] * 0 + space.idf["c:guy pearce"], space.idf["g:drama"])
        self.assertAlmostEqual(sum(v * v for v in space.vector(POOL[0]).values()), 1.0, places=6)

    def test_a_bad_record_is_left_out(self):
        self.assertIsNone(rec.compact({"imdb_id": "kitsu:12", "name": "X"}, "movie"))
        self.assertIsNone(rec.compact({"imdb_id": "tt1"}, "movie"))


class RelatedTests(unittest.TestCase):
    def test_the_same_director_first_then_the_same_kind_of_film(self):
        space = rec.Space(POOL)
        items = rec.related(space, POOL[0], "movie", limit=6)
        names = [i["name"] for i in items]
        nolan = {"Inception", "Interstellar", "The Prestige", "Tenet"}
        self.assertGreaterEqual(len(set(names[:3]) & nolan), 2, names)
        self.assertLessEqual(sum(1 for n in names if n in nolan), rec.PER_PERSON,
                             "one director leads at most three titles of a list")
        self.assertIn("Shutter Island", names, "a mystery thriller about memory comes next")
        self.assertNotIn("Memento", names)
        self.assertEqual(next(i["why"] for i in items if i["name"] in nolan), "Même réalisation (Christopher Nolan)")
        self.assertNotIn("Mamma Mia", names[:4])

    def test_series_by_their_writer(self):
        space = rec.Space(POOL)
        items = rec.related(space, POOL[11], "series", limit=3)
        self.assertEqual(items[0]["name"], "Better Call Saul")
        self.assertEqual(items[0]["why"], "Même scénario (Vince Gilligan)")
        self.assertTrue(all(i["type"] == "series" for i in items))

    def test_reasons_in_french(self):
        self.assertEqual(rec.why_close(POOL[7], POOL[5]), "Mystère des années 2010")
        self.assertEqual(rec.why_close(POOL[6], POOL[5]), "Même réalisation (Martin Scorsese)")
        self.assertEqual(rec.why_close(POOL[10], POOL[9]), "Même réalisation (John Lasseter)")


class ForYouTests(unittest.TestCase):
    def setUp(self):
        self.space = rec.Space(POOL)
        self.known = {e["id"]: e for e in POOL}

    def test_a_taste_from_what_was_seen_and_rated(self):
        history = [{"id": "tt0000001", "completed": True, "user_rating": "5"},  # Memento, loved
                   {"id": "tt0000006", "imported_watched": True},              # Shutter Island, seen
                   {"id": "tt0000009", "completed": True, "user_rating": "1"}]  # Mamma Mia, disliked
        signals = rec.taste_signals(history, [], [], now=0)
        self.assertEqual(signals, {"tt0000001": 1.0, "tt0000006": 0.6, "tt0000009": -1.0})
        items = rec.for_you(self.space, signals, self.known, "movie", limit=5)
        names = [i["name"] for i in items]
        self.assertTrue(set(names) & {"Inception", "Interstellar", "The Prestige", "Gone Girl"})
        for seen in ("Memento", "Shutter Island", "Mamma Mia"):
            self.assertNotIn(seen, names)
        self.assertNotIn("Toy Story", names[:3])
        self.assertTrue(all(i["why"].startswith("Parce que vous avez aimé ") for i in items), "every suggestion says why")

    def test_not_for_me_counts_against_and_is_never_shown(self):
        history = [{"id": "tt0000001", "completed": True, "user_rating": "5"}]
        signals = rec.taste_signals(history, [], ["tt0000002"], now=0)
        self.assertEqual(signals["tt0000002"], -1.0)
        names = [i["name"] for i in rec.for_you(self.space, signals, self.known, "movie", limit=8)]
        self.assertNotIn("Inception", names)

    def test_weights(self):
        self.assertEqual(rec.title_weight(rating=5), 1.0)
        self.assertEqual(rec.title_weight(rating=1), -1.0)
        self.assertEqual(rec.title_weight({"completed": True}), 0.6)
        self.assertEqual(rec.title_weight({"progress_pct": 10, "last_watched": 1}, now=40 * 86400), -0.3, "started and left")
        self.assertEqual(rec.title_weight(in_list=True), 0.3)
        self.assertEqual(rec.title_weight(not_for_me=True), -1.0)

    def test_nothing_without_a_taste(self):
        self.assertEqual(rec.for_you(self.space, {}, self.known, "movie"), [])


class PoolTests(unittest.TestCase):
    def test_about_two_hundred_public_pages(self):
        pages = rec.pool_pages()
        self.assertTrue(150 <= len(pages) <= 260, len(pages))
        self.assertEqual(rec.page_url("movie", "top", "Thriller", 50), "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Thriller&skip=50.json")
        self.assertEqual(rec.page_url("series", "imdbRating", "", 0), "https://v3-cinemeta.strem.io/catalog/series/imdbRating.json")

    def test_pages_are_read_once_per_title_and_a_failing_page_is_skipped(self):
        def fetch(url):
            if "fail" in url:
                raise OSError("down")
            return {"metas": [{"imdb_id": "tt0000001", "name": "Memento"}, {"imdb_id": "tt0000002", "name": "Inception"}]}
        entries = rec.build_pool(fetch, pages=[("movie", "top", "", 0), ("movie", "top", "fail", 0), ("movie", "top", "", 50)], pause=0)
        self.assertEqual(sorted(e["id"] for e in entries), ["tt0000001", "tt0000002"])

    def test_the_store_keeps_the_pool_and_the_set_asides(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "db.sqlite"

        def connect():
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn
        store = rec.Store(connect, threading.Lock())
        self.assertEqual(store.pool_age(100), (0, None))
        store.save_pool(POOL, 100)
        self.assertEqual(store.pool_age(160), (len(POOL), 60))
        self.assertEqual(len(store.space().entries), len(POOL))
        store.set_not_for_me("tt0000002", 120)
        self.assertEqual(store.not_for_me(), ["tt0000002"])
        store.reset_feedback()
        self.assertEqual(store.not_for_me(), [])


if __name__ == "__main__":
    unittest.main()
