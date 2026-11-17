"""Discord presence: « Regarde », the title in large, a clear
state, the progress bar while it plays, and an older Discord still served."""
import asyncio
import unittest

import discord_rpc as rpc

NOW = 10_000


class ActivityTests(unittest.TestCase):
    def test_a_film_playing(self):
        activity = rpc.build_activity("How to Make a Killing", "movie", current_time=600, duration=6000,
                                      poster_url="https://img/p.jpg", year="2026", imdb_id="tt4357198", now=NOW)
        self.assertEqual(activity, {
            "type": 3, "status_display_type": 2,
            "details": "How to Make a Killing", "state": "En lecture",
            "assets": {"large_image": "https://img/p.jpg", "large_text": "How to Make a Killing (2026)"},
            "timestamps": {"start": NOW - 600, "end": NOW - 600 + 6000},
        })
        self.assertNotIn("KINO", activity["state"], "the app's name is already in « Regarde KINO »")

    def test_a_paused_episode(self):
        activity = rpc.build_activity("Breaking Bad", "series", season=1, episode=2, current_time=60, duration=2900,
                                      is_paused=True, now=NOW)
        self.assertEqual(activity["details"], "Breaking Bad")
        self.assertEqual(activity["state"], "En pause · S01E02")
        self.assertNotIn("timestamps", activity, "no bar running while paused")
        self.assertNotIn("buttons", activity)

    def test_an_episode_title_when_one_is_given(self):
        activity = rpc.build_activity("Dexter", "series", season=2, episode=5, ep_title="Le titre", now=NOW)
        self.assertEqual(activity["state"], "En lecture · S02E05 · Le titre")
        self.assertEqual(activity["timestamps"], {"start": NOW})

    def test_no_button_so_the_poster_shows(self):
        # On the user's Discord, an activity with a button showed the app's icon instead of the poster.
        self.assertNotIn("buttons", rpc.build_activity("X", poster_url="https://img/p.jpg", imdb_id="tt1", now=NOW))

    def test_the_basic_activity_drops_the_newer_fields(self):
        activity = rpc.build_activity("X", imdb_id="tt1", now=NOW)
        self.assertEqual(set(rpc.basic_activity(activity)), {"details", "state", "timestamps"})


class FallbackTests(unittest.TestCase):
    def test_an_older_discord_gets_the_basic_activity(self):
        client = rpc.DiscordRPCClient()
        sent = []
        replies = [{"evt": "ERROR", "data": {"message": "child activity fails"}}, {"evt": None}]

        async def send(payload):
            sent.append(payload["args"]["activity"])
            return replies.pop(0)

        async def run():
            client._connected = True
            client._send_frame = send
            client._running = True
            client._queue = asyncio.Queue()
            await client._queue.put({"action": "update", "activity": rpc.build_activity("Dune", imdb_id="tt1160419", now=NOW)})
            await client._queue.put(None)
            await client._worker()

        asyncio.run(run())
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]["type"], 3)
        self.assertNotIn("type", sent[1])
        self.assertEqual(sent[1]["details"], "Dune")


if __name__ == "__main__":
    unittest.main()
