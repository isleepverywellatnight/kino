#!/usr/bin/env python3
"""
KINO Player & Download Engine
Gestion du lecteur vidéo externe (MPV, IINA, VLC), injection de sous-titres,
contrôle par socket IPC, enchaînement automatique des épisodes de séries (playlists M3U),
résolution dynamique des flux suivants et gestionnaire de téléchargements en arrière-plan.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import discord_rpc
from config import (
    IPC_SOCK_PATH,
    PLAYLIST_FILE,
    PORT,
    VIDEO_EXTENSIONS,
    load_config,
    record_history,
)
from debrid_engine import rd_debrid_magnet, search_torrentio
import stream_engine
from meta_engine import download_top_subtitles_for_mpv, get_series_meta

AUTO_STREAM_CACHE = {}
AUTO_STREAM_LOCK = threading.Lock()
_CURRENT_MPV_PROC_PID = None

_MPV_LAUNCH_LOCK = threading.Lock()
_LAST_MPV_LAUNCH_TIME = 0.0
_LAST_MPV_LAUNCH_URL = ""
_LAST_MPV_LAUNCH_RES = None


def _get_window_cb(name):
    """Récupère dynamiquement le callback de fenêtre fourni par desktop.py ou config.py."""
    app_mod = sys.modules.get("app")
    if app_mod and getattr(app_mod, name, None) is not None:
        return getattr(app_mod, name)
    config_mod = sys.modules.get("config")
    if config_mod and getattr(config_mod, name, None) is not None:
        return getattr(config_mod, name)
    return globals().get(name)


def bundled_mpv():
    """mpv.exe shipped inside KINO for Windows (see tools/fetch_mpv_windows.py), if present."""
    if sys.platform != "win32":
        return None
    folders = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        folders.append(Path(sys._MEIPASS) / "mpv")
    folders.append(Path(__file__).resolve().parent / "vendor" / "mpv-windows")
    for folder in folders:
        if (folder / "mpv.exe").is_file():
            return str(folder / "mpv.exe")
    return None


def find_mpv():
    cfg = load_config()
    candidates = [
        cfg.get("mpv_path"),
        bundled_mpv(),
        shutil.which("mpv"),
        shutil.which("iina-cli"),
        shutil.which("iina"),
        # Chemins standards macOS
        "/opt/homebrew/bin/mpv",
        "/usr/local/bin/mpv",
        "/Applications/mpv.app/Contents/MacOS/mpv",
        "/Applications/IINA.app/Contents/MacOS/iina-cli",
        "/Applications/IINA.app/Contents/MacOS/IINA",
        "/Applications/VLC.app/Contents/MacOS/VLC",
        # Chemins standards Windows
        r"C:\Program Files\mpv\mpv.exe",
        r"C:\Program Files (x86)\mpv\mpv.exe",
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        str(Path.home() / "scoop" / "shims" / "mpv.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "mpv.exe"),
        str(Path.home() / "Downloads" / "mpv-x86_64-20260517-git-059bc7025b" / "mpv.exe"),
    ]
    dl_dir = Path.home() / "Downloads"
    if dl_dir.exists():
        for p in sorted(dl_dir.glob("mpv*/mpv.exe"), reverse=True):
            candidates.append(str(p))

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def find_ffmpeg():
    """Préfère le FFmpeg embarqué, puis une installation système existante."""
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and os.path.isfile(bundled):
            return bundled
    except (ImportError, RuntimeError, OSError):
        pass
    path = shutil.which("ffmpeg")
    if path and os.path.exists(path):
        return path
    candidates = [
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"),
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    wg_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if wg_dir.exists():
        for p in wg_dir.glob("**/ffmpeg.exe"):
            if p.is_file():
                candidates.append(str(p))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def build_compatible_stream_command(ffmpeg, source_url, start_sec=0, transcode_video=False,
                                    audio_index=None, transcode_audio=True, hevc_tag=False):
    """Prepare a browser-compatible MP4, converting only the necessary tracks.

    hevc_tag: the copied video is HEVC. FFmpeg labels it « hev1 » in MP4, which
    AVFoundation (and so the macOS WebKit player) refuses; « hvc1 » plays.
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if transcode_video and sys.platform == "darwin":
        # Décodage matériel (repli logiciel automatique si le codec n'est pas pris en charge).
        cmd.extend(["-hwaccel", "videotoolbox"])
    if start_sec > 0:
        cmd.extend(["-ss", f"{start_sec:.2f}"])
    cmd.extend(["-i", source_url])
    cmd.extend(["-map", "0:v:0?", "-map", f"0:{audio_index}?" if audio_index is not None else "0:a:0?", "-sn"])
    # Une vidéo lisible est toujours recopiée, même après un déplacement : la
    # reprise part de l'image clé précédente, son et image ensemble. Réencoder
    # un film 4K à 60 i/s prenait 1,8 Go et tous les cœurs (2026-11-09) : le Mac
    # de 8 Go se figeait. Une conversion indispensable se fait donc en 1080p au
    # plus, sur quatre fils.
    if transcode_video:
        cmd.extend(["-vf", "scale=-2:'min(1080,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-threads", "4"])
    else:
        cmd.extend(["-c:v", "copy"])
        if hevc_tag:
            cmd.extend(["-tag:v", "hvc1"])
    if transcode_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ac", "2"])
    else:
        cmd.extend(["-c:a", "copy"])
    cmd.extend(["-movflags", "frag_keyframe+empty_moov+default_base_moof", "-frag_duration", "1000000", "-f", "mp4", "pipe:1"])
    return cmd


