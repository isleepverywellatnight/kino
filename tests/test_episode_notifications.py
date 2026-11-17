"""Notifications of new episodes: each release announced once, without its
title; nothing on the first check; a click opens the series."""
import threading
import unittest
from unittest.mock import Mock, patch

import app
import config
import desktop
import episode_alerts
import notifier

FOLLOWED = {"id": "tt1", "name": "The Bear", "kind": "followed", "season": 3, "episode": 5, "episode_name": "Secret",
            "released": "2026-11-09", "newest": "2026-11-09", "new_count": 1}
TWO = dict(FOLLOWED, id="tt2", name="Severance", season=2, episode=1, newest="2026-11-10", new_count=2)
PREMIERE = {"id": "tt3", "name": "Nocturne", "kind": "premiere", "season": 1, "episode": 1, "released": "2026-10-30"}
SEASON = {"id": "tt4", "name": "Black Clover", "kind": "season", "season": 5, "episode": 1, "released": "2026-10-03"}


class AlertTests(unittest.TestCase):
    def test_messages_never_name_the_episode(self):
        self.assertEqual(episode_alerts.message(FOLLOWED), ("Nouvel épisode · The Bear", "S03E05 est disponible."))
        self.assertEqual(episode_alerts.message(TWO), ("2 nouveaux épisodes · Severance", "À partir de S02E01."))
        self.assertEqual(episode_alerts.message(PREMIERE), ("Nouvelle série · Nocturne", "La saison 1 est disponible."))
        self.assertEqual(episode_alerts.message(SEASON), ("Nouvelle saison · Black Clover", "La saison 5 est disponible."))
        for item in (FOLLOWED, TWO):
            self.assertNotIn("Secret", "".join(episode_alerts.message(item)))

    def test_one_announcement_per_release(self):
        self.assertEqual(episode_alerts.alert_key(FOLLOWED), "tt1:followed:2026-11-09")
        self.assertEqual(episode_alerts.alert_key(SEASON), "tt4:season:s5")
        alerts, seen = episode_alerts.pending_alerts([FOLLOWED, PREMIERE], None)
        self.assertEqual(alerts, [], "the first check records, announces nothing")
        alerts, seen = episode_alerts.pending_alerts([FOLLOWED, PREMIERE, TWO], seen)
        self.assertEqual([item["id"] for item in alerts], ["tt2"])
        later = dict(FOLLOWED, newest="2026-11-16", episode=6)
        alerts, seen = episode_alerts.pending_alerts([later, TWO], seen)
        self.assertEqual([item["id"] for item in alerts], ["tt1"], "a later episode is a new release")
        self.assertEqual(set(seen), {"tt1:followed:2026-11-16", "tt2:followed:2026-11-10"}, "releases no longer listed are forgotten")

    def test_a_summary_past_three(self):
        many = [dict(FOLLOWED, id=f"tt{i}", name=f"Série {i}") for i in range(5)]
        self.assertEqual(episode_alerts.notifications(many),
                         [{"title": "5 séries ont du nouveau", "body": "Série 0, Série 1, Série 2…", "item": None}])
        self.assertEqual(len(episode_alerts.notifications(many[:3])), 3)


class CheckTests(unittest.TestCase):
    def run_check(self, cfg, items):
        sent, saved = [], []
        with patch.object(app, "load_config", return_value=cfg), patch.object(app, "save_config", side_effect=saved.append), \
                patch.object(app.new_episodes, "find_new_episodes", return_value=items):
            alerts = app.check_new_episodes(sent.append, today="2026-11-11")
        return alerts, sent, saved

    def test_off_first_check_then_new_releases(self):
        self.assertEqual(self.run_check({"episode_notifications": False}, [FOLLOWED]), ([], [], []))
        alerts, sent, saved = self.run_check({}, [FOLLOWED])
        self.assertEqual((alerts, sent), ([], []))
        self.assertEqual(saved, [{"notified_episodes": ["tt1:followed:2026-11-09"]}])
        alerts, sent, saved = self.run_check({"notified_episodes": ["tt1:followed:2026-11-09"]}, [FOLLOWED, PREMIERE])
        self.assertEqual([n["title"] for n in sent], ["Nouvelle série · Nocturne"])
        alerts, sent, saved = self.run_check({"notified_episodes": ["tt1:followed:2026-11-09"]}, [FOLLOWED])
        self.assertEqual((sent, saved), ([], []), "nothing new, nothing written")

    def test_a_notification_carries_the_series_to_open(self):
        fake = Mock()
        with patch.object(config, "NOTIFIER", fake):
            self.assertTrue(app.send_notification({"title": "T", "body": "B", "item": FOLLOWED}))
        fake.send.assert_called_once_with("tt1:followed:2026-11-09", "T", "B",
                                          {"id": "tt1", "name": "The Bear", "kind": "followed", "season": 3, "episode": 5})
        with patch.object(config, "NOTIFIER", None):
            self.assertFalse(app.send_notification({"title": "T", "body": "B", "item": FOLLOWED}))


