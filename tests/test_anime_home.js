// The Anime tab: « À la une » in rows, the week's planning in
// lines, the catalogue; one line of tabs that is the title of the page; every
// work opens KINO's own fiche, on the season it was shown for.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.join(path.join(__dirname, '..'), name), 'utf8');
const { appSource, interfaceScripts } = require('../tools/interface_source.js');
const source = appSource();
const html = require('../tools/interface_source.js').indexHtml();
const css = read('web/css/design.css');
const animeHome = require('../tools/interface_source.js').inFrench(read('web/js/anime-home.js'));
const between = (start, end) => start + source.split(start, 2)[1].split(end, 1)[0];
const helpers = between('function escapeHtml(s)', 'async function checkTraktStatus()');
const frenchTitles = between('const ORIGINAL_TITLES = new Map();', '// --- Immersive fiche: French labels');
const spoilerTarget = between('function scheduleSpoilerTarget(item) {', 'function refreshSpoilerSurfaces() {');

const NAMES = ['animeNextLabel', 'animeWorkFacts', 'animeCardHtml', 'animeWeekDays', 'animeWeekKeep', 'animeAiringLabel',
  'animeWeekRowHtml', 'animeMineStates', 'isAnimeTitle', 'applyAnimeSubTab', 'switchAnimeSubTab', 'openAnimeWork',
  'renderAnimeWeek', 'renderAnimeHome', 'loadAnimeHome', 'setAnimeWeekFilter', 'selectAnimeWeekDay', 'animeWorks', 'ANIME_POPULAR_FROM'];

function element() {
  const classes = new Set();
  const attributes = {};
  return {
    style: {}, innerHTML: '', textContent: '', value: '', hidden: false,
    classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)), add: name => classes.add(name),
      remove: name => classes.delete(name), contains: name => classes.has(name) },
    setAttribute: (name, value) => { attributes[name] = String(value); },
    removeAttribute: name => { delete attributes[name]; },
    getAttribute: name => attributes[name] ?? null,
  };
}

function load(extra = {}) {
  const elements = {};
  const context = {
    document: { getElementById: id => (elements[id] ||= element()), querySelectorAll: () => [], hidden: false },
    userWatchlist: [], userHistory: [], heroSpotlightItems: [], catalogHasMore: false, activeTab: 'anime',
    META_CLIENT_CACHE: new Map(), renderHomeResume() {}, spoilerConcealed: () => false, revealSpoilerEpisode() {},
    setInterval: () => 1, clearInterval() {}, console,
    Date: class extends Date { static now() { return NOW; } },
    ...extra,
  };
  vm.runInNewContext(`${helpers}; ${frenchTitles}; ${spoilerTarget}; ${animeHome};
    ${NAMES.map(name => `this.${name} = ${name};`).join(' ')}
    this.state = () => ({ page: currentAnimeSubTab, library: animeLibraryIds, home: animeHomeData, week: animeWeekData, filter: animeWeekFilter, day: animeWeekDay });
    this.setLibrary = ids => { animeLibraryIds = new Set(ids); };
    this.setWeek = (data, day) => { animeWeekData = data; animeWeekDay = day; };`, context);
  return { context, elements };
}

// Values made inside the page's context compare by content, not by prototype.
const plain = value => JSON.parse(JSON.stringify(value));
const at = (hour, minute = 0, day = 13) => new Date(2026, 10, day, hour, minute).getTime();
const NOW = at(6, 14);

