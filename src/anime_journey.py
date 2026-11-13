"""A cautious anime franchise path backed by AniList relations and KINO playback IDs."""

import json
import re
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import kino_db


ANILIST_URL = "https://graphql.anilist.co"
_PLAYBACK_ID = re.compile(r"tt\d{5,12}\Z")
_RELATIONS = {"SEQUEL", "PREQUEL", "SIDE_STORY", "PARENT", "SPIN_OFF", "ALTERNATIVE", "SUMMARY"}
_FORMATS = {"TV", "TV_SHORT", "ONA", "OVA", "MOVIE", "SPECIAL"}
_ORDER_KEY = "anime_journey_order_v1"
_ORDER_LOCK = threading.RLock()
_SEARCH_QUERY = """
query ($search: String) {
  Page(page: 1, perPage: 8) {
    media(search: $search, type: ANIME) {
      id title { english romaji native } synonyms format startDate { year month day }
    }
  }
}
"""
_WORK_FIELDS = """
id title { english romaji native } synonyms format episodes
startDate { year month day }
"""
_WORK_QUERY = "query ($id: Int) { Media(id: $id, type: ANIME) { " + _WORK_FIELDS + " relations { edges { relationType(version: 2) node { " + _WORK_FIELDS + " } } } } }"
_IDENTITY_QUERY = "query ($id: Int) { Media(id: $id, type: ANIME) { " + _WORK_FIELDS + " } }"


def _normalize(value):
    plain = unicodedata.normalize("NFKD", str(value or "").casefold())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def _year(value):
    match = re.search(r"\b(19|20)\d{2}\b", str(value or ""))
    return int(match.group()) if match else None


def _titles(media):
    titles = media.get("title") or {}
    return [titles.get("english"), titles.get("romaji"), titles.get("native"), *(media.get("synonyms") or [])]


