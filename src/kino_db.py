"""
KINO Database & Local Cache Engine (SQLite + WAL + FTS5)
Architecture haute performance pour KINO Desktop :
- Mode WAL (Write-Ahead Logging) pour lectures concurrentes sans verrouillage
- Cache persistant des métadonnées (TMDB, Cinemeta, traductions FR, trailers, sous-titres)
- Indexation et recherche instantanée FTS5 (Full-Text Search)
- Persistance optimisée de la Watchlist et de l'Historique de visionnage
- Zéro dépendance externe (utilise le module standard sqlite3 de Python)
"""

import json
import math
import platform
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

import ratings

DB_PATH = Path.home() / ".kino_data.db"
_DB_LOCK = threading.RLock()

# Marqueurs de suppression synchronisés via kino_sync.json, rangés dans app_config
# (aucune table ni colonne nouvelle : DATA_SCHEMA inchangé).
SYNC_DELETIONS_KEY = "sync_deletions"
_SYNC_KINDS = ("history", "watchlist")
_MAX_DELETIONS_PER_KIND = 2000

# Un film vu importé (Letterboxd) n'a pas de date de visionnage connue : il est
# daté avant le 1er janvier 2000, donc avant toute lecture réelle dans KINO,
# sur cet appareil comme sur l'autre.
_IMPORTED_WATCHED_BEFORE = 946684800.0


def get_connection():
    """Crée une connexion SQLite configurée pour des performances optimales."""
    conn = sqlite3.connect(str(DB_PATH), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -32000;")  # 32 Mo de cache RAM
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    return conn


def init_db():
    """Initialise les tables, index et recherche FTS5."""
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                # 1. Table de configuration clé-valeur
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS app_config (
                        key TEXT PRIMARY KEY,
                        value_json TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                # 2. Table de cache persistant avec expiration (TTL)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS persistent_cache (
                        cache_key TEXT PRIMARY KEY,
                        value_json TEXT,
                        expires_at REAL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON persistent_cache(expires_at);")

                # 3. Table de Watchlist (Ma Liste)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS watchlist (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        type TEXT,
                        year TEXT,
                        poster TEXT,
                        imdb_rating TEXT,
                        added_at REAL,
                        user_rating TEXT
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_added ON watchlist(added_at DESC);")
                try:
                    conn.execute("ALTER TABLE watchlist ADD COLUMN user_rating TEXT;")
                except sqlite3.OperationalError:
                    pass

                # 4. Table d'historique de visionnage (Reprendre la lecture)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS watch_history (
                        id TEXT PRIMARY KEY,
                        data_json TEXT,
                        last_watched REAL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_history_last_watched ON watch_history(last_watched DESC);")

                # Download state is independent from watchlist/history. Partial files
                # are never exposed as completed media.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS offline_downloads (
                        id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL,
                        status TEXT NOT NULL,
                        source_url TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        title TEXT NOT NULL,
                        media_json TEXT NOT NULL,
                        subtitles_json TEXT NOT NULL DEFAULT '{}',
                        poster_url TEXT NOT NULL DEFAULT '',
                        root_path TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        part_path TEXT NOT NULL,
                        poster_path TEXT NOT NULL,
                        bytes_done INTEGER NOT NULL DEFAULT 0,
                        bytes_total INTEGER NOT NULL DEFAULT 0,
                        etag TEXT NOT NULL DEFAULT '',
                        last_modified TEXT NOT NULL DEFAULT '',
                        accept_ranges INTEGER NOT NULL DEFAULT 0,
                        error_code TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_offline_downloads_fingerprint ON offline_downloads(fingerprint);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_offline_downloads_created ON offline_downloads(created_at DESC);")

                # Local Ce soir decisions and impression counts, independent of
                # watchlist and playback history.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tonight_feedback (
                        media_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL DEFAULT '',
                        media_type TEXT NOT NULL DEFAULT 'movie',
                        genres_json TEXT NOT NULL DEFAULT '[]',
                        decision TEXT NOT NULL DEFAULT '',
                        until_ts REAL NOT NULL DEFAULT 0,
                        shown_count INTEGER NOT NULL DEFAULT 0,
                        last_shown REAL NOT NULL DEFAULT 0,
                        last_action_id TEXT NOT NULL DEFAULT '',
                        undo_json TEXT NOT NULL DEFAULT '{}',
                        updated_at REAL NOT NULL DEFAULT 0
                    );
                """)

                # 5. Table d'index des médias locaux pour recherche instantanée
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS media_catalog (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        imdb_id TEXT UNIQUE,
                        title TEXT,
                        media_type TEXT,
                        year TEXT,
                        poster TEXT,
                        rating TEXT,
                        genres TEXT,
                        overview TEXT,
                        updated_at REAL
                    );
                """)

                # 6. Table virtuelle FTS5 pour recherche plein texte
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5(
                        title,
                        overview,
                        genres,
                        content='media_catalog',
                        content_rowid='id',
                        tokenize='unicode61 remove_diacritics 2'
                    );
                """)

                # Triggers pour maintenir l'index FTS5 synchronisé avec media_catalog
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_media_insert AFTER INSERT ON media_catalog BEGIN
                        INSERT INTO media_fts(rowid, title, overview, genres)
                        VALUES (new.id, new.title, new.overview, new.genres);
                    END;
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_media_update AFTER UPDATE ON media_catalog BEGIN
                        INSERT INTO media_fts(media_fts, rowid, title, overview, genres)
                        VALUES('delete', old.id, old.title, old.overview, old.genres);
                        INSERT INTO media_fts(rowid, title, overview, genres)
                        VALUES (new.id, new.title, new.overview, new.genres);
                    END;
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_media_delete AFTER DELETE ON media_catalog BEGIN
                        INSERT INTO media_fts(media_fts, rowid, title, overview, genres)
                        VALUES('delete', old.id, old.title, old.overview, old.genres);
                    END;
                """)

                # 7. Titres français (affichage et recherche « SOS Fantômes », « Les Évadés »)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS media_aliases (
                        imdb_id TEXT PRIMARY KEY,
                        alias TEXT NOT NULL,
                        name TEXT NOT NULL DEFAULT '',
                        year TEXT NOT NULL DEFAULT '',
                        media_type TEXT NOT NULL DEFAULT 'movie',
                        updated_at REAL
                    );
                """)
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS media_alias_fts USING fts5(
                        alias, content='media_aliases', content_rowid='rowid',
                        tokenize='unicode61 remove_diacritics 2'
                    );
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_alias_insert AFTER INSERT ON media_aliases BEGIN
                        INSERT INTO media_alias_fts(rowid, alias) VALUES (new.rowid, new.alias);
                    END;
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_alias_update AFTER UPDATE ON media_aliases BEGIN
                        INSERT INTO media_alias_fts(media_alias_fts, rowid, alias) VALUES('delete', old.rowid, old.alias);
                        INSERT INTO media_alias_fts(rowid, alias) VALUES (new.rowid, new.alias);
                    END;
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_alias_delete AFTER DELETE ON media_aliases BEGIN
                        INSERT INTO media_alias_fts(media_alias_fts, rowid, alias) VALUES('delete', old.rowid, old.alias);
                    END;
                """)
        finally:
            conn.close()


