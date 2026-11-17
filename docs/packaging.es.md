# Compilar y publicar KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · **Español** · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Estos pasos son para el desarrollo. Para usar KINO, basta con descargarlo (ver el [README](README.es.md)).

## Publicar una versión

Cambia `APP_VERSION` en `src/kino_version.py` y haz push a `main`: eso es todo. Cuando la versión de `main` aún no tiene release, GitHub Actions (`.github/workflows/build.yml`) compila las dos aplicaciones, las prueba, publica la release con su etiqueta `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`) y luego la reinstala en un Mac y un Windows limpios con los comandos del README.

Para que KINO instale por sí mismo las nuevas versiones, la CI firma las actualizaciones con el secreto `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): el contenido de la clave privada creada por `tools/build_update_package.py --init-key`, cuya parte pública es `assets/update_public_key.hex`. Sin este secreto, KINO anuncia la nueva versión y propone descargarla.

Las versiones se publican en este repositorio; las descargas, los scripts de instalación y las actualizaciones de KINO las usan. La CI publica con su propio `GITHUB_TOKEN`: no hace falta ningún otro secreto.

## Compilar en tu equipo

Se necesita Python 3.12. Las bibliotecas del reproductor se copian en `vendor/` (ignorado por git).

### macOS (Apple Silicon)

libmpv se toma de [IINA](https://iina.io), instalado en `/Applications`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python src/desktop.py                     # run from the sources
.venv/bin/pyinstaller --noconfirm packaging/kino_macos.spec
packaging/make_dmg.sh                               # dist/KINO-macOS.dmg
```

### Windows

mpv se descarga en una versión fija cuya suma de comprobación se verifica; [7-Zip](https://7-zip.org) la extrae. El instalador se compila con [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python tools\fetch_mpv_windows.py
.venv\Scripts\python src\desktop.py                 # run from the sources
.venv\Scripts\pyinstaller --noconfirm packaging\kino_windows.spec
iscc /DAppVersion=1.4.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Una aplicación compilada comprueba su contenido con `--self-check`.

## Pruebas

La CI ejecuta ambas en cada push. La prueba de Windows (workflow «Windows UI») inicia el KINO real, lo controla mediante el puerto DevTools de WebView2 con clics y arrastres reales, y comprueba la ventana, el reproductor y los títulos originales.

```bash
python3 -m pytest tests             # updates, translations
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## Traducciones

La interfaz está escrita en francés. `web/js/i18n.js` la traduce al mostrarla con un diccionario por idioma, `web/js/i18n/<idioma>.js`, que asocia cada texto francés a su traducción (`{}` marca una parte variable). Un texto que falta se muestra en inglés, o en francés para el inglés.

```bash
python3 tools/i18n.py extract       # web/ and src/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

Para añadir un idioma: crea `web/js/i18n/<código>.js` siguiendo el modelo de los demás y añade el código en `LANGUAGES` (`web/js/i18n.js`) y `UI_LANGUAGES` (`src/config.py`).

## Contenido de `packaging/`

| Archivo | Función |
|---|---|
| `kino_macos.spec`, `kino_windows.spec` | Compila la aplicación con PyInstaller |
| `make_dmg.sh` | Imagen de disco de macOS |
| `kino.iss` | Instalador de Windows en un clic (Inno Setup) |
| `install.sh`, `install.ps1` | Instalación con un comando, desde la última release |
| `update-helper/` | Asistente de macOS que sustituye KINO.app durante una actualización |
