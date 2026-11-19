// Native player by default on macOS: « Passer l'intro », next episode before the
// credits, « Garder hors ligne » and a switch to the built-in player.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const nativeSource = require('../tools/interface_source.js').inFrench(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/native-player.js'), 'utf8'));
const html = require('../tools/interface_source.js').indexHtml();

function loadNative(state, { intro = null, cues = null } = {}) {
  const elements = {};
  const calls = [];
  let appended = false;
  const clock = { now: 5_000_000 };
  const onlineButtons = {};
  const make = id => elements[id] || (elements[id] = {
    id, value: '0', min: '0', max: id === 'nativeVolume' ? '100' : '1000', hidden: id === 'nativeMoreMenu',
    innerHTML: '', textContent: '', dataset: {}, disabled: false,
    style: { props: {}, setProperty(name, value) { this.props[name] = value; }, display: '' },
    attributes: {}, listeners: {}, classList: { add() {}, remove() {} },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    querySelectorAll(selector) {
      if (selector !== '[data-online]') return [];
      return [...this.innerHTML.matchAll(/data-online="(\d+)"/g)].map(([, index]) => ({
        dataset: { online: index }, addEventListener: (type, fn) => { onlineButtons[index] = fn; },
      }));
    },
    contains: () => false, focus() {},
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
      if (url.startsWith('/api/intro-times')) return intro;
      if (url.startsWith('/api/subtitles')) return { subtitles: [{ lang: 'fr', url: 'https://subs/fr', variant: 'sdh' }] };
      if (url.startsWith('/api/subtitle-cues')) return { cues };
      if (url === '/api/download') return { dl_id: 'x', duplicate: false };
      return { ok: true, state };
    },
    setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, clearTimeout() {},
    Date: FakeDate, URLSearchParams,
    downloadMediaContext: media => ({ id: media.id, name: media.name, type: media.type, season: media.season, episode: media.episode }),
  };
  vm.runInNewContext(nativeSource, context);
  return { NativePlayer: context.window.NativePlayer, elements, calls, clock, onlineButtons };
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const tick = async NativePlayer => { NativePlayer.handleRemoteCommand({ action: 'pause' }); await flush(); };
const episode = { id: 'tt1', type: 'series', name: 'Série', season: 1, episode: 4 };
const playlist = [
  { title: 'Série — S01E04', url: 'https://debrid.example/Serie.S01E04.mkv' },
  { title: 'Série — S01E05', url: 'http://127.0.0.1:8080/api/auto-stream?imdb_id=tt1&season=1&episode=5' },
];

test('the intro times are asked for the episode, with the https source only', async () => {
  const state = { time: 5, duration: 2700, paused: false, tracks: [] };
  const { NativePlayer, calls } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, filename: 'Serie.S01E04.mkv', media: episode, playlist });
  await flush();
  const asked = calls.find(([url]) => url.startsWith('/api/intro-times'))[0];
  assert.match(asked, /title=S%C3%A9rie&type=series&season=1&episode=4&url=https%3A%2F%2Fdebrid\.example%2FSerie\.S01E04\.mkv&imdb_id=tt1/);
  await NativePlayer.openStream({ url: playlist[1].url, title: playlist[1].title, media: { ...episode, episode: 5 }, playlist });
  await flush();
  const local = calls.filter(([url]) => url.startsWith('/api/intro-times'))[1][0];
  assert.match(local, /episode=5&url=&imdb_id=tt1/, 'a local next-episode address is not sent on');
  calls.length = 0;
  await NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: { id: 'tt2', type: 'movie' } });
  await flush();
  assert.ok(!calls.some(([url]) => url.startsWith('/api/intro-times')), 'films have no intro button');
  await NativePlayer.close();
});

