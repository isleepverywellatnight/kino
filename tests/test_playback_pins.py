"""Playback, pinned: the scores, the source search, « Lecture en un clic » and
the next episode give exactly these answers. The numbers come from KINO 1.1.73;
the code behind them may be rearranged, the answers may not change.

Changed on purpose once since: KINO 1.1.80
with « Image : SDR », the image part gives SDR +40 (was +140), HDR and DV + HDR 0
(were −120 and −340), DV alone −350 (unchanged). Only that part moved."""
import base64
import json
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock, call, patch

import addon_manager
import app
import player_engine
import torrent_engine

# --- Scores ----------------------------------------------------------------

RELEASES = [
    ("Dune.Part.Two.2024.MULTI.VFF.2160p.WEB-DL.DV.HDR.DDP5.1.Atmos-FRATERNiTY", 24.0, 150, True, "Torrentio"),
    ("Dune Part Two 2024 FRENCH 1080p BluRay x264 AC3-NoTag", 9.5, 40, False, "Torrentio FR"),
    ("Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.HDR.TrueHD.7.1.Atmos-FGT", 72.0, 300, False, "Comet"),
    ("Dune.Part.Two.2024.VOSTFR.1080p.WEB.H264-GGEZ", 4.2, 12, True, "Torrentio"),
    ("Dune.Part.Two.2024.TRUEFRENCH.720p.HDTS.x264", 1.4, 5, False, "TPB+"),
    ("Dune.2021.MULTI.VFQ.1080p.BluRay.x265-QTZ", 8.0, 60, False, "Torrentio"),
    ("[SubsPlease] Frieren - 12 (1080p) [ABCD1234].mkv", 1.4, 500, False, "Torrentio Anime (Nyaa)"),
    ("Frieren.S01E12.VOSTFR.1080p.WEB.x264-Tsundere-Raws", 1.3, 80, True, "nyaasi"),
    ("The.Office.US.S02E03.MULTI.1080p.WEB.H264.SDR-NTb", 1.1, 25, False, "Peerflix"),
    ("Shogun.2024.S01E01.MULTi.VFF.2160p.DSNP.WEB-DL.SDR.DDP5.1-FW", 7.8, 33, True, "Torrentio"),
    ("Oppenheimer.2023.VFF.2160p.BluRay.x265.10bit.HDR.DTS-HD.MA.5.1", 31.0, 0, False, "KnightCrawler"),
    ("Le.Samourai.1967.VOF.1080p.BluRay.FLAC.x264", 12.0, 9, False, "Torrentio"),
]
PROFILES = [
    dict(pref_lang="vf", pref_quality="4k", hdr_mode="sdr_pref", media_type="movie", runtime_minutes=166, target_year="2024", title_years=()),
    dict(pref_lang="vostfr", pref_quality="1080p", hdr_mode="hdr_native", media_type="series", runtime_minutes=None, target_year="", title_years=()),
    dict(pref_lang="vf", pref_quality="1080p", hdr_mode="hdr_boost", media_type="anime", runtime_minutes=24, target_year="2023", title_years=(2023,)),
]
SCORES = [
    [5470, 1645, 1190, 4514, -28550, -8790, -9290, -5590, -8960, -4584, 1370, -8952],
    [4620, 805, 730, 4834, -29740, 1320, 1270, 5170, 1150, 4476, 330, 708],
    [4710, 1355, 230, 4284, -28990, -8580, 920, 4620, 1250, 4776, 930, -8742],
]


def release(title, size, seeders, instant, source):
    return {"title": title, "size_gb": size, "seeders": seeders, "is_instant": instant, "source": source, "qualities": [], "langs": []}


class ScorePins(unittest.TestCase):
    def test_every_release_keeps_its_score_in_every_profile(self):
        for profile, expected in zip(PROFILES, SCORES):
            got = [torrent_engine.score_torrent(release(*row), **profile) for row in RELEASES]
            self.assertEqual(got, expected, profile)


# --- Source search ---------------------------------------------------------

