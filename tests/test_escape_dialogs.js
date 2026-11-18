// Escape closes the dialog in front before it touches the player.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const helper = 'const ESCAPE_DIALOGS' + source.split('const ESCAPE_DIALOGS', 2)[1]
  .split("window.addEventListener('keydown', (e) => {", 1)[0];

test('the open dialog is closed and reported', () => {
  const closed = [];
  const dialogs = { remoteModal: { style: { display: 'flex' }, classList: { contains: () => false } } };
  const context = {
    document: { getElementById: id => dialogs[id] || null },
    toggleShortcutsModal: () => closed.push('shortcuts'), closeRemoteModal: () => closed.push('remote'),
    closeWatchPartyModal: () => closed.push('watch party'), closeAddonsModal: () => closed.push('addons'),
    closeInAppMoreMenu: () => false,
    result: null,
  };
  vm.runInNewContext(`${helper}; result = [closeOpenDialogOnEscape()];`, context);
  assert.deepEqual(closed, ['remote']);
  dialogs.remoteModal.style.display = 'none';
  vm.runInNewContext('result.push(closeOpenDialogOnEscape());', context);
  assert.deepEqual(Array.from(context.result), [true, false]);
});

test('both Escape branches give dialogs priority', () => {
  const handler = source.split("window.addEventListener('keydown', (e) => {", 2)[1];
  const calls = handler.match(/if \(closeOpenDialogOnEscape\(\)\) return;/g) || [];
  assert.equal(calls.length, 2, 'menus and player both check dialogs first');
  const playerBranch = handler.split("} else if (e.key === 'Escape') {", 2)[1].split('closeInAppPlayer()', 1)[0];
  assert.match(playerBranch, /closeOpenDialogOnEscape\(\)/);
});
