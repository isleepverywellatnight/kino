"""« Préparer en un clic »: with « La qualité choisie d'abord », one click prepares the
best source in the chosen quality when none is ready, answers « preparing » with the
progress, then plays the prepared torrent; it never waits otherwise."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import app
import one_click

QUALITY = {"rd_token": "tok", "source_priority": "quality"}


def source(n, *, tier, ready):
    return {"info_hash": f"h{n}", "magnet": f"magnet:{n}", "title": f"Film.2024.{'2160p' if tier else '1080p'}.{n}",
            "source": "Torrentio", "is_instant": ready, "score_parts": {"priority_points": 10000 if tier else 0}}


# In the server's order: the chosen quality first (its +10000), then the ready 1080p.
FOUR_K_TO_PREPARE = [source(2, tier=True, ready=False), source(3, tier=True, ready=False), source(1, tier=False, ready=True)]
READY = {"ready": True, "files": [{"filename": "Film.2024.2160p.mkv", "download": "https://debrid.example/film.mkv"}]}
PREPARING = {"ready": False, "torrent_id": "RD123", "status": "downloading", "progress": 12.5, "speed": "8 MB/s", "seeders": 40, "files": []}


class PrepareTests(unittest.TestCase):
    def play(self, body, sources, *, cfg=QUALITY, debrid=None, check=None, excluded=()):
        query = SimpleNamespace(runtime_minutes=None, media_type="movie", title="Film", release_year="", season=1, episode=1)
        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/one-click-play"
        handler._request_allowed = Mock(return_value=True)
        handler.read_json_body = Mock(return_value={"imdb_id": "tt1", "type": "movie", "title": "Film", "player_mode": "integrated", **body})
        handler.send_json = Mock()
        with patch.object(app, "load_config", return_value=cfg), patch.object(app, "get_media_meta", return_value={}), \
                patch.object(app.StreamQuery, "from_params", return_value=query), \
                patch.object(app, "get_available_streams", return_value=[dict(s) for s in sources]), \
                patch.object(app, "rd_debrid_magnet", side_effect=debrid or (lambda *a, **k: READY)) as magnet, \
                patch.object(app, "rd_check_torrent", side_effect=check or (lambda *a, **k: READY)) as status, \
                patch.object(app.playback_support, "excluded", return_value=set(excluded)), \
                patch.object(app, "get_resume_position", return_value=0), patch.object(app, "launch_mpv") as launch:
            handler.do_POST()
        self.calls = SimpleNamespace(magnet=magnet, status=status, launch=launch)
        return handler.send_json.call_args

    def test_no_ready_4k_the_best_one_is_prepared_and_the_page_waits(self):
        sent = self.play({}, FOUR_K_TO_PREPARE, debrid=lambda *a, **k: dict(PREPARING))
        self.assertEqual(sent.kwargs, {"status": 202})
        self.assertEqual(sent.args[0], {
            "preparing": True, "torrent_id": "RD123", "progress": 12, "status": "downloading", "speed": "8 MB/s", "seeders": 40,
            "source_key": app.stream_candidate_key(FOUR_K_TO_PREPARE[0]), "chosen_torrent": "Torrentio Film.2024.2160p.2",
            "ready_alternative": True})
        self.assertEqual([c.args[1] for c in self.calls.magnet.call_args_list], ["magnet:2"], "only the best 4K, never the ready 1080p")

    def test_a_4k_that_debrid_already_has_plays_at_once(self):
        sent = self.play({}, FOUR_K_TO_PREPARE)
        self.assertEqual(sent.args[0]["stream_url"], "https://debrid.example/film.mkv")
        self.assertEqual(sent.args[0]["source_key"], app.stream_candidate_key(FOUR_K_TO_PREPARE[0]))
        self.assertEqual(sent.kwargs.get("status", 200), 200)

    def test_the_prepared_torrent_plays_without_asking_debrid_again(self):
        key = app.stream_candidate_key(FOUR_K_TO_PREPARE[0])
        sent = self.play({"prepared": {"torrent_id": "RD123", "source_key": key}}, FOUR_K_TO_PREPARE)
        self.assertEqual(sent.args[0]["stream_url"], "https://debrid.example/film.mkv")
        self.assertEqual((sent.args[0]["source_key"], sent.args[0]["attempted_source_keys"]), (key, [key]))
        self.assertEqual(sent.args[0]["chosen_torrent"], "Torrentio Film.2024.2160p.2")
        self.calls.status.assert_called_once_with("tok", "RD123", season=None, episode=None)
        self.calls.magnet.assert_not_called()

    def test_a_prepared_torrent_still_downloading_or_without_the_file(self):
        key = app.stream_candidate_key(FOUR_K_TO_PREPARE[0])
        sent = self.play({"prepared": {"torrent_id": "RD123", "source_key": key}}, FOUR_K_TO_PREPARE, check=lambda *a, **k: dict(PREPARING))
        self.assertEqual((sent.kwargs, sent.args[0]["progress"]), ({"status": 202}, 12))
        wrong = {"ready": True, "files": [{"filename": "Other.Movie.1999.mkv", "download": "ftp://not-http"}]}
        sent = self.play({"prepared": {"torrent_id": "RD123", "source_key": key}}, FOUR_K_TO_PREPARE, check=lambda *a, **k: wrong)
        self.assertEqual(sent.args, (one_click.PREPARED_WRONG_FILE,))
        self.assertEqual(sent.kwargs, {"status": 422})

    def test_a_ready_4k_the_ready_choice_lancer_vite_or_a_strange_prepared_value_never_wait(self):
        with_ready_4k = [source(4, tier=True, ready=True)] + FOUR_K_TO_PREPARE
        self.play({}, with_ready_4k)
        self.assertEqual(self.calls.magnet.call_args.args[1], "magnet:4")
        sent = self.play({"prefer_ready": True}, FOUR_K_TO_PREPARE, debrid=lambda *a, **k: dict(PREPARING) if a[1] != "magnet:1" else READY)
        self.assertEqual(self.calls.magnet.call_args_list[0].args[1], "magnet:1", "« Lancer une source prête à la place »")
        self.assertNotIn("preparing", sent.args[0])
        sent = self.play({}, FOUR_K_TO_PREPARE, cfg={"rd_token": "tok"},
                         debrid=lambda *a, **k: dict(PREPARING) if a[1] != "magnet:1" else READY)
        self.assertNotIn("preparing", sent.args[0], "« Lancer vite » never waits")
        self.assertEqual([c.args[1] for c in self.calls.magnet.call_args_list], ["magnet:2", "magnet:3", "magnet:1"],
                         "the server's order, unready sources skipped, as before")
        self.play({"prepared": {"torrent_id": "../etc", "source_key": "x"}}, FOUR_K_TO_PREPARE, debrid=lambda *a, **k: dict(PREPARING))
        self.calls.status.assert_not_called()

    def test_a_source_set_aside_is_not_prepared_and_a_refusal_stops(self):
        first = app.stream_candidate_key(FOUR_K_TO_PREPARE[0])
        self.play({"exclude_source_keys": [first]}, FOUR_K_TO_PREPARE, debrid=lambda *a, **k: dict(PREPARING))
        self.assertEqual(self.calls.magnet.call_args_list[0].args[1], "magnet:3")

        def refuse(*args, **kwargs):
            raise HTTPError("https://api.example", 401, "no", {}, None)

        sent = self.play({}, FOUR_K_TO_PREPARE, debrid=refuse)
        self.assertEqual((sent.args[0]["code"], sent.kwargs), ("provider_auth", {"status": 401}))
        self.assertEqual(self.calls.magnet.call_count, 1)

    def test_a_failed_preparation_falls_back_to_ready_sources(self):
        def flaky(token, magnet, **kwargs):
            if magnet == "magnet:2":
                raise RuntimeError("Real-Debrid a refusé ce torrent (statut : dead)")
            return READY

        sent = self.play({}, FOUR_K_TO_PREPARE, debrid=flaky)
        self.assertEqual([c.args[1] for c in self.calls.magnet.call_args_list], ["magnet:2", "magnet:1"])
        self.assertTrue(sent.args[0]["ok"])


class HelperTests(unittest.TestCase):
    def test_the_prepared_value_is_checked(self):
        key = "0123456789abcdef0123456789abcdef"
        self.assertEqual(one_click.prepared({"prepared": {"torrent_id": "tb:42", "source_key": key}}), {"torrent_id": "tb:42", "source_key": key})
        for bad in ({"torrent_id": "a b", "source_key": key}, {"torrent_id": "x", "source_key": "nope"}, "RD1", None):
            self.assertIsNone(one_click.prepared({"prepared": bad}), bad)

    def test_progress_is_kept_between_0_and_100(self):
        for given, shown in ((150, 100), (-3, 0), ("x", 0), ("42.9", 42)):
            self.assertEqual(one_click.preparing({"progress": given}, {}, "k", [])["progress"], shown)


if __name__ == "__main__":
    unittest.main()