def _year_of(text):
    found = re.findall(r"\b(1[89]\d\d|20\d\d)\b", str(text or ""))
    return int(found[0]) if found else None


def _same_work_years(a, b):
    """Two listings of one work: same year (±1), or a year unknown."""
    a, b = _year_of(a), _year_of(b)
    return a is None or b is None or abs(a - b) <= 1


def _alias_holder(conn, alias, media_type, imdb_id, year):
    """Another work of the same kind and year already called `alias`, or None.

    A French title belongs to one work: « Les Affranchis » is GoodFellas (1990),
    not the homonym listings « Goodfellas » or « Good Fellas » without a poster.
    Remakes keep their own: « Psychose » 1960 and 1998 are years apart."""
    rows = conn.execute("""
        SELECT a.imdb_id, COALESCE(NULLIF(c.year, ''), a.year) FROM media_aliases a
        LEFT JOIN media_catalog c ON c.imdb_id = a.imdb_id
        WHERE lower(a.alias) = lower(?) AND a.media_type = ? AND a.imdb_id != ?
    """, (alias, media_type, imdb_id)).fetchall()
    for other_id, other_year in rows:
        if _same_work_years(year, other_year):
            return other_id
    return None


def db_set_aliases(rows):
    """Remember French titles: rows of {id, alias, name, year, type}.

    Returns the ids left without their French title because another listing of
    the same work holds it (see _alias_holder)."""
    rows = [r for r in rows or [] if r.get("id") and r.get("alias")]
    refused = set()
    if not rows:
        return refused
    now = time.time()
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                for r in rows:
                    kind = "series" if r.get("type") in ("series", "anime") else "movie"
                    catalogue_year = conn.execute("SELECT year FROM media_catalog WHERE imdb_id = ?", (str(r["id"]),)).fetchone()
                    year = r.get("year") or (catalogue_year[0] if catalogue_year else "")
                    if _alias_holder(conn, str(r["alias"]), kind, str(r["id"]), year):
                        refused.add(str(r["id"]))
                        continue
                    conn.execute("""
                        INSERT INTO media_aliases (imdb_id, alias, name, year, media_type, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(imdb_id) DO UPDATE SET alias = excluded.alias,
                            name = COALESCE(NULLIF(excluded.name, ''), media_aliases.name),
                            year = COALESCE(NULLIF(excluded.year, ''), media_aliases.year),
                            media_type = excluded.media_type, updated_at = excluded.updated_at
                        WHERE media_aliases.alias != excluded.alias OR media_aliases.name = ''
                    """, (str(r["id"]), str(r["alias"])[:300], str(r.get("name") or "")[:300], str(r.get("year") or "")[:12],
                          kind, now))
        finally:
            conn.close()
    return refused


def db_drop_shared_aliases():
    """Takes back French titles given to several listings of one work.

    Before 1.1.103 a listing without a year took the French title of the famous
    film of the same name (« Les Affranchis » on two homonyms of GoodFellas).
    The rated listing keeps it, else the oldest; the others lose it, and their
    remembered French title too. Returns how many were taken back."""
    dropped = []
    with _DB_LOCK:
        conn = get_connection()
        try:
            groups = conn.execute("""
                SELECT lower(alias), media_type FROM media_aliases GROUP BY lower(alias), media_type HAVING COUNT(*) > 1
            """).fetchall()
            for alias, media_type in groups:
                holders = conn.execute("""
                    SELECT a.imdb_id, COALESCE(NULLIF(c.year, ''), a.year), COALESCE(c.rating, '') FROM media_aliases a
                    LEFT JOIN media_catalog c ON c.imdb_id = a.imdb_id
                    WHERE lower(a.alias) = ? AND a.media_type = ?
                    ORDER BY (COALESCE(c.rating, '') = ''), a.updated_at, a.imdb_id
                """, (alias, media_type)).fetchall()
                kept = []
                for imdb_id, year, _rating in holders:
                    if any(_same_work_years(year, kept_year) for kept_year in kept):
                        dropped.append(imdb_id)
                    else:
                        kept.append(year)
            if dropped:
                with conn:
                    for imdb_id in dropped:
                        conn.execute("DELETE FROM media_aliases WHERE imdb_id = ?", (imdb_id,))
                        conn.execute("DELETE FROM persistent_cache WHERE cache_key = ?", (f"title_fr_v1:{imdb_id}",))
        except sqlite3.Error:
            return 0
        finally:
            conn.close()
    return len(dropped)


# =========================================================================
# GESTION DE LA CONFIGURATION CLÉ-VALEUR
# =========================================================================

def get_config(key, default=None):
    """Récupère une valeur de configuration depuis SQLite."""
    with _DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT value_json FROM app_config WHERE key = ?", (key,)).fetchone()
            if row and row["value_json"]:
                return json.loads(row["value_json"])
            return default
        except Exception:
            return default
        finally:
            conn.close()


def save_config(key, value):
    """Enregistre ou met à jour une valeur de configuration dans SQLite."""
    with _DB_LOCK:
        conn = get_connection()
        try:
            val_json = json.dumps(value, ensure_ascii=False)
            with conn:
                conn.execute(
                    "INSERT INTO app_config (key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = CURRENT_TIMESTAMP",
                    (key, val_json),
                )
            return True
        except Exception:
            return False
        finally:
            conn.close()


