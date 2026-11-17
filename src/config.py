#!/usr/bin/env python3
"""
KINO Configuration & Cache Engine
Gestion de la configuration utilisateur, cache persistant L1/L2,
helpers HTTP et utilitaires de formatage.
"""

import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import discord_rpc
import kino_db
import network_state
import ssl


VALID_PLAYER_MODES = {"integrated", "kino", "external"}
VALID_PREF_LANGS = {"vf", "vostfr", "vo", "multi"}
# Interface languages (web/js/i18n.js); "" follows the system's language.
UI_LANGUAGES = {"fr", "en", "de", "es", "it", "pt", "ru", "zh", "ja"}
VALID_PREF_QUALITIES = {"2160p", "1080p", "720p", "4k", "480p", "sd"}
VALID_HDR_MODES = {"sdr_pref", "hdr_native", "hdr_boost", "hdr_only", "sdr_only", "auto"}
VALID_SOURCE_PRIORITIES = {"speed", "quality"}
VALID_AUDIO_MODES = {"voice_boost", "standard", "normal", "default", "cinema", "none"}


def _safe_string(value, *, max_len=512):
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > max_len:
        text = text[:max_len]
    return text


def _sanitize_provider_tokens(raw_tokens):
    if not isinstance(raw_tokens, dict):
        return {}
    tokens = {}
    for provider, token in raw_tokens.items():
        provider_name = _safe_string(provider, max_len=64).lower()
        if not provider_name:
            continue
        if provider_name not in DEBRID_PROVIDERS:
            continue
        token_value = _safe_string(token, max_len=2048)
        if token_value:
            tokens[provider_name] = token_value
    return tokens


def _sanitize_download_dir(raw_dir):
    try:
        path = str(raw_dir or "").strip()
    except Exception:
        path = ""
    if not path:
        return str(DEFAULT_DOWNLOAD_DIR)
    if path == "~":
        return str(Path.home())
    return str(Path(path).expanduser())


_LIST_ITEM_KEYS = ("id", "name", "type", "year", "poster", "imdbRating", "user_rating")
MAX_CUSTOM_LISTS = 20
MAX_CUSTOM_LIST_ITEMS = 120


def _sanitize_custom_lists(raw_lists):
    """Keep imported Letterboxd lists in the shape the UI renders, bounded in size."""
    if not isinstance(raw_lists, list):
        return None
    lists = []
    seen = set()
    for raw in raw_lists:
        if not isinstance(raw, dict):
            continue
        list_id = _safe_string(raw.get("id"), max_len=160)
        url = _safe_string(raw.get("url"), max_len=512)
        if not list_id or list_id in seen or not url.startswith("https://"):
            continue
        items = []
        for item in raw.get("items") or []:
            if not isinstance(item, dict) or not _safe_string(item.get("id"), max_len=32):
                continue
            items.append({key: _safe_string(item.get(key), max_len=512) for key in _LIST_ITEM_KEYS})
            if len(items) >= MAX_CUSTOM_LIST_ITEMS:
                break
        try:
            updated_at = int(raw.get("updated_at") or 0)
        except (TypeError, ValueError):
            updated_at = 0
        seen.add(list_id)
        lists.append({
            "id": list_id,
            "title": _safe_string(raw.get("title"), max_len=200),
            "description": _safe_string(raw.get("description"), max_len=1000),
            "url": url,
            "items": items,
            "count": len(items),
            "updated_at": max(0, updated_at),
        })
        if len(lists) >= MAX_CUSTOM_LISTS:
            break
    return lists


