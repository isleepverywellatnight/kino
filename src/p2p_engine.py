"""Playback without a debrid service: the torrent is read peer to peer (libtorrent).

KINO downloads the pieces of the chosen file in order and serves the file to
its own player at http://127.0.0.1:<port>/p2p/<info hash>/<file index>, as a
debrid service would serve a link. While it plays, the computer also sends
pieces to other peers: that is how BitTorrent works, and why the user agrees
to it once, after being told what it means (consent()).

prepare() has the signature of a debrid function (debrid_engine.rd_debrid_magnet)
so stream_engine.try_sources can use it unchanged.
"""
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import kino_db

CONSENT_KEY = "p2p_consent_v1"
SETTINGS_KEY = "p2p_settings_v1"
# Bytes per second sent to other peers: « minimum » keeps the exchange going
# (peers favour those who send), « normal » leaves it to BitTorrent.
UPLOAD_LIMITS = {"minimum": 64 * 1024, "256k": 256 * 1024, "1m": 1024 * 1024, "normal": 0}
# Bytes per second received, to leave room for the rest of the house.
DOWNLOAD_LIMITS = {"none": 0, "10m": 10 * 1024 * 1024, "5m": 5 * 1024 * 1024, "2m": 2 * 1024 * 1024}
# encrypted: only peers that encrypt the whole exchange (fewer of them, but the
# provider cannot tell BitTorrent apart as easily). port 0: chosen by the system.
DEFAULTS = {"upload": "minimum", "download": "none", "encrypted": False, "port": 0}
METADATA_TIMEOUT = 30
PIECE_TIMEOUT = 90
READ_AHEAD_BYTES = 24 * 1024 * 1024
KEEP_TORRENTS = 2
KEEP_PIECES = 8
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".webm")
_HASH = re.compile(r"[0-9a-f]{40}")


class P2PConsentNeeded(RuntimeError):
    """The user has not agreed to peer-to-peer playback yet."""
    code = "p2p_consent"


class P2PUnavailable(RuntimeError):
    """libtorrent is missing from this build."""


class P2PSettingsError(ValueError):
    """A setting KINO cannot use; the message is for the user."""


def available():
    try:
        import libtorrent  # noqa: F401
        return True
    except ImportError:
        return False


def consent():
    stored = kino_db.get_config(CONSENT_KEY, None)
    return stored if isinstance(stored, dict) and stored.get("accepted_at") else None


def set_consent(accepted, upload=None):
    if not accepted:
        kino_db.save_config(CONSENT_KEY, {})
        shutdown()
        return None
    # Saved first: a choice made before 1.8 still sits with the old consent.
    save_settings({} if upload is None else {"upload": upload if upload in UPLOAD_LIMITS else "minimum"})
    kino_db.save_config(CONSENT_KEY, {"accepted_at": int(time.time())})
    return consent()


def settings():
    """The user's choices, each one checked, with the defaults for the others."""
    stored = kino_db.get_config(SETTINGS_KEY, None)
    stored = stored if isinstance(stored, dict) else {}
    # Before 1.8 the upload choice was kept with the consent.
    old = kino_db.get_config(CONSENT_KEY, None)
    if "upload" not in stored and isinstance(old, dict) and old.get("upload"):
        stored = {**stored, "upload": old["upload"]}
    value = dict(DEFAULTS)
    if stored.get("upload") in UPLOAD_LIMITS:
        value["upload"] = stored["upload"]
    if stored.get("download") in DOWNLOAD_LIMITS:
        value["download"] = stored["download"]
    value["encrypted"] = stored.get("encrypted") is True
    if isinstance(stored.get("port"), int) and (stored["port"] == 0 or 1024 <= stored["port"] <= 65535):
        value["port"] = stored["port"]
    return value


def save_settings(changes):
    """Saves the choices the user changed and applies them to a running session."""
    value = settings()
    if "upload" in changes:
        if changes["upload"] not in UPLOAD_LIMITS:
            raise P2PSettingsError("Limite d'envoi inconnue.")
        value["upload"] = changes["upload"]
    if "download" in changes:
        if changes["download"] not in DOWNLOAD_LIMITS:
            raise P2PSettingsError("Limite de téléchargement inconnue.")
        value["download"] = changes["download"]
    if "encrypted" in changes:
        value["encrypted"] = changes["encrypted"] is True
    if "port" in changes:
        port = changes["port"]
        port = 0 if port in (None, "") else port
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = -1
        if port != 0 and not 1024 <= port <= 65535:
            raise P2PSettingsError("Choisissez un port entre 1024 et 65535, ou laissez vide.")
        value["port"] = port
    kino_db.save_config(SETTINGS_KEY, value)
    if _ENGINE is not None:
        _ENGINE.apply(value)
    return value


