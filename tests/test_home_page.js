// Accueil: what to watch now first (À continuer, new episodes, Ma Liste), then
// « À la une », titles like the last one seen, and the catalogue.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const { withCalls } = require('../tools/interface_source.js');

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/redesign.css'), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const homeCode = slice('// --- Accueil: personal rows first', 'function renderHomeResume() {');
const historyCode = slice('function getInProgressHistory() {', 'function getWatchedHistory() {') + slice('function getWatchedHistory() {', '\n}\n') + '\n}\n';
const listCode = slice('function listItemKind(m) {', 'function foldSearchText(value) {');
const escapeHtml = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

const film = (id, extra = {}) => ({ id, name: `Film ${id}`, type: 'movie', ...extra });
const similar = count => Array.from({ length: count }, (_, i) => film(`tt9${i}`));

function load({ tab = 'movies', watchlist = [], history = [], related = {} } = {}) {
  const elements = {};
  const element = id => elements[id] || (elements[id] = { id, innerHTML: '', textContent: '', hidden: true });
  const requests = [];
  const context = {
    activeTab: tab, userWatchlist: watchlist, userHistory: history,
    RELATED_CLIENT_CACHE: new Map(Object.entries(related)),
    document: { getElementById: element },
    escapeHtml,
    titleAttrs: m => ` data-title-id="${m.id}"`,
    displayTitle: m => m.name_fr || m.name,
    posterCardHtml: (m, kind) => `<card ${m.id} ${kind}>`,
    api: path => { requests.push(path); return new Promise(() => {}); },
  };
  vm.runInNewContext(`${historyCode}\n${listCode}\n${homeCode}\nthis.api_ = { renderHomeList, renderHomeBecause, homeListItems, homeBecauseSource };`, context);
  return { ...context.api_, elements: new Proxy({}, { get: (_, id) => element(id) }), requests, context };
}

test('Ma Liste: titles of the tab, neither seen nor in progress, newest first', () => {
  const watchlist = [film('tt1'), film('tt2'), { id: 'tt3', name: 'Une série', type: 'series' }, film('tt4'), film('tt5')];
  const history = [{ id: 'tt2', type: 'movie', completed: true }, { id: 'tt4', type: 'movie', progress_pct: 30 }];
  const home = load({ watchlist, history });
  home.renderHomeList();
  assert.equal(home.elements.homeListSection.hidden, false);
  assert.equal(home.elements.homeListRow.innerHTML, '<card tt1 movie><card tt5 movie>');
  assert.equal(home.elements.homeListCount.textContent, '2');
  const series = load({ tab: 'series', watchlist, history });
  series.renderHomeList();
  assert.equal(series.elements.homeListRow.innerHTML, '<card tt3 series>');
});

test('Ma Liste: twelve posters, then the way to the whole list', () => {
  const home = load({ watchlist: Array.from({ length: 15 }, (_, i) => film(`tt${i + 1}`)) });
  home.renderHomeList();
  const out = home.elements.homeListRow.innerHTML;
  assert.equal((out.match(/<card /g) || []).length, 12);
  assert.match(withCalls(out), /<button type="button" class="list-shelf-more" onclick="switchTab\('watchlist'\)">Voir les 15 titres<\/button>$/);
});

test('Ma Liste: hidden when empty or away from the home tabs', () => {
  const empty = load({ watchlist: [] });
  empty.renderHomeList();
  assert.equal(empty.elements.homeListSection.hidden, true);
  const elsewhere = load({ tab: 'watchlist', watchlist: [film('tt1')] });
  elsewhere.renderHomeList();
  assert.equal(elsewhere.elements.homeListSection.hidden, true);
});

test('similar titles follow the last title seen to the end, without the seen ones', () => {
  const history = [
    { id: 'tt0111161', name: 'The Shawshank Redemption', type: 'movie', completed: true, last_watched: 200 },
    { id: 'tt0068646', name: 'The Godfather', type: 'movie', imported_watched: true, last_watched: 100 },
    { id: 'tt7', name: 'En cours', type: 'movie', progress_pct: 40, last_watched: 300 },
    { id: 'tt90', name: 'Déjà vu', type: 'movie', completed: true, last_watched: 50 },
  ];
  const related = { tt0111161: { source: 'tmdb', items: [film('tt0111161'), ...similar(6)] } };
  const home = load({ history, related });
  assert.equal(home.homeBecauseSource().id, 'tt0111161');
  home.renderHomeBecause();
  assert.equal(home.elements.homeBecauseSection.hidden, false);
  assert.equal(home.elements.homeBecauseTitle.innerHTML, 'Si vous avez aimé <span data-title-id="tt0111161">The Shawshank Redemption</span>');
  assert.equal(home.elements.homeBecauseRow.innerHTML, '<card tt91 movie><card tt92 movie><card tt93 movie><card tt94 movie><card tt95 movie>',
    'the source and the titles already seen are left out');
  assert.deepEqual(home.requests, [], 'the title data was already known');
});

test('similar titles: their data is asked once, and too few hide the row', () => {
  const history = [{ id: 'tt0111161', name: 'Les <Évadés>', type: 'movie', completed: true, last_watched: 1 }];
  const home = load({ history });
  home.renderHomeBecause();
  home.renderHomeBecause();
  assert.deepEqual(home.requests, ['/api/related?type=movie&imdb_id=tt0111161'], 'TMDB with a key, else the genre match');
  assert.equal(home.elements.homeBecauseSection.hidden, true);
  const few = load({ history, related: { tt0111161: { items: similar(3) } } });
  few.renderHomeBecause();
  assert.equal(few.elements.homeBecauseSection.hidden, true);
  const series = load({ tab: 'series', history, related: { tt0111161: { items: similar(8) } } });
  series.renderHomeBecause();
  assert.equal(series.elements.homeBecauseSection.hidden, true, 'a film does not lead the series tab');
});

test('the home order: now first, then discovery', () => {
  const at = id => html.indexOf(`id="${id}"`);
  const order = ['homeResumeSection', 'homeNewSection', 'homeListSection', 'heroSpotlight', 'homeForYouSection', 'homeBecauseSection', 'catalogHeader'];
  order.forEach(id => assert.ok(at(id) > 0, id));
  assert.deepEqual([...order].sort((a, b) => at(a) - at(b)), order);
  const list = html.slice(at('homeListSection'), at('heroSpotlight'));
  assert.match(withCalls(list), /onclick="openTonightModal\(\)">Une idée pour ce soir<\/button>/);
  assert.match(withCalls(list), /onclick="switchTab\('watchlist'\)">Ma Liste<\/button>/);
  assert.match(source, /function renderHomeResume\(\) \{\n(?:  \/\/[^\n]*\n)?  if \(!networkOnline && activeTab !== 'history'\) \{\n    hideHomeRows\(\);\n    return;\n  \}\n  renderHomeList\(\);\n  if \(typeof renderHomeRate === 'function'\) renderHomeRate\(\);\n  renderHomeForYou\(\);\n  renderHomeBecause\(\);/,
    'without internet the rows step aside; otherwise Ma Liste and the similar titles come first');
  assert.match(css, /\.home-shelf:not\(\[hidden\]\) \{ display: block;/);
});
