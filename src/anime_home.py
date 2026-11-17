"""The Anime tab: rows of what is on (AniList) and the week's planning, each
work paired with the fiche KINO can play.

AniList knows a work by its own number and counts every season as a work;
KINO plays IMDb fiches, one per series. A public table of identifiers
(arm.haglund.dev) gives the fiche and the season of each work. Only AniList
numbers of public titles are sent out: nothing about what the user watched.
"""

import datetime
import json
import re
import threading
import time
import urllib.error
import urllib.request

import anime_journey
import kino_db


ANILIST_URL = anime_journey.ANILIST_URL
IDS_URL = "https://arm.haglund.dev/api/v2/ids"
_HEADERS = {"Content-Type": "application/json", "User-Agent": "KINO-Desktop/2.0"}
_PLAYBACK_ID = re.compile(r"tt\d{5,12}\Z")
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError)

SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")
SEASON_LABELS = {"WINTER": "Hiver", "SPRING": "Printemps", "SUMMER": "Été", "FALL": "Automne"}
# Formats KINO opens as a series, and the only one it opens as a film. An OVA
# or a special can be either: those are paired when the user opens them.
_SERIES_FORMATS = {"TV", "TV_SHORT", "ONA"}
_WEEK_FORMATS = _SERIES_FORMATS

HOME_TTL = 1800
WEEK_TTL = 1800
_LAST_TTL = 7 * 86400
_MAP_KEY = "anime_playback_map_v1"
_MAP_KEEP = 4000
_FOUND_FOR = 30 * 86400
_MISSING_FOR = 86400
_MAP_LOCK = threading.RLock()

