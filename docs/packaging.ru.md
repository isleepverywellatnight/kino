# Сборка и публикация KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · **Русский** · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Эти шаги нужны для разработки. Чтобы пользоваться KINO, достаточно его скачать (см. [README](README.ru.md)).

## Публикация версии

Измените `version` в `Cargo.toml` ([workspace.package]) и отправьте в `main` — это всё. Если у версии в `main` ещё нет релиза, GitHub Actions (`.github/workflows/build.yml`) собирает оба приложения, тестирует их, публикует релиз с тегом `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`), а затем переустанавливает его на чистых Mac и Windows командами из README.

Чтобы KINO сам устанавливал новые версии, CI подписывает обновления секретом `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): это содержимое закрытого ключа, созданного `cargo run -p kino --bin kino-release -- --init-key`; его открытая часть — `assets/update_public_key.hex`. Без этого секрета KINO сообщает о новой версии и предлагает её скачать.

Релизы публикуются в этом репозитории; ими пользуются загрузки, скрипты установки и обновления KINO. CI публикует со своим `GITHUB_TOKEN`: другие секреты не нужны.

## Сборка на своём компьютере

KINO собирается на Rust (версия из `rust-toolchain.toml`); Python 3.12 нужен только инструментам, которые загружают mpv и FFmpeg. Библиотеки плеера копируются в `vendor/` (игнорируется git).

### macOS (Apple Silicon)

libmpv берётся из [IINA](https://iina.io), установленного в `/Applications`.

```bash
python3.12 -m venv .venv && .venv/bin/pip install imageio-ffmpeg
.venv/bin/python tools/vendor_libmpv.py               # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh "$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

mpv скачивается в фиксированной версии с проверкой контрольной суммы; распаковывает его [7-Zip](https://7-zip.org). Установщик собирается с помощью [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
py -3.12 -m venv .venv; .venv\Scripts\pip install imageio-ffmpeg
.venv\Scripts\python tools\fetch_mpv_windows.py
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1 (.venv\Scripts\python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Собранное приложение проверяет своё содержимое командой `--self-check`.

## Тесты

CI запускает оба при каждом push. Тест Windows (workflow «Windows UI») запускает настоящий KINO, управляет им через порт DevTools WebView2 настоящими щелчками и перетаскиваниями и проверяет окно, плеер и оригинальные названия.

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## Переводы

Интерфейс написан на французском. `web/js/i18n.js` переводит его при отображении с помощью словаря для каждого языка, `web/js/i18n/<язык>.js`, где каждому французскому тексту сопоставлен перевод (`{}` обозначает переменную часть). Отсутствующий текст показывается на английском, а для английского — на французском.

```bash
python3 tools/i18n.py extract       # web/ and crates/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

Чтобы добавить язык: создайте `web/js/i18n/<код>.js` по образцу остальных, затем добавьте код в `LANGUAGES` (`web/js/i18n.js`) и `UI_LANGUAGES` (`crates/kino/src/config.rs`).

## Содержимое `packaging/`

| Файл | Назначение |
|---|---|
| `build_macos_app.sh`, `build_windows_app.ps1` | Assemble the app from the Rust build |
| `make_dmg.sh` | Образ диска macOS |
| `kino.iss` | Установщик Windows в один клик (Inno Setup) |
| `install.sh`, `install.ps1` | Установка одной командой из последнего релиза |
| `update-helper/` | Помощник macOS, заменяющий KINO.app при обновлении |
