"""Diagnostic: what works in KINO, and what to do about the rest, in plain words.

gather() only reads: it never syncs, saves, cleans or asks macOS for
anything. Its one network use is the connection check and the Debrid account
check that Paramètres already makes. checks() turns those facts into lines, and
report() into a text to copy; neither holds a key, a link, an e-mail address or
a user name."""
import json
import os
import platform
import re
import shutil
import time
from pathlib import Path

OK, WARNING, PROBLEM, OFF = "ok", "warning", "problem", "off"
STATE_LABELS = {OK: "OK", WARNING: "À vérifier", PROBLEM: "Problème", OFF: "Désactivé"}
LOW_SPACE = 5 * 10**9      # under this, a download will not fit
SHORT_SPACE = 25 * 10**9   # a 4K film often weighs 20 Go or more
SHORT_PREMIUM = 7 * 86400


def _item(key, label, state, summary, advice="", section=""):
    item = {"id": key, "label": label, "state": state, "summary": summary, "advice": advice}
    if section:
        item["section"] = section
    return item


def _days(seconds):
    days = max(1, round(seconds / 86400))
    return f"{days} jour{'s' if days > 1 else ''}"


def _gigabytes(size):
    value = size / 10**9
    return f"{value:.0f} Go" if value >= 10 else f"{value:.1f} Go".replace(".", ",")


