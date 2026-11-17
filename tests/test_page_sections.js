const path = require('node:path');
// Every screen shows its own sections and nothing left over from another:
// the real interface scripts run on a stand-in page (tools/page_harness.js),
// go through each tab, a search from each tab, the three anime pages and a fiche,
// and the sections shown are checked against the screens they belong to.
const assert = require('node:assert/strict');
const test = require('node:test');
const { loadPage, settle, shown, pageElements } = require('../tools/page_harness.js');

const LIBRARY = ['watchlist', 'history', 'myseries', 'calendar', 'watched', 'offline', 'customlists', 'rdcloud', 'stats'];
const CATALOGUE = ['movies', 'series', 'classics', 'anime'];
// The Anime tab opens on « À la une »; its planning and its catalogue are pages of their own.
const GRIDS = ['movies', 'series', 'classics', 'anime-catalog'];
const TABS = [...CATALOGUE, 'community', 'manga', ...LIBRARY, 'settings'];

// Section → the screens where it may appear. « fiche » is an open title.
const OWNERS = {
  librarySubNav: LIBRARY,
  animeSubNav: ['anime', 'anime-schedule', 'anime-catalog'],
  animeHomePanel: ['anime'],
  homeResumeSection: ['movies', 'series', 'history', 'anime'],
  // New episodes are series: the row is the Séries tab's.
  homeNewSection: ['series'],
  homeListSection: ['movies', 'series'],
  homeForYouSection: ['movies', 'series'],
  homeBecauseSection: ['movies', 'series'],
  heroSpotlight: ['movies', 'series', 'classics', 'anime'],
  catalogHeader: [...GRIDS, 'watchlist', 'myseries', 'calendar', 'watched', 'offline', 'customlists', 'search'],
  postersGrid: [...GRIDS, 'watchlist', 'myseries', 'calendar', 'watched', 'offline', 'customlists', 'search'],
  genreFilters: GRIDS,
  catalogSortWrap: GRIDS,
  loadMoreWrap: GRIDS,
  watchlistActionsWrap: ['watchlist', 'watched', 'customlists'],
  listStatsBadge: ['watchlist', 'watched', 'customlists', 'classics'],
  listFilters: ['watchlist', 'watched', 'customlists'],
  rdCloudPanel: ['rdcloud'],
  viewingStatsPanel: ['stats'],
  communityPanel: ['community'],
  mangaPanel: ['manga'],
  animeSchedulePanel: ['anime-schedule'],
  settingsPanel: ['settings'],
  detailPanel: ['fiche'],
  torrentsPanel: ['fiche'],
  detailMore: ['fiche'],
  debridResultPanel: ['fiche'],
};
// What each screen must show, with the data below.
const MUST = {
  movies: ['homeResumeSection', 'homeListSection', 'homeForYouSection', 'homeBecauseSection', 'catalogHeader', 'postersGrid'],
  series: ['homeResumeSection', 'homeNewSection', 'catalogHeader', 'postersGrid'],
  community: ['communityPanel'],
  manga: ['mangaPanel'],
  watchlist: ['librarySubNav', 'postersGrid'],
  history: ['librarySubNav', 'homeResumeSection'],
  myseries: ['librarySubNav', 'catalogHeader', 'postersGrid'],
  calendar: ['librarySubNav', 'catalogHeader', 'postersGrid'],
  rdcloud: ['librarySubNav', 'rdCloudPanel'],
  stats: ['librarySubNav', 'viewingStatsPanel'],
  settings: ['settingsPanel'],
  // Série 2, in progress, is an anime for the server: « À continuer » shows it on « À la une ».
  anime: ['animeSubNav', 'animeHomePanel', 'heroSpotlight', 'homeResumeSection'],
  'anime-schedule': ['animeSubNav', 'animeSchedulePanel'],
  'anime-catalog': ['animeSubNav', 'catalogHeader', 'postersGrid', 'genreFilters', 'catalogSortWrap'],
  search: ['catalogHeader', 'postersGrid'],
};

