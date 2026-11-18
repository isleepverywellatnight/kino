// Livelier catalogue cards: preview, progress on the poster, placeholders.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
// The stylesheet loaded last, whose preview timing is the one that applies.
const designCss = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const between = (start, end) => start + source.split(start, 2)[1].split(end, 1)[0];

function renderCards(metas, history = []) {
  const grid = { innerHTML: '' };
  const context = {
    document: { getElementById: id => (id === 'postersGrid' ? grid : null) },
    hideWatchedInCatalog: false, activeTab: 'movies', userHistory: history,
    isInWatchlist: () => false, getSeriesTargetEpisode: () => ({ season: 1, episode: 1 }),
    escapeHtml: value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
    escapeJsString: value => String(value),
  };
  vm.runInNewContext(`${between('const ORIGINAL_TITLES = new Map();', '// --- Immersive fiche: French labels')}
    ${between('function frenchRuntime(value) {', '// « Won 2 Oscars')}
    ${between('function posterSkeletons(', 'async function loadRdCloud()')}
    renderPosterCards(${JSON.stringify(metas)}, 'movie'); this.skeletons = posterSkeletons;`, context);
  return { html: grid.innerHTML, skeletons: context.skeletons };
}

test('a card carries a preview with French genres, length and rating, and two actions', () => {
  const { html } = renderCards([{ id: 'tt0107120', name: 'Hocus Pocus', type: 'movie', releaseInfo: '1993', imdbRating: '6.9',
    genres: ['Comedy', 'Family', 'Fantasy'], runtime: '96 min', poster: 'https://example.test/p.jpg' }]);
  assert.match(html, /<div class="poster-preview">/, 'its actions can be reached with the keyboard');
  assert.match(html, /<div class="poster-preview-meta">Comédie · Famille · 1 h 36 · ★ 6.9<\/div>/);
  assert.match(html, /class="poster-preview-desc" data-preview-id="tt0107120" data-preview-type="movie"/);
  assert.match(withCalls(html), /class="poster-preview-play" onclick="oneClickCard\(event, this,/);
  assert.match(withCalls(html), /<button type="button" onclick="toggleCardWatched\(event,[^>]*>Marquer vu</);
  assert.doesNotMatch(html, /btn-oneclick|tabindex="-1"/, 'no play button repeated under the poster');
  assert.match(withCalls(html), /onload="kinoLoaded\(this\)"/);
  assert.doesNotMatch(html, /#4ade80|#38bdf8/, 'no green or blue: monochrome');
});

test('progress is drawn on the poster and series have no « Marquer vu »', () => {
  const { html } = renderCards([{ id: 'tt1', name: 'Film', type: 'movie' }, { id: 'tt2', name: 'Série', type: 'series' }],
    [{ id: 'tt1', progress_pct: 42 }]);
  assert.match(html, /<div class="poster-progress" aria-label="42 % vus"><span style="width:42%"><\/span><\/div>/);
  const series = html.slice(html.indexOf('data-preview-id="tt2"'));
  assert.doesNotMatch(series.split('</div>')[2] || '', /Marquer vu/);
});

test('placeholders keep the grid shape and are announced once', () => {
  const { skeletons } = renderCards([]);
  const html = skeletons(3);
  assert.equal((html.match(/poster-card is-skeleton/g) || []).length, 3);
  assert.match(html, /role="status">Chargement…</);
  assert.match(html, /aria-hidden="true"/);
  assert.match(source, /grid\.innerHTML = posterSkeletons\(\);/);
});

test('the synopsis is fetched once after a pause, then reused by the fiche cache', async () => {
  const calls = [];
  const timers = [];
  const desc = { dataset: { previewId: 'tt0107120', previewType: 'movie' }, textContent: '' };
  const card = { querySelector: () => desc };
  const cache = new Map();
  const context = {
    META_CLIENT_CACHE: cache,
    api: async url => { calls.push(url); return { meta: { description_fr: 'Un adolescent nommé Max…' } }; },
    document: { querySelectorAll: () => [desc] },
    setTimeout: (fn, delay) => { timers.push([fn, delay]); return timers.length; }, clearTimeout: () => {},
  };
  vm.runInNewContext(`${between('const POSTER_PREVIEW_DELAY = 350;', 'function initPosterPreviews()')}
    this.schedule = schedulePosterPreview; this.load = loadPosterPreview;`, context);
  context.schedule(card);
  assert.equal(timers[0][1], 350);
  await context.load(desc);
  assert.equal(desc.textContent, 'Un adolescent nommé Max…');
  assert.equal(desc.dataset.loaded, 'done');
  assert.equal(cache.get('tt0107120').description_fr, 'Un adolescent nommé Max…');
  context.schedule(card);
  assert.equal(timers.length, 1, 'not asked twice');
  assert.equal(calls.length, 1);
});

test('the preview shows on hover after a pause and on keyboard focus; never on touch screens', () => {
  assert.match(designCss, /\.poster-card:hover \.poster-preview, \.poster-card:focus-within \.poster-preview, \.poster-card:focus-visible \.poster-preview \{\s*opacity: 1;\s*transform: none;\s*transition-delay: \.25s;/);
  assert.match(designCss, /\.poster-card:focus-within \.poster-preview \{ transition-delay: 0s; \}/, 'at once on keyboard focus');
  assert.match(css, /@media \(hover: none\) \{ \.poster-preview \{ display: none; \} \}/);
  assert.match(source, /initPosterPreviews\(\);/);
});