def delete_config(key):
    """Supprime une clé de configuration dans SQLite."""
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM app_config WHERE key = ?", (key,))
            return True
        except Exception:
            return False
        finally:
            conn.close()


# =========================================================================
# MARQUEURS DE SUPPRESSION (HISTORIQUE ET WATCHLIST)
# =========================================================================
# Une suppression locale est mémorisée sous la forme {id: deleted_at}. Elle est
# exportée dans kino_sync.json et l'emporte sur toute version plus ancienne d'un
# élément ; un ajout ou un visionnage postérieur l'emporte sur elle.

def _as_stamp(value):
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return 0.0
    return stamp if math.isfinite(stamp) and stamp > 0 else 0.0


def _clean_deletions(raw):
    deletions = {kind: {} for kind in _SYNC_KINDS}
    if not isinstance(raw, dict):
        return deletions
    for kind in _SYNC_KINDS:
        entries = raw.get(kind)
        if not isinstance(entries, dict):
            continue
        for item_id, stamp in entries.items():
            stamp = _as_stamp(stamp)
            if item_id and stamp:
                deletions[kind][str(item_id)] = stamp
    return deletions


def _history_version(last_watched, data):
    """When this history entry was last written by the user: a play, or an
    explicit re-import of a film removed earlier (« reimported_at »). Imported
    films are dated before every real play, so the date alone would lose to the
    old removal."""
    reimported = _as_stamp(data.get("reimported_at")) if isinstance(data, dict) else 0.0
    return max(_as_stamp(last_watched), reimported)


def _merge_deletions(target, other):
    for kind in _SYNC_KINDS:
        for item_id, stamp in other[kind].items():
            if stamp > target[kind].get(item_id, 0.0):
                target[kind][item_id] = stamp
    return target


def _load_deletions(conn):
    row = conn.execute("SELECT value_json FROM app_config WHERE key = ?", (SYNC_DELETIONS_KEY,)).fetchone()
    try:
        return _clean_deletions(json.loads(row["value_json"]) if row and row["value_json"] else {})
    except Exception:
        return _clean_deletions({})


def _save_deletions(conn, deletions):
    kept = {
        kind: dict(sorted(deletions[kind].items(), key=lambda kv: kv[1], reverse=True)[:_MAX_DELETIONS_PER_KIND])
        for kind in _SYNC_KINDS
    }
    conn.execute(
        "INSERT INTO app_config (key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = CURRENT_TIMESTAMP",
        (SYNC_DELETIONS_KEY, json.dumps(kept)),
    )
    return kept


def _mark_deleted(conn, kind, stamps_by_id):
    """Mémorise des suppressions ; stamps_by_id donne l'horodatage de la version supprimée."""
    if not stamps_by_id:
        return
    deletions = _load_deletions(conn)
    now = time.time()
    for item_id, stamp in stamps_by_id.items():
        # La suppression doit dépasser la version retirée, même datée par une
        # horloge en avance sur l'autre appareil.
        deleted_at = max(now, _as_stamp(stamp) + 0.001)
        deletions[kind][str(item_id)] = max(deletions[kind].get(str(item_id), 0.0), deleted_at)
    _save_deletions(conn, deletions)


def _forget_deletion(conn, kind, item_id):
    """Un ajout local explicite remplace une suppression antérieure."""
    deletions = _load_deletions(conn)
    if deletions[kind].pop(str(item_id), None) is not None:
        _save_deletions(conn, deletions)


# =========================================================================
# GESTION DU CACHE PERSISTANT (TTL)
# =========================================================================

def db_cache_get(key):
    """Récupère une valeur en cache SQLite si non expirée."""
    now = time.time()
    with _DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT value_json, expires_at FROM persistent_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] and row["expires_at"] < now:
                conn.execute("DELETE FROM persistent_cache WHERE cache_key = ?", (key,))
                conn.commit()
                return None
            return json.loads(row["value_json"])
        except Exception:
            return None
        finally:
            conn.close()


_CACHE_PURGE_INTERVAL_SEC = 6 * 3600
_last_cache_purge = 0.0


def db_cache_purge_expired(now=None, *, compact=False):
    """Delete expired cache rows: they were only removed when their exact key was read
    again, so every past torrent search stayed in the database (17 MB of 18 MB observed)."""
    global _last_cache_purge
    now = time.time() if now is None else now
    with _DB_LOCK:
        _last_cache_purge = now
        conn = get_connection()
        try:
            with conn:
                removed = conn.execute(
                    "DELETE FROM persistent_cache WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
                ).rowcount
            if compact and removed:
                pages = conn.execute("PRAGMA page_count").fetchone()[0]
                free = conn.execute("PRAGMA freelist_count").fetchone()[0]
                if pages and free / pages > 0.25:
                    conn.execute("VACUUM")
            return removed
        except sqlite3.Error:
            return 0
        finally:
            conn.close()


def db_cache_set(key, val, ttl_sec=86400):
    """Enregistre une valeur dans le cache SQLite avec une durée de vie (TTL)."""
    now = time.time()
    if now - _last_cache_purge > _CACHE_PURGE_INTERVAL_SEC:
        db_cache_purge_expired(now)
    expires_at = (now + ttl_sec) if ttl_sec else None
    val_json = json.dumps(val, ensure_ascii=False)
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO persistent_cache (cache_key, value_json, expires_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        value_json = excluded.value_json,
                        expires_at = excluded.expires_at;
                    """,
                    (key, val_json, expires_at),
                )
        except Exception:
            pass
        finally:
            conn.close()


def db_cache_cleanup():
    """Supprime les entrées de cache expirées."""
    now = time.time()
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM persistent_cache WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
        except Exception:
            pass
        finally:
            conn.close()


# =========================================================================
# GESTION DE LA WATCHLIST (MA LISTE)
# =========================================================================

def db_get_watchlist():
    """Renvoie la liste des éléments de la Watchlist triés par date d'ajout."""
    with _DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, type, year, poster, imdb_rating, user_rating FROM watchlist ORDER BY added_at DESC LIMIT 500"
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "name": r["name"] or "",
                    "type": r["type"] or "movie",
                    "year": r["year"] or "",
                    "poster": r["poster"] or "",
                    "imdbRating": r["imdb_rating"] or "",
                    "user_rating": (r["user_rating"] if "user_rating" in r.keys() else "") or "",
                }
                for r in rows
            ]
        except Exception:
            return []
        finally:
            conn.close()