_CARD_FIELDS = """
id title { english romaji } format episodes status averageScore popularity genres
coverImage { large } startDate { year month } nextAiringEpisode { episode airingAt }
"""
_HOME_QUERY = """
query ($season: MediaSeason, $year: Int, $nextSeason: MediaSeason, $nextYear: Int) {
  trending: Page(perPage: 20) { media(type: ANIME, sort: TRENDING_DESC, isAdult: false) { %(card)s } }
  season: Page(perPage: 20) { media(type: ANIME, season: $season, seasonYear: $year, sort: POPULARITY_DESC, isAdult: false) { %(card)s } }
  upcoming: Page(perPage: 20) { media(type: ANIME, season: $nextSeason, seasonYear: $nextYear, sort: POPULARITY_DESC, isAdult: false) { %(card)s } }
  top: Page(perPage: 20) { media(type: ANIME, sort: SCORE_DESC, isAdult: false, format_in: [TV, MOVIE, ONA]) { %(card)s } }
  movies: Page(perPage: 20) { media(type: ANIME, format: MOVIE, sort: POPULARITY_DESC, isAdult: false) { %(card)s } }
}
""" % {"card": _CARD_FIELDS}
_WEEK_QUERY = """
query ($start: Int, $end: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
      airingAt episode
      media { isAdult %(card)s }
    }
  }
}
""" % {"card": _CARD_FIELDS}
_WEEK_PAGES = 8
_PREQUEL_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id title { english romaji } format startDate { year }
    relations { edges { relationType(version: 2) node { id format } } }
  }
}
"""
_PREQUEL_STEPS = 4


def _post(url, payload, timeout=8):
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _anilist(query, variables):
    body = _post(ANILIST_URL, {"query": query, "variables": variables})
    if body.get("errors"):
        raise ValueError("AniList returned an error")
    return body.get("data") or {}


def _anilist_id(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d{0,7}", str(value)):
        raise ValueError("Invalid AniList ID")
    return int(value)


# --- Seasons -----------------------------------------------------------------

def seasons(today):
    """The anime season of a day and the one after: ((name, year), (name, year))."""
    index = (today.month - 1) // 3
    following = (index + 1) % 4
    return ((SEASONS[index], today.year),
            (SEASONS[following], today.year + (1 if following == 0 else 0)))


def season_label(season):
    return f"{SEASON_LABELS[season[0]]} {season[1]}"


# --- AniList work → the fiche KINO plays -------------------------------------

def _playback(row, anilist_format):
    """What KINO opens for one row of the identifier table, or None."""
    if not isinstance(row, dict):
        return None
    imdb = str(row.get("imdb") or "")
    if not _PLAYBACK_ID.fullmatch(imdb):
        return None
    if anilist_format == "MOVIE":
        return {"id": imdb, "type": "movie", "season": None}
    if anilist_format not in _SERIES_FORMATS:
        return None
    season = next((value for value in (row.get("thetvdb-season"), row.get("themoviedb-season"))
                   if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 99), None)
    return {"id": imdb, "type": "series", "season": season}


def _stored_map():
    stored = kino_db.db_cache_get(_MAP_KEY)
    return stored if isinstance(stored, dict) else {}


def _save_map(stored):
    if len(stored) > _MAP_KEEP:
        newest = sorted(stored.items(), key=lambda pair: pair[1].get("at", 0), reverse=True)[:_MAP_KEEP]
        stored = dict(newest)
    kino_db.db_cache_set(_MAP_KEY, stored, ttl_sec=None)


def playback_ids(works, *, now=None):
    """{AniList id: {"id": IMDb, "type", "season"} or None} for (id, format) pairs.

    Answers are kept: a month when a fiche was found, a day when none was, so
    a new title is asked again once the table knows it. A failed request
    keeps nothing and leaves its works unpaired for this time only.
    """
    now = time.time() if now is None else now
    formats = {}
    for identifier, anilist_format in works:
        try:
            formats.setdefault(_anilist_id(identifier), anilist_format)
        except ValueError:
            continue
    with _MAP_LOCK:
        stored = _stored_map()
        result, asked = {}, []
        for identifier in formats:
            entry = stored.get(str(identifier))
            fresh = isinstance(entry, dict) and now - entry.get("at", 0) < (_FOUND_FOR if entry.get("kino") else _MISSING_FOR)
            if fresh:
                result[identifier] = entry.get("kino")
            else:
                asked.append(identifier)
        changed = False
        for start in range(0, len(asked), 100):
            chunk = asked[start:start + 100]
            try:
                rows = _post(IDS_URL, [{"anilist": identifier} for identifier in chunk])
                if not isinstance(rows, list) or len(rows) != len(chunk):
                    raise ValueError("Unexpected identifier table answer")
            except _NETWORK_ERRORS:
                for identifier in chunk:
                    result[identifier] = (stored.get(str(identifier)) or {}).get("kino")
                continue
            for identifier, row in zip(chunk, rows):
                kino = _playback(row, formats[identifier])
                stored[str(identifier)] = {"kino": kino, "at": now}
                result[identifier] = kino
                changed = True
        if changed:
            _save_map(stored)
    return result


def _remember_playback(identifier, kino, now=None):
    with _MAP_LOCK:
        stored = _stored_map()
        stored[str(identifier)] = {"kino": kino, "at": time.time() if now is None else now}
        _save_map(stored)


# --- Cards --------------------------------------------------------------------

def card(media, kino=None):
    """One AniList work as the interface shows it."""
    titles = media.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or "Sans titre"
    romaji = titles.get("romaji") or ""
    date = media.get("startDate") or {}
    upcoming = media.get("nextAiringEpisode") or {}
    return {
        "id": int(media["id"]),
        "title": title,
        "romaji": romaji if romaji and romaji != title else "",
        "format": media.get("format") or "",
        "episodes": media.get("episodes") or 0,
        "status": media.get("status") or "",
        "score": media.get("averageScore") or 0,
        "popularity": media.get("popularity") or 0,
        "genres": [genre for genre in (media.get("genres") or []) if isinstance(genre, str)][:3],
        "poster": (media.get("coverImage") or {}).get("large") or "",
        "year": date.get("year") or 0,
        "month": date.get("month") or 0,
        "next": ({"episode": upcoming.get("episode") or 0, "airing_at": upcoming.get("airingAt") or 0}
                 if upcoming.get("airingAt") else None),
        "kino": kino,
    }


def _cards(medias):
    medias = [media for media in medias if isinstance(media, dict) and media.get("id")]
    paired = playback_ids((media["id"], media.get("format")) for media in medias)
    seen, cards = set(), []
    for media in medias:
        identifier = int(media["id"])
        if identifier not in seen:
            seen.add(identifier)
            cards.append(card(media, paired.get(identifier)))
    return cards


# --- « À la une » -------------------------------------------------------------

def _rows(data, today):
    current, following = seasons(today)
    plan = (
        ("trending", "Tendances", "Ce qui se regarde le plus en ce moment"),
        ("season", f"Cette saison · {season_label(current)}", "Les plus suivis"),
        ("upcoming", f"La saison prochaine · {season_label(following)}", "Les plus attendus"),
        ("top", "Les mieux notés", ""),
        ("movies", "Films", "Les plus populaires"),
    )
    rows = []
    for key, title, note in plan:
        items = _cards(((data.get(key) or {}).get("media")) or [])
        if items:
            rows.append({"key": key, "title": title, "note": note, "items": items})
    return rows


def library_ids():
    """The user's titles (history, Ma Liste) that KINO knows to be anime.

    Read from what is already on this Mac: the fiches paired above and the
    anime catalogue when it has been loaded. Nothing is asked outside.
    """
    known = {entry["kino"]["id"] for entry in _stored_map().values()
             if isinstance(entry, dict) and isinstance(entry.get("kino"), dict)}
    for kind in ("series", "movie"):
        pool = kino_db.db_cache_get(f"anime_engine:pool:{kind}:v3")
        if isinstance(pool, list):
            known.update(str(meta.get("id")) for meta in pool if isinstance(meta, dict))
    mine = []
    for item in [*kino_db.db_get_history(), *kino_db.db_get_watchlist()]:
        identifier = str((item or {}).get("id") or "")
        if identifier and (identifier in known or item.get("is_anime") or item.get("type") == "anime"):
            mine.append(identifier)
    return list(dict.fromkeys(mine))


def get_home(force_refresh=False, *, today=None):
    """The rows of « À la une », with the user's own anime titles."""
    today = today or datetime.date.today()
    current, following = seasons(today)
    cache_key = f"anime_home_v1:{current[0]}:{current[1]}"
    result = None if force_refresh else kino_db.db_cache_get(cache_key)
    if not isinstance(result, dict):
        try:
            data = _anilist(_HOME_QUERY, {"season": current[0], "year": current[1],
                                          "nextSeason": following[0], "nextYear": following[1]})
            result = {"ok": True, "rows": _rows(data, today), "updated_at": int(time.time())}
            if result["rows"]:
                kino_db.db_cache_set(cache_key, result, ttl_sec=HOME_TTL)
                kino_db.db_cache_set("anime_home_last_v1", result, ttl_sec=_LAST_TTL)
        except _NETWORK_ERRORS:
            last = kino_db.db_cache_get("anime_home_last_v1")
            result = ({**last, "stale": True} if isinstance(last, dict)
                      else {"ok": False, "rows": [], "message": "Les rangées anime sont indisponibles pour le moment."})
    return {**result, "library": library_ids()}