def _sanitize_window_bounds(raw_bounds):
    if not isinstance(raw_bounds, dict):
        return None
    try:
        bounds = {key: int(raw_bounds[key]) for key in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    if not (640 <= bounds["width"] <= 20000 and 420 <= bounds["height"] <= 20000):
        return None
    if not all(-100000 <= bounds[key] <= 100000 for key in ("x", "y")):
        return None
    return bounds


def sanitize_config_payload(data):
    if not isinstance(data, dict):
        return {}

    cleaned = {}
    provider = data.get("debrid_provider")
    if provider is not None:
        provider_name = _safe_string(provider, max_len=64).lower()
        if provider_name in DEBRID_PROVIDERS:
            cleaned["debrid_provider"] = provider_name

    token_value = data.get("rd_token")
    if token_value is not None:
        token_text = _safe_string(token_value, max_len=2048)
        if token_text:
            cleaned["rd_token"] = token_text

    if "download_dir" in data:
        cleaned["download_dir"] = _sanitize_download_dir(data.get("download_dir"))

    player_mode = data.get("player_mode")
    if player_mode is not None:
        mode_value = _safe_string(player_mode, max_len=32).lower()
        if mode_value in VALID_PLAYER_MODES:
            cleaned["player_mode"] = mode_value

    ui_language = data.get("ui_language")
    if ui_language is not None:
        ui_value = _safe_string(ui_language, max_len=8).lower()
        if ui_value in UI_LANGUAGES or ui_value == "":
            cleaned["ui_language"] = ui_value

    pref_lang = data.get("pref_lang")
    if pref_lang is not None:
        lang_value = _safe_string(pref_lang, max_len=16).lower()
        if lang_value == "vost":
            lang_value = "vostfr"
        if lang_value in VALID_PREF_LANGS:
            cleaned["pref_lang"] = lang_value

    pref_quality = data.get("pref_quality")
    if pref_quality is not None:
        quality_value = _safe_string(pref_quality, max_len=16).lower()
        if quality_value in {q.lower() for q in VALID_PREF_QUALITIES}:
            cleaned["pref_quality"] = quality_value

    hdr_mode = data.get("hdr_mode")
    if hdr_mode is not None:
        hdr_value = _safe_string(hdr_mode, max_len=32).lower()
        if hdr_value in VALID_HDR_MODES:
            cleaned["hdr_mode"] = hdr_value

    source_priority = data.get("source_priority")
    if source_priority is not None:
        priority_value = _safe_string(source_priority, max_len=16).lower()
        if priority_value in VALID_SOURCE_PRIORITIES:
            cleaned["source_priority"] = priority_value

    audio_mode = data.get("audio_mode")
    if audio_mode is not None:
        audio_value = _safe_string(audio_mode, max_len=32).lower()
        if audio_value in VALID_AUDIO_MODES:
            cleaned["audio_mode"] = audio_value

    if "rd_retention_days" in data:
        try:
            retention_days = int(data.get("rd_retention_days") or 0)
        except (TypeError, ValueError):
            retention_days = 0
        if 0 <= retention_days <= 3650:
            cleaned["rd_retention_days"] = retention_days

    if "discord_rpc" in data:
        value = data.get("discord_rpc")
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                cleaned["discord_rpc"] = True
            elif normalized in {"0", "false", "no", "off"}:
                cleaned["discord_rpc"] = False
        else:
            cleaned["discord_rpc"] = bool(value)

    if "native_player" in data:
        value = data.get("native_player")
        if isinstance(value, bool):
            cleaned["native_player"] = value

    if "auto_fr_subs" in data:
        value = data.get("auto_fr_subs")
        if isinstance(value, bool):
            cleaned["auto_fr_subs"] = value

    if "episode_notifications" in data:
        value = data.get("episode_notifications")
        if isinstance(value, bool):
            cleaned["episode_notifications"] = value

    # Releases already announced by a notification (episode_alerts.py).
    if "notified_episodes" in data:
        value = data.get("notified_episodes")
        if isinstance(value, list):
            cleaned["notified_episodes"] = [_safe_string(item, max_len=120) for item in value
                                            if isinstance(item, str) and item.strip()][:300]

    if "discord_client_id" in data:
        client_id = _safe_string(data.get("discord_client_id"), max_len=128)
        if client_id and re.fullmatch(r"[A-Za-z0-9]+", client_id):
            cleaned["discord_client_id"] = discord_rpc.normalize_client_id(client_id)

    if "provider_tokens" in data:
        provider_tokens = _sanitize_provider_tokens(data.get("provider_tokens"))
        if provider_tokens:
            cleaned["provider_tokens"] = provider_tokens

    if "letterboxd_user" in data:
        user = _safe_string(data.get("letterboxd_user"), max_len=64).lstrip("@")
        if not user or re.fullmatch(r"[A-Za-z0-9_\-]+", user):
            cleaned["letterboxd_user"] = user

    if "custom_lists" in data:
        custom_lists = _sanitize_custom_lists(data.get("custom_lists"))
        if custom_lists is not None:
            cleaned["custom_lists"] = custom_lists

    if "window_bounds" in data:
        bounds = _sanitize_window_bounds(data.get("window_bounds"))
        if bounds:
            cleaned["window_bounds"] = bounds

    if "tv_device" in data:
        device = _sanitize_tv_device(data.get("tv_device"))
        cleaned["tv_device"] = device or {}

    if "tmdb_key" in data:
        # The user's own TMDB key (titles close to a film); empty removes it.
        key = _safe_string(data.get("tmdb_key"), max_len=400).strip()
        cleaned["tmdb_key"] = key if re.fullmatch(r"[A-Za-z0-9._\-]{20,400}", key) else ""

    return cleaned


def _sanitize_tv_device(value):
    """The TV chosen for « Lire sur la TV »: addresses of the local network only."""
    import tv_cast
    if not isinstance(value, dict):
        return None
    location, control = str(value.get("location") or ""), str(value.get("control_url") or "")
    if not (tv_cast.is_local_url(location) and tv_cast.is_local_url(control)):
        return None
    host = urllib.parse.urlsplit(control).hostname or ""
    return {"location": location[:300], "control_url": control[:300], "host": host,
            "name": _safe_string(value.get("name"), max_len=120) or host}


def get_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    return ssl.create_default_context()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PORT = 8080


def port_is_free(port):
    """True when KINO can serve on this port without sharing it with a live server."""
    import socket
    # Anything already accepting on loopback owns the port, even if bound to 127.0.0.1 only.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Same rule as http.server: connections left in TIME_WAIT by the previous
        # KINO must not move the app to another port (the remote URL contains it).
        if sys.platform != "win32":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def find_available_port(start_port=PORT, max_tries=10):
    for port in range(start_port, start_port + max_tries):
        if port_is_free(port):
            return port
    return start_port
CONFIG_FILE = Path.home() / ".rd_cinehub_config.json"
PLAYLIST_FILE = Path.home() / ".kino_playlist.m3u"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".webm")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

