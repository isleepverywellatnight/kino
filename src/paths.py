"""Where KINO's files are, in development and once built.

In the repository, the Python code is in src/ and its files sit next to it
(web/, addons/, assets/, vendor/). PyInstaller copies them into the bundle with
the same layout, so RESOURCE_DIR / "web" is valid in both cases.
"""
import sys
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SOURCE_DIR.parent
FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_DIR = Path(sys._MEIPASS) if FROZEN and hasattr(sys, "_MEIPASS") else PROJECT_DIR
# Libraries fetched at build time (tools/vendor_libmpv.py, tools/fetch_mpv_windows.py).
VENDOR_DIR = PROJECT_DIR / "vendor"


def resource(*parts) -> Path:
    """A file shipped with KINO, e.g. resource("assets", "kino.ico")."""
    return RESOURCE_DIR.joinpath(*parts)
