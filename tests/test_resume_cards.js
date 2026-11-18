// « À continuer » : large stills, the episode to watch, time left, and the
// next episode's air date when it is not out yet.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const { withCalls } = require('../tools/interface_source.js');

const source = require('../tools/interface_source.js').appSource();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/redesign.css'), 'utf8');

const resumeCode = 'const FRENCH_MONTHS' + source
  .split('const FRENCH_MONTHS', 2)[1]
  .split('async function removeHistoryItem', 1)[0];
const targetCode = 'function getSeriesTargetEpisode(h)' + source
  .split('function getSeriesTargetEpisode(h)', 2)[1]
  .split('const FRENCH_MONTHS', 1)[0];
const remainingCode = 'function formatRemaining(pos, dur)' + source
  .split('function formatRemaining(pos, dur)', 2)[1]
  .split('let catalogSort', 1)[0];

const kindCode = 'function listItemKind(m)' + source.split('function listItemKind(m)', 2)[1].split('\n}\n', 1)[0] + '\n}\n';
class FixedDate extends Date {
  constructor(...args) { super(...(args.length ? args : [Date.UTC(2026, 10, 12)])); }
  static now() { return Date.UTC(2026, 10, 12); }
}

const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

function load(extra = {}) {
  const context = {
    networkOnline: true,
    META_CLIENT_CACHE: new Map(),
    escapeHtml,
    attrJson: value => escapeHtml(JSON.stringify(value)),
    titleAttrs: () => '',
    displayTitle: item => item?.name || '',
    encodeURIComponent,
    // « Today » is the fixtures' own day, not the day the tests run.
    Date: FixedDate,
    Promise,
    ...extra,
  };
  vm.runInNewContext(`${targetCode}\n${remainingCode}\n${kindCode}\n${resumeCode}\nthis.api_ = { frenchReleaseDay, resumeEpisodeInfo, resumeCardView, openResumeCard, resumeImageFallback, requestResumeMetas, renderHomeResume, resumeMetaRequests };`, context);
  return { ...context.api_, context };
}

const TODAY = '2026-11-10';
const seriesMeta = {
  videos: [
    { season: 2, episode: 2, name: 'Fallen', released: '2026-11-02T00:00:00.000Z', thumbnail: 'https://episodes.metahub.space/tt3/2/2/w780.jpg' },
    { season: 2, episode: 3, name: 'Live Free and Die', released: '2026-11-16T00:00:00.000Z', thumbnail: 'https://episodes.metahub.space/tt3/2/3/w780.jpg' },
    { season: 2, number: 4, name: 'Episode 4', released: '2026-11-23T00:00:00.000Z', thumbnail: 'http://insecure.example/4.jpg' },
  ],
};

test('air dates read naturally in French', () => {
  const { frenchReleaseDay } = load();
  assert.equal(frenchReleaseDay('2026-11-11', TODAY), 'demain');
  assert.equal(frenchReleaseDay('2026-11-13', TODAY), 'le 13 novembre');
  assert.equal(frenchReleaseDay('2026-12-01', TODAY), 'le 1er décembre');
  assert.equal(frenchReleaseDay('2027-01-03', TODAY), 'le 3 janvier 2027');
  assert.equal(frenchReleaseDay('', TODAY), '');
  assert.equal(frenchReleaseDay('2026-12-31', '2026-12-30'), 'demain');
});

test('episode details come from the series videos, without placeholders', () => {
  const { resumeEpisodeInfo } = load();
  assert.deepEqual({ ...resumeEpisodeInfo(seriesMeta, 2, 2, TODAY) }, {
    name: 'Fallen', still: 'https://episodes.metahub.space/tt3/2/2/w780.jpg', released: '2026-11-02', upcoming: false,
  });
  assert.equal(resumeEpisodeInfo(seriesMeta, 2, 3, TODAY).upcoming, true);
  const generic = resumeEpisodeInfo(seriesMeta, 2, 4, TODAY);
  assert.equal(generic.name, '', '« Episode 4 » says nothing more than the code');
  assert.equal(generic.still, '', 'only https images');
  assert.equal(resumeEpisodeInfo(seriesMeta, 3, 1, TODAY), null);
  assert.equal(resumeEpisodeInfo({}, 1, 1, TODAY), null);
  assert.equal(resumeEpisodeInfo(null, 1, 1, TODAY), null);
});

