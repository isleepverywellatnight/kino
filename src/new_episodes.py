"""« Nouveaux épisodes » on the home page: for series the user follows, the
episodes released since they last watched them; for series in Ma Liste not
started yet, a new series or a new season that just came out.

Pure logic: the episode lists come from a function (Cinemeta in the app, a
fixture in tests), so nothing here touches the network on its own.
"""
import concurrent.futures
import datetime
import re

DAYS_KEPT = 60       # an episode stays « new » for two months at most
MAX_SERIES = 24      # most recently watched series looked at
SERIES_TYPES = ("series", "anime", "tv")


def episode_code(season, episode):
    return f"S{int(season):02d}E{int(episode):02d}"


def _code_of(value):
    match = re.fullmatch(r"s(\d{1,3})e(\d{1,4})", str(value or ""), re.IGNORECASE)
    return episode_code(match[1], match[2]) if match else ""


def watched_codes(entry):
    """Episodes marked or played to the end, as « S02E08 » codes."""
    codes = {_code_of(code) for code in entry.get("watched_episodes") or []}
    for code, position in (entry.get("ep_positions") or {}).items():
        if isinstance(position, dict) and (position.get("completed") or float(position.get("pct") or 0) >= 85):
            codes.add(_code_of(code))
    codes.discard("")
    return codes


def aired_episodes(videos):
    """Regular episodes with a release day, in order (specials and undated ones left out)."""
    episodes = []
    for video in videos or []:
        try:
            season = int(video.get("season") or 0)
            episode = int(video.get("episode") or video.get("number") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        released = str(video.get("released") or "")[:10]
        if season > 0 and episode > 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", released):
            episodes.append({
                "season": season, "episode": episode, "released": released,
                "name": str(video.get("name") or video.get("title") or "")[:200],
                "thumbnail": str(video.get("thumbnail") or "") if str(video.get("thumbnail") or "").startswith("https://") else "",
            })
    episodes.sort(key=lambda item: (item["season"], item["episode"]))
    return episodes


def _day(timestamp):
    try:
        return datetime.datetime.fromtimestamp(float(timestamp), datetime.timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _is_series(entry):
    return entry.get("type") in SERIES_TYPES and bool(re.fullmatch(r"tt\d{1,12}", str(entry.get("id") or "")))


def find_new_episodes(history, episodes_for, today, days=DAYS_KEPT, limit=12, watchlist=None):
    """Series with at least one episode released after the user's last viewing,
    and series of Ma Liste not started yet whose new season (or first one) came
    out within `days`.

    Each item names the episode to watch next, how many new episodes there are,
    the newest release day, and its `kind`: « followed », « premiere » (a new
    series, played from S01E01) or « season » (a new season of a series not
    started: the card opens the fiche).
    """
    cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days)).isoformat()
    followed = [entry for entry in history or [] if _is_series(entry) and not entry.get("imported_watched")][:MAX_SERIES]
    known = {str(entry.get("id") or "") for entry in history or []}
    listed = [entry for entry in watchlist or [] if _is_series(entry) and entry["id"] not in known][:MAX_SERIES]
    if not followed and not listed:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        lists = list(pool.map(lambda entry: _safe(episodes_for, entry["id"]), followed + listed))

    items = [item for entry, videos in zip(listed, lists[len(followed):])
             for item in [_listed_item(entry, videos, today, cutoff)] if item]
    for entry, videos in zip(followed, lists[:len(followed)]):
        episodes = [item for item in aired_episodes(videos) if item["released"] <= today]
        seen_day = _day(entry.get("last_watched"))
        if not episodes or not seen_day:
            continue
        watched = watched_codes(entry)
        try:
            last = (int(entry.get("season") or 0), int(entry.get("episode") or 0))
        except (TypeError, ValueError):
            continue
        last_done = bool(entry.get("completed")) or float(entry.get("progress_pct") or 0) >= 85 \
            or episode_code(*last) in watched if last[0] and last[1] else True
        ahead = [item for item in episodes
                 if ((item["season"], item["episode"]) > last or ((item["season"], item["episode"]) == last and not last_done))
                 and episode_code(item["season"], item["episode"]) not in watched]
        new = [item for item in ahead if item["released"] > seen_day and item["released"] >= cutoff]
        if not new:
            continue
        upcoming = ahead[0]
        items.append({
            "id": entry["id"], "name": entry.get("name") or "", "type": entry.get("type") or "series",
            "year": entry.get("year") or "", "poster": entry.get("poster") or "",
            "season": upcoming["season"], "episode": upcoming["episode"],
            "episode_name": upcoming["name"], "still": upcoming["thumbnail"], "released": upcoming["released"],
            "in_progress": (upcoming["season"], upcoming["episode"]) == last and not last_done,
            "is_new": upcoming in new, "new_count": len(new), "newest": max(item["released"] for item in new),
            "kind": "followed",
        })
    items.sort(key=lambda item: item["newest"], reverse=True)
    return items[:limit]


def _listed_item(entry, videos, today, cutoff):
    """A series of Ma Liste not started: its newest season, if it began within the window."""
    episodes = [item for item in aired_episodes(videos) if item["released"] <= today]
    if not episodes:
        return None
    first_season = min(item["season"] for item in episodes)
    premieres = {}
    for item in episodes:
        current = premieres.get(item["season"])
        if current is None or item["episode"] < current["episode"]:
            premieres[item["season"]] = item
    recent = [item for item in premieres.values() if item["released"] >= cutoff]
    if not recent:
        return None
    opening = max(recent, key=lambda item: (item["released"], item["season"]))
    season_episodes = [item for item in episodes if item["season"] == opening["season"]]
    return {
        "id": entry["id"], "name": entry.get("name") or "", "type": entry.get("type") or "series",
        "year": entry.get("year") or "", "poster": entry.get("poster") or "",
        "season": opening["season"], "episode": opening["episode"],
        "episode_name": opening["name"], "still": opening["thumbnail"], "released": opening["released"],
        "in_progress": False, "is_new": True, "new_count": len(season_episodes),
        "newest": max(item["released"] for item in season_episodes),
        "kind": "premiere" if opening["season"] == first_season else "season",
    }


def _safe(episodes_for, imdb_id):
    try:
        return episodes_for(imdb_id)
    except Exception:
        return None
