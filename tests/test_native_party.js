// Native player extras: « Annuler le saut » after « Passer l'intro », and the
// Watch Party (events, sync, chat, messages) with the native player.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const nativeSource = require('../tools/interface_source.js').inFrench(require('../tools/interface_source.js').scriptText('native-player.js'));
const app = require('../tools/interface_source.js').appSource();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const { escapeHtml } = require('../tools/interface_source.js').templateHelpers();

function loadNative(state, globals = {}) {
  const elements = {};
  const calls = [];
  const broadcasts = [];
  let appended = false;
  const clock = { now: 9_000_000 };
  const make = id => elements[id] || (elements[id] = {
    id, value: '0', min: '0', max: id === 'nativeVolume' ? '100' : '1000', hidden: id === 'nativeMoreMenu',
    innerHTML: '', textContent: '', dataset: {}, disabled: false, children: [],
    style: { props: {}, setProperty(name, value) { this.props[name] = value; }, display: '' },
    attributes: {}, listeners: {}, classList: { add() {}, remove() {} },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    appendChild(child) { child.parentNode = this; this.children.push(child); },
    querySelectorAll: () => [], contains: () => false, focus() {},
  });
  const chat = { id: 'inAppWpChatBox', parentNode: null };
  const inAppOverlay = make('inAppPlayerOverlay');
  inAppOverlay.appendChild(chat);
  const document = {
    getElementById: id => (id === 'nativePlayer' && !appended ? null : id === 'inAppWpChatBox' ? chat : make(id)),
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
      if (url === '/api/native-player/command' && ['play', 'pause'].includes(body.cmd)) state.paused = body.cmd === 'pause';
      if (url === '/api/native-player/command' && body.cmd === 'toggle') state.paused = !state.paused;
      if (url === '/api/native-player/command' && body.cmd === 'goto') state.time = body.value;
      if (url.startsWith('/api/intro-times')) return { op: { start: 10, end: 95 } };
      return { ok: true, state };
    },
    setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, clearTimeout() {},
    Date: FakeDate, URLSearchParams,
    broadcastWpEvent: (event, time) => broadcasts.push(`${event}@${Math.round(time)}`),
    ...globals,
  };
  vm.runInNewContext(nativeSource, context);
  return { NativePlayer: vm.runInContext('NativePlayer', context), elements, calls, broadcasts, clock, chat, inAppOverlay };
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const commands = calls => calls.filter(([url]) => url === '/api/native-player/command').map(([, body]) => body.value === undefined ? body.cmd : `${body.cmd}=${body.value}`);
const episode = { id: 'tt1', type: 'series', name: 'Série', season: 1, episode: 2 };

test('« Passer l\'intro » offers to go back for 7 s', async () => {
  const state = { time: 17.6, duration: 2700, paused: false, tracks: [] };
  const { NativePlayer, elements, calls, clock } = loadNative(state);
  await NativePlayer.openStream({ url: 'https://debrid.example/e2.mkv', title: 'Série — S01E02', media: episode });
  await flush();
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  elements.nativeSkipGo.listeners.click();
  await flush();
  assert.equal(elements.nativeUndoCard.style.display, 'flex');
  assert.equal(elements.nativeUndoGo.textContent, '↩ Revenir à 0:17');
  calls.length = 0;
  elements.nativeUndoGo.listeners.click();
  await flush();
  assert.deepEqual(commands(calls), ['goto=17']);
  assert.equal(elements.nativeUndoCard.style.display, 'none');
  assert.equal(elements.nativeStatus.textContent, 'Retour à 0:17');

  elements.nativeSkipGo.listeners.click();
  state.time = 17;
  await flush();
  clock.now += 7001;
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  assert.equal(elements.nativeUndoCard.style.display, 'none', 'gone after 7 s');
  await NativePlayer.close();
});

test('play, pause and jumps are shared with the party', async () => {
  const state = { time: 300, duration: 2700, paused: true, tracks: [] };
  const { NativePlayer, broadcasts } = loadNative(state);
  await NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: { id: 'tt2', type: 'movie' } });
  NativePlayer.handleRemoteCommand({ action: 'toggle_play' });
  await flush();
  NativePlayer.handleRemoteCommand({ action: 'seek', value: 600 });
  await flush();
  NativePlayer.handleRemoteCommand({ action: 'pause' });
  await flush();
  NativePlayer.handleRemoteCommand({ action: 'volume', value: 0.5 });
  await flush();
  assert.deepEqual(broadcasts, ['play@300', 'seek@600', 'pause@600'], 'volume is not shared');
  await NativePlayer.close();
});

