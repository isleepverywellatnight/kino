"""Choose a small set of unwatched titles for the time and mood available."""

import concurrent.futures
import json
import math
import re
import time
import unicodedata
import uuid

import kino_db


_MOOD_GENRES = {
    "light": {"comedy", "comedie", "family", "familial", "slice of life", "tranche de vie"},
    "intense": {"action", "crime", "thriller", "horror", "horreur", "mystery", "mystere"},
}


def feedback_state():
    """Read the local decisions and impression counts keyed by media ID."""
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            rows = conn.execute("SELECT * FROM tonight_feedback").fetchall()
            return {row["media_id"]: {**dict(row), "genres": json.loads(row["genres_json"])} for row in rows}
        finally:
            conn.close()


def apply_feedback(media_id, action):
    media_id = str(media_id or "").strip()
    if action not in ("defer", "exclude", "restore", "seen") or not 0 < len(media_id) <= 200:
        raise ValueError("Retour invalide")
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            with conn:
                media = conn.execute("SELECT id, name, type FROM watchlist WHERE id=?", (media_id,)).fetchone()
                if not media:
                    raise ValueError("Titre absent de Ma Liste")
                previous = conn.execute("SELECT * FROM tonight_feedback WHERE media_id=?", (media_id,)).fetchone()
                undo = {"decision": previous["decision"] if previous else "",
                        "until_ts": previous["until_ts"] if previous else 0}
                token = uuid.uuid4().hex
                now = time.time()
                decision = {"defer": "deferred", "exclude": "excluded", "restore": "", "seen": "seen"}[action]
                until = now + 86400 if action == "defer" else 0
                if action == "seen":
                    history_row = conn.execute("SELECT data_json FROM watch_history WHERE id=?", (media_id,)).fetchone()
                    history_before = history_row["data_json"] if history_row else None
                    history = json.loads(history_before) if history_before else {}
                    if media_id in _watched_ids([history]) or (previous and previous["decision"] == "seen"):
                        raise ValueError("Titre déjà vu")
                    history.update({"id": media_id, "name": media["name"], "type": media["type"],
                                    "completed": True, "manual_watched": True,
                                    "progress_pct": 100.0, "last_watched": now})
                    try:
                        old_duration = float(history.get("duration") or 0)
                    except (TypeError, ValueError):
                        old_duration = 0
                    history["duration"] = max(old_duration, 7200)
                    history["position"] = history["duration"]
                    history_after = json.dumps(history, ensure_ascii=False)
                    conn.execute("""INSERT INTO watch_history(id, data_json, last_watched) VALUES (?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json,
                        last_watched=excluded.last_watched""", (media_id, history_after, now))
                    undo["history_before"] = history_before
                    undo["history_after"] = history_after
                conn.execute("""
                    INSERT INTO tonight_feedback(media_id, name, media_type, decision, until_ts,
                        last_action_id, undo_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(media_id) DO UPDATE SET decision=excluded.decision,
                        until_ts=excluded.until_ts, last_action_id=excluded.last_action_id,
                        undo_json=excluded.undo_json, updated_at=excluded.updated_at
                """, (media_id, media["name"], media["type"], decision, until, token,
                      json.dumps(undo), now))
            return token
        finally:
            conn.close()


def undo_feedback(media_id, token):
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            with conn:
                row = conn.execute("SELECT * FROM tonight_feedback WHERE media_id=?", (media_id,)).fetchone()
                if not row or not token or row["last_action_id"] != token:
                    raise ValueError("Annulation indisponible")
                previous = json.loads(row["undo_json"])
                if "history_after" in previous:
                    current = conn.execute("SELECT data_json FROM watch_history WHERE id=?", (media_id,)).fetchone()
                    if not current or current["data_json"] != previous["history_after"]:
                        raise ValueError("Historique modifié depuis ce retour")
                    if previous["history_before"] is None:
                        conn.execute("DELETE FROM watch_history WHERE id=?", (media_id,))
                    else:
                        restored = json.loads(previous["history_before"])
                        conn.execute("UPDATE watch_history SET data_json=?, last_watched=? WHERE id=?",
                                     (previous["history_before"], float(restored.get("last_watched") or 0), media_id))
                conn.execute("""UPDATE tonight_feedback SET decision=?, until_ts=?,
                    last_action_id='', undo_json='{}', updated_at=? WHERE media_id=?""",
                    (previous["decision"], previous["until_ts"], time.time(), media_id))
        finally:
            conn.close()


def reset_feedback():
    """Clear learned choices and impression counts, leaving playback history intact."""
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM tonight_feedback")
        finally:
            conn.close()


