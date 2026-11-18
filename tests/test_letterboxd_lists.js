// Letterboxd lists: one shelf per list (progress, first posters), then a full
// page with Ma Liste's tools (state filter, French search, list order).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const listsCode = source.slice(source.indexOf('function renderCustomListsTab() {'), source.indexOf('// The Anime tab (rows, planning) lives in anime-home.js.'));
const listToolsCode = source.slice(source.indexOf('function surpriseMeFromCurrentList() {'), source.indexOf('async function surpriseMeMedia(poolArg = null) {'));
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

const films = Array.from({ length: 15 }, (_, index) => ({ id: `tt${index + 1}`, name: `Film ${index + 1}`, type: 'movie', year: String(1990 + index) }));
films[0].name = 'The Shawshank Redemption';

function load({ lists, history = [] } = {}) {
  const elements = {};
  const element = id => elements[id] || (elements[id] = {
    id, style: {}, innerHTML: '', textContent: '', hidden: true, value: '',
    querySelector: () => (id === 'listSortSelect' ? elements.addedOption : null),
  });
  elements.addedOption = { textContent: '' };
  element('listSortSelect').value = 'added';
  const calls = [];
  const rendered = [];
  const context = {
    activeTab: 'customlists', userWatchlist: [], userHistory: history, userCustomLists: lists,
    document: { getElementById: element },
    window: { scrollTo() {} },
    localStorage: { getItem: () => null, setItem() {} },
    escapeHtml, attrJson: value => escapeHtml(JSON.stringify(value)),
    posterCardHtml: m => `<div class="poster-card" data-id="${m.id}">${escapeHtml(m.name)}</div>`,
    renderPosterCards: list => rendered.push(Array.from(list, m => m.id)),
    displayTitle: m => (m.id === 'tt1' ? 'Les Évadés' : m.name),
    getWatchedHistory: () => [],
    surpriseMeMedia: options => calls.push(['roulette', Array.from(options.items, m => m.id), options.title]),
  };
  vm.runInNewContext(`${listToolsCode}\n${listsCode}\nthis.api_ = { renderCustomListsTab, openCustomList, closeCustomList, surpriseMeFromCustomList, surpriseMeFromCurrentList, setListFilter, open: () => openCustomListId };`, context);
  return { ...context.api_, elements: new Proxy({}, { get: (_, id) => element(id) }), calls, rendered };
}

const lists = () => [
  { id: 'top', title: 'Top <250>', description: 'Les « classiques » & plus', url: 'https://letterboxd.com/u/list/top/', items: films },
  { id: 'empty', title: 'Vide', description: '', url: 'javascript:alert(1)', items: [] },
];
const history = [{ id: 'tt1', completed: true }, { id: 'tt2', progress_pct: 100, completed: true }, { id: 'tt3', progress_pct: 20 }];

test('each list is a shelf with its progress, first posters and actions', () => {
  const lib = load({ lists: lists(), history });
  lib.renderCustomListsTab();
  const out = lib.elements.postersGrid.innerHTML;
  const [top, empty] = out.split('<section').slice(1);
  assert.match(top, /class="list-shelf" aria-label="Top &lt;250&gt;"/);
  assert.match(top, /<div class="list-shelf-meta">15 titres · 2 vus<\/div>/);
  assert.match(top, /aria-valuenow="13"><span style="width:13%"><\/span>/);
  assert.match(top, /<p class="list-shelf-desc">Les « classiques » &amp; plus<\/p>/);
  assert.equal((top.match(/class="poster-card"/g) || []).length, 12, 'twelve posters on the shelf');
  assert.match(withCalls(top), /onclick="openCustomList\('top'\)">Voir les 15 titres<\/button>/);
  assert.match(withCalls(top), /onclick="openCustomList\('top'\)">Tout voir<\/button>/);
  assert.match(top, /href="https:\/\/letterboxd\.com\/u\/list\/top\/"/);
  assert.match(top, /aria-label="Retirer la liste Top &lt;250&gt;">Retirer<\/button>/);
  assert.match(empty, /Cette liste est vide\./);
  assert.doesNotMatch(empty, /javascript:/, 'only Letterboxd addresses become links');
  assert.equal(lib.elements.watchlistActionsWrap.style.display, 'none');
  assert.equal(lib.elements.listFilters.hidden, true);
});

test('opening a list gives it Ma Liste\'s tools, in list order', () => {
  const lib = load({ lists: lists(), history });
  lib.openCustomList('top');
  assert.equal(lib.open(), 'top');
  assert.match(withCalls(lib.elements.catalogTitle.innerHTML), /onclick="closeCustomList\(\)" aria-label="Revenir aux listes">← Listes<\/button><span>Top &lt;250&gt;<\/span>/);
  assert.equal(lib.elements.watchlistActionsWrap.style.display, 'flex');
  assert.equal(lib.elements.listSurpriseBtnLabel.textContent, 'Roulette sur cette liste');
  assert.equal(lib.elements.addedOption.textContent, 'Ordre de la liste');
  assert.equal(lib.elements.listStatsBadge.textContent, '15 titres · 13 à voir');
  assert.deepEqual(lib.rendered.at(-1), films.map(m => m.id));
  assert.match(lib.elements.listFilters.innerHTML, />À voir <span class="list-count">12<\/span>/);
  assert.match(lib.elements.listFilters.innerHTML, />Commencés <span class="list-count">1<\/span>/);
  lib.setListFilter('state', 'watched');
  assert.deepEqual(lib.rendered.at(-1), ['tt1', 'tt2']);
  lib.setListFilter('state', 'all');
  lib.elements.listFilterInput.value = 'evades';
  lib.setListFilter('state', 'all');
  assert.deepEqual(lib.rendered.at(-1), ['tt1'], 'French title search');
});

