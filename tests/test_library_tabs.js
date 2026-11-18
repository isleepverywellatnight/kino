// The Bibliothèque. One line of tabs, all visible;
// the tab is the title of the page; quiet counts; rows without boxes; the rules
// in design.css only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const read = name => fs.readFileSync(path.join(path.join(__dirname, '..'), name), 'utf8');
const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const css = read('web/css/design.css');
const step = css.slice(css.indexOf('/* ---------- The Bibliothèque ----------'));

const TABS = [['watchlist', 'Ma Liste'], ['history', 'En cours'], ['myseries', 'Mes séries'], ['calendar', 'Calendrier'],
  ['watched', 'Déjà vus'], ['offline', 'Hors ligne'], ['customlists', 'Listes'], ['rdcloud', 'Cloud'], ['stats', 'Statistiques']];

test('one line of tabs, every page on it, in this order', () => {
  const start = html.indexOf('<nav id="librarySubNav" class="library-subnav" aria-label="Bibliothèque"');
  assert.ok(start > 0);
  const nav = html.slice(start, html.indexOf('</nav>', start));
  const found = [...nav.matchAll(/<button class="lib-subtab[^"]*" id="subtab-(\w+)" onclick="switchTab\('(\w+)'\)">([^<]+?)\s*(?:<span id="\w+" class="lib-count"><\/span>)?<\/button>/g)]
    .map(m => [m[1], m[3]]);
  assert.deepEqual(found, TABS);
  assert.ok(found.every(([id], i) => id === [...nav.matchAll(/switchTab\('(\w+)'\)/g)][i][1]));
  assert.doesNotMatch(nav, /<details|Plus/);
  for (const id of ['wlCount', 'histCount', 'watchedCount', 'offlineCount', 'customListsCount']) {
    assert.match(nav, new RegExp(`<span id="${id}" class="lib-count"></span>`), id);
  }
});

test('counts are plain numbers; the sidebar badge counts what is in progress', () => {
  const elements = {};
  const el = id => elements[id] || (elements[id] = { textContent: '?', title: '?' });
  const start = source.indexOf('function updateListBadges() {');
  const context = {
    document: { getElementById: el },
    window: { customLists: [1, 2] },
    userWatchlist: new Array(19).fill({}),
    getInProgressHistory: () => new Array(5).fill({}),
    getWatchedHistory: () => new Array(10).fill({}),
    String,
  };
  vm.runInNewContext(`${source.slice(start, source.indexOf('\n}\n', start) + 3)} this.run = updateListBadges;`, context);
  context.run();
  assert.deepEqual([elements.wlCount.textContent, elements.histCount.textContent, elements.watchedCount.textContent, elements.customListsCount.textContent],
    ['19', '5', '10', '2']);
  assert.equal(elements.libraryTotalBadge.textContent, '5', 'not the sum of three lists');
  assert.equal(elements.libraryTotalBadge.title, '5 lectures en cours');
  context.getInProgressHistory = () => [];
  context.userWatchlist = [];
  context.run();
  assert.deepEqual([elements.wlCount.textContent, elements.libraryTotalBadge.textContent, elements.libraryTotalBadge.title], ['', '', '']);
  assert.doesNotMatch(source, /Count'\);\n\s+if \(\w+\) \w+\.textContent = [^\n]*`\(\$\{/, 'no count in parentheses left');
});

test('the active tab is announced and kept in view', () => {
  const start = source.indexOf('async function switchTab(tab) {');
  const body = source.slice(start, source.indexOf('async function loadCatalog', start));
  assert.match(body, /activeSub\.classList\.add\('active'\);\n\s+activeSub\.setAttribute\('aria-current', 'page'\);/);
  assert.match(body, /t\.classList\.remove\('active'\);\n\s+t\.removeAttribute\('aria-current'\);\n\s+\}\);\n\s+const activeSub/);
  assert.match(body, /activeSub\.scrollIntoView\(\{ block: 'nearest', inline: 'nearest' \}\)/);
  assert.doesNotMatch(source, /libraryMore/);
});

