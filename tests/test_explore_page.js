// Explorer: the imported Letterboxd lists, then the curated selections, each a
// shelf (progress, first posters, Tout voir, Roulette); a selection opens in
// full with a state filter.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const exploreCode = slice('// --- Explorer: imported Letterboxd lists and curated selections, as shelves ---', 'async function importCustomLetterboxdFromInput() {');
const progressCode = slice('function customListProgress(items) {', 'function customListShelfHtml(list) {');
const escapeHtml = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

const films = count => Array.from({ length: count }, (_, index) => ({ id: `tt${index + 1}`, name: `Film ${index + 1}`, type: 'movie' }));
const curated = () => [
  { id: 'top', title: 'Top <250>', author: 'Communauté Letterboxd', description: 'Les « meilleurs » & plus', items: films(20) },
  { id: 'empty', title: 'Vide', author: '', description: '', items: [] },
];
const imported = () => [{ id: "u_list_l'été", title: 'Mon été', description: '', items: films(3) }];

function load({ history = [], custom = imported(), failCurated = false } = {}) {
  const elements = {};
  const element = id => elements[id] || (elements[id] = { id, innerHTML: '', textContent: '', hidden: false });
  const calls = [];
  const context = {
    document: { getElementById: element },
    window: { scrollTo: () => calls.push(['scroll']) },
    LIST_SHELF_SIZE: 12, userHistory: history,
    escapeHtml, attrJson: value => escapeHtml(JSON.stringify(value)),
    posterCardHtml: (m, type) => `<card ${m.id} ${type}>`,
    listItemState: m => (history.some(h => h.id === m.id) ? 'watched' : 'todo'),
    api: async route => {
      if (route === '/api/community/curated') {
        if (failCurated) throw new Error('hors ligne');
        return { collections: curated() };
      }
      return { custom_lists: custom };
    },
    switchTab: async tab => calls.push(['tab', tab]),
    openCustomList: id => calls.push(['list', id]),
    surpriseMeMedia: options => calls.push(['roulette', Array.from(options.items, m => m.id), options.title]),
    alert: message => calls.push(['alert', message]),
  };
  vm.runInNewContext(`${progressCode}\n${exploreCode}\nthis.api_ = { loadCommunityTab, openCommunityList, closeCommunityActiveList, setCommunityStateFilter, openImportedList, surpriseMeFromCommunityList, surpriseMeFromOpenCommunityList, open: () => openCommunityListId };`, context);
  return { ...context.api_, elements: new Proxy({}, { get: (_, id) => element(id) }), calls };
}

const history = [{ id: 'tt1' }, { id: 'tt2' }];

test('imported lists first, then the selections, each a shelf', async () => {
  const page = load({ history });
  await page.loadCommunityTab();
  const out = page.elements.communityListsGrid.innerHTML;
  assert.ok(out.indexOf('Vos listes Letterboxd') < out.indexOf('Mon été'));
  assert.ok(out.indexOf('Mon été') < out.indexOf('>Sélections<'));
  const [mine, top, empty] = out.split('<section').slice(1);
  assert.match(mine, /<div class="list-shelf-meta">3 titres · 2 vus<\/div>/);
  assert.match(withCalls(mine), /onclick="openImportedList\('u_list_l\\'été'\)">Tout voir<\/button>/, 'the id is passed as data');
  assert.match(top, /class="list-shelf" aria-label="Top &lt;250&gt;"/);
  assert.match(top, /<div class="list-shelf-meta">20 titres · 2 vus · par Communauté Letterboxd<\/div>/);
  assert.match(top, /aria-valuenow="10"><span style="width:10%"><\/span>/);
  assert.match(top, /<p class="list-shelf-desc">Les « meilleurs » &amp; plus<\/p>/);
  assert.equal((top.match(/<card /g) || []).length, 12, 'twelve posters on the shelf');
  assert.match(withCalls(top), /onclick="openCommunityList\('top'\)">Voir les 20 titres<\/button>/);
  assert.match(withCalls(top), /onclick="surpriseMeFromCommunityList\('top'\)"/);
  assert.match(empty, /Cette sélection est vide\./);
  assert.equal(page.elements.communityListsGrid.hidden, false);
  assert.equal(page.elements.communityActiveListSection.hidden, true);
});

