// « Nouveaux épisodes » on the home page: cards, labels, spoilers, and no
// series shown twice with « À continuer ».
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const { withCalls } = require('../tools/interface_source.js');

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const resumeCode = 'const FRENCH_MONTHS' + source.split('const FRENCH_MONTHS', 2)[1].split('async function removeHistoryItem', 1)[0];
const targetCode = 'function getSeriesTargetEpisode(h)' + source.split('function getSeriesTargetEpisode(h)', 2)[1].split('const FRENCH_MONTHS', 1)[0];
const monthsCode = source.slice(source.indexOf('const EPISODE_MONTHS'), source.indexOf('function episodeAirDate('))
  + source.slice(source.indexOf('// « 12 déc. 2024 ».'), source.indexOf('// Where the row opens'));
// « À continuer » and the new episodes go by tab: the row below is the Séries tab's.
const kindCode = 'function listItemKind(m)' + source.split('function listItemKind(m)', 2)[1].split('\n}\n', 1)[0] + '\n}\n';
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

const NEW = [
  { id: 'tt2', name: 'Dexter: Resurrection', type: 'series', year: '2025', poster: 'https://p/2.jpg', season: 2, episode: 2,
    episode_name: 'Fallen', still: 'https://img/2.jpg', released: '2026-11-09', in_progress: true, is_new: true, new_count: 1, newest: '2026-11-09' },
  { id: 'tt1', name: 'Black <Doves>', type: 'series', year: '2024', poster: '', season: 2, episode: 1,
    episode_name: 'TBA', still: '', released: '2026-11-05', in_progress: false, is_new: true, new_count: 5, newest: '2026-11-05' },
  { id: 'tt3', name: 'Backlog', type: 'series', season: 1, episode: 2, episode_name: 'Old', still: 'https://img/3.jpg',
    released: '2026-10-01', in_progress: false, is_new: false, new_count: 2, newest: '2026-11-08' },
];

