"""The Anime tab's rows and planning: what is on comes from AniList,
each work is paired with the fiche KINO can play, and nothing about the user
leaves the Mac."""

import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import anime_home
import kino_db


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _media(identifier, title, fmt="TV", **extra):
    return {"id": identifier, "title": {"english": title, "romaji": f"{title} (romaji)"}, "format": fmt,
            "episodes": 12, "status": "RELEASING", "averageScore": 80, "popularity": 50000,
            "genres": ["Action", "Drama", "Fantasy", "Comedy"], "coverImage": {"large": f"https://img/{identifier}.jpg"},
            "startDate": {"year": 2026, "month": 10}, "nextAiringEpisode": {"episode": 7, "airingAt": 1794600000}, **extra}


IDS = {
    101: {"imdb": "tt1000001", "thetvdb-season": 2, "themoviedb-season": 1, "media": "TV"},
    102: {"imdb": "tt1000002", "thetvdb-season": None, "themoviedb-season": 3},
    103: {"imdb": "tt1000003"},
    104: {"imdb": None},
    201: {"imdb": "tt2000001", "thetvdb-season": 1},
}


class Network:
    """Stands for AniList and the identifier table; keeps what was sent."""

    def __init__(self):
        self.sent = []
        self.fail = set()
        self.home = {
            "trending": {"media": [_media(101, "Série A"), _media(102, "Série B"), _media(101, "Série A")]},
            "season": {"media": [_media(103, "Film C", "MOVIE", nextAiringEpisode=None)]},
            "upcoming": {"media": [_media(104, "Série D", status="NOT_YET_RELEASED")]},
            "top": {"media": []},
            "movies": {"media": [_media(105, "OVA E", "OVA")]},
        }
        self.pages = {}
        self.works = {}

    def __call__(self, request, timeout=8):
        payload = json.loads(request.data)
        self.sent.append((request.full_url, payload))
        if request.full_url == anime_home.IDS_URL:
            if "ids" in self.fail:
                raise urllib.error.URLError("table down")
            return _Response(json.dumps([IDS.get(row["anilist"]) for row in payload]).encode())
        if "anilist" in self.fail:
            raise urllib.error.URLError("AniList down")
        variables = payload["variables"]
        if "season" in variables:
            return _Response(json.dumps({"data": self.home}).encode())
        if "page" in variables:
            return _Response(json.dumps({"data": {"Page": self.pages[variables["page"]]}}).encode())
        return _Response(json.dumps({"data": {"Media": self.works.get(variables["id"])}}).encode())

    def count(self, url):
        return sum(1 for sent_url, _ in self.sent if sent_url == url)


