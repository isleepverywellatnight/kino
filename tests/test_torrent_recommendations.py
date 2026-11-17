import sys
import types
import unittest
from unittest.mock import patch

import torrent_engine


class TestTorrentRecommendations(unittest.TestCase):
    def test_multi_subtitles_do_not_claim_french_audio_or_outrank_vf(self):
        subtitle_only = {
            "name": "Torrentio [RD+]",
            "title": "A Monster Calls - Fantasy 2016 Eng Rus Multi Subs 1080p [H264-mp4].mp4",
            "infoHash": "a" * 40,
            "behaviorHints": {"videoSize": 8 * 1024 ** 3},
        }
        french_audio = {
            "name": "Torrentio [RD+]",
            "title": "A.Monster.Calls.2016.FRENCH.1080p.H264.mp4",
            "infoHash": "b" * 40,
            "behaviorHints": {"videoSize": 8 * 1024 ** 3},
        }
        fake_addons = types.ModuleType("addon_manager")
        fake_addons.run_addons_search = lambda **_kwargs: []
        with patch.dict(sys.modules, {"addon_manager": fake_addons}):
            results = torrent_engine.search_multi_torrents(
                imdb_id="tt3416532", query_title="A Monster Calls",
                release_year="2016", pref_lang="vf",
                http_json_fn=lambda *_args, **_kwargs: {"streams": [subtitle_only, french_audio]},
                search_apibay_fn=lambda *_args: [],
            )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], french_audio["title"])
        subtitle_stream = next(item for item in results if item["title"] == subtitle_only["title"])
        self.assertNotIn("MULTI", subtitle_stream["langs"])
        self.assertIn("VO", subtitle_stream["langs"])
        self.assertNotIn("Langue souhaitée : MULTI", subtitle_stream["recommendation"]["reasons"])
        self.assertIn("Audio français non indiqué", subtitle_stream["recommendation"]["warnings"])

    def test_subtitle_language_labels_are_not_audio_evidence(self):
        cases = (
            ("A Monster Calls 2016 Eng Rus Multi-Subs 1080p", "", ["VO"]),
            ("A Monster Calls 2016 Eng Rus Multi.Subtitles 1080p", "", ["VO"]),
            ("A Monster Calls 2016 Eng 1080p", "Multi Subs: French English", ["VO"]),
            ("A Monster Calls 2016 Eng MultiSub 1080p", "", ["VO"]),
            # "Eng Subbed" names English subtitles, so it proves no audio language.
            ("A Monster Calls 2016 Eng Subbed 1080p", "", []),
            ("A Monster Calls 2016 1080p", "French English Subs", []),
            ("A Monster Calls 2016 1080p", "Subtitles: French English", []),
            ("A Monster Calls 2016 MULTI 1080p", "Multi Subs", ["MULTI"]),
            ("A Monster Calls 2016 Eng Multi Subbed 1080p", "", ["VO"]),
            ("A Monster Calls 2016 Eng 1080p", "Sous-titres multi", ["VO"]),
            ("A Monster Calls 2016 Eng Subs Multi 1080p", "", ["VO"]),
            ("A.Monster.Calls.2016.MULTi.VFF.1080p.BluRay", "", ["MULTI", "VFF"]),
            ("A.Monster.Calls.2016.MULTi.1080p.WEB", "Multi Audio", ["MULTI"]),
        )
        for title, meta, expected in cases:
            with self.subTest(title=title, meta=meta):
                self.assertEqual(torrent_engine.parse_release_details(title, meta)["langs"], expected)

    def test_english_track_needs_a_whole_word_and_french_dub_labels_are_known(self):
        cases = (
            ("Revenge.2017.FRENCH.1080p.WEB", ["VF"]),
            ("Avengers.Endgame.2019.FRENCH.1080p.BluRay", ["VF"]),
            ("The.Lengthy.Night.2020.FRENCH.720p", ["VF"]),
            ("Film.2016.TRUEFRENCH.ENG.1080p", ["MULTI", "VFF"]),
            ("Film.2016.FRENCH.ENGLISH.1080p", ["MULTI", "VF"]),
            ("A Monster Calls (2016) MULTi VF2 [1080p] BluRay x264-PopHD.mkv", ["MULTI", "VF"]),
            ("Les.Intouchables.2011.VOF.1080p", ["VF"]),
            ("Film.2016.VFI.1080p.WEB", ["VF"]),
        )
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(torrent_engine.parse_release_details(title)["langs"], expected)

    def test_original_audio_streams_warn_without_changing_the_language_preference(self):
        for title in ("A.Monster.Calls.2016.VOSTFR.1080p", "A Monster Calls 2016 Eng Rus Multi Subs 1080p"):
            with self.subTest(title=title):
                explanation = torrent_engine.explain_torrent_recommendation({"title": title}, "vf", "1080p", True)
                self.assertIn("Audio français non indiqué", explanation["warnings"])
                self.assertFalse(any(reason.startswith("Langue souhaitée") for reason in explanation["reasons"]))
        french = torrent_engine.explain_torrent_recommendation(
            {"title": "A.Monster.Calls.2016.MULTi.VFF.1080p"}, "vf", "1080p", True)
        self.assertIn("Langue souhaitée : VFF", french["reasons"])
        self.assertNotIn("Audio français non indiqué", french["warnings"])
        subtitled = torrent_engine.explain_torrent_recommendation(
            {"title": "A.Monster.Calls.2016.VOSTFR.1080p"}, "vostfr", "1080p", True)
        self.assertEqual(subtitled["warnings"], [])

    def test_instant_original_audio_ranks_before_the_best_french_source_not_ready(self):
        # User decision (2026-11-08): with VF preferred, a source ready right away
        # comes first even in VO; the French one stays listed below, with its reasons.
        french_not_ready = {
            "title": "Le.Film.2016.MULTi.VFF.2160p.BluRay.REMUX.SDR.TrueHD.Atmos-GROUPE",
            "seeders": 400, "size_gb": 40.0, "is_instant": False,
        }
        original_ready = {
            "title": "Le.Film.2016.1080p.WEB-DL.ENG.x264", "seeders": 0, "size_gb": 2.0, "is_instant": True,
        }
        for item in (french_not_ready, original_ready):
            self.assertTrue(torrent_engine.is_plausible_torrent_size(item, media_type="movie", runtime_minutes=120))
        scores = {
            name: torrent_engine.score_torrent(item, pref_lang="vf", pref_quality="4k", media_type="movie",
                                               runtime_minutes=120, target_year="2016")
            for name, item in (("french", french_not_ready), ("original", original_ready))
        }
        self.assertGreater(scores["original"], scores["french"])
        self.assertGreater(scores["french"], 0, "the French source is ranked lower, not discarded")
        explanation = torrent_engine.explain_torrent_recommendation(original_ready, "vf", "4k", True)
        self.assertIn("Audio français non indiqué", explanation["warnings"])

    def test_comet_returns_every_release_with_a_debrid_key(self):
        import base64
        import json
        requested = []

        def fake_http(url, *_args, **_kwargs):
            requested.append(url)
            return {"streams": []}

        fake_addons = types.ModuleType("addon_manager")
        fake_addons.run_addons_search = lambda **_kwargs: []
        with patch.dict(sys.modules, {"addon_manager": fake_addons}):
            torrent_engine.search_multi_torrents(
                imdb_id="tt3416532", token="test-key", tio_key="realdebrid", provider="realdebrid",
                query_title="A Monster Calls", http_json_fn=fake_http, search_apibay_fn=lambda *_args: [],
            )
        comet = next(url for url in requested if "comet." in url)
        config = json.loads(base64.b64decode(comet.split("/")[3]))
        self.assertEqual(config["maxResultsPerResolution"], 0)
        self.assertFalse(config["cachedOnly"])

    def test_numbers_in_the_title_are_not_release_years(self):
        cases = (
            ("2001: A Space Odyssey", "1968", "2001.A.Space.Odyssey.1080p.BluRay.x264.mkv", True),
            ("2001: A Space Odyssey", "1968", "2001.A.Space.Odyssey.1968.1080p.BluRay.mkv", True),
            ("2001: A Space Odyssey", "1968", "2010.The.Year.We.Make.Contact.1984.1080p.mkv", False),
            ("2012", "2009", "2012.1080p.BluRay.x264.mkv", True),
            ("2012", "2009", "2012.2009.1080p.BluRay.x264.mkv", True),
            ("Dune", "2021", "Dune.1984.1080p.BluRay.x264.mkv", False),
            ("Monster", "2004", "Monster.2026.S01E01.1080p.mkv", False),
        )
        for title, year, release, expected in cases:
            with self.subTest(release=release):
                self.assertIs(torrent_engine.is_valid_release_for_show(release, title, year), expected)
        item = {"title": "2001.A.Space.Odyssey.1080p.BluRay.x264.mkv", "size_gb": 10.0}
        score = torrent_engine.score_torrent(item, target_year="1968", title_years=[2001])
        self.assertGreater(score, 0, "the title number no longer costs 10,000 points")

    def test_accents_apostrophes_and_sequels(self):
        cases = (
            ("Amélie", "2001", "Amelie.2001.1080p.BluRay.x264.mkv", True),
            ("Les Misérables", "2012", "Les.Miserables.2012.1080p.BluRay.x264.mkv", True),
            ("Bienvenue chez les Ch'tis", "2008", "Bienvenue.Chez.Les.Chtis.2008.FRENCH.1080p.mkv", True),
            ("Le Fabuleux Destin d'Amélie Poulain", "2001", "Le.Fabuleux.Destin.d.Amelie.Poulain.2001.MULTi.mkv", True),
            ("Ocean's Eleven", "2001", "Oceans.Eleven.2001.1080p.BluRay.mkv", True),
            ("Œdipe roi", "1967", "Oedipe.Roi.1967.1080p.mkv", True),
            ("Léon", "1994", "Leon.Morin.Priest.1961.1080p.mkv", False),
            ("Ocean's Eleven", "2001", "Oceans.Twelve.2004.1080p.BluRay.mkv", False),
            ("Toy Story", "1995", "Toy.Story.2.1080p.BluRay.x264.mkv", False),
            ("Toy Story 2", "1999", "Toy.Story.2.1999.1080p.BluRay.x264.mkv", True),
            ("Les Visiteurs", "1993", "Les.Visiteurs.2.Les.Couloirs.du.Temps.1998.1080p.mkv", False),
        )
        for title, year, release, expected in cases:
            with self.subTest(title=title, release=release):
                self.assertIs(torrent_engine.is_valid_release_for_show(release, title, year), expected)

    def test_html_entities_in_release_names_are_decoded(self):
        parsed = torrent_engine.parse_stremio_stream(
            {"title": "Les Mis&eacute;rables TRUEFRENCH DVDRiP AC3 2013", "infoHash": "a" * 40}, "Torrentio")
        self.assertEqual(parsed["title"], "Les Misérables TRUEFRENCH DVDRiP AC3 2013")
        self.assertIn("VFF", parsed["langs"])

    def test_a_monster_calls_rejects_same_year_wrong_film(self):
        self.assertTrue(torrent_engine.is_valid_release_for_show(
            "A.Monster.Calls.2016.1080p.BluRay.x264.mkv", "A Monster Calls", "2016"))
        self.assertFalse(torrent_engine.is_valid_release_for_show(
            "The.Monster.2016.1080p.BluRay.x264.mkv", "A Monster Calls", "2016"))

    def test_french_title_can_use_verified_original_alias(self):
        releases = [
            {"title": "A.Monster.Calls.2016.1080p.BluRay.x264.mkv", "infoHash": "b" * 40,
             "behaviorHints": {"videoSize": 2 * 1024 ** 3}},
            {"title": "The.Monster.2016.1080p.BluRay.x264.mkv", "infoHash": "c" * 40,
             "behaviorHints": {"videoSize": 2 * 1024 ** 3}},
        ]
        fake_addons = types.ModuleType("addon_manager")
        fake_addons.run_addons_search = lambda **_kwargs: []
        with patch.dict(sys.modules, {"addon_manager": fake_addons}):
            results = torrent_engine.search_multi_torrents(
                imdb_id="tt3416532", query_title="Quelques minutes après minuit",
                title_aliases=["A Monster Calls"], release_year="2016",
                http_json_fn=lambda *_args, **_kwargs: {"streams": releases},
                search_apibay_fn=lambda *_args: [],
            )
        self.assertEqual([item["title"] for item in results], [releases[0]["title"]])
        self.assertIn("Titre cohérent", results[0]["recommendation"]["reasons"])
        self.assertIn("Langue non indiquée", results[0]["recommendation"]["warnings"])

    def test_episode_markers_filter_wrong_episodes_and_keep_packs_uncertain(self):
        self.assertEqual(torrent_engine.release_episode_status("Series.S01E03.1080p", 1, 3), "match")
        self.assertEqual(torrent_engine.release_episode_status("Series.S01E04.1080p", 1, 3), "mismatch")
        self.assertEqual(torrent_engine.release_episode_status("Series.S01E02-E04.1080p", 1, 3), "match")
        self.assertEqual(torrent_engine.release_episode_status("Series.S01.Complete.1080p", 1, 3), "unknown")
        self.assertEqual(torrent_engine.release_episode_status("Series.S02.Complete.1080p", 1, 3), "mismatch")

    def test_anime_absolute_and_season_named_episode_numbers(self):
        cases = (
            ("[SubsPlease] Sousou no Frieren - 12 (1080p) [ABCD1234].mkv", 1, 12, "match"),
            ("[SubsPlease] Sousou no Frieren - 13 (1080p) [ABCD1234].mkv", 1, 12, "mismatch"),
            ("[Erai-raws] Sousou no Frieren - 05v2 [1080p].mkv", 1, 5, "match"),
            ("[SubsPlease] Sousou no Frieren (01-28) (1080p) [Batch]", 1, 12, "unknown"),
            ("[Group] Frieren - 01 ~ 28 (1080p) [Batch]", 1, 12, "unknown"),
            ("[SubsPlease] Dandadan - 13 (1080p).mkv", 2, 1, "unknown"),
            ("[SubsPlease] Spy x Family Part 2 - 01 (1080p).mkv", 1, 13, "unknown"),
            ("[Group] Kaguya-sama 2nd Season - 03 (1080p).mkv", 2, 3, "match"),
            ("[Group] Kaguya-sama 2nd Season - 03 (1080p).mkv", 1, 3, "mismatch"),
            ("[Group] Show S2 - 05 (1080p).mkv", 2, 5, "match"),
            ("[ASW] Kimi no Na wa - 2016 (1080p)", 1, 1, "unknown"),
            ("Series.Season.2.Complete.1080p", 1, 3, "mismatch"),
        )
        for name, season, episode, expected in cases:
            with self.subTest(name=name, season=season, episode=episode):
                self.assertEqual(torrent_engine.release_episode_status(name, season, episode), expected)

    def test_later_seasons_use_the_absolute_number_when_known(self):
        status = torrent_engine.release_episode_status
        self.assertEqual(status("[SubsPlease] Jujutsu Kaisen - 25 (1080p).mkv", 2, 1, 25), "match")
        self.assertEqual(status("[SubsPlease] Jujutsu Kaisen - 26 (1080p).mkv", 2, 1, 25), "mismatch")
        self.assertEqual(status("[Group] Jujutsu Kaisen - 01 (1080p).mkv", 2, 1, 25), "unknown")
        self.assertEqual(status("[SubsPlease] Jujutsu Kaisen - 25 (1080p).mkv", 2, 1), "unknown")
        self.assertEqual(status("[Group] Jujutsu Kaisen Part 2 - 25 (1080p).mkv", 2, 1, 25), "unknown")

    def test_recommendation_explains_signals_without_inventing_resolution(self):
        item = {"title": "Example VOSTFR", "is_instant": True}
        explanation = torrent_engine.explain_torrent_recommendation(item, "vostfr", "1080p", True, "unknown")
        self.assertIn("Disponibilité immédiate signalée", explanation["reasons"])
        self.assertIn("Langue souhaitée : VOSTFR", explanation["reasons"])
        self.assertIn("Épisode à vérifier dans le fichier", explanation["warnings"])
        self.assertFalse(any("Qualité" in reason for reason in explanation["reasons"]))

    def test_french_indexer_name_does_not_imply_a_french_audio_track(self):
        stream = {"name": "Torrentio FR [RD+]", "title": "Example.2016.1080p.WEB", "infoHash": "a" * 40}
        parsed = torrent_engine.parse_stremio_stream(stream, "Torrentio FR")
        self.assertEqual(parsed["langs"], [])
        self.assertTrue(parsed["is_instant"])

    def test_unrelated_release_is_not_restored_when_all_matches_fail(self):
        stream = {
            "title": "My Little Monster S01E01 1080p WEB AAC",
            "infoHash": "a" * 40,
            "behaviorHints": {"videoSize": 2 * 1024 ** 3},
        }
        fake_addons = types.ModuleType("addon_manager")
        fake_addons.run_addons_search = lambda **_kwargs: []

        with patch.dict(sys.modules, {"addon_manager": fake_addons}):
            results = torrent_engine.search_multi_torrents(
                imdb_id="tt0000001",
                media_type="series",
                season=1,
                episode=1,
                query_title="Monster",
                release_year="2004",
                http_json_fn=lambda *_args, **_kwargs: {"streams": [stream]},
                search_apibay_fn=lambda *_args: [],
            )

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
