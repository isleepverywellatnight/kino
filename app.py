#!/usr/bin/env python3
"""
KINO - Streaming and Media Center
Serveur HTTP local et routeur API modulaire.
Aucune dependance externe requise (Python 3 standard).
"""

import base64
import concurrent.futures
import json
import os
import ipaddress
import secrets
import hmac
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer as _StdThreadingHTTPServer


class ThreadingHTTPServer(_StdThreadingHTTPServer):
    """The page opens a dozen parallel requests at load; the default backlog of 5
    made macOS reset the extra connections (spoiler settings, downloads...)."""
    request_queue_size = 128
from pathlib import Path

# Sous-systemes et moteurs modulaires KINO
import kino_db
import config
from config import *

import meta_engine
from meta_engine import *

import debrid_engine
from debrid_engine import *
import cloud_titles
import unified_search
import watch_stats
import episode_alerts
import network_state
import diagnostic
import one_click
import person_page
import series_progress
import release_calendar
import ratings

import player_engine
from player_engine import *
import media_probe
import offline_library
import tonight_mode

import torrent_engine
import stream_engine
from stream_engine import StreamQuery, get_available_streams, resolve_playable_stream, DebridResolutionError
import remote_controller
import trakt_engine
import addon_manager
import anime_engine
import anime_home
import anime_journey
import community_lists
import intro_engine
import discord_rpc
import tv_cast
import error_log
import data_backup
import api_routes
import api_service
import playback_support
import native_view
import new_episodes
import series_playback
import spoiler_protection


# --- « Lire sur la TV » (tv_cast.py) ---
_TV_DEVICES = {"at": 0.0, "list": []}
_TV_PRESENCE = {"paused": None, "at": 0.0}


def tv_devices(refresh=False):
    """The TVs of the local network, looked for again after 30 seconds."""
    if refresh or time.time() - _TV_DEVICES["at"] > 30 or not _TV_DEVICES["list"]:
        try:
            _TV_DEVICES["list"] = tv_cast.discover()
        except OSError:
            _TV_DEVICES["list"] = []
        _TV_DEVICES["at"] = time.time()
    return _TV_DEVICES["list"]


def chosen_tv():
    """The TV saved in the settings, else the first one found (saved for next time)."""
    saved = load_config().get("tv_device") or {}
    if saved.get("control_url"):
        return saved
    found = tv_devices()
    if found:
        save_config({"tv_device": found[0]})
        return found[0]
    return None


def tv_presence(session):
    """Discord while the TV plays: on pause or resume, then every 15 seconds."""
    if session is None:
        _TV_PRESENCE.update(paused=None, at=0.0)
        discord_rpc.discord_rpc.clear()
        return
    paused = session["state"] != "PLAYING"
    if paused == _TV_PRESENCE["paused"] and time.time() - _TV_PRESENCE["at"] < 15:
        return
    _TV_PRESENCE.update(paused=paused, at=time.time())
    media = session["media"]
    discord_rpc.discord_rpc.set_activity(
        title=media.get("name") or session["title"], media_type=media.get("type") or "movie",
        season=media.get("season"), episode=media.get("episode"), current_time=session["position"],
        duration=session["duration"], poster_url=media.get("poster") or "", is_paused=paused,
        year=str(media.get("year") or ""), imdb_id=str(media.get("id") or ""))


def tv_next_of(media):
    """The episode after this one on the TV, None when the series is over."""
    season, episode, finished = meta_engine.next_series_episode(media.get("id"), media.get("season"), media.get("episode"),
                                                    allow_network=True)
    return None if finished else {"season": season, "episode": episode}


def tv_episode_params(media):
    return {"imdb_id": str(media.get("id") or ""), "season": media.get("season"), "episode": media.get("episode"),
            "name": media.get("name") or "Série", "year": media.get("year") or "", "poster": media.get("poster") or ""}


def tv_resolve(media):
    """(Debrid link, file name) of the next episode, found like the players' « épisode suivant »."""
    url = resolve_auto_stream_episode(tv_episode_params(media))
    cached = player_engine.AUTO_STREAM_CACHE.get((str(media.get("id") or ""), int(media["season"]), int(media["episode"]))) or {}
    return url, cached.get("filename") or ""


def tv_prefetch(media):
    try:
        resolve_auto_stream_episode(tv_episode_params(media), update_history=False)
    except Exception:
        pass


_BACKUPS = []


def backups():
    """The daily copies of the user's data."""
    if not _BACKUPS:
        from kino_version import APP_VERSION, DATA_SCHEMA
        _BACKUPS.append(data_backup.Backups(db_path=kino_db.DB_PATH, config_path=config.CONFIG_FILE, lock=kino_db._DB_LOCK,
                                            version=APP_VERSION, data_schema=DATA_SCHEMA,
                                            on_error=lambda error: error_log.log.record("sauvegarde du jour", error)))
    return _BACKUPS[0]


TV_CAST = tv_cast.TvCast(lambda: PORT, record_history, remote_controller.update_player_state,
                         remote_controller.pop_commands, presence=tv_presence,
                         next_of=tv_next_of, resolve=tv_resolve, prefetch=tv_prefetch)

REMOTE_PAIRING_TOKEN = secrets.token_urlsafe(32)


# Stable opaque identifier used to avoid retrying a failed source.
stream_candidate_key = stream_engine.stream_candidate_key


def one_click_film(play):
    """A film's data and runtime: the runtime helps tell the film from its namesakes."""
    film, runtime = {}, 0
    if play["imdb_id"] and play["type"] == "movie":
        try:
            film = get_media_meta(play["imdb_id"], "movie") or {}
            runtime = one_click.runtime_minutes(film)
        except Exception:
            pass
    return film, runtime


def one_click_waiting(token, play, film, query, sources, skip, asked_aside, body, cfg):
    """« Préparer en un clic » (docs/playback.md). With « La qualité choisie d'abord »:
    plays the source the page waited for once Debrid has it, or starts preparing the
    best source in the chosen quality when none is ready. Returns (answer, status),
    or None for the usual way: ready sources first."""
    season = play["season"] if play["is_series"] else None
    episode = play["episode"] if play["is_series"] else None
    aliases_for = lambda candidate: one_click.file_aliases(play, film, candidate)
    done = one_click.prepared(body)
    if done:
        candidate = next((s for s in sources if stream_candidate_key(s) == done["source_key"]), {})
        answer = rd_check_torrent(token, done["torrent_id"], season=season, episode=episode)
        if not (answer.get("ready") and answer.get("files")):
            return one_click.preparing(answer, candidate, done["source_key"], sources), 202
        target = stream_engine.select_playable_file(query, answer["files"], aliases_for(candidate))
        if not target:
            return one_click.PREPARED_WRONG_FILE, 422
        started = start_one_click(play, candidate, answer, target)
        return {"ok": True, "source_key": done["source_key"], "attempted_source_keys": [done["source_key"]], **started}, 200
    best = one_click.source_to_prepare(sources, cfg, body, skip)
    if best is None:
        return None
    pending = {}

    def watch(*args, **kwargs):
        answer = rd_debrid_magnet(*args, **kwargs)
        if isinstance(answer, dict) and not answer.get("ready"):
            pending.update(answer)
        return answer

    attempts = stream_engine.try_sources(
        token, [best], query, debrid=watch, season=season, episode=episode, aliases_for=aliases_for,
        stop=playback_support.is_auth_error,
        use=lambda candidate, debrid, target_file, key: start_one_click(play, candidate, debrid, target_file))
    if attempts.found:
        return {"ok": True, "source_key": attempts.key, "attempted_source_keys": sorted(asked_aside | set(attempts.tried)),
                **attempts.result}, 200
    if pending.get("torrent_id"):
        return one_click.preparing(pending, best, stream_candidate_key(best), sources), 202
    return None  # it failed outright: the usual way, ready sources first


def start_one_click(play, candidate, debrid, target_file):
    """« Lecture en un clic », once a source and its file are found: the
    episode's playlist and the next episode, the resume point, then the player.
    Any failure here hands over to the next source (stream_engine.try_sources)."""
    imdb_id, is_series = play["imdb_id"], play["is_series"]
    playlist_items = None
    if is_series and imdb_id:
        playlist_items = build_series_playlist_items(
            imdb_id=imdb_id,
            season=play["season"],
            start_ep=play["episode"],
            first_url=target_file["download"],
            series_name=play["series_name"],
            year=play["year"],
            poster=play["poster"],
            pref_hash=candidate.get("info_hash", ""),
            first_filename=target_file.get("filename", ""),
        )
        prefetch_next_episode(
            imdb_id=imdb_id,
            season=play["season"],
            next_ep=int(play["episode"]) + 1,
            pref_hash=candidate.get("info_hash", ""),
        )
    resume_sec = get_resume_position(
        imdb_id,
        play["season"] if is_series else None,
        play["episode"] if is_series else None,
    ) if imdb_id else 0
    media_ctx = one_click.media_context(play, target_file)
    if one_click.uses_built_in_player(play):
        mpv_info = {"mode": "integrated", "playlist_count": len(playlist_items) if playlist_items else 1}
    else:
        mpv_info = launch_mpv(
            target_file["download"],
            play["title"],
            playlist_items=playlist_items,
            media_ctx=media_ctx,
            start_sec=resume_sec,
        )
        mpv_info["mode"] = "kino"
        # The external player is started: it is watched now.
        if media_ctx:
            record_history(media_ctx)
    return {
        "chosen_torrent": one_click.chosen_label(candidate),
        "debrid": debrid,
        "file": target_file,
        "stream_url": target_file["download"],
        "playlist": playlist_items or [],
        "mpv": mpv_info,
        "resume_sec": resume_sec,
    }


