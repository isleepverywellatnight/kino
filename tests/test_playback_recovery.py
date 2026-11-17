import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app


def handler_for(path, body):
    handler = object.__new__(app.RequestHandler)
    handler.path = path
    handler._request_allowed = Mock(return_value=True)
    handler.read_json_body = Mock(return_value=body)
    handler.send_json = Mock()
    return handler


class PlaybackRecoveryTests(unittest.TestCase):
    def test_one_click_chooses_matching_movie_inside_debrided_pack(self):
        source = {"info_hash": "pack", "magnet": "magnet:pack", "title": "A.Monster.Calls.2016.1080p", "score": 2000}
        body = {"imdb_id": "tt3416532", "type": "movie", "title": "Quelques minutes après minuit",
                "name": "Quelques minutes après minuit", "year": "2016", "player_mode": "integrated"}
        files = [
            {"filename": "The.Monster.2016.1080p.mkv", "download": "https://example.invalid/wrong", "filesize_bytes": 2_000_000_000},
            {"filename": "A.Monster.Calls.2016.1080p.mkv", "download": "https://example.invalid/right", "filesize_bytes": 3_000_000_000},
        ]
        query = SimpleNamespace(runtime_minutes=None, media_type="movie", title=body["title"], release_year="2016")
        with patch.object(app, "load_config", return_value={"rd_token": "test"}), \
             patch.object(app, "get_media_meta", return_value={"name": "A Monster Calls", "runtime": "108 min"}), \
             patch.object(app.StreamQuery, "from_params", return_value=query), \
             patch.object(app, "get_available_streams", return_value=[source]), \
             patch.object(app, "rd_debrid_magnet", return_value={"ready": True, "files": files}), \
             patch.object(app.playback_support, "excluded", return_value=set()), \
             patch.object(app, "get_resume_position", return_value=0):
            handler = handler_for("/api/one-click-play", body)
            handler.do_POST()
        result = handler.send_json.call_args.args[0]
        self.assertEqual(result["stream_url"], "https://example.invalid/right")

    def test_one_click_skips_explicitly_wrong_single_movie_file(self):
        sources = [
            {"info_hash": "first", "magnet": "magnet:first", "title": "A.Monster.Calls.2016", "score": 2000},
            {"info_hash": "second", "magnet": "magnet:second", "title": "A.Monster.Calls.2016", "score": 1000},
        ]
        body = {"imdb_id": "tt3416532", "type": "movie", "title": "Quelques minutes après minuit",
                "name": "Quelques minutes après minuit", "year": "2016", "player_mode": "integrated"}
        query = SimpleNamespace(runtime_minutes=None, media_type="movie", title=body["title"], release_year="2016")
        def resolve(_token, magnet, **_kwargs):
            filename = "The.Monster.2016.mkv" if magnet == "magnet:first" else "A.Monster.Calls.2016.mkv"
            return {"ready": True, "files": [{"filename": filename,
                    "download": "https://example.invalid/wrong" if magnet == "magnet:first" else "https://example.invalid/right"}]}
        with patch.object(app, "load_config", return_value={"rd_token": "test"}), \
             patch.object(app, "get_media_meta", return_value={"name": "A Monster Calls"}), \
             patch.object(app.StreamQuery, "from_params", return_value=query), \
             patch.object(app, "get_available_streams", return_value=sources), \
             patch.object(app, "rd_debrid_magnet", side_effect=resolve) as debrid, \
             patch.object(app.playback_support, "excluded", return_value=set()), \
             patch.object(app, "get_resume_position", return_value=0):
            handler = handler_for("/api/one-click-play", body)
            handler.do_POST()
        result = handler.send_json.call_args.args[0]
        self.assertEqual(result["stream_url"], "https://example.invalid/right")
        self.assertEqual(debrid.call_count, 2)

    def test_one_click_retries_when_single_file_names_other_episode(self):
        sources = [
            {"info_hash": "first", "magnet": "magnet:first", "title": "Example.S01E02", "score": 2000},
            {"info_hash": "second", "magnet": "magnet:second", "title": "Example.S01E02", "score": 1000},
        ]
        body = {"imdb_id": "tt0000001", "type": "series", "title": "Example — S01E02",
                "name": "Example", "season": 1, "episode": 2, "player_mode": "integrated"}
        query = SimpleNamespace(runtime_minutes=None, media_type="series", title="Example",
                                release_year="", season=1, episode=2)
        def resolve(_token, magnet, **_kwargs):
            episode = 3 if magnet == "magnet:first" else 2
            return {"ready": True, "files": [{"filename": f"Example.S01E0{episode}.mkv",
                    "download": f"https://example.invalid/episode-{episode}", "is_target_ep": True}]}
        with patch.object(app, "load_config", return_value={"rd_token": "test"}), \
             patch.object(app.StreamQuery, "from_params", return_value=query), \
             patch.object(app, "get_available_streams", return_value=sources), \
             patch.object(app, "rd_debrid_magnet", side_effect=resolve) as debrid, \
             patch.object(app.playback_support, "excluded", return_value=set()), \
             patch.object(app, "build_series_playlist_items", return_value=[]), \
             patch.object(app, "prefetch_next_episode"), \
             patch.object(app, "get_resume_position", return_value=0):
            handler = handler_for("/api/one-click-play", body)
            handler.do_POST()
        result = handler.send_json.call_args.args[0]
        self.assertEqual(result["stream_url"], "https://example.invalid/episode-2")
        self.assertEqual(debrid.call_count, 2)

    def test_tonight_route_uses_watchlist_metadata_and_excludes_watched_titles(self):
        watchlist = [{"id": "tt1", "name": "Vu"}, {"id": "tt2", "name": "À découvrir"}]
        handler = handler_for("/api/tonight", {"minutes": 100, "mood": "light"})
        with patch.object(app.kino_db, "db_get_watchlist", return_value=watchlist), \
             patch.object(app.kino_db, "db_get_history", return_value=[{"id": "tt1", "completed": True}]), \
             patch.object(app, "read_tonight_metadata", return_value={"runtime": "90 min", "genres": ["Comedy"]}), \
             patch.object(app, "fetch_tonight_metadata") as fetch:
            handler.do_POST()
        payload = handler.send_json.call_args.args[0]
        self.assertEqual([item["item"]["id"] for item in payload["choices"]], ["tt2"])
        self.assertIn("90 min", payload["choices"][0]["reasons"])
        fetch.assert_not_called()

    def test_one_click_excludes_failed_source_and_does_not_record_before_playback(self):
        sources = [
            {"info_hash": "first", "magnet": "magnet:first", "title": "First", "score": 2000},
            {"info_hash": "second", "magnet": "magnet:second", "title": "Second", "score": 1000},
        ]
        params = {"imdb_id": "tt1234567", "type": "movie", "title": "Example", "name": "Example", "player_mode": "integrated"}
        def resolve(_token, magnet, **_kwargs):
            return {"ready": True, "files": [{"filename": "movie.mkv", "download": f"https://example.invalid/{magnet[-5:]}"}]}
        with patch.object(app, "load_config", return_value={"rd_token": "test"}), \
             patch.object(app, "get_media_meta", return_value={}), \
             patch.object(app.StreamQuery, "from_params", side_effect=lambda *_args: SimpleNamespace(
                 runtime_minutes=None, media_type="movie", title="Example", release_year="")), \
             patch.object(app, "get_available_streams", return_value=sources), \
             patch.object(app, "rd_debrid_magnet", side_effect=resolve) as debrid, \
             patch.object(app, "get_resume_position", return_value=0), \
             patch.object(app, "record_history") as history:
            first = handler_for("/api/one-click-play", params)
            first.do_POST()
            first_result = first.send_json.call_args.args[0]
            second = handler_for("/api/one-click-play", {**params, "exclude_source_keys": first_result["attempted_source_keys"]})
            second.do_POST()
            second_result = second.send_json.call_args.args[0]

        self.assertEqual(first_result["source_key"], app.stream_candidate_key(sources[0]))
        self.assertEqual(second_result["source_key"], app.stream_candidate_key(sources[1]))
        self.assertEqual([call.args[1] for call in debrid.call_args_list], ["magnet:first", "magnet:second"])
        self.assertEqual(len(second_result["attempted_source_keys"]), 2)
        history.assert_not_called()

    def test_probe_route_selects_preferred_language(self):
        tracks = [{"index": 1, "language": "eng"}, {"index": 2, "language": "fra"}]
        info = {"video": [{"index": 0, "codec": "h264"}], "audio": tracks, "container": "mp4"}
        handler = handler_for("/api/probe-media", {"url": "https://example.invalid/movie"})
        with patch.object(app, "load_config", return_value={"pref_lang": "vf"}), \
             patch.object(app, "find_ffmpeg", return_value="ffmpeg"), \
             patch.object(app.media_probe, "inspect_media", return_value=info):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[0]["selected_audio"]["index"], 2)


