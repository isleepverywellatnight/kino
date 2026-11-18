// Library: type and state filters for Ma Liste and Déjà vus, search by French
// title, remembered choices, and clearer offline files.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const listCode = source.slice(source.indexOf('const LIST_VIEW_KEY'), source.indexOf('async function surpriseMeMedia(poolArg = null) {'));
const offlineCode = 'function offlineFreeText(bytes)' + source.split('function offlineFreeText(bytes)', 2)[1].split('async function playOfflineDownload(', 1)[0];
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

const watchlist = [
  { id: 'tt1', name: 'The Shawshank Redemption', type: 'movie', year: '1994', imdbRating: '9.3' },
  { id: 'tt2', name: 'Old Boy', type: 'movie', year: '2003', imdbRating: '8.3' },
  { id: 'tt3', name: 'Black Doves', type: 'series', year: '2024', imdbRating: '7.5' },
  { id: 'tt4', name: 'Frieren', type: 'anime', is_anime: true, year: '2023' },
  { id: 'tt5', name: 'Le Terminal', type: 'movie', year: '2004' },
];
const history = [
  { id: 'tt2', type: 'movie', completed: true },
  { id: 'tt5', type: 'movie', progress_pct: 40 },
  { id: 'tt3', type: 'series', season: 2, episode: 5, completed: true },
  { id: 'tt4', type: 'series', season: 1, episode: 28, completed: true, series_finished: true },
];

function load({ tab = 'watchlist', saved = null, query = '' } = {}) {
  const storage = saved ? { [`kino_list_view_v1`]: JSON.stringify(saved) } : {};
  const option = { textContent: 'Ajout récent' };
  const elements = {
    listFilterInput: { value: query },
    listSortSelect: { value: 'added', querySelector: () => option },
    listStatsBadge: { textContent: '', style: {} },
    listFilters: { innerHTML: '', hidden: true },
    postersGrid: { innerHTML: '' },
  };
  const rendered = [];
  const context = {
    activeTab: tab, userWatchlist: watchlist, userHistory: history,
    document: { getElementById: id => elements[id] || null },
    localStorage: { getItem: key => storage[key] ?? null, setItem: (key, value) => { storage[key] = value; } },
    getWatchedHistory: () => watchlist.filter(m => ['tt2', 'tt3', 'tt4'].includes(m.id)),
    renderPosterCards: list => rendered.push(Array.from(list, m => m.id)),
    displayTitle: m => ({ tt1: 'Les Évadés', tt5: 'Le Terminal' })[m.id] || m.name,
  };
  vm.runInNewContext(`${listCode}\nthis.api_ = { listItemKind, listItemState, setListFilter, resetListFilters, onChangeListSort, renderActiveListTab, view: () => listView };`, context);
  context.api_.renderActiveListTab();
  return { ...context.api_, elements, rendered, storage, option };
}

test('items are films or series, and to watch, started or watched', () => {
  const { listItemKind, listItemState } = load();
  assert.deepEqual(watchlist.map(listItemKind), ['movie', 'movie', 'series', 'series', 'movie']);
  assert.deepEqual(watchlist.map(listItemState), ['todo', 'watched', 'started', 'watched', 'started']);
});

test('Ma Liste filters by type and state, with counts', () => {
  const lib = load();
  assert.deepEqual(lib.rendered.at(-1), ['tt1', 'tt2', 'tt3', 'tt4', 'tt5']);
  const filters = lib.elements.listFilters.innerHTML;
  assert.match(filters, /role="group" aria-label="Type"><span class="list-filter-label" aria-hidden="true">Type<\/span>/);
  assert.match(filters, /class="chip active" aria-pressed="true" onclick="setListFilter\('type', 'all'\)">Tout <span class="list-count">5<\/span>/);
  assert.match(filters, />Films <span class="list-count">3<\/span>/);
  assert.match(filters, />Séries <span class="list-count">2<\/span>/);
  assert.match(filters, />À voir <span class="list-count">1<\/span>/);
  assert.match(filters, />Commencés <span class="list-count">2<\/span>/);
  assert.match(filters, />Vus <span class="list-count">2<\/span>/);
  lib.setListFilter('type', 'series');
  assert.deepEqual(lib.rendered.at(-1), ['tt3', 'tt4']);
  lib.setListFilter('state', 'watched');
  assert.deepEqual(lib.rendered.at(-1), ['tt4']);
  lib.setListFilter('type', 'all');
  assert.deepEqual(lib.rendered.at(-1), ['tt2', 'tt4']);
  assert.equal(lib.elements.listStatsBadge.textContent, '5 titres · 3 à voir · IMDb 8,4 en moyenne');
});

test('choices are remembered per page and bad saved values ignored', () => {
  const lib = load();
  lib.setListFilter('type', 'movie');
  lib.onChangeListSort('alpha');
  const saved = JSON.parse(lib.storage.kino_list_view_v1);
  assert.deepEqual(saved.watchlist, { type: 'movie', state: 'all', sort: 'alpha' });
  const again = load({ saved });
  assert.deepEqual(again.rendered.at(-1), ['tt5', 'tt1', 'tt2'], 'films sorted A → Z by shown (French) title');
  assert.equal(again.elements.listSortSelect.value, 'alpha');
  const bad = load({ saved: { watchlist: { type: 'x', state: 'y', sort: 'z' } } });
  assert.deepEqual({ ...bad.view().watchlist }, { type: 'all', state: 'all', sort: 'added' });
});