def _anilist(query, variables):
    request = urllib.request.Request(
        ANILIST_URL,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "KINO-Desktop/2.0"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        body = json.load(response)
    if body.get("errors"):
        raise ValueError("AniList returned an error")
    return body.get("data") or {}


def _candidate(title, year, media_type):
    data = _anilist(_SEARCH_QUERY, {"search": title[:120]})
    requested = _normalize(title)
    expected_format = {"MOVIE"} if media_type == "movie" else _FORMATS - {"MOVIE"}
    matches = []
    for media in (data.get("Page") or {}).get("media") or []:
        if media.get("format") not in expected_format:
            continue
        if requested not in {_normalize(value) for value in _titles(media)}:
            continue
        media_year = (media.get("startDate") or {}).get("year")
        if year and media_year != year:
            continue
        matches.append(media)
    return matches[0] if len({media.get("id") for media in matches}) == 1 else None


def _work(media, relation, playback=None):
    date = media.get("startDate") or {}
    year = date.get("year")
    title = (media.get("title") or {}).get("english") or (media.get("title") or {}).get("romaji") or "Œuvre sans titre"
    return {
        "id": int(media["id"]),
        "title": title,
        "format": media.get("format") or "UNKNOWN",
        "relation": relation,
        "year": year,
        "release": "-".join(str(date.get(part) or 0).zfill(width) for part, width in (("year", 4), ("month", 2), ("day", 2))) if year else None,
        "episodes": media.get("episodes"),
        "playback": playback,
        "source_url": f"https://anilist.co/anime/{int(media['id'])}",
    }


def _remember_work(media):
    if media.get("id"):
        kino_db.db_cache_set(f"anime_journey_identity_v1:{int(media['id'])}", media, ttl_sec=86400)


def _release_key(work):
    return (work["release"] or "9999-99-99", work["id"])


def _orders():
    stored = kino_db.get_config(_ORDER_KEY, {})
    return stored if isinstance(stored, dict) else {}


def _with_order(result):
    if not result.get("ok"):
        return result
    order = _orders().get(str(result["saga_id"]))
    if not isinstance(order, list):
        return result
    positions = {identifier: index for index, identifier in enumerate(order) if isinstance(identifier, int)}
    works = result["works"]
    if not any(work["id"] in positions for work in works):
        return result
    customized = dict(result)
    customized["works"] = sorted(works, key=lambda work: (positions.get(work["id"], len(positions)), _release_key(work)))
    customized["order_label"] = "Ordre personnalisé"
    return customized


def _saga_id(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d{0,7}", str(value)):
        raise ValueError("Invalid saga ID")
    return int(value)


def save_order(imdb_id, media_type, saga_id, identifiers):
    saga_id = _saga_id(saga_id)
    if not _PLAYBACK_ID.fullmatch(str(imdb_id)) or media_type not in {"movie", "series"}:
        raise ValueError("Invalid playback identity")
    cached = kino_db.db_cache_get(f"anime_journey_v1:{media_type}:{imdb_id}")
    if not isinstance(cached, dict) or cached.get("saga_id") != saga_id:
        raise ValueError("Journey is not loaded")
    expected = [work["id"] for work in cached.get("works") or []]
    if (not isinstance(identifiers, list) or len(identifiers) != len(expected)
            or any(isinstance(identifier, bool) or not isinstance(identifier, int) for identifier in identifiers)
            or set(identifiers) != set(expected)):
        raise ValueError("Order must contain each work exactly once")
    with _ORDER_LOCK:
        orders = _orders()
        orders[str(saga_id)] = identifiers
        if not kino_db.save_config(_ORDER_KEY, dict(list(orders.items())[-100:])):
            raise RuntimeError("Could not save journey order")
    return _with_order(cached)


def reset_order(saga_id):
    saga_id = _saga_id(saga_id)
    with _ORDER_LOCK:
        orders = _orders()
        orders.pop(str(saga_id), None)
        if not kino_db.save_config(_ORDER_KEY, orders):
            raise RuntimeError("Could not clear journey order")


def cached_journey(imdb_id, media_type):
    if not _PLAYBACK_ID.fullmatch(str(imdb_id)) or media_type not in {"movie", "series"}:
        raise ValueError("Invalid playback identity")
    cached = kino_db.db_cache_get(f"anime_journey_v1:{media_type}:{imdb_id}")
    if not isinstance(cached, dict) or not cached.get("ok"):
        raise ValueError("Journey is not loaded")
    return _with_order(cached)


def resolve_member(imdb_id, media_type, anilist_id):
    result = cached_journey(imdb_id, media_type)
    if isinstance(anilist_id, bool) or not re.fullmatch(r"\d{1,8}", str(anilist_id)):
        raise ValueError("Invalid AniList ID")
    work = next((work for work in result["works"] if work["id"] == int(anilist_id)), None)
    if work is None:
        raise ValueError("Work is not in this journey")
    if work.get("playback"):
        return {"ok": True, "media": {"id": work["playback"]["id"], "name": work["title"],
                                        "type": work["playback"]["type"], "is_anime": True,
                                        "year": str(work.get("year") or ""), "poster": ""}}
    return resolve_work(work["id"], current_playback_id=imdb_id)


def get_journey(imdb_id, title, release_year, media_type, *, force_refresh=False):
    """Return a release-order path; never reuse the clicked work's IMDb ID for relatives."""
    playback_id = str(imdb_id or "")
    media_type = str(media_type or "")
    if not _PLAYBACK_ID.fullmatch(playback_id) or media_type not in {"movie", "series"}:
        raise ValueError("Invalid playback identity")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
        raise ValueError("Invalid title")
    year = _year(release_year)
    cache_key = f"anime_journey_v1:{media_type}:{playback_id}"
    if not force_refresh:
        cached = kino_db.db_cache_get(cache_key)
        if isinstance(cached, dict):
            return _with_order(cached)
    try:
        candidate = _candidate(title.strip(), year, media_type)
        if not candidate:
            return {"ok": False, "reason": "unmatched", "message": "Aucune œuvre AniList ne correspond avec certitude à ce titre et cette année."}
        root = (_anilist(_WORK_QUERY, {"id": int(candidate["id"])}).get("Media") or {})
        if not root:
            return {"ok": False, "reason": "unavailable", "message": "Relations AniList indisponibles."}
        _remember_work(root)
        root_id = int(root["id"])
        works = {root_id: _work(root, "SELF", {"id": playback_id, "type": media_type})}
        mainline = {root_id}
        queue = [root_id]
        expanded = set()
        partial = False
        relations_known = False
        while queue and len(expanded) < 8 and len(works) < 32:
            current_id = queue.pop(0)
            if current_id in expanded:
                continue
            expanded.add(current_id)
            try:
                current = root if current_id == root_id else (_anilist(_WORK_QUERY, {"id": current_id}).get("Media") or {})
            except (urllib.error.URLError, TimeoutError, ValueError):
                partial = True
                continue
            edges = (current.get("relations") or {}).get("edges") or []
            relations_known = relations_known or bool(edges)
            for edge in edges:
                node = edge.get("node") or {}
                relation = edge.get("relationType")
                if relation not in _RELATIONS or node.get("format") not in _FORMATS or not node.get("id"):
                    continue
                identifier = int(node["id"])
                _remember_work(node)
                if identifier != root_id and identifier not in works:
                    works[identifier] = _work(node, relation)
                if relation in {"SEQUEL", "PREQUEL"} or (relation == "PARENT" and current_id == root_id):
                    mainline.add(identifier)
                    if identifier not in expanded and identifier not in queue:
                        queue.append(identifier)
                if len(works) >= 32:
                    partial = True
                    break
        partial = partial or bool(queue)
        saga_id = min(mainline, key=lambda identifier: _release_key(works[identifier]))
        result = {
            "ok": True,
            "saga_id": saga_id,
            "order_label": "Ordre de sortie",
            "source": "AniList",
            "relations_known": relations_known,
            "partial": partial,
            "works": sorted(works.values(), key=_release_key),
        }
        kino_db.db_cache_set(cache_key, result, ttl_sec=86400)
        return _with_order(result)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError) as error:
        return {"ok": False, "reason": "unavailable", "message": "Parcours indisponible pour le moment."}


def _cinemeta_results(title, media_type):
    url = f"https://v3-cinemeta.strem.io/catalog/{media_type}/top/search={urllib.parse.quote(title)}.json"
    request = urllib.request.Request(url, headers={"User-Agent": "KINO-Desktop/2.0"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return (json.load(response).get("metas") or [])[:20]


def resolve_work(anilist_id, *, current_playback_id=None):
    """Resolve one related work only when title, year and type uniquely agree."""
    if isinstance(anilist_id, bool) or not re.fullmatch(r"\d{1,8}", str(anilist_id)):
        raise ValueError("Invalid AniList ID")
    if current_playback_id is not None and not _PLAYBACK_ID.fullmatch(str(current_playback_id)):
        raise ValueError("Invalid current playback ID")
    match_key = f"anime_journey_match_v1:{int(anilist_id)}"
    cached = kino_db.db_cache_get(match_key)
    if isinstance(cached, dict):
        return _check_current_playback(cached, current_playback_id)
    try:
        media = kino_db.db_cache_get(f"anime_journey_identity_v1:{int(anilist_id)}")
        if not isinstance(media, dict):
            media = (_anilist(_IDENTITY_QUERY, {"id": int(anilist_id)}).get("Media") or {})
        if not media or media.get("format") not in _FORMATS:
            return {"ok": False, "reason": "unmatched", "message": "Œuvre indisponible dans KINO."}
        search_titles = list(dict.fromkeys(title for title in _titles(media) if isinstance(title, str) and title.strip()))[:3]
        acceptable_titles = {_normalize(title) for title in _titles(media)}
        expected_year = (media.get("startDate") or {}).get("year")
        formats = ["movie"] if media["format"] == "MOVIE" else (["series"] if media["format"] in {"TV", "TV_SHORT", "ONA"} else ["movie", "series"])
        matches = {}
        for media_type in formats:
            for title in search_titles:
                for candidate in _cinemeta_results(title, media_type):
                    identifier = str(candidate.get("id") or "")
                    if not _PLAYBACK_ID.fullmatch(identifier) or candidate.get("type") != media_type:
                        continue
                    if _normalize(candidate.get("name")) not in acceptable_titles:
                        continue
                    if expected_year and _year(candidate.get("releaseInfo") or candidate.get("year")) != expected_year:
                        continue
                    matches[identifier] = candidate
                if matches:
                    break
        if len(matches) != 1:
            result = {"ok": False, "reason": "ambiguous" if matches else "unmatched",
                      "message": "Correspondance de lecture non vérifiée pour cette œuvre."}
            kino_db.db_cache_set(match_key, result, ttl_sec=3600)
            return result
        candidate = next(iter(matches.values()))
        result = {"ok": True, "media": {
            "id": candidate["id"], "name": candidate["name"], "type": candidate["type"],
            "is_anime": True, "year": str(candidate.get("releaseInfo") or candidate.get("year") or ""),
            "poster": candidate.get("poster") or "",
        }}
        kino_db.db_cache_set(match_key, result, ttl_sec=86400)
        return _check_current_playback(result, current_playback_id)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError):
        return {"ok": False, "reason": "unavailable", "message": "Vérification de lecture indisponible."}


def _check_current_playback(result, current_playback_id):
    if result.get("ok") and current_playback_id and result["media"]["id"] == current_playback_id:
        return {"ok": False, "reason": "shared_series",
                "message": "Cette œuvre partage une fiche avec la série ; sa saison exacte reste à vérifier."}
    return result