def db_toggle_watchlist(item):
    """Ajoute ou retire un élément de la Watchlist."""
    item_id = item.get("id")
    if not item_id:
        return db_get_watchlist()
    now = time.time()
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                exists = conn.execute("SELECT added_at FROM watchlist WHERE id = ?", (item_id,)).fetchone()
                if exists:
                    conn.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))
                    _mark_deleted(conn, "watchlist", {item_id: exists["added_at"]})
                else:
                    _forget_deletion(conn, "watchlist", item_id)
                    conn.execute(
                        """
                        INSERT INTO watchlist (id, name, type, year, poster, imdb_rating, added_at, user_rating)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            item.get("name") or "",
                            item.get("type") or "movie",
                            str(item.get("year") or ""),
                            item.get("poster") or "",
                            str(item.get("imdbRating") or ""),
                            now,
                            str(item.get("user_rating") or ""),
                        ),
                    )
        finally:
            conn.close()
    return db_get_watchlist()


# =========================================================================
# GESTION DE L'HISTORIQUE DE VISIONNAGE
# =========================================================================

def normalize_episode_code(value):
    match = re.fullmatch(r"s(\d{1,2})e(\d{1,3})", str(value or ""), re.IGNORECASE)
    return f"S{int(match[1]):02d}E{int(match[2]):02d}" if match else str(value or "")

def db_get_history():
    """Renvoie l'historique complet de visionnage trié par dernier vu."""
    with _DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT data_json FROM watch_history ORDER BY last_watched DESC LIMIT 300"
            ).fetchall()
            res = []
            for r in rows:
                try:
                    res.append(json.loads(r["data_json"]))
                except Exception:
                    pass
            return res
        except Exception:
            return []
        finally:
            conn.close()


def db_record_history(entry):
    """Met à jour ou insère une entrée dans l'historique de lecture."""
    item_id = entry.get("id") or entry.get("filename")
    if not item_id:
        return db_get_history()

    now = time.time()
    with _DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT data_json FROM watch_history WHERE id = ?", (item_id,)).fetchone()
            existing = {}
            if row and row["data_json"]:
                try:
                    existing = json.loads(row["data_json"])
                except Exception:
                    pass

            merged = dict(existing)
            merged.pop("imported_watched", None)
            for k, v in entry.items():
                if v is not None and v != "":
                    merged[k] = v

            watched_eps = list(dict.fromkeys(normalize_episode_code(code) for code in (merged.get("watched_episodes") or [])))
            ep_positions = {normalize_episode_code(code): pos for code, pos in (merged.get("ep_positions") or {}).items()}

            pos = entry.get("position")
            dur = entry.get("duration")
            s_num = merged.get("season")
            e_num = merged.get("episode")
            ep_key = normalize_episode_code(f"s{s_num}e{e_num}") if (s_num is not None and e_num is not None) else None

            if pos is not None and dur is not None and dur > 0:
                is_done = (pos / dur) >= 0.88 or (dur - pos) <= 180
                merged["completed"] = is_done
                if ep_key:
                    if pos <= 0 and not is_done:
                        ep_positions.pop(ep_key, None)
                    else:
                        ep_positions[ep_key] = {"position": pos, "duration": dur, "completed": is_done}
                    if is_done and ep_key not in watched_eps:
                        watched_eps.append(ep_key)
                    elif not is_done and ep_key in watched_eps:
                        watched_eps = [x for x in watched_eps if x != ep_key]

            merged["watched_episodes"] = watched_eps
            merged["ep_positions"] = ep_positions
            merged["last_watched"] = now
            merged["id"] = item_id

            data_str = json.dumps(merged, ensure_ascii=False)
            with conn:
                _forget_deletion(conn, "history", item_id)
                conn.execute(
                    """
                    INSERT INTO watch_history (id, data_json, last_watched)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        data_json = excluded.data_json,
                        last_watched = excluded.last_watched;
                    """,
                    (item_id, data_str, now),
                )
        finally:
            conn.close()
    return db_get_history()


def db_patch_history(item_id, fields):
    """Update fields of an existing history entry without moving it in « Reprendre »."""
    if not item_id or not isinstance(fields, dict):
        return False
    with _DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT data_json FROM watch_history WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return False
            try:
                data = json.loads(row["data_json"] or "{}")
            except ValueError:
                return False
            data.update(fields)
            with conn:
                conn.execute("UPDATE watch_history SET data_json = ? WHERE id = ?",
                             (json.dumps(data, ensure_ascii=False), item_id))
            return True
        finally:
            conn.close()


def db_remove_history(item_id):
    """Supprime un élément de l'historique, y compris lors des prochaines synchronisations."""
    if not item_id:
        return db_get_history()
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                row = conn.execute("SELECT last_watched FROM watch_history WHERE id = ?", (item_id,)).fetchone()
                conn.execute("DELETE FROM watch_history WHERE id = ?", (item_id,))
                _mark_deleted(conn, "history", {item_id: row["last_watched"] if row else 0})
        finally:
            conn.close()
    return db_get_history()


def db_clear_history():
    """Vide l'historique local et mémorise chaque suppression pour la synchronisation."""
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                rows = conn.execute("SELECT id, last_watched FROM watch_history").fetchall()
                conn.execute("DELETE FROM watch_history")
                _mark_deleted(conn, "history", {r["id"]: r["last_watched"] for r in rows})
        finally:
            conn.close()
    return []


def db_drop_wrong_imports(item_ids):
    """Removes films a Letterboxd import added by mistake (wrong match), only
    while they are still as imported: never played in KINO. The removal is
    marked so Google Drive takes it to the other device. Returns the ids removed."""
    ids = [str(i) for i in item_ids or () if i]
    if not ids:
        return []
    dropped = {}
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                for item_id in ids:
                    row = conn.execute("SELECT data_json, last_watched FROM watch_history WHERE id = ?", (item_id,)).fetchone()
                    if not row:
                        continue
                    try:
                        data = json.loads(row["data_json"] or "{}")
                    except Exception:
                        continue
                    if not data.get("imported_watched"):
                        continue
                    conn.execute("DELETE FROM watch_history WHERE id = ?", (item_id,))
                    dropped[item_id] = row["last_watched"]
                _mark_deleted(conn, "history", dropped)
        finally:
            conn.close()
    return list(dropped)