# --- The week's planning ------------------------------------------------------

def _week_bounds(now):
    start = datetime.datetime.combine(datetime.datetime.fromtimestamp(now).date(), datetime.time())
    return int(start.timestamp()), int((start + datetime.timedelta(days=7)).timestamp())


def get_week(force_refresh=False, *, now=None):
    """Every episode airing from today to six days ahead, in airing order."""
    now = time.time() if now is None else now
    start, end = _week_bounds(now)
    cache_key = f"anime_week_v1:{start}"
    if not force_refresh:
        cached = kino_db.db_cache_get(cache_key)
        if isinstance(cached, dict):
            return cached
    try:
        airings = []
        for page in range(1, _WEEK_PAGES + 1):
            data = (_anilist(_WEEK_QUERY, {"start": start - 1, "end": end, "page": page}).get("Page")) or {}
            airings.extend(data.get("airingSchedules") or [])
            if not (data.get("pageInfo") or {}).get("hasNextPage"):
                break
        airings = [airing for airing in airings
                   if isinstance(airing.get("media"), dict) and airing["media"].get("id")
                   and airing["media"].get("format") in _WEEK_FORMATS and not airing["media"].get("isAdult")]
        paired = playback_ids((airing["media"]["id"], airing["media"].get("format")) for airing in airings)
        episodes = []
        for airing in airings:
            work = card(airing["media"], paired.get(int(airing["media"]["id"])))
            work.pop("next", None)
            episodes.append({**work, "episode": airing.get("episode") or 1, "airing_at": airing.get("airingAt") or 0})
        episodes.sort(key=lambda episode: (episode["airing_at"], -episode["popularity"]))
        result = {"ok": True, "from": start, "to": end, "episodes": episodes, "updated_at": int(now)}
        kino_db.db_cache_set(cache_key, result, ttl_sec=WEEK_TTL)
        kino_db.db_cache_set("anime_week_last_v1", result, ttl_sec=2 * 86400)
        return result
    except _NETWORK_ERRORS:
        last = kino_db.db_cache_get("anime_week_last_v1")
        if isinstance(last, dict) and last.get("to", 0) > now:
            return {**last, "stale": True}
        return {"ok": False, "episodes": [], "message": "Planning indisponible pour le moment."}


