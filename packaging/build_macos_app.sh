#!/bin/bash
# Assemble dist/KINO.app from the Rust build (target/release/kino-desktop).
# Needs: cargo build --release --locked -p kino-desktop, vendor/libmpv
# (tools/vendor_libmpv.py), the update helper (packaging/update-helper) and an
# FFmpeg executable to ship.
# Usage: packaging/build_macos_app.sh /path/to/ffmpeg
set -euo pipefail
cd "$(dirname "$0")/.."

FFMPEG=${1:?"Usage: packaging/build_macos_app.sh /path/to/ffmpeg"}
BINARY=target/release/kino-desktop
VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' Cargo.toml | head -1)
[ -x "$BINARY" ] || { echo "$BINARY is missing: run cargo build --release --locked -p kino-desktop" >&2; exit 1; }
[ -f vendor/libmpv/libmpv.2.dylib ] || { echo "vendor/libmpv is missing: run tools/vendor_libmpv.py" >&2; exit 1; }
[ -x packaging/update-helper/kino-update-helper ] || { echo "packaging/update-helper/kino-update-helper is missing" >&2; exit 1; }
[ -n "$VERSION" ] || { echo "The version is missing from Cargo.toml ([workspace.package])" >&2; exit 1; }

APP=dist/KINO.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/assets" "$APP/Contents/Frameworks"
cp "$BINARY" "$APP/Contents/MacOS/KINO"
# The interface, the icon, the update key and helper, FFmpeg.
ditto web "$APP/Contents/Resources/web"
cp assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp assets/kino.png assets/update_public_key.hex "$APP/Contents/Resources/assets/"
cp packaging/update-helper/kino-update-helper "$APP/Contents/Resources/kino-update-helper"
cp "$FFMPEG" "$APP/Contents/Resources/ffmpeg"
chmod 755 "$APP/Contents/Resources/ffmpeg" "$APP/Contents/Resources/kino-update-helper"
# The native player: libmpv and its libraries (each finds the others next to it).
cp vendor/libmpv/*.dylib "$APP/Contents/Frameworks/"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>fr</string>
  <key>CFBundleDisplayName</key><string>KINO</string>
  <key>CFBundleExecutable</key><string>KINO</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>com.kino.desktop</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>KINO</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key><true/>
    <key>NSAllowsArbitraryLoads</key><true/>
  </dict>
  <key>NSLocalNetworkUsageDescription</key><string>KINO cherche votre TV sur le réseau local et lui envoie la vidéo.</string>
</dict>
</plist>
PLIST

# Signed ad hoc, as before: the updater checks the signature with codesign.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
echo "KINO.app $VERSION : $APP"
