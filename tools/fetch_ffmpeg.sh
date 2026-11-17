#!/bin/bash
# FFmpeg shipped in KINO.app: the build imageio-ffmpeg publishes (static,
# arm64), pinned by version and SHA-256, unpacked into vendor/ffmpeg/ffmpeg.
# Usage: tools/fetch_ffmpeg.sh
set -euo pipefail
cd "$(dirname "$0")/.."
WHEEL=imageio_ffmpeg-0.8.1-py3-none-macosx_12_0_arm64.whl
SHA256=5636d691e36e5274b13fe38182b1f627b2e35dcecc63944012c2470f9484fc45
URL=https://files.pythonhosted.org/packages/2b/47/9f9b654f853598bae0850fa22bba168103e45d31b5a2a893ed474f28d54a/$WHEEL
WORK=$(mktemp -d -t kino-ffmpeg)
trap 'rm -rf "$WORK"' EXIT
curl -fsSL --retry 3 -o "$WORK/$WHEEL" "$URL"
echo "$SHA256  $WORK/$WHEEL" | shasum -a 256 -c - >/dev/null
unzip -q -o "$WORK/$WHEEL" 'imageio_ffmpeg/binaries/ffmpeg-*' -d "$WORK/out"
mkdir -p vendor/ffmpeg
cp "$WORK"/out/imageio_ffmpeg/binaries/ffmpeg-* vendor/ffmpeg/ffmpeg
chmod 755 vendor/ffmpeg/ffmpeg
vendor/ffmpeg/ffmpeg -version | head -1
