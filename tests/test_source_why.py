"""Pourquoi cette source : each source carries the parts of its score and
their reasons in plain words; the score itself does not change."""
import unittest
from unittest.mock import patch

import addon_manager
import torrent_engine
from test_playback_pins import APIBAY, MOVIE_STREAMS, fake_http


def reasons(title, *, size=9.0, seeders=0, instant=False, source="Torrentio", **profile):
    item = {"title": title, "size_gb": size, "seeders": seeders, "is_instant": instant, "source": source}
    settings = dict(pref_lang="vf", pref_quality="4k", hdr_mode="sdr_pref", media_type="movie", runtime_minutes=None,
                    target_year="", title_years=())
    settings.update(profile)
    prof = torrent_engine.ScoreProfile(**settings)
    details = torrent_engine.release_details(item)
    found = torrent_engine.score_reasons(item, details, torrent_engine.score_parts(item, details, prof), prof)
    return {reason["part"]: (reason["text"], reason["tone"]) for reason in found}


class ReasonTests(unittest.TestCase):
    def test_a_ready_french_4k_release(self):
        why = reasons("Film.2024.MULTI.VFF.2160p.UHD.BluRay.REMUX.SDR.TrueHD.Atmos", size=60.0, seeders=40, instant=True,
                      target_year="2024")
        self.assertEqual(why, {
            "instant_points": ("Prête tout de suite", "good"),
            "quality_points": ("4K REMUX, la qualité choisie", "good"),
            "language_points": ("VF + VO", "good"),
            "picture_points": ("Image SDR, adaptée à votre écran", "good"),
            "sound_points": ("Son Dolby Atmos", "good"),
            "seeder_points": ("Bien partagée (40 pairs)", "good"),
            "size_points": ("Fichier lourd (35 à 65 Go)", "neutral"),
            "year_points": ("Année 2024 confirmée", "good"),
        })

    def test_what_holds_a_release_back(self):
        why = reasons("Film.2021.VOSTFR.720p.DV.HDTS", size=40.0, target_year="2024", runtime_minutes=120)
        self.assertEqual(why["instant_points"], ("Disponibilité immédiate non signalée", "bad"))
        self.assertEqual(why["quality_points"], ("720p, en dessous de la qualité choisie", "bad"))
        self.assertEqual(why["language_points"], ("VOSTFR seulement", "bad"))
        self.assertEqual(why["picture_points"], ("Dolby Vision seul : couleurs faussées possibles", "bad"))
        self.assertEqual(why["fake_penalty"], ("Copie filmée en salle (CAM, TS)", "bad"))
        self.assertEqual(why["year_points"], ("Autre année : sans doute une autre œuvre", "bad"))
        self.assertNotIn("seeder_points", why, "few seeders say nothing")
        self.assertNotIn("sound_points", why)

    def test_the_words_follow_the_settings(self):
        self.assertEqual(reasons("Film.VOSTFR.1080p.WEB", pref_lang="vostfr", pref_quality="1080p")["language_points"],
                         ("VOSTFR, la langue choisie", "good"))
        self.assertEqual(reasons("Film.VFF.1080p.WEB", pref_lang="vostfr")["language_points"], ("Doublée en français", "bad"))
        self.assertEqual(reasons("Film.1080p.WEB", pref_lang="vostfr")["language_points"],
                         ("Version originale, sous-titres non indiqués", "neutral"))
        self.assertEqual(reasons("Film.2160p.WEB", pref_quality="1080p")["quality_points"], ("4K, au-dessus de la qualité choisie", "neutral"))
        self.assertEqual(reasons("Film.WEB")["quality_points"], ("Qualité non indiquée", "neutral"), "1080p is assumed, not claimed")
        self.assertEqual(reasons("Film.2160p.HDR.DV.WEB", hdr_mode="hdr_native")["picture_points"],
                         ("HDR / Dolby Vision, pour votre écran", "good"))
        self.assertEqual(reasons("Film.2160p.HDR.WEB")["picture_points"],
                         ("HDR, converti pour votre écran standard", "neutral"))
        self.assertEqual(reasons("Film.VFQ.1080p")["language_points"], ("VF québécoise", "neutral"))
        self.assertEqual(reasons("Film.1080p.WEB")["language_points"], ("Pas de VF annoncée", "bad"))
        self.assertEqual(reasons("[SubsPlease] Show - 01 (1080p)", source="nyaasi", size=1.4)["anime_group_points"],
                         ("Équipe anime reconnue, via Nyaa", "good"))

    def test_no_reason_names_a_file_or_a_link(self):
        why = reasons("Secret.Name.2024.MULTI.VFF.2160p.WEB-DL", target_year="2024", instant=True)
        for text, _ in why.values():
            self.assertNotIn("Secret", text)
            self.assertNotIn("http", text)


class ImagePartTests(unittest.TestCase):
    """KINO 1.1.80: on a standard screen the player converts HDR well (measured), so
    SDR only wins a tie and only Dolby Vision alone is held back."""

    def points(self, title, hdr_mode="sdr_pref"):
        return dict(torrent_engine.score_breakdown({"title": title, "size_gb": 9.0}, hdr_mode=hdr_mode))["picture_points"]

    def test_standard_screen(self):
        self.assertEqual(self.points("Film.2024.1080p.WEB"), 40)
        for tag in ("HDR", "HDR10", "HDR10+", "HLG", "DV.HDR"):
            self.assertEqual(self.points(f"Film.2024.2160p.{tag}.WEB"), 0, tag)
        self.assertEqual(self.points("Film.2024.2160p.DV.WEB"), -350)
        self.assertEqual(reasons("Film.2160p.DV.HDR.WEB")["picture_points"], ("HDR, converti pour votre écran standard", "neutral"))
        self.assertEqual(reasons("Film.1080p.WEB")["picture_points"], ("Image SDR, adaptée à votre écran", "good"))

    def test_the_other_image_settings_are_unchanged(self):
        self.assertEqual(self.points("Film.2160p.DV.HDR.WEB", "hdr_native"), 110)
        self.assertEqual(self.points("Film.2160p.DV.WEB", "hdr_boost"), -150)
        self.assertEqual(self.points("Film.1080p.WEB", "hdr_boost"), 100)


class SearchTests(unittest.TestCase):
    def test_each_source_has_its_parts_and_reasons_and_the_same_score(self):
        with patch.object(addon_manager, "run_addons_search", return_value=[]):
            results = torrent_engine.search_multi_torrents(
                "tt15239678", "movie", token="KEY", tio_key="realdebrid", runtime_minutes=166,
                http_json_fn=fake_http(MOVIE_STREAMS), search_apibay_fn=lambda q: APIBAY, query_title="Dune Part Two",
                release_year="2024")
        self.assertEqual([r["score"] for r in results], [4690, 4490, 1970, 1645, 870], "the pinned scores")
        for result in results:
            self.assertEqual(sum(result["score_parts"].values()), result["score"])
            self.assertEqual(list(result["score_parts"]), [part.__name__.strip("_") for part in torrent_engine.SCORE_PARTS])
            self.assertTrue(all(set(reason) == {"part", "text", "tone", "points"} for reason in result["why"]))
            self.assertTrue(all(reason["points"] == result["score_parts"][reason["part"]] for reason in result["why"]))
        first = {reason["text"] for reason in results[0]["why"]}
        self.assertIn("Prête tout de suite", first)
        self.assertIn("HDR, converti pour votre écran standard", first)
        second = {reason["text"] for reason in results[1]["why"]}
        self.assertIn("VOSTFR seulement", second, "the reasons also say what is less good")


if __name__ == "__main__":
    unittest.main()
