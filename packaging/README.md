# Construire et publier KINO

Ces étapes concernent le développement. Pour utiliser KINO, il suffit de le
télécharger (voir le [README](../README.md)).

## Publier une version

Tout se fait sur GitHub Actions (`.github/workflows/build.yml`) :

1. Mettez `APP_VERSION` à jour dans `src/kino_version.py` et poussez sur `main`.
2. Créez le tag correspondant et poussez-le :
   `git tag v1.2.0 && git push origin v1.2.0`.

La CI construit les deux applications, les teste, publie la release
(`KINO-macOS.dmg`, `KINO-Windows.exe`, `SHA256SUMS.txt`), puis réinstalle la
release publiée sur un Mac et un Windows vierges avec les commandes du README.

Pour que KINO installe lui-même les nouvelles versions, la CI signe les mises à
jour avec le secret `KINO_UPDATE_SIGNING_KEY` (Settings → Secrets and variables
→ Actions) : le contenu de la clé privée créée par
`tools/build_update_package.py --init-key`, dont la partie publique est
`assets/update_public_key.hex`. Sans ce secret, KINO annonce la nouvelle
version et propose de la télécharger.

## Construire sur sa machine

Python 3.12 est nécessaire. Les bibliothèques du lecteur sont copiées dans
`vendor/` (ignoré par git).

### macOS (Apple Silicon)

libmpv est reprise d'[IINA](https://iina.io), installé dans `/Applications`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python src/desktop.py                     # lancer depuis le code
.venv/bin/pyinstaller --noconfirm packaging/kino_macos.spec
packaging/make_dmg.sh                               # dist/KINO-macOS.dmg
```

### Windows

mpv est téléchargé dans une version précise, dont l'empreinte est vérifiée ;
[7-Zip](https://7-zip.org) sert à l'extraire. L'installateur est construit avec
[Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python tools\fetch_mpv_windows.py
.venv\Scripts\python src\desktop.py                 # lancer depuis le code
.venv\Scripts\pyinstaller --noconfirm packaging\kino_windows.spec
iscc /DAppVersion=1.2.0 packaging\kino.iss         # dist\KINO-Windows.exe
```

Une application construite vérifie ce qu'elle contient avec `--self-check`.

## Contenu de ce dossier

| Fichier | Rôle |
|---|---|
| `kino_macos.spec`, `kino_windows.spec` | Construction de l'application avec PyInstaller |
| `make_dmg.sh` | Image disque macOS |
| `kino.iss` | Installateur Windows en un clic (Inno Setup) |
| `install.sh`, `install.ps1` | Installation en une commande, depuis la dernière release |
| `update-helper/` | Assistant macOS qui remplace KINO.app pendant une mise à jour |
