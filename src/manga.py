"""Manga: series from MangaDex and from the user's own files, and where the
user stopped reading.

MangaDex (api.mangadex.org) is a public catalogue of series translated by
volunteer groups; KINO reads its JSON and shows the group with each chapter.
Its pages load straight from MangaDex's image servers. The user's own files
are .cbz archives and folders of images in one folder they choose: a series
is a sub-folder (its chapters are .cbz files or folders of images inside), or
a single .cbz at the top.
"""
import hashlib
import re
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path

import kino_db
from config import cached_get, http_json

API = "https://api.mangadex.org"
# MangaDex's own covers sit behind a check meant for browsers, which an image in
# KINO cannot pass: covers come from AniList, through the AniList number each
# MangaDex series gives (links.al).
ANILIST = "https://graphql.anilist.co"
_COVERS_QUERY = "query ($ids: [Int]) { Page(perPage: 50) { media(id_in: $ids, type: MANGA) { id coverImage { large } } } }"
HEADERS = {"User-Agent": "KINO-Desktop (https://github.com/isleepverywellatnight/kino)"}
# Series shown and read: MangaDex's « safe » and « suggestive » ratings; never its adult ones.
CONTENT = ("safe", "suggestive")
LANGUAGES = ("fr", "en", "es", "es-la", "de", "it", "pt-br", "ru", "ja", "zh")
FOLDER_KEY = "manga_folder_v1"
PROGRESS_KEY = "manga_progress_v1"
PROGRESS_KEEP = 200
IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_LOCK = threading.Lock()


class MangaError(ValueError):
    """A request KINO refuses; the message is shown as is."""


def _id(value, what="Manga"):
    if not _ID.fullmatch(str(value or "")):
        raise MangaError(f"{what} inconnu.")
    return str(value)


def _lang(value):
    return value if value in LANGUAGES else "en"


def _get(path, params, ttl):
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{API}{path}?{query}" if query else f"{API}{path}"
    return cached_get(f"mangadex:{url}", ttl, lambda: http_json(url, headers=HEADERS, timeout=12, retries=1))


# ---------- MangaDex ----------

def _title(attributes, lang):
    titles = attributes.get("title") or {}
    alt = {k: v for item in attributes.get("altTitles") or [] for k, v in item.items()}
    for key in (lang, "en", "ja-ro", "ja"):
        if titles.get(key) or alt.get(key):
            return titles.get(key) or alt.get(key)
    return next(iter(titles.values()), "Sans titre")


def _anilist_id(manga):
    value = str(((manga.get("attributes") or {}).get("links") or {}).get("al") or "")
    return int(value) if value.isdigit() else None


def _covers(mangas):
    """{AniList number: cover address} for these series, in one request."""
    ids = sorted({i for i in map(_anilist_id, mangas) if i})
    if not ids:
        return {}
    try:
        data = cached_get(f"manga-covers:{ids}", 7 * 86400, lambda: http_json(
            ANILIST, method="POST", json_data={"query": _COVERS_QUERY, "variables": {"ids": ids}}, headers=HEADERS, timeout=10, retries=1))
    except Exception:
        return {}
    media = (((data or {}).get("data") or {}).get("Page") or {}).get("media") or []
    return {m["id"]: (m.get("coverImage") or {}).get("large") or "" for m in media if m.get("id")}


def _series(manga, lang, covers=None):
    attributes = manga.get("attributes") or {}
    covers = _covers([manga]) if covers is None else covers
    return {
        "id": manga["id"], "source": "mangadex", "title": _title(attributes, lang), "cover": covers.get(_anilist_id(manga), ""),
        "year": attributes.get("year") or "", "status": attributes.get("status") or "",
        "languages": [l for l in attributes.get("availableTranslatedLanguages") or [] if l],
    }


def _list(params, lang, ttl=3600, content=CONTENT):
    data = _get("/manga", {"limit": 24, "contentRating[]": list(content), "hasAvailableChapters": "true", **params}, ttl)
    found = [m for m in data.get("data") or [] if m.get("id")]
    covers = _covers(found)
    return [_series(m, lang, covers) for m in found]


def search(query, lang="fr"):
    query = str(query or "").strip()[:100]
    if not query:
        return []
    # Most followed first: « frieren » gives the series before its spin-offs.
    return _list({"title": query, "order[followedCount]": "desc"}, _lang(lang), ttl=600)


