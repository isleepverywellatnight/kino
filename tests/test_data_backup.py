"""A copy a day of the user's data, the 7 newest kept, and « Restaurer ».
Everything happens in a temporary folder."""
import json
import os
import sqlite3
import stat
import tempfile
import time
import sys
import unittest
from pathlib import Path

import data_backup as backup

DAY = 86400.0
NOON = time.mktime((2026, 11, 12, 12, 0, 0, 0, 0, -1))


def make_db(path, watchlist=("tt1",), history=(("tt2", {"completed": True, "user_rating": "4"}), ("tt3", {"progress_pct": 40})),
            rating_column=True):
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("PRAGMA journal_mode = WAL")
        extra = ", user_rating TEXT" if rating_column else ""
        conn.execute(f"CREATE TABLE watchlist (id TEXT PRIMARY KEY, name TEXT, added_at REAL{extra})")
        conn.execute("CREATE TABLE watch_history (id TEXT PRIMARY KEY, data_json TEXT, last_watched REAL)")
        conn.execute("CREATE TABLE app_config (key TEXT PRIMARY KEY, value_json TEXT, updated_at TIMESTAMP)")
        conn.execute("CREATE TABLE taste_feedback (media_id TEXT PRIMARY KEY, decision TEXT, at REAL)")
        conn.execute("CREATE TABLE persistent_cache (cache_key TEXT PRIMARY KEY, value_json TEXT, expires_at REAL)")
        conn.execute("CREATE TABLE taste_pool (id TEXT PRIMARY KEY, data TEXT)")
        for item in watchlist:
            conn.execute("INSERT INTO watchlist (id, name, added_at) VALUES (?, ?, 1)", (item, item.upper()))
        for item, entry in history:
            conn.execute("INSERT INTO watch_history VALUES (?, ?, 1)", (item, json.dumps(entry)))
        conn.execute("INSERT INTO app_config VALUES ('spoiler_protection_v1', '{\"on\": true}', 0)")
        conn.execute("INSERT INTO taste_feedback VALUES ('tt9', 'not_for_me', 1)")
        conn.executemany("INSERT INTO persistent_cache VALUES (?, ?, 0)", [(f"k{i}", "x" * 500) for i in range(50)])
        conn.executemany("INSERT INTO taste_pool VALUES (?, ?)", [(f"tt{i}", "y" * 500) for i in range(100, 150)])
    conn.close()


