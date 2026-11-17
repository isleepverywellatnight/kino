"""Cloud files grouped by work.

A release name such as « The.Bear.S03E05.1080p.WEB.h264-ETHEL.mkv » becomes
the series « The Bear », season 3, episode 5, in 1080p; the files of one work
form one group. Pure logic: the catalogue match receives its search functions.
"""
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher

VIDEO_EXTENSIONS = {"mkv", "mp4", "avi", "m4v", "mov", "ts", "m2ts", "wmv", "webm", "mpg", "mpeg", "iso"}
OTHER_EXTENSIONS = {"zip", "rar", "7z", "tar", "gz", "srt", "ass", "ssa", "sub", "idx", "nfo", "txt", "jpg", "jpeg",
                    "png", "gif", "pdf", "epub", "cbz", "cbr", "mp3", "flac", "m4a", "aac", "ogg", "exe", "dmg", "apk"}
# Words that only appear after the title in a release name.
RELEASE_WORDS = {
    "4k", "uhd", "hdr", "hdr10", "hdr10+", "dv", "dovi", "bluray", "blu-ray", "bdrip", "brrip", "bdremux", "remux",
    "web-dl", "webdl", "webrip", "web-rip", "hdtv", "pdtv", "dvdrip", "dvdscr", "hdrip", "hdlight", "x264", "x265",
    "h264", "h265", "h-264", "h-265", "hevc", "avc", "xvid", "divx", "10bit", "8bit", "aac", "ac3", "eac3", "dts",
    "dts-hd", "ddp", "ddp5", "dd5", "truehd", "atmos", "multi", "multi-vf2", "vff", "vfq", "vfi", "vf2", "vostfr",
    "subfrench", "truefrench", "amzn", "dsnp", "atvp", "hmax", "nf",
}
# Words that may close a title when no year or marker cut it earlier.
TRAILING_WORDS = {"french", "truefrench", "multi", "vff", "vf", "vostfr", "english", "complete", "integrale",
                  "intégrale", "extended", "unrated", "remastered", "proper", "repack", "internal", "custom", "-"}
# Names that say nothing about the work: kept apart rather than matched.
GENERIC_TITLES = {"video", "film", "movie", "sample", "fichier", "file", "untitled", "download", "telechargement"}
MAX_GROUP_FILES = 200


def comparable(text):
    """Lower case, no accents, no punctuation: « Spider-Man: No Way Home » ≈ « spider man no way home »."""
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^0-9a-z]+", " ", folded.replace("&", " and ")).strip()


def _split_extension(name):
    match = re.match(r"^(.*)\.([A-Za-z0-9]{2,4})$", name)
    if match and match[2].lower() in VIDEO_EXTENSIONS | OTHER_EXTENSIONS:
        return match[1], match[2].lower()
    return name, ""


def _is_release_word(token):
    low = token.lower().strip(".,;:!-+")
    return (low in RELEASE_WORDS or low.split("-")[0] in RELEASE_WORDS
            or re.fullmatch(r"\d{3,4}[pi]", low.split("-")[0]) is not None)


def parse_release(filename, today=None):
    """{"kind": "movie" | "series" | "other", "title", "year", "season", "episode", "quality", "hdr"}."""
    result = {"kind": "other", "title": "", "year": "", "season": None, "episode": None, "quality": "", "hdr": False}
    base, extension = _split_extension(str(filename or "").strip())
    if extension and extension not in VIDEO_EXTENSIONS:
        return result
    grouped = bool(re.match(r"^\s*\[[^\]]*\]", base))
    base = re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", base)
    base = re.sub(r"^\s*(?:www\.)?[\w-]+\.(?:com|org|net|to|cc|tv|io|me|ws|xyz|fr)\s+-\s+", "", base, flags=re.I)
    text = re.sub(r"[._]+", " ", base)
    text = re.sub(r"[\[\](){}]", " ", text)
    tokens = text.split()
    latest_year = (today or date.today()).year + 1

    cut = len(tokens)
    for index, token in enumerate(tokens):
        low = token.lower()
        following = tokens[index + 1].lower() if index + 1 < len(tokens) else ""
        marker = re.match(r"^s(\d{1,2})[ -]?e(\d{1,4})(?:-?e?\d{1,4})?$", low)
        if marker:
            result["season"], result["episode"] = int(marker[1]), int(marker[2])
        elif re.fullmatch(r"(\d{1,2})x(\d{2,3})", low):
            season, episode = low.split("x")
            result["season"], result["episode"] = int(season), int(episode)
        elif re.fullmatch(r"s(\d{1,2})", low) and re.fullmatch(r"e(\d{1,4})", following):
            result["season"], result["episode"] = int(low[1:]), int(following[1:])
        elif re.fullmatch(r"s(\d{1,2})", low) and index > 0:
            result["season"] = int(low[1:])
        elif low in ("season", "saison") and following.isdigit() and index > 0:
            result["season"] = int(following)
        elif low in ("ep", "episode", "épisode") and following.isdigit() and index > 0:
            result["episode"] = int(following)
        elif re.fullmatch(r"ep?(\d{2,4})", low) and index > 0:
            result["episode"] = int(re.sub(r"\D", "", low))
        elif grouped and low == "-" and re.fullmatch(r"\d{1,4}(?:v\d)?", following) and index > 0:
            result["episode"] = int(re.sub(r"v\d$", "", following))
        elif _is_release_word(token) and index > 0:
            pass
        else:
            continue
        cut = index
        break

    title_end = cut
    for index in range(cut - 1, 0, -1):
        if re.fullmatch(r"(19|20)\d\d", tokens[index]) and 1900 <= int(tokens[index]) <= latest_year:
            result["year"] = tokens[index]
            title_end = index
            break
    words = tokens[:title_end]
    while words and words[-1].lower() in TRAILING_WORDS:
        words.pop()
    while words and words[0] == "-":
        words.pop(0)
    title = " ".join(words).strip(" -–")
    plain = comparable(title)
    if not plain or plain in GENERIC_TITLES or (plain.isdigit() and not result["year"]):
        return {**result, "season": None, "episode": None}

    for token in tokens[cut:]:
        low = token.lower().split("-")[0]
        if not result["quality"] and re.fullmatch(r"\d{3,4}[pi]", low):
            result["quality"] = low[:-1] + "p"
        elif not result["quality"] and low in ("4k", "uhd"):
            result["quality"] = "2160p"
        if low.startswith("hdr") or low in ("dv", "dovi"):
            result["hdr"] = True
    result["title"] = title
    result["kind"] = "series" if result["season"] is not None or result["episode"] is not None else "movie"
    return result