test('a film in progress shows its backdrop, the year and the time left', () => {
  const { resumeCardView } = load();
  const view = resumeCardView({
    id: 'tt0111161', name: 'The Shawshank Redemption', type: 'movie', year: '1994',
    poster: 'https://images.metahub.space/poster/medium/tt0111161/img', position: 3420, duration: 8520, progress_pct: 40,
  }, undefined, TODAY);
  assert.equal(view.kicker, 'Film · 1994');
  assert.equal(view.status, 'Il reste 1h25');
  assert.equal(view.progress, 40);
  assert.equal(view.image, 'https://images.metahub.space/background/medium/tt0111161/img');
  assert.equal(view.fallback, '');
  assert.equal(view.poster, 'https://images.metahub.space/poster/medium/tt0111161/img');
  assert.equal(view.action, 'Continuer');
  assert.equal(view.upcoming, false);
  assert.deepEqual({ ...view.play }, {
    imdb_id: 'tt0111161', type: 'movie', season: 1, episode: 1, title: 'The Shawshank Redemption',
    name: 'The Shawshank Redemption', poster: 'https://images.metahub.space/poster/medium/tt0111161/img', year: '1994',
  });
});

test('an episode in progress shows its still, name and time left', () => {
  const { resumeCardView } = load();
  const entry = { id: 'tt3', name: 'Dexter: Resurrection', type: 'series', season: 2, episode: 2, position: 1500, duration: 3300, progress_pct: 45, poster: 'https://example.test/p.jpg' };
  const view = resumeCardView(entry, seriesMeta, TODAY);
  assert.equal(view.kicker, 'S02E02 · Fallen');
  assert.equal(view.status, 'Il reste 30 min');
  assert.equal(view.progress, 45);
  assert.equal(view.image, 'https://episodes.metahub.space/tt3/2/2/w780.jpg');
  assert.equal(view.fallback, 'https://images.metahub.space/background/medium/tt3/img');
  assert.equal(view.play.title, 'Dexter: Resurrection — S02E02');
  assert.deepEqual([view.play.season, view.play.episode], [2, 2]);

  const beforeMeta = resumeCardView(entry, undefined, TODAY);
  assert.equal(beforeMeta.kicker, 'S02E02');
  assert.equal(beforeMeta.image, 'https://images.metahub.space/background/medium/tt3/img');

  const notStarted = resumeCardView({ ...entry, progress_pct: 0, position: 0 }, seriesMeta, TODAY);
  assert.equal(notStarted.status, 'Reprendre la lecture');
  assert.equal(notStarted.progress, 0);
});

test('a finished episode offers the next one', () => {
  const { resumeCardView } = load();
  const view = resumeCardView({ id: 'tt3', name: 'Dexter: Resurrection', type: 'series', season: 2, episode: 1, completed: true, progress_pct: 100, next_season: 2, next_episode: 2 }, seriesMeta, TODAY);
  assert.equal(view.isNext, true);
  assert.equal(view.kicker, 'S02E02 · Fallen');
  assert.equal(view.status, 'Épisode suivant');
  assert.equal(view.progress, 0);
  assert.equal(view.action, 'Épisode suivant · S02E02');
  assert.deepEqual([view.play.season, view.play.episode], [2, 2]);
  assert.equal(view.image, 'https://episodes.metahub.space/tt3/2/2/w780.jpg');
});

test('a next episode not aired yet shows its date and opens the fiche', () => {
  const { resumeCardView } = load();
  const view = resumeCardView({ id: 'tt3', name: 'Dexter: Resurrection', type: 'series', season: 2, episode: 2, completed: true, next_season: 2, next_episode: 3 }, seriesMeta, TODAY);
  assert.equal(view.upcoming, true);
  assert.equal(view.kicker, 'S02E03 · Live Free and Die');
  assert.equal(view.status, 'Prochain épisode le 16 novembre');
  assert.equal(view.action, 'Voir la fiche, prochain épisode le 16 novembre');
  assert.equal(view.image, 'https://images.metahub.space/background/medium/tt3/img', 'no still before airing');
  assert.equal(view.fallback, '');
  assert.deepEqual({ ...view.media }, { id: 'tt3', name: 'Dexter: Resurrection', type: 'series', poster: '', year: '' });
});

test('titles without an IMDb id use their poster', () => {
  const { resumeCardView } = load();
  const view = resumeCardView({ id: 'kitsu:1', name: 'Anime', type: 'series', season: 1, episode: 3, progress_pct: 20, poster: 'https://example.test/a.jpg' }, undefined, TODAY);
  assert.equal(view.image, 'https://example.test/a.jpg');
  assert.equal(load().resumeCardView({ id: 'x', name: 'Local', type: 'movie' }, undefined, TODAY).image, '');
});

