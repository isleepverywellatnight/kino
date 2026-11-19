#!/bin/bash
# The tools that build KINO's AppImage (packaging/build_linux_appimage.sh):
# linuxdeploy, its GTK, GStreamer and AppImage plugins, pinned by release (or
# commit) and SHA-256, into vendor/linuxdeploy/.
# Usage: tools/fetch_linuxdeploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=vendor/linuxdeploy
mkdir -p "$DIR"
fetch() { # name url sha256
  curl -fsSL --retry 3 -o "$DIR/$1" "$2"
  echo "$3  $DIR/$1" | shasum -a 256 -c - >/dev/null
  chmod 755 "$DIR/$1"
}
fetch linuxdeploy-x86_64.AppImage \
  https://github.com/linuxdeploy/linuxdeploy/releases/download/1-alpha-20251107-1/linuxdeploy-x86_64.AppImage \
  c20cd71e3a4e3b80c3483cef793cda3f4e990aca14014d23c544ca3ce1270b4d
fetch linuxdeploy-plugin-appimage-x86_64.AppImage \
  https://github.com/linuxdeploy/linuxdeploy-plugin-appimage/releases/download/1-alpha-20250213-1/linuxdeploy-plugin-appimage-x86_64.AppImage \
  992d502a248e14ab185448ddf6f6e7d25558cb84d4623c354c3af350c25fccb3
fetch linuxdeploy-plugin-gtk.sh \
  https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/7a3fbc31a9e5075073ff8790f26effbac5f84453/linuxdeploy-plugin-gtk.sh \
  b0f4cbc684a0103a9651f0955b635eaea0096b3a66c0f5a2c2aa337960375171
fetch linuxdeploy-plugin-gstreamer.sh \
  https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gstreamer/432af639725bc8b99df12351e86bb2e8a6b3585f/linuxdeploy-plugin-gstreamer.sh \
  d1cf90701047aa9583c274bb66dd82db352255374fbfb8f3c4c89a05fe23bce0
echo "linuxdeploy ready in $DIR"
