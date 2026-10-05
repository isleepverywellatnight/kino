#!/usr/bin/env python3
"""
KINO - Streaming and Media Center
Serveur HTTP local et routeur API modulaire.
Aucune dependance externe requise (Python 3 standard).
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Sous-systemes et moteurs modulaires KINO
import kino_db
import config
from config import *

import meta_engine
from meta_engine import *

import debrid_engine
from debrid_engine import *

import player_engine
from player_engine import *

import torrent_engine
import stream_engine
from stream_engine import StreamQuery, get_available_streams, resolve_playable_stream, DebridResolutionError
import remote_controller
import trakt_engine
import addon_manager
import anime_engine
import community_lists
import intro_engine
import discord_rpc

def get_web_root() -> Path:
    """Trouve l'emplacement du dossier web/ sur disque ou dans le bundle distribuable."""
    base = Path(__file__).resolve().parent
    for cand in (base / "web", base / "_internal" / "web"):
        if cand.is_dir():
            return cand
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for cand_mei in (Path(meipass) / "web", Path(meipass) / "_internal" / "web"):
            if cand_mei.is_dir():
                return cand_mei
    return base / "web"


def load_frontend_file(rel_path: str) -> bytes:
    """Charge un fichier frontend depuis le dossier web/."""
    root = get_web_root()
    target = (root / rel_path).resolve()
    try:
        if target.exists() and target.is_file():
            return target.read_bytes()
    except Exception:
        pass
    return b""




