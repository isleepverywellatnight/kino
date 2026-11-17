"""Subtitles timed for the played release come first; partial (CD1) ones sink."""

import unittest
from unittest.mock import patch

import meta_engine

FEVER = {"url": "https://example.invalid/fever.srt", "lang": "fre", "releaseGroup": "FEVER", "releaseFormat": "HDTV",
         "subtitleFileName": "Game of Thrones - 1x01 - Episode 1.HDTV.FEVER.fr.srt",
         "movieReleaseName": "Game of Thrones - 1x01 - Episode 1.HDTV.FEVER"}
REWARD = {"url": "https://example.invalid/reward.srt", "lang": "fre", "releaseGroup": "REWARD", "releaseFormat": "BluRay",
          "subtitleFileName": "Game.of.Thrones.S01E01.720p.BluRay.X264-REWARD.fre.srt",
          "movieReleaseName": "Game.Of.Thrones.S01.720p.BluRay.x264-MIXED"}


def fetch(subtitles, release=""):
    with patch.object(meta_engine, "cached_get", side_effect=lambda _key, _ttl, fn: fn()), \
         patch.object(meta_engine, "http_json", return_value={"subtitles": subtitles}):
        return meta_engine.fetch_opensubtitles("tt0944947", "series", 1, 1, release)


class SubtitleRankingTests(unittest.TestCase):
    def test_subtitles_timed_for_the_played_release_come_first(self):
        played = "Game.of.Thrones.S01E01.1080p.BluRay.x264-REWARD.mkv"
        self.assertEqual([s["url"] for s in fetch([FEVER, REWARD], played)], [REWARD["url"], FEVER["url"]])
        self.assertEqual([s["url"] for s in fetch([FEVER, REWARD], "Game.of.Thrones.S01E01.HDTV.x264-FEVER.mkv")],
                         [FEVER["url"], REWARD["url"]])
        self.assertEqual([s["url"] for s in fetch([FEVER, REWARD])], [FEVER["url"], REWARD["url"]],
                         "without a release name the provider order is kept")

    def test_partial_cd_subtitles_sink(self):
        cd1 = {"url": "https://example.invalid/cd1.srt", "lang": "fre",
               "subtitleFileName": "A.Space.Odyssey.CE.1968.DVDRip.DivX-QiX.CD1.srt"}
        full = {"url": "https://example.invalid/full.srt", "lang": "fre",
                "subtitleFileName": "2001.A.Space.Odyssey.1968.1080p.BluRay.srt"}
        ranked = fetch([cd1, full])
        self.assertEqual([s["url"] for s in ranked], [full["url"], cd1["url"]])
        self.assertEqual([s["partial"] for s in ranked], [False, True])
        self.assertLess(meta_engine.subtitle_fit({"file": "Film.Part2.srt"}), 0)
        self.assertEqual(meta_engine.subtitle_fit({"file": "Film.2160p.srt"}), 0, "a resolution is not a part number")

    def test_sdh_and_forced_are_read_from_file_names(self):
        sdh = {"url": "https://example.invalid/sdh.srt", "lang": "eng", "subtitleFileName": "Film.2016.SDH.eng.srt"}
        forced = {"url": "https://example.invalid/forced.srt", "lang": "fre", "subtitleFileName": "Film.2016.FORCED.fre.srt"}
        variants = {s["url"]: s["variant"] for s in fetch([sdh, forced])}
        self.assertEqual(variants, {sdh["url"]: "sdh", forced["url"]: "forced"})


    def test_frame_rate_of_the_played_file_is_preferred(self):
        ntsc = {"url": "https://example.invalid/23976.srt", "lang": "fre", "fpsMilli": 23976, "subtitleFileName": "Film.srt"}
        pal = {"url": "https://example.invalid/25.srt", "lang": "fre", "fpsMilli": 25000, "subtitleFileName": "Film.srt"}
        with patch.object(meta_engine, "cached_get", side_effect=lambda _key, _ttl, fn: fn()), \
             patch.object(meta_engine, "http_json", return_value={"subtitles": [ntsc, pal]}):
            on_pal = meta_engine.fetch_opensubtitles("tt1", "movie", fps=25.0)
            on_ntsc = meta_engine.fetch_opensubtitles("tt1", "movie", fps=23.976)
            unknown = meta_engine.fetch_opensubtitles("tt1", "movie")
        self.assertEqual([s["url"] for s in on_pal], [pal["url"], ntsc["url"]])
        self.assertEqual([s["url"] for s in on_ntsc], [ntsc["url"], pal["url"]])
        self.assertEqual([s["url"] for s in unknown], [ntsc["url"], pal["url"]])


class FrameRateProbeTests(unittest.TestCase):
    def test_probe_reads_the_video_frame_rate(self):
        import json
        import media_probe
        output = json.dumps({"format": {}, "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "avg_frame_rate": "24000/1001"},
            {"index": 1, "codec_type": "video", "codec_name": "mjpeg", "avg_frame_rate": "0/0"},
        ]})
        self.assertEqual([v["fps"] for v in media_probe.parse_probe_json(output)["video"]], [23.976, None])
        ffmpeg_text = "Input #0, matroska,webm, from 'x':\n  Stream #0:0(eng): Video: h264 (High), yuv420p, 1920x1080, 25 fps, 25 tbr (default)\n"
        self.assertEqual(media_probe.parse_ffmpeg_output(ffmpeg_text)["video"][0]["fps"], 25.0)

if __name__ == "__main__":
    unittest.main()
