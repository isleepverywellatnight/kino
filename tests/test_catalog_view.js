// « Masquer déjà vus » and the catalog sort survive a restart; broken storage is harmless.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const block = 'let hideWatchedInCatalog = false;' + source.split('let hideWatchedInCatalog = false;', 2)[1]
  .split('function surpriseMeFromCurrentList()', 1)[0];

function context(storage) {
  const chip = { classList: { active: false, toggle(_name, on) { this.active = on; } } };
  const select = { value: 'top' };
  const renders = [];
  return {
    catalogSort: 'top', activeTab: 'anime', catalogItems: [], chip, select, renders,
    localStorage: storage,
    document: { getElementById: id => (id === 'hideWatchedChip' ? chip : id === 'catalogSortSelect' ? select : null) },
    renderPosterCards: (...args) => renders.push(args),
  };
}

test('the view is restored after a restart', () => {
  const store = new Map();
  const storage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
  const first = context(storage);
  vm.runInNewContext(`function setCatalogSort(value) { catalogSort = value; return value; } ${block}; restoreCatalogView(); catalogSort = 'recent'; toggleHideWatchedCatalog();`, first);
  assert.equal(first.renders.length, 1, 'the anime grid is refreshed too');
  const second = context(storage);
  vm.runInNewContext(`function setCatalogSort(value) { catalogSort = value; return value; } ${block}; restoreCatalogView(); result = [hideWatchedInCatalog, catalogSort];`, second);
  assert.deepEqual(Array.from(second.result), [true, 'recent']);
  assert.equal(second.chip.classList.active, true);
  assert.equal(second.select.value, 'recent');
});

test('unreadable or hostile storage falls back to defaults', () => {
  for (const storage of [
    { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } },
    { getItem: () => '{"hideWatched":"yes","sort":"<img>"}', setItem: () => {} },
  ]) {
    const ctx = context(storage);
    vm.runInNewContext(`function setCatalogSort(value) { catalogSort = value; return value; } ${block}; restoreCatalogView(); toggleHideWatchedCatalog(); result = catalogSort;`, ctx);
    assert.equal(ctx.result, 'top');
  }
});