class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, payload, status=200):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def handle_auto_stream(self, params):
        dl_url = resolve_auto_stream_episode(params)
        self.send_response(302)
        self.send_header("Location", dl_url)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.path == "/api/auto-stream":
            try:
                self.handle_auto_stream(params)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
            return
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        try:
            # Fichiers statiques et page principale KINO
            if parsed.path in ("/", "/index.html"):
                raw = load_frontend_file("index.html")
                if not raw:
                    raw = b"<!DOCTYPE html><html><body><h1>KINO Web</h1><p>Frontend non trouve dans web/</p></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(raw)
                return

            if parsed.path.startswith(("/css/", "/js/", "/static/", "/web/")):
                clean_p = parsed.path.lstrip("/")
                for pref in ("static/", "web/"):
                    if clean_p.startswith(pref):
                        clean_p = clean_p[len(pref):]
                raw_asset = load_frontend_file(clean_p)
                if raw_asset:
                    mime = "text/css; charset=utf-8" if clean_p.endswith(".css") else (
                        "application/javascript; charset=utf-8" if clean_p.endswith(".js") else (
                            "image/png" if clean_p.endswith(".png") else (
                                "image/svg+xml" if clean_p.endswith(".svg") else "application/octet-stream"
                            )
                        )
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(raw_asset)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(raw_asset)
                    return
                else:
                    self.send_response(404)
                    self.end_headers()
                    return

            if parsed.path == "/remote":
                remote_html = remote_controller.generate_remote_html(PORT).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(remote_html)))
                self.end_headers()
                self.wfile.write(remote_html)
                return

            if parsed.path == "/api/remote/state":
                self.send_json(remote_controller.get_player_state())
                return

            if parsed.path == "/qrcode.min.js":
                if os.path.exists("qrcode.min.js"):
                    with open("qrcode.min.js", "rb") as qrf:
                        raw = qrf.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return

            if parsed.path in ("/api/remote/info", "/api/remote/qr"):
                local_ip = remote_controller.get_local_ip()
                remote_url = f"http://{local_ip}:{PORT}/remote"
                self.send_json({"status": "ok", "url": remote_url, "local_ip": local_ip, "port": PORT})
                return

            if parsed.path == "/api/player/commands":
                cmds = remote_controller.pop_commands()
                self.send_json({"commands": cmds})
                return

            if parsed.path == "/api/addons":
                addons = addon_manager.list_addons()
                self.send_json({"status": "ok", "addons": addons})
                return

            if parsed.path == "/api/anime/trending":
                items = anime_engine.get_trending_anime()
                self.send_json({"status": "ok", "items": items, "animes": items})
                return

            if parsed.path == "/api/anime/popular":
                limit = int(params.get("limit", 20))
                items = anime_engine.get_popular_anime(limit=limit)
                self.send_json({"status": "ok", "items": items, "animes": items})
                return

            if parsed.path == "/api/anime/search":
                q = params.get("q", "").strip()
                items = anime_engine.search_anime(q) if q else []
                self.send_json({"status": "ok", "items": items, "animes": items})
                return

            if parsed.path == "/api/anime/filler":
                title = params.get("title", "")
                ep = int(params.get("episode", 1))
                res = anime_engine.is_episode_filler(title, ep)
                self.send_json({"status": "ok", "checked": True, **res, "filler": res})
                return

            if parsed.path == "/api/anime/schedule":
                force = params.get("force") == "1"
                sched = anime_engine.get_airing_schedule(force_refresh=force)
                self.send_json(sched)
                return

            if parsed.path == "/api/discord-rpc/status":
                self.send_json({
                    "status": "ok",
                    "connected": getattr(discord_rpc.discord_rpc, "_connected", False),
                    "enabled": discord_rpc.discord_rpc.enabled,
                })
                return

            if parsed.path == "/api/community/curated":
                curated = community_lists.get_curated_lists()
                self.send_json({"status": "ok", "lists": curated, "collections": curated})
                return

            if parsed.path == "/api/trakt/status":
                connected = trakt_engine.is_trakt_connected()
                user_info = kino_db.get_config("trakt_user")
                uname = user_info.get("username") if isinstance(user_info, dict) else None
                self.send_json({"status": "ok", "authenticated": connected, "connected": connected, "username": uname, "user": user_info})
                return

            if parsed.path == "/api/remux":
                raw_url = params.get("url", "")
                ss = params.get("ss", "0")
                if not raw_url:
                    self.send_json({"error": "Paramètre url manquant"}, status=400)
                    return
                ffmpeg = find_ffmpeg()
                if not ffmpeg:
                    self.send_json({"error": "FFmpeg introuvable sur le système"}, status=500)
                    return
                try:
                    start_sec = max(0.0, float(ss))
                except (ValueError, TypeError):
                    start_sec = 0.0

                cmd = [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel", "error",
                ]
                if start_sec > 0:
                    cmd.extend(["-ss", f"{start_sec:.2f}"])
                cmd.extend([
                    "-i", raw_url,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ac", "2",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                    "-f", "mp4",
                    "pipe:1"
                ])

                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "none")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()

                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                try:
                    while True:
                        chunk = proc.stdout.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    try:
                        proc.terminate()
                        proc.wait(timeout=0.8)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                return

            if parsed.path == "/api/auto-stream":
                self.handle_auto_stream(params)
                return

            if parsed.path == "/api/ad-dl":
                prov, tok = _get_provider_and_token(provider="alldebrid")
                raw_link = params.get("link", "")
                q = urllib.parse.urlencode({"agent": "KINO", "apikey": tok, "link": raw_link})
                u = http_json(f"https://api.alldebrid.com/v4/link/unlock?{q}")
                dl_url = ((u.get("data") or {}).get("link")) or ""
                if not dl_url:
                    raise RuntimeError("Impossible de débrider ce lien AllDebrid.")
                self.send_response(302)
                self.send_header("Location", dl_url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            if parsed.path == "/api/torbox-dl":
                prov, tok = _get_provider_and_token(provider="torbox")
                tid = params.get("torrent_id", "")
                fid = params.get("file_id", "0")
                auth = {"Authorization": f"Bearer {tok}"}
                res = http_json(f"https://api.torbox.app/v1/api/torrents/requestdl?token={tok}&torrent_id={tid}&file_id={fid}", headers=auth)
                dl_url = res.get("data") or ""
                if not dl_url:
                    raise RuntimeError("Impossible d'obtenir le lien TorBox.")
                self.send_response(302)
                self.send_header("Location", dl_url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            if parsed.path == "/api/config":
                cfg = load_config()
                prov = cfg.get("debrid_provider", "realdebrid")
                tok = cfg.get("rd_token", "")
                prov_tokens = cfg.get("provider_tokens") or {}
                user_info = None
                ret_days = int(cfg.get("rd_retention_days", 0) or 0)
                if tok:
                    try:
                        user_info = rd_get_user(tok, provider=prov)
                    except Exception:
                        user_info = None
                    if ret_days > 0:
                        threading.Thread(target=rd_cleanup_cloud, args=(tok, ret_days, prov), daemon=True).start()
                self.send_json({
                    "debrid_provider": prov,
                    "configured_providers": {k: bool(v) for k, v in prov_tokens.items()},
                    "has_token": bool(tok),
                    "user": user_info,
                    "download_dir": cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)),
                    "player_mode": cfg.get("player_mode", "kino"),
                    "pref_lang": cfg.get("pref_lang", "vf"),
                    "pref_quality": cfg.get("pref_quality", "4k"),
                    "hdr_mode": cfg.get("hdr_mode", "sdr_pref"),
                    "audio_mode": cfg.get("audio_mode", "voice_boost"),
                    "rd_retention_days": ret_days,
                    "discord_rpc": cfg.get("discord_rpc", True),
                    "discord_client_id": cfg.get("discord_client_id", "631379801826918400"),
                })
                return

            if parsed.path == "/api/intro-times":
                title = params.get("title", "").strip()
                mtype = params.get("type", "series").strip()
                try:
                    season = int(params.get("season", 1) or 1)
                except (ValueError, TypeError):
                    season = 1
                try:
                    episode = int(params.get("episode", 1) or 1)
                except (ValueError, TypeError):
                    episode = 1
                stream_url = params.get("url", "").strip() or None
                imdb_id = params.get("imdb_id", "").strip()

                if not title and imdb_id:
                    try:
                        meta = get_media_meta(imdb_id)
                        title = meta.get("title", "")
                    except Exception:
                        pass

                intro_data = intro_engine.get_intro_timestamps(
                    title=title,
                    media_type=mtype,
                    season=season,
                    episode=episode,
                    stream_url=stream_url
                )
                self.send_json(intro_data)
                return

            if parsed.path == "/api/subtitles":
                imdb_id = params.get("imdb_id", "")
                mtype = params.get("type", "movie")
                season = params.get("season", 1)
                episode = params.get("episode", 1)
                subs = fetch_opensubtitles(imdb_id, mtype, season, episode) if imdb_id else []
                self.send_json({"subtitles": subs})
                return

            if parsed.path == "/api/subtitle-cues":
                sub_url = (params.get("url") or "").strip()
                if not sub_url or not sub_url.startswith("http"):
                    self.send_json({"cues": []})
                    return
                req = urllib.request.Request(sub_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    raw_bytes = resp.read()
                try:
                    srt_text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    srt_text = raw_bytes.decode("latin-1", errors="ignore")
                cues = parse_srt_cues(srt_text)
                self.send_json({"cues": cues})
                return

            if parsed.path == "/api/catalog":
                mtype = params.get("type", "movie")
                genre = params.get("genre", "")
                sort = params.get("sort", "top")
                skip = int(params.get("skip", "0") or 0)
                metas = get_catalog_top(mtype, genre, skip=skip, sort=sort)
                self.send_json({"metas": metas})
                return

            if parsed.path == "/api/trailer":
                title = params.get("title", "")
                year = params.get("year", "")
                yt_id = params.get("yt_id", "")
                lang = params.get("lang", "vf")
                info = resolve_trailer_info(title=title, year=year, yt_id=yt_id, lang=lang)
                self.send_json(info)
                return

            if parsed.path == "/api/search-suggest":
                q = params.get("q", "").strip()
                mtype = params.get("type", "")
                results = kino_db.db_search_fast(q, media_type=mtype if mtype in ("movie", "series") else None, limit=8)
                self.send_json({"ok": True, "results": results})
                return

            if parsed.path == "/api/search":
                q = params.get("q", "")
                mtype = params.get("type", "movie")
                metas = search_cinemeta(q, mtype)
                if not metas:
                    local_matches = kino_db.db_search_fast(q, media_type=mtype if mtype in ("movie", "series") else None, limit=20)
                    if local_matches:
                        metas = local_matches
                self.send_json({"metas": metas})
                return

            if parsed.path == "/api/meta":
                imdb_id = params.get("imdb_id", "")
                mtype = params.get("type", "movie")
                meta = get_media_meta(imdb_id, mtype)
                self.send_json({"meta": meta})
                return

            if parsed.path == "/api/series-meta":
                imdb_id = params.get("imdb_id", "")
                meta = get_series_meta(imdb_id)
                self.send_json({"meta": meta})
                return

            if parsed.path == "/api/user-lists":
                cfg = load_config()
                self.send_json({
                    "watchlist": cfg.get("watchlist", []),
                    "history": cfg.get("history", []),
                    "letterboxd_user": cfg.get("letterboxd_user", ""),
                })
                return

            if parsed.path == "/api/rd-history":
                cfg = load_config()
                prov = cfg.get("debrid_provider", "realdebrid")
                tok = cfg.get("rd_token", "")
                ret_days = int(cfg.get("rd_retention_days", 0) or 0)
                cleaned = {"deleted_downloads": 0, "deleted_torrents": 0}
                if tok and ret_days > 0:
                    cleaned = rd_cleanup_cloud(tok, ret_days, provider=prov)
                items = rd_get_downloads(tok, limit=50, provider=prov)
                self.send_json({"items": items, "cleaned": cleaned})
                return

            if parsed.path == "/api/torrents":
                force = params.get("force") == "1"
                query = StreamQuery.from_params(params)
                torrents = get_available_streams(query, force_refresh=force)
                self.send_json({"torrents": torrents})
                return

            if parsed.path == "/api/letterboxd/custom-lists":
                cfg = load_config()
                self.send_json({"custom_lists": cfg.get("custom_lists", [])})
                return

            if parsed.path == "/api/downloads":
                with DOWNLOADS_LOCK:
                    self.send_json({"downloads": DOWNLOADS})
                return

            self.send_json({"error": "Route introuvable"}, status=404)
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self.read_json_body()

            if parsed.path == "/api/remote/control":
                action = body.get("action", "")
                val = body.get("value")
                remote_controller.push_command(action, val)
                self.send_json({"ok": True, "action": action})
                return

            if parsed.path == "/api/player/state":
                remote_controller.update_player_state(body)
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/addons/toggle":
                aid = body.get("id", "")
                en = bool(body.get("enabled", True))
                addon_manager.toggle_addon(aid, en)
                self.send_json({"ok": True, "addons": addon_manager.list_addons()})
                return

            if parsed.path == "/api/trakt/auth/start":
                res = trakt_engine.start_device_auth()
                self.send_json(res)
                return

            if parsed.path == "/api/trakt/auth/poll":
                dcode = body.get("device_code", "")
                res = trakt_engine.poll_device_token(dcode)
                self.send_json(res)
                return

            if parsed.path == "/api/trakt/disconnect":
                res = trakt_engine.disconnect_trakt()
                self.send_json(res)
                return

            if parsed.path == "/api/trakt/scrobble":
                act = body.get("action", "start")
                meta = body.get("media", {})
                prog = float(body.get("progress", 0.0))
                res = trakt_engine.scrobble_action(act, meta, prog)
                self.send_json(res)
                return

            if parsed.path == "/api/trakt/sync":
                res = trakt_engine.sync_trakt_watchlist()
                self.send_json(res)
                return

            if parsed.path == "/api/config":
                updates = {}
                if body.get("debrid_provider"):
                    updates["debrid_provider"] = body["debrid_provider"].strip().lower()
                new_token = (body.get("rd_token") or "").strip()
                if new_token:
                    updates["rd_token"] = new_token
                if body.get("download_dir"):
                    updates["download_dir"] = body["download_dir"].strip()
                if body.get("player_mode"):
                    updates["player_mode"] = body["player_mode"].strip()
                if body.get("pref_lang"):
                    updates["pref_lang"] = body["pref_lang"].strip()
                if body.get("pref_quality"):
                    updates["pref_quality"] = body["pref_quality"].strip()
                if body.get("hdr_mode"):
                    updates["hdr_mode"] = body["hdr_mode"].strip()
                if body.get("audio_mode"):
                    updates["audio_mode"] = body["audio_mode"].strip()
                if "rd_retention_days" in body and body["rd_retention_days"] is not None:
                    updates["rd_retention_days"] = int(body["rd_retention_days"])
                if "discord_rpc" in body:
                    updates["discord_rpc"] = bool(body["discord_rpc"])
                if "discord_client_id" in body and body["discord_client_id"] is not None:
                    updates["discord_client_id"] = str(body["discord_client_id"]).strip()
                save_config(updates)

                # Validation immédiate auprès du fournisseur débrideur
                check_result = None
                prov = updates.get("debrid_provider") or load_config().get("debrid_provider", "realdebrid")
                target_tok = new_token or load_config().get("rd_token", "")
                if target_tok:
                    try:
                        check_result = rd_get_user(target_tok, provider=prov)
                    except Exception as err:
                        check_result = {"error": str(err)}

                self.send_json({"ok": True, "token_check": check_result})
                return

            if parsed.path == "/api/test-token":
                prov = (body.get("provider") or "realdebrid").strip().lower()
                tok = (body.get("token") or "").strip()
                if not tok:
                    cfg = load_config()
                    tok = cfg.get("provider_tokens", {}).get(prov) or cfg.get("rd_token", "")
                if not tok:
                    self.send_json({"ok": False, "error": "Aucune clé API renseignée."})
                    return
                try:
                    user_info = rd_get_user(tok, provider=prov)
                    self.send_json({"ok": True, "user": user_info})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)})
                return

            if parsed.path == "/api/discord-rpc/update":
                t = body.get("title", "")
                mtype = body.get("media_type", "movie")
                season = body.get("season")
                episode = body.get("episode")
                ep_title = body.get("ep_title")
                cur_t = float(body.get("current_time", 0))
                dur_t = float(body.get("duration", 0))
                poster = body.get("poster")
                is_p = bool(body.get("is_paused", False))
                discord_rpc.discord_rpc.set_activity(
                    title=t,
                    media_type=mtype,
                    season=season,
                    episode=episode,
                    ep_title=ep_title,
                    current_time=cur_t,
                    duration=dur_t,
                    poster_url=poster,
                    is_paused=is_p,
                )
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/discord-rpc/clear":
                discord_rpc.discord_rpc.clear()
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/watched-toggle":
                hist = toggle_watched_status(body)
                self.send_json({"ok": True, "history": hist})
                return

            if parsed.path == "/api/upload-torrent":
                b64_data = body.get("data_b64") or ""
                if not b64_data:
                    raise RuntimeError("Fichier .torrent vide.")
                raw_bytes = base64.b64decode(b64_data)
                info = torrent_file_to_magnet(raw_bytes)
                self.send_json({"ok": True, **info})
                return

            if parsed.path == "/api/save-screenshot":
                cfg = load_config()
                folder = Path(cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)))
                folder.mkdir(parents=True, exist_ok=True)
                data_url = body.get("data_url") or ""
                if "," in data_url:
                    data_url = data_url.split(",", 1)[1]
                raw_png = base64.b64decode(data_url)
                safe_title = re.sub(r"[^a-zA-Z0-9_\-]+", "_", (body.get("title") or "KINO").strip())[:45].strip("_") or "KINO"
                t_str = re.sub(r"[^a-zA-Z0-9_\-]+", "", (body.get("time_str") or "00m00s"))
                fname = f"KINO_Capture_{safe_title}_{t_str}.png"
                out_path = folder / fname
                out_path.write_bytes(raw_png)
                self.send_json({"ok": True, "filename": fname, "path": str(out_path)})
                return

            if parsed.path == "/api/rd-delete":
                cfg = load_config()
                deleted = rd_delete_downloads(
                    cfg.get("rd_token", ""),
                    body.get("ids") or body.get("id"),
                    provider=cfg.get("debrid_provider", "realdebrid"),
                )
                self.send_json({"ok": True, "deleted": deleted})
                return

            if parsed.path == "/api/rd-cleanup":
                cfg = load_config()
                max_age = body.get("max_age_days")
                cleaned = rd_cleanup_cloud(
                    cfg.get("rd_token", ""),
                    max_age_days=max_age,
                    provider=cfg.get("debrid_provider", "realdebrid"),
                )
                self.send_json({"ok": True, "cleaned": cleaned})
                return

            if parsed.path == "/api/watchlist":
                wl = toggle_watchlist(body)
                self.send_json({"ok": True, "watchlist": wl})
                return

            if parsed.path == "/api/import-letterboxd":
                res = import_letterboxd_watchlist(body)
                self.send_json({"ok": True, **res})
                return

            if parsed.path == "/api/letterboxd/import-list":
                url = body.get("url", "")
                res = import_letterboxd_custom_list(url)
                self.send_json({"ok": True, **res})
                return

            if parsed.path == "/api/letterboxd/delete-list":
                lid = body.get("id", "")
                lists = delete_letterboxd_custom_list(lid)
                self.send_json({"ok": True, "custom_lists": lists})
                return

            if parsed.path == "/api/history-remove":
                hist = remove_history(body.get("id", ""))
                self.send_json({"ok": True, "history": hist})
                return

            if parsed.path == "/api/history-record":
                m = body.get("media")
                if isinstance(m, dict) and m.get("id"):
                    hist = record_history(m)
                    self.send_json({"ok": True, "history": hist})
                    return
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/window/action":
                act = body.get("action")
                if WINDOW_ACTION_CALLBACK:
                    WINDOW_ACTION_CALLBACK(act)
                    self.send_json({"ok": True})
                    return
                self.send_json({"ok": False, "note": "Pas de callback fenetre"})
                return

            if parsed.path == "/api/open-folder":
                cfg = load_config()
                folder = Path(cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)))
                folder.mkdir(parents=True, exist_ok=True)
                if sys.platform == "win32":
                    spawn_on_user_desktop(["explorer.exe", str(folder)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(folder)])
                else:
                    subprocess.Popen(["xdg-open", str(folder)])
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/debrid":
                cfg = load_config()
                res = rd_debrid_magnet(
                    cfg.get("rd_token", ""),
                    body.get("magnet", ""),
                    season=body.get("season"),
                    episode=body.get("episode"),
                    resolve_url=body.get("resolve_url"),
                )
                self.send_json(res)
                return

            if parsed.path == "/api/one-click-play":
                cfg = load_config()
                token = cfg.get("rd_token", "")
                if not token:
                    raise RuntimeError("Token API Real-Debrid manquant.")
                imdb_id = body.get("imdb_id", "")
                mtype = body.get("type", "movie")
                season = body.get("season", 1)
                episode = body.get("episode", 1)
                title = body.get("title", "KINO")
                series_name = body.get("name") or title.split(" — ")[0]
                player_mode = str(body.get("player_mode") or cfg.get("player_mode") or ("integrated" if sys.platform == "darwin" else "kino")).lower()

                runtime_min = 0
                if imdb_id and mtype == "movie":
                    try:
                        m_info = get_media_meta(imdb_id, "movie")
                        m_rt = re.search(r"(\d+)", str((m_info or {}).get("runtime") or ""))
                        if m_rt:
                            runtime_min = int(m_rt.group(1))
                    except Exception:
                        pass

                is_series = mtype in ("series", "anime", "tv")
                query = StreamQuery.from_params(body)
                torrents = get_available_streams(query)
                if not torrents:
                    raise RuntimeError("Aucun flux valide trouvé pour ce titre.")

                torrents.sort(key=score_torrent_for_one_click, reverse=True)

                last_err = None
                for cand in torrents[:10]:
                    try:
                        res = rd_debrid_magnet(
                            token,
                            cand.get("magnet", ""),
                            season=season if is_series else None,
                            episode=episode if is_series else None,
                            resolve_url=cand.get("resolve_url", ""),
                        )
                        if res.get("ready") and res.get("files"):
                            target_files = [f for f in res["files"] if f.get("is_target_ep")]
                            if is_series and len(res["files"]) > 1 and not target_files:
                                # Aucun fichier ne correspond à l'épisode recherché dans ce pack -> essayer candidat suivant !
                                continue
                            target_file = target_files[0] if target_files else res["files"][0]
                            playlist_items = None
                            if is_series and imdb_id:
                                playlist_items = build_series_playlist_items(
                                    imdb_id=imdb_id,
                                    season=season,
                                    start_ep=episode,
                                    first_url=target_file["download"],
                                    series_name=series_name,
                                    year=body.get("year", ""),
                                    poster=body.get("poster", ""),
                                    pref_hash=cand.get("info_hash", ""),
                                    first_filename=target_file.get("filename", ""),
                                )
                                prefetch_next_episode(
                                    imdb_id=imdb_id,
                                    season=season,
                                    next_ep=int(episode) + 1,
                                    pref_hash=cand.get("info_hash", ""),
                                )
                            resume_sec = get_resume_position(
                                imdb_id,
                                season if is_series else None,
                                episode if is_series else None,
                            ) if imdb_id else 0
                            media_ctx = {
                                "id": imdb_id,
                                "name": series_name,
                                "type": mtype,
                                "year": body.get("year", ""),
                                "poster": body.get("poster", ""),
                                "season": int(season) if is_series else None,
                                "episode": int(episode) if is_series else None,
                                "filename": target_file["filename"],
                            } if imdb_id else None
                            mpv_info = None
                            is_integrated = (player_mode == "integrated") or (sys.platform == "darwin" and player_mode != "external")
                            if not is_integrated:
                                mpv_info = launch_mpv(
                                    target_file["download"],
                                    title,
                                    playlist_items=playlist_items,
                                    media_ctx=media_ctx,
                                    start_sec=resume_sec,
                                )
                                mpv_info["mode"] = "kino"
                            else:
                                mpv_info = {
                                    "mode": "integrated",
                                    "playlist_count": len(playlist_items) if playlist_items else 1
                                }
                            if media_ctx:
                                record_history(media_ctx)
                            self.send_json({
                                "ok": True,
                                "chosen_torrent": f"{cand.get('source', '')} {cand.get('title', '')}".strip(),
                                "debrid": res,
                                "file": target_file,
                                "stream_url": target_file["download"],
                                "playlist": playlist_items or [],
                                "mpv": mpv_info,
                                "resume_sec": resume_sec,
                            })
                            return
                    except Exception as e:
                        last_err = e
                        continue

                raise RuntimeError(f"Impossible de lancer un flux instantané ({last_err or 'aucun cache RD+ prêt'}).")

            if parsed.path == "/api/debrid-status":
                cfg = load_config()
                res = rd_check_torrent(
                    cfg.get("rd_token", ""),
                    body.get("torrent_id", ""),
                    season=body.get("season"),
                    episode=body.get("episode"),
                )
                self.send_json(res)
                return

            if parsed.path == "/api/download":
                cfg = load_config()
                dl_id = start_background_download(
                    body["url"],
                    body["filename"],
                    cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)),
                )
                self.send_json({"dl_id": dl_id})
                return

            if parsed.path == "/api/download-cancel":
                dl_id = body.get("dl_id")
                with DOWNLOADS_LOCK:
                    if dl_id in DOWNLOADS:
                        DOWNLOADS[dl_id]["cancel"] = True
                self.send_json({"ok": True})
                return

            if parsed.path in ("/api/mpv", "/api/vlc"):
                url = body.get("url", "")
                filename = body.get("filename", "")
                media = body.get("media")
                pack_files = body.get("pack_files")
                playlist_items = None
                if isinstance(pack_files, list) and len(pack_files) > 1:
                    playlist_items = [
                        {"title": f.get("filename", "Épisode"), "url": f.get("download", "")}
                        for f in pack_files
                        if f.get("download")
                    ]
                elif isinstance(media, dict) and media.get("type") == "series" and media.get("id"):
                    playlist_items = build_series_playlist_items(
                        imdb_id=media["id"],
                        season=media.get("season") or 1,
                        start_ep=media.get("episode") or 1,
                        first_url=url,
                        series_name=media.get("name", ""),
                        year=media.get("year", ""),
                        poster=media.get("poster", ""),
                        first_filename=filename,
                    )
                    prefetch_next_episode(
                        imdb_id=media["id"],
                        season=media.get("season") or 1,
                        next_ep=int(media.get("episode") or 1) + 1,
                    )
                display_title = filename
                media_ctx = None
                resume_sec = 0
                if isinstance(media, dict) and media.get("name"):
                    display_title = media["name"]
                    if media.get("season") and media.get("episode"):
                        display_title = f"{media['name']} — S{int(media['season']):02d}E{int(media['episode']):02d}"
                if isinstance(media, dict) and media.get("id"):
                    media_ctx = {
                        "id": media["id"],
                        "name": media.get("name", filename),
                        "type": media.get("type", "movie"),
                        "year": media.get("year", ""),
                        "poster": media.get("poster", ""),
                        "season": int(media["season"]) if media.get("season") else None,
                        "episode": int(media["episode"]) if media.get("episode") else None,
                        "filename": filename,
                    }
                    resume_sec = get_resume_position(
                        media["id"],
                        media_ctx["season"],
                        media_ctx["episode"],
                    )
                mpv_bin = launch_mpv(
                    url,
                    display_title,
                    playlist_items=playlist_items,
                    media_ctx=media_ctx,
                    start_sec=resume_sec,
                )
                if media_ctx:
                    record_history(media_ctx)
                self.send_json({"ok": True, "mpv": mpv_bin, "resume_sec": resume_sec})
                return

            self.send_json({"error": "Route introuvable"}, status=404)
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)


if __name__ == "__main__":
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    url = f"http://127.0.0.1:{PORT}"
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), RequestHandler)
    except Exception:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", PORT), RequestHandler)
        except OSError:
            if "--no-browser" not in sys.argv:
                webbrowser.open(url)
            sys.exit(0)

    print(f"\nKINO est lancé sur : {url}")
    print("Appuyez sur Ctrl+C pour arrêter.\n")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")