test('the search matches the French title, without accents', () => {
  assert.deepEqual(load({ query: 'evades' }).rendered.at(-1), ['tt1']);
  assert.deepEqual(load({ query: 'ÉVADÉS' }).rendered.at(-1), ['tt1']);
  assert.deepEqual(load({ query: '2003' }).rendered.at(-1), ['tt2']);
  assert.deepEqual(load({ query: 'shawshank' }).rendered.at(-1), ['tt1'], 'original title still works');
});

test('when nothing matches, one button shows everything again', () => {
  const lib = load({ query: 'zzz' });
  assert.equal(lib.rendered.length, 0);
  assert.match(lib.elements.postersGrid.innerHTML, /<h3>Aucun titre ne correspond<\/h3>[\s\S]*onclick="resetListFilters\(\)">Tout afficher<\/button>/);
  lib.setListFilter('type', 'series');
  lib.resetListFilters();
  assert.equal(lib.elements.listFilterInput.value, '');
  assert.deepEqual(lib.rendered.at(-1), ['tt1', 'tt2', 'tt3', 'tt4', 'tt5']);
});

test('Déjà vus has type filters only and sorts by « Vu récemment »', () => {
  const lib = load({ tab: 'watched' });
  assert.equal(lib.option.textContent, 'Vu récemment');
  assert.doesNotMatch(lib.elements.listFilters.innerHTML, /État/);
  assert.match(lib.elements.listFilters.innerHTML, />Films <span class="list-count">1<\/span>/);
  assert.equal(lib.elements.listStatsBadge.textContent, '3 titres vus · IMDb 7,9 en moyenne');
  assert.equal(load().option.textContent, 'Ajout récent');
});

test('a group with one real choice is not shown', () => {
  const onlyFilms = watchlist.filter(m => m.type === 'movie');
  const fresh = (() => {
    const elements = { listFilterInput: { value: '' }, listSortSelect: { value: 'added', querySelector: () => null },
      listStatsBadge: { textContent: '', style: {} }, listFilters: { innerHTML: '', hidden: true }, postersGrid: { innerHTML: '' } };
    const ctx = { activeTab: 'watchlist', userWatchlist: onlyFilms, userHistory: [], document: { getElementById: id => elements[id] || null },
      localStorage: { getItem: () => null, setItem() {} }, getWatchedHistory: () => [], renderPosterCards() {}, displayTitle: m => m.name };
    vm.runInNewContext(`${listCode}\nrenderActiveListTab();`, ctx);
    return elements.listFilters;
  })();
  assert.equal(fresh.innerHTML, '', 'all films, all to watch: no filters to offer');
  assert.equal(fresh.hidden, true);
});

test('offline files show the French title, the episode, the real file name and the watch state', () => {
  const grid = { innerHTML: '' };
  const context = {
    activeTab: 'offline', escapeHtml,
    document: { getElementById: id => (id === 'postersGrid' ? grid : null) },
    displayTitle: m => ({ tt1: 'Les Évadés' })[m.id] || m.name,
    titleAttrs: m => ` data-title-id="${m.id}"`,
    userHistory: [{ id: 'tt1', completed: true }, { id: 'tt3', ep_positions: { S02E04: { pct: 40 } } }],
  };
  vm.runInNewContext(`${offlineCode}; renderOfflineLibraryData({downloads: {
    a: {id: 'aaaa1111', filename: 'aaaa1111_Shawshank.1994.1080p.mkv', title: 'The Shawshank Redemption', status: 'completed', available: true,
        bytes_done: 10, bytes_total: 10, poster: '', media: {id: 'tt1', name: 'The Shawshank Redemption', type: 'movie', year: '1994', poster: 'https://img/p.jpg'}},
    b: {id: 'bbbb2222', filename: 'bbbb2222_Black.Doves.S02E04.mkv', title: 'Black Doves', status: 'completed', available: true,
        bytes_done: 10, bytes_total: 10, poster: '/api/offline/poster/bbbb2222', media: {id: 'tt3', name: 'Black Doves', type: 'series', season: 2, episode: 4}},
    c: {id: 'cccc3333', filename: 'cccc3333_home.mp4', title: 'home.mp4', status: 'completed', available: true, bytes_done: 1, bytes_total: 1, media: {}}
  }, used_bytes: 21});`, context);
  const out = grid.innerHTML;
  assert.match(out, /<strong data-title-id="tt1">Les Évadés<\/strong><span class="offline-kind">Film · 1994 · ✓ Vu<\/span><span class="offline-file">Shawshank\.1994\.1080p\.mkv<\/span>/);
  assert.match(out, /<img src="https:\/\/img\/p\.jpg" alt="" loading="lazy" onerror="this\.remove\(\)">/, 'the catalogue poster when no local one');
  assert.match(out, /<strong data-title-id="tt3">Black Doves<\/strong><span class="offline-kind">Série · S02E04 · 40 % vu<\/span><span class="offline-file">Black\.Doves\.S02E04\.mkv<\/span>/);
  assert.match(out, /<img src="\/api\/offline\/poster\/bbbb2222"/);
  assert.match(out, /<strong>home\.mp4<\/strong><span class="offline-kind">Film<\/span><span class="offline-file">home\.mp4<\/span>/);
});

test('filters live under the title and only on the list pages', () => {
  assert.match(html, /<div id="listFilters" class="list-filters" hidden><\/div>/);
  assert.match(html, /id="listSortSelect" class="catalog-sort" aria-label="Trier la liste"[^>]*onchange="onChangeListSort\(this\.value\)"/);
  assert.match(html, /type="search" id="listFilterInput"[^>]*aria-label="Filtrer la liste par titre ou année"/);
  assert.match(source, /if \(listFiltersBox\) listFiltersBox\.hidden = !\(tab === 'watchlist' \|\| tab === 'watched'\);/);
  assert.match(css, /\.list-filters\[hidden\] \{ display: none; \}/);
});
