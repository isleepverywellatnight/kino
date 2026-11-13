"""Local spoiler preferences. Revealing an episode never changes watch history."""

import re
import threading

import kino_db


_KEY = "spoiler_protection_v1"
_LOCK = threading.RLock()
_ID = re.compile(r"[a-z0-9][a-z0-9:_-]{1,95}\Z")


def series_id(value):
    identifier = str(value or "").strip().lower()
    if not _ID.fullmatch(identifier):
        raise ValueError("Invalid series identifier")
    return identifier


def episode_code(season, episode):
    if isinstance(season, bool) or isinstance(episode, bool):
        raise ValueError("Invalid episode")
    if not re.fullmatch(r"\d{1,2}", str(season)) or not re.fullmatch(r"\d{1,3}", str(episode)):
        raise ValueError("Invalid episode")
    try:
        season, episode = int(season), int(episode)
    except (TypeError, ValueError):
        raise ValueError("Invalid episode") from None
    if not 0 <= season <= 99 or not 1 <= episode <= 999:
        raise ValueError("Invalid episode")
    return f"S{season:02d}E{episode:02d}"


def get():
    stored = kino_db.get_config(_KEY, {})
    if not isinstance(stored, dict):
        stored = {}
    overrides = stored.get("overrides")
    revealed = stored.get("revealed")
    return {
        "enabled": stored.get("enabled") is True,
        "overrides": {k: v for k, v in (overrides if isinstance(overrides, dict) else {}).items()
                      if isinstance(k, str) and _ID.fullmatch(k) and isinstance(v, bool)},
        "revealed": {k: [code for code in codes if isinstance(code, str) and re.fullmatch(r"S\d{2}E\d{2,3}", code)]
                     for k, codes in (revealed if isinstance(revealed, dict) else {}).items()
                     if isinstance(k, str) and _ID.fullmatch(k) and isinstance(codes, list)},
    }


def update(action, *, enabled=None, identifier=None, season=None, episode=None):
    if action not in {"global", "series", "reveal", "hide"}:
        raise ValueError("Invalid action")
    if action == "global" and not isinstance(enabled, bool):
        raise ValueError("Invalid global choice")
    if action == "series" and enabled is not None and not isinstance(enabled, bool):
        raise ValueError("Invalid series choice")
    if action != "global":
        identifier = series_id(identifier)
    if action in {"reveal", "hide"}:
        code = episode_code(season, episode)
    with _LOCK:
        state = get()
        if action == "global":
            state["enabled"] = enabled
        elif action == "series":
            if enabled is None:
                state["overrides"].pop(identifier, None)
            else:
                state["overrides"][identifier] = enabled
        else:
            revealed = set(state["revealed"].get(identifier, []))
            if action == "reveal":
                revealed.add(code)
            else:
                revealed.discard(code)
            if revealed:
                state["revealed"][identifier] = sorted(revealed)[:500]
            else:
                state["revealed"].pop(identifier, None)
        if not kino_db.save_config(_KEY, state):
            raise RuntimeError("Could not save spoiler preference")
        return state