# --- Opening a work the table does not know yet -------------------------------

def open_work(anilist_id):
    """The fiche for a work that came without one: a new season is opened on
    its series (found through its prequels), anything else by title and year."""
    identifier = _anilist_id(anilist_id)
    try:
        media = (_anilist(_PREQUEL_QUERY, {"id": identifier}).get("Media")) or {}
    except _NETWORK_ERRORS:
        media = {}
    titles = media.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or ""
    anilist_format = media.get("format")

    def answer(kino, name=title, poster=""):
        return {"ok": True, "media": {
            "id": kino["id"], "name": name, "type": kino["type"], "is_anime": True,
            "year": str((media.get("startDate") or {}).get("year") or ""), "poster": poster,
            "open_season": kino.get("season") or 0,
        }}

    kino = playback_ids([(identifier, anilist_format)]).get(identifier) if media else None
    if kino:
        return answer(kino)
    # A sequel shares the fiche of the season before it.
    current, steps = media, 0
    while current and anilist_format in _SERIES_FORMATS and steps < _PREQUEL_STEPS:
        steps += 1
        prequel = next((edge.get("node") or {} for edge in ((current.get("relations") or {}).get("edges") or [])
                        if edge.get("relationType") == "PREQUEL" and (edge.get("node") or {}).get("format") in _SERIES_FORMATS), None)
        if not prequel or not prequel.get("id"):
            break
        found = playback_ids([(prequel["id"], prequel.get("format"))]).get(int(prequel["id"]))
        if found:
            kino = {"id": found["id"], "type": "series",
                    "season": found["season"] + steps if found.get("season") else None}
            _remember_playback(identifier, kino)
            return answer(kino)
        try:
            current = (_anilist(_PREQUEL_QUERY, {"id": int(prequel["id"])}).get("Media")) or {}
        except _NETWORK_ERRORS:
            break
    try:
        matched = anime_journey.resolve_work(identifier)
    except ValueError:
        matched = {"ok": False}
    if matched.get("ok"):
        found = matched["media"]
        kino = {"id": found["id"], "type": found["type"], "season": None}
        _remember_playback(identifier, kino)
        return answer(kino, found.get("name") or title, found.get("poster") or "")
    return {"ok": False, "title": title, "message": "Aucune fiche KINO vérifiée pour ce titre."}
