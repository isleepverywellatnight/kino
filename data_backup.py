"""A copy a day of what the user has made in KINO, the 7 newest kept,
and « Restaurer ».

What a copy holds: Ma Liste, the history (seen, in progress, ratings), « Ce soir »
and « Pas pour moi » choices, KINO's own settings stored in the database, and the
settings file. What it leaves out: the caches, which KINO finds again by itself
(about two thirds of the database), and the keys (Debrid, TMDB, Trakt…), so that a
copy never holds a secret. Each copy is a folder of its own:

    Backups/2026-11-12/kino_data.db    the database, caches emptied, checked
    Backups/2026-11-12/config.json     the settings, keys removed
    Backups/2026-11-12/backup.json     when, which version, how many titles

« Restaurer » first copies the present state (« Avant restauration »), then puts
back the user's tables and settings; the caches, the keys and what belongs to this
Mac (window, download folder, TV) stay as they are.
"""
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

KEEP_DAILY = 7
KEEP_BEFORE_RESTORE = 2
FIRST_DELAY = 90.0
CHECK_EVERY = 3600.0

# The user's own tables, put back by « Restaurer ».
USER_TABLES = ("watchlist", "watch_history", "app_config", "tonight_feedback", "taste_feedback")
# Emptied in the copy: KINO fills them again from the network.
CACHE_TABLES = ("persistent_cache", "taste_pool", "playback_source_feedback")
# Settings that stay those of this Mac at restore time, and are never copied when secret.
THIS_MAC = ("window_bounds", "download_dir", "tv_device", "debrid_provider")
_SECRET = re.compile(r"token|key|secret|password|auth", re.IGNORECASE)

DAILY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BEFORE_RESTORE = re.compile(r"^avant-restauration-\d{4}-\d{2}-\d{2}-\d{6}$")


def default_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "KINO" / "Backups"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / "KINO" / "Backups"
    return Path.home() / ".local" / "share" / "kino" / "backups"


def is_secret(name):
    return bool(_SECRET.search(str(name)))


def shareable_settings(settings):
    """The settings without a single key or token."""
    return {name: value for name, value in (settings or {}).items() if not is_secret(name)}


def restored_settings(saved, current):
    """The saved settings, with this Mac's keys and own settings kept."""
    kept = {name: value for name, value in (current or {}).items() if is_secret(name) or name in THIS_MAC}
    return {**{name: value for name, value in (saved or {}).items() if not is_secret(name) and name not in THIS_MAC}, **kept}


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn, table):
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def counts(conn):
    """How many titles: in Ma Liste, seen, in progress, rated."""
    tables = _tables(conn)
    found = {"watchlist": 0, "seen": 0, "in_progress": 0, "rated": 0}
    if "watchlist" in tables:
        found["watchlist"] = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    if "watch_history" in tables:
        for (raw,) in conn.execute("SELECT data_json FROM watch_history"):
            try:
                entry = json.loads(raw or "{}")
            except ValueError:
                continue
            if entry.get("completed") or entry.get("imported_watched"):
                found["seen"] += 1
            else:
                found["in_progress"] += 1
            if str(entry.get("user_rating") or "").strip():
                found["rated"] += 1
    return found


def _private(path):
    try:
        os.chmod(path, 0o700 if Path(path).is_dir() else 0o600)
    except OSError:
        pass