test('clicking plays, except an episode not aired yet, which opens the fiche', () => {
  const calls = [];
  const { openResumeCard } = load({
    selectMedia: media => calls.push(['fiche', media.id]),
    oneClickPlay: (play, button) => calls.push(['play', play.imdb_id, button.name]),
  });
  const button = upcoming => ({ name: upcoming ? 'up' : 'now', closest: () => ({ classList: { contains: name => upcoming && name === 'is-upcoming' } }) });
  openResumeCard(button(false), { imdb_id: 'tt1' }, { id: 'tt1' });
  openResumeCard(button(true), { imdb_id: 'tt2' }, { id: 'tt2' });
  assert.deepEqual(calls, [['play', 'tt1', 'now'], ['fiche', 'tt2']]);
});

test('a missing still falls back to the backdrop, then the poster, then nothing', () => {
  const { resumeImageFallback } = load();
  const img = {
    src: 'https://episodes/still.jpg',
    dataset: { fallback: 'https://images/backdrop.jpg', poster: 'https://images/poster.jpg' },
    classes: new Set(),
    removed: false,
    getAttribute() { return this.src; },
    classList: null,
    remove() { this.removed = true; },
  };
  img.classList = { add: name => img.classes.add(name) };
  resumeImageFallback(img);
  assert.equal(img.src, 'https://images/backdrop.jpg');
  assert.equal(img.classes.has('is-poster'), false);
  resumeImageFallback(img);
  assert.equal(img.src, 'https://images/poster.jpg');
  assert.equal(img.classes.has('is-poster'), true);
  resumeImageFallback(img);
  assert.equal(img.removed, true);

  const bare = { src: 'https://images/backdrop.jpg', dataset: { fallback: '', poster: '' }, getAttribute() { return this.src; }, removed: false, remove() { this.removed = true; } };
  resumeImageFallback(bare);
  assert.equal(bare.removed, true);
});

test('series metas are fetched once, then the section is drawn again', async () => {
  const requested = [];
  let renders = 0;
  const { requestResumeMetas, context } = load({
    api: async url => {
      requested.push(url);
      if (url.includes('tt9')) throw new Error('offline');
      return { meta: { videos: [] } };
    },
  });
  context.renderHomeResume = () => { renders += 1; };
  vm.runInNewContext('renderHomeResume = this.renderHomeResume;', context);
  context.META_CLIENT_CACHE.set('tt2', { videos: [] });
  const items = [
    { id: 'tt1', type: 'series' }, { id: 'tt2', type: 'series' }, { id: 'tt5', type: 'movie' }, { id: 'tt9', type: 'series' },
  ];
  requestResumeMetas(items);
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(requested, ['/api/meta?type=series&imdb_id=tt1', '/api/meta?type=series&imdb_id=tt9']);
  assert.equal(context.META_CLIENT_CACHE.has('tt1'), true);
  assert.equal(context.META_CLIENT_CACHE.has('tt9'), false);
  assert.equal(renders, 1);
  requestResumeMetas(items);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(requested.length, 2, 'neither the fetched nor the failed meta is asked again');
});

function renderWith({ activeTab = 'movies', history, metas = {} }) {
  const elements = Object.fromEntries(
    ['homeResumeSection', 'homeResumeGrid', 'resumeSectionTitle', 'homeResumeAll', 'homeResumeClear']
      .map(id => [id, { style: {}, innerHTML: '', textContent: '', classList: { toggle(name, on) { this[name] = on; } } }])
  );
  const requested = [];
  const { renderHomeResume, context } = load({
    activeTab,
    document: { getElementById: id => elements[id] },
    getInProgressHistory: () => history,
    api: async url => { if (url.startsWith('/api/meta')) requested.push(url); return { meta: {} }; },
  });
  for (const [id, meta] of Object.entries(metas)) context.META_CLIENT_CACHE.set(id, meta);
  renderHomeResume();
  return { html: elements.homeResumeGrid.innerHTML, elements, requested };
}

