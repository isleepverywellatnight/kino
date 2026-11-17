"""The local API answers KINO's own page, local players and the paired remote only."""

import types
import unittest

import app


def allowed(path="/api/auto-stream", *, client="127.0.0.1", port=8080, params=None, **headers):
    handler = types.SimpleNamespace(
        headers={"Host": f"127.0.0.1:{port}", **headers},
        client_address=(client, 50000),
        server=types.SimpleNamespace(server_port=port),
    )
    return app.RequestHandler._request_allowed(handler, path, params or {})


class RequestGuardTests(unittest.TestCase):
    def test_kino_page_and_local_players_are_served(self):
        self.assertTrue(allowed(**{"Sec-Fetch-Site": "same-origin"}))
        self.assertTrue(allowed(**{"Sec-Fetch-Site": "none"}))  # address typed or opened by the app
        self.assertTrue(allowed())  # mpv, VLC and FFmpeg send no Sec-Fetch header

    def test_requests_made_by_another_site_are_refused(self):
        # An <img src="http://127.0.0.1:8080/api/auto-stream?name=..."> carries no Origin.
        self.assertFalse(allowed(**{"Sec-Fetch-Site": "cross-site"}))
        self.assertFalse(allowed(**{"Sec-Fetch-Site": "same-site"}))
        self.assertFalse(allowed(Origin="https://evil.example"))
        self.assertFalse(allowed(Host="evil.example:8080"))  # DNS rebinding

    def test_paired_remote_needs_its_token_and_its_own_page(self):
        token = app.REMOTE_PAIRING_TOKEN
        lan = {"client": "192.168.1.20", "Host": "192.168.1.10:8080"}
        self.assertTrue(allowed("/api/remote/state", params={"token": token}, **lan,
                                **{"Sec-Fetch-Site": "same-origin"}))
        self.assertFalse(allowed("/api/remote/state", params={"token": "wrong"}, **lan))
        self.assertFalse(allowed("/api/config", params={"token": token}, **lan))
        self.assertFalse(allowed("/api/remote/control", params={"token": token}, **lan,
                                 **{"Sec-Fetch-Site": "cross-site"}))



class RemoteCommandQueueTests(unittest.TestCase):
    def test_taps_made_before_playback_are_not_replayed(self):
        import remote_controller
        remote_controller.pop_commands()
        remote_controller.push_command("pause")
        queued_at = remote_controller._pending_commands[-1]["timestamp"]
        self.assertEqual(remote_controller.pop_commands(now=queued_at + 60), [])
        remote_controller.push_command("seek_rel", 10)
        fresh = remote_controller.pop_commands(now=remote_controller._pending_commands[-1]["timestamp"] + 1)
        self.assertEqual([(cmd["action"], cmd["value"]) for cmd in fresh], [("seek_rel", 10)])


if __name__ == "__main__":
    unittest.main()
