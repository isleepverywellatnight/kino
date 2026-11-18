// « Calendrier » in Bibliothèque: this week and next week, day by day, each
// episode's state and the right button, « Plus tard », and the empty cases.
// The real interface scripts run on a stand-in page (tools/page_harness.js).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const { loadPage, settle, shown } = require('../tools/page_harness.js');

const html = require('../tools/interface_source.js').indexHtml();
const base = { year: '2025', poster: 'https://img/p.jpg', followed: true, premiere: '', seen: false, before: 0, resume_pct: 0 };
const item = fields => ({ ...base, season: 1, episode: 1, last_episode: fields.episode || 1, ...fields });
const CALENDAR = {
  today: '2026-11-12', start: '2026-11-09', end: '2026-11-23', series: 5, unavailable: 0,
  days: [
    { date: '2026-11-11', items: [item({ id: 'tt1', name: 'Dexter: Resurrection', season: 2, episode: 2, released: '2026-11-11', aired: true, resume_pct: 45 })] },
    { date: '2026-11-12', items: [
      item({ id: 'tt2', name: 'Black <Doves>', season: 2, episode: 1, last_episode: 6, released: '2026-11-12', aired: true, premiere: 'season' }),
      item({ id: 'tt3', name: 'MobLand', season: 2, episode: 9, released: '2026-11-12', aired: true, before: 2 }),
      item({ id: 'tt4', name: 'Vue', season: 1, episode: 3, released: '2026-11-12', aired: true, seen: true })] },
    { date: '2026-11-13', items: [item({ id: 'tt5', name: 'Nocturne', episode: 1, released: '2026-11-13', aired: false, followed: false, premiere: 'series' })] },
  ],
  later: [{ id: 'tt1', name: 'Dexter: Resurrection', year: '2025', poster: '', season: 3, episode: 1, released: '2027-02-04', premiere: 'season', followed: true }],
};

async function open(answer = CALENDAR) {
  const bodies = [];
  const page = await loadPage((url, options = {}) => {
    if (url === '/api/calendar') return answer;
    if (url === '/api/one-click-play') { bodies.push(JSON.parse(options.body)); return { ok: true, debrid: { ready: true, files: [] }, stream_url: '', mpv: { mode: 'integrated' } }; }
    return {};
  });
  await page.context.switchTab('calendar');
  await settle();
  return { page, bodies, grid: () => page.element('postersGrid').innerHTML };
}

const between = (out, from, to) => out.slice(out.indexOf(from), to ? out.indexOf(to) : undefined);
const card = (out, name) => out.slice(out.lastIndexOf('<article', out.indexOf(name)), out.indexOf('</article>', out.indexOf(name)));

test('Bibliothèque has « Calendrier » right after « Mes séries »', () => {
  const nav = html.slice(html.indexOf('id="librarySubNav"'), html.indexOf('</nav>', html.indexOf('id="librarySubNav"')));
  assert.match(withCalls(nav), /id="subtab-myseries"[^>]*>Mes séries<\/button>\s*<button class="lib-subtab" id="subtab-calendar" onclick="switchTab\('calendar'\)">Calendrier<\/button>/);
});

test('two weeks, day by day, with today and tomorrow named', async () => {
  const { page, grid } = await open();
  assert.ok(page.requests.includes('/api/calendar'));
  assert.equal(page.element('catalogTitle').textContent, 'Calendrier');
  const out = grid();
  const heads = [...out.matchAll(/<h3 class="search-group-title">([^<]+) <span class="(?:calendar-range|list-count)">([^<]+)<\/span><\/h3>/g)].map(m => `${m[1]} ${m[2]}`);
  assert.deepEqual(heads, ['Cette semaine 9 – 15 nov.', 'Semaine prochaine 16 – 22 nov.', 'Plus tard 1']);
  const days = [...out.matchAll(/<h4 class="calendar-day-title">([^<]+?)(?: <span class="calendar-near">([^<]+)<\/span>)?<\/h4>/g)].map(m => [m[1], m[2] || '']);
  assert.deepEqual(days, [['Mercredi 11 nov.', 'Hier'], ['Jeudi 12 nov.', 'Aujourd’hui'], ['Vendredi 13 nov.', 'Demain']]);
  assert.match(out, /<section class="calendar-day is-today">\s*<h4 class="calendar-day-title">Jeudi 12 nov\./);
  assert.match(between(out, 'Semaine prochaine', 'Plus tard'), /Rien ne sort la semaine prochaine pour vos séries\./);
  assert.equal(vm.runInContext('navLabel(navStack[navStack.length - 1])', page.context), 'Calendrier');
});

