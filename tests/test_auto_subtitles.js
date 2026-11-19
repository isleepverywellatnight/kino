// French subtitles for a film or an episode in its original language: the
// native player reads the tracks, the built-in player reads the release name.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const seriesSource = require('../tools/interface_source.js').inFrench(require('../tools/interface_source.js').scriptText('series-playback.js'));
const nativeSource = require('../tools/interface_source.js').inFrench(require('../tools/interface_source.js').scriptText('native-player.js'));
const app = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();

const SP = (() => { const context = {}; vm.runInNewContext(`${seriesSource}\nthis.SP = SeriesPlayback;`, context); return context.SP; })();
const plain = value => JSON.parse(JSON.stringify(value));
const audio = (id, lang, selected = false, title = '') => ({ type: 'audio', id: String(id), lang, title, selected });
const sub = (id, lang, title = '', selected = false) => ({ type: 'sub', id: String(id), lang, title, selected });

test('the release name tells a French dub', () => {
  for (const name of ['Film.2024.MULTi.1080p.WEB.mkv', 'Film 2024 TRUEFRENCH 2160p', 'Film.2024.VFF.mkv', 'Film.2024.FRENCH.1080p', 'Serie.S01E02.VF.mkv']) {
    assert.equal(SP.releaseHasFrenchAudio(name), true, name);
  }
  for (const name of ['Film.2024.VOSTFR.1080p.mkv', 'Film.2024.SUBFRENCH.mkv', 'Film.2024.1080p.WEB-DL.x264.mkv', 'Serie.S01E02.1080p.NF.WEB', '']) {
    assert.equal(SP.releaseHasFrenchAudio(name), false, name);
  }
});

test('native player: what to do once the file is loaded', () => {
  const plan = (tracks, release = '') => plain(SP.frenchSubtitlePlan(tracks, release));
  assert.deepEqual(plan([audio(1, 'fre', true), sub(1, 'eng')]), { action: 'none', reason: 'french-audio' });
  assert.deepEqual(plan([audio(1, 'eng', true), sub(1, 'eng'), sub(2, 'fre', 'SDH'), sub(3, 'fre', 'Full')]), { action: 'select', id: '3', reason: 'embedded' },
    'full French subtitles rather than SDH ones');
  assert.deepEqual(plan([audio(1, 'eng', true), sub(1, 'fre', 'Forced')]), { action: 'online', reason: 'missing' }, 'forced ones are not enough');
  assert.deepEqual(plan([audio(1, 'jpn', true), sub(1, 'fre', 'Forcés', true), sub(2, 'fra', '')]), { action: 'select', id: '2', reason: 'embedded' });
  assert.deepEqual(plan([audio(1, 'eng', true), sub(2, 'fr', '', true)]), { action: 'none', reason: 'french-subtitles-on' });
  assert.deepEqual(plan([audio(1, '', true)], 'Film.2024.MULTi.1080p'), { action: 'none', reason: 'french-release' });
  assert.deepEqual(plan([audio(1, '', true)], 'Film.2024.1080p.WEB'), { action: 'online', reason: 'missing' });
  assert.deepEqual(plan([audio(1, 'eng', true)], 'Film.2024.MULTi.1080p'), { action: 'online', reason: 'missing' }, 'a known language wins over the name');
  assert.deepEqual(plan([audio(1, 'und', false, 'Français'), audio(2, 'eng', true)]), { action: 'online', reason: 'missing' });
  assert.deepEqual(plan([]), { action: 'online', reason: 'missing' });
});

test('built-in player: French subtitles for an original-language release', () => {
  const on = { enabled: true, kind: 'opensubtitles', language: 'fr', variant: 'standard' };
  assert.deepEqual(plain(SP.automaticSubtitles('vostfr', 'Film.2024.MULTi')), on, 'a VOSTFR preference stays');
  assert.deepEqual(plain(SP.automaticSubtitles('vf', 'Film.2024.1080p.WEB')), { ...on, automatic: true });
  assert.equal(SP.automaticSubtitles('vf', 'Film.2024.TRUEFRENCH.1080p').enabled, false);
  assert.equal(SP.automaticSubtitles('vf', 'Film.2024.1080p.WEB', false).enabled, false, 'the setting turns it off');
  assert.equal(SP.automaticSubtitles('vf', '').enabled, false, 'no name, no guess');
  assert.equal((app.match(/SeriesPlayback\.automaticSubtitles\(playerPrefs\.prefLang \|\| 'vf', inAppCurrentFilename \|\| inAppCurrentTitle, playerPrefs\.autoFrSubs !== false\)/g) || []).length, 3);
  assert.match(app, /'Version originale : sous-titres français activés\. Le bouton CC permet de les retirer\.'/);
});

