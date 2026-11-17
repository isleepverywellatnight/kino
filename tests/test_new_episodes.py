"""« Nouveaux épisodes »: episodes released since the last viewing of a followed series."""
import datetime
import unittest
from unittest.mock import Mock, patch

import app
import meta_engine
import new_episodes

TODAY = "2026-11-10"


def ts(day):
    return datetime.datetime.fromisoformat(day + "T20:00:00+00:00").timestamp()


def season(number, days, names=None):
    return [{"season": number, "episode": index + 1, "released": f"{day}T00:00:00.000Z",
             "name": (names or {}).get(index + 1, f"Episode {index + 1}"),
             "thumbnail": f"https://img/{number}/{index + 1}.jpg"} for index, day in enumerate(days)]


VIDEOS = {
    # Season 2 dropped on 5 Nov, after a viewing on 1 Nov.
    "tt1": season(1, ["2025-01-01"] * 3) + season(2, ["2026-11-05"] * 3, {1: "Black Door"}),
    # Weekly show: E3 aired 9 Nov, E4 airs on 16 Nov.
    "tt2": season(1, ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"]),
    # Nothing since the last viewing.
    "tt3": season(1, ["2026-10-01", "2026-10-08"]),
    # Released long ago, watched long ago: too old to be « new ».
    "tt4": season(1, ["2025-06-01", "2025-06-08"]),
    "tt5": None,
}


def find(history, today=TODAY):
    return new_episodes.find_new_episodes(history, lambda imdb_id: VIDEOS.get(imdb_id), today)


class FindTests(unittest.TestCase):
    def test_a_new_season_after_the_last_viewing(self):
        history = [{"id": "tt1", "name": "Black Doves", "type": "series", "season": 1, "episode": 3, "completed": True,
                    "last_watched": ts("2026-11-01"), "poster": "https://p/1.jpg", "year": "2024"}]
        (item,) = find(history)
        self.assertEqual((item["season"], item["episode"], item["episode_name"]), (2, 1, "Black Door"))
        self.assertEqual((item["new_count"], item["is_new"], item["in_progress"], item["newest"]), (3, True, False, "2026-11-05"))
        self.assertEqual(item["still"], "https://img/2/1.jpg")
        self.assertEqual((item["name"], item["poster"], item["year"]), ("Black Doves", "https://p/1.jpg", "2024"))

    def test_weekly_episodes_and_the_one_in_progress(self):
        history = [{"id": "tt2", "type": "series", "season": 1, "episode": 2, "completed": True, "last_watched": ts("2026-11-03")}]
        (item,) = find(history)
        self.assertEqual((item["episode"], item["new_count"], item["released"]), (3, 1, "2026-11-09"), "E4 has not aired")
        in_progress = [{"id": "tt2", "type": "series", "season": 1, "episode": 3, "progress_pct": 30, "last_watched": ts("2026-11-08")}]
        (item,) = find(in_progress)
        self.assertEqual((item["episode"], item["in_progress"], item["is_new"]), (3, True, True))

    def test_a_backlog_points_to_the_next_episode(self):
        history = [{"id": "tt2", "type": "series", "season": 1, "episode": 1, "completed": True, "last_watched": ts("2026-11-05")}]
        (item,) = find(history)
        self.assertEqual((item["episode"], item["is_new"], item["new_count"]), (2, False, 1),
                         "E2 aired before the viewing, E3 after: play E2, one new")

    def test_watched_episodes_are_skipped(self):
        history = [{"id": "tt1", "type": "series", "season": 1, "episode": 3, "completed": True, "last_watched": ts("2026-11-01"),
                    "watched_episodes": ["s2e1"], "ep_positions": {"S02E02": {"completed": True}}}]
        (item,) = find(history)
        self.assertEqual((item["episode"], item["new_count"]), (3, 1))

    def test_nothing_new_old_or_unknown_is_left_out(self):
        history = [
            {"id": "tt3", "type": "series", "season": 1, "episode": 1, "completed": True, "last_watched": ts("2026-11-01")},
            {"id": "tt4", "type": "series", "season": 1, "episode": 1, "completed": True, "last_watched": ts("2025-06-02")},
            {"id": "tt5", "type": "series", "season": 1, "episode": 1, "last_watched": ts("2026-11-01")},
            {"id": "tt1", "type": "movie", "last_watched": ts("2026-11-01")},
            {"id": "tt1", "type": "series", "imported_watched": True, "last_watched": ts("2026-11-01")},
            {"id": "local.mkv", "type": "series", "last_watched": ts("2026-11-01")},
            {"id": "tt2", "type": "series", "season": 1, "episode": 3, "completed": True},
        ]
        self.assertEqual(find(history), [], "tt3 caught up (E2 before the viewing), tt4 older than 60 days")

    def test_newest_first_and_failures_do_not_break_the_row(self):
        history = [
            {"id": "tt1", "type": "series", "season": 1, "episode": 3, "completed": True, "last_watched": ts("2026-11-01")},
            {"id": "tt2", "type": "series", "season": 1, "episode": 2, "completed": True, "last_watched": ts("2026-11-03")},
        ]
        def flaky(imdb_id):
            if imdb_id == "tt9":
                raise OSError("offline")
            return VIDEOS.get(imdb_id)
        items = new_episodes.find_new_episodes(history + [{"id": "tt9", "type": "series", "season": 1, "episode": 1,
                                                           "last_watched": ts("2026-11-01")}], flaky, TODAY)
        self.assertEqual([item["id"] for item in items], ["tt2", "tt1"])

    def test_episode_list_cleaning(self):
        cleaned = new_episodes.aired_episodes([
            {"season": 0, "episode": 1, "released": "2026-01-01"}, {"season": 1, "number": 2, "released": "2026-01-08", "thumbnail": "http://x"},
            {"season": 1, "episode": 1, "released": ""}, {"season": "x"}, None,
        ])
        self.assertEqual(cleaned, [{"season": 1, "episode": 2, "released": "2026-01-08", "name": "", "thumbnail": ""}])


LISTED = {
    # New series: S01E01 on 30 Oct, E2 on 6 Nov, E3 not aired yet.
    "tt20": season(1, ["2026-10-30", "2026-11-06", "2026-11-13"], {1: "Pilot"}),
    # Old show, season 3 began on 1 Nov.
    "tt21": season(1, ["2020-01-01"] * 2) + season(2, ["2022-01-01"] * 2) + season(3, ["2026-11-01", "2026-11-08"], {1: "Retour"}),
    # Nothing recent.
    "tt22": season(1, ["2024-01-01"] * 3),
    # Announced only.
    "tt23": season(1, ["2026-12-01"]),
}


def find_listed(watchlist, history=()):
    return new_episodes.find_new_episodes(list(history), lambda imdb_id: LISTED.get(imdb_id) or VIDEOS.get(imdb_id), TODAY,
                                          watchlist=watchlist)


class WatchlistTests(unittest.TestCase):
    def test_a_new_series_in_ma_liste_starts_at_its_first_episode(self):
        (item,) = find_listed([{"id": "tt20", "name": "Nocturne", "type": "series", "year": "2026", "poster": "https://p/20.jpg"}])
        self.assertEqual(item["kind"], "premiere")
        self.assertEqual((item["season"], item["episode"], item["episode_name"], item["released"]), (1, 1, "Pilot", "2026-10-30"))
        self.assertEqual((item["new_count"], item["newest"], item["is_new"], item["in_progress"]), (2, "2026-11-06", True, False))
        self.assertEqual((item["name"], item["poster"]), ("Nocturne", "https://p/20.jpg"))

    def test_a_new_season_of_a_series_not_started(self):
        (item,) = find_listed([{"id": "tt21", "name": "Old show", "type": "series"}])
        self.assertEqual((item["kind"], item["season"], item["episode"], item["new_count"]), ("season", 3, 1, 2))

    def test_nothing_recent_announced_or_already_followed(self):
        watchlist = [{"id": "tt22", "type": "series"}, {"id": "tt23", "type": "series"}, {"id": "tt20", "type": "movie"},
                     {"id": "tt1", "type": "series"}]
        history = [{"id": "tt1", "type": "series", "season": 1, "episode": 3, "completed": True, "last_watched": ts("2026-11-01")}]
        items = find_listed(watchlist, history)
        self.assertEqual([(item["id"], item["kind"]) for item in items], [("tt1", "followed")],
                         "a series already started is followed from its history, not as a premiere")
        imported = [{"id": "tt20", "type": "movie", "imported_watched": True}]
        self.assertEqual(find_listed([{"id": "tt20", "type": "series"}], imported), [], "anything in the history is skipped")

    def test_both_lists_sorted_together(self):
        history = [{"id": "tt2", "type": "series", "season": 1, "episode": 2, "completed": True, "last_watched": ts("2026-11-03")}]
        items = find_listed([{"id": "tt20", "type": "series"}, {"id": "tt21", "type": "series"}], history)
        self.assertEqual([(item["id"], item["newest"]) for item in items],
                         [("tt2", "2026-11-09"), ("tt21", "2026-11-08"), ("tt20", "2026-11-06")])


class SourceTests(unittest.TestCase):
    def test_series_videos_are_light_and_not_cached_on_failure(self):
        payload = {"meta": {"videos": [{"season": 1, "episode": 1, "name": "A &amp; B", "released": "2026-01-01T00:00:00.000Z",
                                        "thumbnail": "https://t/1.jpg", "overview": "long"}]}}
        with patch.object(meta_engine, "http_json", return_value=payload), \
                patch.object(meta_engine, "cached_get", side_effect=lambda key, ttl, fetch: (key, ttl, fetch())):
            key, ttl, videos = meta_engine.series_videos("tt123")
        self.assertEqual((key, ttl), ("series_videos_v1:tt123", 21600))
        self.assertEqual(videos, [{"season": 1, "episode": 1, "name": "A & B", "released": "2026-01-01", "thumbnail": "https://t/1.jpg"}])
        with patch.object(meta_engine, "http_json", side_effect=OSError("down")), \
                patch.object(meta_engine, "cached_get", side_effect=lambda key, ttl, fetch: fetch()):
            self.assertIsNone(meta_engine.series_videos("tt123"))
        self.assertIsNone(meta_engine.series_videos("../x"))

    def test_route(self):
        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/new-episodes"
        handler._request_allowed = Mock(return_value=True)
        handler.send_json = Mock()
        with patch.object(app, "load_config", return_value={"history": [{"id": "tt1"}], "watchlist": [{"id": "tt2"}]}), \
                patch.object(app.new_episodes, "find_new_episodes", return_value=[{"id": "tt1"}]) as finder:
            handler.do_GET()
        body = handler.send_json.call_args.args[0]
        self.assertEqual(body["episodes"], [{"id": "tt1"}])
        self.assertRegex(body["today"], r"^\d{4}-\d{2}-\d{2}$")
        history, source, today = finder.call_args.args
        self.assertEqual((history, source, today), ([{"id": "tt1"}], meta_engine.series_videos, body["today"]))
        self.assertEqual(finder.call_args.kwargs, {"watchlist": [{"id": "tt2"}]})


if __name__ == "__main__":
    unittest.main()
