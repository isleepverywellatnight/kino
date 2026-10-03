# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

project_root = Path.cwd()

# Fichiers de données à inclure
datas = [
    (str(project_root / 'kino.ico'), '.'),
    (str(project_root / 'kino.png'), '.'),
    (str(project_root / 'qrcode.min.js'), '.'),
    (str(project_root / 'kino_bridge.lua'), '.'),
    (str(project_root / 'addons'), 'addons'),
    (str(project_root / 'web'), 'web'),
]

# Dépendances cachées requises pour pywebview sous Windows (Edge WebView2 / pythonnet)
hiddenimports = [
    'config',
    'meta_engine',
    'debrid_engine',
    'player_engine',
    'kino_db',
    'torrent_engine',
    'remote_controller',
    'trakt_engine',
    'addon_manager',
    'anime_engine',
    'community_lists',
    'intro_engine',
    'discord_rpc',
    'app',
    'sqlite3',
    'clr',
    'pythonnet',
    'clr_loader',
    'webview',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
]

a = Analysis(
    ['desktop.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KINO',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / 'kino.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KINO',
)
