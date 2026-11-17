"""Golden data for the Rust port of « Nouveaux épisodes », « Mes séries » and the
« Calendrier » (crates/kino/tests/series.rs).

Fetches the episode lists of a few series from Cinemeta (no key), shaped as
meta_engine.series_videos keeps them, then records what the Python version
computes for random histories at fixed days. Run with HOME pointing at a
temporary folder:
    HOME=$(mktemp -d) .venv/bin/python tools/make_series_fixtures.py
"""
import datetime
import gzip
import html
import json
import pathlib
import random
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import new_episodes  # noqa: E402
import release_calendar  # noqa: E402
import series_progress  # noqa: E402

OUT = ROOT / "crates" / "kino" / "tests" / "fixtures" / "series.json.gz"
SERIES = ["tt0903747", "tt11198330", "tt13443470", "tt9140554", "tt10919420", "tt8111088", "tt1190634",
          "tt12637874", "tt11280740", "tt14452776", "tt7660850", "tt5180504", "tt0475784", "tt2560140", "tt0388629"]
DAYS = ["2023-04-25", "2024-06-20", "2024-07-20", "2024-12-30", "2025-01-10", "2025-08-12", "2025-09-04"]


def videos(imdb_id):
    request = urllib.request.Request(f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json",
                                     headers={"User-Agent": "KINO-Desktop"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
    return [{"season": v.get("season"), "episode": v.get("episode") or v.get("number"),
             "name": html.unescape(str(v.get("name") or v.get("title") or "")),
             "released": str(v.get("released") or "")[:10], "thumbnail": v.get("thumbnail") or ""}
            for v in (data.get("meta") or {}).get("videos") or [] if isinstance(v, dict)]


def entry(rng, imdb_id, episodes, today):
    aired = [v for v in new_episodes.aired_episodes(episodes) if v["released"] <= today] or new_episodes.aired_episodes(episodes)
    reached = rng.choice(aired) if aired else {"season": 1, "episode": 1}
    day = datetime.date.fromisoformat(today) - datetime.timedelta(days=rng.randint(0, 120))
    pct = rng.choice([0, 0.5, 12.0, 50, 84.9, 85, 97.0])
    item = {"id": imdb_id, "name": f"Série {imdb_id[-3:]}", "type": rng.choice(["series", "series", "anime", "tv"]),
            "year": "2020", "poster": "", "season": reached["season"], "episode": reached["episode"],
            "progress_pct": pct, "completed": rng.random() < 0.2,
            "last_watched": datetime.datetime(day.year, day.month, day.day, rng.randint(0, 23), tzinfo=datetime.timezone.utc).timestamp(),
            "watched_episodes": [], "ep_positions": {}}
    for video in rng.sample(aired, min(len(aired), rng.randint(0, 4))):
        code = new_episodes.episode_code(video["season"], video["episode"])
        if rng.random() < 0.5:
            item["watched_episodes"].append(code.lower() if rng.random() < 0.3 else code)
        else:
            item["ep_positions"][code] = {"pct": rng.choice([40, 90]), "completed": rng.random() < 0.3}
    if rng.random() < 0.1:
        item["imported_watched"] = True
    if rng.random() < 0.15:
        item["series_finished"] = True
    return item


def main():
    lists = {imdb_id: videos(imdb_id) for imdb_id in SERIES}
    rng = random.Random(7)
    cases = []
    for index in range(40):
        today = DAYS[index % len(DAYS)]
        local_today = today if rng.random() < 0.8 else (datetime.date.fromisoformat(today) + datetime.timedelta(days=1)).isoformat()
        chosen = rng.sample(SERIES, rng.randint(1, len(SERIES)))
        cut = rng.randint(0, len(chosen))
        history = [entry(rng, imdb_id, lists[imdb_id], today) for imdb_id in chosen[:cut]]
        watchlist = [{"id": imdb_id, "type": "series", "name": f"Liste {imdb_id[-3:]}", "year": "2021", "poster": ""}
                     for imdb_id in chosen[cut:]]
        missing = set(rng.sample(chosen, 1)) if rng.random() < 0.2 else set()
        episodes_for = lambda imdb_id: None if imdb_id in missing else lists[imdb_id]  # noqa: E731
        cases.append({
            "today": today, "local_today": local_today, "history": history, "watchlist": watchlist, "missing": sorted(missing),
            "new": new_episodes.find_new_episodes(history, episodes_for, today, watchlist=watchlist),
            "my": series_progress.my_series(history, watchlist, episodes_for, today),
            "calendar": release_calendar.calendar(history, watchlist, episodes_for, today, local_today=local_today),
        })
    OUT.write_bytes(gzip.compress(json.dumps({"videos": lists, "cases": cases}, ensure_ascii=False).encode()))
    print(OUT, len(cases), "cases")


if __name__ == "__main__":
    main()
