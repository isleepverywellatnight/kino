// Catalogue toolbar: genres on one scrolling line with arrows, the chosen genre
// kept in view, sort and filters grouped on the title line.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/redesign.css'), 'utf8');

function extract(name) {
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, `${name} is defined`);
  return source.slice(start, source.indexOf('\n}\n', start) + 2);
}

function classList() {
  const set = new Set();
  return {
    set,
    toggle(name, on) { if (on) set.add(name); else set.delete(name); },
    contains: name => set.has(name),
  };
}

// A row 600 px wide on screen (left 100), chips 100 px wide with 0 gap.
function makeStrip({ chips = 10, width = 600, activeIndex = 0, scrollLeft = 0 } = {}) {
  const listeners = {};
  const scrolls = [];
  const row = {
    clientWidth: width,
    scrollWidth: chips * 100,
    scrollLeft,
    listeners,
    scrolls,
    addEventListener(type, fn, options) { listeners[type] = { fn, options }; },
    getBoundingClientRect: () => ({ left: 100, right: 100 + width }),
    scrollBy(options) {
      scrolls.push(options);
      row.scrollLeft = Math.max(0, Math.min(row.scrollWidth - row.clientWidth, row.scrollLeft + options.left));
    },
    querySelector(selector) {
      if (selector !== '.chip.active' || activeIndex < 0) return null;
      return {
        getBoundingClientRect: () => {
          const left = 100 + activeIndex * 100 - row.scrollLeft;
          return { left, right: left + 100 };
        },
      };
    },
  };
  const prev = { hidden: true };
  const next = { hidden: true };
  const strip = {
    classList: classList(),
    querySelector: selector => (selector === '.genre-scroll-prev' ? prev : selector === '.genre-scroll-next' ? next : null),
  };
  return { row, strip, prev, next };
}

function load(parts, extra = {}) {
  const observers = [];
  const context = {
    document: {
      getElementById: id => (id === 'genreFilters' ? parts.row : id === 'genreStrip' ? parts.strip : null),
    },
    window: { addEventListener() {} },
    requestAnimationFrame: fn => fn(),
    MutationObserver: class {
      constructor(fn) { this.fn = fn; observers.push(this); }
      observe(target, options) { this.target = target; this.options = options; }
    },
    ResizeObserver: class {
      constructor(fn) { this.fn = fn; observers.push(this); }
      observe(target) { this.target = target; this.resize = true; }
    },
    Math,
    ...extra,
  };
  const names = ['updateGenreArrows', 'scrollGenres', 'revealActiveGenre', 'initGenreStrip'];
  vm.runInNewContext(`${names.map(extract).join('\n')}\nthis.api = { ${names.join(', ')} };`, context);
  return { ...context.api, observers };
}

test('arrows show only when some genres are out of view', () => {
  const fits = makeStrip({ chips: 5 });
  load(fits).updateGenreArrows();
  assert.equal(fits.prev.hidden, true);
  assert.equal(fits.next.hidden, true);
  assert.equal(fits.strip.classList.contains('fade-right'), false);

  const start = makeStrip();
  load(start).updateGenreArrows();
  assert.equal(start.prev.hidden, true);
  assert.equal(start.next.hidden, false);
  assert.equal(start.strip.classList.contains('fade-right'), true);
  assert.equal(start.strip.classList.contains('fade-left'), false);

  const middle = makeStrip({ scrollLeft: 200 });
  load(middle).updateGenreArrows();
  assert.equal(middle.prev.hidden, false);
  assert.equal(middle.next.hidden, false);
  assert.equal(middle.strip.classList.contains('fade-left'), true);

  const end = makeStrip({ scrollLeft: 400 });
  load(end).updateGenreArrows();
  assert.equal(end.prev.hidden, false);
  assert.equal(end.next.hidden, true);
});

test('a hidden row hides both arrows', () => {
  const hidden = makeStrip();
  hidden.row.clientWidth = 0;
  hidden.row.scrollWidth = 0;
  hidden.next.hidden = false;
  load(hidden).updateGenreArrows();
  assert.equal(hidden.prev.hidden, true);
  assert.equal(hidden.next.hidden, true);
});

test('the arrows scroll most of a width, smoothly', () => {
  const parts = makeStrip();
  const api = load(parts);
  api.scrollGenres(1);
  assert.deepEqual({ ...parts.row.scrolls[0] }, { left: 420, behavior: 'smooth' });
  api.scrollGenres(-1);
  assert.equal(parts.row.scrolls[1].left, -420);
  const narrow = makeStrip({ width: 180 });
  load(narrow).scrollGenres(1);
  assert.equal(narrow.row.scrolls[0].left, 160);
});