test('the cards carry the still, the texts and both actions, escaped', () => {
  const hostile = 'Bad "<b>" & \'quote\'';
  const history = [
    { id: 'tt3', name: hostile, type: 'series', season: 2, episode: 2, completed: true, next_season: 2, next_episode: 3, poster: 'https://example.test/p.jpg' },
    { id: 'tt0111161', name: 'The Shawshank Redemption', type: 'movie', year: '1994', position: 3420, duration: 8520, progress_pct: 40 },
  ];
  // Each tab continues its own kind: the series under Séries, the film under Films.
  const series = renderWith({ activeTab: 'series', history, metas: { tt3: seriesMeta } });
  const films = renderWith({ activeTab: 'movies', history, metas: { tt3: seriesMeta } });
  assert.match(series.html, /imdb_id&quot;:&quot;tt3&quot;/);
  assert.doesNotMatch(series.html, /tt0111161/, 'no film under Séries');
  assert.match(films.html, /tt0111161/);
  assert.doesNotMatch(films.html, /imdb_id&quot;:&quot;tt3&quot;/, 'no series under Films');
  const both = renderWith({ activeTab: 'history', history, metas: { tt3: seriesMeta } });
  assert.equal((both.html.match(/<article class="resume-card/g) || []).length, 2, '« En cours » keeps them all');
  const html = series.html + films.html;
  const requested = [...series.requested, ...films.requested];
  assert.match(html, /<article class="resume-card is-upcoming">/);
  assert.match(html, /<span class="resume-kicker">S02E03 · Live Free and Die<\/span>/);
  assert.match(html, /<span class="resume-subinfo">Prochain épisode le 16 novembre<\/span>/);
  assert.match(html, /<span class="resume-subinfo">Il reste 1h25<\/span>/);
  assert.match(html, /<span class="resume-progress-fill" style="width:40%;"><\/span>/);
  assert.match(html, /data-poster="https:\/\/example\.test\/p\.jpg"/);
  assert.match(withCalls(html), /onerror="resumeImageFallback\(this\)"/);
  assert.doesNotMatch(html, /loading="lazy"/, 'the home row loads its few images at once');
  assert.match(withCalls(html), /onclick="openResumeCard\(this, \{ imdb_id: 'tt3'/);
  assert.match(withCalls(html), /onclick="removeHistoryItem\('tt3'\)"/);
  assert.match(html, /aria-label="Voir la fiche, prochain épisode le 16 novembre : Bad &quot;&lt;b&gt;&quot; &amp; &#39;quote&#39;"/);
  assert.doesNotMatch(html, /<b>/);
  assert.deepEqual(requested, [], 'cached metas are used directly');
});

test('the home row shows four titles and asks for missing series metas', () => {
  const history = [1, 2, 3, 4, 5, 6].map(n => ({ id: `tt${n}`, name: `Série ${n}`, type: 'series', season: 1, episode: n, progress_pct: 30 }));
  const home = renderWith({ activeTab: 'series', history });
  assert.equal((home.html.match(/<article class="resume-card/g) || []).length, 4);
  assert.equal(home.elements.homeResumeAll.style.display, 'inline-flex');
  assert.equal(home.elements.homeResumeGrid.classList['resume-row'], true);
  assert.equal(home.requested.length, 4);
  const library = renderWith({ activeTab: 'history', history });
  assert.equal((library.html.match(/<article class="resume-card/g) || []).length, 6);
  assert.equal(library.elements.homeResumeGrid.classList['resume-row'], false);
  assert.match(library.html, /loading="lazy"/);
});

test('the cards are 16:9 stills in one scrolling row on the home page', () => {
  assert.match(css, /\.resume-card \{[^}]*aspect-ratio: 16 \/ 9;/);
  assert.match(css, /\.resume-grid\.resume-row \{[^}]*grid-auto-flow: column;[^}]*grid-auto-columns: calc\(\(100% - 3 \* 14px\) \/ 4\);[^}]*overflow-x: auto;/);
  assert.match(css, /@media \(max-width: 760px\) \{ \.resume-grid\.resume-row \{ grid-auto-columns: min\(72%, 300px\); \} \}/);
  assert.match(css, /\.resume-card:hover \.resume-play, \.resume-main:focus-visible \.resume-play \{ opacity: 1;/);
  assert.match(css, /\.resume-card:hover \.resume-dismiss-btn, \.resume-card:focus-within \.resume-dismiss-btn \{ opacity: 1; \}/);
  assert.match(css, /@media \(hover: none\) \{ \.resume-dismiss-btn \{ opacity: 1; \} \}/);
  assert.match(css, /\.resume-card\.is-upcoming \.resume-play \{ display: none; \}/);
  assert.match(css, /\.resume-card, \.hero-spotlight, \.resume-play, \.resume-still-img \{ transition: none; animation: none; \}/);
});
