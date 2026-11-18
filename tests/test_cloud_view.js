// The Cloud tab: one row per work (poster, title, episodes, size), its files
// below; a file plays under its own title, removals are confirmed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const cloudCode = slice('async function loadRdCloud() {', 'async function onChangeRdRetention(daysStr) {');
const sizeCode = slice('function offlineSize(bytes) {', 'function offlineStatus(item) {');
const purgeCode = slice('async function purgeRdCloud(mode, btn) {', '\n}\n') + '\n}';
const escapeHtml = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const GB = 1024 ** 3;

const file = (id, filename, extra = {}) => ({ id, ids: [id], filename, filesize: '2.00 GB', bytes: 2 * GB,
  download: `https://cloud.example/${id}`, generated: '2026-11-08', season: null, episode: null, quality: '1080p', hdr: false, ...extra });
const groups = () => [
  { key: 'series:the bear', kind: 'series', title: 'The Bear', year: '', count: 3, bytes: 6 * GB, seasons: [1, 3], latest: '2026-11-08', files: [
    file('b1', 'The.Bear.S01E01.mkv', { season: 1, episode: 1 }),
    file('b2', 'The.Bear.S03E01.mkv', { season: 3, episode: 1 }),
    file('b3', 'The.Bear.S03E02.mkv', { season: 3, episode: 2 }),
  ] },
  { key: 'movie:the count of monte cristo:2024', kind: 'movie', title: 'Le Comte de Monte-Cristo', year: '2024', count: 1, bytes: 30 * GB, seasons: [], latest: '2025-12-24', files: [
    file('m1', 'Le.Comte.de.Monte-Cristo.2024.2160p.HDR.mkv', { bytes: 30 * GB, quality: '2160p', hdr: true, generated: '2025-12-24' }),
  ] },
  { key: 'other', kind: 'other', title: '', year: '', count: 1, bytes: 0, seasons: [], latest: '', files: [
    file('o1', 'notes <b>.txt', { bytes: 0, quality: '', download: 'javascript:alert(1)', generated: '' }),
  ] },
];

