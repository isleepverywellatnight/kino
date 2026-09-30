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
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path.home() / ".kino_data.db"
_DB_LOCK = threading.RLock()


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
                        added_at REAL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_added ON watchlist(added_at DESC);")

                # 4. Table d'historique de visionnage (Reprendre la lecture)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS watch_history (
                        id TEXT PRIMARY KEY,
                        data_json TEXT,
                        last_watched REAL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_history_last_watched ON watch_history(last_watched DESC);")

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
        finally:
            conn.close()


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


def db_cache_set(key, val, ttl_sec=86400):
    """Enregistre une valeur dans le cache SQLite avec une durée de vie (TTL)."""
    now = time.time()
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
                "SELECT id, name, type, year, poster, imdb_rating FROM watchlist ORDER BY added_at DESC LIMIT 500"
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "name": r["name"] or "",
                    "type": r["type"] or "movie",
                    "year": r["year"] or "",
                    "poster": r["poster"] or "",
                    "imdbRating": r["imdb_rating"] or "",
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
                exists = conn.execute("SELECT 1 FROM watchlist WHERE id = ?", (item_id,)).fetchone()
                if exists:
                    conn.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))
                else:
                    conn.execute(
                        """
                        INSERT INTO watchlist (id, name, type, year, poster, imdb_rating, added_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            item.get("name") or "",
                            item.get("type") or "movie",
                            str(item.get("year") or ""),
                            item.get("poster") or "",
                            str(item.get("imdbRating") or ""),
                            now,
                        ),
                    )
        finally:
            conn.close()
    return db_get_watchlist()


# =========================================================================
# GESTION DE L'HISTORIQUE DE VISIONNAGE
# =========================================================================

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

            watched_eps = list(merged.get("watched_episodes") or [])
            ep_positions = dict(merged.get("ep_positions") or {})

            pos = entry.get("position")
            dur = entry.get("duration")
            s_num = merged.get("season")
            e_num = merged.get("episode")
            ep_key = f"s{s_num}e{e_num}" if (s_num is not None and e_num is not None) else None

            if pos is not None and dur is not None and dur > 0:
                is_done = (pos / dur) >= 0.88 or (dur - pos) <= 180
                merged["completed"] = is_done
                if ep_key:
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


def db_remove_history(item_id):
    """Supprime un élément de l'historique."""
    if not item_id:
        return db_get_history()
    with _DB_LOCK:
        conn = get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM watch_history WHERE id = ?", (item_id,))
        finally:
            conn.close()
    return db_get_history()


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


def db_search_fast(query, limit=25):
    """Recherche instantanée via FTS5 dans le catalogue local."""
    if not query or not query.strip():
        return []
    clean_q = "".join(c for c in query.strip() if c.isalnum() or c.isspace())
    if not clean_q:
        return []
    tokens = clean_q.split()
    fts_expr = " AND ".join(f'"{t}"*' for t in tokens)

    with _DB_LOCK:
        conn = get_connection()
        try:
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
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()


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

    # Migration Watchlist
    wl = data.get("watchlist")
    if isinstance(wl, list) and wl:
        now = time.time()
        with _DB_LOCK:
            conn = get_connection()
            try:
                with conn:
                    for it in wl:
                        if isinstance(it, dict) and it.get("id"):
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
                            if item_id:
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


# Initialisation automatique au chargement du module
init_db()
