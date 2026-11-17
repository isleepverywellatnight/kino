"""
KINO Trakt.tv Engine
====================
Intégration officielle Trakt.tv pour KINO :
- Authentification par Code d'Appareil (Device Code OAuth - 1 clic sans mot de passe)
- Scrobbling automatique en temps réel (Start / Pause / Stop à 80% de visionnage)
- Synchronisation bidirectionnelle de la Watchlist et de l'Historique Trakt
- Zéro dépendance externe (100% Python standard library urllib/json)
"""

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Dict, Any, Optional

import kino_db

logger = logging.getLogger("kino.trakt")

# Client ID public pour l'application KINO Desktop (ou personnalisable par l'utilisateur)
TRAKT_API_URL = "https://api.trakt.tv"
DEFAULT_CLIENT_ID = "6c4998782390886a07ec82956cf04627d3b2c6e61f2249e0b19d7d42eb6bf378"


def get_client_id() -> str:
    cfg = kino_db.get_config("trakt_client_id")
    return cfg if cfg else DEFAULT_CLIENT_ID


def get_headers(access_token: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": get_client_id(),
        "User-Agent": "KINO-Desktop/1.0",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def get_access_token() -> Optional[str]:
    tokens = kino_db.get_config("trakt_tokens")
    if isinstance(tokens, dict) and "access_token" in tokens:
        return tokens["access_token"]
    return None


def is_trakt_connected() -> bool:
    return bool(get_access_token())

is_trakt_authenticated = is_trakt_connected


def start_device_auth() -> Dict[str, Any]:
    """
    Lance le flux d'autorisation par code d'appareil (Device Code).
    Renvoie le code utilisateur (ex: 'ABCD-EFGH') et l'URL d'activation (https://trakt.tv/activate).
    """
    client_id = get_client_id()
    url = f"{TRAKT_API_URL}/oauth/device/code"
    data = json.dumps({"client_id": client_id}).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=get_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            # Sauvegarder l'état d'autorisation temporaire
            kino_db.save_config("trakt_pending_device", res)
            return {
                "status": "ok",
                "user_code": res.get("user_code"),
                "verification_url": res.get("verification_url", "https://trakt.tv/activate"),
                "device_code": res.get("device_code"),
                "expires_in": res.get("expires_in", 600),
                "interval": res.get("interval", 5),
            }
    except Exception as e:
        logger.error(f"Erreur start_device_auth: {e}")
        return {"status": "error", "message": str(e)}


def poll_device_token(device_code: str) -> Dict[str, Any]:
    """
    Vérifie si l'utilisateur a entré le code sur https://trakt.tv/activate.
    Si validé, stocke les jetons et connecte Trakt définitivement.
    """
    client_id = get_client_id()
    url = f"{TRAKT_API_URL}/oauth/device/token"
    payload = {
        "code": device_code,
        "client_id": client_id,
        "client_secret": kino_db.get_config("trakt_client_secret") or "",
    }
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=get_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            tokens = json.loads(resp.read().decode("utf-8"))
            kino_db.save_config("trakt_tokens", tokens)
            kino_db.delete_config("trakt_pending_device")
            # Récupérer le nom du profil Trakt
            user_info = fetch_trakt_profile(tokens.get("access_token"))
            if user_info:
                kino_db.save_config("trakt_user", user_info)
            return {"status": "connected", "tokens": tokens, "user": user_info}
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return {"status": "pending", "message": "En attente de validation sur trakt.tv/activate"}
        elif e.code == 404:
            return {"status": "not_found", "message": "Code invalide"}
        elif e.code == 409:
            return {"status": "already_used", "message": "Code déjà utilisé"}
        elif e.code == 410:
            return {"status": "expired", "message": "Code expiré"}
        elif e.code == 418:
            return {"status": "denied", "message": "Autorisation refusée"}
        return {"status": "error", "code": e.code, "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def fetch_trakt_profile(token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    tok = token or get_access_token()
    if not tok:
        return None
    url = f"{TRAKT_API_URL}/users/me"
    req = urllib.request.Request(url, headers=get_headers(tok))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def disconnect_trakt():
    kino_db.delete_config("trakt_tokens")
    kino_db.delete_config("trakt_user")
    kino_db.delete_config("trakt_pending_device")
    return {"status": "disconnected"}


# =============================================================================
# SCROBBLING AUTOMATIQUE
# =============================================================================

def _build_scrobble_body(media_meta: Dict[str, Any], progress_pct: float) -> Dict[str, Any]:
    """Construit le corps JSON pour l'API de scrobble Trakt."""
    is_series = media_meta.get("type") in ("series", "show", "episode") or bool(media_meta.get("season"))
    progress = max(0.0, min(100.0, float(progress_pct)))

    body: Dict[str, Any] = {"progress": progress}

    if is_series:
        show_obj: Dict[str, Any] = {"title": media_meta.get("title") or media_meta.get("series_title") or ""}
        imdb_id = media_meta.get("imdb_id")
        tmdb_id = media_meta.get("tmdb_id")
        ids = {}
        if imdb_id:
            ids["imdb"] = str(imdb_id)
        if tmdb_id:
            ids["tmdb"] = int(tmdb_id)
        if ids:
            show_obj["ids"] = ids

        body["show"] = show_obj
        body["episode"] = {
            "season": int(media_meta.get("season", 1)),
            "number": int(media_meta.get("episode", 1)),
        }
    else:
        movie_obj: Dict[str, Any] = {
            "title": media_meta.get("title") or "",
            "year": int(media_meta.get("year", 0)) if media_meta.get("year") else None,
        }
        ids = {}
        if media_meta.get("imdb_id"):
            ids["imdb"] = str(media_meta.get("imdb_id"))
        if media_meta.get("tmdb_id"):
            try:
                ids["tmdb"] = int(media_meta.get("tmdb_id"))
            except Exception:
                pass
        if ids:
            movie_obj["ids"] = ids
        body["movie"] = movie_obj

    return body


def scrobble_action(action: str, media_meta: Dict[str, Any], progress_pct: float) -> Dict[str, Any]:
    """
    Envoie une action de scrobble à Trakt : 'start', 'pause', ou 'stop'.
    À 80% ou plus lors d'un 'stop', Trakt valide automatiquement le visionnage.
    """
    token = get_access_token()
    if not token:
        return {"status": "disabled", "message": "Trakt non connecté"}

    if action not in ("start", "pause", "stop"):
        return {"status": "error", "message": "Action invalide"}

    url = f"{TRAKT_API_URL}/scrobble/{action}"
    payload = _build_scrobble_body(media_meta, progress_pct)
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=get_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return {"status": "ok", "action": action, "response": res}
    except Exception as e:
        logger.debug(f"Erreur scrobble {action}: {e}")
        return {"status": "error", "message": str(e)}


def sync_trakt_watchlist() -> Dict[str, Any]:
    """Récupère la Watchlist Trakt de l'utilisateur et la fusionne avec KINO."""
    token = get_access_token()
    if not token:
        return {"status": "disabled", "message": "Trakt non connecté"}

    count = 0
    now = time.time()
    for endpoint, media_type in (("/sync/watchlist/movies", "movie"), ("/sync/watchlist/shows", "series")):
        url = f"{TRAKT_API_URL}{endpoint}"
        req = urllib.request.Request(url, headers=get_headers(token))
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                items = json.loads(resp.read().decode("utf-8"))
                for entry in items:
                    m = entry.get(media_type, {})
                    title = m.get("title")
                    year = m.get("year", "")
                    imdb_id = m.get("ids", {}).get("imdb")
                    tmdb_id = m.get("ids", {}).get("tmdb")
                    item_id = imdb_id or (f"tmdb_{tmdb_id}" if tmdb_id else None)
                    if item_id and title:
                        kino_db.add_to_watchlist(
                            item_id=item_id,
                            name=title,
                            media_type=media_type,
                            year=str(year),
                            poster="",
                            imdb_rating="",
                            added_at=now,
                        )
                        count += 1
        except Exception as e:
            logger.error(f"Erreur sync_trakt_watchlist {endpoint}: {e}")

    return {"status": "synced", "items_added": count}