H = lambda n: f"{n:040x}"
MOVIE_STREAMS = {
    "main": [
        {"name": "Torrentio\n4k", "title": "Dune.Part.Two.2024.MULTI.VFF.2160p.WEB-DL.DV.HDR.DDP5.1.Atmos-FRATERNiTY\n👤 150 💾 24.0 GB ⚙️ YTS", "infoHash": H(1)},
        {"name": "Torrentio\n4k", "title": "Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.HDR.TrueHD.7.1.Atmos-FGT\n👤 300 💾 72.0 GB ⚙️ TPB", "infoHash": H(2)},
        {"name": "Torrentio\n720p", "title": "Dune.Part.Two.2024.TRUEFRENCH.720p.HDTS.x264\n👤 5 💾 1.4 GB", "infoHash": H(3)},
        {"name": "Torrentio\n1080p", "title": "Dune.2021.MULTI.VFQ.1080p.BluRay.x265-QTZ\n👤 60 💾 8.0 GB", "infoHash": H(4)},
    ],
    "fr": [
        {"name": "Torrentio FR\n1080p", "title": "Dune Part Two 2024 FRENCH 1080p BluRay x264 AC3-NoTag\n👤 40 💾 9.5 GB", "infoHash": H(5)},
        {"name": "Torrentio FR\n4k", "title": "Dune.Part.Two.2024.MULTI.VFF.2160p.WEB-DL.DV.HDR.DDP5.1.Atmos-FRATERNiTY\n👤 150 💾 24.0 GB", "infoHash": H(1)},
    ],
    "comet": [
        {"name": "[RD+] Comet 4K", "title": "Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.HDR.TrueHD.7.1.Atmos-FGT\n💾 72.0 GB", "infoHash": H(2),
         "url": "https://comet.example/resolve/" + H(2) + "/x"},
        {"name": "[RD+] Comet 1080p", "title": "Dune.Part.Two.2024.VOSTFR.1080p.WEB.H264-GGEZ\n💾 4.2 GB", "infoHash": H(6)},
    ],
    "peerflix": [{"name": "Peerflix", "title": "Dune.Part.Two.2024.1080p.WEB-DL.x264.Sample\n💾 0.05 GB", "infoHash": H(7)}],
}
SERIES_STREAMS = {
    "main": [
        {"name": "Torrentio\n1080p", "title": "Shogun.2024.S01E01.MULTi.VFF.2160p.DSNP.WEB-DL.SDR.DDP5.1-FW\n👤 33 💾 7.8 GB", "infoHash": H(11)},
        {"name": "Torrentio\n1080p", "title": "Shogun.2024.S01E02.MULTi.VFF.1080p.WEB-DL-FW\n👤 30 💾 3.1 GB", "infoHash": H(12)},
        {"name": "Torrentio\n1080p", "title": "Shogun.2024.S01.MULTi.1080p.WEB-DL-FW\n👤 90 💾 31.0 GB", "infoHash": H(13)},
    ],
    "anime": [{"name": "Torrentio Anime (Nyaa)", "title": "Shogun.2024.S01E01.VOSTFR.1080p.WEB.x264\n👤 8 💾 1.6 GB", "infoHash": H(14)}],
    "knightcrawler": [{"name": "KnightCrawler", "title": "Shogun.1980.S01E01.720p.HDTV\n👤 2 💾 0.7 GB", "infoHash": H(15)}],
}
APIBAY = [{"title": "Dune.Part.Two.2024.1080p.WEBRip.x265-RARBG", "info_hash": H(20), "size_gb": 2.1, "seeders": 900,
           "source": "APIBay", "qualities": [], "langs": [], "magnet": "magnet:?xt=urn:btih:" + H(20)}]


def bucket_of(url):
    if "language=french/" in url or "language=french|" in url:
        return "fr"
    if "language=japanese" in url:
        return "anime"
    for key, host in (("comet", "comet.feels.legal"), ("knightcrawler", "knightcrawler"), ("tpb", "thepiratebay-plus"), ("peerflix", "peerflix.mov")):
        if host in url:
            return key
    return "main"


def fake_http(streams, asked=None):
    def http_json(url, timeout=0):
        if asked is not None:
            asked.append((url, timeout))
        return {"streams": streams.get(bucket_of(url), [])}
    return http_json


def rows(results):
    return [(r["title"], r["source"], r["score"], bool(r.get("is_instant")), sorted(r.get("langs") or [])) for r in results]


