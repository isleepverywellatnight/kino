// The page asks the app for the window's state one call at a time: a burst of
// pywebview API calls while the window resized crashed KINO on Windows (an
// access violation in pywebview's answer path).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'web/js/app-player.js'), 'utf8');
const code = source.slice(source.indexOf('let windowStateBusy'), source.indexOf("window.addEventListener('resize', syncWindowStateSoon);"));

function load() {
  const calls = { state: 0 };
  const timers = [];
  let release;
  const classes = new Set();
  const context = {
    document: { body: { classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) } } },
    window: { pywebview: { api: { get_state: () => { calls.state += 1; return new Promise(resolve => { release = () => resolve({ maximized: true }); }); } } } },
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout: id => { if (timers[id - 1]) timers[id - 1].fn = null; },
    updateInAppFsBtnUI() {},
  };
  vm.runInNewContext(`${code}; this.api_ = { syncWindowState, syncWindowStateSoon };`, context);
  return { ...context.api_, calls, timers, classes, release: () => release() };
}

test('many requests while one is out: one call now, one more after it', async () => {
  const lib = load();
  for (let i = 0; i < 20; i++) lib.syncWindowState();
  assert.equal(lib.calls.state, 1);
  lib.release();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(lib.calls.state, 2, 'the requests that came meanwhile make a single call');
  lib.release();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(lib.calls.state, 2);
  assert.ok(lib.classes.has('is-maximized'));
});

test('a resize asks once, when the window has settled', () => {
  const lib = load();
  for (let i = 0; i < 30; i++) lib.syncWindowStateSoon();
  assert.equal(lib.calls.state, 0);
  const pending = lib.timers.filter(t => t.fn);
  assert.equal(pending.length, 1);
  assert.equal(pending[0].ms, 150);
  pending[0].fn();
  assert.equal(lib.calls.state, 1);
});
