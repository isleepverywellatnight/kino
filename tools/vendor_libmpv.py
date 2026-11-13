"""Copy libmpv and its dependencies from IINA into vendor/libmpv for KINO's native player.

Only the libraries libmpv actually needs are copied, thinned to arm64. Each one
gets an @loader_path rpath so the set loads without IINA, then an ad-hoc
signature (KINO itself is ad-hoc signed). MANIFEST.json records the source and
the SHA-256 of every original and vendored file.

Usage: .venv_mac/bin/python tools/vendor_libmpv.py [--source /Applications/IINA.app]
"""
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEST = PROJECT / "vendor" / "libmpv"
ARCH = "arm64"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def dependency_closure(frameworks):
    todo, seen = ["libmpv.2.dylib"], []
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        if not (frameworks / name).is_file():
            raise SystemExit(f"Missing dependency in source: {name}")
        seen.append(name)
        listing = run("otool", "-arch", ARCH, "-L", str(frameworks / name))
        todo.extend(dep for dep in re.findall(r"^\s+@rpath/(\S+)", listing, re.M) if dep != name)
    return sorted(seen)


def main():
    source_app = Path(sys.argv[sys.argv.index("--source") + 1]) if "--source" in sys.argv else Path("/Applications/IINA.app")
    frameworks = source_app / "Contents" / "Frameworks"
    info = plistlib.loads((source_app / "Contents" / "Info.plist").read_bytes())
    signature = subprocess.run(["codesign", "-dv", str(source_app)], capture_output=True, text=True).stderr
    team = re.search(r"TeamIdentifier=(\S+)", signature)
    subprocess.run(["codesign", "--verify", "--strict", str(frameworks / "libmpv.2.dylib")], check=True)

    names = dependency_closure(frameworks)
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    files = []
    for name in names:
        original = frameworks / name
        target = DEST / name
        archs = run("lipo", "-archs", str(original)).split()
        if ARCH not in archs:
            raise SystemExit(f"{name} has no {ARCH} slice")
        if len(archs) > 1:
            run("lipo", "-thin", ARCH, str(original), "-output", str(target))
        else:
            shutil.copy2(original, target)
        target.chmod(0o755)
        rpaths = re.findall(r"LC_RPATH\n.*\n\s+path (\S+)", run("otool", "-l", str(target)))
        for rpath in rpaths:
            run("install_name_tool", "-delete_rpath", rpath, str(target))
        run("install_name_tool", "-add_rpath", "@loader_path", str(target))
        run("codesign", "--force", "--sign", "-", str(target))
        files.append({"name": name, "source_sha256": sha256(original), "sha256": sha256(target),
                      "bytes": target.stat().st_size})

    manifest = {
        "source": str(source_app), "source_version": info.get("CFBundleShortVersionString"),
        "source_team": team.group(1) if team else None, "arch": ARCH,
        "note": "mpv and FFmpeg builds shipped by IINA (GPL/LGPL). Sources: mpv.io, ffmpeg.org, github.com/iina/iina.",
        "files": files,
    }
    (DEST / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    total = sum(f["bytes"] for f in files)
    print(f"{len(files)} bibliothèques copiées dans {DEST.relative_to(PROJECT)} ({total / 1e6:.1f} Mo, {ARCH})")


if __name__ == "__main__":
    main()
