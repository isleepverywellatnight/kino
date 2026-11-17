"""The in-app player state reaches the phone remote and the update guard."""

import unittest

import remote_controller


class PlayerStateTests(unittest.TestCase):
    def tearDown(self):
        remote_controller.update_player_state({"playing": False, "paused": True, "current_time": 0, "duration": 0})

    def test_open_session_is_reported_as_playing_even_when_paused(self):
        remote_controller.update_player_state({
            "playing": True, "paused": True, "title": "Film", "sub_title": "S01E02", "media_type": "series",
            "current_time": 42.5, "duration": 3600, "volume": 0.4,
        })
        state = remote_controller.get_player_state()
        self.assertIs(state["playing"], True)
        self.assertEqual((state["current_time"], state["duration"], state["sub_title"]), (42.5, 3600.0, "S01E02"))

    def test_legacy_camel_case_and_invalid_values(self):
        remote_controller.update_player_state({"currentTime": 12, "duration": "abc", "volume": float("nan"),
                                               "updated_at": 0, "unknown": 1})
        state = remote_controller.get_player_state()
        self.assertEqual(state["current_time"], 12.0)
        self.assertNotEqual(state["updated_at"], 0)
        self.assertNotIn("unknown", state)
        remote_controller.update_player_state("not a dict")


    def test_phone_page_says_when_nothing_is_playing(self):
        page = remote_controller.generate_remote_html(8080)
        self.assertIn("!state.playing", page)
        self.assertIn("Lancez une lecture dans KINO", page)
        self.assertIn("<title>Télécommande KINO</title>", page)
        self.assertNotIn("Plein Écran", page)


if __name__ == "__main__":
    unittest.main()
