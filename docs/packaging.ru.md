# Сборка и публикация KINO

[English](../packaging/README.md) · [Français](packaging.fr.md) · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · **Русский** · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Эти шаги нужны для разработки. Чтобы пользоваться KINO, достаточно его скачать (см. [README](README.ru.md)).

## Публикация версии

Измените `version` в `Cargo.toml` ([workspace.package]) и отправьте в `main` — это всё. Если у версии в `main` ещё нет релиза, GitHub Actions (`.github/workflows/build.yml`) собирает все три приложения, тестирует их, публикует релиз с тегом `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `KINO-Windows-arm64.exe`, `KINO-Linux-x86_64.AppImage`, `SHA256SUMS.txt`), а затем переустанавливает его на чистых Mac и Windows командами из README.

Чтобы KINO сам устанавливал новые версии, CI подписывает обновления секретом `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions): это содержимое закрытого ключа, созданного `cargo run -p kino --bin kino-release -- --init-key`; его открытая часть — `assets/update_public_key.hex`. Без этого секрета KINO сообщает о новой версии и предлагает её скачать.

Релизы публикуются в этом репозитории; ими пользуются загрузки, скрипты установки и обновления KINO. CI публикует со своим `GITHUB_TOKEN`: другие секреты не нужны.

## Сборка на своём компьютере

KINO собирается на Rust (версия из `rust-toolchain.toml`); Node.js запускает тесты интерфейса и инструмент перевода. Библиотеки плеера копируются в `vendor/` (игнорируется git).

### macOS (Apple Silicon)

libmpv берётся из [IINA](https://iina.io), установленного в `/Applications`.

```bash
tools/fetch_ffmpeg.sh                                 # FFmpeg, pinned and checked, into vendor/
tools/vendor_libmpv.sh                                # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

mpv скачивается в фиксированной версии с проверкой контрольной суммы; распаковывает его [7-Zip](https://7-zip.org). Установщик собирается с помощью [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
pwsh tools\fetch_ffmpeg.ps1
pwsh tools\fetch_mpv_windows.ps1
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Для Windows на ARM (`KINO-Windows-arm64.exe`) собирайте на ПК с ARM64 и передайте `-Arch arm64` обоим скриптам загрузки и `/DArch=arm64` в `iscc`.

### Linux (x86_64)

AppImage собирается на Ubuntu 22.04, самой старой поддерживаемой системе: он работает на системах с той же glibc или более новой. Внутри — WebKitGTK и плагины GStreamer, которыми страница воспроизводит видео; FFmpeg берётся из системы сборки.

```bash
sudo apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev patchelf ffmpeg \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  xvfb imagemagick
tools/fetch_linuxdeploy.sh                            # linuxdeploy и его плагины, закреплённые и проверенные, в vendor/
cargo run -p kino-desktop                             # запуск из исходников
cargo build --release --locked -p kino-desktop
packaging/build_linux_appimage.sh                     # dist/KINO-Linux-x86_64.AppImage
tools/linux_appimage_test.sh                          # самопроверка, запуск и видео на виртуальном экране
```

Собранное приложение проверяет своё содержимое командой `--self-check`.

## Тесты

CI запускает оба при каждом push. Тест Windows (workflow «Windows UI») запускает настоящий KINO, управляет им через порт DevTools WebView2 настоящими щелчками и перетаскиваниями и проверяет окно, плеер и оригинальные названия.

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
node tests/windows-ui/windows_ui.js  # Windows only: the real window, real mouse input
```

## Переводы

Интерфейс написан на французском. У каждого французского текста есть ключ: `web/js/i18n/fr.js` содержит французский текст каждого ключа, а словарь каждого языка, `web/js/i18n/<язык>.js`, — его перевод (`{}` обозначает изменяемую часть). `web/js/i18n.js` использует их при отображении. Отсутствующий текст показывается на английском, а для английского — на французском. `tests/test_i18n_catalogs.js` проверяет, что у каждого текста в коде есть ключ и что словари переводят только существующие ключи.

```bash
node tools/i18n.js extract          # new texts of web/ and crates/ -> web/js/i18n/fr.js, each with a key
node tools/i18n.js status           # translated texts, per language
node tools/i18n.js missing de       # texts German still lacks
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