test('the same names everywhere: tabs, « Retour », the ⌘K panel', () => {
  assert.match(source, /customlists: 'Listes', rdcloud: 'Cloud',\n\s+stats: 'Statistiques'/);
  assert.match(source, /\{ label: 'Listes', icon: 'library', tab: 'customlists', hint: 'Bibliothèque'/);
  assert.match(source, /\{ label: 'Statistiques', icon: 'pulse', tab: 'stats', hint: 'Bibliothèque', words: 'stats mes statistiques chiffres' \}/);
});

test('the tab is the title: a summary line and the tools, without inline looks', () => {
  assert.match(html, /<span id="listStatsBadge" class="lib-summary" style="display:none;"><\/span>/);
  assert.match(html, /<div id="watchlistActionsWrap" class="lib-tools" style="display:none;">/);
  assert.match(withCalls(html), /<input type="search" id="listFilterInput"[^>]*oninput="renderActiveListTab\(\)">/);
  assert.doesNotMatch(html.slice(html.indexOf('id="watchlistActionsWrap"'), html.indexOf('id="listFilters"')), /font-size|#ff8000|#00e054|#40bcf4/,
    'no inline sizes, no Letterboxd colours: colour comes from the posters only');
  assert.match(html, /<h2 class="explore-title">Statistiques<\/h2>\n\s+<p class="lib-summary">/);
  assert.match(html, /<div id="rdCloudPanel" class="panel lib-panel" style="display:none;">\n\s+<div class="lib-panel-head">/);
  // The repeated title is hidden only while the tabs are shown, and stays for screen readers.
  assert.match(step, /#librarySubNav\[style\*="flex"\] ~ #catalogHeader #catalogTitle:not\(:has\(\.lists-title\)\),\n\.stats-head \.explore-title \{\n  position: absolute;\n  width: 1px;\n  height: 1px;/);
  assert.doesNotMatch(step, /#catalogTitle[^{]*\{[^}]*display: none/, 'never removed from the page');
  assert.match(source, /isHistoryPage \? `\$\{inProg\.length\} lecture\$\{inProg\.length > 1 \? 's' : ''\} en cours` : 'À continuer'/);
});

test('tabs in text with an underline; rows parted by a thin line, without boxes', () => {
  assert.match(step, /\.library-subnav \{[^}]*overflow-x: auto;[^}]*border-bottom: 1px solid var\(--k-line\);[^}]*background: none;/);
  assert.match(step, /\.lib-subtab\.active::after \{[^}]*height: 2px;[^}]*background: var\(--k-text\);/);
  const tab = step.match(/\.lib-subtab \{[^}]*\}/)[0];
  const active = step.match(/\.lib-subtab\.active \{[^}]*\}/)[0];
  assert.match(tab, /font-weight: 600;/);
  assert.doesNotMatch(active, /font-weight/, 'the active tab keeps its width: the others do not move');
  for (const row of ['.offline-item', '.cloud-group']) {
    const rule = step.match(new RegExp(`\\${row} \\{[^}]*\\}`))[0];
    assert.match(rule, /border: 0;\n  border-bottom: 1px solid var\(--k-line\);/, row);
    assert.match(rule, /background: none;/, row);
  }
  for (const tile of ['.stats-tile', '.stats-card']) {
    assert.match(step.match(new RegExp(`\\${tile} \\{[^}]*\\}`))[0], /border: 0;.*background: var\(--k-surface-1\);/, tile);
  }
  assert.match(step, /\.lib-panel \{[^}]*padding: 0;[^}]*border: 0;[^}]*background: none;/);
  assert.match(step, /@media \(max-width: 900px\) \{\n  \.library-subnav \{\n    padding-right: 32px;\n    -webkit-mask-image:/);
});

test('this step uses the shared values only', () => {
  const end = step.indexOf('/* ----------', 10);
  const own = end > 0 ? step.slice(0, end) : step;
  const colours = [...own.matchAll(/#[0-9a-f]{3,8}\b|rgba?\([^)]*\)/gi)].map(m => m[0]);
  assert.deepEqual([...new Set(colours)], ['#000'], 'only the mask of the fading tab line');
  const sizes = [...own.matchAll(/font-size: ([^;]+);/g)].map(m => m[1]);
  assert.ok(sizes.every(size => size.startsWith('var(--k-fs-') || size === '2rem'), `sizes from the scale: ${[...new Set(sizes)]}`);
});

test('the older sheets hold no library rule any more', () => {
  // The part folded in from the earlier sheets.
  const old = css.slice(css.indexOf('The earlier sheets, folded in'), css.indexOf('\nbody {\n  background: radial-gradient'));
  assert.doesNotMatch(old, /\.library-subnav|\.lib-subtab|\.library-more|\.list-filter|\.offline-|\.myseries-|\.calendar-|\.cloud-|\.stats-|\.lists-title|\.btn-surprise|\.catalog-sort/);
});
