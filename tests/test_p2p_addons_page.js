// Sources without a debrid service (p2p-addons.js): the add-ons in Paramètres,
// the P2P consent asked once with what it means, and « Lancer » that waits for it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '..');
const read = name => fs.readFileSync(path.join(ROOT, name), 'utf8');
const html = read('web/index.html');
const code = read('web/js/p2p-addons.js');
const source = require('../tools/interface_source.js').appSource();
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

function load(answers = {}) {
  const calls = [];
  const alerts = [];
  const elements = {};
  const element = id => (elements[id] ||= { id, value: '', checked: false, disabled: false, innerHTML: '', textContent: '', hidden: false, open: false,
    showModal() { this.open = true; }, close() { this.open = false; }, querySelector: () => null });
  const context = {
    document: { getElementById: element }, alerts, LIST_SHELF_SIZE: 12,
    alert: text => alerts.push(text), confirm: () => true, offlineSize: bytes => `${Math.round(bytes / 1048576)} Mo`, escapeHtml, attrJson: value => escapeHtml(JSON.stringify(value)),
    posterCardHtml: m => `<div class="poster-card">${escapeHtml(m.name)}</div>`, encodeURIComponent, Promise,
    api: async (url, options) => {
      calls.push([url, options?.body ? JSON.parse(options.body) : null]);
      const answer = answers[url];
      if (answer instanceof Error) throw answer;
      return typeof answer === 'function' ? answer(options) : answer;
    },
  };
  vm.runInNewContext(`${code}; this.api_ = { askP2PConsent, answerP2PConsent, loadSourcesSettings, setP2PEnabled, setP2PSetting, clearP2PCache, stremioAddonHtml, addStremioAddon, renderAddonCatalogs, state: () => p2pState };`, context);
  return { ...context.api_, calls, elements, context };
}

test('the consent dialog says what P2P means before anything is read', () => {
  const dialog = html.slice(html.indexOf('<dialog id="p2pConsent"'), html.indexOf('</dialog>', html.indexOf('<dialog id="p2pConsent"')));
  for (const fact of ['adresse IP est visible', 'envoie aussi des morceaux', 'ARCOM', 'VPN', 'désactiver la lecture P2P']) {
    assert.match(dialog, new RegExp(fact), fact);
  }
  assert.match(dialog, /onclick="answerP2PConsent\(false\)">Annuler<\/button>\s*<button type="button" class="btn" onclick="answerP2PConsent\(true\)">J’accepte, lire en P2P<\/button>/);
  assert.match(dialog, /<option value="minimum">Minimum \(64 Ko\/s\)<\/option>/, 'the least sharing is the default');
});

test('accepting saves the choice, cancelling saves nothing', async () => {
  const lib = load({ '/api/p2p/consent': options => ({ available: true, accepted: true, upload: JSON.parse(options.body).upload }) });
  const asked = lib.askP2PConsent();
  assert.equal(lib.elements.p2pConsent.open, true);
  lib.elements.p2pConsentUpload.value = 'normal';
  await lib.answerP2PConsent(true);
  assert.equal(await asked, true);
  assert.deepEqual(JSON.parse(JSON.stringify(lib.calls)), [['/api/p2p/consent', { accept: true, upload: 'normal' }]]);
  assert.equal(lib.elements.p2pConsent.open, false);

  const other = load();
  const refused = other.askP2PConsent();
  await other.answerP2PConsent(false);
  assert.equal(await refused, false);
  assert.deepEqual(other.calls, [], 'nothing sent when the user cancels');
});

test('the switch in Paramètres asks first, and turns off at once', async () => {
  const lib = load({ '/api/p2p/consent': { available: true, accepted: false, upload: 'minimum' } });
  const input = { checked: true };
  const pending = lib.setP2PEnabled(input);
  assert.equal(input.checked, false, 'off until the user agrees');
  await lib.answerP2PConsent(false);
  await pending;
  assert.equal(input.checked, false);
  await lib.setP2PEnabled({ checked: false });
  assert.deepEqual(JSON.parse(JSON.stringify(lib.calls.at(-1))), ['/api/p2p/consent', { accept: false }]);
});

