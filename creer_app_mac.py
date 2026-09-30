#!/usr/bin/env python3
"""
Générateur d'application macOS KINO.app.
Crée le bundle d'application natif pour macOS (compatible Finder, Dock, Spotlight).
"""
import os
import shutil
import stat
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "KINO.app"
APP_DIR = BASE_DIR / APP_NAME
CONTENTS = APP_DIR / "Contents"
MACOS = CONTENTS / "MacOS"
RESOURCES = CONTENTS / "Resources"


def build():
    print(f"Création de {APP_NAME}...")
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)

    MACOS.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)

    plist_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>KINO</string>
    <key>CFBundleDisplayName</key>
    <string>KINO</string>
    <key>CFBundleIdentifier</key>
    <string>com.kino.desktop</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <key>CFBundleExecutable</key>
    <string>KINO</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
        <key>NSAllowsLocalNetworking</key>
        <true/>
    </dict>
</dict>
</plist>
"""
    (CONTENTS / "Info.plist").write_text(plist_content, encoding="utf-8")
    (CONTENTS / "PkgInfo").write_text("APPL????", encoding="utf-8")

    launcher_sh = """#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../../.." >/dev/null 2>&1 && pwd )"
cd "$DIR"

if [ -f "Lancer_KINO.command" ]; then
    bash "Lancer_KINO.command"
elif [ -d ".venv_mac" ]; then
    source .venv_mac/bin/activate
    python3 desktop.py
else
    python3 desktop.py
fi
"""
    launcher_file = MACOS / "KINO"
    launcher_file.write_text(launcher_sh, encoding="utf-8")
    try:
        launcher_file.chmod(launcher_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass

    icon_src = BASE_DIR / "kino.png"
    if icon_src.exists():
        shutil.copy(icon_src, RESOURCES / "AppIcon.png")

    print(f"[OK] {APP_NAME} genere avec succes dans : {APP_DIR}")


if __name__ == "__main__":
    build()
