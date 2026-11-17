// Navigation: the search lives in the top
// bar, and its panel (⌘K) offers recent searches, KINO's screens and the titles.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const oldCss = ['style.css', 'redesign.css'].map(f => fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css', f), 'utf8')).join('\n');
const start = source.indexOf('function updateSearchClearBtn() {');
const code = source.slice(start, source.indexOf('async function runSearch() {', start));
const runCode = source.slice(source.indexOf('async function runSearch() {'), source.indexOf('async function oneClickCard('));

function load({ storage = {}, brokenStorage = false } = {}) {
  const elements = {};
  const el = id => elements[id] || (elements[id] = {
    id, value: '', innerHTML: '', placeholder: '', style: {}, attrs: {}, blurred: 0, focused: 0,
    options: [{ value: 'all' }, { value: 'movie' }, { value: 'series' }, { value: 'anime' }, { value: 'raw' }, { value: 'magnet' }],
    setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    blur() { this.blurred++; }, focus() { this.focused++; }, click() { this.clicked = true; },
  });
  el('searchType').value = 'all';
  const calls = { tabs: [], searches: [], magnets: [], opened: [], asked: [] };
  const answers = {};
  const context = {
    document: { getElementById: el, querySelectorAll: () => [], addEventListener: () => {} },
    localStorage: {
      getItem: key => { if (brokenStorage) throw new Error('blocked'); return key in storage ? storage[key] : null; },
      setItem: (key, value) => { if (brokenStorage) throw new Error('blocked'); storage[key] = String(value); },
      removeItem: key => { delete storage[key]; },
    },
    api: async url => { calls.asked.push(url); return answers.next || { results: [] }; },
    escapeHtml: s => String(s), titleAttrs: () => '', displayTitle: it => it.name, rememberFrenchTitles: () => {},
    switchTab: tab => calls.tabs.push(tab), selectMedia: it => calls.opened.push(it.name),
    debridMagnet: q => calls.magnets.push(q), openTonightModal: () => calls.tabs.push('tonight'),
    openDiagnostic: () => {}, toggleShortcutsModal: () => {}, noteSearch: () => {},
    networkOnline: true, activeTab: 'movies', searchResultsShown: false,
    clearTimeout: () => {}, setTimeout: () => 0,
    runSearch: () => { calls.searches.push([el('searchInput').value, el('searchType').value]); },
  };
  vm.runInNewContext(`let searchSuggestItems = []; let searchSuggestIndex = -1; let searchDebounceTimer = null;
${code}
this.api_ = { searchPanelEntries, matchingPlaces, rememberSearch, recentSearches, renderSearchPanel, selectSearchSuggest,
  onSearchKeyDown, onSearchInput, openSearchPanel, items: () => searchSuggestItems };`, context);
  return { ...context.api_, el, calls, storage, answers, context };
}

const plain = value => JSON.parse(JSON.stringify(value));
const labels = entries => plain(entries).map(e => e.kind === 'place' ? `place:${e.place.label}` : e.kind === 'title' ? `title:${e.item.name}` : `${e.kind}:${e.query}`);