_OFFLINE_MEDIA_ROUTE = re.compile(r"/api/offline/media/([0-9a-f]{32})")

# OpenSubtitles files KINO listed recently: the native player only downloads
# one of these, never an address chosen by the page.
_LISTED_SUBTITLES = {}
_LISTED_SUBTITLES_LOCK = threading.Lock()


def remember_listed_subtitles(subtitles):
    with _LISTED_SUBTITLES_LOCK:
        for item in subtitles or []:
            url = str((item or {}).get("url") or "")
            if url.startswith("https://"):
                _LISTED_SUBTITLES[url] = str(item.get("lang") or "")[:8]
        while len(_LISTED_SUBTITLES) > 300:
            _LISTED_SUBTITLES.pop(next(iter(_LISTED_SUBTITLES)))


# KINO's own redirections to a provider file: next episode, AllDebrid, TorBox.
NATIVE_LOCAL_ROUTES = ("/api/auto-stream", "/api/ad-dl", "/api/torbox-dl")


def native_stream_allowed(url):
    """Debrid sources reach mpv only as an https address, or as one of KINO's own
    local redirections (next episode, AllDebrid, TorBox): never a local file or
    one of mpv's special protocols."""
    url = str(url or "")
    if not url or len(url) > 4000 or any(ord(ch) < 33 for ch in url):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme == "https" and parts.hostname and not parts.username:
        return True
    return (parts.scheme == "http" and parts.hostname == "127.0.0.1" and port == config.PORT
            and parts.path in NATIVE_LOCAL_ROUTES)


def search_everything(query):
    """Films and series at once: catalogue films, catalogue series and French
    titles, merged by closeness. A source that fails leaves the others; with
    nothing found online, titles already known to KINO are offered."""
    query = str(query or "").strip()[:120]
    if not query:
        return []

    def ask(function, *args):
        try:
            return function(*args) or []
        except Exception:
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        movies = pool.submit(ask, search_cinemeta, query, "movie")
        series = pool.submit(ask, search_cinemeta, query, "series")
        french = pool.submit(ask, meta_engine.search_by_french_title, query, "all")
        metas = unified_search.merge_results(query, movies.result(), series.result(), french.result())
    if not metas:
        metas = unified_search.merge_results(query, kino_db.db_search_fast(query, media_type=None, limit=20) or [])
    return metas


def check_new_episodes(send, today=None):
    """Announces releases not announced yet (episode_alerts.py); returns them.

    `send(notification)` gets {title, body, item}. Nothing is sent when the
    setting is off; the first check only records what is already out.
    """
    cfg = load_config()
    if cfg.get("episode_notifications", True) is False:
        return []
    today = today or time.strftime("%Y-%m-%d", time.gmtime())
    items = new_episodes.find_new_episodes(cfg.get("history", []), meta_engine.series_videos, today,
                                           watchlist=cfg.get("watchlist", []))
    seen = cfg.get("notified_episodes") if isinstance(cfg.get("notified_episodes"), list) else None
    alerts, remembered = episode_alerts.pending_alerts(items, seen)
    if remembered != seen:
        save_config({"notified_episodes": remembered})
    for notification in episode_alerts.notifications(alerts):
        send(notification)
    return alerts


def send_notification(notification):
    """One notification through macOS; a click opens the series."""
    notifier = config.NOTIFIER
    if notifier is None:
        return False
    item = notification.get("item") or {}
    info = {key: item.get(key) for key in ("id", "name", "kind", "season", "episode") if item.get(key) not in (None, "")}
    identifier = episode_alerts.alert_key(item) if item else f"kino-summary-{int(time.time())}"
    notifier.send(identifier, notification["title"], notification["body"], info)
    return True


def resolve_offline_episode(imdb_id, season, episode, media):
    """The Debrid link of a queued episode, found like « épisode suivant »;
    the history is left alone: downloading is not watching."""
    return resolve_auto_stream_episode({"imdb_id": imdb_id, "season": season, "episode": episode,
                                        "name": media.get("name") or "Série", "year": media.get("year") or ""},
                                       update_history=False)


offline_library.library.resolve_episode = resolve_offline_episode


STATS_GENRE_FETCH = 30


def viewing_stats(history):
    """Statistics of the history. Genres come from KINO's catalogue, else from
    the title's details (cached), for at most 30 titles per request."""
    entries = [entry for entry in history or [] if isinstance(entry, dict) and str(entry.get("id", "")).startswith("tt")]
    genres = kino_db.db_media_genres([entry["id"] for entry in entries])
    missing = [entry for entry in entries if not genres.get(entry["id"])][:STATS_GENRE_FETCH]

    def details(entry):
        kind = "series" if entry.get("type") == "series" else "movie"
        try:
            meta = get_media_meta(entry["id"], kind) or {}
        except Exception:
            return entry["id"], []
        return entry["id"], [str(name) for name in (meta.get("genres") or meta.get("genre") or []) if name]

    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            genres.update({imdb_id: names for imdb_id, names in pool.map(details, missing) if names})
    return watch_stats.compute(history, lambda entry: genres.get(entry.get("id"), []))


CLOUD_MATCH_TTL = 7 * 86400


def match_cloud_titles(titles, limit=40):
    """{key: catalogue media or None} for Cloud works; a match or a miss is
    remembered for a week, a network failure is not remembered."""
    wanted = []
    for entry in titles[:limit]:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "")[:200]
        title = str(entry.get("title") or "")[:200]
        year = str(entry.get("year") or "")
        kind = entry.get("kind")
        if key and title and kind in ("movie", "series"):
            wanted.append((key, title, year if re.fullmatch(r"(19|20)\d\d", year) else "", kind))

    def resolve(entry):
        key, title, year, kind = entry
        cache_key = f"cloud_match_v2:{kind}:{cloud_titles.comparable(title)}:{year}"
        try:
            media = cached_get(cache_key, CLOUD_MATCH_TTL, lambda: cloud_titles.match_title(
                title, year, kind, meta_engine.search_cinemeta, meta_engine.search_by_french_title))
        except Exception:
            media = None
        return key, (media or None)

    if not wanted:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(resolve, wanted))


def listed_subtitle_lang(url):
    with _LISTED_SUBTITLES_LOCK:
        return _LISTED_SUBTITLES.get(str(url or ""))


def offline_media_file(source):
    """File of a completed offline download addressed by its KINO route, else None.

    The page plays offline films through « /api/offline/media/<id> », a relative
    address: FFmpeg, ffprobe and mpv need the file itself.
    """
    match = _OFFLINE_MEDIA_ROUTE.fullmatch(str(source or "").strip())
    if not match:
        return None
    path = offline_library.library.media_path(match.group(1))
    return str(path) if path and path.is_file() else None


def is_web_stream(source):
    parsed = urllib.parse.urlsplit(str(source or ""))
    return parsed.scheme in ("http", "https") and bool(parsed.hostname) and len(str(source)) <= 8192


def tonight_metadata_key(item):
    media_type = "series" if item.get("type") in ("series", "anime") else "movie"
    return f"tonight_meta_v1:{media_type}:{item.get('id', '')}"


def read_tonight_metadata(item):
    media_type = "series" if item.get("type") in ("series", "anime") else "movie"
    return kino_db.db_cache_get(f"meta_fr_v2:{media_type}:{item.get('id', '')}") or kino_db.db_cache_get(tonight_metadata_key(item))


def fetch_tonight_metadata(item):
    media_id = str(item.get("id") or "")
    if not re.fullmatch(r"tt\d+", media_id):
        return {}
    media_type = "series" if item.get("type") in ("series", "anime") else "movie"
    data = http_json(f"https://v3-cinemeta.strem.io/meta/{media_type}/{media_id}.json", timeout=4, retries=0)
    metadata = data.get("meta") or {}
    if metadata:
        kino_db.db_cache_set(tonight_metadata_key(item), metadata, ttl_sec=86400)
    return metadata


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
        if not target.is_relative_to(root.resolve()):
            return b""
        if target.exists() and target.is_file():
            return target.read_bytes()
    except Exception:
        pass
    return b""




class InvalidRequestBody(ValueError):
    """A POST body that is not a JSON object."""


