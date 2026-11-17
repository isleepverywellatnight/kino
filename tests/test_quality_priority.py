"""Setting « Priorité »: « Lancer vite » keeps the order KINO always had;
« La qualité choisie d'abord » puts the chosen quality (in an accepted
language) before everything else, even HDR on a standard screen or a source
still to prepare — while one click and the next episode take ready ones first."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import api_service
import app
import config
import one_click
import player_engine
import stream_engine
import torrent_engine

# Like the real Dune search: the ready 4K versions are HDR, the best SDR one is a 1080p.
SOURCES = {
    "1080p REMUX SDR MULTI ready": ("Dune.Part.Two.2024.1080p.BluRay.REMUX.MULTI.SDR.TrueHD.Atmos", 30.0, True),
    "4K HDR MULTI ready": ("Dune.Part.Two.2024.MULTI.2160p.WEB-DL.HDR.DDP5.1", 18.0, True),
    "4K HDR VFF to prepare": ("Dune.Part.Two.2024.VFF.2160p.WEB-DL.HDR10", 20.0, False),
    "4K Dolby Vision only ready": ("Dune.Part.Two.2024.MULTI.2160p.WEB-DL.DV", 19.0, True),
    "4K VO only ready": ("Dune.Part.Two.2024.2160p.WEB-DL.SDR", 17.0, True),
    "4K other year ready": ("Dune.2021.MULTI.2160p.WEB-DL.HDR", 17.0, True),
    "4K camera ready": ("Dune.Part.Two.2024.MULTI.2160p.HDTS", 17.0, True),
}


def rank(priority, pref_quality="4k", pref_lang="vf"):
    items = [{"title": title, "size_gb": size, "is_instant": ready, "seeders": 50, "label": label}
             for label, (title, size, ready) in SOURCES.items()]
    return torrent_engine.rank_streams(items, "score", titles=("Dune Part Two",), pref_lang=pref_lang, pref_quality=pref_quality,
                                       hdr_mode="sdr_pref", media_type="movie", runtime_minutes=166, release_year="2024",
                                       source_priority=priority)


class RankingTests(unittest.TestCase):
    def test_lancer_vite_keeps_the_order_and_adds_nothing(self):
        ranked = rank("speed")
        self.assertEqual(ranked[0]["label"], "1080p REMUX SDR MULTI ready")
        self.assertTrue(all(item["score_parts"]["priority_points"] == 0 for item in ranked))
        self.assertEqual([item["score"] for item in ranked],
                         [item["score"] for item in rank("anything else")], "an unknown value is « Lancer vite »")

    def test_quality_first_puts_the_chosen_quality_first_ready_ones_leading(self):
        ranked = [item["label"] for item in rank("quality")]
        self.assertEqual(ranked[:2], ["4K HDR MULTI ready", "4K HDR VFF to prepare"],
                         "4K in French first, the ready one leading, even in HDR or to prepare")
        self.assertEqual(ranked[2], "1080p REMUX SDR MULTI ready")
        first = rank("quality")[0]
        self.assertEqual(first["score_parts"]["priority_points"], torrent_engine.QUALITY_FIRST_POINTS)
        self.assertIn({"part": "priority_points", "text": "La qualité choisie d’abord", "tone": "good",
                       "points": torrent_engine.QUALITY_FIRST_POINTS}, first["why"])

    def test_what_stays_out_of_quality_first(self):
        parts = {item["label"]: item["score_parts"]["priority_points"] for item in rank("quality")}
        for label in ("4K Dolby Vision only ready", "4K VO only ready", "4K other year ready", "4K camera ready",
                      "1080p REMUX SDR MULTI ready"):
            self.assertEqual(parts[label], 0, label)

    def test_the_chosen_quality_and_language_lead_the_way(self):
        ranked = rank("quality", pref_quality="1080p")
        self.assertEqual(ranked[0]["label"], "1080p REMUX SDR MULTI ready")
        self.assertEqual({item["label"] for item in ranked if item["score_parts"]["priority_points"]}, {"1080p REMUX SDR MULTI ready"})
        vostfr = {item["label"]: item["score_parts"]["priority_points"] for item in rank("quality", pref_lang="vostfr")}
        self.assertTrue(vostfr["4K VO only ready"], "for VOSTFR, the original version is accepted")

    def test_score_torrent_and_its_breakdown_know_the_setting(self):
        item = {"title": "Film.2024.MULTI.2160p.WEB-DL", "size_gb": 15.0, "is_instant": False}
        self.assertEqual(torrent_engine.score_torrent(item, target_year="2024", priority="quality")
                         - torrent_engine.score_torrent(item, target_year="2024"), torrent_engine.QUALITY_FIRST_POINTS)
        self.assertEqual(dict(torrent_engine.score_breakdown(item))["priority_points"], 0)


class SettingTests(unittest.TestCase):
    def test_only_the_two_values_are_saved_and_the_page_gets_it_back(self):
        self.assertEqual(config.sanitize_config_payload({"source_priority": "QUALITY"})["source_priority"], "quality")
        self.assertEqual(config.sanitize_config_payload({"source_priority": "speed"})["source_priority"], "speed")
        self.assertNotIn("source_priority", config.sanitize_config_payload({"source_priority": "fastest"}))
        with patch.object(config, "load_config", return_value={"source_priority": "quality"}):
            self.assertEqual(api_service.build_config_response()["source_priority"], "quality")
        with patch.object(config, "load_config", return_value={}):
            self.assertEqual(api_service.build_config_response()["source_priority"], "speed")

    def test_the_search_carries_it_and_caches_each_order_apart(self):
        speed = stream_engine.StreamQuery.from_params({"imdb_id": "tt1"}, {})
        quality = stream_engine.StreamQuery.from_params({"imdb_id": "tt1"}, {"source_priority": "quality"})
        odd = stream_engine.StreamQuery.from_params({"imdb_id": "tt1"}, {"source_priority": "fastest"})
        self.assertEqual((speed.source_priority, quality.source_priority, odd.source_priority), ("speed", "quality", "speed"))
        self.assertNotEqual(speed.cache_key("realdebrid", True), quality.cache_key("realdebrid", True))
        adapter = SimpleNamespace(name="realdebrid", token="")
        with patch.object(torrent_engine, "search_multi_torrents", return_value=[]) as search, \
                patch.object(config, "cached_get", side_effect=lambda key, ttl, fetch: fetch()):
            stream_engine.get_available_streams(quality, debrid_adapter=adapter)
        self.assertEqual(search.call_args.kwargs["source_priority"], "quality")


def source(n, ready):
    return {"info_hash": f"h{n}", "magnet": f"magnet:{n}", "title": f"Release {n}", "is_instant": ready, "score": 1000 - n}


class WaitingTests(unittest.TestCase):
    def test_ready_first_only_in_quality_first(self):
        sources = [source(1, False), source(2, True), source(3, False), source(4, True)]
        self.assertIs(one_click.ready_first(sources, {}), sources)
        self.assertEqual([s["magnet"] for s in one_click.ready_first(sources, {"source_priority": "quality"})],
                         ["magnet:2", "magnet:4", "magnet:1", "magnet:3"], "ready ones first, each group in its order")

    def test_one_click_tries_a_ready_source_before_one_to_prepare(self):
        body = {"imdb_id": "tt1", "type": "movie", "title": "Film", "player_mode": "integrated"}
        query = SimpleNamespace(runtime_minutes=None, media_type="movie", title="Film", release_year="", season=1, episode=1)
        tried = []

        def debrid(token, magnet, **kwargs):
            tried.append(magnet)
            return {"ready": True, "files": [{"filename": "Film.mkv", "download": f"https://debrid.example/{magnet[-1]}"}]}

        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/one-click-play"
        handler._request_allowed = Mock(return_value=True)
        handler.read_json_body = Mock(return_value=body)
        handler.send_json = Mock()
        with patch.object(app, "load_config", return_value={"rd_token": "t", "source_priority": "quality"}), \
                patch.object(app, "get_media_meta", return_value={}), patch.object(app.StreamQuery, "from_params", return_value=query), \
                patch.object(app, "get_available_streams", return_value=[source(1, False), source(2, True)]), \
                patch.object(app, "rd_debrid_magnet", side_effect=debrid), patch.object(app.playback_support, "excluded", return_value=set()), \
                patch.object(app, "get_resume_position", return_value=0):
            handler.do_POST()
        self.assertEqual(tried, ["magnet:2"])
        self.assertEqual(handler.send_json.call_args.args[0]["stream_url"], "https://debrid.example/2")

    def test_the_next_episode_too(self):
        tried = []

        def debrid(token, magnet, **kwargs):
            tried.append(magnet)
            return {"ready": True, "files": [{"filename": "e.mkv", "download": "https://debrid.example/e"}]}

        with patch.object(player_engine, "AUTO_STREAM_CACHE", {}), \
                patch.object(player_engine, "load_config", return_value={"rd_token": "t", "source_priority": "quality"}), \
                patch.object(player_engine.stream_engine, "get_available_streams", return_value=[source(1, False), source(2, True)]), \
                patch.object(player_engine, "rd_debrid_magnet", side_effect=debrid), patch.object(player_engine, "record_history"):
            player_engine.resolve_auto_stream_episode({"imdb_id": "tt9", "season": 1, "episode": 2, "name": "Show"})
        self.assertEqual(tried, ["magnet:2"])


if __name__ == "__main__":
    unittest.main()
