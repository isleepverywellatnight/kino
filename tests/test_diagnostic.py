"""Diagnostic: each part of KINO gets one plain line and, when needed, what to
do; collecting the facts changes nothing; the report to copy holds no key,
link, e-mail address or account name."""
import copy
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import api_service
import app
import config
import diagnostic
import kino_db
import network_state

NOW = 1_800_000_000
FACTS = {
    "now": NOW, "version": "1.1.73", "system": "macOS 26.1 · arm64", "online": True,
    "debrid": {"provider": "Real-Debrid", "has_token": True, "status": "connected", "premium": 200 * 86400},
    "trakt": {"connected": True, "expires_at": NOW + 86400},
    "drive": {"found": True, "sync_file": "ok", "modified": NOW - 2 * 3600},
    "player": {"ffmpeg": True, "native_available": True, "native_on": True},
    "notifications": {"available": True, "enabled": True, "status": "authorized"},
    "storage": {"folder": "~/Downloads", "exists": True, "writable": True, "free_bytes": 70 * 10**9, "missing_files": 0},
    "database": {"ok": True, "watchlist": 15, "history": 5},
    "errors": {"day": 0, "recent": []},
}


def facts(**changes):
    result = copy.deepcopy(FACTS)
    for path, value in changes.items():
        part, _, key = path.partition("__")
        if key:
            result[part][key] = value
        else:
            result[part] = value
    return result


def line(check, **changes):
    item = check(facts(**changes))
    return item["state"], item["summary"], item["advice"], item.get("section", "")


class CheckTests(unittest.TestCase):
    def test_all_well(self):
        items = diagnostic.checks(FACTS)
        self.assertEqual([item["id"] for item in items],
                         ["internet", "debrid", "trakt", "drive", "player", "notifications", "storage", "database", "errors"])
        self.assertEqual({item["state"] for item in items}, {"ok"})
        summaries = {item["id"]: item["summary"] for item in items}
        self.assertEqual(summaries["debrid"], "Connecté, abonnement encore 200 jours.")
        self.assertEqual(summaries["drive"], "Dernière synchronisation il y a 2 h.")
        self.assertEqual(summaries["storage"], "70 Go libres dans ~/Downloads.")
        self.assertEqual(summaries["database"], "Base saine : 15 dans Ma Liste, 5 dans l’historique.")

    def test_internet(self):
        self.assertEqual(line(diagnostic.internet_check, online=False)[:2], ("problem", "Pas de connexion à internet."))

    def test_debrid_account(self):
        check = diagnostic.debrid_check
        self.assertEqual(line(check, debrid__has_token=False)[0::3], ("problem", "settings-account"))
        self.assertEqual(line(check, debrid__status="invalid")[:2], ("problem", "Real-Debrid refuse la clé enregistrée."))
        self.assertEqual(line(check, debrid__status="unavailable")[:2], ("warning", "Real-Debrid ne répond pas pour le moment."))
        self.assertEqual(line(check, debrid__status="skipped")[:2], ("warning", "Compte non vérifié : pas de connexion à internet."))
        self.assertEqual(line(check, debrid__premium=0)[:2], ("problem", "Votre abonnement Real-Debrid est terminé."))
        self.assertEqual(line(check, debrid__premium=3 * 86400)[:2], ("warning", "Connecté, mais l’abonnement se termine dans 3 jours."))
        self.assertEqual(line(check, debrid__premium=None)[:2], ("ok", "Connecté."))

    def test_optional_services_are_off_not_problems(self):
        self.assertEqual(line(diagnostic.trakt_check, trakt__connected=False)[0::3], ("off", "settings-sync"))
        self.assertEqual(line(diagnostic.trakt_check, trakt__expires_at=NOW - 1)[:2], ("warning", "La connexion Trakt a expiré."))
        self.assertEqual(line(diagnostic.drive_check, drive={"found": False})[0], "off")
        self.assertEqual(line(diagnostic.drive_check, drive__sync_file="unreadable")[0], "problem")
        self.assertEqual(line(diagnostic.drive_check, drive__sync_file="missing")[0], "warning")
        self.assertEqual(line(diagnostic.notifications_check, notifications__available=False)[0], "off")
        self.assertEqual(line(diagnostic.notifications_check, notifications__enabled=False)[0::3], ("off", "settings-player"))

    def test_notifications_say_where_to_allow_them(self):
        state, summary, advice, _ = line(diagnostic.notifications_check, notifications__status="denied")
        self.assertEqual((state, summary), ("warning", "macOS bloque les notifications de KINO."))
        self.assertIn("Réglages Système → Notifications → KINO", advice)
        self.assertEqual(line(diagnostic.notifications_check, notifications__status="not_determined")[0], "warning")

    def test_player(self):
        self.assertEqual(line(diagnostic.player_check, player__ffmpeg=False)[0], "problem")
        self.assertEqual(line(diagnostic.player_check, player__native_on=False)[0::3], ("ok", "settings-player"))
        self.assertEqual(line(diagnostic.player_check, player__native_available=False)[0], "ok")

    def test_storage(self):
        check = diagnostic.storage_check
        self.assertEqual(line(check, storage__exists=False)[0::3], ("problem", "settings-storage"))
        self.assertEqual(line(check, storage__writable=False)[0], "problem")
        self.assertEqual(line(check, storage__free_bytes=3 * 10**9)[:2], ("problem", "Plus que 3,0 Go libres."))
        self.assertEqual(line(check, storage__free_bytes=20 * 10**9)[:2], ("warning", "20 Go libres."))
        self.assertEqual(line(check, storage__missing_files=2)[:2], ("warning", "70 Go libres. 2 fichiers hors ligne introuvables."))

    def test_database(self):
        self.assertEqual(line(diagnostic.database_check, database={"ok": False, "watchlist": 0, "history": 0})[0::3],
                         ("problem", "settings-updates"))