test('the back button and an unknown list return to the shelves', () => {
  const lib = load({ lists: lists(), history });
  lib.openCustomList('top');
  lib.closeCustomList();
  assert.equal(lib.open(), null);
  assert.match(lib.elements.postersGrid.innerHTML, /list-shelf/);
  lib.openCustomList('gone');
  assert.equal(lib.open(), null, 'a removed list falls back to the overview');
});

test('the roulette skips watched titles, from a shelf or the open list', () => {
  const lib = load({ lists: lists(), history });
  lib.surpriseMeFromCustomList('top');
  assert.deepEqual(lib.calls[0][1], films.map(m => m.id).filter(id => id !== 'tt1' && id !== 'tt2'));
  assert.equal(lib.calls[0][2], 'Top <250>');
  lib.openCustomList('top');
  lib.surpriseMeFromCurrentList();
  assert.equal(lib.calls.length, 2);
  assert.doesNotMatch(source, /isWatchedMedia/, 'the helper that never existed is gone');
});

test('no list: an invitation to import one', () => {
  const lib = load({ lists: [] });
  lib.renderCustomListsTab();
  assert.match(lib.elements.postersGrid.innerHTML, /<h3>Aucune liste Letterboxd importée<\/h3>/);
});

test('the page is monochrome and the overview opens first', () => {
  assert.doesNotMatch(listsCode, /#00e054|#f87171|rgba\(0,\s*224,\s*84/);
  assert.match(source, /if \(tab === 'customlists'\) openCustomListId = null;/);
  assert.match(css, /\.list-shelf-row \{[^}]*grid-auto-flow: column;[^}]*overflow-x: auto;/);
  assert.match(css, /\.list-shelf-progress > span \{ display: block; height: 100%; background: #fafafa; \}/);
});

test('an import says how many removed films it left out, and can bring them back', async () => {
  const { loadPage, settle } = require('../tools/page_harness.js');
  const bodies = [];
  const page = await loadPage((url, options = {}) => {
    if (url === '/api/import-letterboxd') {
      const body = JSON.parse(options.body);
      bodies.push(body);
      return body.restore_removed
        ? { watchlist: [], history: [], found_watched_count: 53, added_watched_count: 51, removed_watched_count: 0 }
        : { watchlist: [], history: [], found_watched_count: 53, added_watched_count: 2, removed_watched_count: 51 };
    }
    return {};
  });
  await settle();
  page.element('lbxUrlInput').value = 'cinephile';
  await page.context.submitLetterboxdImport();
  assert.equal(bodies[0].restore_removed, false);
  assert.equal(page.element('lbxImportStatus').textContent,
    '✓ Synchronisation réussie : 2 dans Déjà vus. 51 films que vous aviez retirés de KINO n’ont pas été réimportés.');
  assert.equal(page.element('lbxRestoreRemovedBtn').hidden, false, 'the dialog stays open with the button');

  await page.context.submitLetterboxdImport({ restoreRemoved: true });
  assert.equal(bodies[1].restore_removed, true);
  assert.equal(page.element('lbxImportStatus').textContent, '✓ Synchronisation réussie : 51 dans Déjà vus.');
  assert.equal(page.element('lbxRestoreRemovedBtn').hidden, true);
  const html = require('../tools/interface_source.js').indexHtml();
  assert.match(withCalls(html), /<button type="button" id="lbxRestoreRemovedBtn" class="btn btn-secondary btn-small" hidden onclick="submitLetterboxdImport\(\{ restoreRemoved: true \}\)">Les réimporter aussi<\/button>/);
});

test('an import also says which wrong films it took back and which titles it could not find', async () => {
  const { loadPage, settle } = require('../tools/page_harness.js');
  const page = await loadPage(url => (url === '/api/import-letterboxd'
    ? { watchlist: [], history: [], found_watched_count: 3, added_watched_count: 2, corrected_watched_count: 2, unmatched_count: 1 }
    : {}));
  await settle();
  page.element('lbxUrlInput').value = 'cinephile';
  await page.context.submitLetterboxdImport();
  assert.equal(page.element('lbxImportStatus').textContent,
    '✓ Synchronisation réussie : 2 dans Déjà vus. 2 films mal reconnus par un import précédent ont été retirés. 1 titre n’a pas été trouvé dans le catalogue.');
  assert.equal(page.element('lbxRestoreRemovedBtn').hidden, true, 'nothing to bring back here');
});