test('the P2P settings show what is saved, and send only what changed', async () => {
  const saved = { available: true, accepted: false, upload: '1m', download: '5m', encrypted: true, port: 51413, cache_bytes: 734003200 };
  const lib = load({
    '/api/p2p': saved,
    '/api/stremio-addons': { addons: [], catalogs: [] },
    '/api/p2p/settings': options => ({ ...saved, ...JSON.parse(options.body) }),
    '/api/p2p/clear': { ...saved, cache_bytes: 0 },
  });
  await lib.loadSourcesSettings();
  const field = id => lib.elements[id];
  assert.deepEqual([field('cfgP2PUpload').value, field('cfgP2PDownload').value, field('cfgP2PEncrypted').checked, field('cfgP2PPort').value],
    ['1m', '5m', true, '51413']);
  assert.equal(field('cfgP2PDownload').disabled, false, 'chosen before agreeing too');
  assert.match(field('cfgP2PCache').textContent, /^700 Mo sur cet ordinateur\./);
  await lib.setP2PSetting('download', '2m');
  assert.deepEqual(JSON.parse(JSON.stringify(lib.calls.at(-1))), ['/api/p2p/settings', { download: '2m' }]);
  assert.equal(field('cfgP2PDownload').value, '2m');
  const sent = lib.calls.length;
  await lib.setP2PSetting('port', '80');
  assert.equal(lib.calls.length, sent, 'a port below 1024 is not sent');
  assert.match(lib.context.alerts.at(-1), /entre 1024 et 65535/);
  assert.equal(field('cfgP2PPort').value, '51413');
  await lib.clearP2PCache();
  assert.deepEqual(lib.calls.at(-1)[0], '/api/p2p/clear');
  assert.equal(field('cfgP2PCache').textContent, 'Aucun fichier pour l’instant.');
  assert.equal(field('cfgP2PClear').disabled, true);
});

test('the settings rows, and no add-on named after Stremio', () => {
  for (const id of ['cfgP2PUpload', 'cfgP2PDownload', 'cfgP2PEncrypted', 'cfgP2PPort', 'cfgP2PCache', 'cfgP2PClear']) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
  assert.match(html, /<option value="256k">256 Ko\/s<\/option>/);
  const shown = html.replace(/<!--[\s\S]*?-->/g, '').replace(/\b(id|for|onclick|onsubmit|onchange)="[^"]*"/g, '');
  assert.doesNotMatch(shown, /Stremio/, 'the add-ons do not come from Stremio');
  assert.doesNotMatch(code.replace(/\/\/.*$/gm, '').replace(/\b\w*Stremio\w*\b/g, ''), /Stremio/);
});

test('add-ons are listed with what they offer, their names as text', async () => {
  const hostile = 'Bad <img src=x onerror=alert(1)>';
  const lib = load({
    '/api/p2p': { available: true, accepted: true, upload: 'minimum' },
    '/api/stremio-addons': { addons: [{ id: 'a"b', name: hostile, version: '1.0.0', streams: true, catalogs: [{}, {}], enabled: false }], catalogs: [] },
  });
  await lib.loadSourcesSettings();
  const list = lib.elements.stremioAddonList.innerHTML;
  assert.match(list, /<li class="addon-row is-off">/);
  assert.match(list, /<strong>Bad &lt;img src=x onerror=alert\(1\)&gt;<\/strong>\s*<span>Sources · 2 catalogues · v1\.0\.0<\/span>/);
  assert.doesNotMatch(list, /<img/);
  assert.match(list, /onclick="removeStremioAddon\(&quot;a\\&quot;b&quot;\)"/);
  assert.equal(lib.elements.cfgP2P.checked, true);
  assert.match(lib.elements.cfgP2PStatus.textContent, /KINO lit les torrents lui-même/);
});

test('an address the server refuses is said, and the field keeps it', async () => {
  const lib = load({ '/api/stremio-addons': Object.assign(new Error('Cet addon ne répond pas.'), { code: '' }) });
  lib.context.document.getElementById('stremioAddonUrl').value = 'https://nope.example/manifest.json';
  await lib.addStremioAddon({ preventDefault() {} });
  assert.deepEqual(lib.context.alerts, ['Cet addon ne répond pas.']);
  assert.equal(lib.elements.stremioAddonUrl.value, 'https://nope.example/manifest.json');
});

test('the add-ons catalogues are rows of posters in Explorer', async () => {
  const lib = load({
    '/api/stremio-addons': { addons: [], catalogs: [{ addon: 'x', addon_name: 'Cinemeta', type: 'series', id: 'top', name: 'Populaires' }] },
    '/api/stremio-addons/catalog?addon=x&type=series&id=top': { items: [{ id: 'tt1', name: 'Série' }] },
  });
  await lib.renderAddonCatalogs();
  assert.equal(lib.elements.addonCatalogs.hidden, false);
  assert.match(lib.elements.addonCatalogs.innerHTML, /<h3 class="list-shelf-title">Populaires<\/h3>\s*<div class="list-shelf-meta">Cinemeta · Séries<\/div>/);
  const empty = load({ '/api/stremio-addons': { addons: [], catalogs: [] } });
  await empty.renderAddonCatalogs();
  assert.equal(empty.elements.addonCatalogs.hidden, true, 'no add-on, no row');
});

test('« Lancer » and a chosen source wait for the consent, then go on', () => {
  assert.match(source, /if \(e\.code === 'p2p_consent'\) \{[\s\S]{0,200}if \(await askP2PConsent\(\)\) return oneClickPlay\(params, btn\);/);
  assert.match(source, /if \(e\.code === 'p2p_consent' && await askP2PConsent\(\)\) \{\n\s+i -= 1;\n\s+continue;/);
  assert.match(html, /<script src="\/js\/anime-home\.js"><\/script>\n\s+<script src="\/js\/p2p-addons\.js"><\/script>/);
  assert.match(html, /<h3 class="settings-subtitle">Sans service de débridage<\/h3>/);
});
