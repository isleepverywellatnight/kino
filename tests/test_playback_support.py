import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import playback_support as support


class SourceFeedbackTests(unittest.TestCase):
    def test_persistence_scope_expiration_and_undo(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(support.kino_db, 'DB_PATH', Path(folder) / 'test.db'):
            first = {'imdb_id': 'tt123', 'type': 'series', 'season': 1, 'episode': 2}
            other = {**first, 'episode': 3}
            key = 'a' * 32
            with patch.object(support.time, 'time', return_value=100):
                support.report(first, key, 'wrong_content')
                support.report(first, 'b' * 32, 'stalled')
            with patch.object(support.time, 'time', return_value=101):
                self.assertEqual(support.excluded(other), set())
                self.assertEqual(support.excluded(first), {key, 'b' * 32})
            with patch.object(support.time, 'time', return_value=100000):
                self.assertEqual(support.excluded(first), {key})
                support.clear(first)
                self.assertEqual(support.excluded(first), set())

    def test_invalid_identity_is_rejected(self):
        for context in ({}, {'imdb_id': 'tt1', 'type': 'series', 'episode': 0}):
            with self.assertRaises(ValueError):
                support.scope(context)

    def test_provider_auth_is_not_reported_as_a_media_error(self):
        from urllib.error import HTTPError
        self.assertTrue(support.is_auth_error(HTTPError('https://secret.invalid', 401, 'Unauthorized', {}, None)))
        self.assertFalse(support.is_auth_error(RuntimeError('timeout')))


if __name__ == '__main__':
    unittest.main()

class FeedbackRouteTests(unittest.TestCase):
    def test_route_persists_and_clears_only_requested_content(self):
        from test_playback_recovery import handler_for
        with tempfile.TemporaryDirectory() as folder, patch.object(support.kino_db, 'DB_PATH', Path(folder) / 'test.db'):
            context = {'imdb_id': 'tt789', 'type': 'movie'}
            handler = handler_for('/api/playback-feedback', {'context': context, 'source_key': 'c' * 32, 'reason': 'wrong_content'})
            handler.do_POST()
            self.assertEqual(support.excluded(context), {'c' * 32})
            handler = handler_for('/api/playback-feedback', {'context': context, 'action': 'clear'})
            handler.do_POST()
            self.assertEqual(support.excluded(context), set())

    def test_provider_rejection_stops_candidates_and_redacts_url(self):
        from test_playback_recovery import handler_for
        from urllib.error import HTTPError
        from types import SimpleNamespace
        import app
        sources = [{'info_hash': str(i), 'magnet': f'magnet:{i}'} for i in range(3)]
        with patch.object(app, 'load_config', return_value={'rd_token': 'fake'}), \
             patch.object(app, 'get_media_meta', return_value={}), \
             patch.object(app.StreamQuery, 'from_params', return_value=SimpleNamespace(runtime_minutes=None)), \
             patch.object(app, 'get_available_streams', return_value=sources), \
             patch.object(support, 'excluded', return_value={app.stream_candidate_key(sources[0])}), \
             patch.object(app, 'rd_debrid_magnet', side_effect=HTTPError('https://secret.invalid/token',401,'SECRET',{},None)) as resolve:
            handler = handler_for('/api/one-click-play', {'imdb_id':'tt789','type':'movie'})
            handler.do_POST()
        self.assertEqual(resolve.call_count,1)
        self.assertEqual(resolve.call_args.args[1],'magnet:1')
        payload = handler.send_json.call_args.args[0]
        self.assertEqual(payload['code'],'provider_auth')
        self.assertNotIn('SECRET',str(payload))
        self.assertEqual(handler.send_json.call_args.kwargs['status'],401)
