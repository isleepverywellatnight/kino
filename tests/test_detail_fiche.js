// Immersive fiche: French labels, initials, large backdrop and inline trailer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();

function load() {
  const start = source.indexOf('const COUNTRY_FR = {');
  const end = source.indexOf('function buildDetailHtml(');
  const context = {};
  vm.runInNewContext(`${source.slice(start, end)}
    this.frenchCountries = frenchCountries; this.frenchRuntime = frenchRuntime; this.frenchAwards = frenchAwards;
    this.personInitials = personInitials; this.largeBackdrop = largeBackdrop;`, context);
  return context;
}

test('countries and running time read in French', () => {
  const { frenchCountries, frenchRuntime } = load();
  assert.equal(frenchCountries('United States, United Kingdom, Canada'), 'États-Unis, Royaume-Uni');
  assert.equal(frenchCountries('Japan'), 'Japon');
  assert.equal(frenchCountries('Atlantis'), 'Atlantis', 'unknown names stay as they are');
  assert.equal(frenchRuntime('76 min'), '1 h 16');
  assert.equal(frenchRuntime('120 min'), '2 h');
  assert.equal(frenchRuntime('57 min'), '57 min');
  assert.equal(frenchRuntime(''), '');
});

test('awards are translated, unknown sentences left out', () => {
  const { frenchAwards } = load();
  assert.equal(frenchAwards('Nominated for 1 Oscar. 7 wins & 17 nominations total'),
    'Nommé à 1 Oscar · 7 récompenses et 17 nominations au total');
  assert.equal(frenchAwards('Won 59 Primetime Emmys. 396 wins & 655 nominations total'),
    'Lauréat de 59 Emmys · 396 récompenses et 655 nominations au total');
  assert.equal(frenchAwards('Won 3 Oscars. Another 1 win total'), 'Lauréat de 3 Oscars');
  assert.equal(frenchAwards(''), '');
});

test('initials and the large backdrop', () => {
  const { personInitials, largeBackdrop } = load();
  assert.equal(personInitials('Catherine O\'Hara'), 'CO');
  assert.equal(personInitials('Cher'), 'C');
  assert.equal(personInitials(''), '?');
  assert.equal(largeBackdrop('https://images.metahub.space/background/medium/tt0107688/img'),
    'https://images.metahub.space/background/large/tt0107688/img');
  assert.equal(largeBackdrop('https://example.com/a.jpg'), 'https://example.com/a.jpg');
});

test('the hero uses the logo, the cast row and the inline trailer', () => {
  const start = source.indexOf('function buildDetailHtml(');
  const builder = source.slice(start, source.indexOf('\nfunction ', start + 10));
  assert.match(withCalls(builder), /class="detail-hero-logo"[^>]*\$\{actOn\('load', 'kinoHeroLogo', THIS\)\} \$\{actOn\('error', 'kinoRemove', THIS\)\}/);
  assert.match(builder, /\$\{act\('playDetailTrailer', trailerId,/);
  assert.match(builder, /class="detail-person" data-person="\$\{person\.name\}"/);
  assert.doesNotMatch(builder, /openTrailerModal\(/, 'the fiche no longer opens the separate window');
  assert.match(source, /loadDetailPeoplePhotos\(media\.id\);/);
});

test('Escape closes the inline trailer before anything else', () => {
  const start = source.indexOf('function closeOpenDialogOnEscape() {');
  const fn = source.slice(start, source.indexOf('\n}\n', start) + 2);
  let closed = 0;
  const context = {
    ESCAPE_DIALOGS: [],
    closeInAppMoreMenu: () => false,
    closeDetailTrailer: () => { closed += 1; },
    document: { getElementById: id => (id === 'detailTrailer' ? { hidden: false } : null) },
  };
  vm.runInNewContext(`${fn}\nthis.escape = closeOpenDialogOnEscape;`, context);
  assert.equal(context.escape(), true);
  assert.equal(closed, 1);
});