class PlaybackRoutes:
    """Lecture : sources, débridage, lecteurs, sous-titres, épisode suivant."""

    def get_player_commands(self, parsed, params):
        """GET /api/player/commands"""
        cmds = remote_controller.pop_commands()
        self.send_json({"commands": cmds})
        return

    def get_remux(self, parsed, params):
        """GET /api/remux"""
        raw_url = params.get("url", "")
        ss = params.get("ss", "0")
        if not raw_url:
            self.send_json({"error": "Paramètre url manquant"}, status=400)
            return
        # FFmpeg also opens local paths and its own protocols: only a web
        # stream or a completed offline download may be converted.
        source = offline_media_file(raw_url) or (raw_url if is_web_stream(raw_url) else None)
        if not source:
            self.send_json({"error": "Adresse du flux invalide", "code": "source_unavailable"}, status=400)
            return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.send_json({"error": "FFmpeg introuvable sur le système"}, status=500)
            return
        try:
            start_sec = max(0.0, float(ss))
        except (ValueError, TypeError):
            start_sec = 0.0

        try:
            audio_index = int(params["audio"]) if "audio" in params else None
            if audio_index is not None and not 0 <= audio_index <= 1024:
                raise ValueError("invalid track")
        except (ValueError, TypeError):
            self.send_json({"error": "Piste audio invalide"}, status=400)
            return

        cmd = build_compatible_stream_command(
            ffmpeg, source, start_sec, params.get("video") == "h264",
            audio_index=audio_index, transcode_audio=params.get("acopy") != "1",
            hevc_tag=params.get("hvc1") == "1")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            first_bytes = proc.stdout.read(8)
            if not first_bytes:
                self.send_json({"error": "Ce lien de lecture est expiré ou inaccessible. Essayez une autre source.", "code": "source_unavailable"}, status=502)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "none")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(first_bytes)
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

    def get_auto_stream(self, parsed, params):
        """GET /api/auto-stream"""
        self.handle_auto_stream(params)
        return

    def get_series_playback_preference(self, parsed, params):
        """GET /api/series-playback-preference"""
        try:
            preference = series_playback.get(params.get("series_id"))
            self.send_json({"preference": preference})
        except ValueError:
            self.send_json({"error": "Identifiant de série invalide."}, status=400)
        return

    def post_series_playback_preference(self, parsed, params, body):
        """POST /api/series-playback-preference"""
        try:
            identifier = body.get("series_id")
            if body.get("action") == "clear":
                series_playback.clear(identifier)
                preference = None
            elif body.get("action") == "save":
                preference = series_playback.save(identifier, body.get("preference"))
            else:
                raise ValueError("Action inconnue")
            self.send_json({"ok": True, "preference": preference})
        except (ValueError, TypeError):
            self.send_json({"error": "Préférence de lecture invalide."}, status=400)
        return

    def get_intro_times(self, parsed, params):
        """GET /api/intro-times"""
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

    def get_subtitles(self, parsed, params):
        """GET /api/subtitles"""
        imdb_id = params.get("imdb_id", "")
        mtype = params.get("type", "movie")
        season = params.get("season", 1)
        episode = params.get("episode", 1)
        release = params.get("release", "")[:300]
        try:
            fps = float(params.get("fps") or 0)
        except ValueError:
            fps = 0
        fps = fps if 1 < fps < 200 else None
        subs = fetch_opensubtitles(imdb_id, mtype, season, episode, release, fps) if imdb_id else []
        remember_listed_subtitles(subs)
        self.send_json({"subtitles": subs})
        return

    def get_subtitle_cues(self, parsed, params):
        """GET /api/subtitle-cues"""
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

    def get_native_player_state(self, parsed, params):
        """GET /api/native-player/state"""
        controller = config.NATIVE_PLAYER
        self.send_json(controller.state() if controller is not None else {"open": False})
        return

    def get_trailer(self, parsed, params):
        """GET /api/trailer"""
        title = params.get("title", "")
        year = params.get("year", "")
        yt_id = params.get("yt_id", "")
        lang = params.get("lang", "vf")
        info = resolve_trailer_info(title=title, year=year, yt_id=yt_id, lang=lang)
        self.send_json(info)
        return

    def get_torrents(self, parsed, params):
        """GET /api/torrents"""
        force = params.get("force") == "1"
        query = StreamQuery.from_params(params)
        torrents = get_available_streams(query, force_refresh=force)
        self.send_json({"torrents": torrents})
        return

    def post_playback_feedback(self, parsed, params, body):
        """POST /api/playback-feedback"""
        try:
            context = body.get("context") or {}
            action = body.get("action", "report")
            if action == "clear":
                playback_support.clear(context)
            elif action == "report":
                playback_support.report(context, body.get("source_key"), body.get("reason"))
            else:
                raise ValueError("Action inconnue.")
            self.send_json({"ok": True})
        except (ValueError, TypeError):
            self.send_json({"error": "Contenu, source ou action invalide."}, status=400)
        return

    def post_probe_media(self, parsed, params, body):
        """POST /api/probe-media"""
        source = str(body.get("url") or "")
        source = offline_media_file(source) or (source if is_web_stream(source) else "")
        if not source:
            self.send_json({"error": "Adresse du flux invalide"}, status=400)
            return
        try:
            info = media_probe.inspect_media(source, find_ffmpeg(), shutil.which("ffprobe"), timeout=8)
            selected = media_probe.select_audio_track(info["audio"], load_config().get("pref_lang", "vf"))
            self.send_json({"ok": True, "media": info, "selected_audio": selected})
        except RuntimeError:
            self.send_json({"error": "Impossible d’analyser ce flux"}, status=422)
        return

    def post_player_state(self, parsed, params, body):
        """POST /api/player/state"""
        remote_controller.update_player_state(body)
        self.send_json({"ok": True})
        return

    def post_save_screenshot(self, parsed, params, body):
        """POST /api/save-screenshot"""
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

    def post_upload_torrent(self, parsed, params, body):
        """POST /api/upload-torrent"""
        b64_data = body.get("data_b64") or ""
        if not b64_data:
            raise RuntimeError("Fichier .torrent vide.")
        raw_bytes = base64.b64decode(b64_data)
        info = torrent_file_to_magnet(raw_bytes)
        self.send_json({"ok": True, **info})
        return

    def post_debrid(self, parsed, params, body):
        """POST /api/debrid"""
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

    def post_one_click_play(self, parsed, params, body):
        """POST /api/one-click-play — the steps are told in docs/playback.md."""
        cfg = load_config()
        token = cfg.get("rd_token", "")
        if not token:
            self.send_json(one_click.NO_PROVIDER, status=401)
            return
        play = one_click.request(body, cfg)

        film, runtime = one_click_film(play)
        query = StreamQuery.from_params(body)
        if runtime and not query.runtime_minutes:
            query.runtime_minutes = runtime
        sources = get_available_streams(query)
        if not sources:
            raise RuntimeError(one_click.NO_STREAM)
        sources = one_click.ready_first(sources, cfg)

        # Sources the page set aside, and those that failed for this title before.
        asked_aside = one_click.set_aside(body)
        skip = set(asked_aside)
        if play["imdb_id"]:
            skip.update(playback_support.excluded(body))
        try:
            # « La qualité choisie d'abord »: the chosen quality, even if Debrid must prepare it.
            waiting = one_click_waiting(token, play, film, query, sources, skip, asked_aside, body, cfg)
            if waiting is not None:
                self.send_json(waiting[0], status=waiting[1])
                return
            attempts = stream_engine.try_sources(
                token, sources, query,
                debrid=rd_debrid_magnet,
                season=play["season"] if play["is_series"] else None,
                episode=play["episode"] if play["is_series"] else None,
                aliases_for=lambda candidate: one_click.file_aliases(play, film, candidate),
                skip=skip,
                stop=playback_support.is_auth_error,
                use=lambda candidate, debrid, target_file, key: start_one_click(play, candidate, debrid, target_file),
            )
        except Exception as error:
            # Only a refusal by the provider stops the sources (stop= above).
            if not playback_support.is_auth_error(error):
                raise
            self.send_json(one_click.REFUSED, status=401)
            return
        if not attempts.found:
            self.send_json(one_click.NO_SOURCE, status=422)
            return
        self.send_json({
            "ok": True,
            "source_key": attempts.key,
            "attempted_source_keys": sorted(asked_aside | set(attempts.tried)),
            **attempts.result,
        })
        return

    def post_debrid_status(self, parsed, params, body):
        """POST /api/debrid-status"""
        cfg = load_config()
        res = rd_check_torrent(
            cfg.get("rd_token", ""),
            body.get("torrent_id", ""),
            season=body.get("season"),
            episode=body.get("episode"),
        )
        self.send_json(res)
        return