test('the search sits in the top bar, « Torrent » in « Outils »', () => {
  const header = html.slice(html.indexOf('<header'), html.indexOf('</header>'));
  assert.ok(header.indexOf('class="search-box no-drag"') > header.indexOf('class="header-left"'));
  assert.ok(header.indexOf('class="search-box no-drag"') < header.indexOf('class="header-right'));
  assert.doesNotMatch(html.slice(html.indexOf('<div class="container">')), /class="search-box/, 'no second search box in the page');
  assert.doesNotMatch(html, /header-btn-torrent|btn-search-bar/);
  assert.match(html, /<div id="headerDropdownMenu" class="header-dropdown-menu">\s*<div class="header-dropdown-item" onclick="openTorrentFile\(\); closeHeaderMenu\(\);">/);
  assert.match(html, /onfocus="openSearchPanel\(\)"/);
  assert.match(html, /ondblclick="if \(!event\.target\.closest\('\.no-drag'\)\) windowAction\('maximize'\)"/, 'a double click in the field does not zoom the window');
  assert.doesNotMatch(oldCss, /^\s*(header|\.nav-tabs?|\.search-box|\.header-[\w-]+|\.logo[\w-]*)\s*[,{]/m, 'the old sheets hold no navigation rule');
  assert.match(css, /grid-template-columns: var\(--k-sidebar-w\) minmax\(0, 1fr\) auto;/);
  assert.match(css, /body\.is-mac-native \.header-left \{ padding-left: 86px; \}/, 'room for the Mac\'s own window buttons');
});

test('before typing: the recent searches, then four screens', () => {
  const page = load({ storage: { kino_recent_searches: JSON.stringify([{ query: 'Dune', mode: 'all' }, { query: 'Lupin', mode: 'series' }]) } });
  assert.deepEqual(labels(page.searchPanelEntries('')), ['recent:Dune', 'recent:Lupin', 'place:Ce soir', 'place:Ma Liste', 'place:En cours', 'place:Paramètres']);
  page.renderSearchPanel('');
  const panel = page.el('searchDropdown').innerHTML;
  assert.match(panel, /<span>Recherches récentes<\/span><button type="button" class="search-panel-clear"/);
  assert.match(panel, /<span class="search-row-hint">Séries<\/span>/, 'a search made in « Séries » says so');
  assert.equal(page.el('searchDropdown').style.display, 'block');
});

test('while typing: the screens that fit, the titles, then « Tout voir »', () => {
  const page = load();
  assert.deepEqual(labels(page.searchPanelEntries('param')), ['place:Paramètres', 'all:param', 'raw:param']);
  assert.deepEqual(plain(page.matchingPlaces('telech')).map(p => p.label), ['Hors ligne'], 'no accents needed');
  assert.deepEqual(plain(page.matchingPlaces('ma li')).map(p => p.label), ['Ma Liste']);
  assert.deepEqual(labels(page.searchPanelEntries('dune', [{ name: 'Dune', type: 'movie' }, { name: 'Dune', type: 'series' }])),
    ['title:Dune', 'title:Dune', 'all:dune', 'raw:dune']);
  assert.deepEqual(labels(page.searchPanelEntries('magnet:?xt=urn:btih:abc')), ['magnet:magnet:?xt=urn:btih:abc']);
});

test('each line does what it says', () => {
  const page = load();
  page.el('searchInput').value = 'param';
  page.renderSearchPanel('param');
  page.selectSearchSuggest(0);
  assert.deepEqual(page.calls.tabs, ['settings']);
  assert.equal(page.el('searchInput').value, '', 'leaving for a screen empties the field');

  page.el('searchInput').value = 'dune';
  page.renderSearchPanel('dune');
  page.selectSearchSuggest(page.items().findIndex(e => e.kind === 'raw'));
  assert.deepEqual(page.calls.searches.at(-1), ['dune', 'raw'], 'the sources, by keywords');
  assert.equal(page.el('searchType').value, 'all', 'the scope comes back');

  page.renderSearchPanel('dune', [{ name: 'Dune, deuxième partie', type: 'movie' }]);
  page.selectSearchSuggest(0);
  assert.deepEqual(page.calls.opened, ['Dune, deuxième partie']);
});

test('recent searches: five at most, no double, never a magnet link, and no storage is fine', () => {
  const page = load();
  for (const q of ['Dune', 'Lupin', 'dune', 'Alien', 'Heat', 'Up', 'Her', 'magnet:?xt=urn:btih:x']) page.rememberSearch(q, 'all');
  assert.deepEqual(plain(page.recentSearches().map(r => r.query)), ['Her', 'Up', 'Heat', 'Alien', 'dune']);
  const broken = load({ brokenStorage: true });
  broken.rememberSearch('Dune', 'all');
  assert.deepEqual(plain(broken.recentSearches()), []);
});

test('the keyboard: arrows and Entrée in the panel, Échap closes it then leaves the field', () => {
  const page = load();
  const key = (k, target = page.el('searchInput')) => {
    const event = { key: k, target, prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
    page.onSearchKeyDown(event);
    return event;
  };
  page.el('searchInput').value = 'param';
  page.renderSearchPanel('param');
  key('ArrowDown');
  key('Enter');
  assert.deepEqual(page.calls.tabs, ['settings']);
  page.renderSearchPanel('');
  const first = key('Escape');
  assert.equal(page.el('searchDropdown').style.display, 'none');
  assert.equal(first.stopped, true, 'the screen behind does not close');
  key('Escape');
  assert.equal(page.el('searchInput').blurred, 2, 'the second one leaves the field');
  key('ArrowDown');
  assert.equal(page.el('searchDropdown').style.display, 'block', '↓ opens the panel again');
});

test('titles that arrive after Entrée do not open the panel again', async () => {
  const page = load();
  page.el('searchInput').value = 'dune';
  page.answers.next = { results: [{ name: 'Dune', type: 'movie' }] };
  page.onSearchInput();
  page.onSearchKeyDown({ key: 'Enter', target: page.el('searchInput'), preventDefault() {}, stopPropagation() {} });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.el('searchDropdown').style.display, 'none');
  assert.deepEqual(page.calls.asked, ['/api/search-suggest?q=dune&type=all']);
});

test('a pasted magnet link opens as such, whatever the scope', async () => {
  const page = load();
  page.el('searchInput').value = 'magnet:?xt=urn:btih:abcdef';
  vm.runInContext(`${runCode}\nthis.realRun = runSearch;`, page.context);
  await page.context.realRun();
  assert.deepEqual(page.calls.magnets, ['magnet:?xt=urn:btih:abcdef']);
});

test('⌘K and / open the panel', () => {
  assert.match(source, /e\.key\.toLowerCase\(\) === 'k'\) \{\n\s+e\.preventDefault\(\);\n\s+const si = document\.getElementById\('searchInput'\);\n\s+if \(si\) \{ si\.focus\(\); si\.select\(\); openSearchPanel\(\); \}/);
  assert.match(source, /if \(e\.key === '\/' && !isInput\) \{[\s\S]{0,200}openSearchPanel\(\);/);
});
