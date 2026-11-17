"""Manga: the catalogue, the chapters of a series gathered from several sites,
the user's own files, and where the user stopped reading.

The catalogue is AniList's: every series, the ones being published now, their
covers and authors. The chapters of one series come from the sites in
manga_sources.py; for each chapter number KINO keeps one copy, in the reading
language when a site has it, else in English. The user's own files are .cbz
archives and folders of images in one folder they choose: a series is a
sub-folder (its chapters are .cbz files or folders of images inside), or a
single .cbz at the top. Volumes downloaded by torrent go there too.
"""
import hashlib
import re
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path

import kino_db
import manga_sources
from config import cached_get, http_json

API = "https://api.mangadex.org"
ANILIST = "https://graphql.anilist.co"
HEADERS = {"User-Agent": "KINO-Desktop (https://github.com/isleepverywellatnight/kino)"}
# Series shown and read: never AniList's adult ones, nor MangaDex's adult ratings.
CONTENT = ("safe", "suggestive")
LANGUAGES = ("fr", "en", "es", "es-la", "de", "it", "pt-br", "ru", "ja", "zh")
FOLDER_KEY = "manga_folder_v1"
PROGRESS_KEY = "manga_progress_v1"
PROGRESS_KEEP = 200
IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_LOCK = threading.Lock()
_FIELDS = ("id title { romaji english native } synonyms coverImage { extraLarge large } startDate { year } "
           "status chapters volumes countryOfOrigin format genres")
_ROWS = {
    # « Tendances »: what is being read now, among the series still coming out.
    "trending": "sort: TRENDING_DESC, status: RELEASING",
    "popular": "sort: POPULARITY_DESC",
    "webtoon": "sort: TRENDING_DESC, countryOfOrigin: KR",
}


class MangaError(ValueError):
    """A request KINO refuses; the message is shown as is."""


def _id(value, what="Manga"):
    if not _ID.fullmatch(str(value or "")):
        raise MangaError(f"{what} inconnu.")
    return str(value)


def _anilist_number(value):
    if not re.fullmatch(r"\d{1,9}", str(value or "")):
        raise MangaError("Manga inconnu.")
    return int(value)


def _lang(value):
    return value if value in LANGUAGES else "en"


def _get(path, params, ttl):
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{API}{path}?{query}" if query else f"{API}{path}"
    return cached_get(f"mangadex:{url}", ttl, lambda: http_json(url, headers=HEADERS, timeout=12, retries=1))


def _anilist(query, variables, ttl):
    key = f"anilist-manga:{query}:{sorted(variables.items())}"
    data = cached_get(key, ttl, lambda: http_json(ANILIST, method="POST", json_data={"query": query, "variables": variables},
                                                  headers=HEADERS, timeout=12, retries=1))
    return (data or {}).get("data") or {}


# ---------- The catalogue (AniList) ----------

def _titles(media):
    title = media.get("title") or {}
    latin = [t for t in media.get("synonyms") or [] if t and t.isascii()]
    return [t for t in (title.get("english"), title.get("romaji"), *latin) if t]


def _series(media):
    title = media.get("title") or {}
    cover = media.get("coverImage") or {}
    return {
        "id": str(media["id"]), "source": "anilist", "title": title.get("english") or title.get("romaji") or "Sans titre",
        "cover": cover.get("extraLarge") or cover.get("large") or "", "year": (media.get("startDate") or {}).get("year") or "",
        "status": (media.get("status") or "").lower(), "kind": media.get("countryOfOrigin") or "",
    }


def search(query, lang="fr"):
    query = str(query or "").strip()[:100]
    if not query:
        return []
    data = _anilist(f"query ($q: String) {{ Page(perPage: 30) {{ media(search: $q, type: MANGA, isAdult: false, "
                    f"sort: [SEARCH_MATCH, POPULARITY_DESC]) {{ {_FIELDS} }} }} }}", {"q": query}, 600)
    return [_series(m) for m in (data.get("Page") or {}).get("media") or []]


def row(name):
    """One row of the tab: « trending », « popular » or « webtoon »."""
    if name not in _ROWS:
        raise MangaError("Rangée inconnue.")
    data = _anilist(f"{{ Page(perPage: 30) {{ media(type: MANGA, isAdult: false, {_ROWS[name]}) {{ {_FIELDS} }} }} }}", {}, 6 * 3600)
    return [_series(m) for m in (data.get("Page") or {}).get("media") or []]


def popular(lang="fr"):
    return row("trending")


def _media(anilist_id):
    data = _anilist(f"query ($id: Int) {{ Media(id: $id, type: MANGA) {{ {_FIELDS} isAdult description(asHtml: false) "
                    "staff(perPage: 6) { edges { role node { name { full } } } } } }", {"id": anilist_id}, 6 * 3600)
    media = data.get("Media")
    if not media or media.get("isAdult"):
        raise MangaError("Ce manga n'est pas disponible dans KINO.")
    return media


