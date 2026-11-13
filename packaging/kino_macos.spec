# PyInstaller configuration for a self-contained macOS application.
from pathlib import Path
import runpy

import imageio_ffmpeg

project = Path(SPECPATH).parent
source = project / "src"
APP_VERSION = runpy.run_path(str(source / "kino_version.py"))["APP_VERSION"]
# Same layout as the repository: src/paths.py finds them in both places.
datas = [
    (str(project / "web"), "web"),
    (str(project / "addons"), "addons"),
    (str(project / "assets" / "kino.png"), "assets"),
    (str(project / "assets" / "kino.ico"), "assets"),
    (str(project / "assets" / "kino_bridge.lua"), "assets"),
    (str(project / "assets" / "update_public_key.hex"), "assets"),
    (str(project / "packaging" / "update-helper" / "kino-update-helper"), "."),
]
binaries = [(imageio_ffmpeg.get_ffmpeg_exe(), "imageio_ffmpeg/binaries")]
# Native player: libmpv and its dependencies, vendored by tools/vendor_libmpv.py.
libmpv_files = sorted((project / "vendor" / "libmpv").glob("*.dylib"))
if not any(path.name == "libmpv.2.dylib" for path in libmpv_files):
    raise SystemExit("vendor/libmpv is missing: run tools/vendor_libmpv.py")
# At the top level: PyInstaller collects libmpv's dependencies there anyway, so a
# subfolder would hold a second copy of every library (45 MB).
binaries += [(str(path), ".") for path in libmpv_files]

a = Analysis(
    [str(source / "desktop.py")],
    pathex=[str(source)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["webview.platforms.cocoa", "Foundation", "AppKit", "WebKit", "objc", "imageio_ffmpeg", "media_probe", "tonight_mode", "kino_update", "native_player", "native_view", "notifier", "UserNotifications", "PyObjCTools.AppHelper", "cryptography.hazmat.primitives.asymmetric.ed25519"],
    excludes=["tkinter", "webview.platforms.winforms", "webview.platforms.edgechromium"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="KINO", console=False,
          icon=str(project / "assets" / "AppIcon.icns"), argv_emulation=False)
collection = COLLECT(exe, a.binaries, a.datas, name="KINO", strip=False, upx=False)
app = BUNDLE(collection, name="KINO.app", icon=str(project / "assets" / "AppIcon.icns"),
             bundle_identifier="com.kino.desktop", info_plist={
                 "CFBundleShortVersionString": APP_VERSION,
                 "CFBundleVersion": APP_VERSION,
                 "LSMinimumSystemVersion": "14.0",
                 "NSHighResolutionCapable": True,
                 "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True, "NSAllowsArbitraryLoads": True},
                 # « Lire sur la TV »: macOS asks once before KINO looks for the TV on the local network.
                 "NSLocalNetworkUsageDescription": "KINO cherche votre TV sur le réseau local et lui envoie la vidéo.",
             })
