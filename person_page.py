"""Page acteur: a person's films and series, each confirmed by the title's own
credits (cast, direction, writing), with a photo and a short French bio.

The catalogue search matches people's names but gives no credits; each found
title is then checked against its full data (the same, cached, as its fiche).
Titles that the credits do not confirm stay apart, under « Autres résultats »:
the data lists only the first actors, so a smaller role can be missed.
Photo and bio come from Wikipedia, with the check that keeps namesakes out."""
import concurrent.futures
import re
import unicodedata
import urllib.parse

import meta_engine
from config import cached_get, http_json

MAX_PER_KIND = 30
PROFILE_TTL = 30 * 86400
BIO_LIMIT = 420
_SPACES = re.compile(r"\s+")


def clean_name(name):
    return _SPACES.sub(" ", str(name or "")).strip()[:80]


def fold(name):
    """« Zoë  Saldaña » and « zoe saldana » are the same person."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    return _SPACES.sub(" ", re.sub(r"[^\w\s]", " ", text)).strip()


def roles_in(meta, name):
    """Where the name appears in a title's credits: « cast », « director », « writer »."""
    wanted = fold(name)
    return [role for role in ("cast", "director", "writer")
            if any(fold(credit) == wanted for credit in (meta.get(role) or []) if isinstance(credit, str))]


def _year(item):
    found = re.search(r"\d{4}", str(item.get("releaseInfo") or item.get("year") or ""))
    return int(found.group(0)) if found else 0


def _entry(found, meta, kind, roles):
    return {
        "id": found.get("id") or found.get("imdb_id"),
        "imdb_id": found.get("imdb_id") or found.get("id"),
        "type": kind,
        "name": found.get("name") or meta.get("name") or "",
        "year": str(found.get("releaseInfo") or found.get("year") or meta.get("year") or ""),
        "releaseInfo": found.get("releaseInfo") or "",
        "poster": found.get("poster") or meta.get("poster") or "",
        "imdbRating": meta.get("imdbRating") or found.get("imdbRating") or "",
        "roles": roles,
    }


def person_works(name, *, search=None, meta_of=None):
    """{"movies", "series", "others"}: confirmed titles, newest first; the rest apart."""
    search = search or meta_engine.search_cinemeta
    meta_of = meta_of or meta_engine.get_media_meta
    found = []
    for kind in ("movie", "series"):
        seen = set()
        for item in (search(name, kind) or [])[:MAX_PER_KIND]:
            key = item.get("id") or item.get("imdb_id")
            if key and str(key).startswith("tt") and key not in seen:
                seen.add(key)
                found.append((kind, item))

    def check(pair):
        kind, item = pair
        try:
            meta = meta_of(item.get("id") or item.get("imdb_id"), kind) or {}
        except Exception:
            meta = {}
        return _entry(item, meta, kind, roles_in(meta, name))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(check, found))
    newest = lambda entry: -_year(entry)
    confirmed = [entry for entry in entries if entry["roles"]]
    return {
        "movies": sorted((e for e in confirmed if e["type"] == "movie"), key=newest),
        "series": sorted((e for e in confirmed if e["type"] == "series"), key=newest),
        "others": sorted((e for e in entries if not e["roles"]), key=newest),
    }


def _short_bio(extract):
    """The first sentences of a Wikipedia summary, at most BIO_LIMIT characters."""
    text = _SPACES.sub(" ", str(extract or "")).strip()
    if len(text) <= BIO_LIMIT:
        return text
    cut = text[:BIO_LIMIT]
    end = max(cut.rfind(". "), cut.rfind(". "))
    return cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…"


def _wikimedia(url):
    parts = urllib.parse.urlsplit(str(url or ""))
    return parts.scheme == "https" and (parts.hostname or "").endswith(".wikimedia.org")


def person_profile(name):
    """{"photo", "description", "bio"} in French when Wikipedia has a page on this person
    of film or TV; empty texts otherwise. None on network trouble (not kept)."""
    name = clean_name(name)

    def fetch():
        summary = meta_engine.screen_person_summary(name)
        if summary is None:
            return None
        if not summary:
            return {"photo": "", "description": "", "bio": ""}
        photo = str((summary.get("thumbnail") or {}).get("source") or "")
        profile = {"photo": photo if _wikimedia(photo) else "", "description": "", "bio": ""}
        en_title = summary.get("title") or name
        try:
            links = http_json("https://en.wikipedia.org/w/api.php", method="POST", headers=meta_engine.WIKIPEDIA_HEADERS,
                              data={"action": "query", "prop": "langlinks", "lllang": "fr", "titles": en_title,
                                    "format": "json", "formatversion": "2"}, timeout=6, retries=1)
            pages = (links.get("query") or {}).get("pages") or []
            fr_title = next((link.get("title") for page in pages for link in (page.get("langlinks") or []) if link.get("title")), "")
            if fr_title:
                fr = http_json("https://fr.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(fr_title.replace(" ", "_"), safe=""),
                               headers=meta_engine.WIKIPEDIA_HEADERS, timeout=6, retries=1)
                if fr.get("type") == "standard":
                    profile["description"] = str(fr.get("description") or "")[:120]
                    profile["bio"] = _short_bio(fr.get("extract"))
        except RuntimeError:
            pass  # the photo stands without a French text
        return profile

    return cached_get(f"person_profile_v1:{fold(name)}", PROFILE_TTL, fetch)


def page(name):
    """Everything the page shows, gathered side by side."""
    name = clean_name(name)
    if not name:
        raise ValueError("Nom manquant.")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        works = pool.submit(person_works, name)
        profile = pool.submit(person_profile, name)
        result = {"name": name, **(profile.result() or {"photo": "", "description": "", "bio": ""})}
        result.update(works.result())
    return result
