// « Tout »: one search for films and series, grouped by type (the group of the
// closest title first), with a type filter; the tabs no longer narrow it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/redesign.css'), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const resultsCode = slice('// « Tout »: films and series of one search', 'async function oneClickCard(');

function load(metas) {
  const grid = { innerHTML: '' };
  const context = {
    document: { getElementById: id => (id === 'postersGrid' ? grid : null) },
    posterCardHtml: (m, kind) => `<card ${m.id} ${kind}>`,
  };
  vm.runInNewContext(`${resultsCode}\nlastSearch = ${JSON.stringify({ query: 'dune', metas, filter: 'all' })};\nthis.api_ = { renderSearchResults, setSearchFilter, state: () => lastSearch };`, context);
  return { ...context.api_, grid };
}

const mixed = [
  { id: 'tt1', name: 'Dune', type: 'movie' },
  { id: 'tt3', name: 'Dune: Prophecy', type: 'series' },
  { id: 'tt2', name: 'Dune: Part Two', type: 'movie' },
];

test('films and series in groups, the closest title first', () => {
  const page = load(mixed);
  page.renderSearchResults();
  const out = page.grid.innerHTML;
  assert.match(out, /^<div class="search-results-tools" role="group" aria-label="Type de résultat">/);
  assert.match(out, /class="chip active" aria-pressed="true" onclick="setSearchFilter\('all'\)">Tout <span class="list-count">3<\/span>/);
  assert.match(out, />Films <span class="list-count">2<\/span><\/button>.*>Séries <span class="list-count">1<\/span><\/button>/);
  const body = out.slice(out.indexOf('</div>') + 6);
  assert.equal(body, '<h3 class="search-group-title">Films <span class="list-count">2</span></h3><card tt1 movie><card tt2 movie>'
    + '<h3 class="search-group-title">Séries <span class="list-count">1</span></h3><card tt3 series>');
});

test('a series found first puts the series first', () => {
  const page = load([mixed[1], mixed[0]]);
  page.renderSearchResults();
  assert.ok(page.grid.innerHTML.indexOf('>Séries <span') < page.grid.innerHTML.indexOf('>Films <span'));
});

test('the filter keeps one type, without group titles', () => {
  const page = load(mixed);
  page.setSearchFilter('series');
  assert.match(page.grid.innerHTML, /class="chip active" aria-pressed="true" onclick="setSearchFilter\('series'\)"/);
  assert.match(page.grid.innerHTML, /<\/div><card tt3 series>$/);
  assert.doesNotMatch(page.grid.innerHTML, /search-group-title/);
  page.setSearchFilter('all');
  assert.match(page.grid.innerHTML, /search-group-title/);
});

test('one type only: no filter and no group title', () => {
  const page = load([mixed[0], mixed[2]]);
  page.renderSearchResults();
  assert.equal(page.grid.innerHTML, '<card tt1 movie><card tt2 movie>');
  page.setSearchFilter('series');
  assert.equal(page.state().filter, 'all', 'a filter with nothing in it falls back to all');
});

test('each group shows 36 titles at most, and counts them all', () => {
  const page = load(Array.from({ length: 50 }, (_, i) => ({ id: `tt${i}`, name: `Film ${i}`, type: i % 10 ? 'movie' : 'series' })));
  page.renderSearchResults();
  assert.equal((page.grid.innerHTML.match(/ movie>/g) || []).length, 36);
  assert.match(page.grid.innerHTML, /Films <span class="list-count">45<\/span><\/h3>/);
});

test('« Tout » is the first choice, and the tabs no longer change it', () => {
  assert.match(html, /<select id="searchType" class="search-type-select" onchange="toggleSearchMode\(\)" aria-label="Chercher dans">\n\s*<option value="all">Tout<\/option>\n\s*<option value="movie">Films<\/option>/);
  const switchTab = source.slice(source.indexOf('async function switchTab(tab)'), source.indexOf('async function loadRdCloud()'));
  assert.ok(switchTab.includes("tab === 'anime'"));
  assert.doesNotMatch(switchTab, /getElementById\('searchType'\)\.value = /);
  const run = slice('async function runSearch() {', 'async function oneClickCard(');
  assert.match(run, /if \(mode === 'all'\) \{\n\s+lastSearch = \{ query: q, metas: data\.metas, filter: 'all' \};\n\s+renderSearchResults\(\);/);
  assert.match(run, /renderPosterCards\(data\.metas\.slice\(0, 36\), mode\);/, 'a chosen type keeps the former results');
  assert.match(run, /textContent = `Résultats pour « \$\{q\} »`;/);
  assert.match(css, /\.search-results-tools \{ grid-column: 1 \/ -1;/);
  assert.match(css, /\.search-group-title \{ grid-column: 1 \/ -1;/);
});
