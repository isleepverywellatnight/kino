"""Page acteur on the server: titles confirmed by their credits, the rest
apart, newest first; a French bio and a checked photo from Wikipedia."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import config
import kino_db
import meta_engine
import person_page


def found(imdb_id, name, year):
    return {"id": imdb_id, "imdb_id": imdb_id, "name": name, "releaseInfo": year, "poster": f"https://img/{imdb_id}.jpg"}


SEARCH = {
    "movie": [found("tt1", "Dune: Part Two", "2024"), found("tt2", "Doctor Sleep", "2019"), found("tt3", "Ferguson Story", "2010"),
              found("tt1", "Dune: Part Two", "2024"), {"id": "kitsu:9", "name": "Not IMDb"}, found("tt4", "Mission: Impossible", "2015")],
    "series": [found("tt5", "Silo", "2023–"), found("tt6", "Nya tider", "1999–2000")],
}
METAS = {
    "tt1": {"cast": ["Timothée Chalamet", "Zendaya", "Rebecca Ferguson"], "director": ["Denis Villeneuve"]},
    "tt2": {"cast": ["Ewan McGregor", "REBECCA  FERGUSON"], "director": ["Mike Flanagan"], "writer": ["Mike Flanagan"]},
    "tt3": {"cast": ["Someone Else"]},
    "tt4": RuntimeError("down"),
    "tt5": {"cast": ["Rebecca Ferguson", "Common"], "imdbRating": "8.0"},
    "tt6": {"cast": ["Sverrir Gudnason"]},
}


def meta_of(imdb_id, kind):
    value = METAS[imdb_id]
    if isinstance(value, Exception):
        raise value
    return value


class WorksTests(unittest.TestCase):
    def test_names_are_compared_without_case_accents_or_spaces(self):
        self.assertEqual(person_page.fold("  Zoë   SALDAÑA "), "zoe saldana")
        self.assertEqual(person_page.roles_in({"cast": ["Zoe Saldana"], "director": ["zoë saldaña"], "writer": [3]}, "Zoë Saldaña"),
                         ["cast", "director"])
        self.assertEqual(person_page.roles_in({}, "x"), [])

    def test_confirmed_titles_newest_first_and_the_rest_apart(self):
        works = person_page.person_works("Rebecca Ferguson", search=lambda name, kind: SEARCH[kind], meta_of=meta_of)
        self.assertEqual([(m["id"], m["roles"]) for m in works["movies"]], [("tt1", ["cast"]), ("tt2", ["cast"])])
        self.assertEqual([m["id"] for m in works["series"]], ["tt5"])
        self.assertEqual([m["id"] for m in works["others"]], ["tt4", "tt3", "tt6"],
                         "unconfirmed (and unreachable) titles stay apart, newest first")
        self.assertEqual(works["series"][0], {"id": "tt5", "imdb_id": "tt5", "type": "series", "name": "Silo", "year": "2023–",
                                              "releaseInfo": "2023–", "poster": "https://img/tt5.jpg", "imdbRating": "8.0",
                                              "roles": ["cast"]})

    def test_a_director_is_found_by_the_direction_credits(self):
        works = person_page.person_works("Mike Flanagan", search=lambda name, kind: SEARCH[kind] if kind == "movie" else [],
                                         meta_of=meta_of)
        self.assertEqual([(m["id"], m["roles"]) for m in works["movies"]], [("tt2", ["director", "writer"])])

    def test_at_most_thirty_titles_of_each_kind_are_checked(self):
        many = [found(f"tt{n}", f"Film {n}", "2000") for n in range(100, 150)]
        asked = []
        person_page.person_works("X", search=lambda name, kind: many if kind == "movie" else [],
                                 meta_of=lambda imdb_id, kind: asked.append(imdb_id) or {})
        self.assertEqual(len(asked), 30)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db = patch.object(kino_db, "DB_PATH", Path(self.temp.name) / "kino.db")
        db.start()
        self.addCleanup(db.stop)
        kino_db.init_db()
        cache = patch.object(config, "MEM_CACHE", {})
        cache.start()
        self.addCleanup(cache.stop)

    def summary(self, photo="https://upload.wikimedia.org/a.jpg"):
        return {"type": "standard", "title": "Rebecca Ferguson", "description": "Swedish actress", "thumbnail": {"source": photo}}

    def test_photo_description_and_a_short_french_bio(self):
        long_bio = "Rebecca Ferguson est une actrice suédoise. " + "Elle joue beaucoup. " * 40

        def http(url, **kwargs):
            if "w/api.php" in url:
                self.assertEqual(kwargs["data"]["titles"], "Rebecca Ferguson")
                return {"query": {"pages": [{"langlinks": [{"lang": "fr", "title": "Rebecca Ferguson"}]}]}}
            self.assertIn("fr.wikipedia.org/api/rest_v1/page/summary/Rebecca_Ferguson", url)
            return {"type": "standard", "description": "actrice suédoise", "extract": long_bio}

        with patch.object(meta_engine, "screen_person_summary", return_value=self.summary()), \
                patch.object(person_page, "http_json", side_effect=http):
            profile = person_page.person_profile("Rebecca Ferguson")
        self.assertEqual(profile["photo"], "https://upload.wikimedia.org/a.jpg")
        self.assertEqual(profile["description"], "actrice suédoise")
        self.assertTrue(profile["bio"].startswith("Rebecca Ferguson est une actrice suédoise."))
        self.assertLessEqual(len(profile["bio"]), person_page.BIO_LIMIT)
        self.assertTrue(profile["bio"].endswith("."), "cut at the end of a sentence")

    def test_no_french_page_no_bio_and_foreign_photos_refused(self):
        with patch.object(meta_engine, "screen_person_summary", return_value=self.summary("https://evil.example/a.jpg")), \
                patch.object(person_page, "http_json", return_value={"query": {"pages": [{}]}}):
            self.assertEqual(person_page.person_profile("Someone"), {"photo": "", "description": "", "bio": ""})

    def test_no_screen_person_or_network_trouble(self):
        with patch.object(meta_engine, "screen_person_summary", return_value=""):
            self.assertEqual(person_page.person_profile("A Politician"), {"photo": "", "description": "", "bio": ""})
        with patch.object(meta_engine, "screen_person_summary", return_value=None) as summary:
            self.assertIsNone(person_page.person_profile("Later"))
            self.assertIsNone(person_page.person_profile("Later"))
        self.assertEqual(summary.call_count, 2, "network trouble is not kept")

    def test_the_cast_photos_use_the_same_check(self):
        with patch.object(meta_engine, "screen_person_summary", return_value=self.summary()) as summary:
            self.assertEqual(meta_engine.person_photo("Rebecca Ferguson"), "https://upload.wikimedia.org/a.jpg")
        summary.assert_called_once_with("Rebecca Ferguson")


class RouteTests(unittest.TestCase):
    def handler(self, path):
        request = object.__new__(app.RequestHandler)
        request.path = path
        request.headers = {}
        request._request_allowed = Mock(return_value=True)
        request.send_json = Mock()
        return request

    def test_the_page_and_a_missing_name(self):
        request = self.handler("/api/person?name=%20Rebecca%20%20Ferguson%20")
        with patch.object(person_page, "page", return_value={"name": "Rebecca Ferguson"}) as page:
            request.do_GET()
        page.assert_called_once_with("Rebecca Ferguson")
        request.send_json.assert_called_once_with({"name": "Rebecca Ferguson"})
        request = self.handler("/api/person?name=")
        request.do_GET()
        request.send_json.assert_called_once_with({"error": "Nom manquant."}, status=400)

    def test_the_page_gathers_profile_and_works(self):
        with patch.object(person_page, "person_profile", return_value=None), \
                patch.object(person_page, "person_works", return_value={"movies": [], "series": [], "others": []}):
            self.assertEqual(person_page.page(" Rebecca  Ferguson "), {"name": "Rebecca Ferguson", "photo": "", "description": "",
                                                                       "bio": "", "movies": [], "series": [], "others": []})


if __name__ == "__main__":
    unittest.main()