def db_import_user_lists(watchlist_items=(), history_entries=(), restore_removed=False):
    """Ajoute à Ma Liste et à l'historique les éléments importés encore absents.

    Un élément déjà présent n'est pas modifié (date, notes, progression). Un
    élément retiré dans KINO (marqueur de suppression) n'est pas réimporté,
    sauf si restore_removed : l'utilisateur l'a demandé, le marqueur disparaît
    et l'entrée garde « reimported_at » pour que la synchronisation Drive ne la
    retire pas à nouveau. Les éléments sautés sont renvoyés dans « removed ».
    Les ajouts à Ma Liste passent en tête, dans l'ordre reçu. Les films vus
    importés sont datés avant l'historique existant et avant toute lecture
    réelle : ils ne passent pas devant une vraie lecture et ne l'emportent pas
    sur elle lors de la synchronisation Drive, même venue de l'autre appareil.
    Renvoie les identifiants ajoutés, par type.
    """
    added = {"watchlist": [], "history": []}
    removed = {"watchlist": [], "history": []}
    now = time.time()
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                deletions = _load_deletions(conn)
                restored = False

                present = {r["id"] for r in conn.execute("SELECT id FROM watchlist")}
                for item in _dict_items(list(watchlist_items)):
                    item_id = str(item.get("id") or "")
                    if not item_id or item_id in present:
                        continue
                    if item_id in deletions["watchlist"]:
                        if not restore_removed:
                            removed["watchlist"].append(item_id)
                            continue
                        del deletions["watchlist"][item_id]
                        restored = True
                    present.add(item_id)
                    conn.execute(
                        """
                        INSERT INTO watchlist (id, name, type, year, poster, imdb_rating, added_at, user_rating)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            item.get("name") or "",
                            item.get("type") or "movie",
                            str(item.get("year") or ""),
                            item.get("poster") or "",
                            str(item.get("imdbRating") or ""),
                            now - 0.001 * len(added["watchlist"]),
                            str(item.get("user_rating") or ""),
                        ),
                    )
                    added["watchlist"].append(item_id)

                rows = conn.execute("SELECT id, last_watched FROM watch_history").fetchall()
                present = {r["id"] for r in rows}
                oldest = min([_IMPORTED_WATCHED_BEFORE, *(s for s in (_as_stamp(r["last_watched"]) for r in rows) if s)])
                for entry in _dict_items(list(history_entries)):
                    item_id = str(entry.get("id") or "")
                    if not item_id or item_id in present:
                        continue
                    reimported = {}
                    if item_id in deletions["history"]:
                        if not restore_removed:
                            removed["history"].append(item_id)
                            continue
                        del deletions["history"][item_id]
                        restored = True
                        reimported = {"reimported_at": now}
                    present.add(item_id)
                    stamp = max(oldest - 1 - 0.001 * len(added["history"]), 1.0)
                    data = dict(entry, id=item_id, last_watched=stamp, **reimported)
                    conn.execute(
                        "INSERT INTO watch_history (id, data_json, last_watched) VALUES (?, ?, ?)",
                        (item_id, json.dumps(data, ensure_ascii=False), stamp),
                    )
                    added["history"].append(item_id)
                if restored:
                    _save_deletions(conn, deletions)
        finally:
            conn.close()
    return {**added, "removed": removed}


# =========================================================================
# RECHERCHE LOCALE RAPIDE (FTS5) & INDEX CATALOGUE
# =========================================================================

def db_index_media(items):
    """Indexe un lot de médias dans le catalogue et dans FTS5."""
    if not items:
        return
    now = time.time()
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                for it in items:
                    imdb_id = it.get("imdb_id") or it.get("id")
                    if not imdb_id:
                        continue
                    title = it.get("title") or it.get("name") or ""
                    mtype = it.get("media_type") or it.get("type") or "movie"
                    year = str(it.get("year") or "")
                    poster = it.get("poster") or ""
                    rating = str(it.get("rating") or it.get("imdbRating") or "")
                    genres = it.get("genres") or ""
                    if isinstance(genres, list):
                        genres = ", ".join(str(g) for g in genres)
                    overview = it.get("overview") or it.get("description") or ""

                    conn.execute(
                        """
                        INSERT INTO media_catalog (imdb_id, title, media_type, year, poster, rating, genres, overview, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(imdb_id) DO UPDATE SET
                            title = excluded.title,
                            media_type = excluded.media_type,
                            year = excluded.year,
                            poster = COALESCE(NULLIF(excluded.poster, ''), media_catalog.poster),
                            rating = excluded.rating,
                            genres = excluded.genres,
                            overview = COALESCE(NULLIF(excluded.overview, ''), media_catalog.overview),
                            updated_at = excluded.updated_at;
                        """,
                        (imdb_id, title, mtype, year, poster, rating, genres, overview, now),
                    )
        except Exception:
            pass
        finally:
            conn.close()


def _folded(text):
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^0-9a-z]+", "", folded)


def _rank_suggestions(query, items, limit):
    """Rated works first, the exact title then the titles that start so; a
    listing that repeats a better one (same name, kind and year, no rating) is left out."""
    wanted = _folded(query)

    def names(item):
        return {n for n in (_folded(item.get("name")), _folded(item.get("name_fr"))) if n}

    def key(item):
        own = names(item)
        return (not str(item.get("imdbRating") or "").strip(), wanted not in own,
                not any(n.startswith(wanted) for n in own), not str(item.get("year") or "").strip())

    kept = []
    for item in sorted(items, key=key):
        rated = bool(str(item.get("imdbRating") or "").strip())
        if not rated and any(names(item) & names(better) and item.get("type") == better.get("type")
                             and _same_work_years(item.get("year"), better.get("year")) for better in kept):
            continue
        kept.append(item)
    return kept[:limit]


def db_search_fast(query, media_type=None, limit=25):
    """Recherche instantanée via FTS5 dans le catalogue local."""
    if not query or not str(query).strip():
        return []
    # Punctuation separates words, as in the FTS index: "Spider-Man" is "spider" + "man",
    # "L'Homme" is "l" + "homme". Deleting it produced "SpiderMan", which matched nothing.
    clean_q = "".join(c if c.isalnum() else " " for c in str(query).strip())
    tokens = clean_q.split()
    if not tokens:
        return []
    fts_expr = " AND ".join(f'"{t}"*' for t in tokens)
    wanted, limit = limit, max(limit * 3, 24)  # more to rank, then the best `wanted`

    with _DB_LOCK:
        conn = get_connection()
        try:
            if media_type and media_type in ("movie", "series"):
                rows = conn.execute(
                    """
                    SELECT c.imdb_id as id, c.title as name, c.media_type as type, c.year, c.poster, c.rating as imdbRating, c.overview
                    FROM media_catalog c
                    JOIN media_fts s ON c.id = s.rowid
                    WHERE media_fts MATCH ? AND c.media_type = ?
                    ORDER BY rank
                    LIMIT ?;
                    """,
                    (fts_expr, media_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT c.imdb_id as id, c.title as name, c.media_type as type, c.year, c.poster, c.rating as imdbRating, c.overview
                    FROM media_catalog c
                    JOIN media_fts s ON c.id = s.rowid
                    WHERE media_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?;
                    """,
                    (fts_expr, limit),
                ).fetchall()
            if not rows:
                rows = _search_compact_titles(conn, tokens, media_type, limit)
            # French titles first: someone typing « Les Évadés » means the French title.
            results, seen = [], set()
            for row in [*_search_aliases(conn, fts_expr, media_type, limit), *rows]:
                item = dict(row)
                if item.get("id") and item["id"] not in seen:
                    seen.add(item["id"])
                    results.append(item)
            return _rank_suggestions(query, results, wanted)
        except Exception:
            return []
        finally:
            conn.close()


_COMPACT_TITLE_SQL = "lower(c.title)"
for _separator in (" ", "-", "'", "’", ":", ".", "·", ","):
    _COMPACT_TITLE_SQL = f"replace({_COMPACT_TITLE_SQL}, '{_separator.replace(chr(39), chr(39) * 2)}', '')"


def _search_aliases(conn, fts_expr, media_type, limit):
    sql = """
        SELECT a.imdb_id as id, COALESCE(NULLIF(c.title, ''), a.name) as name,
               COALESCE(c.media_type, a.media_type) as type, COALESCE(NULLIF(c.year, ''), a.year) as year,
               COALESCE(NULLIF(c.poster, ''), 'https://images.metahub.space/poster/medium/' || a.imdb_id || '/img') as poster,
               c.rating as imdbRating, c.overview, a.alias as name_fr
        FROM media_aliases a
        JOIN media_alias_fts f ON a.rowid = f.rowid
        LEFT JOIN media_catalog c ON c.imdb_id = a.imdb_id
        WHERE media_alias_fts MATCH ?
    """
    params = [fts_expr]
    if media_type in ("movie", "series"):
        sql += " AND COALESCE(c.media_type, a.media_type) = ?"
        params.append(media_type)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _search_compact_titles(conn, tokens, media_type, limit):
    """Fallback for titles typed without their separators: "spiderman", "walle"."""
    compact = "".join(tokens).lower()
    if len(compact) < 4:
        return []
    sql = f"""
        SELECT c.imdb_id as id, c.title as name, c.media_type as type, c.year, c.poster, c.rating as imdbRating, c.overview
        FROM media_catalog c
        WHERE {_COMPACT_TITLE_SQL} LIKE ? ESCAPE '\\'
    """
    params = ["%" + compact.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"]
    if media_type in ("movie", "series"):
        sql += " AND c.media_type = ?"
        params.append(media_type)
    sql += " ORDER BY length(c.title) LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def db_get_media_type(imdb_id):
    """Retourne le type (movie ou series) d'un titre depuis le catalogue local."""
    if not imdb_id:
        return None
    with _DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT media_type FROM media_catalog WHERE imdb_id = ? LIMIT 1", (imdb_id,)).fetchone()
            if row and row["media_type"]:
                return str(row["media_type"]).strip().lower()
        except Exception:
            pass
        finally:
            conn.close()
    return None


def db_get_similar(imdb_id, media_type="movie", genres=None, limit=8):
    """Recherche des suggestions similaires ultra-rapides (< 5ms) depuis le catalogue local."""
    if not imdb_id:
        return []
    target_type = "series" if media_type == "series" else "movie"
    with _DB_LOCK:
        conn = get_connection()
        try:
            where_clauses = ["imdb_id != ?", "length(poster) > 0", "length(rating) > 0"]
            params = [imdb_id]
            if target_type:
                where_clauses.append("media_type = ?")
                params.append(target_type)

            if genres and isinstance(genres, list):
                valid_g = [str(g).strip() for g in genres if g and len(str(g).strip()) >= 3]
                if valid_g:
                    g_conds = ["genres LIKE ?" for _ in valid_g[:3]]
                    for gt in valid_g[:3]:
                        params.append(f"%{gt}%")
                    where_clauses.append(f"({' OR '.join(g_conds)})")

            query_sql = f"""
                SELECT imdb_id as id, title as name, media_type as type, year, poster, rating as imdbRating
                FROM media_catalog
                WHERE {' AND '.join(where_clauses)}
                ORDER BY CAST(rating AS FLOAT) DESC
                LIMIT ?;
            """
            params.append(limit)
            rows = conn.execute(query_sql, params).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()


def db_media_genres(imdb_ids):
    """{imdb_id: [genres]} for the titles KINO's catalogue already knows."""
    ids = [str(i) for i in dict.fromkeys(imdb_ids or []) if i][:500]
    if not ids:
        return {}
    with _DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                f"SELECT imdb_id, genres FROM media_catalog WHERE imdb_id IN ({','.join('?' * len(ids))})", ids
            ).fetchall()
        except Exception:
            return {}
        finally:
            conn.close()
    found = {}
    for imdb_id, genres in rows:
        names = [name.strip() for name in str(genres or "").split(",") if name.strip()]
        if names:
            found[imdb_id] = names
    return found


def db_seed_classics(classics_list):
    """Initialise le catalogue FTS5 local avec le panthéon des classiques."""
    if not classics_list:
        return
    items = []
    for item in classics_list:
        if isinstance(item, (list, tuple)) and len(item) >= 4:
            imdb_id = item[0]
            title = item[1]
            year = item[2]
            rating = item[3]
            genres = item[4] if len(item) > 4 else []
            overview = item[5] if len(item) > 5 else ""
            items.append({
                "imdb_id": imdb_id,
                "title": title,
                "media_type": "movie",
                "year": year,
                "rating": rating,
                "genres": genres,
                "overview": overview,
                "poster": f"https://images.metahub.space/poster/medium/{imdb_id}/img",
            })
        elif isinstance(item, dict):
            items.append(item)
    if items:
        db_index_media(items)


# =========================================================================
# MIGRATION AUTOMATIQUE DEPUIS L'ANCIEN JSON
# =========================================================================

def migrate_from_json(json_config_path):
    """Migre en douceur la watchlist et l'historique de l'ancien fichier JSON vers SQLite."""
    p = Path(json_config_path)
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return

    if not isinstance(data, dict):
        return
    # Ce fichier est relu à chaque lancement : un élément supprimé depuis ne
    # doit pas être réimporté.
    with _DB_LOCK:
        conn = get_connection()
        try:
            deletions = _load_deletions(conn)
        except Exception:
            deletions = _clean_deletions({})
        finally:
            conn.close()

    # Migration Watchlist
    wl = data.get("watchlist")
    if isinstance(wl, list) and wl:
        now = time.time()
        with _DB_LOCK:
            conn = get_connection()
            try:
                with conn:
                    for it in wl:
                        if isinstance(it, dict) and it.get("id") and str(it["id"]) not in deletions["watchlist"]:
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO watchlist (id, name, type, year, poster, imdb_rating, added_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    it["id"],
                                    it.get("name") or "",
                                    it.get("type") or "movie",
                                    str(it.get("year") or ""),
                                    it.get("poster") or "",
                                    str(it.get("imdbRating") or ""),
                                    now,
                                ),
                            )
            except Exception:
                pass
            finally:
                conn.close()

    # Migration Historique
    hist = data.get("history")
    if isinstance(hist, list) and hist:
        now = time.time()
        with _DB_LOCK:
            conn = get_connection()
            try:
                with conn:
                    for entry in hist:
                        if isinstance(entry, dict):
                            item_id = entry.get("id") or entry.get("filename")
                            if item_id and str(item_id) not in deletions["history"]:
                                conn.execute(
                                    """
                                    INSERT OR IGNORE INTO watch_history (id, data_json, last_watched)
                                    VALUES (?, ?, ?)
                                    """,
                                    (item_id, json.dumps(entry, ensure_ascii=False), now),
                                )
            except Exception:
                pass
            finally:
                conn.close()