class CatalogueRoutes:
    """Catalogue et recherche : catalogue, recherche, fiches, anime, nouveaux épisodes."""

    def get_catalog(self, parsed, params):
        """GET /api/catalog"""
        mtype = params.get("type", "movie")
        if mtype not in ("movie", "series", "anime", "classics"):
            self.send_json({"error": "Catalogue inconnu."}, status=400)
            return
        genre = params.get("genre", "")
        sort = params.get("sort", "top")
        skip = max(0, int(params.get("skip", "0"))) if str(params.get("skip", "0")).isdigit() else 0
        metas = get_catalog_top(mtype, genre, skip=skip, sort=sort)
        self.send_json({"metas": metas})
        return

    def get_new_episodes(self, parsed, params):
        """GET /api/new-episodes"""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        cfg = load_config()
        episodes = new_episodes.find_new_episodes(cfg.get("history", []), meta_engine.series_videos, today,
                                                  watchlist=cfg.get("watchlist", []))
        self.send_json({"episodes": episodes, "today": today})
        return

    def get_episodes_fr(self, parsed, params):
        """GET /api/episodes-fr"""
        imdb_id = params.get("imdb_id", "").strip()
        season = params.get("season", "")
        if not re.fullmatch(r"tt\d{1,12}", imdb_id) or not re.fullmatch(r"\d{1,3}", str(season)):
            self.send_json({"error": "Série ou saison invalide."}, status=400)
            return
        self.send_json({"overviews": meta_engine.episode_overviews_fr(imdb_id, int(season))})
        return

    def get_person(self, parsed, params):
        """GET /api/person"""
        name = person_page.clean_name(params.get("name", ""))
        if not name:
            self.send_json({"error": "Nom manquant."}, status=400)
            return
        self.send_json(person_page.page(name))
        return

    def get_people(self, parsed, params):
        """GET /api/people"""
        names = [name for name in params.get("names", "").split("|") if name.strip()][:12]
        self.send_json({"people": meta_engine.person_photos(names)})
        return

    def get_search_suggest(self, parsed, params):
        """GET /api/search-suggest"""
        q = params.get("q", "").strip()
        mtype = params.get("type", "")
        results = kino_db.db_search_fast(q, media_type=mtype if mtype in ("movie", "series") else None, limit=8)
        self.send_json({"ok": True, "results": results})
        return

    def get_search(self, parsed, params):
        """GET /api/search"""
        q = params.get("q", "")
        mtype = params.get("type", "movie")
        # French titles (« SOS Fantômes ») are looked up alongside the catalogue.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            catalogue = pool.submit(search_cinemeta, q, mtype)
            french = pool.submit(meta_engine.search_by_french_title, q, mtype)
            metas = catalogue.result() or []
            try:
                french_matches = french.result() or []
            except Exception:
                french_matches = []
        known = {m.get("id") for m in french_matches}
        metas = french_matches + [m for m in metas if m.get("id") not in known]
        if not metas:
            local_matches = kino_db.db_search_fast(q, media_type=mtype if mtype in ("movie", "series") else None, limit=20)
            if local_matches:
                metas = local_matches
        self.send_json({"metas": metas})
        return

    def get_for_you(self, parsed, params):
        """GET /api/for-you"""
        self.send_json(kino_for_you("series" if params.get("type") in ("series", "anime") else "movie"))
        return

    def get_related(self, parsed, params):
        """GET /api/related"""
        imdb_id = params.get("imdb_id", "").strip()
        if not re.fullmatch(r"tt\d{1,12}", imdb_id):
            self.send_json({"error": "Identifiant de titre invalide."}, status=400)
            return
        self.send_json(related_titles(imdb_id, "series" if params.get("type") in ("series", "anime") else "movie"))
        return

    def get_meta(self, parsed, params):
        """GET /api/meta"""
        imdb_id = params.get("imdb_id", "")
        if not re.fullmatch(r"tt\d{1,12}|[a-z]{2,12}:\d{1,12}", imdb_id.strip()):
            self.send_json({"error": "Identifiant de titre invalide."}, status=400)
            return
        mtype = params.get("type", "movie")
        meta = get_media_meta(imdb_id, mtype)
        self.send_json({"meta": meta})
        return

    def get_series_meta(self, parsed, params):
        """GET /api/series-meta"""
        imdb_id = params.get("imdb_id", "")
        if not re.fullmatch(r"tt\d{1,12}|[a-z]{2,12}:\d{1,12}", imdb_id.strip()):
            self.send_json({"error": "Identifiant de titre invalide."}, status=400)
            return
        meta = get_series_meta(imdb_id)
        self.send_json({"meta": meta})
        return

    def get_anime_trending(self, parsed, params):
        """GET /api/anime/trending"""
        items = anime_engine.get_trending_anime()
        self.send_json({"status": "ok", "items": items, "animes": items})
        return

    def get_anime_popular(self, parsed, params):
        """GET /api/anime/popular"""
        limit = int(params.get("limit", 20))
        items = anime_engine.get_popular_anime(limit=limit)
        self.send_json({"status": "ok", "items": items, "animes": items})
        return

    def get_anime_search(self, parsed, params):
        """GET /api/anime/search"""
        q = params.get("q", "").strip()
        items = anime_engine.search_anime(q) if q else []
        self.send_json({"status": "ok", "items": items, "animes": items})
        return

    def get_anime_journey(self, parsed, params):
        """GET /api/anime/journey"""
        try:
            journey = anime_journey.get_journey(
                params.get("imdb_id"), params.get("title"), params.get("year"),
                params.get("type", "series"), force_refresh=params.get("refresh") == "1",
            )
            self.send_json(journey)
        except (ValueError, TypeError):
            self.send_json({"error": "Œuvre anime invalide."}, status=400)
        return

    def get_anime_filler(self, parsed, params):
        """GET /api/anime/filler"""
        title = params.get("title", "")
        ep = int(params.get("episode", 1))
        res = anime_engine.is_episode_filler(title, ep)
        self.send_json({"status": "ok", "checked": True, **res, "filler": res})
        return

    def get_anime_home(self, parsed, params):
        """GET /api/anime/home"""
        self.send_json(anime_home.get_home(force_refresh=params.get("force") == "1"))
        return

    def get_anime_week(self, parsed, params):
        """GET /api/anime/week"""
        self.send_json(anime_home.get_week(force_refresh=params.get("force") == "1"))
        return

    def post_anime_open(self, parsed, params, body):
        """POST /api/anime/open"""
        try:
            self.send_json(anime_home.open_work(body.get("id")))
        except (ValueError, TypeError):
            self.send_json({"error": "Œuvre anime invalide."}, status=400)
        return

    def post_anime_journey_resolve(self, parsed, params, body):
        """POST /api/anime/journey/resolve"""
        try:
            result = anime_journey.resolve_member(
                body.get("imdb_id"), body.get("type", "series"), body.get("anilist_id"),
            )
            self.send_json(result)
        except (ValueError, TypeError):
            self.send_json({"error": "Œuvre du parcours invalide."}, status=400)
        return

    def post_anime_journey_order(self, parsed, params, body):
        """POST /api/anime/journey/order"""
        try:
            if body.get("action") == "reset":
                current = anime_journey.cached_journey(body.get("imdb_id"), body.get("type", "series"))
                if current["saga_id"] != int(body.get("saga_id")):
                    raise ValueError("Journey does not match saga")
                anime_journey.reset_order(body.get("saga_id"))
                result = anime_journey.cached_journey(body.get("imdb_id"), body.get("type", "series"))
            elif body.get("action") == "save":
                result = anime_journey.save_order(
                    body.get("imdb_id"), body.get("type", "series"),
                    body.get("saga_id"), body.get("ids"),
                )
            else:
                raise ValueError("Invalid order action")
            self.send_json(result)
        except (ValueError, TypeError):
            self.send_json({"error": "Ordre du parcours invalide."}, status=400)
        return

    def post_french_titles(self, parsed, params, body):
        """POST /api/french-titles"""
        items = body.get("items")
        self.send_json({"titles": meta_engine.french_titles(items if isinstance(items, list) else [])})
        return


