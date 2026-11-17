"""macOS « À l'écoute » and the playback keys: what is shown for a
player state, which command each key sends, and the glue with a stand-in
MediaPlayer framework. The real framework is tried in tools-free smoke runs."""
import unittest

import now_playing as np
import remote_controller


def state(**changes):
    base = {"playing": True, "paused": False, "title": "Breaking Bad", "sub_title": "S01E02", "media_type": "series",
            "current_time": 120.0, "duration": 2900.0, "poster": "https://images.example/poster.jpg", "updated_at": 1000.0}
    base.update(changes)
    return base


class FieldsTests(unittest.TestCase):
    def test_a_playing_episode(self):
        self.assertEqual(np.now_playing_fields(state(), 1001.0), {
            "title": "Breaking Bad", "artist": "S01E02", "duration": 2900.0, "elapsed": 120.0, "rate": 1.0,
            "poster": "https://images.example/poster.jpg"})

    def test_nothing_shows_without_a_fresh_player(self):
        self.assertIsNone(np.now_playing_fields(state(playing=False), 1001.0))
        self.assertIsNone(np.now_playing_fields(state(), 1000.0 + np.STALE_AFTER + 0.5), "a closed player stops reporting")
        self.assertIsNone(np.now_playing_fields(None, 0))

    def test_a_paused_film_without_poster(self):
        fields = np.now_playing_fields(state(paused=True, sub_title="", media_type="movie", poster="http://x/p.jpg",
                                             current_time=5000.0, title=""), 1001.0)
        self.assertEqual((fields["rate"], fields["artist"], fields["poster"], fields["elapsed"], fields["title"]),
                         (0.0, "KINO", "", 2900.0, "KINO"))

    def test_the_time_is_resent_only_when_it_jumps(self):
        shown = np.now_playing_fields(state(), 1001.0)
        later = np.now_playing_fields(state(current_time=125.0, updated_at=1005.0), 1006.0)
        self.assertFalse(np.needs_update(shown, later, 1006.0, 1001.0), "five seconds later, five seconds further")
        seek = np.now_playing_fields(state(current_time=900.0, updated_at=1005.0), 1006.0)
        self.assertTrue(np.needs_update(shown, seek, 1006.0, 1001.0))
        paused = np.now_playing_fields(state(paused=True, updated_at=1002.0), 1002.0)
        self.assertTrue(np.needs_update(shown, paused, 1002.0, 1001.0))
        self.assertTrue(np.needs_update(shown, None, 1010.0, 1001.0))
        self.assertFalse(np.needs_update(None, None, 1010.0, 1001.0))


class CommandTests(unittest.TestCase):
    def test_play_pause_is_explicit_from_the_state_before_the_press(self):
        self.assertEqual(np.command_for("toggle", state(paused=False), 1001.0), {"action": "pause", "value": None})
        self.assertEqual(np.command_for("toggle", state(paused=True), 1001.0), {"action": "play", "value": None})
        self.assertEqual(np.command_for("play", state(), 1001.0), {"action": "play", "value": None})

    def test_next_and_previous(self):
        self.assertEqual(np.command_for("next", state(), 1001.0), {"action": "next_ep", "value": None})
        self.assertEqual(np.command_for("previous", state(), 1001.0), {"action": "prev_ep", "value": None})
        film = state(media_type="movie", sub_title="")
        self.assertEqual(np.command_for("next", film, 1001.0), {"action": "seek_rel", "value": 10})
        self.assertEqual(np.command_for("previous", film, 1001.0), {"action": "seek_rel", "value": -10})

    def test_the_position_bar_of_control_centre(self):
        self.assertEqual(np.command_for("position", state(), 1001.0, 845.5), {"action": "seek", "value": 845.5})
        self.assertEqual(np.command_for("position", state(), 1001.0, -3), {"action": "seek", "value": 0.0})
        self.assertIsNone(np.command_for("position", state(), 1001.0, None))

    def test_no_command_without_playback(self):
        self.assertIsNone(np.command_for("toggle", state(playing=False), 1001.0))
        self.assertIsNone(np.command_for("toggle", state(), 2000.0))