if __name__ == "__main__":
    unittest.main()

class RemuxFailureTests(unittest.TestCase):
    def test_empty_ffmpeg_output_is_an_error_before_mp4_headers(self):
        import io
        handler = object.__new__(app.RequestHandler)
        handler.path = '/api/remux?url=https%3A%2F%2Fexample.invalid%2Fexpired.mp4'
        handler._request_allowed = Mock(return_value=True)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.send_json = Mock()
        handler.wfile = io.BytesIO()
        process = Mock(stdout=io.BytesIO(b''))
        with patch.object(app, 'find_ffmpeg', return_value='/bin/ffmpeg'), \
             patch.object(app.subprocess, 'Popen', return_value=process):
            handler.do_GET()
        handler.send_response.assert_not_called()
        self.assertEqual(handler.send_json.call_args.kwargs['status'], 502)
        self.assertEqual(handler.send_json.call_args.args[0]['code'], 'source_unavailable')

    def test_remux_stream_keeps_first_media_bytes(self):
        import io
        handler = object.__new__(app.RequestHandler)
        handler.path = '/api/remux?url=https%3A%2F%2Fexample.invalid%2Fmovie.mp4'
        handler._request_allowed = Mock(return_value=True)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.send_json = Mock()
        handler.wfile = io.BytesIO()
        process = Mock(stdout=io.BytesIO(b'abcdefghrest'))
        with patch.object(app, 'find_ffmpeg', return_value='/bin/ffmpeg'), \
             patch.object(app.subprocess, 'Popen', return_value=process):
            handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        handler.send_json.assert_not_called()
        self.assertEqual(handler.wfile.getvalue(), b'abcdefghrest')