test('« Passer l\'intro » follows the opening, the recap, then the usual window', async () => {
  const state = { time: 95, duration: 2700, paused: false, tracks: [] };
  const run = async (intro, time, type = 'series') => {
    const { NativePlayer } = loadNative({ ...state, time }, { intro });
    await NativePlayer.openStream({ url: playlist[0].url, title: 'x', media: { ...episode, type } });
    await flush();
    const skip = NativePlayer.introSkip({ ...state, time });
    await NativePlayer.close();
    return skip && JSON.parse(JSON.stringify(skip));
  };
  assert.deepEqual(await run({ op: { start: 90, end: 180 } }, 95), { label: 'Passer l\'intro (→ 3:00)', goto: 180.5 });
  assert.equal(await run({ op: { start: 90, end: 180 } }, 30), null, 'outside the opening: nothing');
  assert.deepEqual(await run({ recap: { start: 0, end: 70 } }, 20), { label: 'Passer le récap (→ 1:10)', goto: 70.5 });
  assert.deepEqual(await run(null, 40), { label: 'Passer l\'intro (+60 s)', seek: 60 });
  assert.deepEqual(await run(null, 40, 'anime'), { label: 'Passer l\'intro (+90 s)', seek: 90 });
  assert.deepEqual(await run({ default_skip: 85 }, 40), { label: 'Passer l\'intro (+85 s)', seek: 85 });
  assert.equal(await run(null, 200), null);
  const { NativePlayer } = loadNative({ ...state, duration: 120 });
  await NativePlayer.openStream({ url: playlist[0].url, title: 'x', media: episode });
  assert.equal(NativePlayer.introSkip({ ...state, duration: 120, time: 40 }), null, 'short videos have no intro');
  await NativePlayer.close();
});

test('pressing the button skips once, and ✕ hides it for the episode', async () => {
  const state = { time: 95, duration: 2700, paused: false, tracks: [] };
  const { NativePlayer, elements, calls } = loadNative(state, { intro: { op: { start: 90, end: 180 } } });
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, media: episode, playlist });
  await flush();
  await tick(NativePlayer);
  assert.equal(elements.nativeSkipCard.style.display, 'flex');
  assert.equal(elements.nativeSkipGo.textContent, 'Passer l\'intro (→ 3:00)');
  calls.length = 0;
  elements.nativeSkipGo.listeners.click();
  await flush();
  assert.deepEqual(calls.find(([url]) => url === '/api/native-player/command')[1], { cmd: 'goto', value: 180.5 });
  await tick(NativePlayer);
  assert.equal(elements.nativeSkipCard.style.display, 'none');
  assert.equal(elements.nativeStatus.textContent, 'Intro passée');
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, media: episode, playlist });
  await flush();
  await tick(NativePlayer);
  elements.nativeSkipClose.listeners.click();
  await tick(NativePlayer);
  assert.equal(elements.nativeSkipCard.style.display, 'none');
  await NativePlayer.close();
});

test('the next episode card comes before the credits', async () => {
  const state = { time: 2600, duration: 2700, paused: false, ended: false, tracks: [] };
  const { NativePlayer, elements } = loadNative(state, { intro: { ed: { start: 2580, end: 2640 } } });
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, media: episode, playlist });
  await flush();
  assert.equal(NativePlayer.nearEnd({ time: 2600, duration: 2700 }), true, 'during the ending');
  assert.equal(NativePlayer.nearEnd({ time: 2681, duration: 2700 }), true, 'last 22 s');
  assert.equal(NativePlayer.nearEnd({ time: 2000, duration: 2700 }), false);
  assert.equal(NativePlayer.nearEnd({ time: 100, duration: 150 }), false, 'a short clip waits for its end');
  assert.equal(NativePlayer.nearEnd({ time: 150, duration: 150, ended: true }), true);
  await tick(NativePlayer);
  assert.equal(elements.nativeNextCard.style.display, 'flex');
  assert.equal(elements.nativeNextTitle.textContent, 'Série — S01E05');
  await NativePlayer.close();
});