def session_settings(choices):
    """libtorrent settings for the user's choices."""
    import libtorrent as lt
    port = int(choices.get("port") or 0)
    encrypted = choices.get("encrypted") is True
    policy = lt.enc_policy.pe_forced if encrypted else lt.enc_policy.pe_enabled
    return {
        "upload_rate_limit": UPLOAD_LIMITS.get(choices.get("upload"), UPLOAD_LIMITS["minimum"]),
        "download_rate_limit": DOWNLOAD_LIMITS.get(choices.get("download"), 0),
        "out_enc_policy": int(policy),
        "in_enc_policy": int(policy),
        "allowed_enc_level": int(lt.enc_level.pe_rc4 if encrypted else lt.enc_level.pe_both),
        "listen_interfaces": f"0.0.0.0:{port},[::]:{port}",
    }


def cache_size():
    """Bytes the torrents being read take on this computer."""
    total = 0
    for path in cache_dir().rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def clear_cache():
    """Stops what is being read and removes its files."""
    shutdown()
    shutil.rmtree(cache_dir(), ignore_errors=True)


def state():
    """What Paramètres shows: whether P2P can be used, is accepted, and how."""
    return {"available": available(), "accepted": consent() is not None, **settings(), "cache_bytes": cache_size()}


def cache_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "KINO" / "P2P"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "KINO" / "P2P"
    return Path.home() / ".cache" / "kino" / "p2p"


def info_hash_of(magnet="", resolve_url=""):
    """The 40-hex info hash of a magnet link or of a stream address that holds one."""
    for text in (magnet, resolve_url):
        match = re.search(r"btih:([0-9a-fA-F]{40})", str(text or "")) or re.search(r"/([0-9a-fA-F]{40})(?:/|$)", str(text or ""))
        if match:
            return match.group(1).lower()
    return ""


class Engine:
    """One libtorrent session for KINO, its torrents, and the files it serves."""

    def __init__(self, choices=None, folder=None):
        import libtorrent as lt
        self.lt = lt
        self.folder = Path(folder or cache_dir())
        # What a previous session left behind is not needed: playback restarts from the network.
        shutil.rmtree(self.folder, ignore_errors=True)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.session = lt.session({
            **session_settings(choices or DEFAULTS),
            "enable_lsd": False,
            # The limit counts the data sent to peers, not the TCP acknowledgements
            # of what is received: counted, they held downloads to 150 KB/s.
            "rate_limit_ip_overhead": False,
            "alert_mask": 0,
        })
        self.handles = {}
        self.lock = threading.Lock()
        self.pieces = {}  # (info hash, piece) -> bytes, the latest ones read
        self.pieces_lock = threading.Lock()

    def apply(self, choices):
        current = self.session.get_settings()["listen_interfaces"]
        wanted = session_settings(choices)
        if current == wanted["listen_interfaces"]:
            del wanted["listen_interfaces"]  # reopening the same port drops the peers for nothing
        self.session.apply_settings(wanted)

    def add(self, magnet, info_hash):
        with self.lock:
            entry = self.handles.get(info_hash)
            if entry is None:
                params = self.lt.parse_magnet_uri(magnet)
                params.save_path = str(self.folder)
                params.flags |= self.lt.torrent_flags.sequential_download
                entry = {"handle": self.session.add_torrent(params), "used": time.time(), "file": None}
                self.handles[info_hash] = entry
                self._forget_old()
            entry["used"] = time.time()
            return entry["handle"]

    def _forget_old(self):
        """Only the latest torrents stay, with their files: the others are removed from disk."""
        for info_hash, entry in sorted(self.handles.items(), key=lambda item: item[1]["used"])[:-KEEP_TORRENTS]:
            self.session.remove_torrent(entry["handle"], self.lt.session.delete_files)
            del self.handles[info_hash]

    def metadata(self, handle, timeout=METADATA_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = handle.torrent_file()
            if info is not None:
                # Until the player asks for one, the videos stay wanted at a low priority:
                # a torrent that wants nothing counts as finished and drops its peers,
                # which took a minute to come back.
                files = info.files()
                handle.prioritize_files([1 if files.file_path(i).lower().endswith(VIDEO_EXTENSIONS) else 0
                                         for i in range(files.num_files())])
                return info
            time.sleep(0.25)
        return None

    def read_piece(self, info_hash, handle, piece, timeout=PIECE_TIMEOUT):
        """The bytes of a piece, asked of libtorrent rather than read from the file:
        libtorrent 2 says it has a piece once verified, which can be before the piece
        is written to disk; read too soon, the file still held zeros there."""
        key = (info_hash, piece)
        with self.pieces_lock:
            if key in self.pieces:
                return self.pieces[key]
        handle.read_piece(piece)
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.pieces_lock:
                for alert in self.session.pop_alerts():
                    if isinstance(alert, self.lt.read_piece_alert) and not alert.error.value():
                        self.pieces[(str(alert.handle.info_hashes().v1), alert.piece)] = bytes(alert.buffer)
                        while len(self.pieces) > KEEP_PIECES:
                            del self.pieces[next(iter(self.pieces))]
                if key in self.pieces:
                    return self.pieces[key]
            time.sleep(0.02)
        return None

    def handle(self, info_hash):
        with self.lock:
            entry = self.handles.get(info_hash)
            if entry:
                entry["used"] = time.time()
            return entry

    def stop(self):
        for entry in list(self.handles.values()):
            try:
                self.session.remove_torrent(entry["handle"], self.lt.session.delete_files)
            except Exception:
                pass
        self.handles.clear()


_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def engine():
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            if not available():
                raise P2PUnavailable("La lecture P2P n'est pas disponible dans cette version de KINO.")
            _ENGINE = Engine(settings())
        return _ENGINE


def shutdown():
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            _ENGINE.stop()
            _ENGINE = None


def _filename_of(url):
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]) or "video"