class ReportTests(unittest.TestCase):
    def test_the_report_reads_alone_and_hides_what_must_not_leave(self):
        items = diagnostic.checks(facts(debrid__premium=0, trakt__connected=False))
        items[0]["summary"] = f"KEY-0123456789 https://real-debrid.com/d/ABC me@example.com {Path.home()}/Movies"
        text = diagnostic.report(FACTS, items, secrets=["KEY-0123456789"])
        self.assertTrue(text.startswith("Diagnostic KINO · "))
        self.assertIn("KINO 1.1.73 · macOS 26.1 · arm64", text)
        self.assertIn("[PROBLÈME] Compte Real-Debrid : Votre abonnement Real-Debrid est terminé.\n    → Renouvelez-le", text)
        self.assertIn("[DÉSACTIVÉ] Trakt : Non connecté.\n[OK]", text, "advice only where something is to be done")
        for hidden in ("KEY-0123456789", "real-debrid.com/d", "me@example.com", str(Path.home())):
            self.assertNotIn(hidden, text)
        self.assertIn("[masqué] [lien masqué] [adresse masquée] ~/Movies", text)


class GatherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        db = patch.object(kino_db, "DB_PATH", self.root / "kino.db")
        db.start()
        self.addCleanup(db.stop)
        kino_db.init_db()
        kino_db.save_config("trakt_tokens", {"access_token": "TRAKT-SECRET-777", "refresh_token": "TRAKT-REFRESH-888",
                                             "created_at": time.time() - 100, "expires_in": 50})

    def digest(self):
        return hashlib.sha256((self.root / "kino.db").read_bytes()).hexdigest()

    def test_reading_changes_nothing_and_keeps_secrets_out(self):
        cfg = {"debrid_provider": "realdebrid", "rd_token": "RD-SECRET-123456", "provider_tokens": {"alldebrid": "AD-SECRET-999999"},
               "download_dir": str(self.root / "missing"), "native_player": True}
        user = {"username": "someone-private", "email": "me@example.com", "premium": 30 * 86400}
        before = self.digest()
        with patch.object(config, "load_config", return_value=cfg), \
                patch.object(network_state, "status", return_value={"online": True}) as status, \
                patch.object(api_service, "account_status", return_value=(user, "connected")) as account, \
                patch.object(config, "NOTIFIER", None), patch.object(config, "NATIVE_PLAYER", None), \
                patch.object(kino_db, "find_gdrive_sync_dir", return_value=None):
            result = diagnostic.run()
        self.assertEqual(self.digest(), before, "the database is only read")
        status.assert_called_once_with(refresh=True)
        account.assert_called_once_with("RD-SECRET-123456", "realdebrid")
        states = {item["id"]: (item["state"], item["summary"]) for item in result["items"]}
        self.assertEqual(states["debrid"], ("ok", "Connecté, abonnement encore 30 jours."))
        self.assertEqual(states["trakt"][0], "warning")
        self.assertEqual(states["storage"][0], "problem")
        self.assertEqual(states["notifications"][0], "off")
        sent = json.dumps(result, ensure_ascii=False)
        for secret in ("RD-SECRET-123456", "AD-SECRET-999999", "TRAKT-SECRET-777", "TRAKT-REFRESH-888", "someone-private", "me@example.com"):
            self.assertNotIn(secret, sent)

    def test_offline_the_account_is_not_asked(self):
        with patch.object(config, "load_config", return_value={"rd_token": "RD-SECRET-123456"}), \
                patch.object(network_state, "status", return_value={"online": False}), \
                patch.object(api_service, "account_status") as account, patch.object(config, "NOTIFIER", None), \
                patch.object(kino_db, "find_gdrive_sync_dir", return_value=None):
            result = diagnostic.run()
        account.assert_not_called()
        self.assertEqual([item["state"] for item in result["items"][:2]], ["problem", "warning"])

    def test_the_drive_file_is_read_not_written(self):
        folder = self.root / "Drive" / "KINO"
        folder.mkdir(parents=True)
        self.assertEqual(diagnostic._drive_facts(lambda: folder), {"found": True, "sync_file": "missing"})
        (folder / "kino_sync.json").write_text("{partial", encoding="utf-8")
        self.assertEqual(diagnostic._drive_facts(lambda: folder)["sync_file"], "unreadable")
        (folder / "kino_sync.json").write_text('{"watchlist": []}', encoding="utf-8")
        found = diagnostic._drive_facts(lambda: folder)
        self.assertEqual(found["sync_file"], "ok")
        self.assertEqual((folder / "kino_sync.json").read_text(encoding="utf-8"), '{"watchlist": []}')

    def test_missing_offline_files_are_counted(self):
        folder = self.root / "dl"
        folder.mkdir()
        items = [{"status": "completed", "available": True}, {"status": "completed", "available": False},
                 {"status": "downloading", "available": False}]
        found = diagnostic._storage_facts(folder, items)
        self.assertEqual((found["exists"], found["writable"], found["missing_files"]), (True, True, 1))
        self.assertGreater(found["free_bytes"], 0)


class RouteTests(unittest.TestCase):
    def test_the_interface_asks_and_gets_the_lines(self):
        request = object.__new__(app.RequestHandler)
        request.path = "/api/diagnostic"
        request.headers = {}
        request._request_allowed = Mock(return_value=True)
        request.send_json = Mock()
        with patch.object(diagnostic, "run", return_value={"items": [], "report": "x"}):
            request.do_GET()
        request.send_json.assert_called_once_with({"items": [], "report": "x"})


if __name__ == "__main__":
    unittest.main()
