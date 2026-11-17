"""« Lire sur la TV »: finding a TV, talking DLNA to it, relaying
the source over HTTP, and following the play (history, state, commands). The
network is simulated; the description below is the one of the user's Samsung
Q60BA, trimmed."""
import unittest
from unittest.mock import patch

import tv_cast as tv

SAMSUNG = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
<friendlyName>Samsung Q60BA 55 TV</friendlyName><manufacturer>Samsung Electronics</manufacturer>
<modelName>QE55Q60BAUXXC</modelName><serviceList>
<service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType><controlURL>/upnp/control/RenderingControl1</controlURL></service>
<service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType><controlURL>/upnp/control/AVTransport1</controlURL></service>
</serviceList></device></root>"""
LOCATION = "http://192.168.1.178:9197/dmr"
DEVICE = tv.parse_description(LOCATION, SAMSUNG)


class FindingTests(unittest.TestCase):
    def test_the_samsung_description(self):
        self.assertEqual(DEVICE, {"location": LOCATION, "control_url": "http://192.168.1.178:9197/upnp/control/AVTransport1",
                                  "host": "192.168.1.178", "name": "Samsung Q60BA 55 TV", "maker": "Samsung Electronics",
                                  "model": "QE55Q60BAUXXC"})

    def test_only_players_of_the_local_network(self):
        fetched = []
        pages = {LOCATION: SAMSUNG, "http://192.168.1.254:52424/device.xml": "<root><device><friendlyName>Freebox</friendlyName></device></root>"}

        def fetch(url):
            fetched.append(url)
            return pages[url]
        found = tv.discover(search=lambda: [LOCATION, "http://192.168.1.254:52424/device.xml", "http://8.8.8.8/x.xml", LOCATION],
                            fetch=fetch)
        self.assertEqual([d["name"] for d in found], ["Samsung Q60BA 55 TV"], "a server without AVTransport is not a TV")
        self.assertNotIn("http://8.8.8.8/x.xml", fetched, "never a description outside the local network")

    def test_helpers(self):
        self.assertEqual(tv.mime_for("Film.2024.2160p.mkv"), "video/x-mkv")
        self.assertEqual(tv.mime_for("", "https://x.real-debrid.com/d/ABC/Film.mp4"), "video/mp4")
        self.assertEqual(tv.mime_for("noext"), "video/x-mkv")
        self.assertEqual((tv.hms(3725), tv.seconds_of("1:02:05"), tv.seconds_of("0:00:04.372"), tv.seconds_of("NOT_IMPLEMENTED")),
                         ("1:02:05", 3725.0, 4.0, 0.0))
        self.assertTrue(tv.is_local_url("http://192.168.1.178:9197/dmr"))
        self.assertFalse(tv.is_local_url("http://127.0.0.1:8080/"))
        self.assertFalse(tv.is_local_url("http://example.com/"))


class RendererTests(unittest.TestCase):
    def test_the_dlna_actions(self):
        calls = []
        renderer = tv.Renderer(DEVICE, call=lambda url, action, args="": calls.append((action, args)) or
                               "<CurrentTransportState>PLAYING</CurrentTransportState><RelTime>0:01:02</RelTime><TrackDuration>1:45:05</TrackDuration>")
        renderer.load("http://192.168.1.171:8080/tv/stream/abc/Film.mkv", "Dune & co", "video/x-mkv")
        renderer.play()
        renderer.seek(3725)
        self.assertEqual(renderer.status(), ("PLAYING", 62.0, 6305.0))
        action, args = calls[0]
        self.assertEqual(action, "SetAVTransportURI")
        self.assertIn("<CurrentURI>http://192.168.1.171:8080/tv/stream/abc/Film.mkv</CurrentURI>", args)
        self.assertIn("&lt;dc:title&gt;Dune &amp;amp; co&lt;/dc:title&gt;", args, "the title is escaped twice: in DIDL, then in SOAP")
        self.assertIn("http-get:*:video/x-mkv:DLNA.ORG_OP=01;", args, "byte-range seeking announced: the TV seeks only then")
        self.assertEqual(calls[1], ("Play", "<Speed>1</Speed>"))
        self.assertEqual(calls[2], ("Seek", "<Unit>REL_TIME</Unit><Target>1:02:05</Target>"))


class FakeRenderer:
    def __init__(self, info):
        self.info, self.calls, self.answers = info, [], []

    def load(self, url, title, mime): self.calls.append(("load", url, title, mime))
    def play(self): self.calls.append(("play",))
    def pause(self): self.calls.append(("pause",))
    def stop(self): self.calls.append(("stop",))
    def seek(self, seconds): self.calls.append(("seek", seconds))

    def status(self):
        return self.answers.pop(0) if self.answers else ("PLAYING", 0.0, 0.0)


class CastTests(unittest.TestCase):
    def setUp(self):
        self.saved, self.states, self.commands, self.presence = [], [], [], []
        self.now = [1000.0]
        self.renderers = []

        def make(info):
            self.renderers.append(FakeRenderer(info))
            return self.renderers[-1]
        self.cast = tv.TvCast(lambda: 8080, self.saved.append, self.states.append, lambda: self.commands.pop(0) if self.commands else [],
                              presence=self.presence.append, renderer_for=make, clock=lambda: self.now[0], ip_getter=lambda: "192.168.1.171")
        thread = patch.object(tv.threading, "Thread")
        thread.start()
        self.addCleanup(thread.stop)

    def start(self, **extra):
        media = {"id": "tt1160419", "name": "Dune", "type": "movie", "poster": "https://p/x.jpg", "year": "2021"}
        return self.cast.start(DEVICE, "https://dl.example/d/AB/Dune.2021.2160p.mkv", "Dune (2021)", media, "Dune.2021.2160p.mkv", **extra)

    def test_the_tv_gets_the_relay_not_the_https_link(self):
        status = self.start()
        renderer = self.renderers[0]
        action, url, title, mime = renderer.calls[0]
        token = self.cast.session["token"]
        self.assertEqual(url, f"http://192.168.1.171:8080/tv/stream/{token}/Dune.2021.2160p.mkv")
        self.assertEqual((title, mime, renderer.calls[1]), ("Dune (2021)", "video/x-mkv", ("play",)))
        self.assertEqual((status["active"], status["device"], status["starting"]), (True, "Samsung Q60BA 55 TV", True))

    def test_only_this_tv_with_its_token_reads_the_relay(self):
        self.start()
        token = self.cast.session["token"]
        self.assertTrue(self.cast.relay_allowed("192.168.1.178", f"/tv/stream/{token}/Dune.mkv"))
        self.assertFalse(self.cast.relay_allowed("192.168.1.50", f"/tv/stream/{token}/Dune.mkv"), "another device of the network")
        self.assertFalse(self.cast.relay_allowed("192.168.1.178", "/tv/stream/guess/Dune.mkv"))
        self.cast.stop()
        self.assertFalse(self.cast.relay_allowed("192.168.1.178", f"/tv/stream/{token}/Dune.mkv"), "closed once stopped")

    def test_following_the_play(self):
        self.start(resume_sec=1200)
        session, renderer = self.cast.session, self.renderers[0]
        renderer.answers = [("TRANSITIONING", 0, 0), ("PLAYING", 3.0, 9300.0), ("PLAYING", 1205.0, 9300.0), ("PAUSED_PLAYBACK", 1300.0, 9300.0)]
        self.cast.tick(session)
        self.assertEqual(self.saved, [], "nothing saved before it plays")
        self.cast.tick(session)
        self.assertIn(("seek", 1200.0), renderer.calls, "it resumes where it stopped")
        self.assertEqual(self.states[-1]["current_time"], 1200.0)
        self.assertEqual(self.saved[-1]["position"], 1200)
        self.now[0] += 20
        self.cast.tick(session)
        self.now[0] += 20
        self.cast.tick(session)
        self.assertEqual((self.states[-1]["paused"], self.states[-1]["title"], self.states[-1]["poster"]), (True, "Dune (2021)", "https://p/x.jpg"))
        self.assertEqual(self.saved[-1]["position"], 1300)
        self.assertEqual(self.presence[-1]["state"], "PAUSED_PLAYBACK")

    def test_the_mac_keys_and_the_phone_drive_the_tv(self):
        self.start()
        session, renderer = self.cast.session, self.renderers[0]
        renderer.answers = [("PLAYING", 60.0, 6000.0)]
        self.commands.append([{"action": "pause"}, {"action": "seek_rel", "value": 10}])
        self.cast.tick(session)
        self.assertIn(("pause",), renderer.calls)
        self.assertIn(("seek", 70.0), renderer.calls)

    def test_the_end_saves_the_film_as_seen(self):
        self.start()
        session, renderer = self.cast.session, self.renderers[0]
        renderer.answers = [("PLAYING", 5940.0, 6000.0), ("STOPPED", 0.0, 0.0)]
        self.cast.tick(session)
        self.cast.tick(session)
        self.assertEqual(self.saved[-1]["position"], 6000)
        self.assertFalse(self.cast.status()["active"])
        self.assertEqual((self.states[-1], self.presence[-1]), ({"playing": False, "paused": True}, None))

    def test_a_source_the_tv_never_starts_says_so(self):
        self.start()
        session, renderer = self.cast.session, self.renderers[0]
        renderer.answers = [("STOPPED", 0, 0)]
        self.now[0] += tv.START_TIMEOUT + 1
        self.cast.tick(session)
        self.assertEqual(self.cast.status()["error"], "La TV n'a pas pu lire cette source.")
        self.assertEqual(self.saved, [])

    def test_stop_from_kino(self):
        self.start()
        self.cast.session["played"], self.cast.session["duration"], self.cast.session["position"] = True, 6000.0, 300.0
        self.cast.control("stop")
        self.assertIn(("stop",), self.renderers[0].calls)
        self.assertEqual(self.saved[-1]["position"], 300)
        self.assertFalse(self.cast.status()["active"])

    def test_refuses_what_is_not_a_tv_or_a_source(self):
        with self.assertRaises(ValueError):
            self.cast.start(DEVICE, "file:///etc/passwd", "x")
        with self.assertRaises(ValueError):
            self.cast.start({"location": "http://8.8.8.8/x", "control_url": "http://8.8.8.8/c"}, "https://a/b.mkv", "x")


class NextEpisodeTests(unittest.TestCase):
    """at the end of an episode on the TV, the next one starts by itself."""

    def setUp(self):
        self.saved, self.states, self.commands, self.renderers = [], [], [], []
        self.now = [1000.0]
        self.asked, self.resolved = [], []
        self.series_over = False
        self.link = ("https://dl.example/d/CD/Lupin.S01E03.mkv", "Lupin.S01E03.mkv")

        def next_of(media):
            self.asked.append((media["season"], media["episode"]))
            return None if self.series_over else {"season": media["season"], "episode": media["episode"] + 1}

        def resolve(media):
            self.resolved.append((media["id"], media["season"], media["episode"]))
            return self.link

        def make(info):
            self.renderers.append(FakeRenderer(info))
            return self.renderers[-1]
        self.prefetched = []
        self.cast = tv.TvCast(lambda: 8080, self.saved.append, self.states.append,
                              lambda: self.commands.pop(0) if self.commands else [], renderer_for=make,
                              clock=lambda: self.now[0], ip_getter=lambda: "192.168.1.171",
                              next_of=next_of, resolve=resolve, prefetch=self.prefetched.append)
        thread = patch.object(tv.threading, "Thread")
        self.thread = thread.start()
        self.addCleanup(thread.stop)

    def start_episode(self, kind="series"):
        media = {"id": "tt2531336", "name": "Lupin", "type": kind, "poster": "https://p/l.jpg", "year": "2021"}
        if kind == "series":
            media.update(season=1, episode=2)
        self.cast.start(DEVICE, "https://dl.example/d/AB/Lupin.S01E02.mkv", "Lupin — S01E02", media, "Lupin.S01E02.mkv")
        return self.cast.session

    def watch_to_the_end(self, session):
        session_renderer = session["renderer"]
        session_renderer.answers = [("PLAYING", 2950.0, 3000.0), ("STOPPED", 0.0, 0.0)]
        self.cast.tick(session)
        self.cast.tick(session)

    def test_the_next_episode_starts_after_a_countdown(self):
        first = self.start_episode()
        self.watch_to_the_end(first)
        self.assertEqual(self.saved[-1]["position"], 3000, "the episode is saved as seen first")
        status = self.cast.status()
        self.assertEqual(status["next"], {"code": "S01E03", "in": 10, "finding": False})
        self.assertEqual(self.states[-1]["paused"], True, "paused meanwhile: the play key starts it now")
        self.now[0] += 4
        self.cast.tick(first)
        self.assertEqual((self.cast.status()["next"]["in"], self.resolved), (6, []), "nothing asked during the countdown")
        self.now[0] += 6
        self.cast.tick(first)
        self.assertEqual(self.resolved, [("tt2531336", 1, 3)])
        second = self.cast.session
        self.assertIsNot(second, first)
        self.assertTrue(first["ended"], "the first follow-up stops")
        action, url, title, mime = self.renderers[-1].calls[0]
        self.assertEqual((title, second["media"]["episode"], second["media"]["name"], second["source"]),
                         ("Lupin — S01E03", 3, "Lupin", "https://dl.example/d/CD/Lupin.S01E03.mkv"))
        self.assertTrue(url.startswith(f"http://192.168.1.171:8080/tv/stream/{second['token']}/Lupin.S01E03.mkv"))
        self.assertNotIn("next", self.cast.status())

    def test_the_last_episode_or_a_film_just_ends(self):
        self.series_over = True
        self.watch_to_the_end(self.start_episode())
        self.assertFalse(self.cast.status()["active"])
        self.watch_to_the_end(self.start_episode("movie"))
        self.assertFalse(self.cast.status()["active"])
        self.assertEqual(self.asked, [(1, 2)], "a film never asks for a next episode")

    def test_stopped_in_the_middle_does_not_chain(self):
        session = self.start_episode()
        session["renderer"].answers = [("PLAYING", 1200.0, 3000.0), ("STOPPED", 0.0, 0.0)]
        self.cast.tick(session)
        self.cast.tick(session)
        self.assertFalse(self.cast.status()["active"])
        self.assertEqual(self.asked, [])

    def test_stop_cancels_and_play_starts_it_now(self):
        first = self.start_episode()
        self.watch_to_the_end(first)
        self.cast.control("cancel_next")
        self.now[0] += 30
        self.cast.tick(first)
        self.assertEqual((self.resolved, self.cast.status()["active"]), ([], False))

        first = self.start_episode()
        self.watch_to_the_end(first)
        self.commands.append([{"action": "toggle_play"}])  # the Mac's play key, or the phone
        self.cast.tick(first)
        self.assertEqual(self.resolved, [("tt2531336", 1, 3)])
        self.assertEqual(self.cast.session["media"]["episode"], 3)

    def test_an_episode_not_found_says_so(self):
        self.link = None
        first = self.start_episode()
        self.watch_to_the_end(first)
        self.now[0] += tv.NEXT_DELAY
        self.cast.tick(first)
        status = self.cast.status()
        self.assertEqual((status["active"], status["error"]), (True, "S01E03 introuvable ou pas encore sorti."))
        self.assertTrue(first["ended"])

    def test_the_link_is_prepared_near_the_end(self):
        session = self.start_episode()
        session["renderer"].answers = [("PLAYING", 1200.0, 3000.0), ("PLAYING", 2600.0, 3000.0), ("PLAYING", 2700.0, 3000.0)]
        for _ in range(3):
            self.cast.tick(session)
        prefetches = [c for c in self.thread.call_args_list if c.kwargs.get("target") == self.cast._prefetch_next]
        self.assertEqual(len(prefetches), 1, "once, from 85 %")
        self.cast._prefetch_next(dict(session["media"]))
        self.assertEqual((self.prefetched[-1]["season"], self.prefetched[-1]["episode"]), (1, 3))


class RelayTests(unittest.TestCase):
    def test_range_and_type_reach_the_tv(self):
        opened = []

        class Upstream:
            status = 206
            headers = {"Content-Length": "4", "Content-Range": "bytes 0-3/100", "Accept-Ranges": "bytes", "Content-Type": "application/octet-stream"}
            def __init__(self): self.parts = [b"abcd", b""]
            def read(self, n): return self.parts.pop(0)
            def close(self): pass

        def opener(request, timeout=0):
            opened.append((request.full_url, request.get_header("Range"), request.get_method()))
            return Upstream()
        cast = tv.TvCast(lambda: 8080, print, print, list, renderer_for=FakeRenderer, opener=opener, ip_getter=lambda: "192.168.1.171")
        with patch.object(tv.threading, "Thread"):
            cast.start(DEVICE, "https://dl.example/d/AB/Film.mkv", "Film", {}, "Film.mkv")

        class Handler:
            headers = {"Range": "bytes=0-3"}
            def __init__(self): self.sent, self.body = [], b""
            def send_response(self, code): self.sent.append(("status", code))
            def send_header(self, k, v): self.sent.append((k, v))
            def end_headers(self): pass
            def send_error(self, code): self.sent.append(("error", code))
            @property
            def wfile(self): return self
            def write(self, data): self.body += data
        handler = Handler()
        cast.relay(handler)
        self.assertEqual(opened, [("https://dl.example/d/AB/Film.mkv", "bytes=0-3", "GET")])
        self.assertIn(("status", 206), handler.sent)
        self.assertIn(("Content-Type", "video/x-mkv"), handler.sent, "the TV gets the video type, not Debrid's")
        self.assertIn(("Content-Range", "bytes 0-3/100"), handler.sent)
        self.assertIn(("contentFeatures.dlna.org", tv.DLNA_FEATURES), handler.sent)
        self.assertEqual(handler.body, b"abcd")


if __name__ == "__main__":
    unittest.main()