class LibraryRoutes:
    """Bibliothèque : Ma Liste, historique, hors ligne, statistiques, Ce soir, spoilers, Mes séries, Calendrier."""

    def get_my_series(self, parsed, params):
        """GET /api/my-series"""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        cfg = load_config()
        series = series_progress.my_series(cfg.get("history", []), cfg.get("watchlist", []), meta_engine.series_videos, today)
        self.send_json({"series": series, "today": today})
        return

    def get_calendar(self, parsed, params):
        """GET /api/calendar"""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        cfg = load_config()
        self.send_json(release_calendar.calendar(cfg.get("history", []), cfg.get("watchlist", []), meta_engine.series_videos, today,
                                                 local_today=time.strftime("%Y-%m-%d")))
        return

    def get_stats(self, parsed, params):
        """GET /api/stats"""
        self.send_json(viewing_stats(load_config().get("history", [])))
        return

    def get_user_lists(self, parsed, params):
        """GET /api/user-lists"""
        cfg = load_config()
        self.send_json({
            "watchlist": cfg.get("watchlist", []),
            "history": cfg.get("history", []),
            "letterboxd_user": cfg.get("letterboxd_user", ""),
        })
        return

    def get_downloads(self, parsed, params):
        """GET /api/downloads"""
        download_dir = load_config().get("download_dir", str(DEFAULT_DOWNLOAD_DIR))
        self.send_json({"downloads": offline_library.library.list(),
                        "used_bytes": offline_library.library.usage_bytes(),
                        "free_bytes": offline_library.library.free_bytes(download_dir)})
        return

    def get_spoiler_protection(self, parsed, params):
        """GET /api/spoiler-protection"""
        self.send_json(spoiler_protection.get())
        return

    def post_spoiler_protection(self, parsed, params, body):
        """POST /api/spoiler-protection"""
        try:
            state = spoiler_protection.update(
                body.get("action"), enabled=body.get("enabled"),
                identifier=body.get("series_id"), season=body.get("season"),
                episode=body.get("episode"),
            )
            self.send_json(state)
        except (ValueError, TypeError):
            self.send_json({"error": "Réglage anti-spoilers invalide."}, status=400)
        return

    def post_tonight(self, parsed, params, body):
        """POST /api/tonight"""
        try:
            minutes = int(body.get("minutes") or 0)
            mood = str(body.get("mood") or "discovery")
            offline_only = body.get("offline_only") is True
            if minutes < 15 or minutes > 600 or mood not in ("light", "intense", "discovery"):
                raise ValueError("invalid choice")
            watchlist, coverage = tonight_mode.enrich_watchlist(
                kino_db.db_get_watchlist(), read_tonight_metadata, fetch_tonight_metadata,
                max_fetch=0 if offline_only else 36,
            )
            downloads = offline_library.library.list()
            feedback = tonight_mode.feedback_state()
            history = kino_db.db_get_history()
            choices = tonight_mode.choose_tonight(
                watchlist, history, minutes, mood, feedback=feedback,
                offline_downloads=downloads, offline_only=offline_only,
            )
            empty_reason = tonight_mode.empty_reason(
                watchlist, history, feedback, downloads, offline_only, choices)
            tonight_mode.record_impressions(choices)
            review = [{"id": item["id"], "name": feedback[item["id"]]["name"] or item.get("name"),
                       "decision": feedback[item["id"]]["decision"],
                       "until_ts": feedback[item["id"]]["until_ts"],
                       "undo_token": feedback[item["id"]]["last_action_id"]}
                      for item in watchlist if item["id"] in feedback and
                      (feedback[item["id"]]["decision"] in ("excluded", "seen") or
                       feedback[item["id"]]["decision"] == "deferred" and
                       feedback[item["id"]]["until_ts"] > time.time())]
            self.send_json({"ok": True, "choices": choices, "coverage": coverage,
                            "offline_only": offline_only, "empty_reason": empty_reason,
                            "review": review})
        except ValueError:
            self.send_json({"error": "Choisissez une durée et une ambiance valides."}, status=400)
        return

    def post_tonight_feedback(self, parsed, params, body):
        """POST /api/tonight/feedback"""
        try:
            media_id = str(body.get("id") or "")
            token = tonight_mode.apply_feedback(media_id, body.get("action"))
            self.send_json({"ok": True, "undo_token": token})
        except ValueError:
            self.send_json({"error": "Ce retour ne peut pas être enregistré."}, status=400)
        return

    def post_tonight_undo(self, parsed, params, body):
        """POST /api/tonight/undo"""
        try:
            tonight_mode.undo_feedback(body.get("id"), body.get("undo_token"))
            self.send_json({"ok": True})
        except ValueError:
            self.send_json({"error": "Ce retour ne peut plus être annulé."}, status=409)
        return

    def post_tonight_reset(self, parsed, params, body):
        """POST /api/tonight/reset"""
        tonight_mode.reset_feedback()
        self.send_json({"ok": True})
        return

    def post_watched_toggle(self, parsed, params, body):
        """POST /api/watched-toggle"""
        hist = toggle_watched_status(body)
        self.send_json({"ok": True, "history": hist})
        return

    def post_watchlist(self, parsed, params, body):
        """POST /api/watchlist"""
        wl = toggle_watchlist(body)
        self.send_json({"ok": True, "watchlist": wl})
        return

    def post_history_remove(self, parsed, params, body):
        """POST /api/history-remove"""
        hist = remove_history(body.get("id", ""))
        self.send_json({"ok": True, "history": hist})
        return

    def post_history_record(self, parsed, params, body):
        """POST /api/history-record"""
        m = body.get("media")
        if isinstance(m, dict) and m.get("id"):
            hist = record_history(m)
            self.send_json({"ok": True, "history": hist})
            return
        self.send_json({"ok": True})
        return

    def post_rating(self, parsed, params, body):
        """POST /api/rating"""
        item_id = str(body.get("id") or "")
        try:
            fields = ratings.patch_for(body, time.time())
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
            return
        if not item_id or len(item_id) > 300 or not kino_db.db_patch_history(item_id, fields):
            self.send_json({"error": "Ce titre n'est pas dans l'historique."}, status=404)
            return
        self.send_json({"ok": True, "id": item_id, "fields": fields})
        return

    def post_download(self, parsed, params, body):
        """POST /api/download"""
        cfg = load_config()
        try:
            dl_id, duplicate = offline_library.library.create(
                body.get("url"), body.get("filename"),
                cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)),
                body.get("media"), body.get("subtitles"),
            )
            self.send_json({"dl_id": dl_id, "duplicate": duplicate})
        except offline_library.OfflineError as error:
            self.send_json({"error": str(error), "code": error.code}, status=400)
        return

    def post_download_season(self, parsed, params, body):
        """POST /api/download-season"""
        cfg = load_config()
        episodes = body.get("episodes") if isinstance(body.get("episodes"), list) else []
        media = {key: body.get(key) for key in ("name", "year", "poster") if body.get(key)}
        queued = duplicates = 0
        try:
            for episode in episodes[:60]:
                _, already = offline_library.library.queue_episode(
                    body.get("imdb_id"), body.get("season"), episode,
                    cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)), media)
                duplicates += already
                queued += not already
        except offline_library.OfflineError as error:
            self.send_json({"error": str(error), "code": error.code, "queued": queued}, status=400)
            return
        self.send_json({"queued": queued, "duplicates": duplicates})
        return

    def post_download_cancel(self, parsed, params, body):
        """POST /api/download-cancel"""
        try:
            offline_library.library.pause(body.get("dl_id"))
            self.send_json({"ok": True})
        except offline_library.OfflineError as error:
            self.send_json({"error": str(error), "code": error.code}, status=400)
        return


class DebridRoutes:
    """Cloud Debrid : historique, nettoyage, titres, redirections AllDebrid et TorBox."""

    def get_ad_dl(self, parsed, params):
        """GET /api/ad-dl"""
        # Star imports skip "_" names: call it through its module.
        prov, tok = debrid_engine._get_provider_and_token(provider="alldebrid")
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

    def get_torbox_dl(self, parsed, params):
        """GET /api/torbox-dl"""
        prov, tok = debrid_engine._get_provider_and_token(provider="torbox")
        tid = params.get("torrent_id", "")
        fid = params.get("file_id", "0")
        auth = {"Authorization": f"Bearer {tok}"}
        q = urllib.parse.urlencode({"token": tok, "torrent_id": tid, "file_id": fid})
        res = http_json(f"https://api.torbox.app/v1/api/torrents/requestdl?{q}", headers=auth)
        dl_url = res.get("data") or ""
        if not dl_url:
            raise RuntimeError("Impossible d'obtenir le lien TorBox.")
        self.send_response(302)
        self.send_header("Location", dl_url)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return

    def get_rd_history(self, parsed, params):
        """GET /api/rd-history"""
        cfg = load_config()
        prov = cfg.get("debrid_provider", "realdebrid")
        tok = cfg.get("rd_token", "")
        ret_days = int(cfg.get("rd_retention_days", 0) or 0)
        cleaned = {"deleted_downloads": 0, "deleted_torrents": 0}
        if tok and ret_days > 0:
            cleaned = debrid_engine.rd_auto_cleanup_cloud(tok, ret_days, provider=prov)
        items = rd_get_downloads(tok, limit=100, provider=prov)
        self.send_json({"items": items, "groups": cloud_titles.group_cloud_items(items), "cleaned": cleaned})
        return

    def post_rd_delete(self, parsed, params, body):
        """POST /api/rd-delete"""
        cfg = load_config()
        deleted = rd_delete_downloads(
            cfg.get("rd_token", ""),
            body.get("ids") or body.get("id"),
            provider=cfg.get("debrid_provider", "realdebrid"),
        )
        self.send_json({"ok": True, "deleted": deleted})
        return

    def post_rd_cleanup(self, parsed, params, body):
        """POST /api/rd-cleanup"""
        cfg = load_config()
        max_age = body.get("max_age_days")
        cleaned = rd_cleanup_cloud(
            cfg.get("rd_token", ""),
            max_age_days=max_age,
            provider=cfg.get("debrid_provider", "realdebrid"),
        )
        self.send_json({"ok": True, "cleaned": cleaned})
        return

    def post_cloud_titles(self, parsed, params, body):
        """POST /api/cloud-titles"""
        titles = body.get("titles")
        self.send_json({"matches": match_cloud_titles(titles if isinstance(titles, list) else [])})
        return


class ListsRoutes:
    """Listes et communauté : Letterboxd, sélections d'Explorer."""

    def get_community_curated(self, parsed, params):
        """GET /api/community/curated"""
        curated = community_lists.get_curated_lists()
        self.send_json({"status": "ok", "lists": curated, "collections": curated})
        return

    def get_letterboxd_custom_lists(self, parsed, params):
        """GET /api/letterboxd/custom-lists"""
        cfg = load_config()
        self.send_json({"custom_lists": cfg.get("custom_lists", [])})
        return

    def post_import_letterboxd(self, parsed, params, body):
        """POST /api/import-letterboxd"""
        res = import_letterboxd_watchlist(body)
        self.send_json({"ok": True, **res})
        return

    def post_letterboxd_import_list(self, parsed, params, body):
        """POST /api/letterboxd/import-list"""
        url = body.get("url", "")
        res = import_letterboxd_custom_list(url)
        self.send_json({"ok": True, **res})
        return

    def post_letterboxd_delete_list(self, parsed, params, body):
        """POST /api/letterboxd/delete-list"""
        lid = body.get("id", "")
        lists = delete_letterboxd_custom_list(lid)
        self.send_json({"ok": True, "custom_lists": lists})
        return