MEM_CACHE = {}
MEM_CACHE_LOCK = threading.Lock()
IPC_SOCK_PATH = r"\\.\pipe\kino_mpv" if sys.platform == "win32" else "/tmp/kino_mpv.sock"
# Short-lived files (subtitles for mpv): /tmp on the Mac, as before; Windows has no /tmp.
TEMP_DIR = Path(tempfile.gettempdir()) if sys.platform == "win32" else Path("/tmp")

WINDOW_ACTION_CALLBACK = None
GET_WINDOW_GEOMETRY = None
GET_FORM_HWND = None
DOCK_MPV_WINDOW = None
UNDOCK_MPV_WINDOW = None
SET_FULLSCREEN = None
SET_MAXIMIZED = None
MINIMIZE_WINDOW = None
# Native mpv player controller, set by desktop.py on macOS when libmpv is bundled.
NATIVE_PLAYER = None
# macOS notifications (notifier.Notifier), set by the desktop app when available.
NOTIFIER = None

# Migration douce de l'ancien fichier JSON vers SQLite
kino_db.migrate_from_json(CONFIG_FILE)


MEM_CACHE_MAX_ENTRIES = 400


def _remember(key, val, now):
    """Store in the RAM cache, which never shrank before: a Stream search alone is
    about 280 KB of JSON, kept for the whole session. Oldest entries go first."""
    with MEM_CACHE_LOCK:
        MEM_CACHE[key] = {"val": val, "ts": now}
        if len(MEM_CACHE) > MEM_CACHE_MAX_ENTRIES:
            keep = int(MEM_CACHE_MAX_ENTRIES * 0.9)
            for old_key, _ in sorted(MEM_CACHE.items(), key=lambda item: item[1]["ts"])[:len(MEM_CACHE) - keep]:
                MEM_CACHE.pop(old_key, None)


def cached_get(key, ttl_sec, fetch_fn):
    """Cache hybride L1 (RAM) + L2 (SQLite persistant entre redémarrages)."""
    now = time.time()
    with MEM_CACHE_LOCK:
        hit = MEM_CACHE.get(key)
        if hit and (now - hit["ts"] < ttl_sec):
            return hit["val"]

    # Niveau 2 : SQLite persistant
    db_hit = kino_db.db_cache_get(key)
    if db_hit is not None:
        _remember(key, db_hit, now)
        return db_hit

    val = fetch_fn()
    if val is not None:
        _remember(key, val, now)
        kino_db.db_cache_set(key, val, ttl_sec=ttl_sec)
    return val


