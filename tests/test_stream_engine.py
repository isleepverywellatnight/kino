#!/usr/bin/env python3
"""
Test Suite for KINO stream_engine.py
Vérifie la frontière du module profond stream_engine :
1. Normalisation et nettoyage de StreamQuery
2. Filtrage des homonymes (Monster 2004 vs Monsterland vs My Little Monster)
3. Support des titres animés Romaji (Sousou no Frieren)
4. Fonctionnement de l'adaptateur de débridage (FakeDebridAdapter)
5. Résolution de PlayableStream
"""

import sys
import unittest
from unittest.mock import patch
import debrid_engine
from stream_engine import (
    StreamQuery,
    PlayableStream,
    FakeDebridAdapter,
    DebridResolutionError,
    get_available_streams,
    resolve_playable_stream,
    select_playable_file,
    is_valid_release_for_show,
    is_release_relevant,
)


class TestStreamEngine(unittest.TestCase):

    def test_tv_query_uses_series_identity(self):
        query = StreamQuery.from_params({"type": "tv", "imdb_id": "tt0000001", "season": 2, "episode": 3}, {})
        self.assertEqual((query.media_type, query.season, query.episode), ("series", 2, 3))

    def test_unique_generic_file_remains_playable(self):
        movie = StreamQuery(imdb_id="tt3416532", title="Quelques minutes après minuit", release_year="2016")
        generic_movie = {"filename": "video.mkv", "download": "https://example.invalid/movie"}
        self.assertIs(select_playable_file(movie, [generic_movie], ["A Monster Calls"]), generic_movie)

        series = StreamQuery(imdb_id="tt0000001", media_type="series", season=1, episode=2)
        generic_episode = {"filename": "episode.mkv", "download": "https://example.invalid/episode"}
        self.assertIs(select_playable_file(series, [generic_episode]), generic_episode)

        wrong_season = {"filename": "Example.S02.Complete.mkv", "download": "https://example.invalid/wrong"}
        self.assertIsNone(select_playable_file(series, [wrong_season]))

    def test_accented_titles_and_numbered_sequels_in_debrided_files(self):
        amelie = StreamQuery(imdb_id="tt0211915", title="Le Fabuleux Destin d'Amélie Poulain", release_year="2001")
        single = {"filename": "Amelie.2001.1080p.BluRay.mkv", "download": "https://example.invalid/amelie"}
        self.assertIs(select_playable_file(amelie, [single], ["Amélie"]), single)

        toy_story = StreamQuery(imdb_id="tt0114709", title="Toy Story", release_year="1995")
        sequel = {"filename": "Toy.Story.2.1080p.mkv", "download": "https://example.invalid/2", "filesize_bytes": 9}
        first = {"filename": "Toy.Story.1080p.mkv", "download": "https://example.invalid/1", "filesize_bytes": 8}
        self.assertIs(select_playable_file(toy_story, [sequel, first]), first)
        self.assertIsNone(select_playable_file(toy_story, [sequel]), "a lone sequel file is not launched")

    def test_lone_anime_file_of_another_episode_is_not_launched(self):
        frieren = StreamQuery(imdb_id="tt22248376", media_type="series", season=1, episode=12)
        wrong = {"filename": "[SubsPlease] Sousou no Frieren - 13 (1080p).mkv", "download": "https://example.invalid/13"}
        right = {"filename": "[SubsPlease] Sousou no Frieren - 12 (1080p).mkv", "download": "https://example.invalid/12"}
        self.assertIsNone(select_playable_file(frieren, [wrong]))
        self.assertIs(select_playable_file(frieren, [wrong, right]), right)

    def test_later_season_anime_pack_picks_the_absolute_episode(self):
        from unittest.mock import patch
        import meta_engine
        query = StreamQuery(imdb_id="tt12343534", media_type="series", season=2, episode=1)
        files = [{"filename": f"[SubsPlease] Jujutsu Kaisen - {n} (1080p).mkv",
                  "download": f"https://example.invalid/{n}", "filesize_bytes": 100 - n} for n in (24, 25, 26)]
        with patch.object(meta_engine, "absolute_episode_number", return_value=25):
            self.assertIs(select_playable_file(query, files), files[1])
            self.assertIsNone(select_playable_file(query, [files[2]]))

    def test_uncertain_multi_file_pack_is_not_started_automatically(self):
        movie = StreamQuery(imdb_id="tt3416532", title="A Monster Calls", release_year="2016")
        unrelated = [
            {"filename": "The.Monster.2016.mkv", "download": "https://example.invalid/one"},
            {"filename": "The.Monster.2016.Extras.mkv", "download": "https://example.invalid/two"},
        ]
        self.assertIsNone(select_playable_file(movie, unrelated))

    def test_explicit_wrong_movie_name_without_year_is_rejected(self):
        movie = StreamQuery(imdb_id="tt3416532", title="Quelques minutes après minuit", release_year="2016")
        wrong = {"filename": "The.Monster.mkv", "download": "https://example.invalid/wrong"}
        self.assertIsNone(select_playable_file(movie, [wrong], ["A Monster Calls"]))

    def test_unrestricted_file_size_is_available_for_selection(self):
        def unrestricted(_url, **_kwargs):
            return {"filename": "A.Monster.Calls.2016.mkv", "filesize": 3_000_000_000,
                    "download": "https://example.invalid/movie"}
        with patch.object(debrid_engine, "http_json", side_effect=unrestricted):
            result = debrid_engine.unrestrict_torrent_links("test-token", {"id": "one", "links": ["https://example.invalid/link"]})
        self.assertEqual(result["files"][0]["filesize_bytes"], 3_000_000_000)

    def test_stream_query_normalization(self):
        """Vérifie la création propre de StreamQuery depuis des paramètres bruts."""
        raw_params = {
            "id": "tt22248374",  # Alias de Frieren
            "type": "series",
            "season": "1",
            "episode": "4",
            "title": "Frieren: Beyond Journey's End — S01E04",
            "year": "2023-2024",
            "runtime": "24 min",
        }
        q = StreamQuery.from_params(raw_params)
        self.assertEqual(q.imdb_id, "tt22248376")  # Alias résolu
        self.assertEqual(q.media_type, "series")
        self.assertEqual(q.season, 1)
        self.assertEqual(q.episode, 4)
        self.assertEqual(q.title, "Frieren: Beyond Journey's End")
        self.assertEqual(q.release_year, "2023")
        self.assertEqual(q.runtime_minutes, 24)

    def test_cache_key_changes_with_recommendation_preferences(self):
        french = StreamQuery(imdb_id="tt0000001", title="Monster", pref_lang="vf")
        subtitled = StreamQuery(imdb_id="tt0000001", title="Monster", pref_lang="vostfr")
        self.assertNotEqual(french.cache_key("realdebrid", True), subtitled.cache_key("realdebrid", True))

    def test_homonym_filtering_monster(self):
        """Vérifie que Monster (2004) rejette strictement les homonymes."""
        target = "Monster"
        year = "2004"

        # Doit être accepté
        valid_releases = [
            "Monster.2004.S01E01.Herr.Dr.Tenma.1080p.BluRay.DUAL.FLAC.2.0.x264-Kitsune.mkv",
            "Monster (2004) - S01E01 - Herr Dr. Tenma [Bluray-720p]",
            "Monster S01E01 MULTi 480p WEB x264 AAC -Tsundere-Raws (ADN).mkv",
            "Naoki Urasawa Monster S01E01 1080p",
        ]
        for rel in valid_releases:
            self.assertTrue(
                is_valid_release_for_show(rel, target, year),
                f"Release légitime rejetée à tort : {rel}"
            )

        # Doit être rejeté (homonymes ou dérivés)
        polluted_releases = [
            "My Little Monster S01E01 VOSTFR 1080p WEB x264 AAC",
            "Monsterland S01 1080p",
            "Monsters: The Lyle and Erik Menendez Story S01",
            "Monster Garage S01E01 HDTV",
            "Monster Hunter S01 720p",
            "Re:Monster S01E01 1080p",
        ]
        for rel in polluted_releases:
            self.assertFalse(
                is_valid_release_for_show(rel, target, year),
                f"Homonyme polluant non filtré : {rel}"
            )

    def test_anime_romaji_matching(self):
        """Vérifie que les animés avec titres japonais/romaji sont reconnus."""
        frieren_query = "Frieren: Beyond Journey's End"
        frieren_rel = "[SubsPlease] Sousou no Frieren - 01 (1080p) [F08BC21A].mkv"
        self.assertTrue(is_valid_release_for_show(frieren_rel, frieren_query, "2023"))

    def test_fake_debrid_adapter_resolution(self):
        """Vérifie la résolution de flux via l'adaptateur sans dépendance externe."""
        fake_adapter = FakeDebridAdapter(instant_hashes=["abc123hash"])
        
        # Test availability
        avail = fake_adapter.check_instant_availability(["abc123hash", "unknwonhash"])
        self.assertTrue(avail["abc123hash"])
        self.assertFalse(avail["unknwonhash"])

        # Test resolution
        stream_dict = {
            "title": "Monster (2004) 1080p BluRay",
            "info_hash": "abc123hash",
            "magnet": "magnet:?xt=urn:btih:abc123hash&dn=Monster",
            "qualities": ["1080p", "BluRay"],
        }
        playable = resolve_playable_stream(stream_dict, debrid_adapter=fake_adapter)
        self.assertIsInstance(playable, PlayableStream)
        self.assertTrue(playable.url.startswith("https://mock-cdn.kino.local"))
        self.assertEqual(playable.quality, "1080p")
        self.assertEqual(playable.provider, "fake")

    def test_missing_magnet_raises_error(self):
        """Vérifie qu'un flux invalide lève une exception explicite DebridResolutionError."""
        fake_adapter = FakeDebridAdapter()
        invalid_stream = {"title": "Empty Stream"}
        with self.assertRaises(DebridResolutionError):
            resolve_playable_stream(invalid_stream, debrid_adapter=fake_adapter)


if __name__ == "__main__":
    unittest.main()
