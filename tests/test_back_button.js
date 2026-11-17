// Retour: KINO remembers the screens visited — a tab, a search, an actor's page,
// with or without a fiche open — and comes back one step at a time, where the
// page was scrolled. The real interface scripts run on a stand-in page.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { loadPage, settle, shown } = require('../tools/page_harness.js');

const source = require('../tools/interface_source.js').appSource();
const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const FILM = { id: 'tt1', name: 'Dune: Part Two', name_fr: 'Dune : Deuxième partie', type: 'movie', year: '2024' };
const OTHER = { id: 'tt2', name: 'Doctor Sleep', type: 'movie', year: '2019' };
const PERSON = { name: 'Rebecca Ferguson', photo: '', description: '', bio: '', movies: [{ ...OTHER, roles: ['cast'] }], series: [], others: [] };

async function start() {
  const page = await loadPage(url => {
    if (url.startsWith('/api/person')) return PERSON;
    if (url.startsWith('/api/catalog')) return { metas: [FILM, OTHER] };
    if (url.startsWith('/api/search')) return { metas: [FILM] };
    return {};
  });
  const scrolls = [];
  page.context.scrollTo = (options) => { scrolls.push(typeof options === 'object' ? options.top : options); };
  page.scrolls = scrolls;
  return page;
}
const stack = page => JSON.parse(JSON.stringify(vm.runInContext('navStack', page.context)));
const where = page => stack(page).map(s => `${s.kind}:${s.key}${s.fiche ? `+${s.fiche.id}` : ''}`);
const back = page => page.element('navBackBtn');
const ficheOpen = page => page.element('detailPanel').style.display === 'block';

test('the first screen gives no way back; a fiche does, named after where it leads', async () => {
  const page = await start();
  assert.deepEqual(where(page), ['tab:movies']);
  assert.equal(back(page).hidden, true);
  page.context.scrollY = 1200;
  await page.context.selectMedia(FILM);
  assert.deepEqual(where(page), ['tab:movies', 'tab:movies+tt1']);
  assert.equal(back(page).hidden, false);
  assert.equal(back(page).title, 'Retour : Films (Échap)');
  assert.equal(back(page).getAttribute('aria-label'), 'Retour : Films');
  assert.equal(stack(page)[0].scrollY, 1200, 'where the catalogue was left');
});

test('back from a fiche closes it and puts the catalogue where it was', async () => {
  const page = await start();
  page.context.scrollY = 1200;
  await page.context.selectMedia(FILM);
  page.scrolls.length = 0;
  assert.equal(await page.context.goBack(), true);
  assert.equal(ficheOpen(page), false);
  assert.deepEqual(where(page), ['tab:movies']);
  assert.equal(page.scrolls[0], 1200);
  assert.equal(back(page).hidden, true);
  assert.equal(await page.context.goBack(), false, 'nothing before the first screen');
});

test('fiche, actor, another fiche: each step comes back in order', async () => {
  const page = await start();
  await page.context.selectMedia(FILM);
  await page.context.openPerson('Rebecca Ferguson');
  await settle();
  await page.context.selectMedia(OTHER);
  assert.deepEqual(where(page), ['tab:movies', 'tab:movies+tt1', 'person:Rebecca Ferguson', 'person:Rebecca Ferguson+tt2']);
  assert.equal(back(page).title, 'Retour : Rebecca Ferguson (Échap)');
  await page.context.goBack();
  assert.equal(ficheOpen(page), false);
  assert.equal(page.element('catalogTitle').textContent, 'Rebecca Ferguson', 'the actor page, still there');
  assert.equal(back(page).title, 'Retour : Dune: Part Two (Échap)');
  await page.context.goBack();
  await settle();
  assert.equal(ficheOpen(page), true);
  assert.equal(vm.runInContext('currentMedia.id', page.context), 'tt1');
  assert.equal(vm.runInContext('activeTab', page.context), 'movies');
  assert.deepEqual(where(page), ['tab:movies', 'tab:movies+tt1']);
  await page.context.goBack();
  assert.deepEqual(where(page), ['tab:movies']);
});

