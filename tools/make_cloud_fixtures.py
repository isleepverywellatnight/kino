"""Golden data for the Rust port of the Cloud grouping (crates/kino/tests/cloud.rs).

Takes the release names of the source fixtures (each line of each title), adds
a few hand-made ones, and records what the Python version parses and groups,
at a fixed day. Run with HOME pointing at a temporary folder:
    HOME=$(mktemp -d) .venv/bin/python tools/make_cloud_fixtures.py
"""
import datetime
import gzip
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import cloud_titles  # noqa: E402

FIXTURES = ROOT / "crates" / "kino" / "tests" / "fixtures"
TODAY = datetime.date(2026, 11, 17)
EXTRA = [
    "[SubsPlease] Frieren - 12 (1080p) [ABCD1234].mkv", "[Erai-raws] One Piece - 1100v2 [1080p].mkv",
    "www.Torrenting.com - The.Bear.S03E05.1080p.WEB.h264-ETHEL.mkv", "The.Office.US.3x10.HDTV.avi", "Severance S02 E03 2160p HDR.mkv",
    "Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.DV.HDR.mkv", "video.mp4", "sample.mkv", "1917.2019.1080p.mkv", "2012.mkv",
    "Breaking Bad Saison 2 Episode 4 FRENCH.mkv", "Show.Name.Ep05.720p.mkv", "notes.txt", "archive.zip", "Le.Fabuleux.Destin.d.Amélie.Poulain.2001.FRENCH.1080p.mkv",
    "The.Matrix.1999.REPACK.PROPER.1080p.x265-GRP.mkv", "Spider-Man.No.Way.Home.2021.MULTI.VFF.2160p.mkv", "Movie Without Year 1080p.mkv",
]


def main():
    sources = json.loads(gzip.decompress((FIXTURES / "sources.json.gz").read_bytes()))
    names = []
    for key in sources["details"]:
        names.extend(line for line in key.split("\n")[:2] if line.strip() and "💾" not in line)
    names = list(dict.fromkeys(names + EXTRA))
    parsed = [[name, cloud_titles.parse_release(name, today=TODAY)] for name in names]
    rng = random.Random(3)
    groups = []
    for _ in range(25):
        chosen = rng.sample(names, min(len(names), rng.randint(1, 60)))
        items = [{"id": f"id{index}", "ids": [f"id{index}"], "filename": name, "bytes": rng.randint(0, 9) * 10 ** rng.randint(6, 10),
                  "generated": f"2026-{rng.randint(1, 11):02d}-{rng.randint(1, 28):02d}"} for index, name in enumerate(chosen)]
        groups.append([items, cloud_titles.group_cloud_items(items, today=TODAY)])
    matches = []
    metas = [{"id": "tt0111161", "name": "The Shawshank Redemption", "releaseInfo": "1994"},
             {"id": "tt0137523", "name": "Fight Club", "releaseInfo": "1999"},
             {"id": "tt0110912", "name": "Pulp Fiction", "releaseInfo": "1994"},
             {"id": "tt0816692", "name": "Interstellar", "releaseInfo": "2014", "name_fr": "Interstellaire"},
             {"id": "tt1375666", "name": "Inception", "year": "2010"}, {"id": "x1", "name": "Inception"},
             {"id": "tt9999999", "name": "Inceptio", "releaseInfo": "2010"}]
    for title, year in [("The Shawshank Redemption", "1994"), ("Shawshank Redemption", "1994"), ("Fight Club", ""), ("Fight Club", "2005"),
                        ("Interstellaire", "2014"), ("Inception", "2010"), ("Inceptio", ""), ("Pulp Fictio", "1994"), ("Nothing", "")]:
        matches.append([title, year, cloud_titles.best_match(metas, title, year)])
    (FIXTURES / "cloud.json.gz").write_bytes(gzip.compress(json.dumps(
        {"today": TODAY.isoformat(), "parsed": parsed, "groups": groups, "metas": metas, "matches": matches}, ensure_ascii=False).encode()))
    print(len(parsed), "names,", len(groups), "groupings")


if __name__ == "__main__":
    main()
