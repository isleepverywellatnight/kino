// « Effacer l'historique » empties the whole history, but only after confirmation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();

function extract(name) {
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, `${name} is defined`);
  return source.slice(start, source.indexOf('\n}\n', start) + 2);
}

function load({ history, confirmAnswer }) {
  const calls = { removed: [], confirm: [] };
  const context = {
    userHistory: history,
    window: { confirm: message => { calls.confirm.push(message); return confirmAnswer; } },
    removeHistoryItem: async id => { calls.removed.push(id); },
  };
  vm.runInNewContext(`${extract('clearAllHistory')}\nthis.clear = clearAllHistory;`, context);
  return { clear: context.clear, calls };
}

const history = [
  { id: 'tt1', completed: true, imported_watched: true },
  { id: 'tt2', progress_pct: 12 },
  { id: 'tt3', completed: true },
];

test('the whole history is cleared once the count is confirmed', async () => {
  const { clear, calls } = load({ history, confirmAnswer: true });
  await clear();
  assert.equal(calls.confirm.length, 1);
  assert.match(calls.confirm[0], /^Effacer les 3 titres de l’historique \?/);
  assert.match(calls.confirm[0], /films vus et les lectures en cours seront retirés/);
  assert.deepEqual(calls.removed, ['__all__']);
});

test('cancelling or an empty history removes nothing', async () => {
  const cancelled = load({ history, confirmAnswer: false });
  await cancelled.clear();
  assert.deepEqual(cancelled.calls.removed, []);
  const empty = load({ history: [], confirmAnswer: true });
  await empty.clear();
  assert.equal(empty.calls.confirm.length, 0);
  assert.deepEqual(empty.calls.removed, []);
  const single = load({ history: [{ id: 'tt9' }], confirmAnswer: true });
  await single.clear();
  assert.match(single.calls.confirm[0], /^Effacer le titre de l’historique \?/);
});

test('the button keeps its name and action', () => {
  assert.match(withCalls(html), /id="homeResumeClear"[^>]*onclick="clearAllHistory\(\)">Effacer l’historique<\/button>/);
});
