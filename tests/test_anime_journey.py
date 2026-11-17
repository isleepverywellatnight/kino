"""The public journey response keeps distinct works and their playback identities."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import anime_journey
import kino_db


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _anilist_response(request, timeout=8):
    payload = json.loads(request.data)
    variables = payload["variables"]
    if "search" in variables:
        return _Response(json.dumps({"data": {"Page": {"media": [
            {"id": 20, "title": {"english": "Naruto", "romaji": "NARUTO"},
             "synonyms": [], "format": "TV", "startDate": {"year": 2002}}
        ]}}}).encode())
    if variables["id"] == 20:
        media = {"id": 20, "title": {"english": "Naruto", "romaji": "NARUTO"},
                 "synonyms": [], "format": "TV", "startDate": {"year": 2002, "month": 10, "day": 3},
                 "episodes": 220, "relations": {"edges": [{"relationType": "SEQUEL", "node": {
                     "id": 1735, "title": {"english": "Naruto: Shippuden", "romaji": "NARUTO: Shippuuden"},
                     "synonyms": [], "format": "TV", "startDate": {"year": 2007, "month": 2, "day": 15},
                     "episodes": 500}}]}}
        return _Response(json.dumps({"data": {"Media": media}}).encode())
    if variables["id"] == 1735:
        media = {"id": 1735, "title": {"english": "Naruto: Shippuden"}, "synonyms": [],
                 "format": "TV", "startDate": {"year": 2007}, "relations": {"edges": []}}
        return _Response(json.dumps({"data": {"Media": media}}).encode())
    raise AssertionError(f"Unexpected AniList id: {variables['id']}")


class AnimeJourneyTests(unittest.TestCase):
    def setUp(self):
        self._database_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._database_dir.cleanup)
        database_patch = patch.object(kino_db, "DB_PATH", Path(self._database_dir.name) / "kino.db")
        database_patch.start()
        self.addCleanup(database_patch.stop)
        kino_db.init_db()

    def test_release_journey_keeps_sequel_separate_from_current_playback_id(self):
        with patch("urllib.request.urlopen", side_effect=_anilist_response):
            result = anime_journey.get_journey("tt0409591", "Naruto", "2002", "series", force_refresh=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["order_label"], "Ordre de sortie")
        self.assertEqual([(work["id"], work["format"]) for work in result["works"]],
                         [(20, "TV"), (1735, "TV")])
        self.assertEqual(result["works"][0]["playback"], {"id": "tt0409591", "type": "series"})
        self.assertIsNone(result["works"][1]["playback"])
        self.assertEqual(result["works"][1]["relation"], "SEQUEL")

    def test_film_resolves_to_its_own_verified_imdb_id_and_unmatched_ova_stays_unplayable(self):
        def provider(request, timeout=8):
            if "graphql.anilist.co" in request.full_url:
                identifier = json.loads(request.data)["variables"]["id"]
                if identifier == 5:
                    media = {"id": 5, "title": {"english": "Cowboy Bebop: The Movie - Knockin' on Heaven's Door",
                                                  "romaji": "Cowboy Bebop: Tengoku no Tobira"},
                             "synonyms": ["Cowboy Bebop: The Movie"], "format": "MOVIE",
                             "startDate": {"year": 2001}}
                else:
                    media = {"id": 10863, "title": {"english": "Steins;Gate: Egoistic Poriomania"},
                             "synonyms": [], "format": "OVA", "startDate": {"year": 2012}}
                return _Response(json.dumps({"data": {"Media": media}}).encode())
            if "Cinemeta" in request.headers.get("User-agent", ""):
                raise AssertionError("Unexpected User-Agent")
            if "Cowboy" in request.full_url:
                metas = [{"id": "tt0275277", "name": "Cowboy Bebop: The Movie", "releaseInfo": "2001", "type": "movie"},
                         {"id": "tt0284886", "name": "Beatrice", "releaseInfo": "1919", "type": "movie"}]
            else:
                metas = []
            return _Response(json.dumps({"metas": metas}).encode())

        with patch("urllib.request.urlopen", side_effect=provider):
            film = anime_journey.resolve_work(5, current_playback_id="tt0213338")
            ova = anime_journey.resolve_work(10863, current_playback_id="tt1910272")

        self.assertEqual(film["media"]["id"], "tt0275277")
        self.assertEqual(film["media"]["type"], "movie")
        self.assertFalse(ova["ok"])
        self.assertEqual(ova["reason"], "unmatched")

    def test_middle_season_expands_mainline_and_keeps_earlier_film(self):
        def media(identifier, year, name, relation_edges):
            return {"id": identifier, "title": {"english": name}, "synonyms": [],
                    "format": "TV", "startDate": {"year": year, "month": 1, "day": 1},
                    "episodes": 12, "relations": {"edges": relation_edges}}

        first = media(20, 2002, "Naruto", [])
        middle = media(1735, 2007, "Naruto: Shippuden", [])
        last = media(97938, 2017, "Boruto: Naruto Next Generations", [])
        film = {"id": 442, "title": {"english": "Naruto the Movie: Ninja Clash in the Land of Snow"},
                "synonyms": [], "format": "MOVIE", "startDate": {"year": 2004, "month": 8, "day": 21}}
        node = lambda work: {key: value for key, value in work.items() if key != "relations"}
        first["relations"]["edges"] = [{"relationType": "SEQUEL", "node": node(middle)},
                                         {"relationType": "SIDE_STORY", "node": film}]
        middle["relations"]["edges"] = [{"relationType": "PREQUEL", "node": node(first)},
                                          {"relationType": "SEQUEL", "node": node(last)}]
        last["relations"]["edges"] = [{"relationType": "PREQUEL", "node": node(middle)}]
        works = {20: first, 1735: middle, 97938: last}

        def provider(request, timeout=8):
            variables = json.loads(request.data)["variables"]
            if "search" in variables:
                return _Response(json.dumps({"data": {"Page": {"media": [middle]}}}).encode())
            return _Response(json.dumps({"data": {"Media": works[variables["id"]]}}).encode())

        with patch("urllib.request.urlopen", side_effect=provider):
            result = anime_journey.get_journey("tt0988824", "Naruto: Shippuden", "2007", "series", force_refresh=True)

        self.assertEqual(result["saga_id"], 20)
        self.assertEqual([work["id"] for work in result["works"]], [20, 442, 1735, 97938])
        self.assertEqual(next(work for work in result["works"] if work["id"] == 1735)["playback"]["id"], "tt0988824")

    def test_unrelated_or_unknown_relations_do_not_create_an_invented_saga(self):
        monster = {"id": 19, "title": {"english": "Monster"}, "synonyms": [], "format": "TV",
                   "startDate": {"year": 2004}, "relations": {"edges": []}}

        def provider(request, timeout=8):
            variables = json.loads(request.data)["variables"]
            if "search" in variables:
                return _Response(json.dumps({"data": {"Page": {"media": [monster]}}}).encode())
            return _Response(json.dumps({"data": {"Media": monster}}).encode())

        with patch("urllib.request.urlopen", side_effect=provider):
            result = anime_journey.get_journey("tt9911223344", "Monster", "2004", "series", force_refresh=True)

        self.assertTrue(result["ok"])
        self.assertFalse(result["relations_known"])
        self.assertEqual([work["id"] for work in result["works"]], [19])

    def test_local_order_correction_persists_and_can_be_reset(self):
        anime_journey.reset_order(20)
        with patch("urllib.request.urlopen", side_effect=_anilist_response):
            release = anime_journey.get_journey("tt0409591", "Naruto", "2002", "series", force_refresh=True)
        self.assertEqual([work["id"] for work in release["works"]], [20, 1735])

        custom = anime_journey.save_order("tt0409591", "series", 20, [1735, 20])
        self.assertEqual([work["id"] for work in custom["works"]], [1735, 20])
        self.assertEqual(custom["order_label"], "Ordre personnalisé")
        self.assertEqual([work["id"] for work in anime_journey.get_journey("tt0409591", "Naruto", "2002", "series")["works"]],
                         [1735, 20])
        with self.assertRaises(ValueError):
            anime_journey.save_order("tt0409591", "series", 20, [20, 20])

        anime_journey.reset_order(20)
        restored = anime_journey.get_journey("tt0409591", "Naruto", "2002", "series")
        self.assertEqual([work["id"] for work in restored["works"]], [20, 1735])

    def test_only_a_member_of_the_loaded_saga_can_be_opened(self):
        with patch("urllib.request.urlopen", side_effect=_anilist_response):
            anime_journey.get_journey("tt0409591", "Naruto", "2002", "series", force_refresh=True)
        current = anime_journey.resolve_member("tt0409591", "series", 20)
        self.assertEqual(current["media"]["id"], "tt0409591")
        with self.assertRaises(ValueError):
            anime_journey.resolve_member("tt0409591", "series", 999999)

    def test_same_title_and_year_with_two_anilist_ids_is_not_guessed(self):
        variants = [
            {"id": 44, "title": {"english": "Twin Story"}, "synonyms": [], "format": "TV", "startDate": {"year": 2020}},
            {"id": 55, "title": {"english": "Twin Story"}, "synonyms": [], "format": "TV", "startDate": {"year": 2020}},
        ]

        def provider(request, timeout=8):
            variables = json.loads(request.data)["variables"]
            if "search" in variables:
                return _Response(json.dumps({"data": {"Page": {"media": variants}}}).encode())
            return _Response(json.dumps({"data": {"Media": {**variants[0], "relations": {"edges": []}}}}).encode())

        with patch("urllib.request.urlopen", side_effect=provider):
            result = anime_journey.get_journey("tt9911223355", "Twin Story", "2020", "series", force_refresh=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unmatched")

    def test_verified_playback_identity_remains_available_when_provider_is_offline(self):
        def provider(request, timeout=8):
            if "graphql.anilist.co" in request.full_url:
                media = {"id": 9999, "title": {"english": "Unique Anime Film"}, "synonyms": [],
                         "format": "MOVIE", "startDate": {"year": 2022}}
                return _Response(json.dumps({"data": {"Media": media}}).encode())
            metas = [{"id": "tt9911223366", "name": "Unique Anime Film", "releaseInfo": "2022", "type": "movie"}]
            return _Response(json.dumps({"metas": metas}).encode())

        with patch("urllib.request.urlopen", side_effect=provider):
            first = anime_journey.resolve_work(9999)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            cached = anime_journey.resolve_work(9999)
        self.assertTrue(first["ok"])
        self.assertEqual(cached["media"]["id"], "tt9911223366")


if __name__ == "__main__":
    unittest.main()