class FakeCommand:
    def __init__(self):
        self.enabled, self.targets = False, []

    def setEnabled_(self, value):
        self.enabled = value

    def addTarget_action_(self, target, action):
        self.targets.append((target, action))


class FakeCenter:
    def __init__(self):
        self.info, self.states = "unset", []
        self.commands = {name: FakeCommand() for name in ("toggle", "play", "pause", "next", "previous", "position")}

    def setNowPlayingInfo_(self, info):
        self.info = info

    def setPlaybackState_(self, value):
        self.states.append(value)

    def togglePlayPauseCommand(self): return self.commands["toggle"]
    def playCommand(self): return self.commands["play"]
    def pauseCommand(self): return self.commands["pause"]
    def nextTrackCommand(self): return self.commands["next"]
    def previousTrackCommand(self): return self.commands["previous"]
    def changePlaybackPositionCommand(self): return self.commands["position"]


class GlueTests(unittest.TestCase):
    def setUp(self):
        self.center = FakeCenter()
        holder = type("Holder", (), {"sharedCommandCenter": staticmethod(lambda: self.center),
                                     "defaultCenter": staticmethod(lambda: self.center)})
        self.framework = {"MPRemoteCommandCenter": holder, "MPNowPlayingInfoCenter": holder,
                          "MPMediaItemPropertyTitle": "title", "MPMediaItemPropertyArtist": "artist",
                          "MPMediaItemPropertyPlaybackDuration": "playbackDuration",
                          "MPNowPlayingInfoPropertyElapsedPlaybackTime": "elapsed",
                          "MPNowPlayingInfoPropertyPlaybackRate": "rate", "MPMediaItemPropertyArtwork": "artwork"}
        self.now = [1001.0]
        self.state = state(poster="")
        self.pushed = []
        self.player = np.NowPlaying(self.framework, lambda: self.state, lambda a, v: self.pushed.append((a, v)),
                                    lambda fn: fn(), clock=lambda: self.now[0])

    def test_only_inside_the_bundle(self):
        self.assertIsNone(np.NowPlaying.create(dict, print, print, bundle_id="org.python.python"))

    def test_what_plays_reaches_control_centre_once(self):
        self.assertTrue(self.player.tick())
        self.assertEqual(self.center.info, {"title": "Breaking Bad", "artist": "S01E02", "elapsed": 120.0, "rate": 1.0, "playbackDuration": 2900.0})
        self.assertEqual(self.center.states, [np.PLAYING])
        self.now[0] = 1002.0
        self.state = state(poster="", current_time=121.0, updated_at=1002.0)
        self.assertFalse(self.player.tick(), "nothing new: the system counts the seconds")
        self.state = state(poster="", paused=True, current_time=121.0, updated_at=1002.0)
        self.assertTrue(self.player.tick())
        self.assertEqual((self.center.info["rate"], self.center.states[-1]), (0.0, np.PAUSED))
        self.now[0] = 1010.0
        self.assertTrue(self.player.tick(), "the player closed")
        self.assertEqual((self.center.info, self.center.states[-1]), (None, np.STOPPED))
        self.assertFalse(self.player.tick())

    def test_keys_go_to_the_remote_queue(self):
        self.assertEqual(self.player.handle("toggle"), np.HANDLED)
        self.assertEqual(self.player.handle("position", 300.0), np.HANDLED)
        self.assertEqual(self.pushed, [("pause", None), ("seek", 300.0)])
        self.now[0] = 2000.0
        self.assertEqual(self.player.handle("toggle"), np.NOTHING_PLAYING)
        self.assertEqual(len(self.pushed), 2)


class StateTests(unittest.TestCase):
    def test_the_player_state_keeps_an_https_poster_and_no_address(self):
        remote_controller.update_player_state({"poster": "https://images.example/p.jpg", "title": "Dune"})
        snapshot = remote_controller.player_state()
        self.assertEqual(snapshot["poster"], "https://images.example/p.jpg")
        self.assertNotIn("local_ip", snapshot)
        remote_controller.update_player_state({"poster": "file:///etc/passwd"})
        self.assertEqual(remote_controller.player_state()["poster"], "")
        remote_controller.update_player_state({"poster": "", "title": "Aucune lecture en cours"})


if __name__ == "__main__":
    unittest.main()
