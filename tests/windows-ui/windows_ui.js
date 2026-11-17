// KINO's real window on Windows, driven like a person would (GitHub Actions, windows-latest).
//
// KINO (target/release/kino-desktop.exe) runs with WebView2's DevTools port open (KINO_DEVTOOLS_PORT):
// Playwright reads the page through it, while clicks and drags are real mouse input
// sent to Windows, so the window behaves exactly as under a hand. Each check prints
// « CHECK name: PASS|FAIL detail »; screenshots go to the log as base64 JPEG
// (« SCREENSHOT name » … « END SCREENSHOT »). Exit code 1 when a check fails.
//
// Run: cargo build --release -p kino-desktop, tools/fetch_ffmpeg.ps1, then
//   npm ci --prefix tests/windows-ui && node tests/windows-ui/windows_ui.js   (Windows only)
'use strict';
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { spawn, execFileSync } = require('child_process');
const koffi = require('koffi');
const { chromium } = require('playwright-core');

const PROJECT = path.resolve(__dirname, '..', '..');
const PORT = 9333;
const EXPECTED_CHECKS = 22;
const FFMPEG = process.env.KINO_FFMPEG || path.join(PROJECT, 'vendor', 'ffmpeg', 'ffmpeg.exe');

const user32 = koffi.load('user32.dll');
try {
  koffi.load('shcore.dll').func('int __stdcall SetProcessDpiAwareness(int value)')(2); // physical pixels, like KINO
} catch { /* older Windows */ }
const RECT = koffi.struct('RECT', { left: 'int32', top: 'int32', right: 'int32', bottom: 'int32' });
const POINT = koffi.struct('POINT', { x: 'int32', y: 'int32' });
const MONITORINFO = koffi.struct('MONITORINFO', { cbSize: 'uint32', rcMonitor: RECT, rcWork: RECT, dwFlags: 'uint32' });
const CURSORINFO = koffi.struct('CURSORINFO', { cbSize: 'uint32', flags: 'uint32', hCursor: 'intptr_t', ptScreenPos: POINT });
const win = {
  GetWindowRect: user32.func('bool __stdcall GetWindowRect(intptr_t hwnd, _Out_ RECT *rect)'),
  GetClientRect: user32.func('bool __stdcall GetClientRect(intptr_t hwnd, _Out_ RECT *rect)'),
  ClientToScreen: user32.func('bool __stdcall ClientToScreen(intptr_t hwnd, _Inout_ POINT *point)'),
  MonitorFromWindow: user32.func('intptr_t __stdcall MonitorFromWindow(intptr_t hwnd, uint32 flags)'),
  GetMonitorInfoW: user32.func('bool __stdcall GetMonitorInfoW(intptr_t monitor, _Inout_ MONITORINFO *info)'),
  GetCursorInfo: user32.func('bool __stdcall GetCursorInfo(_Inout_ CURSORINFO *info)'),
  SetCursorPos: user32.func('bool __stdcall SetCursorPos(int x, int y)'),
  mouse_event: user32.func('void __stdcall mouse_event(uint32 flags, uint32 dx, uint32 dy, uint32 data, uintptr_t extra)'),
  FindWindowW: user32.func('intptr_t __stdcall FindWindowW(const char16_t *cls, const char16_t *name)'),
  GetWindowLongW: user32.func('int32 __stdcall GetWindowLongW(intptr_t hwnd, int index)'),
};

// Synchronous output: the log stays whole even when the test ends with process.exit
// (stdout to a pipe is asynchronous on Windows).
function say(text) {
  const data = Buffer.from(`${text}\n`);
  for (let done = 0; done < data.length;) {
    try {
      done += fs.writeSync(1, data, done);
    } catch (error) {
      if (error.code !== 'EAGAIN') throw error;
    }
  }
}

