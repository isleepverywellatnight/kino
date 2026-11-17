"""The trailer shown is in the requested language and names the right film."""

import json
import unittest

import meta_engine


def page(videos):
    data = {"contents": {"sectionList": [{"videoRenderer": {
        "videoId": video_id, "title": {"runs": [{"text": title}]}, "lengthText": {"simpleText": length}}}
        for video_id, title, length in videos]}}
    return f"<script>var ytInitialData = {json.dumps(data)};</script>"


class TrailerPickTests(unittest.TestCase):
    def test_french_trailer_of_the_right_film_is_chosen(self):
        html = page([
            ("intl0000000", "Les Misérables - Bande annonce internationale HD", "2:42"),
            ("short000000", "Les Misérables VF #shorts", "0:15"),
            ("other000000", "LES MISÉRABLES – Bande-annonce VF (2026)", "2:03"),
            ("vf000000000", "Les Misérables Bande Annonce VF Musicale (Teaser)", "1:28"),
            ("review00000", "Les Misérables critique VF complète", "24:10"),
        ])
        results = meta_engine._youtube_video_results(html)
        self.assertEqual(len(results), 5)
        self.assertEqual(results[0], ("intl0000000", "Les Misérables - Bande annonce internationale HD", 162))
        self.assertEqual(meta_engine.pick_trailer(results, "Les Misérables", "2012", "vf"), "vf000000000")

    def test_original_version_prefers_vostfr_or_official_trailer(self):
        results = meta_engine._youtube_video_results(page([
            ("vf000000000", "Dune - Bande-Annonce Officielle (VF)", "3:40"),
            ("vo000000000", "Dune - Official Trailer", "3:05"),
        ]))
        self.assertEqual(meta_engine.pick_trailer(results, "Dune", "2021", "vo"), "vo000000000")

    def test_unreadable_page_falls_back(self):
        self.assertEqual(meta_engine._youtube_video_results("<html>no data</html>"), [])
        self.assertIsNone(meta_engine.pick_trailer([], "Dune", "2021"))


if __name__ == "__main__":
    unittest.main()
