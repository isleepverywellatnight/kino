# Construire et publier KINO

[English](../packaging/README.md) · **Français** · [Deutsch](packaging.de.md) · [Español](packaging.es.md) · [Italiano](packaging.it.md) · [Português](packaging.pt.md) · [Русский](packaging.ru.md) · [中文](packaging.zh.md) · [日本語](packaging.ja.md)

Ces étapes concernent le développement. Pour utiliser KINO, il suffit de le télécharger (voir le [README](README.fr.md)).

## Publier une version

Changez `APP_VERSION` dans `src/kino_version.py` et poussez sur `main` : c'est tout. Quand la version de `main` n'a pas encore de release, GitHub Actions (`.github/workflows/build.yml`) construit les deux applications, les teste, publie la release avec son tag `vX.Y.Z` (`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`), puis la réinstalle sur un Mac et un Windows vierges avec les commandes du README.

Pour que KINO installe lui-même les nouvelles versions, la CI signe les mises à jour avec le secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables → Actions) : le contenu de la clé privée créée par `tools/build_update_package.py --init-key`, dont la partie publique est `assets/update_public_key.hex`. Sans ce secret, KINO annonce la nouvelle version et propose de la télécharger.

Les versions sont publiées dans ce dépôt ; les téléchargements, les scripts d'installation et les mises à jour de KINO les utilisent. La CI publie avec son propre `GITHUB_TOKEN` : aucun autre secret n'est nécessaire.

## Construire sur sa machine

Python 3.12 est nécessaire. Les bibliothèques du lecteur sont copiées dans `vendor/` (ignoré par git).

### macOS (Apple Silicon)

libmpv est reprise d'[IINA](https://iina.io), installé dans `/Applications`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python src/desktop.py                     # run from the sources
.venv/bin/pyinstaller --noconfirm packaging/kino_macos.spec
packaging/make_dmg.sh                               # dist/KINO-macOS.dmg
```

### Windows

mpv est téléchargé dans une version précise, dont l'empreinte est vérifiée ; [7-Zip](https://7-zip.org) sert à l'extraire. L'installateur est construit avec [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python tools\fetch_mpv_windows.py
.venv\Scripts\python src\desktop.py                 # run from the sources
.venv\Scripts\pyinstaller --noconfirm packaging\kino_windows.spec
iscc /DAppVersion=1.4.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Une application construite vérifie ce qu'elle contient avec `--self-check`.

## Tests

La CI lance les deux à chaque push. Le test Windows (workflow « Windows UI ») démarre le vrai KINO, le pilote par le port DevTools de WebView2 avec de vrais clics et glissers, et vérifie la fenêtre, le lecteur et les titres originaux.

```bash
python3 -m pytest tests             # updates, translations
python tests/windows_ui.py          # Windows only: the real window, real mouse input
```

## Traductions

L'interface est écrite en français. `web/js/i18n.js` la traduit à l'affichage avec un dictionnaire par langue, `web/js/i18n/<langue>.js`, qui associe chaque texte français à sa traduction (`{}` marque une partie variable). Un texte absent s'affiche en anglais, ou en français pour l'anglais.

```bash
python3 tools/i18n.py extract       # web/ and src/ texts -> web/js/i18n/strings.json
python3 tools/i18n.py status        # translated texts, per language
python3 tools/i18n.py missing de    # texts German still lacks
```

Pour ajouter une langue : créer `web/js/i18n/<code>.js` sur le modèle des autres, puis ajouter le code dans `LANGUAGES` (`web/js/i18n.js`) et `UI_LANGUAGES` (`src/config.py`).

## Contenu de `packaging/`

| Fichier | Rôle |
|---|---|
| `kino_macos.spec`, `kino_windows.spec` | Construction de l'application avec PyInstaller |
| `make_dmg.sh` | Image disque macOS |
| `kino.iss` | Installateur Windows en un clic (Inno Setup) |
| `install.sh`, `install.ps1` | Installation en une commande, depuis la dernière release |
| `update-helper/` | Assistant macOS qui remplace KINO.app pendant une mise à jour |