const results = [];
function check(name, ok, detail = '') {
  results.push(Boolean(ok));
  say(`CHECK ${name}: ${ok ? 'PASS' : 'FAIL'} ${detail}`);
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
const show = r => `(${r.join(', ')})`;

function windowRect(hwnd) {
  const r = {};
  win.GetWindowRect(hwnd, r);
  return [r.left, r.top, r.right - r.left, r.bottom - r.top];
}

function clientOrigin(hwnd) {
  const p = { x: 0, y: 0 };
  win.ClientToScreen(hwnd, p);
  return [p.x, p.y];
}

function clientSize(hwnd) {
  const r = {};
  win.GetClientRect(hwnd, r);
  return [r.right, r.bottom];
}

function workArea(hwnd) {
  const info = { cbSize: koffi.sizeof(MONITORINFO), rcMonitor: {}, rcWork: {}, dwFlags: 0 };
  win.GetMonitorInfoW(win.MonitorFromWindow(hwnd, 2), info);
  const r = info.rcWork;
  return [r.left, r.top, r.right - r.left, r.bottom - r.top];
}

function cursorShowing() {
  const info = { cbSize: koffi.sizeof(CURSORINFO), flags: 0, hCursor: 0, ptScreenPos: { x: 0, y: 0 } };
  win.GetCursorInfo(info);
  return Boolean(info.flags & 1);
}

// Real mouse input.
async function move(x, y) {
  win.SetCursorPos(Math.trunc(x), Math.trunc(y));
  await sleep(20);
}
const down = () => win.mouse_event(0x0002, 0, 0, 0, 0);
const up = () => win.mouse_event(0x0004, 0, 0, 0, 0);

async function click([x, y]) {
  await move(x, y);
  down();
  await sleep(60);
  up();
  await sleep(250);
}

async function drag([x, y], dx, dy, steps = 12) {
  await move(x, y);
  down();
  await sleep(250); // the bar asks Windows to take over
  for (let i = 1; i <= steps; i++) {
    await move(x + dx * i / steps, y + dy * i / steps);
    await sleep(30);
  }
  await sleep(100);
  up();
  await sleep(400);
}

// The window's pixels on screen (the player is a native window: a page capture would miss it).
function screenshot(name, hwnd) {
  const [x, y, w, h] = windowRect(hwnd);
  const file = path.join(os.tmpdir(), `kino-ui-${process.pid}.jpg`);
  const script = `
Add-Type -AssemblyName System.Drawing
Add-Type -Namespace K -Name Dpi -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[K.Dpi]::SetProcessDPIAware() | Out-Null
$shot = New-Object System.Drawing.Bitmap ${w}, ${h}
[System.Drawing.Graphics]::FromImage($shot).CopyFromScreen(${x}, ${y}, 0, 0, $shot.Size)
$scale = [Math]::Min(1.0, 960.0 / [Math]::Max(${w}, ${h}))
$small = New-Object System.Drawing.Bitmap $shot, ([int](${w} * $scale)), ([int](${h} * $scale))
$jpeg = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$params = New-Object System.Drawing.Imaging.EncoderParameters 1
$params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality), 70L
$small.Save('${file}', $jpeg, $params)`;
  try {
    execFileSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], { stdio: ['ignore', 'ignore', 'inherit'] });
    say(`SCREENSHOT ${name}\n${fs.readFileSync(file).toString('base64')}\nEND SCREENSHOT`);
    fs.rmSync(file, { force: true });
  } catch (error) {
    say(`screenshot ${name} failed: ${error.message}`);
  }
}

