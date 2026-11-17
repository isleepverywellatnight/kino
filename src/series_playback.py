"""Persistent playback preferences scoped to a stable series identifier."""

import re

import kino_db


_PREFIX = "series_playback_v1:"
_ROLES = {"main", "commentary", "description"}
_SUBTITLE_VARIANTS = {"standard", "forced", "sdh"}


def series_id(value):
    identifier = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9:_-]{1,95}", identifier):
        raise ValueError("Invalid series identifier")
    return identifier


def _key(value):
    return _PREFIX + series_id(value)


def normalize_preference(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid playback preference")
    audio = value.get("audio")
    if audio is not None:
        if not isinstance(audio, dict):
            raise ValueError("Invalid audio preference")
        language = str(audio.get("language") or "").lower()
        role = str(audio.get("role") or "main").lower()
        original = audio.get("original", False)
        if not re.fullmatch(r"[a-z]{2,3}", language) or role not in _ROLES or not isinstance(original, bool):
            raise ValueError("Invalid audio preference")
        audio = {"language": language, "role": role, "original": original}

    subtitles = value.get("subtitles")
    if subtitles is not None:
        if not isinstance(subtitles, dict) or not isinstance(subtitles.get("enabled"), bool):
            raise ValueError("Invalid subtitle preference")
        enabled = subtitles["enabled"]
        language = str(subtitles.get("language") or "").lower()
        kind = str(subtitles.get("kind") or "").lower()
        variant = str(subtitles.get("variant") or "standard").lower()
        if enabled and (language not in {"fr", "en"} or kind != "opensubtitles" or variant not in _SUBTITLE_VARIANTS):
            raise ValueError("Invalid subtitle preference")
        subtitles = {"enabled": enabled, "language": language if enabled else "", "kind": kind if enabled else "",
                     "variant": variant if enabled else ""}

    if audio is None and subtitles is None:
        raise ValueError("Empty playback preference")
    return {"audio": audio, "subtitles": subtitles}


def get(value):
    stored = kino_db.get_config(_key(value))
    if stored is None:
        return None
    try:
        return normalize_preference(stored)
    except ValueError:
        return None


def save(value, preference):
    normalized = normalize_preference(preference)
    if not kino_db.save_config(_key(value), normalized):
        raise RuntimeError("Could not save playback preference")
    return normalized


def clear(value):
    if not kino_db.delete_config(_key(value)):
        raise RuntimeError("Could not clear playback preference")
