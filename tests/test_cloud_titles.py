"""Cloud files grouped by work: release names read, files gathered, works
matched to the catalogue without guessing."""
import unittest
from datetime import date
from unittest.mock import Mock, patch

import app
import cloud_titles
import config
import debrid_engine

TODAY = date(2026, 11, 10)


def parse(name):
    return cloud_titles.parse_release(name, today=TODAY)


class ParseTests(unittest.TestCase):
    def assertRelease(self, name, kind, title, year="", season=None, episode=None, quality=""):
        found = parse(name)
        self.assertEqual((found["kind"], found["title"], found["year"], found["season"], found["episode"], found["quality"]),
                         (kind, title, year, season, episode, quality), name)

    def test_series_markers(self):
        self.assertRelease("The.Bear.S03E05.1080p.WEB.h264-ETHEL.mkv", "series", "The Bear", "", 3, 5, "1080p")
        self.assertRelease("Severance S02E01 Hello Ms Cobel 2160p ATVP WEB-DL.mkv", "series", "Severance", "", 2, 1, "2160p")
        self.assertRelease("the.office.us.2x05.dvdrip.avi", "series", "the office us", "", 2, 5)
        self.assertRelease("Arcane.S02E01-E02.MULTi.1080p.mkv", "series", "Arcane", "", 2, 1, "1080p")
        self.assertRelease("Shogun.2024.S01.COMPLETE.1080p.mkv", "series", "Shogun", "2024", 1, None, "1080p")
        self.assertRelease("Doctor.Who.2005.S01E01.Rose.720p.mkv", "series", "Doctor Who", "2005", 1, 1, "720p")
        self.assertRelease("[SubsPlease] Frieren - 05 (1080p) [ABCD].mkv", "series", "Frieren", "", None, 5, "1080p")

    def test_films_and_years_inside_titles(self):
        self.assertRelease("Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.HDR.DV.HEVC.Atmos-FGT.mkv", "movie", "Dune Part Two", "2024", quality="2160p")
        self.assertRelease("Le.Comte.de.Monte-Cristo.2024.MULTi.VFF.2160p.WEB.H265-GROUP.mkv", "movie", "Le Comte de Monte-Cristo", "2024", quality="2160p")
        self.assertRelease("Blade.Runner.2049.2017.1080p.BluRay.x264.mkv", "movie", "Blade Runner 2049", "2017", quality="1080p")
        self.assertRelease("2001.A.Space.Odyssey.1968.1080p.mkv", "movie", "2001 A Space Odyssey", "1968", quality="1080p")
        self.assertRelease("1917.2019.2160p.mkv", "movie", "1917", "2019", quality="2160p")
        self.assertRelease("Wonder.Woman.1984.2020.1080p.mkv", "movie", "Wonder Woman 1984", "2020", quality="1080p")
        self.assertRelease("Charlotte's Web 2006 720p.mkv", "movie", "Charlotte's Web", "2006", quality="720p")
        self.assertRelease("www.Torrent9.com - Inception.2010.TRUEFRENCH.1080p.mkv", "movie", "Inception", "2010", quality="1080p")
        self.assertRelease("Movie.Title.FRENCH.1080p.mkv", "movie", "Movie Title", quality="1080p")
        self.assertTrue(parse("Dune.Part.Two.2024.2160p.HDR.mkv")["hdr"])

    def test_names_that_say_nothing_stay_apart(self):
        for name in ("archive.rar", "subs.fr.srt", "video.mkv", "12345.mkv", "Sample.mkv", "", None):
            self.assertEqual(parse(name)["kind"], "other", name)


class GroupTests(unittest.TestCase):
    def items(self):
        names = ["The.Bear.S03E02.1080p.mkv", "Dune.Part.Two.2024.1080p.mkv", "notes.txt", "The.Bear.S01E01.720p.mkv",
                 "Dune.Part.Two.2024.2160p.mkv", "The Bear S03E01 1080p.mkv"]
        sizes = [2, 8, 0, 1, 30, 2]
        return [{"id": f"id{index}", "ids": [f"id{index}"], "filename": name, "filesize": f"{size} GB",
                 "bytes": size * 1024 ** 3, "download": f"https://cloud.example/{index}", "generated": f"2026-11-0{9 - index}"}
                for index, (name, size) in enumerate(zip(names, sizes))]

    def test_files_of_one_work_form_one_group(self):
        groups = cloud_titles.group_cloud_items(self.items(), today=TODAY)
        self.assertEqual([g["key"] for g in groups], ["series:the bear", "movie:dune part two:2024", "other"])
        bear, dune, other = groups
        self.assertEqual([(f["season"], f["episode"]) for f in bear["files"]], [(1, 1), (3, 1), (3, 2)])
        self.assertEqual((bear["count"], bear["seasons"], bear["latest"], bear["bytes"]), (3, [1, 3], "2026-11-09", 5 * 1024 ** 3))
        self.assertEqual([f["quality"] for f in dune["files"]], ["2160p", "1080p"], "the largest version first")
        self.assertEqual((other["title"], other["count"]), ("", 1))
        self.assertEqual(bear["files"][0]["download"], "https://cloud.example/3", "Cloud fields are kept")

    def test_bad_entries_are_skipped(self):
        self.assertEqual(cloud_titles.group_cloud_items([None, {}, {"filename": ""}, "x"]), [])


