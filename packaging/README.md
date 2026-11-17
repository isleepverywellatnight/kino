# Building and publishing KINO

**English** · [Français](../docs/packaging.fr.md) · [Deutsch](../docs/packaging.de.md) · [Español](../docs/packaging.es.md) · [Italiano](../docs/packaging.it.md) · [Português](../docs/packaging.pt.md) · [Русский](../docs/packaging.ru.md) · [中文](../docs/packaging.zh.md) · [日本語](../docs/packaging.ja.md)

These steps are for development. To use KINO, just download it (see the [README](../README.md)).

## Publishing a version

Change `version` in `Cargo.toml` ([workspace.package]) and push to `main`: that's all. When the version on `main` has no release yet, GitHub Actions (`.github/workflows/build.yml`) builds both apps, tests them, publishes the release with its `vX.Y.Z` tag (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`), then reinstalls it on a clean Mac and a clean Windows with the README's commands.

For KINO to install new versions by itself, the CI signs updates with the `KINO_UPDATE_SIGNING_KEY` secret (Settings → Secrets and variables → Actions): the contents of the private key created by `cargo run -p kino --bin kino-release -- --init-key`, whose public part is `assets/update_public_key.hex`. Without this secret, KINO announces the new version and offers to download it.

Releases are published in this repository; KINO's downloads, install scripts and updates use them. The CI publishes with its own `GITHUB_TOKEN`: no other secret is needed.

## Building on your machine

Rust (the version in `rust-toolchain.toml`) builds KINO; Node.js runs the interface tests and the translation tool. The player's libraries are copied into `vendor/` (ignored by git).

### macOS (Apple Silicon)

libmpv is taken from [IINA](https://iina.io), installed in `/Applications`.

```bash
tools/fetch_ffmpeg.sh                                 # FFmpeg, pinned and checked, into vendor/
tools/vendor_libmpv.sh                                # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

mpv is downloaded at a pinned version whose checksum is verified; [7-Zip](https://7-zip.org) extracts it. The installer is built with [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
pwsh tools\fetch_ffmpeg.ps1
pwsh tools\fetch_mpv_windows.ps1
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

A built app checks what it contains with `--self-check`.

## Tests

The CI runs both on every push. The Windows test (workflow « Windows UI ») starts the real KINO, drives it through WebView2's DevTools port with real clicks and drags, and checks the window, the player and the original titles.

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
node tests/windows-ui/windows_ui.js  # Windows only: the real window, real mouse input
```

## Translations

The interface is written in French. `web/js/i18n.js` translates it as it is displayed, with one dictionary per language, `web/js/i18n/<lang>.js`, mapping each French text to its translation (`{}` marks a variable part). A missing text shows in English, or in French for English.

```bash
node tools/i18n.js extract          # web/ and crates/ texts -> web/js/i18n/strings.json
node tools/i18n.js status           # translated texts, per language
node tools/i18n.js missing de       # texts German still lacks
```

To add a language: create `web/js/i18n/<code>.js` like the others, then add the code to `LANGUAGES` (`web/js/i18n.js`) and `UI_LANGUAGES` (`crates/kino/src/config.rs`).

## Contents of `packaging/`

| File | Role |
|---|---|
| `build_macos_app.sh`, `build_windows_app.ps1` | Assemble the app from the Rust build |
| `make_dmg.sh` | macOS disk image |
| `kino.iss` | One-click Windows installer (Inno Setup) |
| `install.sh`, `install.ps1` | One-command install, from the latest release |
| `update-helper/` | macOS helper that replaces KINO.app during an update |
