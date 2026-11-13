"""KINO's window on Windows: no native title bar, KINO draws its own buttons.

The window is a borderless WinForms form (pywebview's frameless mode). This module
gives it back what a title bar gave, without drawing one:
- minimize and restore from the taskbar (WS_MINIMIZEBOX | WS_SYSMENU, no caption);
- maximize within the screen's work area, so the taskbar stays visible (a
  borderless form maximized by Windows would cover it);
- dragging by Windows itself (WM_NCLBUTTONDOWN on HTCAPTION): exact at any display
  scale, and a click on a button never moves the window.

Every function must run on the window's thread (desktop.run_on_ui).
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2
MONITOR_DEFAULTTONEAREST = 2

# hwnd -> (x, y, width, height) before KINO maximized it.
_restore_rect = {}


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _long(value):
    return ctypes.c_long(value & 0xFFFFFFFF).value


def window_rect(hwnd):
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def work_area(hwnd):
    """(x, y, width, height) of the screen the window is on, without the taskbar."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _place(hwnd, rect):
    x, y, width, height = rect
    user32.SetWindowPos(wintypes.HWND(hwnd), None, int(x), int(y), int(width), int(height),
                        SWP_NOZORDER | SWP_NOACTIVATE)


def frameless_styles(hwnd):
    """No caption and no sizing frame, but the taskbar can still minimize and restore it."""
    hwnd_t = wintypes.HWND(hwnd)
    style = user32.GetWindowLongW(hwnd_t, GWL_STYLE)
    style = (style & ~(WS_CAPTION | WS_THICKFRAME | WS_MAXIMIZEBOX)) | WS_MINIMIZEBOX | WS_SYSMENU
    user32.SetWindowLongW(hwnd_t, GWL_STYLE, _long(style))
    exstyle = user32.GetWindowLongW(hwnd_t, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd_t, GWL_EXSTYLE, _long(exstyle | WS_EX_APPWINDOW))
    user32.SetWindowPos(hwnd_t, None, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    try:
        # Windows 11: rounded corners, no coloured border.
        dwmapi.DwmSetWindowAttribute(hwnd_t, 33, ctypes.byref(ctypes.c_int(2)), 4)
        dwmapi.DwmSetWindowAttribute(hwnd_t, 34, ctypes.byref(ctypes.c_int(0xFFFFFFFE)), 4)
    except OSError:
        pass


def is_maximized(hwnd):
    return hwnd in _restore_rect


def maximize(hwnd):
    if hwnd not in _restore_rect:
        _restore_rect[hwnd] = window_rect(hwnd)
    _place(hwnd, work_area(hwnd))


def restore(hwnd):
    rect = _restore_rect.pop(hwnd, None)
    if rect:
        _place(hwnd, rect)


def toggle_maximize(hwnd):
    if is_maximized(hwnd):
        restore(hwnd)
    else:
        maximize(hwnd)
    return is_maximized(hwnd)


def start_drag(hwnd):
    """Let Windows move the window with the mouse button held. A maximized window first
    returns to its size, under the cursor at the same relative place (as Windows does)."""
    # Only while the button is still down: Windows' move loop would otherwise wait for
    # a release that already happened, and the window would follow the cursor.
    button = 0x02 if user32.GetSystemMetrics(23) else 0x01  # SM_SWAPBUTTON: VK_RBUTTON : VK_LBUTTON
    if not user32.GetAsyncKeyState(button) & 0x8000:
        return
    if is_maximized(hwnd):
        cursor = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        wx, wy, ww, wh = window_rect(hwnd)
        _, _, width, height = _restore_rect.pop(hwnd)
        ratio = (cursor.x - wx) / ww if ww else 0.5
        _place(hwnd, (cursor.x - int(width * ratio), max(wy, cursor.y - (cursor.y - wy)), width, height))
    user32.ReleaseCapture()
    user32.SendMessageW(wintypes.HWND(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, 0)