# =========================================================================
# SYNCHRONISATION GOOGLE DRIVE TRANSPARENTE (MAC <-> WINDOWS)
# =========================================================================

def find_gdrive_sync_dir():
    """Détecte automatiquement le dossier Google Drive KINO sur Mac et Windows."""
    # 1. Dossier courant (si KINO est exécuté depuis Google Drive)
    import paths
    app_dir = paths.PROJECT_DIR
    app_dir_str = str(app_dir).lower()
    if any(k in app_dir_str for k in ("googledrive", "google drive", "mon drive", "my drive")):
        return app_dir

    # 2. Détection macOS (CloudStorage)
    cloud_storage = Path.home() / "Library" / "CloudStorage"
    if cloud_storage.is_dir():
        try:
            for p in cloud_storage.iterdir():
                if p.name.startswith("GoogleDrive-") and p.is_dir():
                    for sub in ("Mon Drive", "My Drive"):
                        cand = p / sub / "KINO"
                        if cand.is_dir():
                            return cand
        except Exception:
            pass

    # 3. Détection Windows (Lecteur virtuel G: ou dossier utilisateur)
    for win_drive in ("G:", "H:", "F:"):
        for sub in ("Mon Drive", "My Drive"):
            try:
                cand = Path(f"{win_drive}\\{sub}\\KINO")
                if cand.is_dir():
                    return cand
            except Exception:
                pass

    for sub in ("Google Drive\\Mon Drive\\KINO", "Google Drive\\My Drive\\KINO", "Google Drive\\KINO"):
        try:
            cand = Path.home() / sub
            if cand.is_dir():
                return cand
        except Exception:
            pass

    return None


