#!/bin/bash
# FFmpeg shipped with KINO: the static build imageio-ffmpeg publishes (macOS
# arm64 for KINO.app, Linux x86_64 for the Flatpak), pinned by version and
# SHA-256, unpacked into vendor/ffmpeg/ffmpeg.
# Usage: tools/fetch_ffmpeg.sh
set -euo pipefail
cd "$(dirname "$0")/.."
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)
    WHEEL=imageio_ffmpeg-0.8.1-py3-none-macosx_12_0_arm64.whl
    SHA256=5636d691e36e5274b13fe38182b1f627b2e35dcecc63944012c2470f9484fc45
    URL=https://files.pythonhosted.org/packages/2b/47/9f9b654f853598bae0850fa22bba168103e45d31b5a2a893ed474f28d54a/$WHEEL
    ;;
  Linux-x86_64)
    WHEEL=imageio_ffmpeg-0.8.1-py3-none-manylinux_2_28_x86_64.whl
    SHA256=050d6850601c3b3890bd2b28306f3b842b3daea390a561101174375a1c460bda
    URL=https://files.pythonhosted.org/packages/de/0f/49292fbef69ee10ebcf97bb741602521caafcc6966d5fa1a2b868cc15655/$WHEEL
    ;;
  *)
    echo "No FFmpeg pinned for $(uname -s) $(uname -m)" >&2
    exit 1
    ;;
esac
WORK=$(mktemp -d "${TMPDIR:-/tmp}/kino-ffmpeg.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
curl -fsSL --retry 3 -o "$WORK/$WHEEL" "$URL"
echo "$SHA256  $WORK/$WHEEL" | shasum -a 256 -c - >/dev/null
unzip -q -o "$WORK/$WHEEL" 'imageio_ffmpeg/binaries/ffmpeg-*' -d "$WORK/out"
mkdir -p vendor/ffmpeg
cp "$WORK"/out/imageio_ffmpeg/binaries/ffmpeg-* vendor/ffmpeg/ffmpeg
chmod 755 vendor/ffmpeg/ffmpeg
vendor/ffmpeg/ffmpeg -version | head -1
