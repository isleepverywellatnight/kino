#!/usr/bin/env python3
"""
KINO Desktop — Application de bureau native multiplateforme (macOS, Windows, Linux)
- Windows : Edge Chromium WebView2 avec accélération matérielle et gestion native Aero.
- macOS : Apple Cocoa + WebKit (WKWebView) natif, fluide et intégré.
"""

import os
import sys
import threading
import time
from pathlib import Path
import webview

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    WM_NCLBUTTONDOWN = 0x00A1
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        "--hide-scrollbars "
        "--autoplay-policy=no-user-gesture-required "
        "--enable-features=PlatformAudioDecoder,MediaFoundationClearPlay,MediaFoundationAudioDecoder "
        "--disable-features=AudioServiceSandbox"
    )
else:
    user32 = None
    WM_NCLBUTTONDOWN = None

import app

_single_instance_mutex = None


def check_single_instance():
    """Vérifie si une instance de KINO est déjà active. Si oui, l'amène au premier plan et quitte proprement."""
    global _single_instance_mutex
    if IS_WIN and user32:
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        mutex_name = "Local\\KINO_SINGLE_INSTANCE_MUTEX"
        _single_instance_mutex = kernel32.CreateMutexW(None, False, mutex_name)
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            hwnd = user32.FindWindowW(None, "KINO")
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
            sys.exit(0)


def find_available_port(start_port=8080, max_tries=10):
    """Trouve un port TCP local disponible pour éviter tout blocage."""
    import socket
    for p in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return start_port


PORT = find_available_port(app.PORT)
app.PORT = PORT

def get_resource_dir():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent

RESOURCE_DIR = get_resource_dir()
ICON_PATH = RESOURCE_DIR / ("kino.png" if IS_MAC else "kino.ico")
if not ICON_PATH.exists():
    ICON_PATH = RESOURCE_DIR / "kino.ico"

server_instance = None
desktop_window = None

HIT_TEST_MAP = {
    "left": 10,         # HTLEFT
    "right": 11,        # HTRIGHT
    "top": 12,          # HTTOP
    "topleft": 13,      # HTTOPLEFT
    "top-left": 13,
    "topright": 14,     # HTTOPRIGHT
    "top-right": 14,
    "bottom": 15,       # HTBOTTOM
    "bottomleft": 16,   # HTBOTTOMLEFT
    "bottom-left": 16,
    "bottomright": 17,  # HTBOTTOMRIGHT
    "bottom-right": 17,
}


def run_server():
    global server_instance
    try:
        server_instance = app.ThreadingHTTPServer(("0.0.0.0", PORT), app.RequestHandler)
        server_instance.serve_forever()
    except Exception:
        try:
            server_instance = app.ThreadingHTTPServer(("127.0.0.1", PORT), app.RequestHandler)
            server_instance.serve_forever()
        except OSError:
            pass


