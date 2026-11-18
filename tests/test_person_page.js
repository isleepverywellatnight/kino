// Page acteur: from a fiche, a person's page with photo, short bio, what they
// do, their films and series (seen ones marked), filters, the titles the credits
// do not confirm apart, and the way back. The real interface scripts run on a
// stand-in page (tools/page_harness.js).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const { loadPage, settle, shown } = require('../tools/page_harness.js');

const source = require('../tools/interface_source.js').appSource();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/redesign.css'), 'utf8');
const title = (id, name, type, year, roles = ['cast']) => ({ id, imdb_id: id, type, name, year, releaseInfo: year, poster: '', roles });
const PAGE = {
  name: 'Rebecca Ferguson', photo: 'https://upload.wikimedia.org/rf.jpg', description: 'actrice suédoise',
  bio: 'Rebecca Ferguson est une actrice <suédoise>.',
  movies: [title('tt1', 'Dune: Part Two', 'movie', '2024'), title('tt2', 'Doctor Sleep', 'movie', '2019', ['cast', 'writer'])],
  series: [title('tt5', 'Silo', 'series', '2023–')],
  others: [title('tt6', 'Nya tider', 'series', '1999', [])],
};
const SECTIONS = ['librarySubNav', 'animeSubNav', 'homeResumeSection', 'homeNewSection', 'homeListSection', 'homeBecauseSection',
  'heroSpotlight', 'catalogHeader', 'postersGrid', 'genreFilters', 'catalogSortWrap', 'loadMoreWrap', 'watchlistActionsWrap',
  'listStatsBadge', 'listFilters', 'rdCloudPanel', 'viewingStatsPanel', 'communityPanel', 'mangaPanel', 'animeSchedulePanel', 'settingsPanel',
  'detailPanel', 'torrentsPanel', 'detailMore', 'debridResultPanel'];

async function open(answer = PAGE, { history = [] } = {}) {
  const page = await loadPage(url => (url.startsWith('/api/person?') ? (typeof answer === 'function' ? answer(url) : answer) : {}));
  vm.runInContext(`userHistory = ${JSON.stringify(history)}; currentMedia = { id: 'tt1', name: 'Dune: Part Two', name_fr: 'Dune : Deuxième partie', type: 'movie' };`, page.context);
  await page.context.openPerson('Rebecca Ferguson');
  await settle();
  return page;
}
const grid = page => page.element('postersGrid').innerHTML;