def spawn_on_user_desktop(args):
    """Lance un processus détaché (cross-platform : Windows via WinSta0\\Default, macOS/Linux via start_new_session)."""
    if sys.platform != "win32":
        proc = subprocess.Popen(args, start_new_session=True)
        return proc.pid
    import ctypes
    import ctypes.wintypes

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD),
            ("lpReserved", ctypes.wintypes.LPWSTR),
            ("lpDesktop", ctypes.wintypes.LPWSTR),
            ("lpTitle", ctypes.wintypes.LPWSTR),
            ("dwX", ctypes.wintypes.DWORD),
            ("dwY", ctypes.wintypes.DWORD),
            ("dwXSize", ctypes.wintypes.DWORD),
            ("dwYSize", ctypes.wintypes.DWORD),
            ("dwXCountChars", ctypes.wintypes.DWORD),
            ("dwYCountChars", ctypes.wintypes.DWORD),
            ("dwFillAttribute", ctypes.wintypes.DWORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("wShowWindow", ctypes.wintypes.WORD),
            ("cbReserved2", ctypes.wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", ctypes.wintypes.HANDLE),
            ("hStdOutput", ctypes.wintypes.HANDLE),
            ("hStdError", ctypes.wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", ctypes.wintypes.HANDLE),
            ("hThread", ctypes.wintypes.HANDLE),
            ("dwProcessId", ctypes.wintypes.DWORD),
            ("dwThreadId", ctypes.wintypes.DWORD),
        ]

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    si.lpDesktop = "WinSta0\\Default"
    si.dwFlags = 1
    si.wShowWindow = 1
    pi = PROCESS_INFORMATION()

    cmd_line = subprocess.list2cmdline(args)
    cmd_buf = ctypes.create_unicode_buffer(cmd_line)
    CREATE_NO_WINDOW = 0x08000000
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000

    ok = ctypes.windll.kernel32.CreateProcessW(
        None,
        cmd_buf,
        None,
        None,
        False,
        CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB,
        None,
        None,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        ok = ctypes.windll.kernel32.CreateProcessW(
            None,
            cmd_buf,
            None,
            None,
            False,
            CREATE_NO_WINDOW,
            None,
            None,
            ctypes.byref(si),
            ctypes.byref(pi),
        )
    if not ok:
        raise RuntimeError(f"CreateProcessW a échoué (code {ctypes.GetLastError()})")

    ctypes.windll.kernel32.CloseHandle(pi.hProcess)
    ctypes.windll.kernel32.CloseHandle(pi.hThread)
    return pi.dwProcessId


def build_series_playlist_items(
    imdb_id,
    season,
    start_ep,
    first_url,
    series_name="",
    year="",
    poster="",
    pref_hash="",
    first_filename="",
):
    """Construit la playlist M3U de la saison : épisode en cours + épisodes suivants via /api/auto-stream."""
    s = int(season or 1)
    ep0 = int(start_ep or 1)
    label_base = (series_name or "Série").strip()

    if imdb_id and first_url:
        with AUTO_STREAM_LOCK:
            AUTO_STREAM_CACHE[(imdb_id, s, ep0)] = {
                "download": first_url,
                "filename": first_filename or f"{label_base}.S{s:02d}E{ep0:02d}",
                "ts": time.time(),
            }

    videos = []
    if imdb_id:
        try:
            meta = get_series_meta(imdb_id)
            if not series_name and meta.get("name"):
                label_base = meta["name"]
            for v in meta.get("videos") or []:
                if int(v.get("season") or 0) == s:
                    ep_num = int(v.get("episode") or v.get("number") or 0)
                    if ep_num > 0:
                        videos.append({
                            "episode": ep_num,
                            "name": v.get("name") or v.get("title") or f"Épisode {ep_num}",
                        })
        except Exception:
            videos = []

    videos.sort(key=lambda x: x["episode"])
    ep0_name = next((v["name"] for v in videos if v["episode"] == ep0), "")
    future_eps = [v for v in videos if v["episode"] > ep0]

    if not future_eps and imdb_id:
        future_eps = [
            {"episode": ep_num, "name": f"Épisode {ep_num}"}
            for ep_num in range(ep0 + 1, min(ep0 + 15, 25))
        ]

    first_title = f"{label_base} — S{s:02d}E{ep0:02d}" + (f" — {ep0_name}" if ep0_name else "")
    items = [{"title": first_title, "url": first_url}]

    for v in future_eps:
        ep_num = v["episode"]
        ep_name = v["name"]
        qs = urllib.parse.urlencode({
            "imdb_id": imdb_id,
            "season": s,
            "episode": ep_num,
            "name": label_base,
            "year": year or "",
            "poster": poster or "",
            "pref_hash": pref_hash or "",
        })
        items.append({
            "title": f"{label_base} — S{s:02d}E{ep_num:02d} — {ep_name}",
            "url": f"http://127.0.0.1:{PORT}/api/auto-stream?{qs}",
        })

    return items


def _episode_entry(episode, filename):
    """The history entry of an episode played or about to be."""
    return {
        "id": episode["imdb_id"],
        "name": episode["name"],
        "type": "series",
        "year": episode["year"],
        "poster": episode["poster"],
        "season": episode["season"],
        "episode": episode["episode"],
        "filename": filename,
    }


def _prefer_previous_source(sources, pref_hash):
    """Keeps the server's ranking; the source of the previous episode wins only a tie."""
    sources.sort(key=lambda c: (
        c.get("score") if isinstance(c.get("score"), (int, float)) else float("-inf"),
        (c.get("info_hash") or "").lower() == pref_hash,
    ), reverse=True)


def resolve_auto_stream_episode(params, *, update_history=True):
    """Résout un épisode ; seul un appel du lecteur actualise l'historique.

    A link found in the last 30 minutes is reused; otherwise the sources are
    tried like « Lecture en un clic » (stream_engine.try_sources)."""
    cfg = load_config()
    token = cfg.get("rd_token", "").strip()
    if not token:
        raise RuntimeError("Token API Real-Debrid manquant.")

    episode = {
        "imdb_id": params.get("imdb_id", "").strip(),
        "season": int(params.get("season") or 1),
        "episode": int(params.get("episode") or 1),
        "name": params.get("name") or "Série",
        "year": params.get("year") or "",
        "poster": params.get("poster") or "",
    }
    imdb_id, s, ep, name = episode["imdb_id"], episode["season"], episode["episode"], episode["name"]
    pref_hash = (params.get("pref_hash") or "").strip().lower()
    label = f"{name} S{s:02d}E{ep:02d}"

    cache_key = (imdb_id, s, ep)
    with AUTO_STREAM_LOCK:
        cached = AUTO_STREAM_CACHE.get(cache_key)
        if cached and (time.time() - cached["ts"] < 1800):
            if imdb_id and update_history:
                record_history(_episode_entry(episode, cached.get("filename", label)))
            return cached["download"]

    if imdb_id:
        q_obj = stream_engine.StreamQuery.from_params({
            "imdb_id": imdb_id, "type": "series", "season": s, "episode": ep,
            "title": name, "year": episode["year"],
        }, cfg)
        torrents = stream_engine.get_available_streams(q_obj)
    else:
        torrents = []
    if not torrents:
        raise RuntimeError(f"Aucun flux trouvé pour {label}.")

    if pref_hash:
        _prefer_previous_source(torrents, pref_hash)
    if cfg.get("source_priority") == "quality":
        # The next episode cannot wait for a preparation either: ready sources first.
        torrents.sort(key=lambda source: not source.get("is_instant"))

    def remember(candidate, debrid, target_file, key):
        dl_url = target_file["download"]
        fname = target_file.get("filename", label)
        with AUTO_STREAM_LOCK:
            AUTO_STREAM_CACHE[cache_key] = {
                "download": dl_url,
                "filename": fname,
                "ts": time.time(),
            }
        if imdb_id and update_history:
            record_history(_episode_entry(episode, fname))
        return dl_url

    attempts = stream_engine.try_sources(token, torrents, q_obj, debrid=rd_debrid_magnet, season=s, episode=ep, use=remember)
    if attempts.found:
        return attempts.result
    raise RuntimeError(f"Impossible de résoudre S{s:02d}E{ep:02d} ({attempts.last_error or 'aucun flux RD+ prêt'}).")


def prefetch_next_episode(imdb_id, season, episode=None, next_ep=None, name="", year="", poster="", pref_hash=""):
    """Pré-résout en arrière-plan le lien du débrideur pour l'épisode suivant."""
    target_ep = int(next_ep) if next_ep is not None else (int(episode) + 1 if episode is not None else 0)
    if not imdb_id or not season or target_ep <= 0:
        return

    def _worker():
        time.sleep(4.0)
        try:
            resolve_auto_stream_episode({
                "imdb_id": imdb_id,
                "season": int(season),
                "episode": target_ep,
                "name": name or "Série",
                "year": year or "",
                "poster": poster or "",
                "pref_hash": pref_hash or "",
            }, update_history=False)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def query_mpv_ipc(sock_path, prop_name):
    if not sock_path:
        return None
    payload = json.dumps({"command": ["get_property", prop_name]}) + "\n"
    if sys.platform == "win32":
        try:
            with open(sock_path, "r+b", buffering=0) as f:
                f.write(payload.encode("utf-8"))
                line = f.readline()
                if line:
                    msg = json.loads(line.decode("utf-8", errors="ignore"))
                    if msg.get("error") == "success":
                        return msg.get("data")
        except Exception:
            return None
        return None

    import socket
    if not hasattr(socket, "AF_UNIX") or not os.path.exists(sock_path):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            s.connect(sock_path)
            s.sendall(payload.encode("utf-8"))
            data = b""
            while b"\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            for line in data.decode("utf-8", errors="ignore").splitlines():
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("error") == "success":
                    return msg.get("data")
    except Exception:
        return None
    return None


def send_mpv_ipc(sock_path, cmd_args):
    if not sock_path or not cmd_args:
        return False
    payload = json.dumps({"command": cmd_args}) + "\n"
    if sys.platform == "win32":
        try:
            with open(sock_path, "r+b", buffering=0) as f:
                f.write(payload.encode("utf-8"))
                return True
        except Exception:
            return False
    import socket
    if not hasattr(socket, "AF_UNIX") or not os.path.exists(sock_path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            s.connect(sock_path)
            s.sendall(payload.encode("utf-8"))
            return True
    except Exception:
        return False
    return False


def background_inject_subtitles(media_ctx):
    if not isinstance(media_ctx, dict) or not media_ctx.get("id"):
        return

    def _worker():
        try:
            time.sleep(1.2)
            ext_subs = download_top_subtitles_for_mpv(
                media_ctx["id"],
                media_ctx.get("type", "movie"),
                media_ctx.get("season", 1),
                media_ctx.get("episode", 1),
                media_ctx.get("filename") or media_ctx.get("title") or "",
            )
            for sub_path in ext_subs:
                send_mpv_ipc(IPC_SOCK_PATH, ["sub-add", sub_path, "auto"])
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def monitor_ipc_playback(proc, media_ctx):
    if not media_ctx or not media_ctx.get("id"):
        return
    base_ep = int(media_ctx.get("episode") or 1)
    season = int(media_ctx.get("season") or 1) if media_ctx.get("type") == "series" else None
    time.sleep(2.0)
    while proc.poll() is None:
        pos = query_mpv_ipc(IPC_SOCK_PATH, "time-pos")
        dur = query_mpv_ipc(IPC_SOCK_PATH, "duration")
        pl_pos = query_mpv_ipc(IPC_SOCK_PATH, "playlist-pos")
        if isinstance(pos, (int, float)) and isinstance(dur, (int, float)) and dur > 30 and pos > 3:
            cur_ep = base_ep + int(pl_pos) if (season and isinstance(pl_pos, int) and pl_pos >= 0) else (base_ep if season else None)
            entry = {
                "id": media_ctx["id"],
                "name": media_ctx.get("name", "KINO"),
                "type": media_ctx.get("type", "movie"),
                "year": media_ctx.get("year", ""),
                "poster": media_ctx.get("poster", ""),
                "season": season,
                "episode": cur_ep,
                "filename": media_ctx.get("filename", ""),
                "position": int(pos),
                "duration": int(dur),
            }
            try:
                record_history(entry)
            except Exception:
                pass
            try:
                is_pause = bool(query_mpv_ipc(IPC_SOCK_PATH, "pause"))
                discord_rpc.discord_rpc.set_activity(
                    title=media_ctx.get("name", "KINO"),
                    media_type=media_ctx.get("type", "movie"),
                    season=season,
                    episode=cur_ep,
                    current_time=float(pos),
                    duration=float(dur),
                    poster_url=media_ctx.get("poster"),
                    is_paused=is_pause,
                )
            except Exception:
                pass
        time.sleep(3.0)


def monitor_mpv_lifecycle(pid, media_ctx=None, is_embedded=False):
    global _CURRENT_MPV_PROC_PID
    set_fullscreen_fn = _get_window_cb("SET_FULLSCREEN")
    set_maximized_fn = _get_window_cb("SET_MAXIMIZED")
    minimize_fn = _get_window_cb("MINIMIZE_WINDOW")
    undock_fn = _get_window_cb("UNDOCK_MPV_WINDOW")
    win_action_fn = _get_window_cb("WINDOW_ACTION_CALLBACK")

    if sys.platform == "win32":
        import ctypes
        SYNCHRONIZE = 0x00100000
        h_proc = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if h_proc:
            if is_embedded:
                def _ipc_events_worker():
                    time.sleep(0.35)
                    try:
                        with open(IPC_SOCK_PATH, "r+b", buffering=0) as pipe:
                            pipe.write(json.dumps({"command": ["observe_property", 1, "fullscreen"]}).encode("utf-8") + b"\n")
                            pipe.write(json.dumps({"command": ["observe_property", 2, "window-maximized"]}).encode("utf-8") + b"\n")
                            pipe.write(json.dumps({"command": ["observe_property", 3, "window-minimized"]}).encode("utf-8") + b"\n")
                            initial_events = 3
                            while _CURRENT_MPV_PROC_PID == pid:
                                line = pipe.readline()
                                if not line:
                                    break
                                try:
                                    msg = json.loads(line.decode("utf-8", errors="ignore"))
                                    if msg.get("event") == "property-change":
                                        if initial_events > 0:
                                            initial_events -= 1
                                            continue
                                        name = msg.get("name")
                                        val = msg.get("data")
                                        if name == "fullscreen" and set_fullscreen_fn and val is not None:
                                            set_fullscreen_fn(bool(val))
                                        elif name == "window-maximized" and set_maximized_fn and val is not None:
                                            set_maximized_fn(bool(val))
                                        elif name == "window-minimized" and minimize_fn and val is True:
                                            minimize_fn()
                                except Exception:
                                    pass
                    except Exception:
                        pass
                threading.Thread(target=_ipc_events_worker, daemon=True).start()

            base_ep = int((media_ctx or {}).get("episode") or 1)
            season = int((media_ctx or {}).get("season") or 1) if (media_ctx and media_ctx.get("type") == "series") else None
            while ctypes.windll.kernel32.WaitForSingleObject(h_proc, 2000) == 258:
                if media_ctx:
                    try:
                        pos = query_mpv_ipc(IPC_SOCK_PATH, "time-pos")
                        dur = query_mpv_ipc(IPC_SOCK_PATH, "duration")
                        pl_pos = query_mpv_ipc(IPC_SOCK_PATH, "playlist-pos")
                        is_paused = bool(query_mpv_ipc(IPC_SOCK_PATH, "pause"))
                        if isinstance(pos, (int, float)) and isinstance(dur, (int, float)) and dur > 30 and pos > 3:
                            cur_ep = base_ep + int(pl_pos) if (season and isinstance(pl_pos, int) and pl_pos >= 0) else (base_ep if season else None)
                            record_history({
                                "id": media_ctx["id"],
                                "name": media_ctx.get("name", "KINO"),
                                "type": media_ctx.get("type", "movie"),
                                "year": media_ctx.get("year", ""),
                                "poster": media_ctx.get("poster", ""),
                                "season": season,
                                "episode": cur_ep,
                                "filename": media_ctx.get("filename", ""),
                                "position": int(pos),
                                "duration": int(dur),
                            })
                            discord_rpc.discord_rpc.set_activity(
                                title=media_ctx.get("name", "KINO"),
                                media_type=media_ctx.get("type", "movie"),
                                season=season,
                                episode=cur_ep,
                                current_time=float(pos),
                                duration=float(dur),
                                poster_url=media_ctx.get("poster"),
                                is_paused=is_paused,
                            )
                    except Exception:
                        pass
            ctypes.windll.kernel32.CloseHandle(h_proc)
    try:
        discord_rpc.discord_rpc.clear()
    except Exception:
        pass
    if _CURRENT_MPV_PROC_PID == pid:
        if is_embedded and undock_fn:
            try:
                undock_fn()
            except Exception:
                pass
        elif not is_embedded and win_action_fn:
            try:
                win_action_fn("show")
            except Exception:
                pass


# --- The external player (mpv, IINA, VLC), step by step (docs/playback.md) ----

def _claim_mpv_launch(url, playlist_items):
    """A second launch within seconds gets the first one's answer; otherwise the
    player to start. (earlier answer or None, player path)."""
    global _LAST_MPV_LAUNCH_TIME, _LAST_MPV_LAUNCH_URL, _LAST_MPV_LAUNCH_RES
    now = time.time()
    with _MPV_LAUNCH_LOCK:
        if ((now - _LAST_MPV_LAUNCH_TIME < 3.5 and _LAST_MPV_LAUNCH_URL == url) or (now - _LAST_MPV_LAUNCH_TIME < 1.2)) and _LAST_MPV_LAUNCH_RES:
            return dict(_LAST_MPV_LAUNCH_RES), None

        _LAST_MPV_LAUNCH_TIME = now
        _LAST_MPV_LAUNCH_URL = url
        if not _LAST_MPV_LAUNCH_RES:
            _LAST_MPV_LAUNCH_RES = {"mpv": "mpv", "pid": 0, "playlist_count": len(playlist_items) if playlist_items else 1}

        mpv_bin = find_mpv()
        if not mpv_bin:
            raise RuntimeError("Aucun lecteur externe compatible (mpv, IINA ou VLC) n'a été trouvé.")
    return None, mpv_bin


def _remember_mpv_launch(url, res):
    global _LAST_MPV_LAUNCH_TIME, _LAST_MPV_LAUNCH_URL, _LAST_MPV_LAUNCH_RES
    _LAST_MPV_LAUNCH_TIME = time.time()
    _LAST_MPV_LAUNCH_URL = url
    _LAST_MPV_LAUNCH_RES = res
    return res


def _playback_target(url, playlist_items):
    """The file, or an M3U playlist when there are several episodes. (target, has_playlist)."""
    if not (playlist_items and len(playlist_items) > 1):
        return url, False
    lines = ["#EXTM3U"]
    for item in playlist_items:
        t = (item.get("title") or "KINO").replace("\n", " ").replace("\r", " ").strip()
        u = (item.get("url") or "").strip()
        if u:
            lines.append(f"#EXTINF:-1,{t}")
            lines.append(u)
    PLAYLIST_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(PLAYLIST_FILE), True


def _write_mpv_input_conf():
    """« s » skips the intro (+85 s) in mpv and IINA; None where it cannot be written."""
    if sys.platform == "win32":
        return None
    try:
        path = "/tmp/kino_mpv_input.conf"
        Path(path).write_text(
            's seek 85 exact ; show-text "Intro passée (+85s)"\n'
            'S seek 85 exact ; show-text "Intro passée (+85s)"\n',
            encoding="utf-8",
        )
        return path
    except Exception:
        return None


def _shared_mpv_options(prefix, plan):
    """Sound, picture and resume: the same for mpv (« -- ») and IINA (« --mpv- »)."""
    args = []
    if plan.audio_mode == "voice_boost":
        args.append(f"{prefix}af=lavfi=[dynaudnorm=f=180:g=13:p=0.92:m=4.5]")
    if plan.hdr_mode != "hdr_native":
        args.extend([f"{prefix}tone-mapping=bt.2446a", f"{prefix}hdr-compute-peak=yes"])
        if plan.hdr_mode == "hdr_boost":
            args.extend([f"{prefix}gamma=9", f"{prefix}brightness=3", f"{prefix}contrast=2"])
        elif plan.is_hdr_media:
            args.extend([f"{prefix}gamma=6", f"{prefix}brightness=2"])
    if plan.start_sec and int(plan.start_sec) > 5:
        args.append(f"{prefix}start={int(plan.start_sec)}")
    return args


def _iina_command(mpv_bin, plan):
    """IINA through its command-line tool; mpv's own options take « --mpv- ». (player, args)."""
    if mpv_bin.endswith("/IINA"):
        cli_cand = Path(mpv_bin).parent / "iina-cli"
        if cli_cand.exists():
            mpv_bin = str(cli_cand)
    if sys.platform == "darwin" and plan.hdr_mode in ("sdr_pref", "hdr_boost"):
        try:
            subprocess.run(
                ["defaults", "write", "com.colliderli.iina", "enableToneMapping", "-bool", "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            )
        except Exception:
            pass
    args = [
        mpv_bin,
        "--no-stdin",
        "--keep-running",
        "--mpv-hwdec=videotoolbox",
        f"--mpv-input-ipc-server={IPC_SOCK_PATH}",
        f"--mpv-alang={plan.alang}",
        f"--mpv-slang={plan.slang}",
    ]
    if plan.input_conf:
        args.append(f"--mpv-input-conf={plan.input_conf}")
    args.extend(_shared_mpv_options("--mpv-", plan))
    if not plan.has_playlist:
        args.append(f"--mpv-force-media-title={plan.safe_title}")
    args.append(plan.target)
    return mpv_bin, args


def _mpv_window_options():
    """Docked in KINO's window on Windows when KINO can, else at KINO's place. (args, docked)."""
    is_docked = False
    dock_title = None
    get_form_hwnd_fn = _get_window_cb("GET_FORM_HWND")
    dock_fn = _get_window_cb("DOCK_MPV_WINDOW")
    if sys.platform == "win32" and get_form_hwnd_fn and dock_fn:
        try:
            form_hwnd = get_form_hwnd_fn()
            if form_hwnd:
                is_docked = True
                dock_title = f"KINO_PLAYER_{int(time.time() * 1000)}"
        except Exception:
            is_docked = False
    args = []
    if is_docked:
        args.append(f"--title={dock_title}")
        bridge_script = Path(__file__).resolve().parent / "kino_bridge.lua"
        if bridge_script.exists():
            args.append(f"--scripts={bridge_script}")
    else:
        get_geom_fn = _get_window_cb("GET_WINDOW_GEOMETRY")
        if get_geom_fn:
            try:
                geo = get_geom_fn()
                if geo:
                    args.append(f"--geometry={geo['width']}x{geo['height']}+{geo['x']}+{geo['y']}")
            except Exception:
                pass
    return args, is_docked


def _mpv_command(mpv_bin, plan):
    """mpv itself: KINO's look (no border, titled KINO), then the shared options. (args, docked)."""
    cfg_dir = Path(mpv_bin).parent / "portable_config"
    args = [mpv_bin]
    if cfg_dir.exists():
        args.append(f"--config-dir={cfg_dir}")
    if sys.platform == "darwin":
        args.append("--hwdec=videotoolbox")
    args.append(f"--input-ipc-server={IPC_SOCK_PATH}")
    if plan.input_conf:
        args.append(f"--input-conf={plan.input_conf}")
    args.extend([
        f"--alang={plan.alang}",
        f"--slang={plan.slang}",
        "--no-border",
        "--border=no",
        "--no-window-dragging",
        "--window-dragging=no",
        "--title=KINO",
        "--force-window=immediate",
    ])
    args.extend(_shared_mpv_options("--", plan))
    window_args, is_docked = _mpv_window_options()
    args.extend(window_args)
    if not plan.has_playlist:
        args.append(f"--force-media-title={plan.safe_title}")
    args.append(plan.target)
    return args, is_docked


def _show_in_discord(media_ctx, safe_title, start_sec):
    if not media_ctx:
        return
    try:
        discord_rpc.discord_rpc.set_activity(
            title=media_ctx.get("name", safe_title),
            media_type=media_ctx.get("type", "movie"),
            season=media_ctx.get("season"),
            episode=media_ctx.get("episode"),
            current_time=float(start_sec),
            poster_url=media_ctx.get("poster"),
        )
    except Exception:
        pass


def _start_on_windows(args, is_docked, media_ctx):
    """Windows: one player at a time, docked or on the user's desktop. Returns the pid."""
    global _CURRENT_MPV_PROC_PID
    CREATE_NO_WINDOW = 0x08000000
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "mpv.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass

    if is_docked:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        pid = proc.pid
        dock_fn = _get_window_cb("DOCK_MPV_WINDOW")
        if dock_fn:
            threading.Thread(target=dock_fn, args=(pid,), daemon=True).start()
    else:
        pid = spawn_on_user_desktop(args)

    _CURRENT_MPV_PROC_PID = pid
    threading.Thread(target=monitor_mpv_lifecycle, args=(pid, media_ctx, is_docked), daemon=True).start()
    if media_ctx:
        background_inject_subtitles(media_ctx)
    return pid


def _start_elsewhere(args, media_ctx):
    """macOS and Linux: the player in its own session, followed until it closes. Returns the pid."""
    global _CURRENT_MPV_PROC_PID
    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _CURRENT_MPV_PROC_PID = proc.pid
    if media_ctx:
        threading.Thread(target=monitor_ipc_playback, args=(proc, media_ctx), daemon=True).start()
        background_inject_subtitles(media_ctx)

    def _wait():
        try:
            proc.wait()
        except Exception:
            pass
        try:
            discord_rpc.discord_rpc.clear()
        except Exception:
            pass
        if _CURRENT_MPV_PROC_PID == proc.pid:
            w_act = _get_window_cb("WINDOW_ACTION_CALLBACK")
            if w_act:
                try:
                    w_act("show")
                except Exception:
                    pass

    threading.Thread(target=_wait, daemon=True).start()
    return proc.pid


def launch_mpv(url: str, title: str = "", playlist_items=None, media_ctx=None, start_sec: int = 0):
    """Starts the external player (mpv, IINA or VLC) with KINO's settings:
    languages, sound, HDR, resume point, playlist, window, Discord."""
    earlier, mpv_bin = _claim_mpv_launch(url, playlist_items)
    if earlier is not None:
        return earlier

    cfg = load_config()
    pref_lang = cfg.get("pref_lang", "vf")
    safe_title = (title or "KINO").replace('"', "'")
    if sys.platform != "win32":
        try:
            if os.path.exists(IPC_SOCK_PATH):
                os.remove(IPC_SOCK_PATH)
        except Exception:
            pass
    target_media, has_playlist = _playback_target(url, playlist_items)
    fname_ctx = (media_ctx.get("filename", "") if isinstance(media_ctx, dict) else "") or ""
    plan = SimpleNamespace(
        alang="eng,ja,jpn,fre,fra,fr" if pref_lang == "vostfr" else "fre,fra,fr,eng",
        slang="fre,fra,fr,eng",
        hdr_mode=cfg.get("hdr_mode", "sdr_pref"),
        audio_mode=cfg.get("audio_mode", "voice_boost"),
        is_hdr_media=bool(re.search(r"\b(hdr|hdr10|hdr10\+|dv|dovi|dolby[\s\.\-]*vision|hlg)\b",
                                    f"{title} {fname_ctx} {url}", re.IGNORECASE)),
        input_conf=_write_mpv_input_conf(),
        start_sec=start_sec,
        has_playlist=has_playlist,
        safe_title=safe_title,
        target=target_media,
    )

    is_docked = False
    if "iina" in mpv_bin.lower():
        mpv_bin, args = _iina_command(mpv_bin, plan)
    elif "vlc" in mpv_bin.lower():
        args = [mpv_bin, target_media]
    else:
        args, is_docked = _mpv_command(mpv_bin, plan)

    win_action_fn = _get_window_cb("WINDOW_ACTION_CALLBACK")
    if not is_docked and win_action_fn:
        try:
            win_action_fn("hide")
        except Exception:
            pass
    _show_in_discord(media_ctx, safe_title, start_sec)

    pid = _start_on_windows(args, is_docked, media_ctx) if sys.platform == "win32" else _start_elsewhere(args, media_ctx)
    return _remember_mpv_launch(url, {"mpv": mpv_bin, "pid": pid, "playlist_count": len(playlist_items) if playlist_items else 1})


