"""« Mes séries »: where each followed series stands, what is left and what
comes next; series of Ma Liste not started apart; never an episode title."""
import json
import unittest
from unittest.mock import Mock, patch

import app
import series_progress

TODAY = "2026-11-12"


def episodes(*specs):
    """specs: (season, episode, released) — every episode named « SECRET » to catch spoilers."""
    return [{"season": s, "episode": e, "released": f"{day}T00:00:00.000Z", "name": f"SECRET {s}x{e}"} for s, e, day in specs]


SHOW = episodes((1, 1, "2025-01-01"), (1, 2, "2025-01-08"), (1, 3, "2025-01-15"), (2, 1, "2026-10-01"), (2, 2, "2026-10-08"),
                (2, 3, "2026-11-16"), (0, 1, "2025-12-25"))


def entry(**fields):
    return {"id": "tt1", "type": "series", "name": "Série", "year": "2025", "poster": "p.jpg", "last_watched": 100, **fields}


class FollowedTests(unittest.TestCase):
    def test_an_episode_in_progress_is_resumed(self):
        item = series_progress.followed(entry(season=1, episode=3, progress_pct=40), SHOW, TODAY)
        self.assertEqual(item["group"], "continue")
        self.assertEqual(item["current"], {"season": 1, "episode": 3, "progress_pct": 40})
        self.assertEqual(item["next"], {"season": 1, "episode": 3, "released": "", "resume": True, "progress_pct": 40})
        self.assertEqual((item["aired"], item["seen"], item["remaining"]), (5, 2, 3), "specials and the coming episode left out")
        self.assertEqual(item["upcoming"], {"season": 2, "episode": 3, "released": "2026-11-16"})

    def test_a_finished_episode_leads_to_the_next_aired_one(self):
        item = series_progress.followed(entry(season=1, episode=3, progress_pct=92), SHOW, TODAY)
        self.assertEqual(item["next"], {"season": 2, "episode": 1, "released": "2026-10-01", "resume": False, "progress_pct": 0})
        self.assertEqual((item["seen"], item["remaining"]), (3, 2))
        self.assertEqual(item["current"]["progress_pct"], 100)

    def test_episodes_marked_seen_count_too(self):
        item = series_progress.followed(entry(season=1, episode=1, completed=True, watched_episodes=["S02E01"],
                                              ep_positions={"S01E02": {"completed": True}}), SHOW, TODAY)
        self.assertEqual(item["next"]["episode"], 3)
        self.assertEqual((item["seen"], item["remaining"]), (3, 2))

    def test_up_to_date_and_finished(self):
        item = series_progress.followed(entry(season=2, episode=2, completed=True), SHOW, TODAY)
        self.assertEqual((item["group"], item["next"], item["remaining"]), ("uptodate", None, 0))
        self.assertEqual(item["upcoming"]["episode"], 3)
        self.assertFalse(item["finished"], "an episode is still to come")
        done = series_progress.followed(entry(season=2, episode=2, completed=True, series_finished=True), SHOW[:5], TODAY)
        self.assertTrue(done["finished"])

    def test_without_the_episode_list_the_series_still_shows(self):
        item = series_progress.followed(entry(season=2, episode=1, progress_pct=10), None, TODAY)
        self.assertEqual((item["known"], item["next"], item["group"]), (False, None, "continue"))
        self.assertEqual(item["current"], {"season": 2, "episode": 1, "progress_pct": 10})


class PageTests(unittest.TestCase):
    def test_groups_order_and_what_is_left_out(self):
        history = [
            entry(id="tt1", name="Ancienne", season=1, episode=1, completed=True, last_watched=10),
            entry(id="tt2", name="Récente", season=1, episode=1, completed=True, last_watched=50),
            entry(id="tt3", name="À jour bientôt", season=2, episode=2, completed=True),
            entry(id="tt4", name="À jour plus tard", season=2, episode=2, completed=True),
            entry(id="tt5", name="Importée", imported_watched=True),
            {"id": "tt6", "type": "movie", "name": "Un film"},
        ]
        watchlist = [{"id": "tt7", "type": "series", "name": "Pas commencée"}, {"id": "tt2", "type": "series", "name": "Récente"},
                     {"id": "tt8", "type": "movie", "name": "Film de la liste"}]
        later = SHOW[:5] + episodes((2, 3, "2026-12-24"))
        lists = {"tt1": SHOW, "tt2": SHOW, "tt3": SHOW, "tt4": later, "tt7": SHOW}
        items = series_progress.my_series(history, watchlist, lists.get, TODAY)
        self.assertEqual([(i["name"], i["group"]) for i in items], [
            ("Récente", "continue"), ("Ancienne", "continue"), ("À jour bientôt", "uptodate"), ("À jour plus tard", "uptodate"),
            ("Pas commencée", "start")])
        start = items[-1]
        self.assertEqual((start["next"]["season"], start["next"]["episode"], start["remaining"], start["seen"]), (1, 1, 5, 0))
        self.assertNotIn("SECRET", json.dumps(items), "no episode title ever")

    def test_a_failing_episode_list_and_nothing_to_show(self):
        def failing(imdb_id):
            raise RuntimeError("down")

        items = series_progress.my_series([entry(season=1, episode=1, progress_pct=20)], [], failing, TODAY)
        self.assertEqual(items[0]["known"], False)
        self.assertEqual(series_progress.my_series([], [], failing, TODAY), [])

    def test_the_route(self):
        request = object.__new__(app.RequestHandler)
        request.path = "/api/my-series"
        request.headers = {}
        request._request_allowed = Mock(return_value=True)
        request.send_json = Mock()
        with patch.object(app, "load_config", return_value={"history": [entry(season=1, episode=3, progress_pct=40)], "watchlist": []}), \
                patch.object(app.meta_engine, "series_videos", return_value=SHOW), patch.object(app.time, "strftime", return_value=TODAY):
            request.do_GET()
        answer = request.send_json.call_args.args[0]
        self.assertEqual(answer["today"], TODAY)
        self.assertEqual(answer["series"][0]["next"]["resume"], True)


if __name__ == "__main__":
    unittest.main()
