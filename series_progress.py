"""« Mes séries »: for each series the user follows, where they are, how many
aired episodes are left, the next one to watch and the next one to come out;
series of Ma Liste not started yet come apart, « À commencer ».

Same rules as « Nouveaux épisodes » (new_episodes.py): episodes before the one
reached count as seen, as do those marked or played to the end. Pure logic:
the episode lists come from a function (Cinemeta in the app, a fixture in tests).
No episode title leaves here: only numbers and dates, never a spoiler."""
import concurrent.futures

from new_episodes import SERIES_TYPES, _is_series, aired_episodes, episode_code, watched_codes

MAX_SERIES = 40
DONE_PCT = 85


def _number(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _episode(item, **extra):
    return {"season": item["season"], "episode": item["episode"], "released": item["released"], **extra}


def _card(entry):
    return {key: entry.get(key) or "" for key in ("id", "name", "year", "poster")}


def _position(entry):
    """The episode reached, its share played, whether it is under way and whether it is done."""
    season, episode = _number(entry.get("season"), 1), _number(entry.get("episode"), 1)
    pct = float(entry.get("progress_pct") or 0)
    in_progress = not entry.get("completed") and 1 <= pct < DONE_PCT
    return season, episode, pct, in_progress, pct >= DONE_PCT or bool(entry.get("completed"))


def resume_point(entry):
    """The episode under way, as (season, episode, share played), or None."""
    season, episode, pct, in_progress, _ = _position(entry)
    return (season, episode, round(pct)) if in_progress else None


def seen_test(entry):
    """Whether an episode counts as seen for this history entry: before the one
    reached, the one reached once finished, or marked or played to the end."""
    season, episode, _, _, done = _position(entry)
    reached, watched = (season, episode), watched_codes(entry)

    def seen(item):
        position = (item["season"], item["episode"])
        return position < reached or (position == reached and done) or episode_code(*position) in watched
    return seen


def followed_series(history, watchlist):
    """The series the user follows: those of the history (not imported as seen),
    then those of Ma Liste not started yet."""
    history_series = [entry for entry in history or [] if _is_series(entry) and not entry.get("imported_watched")][:MAX_SERIES]
    known = {str(entry.get("id") or "") for entry in history or []}
    listed = [entry for entry in watchlist or [] if _is_series(entry) and entry["id"] not in known][:MAX_SERIES]
    return history_series, listed


def episode_lists(entries, episodes_for):
    """Each series' episode list, fetched side by side; None for one that fails."""
    def episodes(entry):
        try:
            return episodes_for(entry["id"])
        except Exception:
            return None

    if not entries:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(episodes, entries))


def followed(entry, videos, today):
    """One series from the history: where the user is and what is left."""
    card = _card(entry)
    season, episode, pct, in_progress, done = _position(entry)
    current = {"season": season, "episode": episode, "progress_pct": round(pct) if in_progress else (100 if done else 0)}
    all_episodes = aired_episodes(videos)
    if videos is None or not all_episodes:
        return {**card, "group": "continue", "known": False, "current": current, "next": None, "upcoming": None,
                "aired": 0, "seen": 0, "remaining": 0, "finished": bool(entry.get("series_finished")),
                "last_watched": entry.get("last_watched") or 0}
    aired = [item for item in all_episodes if item["released"] <= today]
    coming = [item for item in all_episodes if item["released"] > today]
    seen = seen_test(entry)
    left = [item for item in aired if not seen(item)]
    next_item = None
    if in_progress and any((item["season"], item["episode"]) == (season, episode) for item in aired):
        next_item = {"season": season, "episode": episode, "released": "", "resume": True, "progress_pct": round(pct)}
    elif left:
        next_item = _episode(left[0], resume=False, progress_pct=0)
    return {
        **card,
        "group": "continue" if next_item else "uptodate",
        "known": True,
        "current": current,
        "next": next_item,
        "upcoming": _episode(coming[0]) if coming else None,
        "aired": len(aired),
        "seen": len(aired) - len(left),
        "remaining": len(left),
        "finished": bool(entry.get("series_finished")) and not left and not coming,
        "last_watched": entry.get("last_watched") or 0,
    }


def to_start(entry, videos, today):
    """A series of Ma Liste not started yet: its first aired episode, and how many there are."""
    all_episodes = aired_episodes(videos)
    aired = [item for item in all_episodes if item["released"] <= today]
    coming = [item for item in all_episodes if item["released"] > today]
    first = aired[0] if aired else None
    return {
        **_card(entry),
        "group": "start",
        "known": videos is not None and bool(all_episodes),
        "current": None,
        "next": _episode(first, resume=False, progress_pct=0) if first else None,
        "upcoming": _episode(coming[0]) if coming else None,
        "aired": len(aired),
        "seen": 0,
        "remaining": len(aired),
        "finished": False,
        "last_watched": 0,
    }


def my_series(history, watchlist, episodes_for, today):
    """Every followed series, then those of Ma Liste not started, sorted for the page:
    « À continuer » most recent first, « À jour » by the next release, « À commencer » in list order."""
    history_series, listed = followed_series(history, watchlist)
    if not history_series and not listed:
        return []
    lists = episode_lists(history_series + listed, episodes_for)
    items = [followed(entry, videos, today) for entry, videos in zip(history_series, lists)]
    items += [to_start(entry, videos, today) for entry, videos in zip(listed, lists[len(history_series):])]
    order = {"continue": 0, "uptodate": 1, "start": 2}
    no_date = "9999-99-99"
    items.sort(key=lambda item: (
        order[item["group"]],
        -float(item["last_watched"] or 0) if item["group"] == "continue" else 0,
        (item["upcoming"] or {}).get("released", no_date) if item["group"] == "uptodate" else "",
    ))
    return items


__all__ = ["my_series", "followed", "to_start", "seen_test", "resume_point", "followed_series", "episode_lists", "SERIES_TYPES"]