class SearchPins(unittest.TestCase):
    def setUp(self):
        addons = patch.object(addon_manager, "run_addons_search", return_value=[])
        self.addons = addons.start()
        self.addCleanup(addons.stop)

    def movie(self, sort_by="score"):
        return torrent_engine.search_multi_torrents(
            "tt15239678", "movie", token="KEY", tio_key="realdebrid", runtime_minutes=166, sort_by=sort_by,
            http_json_fn=fake_http(MOVIE_STREAMS), search_apibay_fn=lambda q: APIBAY, query_title="Dune Part Two", release_year="2024")

    def test_a_film_merges_filters_scores_and_ranks(self):
        results = self.movie()
        self.assertEqual(rows(results), [
            ("Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.HDR.TrueHD.7.1.Atmos-FGT", "Torrentio", 4690, True, []),
            ("Dune.Part.Two.2024.VOSTFR.1080p.WEB.H264-GGEZ", "Comet", 4490, True, ["VOSTFR"]),
            ("Dune.Part.Two.2024.MULTI.VFF.2160p.WEB-DL.DV.HDR.DDP5.1.Atmos-FRATERNiTY", "Torrentio FR", 1970, False, ["MULTI", "VFF"]),
            ("Dune Part Two 2024 FRENCH 1080p BluRay x264 AC3-NoTag", "Torrentio FR", 1645, False, ["VF"]),
            ("Dune.Part.Two.2024.1080p.WEBRip.x265-RARBG", "APIBay", 870, False, []),
        ], "fake (HDTS), homonym (2021) and sample dropped; duplicates merged")
        self.assertEqual(results[0]["resolve_url"], "https://comet.example/resolve/" + H(2) + "/x", "the instant copy's link is kept")
        self.assertEqual(results[0]["recommendation"], {
            "reasons": ["Titre cohérent", "Disponibilité immédiate signalée", "Qualité souhaitée : 4K"],
            "warnings": ["Langue non indiquée", "Compatibilité audio à vérifier"]})

    def test_each_sort_keeps_its_order(self):
        order = {sort: [r["title"][:28] for r in self.movie(sort)] for sort in ("seeders", "size", "quality")}
        self.assertEqual(order["seeders"], ["Dune.Part.Two.2024.2160p.UHD", "Dune.Part.Two.2024.VOSTFR.10", "Dune.Part.Two.2024.1080p.WEB",
                                            "Dune.Part.Two.2024.MULTI.VFF", "Dune Part Two 2024 FRENCH 10"])
        self.assertEqual(order["size"], ["Dune.Part.Two.2024.2160p.UHD", "Dune.Part.Two.2024.VOSTFR.10", "Dune.Part.Two.2024.MULTI.VFF",
                                         "Dune Part Two 2024 FRENCH 10", "Dune.Part.Two.2024.1080p.WEB"])
        self.assertEqual(order["quality"], order["size"])

    def test_an_episode_keeps_only_its_episode_and_its_show(self):
        results = torrent_engine.search_multi_torrents(
            "tt2788316", "series", 1, 1, http_json_fn=fake_http(SERIES_STREAMS), query_title="Shogun", release_year="2024",
            pref_lang="vostfr", pref_quality="1080p")
        self.assertEqual(rows(results), [
            ("Shogun.2024.S01E01.VOSTFR.1080p.WEB.x264", "Torrentio Anime (Nyaa)", 1866, False, ["VOSTFR"]),
            ("Shogun.2024.S01.MULTi.1080p.WEB-DL-FW", "Torrentio", 1640, False, ["MULTI"]),
            ("Shogun.2024.S01E01.MULTi.VFF.2160p.DSNP.WEB-DL.SDR.DDP5.1-FW", "Torrentio", 1516, False, ["MULTI", "VFF"]),
        ])
        self.assertEqual(results[1]["recommendation"], {"reasons": ["Titre cohérent", "Qualité souhaitée : 1080p"],
                                                        "warnings": ["Épisode à vérifier dans le fichier"]})

    def test_the_indexers_asked_and_their_timeouts(self):
        for token, tio_key, provider in (("KEY", "realdebrid", "alldebrid"), ("", "", "realdebrid"), ("KEY", "", "torbox")):
            asked = []
            torrent_engine.search_multi_torrents("tt1", "series", 2, 3, token=token, tio_key=tio_key, provider=provider,
                                                 http_json_fn=fake_http({}, asked), search_apibay_fn=lambda q: [], query_title="X")
            keyed = f"{tio_key}={token}" if token and tio_key else ""
            sep = f"|{keyed}" if keyed else ""
            expected = sorted([
                (f"https://torrentio.strem.fun/{keyed + '/' if keyed else ''}stream/series/tt1:2:3.json", 20),
                (f"https://torrentio.strem.fun/providers=torrent9,c411,nyaasi|language=french{sep}/stream/series/tt1:2:3.json", 10),
                (f"https://torrentio.strem.fun/providers=nyaasi,tokyotosho,anidex,torrent9|language=japanese,french{sep}/stream/series/tt1:2:3.json", 10),
                (f"https://knightcrawler.elfhosted.com/{keyed + '/' if keyed else ''}stream/series/tt1:2:3.json", 4),
                ("https://thepiratebay-plus.strem.fun/stream/series/tt1:2:3.json", 6),
                ("https://peerflix.mov/stream/series/tt1:2:3.json", 6),
            ])
            comet = [entry for entry in asked if "comet.feels.legal" in entry[0]]
            self.assertEqual(sorted(entry for entry in asked if entry not in comet), expected, provider)
            self.assertEqual(len(comet), 1)
            url, timeout = comet[0]
            self.assertEqual(timeout, 10)
            if not token:
                self.assertEqual(url, "https://comet.feels.legal/stream/series/tt1:2:3.json")
                continue
            settings = json.loads(base64.b64decode(url.split("/")[3]))
            self.assertEqual(settings, {"indexers": ["all"], "maxResultsPerResolution": 0, "maxSize": 107374182400, "cachedOnly": False,
                                        "removeTrash": True, "debridServices": [{"service": provider, "apiKey": token}], "enableTorrent": True})
        self.assertEqual(self.addons.call_args.kwargs, {"query": "X", "media_type": "series", "season": 2, "episode": 3})


