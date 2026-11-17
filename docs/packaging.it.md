# Compilare e pubblicare KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · **Italiano** · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Questi passaggi riguardano lo sviluppo. Per usare KINO basta scaricarlo (vedi il [README](README.it.md)).

## Pubblicare una versione

Cambia `APP_VERSION` in `src/kino_version.py` e fai push su `main`: è tutto. Quando la versione su `main` non ha ancora una release, GitHub Actions (`.github/workflows/build.yml`) compila le due app, le testa, pubblica la release con il tag `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`) e poi la reinstalla su un Mac e un Windows puliti con i comandi del README.

Perché KINO installi da solo le nuove versioni, la CI firma gli aggiornamenti con il secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): il contenuto della chiave privata creata da `tools/build_update_package.py --init-key`, la cui parte pubblica è `assets/update_public_key.hex`. Senza questo secret, KINO annuncia la nuova versione e propone di scaricarla.

Questo repository è privato: le versioni vengono pubblicate nel repository pubblico [kino-releases](https://github.com/isleepverywellatnight/kino-releases), usato da download, script di installazione e aggiornamenti di KINO. La CI vi scrive con il secret `KINORELEASE`: un token fine-grained limitato a kino-releases, con Contents in lettura e scrittura.

## Compilare sul proprio computer

Serve Python 3.12. Le librerie del lettore vengono copiate in `vendor/` (ignorato da git).

### macOS (Apple Silicon)

libmpv viene presa da [IINA](https://iina.io), installato in `/Applications`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python src/desktop.py                     # run from the sources
.venv/bin/pyinstaller --noconfirm packaging/kino_macos.spec
packaging/make_dmg.sh                               # dist/KINO-macOS.dmg
```

### Windows

mpv viene scaricato in una versione fissa, di cui si verifica l'impronta; [7-Zip](https://7-zip.org) lo estrae. L'installer viene creato con [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python tools\fetch_mpv_windows.py
.venv\Scripts\python src\desktop.py                 # run from the sources
.venv\Scripts\pyinstaller --noconfirm packaging\kino_windows.spec
iscc /DAppVersion=1.4.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Un'app compilata verifica il proprio contenuto con `--self-check`.

## Test

La CI li esegue entrambi a ogni push. Il test Windows (workflow «Windows UI») avvia il vero KINO, lo pilota tramite la porta DevTools di WebView2 con clic e trascinamenti reali, e verifica finestra, lettore e titoli originali.

```bash
python3 -m pytest tests             # updates, translations
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## Traduzioni

L'interfaccia è scritta in francese. `web/js/i18n.js` la traduce durante la visualizzazione con un dizionario per lingua, `web/js/i18n/<lingua>.js`, che associa ogni testo francese alla sua traduzione (`{}` indica una parte variabile). Un testo mancante appare in inglese, o in francese per l'inglese.

```bash
python3 tools/i18n.py extract       # web/ and src/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

Per aggiungere una lingua: crea `web/js/i18n/<codice>.js` sul modello degli altri, poi aggiungi il codice in `LANGUAGES` (`web/js/i18n.js`) e `UI_LANGUAGES` (`src/config.py`).

## Contenuto di `packaging/`

| File | Ruolo |
|---|---|
| `kino_macos.spec`, `kino_windows.spec` | Compila l'app con PyInstaller |
| `make_dmg.sh` | Immagine disco macOS |
| `kino.iss` | Installer Windows in un clic (Inno Setup) |
| `install.sh`, `install.ps1` | Installazione con un comando, dall'ultima release |
| `update-helper/` | Assistente macOS che sostituisce KINO.app durante un aggiornamento |
