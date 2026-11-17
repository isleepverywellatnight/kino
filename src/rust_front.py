"""KINO's Rust server, in front of this one.

The Rust server (crates/kino, built as kino-server) answers on KINO's port
and forwards to this Python server, on a private local port, the routes it
does not handle yet. When it is missing or does not start, this server takes
the port itself, as before. KINO_PYTHON_ONLY=1 keeps the Python server alone.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import paths

NAME = "kino-server.exe" if sys.platform == "win32" else "kino-server"
START_TIMEOUT = 5.0


def locate():
    """The kino-server program: given by KINO_RUST_SERVER, shipped with KINO, or built here."""
    if os.environ.get("KINO_PYTHON_ONLY") == "1":
        return None
    candidates = [os.environ.get("KINO_RUST_SERVER"), paths.resource(NAME), paths.RESOURCE_DIR / "_internal" / NAME,
                  paths.PROJECT_DIR / "target" / "release" / NAME]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None


def _answers(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def start(program, port, legacy_port, web_root, pairing_token, native_player=False, notifications=False, log_dir=None):
    """Starts kino-server on `port` in front of this server; the process, or None if it did not answer."""
    env = dict(os.environ, KINO_PORT=str(port), KINO_LEGACY=f"http://127.0.0.1:{legacy_port}", KINO_WEB=str(web_root),
               KINO_PAIRING_TOKEN=pairing_token, KINO_EXIT_WITH_STDIN="1",
               KINO_NATIVE_PLAYER="1" if native_player else "0", KINO_NOTIFICATIONS="1" if notifications else "0")
    log = subprocess.DEVNULL
    if log_dir:
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            log = open(Path(log_dir) / "server.log", "wb")  # noqa: SIM115 - kept open by the child
        except OSError:
            log = subprocess.DEVNULL
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        process = subprocess.Popen([str(program)], stdin=subprocess.PIPE, stdout=log, stderr=log, env=env, creationflags=flags)
    except OSError:
        return None
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None
        if _answers(port):
            return process
        time.sleep(0.05)
    stop(process)
    return None


def stop(process):
    """Ends kino-server (closing its input is enough; this makes sure)."""
    if process is None:
        return
    try:
        if process.stdin:
            process.stdin.close()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


__all__ = ["locate", "start", "stop", "NAME"]
