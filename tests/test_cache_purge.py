"""Expired cache rows are purged and the database file shrinks back."""

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import kino_db


class CachePurgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "kino.db"
        patcher = patch.object(kino_db, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        kino_db.init_db()

    def size(self):
        files = (self.db, Path(str(self.db) + "-wal"))
        return sum(path.stat().st_size for path in files if path.exists())

    def test_expired_searches_are_removed_and_space_is_reclaimed(self):
        big = [{"title": "x" * 2000, "n": n} for n in range(300)]
        for n in range(20):
            kino_db.db_cache_set(f"stream_v9:old:{n}", big, ttl_sec=1)
        kino_db.db_cache_set("meta_fr_v2:movie:tt1", {"name": "Film"}, ttl_sec=3600)
        kino_db.db_cache_set("forever", {"ok": True}, ttl_sec=0)
        size_before = self.size()
        removed = kino_db.db_cache_purge_expired(time.time() + 5, compact=True)
        self.assertEqual(removed, 20)
        self.assertEqual(kino_db.db_cache_get("meta_fr_v2:movie:tt1"), {"name": "Film"})
        self.assertEqual(kino_db.db_cache_get("forever"), {"ok": True})
        self.assertLess(self.size(), size_before / 4)

    def test_cache_writes_purge_periodically(self):
        kino_db.db_cache_set("old", {"v": 1}, ttl_sec=1)
        with patch.object(kino_db, "_last_cache_purge", 0.0), patch("kino_db.time.time", return_value=time.time() + 10):
            kino_db.db_cache_set("new", {"v": 2}, ttl_sec=60)
        conn = kino_db.get_connection()
        try:
            keys = [row[0] for row in conn.execute("SELECT cache_key FROM persistent_cache ORDER BY cache_key")]
        finally:
            conn.close()
        self.assertEqual(keys, ["new"])


if __name__ == "__main__":
    unittest.main()