DEBRID_PROVIDERS = {
    "realdebrid": {
        "name": "Real-Debrid",
        "short": "RD",
        "badge": "RD+",
        "torrentio_key": "realdebrid",
        "token_url": "https://real-debrid.com/apitoken",
    },
    "alldebrid": {
        "name": "AllDebrid",
        "short": "AD",
        "badge": "AD+",
        "torrentio_key": "alldebrid",
        "token_url": "https://alldebrid.fr/apikeys/",
    },
    "torbox": {
        "name": "TorBox",
        "short": "TB",
        "badge": "TB+",
        "torrentio_key": "torbox",
        "token_url": "https://torbox.app/settings",
    },
    "debridlink": {
        "name": "Debrid-Link",
        "short": "DL",
        "badge": "DL+",
        "torrentio_key": "debridlink",
        "token_url": "https://debrid-link.fr/webapp/apikey",
    },
    "premiumize": {
        "name": "Premiumize",
        "short": "PM",
        "badge": "PM+",
        "torrentio_key": "premiumize",
        "token_url": "https://www.premiumize.me/account",
    },
    "megadebrid": {
        "name": "Mega-Debrid",
        "short": "MD",
        "badge": "MD+",
        "torrentio_key": "",
        "token_url": "https://www.mega-debrid.eu/index.php?page=account",
    },
}

INSTANT_BADGES = ("RD+", "AD+", "TB+", "DL+", "PM+", "OC+", "ED+", "MD+")


def load_config():
    cfg = {
        "debrid_provider": "realdebrid",
        "provider_tokens": {},
        "rd_token": "",
        "download_dir": str(DEFAULT_DOWNLOAD_DIR),
        "player_mode": "integrated" if sys.platform == "darwin" else "kino",
        "pref_lang": "vf",
        "pref_quality": "4k",
        "hdr_mode": "sdr_pref",
        "audio_mode": "voice_boost",
        "rd_retention_days": 0,
        "discord_rpc": True,
        "discord_client_id": discord_rpc.DEFAULT_CLIENT_ID,
    }
    legacy = Path.home() / ".rd_cli_config.json"
    for path in (legacy, CONFIG_FILE):
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cfg.update(sanitize_config_payload(loaded))
            except Exception:
                pass
    if not isinstance(cfg.get("provider_tokens"), dict):
        cfg["provider_tokens"] = {}
    if cfg.get("debrid_provider") not in DEBRID_PROVIDERS:
        cfg["debrid_provider"] = "realdebrid"
    raw_rd = cfg.get("rd_token", "") or os.environ.get("RD_API_TOKEN", "")
    if raw_rd and not cfg["provider_tokens"].get("realdebrid"):
        cfg["provider_tokens"]["realdebrid"] = raw_rd

    active_prov = cfg["debrid_provider"]
    cfg["rd_token"] = cfg["provider_tokens"].get(active_prov, "")
    if "player_mode" not in cfg or not cfg["player_mode"]:
        cfg["player_mode"] = "integrated" if sys.platform == "darwin" else "kino"

    cfg["watchlist"] = kino_db.db_get_watchlist()
    cfg["history"] = kino_db.db_get_history()
    return cfg


