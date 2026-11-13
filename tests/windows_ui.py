"""KINO's real window on Windows, driven like a person would (GitHub Actions, windows-latest).

KINO runs from the sources with WebView2's DevTools port open (KINO_DEVTOOLS_PORT):
Playwright reads the page through it, while clicks and drags are real mouse input
sent to Windows, so the window behaves exactly as under a hand. Each check prints
« CHECK name: PASS|FAIL detail »; screenshots go to the log as base64 JPEG
(« SCREENSHOT name » … « END SCREENSHOT »). Exit code 1 when a check fails.

Run: python tests/windows_ui.py   (Windows only; needs playwright and pillow)
"""
import base64
import ctypes
import functools
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PORT = 9333
user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels, like KINO
except OSError:
    pass

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"CHECK {name}: {'PASS' if ok else 'FAIL'} {detail}", flush=True)


def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def client_origin(hwnd):
    p = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y


def client_size(hwnd):
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right, r.bottom


def work_area(hwnd):
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def cursor_showing():
    class CURSORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    user32.GetCursorInfo(ctypes.byref(info))
    return bool(info.flags & 1)


# Real mouse input.
def move(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.02)


def down():
    user32.mouse_event(0x0002, 0, 0, 0, 0)


def up():
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def click(x, y):
    move(x, y)
    down()
    time.sleep(0.06)
    up()
    time.sleep(0.25)


def drag(x, y, dx, dy, steps=12):
    move(x, y)
    down()
    time.sleep(0.25)  # the bar asks Windows to take over
    for i in range(1, steps + 1):
        move(x + dx * i / steps, y + dy * i / steps)
        time.sleep(0.03)
    time.sleep(0.1)
    up()
    time.sleep(0.4)


def screenshot(name, hwnd):
    from PIL import ImageGrab
    x, y, w, h = window_rect(hwnd)
    image = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).convert("RGB")
    image.thumbnail((960, 960))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=70)
    print(f"SCREENSHOT {name}\n{base64.b64encode(buf.getvalue()).decode()}\nEND SCREENSHOT", flush=True)


def make_video(folder):
    import imageio_ffmpeg
    target = folder / "test.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440",
                    "-t", "60", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(target)], check=True)
    return target


def serve(folder):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:8765/test.mp4"


def screen_point(page, hwnd, x, y):
    """Page CSS pixels -> screen pixels."""
    dpr = page.evaluate("window.devicePixelRatio")
    ox, oy = client_origin(hwnd)
    return ox + x * dpr, oy + y * dpr


