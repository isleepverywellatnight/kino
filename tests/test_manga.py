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
import manga_sources
import manga_volumes
import p2p_engine

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


ANILIST_MEDIA = {"id": 118586, "title": {"romaji": "Sousou no Frieren", "english": "Frieren: Beyond Journey's End", "native": "葬送のフリーレン"},
                 "synonyms": ["Frieren at the Funeral", "フリーレン"], "coverImage": {"extraLarge": "https://s4.anilist.co/frieren.jpg"},
                 "startDate": {"year": 2020}, "status": "RELEASING", "countryOfOrigin": "JP", "genres": ["Adventure", "Fantasy"],
                 "isAdult": False, "description": "An elf mage.<br>(Source: Shogakukan)", "chapters": None, "volumes": 13,
                 "staff": {"edges": [{"role": "Story", "node": {"name": {"full": "Yamada Kanehito"}}},
                                     {"role": "Translator", "node": {"name": {"full": "Someone"}}}]}}
WC_SEARCH = """<a href="https://weebcentral.com/series/01J76XYDGDQERFSK333582BNBZ/Sousou-no-Frieren"><picture>
  <img src="https://temp.compsci88.com/cover/fallback/x.jpg" alt="Sousou no Frieren cover"></picture></a>"""
WC_CHAPTERS = """
<div x-data="{ date: '2025-10-15' }"><a href="/chapters/01K7JP7T3V8ZVZJSGF99FFH55K" class="x"><span class="">Chapter 3</span>
 <time datetime="2025-10-15T10:00:00.000Z"></time></a></div>
<div><a href="/chapters/01K7JP7T3V8ZVZJSGF99FFH55J" class="x"><span class="">Chapter 2</span><time datetime="2025-10-08T10:00:00Z"></time></a></div>
<div><a href="/chapters/01K7JP7T3V8ZVZJSGF99FFH55I" class="x"><span class="">Chapter 1</span><time datetime="2025-10-01T10:00:00Z"></time></a></div>"""
WC_IMAGES = """<img src="/static/images/x.svg"><img src="https://scans-hot.planeptune.us/manga/Frieren/0003-001.png" alt="Page 1">
<img src="https://scans-hot.planeptune.us/manga/Frieren/0003-002.png" alt="Page 2">"""
NYAA = """<?xml version="1.0" encoding="utf-8"?><rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0"><channel>
<item><title>Frieren - Beyond Journey's End v01-13 (Digital) (1r0n)</title><nyaa:infoHash>AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</nyaa:infoHash>
 <nyaa:seeders>12</nyaa:seeders><nyaa:size>2.1 GiB</nyaa:size><pubDate>Wed, 01 Oct 2026 10:00:00 -0000</pubDate></item>
<item><title>Frieren v01 (Dead)</title><nyaa:infoHash>bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb</nyaa:infoHash><nyaa:seeders>0</nyaa:seeders></item>
<item><title>Another Mage Story v01</title><nyaa:infoHash>cccccccccccccccccccccccccccccccccccccccc</nyaa:infoHash><nyaa:seeders>50</nyaa:seeders></item>
</channel></rss>"""


class Fake:
    """Stands for AniList and api.mangadex.org; keeps what KINO asked for."""

    def __init__(self):
        self.asked = []

    def __call__(self, url, headers=None, timeout=None, retries=None, method="GET", json_data=None):
        self.asked.append(json_data["query"] if url == manga.ANILIST else url)
        if url == manga.ANILIST:
            if "Media(id" in json_data["query"]:
                return {"data": {"Media": ANILIST_MEDIA}}
            return {"data": {"Page": {"media": [ANILIST_MEDIA]}}}
        path = urllib.parse.urlsplit(url).path
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if path == "/manga":
            return {"data": [manga_json()]}
        if path == f"/manga/{SERIES}":
            return {"data": manga_json()}
        if path == f"/manga/{SERIES}/feed":
            if query["translatedLanguage[]"] == ["en"]:
                return {"total": 1, "data": [chapter_json("4", "00000000-0000-0000-0000-000000000004", group="EN team")]}
            return {"total": 4, "data": [
                chapter_json("1", "00000000-0000-0000-0000-000000000001"),
                chapter_json("1", "00000000-0000-0000-0000-000000000002", group="Autre équipe"),
                chapter_json("2", "00000000-0000-0000-0000-000000000003", external="https://mangaplus.example/2"),
                chapter_json("5", CHAPTER)]}
        if path == f"/at-home/server/{CHAPTER}":
            return {"baseUrl": "https://abc.mangadex.network", "chapter": {"hash": "h1", "data": ["1.png", "2.png"]}}
        raise AssertionError(url)


