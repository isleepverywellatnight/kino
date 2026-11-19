// A clearer fiche: hero, episodes, then the sources (best one, then one group
// per quality, each file once); cast, similar titles and links below them.
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
const builder = slice('function buildDetailHtml(', '// No backdrop for this title');
const sourcesCode = slice('// --- Sources: the best one first', 'async function copyTorrentMagnet(');
const moreCode = slice('// Cast, similar titles and links: below the sources', 'function closeDetailPanel() {');
const { escapeHtml } = require('../tools/interface_source.js').templateHelpers();

function torrent(title, resolution, extra = {}) {
  return { title, qualities: [resolution], langs: ['MULTI'], source: 'Comet', size_gb: 4.2, size_str: '4.2 GB', seeders: 12,
    magnet: `magnet:?xt=urn:btih:${title.length}`, parsed_details: { resolution, source: 'WEB-DL', codec: 'HEVC', hdr_type: 'SDR', audio_formats: ['DDP'] },
    recommendation: { reasons: ['Langue souhaitée : MULTI'], warnings: [] }, score: 100, ...extra };
}

function loadSources(torrents, { best = 0, hidden = false, type = 'movie' } = {}) {
  const elements = {};
  const element = id => elements[id] || (elements[id] = { id, innerHTML: '', textContent: '', value: '' });
  const context = {
    document: { getElementById: element },
    allTorrents: torrents, currentMedia: { type },
    getFilteredTorrents: () => torrents,
    scoreTorrent: t => (torrents.indexOf(t) === best ? 500 : 50),
    spoilerConcealed: () => hidden, spoilerSeriesId: () => 'tt1',
    Spoilers: { neutralTitle: episode => `Épisode ${episode}` },
    escapeHtml,
  };
  vm.runInNewContext(`${sourcesCode}\nthis.api_ = { renderTorrents, toggleSourceGroup, sourceRows, sourceResolution, sourceDetails, expanded: expandedSourceGroups };`, context);
  return { ...context.api_, list: element('torrentsList'), sub: element('torrentsSub') };
}