class MatchTests(unittest.TestCase):
    def test_exact_name_and_close_year(self):
        results = [{"id": "tt1", "name": "Dune", "releaseInfo": "1984"},
                   {"id": "tt15239678", "name": "Dune: Part Two", "releaseInfo": "2024", "poster": "https://img/p.jpg"}]
        self.assertEqual(cloud_titles.best_match(results, "Dune Part Two", "2024")["id"], "tt15239678")
        self.assertIsNone(cloud_titles.best_match(results, "Dune Part Two", "2010"), "a distant year is refused")
        self.assertIsNone(cloud_titles.best_match([{"id": "tt9", "name": "The Bearded Man"}], "The Bear"), "no loose guess")
        self.assertIsNone(cloud_titles.best_match([{"id": "kitsu:1", "name": "Dune"}], "Dune"), "catalogue ids only")
        self.assertIsNone(cloud_titles.best_match([{"id": "tt3", "name": "The Count of Monte Cristo", "releaseInfo": "2024"}],
                                                  "Le Comte de Monte-Cristo", "2024"), "a translated name is left to the French search")
        self.assertIsNone(cloud_titles.best_match([{"id": "tt4", "name": "Aliens", "releaseInfo": "1986"}], "Alien"))
        kill_bill = [{"id": "tt0266697", "name": "Kill Bill: Volume 1", "releaseInfo": "2003"}]
        self.assertEqual(cloud_titles.best_match(kill_bill, "Kill Bill Vol 1", "2003")["id"], "tt0266697",
                         "a close name with the same year")
        self.assertIsNone(cloud_titles.best_match(kill_bill, "Kill Bill Vol 1"), "the same close name without a year")

    def test_the_french_search_helps_french_release_names(self):
        search = Mock(return_value=[{"id": "tt2", "name": "The Mummy", "releaseInfo": "1999"}])
        search_fr = Mock(return_value=[{"id": "tt26446278", "name": "The Count of Monte-Cristo", "releaseInfo": "2024",
                                        "name_fr": "Le Comte de Monte-Cristo"}])
        media = cloud_titles.match_title("Le Comte de Monte-Cristo", "2024", "movie", search, search_fr)
        self.assertEqual(media, {"id": "tt26446278", "name": "The Count of Monte-Cristo", "type": "movie", "year": "2024",
                                 "poster": "https://images.metahub.space/poster/medium/tt26446278/img",
                                 "name_fr": "Le Comte de Monte-Cristo"})
        search.assert_called_once_with("Le Comte de Monte-Cristo", "movie")

    def test_a_miss_is_empty_and_a_failure_raises(self):
        self.assertEqual(cloud_titles.match_title("Nothing", "", "series", lambda *_: []), {})
        self.assertEqual(cloud_titles.match_title("Notes", "", "other", lambda *_: 1 / 0), {})

        def offline(*_):
            raise OSError("network down")
        with self.assertRaises(OSError):
            cloud_titles.match_title("The Bear", "", "series", offline)


class RouteTests(unittest.TestCase):
    def test_matches_are_remembered_but_failures_are_not(self):
        cache = {}

        def cached_get(key, ttl, fetch):
            if key not in cache:
                value = fetch()
                if value is not None:
                    cache[key] = value
            return cache[key]

        calls = []

        def match(title, year, kind, search, search_fr):
            calls.append(title)
            if title == "Offline":
                raise OSError("down")
            return {"id": "tt1", "name": title} if title == "The Bear" else {}

        titles = [{"key": "series:the bear", "title": "The Bear", "kind": "series"},
                  {"key": "movie:x", "title": "Unknown", "year": "2024", "kind": "movie"},
                  {"key": "movie:y", "title": "Offline", "kind": "movie"},
                  {"key": "other", "title": "x", "kind": "other"}, "bad", {"key": "", "title": "x", "kind": "movie"}]
        with patch.object(app, "cached_get", side_effect=cached_get), patch.object(cloud_titles, "match_title", side_effect=match):
            first = app.match_cloud_titles(titles)
            app.match_cloud_titles(titles)
        self.assertEqual(first, {"series:the bear": {"id": "tt1", "name": "The Bear"}, "movie:x": None, "movie:y": None})
        self.assertEqual(sorted(calls), ["Offline", "Offline", "The Bear", "Unknown"], "only the failure is asked again")
        self.assertIn("cloud_match_v2:movie:unknown:2024", cache)

    def test_history_route_sends_groups(self):
        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/rd-history"
        handler._request_allowed = Mock(return_value=True)
        handler.send_json = Mock()
        items = [{"id": "a", "ids": ["a"], "filename": "The.Bear.S01E01.mkv", "download": "https://x/1", "generated": "2026-11-01"}]
        with patch.object(app, "load_config", return_value={"rd_token": "SECRET", "debrid_provider": "realdebrid"}), \
             patch.object(app, "rd_get_downloads", return_value=items) as listing:
            handler.do_GET()
        self.assertEqual(listing.call_args.kwargs["limit"], 100)
        payload = handler.send_json.call_args.args[0]
        self.assertEqual(payload["items"], items)
        self.assertEqual([g["key"] for g in payload["groups"]], ["series:the bear"])

    def test_real_debrid_entries_carry_their_size_in_bytes(self):
        listing = [{"id": "a", "filename": "Film.2024.mkv", "download": "https://x/1", "filesize": 3_000_000_000,
                    "generated": "2026-11-01T10:00:00.000Z"}]
        with patch.object(debrid_engine, "http_json", return_value=listing), \
             patch.object(debrid_engine, "load_config", return_value={}):
            items = debrid_engine.rd_get_downloads("SECRET", provider="realdebrid")
        self.assertEqual(items[0]["bytes"], 3_000_000_000)


if __name__ == "__main__":
    unittest.main()
