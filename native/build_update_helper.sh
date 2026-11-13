#!/bin/zsh
set -euo pipefail
cd "${0:A:h}/.."
CLANG_MODULE_CACHE_PATH=/private/tmp/kino-clang SWIFT_MODULE_CACHE_PATH=/private/tmp/kino-swift \
  swiftc -O -target arm64-apple-macos13.0 \
  native/kino_update_helper.swift -o native/kino-update-helper
codesign --force --sign - native/kino-update-helper
