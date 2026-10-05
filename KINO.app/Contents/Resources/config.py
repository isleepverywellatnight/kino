#!/usr/bin/env python3
"""
KINO Configuration & Cache Engine
Gestion de la configuration utilisateur, cache persistant L1/L2,
helpers HTTP et utilitaires de formatage.
"""

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import discord_rpc
import kino_db
import ssl


def get_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PORT = 8080
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

WINDOW_ACTION_CALLBACK = None
GET_WINDOW_GEOMETRY = None
GET_FORM_HWND = None
DOCK_MPV_WINDOW = None
UNDOCK_MPV_WINDOW = None
SET_FULLSCREEN = None
SET_MAXIMIZED = None
MINIMIZE_WINDOW = None

# Migration douce de l'ancien fichier JSON vers SQLite
kino_db.migrate_from_json(CONFIG_FILE)


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
        with MEM_CACHE_LOCK:
            MEM_CACHE[key] = {"val": db_hit, "ts": now}
        return db_hit

    val = fetch_fn()
    if val is not None:
        with MEM_CACHE_LOCK:
            MEM_CACHE[key] = {"val": val, "ts": now}
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
        "discord_client_id": "631379801826918400",
    }
    legacy = Path.home() / ".rd_cli_config.json"
    for path in (legacy, CONFIG_FILE):
        if path.exists():
            try:
                cfg.update(json.loads(path.read_text(encoding="utf-8")))
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
    prov_tokens = dict(cfg.get("provider_tokens") or {})
    target_prov = new_data.get("debrid_provider") or cfg.get("debrid_provider", "realdebrid")
    if target_prov not in DEBRID_PROVIDERS:
        target_prov = "realdebrid"

    if "rd_token" in new_data:
        tok_val = (new_data.pop("rd_token") or "").strip()
        if tok_val:
            prov_tokens[target_prov] = tok_val

    if "watchlist" in new_data:
        wl = new_data.get("watchlist")
        if isinstance(wl, list):
            for it in wl:
                if isinstance(it, dict):
                    kino_db.db_toggle_watchlist(it)
    if "history" in new_data:
        hist = new_data.get("history")
        if isinstance(hist, list):
            for entry in hist:
                if isinstance(entry, dict):
                    kino_db.db_record_history(entry)

    if "discord_rpc" in new_data:
        discord_rpc.discord_rpc.enabled = bool(new_data["discord_rpc"])
        if not discord_rpc.discord_rpc.enabled:
            discord_rpc.discord_rpc.clear()

    if "discord_client_id" in new_data:
        cid = str(new_data["discord_client_id"]).strip()
        if cid:
            discord_rpc.discord_rpc.set_client_id(cid)

    cfg.update(new_data)
    if "player_mode" in new_data and new_data["player_mode"]:
        cfg["player_mode"] = str(new_data["player_mode"]).strip().lower()
    elif "player_mode" not in cfg or not cfg["player_mode"]:
        cfg["player_mode"] = "integrated" if sys.platform == "darwin" else "kino"
    cfg["debrid_provider"] = target_prov
    cfg["provider_tokens"] = prov_tokens
    cfg["rd_token"] = prov_tokens.get(target_prov, "")

    file_cfg = dict(cfg)
    file_cfg.pop("watchlist", None)
    file_cfg.pop("history", None)
    CONFIG_FILE.write_text(json.dumps(file_cfg, indent=2), encoding="utf-8")
    cfg["watchlist"] = kino_db.db_get_watchlist()
    cfg["history"] = kino_db.db_get_history()
    return cfg


def http_json(url, method="GET", data=None, json_data=None, headers=None, timeout=20):
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
    try:
        ctx = get_ssl_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body) if body.strip() else {}
        except (urllib.error.URLError, ssl.SSLCertVerificationError, ssl.SSLError, Exception) as ssl_err:
            if "CERTIFICATE_VERIFY_FAILED" in str(ssl_err) or "certificate verify failed" in str(ssl_err):
                unver_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=timeout, context=unver_ctx) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    return json.loads(body) if body.strip() else {}
            raise
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error") or err_json.get("detail") or err_body
        except Exception:
            msg = err_body or str(e)
        raise RuntimeError(f"Erreur API ({e.code}): {msg}") from e


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
        with kino_db._DB_LOCK:
            conn = kino_db.get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM watch_history;")
            finally:
                conn.close()
        return []
    return kino_db.db_remove_history(item_id)


def record_history(entry):
    s_num = entry.get("season")
    e_num = entry.get("episode")
    item_id = entry.get("id") or entry.get("filename")
    if s_num and e_num and item_id:
        try:
            from meta_engine import _compute_next_series_episode
            ns, ne = _compute_next_series_episode(item_id, s_num, e_num)
            entry["next_season"] = ns
            entry["next_episode"] = ne
        except Exception:
            pass
    return kino_db.db_record_history(entry)


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
        watched_eps = list(merged.get("watched_episodes") or [])
        ep_positions = dict(merged.get("ep_positions") or {})
        all_already_watched = all(c in watched_eps for c in season_codes)
        if all_already_watched and not force_watched:
            watched_eps = [c for c in watched_eps if c not in season_codes]
            for c in season_codes:
                ep_positions.pop(c, None)
            if merged.get("season") == s:
                merged["completed"] = False
                merged["progress_pct"] = 0
                merged["position"] = 0
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
                from meta_engine import _compute_next_series_episode
                ns, ne = _compute_next_series_episode(item_id, s, last_ep)
                merged["next_season"] = ns
                merged["next_episode"] = ne
            except Exception:
                pass
        merged["watched_episodes"] = watched_eps[-300:]
        merged["ep_positions"] = ep_positions
        merged["updated_at"] = int(time.time())
        hist.insert(0, merged)
        hist = hist[:1000]
        save_config({"history": hist})
        return hist

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
        watched_eps = list(merged.get("watched_episodes") or [])
        ep_positions = dict(merged.get("ep_positions") or {})
        if ep_code in watched_eps and not force_watched:
            watched_eps = [c for c in watched_eps if c != ep_code]
            ep_positions.pop(ep_code, None)
            if merged.get("season") == s and merged.get("episode") == e:
                merged["completed"] = False
                merged["progress_pct"] = 0
                merged["position"] = 0
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
                from meta_engine import _compute_next_series_episode
                ns, ne = _compute_next_series_episode(item_id, s, e)
                merged["next_season"] = ns
                merged["next_episode"] = ne
            except Exception:
                pass
        merged["watched_episodes"] = watched_eps[-300:]
        merged["ep_positions"] = ep_positions
        merged["updated_at"] = int(time.time())
        hist.insert(0, merged)
        hist = hist[:1000]
        save_config({"history": hist})
        return hist

    # Film ou Série entière
    is_currently_done = bool(existing and (existing.get("completed") or float(existing.get("progress_pct") or 0) >= 85.0))
    if is_currently_done and not force_watched:
        hist = [x for x in hist if x.get("id") != item_id]
        save_config({"history": hist})
        return hist
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
