"""« Lecture en un clic », step by step (docs/playback.md).

The pure steps live here: what was asked, which sources the page set aside,
the names a film file may carry, what the player is told, which player opens.
The steps that reach the network or the player stay in app.py:
post_one_click_play() and start_one_click()."""
import re
import sys

import stream_engine
import torrent_engine

NO_PROVIDER = {"error": "Connectez votre fournisseur dans les paramètres avant de lancer la lecture.", "code": "provider_auth"}
REFUSED = {"error": "Votre fournisseur refuse l’accès. Vérifiez votre connexion et votre abonnement dans les paramètres.",
           "code": "provider_auth"}
NO_SOURCE = {"error": "Aucune autre source prête à lire. Vous pouvez réessayer les sources écartées ou ouvrir la fiche.",
             "code": "no_source"}
NO_STREAM = "Aucun flux valide trouvé pour ce titre."
PREPARED_WRONG_FILE = {"error": "La source préparée ne contient pas le fichier attendu. Choisissez une autre source dans la fiche.",
                       "code": "prepared_wrong_file"}

_SOURCE_KEY = re.compile(r"[0-9a-f]{32}")
_TORRENT_ID = re.compile(r"[A-Za-z0-9:_-]{1,80}")
SERIES_TYPES = ("series", "anime", "tv")


def request(body, cfg):
    """What the page asked for, with KINO's defaults."""
    title = body.get("title", "KINO")
    media_type = body.get("type", "movie")
    default_player = "integrated" if sys.platform == "darwin" else "kino"
    return {
        "imdb_id": body.get("imdb_id", ""),
        "type": media_type,
        "is_series": media_type in SERIES_TYPES,
        "season": body.get("season", 1),
        "episode": body.get("episode", 1),
        "title": title,
        "series_name": body.get("name") or title.split(" — ")[0],
        "year": body.get("year", ""),
        "poster": body.get("poster", ""),
        "player_mode": str(body.get("player_mode") or cfg.get("player_mode") or default_player).lower(),
    }


def runtime_minutes(meta):
    """The film's runtime in minutes from its data (« 1 h 50 min » gives 1), 0 when unknown."""
    found = re.search(r"(\d+)", str((meta or {}).get("runtime") or ""))
    return int(found.group(1)) if found else 0


def set_aside(body):
    """The source keys the page asks to skip: at most 30, well formed only."""
    keys = body.get("exclude_source_keys") or []
    if not isinstance(keys, list):
        return set()
    return {key for key in keys[:30] if isinstance(key, str) and _SOURCE_KEY.fullmatch(key)}


def file_aliases(play, meta, candidate):
    """Names a film's file may carry: its original title and the release's own
    cleaned title. An episode is found by its number, not by a name."""
    if play["is_series"]:
        return []
    return [meta.get("name", ""), torrent_engine.clean_title_for_comparison(candidate.get("title") or "")]


def media_context(play, target_file):
    """What the player and the history are told about the title, or None without an IMDb id."""
    if not play["imdb_id"]:
        return None
    return {
        "id": play["imdb_id"],
        "name": play["series_name"],
        "type": play["type"],
        "year": play["year"],
        "poster": play["poster"],
        "season": int(play["season"]) if play["is_series"] else None,
        "episode": int(play["episode"]) if play["is_series"] else None,
        "filename": target_file["filename"],
    }


def uses_built_in_player(play):
    """KINO's own player, unless mpv is asked for; on a Mac, unless « external » is."""
    mode = play["player_mode"]
    return mode == "integrated" or (sys.platform == "darwin" and mode != "external")


def ready_first(sources, cfg):
    """« La qualité choisie d'abord »: one click cannot wait for Debrid to prepare a
    source, so the ready ones come first, in their order (a ready 4K, then the
    best ready one); « Lancer » in the fiche prepares the recommended one."""
    if cfg.get("source_priority") != "quality":
        return sources
    return sorted(sources, key=lambda source: not source.get("is_instant"))


# --- « Préparer en un clic »: the chosen quality, even if Debrid must prepare it ---

def prepared(body):
    """The source the page waited for, now that Debrid has it: {"torrent_id", "source_key"} or None."""
    value = body.get("prepared")
    if not isinstance(value, dict):
        return None
    torrent_id, key = str(value.get("torrent_id") or ""), str(value.get("source_key") or "")
    if not _TORRENT_ID.fullmatch(torrent_id) or not _SOURCE_KEY.fullmatch(key):
        return None
    return {"torrent_id": torrent_id, "source_key": key}


def source_to_prepare(sources, cfg, body, skip):
    """With « La qualité choisie d'abord », when no source in that quality is ready:
    the best one to prepare. None otherwise, or when the page asks for a ready one."""
    if cfg.get("source_priority") != "quality" or body.get("prefer_ready"):
        return None
    chosen = [source for source in sources if (source.get("score_parts") or {}).get("priority_points")]
    if not chosen or any(source.get("is_instant") for source in chosen):
        return None
    return next((source for source in chosen if stream_engine.stream_candidate_key(source) not in skip), None)


def preparing(answer, candidate, key, sources):
    """What the page shows while Debrid prepares the source, and whether a ready one exists."""
    try:
        progress = max(0, min(100, int(float(answer.get("progress") or 0))))
    except (TypeError, ValueError):
        progress = 0
    return {
        "preparing": True,
        "torrent_id": str(answer.get("torrent_id") or ""),
        "progress": progress,
        "status": str(answer.get("status") or ""),
        "speed": str(answer.get("speed") or ""),
        "seeders": int(answer.get("seeders") or 0),
        "source_key": key,
        "chosen_torrent": chosen_label(candidate),
        "ready_alternative": any(source.get("is_instant") for source in sources),
    }


def chosen_label(candidate):
    return f"{candidate.get('source', '')} {candidate.get('title', '')}".strip()