class TestRouteTests(unittest.TestCase):
    def post(self, notifier_object):
        handler = object.__new__(app.RequestHandler)
        handler.path = "/api/notifications/test"
        handler.headers = {"Content-Length": "2"}
        handler._request_allowed = Mock(return_value=True)
        handler.read_json_body = Mock(return_value={})
        handler.send_json = Mock()
        with patch.object(config, "NOTIFIER", notifier_object):
            handler.do_POST()
        return handler.send_json.call_args.args[0]

    def test_statuses(self):
        self.assertEqual(self.post(None), {"ok": False, "status": "unavailable"})
        denied = Mock(); denied.status.return_value = "denied"
        self.assertEqual(self.post(denied), {"ok": False, "status": "denied"})
        denied.send.assert_not_called()
        asked = Mock()
        asked.status.side_effect = ["not_determined", "authorized"]
        asked.request_permission.side_effect = lambda done: done(True)
        self.assertEqual(self.post(asked), {"ok": True, "status": "authorized"})
        self.assertEqual(asked.send.call_args.args[1:], ("KINO", "Les notifications des nouveaux épisodes fonctionnent."))


class SettingTests(unittest.TestCase):
    def test_saved_values(self):
        self.assertEqual(config.sanitize_config_payload({"episode_notifications": False}), {"episode_notifications": False})
        self.assertNotIn("episode_notifications", config.sanitize_config_payload({"episode_notifications": "oui"}))
        cleaned = config.sanitize_config_payload({"notified_episodes": ["tt1:followed:2026-11-09", 3, "", "x" * 500] + ["k"] * 400})
        self.assertEqual(cleaned["notified_episodes"][0], "tt1:followed:2026-11-09")
        self.assertEqual(len(cleaned["notified_episodes"][1]), 120)
        self.assertEqual(len(cleaned["notified_episodes"]), 300)

    def test_notifications_only_inside_the_kino_app(self):
        self.assertIsNone(notifier.Notifier.create(bundle_id="org.python.python"))
        self.assertIsNone(notifier.Notifier.create(bundle_id=""))


class DesktopTests(unittest.TestCase):
    def test_the_watch_asks_once_then_checks_every_half_hour(self):
        stop = threading.Event()
        waits, checks = [], []

        def wait(seconds):
            waits.append(seconds)
            if len(waits) == 3:
                stop.set()

        fake = Mock()
        with patch.object(desktop.app, "NOTIFIER", fake), patch.object(desktop.app, "load_config", return_value={}), \
                patch.object(desktop.app, "check_new_episodes", side_effect=lambda send: checks.append(send)):
            desktop.watch_new_episodes(stop=stop, wait=wait)
        fake.request_permission.assert_called_once_with()
        self.assertEqual(waits, [90, 1800, 1800])
        self.assertEqual(len(checks), 3)
        with patch.object(desktop.app, "NOTIFIER", None), patch.object(desktop.app, "check_new_episodes") as check:
            desktop.watch_new_episodes(stop=stop, wait=wait)
        check.assert_not_called()

    def test_a_click_brings_kino_forward_and_opens_the_series(self):
        window = Mock()
        with patch.object(desktop, "desktop_window", window), patch.object(desktop, "handle_backend_window_action") as action:
            desktop.open_from_notification({"id": "tt1", "name": "The Bear"})
        action.assert_called_once_with("show")
        window.evaluate_js.assert_called_once_with('openFromNotification({"id": "tt1", "name": "The Bear"})')


if __name__ == "__main__":
    unittest.main()