const film = (i, extra = {}) => ({ id: `tt10${i}`, name: `Film ${i}`, type: 'movie', year: '2001', poster: 'p', ...extra });
const DATA = {
  '/api/user-lists': {
    watchlist: [film(1), film(2), { id: 'tt201', name: 'Série', type: 'series' }],
    history: [film(3, { completed: true, last_watched: 10 }), film(4, { position: 100, duration: 1000, last_watched: 20 }),
      { id: 'tt202', name: 'Série 2', type: 'series', ep_positions: { s1e2: { position: 10, duration: 100 } }, watched_episodes: ['s1e1'], last_watched: 30 }],
  },
  '/api/my-series': { series: [{ id: 'tt202', name: 'Série 2', year: '', poster: '', group: 'continue', known: true,
    current: { season: 1, episode: 2, progress_pct: 10 }, next: { season: 1, episode: 2, released: '', resume: true, progress_pct: 10 },
    upcoming: null, aired: 4, seen: 1, remaining: 3, finished: false, last_watched: 30 }], today: '2026-11-12' },
  '/api/calendar': { today: '2026-11-12', start: '2026-11-09', end: '2026-11-23', series: 1, unavailable: 0, later: [],
    days: [{ date: '2026-11-12', items: [{ id: 'tt202', name: 'Série 2', year: '', poster: '', season: 1, episode: 3, last_episode: 3,
      released: '2026-11-12', aired: true, seen: false, before: 1, premiere: '', resume_pct: 0, followed: true }] }] },
  '/api/new-episodes': { episodes: [{ id: 'tt203', name: 'Série 3', season: 1, episode: 4, kind: 'followed', released: '2026-11-01' }] },
  '/api/catalog': { metas: Array.from({ length: 12 }, (_, i) => film(50 + i)) },
  '/api/anime/home': { ok: true, library: ['tt202'], rows: [{ key: 'trending', title: 'Tendances', note: '', items: [
    { id: 1, title: 'Anime 1', format: 'TV', episodes: 12, score: 80, poster: 'p', kino: { id: 'tt301', type: 'series', season: 2 } }] }] },
  '/api/anime/week': { ok: true, from: 1794524400, to: 1795129200, episodes: [
    { id: 1, title: 'Anime 1', format: 'TV', episode: 7, airing_at: 1794530000, popularity: 50000, kino: { id: 'tt301', type: 'series', season: 2 } }] },
  '/api/meta': { meta: { name: 'Film', similar: Array.from({ length: 6 }, (_, i) => film(80 + i)) } },
  // « Si vous avez aimé » reads /api/related (TMDB with a key, else the genre match).
  '/api/related': { source: 'genres', items: Array.from({ length: 6 }, (_, i) => film(80 + i)) },
  '/api/for-you': { ready: true, items: Array.from({ length: 6 }, (_, i) => ({ ...film(70 + i), why: 'Parce que vous avez aimé X' })) },
  '/api/config': { has_token: true, debrid_provider: 'realdebrid' },
  '/api/search': { metas: [film(90), { id: 'tt291', name: 'S', type: 'series' }] },
  '/api/torrents': { torrents: [] },
};

function sectionsShown(page) {
  return Object.keys(OWNERS).filter(id => shown(page.element(id)));
}

function strays(page, screen) {
  return sectionsShown(page).filter(id => !OWNERS[id].includes(screen));
}

function missing(page, screen) {
  return (MUST[screen] || []).filter(id => !shown(page.element(id)));
}

let page;
test.before(async () => {
  page = await loadPage(url => DATA[url.split('?')[0]] || {});
});

async function go(tab) {
  await page.context.switchTab('movies');
  await settle();
  await page.context.switchTab(tab);
  await settle();
}

test('the page knows every section the check looks at', async () => {
  const fs = require('node:fs');
  const ids = pageElements(fs.readFileSync(require('node:path').join(path.join(__dirname, '..'), 'web/index.html'), 'utf8'));
  for (const id of Object.keys(OWNERS)) assert.ok(ids.has(id), id);
});

test('each tab shows its own sections and nothing from another', async () => {
  for (const tab of TABS) {
    await go(tab);
    assert.deepEqual(strays(page, tab), [], `left over on ${tab}`);
    assert.deepEqual(missing(page, tab), [], `missing on ${tab}`);
  }
});

async function goAnime(pageName) {
  await go('anime');
  await page.context.switchAnimeSubTab(pageName);
  await settle();
}

test('each page of the Anime tab shows its own sections: the banner belongs to « À la une »', async () => {
  for (const [pageName, screen] of [['schedule', 'anime-schedule'], ['catalog', 'anime-catalog'], ['home', 'anime']]) {
    await goAnime(pageName);
    assert.deepEqual(strays(page, screen), [], `left over on ${screen}`);
    assert.deepEqual(missing(page, screen), [], `missing on ${screen}`);
  }
  // The catalogue lands after the page is chosen: it must not bring its banner to the planning.
  await goAnime('schedule');
  page.context.selectGenre('', null);
  await settle();
  assert.deepEqual(strays(page, 'anime-schedule'), [], 'a catalogue that lands late');
  await goAnime('home');
});

test('search results stand alone, wherever the search starts', async () => {
  const starts = [...TABS, 'anime-schedule', 'anime-catalog'];
  for (const start of starts) {
    if (start.startsWith('anime-')) await goAnime(start.slice(6));
    else await go(start);
    page.element('searchType').value = 'all';
    page.element('searchInput').value = 'Dune';
    await page.context.runSearch();
    await settle();
    assert.deepEqual(strays(page, 'search'), [], `left over after a search from ${start}`);
    assert.deepEqual(missing(page, 'search'), [], `missing after a search from ${start}`);
  }
  await goAnime('home');
});

test('clearing the search brings the tab back', async () => {
  for (const tab of ['watchlist', 'community', 'stats', 'anime']) {
    await go(tab);
    page.element('searchInput').value = 'Dune';
    await page.context.runSearch();
    await settle();
    page.context.clearSearchInput();
    await settle();
    assert.deepEqual(missing(page, tab), [], `${tab} after clearing`);
    assert.deepEqual(strays(page, tab), [], `${tab} after clearing`);
  }
});

test('a fiche opens above the tab and leaves nothing when closed or left', async () => {
  for (const tab of ['movies', 'watchlist', 'community', 'anime']) {
    await go(tab);
    await page.context.selectMedia(film(7));
    await settle(10);
    assert.ok(['detailPanel', 'torrentsPanel', 'detailMore'].every(id => shown(page.element(id))), `fiche on ${tab}`);
    page.context.closeDetailPanel();
    await settle();
    assert.deepEqual(strays(page, tab), [], `after closing the fiche on ${tab}`);
  }
  await go('movies');
  await page.context.selectMedia(film(7));
  await settle(10);
  await page.context.switchTab('settings');
  await settle();
  assert.deepEqual(strays(page, 'settings'), [], 'a tab change closes the fiche');
});
