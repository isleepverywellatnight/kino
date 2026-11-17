"""Where the chapters and volumes of a manga come from.

The catalogue is AniList's (manga.py). For one series, KINO gathers its
chapters from several sites, each found by the series' titles:

- WeebCentral: English scans of nearly every series, usually within days of
  the Japanese release.
- MangaDex: volunteer translations in many languages; the series is the one
  whose AniList number matches.
- Nyaa: whole volumes (mostly official digital releases, as CBZ) shared by
  torrent, read through the P2P engine (manga_volumes.py).

A site that answers with a browser check is left out: KINO never tries to pass one.
"""
import difflib
import html
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from config import cached_get, get_ssl_context

BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
WEEBCENTRAL = "https://weebcentral.com"
NYAA_RSS = "https://nyaa.si/?page=rss"
NYAA_TRACKERS = ("http://nyaa.tracker.wf:7777/announce", "udp://open.stealth.si:80/announce",
                 "udp://tracker.opentrackr.org:1337/announce", "udp://exodus.desync.com:6969/announce")
_WC_ID = re.compile(r"[0-9A-Z]{26}")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class SourceError(RuntimeError):
    """A site answered with something KINO cannot read."""


def fetch_text(url, headers=None, timeout=15):
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout, context=get_ssl_context()) as response:
        return response.read().decode("utf-8", "replace")


def normalize(title):
    """« Kaiju No. 8 » and « kaiju no 8 » are the same title."""
    text = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def number_key(number):
    """« 12 », « 12.0 » and « 012 » are one chapter; None when there is no number."""
    try:
        return f"{float(number):g}"
    except (TypeError, ValueError):
        return None


# ---------- WeebCentral ----------

def weebcentral_search(query):
    """[(series id, name)] for a title, best match first."""
    params = urllib.parse.urlencode({"text": query, "sort": "Best Match", "order": "Descending", "official": "Any",
                                     "anime": "Any", "adult": "Any", "display_mode": "Full Display"})
    page = fetch_text(f"{WEEBCENTRAL}/search/data?{params}", {"HX-Request": "true"})
    found, seen = [], set()
    for series_id, slug in re.findall(r"https://weebcentral\.com/series/([0-9A-Z]{26})/([^\"'<> ]+)", page):
        if series_id not in seen:
            seen.add(series_id)
            alt = re.search(rf'alt="([^"]+?) cover"[^>]*>\s*</picture>', page[page.find(series_id):][:2000])
            found.append((series_id, html.unescape(alt.group(1)) if alt else urllib.parse.unquote(slug).replace("-", " ")))
    return found


def weebcentral_match(titles):
    """The WeebCentral series for these titles (the AniList ones), or None."""
    wanted = {normalize(t) for t in titles if normalize(t)}
    best = (0.0, None)
    for title in list(dict.fromkeys(t for t in titles if t))[:3]:
        for series_id, name in weebcentral_search(title)[:6]:
            key = normalize(name)
            if key in wanted:
                return series_id
            score = max(difflib.SequenceMatcher(None, key, w).ratio() for w in wanted)
            if score > best[0]:
                best = (score, series_id)
    return best[1] if best[0] >= 0.85 else None


def weebcentral_chapters(series_id):
    """[{id, number, label, date}] of a WeebCentral series, in reading order."""
    if not _WC_ID.fullmatch(str(series_id or "")):
        raise SourceError("Série WeebCentral inconnue.")
    page = fetch_text(f"{WEEBCENTRAL}/series/{series_id}/full-chapter-list")
    found = []
    for block in re.split(r'(?=<a href="(?:https://weebcentral\.com)?/chapters/)', page)[1:]:
        chapter = re.match(r'<a href="(?:https://weebcentral\.com)?/chapters/([0-9A-Z]{26})"', block)
        label = re.search(r'<span class="">([^<]+)</span>', block)
        if not chapter or not label:
            continue
        numbers = _NUMBER.findall(label.group(1))
        date = re.search(r'datetime="([^"]+)"', block)
        found.append({"id": f"wc:{chapter.group(1)}", "number": numbers[-1] if numbers else "",
                      "label": html.unescape(label.group(1)).strip(), "date": date.group(1)[:10] if date else ""})
    found.reverse()
    return found


def weebcentral_pages(chapter_id):
    if not _WC_ID.fullmatch(str(chapter_id or "")):
        raise SourceError("Chapitre WeebCentral inconnu.")
    page = fetch_text(f"{WEEBCENTRAL}/chapters/{chapter_id}/images?is_prev=False&current_page=1&reading_style=long_strip")
    urls = re.findall(r'<img[^>]+src="(https://[^"]+\.(?:png|jpe?g|webp|gif|avif))"', page)
    return list(dict.fromkeys(u for u in urls if "/static/" not in u))


# ---------- MangaDex: the series with this AniList number ----------

def mangadex_match(anilist_id, titles, get):
    """The MangaDex series whose AniList link is this one; `get` is manga._get."""
    for title in list(dict.fromkeys(t for t in titles if t))[:3]:
        data = get("/manga", {"title": title, "limit": 10, "contentRating[]": ["safe", "suggestive"]}, 86400)
        for manga in data.get("data") or []:
            if str(((manga.get("attributes") or {}).get("links") or {}).get("al") or "") == str(anilist_id):
                return manga
    return None


# ---------- Nyaa: whole volumes by torrent ----------

def magnet(info_hash, name=""):
    trackers = "".join(f"&tr={urllib.parse.quote(t, safe='')}" for t in NYAA_TRACKERS)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(name)}{trackers}"


def nyaa_volumes(titles, limit=12):
    """Torrents of volumes for these titles: [{hash, name, size, seeders, date}], most seeded first."""
    wanted = [normalize(t) for t in titles if len(normalize(t)) >= 3]
    found = {}
    for title in list(dict.fromkeys(t for t in titles if t))[:2]:
        # 3_1: literature, translated into English, where the digital volumes are.
        feed = fetch_text(f"{NYAA_RSS}&c=3_1&s=seeders&o=desc&q={urllib.parse.quote(title)}")
        try:
            root = ET.fromstring(feed)
        except ET.ParseError as error:
            raise SourceError("Nyaa ne répond pas comme prévu.") from error
        ns = {"nyaa": "https://nyaa.si/xmlns/nyaa"}
        for item in root.iter("item"):
            name = item.findtext("title") or ""
            info_hash = (item.findtext("nyaa:infoHash", namespaces=ns) or "").lower()
            seeders = int(item.findtext("nyaa:seeders", namespaces=ns) or 0)
            if not re.fullmatch(r"[0-9a-f]{40}", info_hash) or seeders <= 0:
                continue
            if not any(w in normalize(name) for w in wanted):
                continue  # another series that shares a word
            found[info_hash] = {"hash": info_hash, "name": name, "size": item.findtext("nyaa:size", namespaces=ns) or "",
                                "seeders": seeders, "date": (item.findtext("pubDate") or "")[5:16]}
    return sorted(found.values(), key=lambda t: -t["seeders"])[:limit]


def cached(key, ttl, fetch):
    """cached_get, but a site that fails is not remembered as empty."""
    return cached_get(f"manga-source:{key}", ttl, fetch)
