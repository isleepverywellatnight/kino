# KINO bauen und veröffentlichen

[English](../packaging/README.md) · [Français](packaging.fr.md) · **Deutsch** · [Español](packaging.es.md) · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Diese Schritte betreffen die Entwicklung. Um KINO zu nutzen, lade es einfach herunter (siehe [README](README.de.md)).

## Eine Version veröffentlichen

Ändere `version` in `Cargo.toml` ([workspace.package]) und pushe auf `main` – das ist alles. Wenn die Version auf `main` noch keine Release hat, baut GitHub Actions (`.github/workflows/build.yml`) die drei Apps, testet sie, veröffentlicht die Release mit ihrem Tag `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `KINO-Windows-arm64.exe`, `KINO-Linux-x86_64.AppImage`, `KINO-Linux-x86_64.flatpak`, `SHA256SUMS.txt`) und installiert sie dann mit den Befehlen aus der README auf einem frischen Mac und einem frischen Windows.

Damit KINO neue Versionen selbst installiert, signiert die CI die Updates mit dem Secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): dem Inhalt des privaten Schlüssels, den `cargo run -p kino --bin kino-release -- --init-key` erzeugt und dessen öffentlicher Teil `assets/update_public_key.hex` ist. Ohne dieses Secret kündigt KINO die neue Version an und bietet den Download an.

Releases erscheinen in diesem Repository; Downloads, Installationsskripte und Updates von KINO nutzen sie. Die CI veröffentlicht mit ihrem eigenen `GITHUB_TOKEN`: kein weiteres Secret nötig.

## Auf dem eigenen Rechner bauen

Rust (die Version aus `rust-toolchain.toml`) baut KINO; Node.js führt die Oberflächentests und das Übersetzungswerkzeug aus. Die Bibliotheken des Players werden nach `vendor/` kopiert (von git ignoriert).

### macOS (Apple Silicon)

libmpv wird aus [IINA](https://iina.io) übernommen, installiert in `/Applications`.

```bash
tools/fetch_ffmpeg.sh                                 # FFmpeg, pinned and checked, into vendor/
tools/vendor_libmpv.sh                                # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

mpv wird in einer festen Version heruntergeladen, deren Prüfsumme kontrolliert wird; [7-Zip](https://7-zip.org) entpackt sie. Der Installer wird mit [Inno Setup 6](https://jrsoftware.org/isinfo.php) gebaut.

```powershell
pwsh tools\fetch_ffmpeg.ps1
pwsh tools\fetch_mpv_windows.ps1
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Für Windows auf ARM (`KINO-Windows-arm64.exe`) baust du auf einem ARM64-PC und übergibst `-Arch arm64` an beide Download-Skripte und `/DArch=arm64` an `iscc`.

### Linux (x86_64)

Das AppImage wird auf Ubuntu 22.04 gebaut, dem ältesten unterstützten System: Es läuft auf Systemen mit derselben oder einer neueren glibc. Es enthält WebKitGTK und die GStreamer-Plugins, mit denen die Seite Videos abspielt; FFmpeg stammt vom Build-System.

```bash
sudo apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev patchelf ffmpeg \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  xvfb imagemagick
tools/fetch_linuxdeploy.sh                            # linuxdeploy und seine Plugins, fixiert und geprüft, nach vendor/
cargo run -p kino-desktop                             # aus den Quellen starten
cargo build --release --locked -p kino-desktop
packaging/build_linux_appimage.sh                     # dist/KINO-Linux-x86_64.AppImage
tools/linux_appimage_test.sh                          # Selbsttest, Start und Video auf einem virtuellen Bildschirm
```

Das Flatpak (`KINO-Linux-x86_64.flatpak`, GNOME-Laufzeit) enthält denselben Build: `tools/fetch_ffmpeg.sh`, dann `packaging/build_flatpak.sh` (braucht `flatpak`, `flatpak-builder` und Flathub als Benutzer-Remote).

Eine gebaute App prüft ihren Inhalt mit `--self-check`.

## Tests

Die CI führt beide bei jedem Push aus. Der Windows-Test (Workflow „Windows UI“) startet das echte KINO, steuert es über den DevTools-Port von WebView2 mit echten Klicks und Ziehbewegungen und prüft Fenster, Player und Originaltitel.

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
node tests/windows-ui/windows_ui.js  # Windows only: the real window, real mouse input
```

## Übersetzungen

Die Oberfläche ist auf Französisch geschrieben. Jeder französische Text hat einen Schlüssel: `web/js/i18n/fr.js` enthält den französischen Text jedes Schlüssels, das Wörterbuch jeder Sprache, `web/js/i18n/<sprache>.js`, seine Übersetzung (`{}` markiert einen variablen Teil). `web/js/i18n.js` verwendet beide bei der Anzeige. Ein fehlender Text erscheint auf Englisch, bzw. auf Französisch für Englisch. `tests/test_i18n_catalogs.js` prüft, dass jeder Text im Code seinen Schlüssel hat und die Wörterbücher nur vorhandene Schlüssel übersetzen.

```bash
node tools/i18n.js extract          # new texts of web/ and crates/ -> web/js/i18n/fr.js, each with a key
node tools/i18n.js status           # translated texts, per language
node tools/i18n.js missing de       # texts German still lacks
```

Eine Sprache hinzufügen: `web/js/i18n/<code>.js` nach dem Vorbild der anderen anlegen, dann den Code in `LANGUAGES` (`web/js/i18n.js`) und `UI_LANGUAGES` (`crates/kino/src/config.rs`) eintragen.

## Inhalt von `packaging/`

| Datei | Aufgabe |
|---|---|
| `build_macos_app.sh`, `build_windows_app.ps1` | Assemble the app from the Rust build |
| `make_dmg.sh` | macOS-Disk-Image |
| `kino.iss` | Windows-Installer mit einem Klick (Inno Setup) |
| `install.sh`, `install.ps1` | Installation mit einem Befehl aus der neuesten Release |
| `update-helper/` | macOS-Helfer, der KINO.app bei einem Update ersetzt |
