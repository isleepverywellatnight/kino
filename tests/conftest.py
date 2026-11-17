"""The tests import KINO's modules as the app does: from src/ (and tools/)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = [str(ROOT / "src"), str(ROOT / "tools"), str(ROOT / "tests")]
for folder in reversed(FOLDERS):
    if folder not in sys.path:
        sys.path.insert(0, folder)
# Scripts some tests start in a separate Python find the modules the same way.
os.environ["PYTHONPATH"] = os.pathsep.join(FOLDERS + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p])
