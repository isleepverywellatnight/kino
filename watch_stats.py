"""Viewing statistics from KINO's history. Pure logic: the genres of a title
come from a function given by the caller.

History keeps one entry per title (the last position, the episodes seen), not
each viewing: the figures say what that allows and nothing more.
- Films imported as seen (Letterboxd, Trakt) count as films seen, not in the
  viewing time or the months: they were not watched in KINO.
- Viewing time adds the positions KINO saved: a film seen to the end counts
  its length, an episode its saved position or its length once finished.
"""
import re
from datetime import datetime, timezone

MONTHS = 12
TOP_GENRES = 8


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def is_watched(entry):
    return bool(entry.get("completed") or _number(entry.get("progress_pct")) >= 85 or entry.get("imported_watched"))


def _code(value):
    match = re.match(r"^s0*(\d+)e0*(\d+)$", str(value or "").strip(), re.I)
    return f"s{int(match[1])}e{int(match[2])}" if match else None


def episodes_seen(entry):
    """Codes « s1e2 » of the episodes seen: marked as seen, or played to the end."""
    codes = {_code(value) for value in entry.get("watched_episodes") or []}
    for value, position in (entry.get("ep_positions") or {}).items():
        if isinstance(position, dict) and (position.get("completed") or _number(position.get("pct")) >= 85):
            codes.add(_code(value))
    codes.discard(None)
    return codes


def _seconds(entry):
    if entry.get("imported_watched"):
        return 0
    if entry.get("type") == "series":
        total = 0
        for position in (entry.get("ep_positions") or {}).values():
            if not isinstance(position, dict):
                continue
            duration, played = _number(position.get("duration")), _number(position.get("position"))
            total += duration if position.get("completed") and duration else min(played, duration or played)
        return total
    duration, played = _number(entry.get("duration")), _number(entry.get("position"))
    if is_watched(entry) and duration:
        return duration
    return min(played, duration or played)


def _year(entry):
    match = re.search(r"(1[89]\d\d|20\d\d)", str(entry.get("year") or ""))
    return int(match[1]) if match else None


def _month_keys(today, count=MONTHS):
    year, month = today.year, today.month
    keys = []
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return keys[::-1]


def compute(history, genres_for=lambda entry: [], today=None):
    """Totals, favourite genres, titles opened per month (last 12), decades."""
    today = today or datetime.now(timezone.utc)
    entries = [entry for entry in history or [] if isinstance(entry, dict) and entry.get("id")]
    films = [entry for entry in entries if entry.get("type") != "series"]
    series = [entry for entry in entries if entry.get("type") == "series"]

    films_seen = [entry for entry in films if is_watched(entry)]
    series_codes = {entry["id"]: episodes_seen(entry) for entry in series}
    series_finished = [entry for entry in series if entry.get("series_finished") and is_watched(entry)]
    series_started = [entry for entry in series if entry not in series_finished]
    seen_titles = films_seen + [entry for entry in series if series_codes[entry["id"]] or is_watched(entry)]

    genre_counts = {}
    known = 0
    for entry in seen_titles:
        genres = [str(name).strip() for name in (genres_for(entry) or []) if str(name).strip()]
        if genres:
            known += 1
        for name in dict.fromkeys(genres):
            genre_counts[name] = genre_counts.get(name, 0) + 1
    genres = sorted(genre_counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_GENRES]

    months = {key: 0 for key in _month_keys(today)}
    for entry in entries:
        if entry.get("imported_watched"):
            continue
        stamp = _number(entry.get("last_watched"))
        if stamp > 0:
            key = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m")
            if key in months:
                months[key] += 1

    decades = {}
    for entry in seen_titles:
        year = _year(entry)
        if year:
            decades[year // 10 * 10] = decades.get(year // 10 * 10, 0) + 1

    years = [(entry, _year(entry)) for entry in films_seen if _year(entry)]
    oldest = min(years, key=lambda item: item[1], default=None)
    return {
        "films": {"seen": len(films_seen), "started": len(films) - len(films_seen),
                  "imported": sum(1 for entry in films_seen if entry.get("imported_watched"))},
        "episodes": {"seen": sum(len(codes) for codes in series_codes.values())},
        "series": {"total": len(series), "started": len(series_started), "finished": len(series_finished)},
        "watch_seconds": int(sum(_seconds(entry) for entry in entries)),
        "genres": [{"name": name, "count": count} for name, count in genres],
        "genres_known": known,
        "titles_seen": len(seen_titles),
        "months": [{"month": key, "count": count} for key, count in months.items()],
        "decades": [{"decade": decade, "count": count} for decade, count in sorted(decades.items())],
        "oldest": {"id": oldest[0]["id"], "name": oldest[0].get("name") or "", "year": oldest[1]} if oldest else None,
    }
