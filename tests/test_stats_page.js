// Mes statistiques: four figures, three one-series charts in grey, readable
// without the mouse; reached from Bibliothèque → Plus.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const statsCode = require('../tools/interface_source.js').inFrench(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/stats.js'), 'utf8'));
const source = require('../tools/interface_source.js').appSource();
const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const escapeHtml = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

function load({ stats = null, fail = false } = {}) {
  const body = { innerHTML: '' };
  const context = {
    activeTab: 'stats',
    document: { getElementById: id => (id === 'viewingStatsBody' ? body : null) },
    api: async () => { if (fail) throw new Error('hors ligne'); return stats; },
    escapeHtml, titleAttrs: m => ` data-title-id="${m.id}"`, displayTitle: m => m.name,
    GENRE_FR: { Drama: 'Drame', Crime: 'Policier' },
    CLOUD_MONTHS: ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'],
  };
  vm.runInNewContext(`${statsCode}\nthis.api_ = { loadStats, statsHtml, statsDuration };`, context);
  return { ...context.api_, body };
}

const months = Array.from({ length: 12 }, (_, i) => {
  const date = new Date(Date.UTC(2025, 11 + i, 1));
  return { month: `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`, count: [0, 0, 1, 0, 0, 0, 0, 0, 0, 2, 0, 5][i] };
});
const STATS = {
  films: { seen: 5, started: 1, imported: 5 }, episodes: { seen: 9 }, series: { total: 4, started: 3, finished: 1 },
  watch_seconds: 9900, titles_seen: 9, genres_known: 6,
  genres: [{ name: 'Drama', count: 4 }, { name: 'Crime', count: 2 }, { name: '<b>Bio</b>', count: 1 }],
  months, decades: [{ decade: 1990, count: 1 }, { decade: 2020, count: 4 }],
  oldest: { id: 'tt0111161', name: 'Les Évadés', year: 1994 },
};

test('four figures in French', async () => {
  const page = load({ stats: STATS });
  await page.loadStats();
  const tiles = [...page.body.innerHTML.matchAll(/<div class="stats-tile">([\s\S]*?)<\/div>/g)].map(m => m[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim());
  assert.deepEqual(tiles, ['5 Films vus dont 5 importés · 1 en cours', '9 Épisodes vus dans 4 séries', '4 Séries 3 en cours · 1 terminée',
    '2 h 45 De lecture dans KINO positions enregistrées']);
});

test('durations read the French way', () => {
  const { statsDuration } = load();
  const read = seconds => { const d = statsDuration(seconds); return `${d.value}${d.unit ? ' ' + d.unit : ''}`; };
  assert.deepEqual([read(0), read(65), read(3000), read(3600), read(9900), read(36000 + 1800)], ['0 min', '1 min', '50 min', '1 h', '2 h 45', '10 h']);
});

test('genres and decades: one row each, a bar against the largest, the figure beside it', async () => {
  const page = load({ stats: STATS });
  await page.loadStats();
  const out = page.body.innerHTML;
  assert.match(out, /<span class="stats-meter-name">Drame<\/span>\s*<span class="stats-meter-track" aria-hidden="true"><span style="width:100%"><\/span><\/span>\s*<span class="stats-meter-count">4<\/span>/);
  assert.match(out, /title="Policier : 2 titres"/);
  assert.match(out, /width:50%/);
  assert.match(out, /&lt;b&gt;Bio&lt;\/b&gt;/);
  assert.doesNotMatch(out, /<b>Bio/);
  assert.match(out, /Sur 6 titres vus dont le genre est connu, sur 9\./);
  assert.match(out, /<span class="stats-meter-name">Années 2020<\/span>/);
  assert.match(out, /Le plus ancien film vu : <strong data-title-id="tt0111161">Les Évadés<\/strong> \(1994\)\./);
});

test('months: twelve columns, the busiest labelled, each readable on focus and in a table', async () => {
  const page = load({ stats: STATS });
  await page.loadStats();
  const out = page.body.innerHTML;
  assert.equal((out.match(/class="stats-col[ "]/g) || []).length, 12);
  assert.match(out, /<div class="stats-col is-peak" tabindex="0" title="nov 2026 : 5 titres" aria-label="nov 2026 : 5 titres">/);
  assert.match(out, /aria-label="sept 2026 : 2 titres"/);
  assert.match(out, /style="height:100%"/);
  assert.match(out, /style="height:40%"/);
  assert.equal((out.match(/is-peak/g) || []).length, 1);
  assert.match(out, /<table class="sr-only"><caption>Titres ouverts par mois<\/caption>[\s\S]*<td>déc 2025<\/td><td>0<\/td>/);
  assert.equal([...out.matchAll(/<span class="stats-col-label" aria-hidden="true">(.)<\/span>/g)].map(m => m[1]).join(''), 'DJFMAMJJASON');
});

test('nothing seen yet, or no answer', async () => {
  const empty = load({ stats: { films: { seen: 0, started: 0 }, series: { total: 0 }, titles_seen: 0, months } });
  await empty.loadStats();
  assert.match(empty.body.innerHTML, /Rien à compter pour l’instant/);
  const offline = load({ fail: true });
  await offline.loadStats();
  assert.equal(offline.body.innerHTML, '<p class="stats-empty">Statistiques indisponibles : hors ligne</p>');
});

test('reached from its Bibliothèque tab, alone on it, in one grey', () => {
  assert.match(html, /<button class="lib-subtab" id="subtab-stats" onclick="switchTab\('stats'\)">Statistiques<\/button>/);
  assert.match(html, /<div id="viewingStatsPanel" class="panel lib-panel stats-panel" style="display:none;">[\s\S]*?<div id="viewingStatsBody"><\/div>/);
  assert.ok(html.indexOf('src="/js/app-start.js"') > 0 && html.indexOf('src="/js/app-start.js"') < html.indexOf('src="/js/stats.js"'), 'stats.js uses the helpers of the app parts');
  assert.match(source, /\['watchlist', 'history', 'myseries', 'calendar', 'watched', 'offline', 'customlists', 'rdcloud', 'stats', 'library'\]\.includes\(tab\)/);
  assert.match(source, /\} else if \(tab === 'stats'\) \{[\s\S]*?document\.getElementById\('viewingStatsPanel'\)\.style\.display = 'block';\n\s+await loadStats\(\);/);
  const search = source.slice(source.indexOf('async function runSearch() {'), source.indexOf('async function oneClickCard('));
  assert.match(search, /viewingStatsPanel'\)\.style\.display = 'none'/);
  const start = css.indexOf('/* Mes statistiques');
  const statsCss = css.slice(start, css.indexOf('\n\n', start));
  assert.ok(statsCss.includes('.stats-col-bar'), 'the statistics rules are found');
  assert.doesNotMatch(statsCss, /#[0-9a-f]{3,6}\b|rgb\(/i, 'the shared greys only');
});