function load({ history = [], matches = {}, confirmAnswer = true } = {}) {
  const elements = {};
  const element = id => elements[id] || (elements[id] = { id, innerHTML: '', textContent: '', hidden: false, value: '' });
  const requests = [];
  const calls = [];
  const context = {
    document: { getElementById: element },
    window: { activeDebridName: 'Real-Debrid', confirm: message => { calls.push(['confirm', message]); return confirmAnswer; } },
    activeTab: 'rdcloud', userHistory: history, Date,
    escapeHtml, attrJson: value => escapeHtml(JSON.stringify(value)),
    displayTitle: m => m.name_fr || m.name,
    titleAttrs: m => ` data-title-id="${m.id}"`,
    foldSearchText: value => String(value || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(),
    listItemState: m => (history.some(h => h.id === m.id && h.completed) ? 'watched' : 'todo'),
    Spoilers: { watched: (h, id, season, episode) => h.some(x => x.id === id && (x.watched_episodes || []).includes(`s${season}e${episode}`)) },
    rememberFrenchTitles: items => calls.push(['french', items.map(m => m.id)]),
    selectMedia: m => calls.push(['fiche', JSON.parse(JSON.stringify(m))]),
    openMpv: (btn, url, title, options) => calls.push(['play', url, title, JSON.parse(JSON.stringify(options))]),
    alert: message => calls.push(['alert', message]),
    api: async (route, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push([route, body]);
      if (route === '/api/rd-history') return { items: [], groups: groups(), cleaned: { deleted_downloads: 0, deleted_torrents: 0 } };
      if (route === '/api/cloud-titles') return { matches: Object.fromEntries(body.titles.map(t => [t.key, matches[t.key] || null])) };
      return { ok: true };
    },
  };
  vm.runInNewContext(`${sizeCode}\n${cloudCode}\n${purgeCode}\nthis.api_ = { loadRdCloud, setCloudFilter, onCloudSearch, toggleCloudGroup, playCloudFile, deleteCloudFile, deleteCloudGroup, openCloudFiche, purgeRdCloud, cloudDate };`, context);
  return { ...context.api_, elements: new Proxy({}, { get: (_, id) => element(id) }), requests, calls, context };
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const BEAR = { id: 'tt14452776', name: 'The Bear', year: '2022', poster: 'https://img.example/bear.jpg' };
const COUNT = { id: 'tt26446278', name: 'The Count of Monte-Cristo', name_fr: 'Le Comte de Monte-Cristo', year: '2024', poster: 'https://img.example/mc.jpg' };
const MATCHES = { 'series:the bear': BEAR, 'movie:the count of monte cristo:2024': COUNT };

test('one row per work, with its episodes, size and date', async () => {
  const cloud = load();
  await cloud.loadRdCloud();
  const rows = cloud.elements.rdCloudList.innerHTML.split('<article').slice(1);
  assert.equal(rows.length, 3);
  assert.match(rows[0], /<div class="cloud-group-meta">Série · 3 épisodes · saisons 1 et 3 · 6,0 Go · ajouté le 8 nov\.<\/div>/);
  assert.match(rows[0], />Lancer S01E01<\/button>/);
  assert.match(rows[0], /aria-expanded="false">3 épisodes<span class="cloud-chevron"/);
  assert.match(rows[1], /Film · 2160p · HDR · 30 Go · ajouté le 24 déc\. 2025/);
  assert.match(rows[2], /Autres fichiers/);
  assert.doesNotMatch(rows[2], />Lancer</, 'nothing to play in other files');
  assert.match(cloud.elements.rdCloudTools.innerHTML, />Tout <span class="list-count">3<\/span>/);
  assert.match(cloud.elements.rdCloudTools.innerHTML, />Séries <span class="list-count">1<\/span>/);
  assert.match(cloud.elements.rdCloudTools.innerHTML, /3 titres · 5 fichiers · 36 Go/);
  assert.equal(cloud.elements.rdCloudTools.hidden, false);
});

test('posters and catalogue titles come after the list, once', async () => {
  const cloud = load({ matches: MATCHES });
  await cloud.loadRdCloud();
  await settle();
  const asked = cloud.requests.filter(([route]) => route === '/api/cloud-titles');
  assert.equal(asked.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(asked[0][1].titles.map(t => t.key))), ['series:the bear', 'movie:the count of monte cristo:2024']);
  const out = cloud.elements.rdCloudList.innerHTML;
  assert.match(withCalls(out), /<button type="button" class="cloud-poster" onclick="openCloudFiche\(&quot;series:the bear&quot;\)" aria-label="Ouvrir la fiche de The Bear">/);
  assert.match(out, /<img src="https:\/\/img\.example\/bear\.jpg" alt="" loading="lazy"/);
  assert.match(out, /<span data-title-id="tt26446278">Le Comte de Monte-Cristo<\/span> <span class="cloud-group-year">2024<\/span>/);
  cloud.openCloudFiche('series:the bear');
  assert.deepEqual(cloud.calls.find(call => call[0] === 'fiche')[1], { ...BEAR, type: 'series' });
  await cloud.loadRdCloud();
  await settle();
  assert.equal(cloud.requests.filter(([route]) => route === '/api/cloud-titles').length, 1, 'found titles are not asked again');
});

test('a series starts at its first episode not seen, and plays under its title', async () => {
  const cloud = load({ matches: MATCHES, history: [{ id: BEAR.id, watched_episodes: ['s1e1'] }] });
  await cloud.loadRdCloud();
  await settle();
  const row = cloud.elements.rdCloudList.innerHTML.split('<article')[1];
  assert.match(row, /Série · 3 épisodes · 1 vu · saisons 1 et 3/);
  assert.match(withCalls(row), /onclick="playCloudFile\(&quot;series:the bear&quot;, 1, this\)">Lancer S03E01<\/button>/);
  cloud.playCloudFile('series:the bear', 1, null);
  const [, url, title, options] = cloud.calls.find(call => call[0] === 'play');
  assert.equal(url, 'https://cloud.example/b2');
  assert.equal(title, 'S03E01 · The Bear');
  assert.deepEqual(options, { media: { ...BEAR, type: 'series' }, season: 3, episode: 1, files: [
    { filename: 'S03E01 · The Bear', download: 'https://cloud.example/b2' },
    { filename: 'S03E02 · The Bear', download: 'https://cloud.example/b3' },
  ] });
});

test('a work the catalogue does not know plays without a title', async () => {
  const cloud = load();
  await cloud.loadRdCloud();
  cloud.playCloudFile('movie:the count of monte cristo:2024', 0, null);
  const [, , title, options] = cloud.calls.find(call => call[0] === 'play');
  assert.equal(title, 'Le.Comte.de.Monte-Cristo.2024.2160p.HDR.mkv');
  assert.deepEqual(options, { media: null, season: null, episode: null, files: null });
});

test('the files open below their work, escaped, with a link only to the web', async () => {
  const cloud = load();
  await cloud.loadRdCloud();
  cloud.toggleCloudGroup('other');
  cloud.toggleCloudGroup('series:the bear');
  const out = cloud.elements.rdCloudList.innerHTML;
  assert.match(out, /aria-expanded="true">3 épisodes/);
  assert.match(out, /<div class="cloud-file-label">S03E02<\/div>/);
  assert.match(out, /title="The\.Bear\.S03E02\.mkv">The\.Bear\.S03E02\.mkv · 2,0 Go · ajouté le 8 nov\.<\/div>/, 'sizes in French');
  assert.match(out, /Retirer les 3 fichiers du Cloud/);
  assert.match(out, /notes &lt;b&gt;\.txt/);
  assert.doesNotMatch(out, /<b>|javascript:/);
  const other = out.split('<article').at(-1);
  assert.doesNotMatch(other, />Lancer<|>Télécharger<|Lien direct/, 'a file without a web address can only be removed');
  assert.match(other, />Retirer<\/button>/);
  assert.match(out, /href="https:\/\/cloud\.example\/b1" target="_blank" rel="noopener">Lien direct<\/a>/);
  cloud.toggleCloudGroup('other');
  assert.doesNotMatch(cloud.elements.rdCloudList.innerHTML, /notes &lt;b&gt;/);
});

test('filters and search', async () => {
  const cloud = load({ matches: MATCHES });
  await cloud.loadRdCloud();
  await settle();
  cloud.setCloudFilter('movie');
  assert.equal(cloud.elements.rdCloudList.innerHTML.split('<article').length - 1, 1);
  assert.match(withCalls(cloud.elements.rdCloudTools.innerHTML), /class="chip active" aria-pressed="true" onclick="setCloudFilter\('movie'\)"/);
  cloud.setCloudFilter('all');
  cloud.onCloudSearch('comte');
  assert.match(cloud.elements.rdCloudList.innerHTML, /Le Comte de Monte-Cristo/);
  assert.equal(cloud.elements.rdCloudList.innerHTML.split('<article').length - 1, 1, 'French title search, without accents');
  cloud.onCloudSearch('zzz');
  assert.match(cloud.elements.rdCloudList.innerHTML, /Aucun titre ne correspond\./);
});

test('removals are confirmed first', async () => {
  const refused = load({ confirmAnswer: false });
  await refused.loadRdCloud();
  await refused.deleteCloudFile('series:the bear', 0, null);
  await refused.deleteCloudGroup('series:the bear', null);
  await refused.purgeRdCloud('all', null);
  const asked = refused.calls.filter(call => call[0] === 'confirm').map(call => call[1]);
  assert.equal(asked.length, 3);
  assert.equal(refused.requests.filter(([route]) => route !== '/api/rd-history' && route !== '/api/cloud-titles').length, 0);
  assert.match(asked[0], /^Retirer « The\.Bear\.S01E01\.mkv » du Cloud Real-Debrid \?$/);
  assert.match(asked[1], /^Retirer les 3 fichiers de « The Bear » du Cloud Real-Debrid \?$/);
  assert.match(asked[2], /^Vider tout le Cloud Real-Debrid \? .*sans retour possible\.$/);

  const accepted = load();
  await accepted.loadRdCloud();
  await accepted.deleteCloudGroup('series:the bear', null);
  assert.deepEqual(JSON.parse(JSON.stringify(accepted.requests.find(([route]) => route === '/api/rd-delete')[1])), { ids: ['b1', 'b2', 'b3'] });
});

test('an empty Cloud says so', async () => {
  const cloud = load();
  cloud.context.api = async () => ({ items: [], groups: [] });
  await cloud.loadRdCloud();
  assert.match(cloud.elements.rdCloudList.innerHTML, /Aucun fichier récent sur votre compte Real-Debrid\./);
  assert.equal(cloud.elements.rdCloudTools.hidden, true);
});

test('dates in French, the year only when it is not this one', () => {
  const cloud = load();
  const year = new Date().getFullYear();
  assert.equal(cloud.cloudDate(`${year}-03-01`), '1 mars');
  assert.equal(cloud.cloudDate('2019-08-15T10:00'), '15 août 2019');
  assert.equal(cloud.cloudDate('soon'), '');
});

test('a Cloud file never borrows the last opened fiche', async () => {
  const launch = slice('// From a fiche, the title and episode come from it;', '(function initMacNativeWindow() {');
  const opened = [];
  const context = {
    currentMedia: { id: 'tt0000001', name: 'Autre fiche', type: 'movie' }, _isGlobalPlayLaunching: false,
    navigator: { platform: 'MacIntel', userAgent: '' }, window: { kinoPlayerMode: 'integrated' }, setTimeout: () => 0,
    userHistory: [{ id: BEAR.id, ep_positions: { s3e1: { position: 312 } } }, { id: 'tt0000001', position: 999 }],
    document: { getElementById: () => null }, refreshUserLists() {}, openInAppPlayer() {},
    openNativeStream: async options => { opened.push(JSON.parse(JSON.stringify(options))); return true; },
  };
  vm.runInNewContext(`${launch}\nthis.openMpv = openMpv;`, context);
  await context.openMpv(null, 'https://cloud.example/b2', 'S03E01 · The Bear', { media: { ...BEAR, type: 'series' }, season: 3, episode: 1,
    files: [{ filename: 'S03E01 · The Bear', download: 'https://cloud.example/b2' }, { filename: 'S03E02 · The Bear', download: 'https://cloud.example/b3' }] });
  assert.deepEqual(opened[0].media, { ...BEAR, type: 'series', season: 3, episode: 1 });
  assert.equal(opened[0].resumeSec, 312);
  assert.deepEqual(opened[0].playlist.map(entry => entry.title), ['S03E01 · The Bear', 'S03E02 · The Bear']);
  context._isGlobalPlayLaunching = false;
  await context.openMpv(null, 'https://cloud.example/x', 'x.mkv', { media: null });
  assert.equal(opened[1].media, null);
  assert.equal(opened[1].resumeSec, 0);
});

test('the page has its tools row and a monochrome look', () => {
  assert.match(html, /<div id="rdCloudTools" class="cloud-tools" hidden><\/div>\n\s*<div id="rdCloudList" class="torrent-list cloud-list"><\/div>/);
  assert.match(css, /\.cloud-tools\[hidden\] \{ display: none; \}/);
  const cloudCss = css.slice(css.indexOf('/* Cloud: one row per work'), css.indexOf('/* Mes statistiques'));
  assert.ok(cloudCss.length > 2000, 'the Cloud rules are found');
  assert.doesNotMatch(cloudCode + cloudCss, /#[0-9a-f]{3,6}\b|rgb\(/i, 'tokens only');
});
