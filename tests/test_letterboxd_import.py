"""Letterboxd import into Ma Liste and the history, on an isolated database.

Run the suite with a temporary HOME: importing kino_db starts a startup sync.
Letterboxd and Cinemeta are simulated; any real network access fails the test.
"""

import sqlite3
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import config
import kino_db
import meta_engine

# Films known to the simulated Cinemeta: title -> (IMDb ID, year).
FILMS = {
    "Alien": ("tt0078748", "1979"),
    "Blade Runner": ("tt0083658", "1982"),
    "Chinatown": ("tt0071315", "1974"),
    "Dune": ("tt1160419", "2021"),
    "Eraserhead": ("tt0074486", "1977"),
    "Fargo": ("tt0116282", "1996"),
    "Gattaca": ("tt0119177", "1997"),
    "Heat": ("tt0113277", "1995"),
    "Ikiru": ("tt0044741", "1952"),
}


# Searches whose results are not the film itself: the catalogue's
# answers for a title, by type.
SEARCHES = {
    ("Lupin", "movie"): [("tt0079833", "Lupin III: The Castle of Cagliostro", "1979"), ("tt0080388", "Lupin III: The Secret of Mamo", "1978")],
    ("Lupin", "series"): [("tt2531336", "Lupin", "2021–")],
    ("Berserk", "movie"): [("tt0061398", "Berserk", "1967")],
    ("Berserk", "series"): [("tt0318871", "Berserk", "1997–1998")],
    ("Au revoir les enfants", "movie"): [("tt0092593", "Goodbye, Children", "1987")],
    ("Nowhere Film", "movie"): [("tt0000123", "Somewhere Else", "1950")],
}


def fake_search_cinemeta(query, media_type="movie"):
    found = SEARCHES.get((query, media_type))
    if found is not None:
        return [{"id": i, "name": n, "releaseInfo": y, "poster": "", "imdbRating": "7"} for i, n, y in found]
    film = FILMS.get(query)
    if not film or media_type != "movie":
        return []
    imdb_id, year = film
    return [{"id": imdb_id, "name": query, "releaseInfo": year,
             "poster": f"https://example.invalid/{imdb_id}.jpg", "imdbRating": "7.5"}]


def entries(*titles, rating=""):
    return [{"title": t, "year": FILMS[t][1], "imdb_id": "", "user_rating": rating} for t in titles]


def film_id(title):
    return FILMS[title][0]


class LetterboxdImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for target, attr, value in (
            (kino_db, "DB_PATH", root / "kino.db"),
            (config, "CONFIG_FILE", root / "config.json"),
            (kino_db, "find_gdrive_sync_dir", lambda: None),
            (meta_engine, "search_cinemeta", fake_search_cinemeta),
        ):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self.network_calls = []

        def no_network(*args, **kwargs):
            self.network_calls.append(args[0] if args else kwargs)
            raise OSError("network access is not allowed in this test")

        p = patch.object(urllib.request, "urlopen", no_network)
        p.start()
        self.addCleanup(p.stop)
        kino_db.init_db()
        self.letterboxd = {"watchlist": [], "watched": []}

    def tearDown(self):
        self.assertEqual(self.network_calls, [])

    def run_import(self, watchlist=(), watched=(), restore_removed=False):
        self.letterboxd = {"watchlist": list(watchlist), "watched": list(watched)}

        def fake_fetch(url_or_user, max_pages=5, target_section="watchlist"):
            return [dict(e) for e in self.letterboxd[target_section]]

        payload = {"url_or_user": "cinephile", "mode": "both"}
        if restore_removed:
            payload["restore_removed"] = True
        with patch.object(meta_engine, "fetch_letterboxd_entries_from_url", fake_fetch):
            return meta_engine.import_letterboxd_watchlist(payload)

    def watchlist_ids(self):
        return [item["id"] for item in kino_db.db_get_watchlist()]

    def history_ids(self):
        return [entry["id"] for entry in kino_db.db_get_history()]

    def history_entry(self, title):
        return next(e for e in kino_db.db_get_history() if e["id"] == film_id(title))

    def raw_rows(self, table, order):
        conn = sqlite3.connect(str(kino_db.DB_PATH))
        try:
            return conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
        finally:
            conn.close()

    def add_to_watchlist(self, title):
        kino_db.db_toggle_watchlist({"id": film_id(title), "name": title, "type": "movie"})

    def play(self, title, position, duration=6000):
        kino_db.db_record_history({"id": film_id(title), "name": title, "type": "movie",
                                   "position": position, "duration": duration,
                                   "progress_pct": round(100.0 * position / duration, 1)})

    def test_import_saves_watchlist_and_watched_films_in_the_database(self):
        res = self.run_import(watchlist=entries("Alien", "Blade Runner"),
                              watched=entries("Chinatown", "Dune", rating="4.5"))

        self.assertEqual(self.watchlist_ids(), [film_id("Alien"), film_id("Blade Runner")])
        self.assertEqual(set(self.history_ids()), {film_id("Chinatown"), film_id("Dune")})
        chinatown = self.history_entry("Chinatown")
        self.assertTrue(chinatown["completed"])
        self.assertTrue(chinatown["imported_watched"])
        self.assertEqual(chinatown["user_rating"], "4.5")
        self.assertEqual((res["added_wl_count"], res["added_watched_count"]), (2, 2))
        # The API answer is what the database now holds, not an in-memory copy.
        self.assertEqual(res["watchlist"], kino_db.db_get_watchlist())
        self.assertEqual(res["history"], kino_db.db_get_history())

    def test_import_adds_only_new_films_to_an_existing_watchlist(self):
        self.add_to_watchlist("Heat")
        self.add_to_watchlist("Alien")
        before = self.raw_rows("watchlist", "id")

        res = self.run_import(watchlist=entries("Alien", "Blade Runner"))

        self.assertEqual(set(self.watchlist_ids()), {film_id("Heat"), film_id("Alien"), film_id("Blade Runner")})
        self.assertEqual(self.watchlist_ids()[0], film_id("Blade Runner"))
        after = {row[0]: row for row in self.raw_rows("watchlist", "id")}
        for row in before:
            self.assertEqual(after[row[0]], row)  # same date, name and ratings
        self.assertEqual(res["added_wl_count"], 1)

    def test_import_keeps_history_progress_and_lists_imported_films_after_it(self):
        self.play("Eraserhead", 1200)  # in progress, 20 %
        self.play("Fargo", 6000)       # watched in KINO
        before = {e["id"]: e for e in kino_db.db_get_history()}
        before_rows = self.raw_rows("watch_history", "id")

        res = self.run_import(watched=entries("Eraserhead", "Fargo", "Gattaca", rating="5"))

        self.assertEqual(self.history_ids(), [film_id("Fargo"), film_id("Eraserhead"), film_id("Gattaca")])
        for item_id, entry in before.items():
            self.assertEqual(self.history_entry_by_id(item_id), entry)
        after_rows = {row[0]: row for row in self.raw_rows("watch_history", "id")}
        for row in before_rows:
            self.assertEqual(after_rows[row[0]], row)
        eraserhead = self.history_entry("Eraserhead")
        self.assertEqual((eraserhead["position"], eraserhead["completed"]), (1200, False))
        self.assertNotIn("imported_watched", eraserhead)
        self.assertEqual(res["added_watched_count"], 1)

    def history_entry_by_id(self, item_id):
        return next(e for e in kino_db.db_get_history() if e["id"] == item_id)

    def test_importing_twice_changes_nothing(self):
        self.run_import(watchlist=entries("Alien"), watched=entries("Chinatown"))
        wl_rows = self.raw_rows("watchlist", "id")
        hist_rows = self.raw_rows("watch_history", "id")

        res = self.run_import(watchlist=entries("Alien"), watched=entries("Chinatown"))

        self.assertEqual(self.raw_rows("watchlist", "id"), wl_rows)
        self.assertEqual(self.raw_rows("watch_history", "id"), hist_rows)
        self.assertEqual((res["added_wl_count"], res["added_watched_count"]), (0, 0))

    def test_import_does_not_bring_back_films_removed_in_kino(self):
        self.add_to_watchlist("Ikiru")
        self.add_to_watchlist("Ikiru")       # removed from Ma Liste
        self.play("Heat", 6000)
        config.remove_history(film_id("Heat"))  # removed from Déjà vus

        res = self.run_import(watchlist=entries("Ikiru", "Alien"), watched=entries("Heat", "Chinatown"))

        self.assertEqual(self.watchlist_ids(), [film_id("Alien")])
        self.assertEqual(self.history_ids(), [film_id("Chinatown")])
        self.assertEqual((res["added_wl_count"], res["added_watched_count"]), (1, 1))
        # the films left out are counted, so the page can offer them.
        self.assertEqual((res["removed_wl_count"], res["removed_watched_count"]), (1, 1))

    def test_asked_reimport_brings_back_a_cleared_history(self):
        # 2026-11-12: the user cleared « Déjà vus », then imported 50+ films seen on
        # Letterboxd; only the few never removed came back, and nothing said why.
        self.run_import(watched=entries("Alien", "Chinatown", "Fargo"))
        self.play("Heat", 6000)
        kino_db.db_clear_history()
        self.play("Gattaca", 1200)  # a real play after the clear

        first = self.run_import(watched=entries("Alien", "Chinatown", "Fargo", "Ikiru"))
        self.assertEqual((first["added_watched_count"], first["removed_watched_count"]), (1, 3))
        self.assertEqual(set(self.history_ids()), {film_id("Gattaca"), film_id("Ikiru")})

        again = self.run_import(watched=entries("Alien", "Chinatown", "Fargo", "Ikiru"), restore_removed=True)
        self.assertEqual((again["added_watched_count"], again["removed_watched_count"]), (3, 0))
        ids = self.history_ids()
        self.assertEqual(ids[0], film_id("Gattaca"), "a re-imported film never passes a real play")
        self.assertEqual(set(ids), {film_id(t) for t in ("Gattaca", "Ikiru", "Alien", "Chinatown", "Fargo")})
        alien = self.history_entry("Alien")
        self.assertTrue(alien["completed"] and alien["imported_watched"])
        self.assertIn("reimported_at", alien)
        self.assertNotIn("Heat", [e.get("name") for e in kino_db.db_get_history()], "only what Letterboxd lists comes back")
        conn = kino_db.get_connection()
        try:
            markers = kino_db._load_deletions(conn)["history"]
        finally:
            conn.close()
        self.assertEqual({film_id(t) for t in ("Alien", "Chinatown", "Fargo")} & set(markers), set())
        self.assertIn(film_id("Heat"), markers)

    def test_a_title_found_as_a_series_is_the_series_seen_to_the_end(self):
        # 2026-11-12: « Lupin » (2021) came in as « The Castle of Cagliostro » (1979).
        res = self.run_import(watched=[{"title": "Lupin", "year": "2021", "imdb_id": "", "user_rating": "4"},
                                       {"title": "Berserk", "year": "1997", "imdb_id": "", "user_rating": ""},
                                       {"title": "Au revoir les enfants", "year": "1987", "imdb_id": "", "user_rating": ""}])
        self.assertEqual(set(self.history_ids()), {"tt2531336", "tt0318871", "tt0092593"})
        lupin = self.history_entry_by_id("tt2531336")
        self.assertEqual((lupin["type"], lupin["series_finished"], lupin["completed"], lupin["user_rating"]), ("series", True, True, "4"))
        self.assertEqual(lupin["letterboxd"], {"title": "Lupin", "year": "2021"})
        self.assertEqual(self.history_entry_by_id("tt0092593")["type"], "movie", "a film Letterboxd names in French stays found")
        self.assertEqual((res["added_watched_count"], res["unmatched_count"], res["corrected_watched_count"]), (3, 0, 0))

    def test_a_title_that_matches_nothing_is_left_out(self):
        res = self.run_import(watched=[{"title": "Nowhere Film", "year": "2019", "imdb_id": "", "user_rating": ""}] + entries("Alien"))
        self.assertEqual(self.history_ids(), [film_id("Alien")])
        self.assertEqual(res["unmatched_count"], 1)

    def test_a_wrong_film_of_an_earlier_import_is_taken_back(self):
        # What 1.1.90 and before added for « Lupin » (2021) and « Berserk » (1997).
        old = [{"id": i, "name": n, "type": "movie", "completed": True, "imported_watched": True, "progress_pct": 100.0}
               for i, n in (("tt0079833", "Lupin III: The Castle of Cagliostro"), ("tt0061398", "Berserk"))]
        kino_db.db_import_user_lists((), old)
        self.play("Alien", 3000)

        res = self.run_import(watched=[{"title": "Lupin", "year": "2021", "imdb_id": "", "user_rating": ""},
                                       {"title": "Berserk", "year": "1997", "imdb_id": "", "user_rating": ""}] + entries("Alien"))

        self.assertEqual(set(self.history_ids()), {film_id("Alien"), "tt2531336", "tt0318871"})
        self.assertEqual(res["corrected_watched_count"], 2)
        self.assertEqual(self.history_entry("Alien")["position"], 3000, "a real play is never touched")
        conn = kino_db.get_connection()
        try:
            markers = kino_db._load_deletions(conn)["history"]
        finally:
            conn.close()
        self.assertTrue({"tt0079833", "tt0061398"} <= set(markers), "the removal reaches the other device")

    def test_a_wrong_film_played_since_is_kept(self):
        kino_db.db_import_user_lists((), [{"id": "tt0079833", "name": "Lupin III: The Castle of Cagliostro", "type": "movie",
                                           "completed": True, "imported_watched": True, "progress_pct": 100.0}])
        kino_db.db_record_history({"id": "tt0079833", "name": "Lupin III", "type": "movie", "position": 600, "duration": 6000, "progress_pct": 10.0})
        res = self.run_import(watched=[{"title": "Lupin", "year": "2021", "imdb_id": "", "user_rating": ""}])
        self.assertIn("tt0079833", self.history_ids())
        self.assertEqual(res["corrected_watched_count"], 0)

    def test_reimported_films_survive_the_old_removal_still_on_drive(self):
        drive = kino_db.DB_PATH.parent / "Drive" / "KINO"
        drive.mkdir(parents=True)
        mac_db, windows_db = kino_db.DB_PATH, kino_db.DB_PATH.parent / "windows.db"

        def on(db_path, action, *args, **kwargs):
            with patch.object(kino_db, "DB_PATH", db_path), \
                 patch.object(kino_db, "find_gdrive_sync_dir", lambda: drive):
                return action(*args, **kwargs)

        on(windows_db, kino_db.init_db)
        on(mac_db, self.run_import, (), entries("Alien", "Fargo"))
        for device in (mac_db, windows_db):
            on(device, kino_db.db_sync_gdrive)
        on(mac_db, kino_db.db_clear_history)
        for device in (mac_db, windows_db, mac_db):
            on(device, kino_db.db_sync_gdrive)  # the removal is on Drive and on Windows
        self.assertEqual(on(windows_db, self.history_ids), [])

        on(mac_db, self.run_import, (), entries("Alien", "Fargo"), restore_removed=True)
        for device in (mac_db, windows_db, mac_db, windows_db):
            self.assertEqual(on(device, kino_db.db_sync_gdrive)["status"], "synced")

        for device in (mac_db, windows_db):
            self.assertEqual(set(on(device, self.history_ids)), {film_id("Alien"), film_id("Fargo")})

        # Removing one again still works, on both devices.
        on(mac_db, config.remove_history, film_id("Fargo"))
        for device in (mac_db, windows_db):
            on(device, kino_db.db_sync_gdrive)
        for device in (mac_db, windows_db):
            self.assertEqual(on(device, self.history_ids), [film_id("Alien")])

    def test_imported_film_never_overrides_real_progress_from_the_other_device(self):
        drive = kino_db.DB_PATH.parent / "Drive" / "KINO"
        drive.mkdir(parents=True)
        mac_db, windows_db = kino_db.DB_PATH, kino_db.DB_PATH.parent / "windows.db"

        def on(db_path, action, *args):
            with patch.object(kino_db, "DB_PATH", db_path), \
                 patch.object(kino_db, "find_gdrive_sync_dir", lambda: drive):
                return action(*args)

        on(windows_db, kino_db.init_db)
        real_time = time.time
        with patch.object(time, "time", lambda: real_time() - 3600):
            on(windows_db, self.play, "Dune", 2400)  # 40 % on Windows an hour ago, not synced yet
        on(mac_db, self.run_import, (), entries("Dune"))  # empty Mac history
        for device in (mac_db, windows_db, mac_db):
            self.assertEqual(on(device, kino_db.db_sync_gdrive)["status"], "synced")

        for device in (windows_db, mac_db):
            dune = on(device, self.history_entry, "Dune")
            self.assertEqual((dune["position"], dune["completed"]), (2400, False))
            self.assertNotIn("imported_watched", dune)

    def test_save_config_never_rewrites_the_lists(self):
        self.add_to_watchlist("Alien")
        self.play("Eraserhead", 1200)
        wl_rows = self.raw_rows("watchlist", "id")
        hist_rows = self.raw_rows("watch_history", "id")

        config.save_config({"watchlist": kino_db.db_get_watchlist(), "history": kino_db.db_get_history(),
                            "pref_lang": "vo"})

        self.assertEqual(self.raw_rows("watchlist", "id"), wl_rows)
        self.assertEqual(self.raw_rows("watch_history", "id"), hist_rows)


if __name__ == "__main__":
    unittest.main()
