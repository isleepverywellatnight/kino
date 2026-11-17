#!/bin/bash
# Copy libmpv and the libraries it needs from IINA into vendor/libmpv, for
# KINO's native player. Only the libraries libmpv actually loads are copied,
# thinned to arm64; each gets an @loader_path rpath so the set loads without
# IINA, then an ad-hoc signature (KINO itself is ad-hoc signed).
# MANIFEST.json records the source and the SHA-256 of every original and copy.
# Usage: tools/vendor_libmpv.sh [/Applications/IINA.app]
set -euo pipefail
cd "$(dirname "$0")/.."

SOURCE=${1:-/Applications/IINA.app}
FRAMEWORKS="$SOURCE/Contents/Frameworks"
DEST=vendor/libmpv
ARCH=arm64

codesign --verify --strict "$FRAMEWORKS/libmpv.2.dylib"
VERSION=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$SOURCE/Contents/Info.plist")
TEAM=$(codesign -dv "$SOURCE" 2>&1 | sed -n 's/^TeamIdentifier=//p')

# libmpv and, transitively, every @rpath library it names.
todo=(libmpv.2.dylib)
seen=()
while [ ${#todo[@]} -gt 0 ]; do
  name=${todo[${#todo[@]}-1]}
  unset 'todo[${#todo[@]}-1]'
  case " ${seen[*]-} " in *" $name "*) continue ;; esac
  [ -f "$FRAMEWORKS/$name" ] || { echo "Missing dependency in source: $name" >&2; exit 1; }
  seen+=("$name")
  while read -r dep; do
    [ "$dep" != "$name" ] && todo+=("$dep")
  done < <(otool -arch "$ARCH" -L "$FRAMEWORKS/$name" | sed -n 's/^[[:space:]]*@rpath\/\([^[:space:]]*\).*/\1/p')
done

rm -rf "$DEST"
mkdir -p "$DEST"
entries=()
total=0
for name in $(printf '%s\n' "${seen[@]}" | sort); do
  original="$FRAMEWORKS/$name"
  target="$DEST/$name"
  archs=$(lipo -archs "$original")
  case " $archs " in *" $ARCH "*) ;; *) echo "$name has no $ARCH slice" >&2; exit 1 ;; esac
  if [ "$(echo "$archs" | wc -w)" -gt 1 ]; then
    lipo -thin "$ARCH" "$original" -output "$target"
  else
    cp -p "$original" "$target"
  fi
  chmod 755 "$target"
  for rpath in $(otool -l "$target" | awk '/LC_RPATH/ { getline; getline; print $2 }'); do
    install_name_tool -delete_rpath "$rpath" "$target"
  done
  install_name_tool -add_rpath @loader_path "$target"
  codesign --force --sign - "$target"
  bytes=$(stat -f %z "$target")
  total=$((total + bytes))
  entries+=("    {\"name\": \"$name\", \"source_sha256\": \"$(shasum -a 256 "$original" | cut -d' ' -f1)\", \"sha256\": \"$(shasum -a 256 "$target" | cut -d' ' -f1)\", \"bytes\": $bytes}")
done

{
  echo "{"
  echo "  \"source\": \"$SOURCE\", \"source_version\": \"$VERSION\", \"source_team\": \"$TEAM\", \"arch\": \"$ARCH\","
  echo "  \"note\": \"mpv and FFmpeg builds shipped by IINA (GPL/LGPL). Sources: mpv.io, ffmpeg.org, github.com/iina/iina.\","
  echo "  \"files\": ["
  last=$((${#entries[@]} - 1))
  for i in "${!entries[@]}"; do
    if [ "$i" -lt "$last" ]; then echo "${entries[$i]},"; else echo "${entries[$i]}"; fi
  done
  echo "  ]"
  echo "}"
} > "$DEST/MANIFEST.json"
echo "${#seen[@]} bibliothèques copiées dans $DEST ($((total / 1000000)) Mo, $ARCH)"
