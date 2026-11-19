// Native player overlay helpers and the choice between native and web players.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const overlay = require('../tools/interface_source.js').inFrench(require('../tools/interface_source.js').scriptText('native-player.js'));
const app = require('../tools/interface_source.js').appSource();

function loadOverlay() {
  const listeners = [];
  const context = { window: { addEventListener: (...args) => listeners.push(args) }, document: {}, localStorage: {} };
  vm.runInNewContext(overlay, context);
  return { NativePlayer: vm.runInContext('NativePlayer', context), listeners };
}

test('times and track names read naturally in French', () => {
  const { NativePlayer } = loadOverlay();
  assert.equal(NativePlayer.formatTime(65), '1:05');
  assert.equal(NativePlayer.formatTime(4599.9), '1:16:39');
  assert.equal(NativePlayer.trackLabel({ type: 'audio', id: '2', lang: 'fre', channels: '6' }), 'Français · 5.1');
  assert.equal(NativePlayer.trackLabel({ type: 'audio', id: '6', lang: 'eng', title: 'Commentaire', channels: '2' }), 'Anglais · Commentaire · stéréo');
  assert.equal(NativePlayer.trackLabel({ type: 'sub', id: '3', lang: '' }), 'Piste 3');
});

test('keyboard shortcuts match the web player and capture keys first', () => {
  const { NativePlayer, listeners } = loadOverlay();
  const key = (k, extra = {}) => NativePlayer.keyAction({ key: k, ...extra });
  assert.deepEqual([...key(' ')], ['toggle']);
  assert.deepEqual([...key('ArrowRight')], ['seek', 10]);
  assert.deepEqual([...key('ArrowLeft', { shiftKey: true })], ['seek', -60]);
  assert.deepEqual([...key('ArrowUp')], ['volumeStep', 5]);
  assert.deepEqual([...key('Escape')], ['escape']);
  assert.equal(key('a'), null);
  assert.ok(listeners.some(([type, , capture]) => type === 'keydown' && capture === true));
});

function loadPlayOffline({ available, enabled, nativeFails = false }) {
  const start = app.indexOf('async function playOfflineDownload(');
  const source = app.slice(start, app.indexOf('\n}\n', start) + 2);
  const calls = [];
  const context = {
    offlineDownloads: { abc: { id: 'abc', available: true, url: '/api/offline/media/abc', filename: 'film.mkv', media: { id: 'tt1', name: 'Film' } } },
    userHistory: [{ id: 'tt1', position: 600 }],
    pollDownloads: async () => {},
    openInAppPlayer: async (...args) => { calls.push(['web', ...args]); },
    NativePlayer: { available, enabled, open: async (...args) => { calls.push(['native', ...args]); if (nativeFails) throw new Error('x'); } },
  };
  vm.runInNewContext(`${source}\nthis.play = playOfflineDownload;`, context);
  return { play: context.play, calls };
}

test('offline films use the native player only when it is available and switched on', async () => {
  let run = loadPlayOffline({ available: true, enabled: true });
  await run.play('abc');
  assert.deepEqual(run.calls.map(call => call[0]), ['native']);
  assert.equal(run.calls[0][3], 600, 'resume position passed on');
  for (const flags of [{ available: false, enabled: true }, { available: true, enabled: false }]) {
    run = loadPlayOffline(flags);
    await run.play('abc');
    assert.deepEqual(run.calls.map(call => call[0]), ['web']);
  }
  run = loadPlayOffline({ available: true, enabled: true, nativeFails: true });
  await run.play('abc');
  assert.deepEqual(run.calls.map(call => call[0]), ['native', 'web'], 'falls back to the web player');
});

test('the setting is shown only where the native player exists and is saved as a boolean', () => {
  const html = require('../tools/interface_source.js').indexHtml();
  assert.match(html, /id="cfgNativePlayerField" style="display:none;/);
  assert.ok(require('../tools/interface_source.js').interfaceScripts().includes('native-player.js'));
  assert.match(app, /nativeField\.style\.display = cfg\.native_player_available === true \? 'flex' : 'none'/);
  assert.match(app, /native_player: nativeToggle && document\.getElementById\('cfgNativePlayerField'\)\?\.style\.display !== 'none'/);
});
