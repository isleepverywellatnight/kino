#!/bin/bash
# Tests KINO's AppImage as a user starts it, on a virtual screen (Xvfb):
#   1. its self-check passes;
#   2. KINO starts, its server answers, its window stays open and WebKitGTK's
#      processes run from the AppImage (not from the system);
#   3. the page plays an H.264 and an HEVC video with the GStreamer the
#      AppImage ships (tests/linux-appimage, served through KINO_WEB).
# Screenshots go to dist/startup-linux.png and dist/video-linux.png.
# Needs: xvfb-run, ImageMagick (import, convert), ffmpeg with x264 and x265.
# Usage: tools/linux_appimage_test.sh [dist/KINO-Linux-x86_64.AppImage]
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=$(realpath "${1:-dist/KINO-Linux-x86_64.AppImage}")
[ -x "$IMAGE" ] || { echo "$IMAGE is missing" >&2; exit 1; }
WORK=$(mktemp -d -t kino-appimage-test.XXXXXX)
export APPIMAGE_EXTRACT_AND_RUN=1

echo "== Self-check"
"$IMAGE" --self-check --self-check-report "$WORK/self-check.json"
cat "$WORK/self-check.json"; echo

# The test page and its videos.
cp -R tests/linux-appimage "$WORK/web"
ffmpeg -loglevel error -y -f lavfi -i testsrc=size=640x360:rate=25 -t 12 \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$WORK/web/js/h264.mp4"
ffmpeg -loglevel error -y -f lavfi -i testsrc2=size=640x360:rate=25 -t 12 \
  -c:v libx265 -x265-params log-level=error -tag:v hvc1 -pix_fmt yuv420p \
  -movflags +faststart "$WORK/web/js/hevc.mp4"

cat > "$WORK/run.sh" <<'RUN'
#!/bin/bash
# Runs inside xvfb-run: $1 the AppImage, $2 the work folder.
set -uo pipefail
IMAGE=$1 WORK=$2
fail=0
start() { # name [env...]
  local name=$1; shift
  mkdir -p "$WORK/home-$name"
  env HOME="$WORK/home-$name" "$@" "$IMAGE" > "$WORK/$name.log" 2>&1 &
  echo $!
}
stop() {
  kill "$1" 2>/dev/null
  pkill -f appimage_extracted_ 2>/dev/null
  pkill -f "usr//bin/WebKit" 2>/dev/null
  sleep 2
}
answers() {
  for _ in $(seq 60); do
    for port in $(seq 8080 8089); do
      page=$(curl -fsS --noproxy "*" "http://127.0.0.1:$port/" 2>/dev/null) || continue
      [[ "$page" == *"$1"* ]] && return 0
    done
    sleep 1
  done
  return 1
}

echo "== Start-up"
pid=$(start startup)
if answers KINO; then echo "The server answers"; else echo "::error::KINO's server did not answer"; fail=1; fi
sleep 15
if kill -0 "$pid" 2>/dev/null; then echo "KINO is running"; else echo "::error::KINO closed at start-up"; fail=1; fi
if pgrep -af "usr//bin/WebKitWebProcess" >/dev/null; then
  echo "WebKitGTK runs from the AppImage"
else
  echo "::error::WebKitGTK's page process does not run from the AppImage"; pgrep -af WebKit; fail=1
fi
import -window root "$WORK/startup.png" || true
stop "$pid"
cat "$WORK/startup.log"

echo "== Video in the page"
pid=$(start video KINO_WEB="$WORK/web")
answers "KINO video test" || { echo "::error::The test page did not load"; fail=1; }
green=0
for _ in $(seq 30); do
  sleep 1
  import -window root "$WORK/video.png" 2>/dev/null || continue
  green=$(convert "$WORK/video.png" -alpha off -fuzz 8% -fill white -opaque '#00c853' \
    -fill black +opaque white -format '%[fx:int(mean*100)]' info:)
  [ "$green" -ge 20 ] && break
done
if [ "$green" -ge 20 ]; then
  echo "Both videos play ($green % of the screen is the page's green)"
else
  echo "::error::The page's videos did not play ($green % green)"; fail=1
fi
stop "$pid"
cat "$WORK/video.log"
exit $fail
RUN
chmod 755 "$WORK/run.sh"

status=0
xvfb-run -a -s "-screen 0 1600x1000x24" "$WORK/run.sh" "$IMAGE" "$WORK" || status=$?
mkdir -p dist
cp "$WORK/startup.png" dist/startup-linux.png 2>/dev/null || true
cp "$WORK/video.png" dist/video-linux.png 2>/dev/null || true
rm -rf "$WORK"
exit $status
