# Compilar y publicar KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · **Español** · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Estos pasos son para el desarrollo. Para usar KINO, basta con descargarlo (ver el [README](README.es.md)).

## Publicar una versión

Cambia `version` en `Cargo.toml` ([workspace.package]) y haz push a `main`: eso es todo. Cuando la versión de `main` aún no tiene release, GitHub Actions (`.github/workflows/build.yml`) compila las tres aplicaciones, las prueba, publica la release con su etiqueta `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `KINO-Linux-x86_64.AppImage`, `SHA256SUMS.txt`) y luego la reinstala en un Mac y un Windows limpios con los comandos del README.

Para que KINO instale por sí mismo las nuevas versiones, la CI firma las actualizaciones con el secreto `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): el contenido de la clave privada creada por `cargo run -p kino --bin kino-release -- --init-key`, cuya parte pública es `assets/update_public_key.hex`. Sin este secreto, KINO anuncia la nueva versión y propone descargarla.

Las versiones se publican en este repositorio; las descargas, los scripts de instalación y las actualizaciones de KINO las usan. La CI publica con su propio `GITHUB_TOKEN`: no hace falta ningún otro secreto.

## Compilar en tu equipo

Rust (la versión de `rust-toolchain.toml`) compila KINO; Node.js ejecuta las pruebas de la interfaz y la herramienta de traducción. Las bibliotecas del reproductor se copian en `vendor/` (ignorado por git).

### macOS (Apple Silicon)

libmpv se toma de [IINA](https://iina.io), instalado en `/Applications`.

```bash
tools/fetch_ffmpeg.sh                                 # FFmpeg, pinned and checked, into vendor/
tools/vendor_libmpv.sh                                # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

mpv se descarga en una versión fija cuya suma de comprobación se verifica; [7-Zip](https://7-zip.org) la extrae. El instalador se compila con [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
pwsh tools\fetch_ffmpeg.ps1
pwsh tools\fetch_mpv_windows.ps1
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

### Linux (x86_64)

El AppImage se compila en Ubuntu 22.04, el sistema más antiguo admitido: funciona en sistemas con la misma glibc o una más reciente. Incluye WebKitGTK y los plugins de GStreamer con los que la página reproduce vídeos; FFmpeg es el del sistema de compilación.

```bash
sudo apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev patchelf ffmpeg \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  xvfb imagemagick
tools/fetch_linuxdeploy.sh                            # linuxdeploy y sus plugins, fijados y verificados, en vendor/
cargo run -p kino-desktop                             # ejecutar desde el código fuente
cargo build --release --locked -p kino-desktop
packaging/build_linux_appimage.sh                     # dist/KINO-Linux-x86_64.AppImage
tools/linux_appimage_test.sh                          # autocomprobación, arranque y vídeo, en una pantalla virtual
```

Una aplicación compilada comprueba su contenido con `--self-check`.

## Pruebas

La CI ejecuta ambas en cada push. La prueba de Windows (workflow «Windows UI») inicia el KINO real, lo controla mediante el puerto DevTools de WebView2 con clics y arrastres reales, y comprueba la ventana, el reproductor y los títulos originales.

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
node tests/windows-ui/windows_ui.js  # Windows only: the real window, real mouse input
```

## Traducciones

La interfaz está escrita en francés. Cada texto francés tiene una clave: `web/js/i18n/fr.js` da el texto francés de cada clave y el diccionario de cada idioma, `web/js/i18n/<idioma>.js`, su traducción (`{}` marca una parte variable). `web/js/i18n.js` los usa al mostrar la página. Un texto que falta aparece en inglés, o en francés para el inglés. `tests/test_i18n_catalogs.js` comprueba que cada texto del código tiene su clave y que los diccionarios solo traducen claves existentes.

```bash
node tools/i18n.js extract          # new texts of web/ and crates/ -> web/js/i18n/fr.js, each with a key
node tools/i18n.js status           # translated texts, per language
node tools/i18n.js missing de       # texts German still lacks
```

Para añadir un idioma: crea `web/js/i18n/<código>.js` siguiendo el modelo de los demás y añade el código en `LANGUAGES` (`web/js/i18n.js`) y `UI_LANGUAGES` (`crates/kino/src/config.rs`).

## Contenido de `packaging/`

| Archivo | Función |
|---|---|
| `build_macos_app.sh`, `build_windows_app.ps1` | Assemble the app from the Rust build |
| `make_dmg.sh` | Imagen de disco de macOS |
| `kino.iss` | Instalador de Windows en un clic (Inno Setup) |
| `install.sh`, `install.ps1` | Instalación con un comando, desde la última release |
| `update-helper/` | Asistente de macOS que sustituye KINO.app durante una actualización |