test('« Garder hors ligne » keeps a Debrid file with the online subtitle shown', async () => {
  const state = { time: 300, duration: 2700, paused: false, tracks: [] };
  const cues = [{ start: 1, end: 2, text: 'Salut' }];
  const { NativePlayer, elements, calls, onlineButtons } = loadNative(state, { cues });
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, filename: 'Serie.S01E04.mkv', media: episode, playlist });
  assert.equal(elements.nativeKeep.hidden, false);
  elements.nativeMoreBtn.listeners.click({ detail: 1 });
  await flush();
  assert.match(elements.nativeOnlineSubs.innerHTML, /data-online="0">Français · SDH<\/button>/);
  onlineButtons['0']();
  await flush();
  assert.deepEqual(calls.find(([url]) => url === '/api/native-player/subtitle')[1], { url: 'https://subs/fr', label: 'OpenSubtitles · SDH' });
  state.tracks = [{ id: '3', type: 'sub', lang: 'fr', title: 'OpenSubtitles · SDH', external: true, selected: true }];
  await tick(NativePlayer);
  calls.length = 0;
  elements.nativeKeep.listeners.click();
  await flush();
  await flush();
  assert.match(calls[0][0], /^\/api\/subtitle-cues\?url=https%3A%2F%2Fsubs%2Ffr$/);
  const download = calls.find(([url]) => url === '/api/download');
  assert.equal(download[1].url, playlist[0].url);
  assert.equal(download[1].filename, 'Serie.S01E04.mkv');
  assert.deepEqual(download[1].media, { id: 'tt1', name: 'Série', type: 'series', season: 1, episode: 4 });
  assert.deepEqual(download[1].subtitles, { label: 'CC : Français · SDH', lang: 'fr', kind: 'opensubtitles', variant: 'sdh', cues });
  assert.equal(elements.nativeStatus.textContent, 'Ajouté à la bibliothèque hors ligne.');

  state.tracks = [{ id: '1', type: 'sub', lang: 'fre', title: '', selected: true }];
  await tick(NativePlayer);
  calls.length = 0;
  elements.nativeKeep.listeners.click();
  await flush();
  await flush();
  assert.equal(calls.find(([url]) => url === '/api/download')[1].subtitles, null, 'a subtitle inside the file needs no copy');
  await NativePlayer.close();
});

test('local sources and unnamed files cannot be kept from the player', async () => {
  const state = { time: 5, duration: 2700, paused: false, tracks: [] };
  const { NativePlayer, elements } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[1].url, title: playlist[1].title, media: episode, playlist });
  assert.equal(elements.nativeKeep.hidden, true, 'next-episode address');
  await NativePlayer.openStream({ url: 'https://debrid.example/d/abc', title: 'Film', filename: 'abc', media: { id: 'tt2', type: 'movie' } });
  assert.equal(elements.nativeKeep.hidden, true, 'no video file name');
  await NativePlayer.open({ id: 'x', filename: 'film.mkv' }, { id: 'tt2', type: 'movie' }, 0, 'Film');
  assert.equal(elements.nativeKeep.hidden, true, 'already offline');
  await NativePlayer.close();
});

test('« Ouvrir dans le lecteur intégré » hands over at the same moment', async () => {
  const state = { time: 1234.7, duration: 2700, paused: false, tracks: [] };
  const handed = [];
  const { NativePlayer, elements, calls } = loadNative(state);
  await NativePlayer.openStream({ url: playlist[0].url, title: playlist[0].title, media: episode, playlist, fallback: info => handed.push(info) });
  assert.equal(elements.nativeWeb.hidden, false);
  calls.length = 0;
  elements.nativeWeb.listeners.click();
  await flush();
  await flush();
  assert.equal(handed.length, 1);
  assert.equal(handed[0].position, 1234);
  assert.equal(handed[0].first, true);
  assert.equal(NativePlayer.isOpen(), false);
  const order = calls.map(([url]) => url);
  assert.ok(order.indexOf('/api/history-record') < order.indexOf('/api/native-player/close'), 'progress saved first');
  const none = loadNative(state);
  await none.NativePlayer.openStream({ url: playlist[0].url, title: 'x', media: episode });
  assert.equal(none.elements.nativeWeb.hidden, true, 'no built-in player to hand over to');
  await none.NativePlayer.close();
});

test('the setting is now « Lecteur natif (recommandé) »', () => {
  assert.match(html, /Lecteur natif \(recommandé\)<\/label>/);
  assert.doesNotMatch(html, /Lecteur natif \(bêta\)/);
  assert.match(html, /Options → Ouvrir dans le lecteur intégré/);
});