def prepare(token, magnet, season=None, episode=None, resolve_url="", *, base_url):
    """Like a debrid service: {"ready": bool, "files": [{download, filename, filesize_bytes}]}.

    A direct web link (an add-on that serves files) plays as it is. A torrent
    is added, its file list read, and each video gets a local address."""
    if resolve_url and str(resolve_url).startswith(("http://", "https://")) and not magnet.startswith("magnet:"):
        if not info_hash_of(resolve_url):
            return {"ready": True, "files": [{"download": resolve_url, "filename": _filename_of(resolve_url)}]}
    info_hash = info_hash_of(magnet, resolve_url)
    if not info_hash:
        return {"ready": False, "files": []}
    if consent() is None:
        raise P2PConsentNeeded("Acceptez la lecture P2P dans Paramètres avant de lancer un torrent sans service de débridage.")
    if not str(magnet).startswith("magnet:"):
        magnet = f"magnet:?xt=urn:btih:{info_hash}"
    current = engine()
    info = current.metadata(current.add(magnet, info_hash))
    if info is None:
        return {"ready": False, "files": []}
    files = info.files()
    videos = []
    for index in range(files.num_files()):
        path = files.file_path(index)
        if path.lower().endswith(VIDEO_EXTENSIONS):
            videos.append({
                "download": f"{base_url}/p2p/{info_hash}/{index}",
                "filename": path.replace("\\", "/").rsplit("/", 1)[-1],
                "filesize_bytes": files.file_size(index),
                "p2p": True,
            })
    return {"ready": bool(videos), "files": videos, "p2p": True}


def _content_type(path):
    suffix = Path(path).suffix.lower()
    return {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
            ".ts": "video/mp2t", ".avi": "video/x-msvideo"}.get(suffix, "video/x-matroska")


def _byte_range(header, size):
    """(start, end) inclusive from a Range header, or None for the whole file."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", str(header or "").strip())
    if not match or (not match.group(1) and not match.group(2)):
        return None
    if not match.group(1):
        length = min(int(match.group(2)), size)
        return size - length, size - 1
    start = int(match.group(1))
    end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
    return (start, end) if start <= end < size else (size, size)


def serve(handler, info_hash, index):
    """Sends one file of a torrent to the player, piece by piece, as it arrives."""
    if not _HASH.fullmatch(info_hash or "") or _ENGINE is None:
        handler.send_error(404)
        return
    entry = _ENGINE.handle(info_hash)
    info = entry["handle"].torrent_file() if entry else None
    if info is None or not 0 <= index < info.files().num_files():
        handler.send_error(404)
        return
    handle, files = entry["handle"], info.files()
    size = files.file_size(index)
    if entry["file"] != index:
        priorities = [0] * files.num_files()
        priorities[index] = 7
        handle.prioritize_files(priorities)
        entry["file"] = index
    byte_range = _byte_range(handler.headers.get("Range"), size)
    if byte_range and byte_range[0] >= size:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.end_headers()
        return
    start, end = byte_range or (0, size - 1)
    handler.send_response(206 if byte_range else 200)
    handler.send_header("Content-Type", _content_type(files.file_path(index)))
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(end - start + 1))
    if byte_range:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    piece_length = info.piece_length()
    file_start = files.file_offset(index)
    position = start
    try:
        while position <= end:
            absolute = file_start + position
            piece = absolute // piece_length
            ahead = max(1, READ_AHEAD_BYTES // piece_length)
            for offset, upcoming in enumerate(range(piece, min(piece + ahead, info.num_pieces()))):
                handle.set_piece_deadline(upcoming, 300 + 150 * offset)
            waited = time.time()
            while not handle.have_piece(piece):
                if time.time() - waited > PIECE_TIMEOUT:
                    return
                time.sleep(0.1)
            entry["used"] = time.time()
            whole = _ENGINE.read_piece(info_hash, handle, piece)
            if not whole:
                return
            inside = absolute - piece * piece_length
            data = whole[inside:inside + min(piece_length - inside, end - position + 1)]
            if not data:
                return
            handler.wfile.write(data)
            position += len(data)
    except OSError:
        # The player went away, or the torrent was removed while it played.
        return
