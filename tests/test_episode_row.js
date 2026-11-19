// Series fiche: episodes as a row of stills, opened on the episode to watch,
// with air dates, French summaries, and spoiler protection kept.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const Spoilers = require('../web/js/spoilers.js');

const source = require('../tools/interface_source.js').appSource();
const css = fs.readFileSync('./web/css/design.css', 'utf8');
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const slice = source.slice(source.indexOf('function renderDetailEpisodes('), source.indexOf('function pickSeriesEpisodeSources('));
const targetSource = source.slice(source.indexOf('function getSeriesTargetEpisode(h)'), source.indexOf('const FRENCH_MONTHS'));

const videos = [
  { season: 2, episode: 1, name: 'Premier', overview: 'First summary', thumbnail: 'https://img/1.jpg', released: '2026-10-01T00:00:00.000Z' },
  { season: 2, episode: 2, name: 'Deuxième', overview: 'Second summary', thumbnail: 'https://img/2.jpg', released: '2026-10-08T00:00:00.000Z' },
  { season: 2, episode: 3, name: 'Troisième', overview: 'Third summary', thumbnail: 'https://img/3.jpg', released: '2026-10-15T00:00:00.000Z' },
  { season: 2, episode: 4, name: 'TBA', overview: '', thumbnail: 'https://img/4.jpg', released: '2099-12-01T00:00:00.000Z' },
  { season: 1, episode: 1, name: 'Ancien', released: '2025-01-01T00:00:00.000Z' },
];

function render({ history = [], spoilers = false, fr = null, season = 2, onApi = null } = {}) {
  const elements = {
    detailEpisodesList: { innerHTML: '', querySelector: () => null },
    detailSeasonSummary: { textContent: '' },
    detailSeasonAllWatchedBtn: { style: {} }, markSeasonWatchedBtn: { style: {} },
  };
  const requests = [];
  const context = {
    document: { getElementById: id => elements[id] || null, querySelectorAll: () => [] },
    seriesMetaVideos: videos, currentMedia: { id: 'tt100', type: 'series', name: 'Série' },
    userHistory: history, activeTab: 'series', Spoilers, escapeHtml, encodeURIComponent,
    spoilerSeriesId: () => 'tt100',
    spoilerConcealed: (id, s, e) => spoilers && !Spoilers.watched(context.userHistory, id, s, e),
    api: url => { requests.push(url); return onApi ? onApi(url) : new Promise(() => {}); },
  };
  vm.runInNewContext(`${targetSource}\n${slice}\n${fr ? `EPISODE_OVERVIEWS_FR.set('tt100:2', ${JSON.stringify(fr)});` : ''}\nrenderDetailEpisodes(${season});\nthis.api_ = { episodeAirLabel, episodeToWatch, episodeName, EPISODE_OVERVIEWS_FR };`, context);
  return { html: elements.detailEpisodesList.innerHTML, summary: elements.detailSeasonSummary.textContent, requests, api: context.api_ };
}

const tile = (html, ep) => html.split('<article').find(part => part.includes(`data-ep="${ep}"`)) || '';

