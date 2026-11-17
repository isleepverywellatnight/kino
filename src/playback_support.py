"""Content-scoped source feedback. No playable URLs or credentials are stored."""
import json
import re
import time
from contextlib import closing

import kino_db


def scope(context):
    if not isinstance(context, dict):
        raise ValueError('Contexte invalide.')
    identity = str(context.get('imdb_id') or context.get('id') or '')
    if not re.fullmatch(r'[\w:-]{1,160}', identity):
        raise ValueError('Identifiant du contenu manquant ou invalide.')
    series = context.get('type') in ('series', 'anime', 'tv')
    season, episode = None, None
    if series:
        season, episode = int(context.get('season', 1)), int(context.get('episode', 1))
        if season < 0 or episode < 1:
            raise ValueError('Épisode invalide.')
    return json.dumps([identity, 'series' if series else 'movie', season, episode])


def _connect():
    conn = kino_db.get_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS playback_source_feedback (
        scope TEXT NOT NULL, source_key TEXT NOT NULL, reason TEXT NOT NULL,
        expires_at REAL, PRIMARY KEY (scope, source_key))''')
    conn.commit()
    return conn


def report(context, source_key, reason):
    target = scope(context)
    if not isinstance(source_key, str) or not re.fullmatch(r'[0-9a-f]{32}', source_key):
        raise ValueError('Source non identifiée. Choisissez une source depuis la fiche.')
    if reason not in ('wrong_content', 'stalled', 'no_audio'):
        raise ValueError('Problème inconnu.')
    expires = None if reason == 'wrong_content' else time.time() + 3600
    with closing(_connect()) as conn, conn:
        conn.execute('''INSERT INTO playback_source_feedback VALUES (?, ?, ?, ?)
            ON CONFLICT(scope, source_key) DO UPDATE SET
            reason=CASE WHEN expires_at IS NULL THEN reason ELSE excluded.reason END,
            expires_at=CASE WHEN expires_at IS NULL THEN NULL ELSE excluded.expires_at END''',
            (target, source_key, reason, expires))


def excluded(context):
    target = scope(context)
    with closing(_connect()) as conn, conn:
        conn.execute('DELETE FROM playback_source_feedback WHERE expires_at <= ?', (time.time(),))
        return {row[0] for row in conn.execute(
            'SELECT source_key FROM playback_source_feedback WHERE scope=?', (target,))}


def clear(context):
    with closing(_connect()) as conn, conn:
        conn.execute('DELETE FROM playback_source_feedback WHERE scope=?', (scope(context),))


def is_auth_error(error):
    return getattr(error, 'code', None) in (401, 403) or getattr(error, 'status_code', None) in (401, 403)
