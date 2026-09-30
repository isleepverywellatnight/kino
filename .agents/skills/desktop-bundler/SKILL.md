---
name: desktop-bundler
description: Expert packaging and bundling for Python desktop applications (PyInstaller, pywebview, standalone executables for Windows .exe and macOS .app/.dmg without requiring Python installed).
---

# Desktop Bundler (Python & PyWebView Packaging)

Guide expert pour transformer des applications Python desktop (notamment basées sur `pywebview`, WebKit et Edge WebView2) en exécutables natifs distribuables, autonomes et légers, sans dépendance Python externe.

## 🎯 Objectifs
1. Produire un exécutable autonome `.exe` (Windows) et `.app` / `.dmg` (macOS) en un seul clic ou script reproductible.
2. Éliminer toute console terminale noire intempestive (`--noconsole` / `pythonw`).
3. Embarquer proprement tous les assets : icônes (`.ico`, `.png`, `.icns`), templates HTML/CSS/JS, polices, fichiers de config par défaut.
4. Gérer les dépendances dynamiques et WebView2 (`pythonnet`, `clr_loader`, `pyobjc`, etc.).

---

## 🛠️ Configuration PyInstaller recommandée (Windows)

### 1. Fichier de spécification PyInstaller (`kino.spec`)
Toujours privilégier un fichier `.spec` plutôt que de longues lignes de commande pour garantir la reproductibilité :

```python
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None
project_root = Path.cwd()

# Collecte des fichiers de données nécessaires
added_files = [
    (str(project_root / 'kino.ico'), '.'),
    (str(project_root / 'kino.png'), '.'),
]

# Modules cachés souvent requis par pywebview sur Windows (pythonnet / clr)
hidden = [
    'clr',
    'pythonnet',
    'clr_loader',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
]

a = Analysis(
    ['desktop.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='KINO',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # Pas de console noire !
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / 'kino.ico'),
)
```

### 2. Gestion des chemins d'accès au runtime (`sys._MEIPASS`)
Dans le code Python (`desktop.py`, `app.py`), toujours utiliser une fonction helper pour localiser les ressources, que l'application soit exécutée en mode script ou compilée :

```python
import sys
from pathlib import Path

def get_bundle_dir():
    """Renvoie le répertoire racine des fichiers embarqués (PyInstaller ou local)."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent

RESOURCE_DIR = get_bundle_dir()
ICON_PATH = RESOURCE_DIR / "kino.ico"
```

---

## 🍏 Packaging macOS (App Bundle & DMG)

### 1. Structure du bundle macOS `.app`
Sur macOS, un exécutable autonome doit respecter la structure Apple :
```
KINO.app/
  Contents/
    Info.plist
    MacOS/
      KINO (binaire ou script wrapper exécutable)
    Resources/
      kino.icns
```

### 2. Attributs et permissions critiques
- `chmod +x KINO.app/Contents/MacOS/*`
- Retrait de la quarantaine Gatekeeper pour les tests locaux : `xattr -cr KINO.app`
- Prise en charge des architectures Apple Silicon (M1/M2/M3/M4) et Intel : utiliser un Python universel (`universal2`) lors du build si ciblage multi-architecture.

---

## ⚡ Checklist de vérification avant distribution
- [ ] L'exécutable démarre sans invite de commande (`console=False`).
- [ ] L'icône apparaît correctement dans la barre des tâches / le Dock.
- [ ] Les données persistantes (configs, base de données, cache de téléchargement) sont bien stockées dans le dossier utilisateur (`Path.home() / '.kino'`), et JAMAIS dans le dossier temporaire `_MEIPASS` qui est détruit à la fermeture.
- [ ] Le port local utilisé (ex: 8080) gère les conflits ou réutilise proprement l'adresse `SO_REUSEADDR`.