def _ago(seconds):
    minutes = int(seconds // 60)
    if minutes < 1:
        return "à l’instant"
    if minutes < 60:
        return f"il y a {minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"il y a {hours} h"
    return f"il y a {hours // 24} jours"


def internet_check(facts):
    if facts["online"]:
        return _item("internet", "Internet", OK, "Internet répond.")
    return _item("internet", "Internet", PROBLEM, "Pas de connexion à internet.",
                 "Vérifiez le Wi-Fi. Vos fichiers hors ligne restent disponibles.")


def debrid_check(facts):
    debrid = facts["debrid"]
    name = debrid["provider"]
    label = f"Compte {name}"
    if not debrid["has_token"]:
        return _item("debrid", label, PROBLEM, f"Aucune clé {name} enregistrée.",
                     "Collez votre clé dans Compte Debrid : sans elle, KINO ne peut pas lire les films et les séries.",
                     "settings-account")
    status = debrid["status"]
    if status == "skipped":
        return _item("debrid", label, WARNING, "Compte non vérifié : pas de connexion à internet.",
                     "Relancez le diagnostic quand internet répond.")
    if status == "invalid":
        return _item("debrid", label, PROBLEM, f"{name} refuse la clé enregistrée.",
                     f"Elle a peut-être changé : copiez-la de nouveau depuis votre compte {name} et collez-la dans Compte Debrid.",
                     "settings-account")
    if status != "connected":
        return _item("debrid", label, WARNING, f"{name} ne répond pas pour le moment.",
                     "Réessayez dans quelques minutes ; si cela dure, le service a peut-être une panne.")
    premium = debrid.get("premium")
    if premium is not None and premium <= 0:
        return _item("debrid", label, PROBLEM, f"Votre abonnement {name} est terminé.",
                     f"Renouvelez-le sur le site de {name} : les sources ne se lancent plus sans lui.")
    if premium is not None and premium < SHORT_PREMIUM:
        return _item("debrid", label, WARNING, f"Connecté, mais l’abonnement se termine dans {_days(premium)}.",
                     f"Pensez à le renouveler sur le site de {name}.")
    left = f", abonnement encore {_days(premium)}" if premium else ""
    return _item("debrid", label, OK, f"Connecté{left}.")


def trakt_check(facts):
    trakt = facts["trakt"]
    if not trakt["connected"]:
        return _item("trakt", "Trakt", OFF, "Non connecté.", "Facultatif : reliez Trakt dans Synchronisation pour suivre vos visionnages.",
                     "settings-sync")
    expires = trakt.get("expires_at")
    if expires and expires <= facts["now"]:
        return _item("trakt", "Trakt", WARNING, "La connexion Trakt a expiré.",
                     "Déconnectez puis reconnectez Trakt dans Synchronisation.", "settings-sync")
    return _item("trakt", "Trakt", OK, "Connecté.")


def drive_check(facts):
    drive = facts["drive"]
    if not drive["found"]:
        return _item("drive", "Google Drive", OFF, "Aucun dossier KINO dans Google Drive.",
                     "Facultatif : avec Google Drive pour ordinateur et un dossier « KINO » dans Mon Drive, "
                     "vos listes et votre historique suivent sur vos autres appareils.")
    if drive["sync_file"] == "unreadable":
        return _item("drive", "Google Drive", PROBLEM, "Le fichier de synchronisation est illisible.",
                     "Google Drive est peut-être en train de le copier : attendez la fin, puis relancez KINO.", "settings-sync")
    if drive["sync_file"] == "missing":
        return _item("drive", "Google Drive", WARNING, "Dossier trouvé, pas encore synchronisé.",
                     "Lancez une synchronisation dans Synchronisation.", "settings-sync")
    return _item("drive", "Google Drive", OK, f"Dernière synchronisation {_ago(facts['now'] - drive['modified'])}.")


def player_check(facts):
    player = facts["player"]
    if not player["ffmpeg"]:
        return _item("player", "Lecteur", PROBLEM, "Un composant de lecture manque (FFmpeg).",
                     "Réinstallez KINO depuis Mises à jour, ou revenez à la version précédente.", "settings-updates")
    if player["native_available"] and player["native_on"]:
        return _item("player", "Lecteur", OK, "Lecteur natif prêt.")
    if player["native_available"]:
        return _item("player", "Lecteur", OK, "Lecteur intégré de KINO.",
                     "Le lecteur natif lit plus de formats : activez « Lecteur natif » dans Lecture.", "settings-player")
    return _item("player", "Lecteur", OK, "Lecteur intégré de KINO (le lecteur natif n’existe que dans l’app pour Mac).")


def notifications_check(facts):
    notes = facts["notifications"]
    label = "Notifications"
    if not notes["available"]:
        return _item("notifications", label, OFF, "Disponibles seulement dans l’app KINO pour Mac.")
    if not notes["enabled"]:
        return _item("notifications", label, OFF, "Désactivées dans Paramètres.",
                     "Activez « Notifications des nouveaux épisodes » dans Lecture pour être prévenu.", "settings-player")
    status = notes["status"]
    if status in ("authorized", "provisional", "ephemeral"):
        return _item("notifications", label, OK, "Autorisées pour les nouveaux épisodes.")
    if status == "denied":
        return _item("notifications", label, WARNING, "macOS bloque les notifications de KINO.",
                     "Autorisez-les dans Réglages Système → Notifications → KINO.")
    if status == "not_determined":
        return _item("notifications", label, WARNING, "macOS n’a pas encore reçu de réponse.",
                     "Acceptez la demande d’autorisation de KINO quand elle apparaît.")
    return _item("notifications", label, WARNING, "État des notifications inconnu.")


def storage_check(facts):
    storage = facts["storage"]
    label = "Espace disque"
    if not storage["exists"]:
        return _item("storage", label, PROBLEM, f"Le dossier de téléchargement {storage['folder']} n’existe pas.",
                     "Choisissez un autre dossier dans Stockage.", "settings-storage")
    if not storage["writable"]:
        return _item("storage", label, PROBLEM, f"KINO ne peut pas écrire dans {storage['folder']}.",
                     "Choisissez un autre dossier dans Stockage.", "settings-storage")
    free = storage["free_bytes"]
    missing = storage["missing_files"]
    lost = f" {missing} fichier{'s' if missing > 1 else ''} hors ligne introuvable{'s' if missing > 1 else ''}." if missing else ""
    if free is not None and free < LOW_SPACE:
        return _item("storage", label, PROBLEM, f"Plus que {_gigabytes(free)} libres.{lost}",
                     "Libérez de la place ou retirez des fichiers dans Bibliothèque → Hors ligne.")
    if free is not None and free < SHORT_SPACE:
        return _item("storage", label, WARNING, f"{_gigabytes(free)} libres.{lost}",
                     "Un film 4K pèse souvent 20 Go ou plus : libérez de la place avant d’en garder un hors ligne.")
    if missing:
        return _item("storage", label, WARNING, f"{_gigabytes(free or 0)} libres.{lost}",
                     "Le fichier a été déplacé ou supprimé : retirez-le de Bibliothèque → Hors ligne, puis gardez-le de nouveau.")
    return _item("storage", label, OK, f"{_gigabytes(free or 0)} libres dans {storage['folder']}.")


def database_check(facts):
    base = facts["database"]
    if not base["ok"]:
        return _item("database", "Données", PROBLEM, "La base de données de KINO a un problème.",
                     "Fermez et rouvrez KINO. Si cela continue, revenez à la version précédente dans Mises à jour : "
                     "une copie de vos données est faite avant chaque installation.", "settings-updates")
    return _item("database", "Données", OK,
                 f"Base saine : {base['watchlist']} dans Ma Liste, {base['history']} dans l’historique.")


def errors_check(facts):
    """The error log (error_log.py): how many failures in the last 24 hours."""
    errors = facts.get("errors") or {"day": 0}
    day = int(errors.get("day") or 0)
    if not day:
        return _item("errors", "Erreurs", OK, "Aucune erreur ces dernières 24 heures.")
    return _item("errors", "Erreurs", WARNING, f"{day} erreur{'s' if day > 1 else ''} ces dernières 24 heures.",
                 "Copiez le rapport : il liste les dernières, sans clé ni lien.")


CHECKS = (internet_check, debrid_check, trakt_check, drive_check, player_check, notifications_check, storage_check,
          database_check, errors_check)


def checks(facts):
    return [check(facts) for check in CHECKS]


def _scrub(text, secrets=()):
    """Last guard for the copied text: no key, link, e-mail or home folder."""
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "[masqué]")
    text = re.sub(r"\b[a-z][a-z0-9+.-]*://\S+", "[lien masqué]", text, flags=re.IGNORECASE)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[adresse masquée]", text)
    home = str(Path.home())
    if len(home) > 1:
        text = text.replace(home, "~")
    return text


