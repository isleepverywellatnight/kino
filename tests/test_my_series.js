// « Mes séries » in Bibliothèque: three groups, where each series stands, what
// is left and what comes next, a button to resume or start, and the fiche.
// The real interface scripts run on a stand-in page (tools/page_harness.js).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { loadPage, settle, shown } = require('../tools/page_harness.js');

const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const base = { year: '2025', poster: 'https://img/p.jpg', known: true, finished: false, last_watched: 1 };
const SERIES = [
  { ...base, id: 'tt1', name: 'Dexter: Resurrection', group: 'continue', current: { season: 2, episode: 2, progress_pct: 45 },
    next: { season: 2, episode: 2, released: '', resume: true, progress_pct: 45 }, upcoming: { season: 2, episode: 3, released: '2026-11-16' },
    aired: 12, seen: 11, remaining: 1 },
  { ...base, id: 'tt2', name: 'Black <Doves>', group: 'continue', current: { season: 1, episode: 6, progress_pct: 100 },
    next: { season: 2, episode: 1, released: '2026-11-05', resume: false, progress_pct: 0 }, upcoming: null, aired: 12, seen: 7, remaining: 5 },
  { ...base, id: 'tt3', name: 'MobLand', group: 'uptodate', current: { season: 2, episode: 8, progress_pct: 100 }, next: null,
    upcoming: { season: 2, episode: 9, released: '2026-11-13' }, aired: 18, seen: 18, remaining: 0 },
  { ...base, id: 'tt4', name: 'Nocturne', group: 'start', current: null, next: { season: 1, episode: 1, released: '2026-10-30', resume: false, progress_pct: 0 },
    upcoming: null, aired: 3, seen: 0, remaining: 3 },
];

async function open(answer = { series: SERIES, today: '2026-11-12' }) {
  const bodies = [];
  const page = await loadPage((url, options = {}) => {
    if (url === '/api/my-series') return answer;
    if (url === '/api/one-click-play') { bodies.push(JSON.parse(options.body)); return { ok: true, debrid: { ready: true, files: [] }, stream_url: '', mpv: { mode: 'integrated' } }; }
    return {};
  });
  await page.context.switchTab('myseries');
  await settle();
  return { page, bodies, grid: () => page.element('postersGrid').innerHTML };
}

test('Bibliothèque has « Mes séries » right after « En cours »', () => {
  const nav = html.slice(html.indexOf('id="librarySubNav"'), html.indexOf('</nav>', html.indexOf('id="librarySubNav"')));
  assert.match(nav, /id="subtab-history"[^>]*>En cours[\s\S]*<button class="lib-subtab" id="subtab-myseries" onclick="switchTab\('myseries'\)">Mes séries<\/button>/);
});

test('three groups, each series with where it stands, what is left and what comes next', async () => {
  const { page, grid } = await open();
  assert.ok(page.requests.includes('/api/my-series'));
  assert.equal(page.element('catalogTitle').textContent, 'Mes séries');
  const out = grid();
  const heads = [...out.matchAll(/<h3 class="search-group-title">([^<]+) <span class="list-count">(\d+)<\/span><\/h3>/g)].map(m => `${m[1]} ${m[2]}`);
  assert.deepEqual(heads, ['À continuer 2', 'À jour 1', 'À commencer 1']);
  const card = name => out.slice(out.lastIndexOf('<article', out.indexOf(name)), out.indexOf('</article>', out.indexOf(name)));
  assert.match(card('Dexter: Resurrection'), /S2 · É2 en cours \(45 %\)[\s\S]*1 épisode à voir[\s\S]*Prochain à sortir : S2 · É3 le 16 nov\. 2026/);
  assert.match(card('Dexter: Resurrection'), /onclick="playMySeries\('tt1', this\)">Reprendre S2 · É2<\/button>/);
  assert.match(card('Dexter: Resurrection'), /aria-label="11 épisodes vus sur 12"><div class="progress-fill" style="width:92%">/);
  assert.match(card('Black &lt;Doves&gt;'), /Vu jusqu’à S1 · É6[\s\S]*5 épisodes à voir[\s\S]*>Lancer S2 · É1<\/button>/);
  assert.match(card('MobLand'), /Vu jusqu’à S2 · É8[\s\S]*À jour[\s\S]*Prochain à sortir : S2 · É9 le 13 nov\. 2026/);
  assert.doesNotMatch(card('MobLand'), /playMySeries/, 'nothing to play while up to date');
  assert.match(card('Nocturne'), /3 épisodes à découvrir[\s\S]*>Lancer S1 · É1<\/button>/);
  assert.doesNotMatch(card('Nocturne'), /progress-bar/);
  assert.equal(vm.runInContext('navLabel(navStack[navStack.length - 1])', page.context), 'Mes séries');
});

test('the page sits in Bibliothèque like its other tabs', async () => {
  const { page } = await open();
  for (const id of ['librarySubNav', 'catalogHeader', 'postersGrid']) assert.equal(shown(page.element(id)), true, id);
  for (const id of ['heroSpotlight', 'genreFilters', 'homeResumeSection', 'detailPanel']) assert.equal(shown(page.element(id)), false, id);
});

test('the button plays the right episode; « Fiche » opens the series', async () => {
  const { page, bodies } = await open();
  await page.context.playMySeries('tt2');
  await settle();
  assert.deepEqual({ ...bodies[0], player_mode: undefined }, { imdb_id: 'tt2', type: 'series', season: 2, episode: 1,
    title: 'Black <Doves> — S02E01', name: 'Black <Doves>', poster: 'https://img/p.jpg', year: '2025', player_mode: undefined });
  await page.context.openMySeries('tt3');
  await settle();
  assert.equal(vm.runInContext('currentMedia.id', page.context), 'tt3');
  assert.equal(vm.runInContext('currentMedia.type', page.context), 'series');
});

test('no series yet, an episode list missing, or an error', async () => {
  const empty = await open({ series: [], today: '2026-11-12' });
  assert.match(empty.grid(), /<h3>Aucune série suivie pour l’instant<\/h3><p>Lancez un épisode, ou ajoutez une série à Ma Liste/);
  const unknown = await open({ series: [{ ...SERIES[0], known: false, next: null, group: 'continue' }] });
  assert.match(unknown.grid(), /S2 · É2[\s\S]*Liste des épisodes indisponible pour le moment/);
  const offline = await open({ error: 'Pas de connexion à internet.', code: 'offline' });
  assert.match(offline.grid(), /<h3>Mes séries a besoin d’internet<\/h3>/);
  const failed = await open({ error: 'boom' });
  assert.match(failed.grid(), /Impossible d’afficher vos séries : boom/);
});
