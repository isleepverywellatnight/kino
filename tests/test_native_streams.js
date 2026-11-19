// Native player, step 4: Debrid sources, episodes in a row, hand-over to the
// built-in player when a source never starts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const nativeSource = require('../tools/interface_source.js').inFrench(require('../tools/interface_source.js').scriptText('native-player.js'));
const app = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();

function loadNative(state, { openFails = false } = {}) {
  const elements = {};
  const calls = [];
  let appended = false;
  const clock = { now: 1_000_000 };
  const make = id => elements[id] || (elements[id] = {
    id, value: '0', min: '0', max: id === 'nativeVolume' ? '100' : '1000', hidden: id === 'nativeMoreMenu',
    innerHTML: '', textContent: '', dataset: {}, disabled: false,
    style: { props: {}, setProperty(name, value) { this.props[name] = value; }, display: '' },
    attributes: {}, listeners: {}, classList: { add() {}, remove() {} },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    querySelectorAll: () => [], contains: () => false, focus() {},
  });
  const document = {
    getElementById: id => (id === 'nativePlayer' && !appended ? null : make(id)),
    createElement: () => make('nativePlayer'),
    body: { appendChild: () => { appended = true; }, classList: { contains: () => false } },
    documentElement: { classList: { add() {}, remove() {} } },
    activeElement: null,
  };
  const FakeDate = class extends Date { static now() { return clock.now; } };
  const context = {
    window: { addEventListener() {} }, playerPrefs: {}, document, localStorage: { getItem: () => null, setItem() {} },
    api: async (url, options) => {
      const body = options?.body ? JSON.parse(options.body) : null;
      calls.push([url, body]);
      if (url === '/api/native-player/open' && openFails) throw new Error('Adresse de lecture refusée.');
      return { ok: true, state };
    },
    setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, clearTimeout() {},
    Date: FakeDate, URLSearchParams,
  };
  vm.runInNewContext(nativeSource, context);
  return { NativePlayer: vm.runInContext('NativePlayer', context), elements, calls, clock };
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const playing = () => ({ time: 5, duration: 1500, paused: false, ended: false, idle: false, volume: 100, tracks: [] });
const playlist = [
  { title: 'Série — S01E01', url: 'https://debrid.example/1.mkv' },
  { title: 'Série — S01E02', url: 'http://127.0.0.1:8080/api/auto-stream?imdb_id=tt1&season=1&episode=2' },
  { title: 'Série — S01E03', url: 'http://127.0.0.1:8080/api/auto-stream?imdb_id=tt1&season=1&episode=3' },
];
const media = { id: 'tt1', type: 'series', name: 'Série', season: 1, episode: 1 };

test('a Debrid source opens by its address, at the resume point', async () => {
  const state = playing();
  const { NativePlayer, calls, elements } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[0].url, title: 'Série — S01E01', media, resumeSec: 300, filename: 'Serie.S01E01.mkv', playlist });
  const open = calls.find(([url]) => url === '/api/native-player/open')[1];
  assert.deepEqual(open, { stream_url: 'https://debrid.example/1.mkv', start: 300, volume: 100 });
  assert.equal(NativePlayer.isOpen(), true);
  assert.equal(elements.nativeTitle.textContent, 'Série — S01E01');
  assert.equal(elements.nativePrev.hidden, true, 'first episode: no previous');
  assert.equal(elements.nativeNext.hidden, false);
  await NativePlayer.close();
});

test('a single source has no episode buttons; a playlist starts at the chosen item', async () => {
  const { NativePlayer, elements, calls } = loadNative(playing());
  await NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: { id: 'tt2', type: 'movie' } });
  assert.equal(elements.nativePrev.hidden, true);
  assert.equal(elements.nativeNext.hidden, true);
  await NativePlayer.openStream({ url: playlist[1].url, title: playlist[1].title, media, playlist });
  assert.equal(elements.nativePrev.hidden, false);
  assert.equal(elements.nativeNext.hidden, false);
  await assert.rejects(NativePlayer.openStream({ title: 'sans adresse' }));
  assert.ok(calls.length > 0);
  await NativePlayer.close();
});

test('the end of an episode counts down, then plays the next one', async () => {
  const state = playing();
  const { NativePlayer, elements, calls, clock } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, media, playlist });
  Object.assign(state, { ended: true, paused: true, time: 1500 });
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  assert.equal(elements.nativeNextCard.style.display, 'flex');
  assert.equal(elements.nativeNextCount.textContent, '10');
  assert.equal(elements.nativeNextTitle.textContent, 'Série — S01E02');
  clock.now += 4000;
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  assert.equal(elements.nativeNextCount.textContent, '6');
  calls.length = 0;
  clock.now += 6000;
  Object.assign(state, { ended: true });
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  Object.assign(state, playing());
  await flush();
  const opened = calls.filter(([url]) => url === '/api/native-player/open').map(([, body]) => body.stream_url);
  assert.deepEqual(opened, [playlist[1].url]);
  assert.equal(elements.nativeTitle.textContent, 'Série — S01E02');
  const saved = calls.find(([url]) => url === '/api/history-record');
  assert.equal(saved[1].media.episode, 1, 'the finished episode is saved before moving on');
  await NativePlayer.close();
});

