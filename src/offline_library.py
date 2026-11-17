"""Persistent, locally served downloads for the KINO offline library."""

import errno
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import kino_db
from config import HEADERS, VIDEO_EXTENSIONS


ACTIVE = {"queued", "downloading"}
# A season queued for offline waits its turn: one download at a time, so that
# each one checks the free space it really has.
WAITING = "waiting"
STATES = ACTIVE | {WAITING, "paused", "interrupted", "completed", "failed"}
# Episodes of a queued season keep KINO's address, not a Debrid link: the link
# is asked for when their turn comes, so it has not expired.
EPISODE_SOURCE = re.compile(r"^kino-episode:(tt\d{1,12})/(\d{1,4})/(\d{1,5})$")
FIELDS = {
    "status", "source_url", "subtitles_json", "bytes_done", "bytes_total", "etag",
    "last_modified", "accept_ranges", "error_code", "poster_url",
}
CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)", re.I)


class OfflineError(ValueError):
    def __init__(self, message, code="invalid_download"):
        super().__init__(message)
        self.code = code


def _read_one(download_id):
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            row = conn.execute("SELECT * FROM offline_downloads WHERE id=?", (download_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def _read_all():
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM offline_downloads ORDER BY created_at DESC").fetchall()]
        finally:
            conn.close()


def _insert(row):
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            with conn:
                columns = ", ".join(row)
                placeholders = ", ".join("?" for _ in row)
                conn.execute(f"INSERT INTO offline_downloads ({columns}) VALUES ({placeholders})", tuple(row.values()))
        finally:
            conn.close()


def _update(download_id, **changes):
    if not changes or not set(changes).issubset(FIELDS):
        raise ValueError("Invalid offline download update")
    changes["updated_at"] = time.time()
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            with conn:
                clauses = ", ".join(f"{key}=?" for key in changes)
                conn.execute(f"UPDATE offline_downloads SET {clauses} WHERE id=?", (*changes.values(), download_id))
        finally:
            conn.close()


def _delete(download_id):
    with kino_db._DB_LOCK:
        conn = kino_db.get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM offline_downloads WHERE id=?", (download_id,))
        finally:
            conn.close()


def _safe_name(name):
    filename = Path(str(name or "").replace("\\", "/")).name
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")[:180]
    if not filename or Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
        raise OfflineError("Choisissez un fichier vidéo pris en charge.")
    return filename


def _managed_path(row, key):
    path = Path(row[key])
    root = Path(row["root_path"])
    prefix = row["id"] + "_"
    if not path.name.startswith(prefix) or path.is_symlink() or root.is_symlink():
        raise OfflineError("Chemin du téléchargement invalide.")
    if path.parent.resolve() != root.resolve():
        raise OfflineError("Chemin du téléchargement invalide.")
    return path


def _subtitle_snapshot(value):
    if not isinstance(value, dict) or value.get("lang") == "off":
        return "{}"
    cues = value.get("cues")
    if not isinstance(cues, list) or len(cues) > 10000:
        return "{}"
    clean = []
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        try:
            start = float(cue.get("start"))
            end = float(cue.get("end"))
        except (ValueError, TypeError):
            continue
        if 0 <= start < end and end < 1000000:
            clean.append({"start": start, "end": end, "text": str(cue.get("text") or "")[:1000]})
    if not clean:
        return "{}"
    result = json.dumps({
        "label": str(value.get("label") or "CC")[:80],
        "lang": str(value.get("lang") or "")[:12],
        "kind": str(value.get("kind") or "opensubtitles")[:30],
        "variant": str(value.get("variant") or "standard")[:30],
        "cues": clean,
    }, ensure_ascii=False)
    return result if len(result.encode("utf-8")) <= 2_000_000 else "{}"


def _media_snapshot(media):
    media = media if isinstance(media, dict) else {}
    result = {}
    for key in ("id", "name", "type", "year", "poster", "runtime", "season", "episode"):
        if key in media:
            result[key] = str(media[key])[:500] if key not in ("season", "episode") else media[key]
    for key in ("season", "episode"):
        try:
            result[key] = max(1, min(10000, int(result[key]))) if result.get(key) else None
        except (ValueError, TypeError):
            result[key] = None
    return result


def _fingerprint(media, filename):
    identity = f"{media.get('id', '')}|{media.get('season') or ''}|{media.get('episode') or ''}|{filename.casefold()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


# Leave room for macOS and other apps: a download never fills the disk to the last byte.
DISK_RESERVE_BYTES = 1024 ** 3


def episode_source(imdb_id, season, episode):
    return f"kino-episode:{imdb_id}/{int(season)}/{int(episode)}"


class OfflineLibrary:
    def __init__(self):
        self._lock = threading.RLock()
        self._active = {}
        # (imdb_id, season, episode, media) -> playable link, set by the app.
        self.resolve_episode = None

    def reconcile(self):
        # A prior process may have ended while writing. Its part remains resumable.
        with kino_db._DB_LOCK:
            conn = kino_db.get_connection()
            try:
                with conn:
                    conn.execute("UPDATE offline_downloads SET status='interrupted', error_code='app_closed', "
                                 "updated_at=? WHERE status IN ('queued', 'downloading')", (time.time(),))
            finally:
                conn.close()
        self.start_next()

    def queue_episode(self, imdb_id, season, episode, dest_dir, media=None):
        """Adds one episode of a season to the offline queue; (id, already there)."""
        imdb_id = str(imdb_id or "")
        if not re.fullmatch(r"tt\d{1,12}", imdb_id):
            raise OfflineError("Série inconnue.")
        try:
            season, episode = int(season), int(episode)
        except (TypeError, ValueError) as error:
            raise OfflineError("Épisode inconnu.") from error
        if not (1 <= season <= 9999 and 1 <= episode <= 99999):
            raise OfflineError("Épisode inconnu.")
        clean_media = _media_snapshot({**(media if isinstance(media, dict) else {}), "id": imdb_id, "type": "series",
                                       "season": season, "episode": episode})
        safe_name = _safe_name(f"{clean_media.get('name') or 'Série'} S{season:02d}E{episode:02d}.mkv")
        root = Path(dest_dir).expanduser().resolve() / "KINO Offline"
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise OfflineError("Le dossier de téléchargement est inaccessible.", "folder_unavailable") from error
        with self._lock:
            # The same episode already kept, on its way or in the queue, whatever its file name.
            for row in _read_all():
                other = json.loads(row["media_json"] or "{}")
                if (other.get("id"), other.get("season"), other.get("episode")) == (imdb_id, season, episode) \
                        and row["status"] != "failed":
                    return row["id"], True
            download_id = uuid.uuid4().hex
            now = time.time()
            poster_url = str(clean_media.get("poster") or "")
            _insert({
                "id": download_id, "fingerprint": _fingerprint(clean_media, safe_name),
                "status": WAITING, "source_url": episode_source(imdb_id, season, episode), "filename": safe_name,
                "title": str(clean_media.get("name") or safe_name)[:240],
                "media_json": json.dumps(clean_media, ensure_ascii=False), "subtitles_json": "",
                "poster_url": poster_url if poster_url.startswith(("http://", "https://")) else "",
                "root_path": str(root), "file_path": str(root / f"{download_id}_{safe_name}"),
                "part_path": str(root / f"{download_id}_{safe_name}.part"),
                "poster_path": str(root / f"{download_id}_poster"),
                "bytes_done": 0, "bytes_total": 0, "etag": "", "last_modified": "",
                "accept_ranges": 0, "error_code": "", "created_at": now, "updated_at": now,
            })
        self.start_next()
        return download_id, False

    def start_next(self):
        """Starts the oldest waiting episode when nothing else downloads."""
        with self._lock:
            if self._active:
                return None
            waiting = [row for row in _read_all() if row["status"] == WAITING]
            if not waiting:
                return None
            row = min(waiting, key=lambda item: (item["created_at"], item["id"]))
            _update(row["id"], status="queued")
            self._launch(row["id"])
            return row["id"]

    def _source_url(self, row):
        """The link to download: a queued episode asks for its link now."""
        match = EPISODE_SOURCE.fullmatch(row["source_url"] or "")
        if not match:
            return row["source_url"]
        if self.resolve_episode is None:
            raise OfflineError("Aucune source disponible pour cet épisode.", "source_unavailable")
        try:
            url = self.resolve_episode(match.group(1), int(match.group(2)), int(match.group(3)),
                                       json.loads(row["media_json"] or "{}"))
        except Exception as error:
            raise OfflineError("Aucune source disponible pour cet épisode.", "source_unavailable") from error
        parsed = urllib.parse.urlparse(str(url or ""))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise OfflineError("Aucune source disponible pour cet épisode.", "source_unavailable")
        return url

    def get(self, download_id):
        return _read_one(str(download_id or ""))

    def _available(self, row):
        if row["status"] != "completed":
            return False
        try:
            path = _managed_path(row, "file_path")
            size = path.stat().st_size
            if not path.is_file() or size <= 0 or size != row["bytes_done"] or (
                    row["bytes_total"] and size != row["bytes_total"]):
                raise OSError("missing or changed")
            return True
        except (OSError, OfflineError):
            _update(row["id"], status="interrupted", error_code="missing_file")
            row["status"] = "interrupted"
            row["error_code"] = "missing_file"
            return False

    def public(self, row):
        available = self._available(row)
        total = int(row["bytes_total"] or 0)
        done = int(row["bytes_done"] or 0)
        media = json.loads(row["media_json"] or "{}")
        if available:
            media["offline_id"] = row["id"]
            media["poster"] = f"/api/offline/poster/{row['id']}" if Path(row["poster_path"]).is_file() else ""
        return {
            "id": row["id"], "filename": row["filename"], "title": row["title"],
            "status": row["status"], "error_code": row["error_code"],
            "bytes_done": done, "bytes_total": total,
            "progress": min(100, round(done * 100 / total, 1)) if total else 0,
            "available": available, "media": media,
            "poster": media.get("poster", "") if available else "",
            "has_subtitles": bool(json.loads(row["subtitles_json"] or "{}")),
            "url": f"/api/offline/media/{row['id']}" if available else "",
            "created_at": row["created_at"],
        }

    def list(self):
        rows = _read_all()
        return {row["id"]: self.public(row) for row in rows}

    def usage_bytes(self):
        used = 0
        for row in _read_all():
            for key in ("file_path", "part_path", "poster_path"):
                try:
                    path = _managed_path(row, key)
                    if path.is_file():
                        used += path.stat().st_size
                except (OSError, OfflineError):
                    pass
        return used

    def free_bytes(self, dest_dir):
        """Free space on the volume that receives downloads (the folder may not exist yet)."""
        path = Path(dest_dir or Path.home()).expanduser()
        while not path.exists() and path != path.parent:
            path = path.parent
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None

    def create(self, url, filename, dest_dir, media=None, subtitles=None):
        parsed = urllib.parse.urlparse(str(url or ""))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise OfflineError("Lien vidéo invalide.")
        safe_name = _safe_name(filename)
        clean_media = _media_snapshot(media)
        root = Path(dest_dir).expanduser().resolve() / "KINO Offline"
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise OfflineError("Le dossier de téléchargement est inaccessible.", "folder_unavailable") from error
        fingerprint = _fingerprint(clean_media, safe_name)
        with self._lock:
            for row in _read_all():
                if row["fingerprint"] != fingerprint:
                    continue
                if row["status"] == "completed" and self._available(row):
                    return row["id"], True
                if row["status"] in ACTIVE:
                    return row["id"], True
                # Choosing a fresh Playable Stream in the source list is an
                # explicit restart when the old URL has expired.
                if row["source_url"] != url:
                    self.resume(row["id"], restart=True, source_url=url)
                    return row["id"], False
                return row["id"], True
            download_id = uuid.uuid4().hex
            now = time.time()
            media_json = json.dumps(clean_media, ensure_ascii=False)
            poster_url = str(clean_media.get("poster") or "")
            row = {
                "id": download_id, "fingerprint": fingerprint,
                "status": "queued", "source_url": url, "filename": safe_name,
                "title": str(clean_media.get("name") or safe_name)[:240],
                "media_json": media_json, "subtitles_json": _subtitle_snapshot(subtitles),
                "poster_url": poster_url if poster_url.startswith(("http://", "https://")) else "",
                "root_path": str(root), "file_path": str(root / f"{download_id}_{safe_name}"),
                "part_path": str(root / f"{download_id}_{safe_name}.part"),
                "poster_path": str(root / f"{download_id}_poster"),
                "bytes_done": 0, "bytes_total": 0, "etag": "", "last_modified": "",
                "accept_ranges": 0, "error_code": "", "created_at": now, "updated_at": now,
            }
            _insert(row)
            self._launch(download_id)
            return download_id, False

    def _launch(self, download_id):
        stop = threading.Event()
        self._active[download_id] = stop
        thread = threading.Thread(target=self._worker, args=(download_id, stop), daemon=True)
        thread.start()

    def pause(self, download_id):
        with self._lock:
            row = self.get(download_id)
            if not row:
                raise OfflineError("Téléchargement introuvable.", "not_found")
            if row["status"] in ACTIVE and download_id in self._active:
                self._active[download_id].set()
                return
            if row["status"] in ("queued", WAITING):
                _update(download_id, status="paused")

    def resume(self, download_id, restart=False, source_url=None):
        with self._lock:
            row = self.get(download_id)
            if not row:
                raise OfflineError("Téléchargement introuvable.", "not_found")
            if row["status"] in ACTIVE or download_id in self._active:
                raise OfflineError("Téléchargement déjà en cours.")
            if row["status"] == "completed" and self._available(row):
                raise OfflineError("Fichier déjà disponible hors ligne.")
            part = _managed_path(row, "part_path")
            if restart:
                part.unlink(missing_ok=True)
                changes = dict(bytes_done=0, bytes_total=0, etag="", last_modified="",
                               accept_ranges=0, error_code="", status="queued")
                if source_url:
                    parsed = urllib.parse.urlparse(source_url)
                    if parsed.scheme not in ("http", "https") or not parsed.hostname:
                        raise OfflineError("Lien vidéo invalide.")
                    changes["source_url"] = source_url
                _update(download_id, **changes)
            else:
                size = part.stat().st_size if part.is_file() else 0
                if row["bytes_done"] and not size:
                    raise OfflineError("Fichier partiel introuvable. Recommencez le téléchargement.", "restart_required")
                if part.is_file() and not size:
                    part.unlink()
                if size and (not row["accept_ranges"] or not row["bytes_total"] or not (
                        row["etag"] and not row["etag"].startswith("W/") or row["last_modified"])):
                    raise OfflineError("Reprise non vérifiable. Recommencez le téléchargement.", "restart_required")
                _update(download_id, bytes_done=size, status="queued", error_code="")
            self._launch(download_id)

    def remove(self, download_id):
        with self._lock:
            row = self.get(download_id)
            if not row:
                raise OfflineError("Téléchargement introuvable.", "not_found")
            if row["status"] in ACTIVE or download_id in self._active:
                raise OfflineError("Mettez le téléchargement en pause avant de le retirer.")
            for key in ("file_path", "part_path", "poster_path"):
                _managed_path(row, key).unlink(missing_ok=True)
            _delete(download_id)

    def media_path(self, download_id):
        row = self.get(download_id)
        if not row or not self._available(row):
            return None
        return _managed_path(row, "file_path")

    def poster_path(self, download_id):
        row = self.get(download_id)
        if not row or not self._available(row):
            return None
        path = _managed_path(row, "poster_path")
        return path if path.is_file() else None

    def subtitles(self, download_id):
        row = self.get(download_id)
        if not row or not self._available(row):
            return None
        return json.loads(row["subtitles_json"] or "{}")

    def _fetch_poster(self, row):
        url = row["poster_url"]
        if not url or Path(row["poster_path"]).is_file():
            return
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read(4_000_001)
            if len(data) > 4_000_000 or not (data.startswith(b"\xff\xd8") or
                    data.startswith(b"\x89PNG") or data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
                return
            path = _managed_path(row, "poster_path")
            temp = path.with_name(path.name + ".part")
            temp.write_bytes(data)
            os.replace(temp, path)
        except (OSError, urllib.error.URLError, OfflineError):
            pass

    def _worker(self, download_id, stop):
        try:
            row = self.get(download_id)
            part = _managed_path(row, "part_path")
            final = _managed_path(row, "file_path")
            offset = part.stat().st_size if part.is_file() else 0
            headers = dict(HEADERS)
            if offset:
                validator = row["etag"] if row["etag"] and not row["etag"].startswith("W/") else row["last_modified"]
                headers.update({"Range": f"bytes={offset}-", "If-Range": validator})
            if stop.is_set():
                _update(download_id, status="paused", bytes_done=offset)
                return
            request = urllib.request.Request(self._source_url(row), headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
                if offset and status != 206:
                    raise OfflineError("Le serveur ne permet pas une reprise sûre.", "resume_unavailable")
                if not offset and status != 200:
                    raise OfflineError("Réponse vidéo inattendue.", "invalid_response")
                if "text/html" in response.headers.get("Content-Type", "").lower():
                    raise OfflineError("Lien expiré ou page de connexion.", "url_expired")
                etag = response.headers.get("ETag", "")
                last_modified = response.headers.get("Last-Modified", "")
                length = int(response.headers.get("Content-Length", "0") or 0)
                if offset:
                    match = CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", "").strip())
                    if not match or int(match.group(1)) != offset or match.group(3) == "*":
                        raise OfflineError("Plage de reprise invalide.", "resume_unavailable")
                    total = int(match.group(3))
                    if total != row["bytes_total"] or (row["etag"] and etag and etag != row["etag"]) or (
                            row["last_modified"] and last_modified and last_modified != row["last_modified"]):
                        raise OfflineError("Le fichier distant a changé. Recommencez le téléchargement.", "restart_required")
                    if length and length != total - offset:
                        raise OfflineError("Taille de reprise invalide.", "resume_unavailable")
                else:
                    total = length
                remaining = total - offset if total else 0
                if remaining and shutil.disk_usage(part.parent).free - remaining < DISK_RESERVE_BYTES:
                    raise OfflineError("Espace disque insuffisant pour ce fichier.", "disk_full")
                _update(download_id, status="downloading", bytes_total=total,
                        etag=etag or row["etag"], last_modified=last_modified or row["last_modified"],
                        accept_ranges=1 if response.headers.get("Accept-Ranges", "").lower() == "bytes" else row["accept_ranges"])
                flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | (os.O_APPEND if offset else os.O_CREAT | os.O_EXCL)
                fd = os.open(part, flags, 0o600)
                done = offset
                last_save = time.monotonic()
                with os.fdopen(fd, "ab" if offset else "wb") as file:
                    while not stop.is_set():
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        file.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if now - last_save > 0.5:
                            _update(download_id, bytes_done=done)
                            last_save = now
                    file.flush()
                    os.fsync(file.fileno())
                if stop.is_set():
                    _update(download_id, status="paused", bytes_done=done)
                    return
                if not done or total and done != total:
                    raise OfflineError("Téléchargement interrompu avant la fin.", "network_interrupted")
                os.replace(part, final)
                self._fetch_poster(row)
                _update(download_id, status="completed", bytes_done=done,
                        bytes_total=total or done, error_code="")
        except urllib.error.HTTPError as error:
            code = "url_expired" if error.code in (401, 403, 404, 410) else "network_interrupted"
            error.close()
            _update(download_id, status="failed", error_code=code)
        except urllib.error.URLError:
            _update(download_id, status="interrupted", error_code="network_interrupted")
        except OSError as error:
            code = "disk_full" if error.errno == errno.ENOSPC else "file_error"
            _update(download_id, status="failed", error_code=code)
        except OfflineError as error:
            _update(download_id, status="failed", error_code=error.code)
        except Exception:
            _update(download_id, status="failed", error_code="unknown_error")
        finally:
            with self._lock:
                self._active.pop(download_id, None)
            self.start_next()


library = OfflineLibrary()