test('a friend\'s event moves the native player without being sent back', async () => {
  const state = { time: 300, duration: 2700, paused: true, tracks: [] };
  const { NativePlayer, calls, broadcasts } = loadNative(state);
  await NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: { id: 'tt2', type: 'movie' } });
  calls.length = 0;
  await NativePlayer.applySync({ type: 'event', event: 'play', time: 301 });
  assert.deepEqual(commands(calls), ['play'], 'less than 1.5 s apart: no jump');
  await NativePlayer.applySync({ type: 'event', event: 'pause', time: 900 });
  await NativePlayer.applySync({ type: 'sync', time: 1200, paused: false });
  await NativePlayer.applySync({ type: 'event', event: 'seek', time: 50 });
  assert.deepEqual(commands(calls), ['play', 'goto=900', 'pause', 'goto=1200', 'play', 'goto=50']);
  assert.deepEqual(broadcasts, [], 'nothing echoed back to the party');
  await NativePlayer.close();
  calls.length = 0;
  await NativePlayer.applySync({ type: 'event', event: 'play', time: 0 });
  assert.deepEqual(commands(calls), [], 'closed: nothing to move');
});

test('the party chat moves into the native player and back', async () => {
  const state = { time: 5, duration: 2700, paused: false, tracks: [] };
  const { NativePlayer, elements, chat, inAppOverlay } = loadNative(state);
  await NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: { id: 'tt2', type: 'movie' } });
  assert.equal(chat.parentNode, elements.nativePlayer);
  await NativePlayer.close();
  assert.equal(chat.parentNode, inAppOverlay);
});

test('KINO messages show in the native player as plain text', async () => {
  const state = { time: 5, duration: 2700, paused: false, tracks: [] };
  const { NativePlayer, elements } = loadNative(state);
  await NativePlayer.openStream({ url: 'https://debrid.example/film.mkv', title: 'Film', media: { id: 'tt2', type: 'movie' } });
  NativePlayer.notify('<strong>Ami :</strong> on lance ?<br><img src=x onerror=alert(1)>', 2000);
  assert.equal(elements.nativeStatus.textContent, 'Ami : on lance ?');
  await NativePlayer.close();
});

function wpFunctions() {
  return app.slice(app.indexOf('// What a friend joining the party receives'), app.indexOf('function broadcastWpEvent(event, time) {'))
    + app.slice(app.indexOf('function appendWpChatMessage(user, text) {'), app.indexOf('// --- Gestionnaire d\'Add-ons ---'));
}

test('incoming party events go to the native player when it plays', () => {
  const applied = [];
  const toasts = [];
  const msgs = { appended: [], scrollTop: 0, scrollHeight: 10, appendChild(div) { this.appended.push(div); } };
  const context = {
    NativePlayer: { isOpen: () => true, applySync: data => applied.push(data), remoteState: () => ({ current_time: 812.4, paused: false, title: 'Film' }) },
    document: {
      getElementById: id => ({ inAppWpChatBox: { style: {} }, inAppWpMessages: msgs, inAppVideo: { currentTime: 1, paused: true } })[id] || null,
      createElement: () => ({ style: {}, innerHTML: '' }),
    },
    showInAppToast: msg => toasts.push(msg), escapeHtml, inAppCurrentTitle: 'Web',
  };
  vm.runInNewContext(`${wpFunctions()}\nthis.api_ = { handleWpIncomingData, wpPlayerSnapshot };`, context);
  context.api_.handleWpIncomingData({ type: 'event', event: 'pause', time: 40 });
  assert.equal(applied.length, 1);
  assert.deepEqual(toasts, ['Pause synchronisée']);
  assert.deepEqual({ ...context.api_.wpPlayerSnapshot() }, { time: 812.4, paused: false, title: 'Film' });
  context.NativePlayer.isOpen = () => false;
  assert.deepEqual({ ...context.api_.wpPlayerSnapshot() }, { time: 1, paused: true, title: 'Web' }, 'the web player otherwise');

  context.api_.handleWpIncomingData({ type: 'chat', user: '<img src=x onerror=alert(1)>', text: '<b>salut</b> & bienvenue dans la soirée cinéma' });
  const line = msgs.appended[0].innerHTML;
  assert.doesNotMatch(line, /<img|<b>salut/);
  assert.match(withCalls(line), /&lt;img src=x onerror=alert\(1\)&gt; :/);
  assert.match(line, /color:#a3a3a3/, 'monochrome');
  assert.doesNotMatch(String(toasts.at(-1)), /<b>salut|<img/);
  assert.match(withCalls(toasts.at(-1)), /^<strong>&lt;img src=x onerror=alert\(1\)&gt; :<\/strong> &lt;b&gt;salut&lt;\/b&gt; &amp; bienvenue…$/);
});

test('toasts, party window and menu reach the native player', () => {
  assert.match(app, /function showInAppToast\(msg, duration = 3000\) \{\n  \/\/ The native player shows its own messages \(Watch Party, remote…\)\.\n  if \(NativePlayer\?\.isOpen\?\.\(\) && typeof NativePlayer\.notify === 'function'\) \{/);
  assert.match(nativeSource, /id="nativeParty">Watch Party<\/button>/);
  assert.match(css, /#remoteModal, #watchPartyModal \{ z-index: 10050; \}/);
  const chatCode = app.slice(app.indexOf('function appendWpChatMessage(user, text) {'), app.indexOf('// --- Gestionnaire d\'Add-ons ---'));
  assert.doesNotMatch(chatCode, /#4ade80|#38bdf8/, 'no green or blue left in the chat');
});
