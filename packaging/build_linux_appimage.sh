#!/bin/bash
# Assemble dist/KINO-Linux-x86_64.AppImage from the Rust build
# (target/release/kino-desktop). Needs: cargo build --release --locked -p
# kino-desktop, vendor/linuxdeploy (tools/fetch_linuxdeploy.sh), patchelf,
# WebKitGTK 4.1 (libwebkit2gtk-4.1-dev), GStreamer's plugins
# (gstreamer1.0-plugins-base, -good, -bad, -libav) and ffmpeg.
#
# Inside the AppImage, KINO is usr/bin/kino, with its interface (web/), its
# assets and FFmpeg next to it, where it looks for them. FFmpeg is the
# system's, with the same libraries (libavcodec…) as GStreamer's libav plugin. WebKitGTK starts its
# helper processes (WebKitWebProcess, WebKitNetworkProcess, WebKitGPUProcess)
# from a folder written into libwebkit2gtk; that path is rewritten, same
# length, to usr/bin relative to the AppImage's root, the folder AppRun starts
# KINO in. The page's <video> (the « intégré » player) plays with the
# GStreamer plugins shipped in usr/lib/gstreamer-1.0: those of the same
# GStreamer as the one shipped, which a system's newer plugins may not load in.
# Usage: packaging/build_linux_appimage.sh
set -euo pipefail
cd "$(dirname "$0")/.."

BINARY=target/release/kino-desktop
FFMPEG=/usr/bin/ffmpeg
TOOLS=$PWD/vendor/linuxdeploy
VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' Cargo.toml | head -1)
MULTIARCH=x86_64-linux-gnu
WEBKIT_DIR=/usr/lib/$MULTIARCH/webkit2gtk-4.1
[ -x "$BINARY" ] || { echo "$BINARY is missing: run cargo build --release --locked -p kino-desktop" >&2; exit 1; }
[ -x "$FFMPEG" ] || { echo "$FFMPEG is missing (ffmpeg)" >&2; exit 1; }
[ -x "$TOOLS/linuxdeploy-x86_64.AppImage" ] || { echo "vendor/linuxdeploy is missing: run tools/fetch_linuxdeploy.sh" >&2; exit 1; }
command -v patchelf >/dev/null || { echo "patchelf is missing" >&2; exit 1; }
[ -x "$WEBKIT_DIR/WebKitWebProcess" ] || { echo "WebKitGTK 4.1 is missing ($WEBKIT_DIR)" >&2; exit 1; }
[ -n "$VERSION" ] || { echo "The version is missing from Cargo.toml ([workspace.package])" >&2; exit 1; }

APPDIR=$PWD/dist/KINO.AppDir
STAGE=$PWD/dist/linux-stage
OUTPUT=$PWD/dist/KINO-Linux-x86_64.AppImage
rm -rf "$APPDIR" "$STAGE" "$OUTPUT"
mkdir -p "$APPDIR/usr/bin/assets" "$STAGE"
install -m 755 "$BINARY" "$STAGE/kino"

# The menu entry.
cat > "$STAGE/kino.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=KINO
Comment=Films, series, anime and manga
Exec=kino
Icon=kino
Categories=AudioVideo;Video;Player;
Terminal=false
StartupWMClass=kino
X-AppImage-Version=$VERSION
DESKTOP

cat > "$STAGE/AppRun" <<'APPRUN'
#!/bin/sh
# Starts KINO from the AppImage's root, so that WebKitGTK finds its helper
# processes (see packaging/build_linux_appimage.sh). linuxdeploy's AppRun
# sets the GTK plugin's settings, then runs this one.
APPDIR=${APPDIR:-$(dirname "$(readlink -f "$0")")}
export APPDIR
cd "$APPDIR" || exit 1
exec "$APPDIR/usr/bin/kino" "$@"
APPRUN
chmod 755 "$STAGE/AppRun"

