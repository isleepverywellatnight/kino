"""Golden data for the Rust port of the source search (crates/kino/tests/sources.rs).

Fetches what Torrentio answers (no key) for a few titles, keeps a sample of each
answer, then records what the Python version computes from it: each release's
details, relevance, score parts and reasons, and the whole ranked list for
several user profiles. Run with HOME pointing at a temporary folder:
    HOME=$(mktemp -d) .venv/bin/python tools/make_source_fixtures.py
"""
import gzip
import json
import pathlib
import random
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import torrent_engine as te  # noqa: E402

OUT = ROOT / "crates" / "kino" / "tests" / "fixtures" / "sources.json.gz"
CASES = [
    {"imdb_id": "tt0111161", "media_type": "movie", "title": "The Shawshank Redemption", "year": "1994", "runtime": 142},
    {"imdb_id": "tt6751668", "media_type": "movie", "title": "Parasite", "year": "2019", "runtime": 132},
    {"imdb_id": "tt0113277", "media_type": "movie", "title": "Heat", "year": "1995", "runtime": 170},
    {"imdb_id": "tt15398776", "media_type": "movie", "title": "Oppenheimer", "year": "2023", "runtime": 180},
    {"imdb_id": "tt0245429", "media_type": "movie", "title": "Spirited Away", "year": "2001", "runtime": 125},
    {"imdb_id": "tt0903747", "media_type": "series", "title": "Breaking Bad", "year": "2008", "season": 1, "episode": 3, "runtime": 47},
    {"imdb_id": "tt0944947", "media_type": "series", "title": "Game of Thrones", "year": "2011", "season": 8, "episode": 6, "runtime": 79},
    {"imdb_id": "tt0388629", "media_type": "series", "title": "One Piece", "year": "1999", "season": 1, "episode": 1, "runtime": 24, "absolute": 1},
    {"imdb_id": "tt2560140", "media_type": "series", "title": "Attack on Titan", "year": "2013", "season": 2, "episode": 1, "runtime": 24, "absolute": 26},
    {"imdb_id": "tt0108778", "media_type": "series", "title": "Friends", "year": "1994", "season": 3, "episode": 10, "runtime": 22},
]
PROFILES = [
    {"pref_lang": "vf", "pref_quality": "4k", "hdr_mode": "sdr_pref", "source_priority": "speed"},
    {"pref_lang": "vostfr", "pref_quality": "1080p", "hdr_mode": "hdr_native", "source_priority": "quality"},
    {"pref_lang": "vo", "pref_quality": "720p", "hdr_mode": "sdr_only", "source_priority": "speed"},
    {"pref_lang": "multi", "pref_quality": "4k", "hdr_mode": "hdr_boost", "source_priority": "quality"},
]
SORTS = ["score", "seeders", "size", "quality"]


def fetch(target, label_url):
    request = urllib.request.Request(label_url, headers={"User-Agent": "KINO/2.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8")).get("streams") or []


def main():
    random.seed(7)
    cases = []
    for case in CASES:
        target = te.stream_target(case["imdb_id"], case["media_type"], case.get("season", 1), case.get("episode", 1))
        raw = {}
        for bucket, url, label, _timeout in te.indexer_sources(target)[:3]:
            try:
                streams = fetch(target, url)
            except Exception as error:
                print("skip", bucket, case["title"], error)
                streams = []
            raw[bucket] = {"label": label, "streams": streams[:45] + random.sample(streams[45:], min(15, len(streams[45:])))}
        cases.append({**case, "target": target, "raw": raw})
        print(case["title"], {k: len(v["streams"]) for k, v in raw.items()})

    titles = sorted({s.get("title") or s.get("name") or "" for c in cases for b in c["raw"].values() for s in b["streams"]})
    details = {t: te.parse_release_details(t) for t in titles}

    for case in cases:
        buckets = {bucket: [p for p in (te.parse_stremio_stream(s, data["label"]) for s in data["streams"]) if p]
                   for bucket, data in case["raw"].items()}
        merged = te.merge_streams(json.loads(json.dumps(buckets)))
        case["merged"] = json.loads(json.dumps(merged))
        names = (case["title"],)
        case["results"] = []
        for profile in PROFILES:
            for sort_by in SORTS:
                kept = te.keep_relevant_streams(
                    json.loads(json.dumps(merged)), media_type=case["media_type"], season=case.get("season", 1),
                    episode=case.get("episode", 1), absolute_episode=case.get("absolute"), runtime_minutes=case["runtime"],
                    titles=names, release_year=case["year"], pref_lang=profile["pref_lang"], pref_quality=profile["pref_quality"])
                ranked = te.rank_streams(
                    kept, sort_by, titles=names, pref_lang=profile["pref_lang"], pref_quality=profile["pref_quality"],
                    hdr_mode=profile["hdr_mode"], media_type=case["media_type"], runtime_minutes=case["runtime"],
                    release_year=case["year"], source_priority=profile["source_priority"])
                key = lambda item: item.get("info_hash") or item.get("title")
                if sort_by == "score":
                    entries = [{"key": key(item), "score": item["score"], "parts": item["score_parts"], "why": item["why"],
                                "recommendation": item.get("recommendation")} for item in ranked]
                else:  # the order is all that differs
                    entries = [key(item) for item in ranked]
                case["results"].append({"profile": profile, "sort_by": sort_by, "ranked": entries})
    OUT.write_bytes(gzip.compress(json.dumps({"details": details, "cases": cases}, ensure_ascii=False).encode("utf-8"), mtime=0))
    print(OUT, OUT.stat().st_size // 1024, "KiB")


if __name__ == "__main__" and "--episodes" not in sys.argv:
    main()


def episode_fixtures():
    """Which file of a pack is the episode asked for (debrid_engine.is_target_episode_file)."""
    import debrid_engine as de
    data = json.loads(gzip.decompress(OUT.read_bytes()))
    names = sorted(data["details"]) + [
        "[SubsPlease] Frieren - 12 (1080p) [ABCD1234].mkv", "[Erai-raws] Shingeki no Kyojin - 26 [1080p].mkv",
        "Attack.on.Titan.S02E01.1080p.WEB.mkv", "One Piece - 1071 [1080p].mkv", "One.Piece.NCOP.01.mkv", "Show.3x07.HDTV.mkv",
        "Naruto Shippuden Episode 12.mkv", "Season 2 - 05.mkv", "Show Part 2 - 01.mkv", "Show.S01E01-E03.mkv", "[Group] Show - 05v2.mkv",
        "Show 2nd Season - 03.mkv", "Show (2019) - 04.mkv", "Show ep.4 720p.mkv", "Show #04.mkv", "Show [04].mkv", "Show (04).mkv",
        "Show - OP.mkv", "Show - Sample.mkv", "Movie.2160p.mkv", "",
    ]
    cases = [(None, None, None), (1, 1, None), (1, 4, None), (1, 12, None), (2, 1, 26), (3, 7, None), (8, 6, None), (1, 1071, None), (2, 5, 30)]
    rows = [[name, s, e, a, de.is_target_episode_file(name, s, e, a)] for name in names for s, e, a in cases]
    path = OUT.parent / "episodes.json.gz"
    path.write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False).encode("utf-8"), mtime=0))
    print(path, len(rows), "cases")


if __name__ == "__main__" and "--episodes" in sys.argv:
    episode_fixtures()