test('each episode says where it stands, and only the next one to watch can be played', async () => {
  const { grid } = await open();
  const out = grid();
  assert.match(withCalls(card(out, 'Dexter: Resurrection')), /S2 · É2[\s\S]*En cours \(45 %\)[\s\S]*onclick="playCalendarEpisode\('tt1', 2, 2, this\)">Reprendre S2 · É2<\/button>/);
  assert.match(card(out, 'Black &lt;Doves&gt;'), /S2 · É1 à É6 \(6 épisodes\) · Nouvelle saison[\s\S]*Disponible[\s\S]*>Lancer S2 · É1<\/button>/);
  assert.match(card(out, 'MobLand'), /Disponible · 2 épisodes à voir avant/);
  assert.doesNotMatch(card(out, 'MobLand'), /playCalendarEpisode/, 'not ahead of the episodes left');
  assert.match(out, /<article class="offline-item myseries-item calendar-item is-seen">[\s\S]*?Vue[\s\S]*?>Vu</);
  assert.match(card(out, 'Nocturne'), /S1 · É1 · Première[\s\S]*Dans Ma Liste/);
  assert.doesNotMatch(card(out, 'Nocturne'), /playCalendarEpisode/, 'not out yet');
  const later = between(out, 'Plus tard');
  assert.match(later, /S3 · É1 · Nouvelle saison[\s\S]*Le 4 févr\. 2027[\s\S]*openCalendarSeries\('tt1'\)/);
  assert.doesNotMatch(later, /playCalendarEpisode/);
});

test('the page sits in Bibliothèque like its other tabs', async () => {
  const { page } = await open();
  for (const id of ['librarySubNav', 'catalogHeader', 'postersGrid']) assert.equal(shown(page.element(id)), true, id);
  for (const id of ['heroSpotlight', 'genreFilters', 'homeResumeSection', 'detailPanel']) assert.equal(shown(page.element(id)), false, id);
});

test('the button plays that episode; « Fiche » opens the series', async () => {
  const { page, bodies } = await open();
  await page.context.playCalendarEpisode('tt2', 2, 1);
  await settle();
  assert.deepEqual({ ...bodies[0], player_mode: undefined }, { imdb_id: 'tt2', type: 'series', season: 2, episode: 1,
    title: 'Black <Doves> — S02E01', name: 'Black <Doves>', poster: 'https://img/p.jpg', year: '2025', player_mode: undefined });
  await page.context.openCalendarSeries('tt5');
  await settle();
  assert.equal(vm.runInContext('currentMedia.id', page.context), 'tt5');
  assert.equal(vm.runInContext('currentMedia.type', page.context), 'series');
});

test('no series, a missing list, an empty fortnight, offline, an error', async () => {
  const none = await open({ ...CALENDAR, series: 0, days: [], later: [] });
  assert.match(none.grid(), /<h3>Aucune série suivie pour l’instant<\/h3><p>Lancez un épisode, ou ajoutez une série à Ma Liste/);
  const quiet = await open({ ...CALENDAR, days: [], later: [], unavailable: 2 });
  assert.match(quiet.grid(), /2 séries sans liste d’épisodes pour le moment : le calendrier est incomplet\./);
  assert.match(quiet.grid(), /Rien ne sort cette semaine pour vos séries\.[\s\S]*Rien ne sort la semaine prochaine/);
  assert.doesNotMatch(quiet.grid(), /Plus tard/);
  const local = await open({ ...CALENDAR, today: '2026-11-11', local_today: '2026-11-12' });
  assert.match(local.grid(), /<section class="calendar-day is-today">\s*<h4 class="calendar-day-title">Jeudi 12 nov\. <span class="calendar-near">Aujourd’hui<\/span>/,
    'the day labels follow the Mac’s own date');
  const turn = await open({ ...CALENDAR, today: '2026-11-30', start: '2026-11-30', days: [], later: [] });
  assert.match(turn.grid(), /Cette semaine <span class="calendar-range">30 nov\. – 6 déc\.<\/span>/);
  const offline = await open({ error: 'Pas de connexion à internet.', code: 'offline' });
  assert.match(offline.grid(), /<h3>Le calendrier a besoin d’internet<\/h3>/);
  const failed = await open({ error: 'boom' });
  assert.match(failed.grid(), /Impossible d’afficher le calendrier : boom/);
});
