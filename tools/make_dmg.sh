#!/bin/bash
# Package dist/KINO.app into a compressed disk image with an Applications shortcut.
# Usage: tools/make_dmg.sh [output.dmg]   (default: dist/KINO-<version>-arm64.dmg)
set -euo pipefail
cd "$(dirname "$0")/.."

APP=dist/KINO.app
[ -d "$APP" ] || { echo "dist/KINO.app est absent : lancez d'abord pyinstaller kino_macos.spec" >&2; exit 1; }
VERSION=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$APP/Contents/Info.plist")
OUTPUT=${1:-dist/KINO-$VERSION-arm64.dmg}

STAGING=$(mktemp -d -t kino-dmg)
trap 'rm -rf "$STAGING"' EXIT
ditto "$APP" "$STAGING/KINO.app"
ln -s /Applications "$STAGING/Applications"

rm -f "$OUTPUT"
hdiutil create -volname "KINO $VERSION" -srcfolder "$STAGING" -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$OUTPUT"
echo "Image disque : $OUTPUT"
