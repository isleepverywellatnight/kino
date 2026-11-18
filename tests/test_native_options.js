// Native player, step 3: « Options », online subtitles, capture, phone remote,
// Discord and Trakt, like the built-in player.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const nativeSource = require('../tools/interface_source.js').inFrench(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/native-player.js'), 'utf8'));
const app = require('../tools/interface_source.js').appSource();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');

// A small DOM: elements are created on first lookup once the overlay exists.
function loadNative(state, extra = {}) {
  const elements = {};
  const calls = [];
  let appended = false;
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
  const context = {
    window: { addEventListener() {} }, document, localStorage: { getItem: () => null, setItem() {} },
    api: async (url, options) => {
      const body = options?.body ? JSON.parse(options.body) : null;
      calls.push([url, body]);
      if (url === '/api/native-player/screenshot') return { ok: true, filename: 'KINO_Capture_Film_1m40s.png' };
      if (url.startsWith('/api/subtitles')) return { subtitles: extra.subtitles || [] };
      return { ok: true, state };
    },
    escapeHtml: value => String(value).replaceAll('<', '&lt;'),
    setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, clearTimeout() {}, Date, URLSearchParams,
    ...extra.globals,
  };
  vm.runInNewContext(nativeSource, context);
  return { NativePlayer: context.window.NativePlayer, elements, calls };
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const baseState = () => ({ time: 100, duration: 1000, paused: true, volume: 80, muted: false, speed: 1, sub_scale: 1,
  voice: 'normal', clarity: 0, tracks: [{ id: '1', type: 'sub', lang: 'fre', selected: false }, { id: '2', type: 'sub', lang: 'fr', title: 'OpenSubtitles', external: true, selected: true }] });

test('settings cycle through the built-in player steps', () => {
  const { NativePlayer } = loadNative(baseState());
  const next = (name, state) => NativePlayer.nextSetting(name, state);
  assert.equal(next('speed', { speed: 1 }), 1.25);
  assert.equal(next('speed', { speed: 2 }), 0.75);
  assert.equal(next('speed', { speed: 1.1 }), 1.25, 'an unusual speed snaps to the nearest step');
  assert.equal(next('sub_scale', { sub_scale: 1 }), 1.25);
  assert.equal(next('sub_scale', { sub_scale: 1.5 }), 0.8);
  assert.equal(next('voice', { voice: 'normal' }), 'boost');
  assert.equal(next('voice', { voice: 'boost' }), 'normal');
  assert.equal(next('clarity', { clarity: 3 }), 0);
  assert.equal(next('clarity', {}), 1);
  assert.equal(next('other', {}), null);
  assert.equal(NativePlayer.speedLabel(1), '1.0x');
  assert.equal(NativePlayer.speedLabel(1.25), '1.25x');
  assert.equal(NativePlayer.speedLabel(2), '2.0x');
});

test('online subtitles: two per language, named in French', () => {
  const { NativePlayer } = loadNative(baseState());
  const choices = NativePlayer.onlineSubtitleChoices([
    { lang: 'en', url: 'https://s/en1' }, { lang: 'fr', url: 'https://s/fr1', variant: 'sdh' }, { lang: 'fr', url: 'https://s/fr2', partial: true },
    { lang: 'fr', url: 'https://s/fr3' }, { lang: 'de', url: 'https://s/de' },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(choices.map(choice => [choice.label, choice.title, choice.url]))), [
    ['Français #1 · SDH', 'OpenSubtitles #1 · SDH', 'https://s/fr1'],
    ['Français #2 · partiel', 'OpenSubtitles #2', 'https://s/fr2'],
    ['Anglais', 'OpenSubtitles', 'https://s/en1'],
  ]);
  assert.deepEqual([...NativePlayer.onlineSubtitleChoices(null)], []);
});

test('the remote sees the native player and drives it', async () => {
  const state = baseState();
  const { NativePlayer, calls } = loadNative(state);
  await NativePlayer.open({ id: 'abc', filename: 'Film.S01E02.mkv' }, { id: 'tt1', name: 'Série', type: 'series', season: 1, episode: 2 }, 0, 'Série — S01E02');
  assert.deepEqual({ ...NativePlayer.remoteState() }, {
    playing: true, paused: true, title: 'Série — S01E02', sub_title: 'S01E02', media_type: 'series',
    poster: '', current_time: 100, duration: 1000, volume: 0.8,
  }, 'the poster goes to « À l’écoute » on macOS');
  calls.length = 0;
  for (const cmd of [{ action: 'toggle_play' }, { action: 'seek_rel', value: 30 }, { action: 'seek', time: 600 }, { action: 'seek', value: -5 },
    { action: 'volume', value: 0.4 }, { action: 'cycle_subs' }, { action: 'next_ep' }, { action: 'volume', value: 0 }, { action: 'volume', value: 'x' }]) {
    NativePlayer.handleRemoteCommand(cmd);
  }
  await flush();
  const commands = calls.filter(([url]) => url === '/api/native-player/command').map(([, body]) => body.value === undefined ? body.cmd : `${body.cmd}=${body.value}`);
  assert.deepEqual(commands, ['toggle', 'seek=30', 'goto=600', 'goto=0', 'volume=40', 'subtitles=no', 'mute']);
  assert.equal(NativePlayer.nextSubtitleTrack({ tracks: [{ id: '1', type: 'sub', selected: false }, { id: '2', type: 'sub', selected: false }] }), '1');
  assert.equal(NativePlayer.nextSubtitleTrack({ tracks: [{ id: '1', type: 'sub', selected: true }, { id: '2', type: 'sub' }] }), '2');
  assert.equal(NativePlayer.nextSubtitleTrack({ tracks: [] }), null);
  state.muted = true;
  calls.length = 0;
  await NativePlayer.close();
});

test('Trakt follows play, pause and 80 %, Discord follows pauses', async () => {
  const state = baseState();
  const trakt = [];
  const { NativePlayer, calls } = loadNative(state, { globals: { scrobbleTrakt: async (action, media, pct) => { trakt.push(`${action}@${pct}:${media.name}`); return true; } } });
  await NativePlayer.open({ id: 'abc', filename: 'film.mkv' }, { id: 'tt1', type: 'movie' }, 0, 'Film');
  assert.deepEqual(trakt, [], 'nothing while paused at the start');
  const step = async changes => { Object.assign(state, changes); NativePlayer.handleRemoteCommand({ action: 'play' }); await flush(); };
  await step({ paused: false });
  await step({ paused: false, time: 150 });
  await step({ paused: true });
  await step({ paused: false, time: 820 });
  await step({ time: 900 });
  assert.deepEqual(trakt, ['start@10:Film', 'pause@15:Film', 'stop@82:Film']);
  const discord = calls.filter(([url]) => url === '/api/discord-rpc/update').map(([, body]) => body.is_paused);
  assert.deepEqual(discord, [true, false, true, false], 'once per change (and every 4 s while playing)');
  calls.length = 0;
  await NativePlayer.close();
  assert.deepEqual(trakt.length, 3, 'already marked watched: nothing more on close');
  assert.ok(calls.some(([url]) => url === '/api/discord-rpc/clear'));
});

test('closing before 80 % pauses the Trakt scrobble', async () => {
  const state = baseState();
  const trakt = [];
  const { NativePlayer } = loadNative(state, { globals: { scrobbleTrakt: async (action, media, pct) => { trakt.push(`${action}@${pct}`); return true; } } });
  await NativePlayer.open({ id: 'abc', filename: 'film.mkv' }, { id: 'tt1', name: 'Film', type: 'movie' }, 0, 'Film');
  state.paused = false;
  NativePlayer.handleRemoteCommand({ action: 'play' });
  await flush();
  await NativePlayer.close();
  assert.deepEqual(trakt, ['start@10', 'pause@10']);
});

test('the menu builds settings, online subtitles and sharing actions', async () => {
  const state = baseState();
  const { NativePlayer, elements, calls } = loadNative(state, {
    subtitles: [{ lang: 'fr', url: 'https://subs/fr' }],
    globals: { openRemoteModal: () => calls.push(['remote modal']) },
  });
  await NativePlayer.open({ id: 'abc', filename: 'film.mkv' }, { id: 'tt0111161', name: 'Film', type: 'movie' }, 0, 'Film');
  const markup = elements.nativePlayer.innerHTML;
  for (const piece of ['id="nativeMoreBtn" aria-expanded="false" aria-controls="nativeMoreMenu"', 'id="nativeMoreMenu" class="kp-menu" role="group" aria-label="Options de lecture" hidden',
    'id="nativeSubSize" data-name="Taille des sous-titres"', 'id="nativeVoice" data-name="Voix"', 'id="nativeClarity" data-name="Clarté"',
    'id="nativeSpeed" data-name="Vitesse"', 'Sous-titres en ligne', 'id="nativeOnlineSubs"', 'id="nativeShot">Capturer l\'image', 'id="nativeRemote">Télécommande']) {
    assert.ok(markup.includes(piece), piece);
  }
  assert.equal(elements.nativeSpeed.textContent, '1.0x');
  assert.equal(elements.nativeClarity.textContent, 'Normal');
  calls.length = 0;
  elements.nativeMoreBtn.listeners.click({ detail: 1 });
  assert.equal(elements.nativeMoreMenu.hidden, false);
  await flush();
  const search = calls.find(([url]) => url.startsWith('/api/subtitles'))[0];
  assert.match(search, /imdb_id=tt0111161&type=movie&season=1&episode=1&release=film\.mkv/);
  assert.match(elements.nativeOnlineSubs.innerHTML, /data-online="0">Français<\/button>/);
  elements.nativeSpeed.listeners.click();
  elements.nativeClarity.listeners.click();
  await flush();
  assert.deepEqual(calls.filter(([url]) => url === '/api/native-player/command').map(([, body]) => `${body.cmd}=${body.value}`), ['speed=1.25', 'clarity=1']);
  elements.nativeShot.listeners.click();
  await flush();
  assert.equal(elements.nativeMoreMenu.hidden, true, 'an action closes the menu');
  assert.deepEqual(calls.find(([url]) => url === '/api/native-player/screenshot')[1], { title: 'Film', time_str: '1m40s' });
  assert.equal(elements.nativeStatus.textContent, 'Capture enregistrée : KINO_Capture_Film_1m40s.png');
  elements.nativeRemote.listeners.click();
  assert.ok(calls.some(([name]) => name === 'remote modal'));
  await NativePlayer.close();
});

test('a title without an IMDb id cannot search subtitles online', async () => {
  const { NativePlayer, elements, calls } = loadNative(baseState());
  await NativePlayer.open({ id: 'abc', filename: 'film.mkv' }, { name: 'Vidéo perso' }, 0, 'Vidéo perso');
  elements.nativeMoreBtn.listeners.click({ detail: 1 });
  await flush();
  assert.ok(!calls.some(([url]) => url.startsWith('/api/subtitles')));
  assert.match(elements.nativeOnlineSubs.innerHTML, /Pas de recherche possible pour ce fichier/);
  await NativePlayer.close();
});

test('Escape closes the menu, then a dialog, then the player; the remote polls the native player', () => {
  assert.match(nativeSource, /if \(closeMenu\(true\)\) return;\n      if \(typeof closeOpenDialogOnEscape === 'function' && closeOpenDialogOnEscape\(\)\) return;/);
  assert.match(nativeSource, /if \(menuKey\(event\)\) \{\n      event\.stopImmediatePropagation\(\);/);
  const polling = app.slice(app.indexOf('function startRemotePolling()'));
  assert.match(polling, /window\.NativePlayer\.isOpen\(\)[\s\S]*?body: JSON\.stringify\(window\.NativePlayer\.remoteState\(\)\)[\s\S]*?window\.NativePlayer\.handleRemoteCommand\(cmd\)/);
  assert.match(css, /html\.native-playing body > \*:not\(#nativePlayer\):not\(#remoteModal\):not\(#watchPartyModal\) \{ opacity: 0 !important;/);
  assert.match(css, /#remoteModal, #watchPartyModal \{ z-index: 10050; \}/, 'the QR code and the party open over either player');
});

test('the built-in player also reports its poster for « À l’écoute »', () => {
  const app = require('../tools/interface_source.js').appSource();
  assert.match(app, /media_type: \(inAppCurrentMedia && inAppCurrentMedia\.type\) \|\| 'movie',\n\s+poster: \(inAppCurrentMedia && inAppCurrentMedia\.poster\) \|\| '',/);
});

test('both players give Discord the year and the IMDb id of the title', () => {
  const app = require('../tools/interface_source.js').appSource();
  const native = require('../tools/interface_source.js').inFrench(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/native-player.js'), 'utf8'));
  assert.match(app, /year: String\(inAppCurrentMedia\.year \|\| ''\),\n\s+imdb_id: inAppCurrentMedia\.id \|\| '',/);
  assert.match(native, /year: String\(media\.year \|\| ''\), imdb_id: media\.id \|\| '',/);
});
