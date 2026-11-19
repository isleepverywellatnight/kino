# Construire et publier KINO

[English](../packaging/README.md) · **Français** · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Ces étapes concernent le développement. Pour utiliser KINO, il suffit de le télécharger (voir le [README](README.fr.md)).

## Publier une version

Changez `version` dans `Cargo.toml` ([workspace.package]) et poussez sur `main` : c'est tout. Quand la version de `main` n'a pas encore de release, GitHub Actions (`.github/workflows/build.yml`) construit les trois applications, les teste, publie la release avec son tag `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `KINO-Windows-arm64.exe`, `KINO-Linux-x86_64.AppImage`, `SHA256SUMS.txt`), puis la réinstalle sur un Mac et un Windows vierges avec les commandes du README.

Pour que KINO installe lui-même les nouvelles versions, la CI signe les mises à jour avec le secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions) : le contenu de la clé privée créée par `cargo run -p kino --bin kino-release -- --init-key`, dont la partie publique est `assets/update_public_key.hex`. Sans ce secret, KINO annonce la nouvelle version et propose de la télécharger.

Les versions sont publiées dans ce dépôt ; les téléchargements, les scripts d'installation et les mises à jour de KINO les utilisent. La CI publie avec son propre `GITHUB_TOKEN` : aucun autre secret n'est nécessaire.

## Construire sur sa machine

Rust (la version de `rust-toolchain.toml`) construit KINO ; Node.js exécute les tests de l'interface et l'outil de traduction. Les bibliothèques du lecteur sont copiées dans `vendor/` (ignoré par git).

### macOS (Apple Silicon)

libmpv est reprise d'[IINA](https://iina.io), installé dans `/Applications`.

```bash
tools/fetch_ffmpeg.sh                                 # FFmpeg, pinned and checked, into vendor/
tools/vendor_libmpv.sh                                # libmpv from IINA, into vendor/
cargo run -p kino-desktop                             # run from the sources
cargo build --release --locked -p kino-desktop
packaging/build_macos_app.sh
packaging/make_dmg.sh                                 # dist/KINO-macOS.dmg
```

### Windows

mpv est téléchargé dans une version précise, dont l'empreinte est vérifiée ; [7-Zip](https://7-zip.org) sert à l'extraire. L'installateur est construit avec [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
pwsh tools\fetch_ffmpeg.ps1
pwsh tools\fetch_mpv_windows.ps1
cargo run -p kino-desktop                           # run from the sources
cargo build --release --locked -p kino-desktop
pwsh packaging\build_windows_app.ps1
iscc /DAppVersion=1.9.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Pour Windows sur ARM (`KINO-Windows-arm64.exe`), construisez sur un PC ARM64 et passez `-Arch arm64` aux deux scripts de téléchargement et `/DArch=arm64` à `iscc`.

### Linux (x86_64)

L'AppImage se construit sur Ubuntu 22.04, le plus ancien système pris en charge : il fonctionne sur les systèmes qui ont la même glibc ou une plus récente. Il contient WebKitGTK et les plugins GStreamer avec lesquels la page lit les vidéos ; FFmpeg est celui du système de construction.

```bash
sudo apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev patchelf ffmpeg \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
  xvfb imagemagick
tools/fetch_linuxdeploy.sh                            # linuxdeploy et ses plugins, épinglés et vérifiés, dans vendor/
cargo run -p kino-desktop                             # lancer depuis les sources
cargo build --release --locked -p kino-desktop
packaging/build_linux_appimage.sh                     # dist/KINO-Linux-x86_64.AppImage
tools/linux_appimage_test.sh                          # auto-vérification, démarrage et vidéo, sur un écran virtuel
```

Une application construite vérifie ce qu'elle contient avec `--self-check`.

## Tests

La CI lance les deux à chaque push. Le test Windows (workflow « Windows UI ») démarre le vrai KINO, le pilote par le port DevTools de WebView2 avec de vrais clics et glissers, et vérifie la fenêtre, le lecteur et les titres originaux.

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests (server and app)
node --test tests/test_*.js         # the interface
node tests/windows-ui/windows_ui.js  # Windows only: the real window, real mouse input
```

## Traductions

L'interface est écrite en français. Chaque texte français a une clé : `web/js/i18n/fr.js` donne le texte français de chaque clé, et le dictionnaire de chaque langue, `web/js/i18n/<langue>.js`, sa traduction (`{}` marque une partie variable). `web/js/i18n.js` s'en sert à l'affichage. Un texte absent s'affiche en anglais, ou en français pour l'anglais. `tests/test_i18n_catalogs.js` vérifie que chaque texte du code a sa clé et que les dictionnaires ne traduisent que des clés qui existent.

```bash
node tools/i18n.js extract          # new texts of web/ and crates/ -> web/js/i18n/fr.js, each with a key
node tools/i18n.js status           # translated texts, per language
node tools/i18n.js missing de       # texts German still lacks
```

Pour ajouter une langue : créer `web/js/i18n/<code>.js` sur le modèle des autres, puis ajouter le code dans `LANGUAGES` (`web/js/i18n.js`) et `UI_LANGUAGES` (`crates/kino/src/config.rs`).

## Contenu de `packaging/`

| Fichier | Rôle |
|---|---|
| `build_macos_app.sh`, `build_windows_app.ps1` | Assemble the app from the Rust build |
| `make_dmg.sh` | Image disque macOS |
| `kino.iss` | Installateur Windows en un clic (Inno Setup) |
| `install.sh`, `install.ps1` | Installation en une commande, depuis la dernière release |
| `update-helper/` | Assistant macOS qui remplace KINO.app pendant une mise à jour |