function loadNative({ tracks, subtitles = [], autoSetting = true, online = true }) {
  const elements = {};
  const calls = [];
  let appended = false;
  const state = { time: 5, duration: 1500, paused: false, ended: false, idle: false, volume: 100, tracks };
  const make = id => elements[id] || (elements[id] = {
    id, value: '0', min: '0', max: '1000', hidden: false, innerHTML: '', textContent: '', dataset: {}, disabled: false,
    style: { props: {}, setProperty(name, value) { this.props[name] = value; }, display: '' },
    attributes: {}, listeners: {}, classList: { add() {}, remove() {} },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    querySelectorAll: () => [], contains: () => false, focus() {},
  });
  const context = {
    window: { addEventListener() {} }, playerPrefs: { autoFrSubs: autoSetting },
    document: {
      getElementById: id => (id === 'nativePlayer' && !appended ? null : make(id)),
      createElement: () => make('nativePlayer'),
      body: { appendChild: () => { appended = true; }, classList: { contains: () => false } },
      documentElement: { classList: { add() {}, remove() {} } },
      activeElement: null,
    },
    localStorage: { getItem: () => null, setItem() {} },
    api: async (url, options) => {
      const body = options?.body ? JSON.parse(options.body) : null;
      calls.push([url.split('?')[0], body, url]);
      if (url.startsWith('/api/subtitles')) return { subtitles };
      return { ok: true, state };
    },
    setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, clearTimeout() {},
    URLSearchParams, networkOnline: online,
  };
  vm.runInNewContext(`${seriesSource}\n${nativeSource}`, context);
  return { NativePlayer: vm.runInContext('NativePlayer', context), elements, calls, state };
}

const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };
const film = { id: 'tt0111161', type: 'movie', name: 'Film' };

test('native player: the file’s French subtitles are switched on', async () => {
  const player = loadNative({ tracks: [audio(1, 'eng', true), sub(1, 'eng'), sub(2, 'fre', 'Full')] });
  await player.NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: film, filename: 'Film.2024.1080p.WEB.mkv' });
  await flush();
  const commands = player.calls.filter(([url]) => url === '/api/native-player/command').map(([, body]) => body);
  assert.deepEqual(commands, [{ cmd: 'subtitles', value: '2' }]);
  assert.equal(player.calls.some(([url]) => url === '/api/subtitles'), false, 'nothing is fetched');
  await player.NativePlayer.close();
});

test('native player: French subtitles from OpenSubtitles when the file has none', async () => {
  const subtitles = [{ url: 'https://subs.example/forced', lang: 'fr', variant: 'forced' }, { url: 'https://subs.example/fr', lang: 'fr' },
    { url: 'https://subs.example/en', lang: 'en' }];
  const player = loadNative({ tracks: [audio(1, 'eng', true)], subtitles });
  await player.NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: film, filename: 'Film.2024.1080p.WEB.mkv' });
  await flush();
  const search = player.calls.find(([url]) => url === '/api/subtitles');
  assert.match(search[2], /imdb_id=tt0111161&type=movie/);
  const added = player.calls.filter(([url]) => url === '/api/native-player/subtitle').map(([, body]) => body);
  assert.deepEqual(added, [{ url: 'https://subs.example/fr', label: 'OpenSubtitles #2' }], 'the full French one, not the forced one');
  await player.NativePlayer.close();
});

test('native player: without internet, no search, and the reason on screen', async () => {
  const player = loadNative({ tracks: [audio(1, 'eng', true)], subtitles: [{ url: 'https://subs.example/fr', lang: 'fr' }], online: false });
  await player.NativePlayer.openStream({ url: 'http://127.0.0.1:8080/api/offline/media/abc', title: 'Film', media: film, filename: 'Film.2024.1080p.WEB.mkv' });
  await flush();
  assert.equal(player.calls.some(([url]) => url === '/api/subtitles'), false);
  assert.equal(player.elements.nativeStatus.textContent, 'Sans internet : pas de sous-titres français en ligne');
  await player.NativePlayer.close();
});

test('native player: French audio, the setting off, or « Aucun » chosen leave subtitles alone', async () => {
  const french = loadNative({ tracks: [audio(1, 'fre', true), sub(1, 'fre')] });
  await french.NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: film, filename: 'Film.MULTi.mkv' });
  await flush();
  assert.equal(french.calls.some(([url]) => url === '/api/native-player/command' || url === '/api/subtitles'), false);

  const off = loadNative({ tracks: [audio(1, 'eng', true), sub(2, 'fre')], autoSetting: false });
  await off.NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: film, filename: 'Film.mkv' });
  await flush();
  assert.equal(off.calls.some(([url]) => url === '/api/native-player/command'), false);

  const series = { id: 'tt1', type: 'series', name: 'Série', season: 1, episode: 1 };
  const playlist = [{ title: 'S01E01', url: 'https://debrid.example/1.mkv' }, { title: 'S01E02', url: 'https://debrid.example/2.mkv' }];
  const declined = loadNative({ tracks: [audio(1, 'eng', true), sub(2, 'fre')] });
  await declined.NativePlayer.openStream({ url: playlist[0].url, title: 'S01E01', media: series, filename: 'Serie.S01E01.mkv', playlist });
  await flush();
  declined.elements.nativeSubs.listeners.change({ target: { value: 'no' } });
  await flush();
  const before = declined.calls.length;
  declined.elements.nativeNext.listeners.click();
  await flush();
  assert.ok(declined.calls.slice(before).some(([url, body]) => url === '/api/native-player/open' && body.stream_url === playlist[1].url), 'the next episode opened');
  const after = declined.calls.slice(before).filter(([url, body]) => url === '/api/native-player/command' && body.cmd === 'subtitles');
  assert.deepEqual(after, [], 'the next episode keeps them off');
});

test('the setting: on by default, saved, sent to both players', () => {
  assert.match(html, /<label class="settings-label" for="cfgAutoFrSubs">Sous-titres français automatiques<\/label>[\s\S]*?<input type="checkbox" id="cfgAutoFrSubs" role="switch" checked>/);
  assert.match(app, /playerPrefs\.autoFrSubs = cfg\.auto_fr_subs !== false;/);
  assert.match(app, /auto_fr_subs: checked\('cfgAutoFrSubs', true\),/);
});