def group_key(parsed):
    if parsed["kind"] == "other":
        return "other"
    key = f"{parsed['kind']}:{comparable(parsed['title'])}"
    return f"{key}:{parsed['year']}" if parsed["kind"] == "movie" and parsed["year"] else key


def _file_order(kind):
    if kind == "series":
        return lambda f: (f["season"] or 0, f["episode"] or 0, f["filename"].lower())
    if kind == "movie":
        return lambda f: -int(f.get("bytes") or 0)
    return lambda f: f["filename"].lower()


def group_cloud_items(items, today=None):
    """Cloud files gathered by work, newest work first, « other » files last.

    Each group: {key, kind, title, year, files, count, bytes, seasons, latest}.
    Each file keeps its Cloud fields and gains season, episode, quality, hdr.
    """
    groups = {}
    for item in items or []:
        if not isinstance(item, dict) or not item.get("filename"):
            continue
        parsed = parse_release(item["filename"], today=today)
        key = group_key(parsed)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {"key": key, "kind": parsed["kind"], "title": parsed["title"],
                                   "year": parsed["year"], "files": []}
        if not group["year"] and parsed["year"]:
            group["year"] = parsed["year"]
        if len(group["files"]) < MAX_GROUP_FILES:
            group["files"].append({**item, "season": parsed["season"], "episode": parsed["episode"],
                                   "quality": parsed["quality"], "hdr": parsed["hdr"]})
    ordered = [g for g in groups.values() if g["kind"] != "other"] + [g for g in groups.values() if g["kind"] == "other"]
    for group in ordered:
        group["files"].sort(key=_file_order(group["kind"]))
        group["count"] = len(group["files"])
        group["bytes"] = sum(int(f.get("bytes") or 0) for f in group["files"])
        group["seasons"] = sorted({f["season"] for f in group["files"] if f["season"] is not None})
        group["latest"] = max((str(f.get("generated") or "") for f in group["files"]), default="")
    return ordered


def _years(text):
    return [int(year) for year in re.findall(r"\b(1[89]\d\d|20\d\d)\b", str(text or ""))]


def best_match(results, title, year="", kind="movie"):
    """The catalogue entry named like the file, or None: a wrong poster is worse
    than none. Names must be nearly identical (0.95), or close (0.9) with a
    release year within one year of the file's."""
    wanted = comparable(title)
    best, best_rank = None, 0.0
    for meta in results or []:
        meta_id = str((meta or {}).get("id") or "")
        if not meta_id.startswith("tt"):
            continue
        names = [name for name in (comparable(meta.get("name")), comparable(meta.get("name_fr"))) if name]
        if not names:
            continue
        ratio = max(1.0 if name == wanted else SequenceMatcher(None, name, wanted).ratio() for name in names)
        found = _years(meta.get("releaseInfo") or meta.get("year"))
        if year and found and abs(found[0] - int(year)) > 1:
            continue
        same_year = bool(year and found)
        if ratio < 0.95 and not (same_year and ratio >= 0.9):
            continue
        rank = ratio + (0.1 if same_year else 0)
        if rank > best_rank:
            best, best_rank = meta, rank
    return best


def match_title(title, year, kind, search, search_fr=None):
    """Catalogue media for a Cloud work, {} when the catalogue has none.

    A failing search raises, so that the caller does not remember a miss caused
    by the network.
    """
    if kind not in ("movie", "series") or len(comparable(title)) < 2:
        return {}
    found = best_match(search(title, kind) or [], title, year, kind)
    if not found and search_fr:
        found = best_match(search_fr(title, kind) or [], title, year, kind)
    if not found:
        return {}
    media_id = found["id"]
    years = _years(found.get("releaseInfo") or found.get("year"))
    media = {"id": media_id, "name": found.get("name") or title, "type": kind,
             "year": str(years[0]) if years else str(year or ""),
             "poster": found.get("poster") or f"https://images.metahub.space/poster/medium/{media_id}/img"}
    if found.get("name_fr"):
        media["name_fr"] = found["name_fr"]
    return media
