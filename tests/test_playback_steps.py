"""Playback, step by step (docs/playback.md): each step on its own, and the
long functions kept short. test_playback_pins.py checks that the answers
did not change."""
import ast
import inspect
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import one_click
import player_engine
import stream_engine
import torrent_engine

ROOT = Path(__file__).resolve().parent.parent


def ready(url="https://debrid.example/a.mkv"):
    return {"ready": True, "files": [{"filename": "a.mkv", "download": url}]}


def source(n):
    return {"info_hash": f"h{n}", "magnet": f"magnet:{n}", "title": f"Release {n}"}


class ScoreParts(unittest.TestCase):
    def test_eleven_parts_that_add_up_to_the_score(self):
        item = {"title": "Dune.Part.Two.2024.MULTI.VFF.2160p.WEB-DL.DV.HDR.DDP5.1.Atmos-FRATERNiTY", "size_gb": 24.0,
                "seeders": 150, "is_instant": True, "source": "Torrentio"}
        parts = torrent_engine.score_breakdown(item, target_year="2024", runtime_minutes=166)
        self.assertEqual([name for name, _ in parts], ["instant_points", "quality_points", "language_points", "picture_points",
                                                     "sound_points", "seeder_points", "fake_penalty", "size_points",
                                                     "anime_group_points", "year_points", "priority_points"])
        self.assertEqual(dict(parts), {"instant_points": 3500, "quality_points": 300, "language_points": 950, "picture_points": 0,
                                       "sound_points": 80, "seeder_points": 80, "fake_penalty": 0, "size_points": 60,
                                       "anime_group_points": 0, "year_points": 500, "priority_points": 0})
        self.assertEqual(sum(points for _, points in parts), torrent_engine.score_torrent(item, target_year="2024", runtime_minutes=166))

    def test_each_part_says_which_question_it_answers(self):
        for number, part in enumerate(torrent_engine.SCORE_PARTS, start=1):
            self.assertTrue(part.__doc__.startswith(f"{number}. "), part.__name__)


class SourceSearchSteps(unittest.TestCase):
    def test_the_indexers_in_launch_order(self):
        sources = torrent_engine.indexer_sources("movie/tt1")
        self.assertEqual([(key, label, timeout) for key, _, label, timeout in sources], [
            ("main", "Torrentio", 20), ("fr", "Torrentio FR", 10), ("anime", "Torrentio Anime (Nyaa)", 10), ("comet", "Comet", 10),
            ("knightcrawler", "KnightCrawler", 4), ("tpb", "TPB+", 6), ("peerflix", "Peerflix", 6)])
        self.assertEqual(torrent_engine.stream_target("tt1", "anime", "2", "3"), "series/tt1:2:3")
        self.assertEqual(torrent_engine.stream_target("tt1"), "movie/tt1")

    def test_a_duplicate_brings_its_languages_and_its_instant_link(self):
        first = {"info_hash": "AB", "title": "x", "langs": ["VFF"], "qualities": ["1080p"], "is_instant": False}
        copy = {"info_hash": "ab", "title": "x", "langs": ["MULTI", "VFF"], "qualities": ["4K"], "is_instant": True, "resolve_url": "u"}
        loose = {"title": "no hash"}
        buckets = {key: [] for key in torrent_engine.MERGE_ORDER}
        buckets.update(fr=[first], comet=[copy], apibay=[loose])
        merged = torrent_engine.merge_streams(buckets)
        self.assertEqual(merged, [first, loose])
        self.assertEqual((first["langs"], first["qualities"], first["is_instant"], first["resolve_url"]),
                         (["VFF", "MULTI"], ["4K", "1080p"], True, "u"))

    def test_search_multi_torrents_is_the_sum_of_its_steps(self):
        source_text = inspect.getsource(torrent_engine.search_multi_torrents)
        body = source_text[source_text.index('"""', source_text.index('"""') + 3) + 3:]
        for step in ("stream_target", "fetch_streams", "indexer_sources", "merge_streams", "keep_relevant_streams", "rank_streams"):
            self.assertIn(step + "(", body)


