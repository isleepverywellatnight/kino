#!/bin/bash
# Install or update KINO on a Mac (Apple Silicon, macOS 14 or newer):
#   curl -fsSL https://raw.githubusercontent.com/isleepverywellatnight/kino/main/packaging/install.sh | bash
# Downloads the latest release, checks its SHA-256, copies KINO into
# /Applications and opens it. Nothing else to install.
set -euo pipefail

REPO="isleepverywellatnight/kino"
BASE="https://github.com/$REPO/releases/latest/download"
APP="/Applications/KINO.app"

fail() { echo "KINO : $*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "ce script est pour macOS. Sur Windows, voir le README."
[ "$(uname -m)" = "arm64" ] || fail "KINO demande un Mac Apple Silicon (M1 ou plus récent)."
major=$(sw_vers -productVersion | cut -d. -f1)
[ "$major" -ge 14 ] || fail "KINO demande macOS 14 Sonoma ou plus récent."

work=$(mktemp -d -t kino-install)
mount="$work/volume"
cleanup() { hdiutil detach -quiet "$mount" 2>/dev/null || true; rm -rf "$work"; }
trap cleanup EXIT

echo "Téléchargement de KINO…"
curl -fL --progress-bar -o "$work/KINO-macOS.dmg" "$BASE/KINO-macOS.dmg"
curl -fsSL -o "$work/SHA256SUMS.txt" "$BASE/SHA256SUMS.txt"
expected=$(awk '$2 == "KINO-macOS.dmg" || $2 == "*KINO-macOS.dmg" {print $1}' "$work/SHA256SUMS.txt")
actual=$(shasum -a 256 "$work/KINO-macOS.dmg" | awk '{print $1}')
[ -n "$expected" ] && [ "$expected" = "$actual" ] || fail "le fichier téléchargé est incomplet ou modifié."

hdiutil attach -quiet -nobrowse -readonly -mountpoint "$mount" "$work/KINO-macOS.dmg"
[ -d "$mount/KINO.app" ] || fail "image disque inattendue."

if pgrep -xq KINO; then
  echo "Fermeture de KINO…"
  osascript -e 'tell application id "com.kino.desktop" to quit' >/dev/null 2>&1 || true
  for _ in $(seq 20); do pgrep -xq KINO || break; sleep 0.5; done
fi

echo "Installation dans Applications…"
SUDO=""
[ -w /Applications ] || SUDO="sudo"
$SUDO rm -rf "$APP"
$SUDO ditto "$mount/KINO.app" "$APP"
$SUDO xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

echo "KINO est installé."
open "$APP"