def sites(url, headers=None, timeout=None):
    """Stands for WeebCentral and Nyaa."""
    if "/search/data" in url:
        return WC_SEARCH
    if url.endswith("/full-chapter-list"):
        return WC_CHAPTERS
    if "/images?" in url:
        return WC_IMAGES
    if url.startswith(manga_sources.NYAA_RSS):
        return NYAA
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
                       patch.object(manga, "cached_get", lambda key, ttl, fetch: fetch()),
                       patch.object(manga_sources, "cached_get", lambda key, ttl, fetch: fetch()),
                       patch.object(manga_sources, "fetch_text", sites)):
            target.start()
            self.addCleanup(target.stop)


class CatalogueTests(Database):
    def test_search_and_the_rows(self):
        found = manga.search("frieren", "fr")
        self.assertEqual(found, [{"id": "118586", "source": "anilist", "title": "Frieren: Beyond Journey's End",
                                  "cover": "https://s4.anilist.co/frieren.jpg", "year": 2020, "status": "releasing", "kind": "JP"}])
        self.assertIn("isAdult: false", self.fake.asked[-1], "never AniList's adult series")
        self.assertEqual(manga.search("  ", "fr"), [])
        manga.row("trending")
        self.assertIn("sort: TRENDING_DESC, status: RELEASING", self.fake.asked[-1], "what is coming out now")
        with self.assertRaises(manga.MangaError):
            manga.row("everything")

    def test_a_series_page(self):
        info = manga.details("118586", "fr")
        self.assertEqual((info["description"], info["authors"], info["tags"], info["volumes_total"]),
                         ("Une mage elfe.", ["Yamada Kanehito"], ["Adventure", "Fantasy"], 13), "the French summary from MangaDex")
        self.assertEqual(manga.details("118586", "en")["description"], "An elf mage.", "AniList's, without markup or source")
        self.assertEqual(info["titles"], ["Frieren: Beyond Journey's End", "Sousou no Frieren", "Frieren at the Funeral"])
        with patch.object(manga, "_anilist", return_value={"Media": {**ANILIST_MEDIA, "isAdult": True}}):
            with self.assertRaises(manga.MangaError):
                manga.details("118586")
        for bad in ("", "../../etc", "12a"):
            with self.assertRaises(manga.MangaError):
                manga.details(bad)
        self.assertEqual(manga.details(SERIES, "en")["id"], "118586", "a series opened from MangaDex before: its AniList entry")


class ChapterTests(Database):
    def test_every_chapter_once_in_the_reading_language_first(self):
        chapters = manga.chapters("118586", "fr")
        self.assertEqual([(c["number"], c["source"], c["lang"]) for c in chapters],
                         [("1", "MangaDex", "fr"), ("2", "WeebCentral", "en"), ("3", "WeebCentral", "en"),
                          ("4", "MangaDex", "en"), ("5", "MangaDex", "fr")],
                         "French when there is, else English; a chapter hosted by its publisher gives way")
        self.assertEqual(chapters[0]["group"], "Blue Solo")
        self.assertEqual((chapters[2]["id"], chapters[2]["date"]), ("wc:01K7JP7T3V8ZVZJSGF99FFH55K", "2025-10-15"))
        english = manga.chapters("118586", "en")
        self.assertEqual([(c["number"], c["source"]) for c in english],
                         [("1", "WeebCentral"), ("2", "WeebCentral"), ("3", "WeebCentral"), ("4", "MangaDex")])

    def test_one_site_down_the_others_still_answer(self):
        def down(*args, **kwargs):
            raise OSError("WeebCentral is down")
        with patch.object(manga_sources, "fetch_text", down):
            self.assertEqual([c["number"] for c in manga.chapters("118586", "fr")], ["1", "2", "4", "5"])

    def test_the_pages_of_a_chapter(self):
        self.assertEqual(manga.pages(f"md:{CHAPTER}"), ["https://abc.mangadex.network/data/h1/1.png", "https://abc.mangadex.network/data/h1/2.png"])
        self.assertEqual(manga.pages("wc:01K7JP7T3V8ZVZJSGF99FFH55K"),
                         ["https://scans-hot.planeptune.us/manga/Frieren/0003-001.png", "https://scans-hot.planeptune.us/manga/Frieren/0003-002.png"])
        with patch.object(manga, "_get", return_value={"baseUrl": "http://plain.example", "chapter": {"hash": "h", "data": ["1.png"]}}):
            with self.assertRaises(manga.MangaError, msg="only a secure image server"):
                manga.pages(CHAPTER)
        for bad in ("wc:../x", "md:nope", "zz:1"):
            with self.assertRaises(manga.MangaError):
                manga.pages(bad)