test('a selection opens in full, with a state filter, and closes', async () => {
  const page = load({ history });
  await page.loadCommunityTab();
  page.openCommunityList('top');
  assert.equal(page.open(), 'top');
  assert.equal(page.elements.communityListsGrid.hidden, true);
  assert.equal(page.elements.communityActiveListSection.hidden, false);
  assert.equal(page.elements.communityActiveListTitle.textContent, 'Top <250>');
  assert.equal(page.elements.communityActiveListMeta.textContent, '20 titres · 2 vus · par Communauté Letterboxd');
  assert.equal((page.elements.communityActiveListGrid.innerHTML.match(/<card /g) || []).length, 20);
  assert.match(page.elements.communityActiveListFilters.innerHTML, />À voir <span class="list-count">18<\/span>/);
  page.setCommunityStateFilter('watched');
  assert.equal(page.elements.communityActiveListGrid.innerHTML, '<card tt1 movie><card tt2 movie>');
  assert.match(withCalls(page.elements.communityActiveListFilters.innerHTML), /class="chip active" aria-pressed="true" onclick="setCommunityStateFilter\('watched'\)"/);
  page.openCommunityList('empty');
  assert.match(page.elements.communityActiveListGrid.innerHTML, /Cette sélection est vide\./);
  assert.equal(page.elements.communityActiveListDesc.hidden, true);
  page.closeCommunityActiveList();
  assert.equal(page.open(), null);
  assert.equal(page.elements.communityListsGrid.hidden, false);
  assert.equal(page.elements.communityActiveListSection.hidden, true);
  page.openCommunityList('gone');
  assert.equal(page.open(), null, 'an unknown selection keeps the shelves');
});

test('coming back to Explorer shows the shelves again', async () => {
  const page = load();
  await page.loadCommunityTab();
  page.openCommunityList('top');
  await page.loadCommunityTab();
  assert.equal(page.open(), null);
  assert.equal(page.elements.communityActiveListSection.hidden, true);
});

test('an imported list opens on its own page in the library', async () => {
  const page = load();
  await page.loadCommunityTab();
  await page.openImportedList("u_list_l'été");
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls)), [['tab', 'customlists'], ['list', "u_list_l'été"]]);
});

test('the roulette draws among titles not seen yet', async () => {
  const page = load({ history });
  await page.loadCommunityTab();
  page.surpriseMeFromCommunityList('top');
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls[0])),
    ['roulette', films(20).map(m => m.id).filter(id => id !== 'tt1' && id !== 'tt2'), 'Top <250>']);
  page.openCommunityList('top');
  page.surpriseMeFromOpenCommunityList();
  assert.equal(page.calls.filter(call => call[0] === 'roulette').length, 2);
  page.surpriseMeFromCommunityList('empty');
  assert.deepEqual(page.calls.at(-1), ['alert', 'Cette sélection ne contient aucun titre.']);
});

test('an error is shown as text', async () => {
  const page = load({ failCurated: true });
  await page.loadCommunityTab();
  assert.equal(page.elements.communityListsGrid.innerHTML, '<p class="list-shelf-empty">Erreur : hors ligne</p>');
});

test('the page: a form to import, shelves, one selection in full, monochrome', () => {
  const panel = html.slice(html.indexOf('<div id="communityPanel"'), html.indexOf('<!-- Panneau Planning Simulcast Anime -->'));
  assert.match(withCalls(panel), /<form class="explore-import" onsubmit="kinoSubmit\(event, 'importCustomLetterboxdFromInput'\)">/);
  assert.match(panel, /<button type="submit" class="btn">Importer<\/button>/);
  assert.match(panel, /<div id="communityListsGrid" class="explore-shelves"><\/div>/);
  assert.match(panel, /<section id="communityActiveListSection" class="explore-open" hidden>/);
  assert.match(withCalls(panel), /onclick="closeCommunityActiveList\(\)" aria-label="Revenir à Explorer">← Explorer<\/button>/);
  assert.match(panel, /<div id="communityActiveListGrid" class="posters-grid"><\/div>/);
  assert.doesNotMatch(panel + exploreCode, /#00e054|#4ade80|#ef4444|rgba\(0,\s*224/);
  assert.match(css, /\.explore-open:not\(\[hidden\]\) \{ display: flex;/);
  assert.match(source, /\} else if \(activeTab === 'community'\) \{\n\s+loadCommunityTab\(\);/, 'an import refreshes Explorer');
});
