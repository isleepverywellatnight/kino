# Building and publishing KINO

**English** · [Français](../docs/packaging.fr.md) · [Deutsch](../docs/packaging.de.md) · [Español](../docs/packaging.es.md) · [Italiano](../docs/packaging.it.md) · [Português](../docs/packaging.pt.md) · [Русский](../docs/packaging.ru.md) · [中文](../docs/packaging.zh.md) · [日本語](../docs/packaging.ja.md)

These steps are for development. To use KINO, just download it (see the [README](../README.md)).

## Publishing a version

Change `APP_VERSION` in `src/kino_version.py` and push to `main`: that's all. When the version on `main` has no release yet, GitHub Actions (`.github/workflows/build.yml`) builds both apps, tests them, publishes the release with its `vX.Y.Z` tag (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`), then reinstalls it on a clean Mac and a clean Windows with the README's commands.

For KINO to install new versions by itself, the CI signs updates with the `KINO_UPDATE_SIGNING_KEY` secret (Settings → Secrets and variables → Actions): the contents of the private key created by `tools/build_update_package.py --init-key`, whose public part is `assets/update_public_key.hex`. Without this secret, KINO announces the new version and offers to download it.

This repository is private: releases are published in the public [kino-releases](https://github.com/isleepverywellatnight/kino-releases) repository, which KINO's downloads, install scripts and updates use. The CI writes there with the `KINORELEASE` secret: a fine-grained token limited to kino-releases, with Contents in read and write.

## Building on your machine

Python 3.12 is required. The player's libraries are copied into `vendor/` (ignored by git).

### macOS (Apple Silicon)

libmpv is taken from [IINA](https://iina.io), installed in `/Applications`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python src/desktop.py                     # run from the sources
.venv/bin/pyinstaller --noconfirm packaging/kino_macos.spec
packaging/make_dmg.sh                               # dist/KINO-macOS.dmg
```

### Windows

mpv is downloaded at a pinned version whose checksum is verified; [7-Zip](https://7-zip.org) extracts it. The installer is built with [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python tools\fetch_mpv_windows.py
.venv\Scripts\python src\desktop.py                 # run from the sources
.venv\Scripts\pyinstaller --noconfirm packaging\kino_windows.spec
iscc /DAppVersion=1.4.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

A built app checks what it contains with `--self-check`.

## Tests

The CI runs both on every push. The Windows test (workflow « Windows UI ») starts the real KINO, drives it through WebView2's DevTools port with real clicks and drags, and checks the window, the player and the original titles.

```bash
python3 -m pytest tests             # updates, translations
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## Translations

The interface is written in French. `web/js/i18n.js` translates it as it is displayed, with one dictionary per language, `web/js/i18n/<lang>.js`, mapping each French text to its translation (`{}` marks a variable part). A missing text shows in English, or in French for English.

```bash
python3 tools/i18n.py extract       # web/ and src/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

To add a language: create `web/js/i18n/<code>.js` like the others, then add the code to `LANGUAGES` (`web/js/i18n.js`) and `UI_LANGUAGES` (`src/config.py`).

## Contents of `packaging/`

| File | Role |
|---|---|
| `kino_macos.spec`, `kino_windows.spec` | Builds the app with PyInstaller |
| `make_dmg.sh` | macOS disk image |
| `kino.iss` | One-click Windows installer (Inno Setup) |
| `install.sh`, `install.ps1` | One-command install, from the latest release |
| `update-helper/` | macOS helper that replaces KINO.app during an update |
