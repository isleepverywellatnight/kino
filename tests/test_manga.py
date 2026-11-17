"""Manga: MangaDex series and chapters, the user's own files, where the reader stopped."""
import io
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import app
import kino_db
import manga

SERIES = "b0b721ff-c388-4486-aa0f-c2b0bb321512"
CHAPTER = "6e04e431-fc6b-4e38-b952-6232c72df112"


def manga_json(rating="safe"):
    return {"id": SERIES, "type": "manga", "attributes": {
        "title": {"ja-ro": "Sousou no Frieren"}, "altTitles": [{"fr": "Frieren"}, {"en": "Frieren: Beyond Journey's End"}],
        "description": {"en": "An elf mage.", "fr": "Une mage elfe."}, "year": 2020, "status": "ongoing", "contentRating": rating,
        "availableTranslatedLanguages": ["fr", "en", None], "tags": [{"attributes": {"name": {"en": "Fantasy"}}}],
        "links": {"al": "118586", "mu": "x"}},
        "relationships": [{"type": "cover_art", "attributes": {"fileName": "cover.jpg"}},
                          {"type": "author", "attributes": {"name": "Yamada Kanehito"}}]}


def chapter_json(number, chapter_id, group="Blue Solo", external=None):
    return {"id": chapter_id, "attributes": {"chapter": number, "volume": None, "title": f"Titre {number}", "pages": 20,
                                             "externalUrl": external},
            "relationships": [{"type": "scanlation_group", "attributes": {"name": group}}]}


class Fake:
    """Stands for api.mangadex.org; keeps the addresses KINO asked for."""

    def __init__(self):
        self.asked = []

    def __call__(self, url, headers=None, timeout=None, retries=None, method="GET", json_data=None):
        self.asked.append(url)
        if url == manga.ANILIST:
            self.covers_asked = json_data["variables"]["ids"]
            return {"data": {"Page": {"media": [{"id": 118586, "coverImage": {"large": "https://s4.anilist.co/frieren.jpg"}}]}}}
        path = urllib.parse.urlsplit(url).path
        if path == "/manga":
            return {"data": [manga_json()]}
        if path == f"/manga/{SERIES}":
            return {"data": manga_json()}
        if path == f"/manga/{SERIES}/feed":
            return {"total": 4, "data": [
                chapter_json("1", "00000000-0000-0000-0000-000000000001"),
                chapter_json("1", "00000000-0000-0000-0000-000000000002", group="Autre équipe"),
                chapter_json("2", "00000000-0000-0000-0000-000000000003", external="https://mangaplus.example/2"),
                chapter_json("3", CHAPTER)]}
        if path == f"/at-home/server/{CHAPTER}":
            return {"baseUrl": "https://abc.mangadex.network", "chapter": {"hash": "h1", "data": ["1.png", "2.png"]}}
        raise AssertionError(url)


class Database(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        database = patch.object(kino_db, "DB_PATH", self.folder / "kino.db")
        database.start()
        self.addCleanup(database.stop)
        kino_db.init_db()
        self.fake = Fake()
        for target in (patch.object(manga, "http_json", self.fake),
                       patch.object(manga, "cached_get", lambda key, ttl, fetch: fetch())):
            target.start()
            self.addCleanup(target.stop)


class MangaDexTests(Database):
    def test_search_and_the_first_page(self):
        found = manga.search("frieren", "fr")
        self.assertEqual(found, [{"id": SERIES, "source": "mangadex", "title": "Frieren",
                                  "cover": "https://s4.anilist.co/frieren.jpg",
                                  "year": 2020, "status": "ongoing", "languages": ["fr", "en"]}])
        self.assertEqual(self.fake.covers_asked, [118586], "covers from AniList: MangaDex's sit behind a browser check")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.fake.asked[-2]).query)
        self.assertEqual(query["contentRating[]"], ["safe", "suggestive"], "never MangaDex's adult ratings")
        self.assertEqual(query["order[followedCount]"], ["desc"], "the series before its spin-offs")
        self.assertEqual(manga.search("  ", "fr"), [])
        manga.popular("fr")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.fake.asked[-2]).query)
        self.assertEqual((query["contentRating[]"], query["availableTranslatedLanguage[]"]), (["safe"], ["fr"]))
        self.assertEqual(manga.search("frieren", "klingon")[0]["title"], "Frieren: Beyond Journey's End", "an unknown language reads English")

    def test_a_series_page(self):
        info = manga.details(SERIES, "fr")
        self.assertEqual((info["description"], info["authors"], info["tags"]), ("Une mage elfe.", ["Yamada Kanehito"], ["Fantasy"]))
        with patch.object(manga, "_get", return_value={"data": manga_json(rating="erotica")}):
            with self.assertRaises(manga.MangaError):
                manga.details(SERIES)
        for bad in ("", "../../etc", "1234"):
            with self.assertRaises(manga.MangaError):
                manga.details(bad)

    def test_chapters_one_copy_each_with_their_group(self):
        chapters = manga.chapters(SERIES, "fr")
        self.assertEqual([(c["number"], c["group"], c["external"]) for c in chapters],
                         [("1", "Blue Solo", ""), ("2", "Blue Solo", "https://mangaplus.example/2"), ("3", "Blue Solo", "")])

    def test_the_pages_of_a_chapter(self):
        self.assertEqual(manga.pages(CHAPTER), ["https://abc.mangadex.network/data/h1/1.png", "https://abc.mangadex.network/data/h1/2.png"])
        with patch.object(manga, "_get", return_value={"baseUrl": "http://plain.example", "chapter": {"hash": "h", "data": ["1.png"]}}):
            with self.assertRaises(manga.MangaError, msg="only a secure image server"):
                manga.pages(CHAPTER)