def record_impressions(choices):
    """Count titles actually returned in a Ce soir suggestion set."""
    now = time.time()
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            with conn:
                for choice in choices:
                    item = choice.get("item") or {}
                    media_id = str(item.get("id") or "")
                    if not media_id:
                        continue
                    genres = item.get("genres") or []
                    if isinstance(genres, str):
                        genres = genres.split(",")
                    genres = [str(genre)[:80] for genre in genres if genre][:20]
                    conn.execute("""
                        INSERT INTO tonight_feedback(media_id, name, media_type, genres_json,
                            shown_count, last_shown, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)
                        ON CONFLICT(media_id) DO UPDATE SET
                            name=excluded.name, media_type=excluded.media_type,
                            genres_json=excluded.genres_json,
                            shown_count=tonight_feedback.shown_count + 1,
                            last_shown=excluded.last_shown, updated_at=excluded.updated_at
                    """, (media_id, str(item.get("name") or "")[:300],
                          str(item.get("type") or "movie")[:20], json.dumps(genres), now, now))
        finally:
            conn.close()


def _normalize(value):
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(character for character in text if not unicodedata.combining(character)).strip()


def runtime_minutes(value):
    """Read a duration in minutes; return None when the duration is unknown."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) if 0 < value <= 1000 else None
    text = _normalize(value)
    hours = re.search(r"\b(\d{1,2})\s*h(?:eures?)?\s*(\d{1,2})?", text)
    if hours:
        result = int(hours.group(1)) * 60 + int(hours.group(2) or 0)
        return result if 0 < result <= 1000 else None
    minutes = re.fullmatch(r"\s*(\d{1,3})\s*(?:min(?:ute)?s?)?\s*", text)
    return int(minutes.group(1)) if minutes and int(minutes.group(1)) > 0 else None


def _watched_ids(history):
    watched = set()
    for entry in history or []:
        if not entry.get("id"):
            continue
        if entry.get("imported_watched") or entry.get("manual_watched"):
            watched.add(str(entry["id"]))
            continue
        # Completing one episode is not evidence that the entire series was seen.
        if entry.get("type") in ("series", "anime") and entry.get("season") and entry.get("episode"):
            continue
        try:
            progress = float(entry.get("progress_pct") or 0)
        except (TypeError, ValueError):
            progress = 0
        if entry.get("completed") or progress >= 85:
            watched.add(str(entry["id"]))
    return watched


def empty_reason(items, history, feedback, offline_downloads, offline_only, choices, now=None):
    if choices:
        return ""
    items = items or []
    if not items:
        return "empty_watchlist"
    ids = {str(item.get("id") or "") for item in items}
    now = time.time() if now is None else now
    watched = _watched_ids(history)
    watched.update(item_id for item_id, entry in feedback.items() if entry.get("decision") == "seen")
    if ids.issubset(watched):
        return "all_watched"
    if offline_only and not any(download.get("available") and
                                str((download.get("media") or {}).get("id") or "") in ids
                                for download in (offline_downloads or {}).values()):
        return "no_offline"
    if all(item_id in watched or (feedback.get(item_id) or {}).get("decision") == "excluded" or (
            (feedback.get(item_id) or {}).get("decision") == "deferred" and
            float((feedback.get(item_id) or {}).get("until_ts") or 0) > now) for item_id in ids):
        return "all_deferred"
    return "no_match"


def choose_tonight(items, history, max_minutes, mood="discovery", limit=3,
                   feedback=None, now=None, offline_downloads=None, offline_only=False):
    """Return eligible watchlist entries with factual reasons and uncertainty flags."""
    if mood not in ("discovery", *_MOOD_GENRES):
        raise ValueError("Unknown mood")
    if not isinstance(max_minutes, (int, float)) or isinstance(max_minutes, bool) or not math.isfinite(max_minutes) or max_minutes <= 0:
        raise ValueError("Time available must be positive")
    watched = _watched_ids(history)
    selected = []
    seen = set()
    feedback = feedback or {}
    now = time.time() if now is None else now
    any_shown = any((entry or {}).get("shown_count", 0) for entry in feedback.values())
    rejected_genre_counts = {}
    for entry in feedback.values():
        if (entry or {}).get("decision") != "excluded":
            continue
        for genre in {_normalize(genre) for genre in entry.get("genres", []) if genre}:
            rejected_genre_counts[genre] = rejected_genre_counts.get(genre, 0) + 1
    often_rejected = {genre for genre, count in rejected_genre_counts.items() if count >= 2}
    offline_by_media = {}
    for download in (offline_downloads or {}).values():
        media_id = str((download.get("media") or {}).get("id") or "")
        if download.get("available") and media_id:
            offline_by_media.setdefault(media_id, []).append(download)
    for downloads in offline_by_media.values():
        downloads.sort(key=lambda entry: (
            int((entry.get("media") or {}).get("season") or 0),
            int((entry.get("media") or {}).get("episode") or 0),
        ))
    for order, original in enumerate(items or []):
        item = dict(original)
        item_id = str(item.get("id") or "")
        if not item_id or item_id in seen or item_id in watched:
            continue
        seen.add(item_id)
        preference = feedback.get(item_id) or {}
        if preference.get("decision") in ("excluded", "seen") or (
                preference.get("decision") == "deferred" and
                float(preference.get("until_ts") or 0) > now):
            continue
        available_downloads = offline_by_media.get(item_id) or []
        if offline_only and not available_downloads:
            continue
        duration = runtime_minutes(item.get("runtime_minutes") or item.get("runtime"))
        if duration is not None and duration > max_minutes:
            continue
        raw_genres = item.get("genres") or []
        if isinstance(raw_genres, str):
            raw_genres = raw_genres.split(",")
        genres = {_normalize(genre) for genre in raw_genres if genre}
        matching = genres.intersection(_MOOD_GENRES.get(mood, set()))
        if mood != "discovery" and genres and not matching:
            continue
        duration_label = f"{duration} min" if duration is not None else "Durée inconnue"
        if duration is not None and item.get("type") in ("series", "anime"):
            duration_label += " par épisode"
        reasons = [duration_label]
        if matching:
            reasons.append("Ambiance légère" if mood == "light" else "Ambiance intense")
        elif mood == "discovery":
            reasons.append("À découvrir dans votre liste")
        else:
            reasons.append("Ambiance non renseignée")
        shown_count = max(0, int(preference.get("shown_count") or 0))
        if any_shown and shown_count == 0:
            reasons.append("Encore jamais proposé")
        if available_downloads:
            reasons.append("Disponible hors ligne")
        if often_rejected and genres and not genres & often_rejected:
            reasons.append("Évite les genres souvent écartés")
        score = (8 if matching else 2 if mood == "discovery" else 0) + (
            3 + min(4, int(4 * duration / max_minutes)) if duration is not None else 0) - min(12, shown_count * 3) - (
            4 if genres & often_rejected else 0)
        selected.append({
            "item": item,
            "reasons": reasons,
            "duration_known": duration is not None,
            "mood_known": bool(matching) if mood != "discovery" else True,
            "offline_id": available_downloads[0]["id"] if available_downloads else None,
            "_score": score,
            "_genres": genres,
            "_last_shown": float(preference.get("last_shown") or 0),
            "_order": order,
        })
    result = []
    remaining = selected[:]
    while remaining and len(result) < max(0, int(limit)):
        used_genres = set().union(*(choice["_genres"] for choice in result)) if result else set()
        remaining.sort(key=lambda candidate: (
            -(candidate["_score"] - (4 if candidate["_genres"] & used_genres else 0)),
            candidate["_last_shown"], candidate["_order"]
        ))
        candidate = remaining.pop(0)
        if used_genres and candidate["_genres"] and not candidate["_genres"] & used_genres:
            candidate["reasons"].append("Genre différent des autres idées")
        result.append(candidate)
    for candidate in result:
        candidate.pop("_order")
        candidate.pop("_score")
        candidate.pop("_last_shown")
        candidate.pop("_genres")
    return result


def enrich_watchlist(items, read_cached, fetch_metadata, max_fetch=36, timeout=7):
    """Use cached metadata first; bound network requests and disclose partial coverage."""
    enriched = [dict(item) for item in items or []]
    pending_items = []
    matched = set()

    def merge(index, metadata):
        item = enriched[index]
        if isinstance(metadata, dict):
            for key in ("runtime", "runtime_minutes", "genres"):
                if metadata.get(key) and not item.get(key):
                    item[key] = metadata[key]
        if (item.get("runtime") or item.get("runtime_minutes")) and item.get("genres"):
            matched.add(index)

    for index, item in enumerate(enriched):
        try:
            cached = read_cached(item)
        except Exception:
            cached = None
        merge(index, cached)
        if index not in matched and item.get("id"):
            pending_items.append((index, item))
    if pending_items and max_fetch > 0:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=6)
        futures = {executor.submit(fetch_metadata, item): index for index, item in pending_items[:max_fetch]}
        done, pending = concurrent.futures.wait(futures, timeout=timeout)
        for future in done:
            try:
                merge(futures[future], future.result())
            except Exception:
                pass
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
    return enriched, {"enriched": len(matched), "total": len(enriched), "partial": len(matched) < len(enriched)}
