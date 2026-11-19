// What sites and services send (release names, episode data, debrid status,
// cast, synopses, list items) is shown as text: no markup it carries becomes
// part of the page, and inline handlers only ever receive numbers or JSON.
const assert = require('node:assert/strict');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const between = (start, end) => start + source.split(start, 2)[1].split(end, 1)[0];
// The page's render helpers (web/js/html.js), then app-social's own.
const helpers = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'web', 'js', 'html.js'), 'utf8')
  + between('function listMediaArg(m)', 'async function checkTraktStatus()');

const HOSTILE = `Tom & "Jerry's" <img src=x onerror="window.pwned=1">`;
const BREAKOUT = `1); window.pwned = 1; (`;

function element() {
  return { style: {}, innerHTML: '', textContent: '', classList: { add() {}, remove() {}, toggle() {} }, scrollIntoView() {} };
}

// Outside quoted attribute values, no tag the data carried.
function assertNoInjectedMarkup(fragment) {
  const html = String(fragment);
  const outsideAttributes = html.replace(/=(["'])[\s\S]*?\1/g, '=""');
  assert.doesNotMatch(outsideAttributes, /<img src=x/);
  assert.doesNotMatch(withCalls(html), /onerror="window\.pwned/, 'no attribute of its own');
}

// Every inline handler is one call whose arguments are plain values.
function assertHandlersAreCalls(fragment) {
  const html = String(fragment);
  const decode = text => text.replaceAll('&quot;', '"').replaceAll('&#39;', "'").replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>').replaceAll('&amp;', '&');
  for (const [, , body] of html.matchAll(/on(?:click|error)=(["'])(.*?)\1/g)) {
    const code = decode(body);
    if (code === 'this.remove()') continue;
    const name = code.split('(')[0];
    let calls = 0;
    const context = { [name]: () => { calls++; }, this: {}, event: {}, window: {} };
    vm.runInNewContext(code.replace(/\bthis\b/g, 'null'), context);
    assert.equal(calls, 1, code);
    assert.equal(context.window.pwned, undefined, code);
  }
}

test('episode tiles show hostile episode data as text and keep numeric handlers', () => {
  const tiles = between('function episodeAirDate(video)', 'function revealEpisodeToWatch(');
  const context = {
    Spoilers: { neutralTitle: n => `Épisode ${n}`, watched: () => false },
    spoilerConcealed: () => false, userHistory: [],
  };
  const video = { episode: BREAKOUT, name: HOSTILE, overview: HOSTILE, thumbnail: `x" onerror="window.pwned=1`, released: HOSTILE };
  const html = vm.runInNewContext(`${helpers}; ${tiles};
    episodeTileHtml(${JSON.stringify(video)}, { seasonNum: ${JSON.stringify(BREAKOUT)}, seriesId: 'tt1', epPosMap: {},
      today: '2026-01-01', fr: {}, target: {} })`, context);
  assertNoInjectedMarkup(html);
  assertHandlersAreCalls(html);
  assert.match(String(html), /&lt;img src=x/, 'the hostile title is still shown, as text');
});

test('debrid progress shows the service’s words as text', () => {
  const debrid = between('function renderDebridState(', '// Where this title (or episode) was left');
  const box = element();
  const context = { document: { getElementById: () => box }, setTimeout: () => 0, window: {}, currentMedia: null };
  vm.runInNewContext(`${helpers}; ${debrid};
    renderDebridState({ ready: false, progress: ${JSON.stringify(`50%"><img src=x onerror="window.pwned=1">`)},
      status: ${JSON.stringify(HOSTILE)}, speed: ${JSON.stringify(HOSTILE)}, seeders: ${JSON.stringify(HOSTILE)} }, 1, 1)`, context);
  assertNoInjectedMarkup(box.innerHTML);
  context.Spoilers = { episodeNumber: () => null, neutralTitle: n => `Épisode ${n}` };
  context.spoilerConcealed = () => false;
  context.spoilerSeriesId = () => 'tt1';
  vm.runInNewContext(`var lastReadyDebrid;
    renderDebridState({ ready: true, files: [{ filename: ${JSON.stringify(HOSTILE)}, filesize: ${JSON.stringify(HOSTILE)}, is_target_ep: true }] },
      1, 1, ${JSON.stringify(HOSTILE)}, 1)`, context);
  assertNoInjectedMarkup(box.innerHTML);
  assertHandlersAreCalls(box.innerHTML);
});

test('a source row shows hostile release data as text', () => {
  const rows = between('function sourceResolution(t)', 'function renderTorrents()');
  const t = {
    title: HOSTILE, meta: HOSTILE, source: HOSTILE, size_str: HOSTILE, seeders: 12, size_gb: 4,
    qualities: [HOSTILE, '1080p'], langs: [HOSTILE, 'VFF'], is_instant: true, magnet: 'magnet:?xt=urn:btih:x',
    parsed_details: { source: HOSTILE, codec: HOSTILE, hdr_type: HOSTILE, audio_formats: [HOSTILE], resolution: '1080p' },
    why: [{ text: HOSTILE, tone: 'good', points: 10 }, { text: HOSTILE, tone: 'bad', points: -5 }],
    recommendation: { reasons: [HOSTILE], warnings: [HOSTILE] },
  };
  const context = { expandedSourceGroups: new Set(), Spoilers: { neutralTitle: n => `Épisode ${n}` } };
  const html = vm.runInNewContext(`${helpers}; ${rows};
    sourceRowHtml({ t: ${JSON.stringify(t)}, idx: 0, copies: 2 }, true, false, 1, ${JSON.stringify({ ...t, title: 'Best' })})`, context);
  assertNoInjectedMarkup(html);
  assertHandlersAreCalls(html);
});

test('the slot machine shows a hostile rating and title as text', () => {
  const start = 'render(strip, slots.map((m, idx) => {';
  const body = source.split(start, 2)[1].split('  }));', 1)[0];
  const render = 'function renderSlots(slots) { return String(html`${slots.map((m, idx) => {' + body + '})}`); }';
  const html = vm.runInNewContext(`${helpers}; ${render};
    renderSlots([{ name: ${JSON.stringify(HOSTILE)}, imdbRating: ${JSON.stringify(`8.1<img src=x onerror="window.pwned=1">`)},
      year: ${JSON.stringify(HOSTILE)}, poster: ${JSON.stringify(`x" onerror="window.pwned=1`)} }])`, {});
  assertNoInjectedMarkup(html);
  assert.match(html, /★ 8\.1&lt;img/, 'the rating is still shown, as text');
});
