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
import urllib.request
from pathlib import Path

import discord_rpc
from config import (
    DEFAULT_DOWNLOAD_DIR,
    HEADERS,
    IPC_SOCK_PATH,
    PLAYLIST_FILE,
    PORT,
    VIDEO_EXTENSIONS,
    format_size,
    load_config,
    record_history,
)
from debrid_engine import rd_debrid_magnet, score_torrent_for_one_click, search_torrentio
import stream_engine
from meta_engine import download_top_subtitles_for_mpv, get_series_meta

DOWNLOADS = {}
DOWNLOADS_LOCK = threading.Lock()
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


def find_mpv():
    cfg = load_config()
    candidates = [
        cfg.get("mpv_path"),
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
    """Détecte l'exécutable FFmpeg sur la machine."""
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


def resolve_auto_stream_episode(params):
    """Résout à la demande l'URL directe Real-Debrid d'un épisode de série lorsque MPV passe au suivant."""
    cfg = load_config()
    token = cfg.get("rd_token", "").strip()
    if not token:
        raise RuntimeError("Token API Real-Debrid manquant.")

    imdb_id = params.get("imdb_id", "").strip()
    s = int(params.get("season") or 1)
    ep = int(params.get("episode") or 1)
    name = params.get("name") or "Série"
    year = params.get("year") or ""
    poster = params.get("poster") or ""
    pref_hash = (params.get("pref_hash") or "").strip().lower()

    cache_key = (imdb_id, s, ep)
    with AUTO_STREAM_LOCK:
        cached = AUTO_STREAM_CACHE.get(cache_key)
        if cached and (time.time() - cached["ts"] < 1800):
            if imdb_id:
                record_history({
                    "id": imdb_id,
                    "name": name,
                    "type": "series",
                    "year": year,
                    "poster": poster,
                    "season": s,
                    "episode": ep,
                    "filename": cached.get("filename", f"{name} S{s:02d}E{ep:02d}"),
                })
            return cached["download"]

    if imdb_id:
        q_obj = stream_engine.StreamQuery(
            imdb_id=imdb_id,
            media_type="series",
            season=s,
            episode=ep,
            title=name,
            release_year=year,
        )
        torrents = stream_engine.get_available_streams(q_obj)
    else:
        torrents = []
    if not torrents:
        raise RuntimeError(f"Aucun flux trouvé pour {name} S{s:02d}E{ep:02d}.")

    torrents.sort(
        key=lambda c: (
            1 if (pref_hash and (c.get("info_hash") or "").lower() == pref_hash) else 0,
            score_torrent_for_one_click(c),
        ),
        reverse=True,
    )

    last_err = None
    for cand in torrents[:10]:
        try:
            res = rd_debrid_magnet(
                token,
                cand.get("magnet", ""),
                season=s,
                episode=ep,
                resolve_url=cand.get("resolve_url", ""),
            )
            if res.get("ready") and res.get("files"):
                target_files = [f for f in res["files"] if f.get("is_target_ep")]
                if len(res["files"]) > 1 and not target_files:
                    continue
                target_file = target_files[0] if target_files else res["files"][0]
                dl_url = target_file["download"]
                fname = target_file.get("filename", f"{name} S{s:02d}E{ep:02d}")
                with AUTO_STREAM_LOCK:
                    AUTO_STREAM_CACHE[cache_key] = {
                        "download": dl_url,
                        "filename": fname,
                        "ts": time.time(),
                    }
                if imdb_id:
                    record_history({
                        "id": imdb_id,
                        "name": name,
                        "type": "series",
                        "year": year,
                        "poster": poster,
                        "season": s,
                        "episode": ep,
                        "filename": fname,
                    })
                return dl_url
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Impossible de résoudre S{s:02d}E{ep:02d} ({last_err or 'aucun flux RD+ prêt'}).")


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
            })
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


