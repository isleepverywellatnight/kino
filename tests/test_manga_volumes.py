"""Whole volumes by torrent: only the chosen archive is downloaded, after the P2P
consent, then copied into the manga folder where the local reader finds it."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import kino_db
import manga
import manga_volumes
import p2p_engine

HASH = "a" * 40


class Storage:
    def __init__(self, names, sizes):
        self.names, self.sizes = names, sizes

    def num_files(self):
        return len(self.names)

    def file_path(self, index):
        return self.names[index]

    def file_size(self, index):
        return self.sizes[index]


class Handle:
    def __init__(self, storage):
        self.storage, self.priorities, self.done = storage, None, [0] * storage.num_files()

    def torrent_file(self):
        return self

    def files(self):
        return self.storage

    def prioritize_files(self, priorities):
        self.priorities = list(priorities)

    def file_progress(self):
        return self.done


class Engine:
    def __init__(self, folder, handle):
        self.folder, self._handle, self.added = folder, handle, []

    def apply(self, choices):
        pass

    def handle(self, info_hash):
        return {"handle": self._handle} if self.added else None

    def add(self, magnet, info_hash):
        self.added.append(magnet)
        return self._handle

    def metadata(self, handle):
        return handle.torrent_file()


class VolumeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for target in (patch.object(kino_db, "DB_PATH", self.folder / "kino.db"),
                       patch.object(manga_volumes, "DEFAULT_FOLDER", self.folder / "KINO Manga"),
                       patch.dict(manga_volumes._JOBS, clear=True)):
            target.start()
            self.addCleanup(target.stop)
        kino_db.init_db()
        names = ["Frieren/Frieren v02.cbz", "Frieren/Frieren v01.cbz", "Frieren/notes.txt"]
        self.handle = Handle(Storage(names, [100, 200, 5]))
        self.engine = Engine(self.folder / "cache", self.handle)
        for target in (patch.object(p2p_engine, "engine", lambda: self.engine), patch.object(p2p_engine, "_ENGINE", self.engine)):
            target.start()
            self.addCleanup(target.stop)

    def test_nothing_before_the_consent(self):
        with self.assertRaises(p2p_engine.P2PConsentNeeded):
            manga_volumes.files(HASH)
        with self.assertRaises(p2p_engine.P2PConsentNeeded):
            manga_volumes.start(HASH, 1, "Frieren")
        self.assertEqual(self.engine.added, [], "no torrent opened")

    def test_the_chosen_volume_is_downloaded_then_kept_in_the_manga_folder(self):
        p2p_engine.set_consent(True)
        self.assertEqual([f["name"] for f in manga_volumes.files(HASH)], ["Frieren v01.cbz", "Frieren v02.cbz"], "archives only, in order")
        self.assertTrue(self.engine.added[0].startswith(f"magnet:?xt=urn:btih:{HASH}"))
        state = manga_volumes.start(HASH, 1, "Frieren: Beyond/Journey")
        self.assertEqual(self.handle.priorities, [0, 7, 0], "only the chosen volume")
        self.assertEqual((state["progress"], state["done"]), (0.0, False))
        self.handle.done[1] = 100
        self.assertEqual(manga_volumes.status(HASH, 1)["progress"], 0.5)

        source = self.folder / "cache" / "Frieren" / "Frieren v01.cbz"
        source.parent.mkdir(parents=True)
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("001.png", b"\x89PNG page")
        self.handle.done[1] = 200
        state = manga_volumes.status(HASH, 1)
        self.assertTrue(state["done"])
        kept = self.folder / "KINO Manga" / "Frieren  Beyond Journey" / "Frieren v01.cbz"
        self.assertTrue(kept.is_file(), "copied into the manga folder, chosen for the user when they had none")
        self.assertEqual(manga.folder(), self.folder / "KINO Manga")
        series = manga.local_series()["series"]
        self.assertEqual((series[0]["id"], series[0]["chapters"]), (state["local"], 1), "read by the local reader")

    def test_a_refused_request(self):
        p2p_engine.set_consent(True)
        for bad in (("x" * 40, 0), (HASH, 2), (HASH, 9)):
            with self.assertRaises(manga.MangaError):
                manga_volumes.start(*bad, "Frieren")
        with self.assertRaises(manga.MangaError):
            manga_volumes.status(HASH, 0)


if __name__ == "__main__":
    unittest.main()
