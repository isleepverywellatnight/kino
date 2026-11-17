"""Download the mpv build bundled with KINO for Windows into vendor/mpv-windows.

The archive is pinned by name and SHA-256: a different file is refused. Only the
player itself is kept (mpv.exe and the Direct3D shader compiler it loads);
the documentation and mpv's own installer scripts are left out. MANIFEST.json
records the source and the SHA-256 of every extracted file.

Builds by shinchiro (github.com/shinchiro/mpv-winbuild-cmake), mirrored on
SourceForge. mpv and FFmpeg are under GPL/LGPL: sources at mpv.io and ffmpeg.org.

Usage: python tools/fetch_mpv_windows.py [--archive path/to/mpv-x86_64-....7z]
Needs 7-Zip (7z.exe; installed on GitHub's Windows runners), or 7zz/7za elsewhere.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEST = PROJECT / "vendor" / "mpv-windows"

BUILD = "20261018"
ARCHIVE = f"mpv-x86_64-{BUILD}-git-01e2533f31.7z"
ARCHIVE_SHA256 = "d7157567a2fd88e46f739d2e8b8e6c02d2ad812db04547b45c59a2800821991f"
SOURCES = (
    f"https://downloads.sourceforge.net/project/mpv-player-windows/64bit/{ARCHIVE}",
    f"https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/{BUILD}/{ARCHIVE}",
)
KEEP = ("mpv.exe", "mpv.com", "d3dcompiler_43.dll")
MAX_BYTES = 200 * 1024 * 1024


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, target):
    request = urllib.request.Request(url, headers={"User-Agent": "KINO-build"})
    with urllib.request.urlopen(request, timeout=120) as response, open(target, "wb") as out:
        copied = 0
        while chunk := response.read(1 << 20):
            copied += len(chunk)
            if copied > MAX_BYTES:
                raise ValueError("archive trop volumineuse")
            out.write(chunk)


def fetch(workdir):
    archive = workdir / ARCHIVE
    errors = []
    for url in SOURCES:
        try:
            download(url, archive)
        except Exception as error:  # network or HTTP error: try the next mirror
            errors.append(f"{url}: {error}")
            continue
        actual = sha256(archive)
        if actual == ARCHIVE_SHA256:
            return archive, url
        errors.append(f"{url}: SHA-256 {actual} au lieu de {ARCHIVE_SHA256}")
    raise SystemExit("Impossible de récupérer mpv :\n  " + "\n  ".join(errors))


def seven_zip():
    """The 7-Zip command line: the archives use the BCJ2 filter, which Python modules do not read."""
    for name in ("7z", "7zz", "7za"):
        found = shutil.which(name)
        if found:
            return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
            return str(Path(base) / "7-Zip" / "7z.exe")
    raise SystemExit("7-Zip est introuvable : installez-le (7-zip.org) puis relancez.")


def main():
    tool = seven_zip()
    with tempfile.TemporaryDirectory(prefix="kino-mpv-") as tmp:
        workdir = Path(tmp)
        if "--archive" in sys.argv:
            archive = Path(sys.argv[sys.argv.index("--archive") + 1])
            source = str(archive)
            if sha256(archive) != ARCHIVE_SHA256:
                raise SystemExit(f"{archive.name} n'a pas l'empreinte attendue.")
        else:
            archive, source = fetch(workdir)
        extracted = workdir / "extracted"
        subprocess.run([tool, "e", "-y", f"-o{extracted}", str(archive), *KEEP],
                       check=True, stdout=subprocess.DEVNULL)
        if DEST.exists():
            shutil.rmtree(DEST)
        DEST.mkdir(parents=True)
        files = []
        for name in KEEP:
            path = extracted / name
            if not path.is_file():
                raise SystemExit(f"{name} absent de {ARCHIVE}")
            shutil.copy2(path, DEST / name)
            files.append({"name": name, "sha256": sha256(DEST / name), "bytes": (DEST / name).stat().st_size})
    manifest = {
        "source": source, "archive": ARCHIVE, "archive_sha256": ARCHIVE_SHA256,
        "note": "mpv and FFmpeg built by shinchiro/mpv-winbuild-cmake (GPL/LGPL). Sources: mpv.io, ffmpeg.org.",
        "files": files,
    }
    (DEST / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    total = sum(f["bytes"] for f in files)
    print(f"mpv {BUILD} copié dans {DEST.relative_to(PROJECT)} ({total / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