class AnimeHomeTests(unittest.TestCase):
    def setUp(self):
        self._database_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._database_dir.cleanup)
        database_patch = patch.object(kino_db, "DB_PATH", Path(self._database_dir.name) / "kino.db")
        database_patch.start()
        self.addCleanup(database_patch.stop)
        kino_db.init_db()
        self.network = Network()
        network_patch = patch("urllib.request.urlopen", side_effect=self.network)
        network_patch.start()
        self.addCleanup(network_patch.stop)
        self.today = datetime.date(2026, 11, 13)

    def test_seasons_follow_the_calendar_and_cross_the_year(self):
        self.assertEqual(anime_home.seasons(datetime.date(2026, 11, 13)), (("FALL", 2026), ("WINTER", 2027)))
        self.assertEqual(anime_home.seasons(datetime.date(2026, 1, 1)), (("WINTER", 2026), ("SPRING", 2026)))
        self.assertEqual(anime_home.seasons(datetime.date(2026, 9, 30)), (("SUMMER", 2026), ("FALL", 2026)))
        self.assertEqual(anime_home.season_label(("FALL", 2026)), "Automne 2026")
        self.assertEqual(anime_home.season_label(("SUMMER", 2027)), "Été 2027")

    def test_a_work_is_paired_with_its_fiche_and_its_season(self):
        self.assertEqual(anime_home._playback(IDS[101], "TV"), {"id": "tt1000001", "type": "series", "season": 2})
        self.assertEqual(anime_home._playback(IDS[102], "ONA"), {"id": "tt1000002", "type": "series", "season": 3},
                         "the second table's season when the first has none")
        self.assertEqual(anime_home._playback(IDS[103], "MOVIE"), {"id": "tt1000003", "type": "movie", "season": None})
        self.assertIsNone(anime_home._playback(IDS[103], "OVA"), "an OVA can be a film or a series: paired when opened")
        self.assertIsNone(anime_home._playback(IDS[104], "TV"))
        self.assertIsNone(anime_home._playback(None, "TV"))
        self.assertIsNone(anime_home._playback({"imdb": "<script>"}, "TV"))
        self.assertEqual(anime_home._playback({"imdb": "tt1000009", "thetvdb-season": "a", "themoviedb-season": True}, "TV")["season"], None)

    def test_the_table_is_asked_once_for_all_the_works_and_its_answers_are_kept(self):
        works = [(101, "TV"), (103, "MOVIE"), (104, "TV"), (101, "TV"), ("x", "TV")]
        first = anime_home.playback_ids(works, now=1000)
        self.assertEqual(first, {101: {"id": "tt1000001", "type": "series", "season": 2},
                                 103: {"id": "tt1000003", "type": "movie", "season": None}, 104: None})
        self.assertEqual(self.network.sent, [(anime_home.IDS_URL, [{"anilist": 101}, {"anilist": 103}, {"anilist": 104}])])
        self.assertEqual(anime_home.playback_ids(works, now=2000), first)
        self.assertEqual(self.network.count(anime_home.IDS_URL), 1, "kept: nothing asked again")
        # A work without a fiche is asked again the next day; a found one is kept a month.
        anime_home.playback_ids(works, now=1000 + 86400 + 1)
        self.assertEqual(self.network.sent[-1], (anime_home.IDS_URL, [{"anilist": 104}]))
        anime_home.playback_ids(works, now=1000 + 31 * 86400)
        self.assertEqual(self.network.sent[-1][1], [{"anilist": 101}, {"anilist": 103}, {"anilist": 104}])

    def test_a_table_that_does_not_answer_keeps_nothing(self):
        self.network.fail.add("ids")
        self.assertEqual(anime_home.playback_ids([(101, "TV")], now=1000), {101: None})
        self.network.fail.clear()
        self.assertEqual(anime_home.playback_ids([(101, "TV")], now=1001)[101]["id"], "tt1000001", "asked again at once")

    def test_the_rows_carry_their_season_their_cards_and_the_users_anime(self):
        kino_db.db_record_history({"id": "tt1000001", "name": "Série A", "type": "series", "last_watched": 5})
        kino_db.db_record_history({"id": "tt7777777", "name": "Lupin", "type": "series", "last_watched": 6})
        kino_db.db_record_history({"id": "tt8888888", "name": "Marqué", "type": "series", "is_anime": True, "last_watched": 7})
        result = anime_home.get_home(today=self.today)
        self.assertTrue(result["ok"])
        self.assertEqual([(row["key"], row["title"]) for row in result["rows"]], [
            ("trending", "Tendances"), ("season", "Cette saison · Automne 2026"),
            ("upcoming", "La saison prochaine · Hiver 2027"), ("movies", "Films")], "an empty row is left out")
        trending = result["rows"][0]["items"]
        self.assertEqual([item["id"] for item in trending], [101, 102], "a work appears once in a row")
        self.assertEqual(trending[0], {
            "id": 101, "title": "Série A", "romaji": "Série A (romaji)", "format": "TV", "episodes": 12,
            "status": "RELEASING", "score": 80, "popularity": 50000, "genres": ["Action", "Drama", "Fantasy"],
            "poster": "https://img/101.jpg", "year": 2026, "month": 10,
            "next": {"episode": 7, "airing_at": 1794600000}, "kino": {"id": "tt1000001", "type": "series", "season": 2}})
        self.assertIsNone(result["rows"][1]["items"][0]["next"])
        self.assertIsNone(result["rows"][2]["items"][0]["kino"])
        self.assertIsNone(result["rows"][3]["items"][0]["kino"], "the OVA waits to be opened")
        self.assertEqual(sorted(result["library"]), ["tt1000001", "tt8888888"], "Lupin is not an anime")

    def test_nothing_about_the_user_is_sent_out(self):
        kino_db.db_record_history({"id": "tt1000001", "name": "Série A", "type": "series", "last_watched": 5})
        kino_db.db_record_history({"id": "tt7777777", "name": "Lupin", "type": "series", "last_watched": 6})
        self.network.pages = {1: {"pageInfo": {"hasNextPage": False}, "airingSchedules": []}}
        anime_home.get_home(today=self.today)
        anime_home.get_week(now=1794550000)
        for url, payload in self.network.sent:
            text = json.dumps(payload)
            self.assertNotIn("tt7777777", text)
            self.assertNotIn("Lupin", text)
            if url == anime_home.IDS_URL:
                self.assertTrue(all(list(row) == ["anilist"] and isinstance(row["anilist"], int) for row in payload),
                                "AniList numbers of public titles only")
            else:
                self.assertEqual(url, anime_home.ANILIST_URL)

    def test_the_rows_are_kept_half_an_hour_and_the_last_ones_serve_when_anilist_is_down(self):
        anime_home.get_home(today=self.today)
        anime_home.get_home(today=self.today)
        self.assertEqual(self.network.count(anime_home.ANILIST_URL), 1)
        self.network.fail.add("anilist")
        stale = anime_home.get_home(force_refresh=True, today=self.today)
        self.assertTrue(stale["ok"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["rows"][0]["items"][0]["id"], 101)

    def test_without_anilist_and_without_a_past_visit_the_page_says_so(self):
        self.network.fail.add("anilist")
        result = anime_home.get_home(today=self.today)
        self.assertEqual((result["ok"], result["rows"], result["library"]), (False, [], []))
        self.assertIn("indisponibles", result["message"])

    def test_the_planning_reads_every_page_of_the_week_in_airing_order(self):
        now = datetime.datetime(2026, 11, 13, 6, 0).timestamp()
        start = int(datetime.datetime(2026, 11, 13).timestamp())
        airing = lambda at, media, episode=3: {"airingAt": start + at, "episode": episode, "media": media}
        self.network.pages = {
            1: {"pageInfo": {"hasNextPage": True}, "airingSchedules": [
                airing(7200, _media(102, "Série B", popularity=900)),
                airing(3600, _media(101, "Série A")),
                airing(3600, _media(106, "Adulte", isAdult=True)),
                airing(3600, _media(103, "Film C", "MOVIE"))]},
            2: {"pageInfo": {"hasNextPage": False}, "airingSchedules": [
                airing(7200, _media(201, "Série F", "ONA"), 12)]},
        }
        result = anime_home.get_week(now=now)
        self.assertTrue(result["ok"])
        self.assertEqual((result["from"], result["to"]), (start, int(datetime.datetime(2026, 11, 20).timestamp())))
        self.assertEqual([(episode["id"], episode["episode"], episode["airing_at"] - start) for episode in result["episodes"]],
                         [(101, 3, 3600), (201, 12, 7200), (102, 3, 7200)], "by hour, the most followed first")
        self.assertEqual(result["episodes"][1]["kino"], {"id": "tt2000001", "type": "series", "season": 1})
        self.assertNotIn("next", result["episodes"][0])
        asked = [payload["variables"] for url, payload in self.network.sent if url == anime_home.ANILIST_URL]
        self.assertEqual([variables["page"] for variables in asked], [1, 2])
        self.assertEqual((asked[0]["start"], asked[0]["end"]), (start - 1, result["to"]))
        anime_home.get_week(now=now + 600)
        self.assertEqual(self.network.count(anime_home.ANILIST_URL), 2, "kept for the day's visits")
        self.network.fail.add("anilist")
        stale = anime_home.get_week(force_refresh=True, now=now + 900)
        self.assertTrue(stale["stale"])
        self.assertEqual(len(stale["episodes"]), 3)
        late = anime_home.get_week(force_refresh=True, now=now + 8 * 86400)
        self.assertEqual((late["ok"], late["episodes"]), (False, []), "a week that is over is not shown as the planning")

    def test_a_new_season_opens_on_the_fiche_of_its_series(self):
        sequel = {"id": 300, "title": {"english": "Série A Season 4"}, "format": "TV", "startDate": {"year": 2027},
                  "relations": {"edges": [
                      {"relationType": "ADAPTATION", "node": {"id": 900, "format": "MANGA"}},
                      {"relationType": "PREQUEL", "node": {"id": 301, "format": "TV"}}]}}
        middle = {"id": 301, "title": {"english": "Série A Season 3"}, "format": "TV", "startDate": {"year": 2026},
                  "relations": {"edges": [{"relationType": "PREQUEL", "node": {"id": 101, "format": "TV"}}]}}
        self.network.works = {300: sequel, 301: middle}
        result = anime_home.open_work(300)
        self.assertEqual(result, {"ok": True, "media": {
            "id": "tt1000001", "name": "Série A Season 4", "type": "series", "is_anime": True,
            "year": "2027", "poster": "", "open_season": 4}}, "two seasons after the one the table knows as season 2")
        sent = len(self.network.sent)
        self.assertEqual(anime_home.open_work(300)["media"]["open_season"], 4)
        self.assertEqual(len(self.network.sent) - sent, 1, "remembered: only the work itself is read again")

    def test_a_work_without_prequel_is_matched_by_title_or_left_to_the_search(self):
        self.network.works = {105: {"id": 105, "title": {"english": "OVA E"}, "format": "OVA", "startDate": {"year": 2020},
                                    "relations": {"edges": []}}}
        found = {"ok": True, "media": {"id": "tt3000001", "name": "OVA E", "type": "movie", "poster": "p"}}
        with patch("anime_journey.resolve_work", return_value=found) as resolve:
            result = anime_home.open_work("105")
        resolve.assert_called_once_with(105)
        self.assertEqual(result["media"], {"id": "tt3000001", "name": "OVA E", "type": "movie", "is_anime": True,
                                           "year": "2020", "poster": "p", "open_season": 0})
        self.network.works[104] = {"id": 104, "title": {"romaji": "Série D"}, "format": "TV", "relations": {"edges": []}}
        with patch("anime_journey.resolve_work", return_value={"ok": False, "reason": "unmatched"}):
            unknown = anime_home.open_work(104)
        self.assertEqual((unknown["ok"], unknown["title"]), (False, "Série D"))

    def test_only_an_anilist_number_opens_a_work(self):
        for bad in (None, "", "0", "12a", True, "1; drop", 10 ** 9):
            with self.assertRaises(ValueError):
                anime_home.open_work(bad)
        self.assertEqual(self.network.sent, [])


if __name__ == "__main__":
    unittest.main()
