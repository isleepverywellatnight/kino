# Compilare e pubblicare KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · **Italiano** · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Questi passaggi riguardano lo sviluppo. Per usare KINO basta scaricarlo (vedi il [README](README.it.md)).

## Pubblicare una versione

Cambia `version` in `Cargo.toml` ([workspace.package]) e fai push su `main`: è tutto. Quando la versione su `main` non ha ancora una release, GitHub Actions (`.github/workflows/build.yml`) compila le due app, le testa, pubblica la release con il tag `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`) e poi la reinstalla su un Mac e un Windows puliti con i comandi del README.

Perché KINO installi da solo le nuove versioni, la CI firma gli aggiornamenti con il secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): il contenuto della chiave privata creata da `cargo run -p kino --bin kino-release -- --init-key`, la cui parte pubblica è `assets/update_public_key.hex`. Senza questo secret, KINO annuncia la nuova versione e propone di scaricarla.

Le versioni vengono pubblicate in questo repository; download, script di installazione e aggiornamenti di KINO le usano. La CI pubblica con il proprio `GITHUB_TOKEN`: nessun altro secret necessario.

## Compilare sul proprio computer

Rust (la versione di `rust-toolchain.toml`) compila KINO; Node.js esegue i test dell'interfaccia e lo strumento di traduzione. Le librerie del lettore vengono copiate in `vendor/` (ignorato da git).

### macOS (Apple Silicon)

libmpv viene presa da [IINA](https://iina.io), installato in `/Applications`.

```bash
tools/fetch_ffmpeg.sh                                 # FFmpeg, pinned and checked, into vendor/
tools/vendor_libmpv.sh                                # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

mpv viene scaricato in una versione fissa, di cui si verifica l'impronta; [7-Zip](https://7-zip.org) lo estrae. L'installer viene creato con [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
pwsh tools\fetch_ffmpeg.ps1
pwsh tools\fetch_mpv_windows.ps1
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Un'app compilata verifica il proprio contenuto con `--self-check`.

## Test

La CI li esegue entrambi a ogni push. Il test Windows (workflow «Windows UI») avvia il vero KINO, lo pilota tramite la porta DevTools di WebView2 con clic e trascinamenti reali, e verifica finestra, lettore e titoli originali.

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
node tests/windows-ui/windows_ui.js  # Windows only: the real window, real mouse input
```

## Traduzioni

L'interfaccia è scritta in francese. Ogni testo francese ha una chiave: `web/js/i18n/fr.js` fornisce il testo francese di ogni chiave e il dizionario di ogni lingua, `web/js/i18n/<lingua>.js`, la sua traduzione (`{}` indica una parte variabile). `web/js/i18n.js` li usa durante la visualizzazione. Un testo mancante appare in inglese, o in francese per l'inglese. `tests/test_i18n_catalogs.js` verifica che ogni testo del codice abbia la sua chiave e che i dizionari traducano solo chiavi esistenti.

```bash
node tools/i18n.js extract          # new texts of web/ and crates/ -> web/js/i18n/fr.js, each with a key
node tools/i18n.js status           # translated texts, per language
node tools/i18n.js missing de       # texts German still lacks
```

Per aggiungere una lingua: crea `web/js/i18n/<codice>.js` sul modello degli altri, poi aggiungi il codice in `LANGUAGES` (`web/js/i18n.js`) e `UI_LANGUAGES` (`crates/kino/src/config.rs`).

## Contenuto di `packaging/`

| File | Ruolo |
|---|---|
| `build_macos_app.sh`, `build_windows_app.ps1` | Assemble the app from the Rust build |
| `make_dmg.sh` | Immagine disco macOS |
| `kino.iss` | Installer Windows in un clic (Inno Setup) |
| `install.sh`, `install.ps1` | Installazione con un comando, dall'ultima release |
| `update-helper/` | Assistente macOS che sostituisce KINO.app durante un aggiornamento |