def launch_mpv(url: str, title: str = "", playlist_items=None, media_ctx=None, start_sec: int = 0):
    global _LAST_MPV_LAUNCH_TIME, _LAST_MPV_LAUNCH_URL, _LAST_MPV_LAUNCH_RES
    now = time.time()
    with _MPV_LAUNCH_LOCK:
        if ((now - _LAST_MPV_LAUNCH_TIME < 3.5 and _LAST_MPV_LAUNCH_URL == url) or (now - _LAST_MPV_LAUNCH_TIME < 1.2)) and _LAST_MPV_LAUNCH_RES:
            return dict(_LAST_MPV_LAUNCH_RES)

        _LAST_MPV_LAUNCH_TIME = now
        _LAST_MPV_LAUNCH_URL = url
        if not _LAST_MPV_LAUNCH_RES:
            _LAST_MPV_LAUNCH_RES = {"mpv": "mpv", "pid": 0, "playlist_count": len(playlist_items) if playlist_items else 1}

        mpv_bin = find_mpv()
        if not mpv_bin:
            raise RuntimeError("Aucun lecteur externe compatible (mpv, IINA ou VLC) n'a été trouvé.")

    cfg = load_config()
    pref_lang = cfg.get("pref_lang", "vf")
    alang = "eng,ja,jpn,fre,fra,fr" if pref_lang == "vostfr" else "fre,fra,fr,eng"
    slang = "fre,fra,fr,eng"

    safe_title = (title or "KINO").replace('"', "'")
    is_iina = "iina" in mpv_bin.lower()
    is_vlc = "vlc" in mpv_bin.lower()

    if sys.platform != "win32":
        try:
            if os.path.exists(IPC_SOCK_PATH):
                os.remove(IPC_SOCK_PATH)
        except Exception:
            pass

    target_media = url
    has_playlist = bool(playlist_items and len(playlist_items) > 1)
    if has_playlist:
        lines = ["#EXTM3U"]
        for item in playlist_items:
            t = (item.get("title") or "KINO").replace("\n", " ").replace("\r", " ").strip()
            u = (item.get("url") or "").strip()
            if u:
                lines.append(f"#EXTINF:-1,{t}")
                lines.append(u)
        PLAYLIST_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        target_media = str(PLAYLIST_FILE)

    hdr_mode = cfg.get("hdr_mode", "sdr_pref")
    audio_mode = cfg.get("audio_mode", "voice_boost")
    fname_ctx = (media_ctx.get("filename", "") if isinstance(media_ctx, dict) else "") or ""
    check_hdr_str = f"{title} {fname_ctx} {url}"
    is_hdr_media = bool(re.search(r"\b(hdr|hdr10|hdr10\+|dv|dovi|dolby[\s\.\-]*vision|hlg)\b", check_hdr_str, re.IGNORECASE))

    ext_sub_files = []
    sub_sep = ";" if sys.platform == "win32" else ":"

    mpv_input_conf = None
    if sys.platform != "win32":
        try:
            mpv_input_conf = "/tmp/kino_mpv_input.conf"
            Path(mpv_input_conf).write_text(
                's seek 85 exact ; show-text "Intro passée (+85s)"\n'
                'S seek 85 exact ; show-text "Intro passée (+85s)"\n',
                encoding="utf-8",
            )
        except Exception:
            mpv_input_conf = None

    is_docked = False
    if is_iina:
        if mpv_bin.endswith("/IINA"):
            cli_cand = Path(mpv_bin).parent / "iina-cli"
            if cli_cand.exists():
                mpv_bin = str(cli_cand)
        if sys.platform == "darwin" and hdr_mode in ("sdr_pref", "hdr_boost"):
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
            f"--mpv-alang={alang}",
            f"--mpv-slang={slang}",
        ]
        if mpv_input_conf:
            args.append(f"--mpv-input-conf={mpv_input_conf}")
        if audio_mode == "voice_boost":
            args.append("--mpv-af=lavfi=[dynaudnorm=f=180:g=13:p=0.92:m=4.5]")
        if ext_sub_files:
            args.append(f"--mpv-sub-files={sub_sep.join(ext_sub_files)}")
        if hdr_mode != "hdr_native":
            args.extend([
                "--mpv-tone-mapping=bt.2446a",
                "--mpv-hdr-compute-peak=yes",
            ])
            if hdr_mode == "hdr_boost":
                args.extend([
                    "--mpv-gamma=9",
                    "--mpv-brightness=3",
                    "--mpv-contrast=2",
                ])
            elif is_hdr_media:
                args.extend([
                    "--mpv-gamma=6",
                    "--mpv-brightness=2",
                ])
        if start_sec and int(start_sec) > 5:
            args.append(f"--mpv-start={int(start_sec)}")
        if not has_playlist:
            args.append(f"--mpv-force-media-title={safe_title}")
        args.append(target_media)
    elif is_vlc:
        args = [mpv_bin, target_media]
    else:
        cfg_dir = Path(mpv_bin).parent / "portable_config"
        args = [mpv_bin]
        if cfg_dir.exists():
            args.append(f"--config-dir={cfg_dir}")
        if sys.platform == "darwin":
            args.append("--hwdec=videotoolbox")
        args.append(f"--input-ipc-server={IPC_SOCK_PATH}")
        if mpv_input_conf:
            args.append(f"--input-conf={mpv_input_conf}")
        args.extend([
            f"--alang={alang}",
            f"--slang={slang}",
            "--no-border",
            "--border=no",
            "--no-window-dragging",
            "--window-dragging=no",
            "--title=KINO",
            "--force-window=immediate",
        ])
        if audio_mode == "voice_boost":
            args.append("--af=lavfi=[dynaudnorm=f=180:g=13:p=0.92:m=4.5]")
        if ext_sub_files:
            args.append(f"--sub-files={sub_sep.join(ext_sub_files)}")
        if hdr_mode != "hdr_native":
            args.extend([
                "--tone-mapping=bt.2446a",
                "--hdr-compute-peak=yes",
            ])
            if hdr_mode == "hdr_boost":
                args.extend([
                    "--gamma=9",
                    "--brightness=3",
                    "--contrast=2",
                ])
            elif is_hdr_media:
                args.extend([
                    "--gamma=6",
                    "--brightness=2",
                ])
        if start_sec and int(start_sec) > 5:
            args.append(f"--start={int(start_sec)}")

        is_docked = False
        dock_title = None
        get_form_hwnd_fn = _get_window_cb("GET_FORM_HWND")
        dock_fn = _get_window_cb("DOCK_MPV_WINDOW")
        if not (is_iina or is_vlc) and sys.platform == "win32" and get_form_hwnd_fn and dock_fn:
            try:
                form_hwnd = get_form_hwnd_fn()
                if form_hwnd:
                    is_docked = True
                    dock_title = f"KINO_PLAYER_{int(time.time() * 1000)}"
            except Exception:
                is_docked = False

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

        if not has_playlist:
            args.append(f"--force-media-title={safe_title}")
        args.append(target_media)

    win_action_fn = _get_window_cb("WINDOW_ACTION_CALLBACK")
    if not is_docked and win_action_fn:
        try:
            win_action_fn("hide")
        except Exception:
            pass

    if media_ctx:
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

    global _CURRENT_MPV_PROC_PID
    if sys.platform == "win32":
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
        res = {"mpv": mpv_bin, "pid": pid, "playlist_count": len(playlist_items) if playlist_items else 1}
        _LAST_MPV_LAUNCH_TIME = time.time()
        _LAST_MPV_LAUNCH_URL = url
        _LAST_MPV_LAUNCH_RES = res
        return res
    else:
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
        res = {"mpv": mpv_bin, "pid": proc.pid, "playlist_count": len(playlist_items) if playlist_items else 1}
        _LAST_MPV_LAUNCH_TIME = time.time()
        _LAST_MPV_LAUNCH_URL = url
        _LAST_MPV_LAUNCH_RES = res
        return res


