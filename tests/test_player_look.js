// Both players share one look; the built-in player's secondary settings and
// actions live in an « Options » menu.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const app = require('../tools/interface_source.js').appSource();
const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/redesign.css'), 'utf8');
const nativeSource = require('../tools/interface_source.js').inFrench(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/native-player.js'), 'utf8'));

function extract(name) {
  const start = app.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, `${name} is defined`);
  return app.slice(start, app.indexOf('\n}\n', start) + 2);
}

const playerMarkup = html.slice(html.indexOf('<div id="inAppPlayerOverlay"'), html.indexOf('<!-- Volet Chat Watch Party'));
const topBar = playerMarkup.slice(playerMarkup.indexOf('<div class="inapp-hud-top'));
const menuMarkup = topBar.slice(topBar.indexOf('<div id="inAppMoreMenu"'));
const barOnly = topBar.slice(0, topBar.indexOf('<div id="inAppMoreMenu"'));

test('the top bar keeps the title, languages and « Options »', () => {
  assert.match(barOnly, /<div id="inAppTitle" class="kp-title"><\/div>/);
  for (const id of ['inAppMpvSuggestBtn', 'inAppAudioFixBtn', 'inAppAudioTrack', 'inAppSubsBtn', 'inAppMoreBtn']) {
    assert.match(barOnly, new RegExp(`id="${id}"`), `${id} in the bar`);
  }
  assert.match(barOnly, /id="inAppMoreBtn" onclick="toggleInAppMoreMenu\(event\)" aria-expanded="false" aria-controls="inAppMoreMenu"/);
  for (const id of ['inAppSubSizeBtn', 'inAppAudioBtn', 'inAppClarityBtn', 'inAppSpeedBtn', 'inAppSeriesPreferenceBtn',
    'inAppSaveOfflineBtn', 'inAppShotBtn', 'inAppRemoteBtn', 'inAppWpBtn', 'inAppExternalBtn']) {
    assert.doesNotMatch(barOnly, new RegExp(`id="${id}"`), `${id} left the bar`);
    assert.match(menuMarkup, new RegExp(`id="${id}"`), `${id} in the menu`);
  }
  assert.match(menuMarkup, /<div id="inAppMoreMenu" class="kp-menu" role="group" aria-label="Options de lecture" hidden>/);
  assert.match(menuMarkup, /onclick="openPlaybackHelp\(\)" aria-haspopup="dialog">Problème de lecture<\/button>/);
  for (const [id, name] of [['inAppSubSizeBtn', 'Taille des sous-titres'], ['inAppAudioBtn', 'Voix'], ['inAppClarityBtn', 'Clarté'], ['inAppSpeedBtn', 'Vitesse']]) {
    assert.match(menuMarkup, new RegExp(`id="${id}" data-name="${name}"`));
  }
});