class AccountRoutes:
    """Réglages et comptes : configuration, Trakt, Discord, Google Drive, add-ons, notifications."""

    def get_config(self, parsed, params):
        """GET /api/config"""
        self.send_json(api_routes.handle_config_get())
        return

    def post_not_for_me(self, parsed, params, body):
        """POST /api/for-you/not-for-me"""
        media_id = str(body.get("id") or "")
        if not re.fullmatch(r"tt\d{1,12}", media_id):
            self.send_json({"error": "Identifiant de titre invalide."}, status=400)
            return
        suggestion_store().set_not_for_me(media_id, time.time())
        self.send_json({"ok": True})
        return

    def post_for_you_reset(self, parsed, params, body):
        """POST /api/for-you/reset"""
        suggestion_store().reset_feedback()
        self.send_json({"ok": True})
        return

    def get_for_you_status(self, parsed, params):
        """GET /api/for-you/status"""
        count, age = suggestion_store().pool_age(time.time())
        self.send_json({"pool": count, "updated_at": (time.time() - age) if age is not None else None,
                        "not_for_me": len(suggestion_store().not_for_me()), "building": meta_engine._SUGGESTIONS["building"]})
        return

    def post_tmdb_key(self, parsed, params, body):
        """POST /api/tmdb/key"""
        import tmdb
        key = str(body.get("key") or "").strip()
        if not key:
            save_config({"tmdb_key": ""})
            self.send_json({"ok": True, "status": "none"})
            return
        try:
            accepted = tmdb.check_key(key)
        except OSError:
            self.send_json({"error": "TMDB ne répond pas. Vérifiez la connexion et réessayez."}, status=504)
            return
        if not accepted:
            self.send_json({"error": "TMDB refuse cette clé. Copiez la « Clé d'API » de themoviedb.org → Paramètres → API."}, status=400)
            return
        save_config({"tmdb_key": key})
        self.send_json({"ok": True, "status": "valid"})
        return

    def post_config(self, parsed, params, body):
        """POST /api/config"""
        self.send_json(api_routes.handle_config_post(body))
        return

    def get_trakt_status(self, parsed, params):
        """GET /api/trakt/status"""
        connected = trakt_engine.is_trakt_connected()
        user_info = kino_db.get_config("trakt_user")
        uname = user_info.get("username") if isinstance(user_info, dict) else None
        self.send_json({"status": "ok", "authenticated": connected, "connected": connected, "username": uname, "user": user_info})
        return

    def get_sync_gdrive(self, parsed, params):
        """GET /api/sync/gdrive"""
        res = kino_db.db_sync_gdrive()
        self.send_json(res)
        return

    def post_sync_gdrive(self, parsed, params, body):
        """POST /api/sync/gdrive"""
        res = kino_db.db_sync_gdrive()
        self.send_json(res)
        return

    def get_discord_rpc_status(self, parsed, params):
        """GET /api/discord-rpc/status"""
        self.send_json({
            "status": "ok",
            "connected": getattr(discord_rpc.discord_rpc, "_connected", False),
            "enabled": discord_rpc.discord_rpc.enabled,
        })
        return

    def get_addons(self, parsed, params):
        """GET /api/addons"""
        addons = addon_manager.list_addons()
        self.send_json({"status": "ok", "addons": addons})
        return

    def post_trakt_auth_start(self, parsed, params, body):
        """POST /api/trakt/auth/start"""
        res = trakt_engine.start_device_auth()
        self.send_json(res)
        return

    def post_trakt_auth_poll(self, parsed, params, body):
        """POST /api/trakt/auth/poll"""
        dcode = body.get("device_code", "")
        res = trakt_engine.poll_device_token(dcode)
        self.send_json(res)
        return

    def post_trakt_disconnect(self, parsed, params, body):
        """POST /api/trakt/disconnect"""
        res = trakt_engine.disconnect_trakt()
        self.send_json(res)
        return

    def post_trakt_scrobble(self, parsed, params, body):
        """POST /api/trakt/scrobble"""
        act = body.get("action", "start")
        meta = body.get("media", {})
        prog = float(body.get("progress", 0.0))
        res = trakt_engine.scrobble_action(act, meta, prog)
        self.send_json(res)
        return

    def post_trakt_sync(self, parsed, params, body):
        """POST /api/trakt/sync"""
        res = trakt_engine.sync_trakt_watchlist()
        self.send_json(res)
        return

    def post_discord_rpc_update(self, parsed, params, body):
        """POST /api/discord-rpc/update"""
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
            year=str(body.get("year") or ""),
            imdb_id=str(body.get("imdb_id") or ""),
        )
        self.send_json({"ok": True})
        return

    def post_discord_rpc_clear(self, parsed, params, body):
        """POST /api/discord-rpc/clear"""
        discord_rpc.discord_rpc.clear()
        self.send_json({"ok": True})
        return

    def post_addons_toggle(self, parsed, params, body):
        """POST /api/addons/toggle"""
        aid = body.get("id", "")
        en = bool(body.get("enabled", True))
        addon_manager.toggle_addon(aid, en)
        self.send_json({"ok": True, "addons": addon_manager.list_addons()})
        return

    def post_notifications_test(self, parsed, params, body):
        """POST /api/notifications/test"""
        notifier = config.NOTIFIER
        if notifier is None:
            self.send_json({"ok": False, "status": "unavailable"})
            return
        status = notifier.status()
        if status == "not_determined":
            granted = threading.Event()
            notifier.request_permission(lambda ok: granted.set())
            granted.wait(60)
            status = notifier.status()
        if status in ("authorized", "provisional", "ephemeral"):
            notifier.send(f"kino-test-{int(time.time())}", "KINO", "Les notifications des nouveaux épisodes fonctionnent.")
        self.send_json({"ok": status in ("authorized", "provisional", "ephemeral"), "status": status})
        return


class AppRoutes:
    """Application : télécommande, fenêtre, dossier de téléchargement, connexion, diagnostic, TV."""

    def post_client_error(self, parsed, params, body):
        """POST /api/client-error"""
        error_log.log.record(f"page {str(body.get('tab') or '')[:30]}".strip(), message=str(body.get("message") or "")[:500],
                             kind=str(body.get("kind") or "Erreur de la page")[:60],
                             stack=f"{str(body.get('source') or '')[:200]}\n{str(body.get('stack') or '')[:2000]}")
        self.send_json({"ok": True})
        return

    def get_tv_devices(self, parsed, params):
        """GET /api/tv/devices"""
        devices = tv_devices(refresh=params.get("refresh") == "1")
        self.send_json({"devices": devices, "chosen": load_config().get("tv_device") or {}})
        return

    def post_tv_device(self, parsed, params, body):
        """POST /api/tv/device"""
        location = str(body.get("location") or "")
        device = next((d for d in tv_devices() if d["location"] == location), None)
        if not device:
            self.send_json({"error": "Cette TV n'est plus sur le réseau."}, status=404)
            return
        save_config({"tv_device": device})
        self.send_json({"ok": True, "chosen": device})
        return

    def post_tv_play(self, parsed, params, body):
        """POST /api/tv/play"""
        device = chosen_tv()
        if not device:
            self.send_json({"error": "Aucune TV trouvée sur le réseau. Allumez-la, sur la même box que ce Mac."}, status=404)
            return
        media = body.get("media") if isinstance(body.get("media"), dict) else {}
        try:
            status = TV_CAST.start(device, str(body.get("stream_url") or ""), str(body.get("title") or ""),
                                   media, str(body.get("filename") or ""), body.get("resume_sec") or 0)
        except (tv_cast.TvError, ValueError) as error:
            self.send_json({"error": str(error)}, status=502)
            return
        except OSError:
            self.send_json({"error": "La TV ne répond pas. Vérifiez qu'elle est allumée."}, status=504)
            return
        self.send_json({"ok": True, **status})
        return

    def post_tv_control(self, parsed, params, body):
        """POST /api/tv/control"""
        try:
            status = TV_CAST.control(str(body.get("action") or ""), body.get("value"))
        except (tv_cast.TvError, ValueError) as error:
            self.send_json({"error": str(error)}, status=409)
            return
        except OSError:
            self.send_json({"error": "La TV ne répond pas."}, status=504)
            return
        self.send_json({"ok": True, **status})
        return

    def get_tv_status(self, parsed, params):
        """GET /api/tv/status"""
        self.send_json(TV_CAST.status())
        return

    def get_backups(self, parsed, params):
        """GET /api/backups"""
        self.send_json({"items": backups().list(), "keep": data_backup.KEEP_DAILY})
        return

    def post_backup_now(self, parsed, params, body):
        """POST /api/backups/now"""
        try:
            entry = backups().make()
        except (OSError, sqlite3.Error) as error:
            error_log.log.record("sauvegarde", error)
            self.send_json({"error": "La sauvegarde n'a pas pu être faite."}, status=500)
            return
        self.send_json({"ok": True, "backup": entry, "items": backups().list()})
        return

    def post_backup_restore(self, parsed, params, body):
        """POST /api/backups/restore"""
        try:
            result = backups().restore(str(body.get("id") or ""))
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
            return
        except (OSError, sqlite3.Error) as error:
            error_log.log.record("restauration", error)
            self.send_json({"error": "La restauration n'a pas pu se faire. Vos données n'ont pas changé."}, status=500)
            return
        config.MEM_CACHE.clear()
        self.send_json({"ok": True, **result})
        return

    def get_network(self, parsed, params):
        """GET /api/network"""
        self.send_json(network_state.status(refresh=params.get("refresh") == "1"))
        return

    def get_diagnostic(self, parsed, params):
        """GET /api/diagnostic"""
        self.send_json(diagnostic.run())
        return

    def get_remote(self, parsed, params):
        """GET /remote"""
        remote_html = remote_controller.generate_remote_html(PORT).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(remote_html)))
        self.end_headers()
        self.wfile.write(remote_html)
        return

    def get_remote_state(self, parsed, params):
        """GET /api/remote/state"""
        self.send_json(remote_controller.get_player_state())
        return

    def post_remote_control(self, parsed, params, body):
        """POST /api/remote/control"""
        action = body.get("action", "")
        val = body.get("value")
        remote_controller.push_command(action, val)
        self.send_json({"ok": True, "action": action})
        return

    def post_window_action(self, parsed, params, body):
        """POST /api/window/action"""
        act = body.get("action")
        if WINDOW_ACTION_CALLBACK:
            WINDOW_ACTION_CALLBACK(act)
            self.send_json({"ok": True})
            return
        self.send_json({"ok": False, "note": "Pas de callback fenetre"})
        return

    def post_open_folder(self, parsed, params, body):
        """POST /api/open-folder"""
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


