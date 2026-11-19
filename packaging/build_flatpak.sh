#!/bin/bash
# Build dist/KINO-Linux-x86_64.flatpak, a single-file bundle of KINO for the
# GNOME runtime (packaging/flatpak/com.kino.desktop.yml), from the Rust build.
# Needs: cargo build --release --locked -p kino-desktop, vendor/ffmpeg
# (tools/fetch_ffmpeg.sh), flatpak and flatpak-builder, and Flathub as a user
# remote (the runtime and SDK are installed from it).
# Usage: packaging/build_flatpak.sh
set -euo pipefail
cd "$(dirname "$0")/.."

BINARY=target/release/kino-desktop
FFMPEG=vendor/ffmpeg/ffmpeg
[ -x "$BINARY" ] || { echo "$BINARY is missing: run cargo build --release --locked -p kino-desktop" >&2; exit 1; }
[ -x "$FFMPEG" ] || { echo "$FFMPEG is missing: run tools/fetch_ffmpeg.sh" >&2; exit 1; }

# What the manifest's module installs.
STAGE=dist/flatpak-stage
rm -rf "$STAGE" dist/flatpak-build dist/flatpak-repo dist/KINO-Linux-x86_64.flatpak
mkdir -p "$STAGE"
install -m 755 "$BINARY" "$STAGE/kino"
install -m 755 "$FFMPEG" "$STAGE/ffmpeg"
cp -R web "$STAGE/web"
cp assets/kino.png assets/update_public_key.hex "$STAGE/"
cp packaging/flatpak/com.kino.desktop.desktop packaging/flatpak/com.kino.desktop.metainfo.xml "$STAGE/"

flatpak-builder --user --install-deps-from=flathub --disable-rofiles-fuse --force-clean \
  --repo=dist/flatpak-repo dist/flatpak-build packaging/flatpak/com.kino.desktop.yml
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
  dist/flatpak-repo dist/KINO-Linux-x86_64.flatpak com.kino.desktop
ls -la dist/KINO-Linux-x86_64.flatpak