function makeVideo(folder) {
  const target = path.join(folder, 'test.mp4');
  execFileSync(FFMPEG, ['-y', '-loglevel', 'error',
    '-f', 'lavfi', '-i', 'testsrc=size=1280x720:rate=25',
    '-f', 'lavfi', '-i', 'sine=frequency=440',
    '-t', '60', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', target], { stdio: 'inherit' });
  return target;
}

// The test video over HTTP, with byte ranges (players seek with them).
function serve(file) {
  const size = fs.statSync(file).size;
  const server = http.createServer((req, res) => {
    if (req.url !== '/test.mp4') { res.writeHead(404).end(); return; }
    const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '');
    let start = 0, end = size - 1;
    if (range) {
      start = range[1] ? Number(range[1]) : size - Number(range[2]);
      end = range[1] && range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
    }
    res.writeHead(range ? 206 : 200, {
      'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes', 'Content-Length': end - start + 1,
      ...(range ? { 'Content-Range': `bytes ${start}-${end}/${size}` } : {}),
    });
    if (req.method === 'HEAD') { res.end(); return; }
    fs.createReadStream(file, { start, end }).pipe(res);
  });
  server.listen(8765, '127.0.0.1');
  server.unref();
  return 'http://127.0.0.1:8765/test.mp4';
}

// Page CSS pixels -> screen pixels.
async function screenPoint(page, hwnd, x, y) {
  const dpr = await page.evaluate(() => window.devicePixelRatio);
  const [ox, oy] = clientOrigin(hwnd);
  return [ox + x * dpr, oy + y * dpr];
}

async function centerOf(page, hwnd, selector) {
  const box = await page.locator(selector).first().boundingBox();
  return screenPoint(page, hwnd, box.x + box.width / 2, box.y + box.height / 2);
}

// A point of the bar that is not a control: where a press should move the window.
function emptyBarPoint(selector) {
  const bar = document.querySelector(selector);
  const r = bar.getBoundingClientRect();
  const y = r.top + r.height / 2;
  for (let x = r.left + 4; x < r.right - 4; x += 4) {
    const el = document.elementFromPoint(x, y);
    if (el && el.closest('.pywebview-drag-region') && !el.closest('.no-drag, button, input, select, textarea, a, label, [role="button"]')) {
      return { x, y };
    }
  }
  return null;
}

async function waitForLayout(page, hwnd) {
  // The page has laid out at the window's new size (before aiming at a button).
  const width = workArea(hwnd)[2] / await page.evaluate(() => devicePixelRatio);
  await page.waitForFunction(w => innerWidth === w, width, { timeout: 5000 });
}

async function pageTargetReady() {
  try {
    const response = await fetch(`http://127.0.0.1:${PORT}/json/list`, { signal: AbortSignal.timeout(2000) });
    const targets = await response.json();
    return targets.some(t => (t.url || '').includes('127.0.0.1') && t.type === 'page');
  } catch {
    return false;
  }
}

async function run(hwnd, videoUrl) {
  // Playwright may keep the about:blank it saw before KINO navigated: connect again.
  let browser = null, page = null;
  for (let attempt = 0; attempt < 12 && !page; attempt++) {
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`);
    for (let i = 0; i < 10 && !page; i++) {
      page = browser.contexts().flatMap(ctx => ctx.pages()).find(pg => pg.url().includes('127.0.0.1')) || null;
      if (!page) await sleep(500);
    }
    if (!page) {
      await browser.close();
      await sleep(2000);
    }
  }
  check('KINO page loaded', page, page ? page.url() : '');
  if (!page) {
    screenshot('no-page', hwnd);
    return;
  }
  await page.waitForFunction(() => document.body.classList.contains('is-win-native'), null, { timeout: 30000 });
  await page.waitForTimeout(1500);
  // The test machine's screen is small: the whole window on it.
  const smallWindow = async () => {
    await page.evaluate(() => pywebview.api.set_bounds(20, 20, 900, 620));
  };
  await smallWindow();
  await page.waitForTimeout(1000);
  screenshot('start', hwnd);
  const logo = await centerOf(page, hwnd, 'header .logo');
  for (let i = 0; i < 6; i++) await move(logo[0] + i, logo[1] + i);
  const baselineCursor = cursorShowing();
  say(`cursor shown by Windows in a normal window: ${baselineCursor}`);

  const style = win.GetWindowLongW(hwnd, -16) >>> 0;
  const [cw, ch] = clientSize(hwnd);
  const [, , ww, wh] = windowRect(hwnd);
  check('no native title bar', (style & 0x00C00000) !== 0x00C00000 && cw === ww && ch === wh,
    `style=0x${style.toString(16)} client=${cw}x${ch} window=${ww}x${wh}`);
  check('Windows buttons shown', await page.isVisible('header .win-controls') && !await page.isVisible('header .mac-controls'));
  const box = await page.locator('header .win-controls').boundingBox();
  const inner = await page.evaluate(() => window.innerWidth);
  check('Windows buttons at the right edge', box && Math.abs(box.x + box.width - inner) <= 1,
    `right=${box && box.x + box.width} inner=${inner}`);

  // Titles: the original one, never a translation (Wikidata).
  const titles = await page.evaluate(() => fetch('/api/original-titles', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: [{ id: 'tt0111161' }, { id: 'tt0363163' }, { id: 'tt0113247' }, { id: 'tt0245429' }] }),
  }).then(r => r.json()));
  const got = titles.titles || {};
  check('original titles', got.tt0111161 === 'The Shawshank Redemption' && got.tt0363163 === 'Der Untergang'
    && got.tt0113247 === 'La Haine' && got.tt0245429 === '', JSON.stringify(got));

  // A click on a control inside the bar never moves the window.
  let before = windowRect(hwnd);
  await click(await centerOf(page, hwnd, 'header .search-box input'));
  check('click on the search field keeps the window', same(windowRect(hwnd), before), `${show(before)} -> ${show(windowRect(hwnd))}`);

  // A drag on the bar moves the window by exactly the mouse movement.
  let spot = await page.evaluate(emptyBarPoint, 'header');
  check('empty spot on the bar', spot, JSON.stringify(spot));
  if (spot) {
    before = windowRect(hwnd);
    await drag(await screenPoint(page, hwnd, spot.x, spot.y), 140, 70);
    let after = windowRect(hwnd);
    check('drag moves the window', Math.abs(after[0] - before[0] - 140) <= 3 && Math.abs(after[1] - before[1] - 70) <= 3,
      `${show(before)} -> ${show(after)}`);
    check('drag keeps the size', same(after.slice(2), before.slice(2)));
    await smallWindow(); // back on the small screen
    await page.waitForTimeout(500);

    // Maximize button: the work area, taskbar visible; again: back.
    before = windowRect(hwnd);
    await click(await centerOf(page, hwnd, 'header .win-max'));
    await page.waitForTimeout(600);
    check('maximize fills the work area', same(windowRect(hwnd), workArea(hwnd)), `${show(windowRect(hwnd))} vs ${show(workArea(hwnd))}`);
    check('restore icon when maximized', await page.evaluate(() => document.body.classList.contains('is-maximized')));
    screenshot('maximized', hwnd);
    await waitForLayout(page, hwnd);
    await click(await centerOf(page, hwnd, 'header .win-max'));
    await page.waitForTimeout(400);
    check('restore returns', same(windowRect(hwnd), before), `${show(windowRect(hwnd))} vs ${show(before)}`);

    // Double press on the bar maximizes too.
    await page.evaluate(() => {
      window.__kinoEvents = [];
      for (const type of ['mousedown', 'mouseup', 'click', 'dblclick']) {
        document.addEventListener(type, e => window.__kinoEvents.push(
          `${type}${e.isTrusted ? '' : '(synthetic)'} @${Math.round(performance.now())}`), true);
      }
    });
    spot = await page.evaluate(emptyBarPoint, 'header');
    const point = await screenPoint(page, hwnd, spot.x, spot.y);
    await click(point);
    await move(...point);
    down(); await sleep(50); up();
    await sleep(600);
    check('double press maximizes', same(windowRect(hwnd), workArea(hwnd)),
      `${show(windowRect(hwnd))} events=${JSON.stringify(await page.evaluate(() => window.__kinoEvents))}`);
    if (!same(windowRect(hwnd), workArea(hwnd))) {
      await page.evaluate(() => windowAction('maximize'));
      await sleep(600);
      say(`after windowAction('maximize'): ${show(windowRect(hwnd))}`);
    }
    await waitForLayout(page, hwnd);
    await page.waitForTimeout(300);
    await click(await centerOf(page, hwnd, 'header .win-max'));
    await page.waitForTimeout(600);
    check('restore after double press', !same(windowRect(hwnd), workArea(hwnd)), show(windowRect(hwnd)));
  }

  // The player: its buttons do not move the window.
  await page.evaluate(url => openInAppPlayer(url, 'Test KINO'), videoUrl);
  await page.waitForFunction(() => { const v = document.getElementById('inAppVideo'); return v && v.currentTime > 1; }, null, { timeout: 30000 });
  screenshot('player', hwnd);
  const buttons = await page.evaluate(() => [...document.querySelectorAll('.inapp-hud-top .win-btn')].map(el => {
    const r = el.getBoundingClientRect();
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return { cls: el.className, x: Math.round(r.x), w: Math.round(r.width), top: hit === el || el.contains(hit),
      hit: hit ? (hit.id || hit.className || hit.tagName) : null, inner: innerWidth };
  }));
  check('player shows the three Windows buttons', buttons.length === 3 && buttons.every(b => b.top && b.w > 0), JSON.stringify(buttons));
  before = windowRect(hwnd);
  for (const selector of ['#inAppSubsBtn', '#inAppMoreBtn', '#inAppMoreBtn']) {
    if (await page.isVisible(selector)) {
      await move(...await centerOf(page, hwnd, '#inAppVideo'));
      await click(await centerOf(page, hwnd, selector));
    }
  }
  check('player buttons keep the window', same(windowRect(hwnd), before), `${show(before)} -> ${show(windowRect(hwnd))}`);
  spot = await page.evaluate(emptyBarPoint, '.inapp-hud-top');
  if (spot) {
    before = windowRect(hwnd);
    await drag(await screenPoint(page, hwnd, spot.x, spot.y), 60, 40); // to the right: the cursor cannot go left of the screen
    const after = windowRect(hwnd);
    check('player bar drags the window', Math.abs(after[0] - before[0] - 60) <= 3 && Math.abs(after[1] - before[1] - 40) <= 3,
      `${show(before)} -> ${show(after)}`);
    await smallWindow();
    await page.waitForTimeout(500);
  }

  // Fullscreen: once the controls hide, a mouse move brings the cursor back.
  await page.evaluate(() => toggleInAppFullscreen());
  await page.waitForTimeout(800);
  const [, , fw, fh] = windowRect(hwnd);
  const [, , aw, ah] = workArea(hwnd);
  check('fullscreen covers the screen', fw >= aw && fh >= ah, show(windowRect(hwnd)));
  const [cx, cy] = await centerOf(page, hwnd, '#inAppVideo');
  await move(cx, cy);
  await page.waitForTimeout(4000);
  const idle = await page.evaluate(() => document.getElementById('inAppPlayerOverlay').classList.contains('idle'));
  check('fullscreen hides the controls when idle', idle);
  for (let i = 1; i < 8; i++) {
    await move(cx + i * 6, cy + i * 3);
    await sleep(50);
  }
  await page.waitForTimeout(300);
  const state = await page.evaluate(() => {
    const o = document.getElementById('inAppPlayerOverlay');
    return { idle: o.classList.contains('idle'), cursor: getComputedStyle(o).cursor };
  });
  check('mouse move shows the controls in fullscreen', !state.idle, JSON.stringify(state));
  const shown = cursorShowing();
  check('cursor visible in fullscreen', shown === baselineCursor && state.cursor !== 'none',
    JSON.stringify({ ...state, windows_shows_cursor: shown, baseline: baselineCursor }));
  screenshot('fullscreen-after-move', hwnd);
  await page.evaluate(() => toggleInAppFullscreen());
  await page.waitForTimeout(800);
  await browser.close();
}

async function main() {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'kino-ui-'));
  const videoUrl = serve(makeVideo(work));
  // A home of its own: the test never touches the runner user's KINO data.
  const home = path.join(work, 'home');
  fs.mkdirSync(home);
  const env = { ...process.env, KINO_DEVTOOLS_PORT: String(PORT), USERPROFILE: home, HOME: home };
  const kino = spawn(path.join(PROJECT, 'target', 'release', 'kino-desktop.exe'), [], { env, cwd: PROJECT, stdio: 'inherit' });
  let exitCode = null;
  kino.on('exit', code => { exitCode = code ?? -1; });
  try {
    // Connect once KINO's page is a DevTools target (before, Playwright may keep about:blank).
    for (let i = 0; i < 120 && !await pageTargetReady(); i++) await sleep(1000);
    let hwnd = 0;
    for (let i = 0; i < 30 && !hwnd; i++) {
      hwnd = win.FindWindowW(null, 'KINO');
      if (!hwnd) await sleep(500);
    }
    check('window found', hwnd);
    if (hwnd) await run(hwnd, videoUrl);
  } catch (error) {
    check('test ran to the end', false, error.stack);
  } finally {
    say(`KINO still running at the end: ${exitCode === null} (exit code ${exitCode})`);
    kino.kill();
  }
  say(`${results.filter(Boolean).length}/${results.length} checks passed`);
  // Every check ran (a run cut short is a failure too) and passed.
  process.exit(results.length >= EXPECTED_CHECKS && results.every(Boolean) ? 0 : 1);
}

main();