test('the chosen genre is brought into view with room for the faded edge', () => {
  const last = makeStrip({ activeIndex: 9 });
  load(last).revealActiveGenre();
  // Chip spans 1000-1100 on screen, row ends at 700: 400 + 40 px margin,
  // clamped to the end of the row.
  assert.equal(last.row.scrolls[0].left, 440);
  assert.equal(last.row.scrollLeft, 400);
  assert.equal(last.next.hidden, true);
  assert.equal(last.prev.hidden, false);

  const first = makeStrip({ activeIndex: 0, scrollLeft: 300 });
  load(first).revealActiveGenre();
  assert.equal(first.row.scrolls[0].left, -340);
  assert.equal(first.row.scrollLeft, 0);

  const visible = makeStrip({ activeIndex: 2 });
  load(visible).revealActiveGenre();
  assert.equal(visible.row.scrolls.length, 0);

  const fits = makeStrip({ chips: 5, activeIndex: 4 });
  load(fits).revealActiveGenre();
  assert.equal(fits.row.scrolls.length, 0);

  const none = makeStrip({ activeIndex: -1 });
  load(none).revealActiveGenre();
  assert.equal(none.row.scrolls.length, 0);
  assert.equal(none.next.hidden, false);
});

test('the mouse wheel scrolls the genres sideways, the page otherwise', () => {
  const parts = makeStrip({ scrollLeft: 100 });
  load(parts).initGenreStrip();
  const wheel = parts.row.listeners.wheel;
  assert.equal(wheel.options.passive, false);
  let prevented = 0;
  wheel.fn({ deltaY: 120, deltaX: 0, preventDefault: () => { prevented += 1; } });
  assert.equal(parts.row.scrollLeft, 220);
  assert.equal(prevented, 1);
  wheel.fn({ deltaY: 2, deltaX: 30, preventDefault: () => { prevented += 1; } });
  assert.equal(prevented, 1, 'a sideways trackpad gesture keeps its native scrolling');

  const fits = makeStrip({ chips: 5 });
  load(fits).initGenreStrip();
  fits.row.listeners.wheel.fn({ deltaY: 120, deltaX: 0, preventDefault: () => { prevented += 1; } });
  assert.equal(prevented, 1, 'the page scrolls when every genre fits');
  assert.equal(parts.row.listeners.scroll.options.passive, true);
});

test('rebuilt genres, hidden rows and new widths keep the arrows right', () => {
  const parts = makeStrip({ activeIndex: 9 });
  const { initGenreStrip, observers } = load(parts);
  initGenreStrip();
  const mutation = observers.find(o => !o.resize);
  const resize = observers.find(o => o.resize);
  assert.equal(mutation.target, parts.row);
  assert.equal(mutation.options.childList, true);
  assert.deepEqual([...mutation.options.attributeFilter], ['style']);
  assert.equal(resize.target, parts.row);

  // A section rebuilds the chips: the chosen genre comes back into view.
  mutation.fn([{ type: 'childList' }]);
  assert.equal(parts.row.scrollLeft, 400);

  // The library hides the row through its style attribute.
  parts.row.clientWidth = 0;
  parts.row.scrollWidth = 0;
  mutation.fn([{ type: 'attributes', attributeName: 'style' }]);
  assert.equal(parts.prev.hidden, true);
  assert.equal(parts.next.hidden, true);

  // A narrower window: the chosen genre is brought back into view.
  const narrow = makeStrip({ activeIndex: 9, width: 300 });
  const second = load(narrow);
  second.initGenreStrip();
  second.observers.find(o => o.resize).fn([]);
  assert.equal(narrow.row.scrollLeft, 700);
});

test('selecting a genre keeps it in view', () => {
  const body = extract('selectGenre');
  assert.match(body, /syncChipPressed\(genreChips, el\);\n\s+revealActiveGenre\(\);/);
  assert.match(source, /initPosterPreviews\(\);\s*\n\s*initGenreStrip\(\);/);
});

test('the toolbar markup groups the controls and wraps the genres', () => {
  assert.match(html, /<div class="genre-strip" id="genreStrip">\s*<button type="button" class="genre-scroll genre-scroll-prev" aria-label="Genres précédents" onclick="scrollGenres\(-1\)" hidden>‹<\/button>\s*<div id="genreFilters" class="genre-row" role="group" aria-label="Genres">/);
  assert.match(html, /<button type="button" class="genre-scroll genre-scroll-next" aria-label="Genres suivants" onclick="scrollGenres\(1\)" hidden>›<\/button>\s*<\/div>/);
  assert.match(html, /<select id="catalogSortSelect" class="catalog-sort" aria-label="Trier le catalogue" onchange="onChangeCatalogSort\(this\.value\)">/);
  for (const [value, label] of [['top', 'Tendances'], ['imdbRating', 'Mieux notés'], ['recent', 'Plus récents'], ['oldest', 'Plus anciens']]) {
    assert.match(html, new RegExp(`<option value="${value}">${label}</option>`));
  }
  assert.match(html, /id="hideWatchedChip"[^>]*>Masquer déjà vus<\/button>/);
});

test('the genre row stays on one line, without a visible scrollbar', () => {
  assert.match(css, /\.genre-row \{[^}]*flex-wrap: nowrap !important;[^}]*overflow-x: auto;/);
  assert.match(css, /\.genre-row \{[^}]*scrollbar-width: none;/);
  assert.match(css, /\.genre-row::-webkit-scrollbar \{ display: none; \}/);
  assert.match(css, /\.genre-row \.chip \{ flex: 0 0 auto; white-space: nowrap; \}/);
  assert.match(css, /\.genre-scroll\[hidden\] \{ display: none; \}/);
  assert.match(css, /\.genre-strip\.fade-left\.fade-right \.genre-row \{[^}]*mask-image/);
});
