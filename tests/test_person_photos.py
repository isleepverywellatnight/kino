"""Cast photos for the immersive fiche (Wikipedia summaries, no key)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import config
import kino_db
import meta_engine

THUMB = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Actor.jpg/330px-Actor.jpg"


def summary(description, thumb=THUMB, kind="standard"):
    return {"type": kind, "description": description, "thumbnail": {"source": thumb} if thumb else {}}


class PersonPhotoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(kino_db, "DB_PATH", Path(self.temp.name) / "kino.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        kino_db.init_db()
        config.MEM_CACHE.clear()
        self.addCleanup(config.MEM_CACHE.clear)

    def lookup(self, name, responses):
        calls = []

        def fake(url, **kwargs):
            calls.append(url.rsplit("/", 1)[1])
            result = responses[len(calls) - 1]
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(meta_engine, "http_json", side_effect=fake):
            return meta_engine.person_photo(name), calls

    def test_actor_page_gives_its_photo(self):
        photo, calls = self.lookup("Chris Sarandon", [summary("American actor (born 1942)")])
        self.assertEqual(photo, THUMB)
        self.assertEqual(calls, ["Chris_Sarandon"])

    def test_namesake_that_is_not_from_film_is_skipped_for_the_actor_page(self):
        photo, calls = self.lookup("John Smith", [summary("British politician"), summary("English actor", THUMB)])
        self.assertEqual(photo, THUMB)
        self.assertEqual(calls, ["John_Smith", "John_Smith_%28actor%29"])

    def test_missing_pages_and_foreign_images_give_no_photo(self):
        not_found = RuntimeError("Erreur API (404) de en.wikipedia.org : not found")
        photo, _ = self.lookup("Personne Inconnue", [not_found, not_found, not_found])
        self.assertEqual(photo, "")
        config.MEM_CACHE.clear()
        photo, _ = self.lookup("Acteur Bizarre", [summary("American actor", "https://example.com/x.jpg")])
        self.assertEqual(photo, "", "only Wikimedia-hosted images")

    def test_network_trouble_is_not_cached(self):
        photo, _ = self.lookup("Danny Elfman", [RuntimeError("Erreur API (503) de en.wikipedia.org : busy")])
        self.assertIsNone(photo)
        photo, calls = self.lookup("Danny Elfman", [summary("American composer (born 1953)")])
        self.assertEqual(photo, THUMB)
        self.assertEqual(len(calls), 1, "asked again after the failure")
        _, calls = self.lookup("Danny Elfman", [])
        self.assertEqual(calls, [], "then served from the cache")

    def test_several_names_are_deduplicated_and_capped(self):
        with patch.object(meta_engine, "person_photo", side_effect=lambda name: THUMB if name == "A" else "") as lookup:
            result = meta_engine.person_photos(["A", " A ", "B"] + [f"N{i}" for i in range(20)])
        self.assertEqual(result["A"], THUMB)
        self.assertIsNone(result["B"])
        self.assertEqual(len(result), 12)
        self.assertEqual(lookup.call_count, 12)

    def test_route_splits_names_on_pipes(self):
        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/people?names=" + "|".join(["Catherine O'Hara", "Danny Elfman", ""])
        handler.headers = {}
        handler._request_allowed = Mock(return_value=True)
        handler.send_json = Mock()
        with patch.object(meta_engine, "person_photos", return_value={"Danny Elfman": THUMB}) as photos:
            handler.do_GET()
        photos.assert_called_once_with(["Catherine O'Hara", "Danny Elfman"])
        self.assertEqual(handler.send_json.call_args.args[0], {"people": {"Danny Elfman": THUMB}})


class CatalogueEntityTests(unittest.TestCase):
    def test_html_entities_in_names_are_decoded(self):
        meta = {"meta": {"name": "L&apos;Étrange Noël", "cast": ["Catherine O&apos;Hara", "Danny Elfman"],
                         "director": ["Henry Selick"], "description": "Jack &amp; Sally", "genres": []}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(kino_db, "DB_PATH", Path(directory) / "kino.db"), \
                patch.object(meta_engine, "http_json", return_value=meta), \
                patch.object(meta_engine, "translate_text_fr", side_effect=lambda text: text), \
                patch.object(meta_engine, "get_catalog_top", return_value=[]):
            kino_db.init_db()
            config.MEM_CACHE.clear()
            result = meta_engine.get_media_meta("tt0107688", "movie")
            config.MEM_CACHE.clear()
        self.assertEqual(result["cast"], ["Catherine O'Hara", "Danny Elfman"])
        self.assertEqual(result["name"], "L'Étrange Noël")
        self.assertEqual(result["description"], "Jack & Sally")


if __name__ == "__main__":
    unittest.main()