test('« Annuler » keeps the finished episode on screen', async () => {
  const state = playing();
  const { NativePlayer, elements, calls } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, media, playlist });
  Object.assign(state, { ended: true, paused: true });
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  elements.nativeNextCancel.listeners.click();
  assert.equal(elements.nativeNextCard.style.display, 'none');
  assert.equal(calls.filter(([url]) => url === '/api/native-player/open').length, 1);
  await NativePlayer.close();
});

test('previous and next follow the playlist, with the episode number', async () => {
  const state = playing();
  const { NativePlayer, elements, calls } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[1].url, title: playlist[1].title, media: { ...media, episode: 2 }, playlist });
  calls.length = 0;
  NativePlayer.handleRemoteCommand({ action: 'next_ep' });
  await flush();
  assert.equal(elements.nativeTitle.textContent, 'Série — S01E03');
  assert.equal(elements.nativeNext.hidden, true, 'last episode: no next');
  elements.nativePrev.listeners.click();
  await flush();
  elements.nativePrev.listeners.click();
  await flush();
  assert.equal(elements.nativeTitle.textContent, 'Série — S01E01');
  assert.deepEqual(calls.filter(([url]) => url === '/api/native-player/open').map(([, body]) => body.stream_url), [playlist[2].url, playlist[1].url, playlist[0].url]);
  assert.deepEqual({ ...NativePlayer.episodeOf('Série — S02E10 — Titre') }, { season: 2, episode: 10 });
  assert.deepEqual({ ...NativePlayer.episodeOf('Film') }, {});
  await NativePlayer.close();
});

test('a source that never starts goes to the built-in player', async () => {
  const state = { time: 0, duration: 0, paused: false, idle: false, tracks: [] };
  const handed = [];
  const { NativePlayer, calls, clock } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, media, playlist, fallback: info => handed.push(info) });
  clock.now += 500;
  state.idle = true;
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  assert.equal(handed.length, 0, 'mpv may be idle for an instant while it starts');
  clock.now += 1000;
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  assert.equal(handed.length, 1);
  assert.equal(handed[0].first, true);
  assert.equal(handed[0].url, playlist[0].url);
  assert.equal(NativePlayer.isOpen(), false);
  assert.ok(calls.some(([url]) => url === '/api/native-player/close'));
  assert.ok(!calls.some(([url]) => url === '/api/history-record'), 'nothing played: no history');
});

test('a stream that stays silent for 45 s is handed over too', async () => {
  const state = { time: 0, duration: 0, paused: false, idle: false, tracks: [] };
  const handed = [];
  const { NativePlayer, clock } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, media, fallback: info => handed.push(info) });
  clock.now += 46000;
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  assert.equal(handed.length, 1);
});

test('a later episode that fails is handed over as itself', async () => {
  const state = playing();
  const handed = [];
  const { NativePlayer, clock } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, media, playlist, fallback: info => handed.push(info) });
  Object.assign(state, { duration: 0, time: 0, idle: true });
  NativePlayer.handleRemoteCommand({ action: 'next_ep' });
  await flush();
  clock.now += 2000;
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  assert.equal(handed.length, 1);
  assert.equal(handed[0].first, false);
  assert.equal(handed[0].title, 'Série — S01E02');
  assert.equal(handed[0].media.episode, 2);
});

test('a refused address lets the caller use the built-in player', async () => {
  const { NativePlayer } = loadNative(playing(), { openFails: true });
  await assert.rejects(NativePlayer.openStream({ url: 'https://debrid.example/1.mkv', title: 'Film' }));
  assert.equal(NativePlayer.isOpen(), false);
});

test('one-click play and chosen sources try the native player first', () => {
  const helper = app.slice(app.indexOf('async function openNativeStream(options) {'));
  assert.match(helper, /if \(!native\?\.available \|\| !native\.enabled \|\| typeof native\.openStream !== 'function'\) return false;/);
  const oneClick = app.slice(app.indexOf('async function oneClickPlay(params, btn) {'), app.indexOf('// --- French display titles'));
  assert.match(oneClick, /const native = await openNativeStream\(\{ url: res\.stream_url, title: params\.title, media: nativeMedia,\s+resumeSec: res\.resume_sec \|\| 0, filename: releaseName, playlist: pl, fallback: openWeb \}\);\n      if \(!native\) openWeb\(null\);/);
  assert.match(oneClick, /: openInAppPlayer\(res\.stream_url, params\.title, pl, currentMedia, info\?\.position \|\| res\.resume_sec \|\| 0, releaseName, recovery\)\);/, 'the first source keeps the recovery of other sources');
  assert.match(oneClick, /\? openInAppPlayer\(info\.url, info\.title, pl, info\.media, info\.position \|\| 0\)/);
  const chosen = app.slice(app.indexOf('async function openMpv(btn, url, filename, options = null) {'));
  assert.match(chosen, /const native = await openNativeStream\(\{ url, title: filename, media: nativeMedia, resumeSec, filename, playlist: pl, fallback: openWeb \}\);\n    if \(!native\) openWeb\(null\);/);
  assert.match(app, /await NativePlayer\.open\(item, media, resumeSec, title,\n        info => openInAppPlayer\(item\.url, title, null, media, info\?\.position \|\| resumeSec, item\.filename\)\);/);
  assert.match(html, /Lit les films hors ligne et les sources Debrid sans conversion/);
});