test('a search, then a fiche from it, then back to the results and the tab', async () => {
  const page = await start();
  await page.context.switchTab('series');
  page.element('searchType').value = 'all';
  page.element('searchInput').value = 'dune';
  await page.context.runSearch();
  await page.context.selectMedia(FILM);
  assert.deepEqual(where(page), ['tab:movies', 'tab:series', 'search:all:dune', 'search:all:dune+tt1']);
  const searches = page.requests.filter(url => url.startsWith('/api/search')).length;
  await page.context.goBack();
  assert.equal(page.requests.filter(url => url.startsWith('/api/search')).length, searches, 'the results under the fiche stay as they are');
  assert.equal(back(page).title, 'Retour : Séries (Échap)');
  await page.context.goBack();
  assert.equal(vm.runInContext('activeTab', page.context), 'series');
  assert.equal(vm.runInContext('searchResultsShown', page.context), false);
});

test('the same screen again adds no step; leaving by another way is a step back', async () => {
  const page = await start();
  await page.context.switchTab('movies');
  await page.context.selectMedia(FILM);
  await page.context.selectMedia(FILM);  // the fiche redrawn (a title marked seen…)
  assert.deepEqual(where(page), ['tab:movies', 'tab:movies+tt1']);
  page.context.closeFiche();
  await settle();
  assert.deepEqual(where(page), ['tab:movies']);
  assert.equal(ficheOpen(page), false);
  page.context.openConfig();
  await settle();
  assert.deepEqual(where(page), ['tab:movies', 'tab:settings']);
  page.context.closeConfig();
  await settle();
  assert.deepEqual(where(page), ['tab:movies']);
});

test('the actor page’s own « Retour » goes one step back too', async () => {
  const page = await start();
  await page.context.selectMedia(FILM);
  await page.context.openPerson('Rebecca Ferguson');
  await settle();
  page.context.backFromPerson();
  await settle();
  assert.equal(vm.runInContext('currentMedia.id', page.context), 'tt1');
  assert.deepEqual(where(page), ['tab:movies', 'tab:movies+tt1']);
});

test('the history keeps the last fifty screens', async () => {
  const page = await start();
  for (let i = 0; i < 60; i++) await page.context.selectMedia({ id: `tt${100 + i}`, name: `Film ${i}`, type: 'movie' });
  assert.equal(stack(page).length, 50);
  assert.equal(stack(page)[49].fiche.id, 'tt159');
});

test('Échap, ⌘[ / Alt+← and the mouse button go back; the button sits in the search bar', () => {
  const escape = source.slice(source.indexOf("if (e.key === 'Escape') {\n      if (closeOpenDialogOnEscape()) return;"));
  assert.match(escape, /if \(dp && dp\.style\.display === 'block'\) \{\n\s+closeFiche\(\);\n\s+return;\n\s+\}\n\s+if \(canGoBack\(\)\) \{\n\s+goBack\(\);\n\s+return;\n\s+\}\n\s+\(async \(\) => \{/,
    'the fiche first, then the previous screen, then full screen');
  assert.match(source, /if \(!isInput && \(\(\(e\.metaKey \|\| e\.ctrlKey\) && e\.key === '\['\) \|\| \(e\.altKey && e\.key === 'ArrowLeft'\)\) && canGoBack\(\)\)/);
  assert.match(source, /window\.addEventListener\('mouseup', event => \{[\s\S]*?event\.button !== 3/);
  assert.match(source, /<button class="detail-close-btn" onclick="closeFiche\(\)" title="Fermer la fiche \(Échap\)"/);
  const bar = html.slice(html.indexOf('<div class="search-box'), html.indexOf('id="searchType"'));
  assert.match(bar, /<button type="button" id="navBackBtn" class="nav-back-btn" onclick="goBack\(\)" hidden aria-label="Retour">[\s\S]*<span>Retour<\/span>/);
  assert.match(html, /<span>Écran précédent<\/span><div><kbd>Échap<\/kbd> \/ <kbd>Cmd\+\[<\/kbd> \/ <kbd>Alt\+←<\/kbd><\/div>/);
  assert.match(css, /\.nav-back-btn\[hidden\] \{ display: none; \}/);
  assert.match(css, /@media \(max-width: 640px\) \{ \.nav-back-btn span \{ display: none; \}/);
});

test('a page check: the back button shows only once there is somewhere to go', async () => {
  const page = await start();
  assert.equal(shown(back(page)), false);
  await page.context.switchTab('watchlist');
  assert.equal(shown(back(page)), true);
  assert.equal(back(page).title, 'Retour : Films (Échap)');
});
