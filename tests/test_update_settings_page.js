// Settings → Updates: the switch for the automatic check reflects KINO's state
// and is put back when the app refuses the change.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const ROOT = path.join(__dirname, '..');
const html = require('../tools/interface_source.js').indexHtml();
const source = require('../tools/interface_source.js').scriptText('app-start.js');
const start = source.indexOf('let kinoUpdatePendingAction');
const end = source.indexOf('async function checkKinoUpdates()');
const code = source.slice(start, end);

function load(bridge) {
  const elements = {};
  const element = id => (elements[id] ||= { id, textContent: '', hidden: false, disabled: false, checked: false, style: {} });
  const context = { document: { getElementById: element }, window: { pywebview: bridge ? { api: bridge } : undefined }, Math, Number, String };
  vm.runInNewContext(`${code}; this.api_ = { renderKinoUpdate, setKinoUpdateAuto };`, context);
  return { ...context.api_, elements };
}

test('the switch sits in Settings → Updates', () => {
  assert.match(withCalls(html), /id="settings-updates"[\s\S]*<label class="settings-label" for="kinoUpdateAuto">Rechercher automatiquement<\/label>[\s\S]*toutes les 6 heures[\s\S]*id="kinoUpdateAuto" role="switch" onchange="setKinoUpdateAuto\(this\)"/);
});

test('it shows the state, and an answer of the app replaces it', async () => {
  const sent = [];
  const lib = load({ set_update_auto_check: async enabled => { sent.push(enabled); return { version: '9.1.0', auto_check: enabled }; } });
  lib.renderKinoUpdate({ version: '9.1.0', auto_check: true });
  assert.equal(lib.elements.kinoUpdateAuto.checked, true);
  const input = { checked: false };
  await lib.setKinoUpdateAuto(input);
  assert.deepEqual(sent, [false]);
  assert.equal(lib.elements.kinoUpdateAuto.checked, false);
});

test('outside the app the switch goes back as it was', async () => {
  const lib = load(null);
  const input = { checked: false };
  await lib.setKinoUpdateAuto(input);
  assert.equal(input.checked, true);
  assert.match(lib.elements.kinoUpdateMessage.textContent, /application KINO/);
});