def on_closed():
    global server_instance
    if sys.platform == "win32":
        try:
            import subprocess
            subprocess.run(["taskkill", "/F", "/IM", "mpv.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    if server_instance:
        try:
            server_instance.shutdown()
        except Exception:
            pass


class WindowApi:
    def __init__(self, get_window):
        self._get_window = get_window
        self._saved_mac_frame = None

    def minimize(self):
        w = self._get_window()
        if w:
            w.minimize()

    def toggle_maximize(self):
        w = self._get_window()
        if not w:
            return
        if IS_MAC and getattr(w, "native", None) is not None:
            try:
                from PyObjCTools import AppHelper

                def _mac_zoom():
                    ns_win = w.native
                    ns_win.zoom_(None)
                    w.maximized = bool(ns_win.isZoomed())

                AppHelper.callAfter(_mac_zoom)
                return
            except Exception:
                pass
        if getattr(w, "maximized", False):
            w.restore()
            w.maximized = False
        else:
            w.maximize()
            w.maximized = True

    def start_window_drag(self):
        if not IS_MAC:
            return
        w = self._get_window()
        if not w or getattr(w, "native", None) is None:
            return
        try:
            import AppKit
            from PyObjCTools import AppHelper
            import webview.platforms.cocoa as cocoa

            def _do_drag():
                ns_win = w.native
                bv = cocoa.BrowserView.instances.get(w.uid)
                ev = getattr(bv.webview, "_last_mouse_down_event", None) if (bv and getattr(bv, "webview", None)) else None
                if ev is None:
                    ev = AppKit.NSApplication.sharedApplication().currentEvent()
                if ev is not None:
                    if ev.clickCount() >= 2:
                        ns_win.zoom_(None)
                        w.maximized = bool(ns_win.isZoomed())
                    else:
                        ns_win.performWindowDragWithEvent_(ev)

            AppHelper.callAfter(_do_drag)
        except Exception:
            pass

    def toggle_fullscreen(self):
        w = self._get_window()
        if w:
            try:
                w.toggle_fullscreen()
            except Exception:
                pass

    def close(self):
        w = self._get_window()
        if w:
            w.destroy()

    def start_resize(self, direction):
        if not IS_WIN or not user32:
            return
        w = self._get_window()
        if not w or getattr(w, "maximized", False):
            return
        code = HIT_TEST_MAP.get(direction)
        if not code:
            return
        try:
            form = w.gui.BrowserView.instances.get(w.uid)
            if form:
                hwnd = form.Handle.ToInt64()
                user32.ReleaseCapture()
                user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, code, 0)
        except Exception:
            pass

    def set_bounds(self, x, y, width, height):
        w = self._get_window()
        if not w:
            return
        min_w, min_h = 640, 420
        width = max(min_w, int(width))
        height = max(min_h, int(height))
        x = int(x)
        y = int(y)
        if IS_MAC and getattr(w, "native", None) is not None:
            try:
                import AppKit
                from PyObjCTools import AppHelper

                def _apply():
                    ns_win = w.native
                    screen = AppKit.NSScreen.mainScreen().frame()
                    flipped_y = screen.size.height - y - height
                    rect = AppKit.NSMakeRect(
                        screen.origin.x + x,
                        screen.origin.y + flipped_y,
                        width,
                        height,
                    )
                    ns_win.setFrame_display_(rect, True)

                AppHelper.callAfter(_apply)
                return
            except Exception:
                pass
        try:
            w.move(x, y)
            w.resize(width, height)
        except Exception:
            pass

    def resize(self, width, height):
        w = self._get_window()
        if w:
            try:
                w.resize(int(width), int(height))
            except Exception:
                pass

    def get_state(self):
        w = self._get_window()
        if not w:
            return {}
        try:
            if IS_MAC and getattr(w, "native", None) is not None:
                import AppKit
                frame = w.native.frame()
                screen = AppKit.NSScreen.mainScreen().frame()
                top_left_y = int(screen.size.height - (frame.origin.y + frame.size.height))
                is_fs = bool(getattr(w, "fullscreen", False))
                if hasattr(w.native, "styleMask"):
                    is_fs = is_fs or bool(w.native.styleMask() & (1 << 14))
                return {
                    "maximized": is_zoomed,
                    "fullscreen": is_fs,
                    "width": int(frame.size.width),
                    "height": int(frame.size.height),
                    "x": int(frame.origin.x),
                    "y": top_left_y,
                    "platform": sys.platform,
                }
            return {
                "maximized": bool(getattr(w, "maximized", False)),
                "fullscreen": bool(getattr(w, "fullscreen", False)),
                "width": int(w.width),
                "height": int(w.height),
                "x": int(w.x),
                "y": int(w.y),
                "platform": sys.platform,
            }
        except Exception:
            return {}


window_api_instance = None


def handle_backend_window_action(action):
    global desktop_window, window_api_instance
    if not desktop_window:
        return
    if action == "minimize":
        desktop_window.minimize()
    elif action == "maximize":
        if window_api_instance:
            window_api_instance.toggle_maximize()
        elif getattr(desktop_window, "maximized", False):
            desktop_window.restore()
        else:
            desktop_window.maximize()
    elif action == "fullscreen":
        try:
            desktop_window.toggle_fullscreen()
        except Exception:
            pass
    elif action == "close":
        desktop_window.destroy()
    elif action == "hide":
        try:
            desktop_window.hide()
        except Exception:
            pass
    elif action == "show":
        try:
            desktop_window.show()
            desktop_window.restore()
            if IS_MAC:
                import AppKit
                from PyObjCTools import AppHelper
                AppHelper.callAfter(
                    lambda: AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                )
        except Exception:
            pass


def get_window_geometry():
    global desktop_window
    if desktop_window:
        try:
            return {
                "x": int(desktop_window.x),
                "y": int(desktop_window.y),
                "width": int(desktop_window.width),
                "height": int(desktop_window.height),
            }
        except Exception:
            pass
    return None


EMBEDDED_PLAYER_PANEL = None
EMBEDDED_PLAYER_HWND = None


def get_embedded_hwnd():
    global EMBEDDED_PLAYER_HWND
    return EMBEDDED_PLAYER_HWND


def show_or_hide_embedded_player(show: bool):
    global desktop_window, EMBEDDED_PLAYER_PANEL
    if not desktop_window:
        return
    if IS_WIN:
        try:
            form = desktop_window.gui.BrowserView.instances.get(desktop_window.uid)
            if not form:
                return
            from webview.platforms.winforms import WinForms
            def _toggle():
                if show:
                    if hasattr(form, "browser") and hasattr(form.browser, "webview"):
                        form.browser.webview.Visible = False
                    if EMBEDDED_PLAYER_PANEL:
                        EMBEDDED_PLAYER_PANEL.Visible = True
                        EMBEDDED_PLAYER_PANEL.BringToFront()
                        EMBEDDED_PLAYER_PANEL.Focus()
                else:
                    if EMBEDDED_PLAYER_PANEL:
                        EMBEDDED_PLAYER_PANEL.Visible = False
                    if hasattr(form, "browser") and hasattr(form.browser, "webview"):
                        form.browser.webview.Visible = True
                        form.browser.webview.BringToFront()
                        form.browser.webview.Focus()
            form.Invoke(WinForms.MethodInvoker(_toggle))
        except Exception:
            pass


def configure_window_styles(w):
    global EMBEDDED_PLAYER_PANEL, EMBEDDED_PLAYER_HWND
    if not IS_WIN:
        return
    for _ in range(40):
        time.sleep(0.05)
        try:
            form = w.gui.BrowserView.instances.get(w.uid)
            if form and form.Handle:
                hwnd = form.Handle.ToInt64()
                try:
                    import ctypes
                    dwmapi = ctypes.windll.dwmapi
                    c_int = ctypes.c_int
                    byref = ctypes.byref
                    # DWMWA_BORDER_COLOR = 34 (0xFFFFFFFE = DWMWA_COLOR_NONE, retire la bordure grise)
                    dwmapi.DwmSetWindowAttribute(hwnd, 34, byref(c_int(0xFFFFFFFE)), 4)
                    # DWMWA_WINDOW_CORNER_PREFERENCE = 33 (2 = DWMWCP_ROUND, coins arrondis macos)
                    dwmapi.DwmSetWindowAttribute(hwnd, 33, byref(c_int(2)), 4)
                    # DWMWA_CAPTION_COLOR = 35 (0x000B0909 = #09090b)
                    dwmapi.DwmSetWindowAttribute(hwnd, 35, byref(c_int(0x000B0909)), 4)
                except Exception:
                    pass

                # Creation du panel de lecture video integre MPV
                try:
                    from webview.platforms.winforms import WinForms, ColorTranslator
                    def _init_panel():
                        global EMBEDDED_PLAYER_PANEL, EMBEDDED_PLAYER_HWND
                        panel = WinForms.Panel()
                        panel.Dock = WinForms.DockStyle.Fill
                        panel.BackColor = ColorTranslator.FromHtml("#09090b")
                        panel.Visible = False
                        form.Controls.Add(panel)
                        EMBEDDED_PLAYER_PANEL = panel
                        EMBEDDED_PLAYER_HWND = panel.Handle.ToInt64()
                    form.Invoke(WinForms.MethodInvoker(_init_panel))
                except Exception:
                    pass
                break
        except Exception:
            pass


def install_macos_hooks():
    """Active le glisser natif Cocoa (performWindowDragWithEvent_) pour le Window Tiling macOS."""
    if not IS_MAC:
        return
    try:
        import json
        import AppKit
        import webview.platforms.cocoa as cocoa

        # Désactive le déplacement JS émulé de pywebview afin que seul le drag natif Cocoa opère
        webview.settings["DRAG_REGION_SELECTOR"] = ".__pywebview_disabled_drag__"

        orig_mouse_down = cocoa.BrowserView.WebKitHost.mouseDown_

        def hooked_mouse_down(self, event):
            self._last_mouse_down_event = event
            return orig_mouse_down(self, event)

        cocoa.BrowserView.WebKitHost.mouseDown_ = hooked_mouse_down

        orig_did_receive = cocoa.BrowserView.JSBridge.userContentController_didReceiveScriptMessage_

        def hooked_did_receive(self, controller, message):
            try:
                body = json.loads(message.body())
                if body.get("funcName") == "start_window_drag":
                    w = self.window
                    ns_win = getattr(w, "native", None)
                    bv = cocoa.BrowserView.instances.get(w.uid) if w else None
                    wv = getattr(bv, "webview", None) if bv else None
                    ev = getattr(wv, "_last_mouse_down_event", None) if wv else None
                    if ev is None:
                        ev = AppKit.NSApplication.sharedApplication().currentEvent()
                    if ns_win is not None and ev is not None:
                        if ev.clickCount() >= 2:
                            ns_win.zoom_(None)
                            w.maximized = bool(ns_win.isZoomed())
                        else:
                            ns_win.performWindowDragWithEvent_(ev)
                    return
            except Exception:
                pass
            return orig_did_receive(self, controller, message)

        cocoa.BrowserView.JSBridge.userContentController_didReceiveScriptMessage_ = hooked_did_receive
    except Exception:
        pass


def configure_macos_window(w):
    """Configure les boutons tricolores natifs, le Window Tiling macOS Sequoia et le menu Fenêtre."""
    if not IS_MAC:
        return
    for _ in range(40):
        time.sleep(0.05)
        if getattr(w, "native", None) is not None:
            break
    if getattr(w, "native", None) is None:
        return
    try:
        import AppKit
        from PyObjCTools import AppHelper

        def _setup():
            ns_win = w.native
            if not ns_win:
                return
            try:
                import webview.platforms.cocoa as cocoa
                bv = cocoa.BrowserView.instances.get(w.uid)
                wv = getattr(bv, "webview", None) if bv else None
                if wv is not None and hasattr(wv, "configuration"):
                    cfg_wk = wv.configuration()
                    if hasattr(cfg_wk, "setAllowsPictureInPictureMediaPlayback_"):
                        cfg_wk.setAllowsPictureInPictureMediaPlayback_(True)
                    prefs = cfg_wk.preferences()
                    if prefs is not None:
                        try:
                            prefs.setValue_forKey_(True, "allowsPictureInPictureMediaPlayback")
                            prefs.setValue_forKey_(True, "elementFullscreenEnabled")
                        except Exception:
                            pass
            except Exception:
                pass
            # Style natif complet : Titled + Closable + Miniaturizable + Resizable + FullSizeContentView
            mask = (
                AppKit.NSTitledWindowMask
                | AppKit.NSClosableWindowMask
                | AppKit.NSMiniaturizableWindowMask
                | AppKit.NSResizableWindowMask
                | (1 << 15)  # NSWindowStyleMaskFullSizeContentView
            )
            ns_win.setStyleMask_(mask)
            ns_win.setTitlebarAppearsTransparent_(True)
            ns_win.setTitleVisibility_(1)  # NSWindowTitleHidden

            # Toolbar unifiée invisible pour centrer verticalement les boutons macOS dans le header KINO
            if ns_win.toolbar() is None:
                tb = AppKit.NSToolbar.alloc().initWithIdentifier_("kino.toolbar")
                tb.setShowsBaselineSeparator_(False)
                ns_win.setToolbar_(tb)
            if hasattr(ns_win, "setToolbarStyle_"):
                ns_win.setToolbarStyle_(3)  # NSWindowToolbarStyleUnified

            # Réaffiche et active les 3 boutons macOS natifs (dont le bouton vert de pré-configuration de taille)
            for btn_id in (
                AppKit.NSWindowCloseButton,
                AppKit.NSWindowMiniaturizeButton,
                AppKit.NSWindowZoomButton,
            ):
                btn = ns_win.standardWindowButton_(btn_id)
                if btn is not None:
                    btn.setHidden_(False)
                    btn.setEnabled_(True)

            # Active le comportement FullScreenPrimary requis pour le menu de disposition au survol du bouton vert
            beh = ns_win.collectionBehavior()
            beh |= getattr(AppKit, "NSWindowCollectionBehaviorFullScreenPrimary", 1 << 7)
            ns_win.setCollectionBehavior_(beh)

            # Ajoute le menu système "Fenêtre" pour activer "Déplacer et redimensionner" (Sequoia Tiling + raccourcis)
            ns_app = AppKit.NSApplication.sharedApplication()
            main_menu = ns_app.mainMenu()
            if main_menu is not None and ns_app.windowsMenu() is None:
                win_menu = AppKit.NSMenu.alloc().initWithTitle_("Fenêtre")
                win_menu.addItemWithTitle_action_keyEquivalent_("Placer dans le Dock", "performMiniaturize:", "m")
                win_menu.addItemWithTitle_action_keyEquivalent_("Réduire/agrandir", "performZoom:", "")
                fs_item = win_menu.addItemWithTitle_action_keyEquivalent_("Plein écran", "toggleFullScreen:", "f")
                fs_item.setKeyEquivalentModifierMask_(AppKit.NSControlKeyMask | AppKit.NSCommandKeyMask)
                win_menu.addItem_(AppKit.NSMenuItem.separatorItem())
                win_menu.addItemWithTitle_action_keyEquivalent_("Tout ramener au premier plan", "arrangeInFront:", "")

                win_menu_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Fenêtre", None, "")
                win_menu_item.setSubmenu_(win_menu)
                main_menu.addItem_(win_menu_item)
                ns_app.setWindowsMenu_(win_menu)
                ns_app.addWindowsItem_title_filename_(ns_win, "KINO", False)

        AppHelper.callAfter(_setup)
    except Exception:
        pass


def save_current_window_bounds():
    global window_api_instance
    if not window_api_instance:
        return
    try:
        st = window_api_instance.get_state()
        if st and not st.get("maximized") and st.get("width", 0) >= 640 and st.get("height", 0) >= 420:
            app.save_config({
                "window_bounds": {
                    "x": int(st["x"]),
                    "y": int(st["y"]),
                    "width": int(st["width"]),
                    "height": int(st["height"]),
                }
            })
    except Exception:
        pass


def main():
    global desktop_window, window_api_instance
    check_single_instance()
    app.WINDOW_ACTION_CALLBACK = handle_backend_window_action
    app.GET_WINDOW_GEOMETRY = get_window_geometry
    app.GET_EMBEDDED_HWND = get_embedded_hwnd
    app.SHOW_EMBEDDED_PLAYER = show_or_hide_embedded_player

    if IS_MAC:
        install_macos_hooks()

    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    time.sleep(0.3)

    api = WindowApi(lambda: desktop_window)
    window_api_instance = api

    cfg = app.load_config()
    bounds = cfg.get("window_bounds") if isinstance(cfg.get("window_bounds"), dict) else {}
    init_w = max(640, int(bounds.get("width", 1280)))
    init_h = max(420, int(bounds.get("height", 820)))
    init_x = int(bounds["x"]) if "x" in bounds and bounds["x"] is not None else None
    init_y = int(bounds["y"]) if "y" in bounds and bounds["y"] is not None else None

    desktop_window = webview.create_window(
        title="KINO",
        url=f"http://127.0.0.1:{PORT}",
        x=init_x,
        y=init_y,
        width=init_w,
        height=init_h,
        min_size=(640, 420),
        frameless=True,
        resizable=True,
        easy_drag=False,
        background_color="#09090b",
        text_select=True,
        js_api=api,
    )
    desktop_window.events.closing += save_current_window_bounds
    desktop_window.events.closed += on_closed

    icon_arg = str(ICON_PATH) if ICON_PATH.exists() else None
    
    if IS_WIN:
        webview.start(configure_window_styles, desktop_window, gui="edgechromium", debug=False, icon=icon_arg)
    elif IS_MAC:
        # Sur macOS, pywebview utilise nativement WebKit / Cocoa avec Window Tiling et Traffic Lights
        webview.start(configure_macos_window, desktop_window, gui="cocoa", debug=False, icon=icon_arg)
    else:
        # Linux (GTK / Qt)
        webview.start(debug=False, icon=icon_arg)


if __name__ == "__main__":
    main()