class SiteTests(unittest.TestCase):
    def test_titles_match_whatever_their_punctuation(self):
        self.assertEqual(manga_sources.normalize("Kaiju No. 8"), manga_sources.normalize("kaiju no 8"))
        self.assertEqual(manga_sources.number_key("012"), "12")
        self.assertIsNone(manga_sources.number_key(""))
        with patch.object(manga_sources, "fetch_text", sites):
            self.assertEqual(manga_sources.weebcentral_match(["Frieren: Beyond Journey's End", "Sousou no Frieren"]), "01J76XYDGDQERFSK333582BNBZ")
            self.assertIsNone(manga_sources.weebcentral_match(["Completely Different Title"]), "no guess on a weak match")

    def test_volumes_by_torrent(self):
        with patch.object(manga_sources, "fetch_text", sites):
            found = manga_sources.nyaa_volumes(["Frieren: Beyond Journey's End", "Frieren"])
        self.assertEqual(found, [{"hash": "a" * 40, "name": "Frieren - Beyond Journey's End v01-13 (Digital) (1r0n)", "size": "2.1 GiB",
                                  "seeders": 12, "date": "01 Oct 2026"}], "no dead torrent, no other series")
        self.assertTrue(manga_sources.magnet("a" * 40, "x").startswith(f"magnet:?xt=urn:btih:{'a' * 40}&dn=x&tr="))


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
        manga.save_progress({"source": "anilist", "series": "118586", "title": "Frieren", "chapter": "wc:01K7JP7T3V8ZVZJSGF99FFH55K", "page": 3})
        self.assertEqual([(p["source"], p["title"], p["page"]) for p in manga.progress()],
                         [("anilist", "Frieren", 3), ("local", "One Piece", 2), ("mangadex", "Frieren", 7)])
        for bad in ({"source": "web", "series": SERIES}, {"source": "mangadex", "series": "x"}, {"source": "local", "series": "../x"},
                    {"source": "anilist", "series": "1; drop"}):
            with self.assertRaises(manga.MangaError):
                manga.save_progress(bad)
        manga.forget("local", "a" * 20)
        self.assertEqual([p["source"] for p in manga.progress()], ["anilist", "mangadex"])

    def test_a_site_down_or_a_refused_request(self):
        handler = object.__new__(app.RequestHandler)
        handler.send_json = Mock()
        handler.get_manga_details(None, {"id": "nope"})
        self.assertEqual(handler.send_json.call_args.kwargs, {"status": 400})
        with patch.object(manga, "search", side_effect=TimeoutError()):
            handler.get_manga_search(None, {"q": "x"})
        self.assertEqual(handler.send_json.call_args, ((({"error": "Les sources manga ne répondent pas pour le moment."}),), {"status": 502}))
        with patch.object(manga_volumes, "files", side_effect=p2p_engine.P2PConsentNeeded()):
            handler.get_manga_volume_files(None, {"hash": "a" * 40})
        self.assertEqual((handler.send_json.call_args.args[0]["code"], handler.send_json.call_args.kwargs), ("p2p_consent", {"status": 409}))
        handler.get_manga_row(None, {"name": "x"})
        self.assertEqual(handler.send_json.call_args.kwargs, {"status": 400})


if __name__ == "__main__":
    unittest.main()