class Backups:
    def __init__(self, directory=None, db_path=None, config_path=None, lock=None, clock=time.time,
                 version="", data_schema=1, on_error=None):
        self.directory = Path(directory) if directory else default_dir()
        self.db_path = Path(db_path) if db_path else Path.home() / ".kino_data.db"
        self.config_path = Path(config_path) if config_path else Path.home() / ".rd_cinehub_config.json"
        self._lock = lock or threading.RLock()
        self._clock = clock
        self.version = version
        self.data_schema = data_schema
        self._on_error = on_error
        self._busy = threading.Lock()
        self._thread = None

    # --- making a copy --------------------------------------------------------
    def _day(self, at=None):
        return time.strftime("%Y-%m-%d", time.localtime(self._clock() if at is None else at))

    def make(self, before_restore=False):
        """One copy of the present data; today's copy is replaced. Returns its entry."""
        with self._busy:
            now = self._clock()
            name = (f"avant-restauration-{self._day(now)}-{time.strftime('%H%M%S', time.localtime(now))}"
                    if before_restore else self._day(now))
            self.directory.mkdir(parents=True, exist_ok=True)
            _private(self.directory)
            work = self.directory / f".work-{secrets.token_hex(6)}"
            work.mkdir(mode=0o700)
            try:
                found = self._copy_database(work / "kino_data.db")
                settings = {}
                if self.config_path.exists():
                    try:
                        settings = json.loads(self.config_path.read_text(encoding="utf-8")) or {}
                    except ValueError:
                        settings = {}
                (work / "config.json").write_text(json.dumps(shareable_settings(settings), indent=2, ensure_ascii=False),
                                                  encoding="utf-8")
                _private(work / "config.json")
                manifest = {"created_at": now, "kind": "before_restore" if before_restore else "daily",
                            "version": self.version, "data_schema": self.data_schema, "counts": found}
                (work / "backup.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                final = self.directory / name
                if final.exists():
                    shutil.rmtree(final)
                work.rename(final)
            except Exception:
                shutil.rmtree(work, ignore_errors=True)
                raise
            self.prune()
            return self._entry(final)

    def _copy_database(self, destination):
        if not self.db_path.exists():
            raise FileNotFoundError("Aucune donnée KINO à sauvegarder.")
        with self._lock:
            source = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
            try:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
        conn = sqlite3.connect(destination)
        try:
            present = _tables(conn)
            with conn:
                for table in CACHE_TABLES:
                    if table in present:
                        conn.execute(f'DELETE FROM "{table}"')
            conn.execute("VACUUM")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("La copie de la base est abîmée.")
            found = counts(conn)
        finally:
            conn.close()
        _private(destination)
        return found

    # --- the copies -------------------------------------------------------------
    def _entry(self, folder):
        try:
            manifest = json.loads((folder / "backup.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not (folder / "kino_data.db").is_file():
            return None
        size = sum(path.stat().st_size for path in folder.iterdir() if path.is_file())
        return {"id": folder.name, "created_at": float(manifest.get("created_at") or 0),
                "kind": manifest.get("kind") or "daily", "version": str(manifest.get("version") or ""),
                "counts": manifest.get("counts") or {}, "bytes": size,
                "restorable": manifest.get("data_schema") == self.data_schema}

    def _folders(self):
        if not self.directory.is_dir():
            return []
        return [path for path in self.directory.iterdir()
                if path.is_dir() and (DAILY.match(path.name) or BEFORE_RESTORE.match(path.name))]

    def list(self):
        """The copies, newest first."""
        entries = [entry for entry in map(self._entry, self._folders()) if entry]
        return sorted(entries, key=lambda entry: entry["created_at"], reverse=True)

    def prune(self):
        """The 7 newest daily copies and the 2 newest « Avant restauration » stay."""
        entries = self.list()
        removed = 0
        for kind, keep in (("daily", KEEP_DAILY), ("before_restore", KEEP_BEFORE_RESTORE)):
            for entry in [e for e in entries if e["kind"] == kind][keep:]:
                shutil.rmtree(self.directory / entry["id"], ignore_errors=True)
                removed += 1
        for work in self.directory.glob(".work-*") if self.directory.is_dir() else []:
            if self._clock() - work.stat().st_mtime > 3600:
                shutil.rmtree(work, ignore_errors=True)
        return removed

    def due(self):
        """True when there is no copy of today yet."""
        return not (self.directory / self._day() / "backup.json").is_file()

    # --- « Restaurer » ----------------------------------------------------------
    def restore(self, backup_id):
        """Puts back the copy `backup_id`, after copying the present state."""
        if not (DAILY.match(str(backup_id)) or BEFORE_RESTORE.match(str(backup_id))):
            raise ValueError("Sauvegarde inconnue.")
        folder = self.directory / backup_id
        entry = self._entry(folder)
        if not entry:
            raise ValueError("Sauvegarde introuvable.")
        if not entry["restorable"]:
            raise ValueError("Cette sauvegarde vient d'une version de KINO trop différente.")
        saved = sqlite3.connect(folder.joinpath("kino_data.db").resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if saved.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Cette sauvegarde est abîmée.")
            before = self.make(before_restore=True)
            rows = {}
            saved_tables = _tables(saved)
            for table in USER_TABLES:
                if table in saved_tables:
                    columns = _columns(saved, table)
                    rows[table] = (columns, saved.execute(
                        f'SELECT {", ".join(chr(34) + c + chr(34) for c in columns)} FROM "{table}"').fetchall())
        finally:
            saved.close()
        with self._lock:
            live = sqlite3.connect(self.db_path, timeout=15)
            try:
                live.execute("PRAGMA busy_timeout = 10000")
                present = _tables(live)
                with live:
                    for table, (columns, values) in rows.items():
                        if table not in present:
                            continue
                        shared = [c for c in columns if c in set(_columns(live, table))]
                        index = [columns.index(c) for c in shared]
                        live.execute(f'DELETE FROM "{table}"')
                        names = ", ".join(f'"{c}"' for c in shared)
                        marks = ", ".join("?" for _ in shared)
                        live.executemany(f'INSERT OR REPLACE INTO "{table}" ({names}) VALUES ({marks})',
                                         [tuple(row[i] for i in index) for row in values])
            finally:
                live.close()
        self._restore_settings(folder / "config.json")
        return {"restored": entry, "before": before}

    def _restore_settings(self, saved_path):
        try:
            saved = json.loads(saved_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        current = {}
        if self.config_path.exists():
            try:
                current = json.loads(self.config_path.read_text(encoding="utf-8")) or {}
            except ValueError:
                current = {}
        merged = restored_settings(saved if isinstance(saved, dict) else {}, current)
        temporary = self.config_path.with_name(self.config_path.name + ".restoring")
        temporary.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        _private(temporary)
        os.replace(temporary, self.config_path)

    # --- once a day -------------------------------------------------------------
    def tick(self):
        """Makes today's copy if it is missing; never raises."""
        try:
            if self.due() and self.db_path.exists():
                return self.make()
        except Exception as error:
            if self._on_error:
                self._on_error(error)
        return None

    def start(self, first_delay=FIRST_DELAY, every=CHECK_EVERY):
        if self._thread and self._thread.is_alive():
            return self._thread

        def loop():
            time.sleep(first_delay)
            while True:
                self.tick()
                time.sleep(every)
        self._thread = threading.Thread(target=loop, daemon=True, name="backups")
        self._thread.start()
        return self._thread
