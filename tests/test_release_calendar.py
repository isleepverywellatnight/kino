"""« Calendrier »: this week's and next week's episodes of the series followed,
day by day, grouped when a season comes out at once; then « Plus tard »;
never an episode title."""
import json
import unittest
from unittest.mock import Mock, patch

import app
import release_calendar

TODAY = "2026-11-12"  # a Thursday: the window runs from Monday 9 to Sunday 22 November


def episodes(*specs):
    """specs: (season, episode, released) — every episode named « SECRET » to catch spoilers."""
    return [{"season": s, "episode": e, "released": f"{day}T00:00:00.000Z", "name": f"SECRET {s}x{e}"} for s, e, day in specs]


WEEKLY = episodes((1, 1, "2026-10-26"), (1, 2, "2026-11-02"), (1, 3, "2026-11-09"), (1, 4, "2026-11-16"),
                  (1, 5, "2026-11-23"), (1, 6, "2026-11-30"), (0, 1, "2026-11-10"))
BINGE = episodes((1, 1, "2025-01-01"), (2, 1, "2026-11-13"), (2, 2, "2026-11-13"), (2, 3, "2026-11-13"))


def entry(**fields):
    return {"id": "tt1", "type": "series", "name": "Série", "year": "2026", "poster": "p.jpg", "last_watched": 100, **fields}


def calendar(history, watchlist, lists):
    return release_calendar.calendar(history, watchlist, lists.get, TODAY)


class WindowTests(unittest.TestCase):
    def test_monday_to_sunday_of_two_weeks(self):
        self.assertEqual(release_calendar.window(TODAY), ("2026-11-09", "2026-11-23"))
        self.assertEqual(release_calendar.window("2026-11-09"), ("2026-11-09", "2026-11-23"), "a Monday starts its own week")
        self.assertEqual(release_calendar.window("2026-11-15"), ("2026-11-09", "2026-11-23"), "a Sunday ends it")
        self.assertEqual(release_calendar.window("2026-12-31"), ("2026-12-28", "2027-01-11"))


class CalendarTests(unittest.TestCase):
    def test_days_states_and_later(self):
        data = calendar([entry(season=1, episode=2, completed=True)], [], {"tt1": WEEKLY})
        self.assertEqual((data["start"], data["end"], data["series"], data["unavailable"]), ("2026-11-09", "2026-11-23", 1, 0))
        self.assertEqual([day["date"] for day in data["days"]], ["2026-11-09", "2026-11-16"], "specials left out")
        out, coming = data["days"][0]["items"][0], data["days"][1]["items"][0]
        self.assertEqual((out["episode"], out["aired"], out["seen"], out["before"], out["followed"]), (3, True, False, 0, True))
        self.assertEqual((coming["episode"], coming["aired"], coming["before"]), (4, False, 1))
        self.assertEqual([(i["episode"], i["released"]) for i in data["later"]], [(5, "2026-11-23")], "first release after the window")
        self.assertNotIn("SECRET", json.dumps(data), "no episode title ever")

    def test_a_season_out_at_once_is_one_row(self):
        data = calendar([entry(season=1, episode=1, completed=True)], [], {"tt1": BINGE})
        row = data["days"][0]["items"]
        self.assertEqual(len(row), 1)
        self.assertEqual((row[0]["season"], row[0]["episode"], row[0]["last_episode"], row[0]["premiere"], row[0]["aired"]),
                         (2, 1, 3, "season", False))

    def test_seen_ones_apart_and_the_episode_under_way(self):
        watched = entry(season=1, episode=2, progress_pct=40, watched_episodes=["S02E01"])
        data = calendar([watched], [], {"tt1": episodes((1, 1, "2026-11-10"), (1, 2, "2026-11-10"), (1, 3, "2026-11-10"))})
        rows = data["days"][0]["items"]
        self.assertEqual([(r["episode"], r["last_episode"], r["seen"], r["resume_pct"]) for r in rows],
                         [(1, 1, True, 0), (2, 3, False, 40)])

    def test_ma_liste_series_not_started(self):
        data = calendar([], [{"id": "tt2", "type": "series", "name": "Nouvelle"}],
                        {"tt2": episodes((1, 1, "2026-11-11"), (1, 2, "2026-11-18"))})
        first, second = data["days"][0]["items"][0], data["days"][1]["items"][0]
        self.assertEqual((first["followed"], first["premiere"], first["before"], first["seen"]), (False, "series", 0, False))
        self.assertEqual(second["before"], 1)

    def test_order_within_a_day_and_series_without_a_list(self):
        def lists(imdb_id):
            if imdb_id == "tt9":
                raise RuntimeError("down")
            return {"tt1": WEEKLY, "tt2": WEEKLY, "tt3": WEEKLY}[imdb_id]

        history = [entry(id="tt1", name="Zèbre", season=1, episode=2, completed=True),
                   entry(id="tt3", name="Abeille", season=1, episode=2, completed=True), entry(id="tt9", name="Panne")]
        data = release_calendar.calendar(history, [{"id": "tt2", "type": "series", "name": "Avant"}], lists, TODAY)
        self.assertEqual([i["name"] for i in data["days"][0]["items"]], ["Abeille", "Zèbre", "Avant"], "followed first, then Ma Liste")
        self.assertEqual((data["series"], data["unavailable"]), (4, 1))

    def test_the_weeks_follow_the_macs_own_date(self):
        late_sunday = release_calendar.calendar([entry(season=1, episode=2, completed=True)], [], {"tt1": WEEKLY}.get,
                                                "2026-11-15", local_today="2026-11-16")
        self.assertEqual((late_sunday["start"], late_sunday["local_today"]), ("2026-11-16", "2026-11-16"),
                         "past midnight on Monday the new week shows, though it is still Sunday in UTC")
        self.assertFalse(late_sunday["days"][0]["items"][0]["aired"], "what is out still follows UTC, like the release dates")

    def test_nothing_followed(self):
        data = calendar([{"id": "tt5", "type": "movie", "name": "Film"}], [], {})
        self.assertEqual((data["series"], data["days"], data["later"]), (0, [], []))

    def test_the_route(self):
        request = object.__new__(app.RequestHandler)
        request.path = "/api/calendar"
        request.headers = {}
        request._request_allowed = Mock(return_value=True)
        request.send_json = Mock()
        with patch.object(app, "load_config", return_value={"history": [entry(season=1, episode=2, completed=True)], "watchlist": []}), \
                patch.object(app.meta_engine, "series_videos", return_value=WEEKLY), patch.object(app.time, "strftime", return_value=TODAY):
            request.do_GET()
        answer = request.send_json.call_args.args[0]
        self.assertEqual((answer["today"], answer["start"]), (TODAY, "2026-11-09"))
        self.assertEqual(answer["days"][0]["items"][0]["episode"], 3)


if __name__ == "__main__":
    unittest.main()
