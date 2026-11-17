"""
KINO Remote Controller Engine
==============================
Serveur et gestionnaire de télécommande tactile mobile pour smartphone :
- Détection automatique de l'IP locale (Wi-Fi)
- Synchronisation d'état en temps réel (Play/Pause, Temps, Durée, Volume, Titre)
- File d'attente de commandes bidirectionnelle
- Interface Web Mobile ultra-réactive optimisée pour iOS Safari et Android
- Génération de QR Code SVG autonome (zéro dépendance externe)
"""

import json
import socket
import threading
import time
from typing import Dict, Any, List, Optional

import paths

_STATE_LOCK = threading.RLock()
_player_state: Dict[str, Any] = {
    "playing": False,
    "paused": True,
    "current_time": 0.0,
    "duration": 0.0,
    "volume": 1.0,
    "title": "Aucune lecture en cours",
    "sub_title": "",
    "media_type": "movie",
    # The title's poster, for « À l'écoute » on macOS (now_playing.py).
    "poster": "",
    "updated_at": time.time(),
}

_pending_commands: List[Dict[str, Any]] = []


def get_local_ip() -> str:
    """Détecte l'adresse IP locale de la machine sur le réseau local (Wi-Fi/Ethernet)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    finally:
        s.close()

    try:
        hostname = socket.gethostname()
        candidates = socket.gethostbyname_ex(hostname)[2]
        for ip in candidates:
            if ip.startswith(("192.168.", "10.")):
                return ip
        for ip in candidates:
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


SERVER_PORT = 8080


def get_player_state() -> Dict[str, Any]:
    with _STATE_LOCK:
        st = dict(_player_state)
        ip = get_local_ip()
        st["local_ip"] = ip
        st["url"] = f"http://{ip}:{SERVER_PORT}/remote"
        return st


def player_state() -> Dict[str, Any]:
    """The player state alone (no network address): read each second by now_playing."""
    with _STATE_LOCK:
        return dict(_player_state)


_STATE_ALIASES = {"currentTime": "current_time", "subTitle": "sub_title", "mediaType": "media_type"}


def _clean_state_value(key: str, value: Any):
    if key in ("playing", "paused"):
        return bool(value)
    if key in ("current_time", "duration", "volume"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number and number not in (float("inf"), float("-inf")) and number >= 0 else None
    if key == "poster":
        text = str(value or "")
        return text if text.startswith("https://") and len(text) <= 1000 else ""
    return str(value or "")[:200]


def update_player_state(new_state: Dict[str, Any]):
    """Record the in-app player state; `playing` means a Playback Session is open."""
    if not isinstance(new_state, dict):
        return
    with _STATE_LOCK:
        for raw_key, raw_value in new_state.items():
            key = _STATE_ALIASES.get(raw_key, raw_key)
            if key == "updated_at" or key not in _player_state:
                continue
            value = _clean_state_value(key, raw_value)
            if value is not None:
                _player_state[key] = value
        _player_state["updated_at"] = time.time()


def push_command(action: str, value: Any = None):
    with _STATE_LOCK:
        _pending_commands.append({"action": action, "value": value, "timestamp": time.time()})
        # Garder seulement les 20 dernières commandes
        if len(_pending_commands) > 20:
            _pending_commands.pop(0)


COMMAND_MAX_AGE_SEC = 5.0


def pop_commands(now: float = None) -> List[Dict[str, Any]]:
    """Return fresh commands only: a tap made before playback must not fire later."""
    global _pending_commands
    now = time.time() if now is None else now
    with _STATE_LOCK:
        cmds = [cmd for cmd in _pending_commands if now - cmd.get("timestamp", 0) <= COMMAND_MAX_AGE_SEC]
        _pending_commands = []
        return cmds


def generate_remote_html(port: int = 8080) -> str:
    """The phone remote's page (web/remote.html, served by both servers)."""
    return paths.resource("web", "remote.html").read_text(encoding="utf-8")


def generate_qr_code_svg(url: str) -> str:
    """Génère la structure SVG pour la télécommande KINO Remote."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="196" height="196">
  <rect width="200" height="200" fill="#ffffff" rx="10"/>
  <text x="100" y="90" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" font-size="14" font-weight="bold" fill="#18181b" text-anchor="middle">KINO Remote</text>
  <text x="100" y="115" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" font-size="11" fill="#71717a" text-anchor="middle">QR Code Local</text>
</svg>"""