function load({ tab = 'series', concealed = () => false, history = [], fresh = NEW } = {}) {
  const elements = Object.fromEntries(['homeNewSection', 'homeNewGrid', 'homeResumeSection', 'homeResumeGrid', 'resumeSectionTitle', 'homeResumeAll', 'homeResumeClear']
    .map(id => [id, { style: {}, innerHTML: '', textContent: '', classList: { toggle() {} } }]));
  const requests = [];
  const context = {
    activeTab: tab, networkOnline: true, document: { getElementById: id => elements[id] || null },
    META_CLIENT_CACHE: new Map(), escapeHtml, encodeURIComponent, Date, Promise,
    attrJson: value => escapeHtml(JSON.stringify(value)), titleAttrs: () => '', displayTitle: m => m.name,
    spoilerConcealed: concealed, getInProgressHistory: () => history, formatRemaining: () => 'Il reste 5 min',
    api: url => { requests.push(url); return Promise.resolve({ episodes: fresh, today: '2026-11-10' }); },
  };
  vm.runInNewContext(`${targetCode}\n${monthsCode}\n${kindCode}\n${resumeCode}\nthis.api_ = { releasedLabel, renderHomeResume, newEpisodeCardHtml, setNew: (list, today) => { newEpisodes = list; newEpisodesToday = today; newEpisodesAt = Date.now(); } };`, context);
  return { ...context.api_, elements, requests };
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const card = (out, id) => out.split('<article').find(part => part.includes(`imdb_id&quot;:&quot;${id}&quot;`)) || '';

test('release days read naturally', () => {
  const { releasedLabel } = load();
  assert.equal(releasedLabel('2026-11-10', '2026-11-10'), 'Sorti aujourd’hui');
  assert.equal(releasedLabel('2026-11-09', '2026-11-10'), 'Sorti hier');
  assert.equal(releasedLabel('2026-11-05', '2026-11-10'), 'Sorti il y a 5 jours');
  assert.equal(releasedLabel('2026-10-01', '2026-11-10'), 'Sorti le 1er oct. 2026');
});

test('the row is asked once, then shown right after « À continuer »', async () => {
  const lib = load();
  lib.renderHomeResume();
  assert.deepEqual(lib.requests, ['/api/new-episodes']);
  await flush();
  await flush();
  assert.equal(lib.elements.homeNewSection.style.display, 'block');
  assert.equal((lib.elements.homeNewGrid.innerHTML.match(/<article class="resume-card is-new"/g) || []).length, 3);
  lib.renderHomeResume();
  assert.deepEqual(lib.requests, ['/api/new-episodes'], 'not asked again within 10 minutes');
  assert.ok(html.indexOf('id="homeResumeSection"') < html.indexOf('id="homeNewSection"'));
  assert.ok(html.indexOf('id="homeNewSection"') < html.indexOf('id="heroSpotlight"'), 'both before « À la une »');
});

test('each card says which episode, when it came out, and how many are new', () => {
  const lib = load();
  lib.setNew(NEW, '2026-11-10');
  lib.renderHomeResume();
  const out = lib.elements.homeNewGrid.innerHTML;
  const dexter = card(out, 'tt2');
  assert.match(dexter, /<span class="resume-new-badge">Nouveau<\/span>/);
  assert.match(dexter, /<span class="resume-kicker">S02E02 · Fallen<\/span>/);
  assert.match(dexter, /<span class="resume-subinfo">Reprendre · sorti hier<\/span>/);
  assert.match(dexter, /aria-label="Reprendre S02E02 : Dexter: Resurrection"/);
  assert.match(dexter, /src="https:\/\/img\/2\.jpg" data-fallback="https:\/\/images\.metahub\.space\/background\/medium\/tt2\/img"/);
  assert.match(withCalls(dexter), /openResumeCard\(this, \{ imdb_id: 'tt2', type: 'series', season: 2, episode: 2/);
  const doves = card(out, 'tt1');
  assert.match(doves, /<span class="resume-kicker">S02E01<\/span>/, '« TBA » left out');
  assert.match(doves, /Sorti il y a 5 jours · \+4 autres/);
  assert.match(doves, /Black &lt;Doves&gt;/);
  assert.doesNotMatch(doves, /Black <Doves>/);
  assert.match(doves, /src="https:\/\/images\.metahub\.space\/background\/medium\/tt1\/img"/, 'backdrop when no still');
  assert.match(card(out, 'tt3'), /2 nouveaux épisodes depuis votre dernier visionnage/);
  const single = lib.newEpisodeCardHtml({ ...NEW[2], new_count: 1 }, '2026-11-10');
  assert.match(single, /1 nouvel épisode depuis votre dernier visionnage/);
});

test('Ma Liste: a new series plays its first episode, a new season opens the fiche', () => {
  const lib = load();
  const premiere = lib.newEpisodeCardHtml({ id: 'tt20', name: 'Nocturne', season: 1, episode: 1, episode_name: 'Pilot', still: 'https://img/20.jpg',
    released: '2026-10-30', new_count: 3, is_new: true, in_progress: false, kind: 'premiere' }, '2026-11-10');
  assert.match(premiere, /<article class="resume-card is-new" role="listitem">/);
  assert.match(premiere, /<span class="resume-new-badge">Nouvelle série<\/span>/);
  assert.match(premiere, /<span class="resume-kicker">S01E01 · Pilot<\/span>/);
  assert.match(premiere, /<span class="resume-subinfo">Sorti le 30 oct\. 2026 · 3 épisodes<\/span>/);
  assert.match(premiere, /aria-label="Lancer S01E01 : Nocturne"/);
  assert.match(premiere, /resume-play/);
  const season = lib.newEpisodeCardHtml({ id: 'tt21', name: 'Old show', season: 3, episode: 1, episode_name: 'Retour', still: 'https://img/21.jpg',
    released: '2026-11-09', new_count: 1, is_new: true, in_progress: false, kind: 'season' }, '2026-11-10');
  assert.match(season, /<article class="resume-card is-new opens-fiche" role="listitem">/);
  assert.match(season, /<span class="resume-new-badge">Saison 3<\/span>/);
  assert.match(season, /<span class="resume-kicker">Saison 3 disponible<\/span>/);
  assert.match(season, /<span class="resume-subinfo">Sorti hier<\/span>/);
  assert.match(season, /aria-label="Voir la fiche : Old show, saison 3 disponible"/);
  assert.doesNotMatch(season, /Retour|img\/21\.jpg|resume-play/, 'no episode name, still or play mark');
  assert.match(source, /if \(card\?\.classList\.contains\('is-upcoming'\) \|\| card\?\.classList\.contains\('opens-fiche'\)\) selectMedia\(media\);/);
});

test('spoiler protection hides the new episode\'s name and still', () => {
  const lib = load({ concealed: (id, season, episode) => id === 'tt2' && season === 2 && episode === 2 });
  lib.setNew(NEW, '2026-11-10');
  lib.renderHomeResume();
  const dexter = card(lib.elements.homeNewGrid.innerHTML, 'tt2');
  assert.doesNotMatch(dexter, /Fallen|img\/2\.jpg/);
  assert.match(dexter, /<span class="resume-kicker">S02E02<\/span>/);
});

test('a series in the new row is not repeated in « À continuer », except on the history page', () => {
  const history = [
    { id: 'tt2', name: 'Dexter: Resurrection', type: 'series', season: 2, episode: 2, progress_pct: 40 },
    { id: 'tt9', name: 'Autre', type: 'series', season: 1, episode: 1, progress_pct: 40 },
  ];
  const home = load({ history });
  home.setNew(NEW, '2026-11-10');
  home.renderHomeResume();
  assert.doesNotMatch(home.elements.homeResumeGrid.innerHTML, /Dexter/);
  assert.match(home.elements.homeResumeGrid.innerHTML, /Autre/);
  const library = load({ tab: 'history', history });
  library.setNew(NEW, '2026-11-10');
  library.renderHomeResume();
  assert.match(library.elements.homeResumeGrid.innerHTML, /Dexter/);
  assert.equal(library.elements.homeNewSection.style.display, 'none', 'the row stays on the home page');
});

test('new episodes are series: the Films tab neither shows nor asks for them', async () => {
  const films = load({ tab: 'movies', history: [
    { id: 'tt7', name: 'Un film', type: 'movie', progress_pct: 40 },
    { id: 'tt9', name: 'Une série', type: 'series', season: 1, episode: 1, progress_pct: 40 }] });
  films.setNew(NEW, '2026-11-10');
  films.renderHomeResume();
  await flush();
  assert.deepEqual(films.requests, []);
  assert.equal(films.elements.homeNewSection.style.display, 'none');
  assert.equal(films.elements.homeNewGrid.innerHTML, '');
  assert.match(films.elements.homeResumeGrid.innerHTML, /Un film/);
  assert.doesNotMatch(films.elements.homeResumeGrid.innerHTML, /Une série/);
});

test('no new episode: no row', async () => {
  const lib = load({ fresh: [] });
  lib.renderHomeResume();
  await flush();
  await flush();
  assert.equal(lib.elements.homeNewSection.style.display, 'none');
  assert.match(css, /\.resume-new-badge \{[^}]*background: #fafafa;/);
});