def popular(lang="fr"):
    """The most followed series that have chapters in the user's language."""
    lang = _lang(lang)
    # The first page of the tab shows « safe » series only; a search finds the others.
    return _list({"order[followedCount]": "desc", "availableTranslatedLanguage[]": [lang]}, lang, ttl=6 * 3600,
                 content=("safe",))


def details(manga_id, lang="fr"):
    manga_id, lang = _id(manga_id), _lang(lang)
    manga = (_get(f"/manga/{manga_id}", {"includes[]": ["author"]}, 3600) or {}).get("data") or {}
    if not manga or (manga.get("attributes") or {}).get("contentRating") not in CONTENT:
        raise MangaError("Ce manga n'est pas disponible dans KINO.")
    attributes = manga["attributes"]
    descriptions = attributes.get("description") or {}
    authors = [(r.get("attributes") or {}).get("name") for r in manga.get("relationships") or [] if r.get("type") == "author"]
    tags = [((t.get("attributes") or {}).get("name") or {}).get("en") for t in attributes.get("tags") or []]
    return {
        **_series(manga, lang),
        "description": descriptions.get(lang) or descriptions.get("en") or "",
        "authors": [a for a in authors if a][:3],
        "tags": [t for t in tags if t][:8],
    }


def chapters(manga_id, lang="fr"):
    """The chapters in one language, in reading order; one copy per chapter number."""
    manga_id, lang = _id(manga_id), _lang(lang)
    found, seen, offset = [], set(), 0
    while offset < 2000:
        page = _get(f"/manga/{manga_id}/feed", {
            "translatedLanguage[]": [lang], "limit": 500, "offset": offset, "order[volume]": "asc",
            "order[chapter]": "asc", "includes[]": "scanlation_group", "contentRating[]": list(CONTENT)}, 1800)
        for chapter in page.get("data") or []:
            attributes = chapter.get("attributes") or {}
            number = attributes.get("chapter") or ""
            if number in seen and number:
                continue
            seen.add(number)
            groups = [(r.get("attributes") or {}).get("name") for r in chapter.get("relationships") or [] if r.get("type") == "scanlation_group"]
            found.append({
                "id": chapter["id"], "number": number, "volume": attributes.get("volume") or "",
                "title": attributes.get("title") or "", "pages": attributes.get("pages") or 0,
                "group": next((g for g in groups if g), ""),
                # Hosted by its publisher (MangaPlus…): it opens there, not in KINO.
                "external": attributes.get("externalUrl") or "",
            })
        offset += 500
        if offset >= int(page.get("total") or 0):
            break
    return found


def pages(chapter_id):
    """The image addresses of a chapter, from the MangaDex server it is given to read from."""
    chapter_id = _id(chapter_id, "Chapitre")
    data = _get(f"/at-home/server/{chapter_id}", {}, 300) or {}
    chapter = data.get("chapter") or {}
    base, digest = data.get("baseUrl"), chapter.get("hash")
    if not base or not digest or not str(base).startswith("https://"):
        raise MangaError("Ce chapitre ne se lit pas dans KINO.")
    return [f"{base}/data/{digest}/{name}" for name in chapter.get("data") or []]


# ---------- The user's own files ----------

def folder():
    path = kino_db.get_config(FOLDER_KEY, "")
    return Path(path) if path and Path(path).is_dir() else None


def set_folder(path):
    path = Path(str(path or "")).expanduser()
    if not path.is_dir():
        raise MangaError("Ce dossier n'existe pas.")
    kino_db.save_config(FOLDER_KEY, str(path.resolve()))
    return local_series()


def _key(relative):
    return hashlib.sha256(str(relative).encode("utf-8")).hexdigest()[:20]


def _natural(text):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(text))]


def _is_chapter(path):
    return (path.is_file() and path.suffix.lower() == ".cbz") or (path.is_dir() and any(
        p.suffix.lower() in IMAGES for p in path.iterdir() if p.is_file()))