def start_background_download(url, filename, dest_dir):
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename)
    dl_id = f"{int(time.time() * 1000)}_{safe_name}"
    dest_path = Path(dest_dir)
    dest_path.mkdir(parents=True, exist_ok=True)
    filepath = dest_path / safe_name

    with DOWNLOADS_LOCK:
        DOWNLOADS[dl_id] = {
            "id": dl_id,
            "filename": safe_name,
            "path": str(filepath),
            "progress": 0,
            "downloaded": "0 B",
            "total": "?",
            "speed": "0 B/s",
            "status": "downloading",
            "cancel": False,
        }

    def worker():
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                start_t = time.time()
                with open(filepath, "wb") as f:
                    while True:
                        with DOWNLOADS_LOCK:
                            if DOWNLOADS[dl_id].get("cancel"):
                                break
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        elapsed = max(time.time() - start_t, 0.1)
                        spd = downloaded / elapsed
                        pct = round(downloaded * 100 / total, 1) if total else 0
                        with DOWNLOADS_LOCK:
                            DOWNLOADS[dl_id].update({
                                "progress": pct,
                                "downloaded": format_size(downloaded),
                                "total": format_size(total) if total else "?",
                                "speed": f"{format_size(spd)}/s",
                            })
            with DOWNLOADS_LOCK:
                if DOWNLOADS[dl_id].get("cancel"):
                    DOWNLOADS[dl_id]["status"] = "cancelled"
                    try:
                        filepath.unlink(missing_ok=True)
                    except Exception:
                        pass
                else:
                    DOWNLOADS[dl_id]["status"] = "completed"
                    DOWNLOADS[dl_id]["progress"] = 100
        except Exception as e:
            with DOWNLOADS_LOCK:
                DOWNLOADS[dl_id]["status"] = f"error: {e}"

    threading.Thread(target=worker, daemon=True).start()
    return dl_id