class RequestHandler(PlaybackRoutes, CatalogueRoutes, LibraryRoutes, DebridRoutes, ListsRoutes, AccountRoutes, AppRoutes, BaseHTTPRequestHandler):
    # Exact paths and their handler, by theme (the classes above). Paths that
    # need a pattern, a prefix or a second condition stay in do_GET / do_POST.
    GET_ROUTES = {
        # PlaybackRoutes
        "/api/player/commands": "get_player_commands",
        "/api/remux": "get_remux",
        "/api/auto-stream": "get_auto_stream",
        "/api/series-playback-preference": "get_series_playback_preference",
        "/api/intro-times": "get_intro_times",
        "/api/subtitles": "get_subtitles",
        "/api/subtitle-cues": "get_subtitle_cues",
        "/api/native-player/state": "get_native_player_state",
        "/api/trailer": "get_trailer",
        "/api/torrents": "get_torrents",
        # CatalogueRoutes
        "/api/catalog": "get_catalog",
        "/api/new-episodes": "get_new_episodes",
        "/api/episodes-fr": "get_episodes_fr",
        "/api/people": "get_people",
        "/api/person": "get_person",
        "/api/search-suggest": "get_search_suggest",
        "/api/search": "get_search",
        "/api/meta": "get_meta",
        "/api/related": "get_related",
        "/api/for-you": "get_for_you",
        "/api/for-you/status": "get_for_you_status",
        "/api/series-meta": "get_series_meta",
        "/api/anime/trending": "get_anime_trending",
        "/api/anime/popular": "get_anime_popular",
        "/api/anime/search": "get_anime_search",
        "/api/anime/journey": "get_anime_journey",
        "/api/anime/filler": "get_anime_filler",
        "/api/anime/home": "get_anime_home",
        "/api/anime/week": "get_anime_week",
        # LibraryRoutes
        "/api/my-series": "get_my_series",
        "/api/calendar": "get_calendar",
        "/api/stats": "get_stats",
        "/api/user-lists": "get_user_lists",
        "/api/downloads": "get_downloads",
        "/api/spoiler-protection": "get_spoiler_protection",
        # DebridRoutes
        "/api/ad-dl": "get_ad_dl",
        "/api/torbox-dl": "get_torbox_dl",
        "/api/rd-history": "get_rd_history",
        # ListsRoutes
        "/api/community/curated": "get_community_curated",
        "/api/letterboxd/custom-lists": "get_letterboxd_custom_lists",
        # AccountRoutes
        "/api/config": "get_config",
        "/api/trakt/status": "get_trakt_status",
        "/api/sync/gdrive": "get_sync_gdrive",
        "/api/discord-rpc/status": "get_discord_rpc_status",
        "/api/addons": "get_addons",
        # AppRoutes
        "/remote": "get_remote",
        "/api/remote/state": "get_remote_state",
        "/api/tv/devices": "get_tv_devices",
        "/api/tv/status": "get_tv_status",
        "/api/backups": "get_backups",
        "/api/network": "get_network",
        "/api/diagnostic": "get_diagnostic",
    }
    POST_ROUTES = {
        # PlaybackRoutes
        "/api/series-playback-preference": "post_series_playback_preference",
        "/api/playback-feedback": "post_playback_feedback",
        "/api/probe-media": "post_probe_media",
        "/api/player/state": "post_player_state",
        "/api/save-screenshot": "post_save_screenshot",
        "/api/upload-torrent": "post_upload_torrent",
        "/api/debrid": "post_debrid",
        "/api/one-click-play": "post_one_click_play",
        "/api/debrid-status": "post_debrid_status",
        # CatalogueRoutes
        "/api/anime/open": "post_anime_open",
        "/api/anime/journey/resolve": "post_anime_journey_resolve",
        "/api/anime/journey/order": "post_anime_journey_order",
        "/api/french-titles": "post_french_titles",
        # LibraryRoutes
        "/api/spoiler-protection": "post_spoiler_protection",
        "/api/tonight": "post_tonight",
        "/api/tonight/feedback": "post_tonight_feedback",
        "/api/tonight/undo": "post_tonight_undo",
        "/api/tonight/reset": "post_tonight_reset",
        "/api/watched-toggle": "post_watched_toggle",
        "/api/watchlist": "post_watchlist",
        "/api/history-remove": "post_history_remove",
        "/api/history-record": "post_history_record",
        "/api/rating": "post_rating",
        "/api/download": "post_download",
        "/api/download-season": "post_download_season",
        "/api/download-cancel": "post_download_cancel",
        # DebridRoutes
        "/api/rd-delete": "post_rd_delete",
        "/api/rd-cleanup": "post_rd_cleanup",
        "/api/cloud-titles": "post_cloud_titles",
        # ListsRoutes
        "/api/import-letterboxd": "post_import_letterboxd",
        "/api/letterboxd/import-list": "post_letterboxd_import_list",
        "/api/letterboxd/delete-list": "post_letterboxd_delete_list",
        # AccountRoutes
        "/api/config": "post_config",
        "/api/tmdb/key": "post_tmdb_key",
        "/api/for-you/not-for-me": "post_not_for_me",
        "/api/for-you/reset": "post_for_you_reset",
        "/api/sync/gdrive": "post_sync_gdrive",
        "/api/trakt/auth/start": "post_trakt_auth_start",
        "/api/trakt/auth/poll": "post_trakt_auth_poll",
        "/api/trakt/disconnect": "post_trakt_disconnect",
        "/api/trakt/scrobble": "post_trakt_scrobble",
        "/api/trakt/sync": "post_trakt_sync",
        "/api/discord-rpc/update": "post_discord_rpc_update",
        "/api/discord-rpc/clear": "post_discord_rpc_clear",
        "/api/addons/toggle": "post_addons_toggle",
        "/api/notifications/test": "post_notifications_test",
        # AppRoutes
        "/api/remote/control": "post_remote_control",
        "/api/client-error": "post_client_error",
        "/api/tv/device": "post_tv_device",
        "/api/tv/play": "post_tv_play",
        "/api/tv/control": "post_tv_control",
        "/api/backups/now": "post_backup_now",
        "/api/backups/restore": "post_backup_restore",
        "/api/window/action": "post_window_action",
        "/api/open-folder": "post_open_folder",
    }

    def log_message(self, fmt, *args):
        pass

    def send_json(self, payload, status=200):
        # A failure while the internet is down says so, for the interface.
        if status >= 500 and isinstance(payload, dict) and payload.get("error") and "code" not in payload \
                and (payload["error"] == network_state.MESSAGE or network_state.known_offline()):
            payload = {**payload, "code": "offline"}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            raise InvalidRequestBody() from None
        if length <= 0:
            return {}
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise InvalidRequestBody() from None
        if not isinstance(body, dict):
            raise InvalidRequestBody()
        return body

    def _request_allowed(self, path, params):
        # Browsers label requests made by another site (an <img> or <video> pointing
        # here carries no Origin). Players such as mpv or VLC send no Sec-Fetch header.
        if (self.headers.get("Sec-Fetch-Site") or "").strip().lower() in ("cross-site", "same-site"):
            return False
        if path.startswith("/tv/stream/"):
            return TV_CAST.relay_allowed(self.client_address[0], path)
        try:
            local = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            local = False
        if local:
            host = urllib.parse.urlsplit("//" + self.headers.get("Host", "")).hostname
            if host not in ("127.0.0.1", "localhost", "::1"):
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed_origin = urllib.parse.urlparse(origin)
                if (parsed_origin.hostname not in ("127.0.0.1", "localhost", "::1")
                        or parsed_origin.port != self.server.server_port):
                    return False
            return True
        if path not in ("/remote", "/api/remote/state", "/api/remote/control"):
            return False
        return hmac.compare_digest(params.get("token", ""), REMOTE_PAIRING_TOKEN)

    def handle_auto_stream(self, params):
        dl_url = resolve_auto_stream_episode(params)
        self.send_response(302)
        self.send_header("Location", dl_url)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def handle_native_player(self, action, body):
        """Native mpv player (macOS, « Lecteur natif »): offline films and Debrid sources."""
        controller = config.NATIVE_PLAYER
        if controller is None:
            self.send_json({"error": "Lecteur natif indisponible sur cette installation."}, status=503)
            return
        try:
            if action == "open":
                download_id = str(body.get("offline_id") or "")
                stream = body.get("stream_url")
                if stream is not None and not download_id:
                    if not native_stream_allowed(stream):
                        self.send_json({"error": "Adresse de lecture refusée."}, status=400)
                        return
                    path = stream
                else:
                    path = offline_media_file(f"/api/offline/media/{download_id}") \
                        if re.fullmatch(r"[0-9a-f]{32}", download_id) else None
                if not path:
                    self.send_json({"error": "Ce film hors ligne est introuvable."}, status=404)
                    return
                start, volume = body.get("start"), body.get("volume")
                start = float(start) if isinstance(start, (int, float)) and not isinstance(start, bool) else 0.0
                volume = volume if isinstance(volume, (int, float)) and not isinstance(volume, bool) else None
                pref_lang = load_config().get("pref_lang", "vf")
                state = controller.open(path, start=min(max(start, 0.0), 360000.0), volume=volume,
                                        pref_lang=pref_lang)
                if download_id:
                    state = self.add_offline_subtitle(controller, download_id, pref_lang) or state
            elif action == "command":
                state = controller.command(str(body.get("cmd") or ""), body.get("value"))
            elif action == "screenshot":
                cfg = load_config()
                folder = Path(cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)))
                folder.mkdir(parents=True, exist_ok=True)
                safe_title = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(body.get("title") or "KINO").strip())[:45].strip("_") or "KINO"
                t_str = re.sub(r"[^a-zA-Z0-9_\-]+", "", str(body.get("time_str") or "00m00s"))[:20]
                saved = controller.screenshot(folder / f"KINO_Capture_{safe_title}_{t_str}.png")
                self.send_json({"ok": True, "filename": saved.name})
                return
            elif action == "subtitle":
                url = str(body.get("url") or "")
                lang = listed_subtitle_lang(url)
                if lang is None:
                    self.send_json({"error": "Sous-titre inconnu."}, status=400)
                    return
                try:
                    request = urllib.request.Request(url, headers=HEADERS)
                    with urllib.request.urlopen(request, timeout=8) as response:
                        raw = response.read(native_view.SUBTITLE_MAX_BYTES + 1)
                except (urllib.error.URLError, OSError):
                    self.send_json({"error": "Sous-titre indisponible pour le moment."}, status=502)
                    return
                if len(raw) > native_view.SUBTITLE_MAX_BYTES:
                    self.send_json({"error": "Sous-titre trop volumineux."}, status=400)
                    return
                text = native_view.decode_subtitle(raw)
                if not meta_engine.parse_srt_cues(text):
                    self.send_json({"error": "Sous-titre illisible."}, status=400)
                    return
                label = str(body.get("label") or "OpenSubtitles")[:60]
                state = controller.add_subtitle(text, label, lang, select=True)
            else:
                state = controller.close()
            self.send_json({"ok": True, "state": state})
        except (ValueError, TypeError):
            self.send_json({"error": "Commande du lecteur invalide."}, status=400)
        except Exception:
            self.send_json({"error": "Le lecteur natif n'a pas pu démarrer."}, status=500)

    @staticmethod
    def add_offline_subtitle(controller, download_id, pref_lang):
        """The subtitle kept with an offline film, chosen when it was saved, goes to mpv too.
        Selected for « VOSTFR » when it is French; otherwise listed in CC."""
        try:
            subtitle = offline_library.library.subtitles(download_id) or {}
            text = native_view.cues_to_srt(subtitle.get("cues"))
            if not text:
                return None
            lang = str(subtitle.get("lang") or "")[:8]
            return controller.add_subtitle(text, "gardé avec le film", lang, select=pref_lang == "vostfr" and lang == "fr")
        except Exception:
            return None

    def serve_offline_asset(self, path, head_only=False):
        match = re.fullmatch(r"/api/offline/(media|poster|subtitles)/([0-9a-f]{32})", path)
        if not match:
            self.send_error(404)
            return
        kind, download_id = match.groups()
        if kind == "subtitles":
            subtitle = offline_library.library.subtitles(download_id)
            if subtitle is None:
                self.send_error(404)
            elif head_only:
                self.send_response(200)
                self.end_headers()
            else:
                self.send_json({"subtitle": subtitle})
            return
        file_path = (offline_library.library.media_path(download_id) if kind == "media"
                     else offline_library.library.poster_path(download_id))
        if not file_path:
            self.send_error(404)
            return
        size = file_path.stat().st_size
        start, end = 0, size - 1
        partial = False
        if kind == "media" and self.headers.get("Range"):
            request_range = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers["Range"].strip())
            if not request_range or not any(request_range.groups()):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            first, last = request_range.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                suffix = int(last)
                start = max(0, size - suffix)
            if start >= size or end < start or not last and not first:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            partial = True
        if kind == "media":
            content_type = "video/mp4" if file_path.suffix.lower() in (".mp4", ".m4v", ".mov") else "video/x-matroska" if file_path.suffix.lower() == ".mkv" else "application/octet-stream"
        else:
            with file_path.open("rb") as image_file:
                signature = image_file.read(12)
            content_type = "image/png" if signature.startswith(b"\x89PNG") else "image/webp" if signature.startswith(b"RIFF") else "image/jpeg"
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes" if kind == "media" else "none")
        self.send_header("Cache-Control", "private, no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only:
            return
        with file_path.open("rb") as file:
            file.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        if not self._request_allowed(parsed.path, params):
            self.send_error(403)
            return
        if parsed.path.startswith("/api/offline/"):
            self.serve_offline_asset(parsed.path, head_only=True)
            return
        if parsed.path.startswith("/tv/stream/"):
            TV_CAST.relay(self, head=True)
            return
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
        if not self._request_allowed(parsed.path, params):
            self.send_error(403)
            return

        try:
            if parsed.path.startswith("/tv/stream/"):
                TV_CAST.relay(self)
                return
            if parsed.path.startswith("/api/offline/"):
                self.serve_offline_asset(parsed.path)
                return
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

            if parsed.path == "/icons.svg" or parsed.path.startswith(("/css/", "/js/", "/static/", "/web/")):
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

            if parsed.path == "/qrcode.min.js":
                resource_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
                qr_path = resource_dir / "qrcode.min.js"
                if qr_path.is_file():
                    with qr_path.open("rb") as qrf:
                        raw = qrf.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return

            if parsed.path in ("/api/remote/info", "/api/remote/qr"):
                local_ip = remote_controller.get_local_ip()
                remote_url = f"http://{local_ip}:{PORT}/remote?token={REMOTE_PAIRING_TOKEN}"
                self.send_json({"status": "ok", "url": remote_url, "local_ip": local_ip, "port": PORT})
                return

            if parsed.path == "/api/search" and params.get("type") == "all":
                self.send_json({"metas": search_everything(params.get("q", ""))})
                return

            route = self.GET_ROUTES.get(parsed.path)
            if route:
                getattr(self, route)(parsed, params)
                return

            self.send_json({"error": "Route introuvable"}, status=404)
        except Exception as e:
            error_log.log.record(f"serveur GET {parsed.path}", e)
            self.send_json({"error": str(e)}, status=500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        if not self._request_allowed(parsed.path, params):
            self.send_error(403)
            return
        try:
            try:
                body = self.read_json_body()
            except InvalidRequestBody:
                self.send_json({"error": "Requête invalide : un objet JSON est attendu."}, status=400)
                return

            if parsed.path in ("/api/native-player/open", "/api/native-player/command", "/api/native-player/close",
                               "/api/native-player/screenshot", "/api/native-player/subtitle"):
                self.handle_native_player(parsed.path.rsplit("/", 1)[1], body)
                return

            if parsed.path in ("/api/download-resume", "/api/download-restart", "/api/download-remove"):
                try:
                    dl_id = body.get("dl_id")
                    if parsed.path == "/api/download-remove":
                        offline_library.library.remove(dl_id)
                    else:
                        offline_library.library.resume(dl_id, restart=parsed.path == "/api/download-restart")
                    self.send_json({"ok": True})
                except offline_library.OfflineError as error:
                    self.send_json({"error": str(error), "code": error.code}, status=400)
                return

            if parsed.path in ("/api/mpv", "/api/vlc"):
                url = body.get("url", "")
                url = offline_media_file(url) or url
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

            route = self.POST_ROUTES.get(parsed.path)
            if route:
                getattr(self, route)(parsed, params, body)
                return

            self.send_json({"error": "Route introuvable"}, status=404)
        except Exception as e:
            error_log.log.record(f"serveur POST {parsed.path}", e)
            self.send_json({"error": str(e)}, status=500)


if __name__ == "__main__":
    offline_library.library.reconcile()
    kino_db.start_background_maintenance()
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