def save_config(new_data):
    cfg = load_config()
    updates = sanitize_config_payload(new_data or {})
    if not updates:
        return cfg

    prov_tokens = dict(cfg.get("provider_tokens") or {})
    target_prov = updates.get("debrid_provider") or cfg.get("debrid_provider", "realdebrid")
    if target_prov not in DEBRID_PROVIDERS:
        target_prov = "realdebrid"

    if "rd_token" in updates:
        tok_val = updates["rd_token"].strip()
        if tok_val:
            prov_tokens[target_prov] = tok_val

    # Ma Liste et l'historique ne passent jamais par ici (filtrés ci-dessus) :
    # ils s'écrivent par les fonctions kino_db dédiées. Basculer chaque élément
    # avec db_toggle_watchlist retirerait ceux déjà présents.

    if "discord_rpc" in updates:
        discord_rpc.discord_rpc.enabled = bool(updates["discord_rpc"])
        if not discord_rpc.discord_rpc.enabled:
            discord_rpc.discord_rpc.clear()

    if "discord_client_id" in updates:
        cid = str(updates["discord_client_id"]).strip()
        if cid:
            discord_rpc.discord_rpc.set_client_id(cid)

    cfg.update(updates)
    if "player_mode" in updates and updates["player_mode"]:
        cfg["player_mode"] = str(updates["player_mode"]).strip().lower()
    elif "player_mode" not in cfg or not cfg["player_mode"]:
        cfg["player_mode"] = "integrated" if sys.platform == "darwin" else "kino"
    cfg["debrid_provider"] = target_prov
    if "provider_tokens" in updates and isinstance(updates["provider_tokens"], dict):
        prov_tokens.update(updates["provider_tokens"])
    cfg["provider_tokens"] = prov_tokens
    cfg["rd_token"] = prov_tokens.get(target_prov, "")

    file_cfg = dict(cfg)
    file_cfg.pop("watchlist", None)
    file_cfg.pop("history", None)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(file_cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass
    cfg["watchlist"] = kino_db.db_get_watchlist()
    cfg["history"] = kino_db.db_get_history()
    return cfg


def _public_host(url):
    """Host only: Debrid keys travel in some paths and query strings."""
    try:
        return urllib.parse.urlsplit(str(url)).hostname or "service distant"
    except ValueError:
        return "service distant"


def _upstream_message(body):
    text = (body or "").strip()
    if not text:
        return "réponse vide"
    if text.startswith("<"):
        return "page d'erreur du service"
    return re.sub(r"\s+", " ", text)[:200]


def http_json(url, method="GET", data=None, json_data=None, headers=None, timeout=20, retries=3):
    req_headers = dict(HEADERS)
    if headers:
        req_headers.update(headers)
    encoded_data = None
    if json_data is not None:
        encoded_data = json.dumps(json_data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    elif data is not None:
        encoded_data = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
        req_headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=encoded_data, headers=req_headers, method=method)
    last_error = None
    retryable_statuses = {429, 500, 502, 503, 504}
    # No internet a few seconds ago: say so at once instead of waiting for retries.
    if network_state.known_offline():
        raise network_state.OfflineError()

    for attempt in range(max(0, int(retries)) + 1):
        try:
            ctx = get_ssl_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                network_state.note_success()
                body = resp.read().decode("utf-8", errors="replace")
                if not body.strip():
                    return {}
                try:
                    return json.loads(body)
                except json.JSONDecodeError as json_err:
                    raise RuntimeError(f"Réponse JSON invalide de {_public_host(url)}") from json_err
        except urllib.error.HTTPError as e:
            network_state.note_success()  # the server answered: the internet works
            last_error = e
            err_body = e.read().decode("utf-8", errors="ignore")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error") or err_json.get("detail") or err_body
            except Exception:
                msg = err_body or str(e)
            msg = _upstream_message(msg if isinstance(msg, str) else json.dumps(msg))

            if e.code not in retryable_statuses or attempt >= int(retries):
                raise RuntimeError(f"Erreur API ({e.code}) de {_public_host(url)} : {msg}") from e
            time.sleep(min(2 ** attempt, 2.0))
        except Exception as exc:
            if network_state.is_network_error(exc):
                # No DNS or no route: retrying will not help.
                network_state.note_failure(exc)
                raise network_state.OfflineError() from exc
            last_error = exc
            if attempt >= int(retries):
                raise
            time.sleep(min(2 ** attempt, 2.0))

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Erreur API inconnue de {_public_host(url)}")


def format_size(num_bytes):
    if not num_bytes:
        return "0 B"
    num_bytes = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"


def get_resume_position(imdb_id, season=None, episode=None):
    if not imdb_id:
        return 0
    cfg = load_config()
    hist = cfg.get("history", [])
    item = next((x for x in hist if x.get("id") == imdb_id), None)
    if not item:
        return 0
    if season and episode:
        ep_code = f"S{int(season):02d}E{int(episode):02d}"
        ep_pos = (item.get("ep_positions") or {}).get(ep_code)
        if ep_pos:
            pct = ep_pos.get("pct", 0)
            if 1.0 <= pct < 85.0:
                return int(ep_pos.get("pos", 0))
            return 0
    pct = item.get("progress_pct", 0)
    if 1.0 <= pct < 85.0:
        if not season or (item.get("season") == int(season) and item.get("episode") == int(episode)):
            return int(item.get("position", 0))
    return 0


def remove_history(item_id):
    if item_id == "__all__":
        return kino_db.db_clear_history()
    return kino_db.db_remove_history(item_id)


def _episode_finished(entry):
    """Same threshold as kino_db.db_record_history: the in-app player only sends
    position and duration, never `completed` or `progress_pct`."""
    if entry.get("completed"):
        return True
    try:
        if float(entry.get("progress_pct") or 0) >= 85:
            return True
        position = float(entry.get("position") or 0)
        duration = float(entry.get("duration") or 0)
    except (TypeError, ValueError):
        return False
    return duration > 0 and (position / duration >= 0.88 or duration - position <= 180)


def _clean_progress(entry):
    """Position, duration and share kept only as finite numbers: a strange value
    from a page must not stop the history (found by the error log)."""
    cleaned = dict(entry)
    for key in ("position", "duration", "progress_pct"):
        if key in cleaned and cleaned[key] is not None:
            try:
                number = float(cleaned[key])
            except (TypeError, ValueError):
                number = None
            if number is None or not math.isfinite(number) or number < 0:
                cleaned.pop(key)
            else:
                cleaned[key] = number
    return cleaned


def record_history(entry):
    entry = _clean_progress(entry)
    s_num = entry.get("season")
    e_num = entry.get("episode")
    item_id = entry.get("id") or entry.get("filename")
    if s_num and e_num and item_id:
        try:
            from meta_engine import next_series_episode
            finished = _episode_finished(entry)
            ns, ne, series_done = next_series_episode(item_id, s_num, e_num, allow_network=finished)
            entry["next_season"] = ns
            entry["next_episode"] = ne
            entry["series_finished"] = bool(finished and series_done)
        except Exception:
            pass
    return kino_db.db_record_history(entry)


def refresh_finished_series(limit=40):
    """A series watched to its last episode returns to « À continuer » once new
    episodes are listed. Uses the 24 h episode-list cache: one light request per
    finished series and per day at most."""
    try:
        from meta_engine import next_series_episode
    except Exception:
        return 0
    revived = 0
    finished = [item for item in kino_db.db_get_history()
                if item.get("type") == "series" and item.get("series_finished")][:limit]
    for item in finished:
        try:
            ns, ne, done = next_series_episode(item.get("id"), item.get("season"), item.get("episode"),
                                               allow_network=True)
        except Exception:
            continue
        if not done and kino_db.db_patch_history(item.get("id"), {
                "series_finished": False, "next_season": ns, "next_episode": ne}):
            revived += 1
    return revived


def toggle_watched_status(payload):
    """Marque ou démarque un film, un épisode ou une saison entière de série comme 'Vu'."""
    cfg = load_config()
    hist = cfg.get("history", [])
    item_id = (payload.get("id") or "").strip()
    if not item_id:
        return hist
    mtype = payload.get("type", "movie")
    force_watched = bool(payload.get("force_watched"))
    existing = next((x for x in hist if x.get("id") == item_id), None)

    if mtype == "series" and payload.get("season") and payload.get("season_all"):
        s = int(payload["season"])
        ep_nums = [int(x) for x in (payload.get("episodes") or []) if int(x or 0) > 0]
        if not ep_nums:
            ep_nums = list(range(1, 11))
        season_codes = [f"S{s:02d}E{e:02d}" for e in sorted(set(ep_nums))]
        if existing:
            hist = [x for x in hist if x.get("id") != item_id]
            merged = dict(existing)
        else:
            merged = {
                "id": item_id,
                "name": payload.get("name", ""),
                "type": "series",
                "year": payload.get("year", ""),
                "poster": payload.get("poster", ""),
            }
        watched_eps = list(dict.fromkeys(kino_db.normalize_episode_code(code) for code in (merged.get("watched_episodes") or [])))
        ep_positions = {kino_db.normalize_episode_code(code): pos for code, pos in (merged.get("ep_positions") or {}).items()}
        all_already_watched = all(c in watched_eps for c in season_codes)
        if all_already_watched and not force_watched:
            watched_eps = [c for c in watched_eps if c not in season_codes]
            for c in season_codes:
                ep_positions.pop(c, None)
            if merged.get("season") == s:
                merged["completed"] = False
                merged["progress_pct"] = 0
                merged["position"] = 0
                merged["series_finished"] = False
        else:
            for c in season_codes:
                if c not in watched_eps:
                    watched_eps.append(c)
                ep_positions[c] = {"pos": 2700, "dur": 2700, "pct": 100.0}
            last_ep = max(ep_nums)
            merged["season"] = s
            merged["episode"] = last_ep
            merged["completed"] = True
            merged["progress_pct"] = 100.0
            merged["position"] = 2700
            merged["duration"] = 2700
            try:
                from meta_engine import next_series_episode
                ns, ne, series_done = next_series_episode(item_id, s, last_ep, allow_network=True)
                merged["next_season"] = ns
                merged["next_episode"] = ne
                merged["series_finished"] = series_done
            except Exception:
                pass
        merged["watched_episodes"] = watched_eps[-300:]
        merged["ep_positions"] = ep_positions
        merged["updated_at"] = int(time.time())
        hist.insert(0, merged)
        hist = hist[:1000]
        return kino_db.db_record_history(merged)

    if mtype == "series" and payload.get("season") and payload.get("episode"):
        s = int(payload["season"])
        e = int(payload["episode"])
        ep_code = f"S{s:02d}E{e:02d}"
        if existing:
            hist = [x for x in hist if x.get("id") != item_id]
            merged = dict(existing)
        else:
            merged = {
                "id": item_id,
                "name": payload.get("name", ""),
                "type": "series",
                "year": payload.get("year", ""),
                "poster": payload.get("poster", ""),
            }
        watched_eps = list(dict.fromkeys(kino_db.normalize_episode_code(code) for code in (merged.get("watched_episodes") or [])))
        ep_positions = {kino_db.normalize_episode_code(code): pos for code, pos in (merged.get("ep_positions") or {}).items()}
        if ep_code in watched_eps and not force_watched:
            watched_eps = [c for c in watched_eps if c != ep_code]
            ep_positions.pop(ep_code, None)
            if merged.get("season") == s and merged.get("episode") == e:
                merged["completed"] = False
                merged["progress_pct"] = 0
                merged["position"] = 0
                merged["series_finished"] = False
        else:
            if ep_code not in watched_eps:
                watched_eps.append(ep_code)
            ep_positions[ep_code] = {"pos": 2700, "dur": 2700, "pct": 100.0}
            merged["season"] = s
            merged["episode"] = e
            merged["completed"] = True
            merged["progress_pct"] = 100.0
            merged["position"] = 2700
            merged["duration"] = 2700
            try:
                from meta_engine import next_series_episode
                ns, ne, series_done = next_series_episode(item_id, s, e, allow_network=True)
                merged["next_season"] = ns
                merged["next_episode"] = ne
                merged["series_finished"] = series_done
            except Exception:
                pass
        merged["watched_episodes"] = watched_eps[-300:]
        merged["ep_positions"] = ep_positions
        merged["updated_at"] = int(time.time())
        hist.insert(0, merged)
        hist = hist[:1000]
        return kino_db.db_record_history(merged)

    # Film ou Série entière
    is_currently_done = bool(existing and (existing.get("completed") or float(existing.get("progress_pct") or 0) >= 85.0))
    if is_currently_done and not force_watched:
        return kino_db.db_remove_history(item_id)
    return record_history({
        "id": item_id,
        "name": payload.get("name", ""),
        "type": mtype,
        "year": payload.get("year", ""),
        "poster": payload.get("poster", ""),
        "imdbRating": payload.get("imdbRating", ""),
        "position": 7200,
        "duration": 7200,
    })


# Démarrage de l'agent Discord Rich Presence
try:
    _startup_cfg = load_config()
    if _startup_cfg.get("discord_client_id"):
        discord_rpc.discord_rpc.set_client_id(str(_startup_cfg["discord_client_id"]).strip())
    discord_rpc.discord_rpc.enabled = bool(_startup_cfg.get("discord_rpc", True))
    discord_rpc.discord_rpc.start()
except Exception:
    pass