# --- « Lecture en un clic » -------------------------------------------------

def handler_for(body):
    handler = object.__new__(app.RequestHandler)
    handler.path = "/api/one-click-play"
    handler._request_allowed = Mock(return_value=True)
    handler.read_json_body = Mock(return_value=body)
    handler.send_json = Mock()
    return handler


def source(n, score=1000):
    return {"info_hash": f"hash{n}", "magnet": f"magnet:{n}", "title": f"Release {n}", "source": "Torrentio", "score": score}


def ready(url="https://debrid.example/film.mkv", name="Film.2024.mkv"):
    return {"ready": True, "files": [{"filename": name, "download": url}]}


class OneClickPins(unittest.TestCase):
    def play(self, body, sources, debrid, *, platform="darwin", meta=None, resume=0, mpv=None, playlist=None):
        query = SimpleNamespace(runtime_minutes=None, media_type=body.get("type", "movie"), title=body.get("title", ""), release_year="",
                                season=int(body.get("season", 1)), episode=int(body.get("episode", 1)))
        with patch.object(app, "load_config", return_value={"rd_token": "test"}), \
                patch.object(app.sys, "platform", platform), \
                patch.object(app, "get_media_meta", return_value=meta or {}) as get_meta, \
                patch.object(app.StreamQuery, "from_params", return_value=query), \
                patch.object(app, "get_available_streams", return_value=sources) as search, \
                patch.object(app, "rd_debrid_magnet", side_effect=debrid) as resolve, \
                patch.object(app.playback_support, "excluded", return_value=set()), \
                patch.object(app, "get_resume_position", return_value=resume) as resume_at, \
                patch.object(app, "build_series_playlist_items", return_value=playlist) as build, \
                patch.object(app, "prefetch_next_episode") as prefetch, \
                patch.object(app, "launch_mpv", side_effect=mpv or (lambda *a, **k: {"pid": 1})) as launch, \
                patch.object(app, "record_history") as history:
            handler = handler_for(body)
            handler.do_POST()
        self.calls = SimpleNamespace(meta=get_meta, search=search, resolve=resolve, resume=resume_at, build=build,
                                     prefetch=prefetch, launch=launch, history=history, query=query)
        return handler.send_json.call_args

    def test_a_film_in_the_built_in_player(self):
        sent = self.play({"imdb_id": "tt1", "type": "movie", "title": "Film", "year": "2024", "poster": "p.jpg"},
                         [source(1)], lambda *a, **k: ready(), meta={"name": "Film VO", "runtime": "1 h 50 min"}, resume=42)
        answer = sent.args[0]
        key = app.stream_candidate_key(source(1))
        self.assertEqual(answer, {
            "ok": True, "source_key": key, "attempted_source_keys": [key], "chosen_torrent": "Torrentio Release 1",
            "debrid": ready(), "file": ready()["files"][0], "stream_url": "https://debrid.example/film.mkv", "playlist": [],
            "mpv": {"mode": "integrated", "playlist_count": 1}, "resume_sec": 42})
        self.assertEqual(self.calls.query.runtime_minutes, 1, "the film's runtime from its data goes into the search")
        self.calls.resolve.assert_called_once_with("test", "magnet:1", season=None, episode=None, resolve_url="")
        self.calls.resume.assert_called_once_with("tt1", None, None)
        self.calls.launch.assert_not_called()
        self.calls.history.assert_not_called()

    def test_a_film_in_mpv_is_launched_and_recorded(self):
        sent = self.play({"imdb_id": "tt1", "type": "movie", "title": "Film", "year": "2024", "poster": "p.jpg", "player_mode": "external"},
                         [source(1)], lambda *a, **k: ready(), platform="linux", resume=10)
        media = {"id": "tt1", "name": "Film", "type": "movie", "year": "2024", "poster": "p.jpg", "season": None, "episode": None,
                 "filename": "Film.2024.mkv"}
        self.calls.launch.assert_called_once_with("https://debrid.example/film.mkv", "Film", playlist_items=None, media_ctx=media, start_sec=10)
        self.calls.history.assert_called_once_with(media)
        self.assertEqual(sent.args[0]["mpv"], {"pid": 1, "mode": "kino"})

    def test_mac_uses_the_built_in_player_unless_external_is_asked(self):
        for mode, launched in (("kino", False), ("integrated", False), ("external", True)):
            self.play({"imdb_id": "tt1", "type": "movie", "title": "Film", "player_mode": mode}, [source(1)], lambda *a, **k: ready())
            self.assertEqual(self.calls.launch.called, launched, mode)

    def test_an_episode_gets_its_playlist_and_the_next_one_is_prepared(self):
        playlist = [{"url": "https://debrid.example/e5.mkv"}, {"url": "kino-next"}]
        sent = self.play({"imdb_id": "tt9", "type": "series", "season": "2", "episode": "5", "title": "Show — S02E05", "year": "2020",
                          "poster": "s.jpg"}, [source(1)], lambda *a, **k: ready("https://debrid.example/e5.mkv", "Show.S02E05.mkv"),
                         playlist=playlist)
        self.calls.resolve.assert_called_once_with("test", "magnet:1", season="2", episode="5", resolve_url="")
        self.calls.build.assert_called_once_with(imdb_id="tt9", season="2", start_ep="5", first_url="https://debrid.example/e5.mkv",
                                                 series_name="Show", year="2020", poster="s.jpg", pref_hash="hash1",
                                                 first_filename="Show.S02E05.mkv")
        self.calls.prefetch.assert_called_once_with(imdb_id="tt9", season="2", next_ep=6, pref_hash="hash1")
        self.calls.resume.assert_called_once_with("tt9", "2", "5")
        self.calls.meta.assert_not_called()
        self.assertEqual(sent.args[0]["playlist"], playlist)
        self.assertEqual(sent.args[0]["mpv"], {"mode": "integrated", "playlist_count": 2})

    def test_the_file_is_chosen_with_the_film_names_only_for_films(self):
        chosen = []

        def select(query, files, aliases=()):
            chosen.append(list(aliases))
            return files[0]

        with patch.object(app.stream_engine, "select_playable_file", side_effect=select):
            self.play({"imdb_id": "tt1", "type": "movie", "title": "Film"}, [{**source(1), "title": "Film.2024.FRENCH.1080p"}],
                      lambda *a, **k: ready(), meta={"name": "Film VO"})
            self.play({"imdb_id": "tt9", "type": "series", "title": "Show"}, [source(1)], lambda *a, **k: ready())
        self.assertEqual(chosen[0][0], "Film VO")
        self.assertEqual(chosen[0][1], torrent_engine.clean_title_for_comparison("Film.2024.FRENCH.1080p"))
        self.assertEqual(chosen[1], [])

    def test_sources_set_aside_still_count_in_the_first_ten(self):
        sources = [source(n) for n in range(12)]
        skipped = [app.stream_candidate_key(item) for item in sources[:10]]
        sent = self.play({"imdb_id": "tt1", "type": "movie", "title": "Film", "exclude_source_keys": skipped}, sources,
                         lambda *a, **k: ready())
        self.assertEqual(sent.args, ({"error": "Aucune autre source prête à lire. Vous pouvez réessayer les sources écartées ou ouvrir la fiche.",
                                      "code": "no_source"},))
        self.assertEqual(sent.kwargs, {"status": 422})
        self.calls.resolve.assert_not_called()

    def test_only_valid_keys_are_set_aside_and_they_are_reported(self):
        sources = [source(1), source(2)]
        first = app.stream_candidate_key(sources[0])
        sent = self.play({"imdb_id": "tt1", "type": "movie", "title": "Film", "exclude_source_keys": [first, "not-a-key", 7]},
                         sources, lambda *a, **k: ready())
        second = app.stream_candidate_key(sources[1])
        self.assertEqual(sent.args[0]["source_key"], second)
        self.assertEqual(sent.args[0]["attempted_source_keys"], sorted([first, second]))

    def test_unready_unplayable_and_failing_sources_give_way_to_the_next(self):
        answers = {"magnet:0": {"ready": False}, "magnet:1": {"ready": True, "files": []},
                   "magnet:2": RuntimeError("timeout"), "magnet:3": {"ready": True, "files": [{"filename": "x.mkv", "download": "ftp://no"}]},
                   "magnet:4": ready("https://debrid.example/4.mkv")}

        def debrid(token, magnet, **kwargs):
            answer = answers[magnet]
            if isinstance(answer, Exception):
                raise answer
            return answer

        sent = self.play({"imdb_id": "tt1", "type": "movie", "title": "Film"}, [source(n) for n in range(5)], debrid)
        self.assertEqual(sent.args[0]["stream_url"], "https://debrid.example/4.mkv")
        self.assertEqual(len(sent.args[0]["attempted_source_keys"]), 5)

    def test_a_player_that_fails_to_start_moves_to_the_next_source(self):
        launches = iter([RuntimeError("mpv absent"), {"pid": 2}])

        def mpv(*args, **kwargs):
            answer = next(launches)
            if isinstance(answer, Exception):
                raise answer
            return answer

        sent = self.play({"imdb_id": "tt1", "type": "movie", "title": "Film", "player_mode": "external"},
                         [source(1), source(2, 900)], lambda *a, **k: ready(), platform="linux", mpv=mpv)
        self.assertEqual(sent.args[0]["source_key"], app.stream_candidate_key(source(2)))
        self.assertEqual(self.calls.launch.call_count, 2)

    def test_a_refusing_provider_stops_at_once(self):
        def refuse(*args, **kwargs):
            raise HTTPError("https://api.example/x", 403, "Forbidden", {}, None)

        sent = self.play({"imdb_id": "tt1", "type": "movie", "title": "Film"}, [source(1), source(2)], refuse)
        self.assertEqual(sent.kwargs, {"status": 401})
        self.assertEqual(sent.args[0]["code"], "provider_auth")
        self.assertEqual(self.calls.resolve.call_count, 1)

    def test_a_refusal_only_counts_with_its_http_code(self):
        def message_only(*args, **kwargs):
            raise RuntimeError("Erreur API (401) de api.example : bad_token")

        sent = self.play({"imdb_id": "tt1", "type": "movie", "title": "Film"}, [source(1), source(2)], message_only)
        self.assertEqual(sent.kwargs, {"status": 422}, "a message alone is a failed source, not a refusal")
        self.assertEqual(self.calls.resolve.call_count, 2)

    def test_no_key_and_no_source(self):
        handler = handler_for({"imdb_id": "tt1"})
        # Without a key, KINO reads sources itself when its P2P engine is there (test_p2p.py).
        with patch.object(app, "load_config", return_value={}), patch.object(app.p2p_engine, "available", return_value=False):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.kwargs, {"status": 401})
        handler = handler_for({"imdb_id": "tt1", "type": "movie"})
        with patch.object(app, "load_config", return_value={"rd_token": "t"}), patch.object(app, "get_media_meta", return_value={}), \
                patch.object(app, "get_available_streams", return_value=[]):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args, call({"error": "Aucun flux valide trouvé pour ce titre."}, status=500))


