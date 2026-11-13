# PyInstaller configuration for KINO on Windows (x64): a folder with KINO.exe,
# packaged by installer/kino.iss into a per-user installer.
from pathlib import Path
import runpy

import imageio_ffmpeg
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

project = Path(SPECPATH)
APP_VERSION = runpy.run_path(str(project / "kino_version.py"))["APP_VERSION"]
version_parts = tuple(int(part) for part in APP_VERSION.split(".")) + (0,)

datas = [
    (str(project / "web"), "web"),
    (str(project / "addons"), "addons"),
    (str(project / "kino.png"), "."),
    (str(project / "kino.ico"), "."),
    (str(project / "qrcode.min.js"), "."),
    (str(project / "kino_bridge.lua"), "."),
    (str(project / "update_public_key.hex"), "."),
]
binaries = [(imageio_ffmpeg.get_ffmpeg_exe(), "imageio_ffmpeg/binaries")]
# The player: mpv.exe and its shader compiler, fetched by tools/fetch_mpv_windows.py.
mpv_dir = project / "vendor" / "mpv-windows"
if not (mpv_dir / "mpv.exe").is_file():
    raise SystemExit("vendor/mpv-windows is missing: run tools/fetch_mpv_windows.py")
# As data: copied byte for byte, without PyInstaller's analysis of the executable.
datas += [(str(path), "mpv") for path in sorted(mpv_dir.iterdir()) if path.is_file()]

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_parts, prodvers=version_parts, mask=0x3F, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040C04B0", [
            StringStruct("CompanyName", "KINO"),
            StringStruct("FileDescription", "KINO"),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("InternalName", "KINO"),
            StringStruct("LegalCopyright", "MIT License"),
            StringStruct("OriginalFilename", "KINO.exe"),
            StringStruct("ProductName", "KINO"),
            StringStruct("ProductVersion", APP_VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
    ],
)

a = Analysis(
    [str(project / "desktop.py")],
    pathex=[str(project)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["webview.platforms.winforms", "webview.platforms.edgechromium", "clr", "imageio_ffmpeg",
                   "media_probe", "tonight_mode", "kino_update", "native_player", "native_view", "notifier",
                   "cryptography.hazmat.primitives.asymmetric.ed25519"],
    excludes=["tkinter", "webview.platforms.cocoa", "webview.platforms.gtk", "webview.platforms.qt",
              "webview.platforms.cef", "webview.platforms.android"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="KINO", console=False,
          icon=str(project / "kino.ico"), version=version_info)
collection = COLLECT(exe, a.binaries, a.datas, name="KINO", strip=False, upx=False)