def image(color):
    return bytes([137, 80, 78, 71]) + bytes([color]) * 20


class LocalTests(Database):
    def setUp(self):
        super().setUp()
        root = self.folder / "Mangas"
        (root / "One Piece").mkdir(parents=True)
        with zipfile.ZipFile(root / "One Piece" / "Tome 2.cbz", "w") as archive:
            archive.writestr("p10.png", image(2))
            archive.writestr("p2.png", image(1))
            archive.writestr("__MACOSX/p1.png", b"x")
            archive.writestr("notes.txt", "x")
        chapter = root / "One Piece" / "Tome 1"
        chapter.mkdir()
        (chapter / "001.jpg").write_bytes(image(5))
        (root / "Oneshot.cbz").write_bytes(self.zip_with("a.png"))
        (root / "Vide").mkdir()
        (root / ".cache").mkdir()
        self.root = root

    def zip_with(self, name):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(name, image(9))
        return buffer.getvalue()

    def test_series_from_the_chosen_folder(self):
        self.assertEqual(manga.local_series(), {"folder": "", "series": []})
        with self.assertRaises(manga.MangaError):
            manga.set_folder(self.folder / "nowhere")
        listed = manga.set_folder(self.root)
        self.assertEqual([(s["title"], s["chapters"]) for s in listed["series"]], [("One Piece", 2), ("Oneshot", 1)],
                         "an empty folder and a hidden one are left out")
        key = listed["series"][0]["id"]
        chapters = manga.local_chapters(key)
        self.assertEqual([(c["title"], c["pages"]) for c in chapters["chapters"]], [("Tome 1", 1), ("Tome 2", 2)], "in natural order")
        self.assertEqual(manga.local_page(key, 1, 0), (image(1), "image/png"), "p2 before p10")
        self.assertEqual(manga.local_page(key, 0, 0), (image(5), "image/jpeg"))
        for bad in ((key, 5, 0), (key, 1, 9), ("0" * 20, 0, 0), ("../..", 0, 0)):
            with self.assertRaises(manga.MangaError):
                manga.local_page(*bad)

    def test_the_page_route(self):
        key = manga.set_folder(self.root)["series"][0]["id"]
        handler = object.__new__(app.RequestHandler)
        handler.send_response, handler.send_header, handler.end_headers, handler.send_error = Mock(), Mock(), Mock(), Mock()
        handler.wfile = io.BytesIO()
        handler.get_manga_local_page(None, {"series": key, "chapter": "1", "page": "0"})
        self.assertEqual(handler.wfile.getvalue(), image(1))
        handler.get_manga_local_page(None, {"series": key, "chapter": "x", "page": "0"})
        handler.send_error.assert_called_once_with(404)


class ProgressTests(Database):
    def test_where_the_reader_stopped(self):
        manga.save_progress({"source": "mangadex", "series": SERIES, "title": "Frieren", "chapter": CHAPTER, "number": "3",
                             "page": 7, "pages": 20, "lang": "fr"})
        manga.save_progress({"source": "local", "series": "a" * 20, "title": "One Piece", "chapter": "1", "page": "2"})
        self.assertEqual([(p["source"], p["title"], p["page"]) for p in manga.progress()][:2], [("local", "One Piece", 2), ("mangadex", "Frieren", 7)])
        for bad in ({"source": "web", "series": SERIES}, {"source": "mangadex", "series": "x"}, {"source": "local", "series": "../x"}):
            with self.assertRaises(manga.MangaError):
                manga.save_progress(bad)
        manga.forget("local", "a" * 20)
        self.assertEqual([p["source"] for p in manga.progress()], ["mangadex"])

    def test_mangadex_down_or_a_refused_request(self):
        handler = object.__new__(app.RequestHandler)
        handler.send_json = Mock()
        handler.get_manga_details(None, {"id": "nope"})
        self.assertEqual(handler.send_json.call_args.kwargs, {"status": 400})
        with patch.object(manga, "search", side_effect=TimeoutError()):
            handler.get_manga_search(None, {"q": "x"})
        self.assertEqual(handler.send_json.call_args, ((({"error": "MangaDex ne répond pas pour le moment."}),), {"status": 502}))


if __name__ == "__main__":
    unittest.main()