# --- Next episode ------------------------------------------------------------

class NextEpisodePins(unittest.TestCase):
    def resolve(self, streams, debrid, params=None, *, update_history=True, cache=None):
        cache = {} if cache is None else cache
        with patch.object(player_engine, "AUTO_STREAM_CACHE", cache), \
                patch.object(player_engine, "load_config", return_value={"rd_token": "tok"}), \
                patch.object(player_engine.stream_engine, "get_available_streams", return_value=streams) as search, \
                patch.object(player_engine, "rd_debrid_magnet", side_effect=debrid) as resolve, \
                patch.object(player_engine, "record_history") as history:
            try:
                result = player_engine.resolve_auto_stream_episode(
                    params or {"imdb_id": "tt9", "season": 2, "episode": 5, "name": "Show", "year": "2020", "poster": "s.jpg"},
                    update_history=update_history)
            except RuntimeError as error:
                result = error
        self.calls = SimpleNamespace(search=search, resolve=resolve, history=history, cache=cache)
        return result

    def test_the_link_is_kept_half_an_hour_and_the_history_updated(self):
        url = self.resolve([source(1)], lambda *a, **k: ready("https://debrid.example/e5.mkv", "Show.S02E05.mkv"))
        self.assertEqual(url, "https://debrid.example/e5.mkv")
        self.calls.resolve.assert_called_once_with("tok", "magnet:1", season=2, episode=5, resolve_url="")
        self.assertEqual(self.calls.cache[("tt9", 2, 5)]["filename"], "Show.S02E05.mkv")
        self.calls.history.assert_called_once_with({"id": "tt9", "name": "Show", "type": "series", "year": "2020", "poster": "s.jpg",
                                                    "season": 2, "episode": 5, "filename": "Show.S02E05.mkv"})
        cache = self.calls.cache
        again = self.resolve([], lambda *a, **k: None, cache=cache)
        self.assertEqual(again, "https://debrid.example/e5.mkv")
        self.calls.search.assert_not_called()
        self.calls.history.assert_called_once()

    def test_downloading_does_not_touch_the_history(self):
        self.resolve([source(1)], lambda *a, **k: ready(), update_history=False)
        self.calls.history.assert_not_called()

    def test_the_previous_source_wins_only_a_tie(self):
        streams = [source(1, 900), source(2, 1000), source(3, 1000)]
        self.resolve(streams, lambda *a, **k: ready(), {"imdb_id": "tt9", "season": 2, "episode": 5, "name": "Show", "pref_hash": "HASH3"})
        self.assertEqual(self.calls.resolve.call_args.args[1], "magnet:3")
        self.resolve([source(1, 900), source(2, 1000)], lambda *a, **k: ready(),
                     {"imdb_id": "tt9", "season": 2, "episode": 5, "name": "Show", "pref_hash": "hash1"})
        self.assertEqual(self.calls.resolve.call_args.args[1], "magnet:2")

    def test_failures_try_the_next_and_the_last_error_is_told(self):
        def fail(token, magnet, **kwargs):
            raise RuntimeError(f"down {magnet}")

        error = self.resolve([source(n) for n in range(12)], fail)
        self.assertEqual(str(error), "Impossible de résoudre S02E05 (down magnet:9).")
        self.assertEqual(self.calls.resolve.call_count, 10)
        error = self.resolve([source(1)], lambda *a, **k: {"ready": False})
        self.assertEqual(str(error), "Impossible de résoudre S02E05 (aucun flux RD+ prêt).")
        error = self.resolve([], lambda *a, **k: None)
        self.assertEqual(str(error), "Aucun flux trouvé pour Show S02E05.")

    def test_no_key(self):
        with patch.object(player_engine, "load_config", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "Token API Real-Debrid manquant."):
                player_engine.resolve_auto_stream_episode({"imdb_id": "tt9"})


if __name__ == "__main__":
    unittest.main()
