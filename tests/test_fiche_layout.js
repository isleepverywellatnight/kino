// the fiche. A hero glowing with its own image,
// one season and episode chooser (the row of the fiche), the recommended source
// first and the others folded under « Choisir une autre source ».
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
const sourcesCode = slice('// --- Sources: the best one first', 'async function copyTorrentMagnet(');
const escapeHtml = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

function torrent(title, resolution) {
  return { title, qualities: [resolution], langs: ['MULTI'], source: 'Comet', size_str: '4 GB', seeders: 3,
    magnet: `magnet:?xt=urn:btih:${title.length}`, parsed_details: { resolution }, recommendation: { reasons: [], warnings: [] } };
}

function loadSources(torrents, best) {
  const elements = {};
  const element = id => elements[id] || (elements[id] = { id, innerHTML: '', textContent: '', value: '' });
  const context = {
    document: { getElementById: element }, allTorrents: torrents, currentMedia: { type: 'movie' },
    getFilteredTorrents: () => torrents, scoreTorrent: t => (torrents.indexOf(t) === best ? 500 : 50),
    spoilerConcealed: () => false, spoilerSeriesId: () => 'tt1', Spoilers: { neutralTitle: () => '' }, escapeHtml,
  };
  vm.runInNewContext(`${sourcesCode}\nthis.api_ = { renderTorrents, open: value => { sourcesMoreOpen = value; } };`, context);
  return { ...context.api_, list: element('torrentsList') };
}

test('the other sources wait, folded, under the recommended one', () => {
  const torrents = [torrent('Best.1080p', '1080p'), torrent('Other.2160p', '4K'), torrent('Other.720p', '720p')];
  const view = loadSources(torrents, 0);
  view.renderTorrents();
  const out = view.list.innerHTML;
  assert.match(withCalls(out), /^<div class="source-best">[\s\S]*?debridFromIndex\(0\)[\s\S]*?<\/div><details class="sources-more" ontoggle="rememberSourcesMore\(this\)"><summary>Choisir une autre source <span class="list-count">2<\/span><\/summary>/);
  assert.match(out, /<details class="sources-more"[\s\S]*aria-label="4K"[\s\S]*aria-label="720p"[\s\S]*<\/details>$/);
  view.open(true);
  view.renderTorrents();
  assert.match(withCalls(view.list.innerHTML), /<details class="sources-more" open ontoggle=/, 'a filter or a sort keeps it open');
  assert.match(out, /title="Copier le lien Magnet" aria-label="Copier le lien Magnet"><svg class="ui-icon ui-icon-sm" aria-hidden="true"><use href="\/icons\.svg#link"><\/use><\/svg><\/button>/);
  assert.match(source, /lastReadyDebrid = null;\n  sourcesMoreOpen = false;/, 'a new title or episode starts folded');
});

test('without a recommended source, every group shows', () => {
  const view = loadSources([torrent('A.1080p', '1080p'), torrent('B.2160p', '4K')], -1);
  view.renderTorrents();
  assert.doesNotMatch(view.list.innerHTML, /sources-more/);
  assert.match(css, /#torrentsPanel:has\(\.sources-more:not\(\[open\]\)\) #sourcesFilters \{ display: none !important; \}/);
  assert.match(html, /<div class="filters" id="sourcesFilters"/);
});

test('one chooser for the episode: the row of the fiche', () => {
  assert.match(source, /sc\.style\.display = seasons\.length \? 'none' : 'flex';/);
  assert.doesNotMatch(source, /Saisons :<\/span>/);
  assert.match(source, /<div class="detail-season-pills" role="group" aria-label="Saisons">/);
});

test('the fiche glows with its own image once it has loaded', () => {
  const code = slice('function ficheGlow(img) {', '// No backdrop for this title');
  const props = {};
  const img = { currentSrc: 'https://img/b"g.jpg' };
  const panel = { style: { setProperty: (k, v) => { props[k] = v; } }, contains: el => el === img };
  vm.runInNewContext(`${code}\nficheGlow(img); ficheGlow({ currentSrc: 'https://other' });`, { document: { getElementById: () => panel }, img, encodeURIComponent });
  assert.equal(props['--hero-glow'], 'url("https://img/b%22g.jpg")', 'an image of another fiche is ignored');
  assert.match(withCalls(source), /\$\{actOn\('load', 'ficheGlow', THIS\)\}/);
  assert.match(source, /detail\.style\.display = 'block';\n  detail\.style\.removeProperty\('--hero-glow'\);/);
  assert.match(css, /#detailPanel::before \{[\s\S]*?background: var\(--hero-glow, none\) center \/ cover no-repeat;[\s\S]*?filter: blur\(70px\)/);
});

test('« Vous aimerez aussi » takes TMDB\'s recommendations once known', async () => {
  const code = slice('function similarSectionHtml(list, isSeries) {', '// No backdrop for this title');
  const section = { outerHTML: '<old>' };
  const more = { hidden: true, querySelector: sel => (sel === '.detail-similar' ? section : null), insertAdjacentHTML() {} };
  const requests = [];
  const items = ['tt1', 'tt2', 'tt3', 'tt4', 'tt5'].map(id => ({ id, name: id, type: 'movie' }));
  const context = {
    RELATED_CLIENT_CACHE: new Map(), currentMedia: { id: 'tt0137523' },
    getWatchedHistory: () => [{ id: 'tt2' }],
    posterCardHtml: m => `<card ${m.id}>`,
    document: { getElementById: () => more },
    api: async url => { requests.push(url); return { source: 'tmdb', items }; },
  };
  vm.runInNewContext(`${code}\nthis.load = loadRelatedForFiche;`, context);
  await context.load({ id: 'tt0137523' }, false);
  assert.deepEqual(requests, ['/api/related?type=movie&imdb_id=tt0137523']);
  assert.match(section.outerHTML, /<card tt1><card tt3><card tt4><card tt5>/, 'a title already seen is left out');
  assert.equal(more.hidden, false);
  await context.load({ id: 'tt0137523' }, false);
  assert.equal(requests.length, 1, 'asked once per title');
  assert.match(source, /refreshSpoilerSurfaces\(\);\n  loadRelatedForFiche\(media, rendered\.isSeries\);/);
});

test('Paramètres → Connexions has the TMDB key, checked before it is kept', () => {
  assert.match(withCalls(html), /<div class="set-block" id="settings-tmdb" aria-labelledby="settings-tmdb-title">[\s\S]*?<input type="password" id="cfgTmdbKey"[\s\S]*?onclick="verifyTmdbKey\(this\)">Vérifier<\/button>/);
  assert.match(source, /await api\('\/api\/tmdb\/key', \{ method: 'POST'/);
  assert.match(source, /RELATED_CLIENT_CACHE\.clear\(\);/);
});