test('the player has no purple or yellow left', () => {
  assert.doesNotMatch(playerMarkup, /124,\s*58,\s*237|234,\s*179,\s*8|#facc15|#ddd6fe/);
  assert.match(barOnly, /class="inapp-btn kp-pill kp-accent" id="inAppMpvSuggestBtn"/);
  assert.match(barOnly, /class="inapp-btn kp-pill kp-accent" id="inAppAudioFixBtn"/);
  assert.doesNotMatch(css, /#inAppMpvSuggestBtn, #inAppAudioFixBtn/);
});

test('menu labels show only the value, the name comes from the menu', () => {
  assert.match(app, /btn\.textContent = isBoost \? 'Boost' : 'Normal';/);
  assert.doesNotMatch(app, /'Voix : (Boost|Normal)'/);
  assert.match(extract('applyInAppClarity'), /btn\.textContent = preset\.label\.replace\(\/\^Clarté : \/, ''\);/);
  assert.match(css, /\.inapp-btn\.kp-menu-item\[data-name\]::before \{ content: attr\(data-name\);/);
});

function fakeButton(id, extra = {}) {
  const attributes = {};
  return {
    id, style: {}, dataset: {}, disabled: false, focused: false,
    setAttribute(name, value) { attributes[name] = String(value); },
    getAttribute(name) { return attributes[name] ?? null; },
    focus() { state.active = this; },
    closest(selector) { return selector === 'button' ? this : (selector === '.kp-more' && this.inMore ? {} : null); },
    ...extra,
  };
}
const state = { active: null };

function loadMenu() {
  state.active = null;
  const items = [
    fakeButton('size', { dataset: { name: 'Taille' } }),
    fakeButton('voice', { dataset: { name: 'Voix' } }),
    fakeButton('prefs', { style: { display: 'none' } }),
    fakeButton('keep'),
    fakeButton('help'),
  ];
  const listeners = {};
  const menu = {
    hidden: true,
    querySelectorAll: () => items,
    contains: element => items.includes(element),
    addEventListener: (type, fn) => { listeners[`menu:${type}`] = fn; },
  };
  const moreButton = fakeButton('inAppMoreBtn', { inMore: true });
  moreButton.setAttribute('aria-expanded', 'false');
  let idleResets = 0;
  const context = {
    document: {
      getElementById: id => ({ inAppMoreMenu: menu, inAppMoreBtn: moreButton })[id] || null,
      addEventListener: (type, fn) => { listeners[`doc:${type}`] = fn; },
      get activeElement() { return state.active; },
    },
    resetInAppIdleTimer: () => { idleResets += 1; },
  };
  const names = ['isInAppMoreMenuOpen', 'inAppMenuItems', 'openInAppMoreMenu', 'closeInAppMoreMenu', 'toggleInAppMoreMenu', 'handleInAppMenuKey', 'initInAppMoreMenu'];
  vm.runInNewContext(`${names.map(extract).join('\n')}\nthis.api = { ${names.join(', ')} };`, context);
  return { ...context.api, menu, items, moreButton, listeners, idleResets: () => idleResets };
}

test('« Options » opens and closes, and a keyboard press moves focus inside', () => {
  const m = loadMenu();
  m.toggleInAppMoreMenu({ detail: 1 });
  assert.equal(m.menu.hidden, false);
  assert.equal(m.moreButton.getAttribute('aria-expanded'), 'true');
  assert.equal(state.active, null, 'a mouse click leaves focus on the button');
  assert.equal(m.idleResets(), 1, 'the controls stay visible');
  m.toggleInAppMoreMenu({ detail: 1 });
  assert.equal(m.menu.hidden, true);
  assert.equal(m.moreButton.getAttribute('aria-expanded'), 'false');
  m.toggleInAppMoreMenu({ detail: 0 });
  assert.equal(state.active.id, 'size');
  assert.equal(m.closeInAppMoreMenu(true), true);
  assert.equal(state.active.id, 'inAppMoreBtn', 'focus returns to « Options »');
  assert.equal(m.closeInAppMoreMenu(true), false, 'nothing to close twice');
});

test('arrows move through visible items; Space and Enter press them', () => {
  const m = loadMenu();
  m.openInAppMoreMenu(true);
  const key = k => { let prevented = false; const handled = m.handleInAppMenuKey({ key: k, preventDefault: () => { prevented = true; } }); return { handled, prevented }; };
  assert.deepEqual(key('ArrowDown'), { handled: true, prevented: true });
  assert.equal(state.active.id, 'voice');
  key('ArrowDown');
  assert.equal(state.active.id, 'keep', 'hidden items are skipped');
  key('End');
  assert.equal(state.active.id, 'help');
  key('ArrowDown');
  assert.equal(state.active.id, 'size', 'wraps around');
  key('ArrowUp');
  assert.equal(state.active.id, 'help');
  key('Home');
  assert.equal(state.active.id, 'size');
  assert.deepEqual(key(' '), { handled: true, prevented: false }, 'the browser presses the button');
  assert.deepEqual(key('Enter'), { handled: true, prevented: false });
  assert.deepEqual(key('f'), { handled: false, prevented: false }, 'player shortcuts still work');
  state.active = null;
  assert.equal(key('ArrowDown').handled, false, 'outside the menu, arrows drive the player');
  m.closeInAppMoreMenu(false);
  state.active = m.items[0];
  assert.equal(key('ArrowDown').handled, false, 'a closed menu takes no keys');
});

test('cycling settings keep the menu open, actions and outside clicks close it', () => {
  const m = loadMenu();
  m.initInAppMoreMenu();
  m.openInAppMoreMenu(false);
  m.listeners['menu:click']({ target: m.items[1] });
  assert.equal(m.menu.hidden, false);
  m.listeners['menu:click']({ target: m.items[4] });
  assert.equal(m.menu.hidden, true);
  m.openInAppMoreMenu(false);
  m.listeners['doc:pointerdown']({ target: m.moreButton });
  assert.equal(m.menu.hidden, false, 'clicking « Options » itself is handled by its toggle');
  m.listeners['doc:pointerdown']({ target: { closest: () => null } });
  assert.equal(m.menu.hidden, true);
});

test('Escape, closing the player and the idle timer respect the menu', () => {
  assert.match(extract('closeOpenDialogOnEscape'), /^function closeOpenDialogOnEscape\(\) \{\n  if \(closeInAppMoreMenu\(true\)\) return true;/);
  assert.match(app, /if \(isInput\) return;\n  if \(handleInAppMenuKey\(e\)\) return;/);
  assert.match(extract('resetInAppIdleTimer'), /!inAppIsDraggingVol && !isInAppMoreMenuOpen\(\)/);
  assert.match(app, /overlay\.classList\.remove\('active', 'idle'\);\n  \}\n  closeInAppMoreMenu\(false\);/);
  assert.match(app, /initGenreStrip\(\);\ninitInAppMoreMenu\(\);/);
});

// A tiny DOM, enough for the native overlay to build and render.
function loadNative(state) {
  const elements = {};
  let appended = false;
  const make = id => elements[id] || (elements[id] = {
    id, value: '0', min: id === 'nativeVolume' ? '0' : '0', max: id === 'nativeVolume' ? '100' : '1000',
    innerHTML: '', textContent: '', dataset: {}, style: { props: {}, setProperty(name, value) { this.props[name] = value; }, display: '' },
    attributes: {}, classList: { add() {}, remove() {} },
    setAttribute(name, value) { this.attributes[name] = value; }, addEventListener() {},
  });
  const document = {
    getElementById: id => (id === 'nativePlayer' && !appended ? null : make(id)),
    createElement: () => make('nativePlayer'),
    body: { appendChild: () => { appended = true; }, classList: { contains: () => false } },
    documentElement: { classList: { add() {}, remove() {} } },
    activeElement: null,
  };
  const context = {
    window: { addEventListener() {} }, document, localStorage: { getItem: () => '0.8', setItem() {} },
    api: async () => ({ state }), escapeHtml: value => String(value).replaceAll('<', '&lt;'),
    SVG_ICONS: { play: '<svg data-icon="play"/>', pause: '<svg data-icon="pause"/>', volHigh: '<svg data-icon="volHigh"/>', volLow: '<svg data-icon="volLow"/>', volMute: '<svg data-icon="volMute"/>' },
    setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, clearTimeout() {}, Date,
  };
  vm.runInNewContext(nativeSource, context);
  return { NativePlayer: context.window.NativePlayer, elements };
}

test('the native player uses the built-in player classes and icons', async () => {
  const state = { time: 1500, duration: 3300, paused: true, volume: 40, muted: false, width: 3840, height: 2160, hwdec: 'videotoolbox',
    tracks: [{ id: 1, type: 'audio', lang: 'eng', channels: 6, selected: true }, { id: 2, type: 'sub', lang: 'fre', selected: true }] };
  const { NativePlayer, elements } = loadNative(state);
  await NativePlayer.open({ id: 'x', filename: 'x.mkv' }, { id: 'tt1' }, 0, 'Film');
  const markup = elements.nativePlayer.innerHTML;
  for (const piece of ['class="inapp-hud-top native-top"', 'class="inapp-btn kp-pill kp-back" id="nativeBack"', 'class="kp-title"',
    'class="inapp-btn kp-pill kp-select" id="nativeAudio"', 'class="inapp-btn kp-pill kp-select" id="nativeSubs"',
    'class="inapp-hud-bottom native-bottom"', 'class="kp-range kp-seek" id="nativeSeek"', 'class="inapp-controls-row"',
    'class="inapp-btn inapp-btn-icon" id="nativePlay"', 'class="inapp-vol-group"', 'class="kp-range kp-volume" id="nativeVolume"',
    'id="nativeVolumeText"', 'class="inapp-btn inapp-btn-icon" id="nativeFullscreen"']) {
    assert.ok(markup.includes(piece), piece);
  }
  assert.match(markup, /<svg data-icon="pause"\/>/, 'shared icons from app.js');
  assert.equal(elements.nativePlay.innerHTML, '<svg data-icon="play"/>');
  assert.equal(elements.nativePlay.attributes['aria-label'], 'Lecture');
  assert.equal(elements.nativeSeek.value, '455');
  assert.equal(elements.nativeSeek.style.props['--fill'], '45.5%');
  assert.equal(elements.nativeVolume.style.props['--fill'], '40%');
  assert.equal(elements.nativeVolumeText.textContent, '40%');
  assert.equal(elements.nativeMute.innerHTML, '<svg data-icon="volLow"/>');
  assert.equal(elements.nativeTime.textContent, '25:00 / 55:00');
  assert.match(elements.nativeSubs.innerHTML, /<option value="no" >CC : Off<\/option><option value="2" selected>CC : Français<\/option>/);
  assert.match(elements.nativeAudio.innerHTML, /<option value="1" selected>Anglais · 5\.1<\/option>/);

  state.paused = false;
  state.muted = true;
  await NativePlayer.close();
});

test('both players share the bar, pill, menu and focus styles', () => {
  assert.match(css, /\.inapp-player-overlay \.inapp-hud-top, #nativePlayer \.inapp-hud-top \{/);
  assert.match(css, /#nativePlayer\.native-idle \.inapp-hud-top, #nativePlayer\.native-idle \.inapp-hud-bottom \{ opacity: 0; pointer-events: none; \}/);
  assert.match(css, /\.inapp-btn\.kp-pill \{[^}]*border-radius: 999px;/);
  assert.match(css, /\.kp-menu\[hidden\] \{ display: none; \}/);
  assert.match(css, /\.kp-range::-webkit-slider-runnable-track \{[^}]*linear-gradient\(to right, #fafafa var\(--fill\), rgba\(250, 250, 250, \.22\) var\(--fill\)\)/);
  assert.match(css, /\.inapp-player-overlay \.inapp-btn:focus-visible, #nativePlayer \.inapp-btn:focus-visible,[^{]*\{\n  outline: 2px solid #fafafa !important;/);
});