test('each episode is a still with its number, title, air date and summary', () => {
  const { html, summary } = render();
  assert.equal((html.match(/<article class="ep-tile/g) || []).length, 4);
  assert.match(tile(html, 1), /<img class="ep-tile-img" src="https:\/\/img\/1\.jpg"/);
  assert.match(tile(html, 1), /<span class="ep-tile-num">1\.<\/span> Premier/);
  assert.match(tile(html, 1), /<div class="ep-tile-meta">1er oct\. 2026<\/div>/);
  assert.match(tile(html, 1), /<p class="ep-tile-desc">First summary<\/p>/);
  assert.match(withCalls(tile(html, 1)), /onclick="oneClickSeriesEpisode\(this, 2, 1\)" aria-label="Lancer S02E01 · Premier"/);
  assert.match(withCalls(tile(html, 1)), /onclick="pickSeriesEpisodeSources\(2, 1\)">Sources/);
  assert.match(withCalls(tile(html, 1)), /aria-pressed="false"[^>]*onclick="toggleWatchedSeriesEp\(2, 1\)">✓ Marquer vu/);
  assert.equal(summary, 'Saison 2 · 4 épisodes · 0 vu · 1 à venir');
});

test('an episode not aired yet shows its date and cannot be launched', () => {
  const { html } = render();
  const upcoming = tile(html, 4);
  assert.match(upcoming, /class="ep-tile is-upcoming"/);
  assert.match(upcoming, /<div class="ep-tile-art">/, 'not a button');
  assert.doesNotMatch(upcoming, /oneClickSeriesEpisode|pickSeriesEpisodeSources|toggleWatchedSeriesEp|ep-tile-play|img\/4\.jpg/);
  assert.match(upcoming, /<span class="ep-tile-badge">Inédit<\/span>/);
  assert.match(upcoming, /Diffusion le 1er déc\. 2099/);
  assert.match(upcoming, /4\.<\/span> Épisode 4/, '« TBA » becomes a neutral title');
});

test('with no history, the first aired episode is « À suivre »', () => {
  const { html } = render();
  assert.match(tile(html, 1), /class="ep-tile is-next"[^>]*data-focus="1"/);
  assert.match(tile(html, 1), /<span class="ep-tile-badge">À suivre<\/span>/);
});

test('an episode in progress is « En cours » with its progress, and « Reprendre »', () => {
  const history = [{ id: 'tt100', type: 'series', season: 2, episode: 2, progress_pct: 40, ep_positions: { S02E02: { pct: 40 } }, watched_episodes: ['s2e1'] }];
  const { html, summary } = render({ history });
  assert.match(tile(html, 2), /class="ep-tile is-next"/);
  assert.match(tile(html, 2), /<span class="ep-tile-badge">En cours<\/span>/);
  assert.match(tile(html, 2), /8 oct\. 2026 · 40 % vu/);
  assert.match(tile(html, 2), /aria-label="Reprendre S02E02 · Deuxième"/);
  assert.match(tile(html, 2), /<span class="ep-tile-progress"><span style="width:40%"><\/span><\/span>/);
  assert.match(tile(html, 1), /class="ep-tile is-watched"/);
  assert.match(tile(html, 1), /<span class="ep-tile-badge">✓ Vu<\/span>/);
  assert.match(tile(html, 1), /aria-pressed="true"[^>]*>✓ Vu<\/button>/);
  assert.equal(summary, 'Saison 2 · 4 épisodes · 1 vu · 1 à venir');
});

test('after the last aired episode, the row opens on the next one without « À suivre »', () => {
  const history = [{ id: 'tt100', type: 'series', season: 2, episode: 3, completed: true, next_season: 2, next_episode: 4, watched_episodes: ['s2e1', 's2e2', 's2e3'] }];
  const { html } = render({ history });
  assert.doesNotMatch(html, /is-next/);
  assert.match(tile(html, 4), /data-focus="1"/);
});

test('another season opens on its first unwatched episode', () => {
  const history = [{ id: 'tt100', type: 'series', season: 1, episode: 1, progress_pct: 30 }];
  const { html } = render({ history });
  assert.match(tile(html, 1), /is-next/);
});

test('French summaries replace the English ones, and are asked once per season', () => {
  const { html } = render({ fr: { 1: 'Premier résumé', 2: 'Deuxième résumé' } });
  assert.match(tile(html, 1), /Premier résumé/);
  assert.match(tile(html, 3), /Third summary/, 'kept when no translation');
  const fresh = render();
  assert.deepEqual(fresh.requests, ['/api/episodes-fr?imdb_id=tt100&season=2']);
  assert.equal(render({ fr: {} }).requests.length, 0, 'already asked');
});

test('spoiler protection hides title, summary, still and labels of unwatched episodes', () => {
  const history = [{ id: 'tt100', watched_episodes: ['s2e1'] }];
  const { html } = render({ history, spoilers: true, fr: { 2: 'Résumé secret' } });
  const hidden = tile(html, 2);
  assert.match(hidden, /data-hidden="1"/);
  assert.doesNotMatch(hidden, /Deuxième|Second summary|Résumé secret|img\/2\.jpg/);
  assert.match(hidden, /Image masquée pour l'épisode 2/);
  assert.match(hidden, /aria-label="Lancer S02E02 · Épisode 2"/);
  assert.match(hidden, /Afficher pour cet épisode/);
  assert.match(tile(html, 1), /Premier/, 'watched episodes stay visible');
});

test('dates read naturally in French', () => {
  const { api } = render();
  assert.equal(api.episodeAirLabel('2026-12-01'), '1er déc. 2026');
  assert.equal(api.episodeAirLabel('2027-01-15'), '15 janv. 2027');
  assert.equal(api.episodeAirLabel(''), '');
  assert.equal(api.episodeName({ name: 'Episode 5' }, 5), 'Épisode 5');
  assert.equal(api.episodeName({ name: 'Le vrai titre' }, 5), 'Le vrai titre');
});

test('the section, the row arrows and the French route are wired', () => {
  assert.match(withCalls(source), /<div id="detailEpisodesList" class="ep-row" role="list" aria-label="Épisodes" \$\{actOn\('scroll', 'updateEpisodeArrows'\)\}><\/div>/);
  assert.match(source, /aria-label="Épisodes précédents" \$\{act\('scrollEpisodes', -1\)\} hidden>‹/);
  assert.match(source, /<p id="detailSeasonSummary" class="ep-summary" aria-live="polite"><\/p>/);
  assert.match(source, /window\.addEventListener\('resize', updateEpisodeArrows\);/);
  assert.match(source, /behavior: 'instant' \}\);/, 'the row opens on the episode at once');
  assert.match(css, /\.ep-row \{[^}]*grid-auto-flow: column;[^}]*overflow-x: auto;/);
  assert.match(css, /\.ep-tile\.is-next \{ border-color: #fafafa; \}/);
  assert.match(css, /\.ep-tile-desc \{[^}]*-webkit-line-clamp: 3;/);
});