test('a click on a person in the fiche opens their page', () => {
  assert.match(withCalls(source), /class="detail-person" data-person="\$\{escapeHtml\(person\.name\)\}" \$\{act\('openPerson', person\.name\)\} title="Films et séries de /);
});

test('the page: photo, description, short bio, what they do, and the way back', async () => {
  const page = await open(PAGE, { history: [{ id: 'tt2', type: 'movie', completed: true }] });
  assert.ok(page.requests.includes('/api/person?name=Rebecca%20Ferguson'));
  assert.equal(page.element('catalogTitle').textContent, 'Rebecca Ferguson');
  const out = grid(page);
  assert.match(out, /<div class="person-photo has-photo" aria-hidden="true"><img src="https:\/\/upload\.wikimedia\.org\/rf\.jpg" alt=""><\/div>/);
  assert.match(out, /<p class="person-description">actrice suédoise<\/p>/);
  assert.match(out, /<p class="person-bio">Rebecca Ferguson est une actrice &lt;suédoise&gt;\.<\/p>/);
  assert.match(out, /<p class="person-facts">Joue dans 2 films et 1 série · Écrit 1 film · 1 déjà vu<\/p>/);
  assert.match(withCalls(out), /onclick="backFromPerson\(\)">Retour à « Dune: Part Two »<\/button>/);
});

test('films and series in their groups, seen ones marked on their cards', async () => {
  const page = await open(PAGE, { history: [{ id: 'tt2', type: 'movie', completed: true }] });
  const out = grid(page);
  assert.ok(out.indexOf('Films <span class="list-count">2</span>') < out.indexOf('Séries <span class="list-count">1</span>'));
  assert.ok(out.indexOf('Dune: Part Two') < out.indexOf('Doctor Sleep'), 'the server order, newest first');
  const shown = withCalls(out);
  const card = id => shown.slice(shown.indexOf(`selectMedia({ id: '${id}'`), shown.indexOf('<div class="poster-card"', shown.indexOf(`selectMedia({ id: '${id}'`)));
  assert.match(card('tt2'), /class="poster-watched"[^>]*>✓ Vu<\/button>/);
  assert.doesNotMatch(card('tt1'), /class="poster-watched"/);
  assert.match(withCalls(out), /setPersonFilter\('all'\)">Tout <span class="list-count">3<\/span>[\s\S]*setPersonFilter\('movie'\)">Films[\s\S]*setPersonFilter\('series'\)">Séries[\s\S]*setPersonFilter\('unseen'\)">Pas encore vus <span class="list-count">2<\/span>/);
  page.context.setPersonFilter('unseen');
  assert.doesNotMatch(grid(page), /Doctor Sleep/);
  assert.match(grid(page), /Dune: Part Two[\s\S]*Silo/);
  page.context.setPersonFilter('series');
  assert.doesNotMatch(grid(page), /class="poster-title"[^>]*>Dune: Part Two/, 'the film\'s card is filtered out (the way back still names it)');
});

test('titles the credits do not confirm stay apart until asked for', async () => {
  const page = await open();
  assert.doesNotMatch(grid(page), /Nya tider/);
  assert.match(withCalls(grid(page)), /onclick="togglePersonOthers\(\)" aria-expanded="false">Autres résultats pour ce nom \(1\)<\/button>/);
  page.context.togglePersonOthers();
  assert.match(grid(page), /Autres résultats pour ce nom <span class="list-count">1<\/span>[\s\S]*un petit rôle, ou un homonyme[\s\S]*Nya tider/);
});

test('the page stands alone like a search, and a later search replaces it', async () => {
  const page = await open();
  const visible = SECTIONS.filter(id => shown(page.element(id)));
  assert.deepEqual(visible, ['catalogHeader', 'postersGrid']);
  page.element('searchType').value = 'all';
  page.element('searchInput').value = 'Silo';
  await page.context.runSearch();
  assert.equal(vm.runInContext('personView', page.context), null);
});

test('marking a card seen redraws the page, not the catalogue', async () => {
  const page = await open();
  vm.runInContext("activeTab = 'movies'; catalogItems = [{ id: 'tt999', name: 'Catalogue', type: 'movie' }];", page.context);
  page.context.fetch = async () => ({ ok: true, status: 200, json: async () => ({ history: [{ id: 'tt1', type: 'movie', completed: true }] }) });
  await page.context.toggleCardWatched(null, { id: 'tt1', name: 'Dune: Part Two', type: 'movie' });
  assert.doesNotMatch(grid(page), /Catalogue/);
  assert.match(grid(page), /1 déjà vu/);
});

test('a slow answer for another person, offline, or an error', async () => {
  let release;
  const page = await loadPage(url => (url.includes('Slow') ? new Promise(resolve => { release = resolve; }) : PAGE));
  page.context.openPerson('Slow Person');
  await settle();
  await page.context.openPerson('Rebecca Ferguson');
  release({ ...PAGE, bio: 'LATE' });
  await settle();
  assert.doesNotMatch(grid(page), /LATE/);
  assert.equal(page.element('catalogTitle').textContent, 'Rebecca Ferguson');
  const offline = await open({ error: 'Pas de connexion à internet.', code: 'offline' });
  assert.match(grid(offline), /<h3>La page de cette personne a besoin d’internet<\/h3>/);
  const failed = await open({ error: 'Délai <dépassé>' });
  assert.match(grid(failed), /Impossible d’afficher cette page : Délai &lt;dépassé&gt;/);
  const nobody = await open({ name: 'Rebecca Ferguson', photo: '', description: '', bio: '', movies: [], series: [], others: [] });
  assert.match(grid(nobody), /<h3>Aucun titre confirmé<\/h3>/);
  assert.match(grid(nobody), /<div class="person-photo" aria-hidden="true">RF<\/div>/, 'initials without a photo');
});

test('the page keeps the monochrome look and fits a phone', () => {
  const block = css.slice(css.indexOf('/* Page acteur */'), css.indexOf('.search-group-title {'));
  for (const [, hex] of block.matchAll(/#([0-9a-f]{3,6})\b/gi)) {
    const full = hex.length === 3 ? hex.replace(/./g, c => c + c) : hex;
    assert.ok(full.slice(0, 2) === full.slice(2, 4) && full.slice(2, 4) === full.slice(4, 6), `grey only: #${hex}`);
  }
  assert.match(block, /@media \(max-width: 640px\) \{ \.person-head \{ flex-direction: column; \}/);
});