test('one line of tabs: À la une, Planning, Catalogue; the page follows « À continuer » order', () => {
  const start = html.indexOf('<nav id="animeSubNav" class="library-subnav" aria-label="Anime" style="display:none;">');
  assert.ok(start > 0);
  const nav = html.slice(start, html.indexOf('</nav>', start));
  assert.deepEqual([...nav.matchAll(/<button class="lib-subtab[^"]*" id="(\w+)" onclick="switchAnimeSubTab\('(\w+)'\)">([^<]+)<\/button>/g)].map(m => m.slice(1)),
    [['animeSubTabHome', 'home', 'À la une'], ['animeSubTabSchedule', 'schedule', 'Planning'], ['animeSubTabCatalog', 'catalog', 'Catalogue']]);
  const order = ['id="animeSubNav"', 'id="homeResumeSection"', 'id="heroSpotlight"', 'id="animeHomePanel"', 'id="catalogHeader"', 'id="animeSchedulePanel"']
    .map(mark => html.indexOf(mark));
  assert.deepEqual(order, [...order].sort((a, b) => a - b), 'tabs, what is in progress, the banner, the rows');
  assert.ok(order.every(index => index > 0));
  assert.doesNotMatch(html.slice(html.indexOf('id="animeSchedulePanel"'), html.indexOf('<!-- Paramètres')), /style="(?!display:none;")|Simulcast|Heure Locale/i,
    'the planning has no inline look left');
  const scripts = interfaceScripts();
  assert.ok(scripts.indexOf('app-tabs.js') < scripts.indexOf('anime-home.js') && scripts.indexOf('anime-home.js') < scripts.indexOf('app-start.js'));
  assert.doesNotMatch(source, /renderAnimeScheduleGrid|openAnimeFromSchedule|api\/anime\/schedule|schedule-card/, 'the older planning is gone');
});