def center_of(page, hwnd, selector):
    box = page.locator(selector).first.bounding_box()
    return screen_point(page, hwnd, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


EMPTY_BAR_POINT = """(selector) => {
  // A point of the bar that is not a control: where a press should move the window.
  const bar = document.querySelector(selector);
  const r = bar.getBoundingClientRect();
  const y = r.top + r.height / 2;
  for (let x = r.left + 4; x < r.right - 4; x += 4) {
    const el = document.elementFromPoint(x, y);
    if (el && el.closest('.pywebview-drag-region') && !el.closest('.no-drag, button, input, select, textarea, a, label, [role="button"]')) {
      return {x, y};
    }
  }
  return null;
}"""


def main():
    from playwright.sync_api import sync_playwright

    work = Path(tempfile.mkdtemp(prefix="kino-ui-"))
    make_video(work)
    video_url = serve(work)
    env = dict(os.environ, KINO_DEVTOOLS_PORT=str(PORT), KINO_TRACE_FILE=str(work / "trace.txt"))
    kino = subprocess.Popen([sys.executable, str(PROJECT / "src" / "desktop.py")], env=env, cwd=str(PROJECT))
    try:
        for _ in range(90):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
                break
            except OSError:
                time.sleep(1)
        hwnd = 0
        for _ in range(30):
            hwnd = user32.FindWindowW(None, "KINO")
            if hwnd:
                break
            time.sleep(0.5)
        check("window found", hwnd)

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
            page = None
            for _ in range(60):  # the page may still be about:blank
                page = next((pg for ctx in browser.contexts for pg in ctx.pages if "127.0.0.1" in pg.url), None)
                if page:
                    break
                time.sleep(0.5)
            page.wait_for_function("document.body.classList.contains('is-win-native')", timeout=30000)
            page.wait_for_timeout(1500)
            # The test machine's screen is small: the whole window on it.
            page.evaluate("pywebview.api.set_bounds(20, 20, 900, 620)")
            page.wait_for_timeout(1000)
            screenshot("start", hwnd)
            move(*center_of(page, hwnd, "header .logo"))
            for i in range(5):
                move(*[v + i for v in center_of(page, hwnd, "header .logo")])
            baseline_cursor = cursor_showing()
            print(f"cursor shown by Windows in a normal window: {baseline_cursor}", flush=True)

            style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
            cw, ch = client_size(hwnd)
            _, _, ww, wh = window_rect(hwnd)
            check("no native title bar", not (style & 0x00C00000) == 0x00C00000 and (cw, ch) == (ww, wh),
                  f"style={style:#x} client={cw}x{ch} window={ww}x{wh}")
            check("Windows buttons shown", page.is_visible("header .win-controls") and not page.is_visible("header .mac-controls"))
            box = page.locator("header .win-controls").bounding_box()
            inner = page.evaluate("window.innerWidth")
            check("Windows buttons at the right edge", box and abs(box["x"] + box["width"] - inner) <= 1,
                  f"right={box and box['x'] + box['width']} inner={inner}")

            # Titles: the original one, never a translation (Wikidata).
            titles = page.evaluate("""() => fetch('/api/original-titles', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({items: [{id: 'tt0111161'}, {id: 'tt0363163'}, {id: 'tt0113247'}, {id: 'tt0245429'}]})}).then(r => r.json())""")
            got = titles.get("titles", {})
            check("original titles", got.get("tt0111161") == "The Shawshank Redemption" and got.get("tt0363163") == "Der Untergang"
                  and got.get("tt0113247") == "La Haine" and got.get("tt0245429") == "", json.dumps(got, ensure_ascii=False))

            # A click on a control inside the bar never moves the window.
            before = window_rect(hwnd)
            click(*center_of(page, hwnd, "header .search-box input"))
            check("click on the search field keeps the window", window_rect(hwnd) == before, f"{before} -> {window_rect(hwnd)}")

            # A drag on the bar moves the window by exactly the mouse movement.
            spot = page.evaluate(EMPTY_BAR_POINT, "header")
            check("empty spot on the bar", spot, str(spot))
            if spot:
                sx, sy = screen_point(page, hwnd, spot["x"], spot["y"])
                before = window_rect(hwnd)
                drag(sx, sy, 140, 70)
                after = window_rect(hwnd)
                check("drag moves the window", abs(after[0] - before[0] - 140) <= 3 and abs(after[1] - before[1] - 70) <= 3,
                      f"{before} -> {after}")
                check("drag keeps the size", after[2:] == before[2:])

                # Maximize button: the work area, taskbar visible; again: back.
                before = window_rect(hwnd)
                click(*center_of(page, hwnd, "header .win-max"))
                page.wait_for_timeout(400)
                check("maximize fills the work area", window_rect(hwnd) == work_area(hwnd),
                      f"{window_rect(hwnd)} vs {work_area(hwnd)}")
                check("restore icon when maximized", page.evaluate("document.body.classList.contains('is-maximized')"))
                screenshot("maximized", hwnd)
                click(*center_of(page, hwnd, "header .win-max"))
                page.wait_for_timeout(400)
                check("restore returns", window_rect(hwnd) == before, f"{window_rect(hwnd)} vs {before}")

                # Double press on the bar maximizes too.
                spot = page.evaluate(EMPTY_BAR_POINT, "header")
                sx, sy = screen_point(page, hwnd, spot["x"], spot["y"])
                click(sx, sy)
                move(sx, sy)
                down(); time.sleep(0.05); up()
                time.sleep(0.6)
                check("double press maximizes", window_rect(hwnd) == work_area(hwnd), f"{window_rect(hwnd)}")
                click(*center_of(page, hwnd, "header .win-max"))
                page.wait_for_timeout(400)

            # The player: its buttons do not move the window.
            page.evaluate("(url) => openInAppPlayer(url, 'Test KINO')", video_url)
            page.wait_for_function("() => { const v = document.getElementById('inAppVideo'); return v && v.currentTime > 1; }", timeout=30000)
            screenshot("player", hwnd)
            buttons = page.evaluate("""() => [...document.querySelectorAll('.inapp-hud-top .win-btn')].map(el => {
                const r = el.getBoundingClientRect();
                const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                return {cls: el.className, x: Math.round(r.x), w: Math.round(r.width), top: hit === el || el.contains(hit),
                        hit: hit ? (hit.id || hit.className || hit.tagName) : null, inner: innerWidth};
            })""")
            check("player shows the three Windows buttons", len(buttons) == 3 and all(b["top"] and b["w"] > 0 for b in buttons),
                  json.dumps(buttons))
            before = window_rect(hwnd)
            for selector in ("#inAppSubsBtn", "#inAppMoreBtn", "#inAppMoreBtn"):
                if page.is_visible(selector):
                    move(*center_of(page, hwnd, "#inAppVideo"))
                    click(*center_of(page, hwnd, selector))
            check("player buttons keep the window", window_rect(hwnd) == before, f"{before} -> {window_rect(hwnd)}")
            spot = page.evaluate(EMPTY_BAR_POINT, ".inapp-hud-top")
            if spot:
                sx, sy = screen_point(page, hwnd, spot["x"], spot["y"])
                before = window_rect(hwnd)
                drag(sx, sy, -60, 40)
                after = window_rect(hwnd)
                check("player bar drags the window", abs(after[0] - before[0] + 60) <= 3 and abs(after[1] - before[1] - 40) <= 3,
                      f"{before} -> {after}")

            # Fullscreen: once the controls hide, a mouse move brings the cursor back.
            page.evaluate("toggleInAppFullscreen()")
            page.wait_for_timeout(800)
            check("fullscreen covers the screen", window_rect(hwnd)[2:] >= work_area(hwnd)[2:], f"{window_rect(hwnd)}")
            cx, cy = center_of(page, hwnd, "#inAppVideo")
            move(cx, cy)
            page.wait_for_timeout(4000)
            idle = page.evaluate("document.getElementById('inAppPlayerOverlay').classList.contains('idle')")
            check("fullscreen hides the controls when idle", idle)
            for i in range(1, 8):
                move(cx + i * 6, cy + i * 3)
                time.sleep(0.05)
            page.wait_for_timeout(300)
            state = page.evaluate("""() => { const o = document.getElementById('inAppPlayerOverlay');
                return { idle: o.classList.contains('idle'), cursor: getComputedStyle(o).cursor }; }""")
            check("mouse move shows the controls in fullscreen", not state["idle"], json.dumps(state))
            shown = cursor_showing()
            check("cursor visible in fullscreen", shown == baseline_cursor and state["cursor"] != "none",
                  json.dumps({**state, "windows_shows_cursor": shown, "baseline": baseline_cursor}))
            screenshot("fullscreen-after-move", hwnd)
            page.evaluate("toggleInAppFullscreen()")
            page.wait_for_timeout(800)
            browser.close()
    finally:
        kino.terminate()
        try:
            kino.wait(10)
        except subprocess.TimeoutExpired:
            kino.kill()
        trace = work / "trace.txt"
        if not all(results) and trace.exists():
            print("--- Python threads ---")
            print(trace.read_text(encoding="utf-8", errors="replace")[-6000:])
    print(f"{results.count(True)}/{len(results)} checks passed")
    sys.exit(0 if results and all(results) else 1)


if __name__ == "__main__":
    main()
