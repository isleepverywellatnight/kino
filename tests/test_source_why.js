// Pourquoi cette source : the recommended source says what put it first, and
// every source can unfold its reasons and how it compares with the recommended one.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const sourcesCode = source.slice(source.indexOf('// --- Sources: the best one first'), source.indexOf('async function copyTorrentMagnet('));
const escapeHtml = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

const PARTS = ['instant_points', 'quality_points', 'language_points', 'picture_points', 'sound_points', 'seeder_points',
  'fake_penalty', 'size_points', 'anime_group_points', 'year_points'];
const parts = values => Object.fromEntries(PARTS.map((part, i) => [part, values[i] || 0]));
const reason = (part, text, tone, points) => ({ part, text, tone, points });

const BEST = {
  title: 'Film.2024.MULTI.VFF.2160p.REMUX', qualities: ['4K'], langs: ['MULTI', 'VFF'], source: 'Comet', size_gb: 60, score: 5500,
  parsed_details: { resolution: '4K' }, recommendation: { reasons: ['Titre cohérent'], warnings: [] },
  score_parts: parts([3500, 480, 950, 140, 80, 80, 0, 30, 0, 500]),
  why: [reason('instant_points', 'Prête tout de suite', 'good', 3500), reason('quality_points', '4K REMUX, la qualité choisie', 'good', 480),
    reason('language_points', 'VF + VO', 'good', 950), reason('picture_points', 'Image SDR, adaptée à votre écran', 'good', 140),
    reason('sound_points', 'Son Dolby Atmos', 'good', 80), reason('size_points', 'Fichier lourd (35 à 65 Go)', 'neutral', 30),
    reason('year_points', 'Année 2024 confirmée', 'good', 500)],
};
const OTHER = {
  title: 'Film.2024.VOSTFR.2160p.<b>', qualities: ['4K'], langs: ['VOSTFR'], source: 'Torrentio', size_gb: 20, score: 1500,
  parsed_details: { resolution: '4K' }, score_parts: parts([0, 300, 250, 140, 0, 80, 0, 60, 0, 500]),
  why: [reason('instant_points', 'Disponibilité immédiate non signalée', 'bad', 0), reason('quality_points', '4K, la qualité choisie', 'good', 300),
    reason('language_points', 'VOSTFR seulement', 'bad', 250), reason('size_points', 'Taille <adaptée>', 'good', 60)],
};

function render(torrents, { hidden = false, maxScore } = {}) {
  const elements = {};
  const element = id => elements[id] || (elements[id] = { id, innerHTML: '', textContent: '', value: '' });
  const context = {
    document: { getElementById: element }, allTorrents: torrents, currentMedia: { type: hidden ? 'series' : 'movie' },
    getFilteredTorrents: () => torrents,
    scoreTorrent: t => (maxScore !== undefined ? maxScore : t.score),
    spoilerConcealed: () => hidden, spoilerSeriesId: () => 'tt1', Spoilers: { neutralTitle: () => 'Épisode' }, escapeHtml,
  };
  vm.runInNewContext(`${sourcesCode}\nthis.api_ = { renderTorrents, sourceComparison, sourceReasons };`, context);
  context.api_.renderTorrents();
  return { html: element('torrentsList').innerHTML, api: context.api_ };
}

test('the recommended source says what put it first, in the order of their weight', () => {
  const { html } = render([BEST, OTHER]);
  const best = html.slice(0, html.indexOf('<section class="source-group"'));
  assert.match(best, /<div class="torrent-explanation">Pourquoi celle-ci : Prête tout de suite · VF \+ VO · Année 2024 confirmée · 4K REMUX, la qualité choisie<\/div>/);
  assert.doesNotMatch(best, /Pourquoi : Titre cohérent/, 'the score’s reasons replace the announced signals');
  assert.match(best, /<details class="source-why"><summary>Pourquoi ce rang \?<\/summary><p><strong>Points forts :<\/strong> Prête tout de suite · VF \+ VO · Année 2024 confirmée · 4K REMUX, la qualité choisie · Image SDR, adaptée à votre écran · Son Dolby Atmos<\/p><p><strong>À noter :<\/strong> Fichier lourd \(35 à 65 Go\)<\/p><\/details>/);
  assert.doesNotMatch(best, /Moins bien que la recommandée/);
});

test('another source tells where it falls behind the recommended one, and where it does better', () => {
  const { html, api } = render([BEST, OTHER]);
  const other = html.slice(html.indexOf('<section class="source-group"'));
  assert.match(other, /<strong>Points forts :<\/strong> 4K, la qualité choisie · Taille &lt;adaptée&gt;<\/p>/);
  assert.match(other, /<strong>À noter :<\/strong> Disponibilité immédiate non signalée · VOSTFR seulement<\/p>/);
  assert.match(other, /<strong>Moins bien que la recommandée :<\/strong> disponibilité, langue, qualité · mieux : taille<\/p>/);
  assert.match(other, /Film\.2024\.VOSTFR\.2160p\.&lt;b&gt;/);
  assert.deepEqual(JSON.parse(JSON.stringify(api.sourceComparison(OTHER, BEST))), { behind: ['disponibilité', 'langue', 'qualité'], ahead: ['taille'] });
});

test('older answers without reasons keep the announced signals, and nothing unfolds', () => {
  const plain = ({ why, score_parts, ...rest }) => rest;
  const { html } = render([plain(BEST), plain(OTHER)]);
  assert.match(html, /<div class="torrent-explanation">Pourquoi : Titre cohérent<\/div>/);
  assert.doesNotMatch(html, /source-why/);
});

test('a concealed episode shows no reasons, and no comparison without a recommended source', () => {
  assert.doesNotMatch(render([BEST, OTHER], { hidden: true }).html, /Pourquoi|source-why/);
  const { html } = render([OTHER, BEST], { maxScore: 50 });
  assert.doesNotMatch(html, /Recommandé|Pourquoi celle-ci|Moins bien que la recommandée/);
  assert.match(html, /<summary>Pourquoi ce rang \?<\/summary>/, 'each source still unfolds its own reasons');
});

test('the reasons fold under each source, in the page’s monochrome style', () => {
  assert.match(css, /\.source-why summary \{[^}]*cursor: pointer;/);
  assert.match(css, /\.source-why summary:hover, \.source-why summary:focus-visible \{/);
  assert.doesNotMatch(css.slice(css.indexOf('.source-why {'), css.indexOf('.source-why strong')), /#(?![0-9a-f]{3}\b)(?:[0-9a-f]{6})\b(?<!fafafa|d4d4d4|b5b5b5|525252)/i);
});
