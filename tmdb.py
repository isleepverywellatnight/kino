"""Titles close to a film or a series, from TMDB's recommendations.

KINO's own « Si vous avez aimé » only matched genres; Trakt refuses KINO's
built-in key (403) and Letterboxd blocks robots (tried on 2026-11-12). TMDB
needs a free personal key, pasted by the user in Paramètres → Connexions.

KINO speaks IMDb ids: the title is found on TMDB by its IMDb id, its
recommendations are read (then « similar » if they are few), and each one is
brought back to its IMDb id (cached for months: an id never changes). The
network and the cache are passed in, so the tests run without them.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://api.themoviedb.org/3"
POSTER = "https://image.tmdb.org/t/p/w342"
LIMIT = 12
MIN_VOTES = 20
RELATED_TTL = 7 * 86400
IDS_TTL = 90 * 86400


class TmdbError(Exception):
    pass


def key_headers_params(key):
    """A « Jeton d'accès en lecture » (v4, a JWT) goes in a header; an API key (v3) in the address."""
    key = str(key or "").strip()
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}"}, {}
    return {}, {"api_key": key}


def http_get(path, key, params=None, timeout=6):
    headers, auth = key_headers_params(key)
    query = urllib.parse.urlencode({**(params or {}), **auth})
    request = urllib.request.Request(f"{API}{path}?{query}", headers={"Accept": "application/json", "User-Agent": "KINO", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise TmdbError("Clé TMDB refusée." if error.code == 401 else f"TMDB a répondu {error.code}.") from None


def check_key(key, get=http_get):
    """True when TMDB accepts the key."""
    if not str(key or "").strip():
        return False
    try:
        get("/configuration", key)
        return True
    except TmdbError:
        return False


def _kind(media_type):
    return "tv" if media_type in ("series", "anime", "tv") else "movie"


def tmdb_id(imdb_id, media_type, key, get=http_get):
    """TMDB's id of an IMDb title (the series or the film, as asked)."""
    found = get(f"/find/{imdb_id}", key, {"external_source": "imdb_id"})
    wanted, other = ("tv_results", "movie_results") if _kind(media_type) == "tv" else ("movie_results", "tv_results")
    for group in (wanted, other):
        for item in found.get(group) or []:
            if item.get("id"):
                return int(item["id"]), "tv" if group == "tv_results" else "movie"
    return None, None


def _year(item):
    text = str(item.get("release_date") or item.get("first_air_date") or "")[:4]
    return text if text.isdigit() else ""


def related(imdb_id, media_type, key, get=http_get, cache=None, limit=LIMIT):
    """Up to `limit` titles close to this one, as KINO's cards: IMDb id, name, type, year, poster, rating."""
    if not re.fullmatch(r"tt\d{1,12}", str(imdb_id or "")) or not str(key or "").strip():
        return []
    cache = cache or (lambda _key, _ttl, fetch: fetch())

    def fetch():
        source_id, kind = tmdb_id(imdb_id, media_type, key, get)
        if not source_id:
            return []
        params = {"language": "en-US", "page": 1}
        results = list(get(f"/{kind}/{source_id}/recommendations", key, params).get("results") or [])
        if len(results) < 8:
            seen = {r.get("id") for r in results}
            results += [r for r in get(f"/{kind}/{source_id}/similar", key, params).get("results") or [] if r.get("id") not in seen]
        results = [r for r in results if r.get("id") and not r.get("adult")][: limit + 6]

        def imdb_of(result):
            rid = int(result["id"])
            return cache(f"tmdb_imdb:{kind}:{rid}", IDS_TTL,
                         lambda: (get(f"/{kind}/{rid}/external_ids", key) or {}).get("imdb_id") or "")

        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(imdb_of, results))
        items = []
        for result, rid in zip(results, ids):
            if not rid or rid == imdb_id or any(item["id"] == rid for item in items):
                continue
            votes = int(result.get("vote_count") or 0)
            rating = float(result.get("vote_average") or 0)
            items.append({
                "id": rid,
                "name": result.get("title") or result.get("name") or "",
                "type": "series" if kind == "tv" else "movie",
                "year": _year(result),
                "poster": f"{POSTER}{result['poster_path']}" if result.get("poster_path") else f"https://images.metahub.space/poster/medium/{rid}/img",
                "imdbRating": f"{rating:.1f}" if votes >= MIN_VOTES and rating else "",
            })
            if len(items) >= limit:
                break
        return items

    return cache(f"tmdb_related_v1:{_kind(media_type)}:{imdb_id}", RELATED_TTL, fetch)