# GStreamer's plugins for playback: all of « base » (names change between
# versions), and from the others the demuxers, decoders, parsers and outputs
# a <video> uses.
GST_DIR=/usr/lib/$MULTIARCH/gstreamer-1.0
[ -f "$GST_DIR/libgstplayback.so" ] || { echo "GStreamer's plugins are missing ($GST_DIR)" >&2; exit 1; }
mkdir -p "$STAGE/gstreamer-1.0"
plugins=(coreelements libav
  autodetect pulseaudio isomp4 matroska audioparsers vpx videofilter deinterlace
  interleave wavparse id3demux apetag icydemux flac audiofx equalizer adaptivedemux2
  videoparsersbad mpegtsdemux hls)
plugins+=($(dpkg -L gstreamer1.0-plugins-base | sed -n 's#.*/gstreamer-1.0/libgst\(.*\)\.so$#\1#p'))
for plugin in "${plugins[@]}"; do
  [ -f "$GST_DIR/libgst$plugin.so" ] && cp "$GST_DIR/libgst$plugin.so" "$STAGE/gstreamer-1.0/"
done
for needed in coreelements playback app typefindfunctions libav isomp4 matroska autodetect videoparsersbad; do
  [ -f "$STAGE/gstreamer-1.0/libgst$needed.so" ] || { echo "GStreamer plugin $needed is missing" >&2; exit 1; }
done
export GSTREAMER_PLUGINS_DIR="$STAGE/gstreamer-1.0"

# KINO, WebKitGTK's helper processes and the libraries they need, except
# those every system has its own of (glibc, GL drivers).
helpers=()
for helper in "$WEBKIT_DIR"/WebKit*Process; do
  helpers+=(--executable "$helper")
done
export DEPLOY_GTK_VERSION=3 NO_STRIP=1 APPIMAGE_EXTRACT_AND_RUN=1
export PATH="$TOOLS:$PATH"
"$TOOLS/linuxdeploy-x86_64.AppImage" --appdir "$APPDIR" \
  --executable "$STAGE/kino" --executable "$FFMPEG" "${helpers[@]}" \
  --desktop-file "$STAGE/kino.desktop" --icon-file assets/kino.png --icon-filename kino \
  --custom-apprun "$STAGE/AppRun" \
  --plugin gtk --plugin gstreamer

# KINO's interface and its assets, next to it; WebKitGTK's injected
# bundle next to its processes.
cp -R web "$APPDIR/usr/bin/web"
cp assets/kino.png assets/update_public_key.hex "$APPDIR/usr/bin/assets/"
cp -R "$WEBKIT_DIR/injected-bundle" "$APPDIR/usr/bin/injected-bundle"

# WebKitGTK's folder, rewritten to usr/bin from the AppImage's root (AppRun's
# folder), padded with « ./ » (and a doubled « / ») to the same length.
LIB="$APPDIR/usr/lib/libwebkit2gtk-4.1.so.0"
grep -qa "$WEBKIT_DIR" "$LIB" || { echo "$WEBKIT_DIR is not in $LIB" >&2; exit 1; }
target=usr/bin
[ $(( (${#WEBKIT_DIR} - ${#target}) % 2 )) -eq 0 ] || target=usr//bin
while [ ${#target} -lt ${#WEBKIT_DIR} ]; do target=./$target; done
[ ${#target} -eq ${#WEBKIT_DIR} ] || { echo "Cannot rewrite $WEBKIT_DIR to the same length" >&2; exit 1; }
sed -i "s#$WEBKIT_DIR#$target#g" "$LIB"
! grep -qa "$WEBKIT_DIR" "$LIB"
for helper in WebKitWebProcess WebKitNetworkProcess; do
  [ -x "$APPDIR/usr/bin/$helper" ] || { echo "$helper is missing from the AppImage" >&2; exit 1; }
done

LDAI_OUTPUT="$OUTPUT" \
  "$TOOLS/linuxdeploy-plugin-appimage-x86_64.AppImage" --appdir "$APPDIR"
ls -la "$OUTPUT"