def _local_entries(root):
    """{series key: (series path, [chapter paths])} for the chosen folder."""
    entries = {}
    for item in sorted(root.iterdir(), key=lambda p: _natural(p.name)):
        if item.name.startswith("."):
            continue
        if item.is_file() and item.suffix.lower() == ".cbz":
            entries[_key(item.relative_to(root))] = (item, [item])
        elif item.is_dir():
            inside = [p for p in sorted(item.iterdir(), key=lambda p: _natural(p.name)) if not p.name.startswith(".") and _is_chapter(p)]
            if inside:
                entries[_key(item.relative_to(root))] = (item, inside)
            elif _is_chapter(item):
                entries[_key(item.relative_to(root))] = (item, [item])
    return entries


def local_series():
    root = folder()
    if root is None:
        return {"folder": "", "series": []}
    series = [{"id": key, "source": "local", "title": path.stem if path.is_file() else path.name,
               "chapters": len(found), "cover": f"/api/manga/local/page?series={key}&chapter=0&page=0"}
              for key, (path, found) in _local_entries(root).items()]
    return {"folder": str(root), "series": series}


def _local(series_key):
    root = folder()
    entry = _local_entries(root).get(series_key) if root else None
    if entry is None:
        raise MangaError("Série inconnue dans votre dossier.")
    return entry


def local_chapters(series_key):
    path, found = _local(series_key)
    return {"title": path.stem if path.is_file() else path.name,
            "chapters": [{"id": str(index), "number": str(index + 1), "title": chapter.stem if chapter.is_file() else chapter.name,
                          "pages": len(_page_names(chapter))} for index, chapter in enumerate(found)]}


def _page_names(chapter):
    if chapter.is_file():
        with zipfile.ZipFile(chapter) as archive:
            return sorted((n for n in archive.namelist() if n.lower().endswith(IMAGES) and not n.startswith("__MACOSX")), key=_natural)
    return sorted((p.name for p in chapter.iterdir() if p.is_file() and p.suffix.lower() in IMAGES), key=_natural)


def local_page(series_key, chapter_index, page_index):
    """(bytes, content type) of one page; only from the chosen folder."""
    _path, found = _local(series_key)
    try:
        chapter = found[int(chapter_index)]
        name = _page_names(chapter)[int(page_index)]
    except (ValueError, IndexError):
        raise MangaError("Page inconnue.") from None
    suffix = Path(name).suffix.lower()
    kind = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif"}.get(suffix, "image/jpeg")
    if chapter.is_file():
        with zipfile.ZipFile(chapter) as archive:
            return archive.read(name), kind
    return (chapter / name).read_bytes(), kind


# ---------- Where the user stopped ----------

def progress():
    stored = kino_db.get_config(PROGRESS_KEY, {})
    items = list(stored.values()) if isinstance(stored, dict) else []
    return sorted(items, key=lambda item: item.get("updated", 0), reverse=True)


def save_progress(entry):
    source = entry.get("source")
    if source not in ("mangadex", "local"):
        raise MangaError("Source inconnue.")
    series = _id(entry.get("series")) if source == "mangadex" else str(entry.get("series") or "")
    if source == "local" and not re.fullmatch(r"[0-9a-f]{20}", series):
        raise MangaError("Série inconnue.")
    record = {
        "source": source, "series": series, "title": str(entry.get("title") or "")[:200], "cover": str(entry.get("cover") or "")[:500],
        "chapter": str(entry.get("chapter") or "")[:60], "number": str(entry.get("number") or "")[:20], "lang": _lang(entry.get("lang")),
        "page": max(0, int(entry.get("page") or 0)), "pages": max(0, int(entry.get("pages") or 0)), "updated": round(time.time(), 3),
    }
    with _LOCK:
        stored = kino_db.get_config(PROGRESS_KEY, {})
        stored = stored if isinstance(stored, dict) else {}
        stored[f"{source}:{series}"] = record
        if len(stored) > PROGRESS_KEEP:
            for key, _ in sorted(stored.items(), key=lambda kv: kv[1].get("updated", 0))[:-PROGRESS_KEEP]:
                del stored[key]
        kino_db.save_config(PROGRESS_KEY, stored)
    return record


def forget(source, series):
    with _LOCK:
        stored = kino_db.get_config(PROGRESS_KEY, {})
        if isinstance(stored, dict) and stored.pop(f"{source}:{series}", None) is not None:
            kino_db.save_config(PROGRESS_KEY, stored)