def report(facts, items, secrets=()):
    marks = {OK: "OK", WARNING: "À VÉRIFIER", PROBLEM: "PROBLÈME", OFF: "DÉSACTIVÉ"}
    lines = [f"Diagnostic KINO · {time.strftime('%d/%m/%Y à %H:%M', time.localtime(facts['now']))}",
             f"KINO {facts['version']} · {facts['system']}", ""]
    for item in items:
        lines.append(f"[{marks[item['state']]}] {item['label']} : {item['summary']}")
        if item["advice"] and item["state"] in (WARNING, PROBLEM):
            lines.append(f"    → {item['advice']}")
    recent = (facts.get("errors") or {}).get("recent") or []
    if recent:
        import error_log
        lines += ["", "Dernières erreurs :"] + [f"  {error_log.line_for(entry)}" for entry in recent]
    lines += ["", "Ce rapport ne contient ni clé, ni lien, ni adresse e-mail, ni nom de compte."]
    return _scrub("\n".join(lines), secrets)


def short_path(path):
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if len(home) > 1 and text.startswith(home) else text


# --- What KINO knows right now (read only) ---------------------------------

def _drive_facts(find_dir):
    folder = find_dir()
    if not folder:
        return {"found": False}
    sync_file = Path(folder) / "kino_sync.json"
    if not sync_file.exists():
        return {"found": True, "sync_file": "missing"}
    try:
        data = json.loads(sync_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        return {"found": True, "sync_file": "unreadable"}
    return {"found": True, "sync_file": "ok", "modified": sync_file.stat().st_mtime}


def _storage_facts(folder, offline_items):
    folder = Path(folder).expanduser()
    exists = folder.is_dir()
    free = None
    probe = folder if exists else next((p for p in folder.parents if p.is_dir()), None)
    if probe is not None:
        try:
            free = shutil.disk_usage(probe).free
        except OSError:
            free = None
    missing = sum(1 for item in offline_items if item.get("status") == "completed" and not item.get("available"))
    return {"folder": short_path(folder), "exists": exists, "writable": exists and os.access(folder, os.W_OK),
            "free_bytes": free, "missing_files": missing}


def _database_facts(kino_db):
    try:
        conn = kino_db.get_connection()
        try:
            ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            watchlist = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
            history = conn.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
        finally:
            conn.close()
        return {"ok": ok, "watchlist": watchlist, "history": history}
    except Exception:
        return {"ok": False, "watchlist": 0, "history": 0}


def _errors_facts():
    try:
        import error_log
        day_ago = time.time() - 86400
        return {"day": error_log.log.count_since(day_ago), "recent": error_log.log.recent(limit=10)}
    except Exception:
        return {"day": 0, "recent": []}


def _system():
    mac = platform.mac_ver()[0]
    name = f"macOS {mac}" if mac else platform.system()
    return f"{name} · {platform.machine()}"


def gather():
    """The facts, read now. Returns (facts, secrets): secrets only feed _scrub."""
    import api_service
    import config
    import kino_db
    import network_state
    import offline_library
    from kino_version import APP_VERSION

    cfg = config.load_config()
    provider = cfg.get("debrid_provider", "realdebrid")
    meta = config.DEBRID_PROVIDERS.get(provider) or config.DEBRID_PROVIDERS["realdebrid"]
    token = cfg.get("rd_token", "") or ""
    online = network_state.status(refresh=True)["online"]

    debrid = {"provider": meta["name"], "has_token": bool(token), "status": "skipped", "premium": None}
    if token and online:
        user, status = api_service.account_status(token, provider)
        debrid["status"] = status
        if isinstance(user, dict) and user.get("premium") is not None:
            try:
                debrid["premium"] = int(user["premium"])
            except (TypeError, ValueError):
                pass

    tokens = kino_db.get_config("trakt_tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    expires = None
    if tokens.get("created_at") and tokens.get("expires_in"):
        try:
            expires = float(tokens["created_at"]) + float(tokens["expires_in"])
        except (TypeError, ValueError):
            expires = None

    try:
        import imageio_ffmpeg
        ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file()
    except Exception:
        ffmpeg = False

    notifier = config.NOTIFIER
    try:
        note_status = notifier.status(timeout=2.0) if notifier is not None else "unavailable"
    except Exception:
        note_status = "unknown"

    try:
        offline_items = list(offline_library.library.list().values())
    except Exception:
        offline_items = []

    facts = {
        "now": time.time(),
        "version": APP_VERSION,
        "system": _system(),
        "online": bool(online),
        "debrid": debrid,
        "trakt": {"connected": bool(tokens.get("access_token")), "expires_at": expires},
        "drive": _drive_facts(kino_db.find_gdrive_sync_dir),
        "player": {"ffmpeg": ffmpeg, "native_available": config.NATIVE_PLAYER is not None,
                   "native_on": bool(cfg.get("native_player", config.NATIVE_PLAYER is not None))},
        "notifications": {"available": notifier is not None, "enabled": cfg.get("episode_notifications", True) is not False,
                          "status": note_status},
        "storage": _storage_facts(cfg.get("download_dir") or config.DEFAULT_DOWNLOAD_DIR, offline_items),
        "database": _database_facts(kino_db),
        "errors": _errors_facts(),
    }
    secrets = [token, cfg.get("tmdb_key", ""), *(v for v in (cfg.get("provider_tokens") or {}).values() if isinstance(v, str)),
               *(str(v) for k, v in tokens.items() if "token" in k)]
    return facts, secrets


def run():
    facts, secrets = gather()
    items = checks(facts)
    return {"version": facts["version"], "system": facts["system"], "items": items,
            "report": report(facts, items, secrets)}
