// External titles, synopses and file names are rendered as text, never as markup,
// and inline handlers keep working when a title contains quotes or apostrophes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const between = (start, end) => start + source.split(start, 2)[1].split(end, 1)[0];
const helpers = between('function escapeHtml(s)', 'async function checkTraktStatus()');
// French display titles (titleAttrs, displayTitle, frenchGenre) are used by the
// card and hero templates; load the real ones so their escaping is tested too.
const frenchTitles = between('const ORIGINAL_TITLES = new Map();', '// --- Immersive fiche: French labels');
const hero = between('function drawHeroSpotlightSlide()', 'let caseAudioCtx = null;');
const posters = between('// One catalogue card', 'async function loadRdCloud()');
const customLists = between('function renderCustomListsTab()', 'function surpriseMeFromCustomList(');
const animeHome = require('../tools/interface_source.js').inFrench(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/anime-home.js'), 'utf8'));

const HOSTILE = `Tom & "Jerry's" <img src=x onerror="window.pwned=1">`;

function element() {
  return { style: { setProperty(name, value) { this[name] = value; } }, innerHTML: '', textContent: '', scrollIntoView() {} };
}

function documentWith(elements) {
  return { getElementById: id => (elements[id] ||= element()) };
}

// Parse the onclick attribute the way a browser would: decode entities, then evaluate the call.
function handlerCalls(html, functionName) {
  const calls = [];
  const decode = text => text.replaceAll('&quot;', '"').replaceAll('&#39;', "'").replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>').replaceAll('&amp;', '&');
  for (const [, quote, body] of html.matchAll(/onclick=(["'])(.*?)\1/g)) {
    const code = decode(body);
    if (!code.startsWith(functionName + '(')) continue;
    const context = { [functionName]: (...args) => calls.push(args), event: {}, document: { querySelector: () => null } };
    vm.runInNewContext(code, context);
    assert.ok(quote);
  }
  return calls;
}

// Text inside a quoted attribute value is inert; markup must not appear anywhere else.
function assertNoInjectedMarkup(html) {
  const outsideAttributes = html.replace(/=(["'])[\s\S]*?\1/g, '=""');
  assert.doesNotMatch(outsideAttributes, /<img src=x/);
  assert.match(html, /&lt;img src=x/, 'the hostile title should still be shown, as text');
}

test('hero spotlight shows hostile catalog text as text, then the French synopsis', async () => {
  const box = element();
  const descEl = { textContent: '' };
  const requests = [];
  const context = {
    document: { getElementById: () => box, querySelector: () => descEl },
    heroSpotlightItems: [{ id: 'tt1', type: 'movie', name: HOSTILE, description: HOSTILE, genres: [HOSTILE], year: '2016' }],
    heroSpotlightIdx: 0, activeTab: 'movies', Image: function Image() {},
    api: async path => { requests.push(path); return { meta: { description_fr: 'Un garçon <b>et</b> un monstre.' } }; },
  };
  vm.runInNewContext(`${helpers}; ${frenchTitles}; ${hero}; drawHeroSpotlightSlide();`, context);
  assertNoInjectedMarkup(box.innerHTML);
  assert.match(box.innerHTML, /Tom &amp; &quot;Jerry&#39;s&quot; &lt;img/);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(requests, ['/api/meta?type=movie&imdb_id=tt1']);
  assert.equal(descEl.textContent, 'Un garçon <b>et</b> un monstre.', 'set as text, not markup');
  vm.runInNewContext('drawHeroSpotlightSlide();', context);
  assert.match(box.innerHTML, /Un garçon &lt;b&gt;et&lt;\/b&gt; un monstre\./);
  assert.equal(requests.length, 1, 'the translation is fetched once per title');
});

test('poster cards escape names and keep a working click payload', () => {
  const elements = {};
  const context = {
    document: documentWith(elements), activeTab: 'movies', hideWatchedInCatalog: false, userHistory: [],
    isInWatchlist: () => false,
  };
  vm.runInNewContext(`${helpers}; ${frenchTitles}; ${posters}; renderPosterCards([{id: 'tt1', name: ${JSON.stringify(HOSTILE)}, type: 'movie', year: '2016', poster: 'https://example.test/p.jpg'}], 'movie');`, context);
  const html = elements.postersGrid.innerHTML;
  assertNoInjectedMarkup(html);
  const [args] = handlerCalls(html, 'selectMedia');
  assert.equal(args[0].name, HOSTILE);
});

test('Letterboxd list cards survive quotes in titles', () => {
  const elements = {};
  const list = {
    id: 'u-list', title: HOSTILE, description: HOSTILE, url: 'https://letterboxd.com/u/list/x/',
    items: [{ id: 'tt2', name: HOSTILE, year: '1999', poster: 'https://example.test/p.jpg', imdbRating: '8.1' }],
  };
  const context = {
    document: documentWith(elements), userCustomLists: [list], activeTab: 'customlists', hideWatchedInCatalog: false,
    userHistory: [], isInWatchlist: () => false, listItemState: () => 'todo', window: {},
  };
  vm.runInNewContext(`${helpers}; ${frenchTitles}; ${posters}; ${customLists}; renderCustomListsTab();`, context);
  const html = elements.postersGrid.innerHTML;
  assertNoInjectedMarkup(html);
  const [args] = handlerCalls(html, 'selectMedia');
  assert.equal(args[0].name, HOSTILE);
  assert.deepEqual(handlerCalls(html, 'deleteLetterboxdCustomList'), [['u-list']]);
});

test('anime rows and planning lines show hostile titles as text, and open a work by its number only', () => {
  const elements = {};
  const work = { id: 154587, title: HOSTILE, romaji: `Sousou "no" Frieren's`, format: 'TV', episodes: 28, score: 90, genres: ['Fantasy'],
    poster: 'p', status: 'RELEASING', next: null, kino: null, popularity: 50000 };
  const context = {
    document: documentWith(elements), userWatchlist: [], userHistory: [], heroSpotlightItems: [], activeTab: 'anime',
    renderHomeResume() {}, scheduleSpoilerTarget: () => null, spoilerConcealed: () => false,
    setInterval: () => 0, clearInterval() {},
  };
  const dayStart = new Date(2026, 10, 13).getTime() / 1000;
  vm.runInNewContext(`${helpers}; ${frenchTitles}; ${animeHome};
    animeHomeData = { ok: true, rows: [{ key: 'trending', title: ${JSON.stringify(HOSTILE)}, note: ${JSON.stringify(HOSTILE)}, items: [${JSON.stringify(work)}] }] };
    renderAnimeHome();
    animeWeekFilter = 'all';
    animeWeekData = { ok: true, from: ${dayStart}, to: ${dayStart + 7 * 86400}, episodes: [{ ...${JSON.stringify(work)}, episode: 3, airing_at: ${dayStart + 3600} }] };
    animeWeekDay = 0;
    renderAnimeWeek();`, context);
  for (const id of ['animeHomePanel', 'animeScheduleGrid']) {
    const html = elements[id].innerHTML;
    assertNoInjectedMarkup(html);
    assert.deepEqual(handlerCalls(html, 'openAnimeWork'), [[154587]], id);
    assert.doesNotMatch(html.replace(/=(["'])[\s\S]*?\1/g, '=""'), /Frieren's/, 'the second title stays inside its attribute');
  }
});