test('each page shows its own parts; the banner and « Voir plus » belong to one page each', () => {
  const { context, elements } = load({ heroSpotlightItems: [{}], catalogHasMore: true });
  const shown = () => Object.fromEntries(['animeHomePanel', 'animeSchedulePanel', 'catalogHeader', 'genreFilters', 'catalogSortWrap', 'postersGrid', 'loadMoreWrap', 'heroSpotlight']
    .map(id => [id, elements[id].style.display]));
  context.applyAnimeSubTab();
  assert.deepEqual(shown(), { animeHomePanel: 'block', animeSchedulePanel: 'none', catalogHeader: 'none', genreFilters: 'none',
    catalogSortWrap: 'none', postersGrid: 'none', loadMoreWrap: 'none', heroSpotlight: 'flex' });
  context.switchAnimeSubTab('schedule');
  assert.deepEqual(shown(), { animeHomePanel: 'none', animeSchedulePanel: 'block', catalogHeader: 'none', genreFilters: 'none',
    catalogSortWrap: 'none', postersGrid: 'none', loadMoreWrap: 'none', heroSpotlight: 'none' });
  assert.equal(elements.homeResumeSection.style.display, 'none');
  assert.equal(elements.animeSubTabSchedule.getAttribute('aria-current'), 'page');
  assert.equal(elements.animeSubTabHome.getAttribute('aria-current'), null);
  context.switchAnimeSubTab('catalog');
  assert.deepEqual(shown(), { animeHomePanel: 'none', animeSchedulePanel: 'none', catalogHeader: 'flex', genreFilters: 'flex',
    catalogSortWrap: 'flex', postersGrid: 'grid', loadMoreWrap: 'flex', heroSpotlight: 'none' });
  context.switchAnimeSubTab('nowhere');
  assert.equal(context.state().page, 'home');
  // The catalogue loads behind every page and re-applies the page when it lands; leaving the tab hides both pages.
  assert.match(source, /if \(activeTab === 'anime' && !searchResultsShown && !personView\) applyAnimeSubTab\(\);/);
  assert.match(source, /for \(const id of \['animeSchedulePanel', 'animeHomePanel'\]\) \{\n\s+const animePage = document\.getElementById\(id\);\n\s+if \(animePage\) animePage\.style\.display = 'none';/);
  assert.match(source, /selectGenre\('', document\.querySelector\('#genreFilters \.chip'\)\);\n\s+await switchAnimeSubTab\(currentAnimeSubTab\);/);
});

test('a card says the next episode and when, in a few words', () => {
  const { context } = load();
  const next = (ms, episode = 8) => context.animeNextLabel({ episode, airing_at: ms / 1000 }, NOW);
  assert.equal(next(at(5)), 'Ép. 8 · diffusé');
  assert.equal(next(at(16, 30)), 'Ép. 8 · 16:30');
  assert.equal(next(at(0, 30, 14)), 'Ép. 8 · demain', 'a few minutes after midnight is tomorrow, not « dans 18 h »');
  assert.equal(next(at(23, 0, 15)), 'Ép. 8 · dim.');
  assert.equal(next(at(9, 0, 28), 1181), 'Ép. 1181 · 28 nov.');
  assert.equal(context.animeNextLabel(null, NOW), '');
  assert.equal(context.animeWorkFacts({ format: 'TV', episodes: 12, score: 87, status: 'RELEASING' }), 'Série · 12 ép. · 87 %');
  assert.equal(context.animeWorkFacts({ format: 'MOVIE', episodes: 1, score: 90, status: 'FINISHED' }), 'Film · 90 %');
  assert.equal(context.animeWorkFacts({ format: 'ONA', episodes: 0, score: 0, status: 'NOT_YET_RELEASED', year: 2027, month: 1 }), 'Série web · janv. 2027');
  assert.equal(context.animeWorkFacts({ format: 'TV', status: 'NOT_YET_RELEASED', year: 2027, month: 0 }), 'Série · 2027');
  assert.equal(context.animeWorkFacts({ format: '', score: 0 }), 'Anime');
});

test('a card carries the poster card look, its badge and what the user did with the title', () => {
  const { context } = load({
    userWatchlist: [{ id: 'tt1' }],
    userHistory: [{ id: 'tt2', type: 'series', progress_pct: 30 }, { id: 'tt3', type: 'movie', completed: true },
      { id: 'tt4', type: 'series', completed: true, series_finished: false }, { id: 'tt5', type: 'series', completed: true, series_finished: true }],
  });
  const mine = context.animeMineStates();
  assert.deepEqual(plain([...mine]), [['tt1', 'Ma Liste'], ['tt2', 'En cours'], ['tt3', '✓ Vu'], ['tt4', 'En cours'], ['tt5', '✓ Vu']]);
  const work = { id: 42, title: 'Frieren', romaji: 'Sousou no Frieren', format: 'TV', episodes: 28, score: 90, status: 'RELEASING',
    poster: 'https://img/42.jpg', next: { episode: 8, airing_at: at(16, 30) / 1000 }, kino: { id: 'tt2', type: 'series', season: 2 } };
  const card = context.animeCardHtml(work, NOW, mine);
  assert.match(card, /<div class="poster-card anime-card" data-anime="42" onclick="openAnimeWork\(42\)">/);
  assert.match(card, /<span class="poster-watched is-static">En cours<\/span>/);
  assert.match(card, /<span class="anime-card-next">Ép\. 8 · 16:30<\/span>/);
  assert.match(card, /<div class="poster-title" title="Sousou no Frieren">Frieren<\/div>\s*<div class="poster-year">Série · 28 ép\. · 90 %<\/div>/);
  assert.equal(context.animeWorks.get(42), work, 'kept for the click');
  const bare = context.animeCardHtml({ id: 43, title: 'Autre', format: 'MOVIE', kino: null }, NOW, mine);
  assert.doesNotMatch(bare, /poster-watched|anime-card-next| title="/);
  // Keyboard: the cards are poster cards, reached and opened like the others.
  assert.match(source, /const KEYBOARD_CARD_SELECTOR = '\.poster-card, \.card, \.search-suggest-item';/);
});

test('« À continuer » on « À la une » keeps the anime titles the server names', () => {
  const { context } = load();
  context.setLibrary(['tt10']);
  assert.equal(context.isAnimeTitle({ id: 'tt10', type: 'series' }), true);
  assert.equal(context.isAnimeTitle({ id: 'tt11', type: 'series' }), false);
  assert.equal(context.isAnimeTitle({ id: 'tt11', type: 'series', is_anime: true }), true);
  assert.equal(context.isAnimeTitle({ id: 'tt12', type: 'anime' }), true);
  assert.equal(context.isAnimeTitle(null), false);
  assert.match(source, /function resumeBelongsToTab\(entry\) \{\n  if \(activeTab === 'history'\) return true;\n  if \(activeTab === 'anime'\) return isAnimeTitle\(entry\);\n  return listItemKind\(entry\) === homeKind\(\);\n\}/);
  assert.match(source, /const animeHome = activeTab === 'anime' && currentAnimeSubTab === 'home';\n\s+const inProg = getInProgressHistory\(\)\.filter\(h => \(activeTab === 'history' \|\| !shownAsNew\.has\(h\.id\)\) && resumeBelongsToTab\(h\)\);\n\s+if \(activeTab !== 'movies' && activeTab !== 'series' && activeTab !== 'history' && !animeHome\) \{/);
});

test('the rows arrive once, say when AniList is down, and offer the catalogue when there is nothing', async () => {
  let calls = 0;
  let answer = { ok: true, library: ['tt9'], rows: [{ key: 'trending', title: 'Tendances', note: 'Ce qui se regarde', items: [{ id: 1, title: 'A', format: 'TV' }] }] };
  const { context, elements } = load({ api: async () => { calls += 1; if (answer instanceof Error) throw answer; return answer; } });
  context.renderAnimeHome();
  assert.match(elements.animeHomePanel.innerHTML, /role="status">Chargement…/);
  await Promise.all([context.loadAnimeHome(), context.loadAnimeHome()]);
  assert.equal(calls, 1, 'one request for two callers');
  assert.match(elements.animeHomePanel.innerHTML, /<section class="home-shelf anime-shelf" aria-label="Tendances">[\s\S]*<span class="section-title">Tendances<\/span>\s*<span class="section-note">Ce qui se regarde<\/span>[\s\S]*<div class="list-shelf-row">\s*<div class="poster-card anime-card" data-anime="1"/);
  assert.deepEqual(plain([...context.state().library]), ['tt9']);
  answer = { ok: false, rows: [], library: [], message: 'indisponibles' };
  await context.loadAnimeHome(true);
  assert.match(elements.animeHomePanel.innerHTML, /data-anime="1"/, 'the rows already shown stay');
  answer = { ...context.state().home, stale: true };
  await context.loadAnimeHome();
  assert.match(elements.animeHomePanel.innerHTML, /<p class="anime-home-stale">AniList ne répond pas/);

  const offline = Object.assign(new Error('x'), { code: 'offline' });
  const empty = load({ api: async () => { throw offline; } });
  await empty.context.loadAnimeHome();
  assert.match(empty.elements.animeHomePanel.innerHTML, /Pas de connexion à internet[\s\S]*onclick="loadAnimeHome\(true\)">Réessayer<[\s\S]*onclick="switchAnimeSubTab\('catalog'\)">Ouvrir le catalogue</);
});

test('a paired work opens its fiche under the series name, on its season', async () => {
  const calls = [];
  const opened = [];
  const { context } = load({
    api: async (url, options) => { calls.push([url, options?.body]); return { meta: { name: 'Jujutsu Kaisen', releaseInfo: '2020–', poster: 'cinemeta.jpg' } }; },
    selectMedia: async media => { opened.push(media); },
  });
  context.animeCardHtml({ id: 145064, title: 'JUJUTSU KAISEN Season 2', year: 2023, poster: 'anilist.jpg', format: 'TV', kino: { id: 'tt12343534', type: 'series', season: 2 } });
  await context.openAnimeWork(145064);
  assert.deepEqual(calls, [['/api/meta?type=series&imdb_id=tt12343534', undefined]]);
  assert.deepEqual(plain(opened), [{ id: 'tt12343534', name: 'Jujutsu Kaisen', type: 'series', is_anime: true, year: '2020–', poster: 'cinemeta.jpg', open_season: 2 }],
    'the name sources are searched with is the series’ own, not the season’s');
  await context.openAnimeWork(145064);
  assert.equal(calls.length, 1, 'the fiche read once is kept');
  await context.openAnimeWork(999);
  assert.equal(opened.length, 2, 'an unknown number opens nothing');
  // The fiche starts on that season when the series has not been started.
  assert.match(source, /if \(!histItem && Number\(media\.open_season\) > 1\) \{\n\s+targetSeason = Number\(media\.open_season\);\n\s+targetEpisode = 1;\n\s+\}/);
});

test('a work without a fiche asks the server, then falls back to a search by title', async () => {
  const calls = [];
  const opened = [];
  const searched = [];
  let found = { ok: true, media: { id: 'tt28776190', type: 'series', open_season: 3 } };
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const { context, elements } = load({
    api: async (url, options) => {
      calls.push([url, options?.method, options?.body]);
      if (url === '/api/anime/open') { await gate; return found; }
      throw new Error('fiche unavailable');
    },
    selectMedia: async media => { opened.push(media); },
    runSearch: async () => { searched.push([elements.searchInput.value, elements.searchType.value]); },
  });
  context.animeCardHtml({ id: 189323, title: 'Shangri-La Frontier Season 3', year: 2027, poster: 'p.jpg', format: 'TV', kino: null });
  const first = context.openAnimeWork(189323);
  const second = context.openAnimeWork(189323);
  release();
  await Promise.all([first, second]);
  assert.deepEqual(calls[0], ['/api/anime/open', 'POST', '{"id":189323}']);
  assert.equal(calls.filter(call => call[0] === '/api/anime/open').length, 1, 'a second click while it opens does nothing');
  assert.deepEqual(plain(opened), [{ id: 'tt28776190', name: 'Shangri-La Frontier Season 3', type: 'series', is_anime: true, year: '2027', poster: 'p.jpg', open_season: 3 }],
    'without the fiche’s own name, the work’s');
  assert.deepEqual(plain(context.animeWorks.get(189323).kino), { id: 'tt28776190', type: 'series', season: 3 }, 'remembered for the next click');

  found = { ok: false, title: 'Inconnu' };
  context.animeCardHtml({ id: 7, title: "L'inconnu", format: 'TV', kino: null });
  await context.openAnimeWork(7);
  assert.deepEqual(searched, [["L'inconnu", 'anime']]);
  assert.equal(opened.length, 1);
});

test('the week is seven days from the first, named from today', () => {
  const { context } = load();
  const from = at(0) / 1000;
  const episode = (id, ms, popularity = 50000, kino = null) => ({ id, title: `T${id}`, episode: 3, airing_at: ms / 1000, popularity, kino, format: 'TV' });
  const data = { ok: true, from, episodes: [episode(1, at(1, 50), 59), episode(2, at(9)), episode(3, at(23, 59)), episode(4, at(0, 0, 14)),
    episode(5, at(12, 0, 19), 900, { id: 'tt9', type: 'series', season: 1 }), episode(6, at(0, 0, 20))] };
  const days = context.animeWeekDays(data, NOW);
  assert.deepEqual(plain(days.map(day => day.label)), ['Aujourd’hui', 'Demain', 'Dim. 15', 'Lun. 16', 'Mar. 17', 'Mer. 18', 'Jeu. 19']);
  assert.deepEqual(plain(days.map(day => day.episodes.map(item => item.id))), [[1, 2, 3], [4], [], [], [], [], [5]], 'midnight opens the next day; day eight is out');
  assert.deepEqual(plain(context.animeWeekDays(data, at(8, 0, 14)).map(day => day.label).slice(0, 3)), ['Hier', 'Aujourd’hui', 'Demain'],
    'a planning kept past midnight still names its days right');
  assert.deepEqual(plain(context.animeWeekDays(null, NOW)), []);

  const mine = new Map([['tt9', 'Ma Liste']]);
  const keep = filter => data.episodes.filter(item => context.animeWeekKeep(item, filter, mine)).map(item => item.id);
  assert.equal(context.ANIME_POPULAR_FROM, 10000);
  assert.deepEqual(keep('popular'), [2, 3, 4, 5, 6], 'the user’s own series stays, however small');
  assert.deepEqual(keep('mine'), [5]);
  assert.deepEqual(keep('all'), [1, 2, 3, 4, 5, 6]);
});

test('a line says when: the hour first, then how long', () => {
  const { context } = load();
  const label = ms => context.animeAiringLabel(ms / 1000, NOW);
  assert.equal(label(at(9)), 'Dans 2 h 46');
  assert.equal(label(at(6, 26)), 'Dans 12 min');
  assert.equal(label(at(6, 14, 14)), 'Dans 24 h');
  assert.equal(label(at(19, 14, 13)), 'Dans 13 h');
  assert.equal(label(at(6, 14, 16)), 'Dans 3 j');
  assert.equal(label(at(4)), 'Diffusé il y a 2 h 14');
  assert.equal(label(at(6, 13)), 'Diffusé il y a 1 min');
  const row = context.animeWeekRowHtml({ id: 5, title: 'Frieren', romaji: 'Sousou no Frieren', episode: 7, airing_at: at(9) / 1000, format: 'TV',
    genres: ['Adventure', 'Drama', 'Fantasy'], score: 90, poster: 'p.jpg', kino: { id: 'tt9', type: 'series', season: 2 } }, NOW, new Map([['tt9', 'Ma Liste']]));
  assert.match(row, /<article class="offline-item myseries-item week-row" data-anime="5">\s*<time class="week-time" datetime="[^"]+">09:00<\/time>/);
  assert.match(row, /<strong title="Sousou no Frieren">Frieren<\/strong>\s*<span class="myseries-fact">Épisode 7 · Ma Liste<\/span>\s*<span class="myseries-fact">Série · Aventure · Drame · 90 %<\/span>/);
  assert.match(row, /<span class="week-when">Dans 2 h 46<\/span>\s*<button type="button" class="btn btn-secondary" onclick="openAnimeWork\(5\)">Fiche<\/button>/);
  assert.match(context.animeWeekRowHtml({ id: 6, title: 'Vu', episode: 1, airing_at: at(5) / 1000 }, NOW, new Map()), /week-row is-aired"/);
  assert.match(context.animeWeekRowHtml({ id: 8, title: 'K-On', episode: 1, airing_at: at(9) / 1000, format: 'TV_SHORT', genres: ['Slice of Life', 'Supernatural'] }, NOW, new Map()),
    /<span class="myseries-fact">Série courte · Tranche de vie · Surnaturel<\/span>/);
});

test('the planning counts, filters and keeps the day; an episode not reached hides its image for a followed series only', () => {
  const concealed = [];
  const revealed = [];
  const { context, elements } = load({
    userHistory: [{ id: 'tt9', type: 'series', watched_episodes: ['s2e1', 's2e2'] }],
    spoilerConcealed: (id, season, episode) => { concealed.push([id, season, episode]); return true; },
    revealSpoilerEpisode: (season, episode, id) => { revealed.push([season, episode, id]); },
  });
  const from = at(0) / 1000;
  const episode = (id, ms, popularity, kino = null) => ({ id, title: `T${id}`, episode: 3, airing_at: ms / 1000, popularity, kino, format: 'TV', poster: `p${id}.jpg` });
  context.setWeek({ ok: true, from, episodes: [episode(1, at(9), 50000), episode(2, at(10), 300), episode(3, at(11), 300, { id: 'tt9', type: 'series', season: null }),
    episode(4, at(9, 0, 14), 50000)] }, -1);
  context.renderAnimeWeek();
  assert.equal(elements.animeScheduleSummary.textContent, '3 épisodes sur sept jours · 1 de vos animes');
  assert.match(elements.animeScheduleDays.innerHTML, /^<button type="button" class="chip active" aria-pressed="true" onclick="selectAnimeWeekDay\(0\)">Aujourd’hui <span class="lib-count">2<\/span><\/button><button type="button" class="chip" aria-pressed="false" onclick="selectAnimeWeekDay\(1\)">Demain <span class="lib-count">1<\/span>/);
  const rows = () => [...elements.animeScheduleGrid.innerHTML.matchAll(/data-anime="(\d+)"/g)].map(match => Number(match[1]));
  assert.deepEqual(rows(), [1, 3]);
  // Series tt9 is followed (season 2 only): its episode 3 is hidden, with a way to show it; the others keep their cover.
  assert.deepEqual(concealed, [['tt9', 2, 3]]);
  assert.match(elements.animeScheduleGrid.innerHTML, /src="p1\.jpg"/);
  assert.doesNotMatch(elements.animeScheduleGrid.innerHTML, /src="p3\.jpg"/);
  assert.match(elements.animeScheduleGrid.innerHTML, /role="img" aria-label="Image masquée pour l’épisode 3">[\s\S]*onclick="revealScheduleEpisode\(3, 3\)"[^>]*>Afficher</);
  vm.runInNewContext('revealScheduleEpisode(3, 3)', context);
  assert.deepEqual(revealed, [[2, 3, 'tt9']]);

  context.setAnimeWeekFilter('all');
  assert.deepEqual(rows(), [1, 2, 3]);
  assert.equal(elements.animeScheduleFilter.value, 'all');
  context.setAnimeWeekFilter('mine');
  assert.deepEqual(rows(), [3]);
  context.selectAnimeWeekDay(1);
  assert.match(elements.animeScheduleGrid.innerHTML, /Aucun de vos animes ce jour-là/);
  context.setAnimeWeekFilter('bogus');
  assert.equal(context.state().filter, 'popular');
  assert.deepEqual(rows(), [4], 'the chosen day stays');

  context.setWeek({ ok: false, episodes: [], message: 'Planning indisponible pour le moment.' }, 0);
  context.renderAnimeWeek();
  assert.equal(elements.animeScheduleSummary.textContent, 'Planning indisponible pour le moment.');
  assert.equal(elements.animeScheduleGrid.innerHTML, '');
});

test('the styles speak the shared language, and the older sheets hold no planning rule', () => {
  const start = css.indexOf('/* ---------- Anime: « À la une »');
  assert.ok(start > css.indexOf('/* ---------- The Bibliothèque ----------'));
  // Up to the Windows title bar (1.4.0), which comes right after it.
  const next = css.indexOf('/* Windows: KINO', start);
  const own = css.slice(start, next > 0 ? next : undefined);
  assert.match(own, /#animeSubNav\[style\*="flex"\] ~ #catalogHeader #catalogTitle \{\n  position: absolute;\n  width: 1px;\n  height: 1px;/, 'the tab is the title');
  assert.deepEqual([...new Set([...own.matchAll(/#[0-9a-f]{3,8}\b|rgba?\([^)]*\)/gi)].map(match => match[0]))], ['rgba(9, 9, 11, .78)'],
    'one veil, under the badge that sits on a poster');
  const sizes = [...own.matchAll(/font-size: ([^;]+);/g)].map(match => match[1]);
  assert.ok(sizes.length > 4 && sizes.every(size => size.startsWith('var(--k-fs-')), `sizes from the scale: ${[...new Set(sizes)]}`);
  assert.match(own, /\.anime-card\.is-opening \{ opacity: \.55; pointer-events: none; \}/);
  assert.doesNotMatch(read('web/css/style.css') + read('web/css/redesign.css'), /schedule-|badge-status|SIMULCAST/i);
});
