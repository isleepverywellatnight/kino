"""Is the internet reachable? KINO checks at launch and when the interface
asks, and learns from every request made through config.http_json. While the
internet is known to be down, those requests fail at once with a readable
message instead of retrying for seconds.

The check only opens a connection to hosts KINO already uses (the catalogue,
Wikipedia) and closes it: nothing is sent."""
import errno
import socket
import threading
import time
import urllib.error

PROBE_HOSTS = (("v3-cinemeta.strem.io", 443), ("fr.wikipedia.org", 443))
PROBE_TIMEOUT = 1.5
FRESH_SECONDS = 20     # a check is trusted this long
FAST_FAIL_SECONDS = 10  # after a network failure, requests fail at once this long

MESSAGE = "Pas de connexion à internet."
_NETWORK_ERRNOS = {errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTUNREACH}

_lock = threading.Lock()        # guards _state
_probe_lock = threading.Lock()  # one check at a time; others wait for its answer
_state = {"online": None, "checked_at": 0.0, "checks": 0}


class OfflineError(ConnectionError):
    """A request needed the internet while it is down."""

    def __init__(self, message=MESSAGE):
        super().__init__(message)


def is_network_error(error):
    """True for « no network » failures (no DNS, no route), not for a slow or
    refusing server."""
    if isinstance(error, OfflineError):
        return True
    if isinstance(error, urllib.error.URLError) and not isinstance(error, urllib.error.HTTPError):
        error = error.reason
    if isinstance(error, socket.gaierror):
        return True
    return isinstance(error, OSError) and error.errno in _NETWORK_ERRNOS


def _set(online, now=None):
    with _lock:
        _state["online"] = bool(online)
        _state["checked_at"] = time.monotonic() if now is None else now
        _state["checks"] += 1


def note_success():
    _set(True)


def note_failure(error):
    """Remember a network failure; other errors say nothing about the internet."""
    if is_network_error(error) and not isinstance(error, OfflineError):
        _set(False)


def known_offline(now=None):
    """The internet was seen down a few seconds ago."""
    now = time.monotonic() if now is None else now
    with _lock:
        return _state["online"] is False and now - _state["checked_at"] < FAST_FAIL_SECONDS


def probe(hosts=PROBE_HOSTS, timeout=PROBE_TIMEOUT, connect=socket.create_connection):
    """Open and close a connection to the first reachable host."""
    for host, port in hosts:
        try:
            connect((host, port), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def status(refresh=False, check=None):
    """{"online": bool}: checked again when asked or when the last check is old."""
    check = check or probe
    with _lock:
        fresh = _state["online"] is not None and time.monotonic() - _state["checked_at"] < FRESH_SECONDS
        online = _state["online"]
        seen = _state["checks"]
    if refresh or not fresh:
        with _probe_lock:
            with _lock:
                # Another request checked while this one waited: use its answer.
                # (A counter, not the clock: Windows' ticks every 16 ms.)
                done = _state["online"] is not None and _state["checks"] != seen
                online = _state["online"]
            if not done:
                online = check()
                _set(online)
    return {"online": bool(online)}


def warm():
    """Launch: check while the window opens, so the first tab knows at once."""
    try:
        status()
    except Exception:
        pass


def reset():
    """Tests: forget what was seen."""
    with _lock:
        _state.update(online=None, checked_at=0.0, checks=0)