test('the best source first, then one group per quality', () => {
  const torrents = [
    torrent('Film.2024.1080p.MULTi.WEB', '1080p'),
    ...Array.from({ length: 6 }, (_, i) => torrent(`Film.2024.2160p.v${i}`, '4K')),
    torrent('Film.2024.720p', '720p'),
    torrent('Film.2024.1080p.other', '1080p'),
    torrent('Film.2024.DVDRip', 'SD'),
  ];
  const view = loadSources(torrents);
  view.renderTorrents();
  const out = view.list.innerHTML;
  assert.match(out, /^<div class="source-best">\s*<div class="torrent-item is-recommended">/);
  assert.match(out, /Recommandé[\s\S]*Film\.2024\.1080p\.MULTi\.WEB[\s\S]*Pourquoi : Langue souhaitée : MULTI/);
  const groups = [...out.matchAll(/<h4 class="source-group-title">([^<]+) <span class="list-count">(\d+)<\/span><\/h4>/g)].map(m => `${m[1]} ${m[2]}`);
  assert.deepEqual(groups, ['4K 6', '1080p 1', '720p 1', 'Autres qualités 1']);
  const fourK = out.slice(out.indexOf('aria-label="4K"'), out.indexOf('aria-label="1080p"'));
  assert.equal((fourK.match(/class="torrent-item /g) || []).length, 4, 'four rows per group');
  assert.match(withCalls(fourK), /onclick="toggleSourceGroup\('4k'\)" aria-expanded="false">Voir 2 autres en 4K<\/button>/);
  assert.match(withCalls(out), /onclick="debridFromIndex\(0\)"><svg class="ui-icon ui-icon-xs" aria-hidden="true"><use href="\/icons\.svg#play-solid"><\/use><\/svg> Lancer<\/button>/);
  assert.match(withCalls(out), /onclick="debridFromIndex\(1\)">Sélectionner<\/button>/, 'rows keep their index in the filtered list');
  assert.equal(view.sub.textContent, '10 sources');
});

test('a group opens and closes', () => {
  const torrents = Array.from({ length: 7 }, (_, i) => torrent(`Film.4K.v${i}`, '4K'));
  const view = loadSources(torrents, { best: -1 });
  view.renderTorrents();
  assert.equal((view.list.innerHTML.match(/class="torrent-item /g) || []).length, 4);
  view.toggleSourceGroup('4k');
  assert.equal((view.list.innerHTML.match(/class="torrent-item /g) || []).length, 7);
  assert.match(view.list.innerHTML, /aria-expanded="true">Réduire<\/button>/);
  view.toggleSourceGroup('4k');
  assert.equal((view.list.innerHTML.match(/class="torrent-item /g) || []).length, 4);
  assert.match(source, /allTorrents = data\.torrents \|\| \[\];\n\s+expandedSourceGroups\.clear\(\);/, 'new sources start closed');
});

test('the same file found several times shows once, the best copy kept', () => {
  const same = { size_gb: 6.6, size_str: '6.6 GB' };
  const torrents = [
    torrent('The Film 1994 MULTi 1080p BluRay x265-Winks.mkv', '1080p', { ...same, source: 'StremThru' }),
    torrent('The.Film.1994.MULTi.1080p.BluRay.x265-Winks.mkv', '1080p', { ...same, source: 'Comet' }),
    torrent('The Film 1994 MULTi 1080p BluRay x265-Winks.mkv', '1080p', { ...same, source: 'DMM' }),
    torrent('The Film 1994 MULTi 1080p BluRay x265-Winks.mkv', '1080p', { size_gb: 9.1, size_str: '9.1 GB' }),
  ];
  const view = loadSources(torrents, { best: 2 });
  const rows = view.sourceRows(torrents, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(rows.map(row => [row.idx, row.copies]))), [[2, 3], [3, 1]], 'names differing only by punctuation are the same file; the best copy represents it');
  view.renderTorrents();
  assert.match(view.list.innerHTML, /DMM • 6\.6 GB • WEB-DL · HEVC · DDP • 12 pairs • trouvé 3 fois/);
  assert.equal(view.sub.textContent, '2 versions · 4 sources');
});

test('details, qualities and spoilers', () => {
  const view = loadSources([]);
  assert.equal(view.sourceResolution({ qualities: ['4K+'] }), '4k');
  assert.equal(view.sourceResolution({ parsed_details: { resolution: '2160p' } }), '4k');
  assert.equal(view.sourceResolution({ qualities: ['SD'] }), 'other');
  assert.equal(view.sourceDetails({ parsed_details: { source: 'BluRay', codec: 'HEVC', hdr_type: 'HDR10', audio_formats: ['DTS-HD', '5.1'] } }), 'BluRay · HEVC · HDR10 · DTS-HD · 5.1');
  const hidden = loadSources([torrent('Show.S01E02.Episode.Name.1080p', '1080p')], { hidden: true, type: 'series' });
  hidden.renderTorrents();
  assert.doesNotMatch(hidden.list.innerHTML, /Episode\.Name/, 'a concealed episode keeps its name hidden');
  const bare = loadSources([{ title: 'Show.S01E02.Name', meta: 'S01E02 « Pilot »', source: 'Site', qualities: [], langs: [] }], { best: -1, hidden: true, type: 'series' });
  bare.renderTorrents();
  assert.doesNotMatch(bare.list.innerHTML, /Pilot/, 'nor its description');
});

test('names, sites and badges from the sources are shown as text', () => {
  const hostile = '<img src=x onerror=alert(1)>';
  const view = loadSources([torrent(hostile, hostile, { source: hostile, langs: [hostile] })]);
  view.renderTorrents();
  assert.doesNotMatch(view.list.innerHTML, /<img src=x/);
  assert.match(view.list.innerHTML, /&lt;img src=x/);
});

test('the lower part follows the fiche', () => {
  const more = { innerHTML: 'old', hidden: false };
  const context = { document: { getElementById: id => (id === 'detailMore' ? more : null) } };
  vm.runInNewContext(`${moreCode}\nthis.show = showDetailMore; this.hide = hideDetailMore;`, context);
  context.show('<section>Distribution</section>');
  assert.equal(more.hidden, false);
  context.hide();
  assert.deepEqual([more.innerHTML, more.hidden], ['', true]);
  for (const place of ['async function selectMedia(media)', 'function closeDetailPanel()', 'async function runSearch()', 'async function switchTab(tab)']) {
    const body = source.slice(source.indexOf(place), source.indexOf('\n}\n', source.indexOf(place)));
    assert.match(body, /hideDetailMore\(\);/, place);
  }
  assert.match(source, /render\(detail, rendered\.html\);\n\s+showDetailMore\(rendered\.moreHtml\);\n\s+loadDetailPeoplePhotos\(media\.id\);/);
  assert.match(source, /querySelectorAll\('#detailMore \.detail-person\[data-person\]'\)/);
});

test('the fiche: hero actions to decide, episodes, then the rest after the sources', () => {
  const actions = builder.slice(builder.indexOf('<div class="detail-actions">'), builder.indexOf('</section>', builder.indexOf('<div class="detail-actions">')));
  assert.match(actions, /id="detailMainPlayBtn"/);
  assert.match(actions, /Bande-annonce/);
  assert.doesNotMatch(actions, /IMDb ↗|Letterboxd ↗|detailSpoilerChoice/);
  assert.match(builder, /<div class="detail-episodes-tools">\n\s+<div class="spoiler-series-setting"><label for="detailSpoilerChoice">Spoilers<\/label>/);
  assert.match(builder, /<\/section>\n\s+\$\{episodesHtml\}\n\s+\$\{journeyHtml\}\n\s+`,\n\s+moreHtml: html`\$\{castHtml\}\$\{similarHtml\}\$\{linksHtml\}`,/);
  // one function draws « Vous aimerez aussi », for the genre match and for TMDB.
  assert.match(source, /<div class="list-shelf-row">\$\{list\.map\(sm => posterCardHtml\(sm, /);
  assert.match(builder, /const similarHtml = similarSectionHtml\(Array\.isArray\(meta\.similar\) \? meta\.similar : \[\], isSeries\);/);
  assert.match(builder, /href="https:\/\/www\.imdb\.com\/title\/\$\{encodeURIComponent\(media\.id\)\}\/" target="_blank" rel="noopener"/);
  assert.match(builder, /\$\{!isSeries \? html`<a class="btn btn-secondary" href="https:\/\/letterboxd\.com\/imdb\//);
  const at = id => html.indexOf(`id="${id}"`);
  assert.ok(at('detailPanel') < at('torrentsPanel') && at('torrentsPanel') < at('detailMore'));
  assert.match(html, /<div id="detailMore" class="panel detail-more" hidden><\/div>/);
  assert.match(css, /\.detail-more\[hidden\] \{ display: none; \}/);
});
