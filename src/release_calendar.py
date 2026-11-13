"""« Calendrier »: the episodes of the series the user follows coming out this
week and next week (Monday to Sunday), day by day, then each series' first
release after these two weeks, « Plus tard ».

The series are those of « Mes séries » (series_progress.followed_series), with
the same rules for what counts as seen. Episodes of one series out the same
day are grouped (a whole season put online at once). Numbers and dates only:
no episode title leaves here. Pure logic: the episode lists come from a
function (Cinemeta in the app, a fixture in tests)."""
import datetime

import series_progress
from new_episodes import aired_episodes

WEEKS = 2
LATER_LIMIT = 12


def window(today, weeks=WEEKS):
    """Monday of this week, and the Monday after the last week shown (not included)."""
    day = datetime.date.fromisoformat(today)
    start = day - datetime.timedelta(days=day.weekday())
    return start.isoformat(), (start + datetime.timedelta(days=7 * weeks)).isoformat()


def _premiere(item):
    if item["episode"] != 1:
        return ""
    return "series" if item["season"] == 1 else "season"


def _base(entry, followed):
    return {**{key: entry.get(key) or "" for key in ("id", "name", "year", "poster")}, "followed": followed}


def _releases(entry, episodes, start, end, today, seen, followed):
    """This series' episodes out between start and end, those of one day in a row grouped.
    `resume_pct` is set on the episode under way."""
    unseen = [(item["season"], item["episode"]) for item in episodes if item["released"] <= today and not seen(item)]
    under_way = series_progress.resume_point(entry) if followed else None
    items = []
    for item in episodes:
        if not start <= item["released"] < end:
            continue
        was_seen = seen(item)
        last = items[-1] if items else None
        if (last and last["released"] == item["released"] and last["season"] == item["season"]
                and last["last_episode"] + 1 == item["episode"] and last["seen"] == was_seen):
            last["last_episode"] = item["episode"]
            continue
        position = (item["season"], item["episode"])
        items.append({
            **_base(entry, followed),
            "season": item["season"], "episode": item["episode"], "last_episode": item["episode"],
            "released": item["released"], "aired": item["released"] <= today, "seen": was_seen,
            "before": sum(1 for code in unseen if code < position), "premiere": _premiere(item),
            "resume_pct": under_way[2] if under_way and under_way[:2] == position else 0,
        })
    return items


def _later(entry, episodes, end, followed):
    coming = [item for item in episodes if item["released"] >= end]
    if not coming:
        return None
    first = min(coming, key=lambda item: (item["released"], item["season"], item["episode"]))
    return {**_base(entry, followed), "season": first["season"], "episode": first["episode"],
            "released": first["released"], "premiere": _premiere(first)}


def calendar(history, watchlist, episodes_for, today, weeks=WEEKS, local_today=None):
    """The page: {today, local_today, start, end, days: [{date, items}], later, series, unavailable}.
    `today` (UTC, like the release dates) says what is out; `local_today`, the
    Mac's own date, places the weeks and « Aujourd'hui ». `days` holds only days
    with a release; `unavailable` counts the series whose episode list could not be read."""
    local_today = local_today or today
    start, end = window(local_today, weeks)
    history_series, listed = series_progress.followed_series(history, watchlist)
    entries = history_series + listed
    answer = {"today": today, "local_today": local_today, "start": start, "end": end, "days": [], "later": [],
              "series": len(entries), "unavailable": 0}
    lists = series_progress.episode_lists(entries, episodes_for)
    items, later = [], []
    for index, (entry, videos) in enumerate(zip(entries, lists)):
        if videos is None:
            answer["unavailable"] += 1
            continue
        episodes = aired_episodes(videos)
        followed = index < len(history_series)
        seen = series_progress.seen_test(entry) if followed else (lambda item: False)
        items += _releases(entry, episodes, start, end, today, seen, followed)
        upcoming = _later(entry, episodes, end, followed)
        if upcoming:
            later.append(upcoming)
    items.sort(key=lambda item: (item["released"], not item["followed"], str(item["name"]).lower(), item["season"], item["episode"]))
    for item in items:
        if not answer["days"] or answer["days"][-1]["date"] != item["released"]:
            answer["days"].append({"date": item["released"], "items": []})
        answer["days"][-1]["items"].append(item)
    later.sort(key=lambda item: (item["released"], str(item["name"]).lower()))
    answer["later"] = later[:LATER_LIMIT]
    return answer


__all__ = ["calendar", "window", "WEEKS", "LATER_LIMIT"]