class TrySources(unittest.TestCase):
    def attempt(self, candidates, debrid, **kwargs):
        use = kwargs.pop("use", Mock(side_effect=lambda candidate, answer, target, key: target["download"]))
        with patch.object(stream_engine, "select_playable_file", side_effect=kwargs.pop("select", lambda q, files, *a: files[0])):
            return stream_engine.try_sources("tok", candidates, "query", debrid=debrid, use=use, **kwargs), use

    def test_the_first_working_source_is_used(self):
        answers = iter([{"ready": False}, ready("https://debrid.example/2.mkv")])
        attempts, use = self.attempt([source(1), source(2), source(3)], lambda *a, **k: next(answers), season=2, episode=5)
        self.assertTrue(attempts.found)
        self.assertEqual(attempts.result, "https://debrid.example/2.mkv")
        self.assertEqual(attempts.key, stream_engine.stream_candidate_key(source(2)))
        self.assertEqual(attempts.tried, [stream_engine.stream_candidate_key(source(n)) for n in (1, 2)])
        self.assertEqual(use.call_args.args[0], source(2))

    def test_set_aside_sources_count_in_the_limit(self):
        candidates = [source(n) for n in range(5)]
        skip = {stream_engine.stream_candidate_key(item) for item in candidates[:3]}
        debrid = Mock(return_value=ready())
        attempts, _ = self.attempt(candidates, debrid, skip=skip, limit=3)
        self.assertFalse(attempts.found)
        debrid.assert_not_called()

    def test_a_failure_anywhere_moves_on_and_is_remembered(self):
        fails = iter([RuntimeError("player"), "https://debrid.example/ok.mkv"])

        def use(*args):
            answer = next(fails)
            if isinstance(answer, Exception):
                raise answer
            return answer

        attempts, _ = self.attempt([source(1), source(2)], lambda *a, **k: ready(), use=use)
        self.assertEqual(attempts.result, "https://debrid.example/ok.mkv")
        attempts, _ = self.attempt([source(1)], Mock(side_effect=OSError("down")))
        self.assertEqual((attempts.found, str(attempts.last_error)), (False, "down"))
        attempts, _ = self.attempt([source(1)], lambda *a, **k: ready(), select=lambda *a: None)
        self.assertFalse(attempts.found)

    def test_a_stopping_error_ends_everything(self):
        debrid = Mock(side_effect=PermissionError("refused"))
        with self.assertRaises(PermissionError):
            self.attempt([source(1), source(2)], debrid, stop=lambda error: isinstance(error, PermissionError))
        self.assertEqual(debrid.call_count, 1)

    def test_names_are_given_to_the_file_choice_only_when_asked(self):
        seen = []
        self.attempt([source(1)], lambda *a, **k: ready(), select=lambda q, files, *aliases: seen.append(aliases) or files[0])
        self.attempt([source(1)], lambda *a, **k: ready(), aliases_for=lambda c: ["Film"],
                     select=lambda q, files, *aliases: seen.append(aliases) or files[0])
        self.assertEqual(seen, [(), (["Film"],)])


class OneClickSteps(unittest.TestCase):
    def test_what_was_asked(self):
        with patch.object(one_click.sys, "platform", "darwin"):
            play = one_click.request({"imdb_id": "tt9", "type": "anime", "title": "Show — S01E02"}, {})
        self.assertEqual(play, {"imdb_id": "tt9", "type": "anime", "is_series": True, "season": 1, "episode": 1, "title": "Show — S01E02",
                                "series_name": "Show", "year": "", "poster": "", "player_mode": "integrated"})
        with patch.object(one_click.sys, "platform", "win32"):
            self.assertEqual(one_click.request({}, {})["player_mode"], "kino")
            self.assertEqual(one_click.request({}, {"player_mode": "VLC"})["player_mode"], "vlc")

    def test_sources_set_aside(self):
        good = "0123456789abcdef0123456789abcdef"
        self.assertEqual(one_click.set_aside({"exclude_source_keys": [good, good.upper(), "x", 3]}), {good})
        self.assertEqual(one_click.set_aside({"exclude_source_keys": "nope"}), set())
        many = [f"{n:032x}" for n in range(40)]
        self.assertEqual(len(one_click.set_aside({"exclude_source_keys": many})), 30)

    def test_names_context_and_player(self):
        film = {"imdb_id": "tt1", "type": "movie", "is_series": False, "season": 1, "episode": 1, "series_name": "Film",
                "year": "2024", "poster": "", "player_mode": "external"}
        self.assertEqual(one_click.file_aliases(film, {"name": "Film VO"}, {"title": "Film.2024.1080p"})[0], "Film VO")
        self.assertEqual(one_click.file_aliases({**film, "is_series": True}, {}, {}), [])
        self.assertIsNone(one_click.media_context({**film, "imdb_id": ""}, {"filename": "f"}))
        self.assertEqual(one_click.runtime_minutes({"runtime": "1 h 50 min"}), 1)
        self.assertEqual(one_click.runtime_minutes({"runtime": "108 min"}), 108)
        self.assertEqual(one_click.runtime_minutes(None), 0)
        for platform, mode, built_in in (("darwin", "external", False), ("darwin", "kino", True), ("linux", "kino", False),
                                         ("linux", "integrated", True)):
            with patch.object(one_click.sys, "platform", platform):
                self.assertEqual(one_click.uses_built_in_player({**film, "player_mode": mode}), built_in, (platform, mode))


class ShortFunctions(unittest.TestCase):
    LIMITS = {
        (ROOT / "src" / "app.py", "post_one_click_play"): 60,
        (ROOT / "src" / "app.py", "start_one_click"): 60,
        (ROOT / "src" / "torrent_engine.py", "search_multi_torrents"): 40,
        (ROOT / "src" / "torrent_engine.py", "score_torrent"): 30,
        (ROOT / "src" / "player_engine.py", "resolve_auto_stream_episode"): 70,
    }

    def test_the_long_playback_functions_stay_short(self):
        for (path, name), limit in self.LIMITS.items():
            node = next(n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                        if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertLessEqual(node.end_lineno - node.lineno + 1, limit, name)

    def test_the_document_tells_every_step(self):
        document = (ROOT / "docs" / "dev" / "playback.md").read_text(encoding="utf-8")
        for step in ("one_click.request", "one_click.set_aside", "stream_engine.try_sources", "start_one_click",
                     "one_click.uses_built_in_player", "resolve_auto_stream_episode", "indexer_sources", "fetch_streams",
                     "merge_streams", "keep_relevant_streams", "rank_streams", "score_breakdown"):
            self.assertIn(f"`{step}", document, step)
        for part in torrent_engine.SCORE_PARTS:
            self.assertIn(f"`{part.__name__}`", document)

    def test_the_old_names_still_answer(self):
        self.assertIs(app.stream_candidate_key, stream_engine.stream_candidate_key)
        self.assertIs(player_engine.stream_engine.try_sources, stream_engine.try_sources)


if __name__ == "__main__":
    unittest.main()
