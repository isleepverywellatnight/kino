"""KINO's error log: what failed, where and when, kept on this
computer only, in a few small files.

Until 1.1.96 an error was answered to the page and forgotten, a failed
background task left no trace, and an error of the page itself went nowhere.
Now the server's routes, the background threads and the page (window errors and
rejected promises, sent to /api/client-error) write one line each here, and the
Diagnostic shows the recent ones.

Nothing private is written: links keep their host only, magnets, keys and
e-mail addresses are masked, the home folder becomes « ~ ». Standard library
only; the same error repeated within a minute is written once.
"""
import json
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

MAX_BYTES = 512 * 1024
BACKUPS = 2
REPEAT_WINDOW = 60.0
MAX_MESSAGE = 500
MAX_FRAMES = 8


def default_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "KINO" / "Logs"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / "KINO" / "Logs"
    return Path.home() / ".local" / "share" / "kino" / "logs"


_URL = re.compile(r"\b([a-z][a-z0-9+.-]*)://([^/\s:?#]+)[^\s\"'<>)]*", re.IGNORECASE)
_MAGNET = re.compile(r"magnet:\?\S+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{28,}\b")


def redact(text, secrets=()):
    """The text without keys, links' paths, magnets, e-mails or the home folder."""
    text = str(text or "")
    for secret in secrets or ():
        if secret and len(str(secret)) >= 6:
            text = text.replace(str(secret), "[clé]")
    text = _MAGNET.sub("magnet:[…]", text)
    text = _URL.sub(lambda m: f"{m.group(1)}://{m.group(2)}/…", text)
    text = _EMAIL.sub("[e-mail]", text)
    text = _TOKEN.sub("[…]", text)
    home = str(Path.home())
    if len(home) > 1:
        text = text.replace(home, "~")
    return text


def _frames(error=None, stack_text=""):
    """The last frames, as « file:line in function » (file names only)."""
    if error is not None and getattr(error, "__traceback__", None) is not None:
        summary = traceback.extract_tb(error.__traceback__)[-MAX_FRAMES:]
        return [f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}" for frame in summary]
    lines = [line.strip() for line in str(stack_text or "").splitlines() if line.strip()]
    return lines[:MAX_FRAMES]


class ErrorLog:
    def __init__(self, directory=None, secrets=lambda: (), clock=time.time):
        self.dir = Path(directory) if directory else default_dir()
        self._secrets = secrets
        self._clock = clock
        self._lock = threading.Lock()
        self._last = {}  # signature -> time written

    @property
    def path(self):
        return self.dir / "errors.log"

    def record(self, where, error=None, message=None, kind=None, stack=""):
        """One line for one failure. Never raises: the log must not break KINO."""
        try:
            secrets = tuple(self._secrets() or ())
        except Exception:
            secrets = ()
        try:
            kind = kind or (type(error).__name__ if error is not None else "Erreur")
            text = message if message is not None else (str(error) if error is not None else "")
            entry = {
                "t": round(self._clock(), 3),
                "where": redact(where, secrets)[:120],
                "kind": redact(kind, secrets)[:60],
                "message": redact(text, secrets)[:MAX_MESSAGE],
                "trace": [redact(frame, secrets)[:200] for frame in _frames(error, stack)],
            }
            signature = (entry["where"], entry["kind"], entry["message"])
            with self._lock:
                now = entry["t"]
                if now - self._last.get(signature, -REPEAT_WINDOW - 1) < REPEAT_WINDOW:
                    return False
                self._last[signature] = now
                if len(self._last) > 200:
                    self._last = dict(sorted(self._last.items(), key=lambda kv: kv[1])[-100:])
                self.dir.mkdir(parents=True, exist_ok=True)
                self._rotate()
                with open(self.path, "a", encoding="utf-8") as out:
                    out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return True
        except Exception:
            return False

    def _rotate(self):
        try:
            if self.path.stat().st_size < MAX_BYTES:
                return
        except FileNotFoundError:
            return
        for index in range(BACKUPS, 0, -1):
            older = self.dir / f"errors.log.{index}"
            newer = self.dir / (f"errors.log.{index - 1}" if index > 1 else "errors.log")
            if newer.exists():
                os.replace(newer, older)

    def recent(self, limit=20, since=None):
        """The latest entries, newest first (since: a time, to keep only the later ones)."""
        entries = []
        for name in ["errors.log"] + [f"errors.log.{index}" for index in range(1, BACKUPS + 1)]:
            try:
                lines = (self.dir / name).read_text(encoding="utf-8").splitlines()
            except (FileNotFoundError, OSError):
                continue
            for line in reversed(lines):
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if since is not None and entry.get("t", 0) < since:
                    return entries
                entries.append(entry)
                if len(entries) >= limit:
                    return entries
        return entries

    def count_since(self, since):
        return len(self.recent(limit=10_000, since=since))

    def install(self):
        """Uncaught errors of the main program and of every thread come here too."""
        previous_hook = sys.excepthook

        def main_hook(kind, error, trace):
            self.record("programme", error)
            previous_hook(kind, error, trace)

        previous_thread_hook = threading.excepthook

        def thread_hook(args):
            if args.exc_type is not SystemExit:
                self.record(f"tâche {getattr(args.thread, 'name', '')}".strip(), args.exc_value)
            previous_thread_hook(args)

        sys.excepthook = main_hook
        threading.excepthook = thread_hook


def line_for(entry):
    """One entry for a human: « 12/11 03:14 · serveur GET /api/x · ValueError : message »."""
    when = time.strftime("%d/%m %H:%M", time.localtime(entry.get("t", 0)))
    return f"{when} · {entry.get('where', '')} · {entry.get('kind', '')} : {entry.get('message', '')}"


def _config_secrets():
    """The keys KINO holds, so that they never reach the log even inside a message."""
    try:
        import config
        cfg = config.load_config()
        found = [cfg.get("rd_token", "")]
        found += [value for value in (cfg.get("provider_tokens") or {}).values() if isinstance(value, str)]
        found.append(cfg.get("tmdb_key", ""))
        return [value for value in found if value]
    except Exception:
        return []


_SECRETS_CACHE = {"at": 0.0, "list": []}


def _cached_secrets():
    if time.time() - _SECRETS_CACHE["at"] > 60:
        _SECRETS_CACHE["list"], _SECRETS_CACHE["at"] = _config_secrets(), time.time()
    return _SECRETS_CACHE["list"]


# The one log of the app.
log = ErrorLog(secrets=_cached_secrets)
