"""Whole manga volumes by torrent, kept in the user's manga folder.

A volume pack found on Nyaa (manga_sources.nyaa_volumes) is opened with the P2P
engine, after the same consent as films. KINO lists its comic archives, then
downloads only the volume the user chose. Once complete, the archive is copied
into the manga folder (<folder>/<series>/<file>.cbz): it is then one of the
user's own files, read offline by the local reader.
"""
import re
import shutil
import threading
import time
from pathlib import Path

import kino_db
import manga
import manga_sources
import p2p_engine

ARCHIVES = (".cbz", ".zip")
DEFAULT_FOLDER = Path.home() / "Documents" / "KINO Manga"
_HASH = re.compile(r"[0-9a-f]{40}")
_JOBS = {}  # (info hash, file index) -> {"series", "name", "size", "path", "error"}
_LOCK = threading.Lock()


def _hash(value):
    value = str(value or "").lower()
    if not _HASH.fullmatch(value):
        raise manga.MangaError("Torrent inconnu.")
    return value


def _handle(info_hash, name=""):
    engine = p2p_engine.engine()
    entry = engine.handle(info_hash)
    handle = entry["handle"] if entry else engine.add(manga_sources.magnet(info_hash, name), info_hash)
    info = engine.metadata(handle)
    if info is None:
        raise manga.MangaError("Ce torrent ne répond pas : aucun pair pour l'instant.")
    return engine, handle, info


def files(info_hash, name=""):
    """The comic archives of a torrent: [{index, name, size}], in volume order."""
    if p2p_engine.consent() is None:
        raise p2p_engine.P2PConsentNeeded("Acceptez la lecture P2P pour télécharger un volume.")
    _engine, _handle_, info = _handle(_hash(info_hash), name)
    storage = info.files()
    found = [{"index": i, "name": storage.file_path(i).replace("\\", "/").rsplit("/", 1)[-1], "size": storage.file_size(i)}
             for i in range(storage.num_files()) if storage.file_path(i).lower().endswith(ARCHIVES)]
    return sorted(found, key=lambda f: manga._natural(f["name"]))


def _folder():
    current = manga.folder()
    if current is not None:
        return current
    DEFAULT_FOLDER.mkdir(parents=True, exist_ok=True)
    kino_db.save_config(manga.FOLDER_KEY, str(DEFAULT_FOLDER))
    return DEFAULT_FOLDER


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]+', " ", str(name or "")).strip()[:120] or "Manga"


def start(info_hash, index, series):
    """Downloads one archive of the torrent, before the others."""
    if p2p_engine.consent() is None:
        raise p2p_engine.P2PConsentNeeded("Acceptez la lecture P2P pour télécharger un volume.")
    info_hash = _hash(info_hash)
    _engine, handle, info = _handle(info_hash)
    storage = info.files()
    index = int(index)
    if not 0 <= index < storage.num_files() or not storage.file_path(index).lower().endswith(ARCHIVES):
        raise manga.MangaError("Volume inconnu dans ce torrent.")
    with _LOCK:
        wanted = [index] + [i for (h, i) in _JOBS if h == info_hash and i != index and not _JOBS[(h, i)].get("path")]
        priorities = [0] * storage.num_files()
        for i in wanted:
            priorities[i] = 7 if i == index else 4
        handle.prioritize_files(priorities)
        _JOBS.setdefault((info_hash, index), {"series": _safe(series), "name": storage.file_path(index).replace("\\", "/").rsplit("/", 1)[-1],
                                              "size": storage.file_size(index), "path": "", "error": ""})
    return status(info_hash, index)


def status(info_hash, index):
    """{progress 0-1, done, error, name}; a finished volume is copied into the manga folder once."""
    info_hash, index = _hash(info_hash), int(index)
    job = _JOBS.get((info_hash, index))
    if job is None:
        raise manga.MangaError("Aucun téléchargement de ce volume.")
    if job["path"] or job["error"]:
        return _answer(job, 1.0 if job["path"] else 0.0)
    engine = p2p_engine._ENGINE
    entry = engine.handle(info_hash) if engine else None
    if entry is None:
        job.update(error="Le téléchargement s'est arrêté.")
        return _answer(job, 0.0)
    handle, info = entry["handle"], entry["handle"].torrent_file()
    done = handle.file_progress()[index]
    progress = done / job["size"] if job["size"] else 0.0
    if done >= job["size"]:
        _finish(job, Path(engine.folder) / info.files().file_path(index))
    return _answer(job, min(progress, 1.0))


def _finish(job, source):
    """Copies the archive once it reads as a whole: libtorrent may still be writing its last pieces."""
    target = _folder() / job["series"] / job["name"]
    target.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        try:
            with manga.zipfile.ZipFile(source) as archive:
                if archive.testzip() is None:
                    shutil.copyfile(source, target)
                    job["path"] = str(target)
                    return
        except (OSError, manga.zipfile.BadZipFile):
            pass
        time.sleep(0.25)


def _answer(job, progress):
    local = ""
    root = manga.folder()
    if job["path"] and root is not None:
        try:
            local = manga._key(Path(job["path"]).parent.relative_to(root))
        except ValueError:
            local = ""
    return {"name": job["name"], "progress": round(progress, 4), "done": bool(job["path"]), "error": job["error"],
            "series": job["series"], "local": local}