def _from_mangadex(manga_id):
    """A series opened before the catalogue moved to AniList: its AniList number."""
    manga = (_get(f"/manga/{_id(manga_id)}", {}, 86400) or {}).get("data") or {}
    number = str(((manga.get("attributes") or {}).get("links") or {}).get("al") or "")
    if not number.isdigit():
        raise MangaError("Ce manga n'est plus dans le catalogue de KINO.")
    return int(number)


def details(series_id, lang="fr"):
    series_id = str(series_id or "")
    anilist_id = _from_mangadex(series_id) if _ID.fullmatch(series_id) else _anilist_number(series_id)
    media, lang = _media(anilist_id), _lang(lang)
    authors = [((e.get("node") or {}).get("name") or {}).get("full") for e in (media.get("staff") or {}).get("edges") or []
               if re.search(r"story|art", e.get("role") or "", re.I)]
    description = re.sub(r"<[^>]+>|\(Source:[^)]*\)", "", media.get("description") or "").strip()
    if lang != "en":
        found = _mangadex_series(anilist_id, _titles(media))
        description = ((found or {}).get("attributes") or {}).get("description", {}).get(lang) or description
    return {**_series(media), "description": description, "authors": list(dict.fromkeys(a for a in authors if a))[:3],
            "tags": (media.get("genres") or [])[:6], "chapters_total": media.get("chapters") or 0,
            "volumes_total": media.get("volumes") or 0, "titles": _titles(media)}


# ---------- The chapters, from every site ----------

def _mangadex_series(anilist_id, titles):
    try:
        return manga_sources.mangadex_match(anilist_id, titles, _get)
    except Exception:
        return None


def _mangadex_chapters(manga_id, lang):
    """The readable chapters of a MangaDex series in one language, one copy per number."""
    found, seen, offset = [], set(), 0
    while offset < 3000:
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
                "id": f"md:{chapter['id']}", "number": number, "volume": attributes.get("volume") or "",
                "title": attributes.get("title") or "", "pages": attributes.get("pages") or 0, "lang": lang,
                "group": next((g for g in groups if g), ""), "source": "MangaDex",
                # Hosted by its publisher (MangaPlus…): it opens there, not in KINO.
                "external": attributes.get("externalUrl") or "",
            })
        offset += 500
        if offset >= int(page.get("total") or 0):
            break
    return found


def _weebcentral_chapters(titles):
    def fetch():
        series_id = manga_sources.weebcentral_match(titles)
        return manga_sources.weebcentral_chapters(series_id) if series_id else []
    return [{**c, "title": "", "volume": "", "pages": 0, "lang": "en", "group": "", "source": "WeebCentral", "external": ""}
            for c in manga_sources.cached(f"wc:{titles[:3]}", 1800, fetch)]


def chapters(series_id, lang="fr"):
    """Every chapter once, in reading order: in the reading language when a site has it, else in English."""
    lang = _lang(lang)
    series_id = str(series_id or "")
    anilist_id = _from_mangadex(series_id) if _ID.fullmatch(series_id) else _anilist_number(series_id)
    titles = _titles(_media(anilist_id))
    md = _mangadex_series(anilist_id, titles)
    lists, failed = {}, []
    for name, read in (("wc", lambda: _weebcentral_chapters(titles)),
                       ("md-en", lambda: _mangadex_chapters(md["id"], "en") if md else []),
                       ("md-lang", lambda: _mangadex_chapters(md["id"], lang) if md and lang != "en" else [])):
        try:
            lists[name] = read()
        except Exception:
            lists[name], failed = [], failed + [name]
    if failed and not any(lists.values()):
        raise OSError("Les sources de chapitres ne répondent pas.")
    order = ["md-lang", "wc", "md-en"] if lang != "en" else ["wc", "md-en"]
    chosen = {}
    for name in order:
        for chapter in lists[name]:
            key = manga_sources.number_key(chapter["number"]) or f"x:{chapter['id']}"
            current = chosen.get(key)
            # A chapter hosted elsewhere gives way to one KINO can show.
            if current is None or (current["external"] and not chapter["external"]):
                chosen[key] = chapter
    return sorted(chosen.values(), key=lambda c: (float(manga_sources.number_key(c["number"]) or 1e9), c["id"]))


def pages(chapter_id):
    """The image addresses of a chapter, from the site it comes from."""
    chapter_id = str(chapter_id or "")
    if chapter_id.startswith("wc:"):
        try:
            found = manga_sources.weebcentral_pages(chapter_id[3:])
        except manga_sources.SourceError as error:
            raise MangaError(str(error)) from None
        if not found:
            raise MangaError("Ce chapitre ne se lit pas dans KINO.")
        return found
    chapter_id = _id(chapter_id[3:] if chapter_id.startswith("md:") else chapter_id, "Chapitre")
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
    if source not in ("anilist", "mangadex", "local"):
        raise MangaError("Source inconnue.")
    series = str(entry.get("series") or "")
    if source == "mangadex":
        series = _id(series)
    elif source == "anilist":
        series = str(_anilist_number(series))
    elif not re.fullmatch(r"[0-9a-f]{20}", series):
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