def _dict_items(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def db_sync_gdrive():
    """
    Synchronise intelligemment l'état KINO (progression, historique, watchlist, config)
    avec Google Drive pour un partage transparent entre Mac et Windows.

    Les suppressions sont transmises par des marqueurs horodatés (« deleted ») :
    un élément supprimé n'est pas réinséré par une version plus ancienne venue
    de l'autre appareil, mais revient s'il y a été revu ou rajouté depuis.
    """
    gdrive_dir = find_gdrive_sync_dir()
    if not gdrive_dir:
        return {"status": "disabled", "message": "Google Drive KINO non détecté"}

    sync_file = gdrive_dir / "kino_sync.json"
    now = time.time()
    device_name = platform.system()

    with _DB_LOCK:
        conn = get_connection()
        try:
            remote_data = None
            if sync_file.exists():
                try:
                    remote_data = json.loads(sync_file.read_text(encoding="utf-8"))
                except Exception:
                    remote_data = None
                if not isinstance(remote_data, dict):
                    # Ne pas écraser un fichier illisible (copie Drive en cours…) :
                    # il peut contenir les ajouts de l'autre appareil.
                    return {"status": "error", "message": "Fichier de synchronisation Google Drive illisible"}

            with conn:
                deletions = _load_deletions(conn)

                # 1. Importer les modifications distantes si le fichier existe
                if remote_data is not None:
                    _merge_deletions(deletions, _clean_deletions(remote_data.get("deleted")))
                    deleted_wl = deletions["watchlist"]
                    deleted_hist = deletions["history"]

                    # A. Suppressions venues de l'autre appareil : retirer les versions locales antérieures
                    for item_id, deleted_at in deleted_wl.items():
                        conn.execute("DELETE FROM watchlist WHERE id = ? AND COALESCE(added_at, 0) <= ?",
                                     (item_id, deleted_at))
                    for item_id, deleted_at in deleted_hist.items():
                        row = conn.execute("SELECT last_watched, data_json FROM watch_history WHERE id = ?", (item_id,)).fetchone()
                        if not row:
                            continue
                        try:
                            data = json.loads(row["data_json"] or "{}")
                        except Exception:
                            data = {}
                        if _history_version(row["last_watched"], data) <= deleted_at:
                            conn.execute("DELETE FROM watch_history WHERE id = ?", (item_id,))

                    # B. Fusion Watchlist (un ajout antérieur à une suppression n'est pas réinséré)
                    for item in _dict_items(remote_data.get("watchlist")):
                        item_id = str(item.get("id") or "")
                        if not item_id:
                            continue
                        r_added = _as_stamp(item.get("added_at"))
                        if item_id in deleted_wl and r_added <= deleted_wl[item_id]:
                            continue
                        conn.execute("""
                            INSERT OR IGNORE INTO watchlist (id, name, type, year, poster, imdb_rating, added_at, user_rating)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            item_id,
                            item.get("name") or "",
                            item.get("type") or "movie",
                            str(item.get("year") or ""),
                            item.get("poster") or "",
                            str(item.get("imdbRating") or ""),
                            r_added or now,
                            str(item.get("user_rating") or "")
                        ))

                    # C. Fusion Historique de lecture (garder le timestamp de visionnage le plus récent)
                    for entry in _dict_items(remote_data.get("history")):
                        item_id = str(entry.get("id") or "")
                        if not item_id:
                            continue
                        r_last = _as_stamp(entry.get("last_watched"))
                        if item_id in deleted_hist and _history_version(r_last, entry.get("data")) <= deleted_hist[item_id]:
                            continue
                        remote_entry = entry.get("data") or {}
                        data_str = json.dumps(remote_entry, ensure_ascii=False)
                        row = conn.execute("SELECT last_watched, data_json FROM watch_history WHERE id = ?", (item_id,)).fetchone()
                        if not row:
                            conn.execute("""
                                INSERT INTO watch_history (id, data_json, last_watched)
                                VALUES (?, ?, ?)
                            """, (item_id, data_str, r_last))
                            continue
                        try:
                            local_entry = json.loads(row["data_json"] or "{}")
                        except Exception:
                            local_entry = {}
                        # The entry played last wins, but the newer rating stays.
                        if r_last > _as_stamp(row["last_watched"]):
                            merged = ratings.newer_rating(remote_entry, local_entry)
                            conn.execute("""
                                UPDATE watch_history SET data_json = ?, last_watched = ? WHERE id = ?
                            """, (json.dumps(merged, ensure_ascii=False), r_last, item_id))
                        else:
                            merged = ratings.newer_rating(local_entry, remote_entry)
                            if merged is not local_entry:
                                conn.execute("UPDATE watch_history SET data_json = ? WHERE id = ?",
                                             (json.dumps(merged, ensure_ascii=False), item_id))

                    # D. Fusion Config (clés debrid, letterboxd si vides localement)
                    remote_cfg = remote_data.get("config")
                    if isinstance(remote_cfg, dict):
                        for k, v in remote_cfg.items():
                            if k == SYNC_DELETIONS_KEY:
                                continue
                            row = conn.execute("SELECT value_json FROM app_config WHERE key = ?", (k,)).fetchone()
                            if not row or not row["value_json"]:
                                conn.execute("INSERT OR REPLACE INTO app_config (key, value_json) VALUES (?, ?)", (k, json.dumps(v)))

                # Un élément encore présent a été revu ou rajouté après sa suppression.
                for kind, table in (("history", "watch_history"), ("watchlist", "watchlist")):
                    present = {r["id"] for r in conn.execute(f"SELECT id FROM {table}")}
                    for item_id in [i for i in deletions[kind] if i in present]:
                        del deletions[kind][item_id]
                deletions = _save_deletions(conn, deletions)

            # 2. Exporter l'état consolidé vers Google Drive
            rows_wl = conn.execute("SELECT id, name, type, year, poster, imdb_rating, added_at, user_rating FROM watchlist").fetchall()
            wl_export = [
                {
                    "id": r["id"],
                    "name": r["name"] or "",
                    "type": r["type"] or "movie",
                    "year": r["year"] or "",
                    "poster": r["poster"] or "",
                    "imdbRating": r["imdb_rating"] or "",
                    "user_rating": (r["user_rating"] if "user_rating" in r.keys() else "") or "",
                    "added_at": r["added_at"],
                }
                for r in rows_wl
            ]

            rows_hist = conn.execute("SELECT id, data_json, last_watched FROM watch_history ORDER BY last_watched DESC LIMIT 200").fetchall()
            hist_export = []
            for r in rows_hist:
                try:
                    d = json.loads(r["data_json"])
                except Exception:
                    d = {}
                hist_export.append({
                    "id": r["id"],
                    "data": d,
                    "last_watched": r["last_watched"]
                })

            cfg_rows = conn.execute("SELECT key, value_json FROM app_config WHERE key != ?", (SYNC_DELETIONS_KEY,)).fetchall()
            cfg_export = {}
            for r in cfg_rows:
                try:
                    cfg_export[r["key"]] = json.loads(r["value_json"])
                except Exception:
                    cfg_export[r["key"]] = r["value_json"]

            consolidated = {
                # Version 2 : ajoute « deleted ». Les champs lus par les versions
                # précédentes gardent leur forme.
                "version": 2,
                "updated_at": now,
                "device": device_name,
                "config": cfg_export,
                "watchlist": wl_export,
                "history": hist_export,
                "deleted": deletions,
            }

            # Écriture atomique sécurisée
            tmp_sync = sync_file.with_suffix(".tmp")
            tmp_sync.write_text(json.dumps(consolidated, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_sync.replace(sync_file)

            return {
                "status": "synced",
                "path": str(sync_file),
                "device": device_name,
                "updated_at": now,
                "watchlist_count": len(wl_export),
                "history_count": len(hist_export),
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}
        finally:
            conn.close()


def start_background_maintenance():
    """Startup work that touches shared state, run by the app once it owns the session.

    It used to start when this module was imported: a second KINO window (closed at once
    by the single-instance lock) and any tool importing the module synchronised Google
    Drive in parallel with the running app.
    """
    db_cache_purge_expired(compact=True)
    try:
        threading.Thread(target=db_sync_gdrive, daemon=True).start()
    except Exception:
        pass


# Initialisation automatique au chargement du module (tables seulement)
init_db()
db_drop_shared_aliases()

