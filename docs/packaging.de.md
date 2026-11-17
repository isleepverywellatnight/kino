# KINO bauen und veröffentlichen

[English](../packaging/README.md) · [Français](packaging.fr.md) · **Deutsch** · [Español](packaging.es.md) · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Diese Schritte betreffen die Entwicklung. Um KINO zu nutzen, lade es einfach herunter (siehe [README](README.de.md)).

## Eine Version veröffentlichen

Ändere `APP_VERSION` in `src/kino_version.py` und pushe auf `main` – das ist alles. Wenn die Version auf `main` noch keine Release hat, baut GitHub Actions (`.github/workflows/build.yml`) beide Apps, testet sie, veröffentlicht die Release mit ihrem Tag `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`) und installiert sie dann mit den Befehlen aus der README auf einem frischen Mac und einem frischen Windows.

Damit KINO neue Versionen selbst installiert, signiert die CI die Updates mit dem Secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): dem Inhalt des privaten Schlüssels, den `tools/build_update_package.py --init-key` erzeugt und dessen öffentlicher Teil `assets/update_public_key.hex` ist. Ohne dieses Secret kündigt KINO die neue Version an und bietet den Download an.

Releases erscheinen in diesem Repository; Downloads, Installationsskripte und Updates von KINO nutzen sie. Die CI veröffentlicht mit ihrem eigenen `GITHUB_TOKEN`: kein weiteres Secret nötig.

## Auf dem eigenen Rechner bauen

Python 3.12 wird benötigt. Die Bibliotheken des Players werden nach `vendor/` kopiert (von git ignoriert).

### macOS (Apple Silicon)

libmpv wird aus [IINA](https://iina.io) übernommen, installiert in `/Applications`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python src/desktop.py                     # run from the sources
.venv/bin/pyinstaller --noconfirm packaging/kino_macos.spec
packaging/make_dmg.sh                               # dist/KINO-macOS.dmg
```

### Windows

mpv wird in einer festen Version heruntergeladen, deren Prüfsumme kontrolliert wird; [7-Zip](https://7-zip.org) entpackt sie. Der Installer wird mit [Inno Setup 6](https://jrsoftware.org/isinfo.php) gebaut.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python tools\fetch_mpv_windows.py
.venv\Scripts\python src\desktop.py                 # run from the sources
.venv\Scripts\pyinstaller --noconfirm packaging\kino_windows.spec
iscc /DAppVersion=1.4.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Eine gebaute App prüft ihren Inhalt mit `--self-check`.

## Tests

Die CI führt beide bei jedem Push aus. Der Windows-Test (Workflow „Windows UI“) startet das echte KINO, steuert es über den DevTools-Port von WebView2 mit echten Klicks und Ziehbewegungen und prüft Fenster, Player und Originaltitel.

```bash
python3 -m pytest tests             # updates, translations
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## Übersetzungen

Die Oberfläche ist auf Französisch geschrieben. `web/js/i18n.js` übersetzt sie bei der Anzeige mit einem Wörterbuch pro Sprache, `web/js/i18n/<sprache>.js`, das jedem französischen Text seine Übersetzung zuordnet (`{}` markiert einen variablen Teil). Ein fehlender Text erscheint auf Englisch, für Englisch auf Französisch.

```bash
python3 tools/i18n.py extract       # web/ and src/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

Eine Sprache hinzufügen: `web/js/i18n/<code>.js` nach dem Vorbild der anderen anlegen, dann den Code in `LANGUAGES` (`web/js/i18n.js`) und `UI_LANGUAGES` (`src/config.py`) eintragen.

## Inhalt von `packaging/`

| Datei | Aufgabe |
|---|---|
| `kino_macos.spec`, `kino_windows.spec` | Baut die App mit PyInstaller |
| `make_dmg.sh` | macOS-Disk-Image |
| `kino.iss` | Windows-Installer mit einem Klick (Inno Setup) |
| `install.sh`, `install.ps1` | Installation mit einem Befehl aus der neuesten Release |
| `update-helper/` | macOS-Helfer, der KINO.app bei einem Update ersetzt |