def rows(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class BackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / ".kino_data.db"
        self.config = self.root / ".rd_cinehub_config.json"
        make_db(self.db)
        self.config.write_text(json.dumps({
            "rd_token": "SECRET-RD", "provider_tokens": {"realdebrid": "SECRET-RD"}, "tmdb_key": "SECRET-TMDB",
            "pref_lang": "vf", "custom_lists": [{"name": "Noël"}], "window_bounds": {"w": 1200}, "download_dir": "/Volumes/A",
        }))
        self.now = [NOON]
        self.backups = backup.Backups(self.root / "Backups", self.db, self.config, clock=lambda: self.now[0],
                                      version="1.1.101", data_schema=1)

    def test_a_copy_holds_the_data_without_caches_or_keys(self):
        entry = self.backups.make()
        folder = self.root / "Backups" / "2026-11-12"
        self.assertEqual(entry["id"], "2026-11-12")
        self.assertEqual(entry["counts"], {"watchlist": 1, "seen": 1, "in_progress": 1, "rated": 1})
        copy = folder / "kino_data.db"
        self.assertEqual(rows(copy, "SELECT id FROM watchlist"), [("tt1",)])
        self.assertEqual(rows(copy, "SELECT COUNT(*) FROM persistent_cache")[0][0], 0, "caches are emptied")
        self.assertEqual(rows(copy, "SELECT COUNT(*) FROM taste_pool")[0][0], 0)
        self.assertLess(copy.stat().st_size, self.db.stat().st_size)
        settings = (folder / "config.json").read_text(encoding="utf-8")
        self.assertNotIn("SECRET", settings, "a copy never holds a key")
        self.assertEqual(json.loads(settings)["custom_lists"], [{"name": "Noël"}])
        if sys.platform != "win32":  # Windows has no owner-only rights to check this way.
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in (self.root / "Backups").iterdir()], ["2026-11-12"], "no work folder left")

    def test_one_a_day_the_seven_newest_kept(self):
        self.assertTrue(self.backups.due())
        self.backups.tick()
        self.assertFalse(self.backups.due())
        self.assertIsNone(self.backups.tick(), "once a day")
        self.now[0] += 3600
        self.backups.make()  # « Sauvegarder maintenant » replaces today's copy
        self.assertEqual(len(self.backups.list()), 1)
        for _ in range(9):
            self.now[0] += DAY
            self.assertTrue(self.backups.due())
            self.backups.tick()
        ids = [e["id"] for e in self.backups.list()]
        self.assertEqual(len(ids), backup.KEEP_DAILY)
        self.assertEqual(ids[0], "2026-11-21")
        self.assertEqual(ids[-1], "2026-11-15")

    def test_restore_puts_the_data_back_and_keeps_the_present_first(self):
        self.backups.make()
        # Later that day: Ma Liste emptied by mistake, a new key, a new window size.
        conn = sqlite3.connect(self.db)
        with conn:
            conn.execute("DELETE FROM watchlist")
            conn.execute("DELETE FROM taste_feedback")
            conn.execute("INSERT INTO watch_history VALUES ('tt7', '{}', 2)")
        conn.close()
        settings = json.loads(self.config.read_text())
        settings.update(rd_token="NEW-RD", pref_lang="vo", window_bounds={"w": 900})
        settings.pop("custom_lists")
        self.config.write_text(json.dumps(settings))
        self.now[0] += 60
        result = self.backups.restore("2026-11-12")
        self.assertEqual(rows(self.db, "SELECT id, name FROM watchlist"), [("tt1", "TT1")])
        self.assertEqual(sorted(r[0] for r in rows(self.db, "SELECT id FROM watch_history")), ["tt2", "tt3"])
        self.assertEqual(rows(self.db, "SELECT media_id FROM taste_feedback"), [("tt9",)])
        self.assertEqual(rows(self.db, "SELECT COUNT(*) FROM persistent_cache")[0][0], 50, "the caches stay as they are")
        restored = json.loads(self.config.read_text())
        self.assertEqual((restored["pref_lang"], restored["custom_lists"]), ("vf", [{"name": "Noël"}]))
        self.assertEqual((restored["rd_token"], restored["window_bounds"]), ("NEW-RD", {"w": 900}),
                         "the present keys and this Mac's own settings are kept")
        self.assertEqual(restored["tmdb_key"], "SECRET-TMDB")
        before = result["before"]
        self.assertTrue(before["id"].startswith("avant-restauration-2026-11-12-"))
        self.assertEqual(before["counts"]["watchlist"], 0, "the state just before is kept, to go back")
        self.assertEqual({e["kind"] for e in self.backups.list()}, {"daily", "before_restore"})
        self.assertFalse(self.config.with_name(self.config.name + ".restoring").exists())

    def test_restore_from_an_older_table_layout(self):
        old = self.root / "old.db"
        make_db(old, watchlist=("tt5",), rating_column=False)
        self.backups.make()
        folder = self.root / "Backups" / "2026-11-12"
        (folder / "kino_data.db").unlink()
        os.replace(old, folder / "kino_data.db")
        self.backups.restore("2026-11-12")
        self.assertEqual(rows(self.db, "SELECT id, user_rating FROM watchlist"), [("tt5", None)])

    def test_restore_refuses_what_is_not_a_copy(self):
        self.backups.make()
        for wrong in ("../.kino_data.db", "2026-11-11", "Backups", ""):
            with self.assertRaises(ValueError):
                self.backups.restore(wrong)
        manifest = self.root / "Backups" / "2026-11-12" / "backup.json"
        data = json.loads(manifest.read_text())
        manifest.write_text(json.dumps({**data, "data_schema": 2}))
        self.assertFalse(self.backups.list()[0]["restorable"])
        with self.assertRaises(ValueError):
            self.backups.restore("2026-11-12")

    def test_never_raises_from_the_daily_check(self):
        errors = []
        broken = backup.Backups(self.root / "Backups", self.root / "missing.db", self.config, clock=lambda: NOON,
                                on_error=errors.append)
        self.assertIsNone(broken.tick())
        self.root.joinpath("Backups").mkdir()
        self.db.write_bytes(b"not a database")
        self.assertIsNone(self.backups.tick())

    def test_settings_helpers(self):
        self.assertTrue(all(backup.is_secret(k) for k in ("rd_token", "provider_tokens", "tmdb_key", "trakt_auth", "password")))
        self.assertFalse(backup.is_secret("pref_lang"))
        self.assertEqual(backup.restored_settings({"pref_lang": "vf", "rd_token": "OLD", "tv_device": {"a": 1}},
                                                  {"rd_token": "NOW", "tv_device": {"b": 2}, "pref_lang": "vo"}),
                         {"pref_lang": "vf", "rd_token": "NOW", "tv_device": {"b": 2}})


if __name__ == "__main__":
    unittest.main()
