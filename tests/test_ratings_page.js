// « Noter en un geste »: the three gestures, when the end of a
// playback asks, the Home row « Vos derniers vus, à noter », and the fiche.
const assert = require('node:assert/strict');
const { raw } = require('../tools/interface_source.js').templateHelpers();
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const read = name => fs.readFileSync(path.join(path.join(__dirname, '..'), name), 'utf8');
const code = read('web/js/ratings.js');
const html = require('../tools/interface_source.js').indexHtml();
const plain = value => JSON.parse(JSON.stringify(value));

function element(id) {
  const classes = new Set();
  return {
    id, hidden: true, textContent: '', innerHTML: '', src: '', dataset: {},
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c), toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) },
    querySelector: () => null, querySelectorAll: () => [],
  };
}

function load({ history = [], tab = 'movies', fail = false, playing = false } = {}) {
  const elements = {};
  const el = id => elements[id] || (elements[id] = element(id));
  if (playing) el('inAppPlayerOverlay').classList.add('active');
  const calls = [];
  const context = {
    userHistory: history,
    networkOnline: true,
    FOR_YOU_CACHE: new Map([['movie', { items: [1] }]]),
    RELATED_CLIENT_CACHE: new Map([['x', 1]]),
    document: { getElementById: el, documentElement: { classList: element('html').classList } },
    api: async (url, options) => {
      const body = JSON.parse(options.body);
      calls.push([url, body]);
      if (fail) throw new Error('down');
      const fields = {};
      if ('rating' in body) Object.assign(fields, { user_rating: body.rating, rated_at: 1, rating_skipped: false });
      if ('asked' in body) fields.rating_asked = body.asked;
      if ('skip' in body) fields.rating_skipped = body.skip;
      return { ok: true, id: body.id, fields };
    },
        onHomeTab: () => tab === 'movies' || tab === 'series',
    homeKind: () => (tab === 'series' ? 'series' : 'movie'),
    posterCardHtml: m => raw(`<poster ${m.id} ${m.poster}>`),
    displayTitle: m => m.name,
    refreshUserLists: async () => {},
    renderHomeForYou: () => calls.push(['renderHomeForYou']),
    // Short waits run at once; the card's 30 s are left to the test.
    setTimeout: (fn, ms) => { if ((ms || 0) <= 1500) fn(); return 0; }, clearTimeout: () => {},
    Promise, Number, String, Array, JSON, Infinity, parseFloat,
  };
  vm.runInNewContext(`${code}\nthis.RATING_GESTURES = RATING_GESTURES;`, context);
  return { context, el, calls };
}

const heat = (extra = {}) => ({ id: 'tt0113277', name: 'Heat', type: 'movie', poster: 'https://p/heat.jpg', completed: true, last_watched: 30, ...extra });
const lupin = (extra = {}) => ({ id: 'tt2531336', name: 'Lupin', type: 'series', season: 2, episode: 5, completed: true, next_season: 2, next_episode: 6,
  watched_episodes: ['s1e1', 's1e2', 's1e3'], last_watched: 20, ...extra });

test('three gestures, read from any stored value', () => {
  const { context } = load();
  assert.deepEqual(plain(context.RATING_GESTURES.map(g => [g.key, g.value, g.label])),
    [['disliked', '1', 'Pas aimé'], ['liked', '4', 'Aimé'], ['loved', '5', 'Adoré']]);
  assert.deepEqual(['', '1', '2.5', '3', '3.5', '4', '4.5', '5'].map(context.ratingGesture),
    ['', 'disliked', 'disliked', '', 'liked', 'liked', 'liked', 'loved']);
  assert.match(String(context.ratingBadgeHtml('5')), /title="Votre avis : Adoré".*#heart-fill/);
  assert.match(String(context.ratingBadgeHtml('3')), /★ 3\/5/, 'an imported 3 keeps its stars');
  assert.equal(context.ratingBadgeHtml(''), '');
});

test('the end of a film, or of a season, asks once', () => {
  const { context } = load();
  const ask = context.ratingAskFor;
  assert.equal(ask(heat()), 'film');
  assert.equal(ask(heat({ rating_asked: 'film' })), '');
  assert.equal(ask(heat({ user_rating: '4' })), '', 'never for a title already rated');
  assert.equal(ask(heat({ completed: false, progress_pct: 40 })), '');
  assert.equal(ask(lupin()), '', 'in the middle of a season');
  assert.equal(ask(lupin({ next_season: 3, next_episode: 1 })), 'S2');
  assert.equal(ask(lupin({ next_season: 3, rating_asked: 'S2' })), '');
  assert.equal(ask(lupin({ season: 3, next_season: 3, series_finished: true, rating_asked: 'S2' })), 'S3', 'the next season asks again');
  assert.equal(ask(lupin({ next_season: 3, completed: false })), '', 'the finale left halfway');
  assert.equal(ask(null), '');
});

test('the titles to rate: seen, of the tab, not rated nor passed, last seen first', () => {
  const { context } = load();
  const history = [
    heat({ last_watched: 10 }),
    heat({ id: 'tt1', name: 'Alien', last_watched: 50, poster: '' }),
    heat({ id: 'tt2', name: 'Rated', user_rating: '5' }),
    heat({ id: 'tt3', name: 'Passed', rating_skipped: true }),
    heat({ id: 'tt4', name: 'Halfway', completed: false, progress_pct: 30 }),
    heat({ id: 'tt5', name: 'Imported', completed: false, imported_watched: true, last_watched: 5 }),
    heat({ id: 'Some.File.mkv', name: 'Some.File.mkv', poster: '' }),
    lupin(),
    lupin({ id: 'tt6', name: 'Two episodes', watched_episodes: ['s1e1', 's1e2'] }),
    lupin({ id: 'tt7', name: 'Finished', watched_episodes: [], series_finished: true }),
  ];
  assert.deepEqual(plain(context.ratingCandidates(history, 'movie').map(e => e.name)), ['Alien', 'Heat', 'Imported']);
  assert.deepEqual(plain(context.ratingCandidates(history, 'series').map(e => e.name)), ['Lupin', 'Finished']);
  assert.equal(context.ratingCandidates(history, 'movie', 1).length, 1);
  assert.equal(context.ratingPoster({ id: 'tt1' }), 'https://images.metahub.space/poster/small/tt1/img');
});

test('a gesture saves the rating; the same one twice takes it back', async () => {
  const entry = heat();
  const { context, calls } = load({ history: [entry] });
  assert.equal(await context.rateTitle('tt0113277', '5'), '5');
  assert.deepEqual(calls[0], ['/api/rating', { id: 'tt0113277', rating: '5' }]);
  assert.equal(entry.user_rating, '5');
  assert.equal(context.FOR_YOU_CACHE.size, 0, '« Pour vous » is asked again');
  assert.equal(context.RELATED_CLIENT_CACHE.size, 0);
  assert.ok(calls.some(c => c[0] === 'renderHomeForYou'));
  assert.equal(await context.rateTitle('tt0113277', '5'), '', 'second click');
  assert.equal(entry.user_rating, '');
  assert.equal(await context.rateTitle('tt0113277', '4'), '4', 'another gesture replaces it');
});

test('the gestures: pressed state, labels, and a compact form for the Home row', () => {
  const { context } = load();
  const full = String(context.ratingGesturesHtml('tt1', '4'));
  assert.equal((full.match(/class="rate-gesture /g) || []).length, 3);
  assert.match(full, /is-liked" data-rate-id="tt1" data-rate-value="4" aria-pressed="true" title="Aimé \(cliquer pour retirer\)"/);
  assert.match(full, /#thumb-up-fill/);
  assert.match(full, /#thumb-down"/);
  assert.match(full, /<span>Pas aimé<\/span>/);
  const compact = String(context.ratingGesturesHtml('tt1', '', { labels: false }));
  assert.match(compact, /is-compact/);
  assert.match(compact, /aria-label="Adoré"/);
  assert.doesNotMatch(compact, /<span>/);
  assert.match(String(context.ficheRatingHtml('tt1', heat(), '3')), /Votre avis<\/span><span class="detail-chip">★ 3\/5<\/span>/);
  assert.match(String(context.ficheRatingHtml('tt1', null, '4')), /★ 4\/5 · Ma note/, 'a Ma Liste title out of the history keeps the chip');
  assert.equal(String(context.ficheRatingHtml('tt1', null, '')), '');
});

test('the Home row shows the titles of the tab, and hides when all are rated', () => {
  const page = load({ history: [heat(), heat({ id: 'tt1', name: 'Alien' }), lupin({ next_season: 3 })] });
  page.context.renderHomeRate();
  const row = page.el('homeRateRow').innerHTML;
  assert.equal(page.el('homeRateSection').hidden, false);
  assert.equal(page.el('homeRateCount').textContent, '2 titres · un clic par titre suffit');
  assert.match(row, /<poster tt0113277 https:\/\/p\/heat.jpg>/);
  assert.match(row, /data-rate-item="tt1"/);
  assert.doesNotMatch(row, /tt2531336/, 'series wait on the Séries tab');
  assert.match(row, />Passer<\/button>/);
  const done = load({ history: [heat({ user_rating: '4' })] });
  done.context.renderHomeRate();
  assert.equal(done.el('homeRateSection').hidden, true);
  const elsewhere = load({ history: [heat()], tab: 'watchlist' });
  elsewhere.context.renderHomeRate();
  assert.equal(elsewhere.el('homeRateSection').hidden, true);
});

test('closing the player after a film asks « Vous avez aimé … ? » once', async () => {
  const entry = heat();
  const page = load({ history: [entry] });
  await page.context.askRatingAfterPlayback('tt0113277');
  assert.equal(page.el('ratePrompt').hidden, false);
  assert.equal(page.el('ratePromptTitle').textContent, 'Vous avez aimé Heat ?');
  assert.equal(page.el('ratePromptKicker').textContent, 'Film terminé');
  assert.match(page.el('ratePromptGestures').innerHTML, /<span>Adoré<\/span>/);
  assert.deepEqual(page.calls.at(-1), ['/api/rating', { id: 'tt0113277', asked: 'film' }]);
  page.context.closeRatePrompt();
  assert.equal(page.el('ratePrompt').hidden, true);
  page.el('ratePrompt').hidden = true;
  await page.context.askRatingAfterPlayback('tt0113277');
  assert.equal(page.el('ratePrompt').hidden, true, 'asked already');
});

test('a season finale asks too; nothing while a new playback runs', async () => {
  const page = load({ history: [lupin({ next_season: 3, next_episode: 1 })] });
  await page.context.askRatingAfterPlayback('tt2531336');
  assert.equal(page.el('ratePromptKicker').textContent, 'Saison 2 terminée');
  const busy = load({ history: [heat()], playing: true });
  await busy.context.askRatingAfterPlayback('tt0113277');
  assert.equal(busy.el('ratePrompt').hidden, true);
  assert.equal(busy.calls.length, 0);
  const halfway = load({ history: [heat({ completed: false, progress_pct: 20 })] });
  await halfway.context.askRatingAfterPlayback('tt0113277');
  assert.equal(halfway.el('ratePrompt').hidden, true);
});

test('a failed save says so and lets the user try again', async () => {
  const page = load({ history: [heat()], fail: true });
  const status = { textContent: '' };
  const buttons = [{ disabled: false }, { disabled: false }, { disabled: false }];
  const group = { querySelectorAll: () => buttons };
  const prompt = { querySelector: () => status };
  const button = { dataset: { rateId: 'tt0113277', rateValue: '5' }, closest: s => (s === '.rate-prompt' ? prompt : s === '.rate-gestures' ? group : null) };
  await page.context.rateFromButton(null, button);
  assert.equal(status.textContent, 'Note non enregistrée. Réessayez.');
  assert.ok(buttons.every(b => !b.disabled));
});

test('the page holds the row, the card, and the players ask when they close', () => {
  const rate = html.indexOf('id="homeRateSection"');
  assert.ok(rate > 0 && rate < html.indexOf('id="homeForYouSection"'), 'just before « Pour vous »');
  assert.match(html, /id="ratePrompt" class="rate-prompt" role="region"[^>]*hidden/);
  assert.match(html, /<script src="\/js\/ratings\.js"><\/script>/);
  const player = read('web/js/app-player.js');
  const close = player.slice(player.indexOf('function closeInAppPlayer()'));
  assert.ok(close.indexOf("const finishedId = inAppCurrentMedia?.id || '';") < close.indexOf('askRatingAfterPlayback(finishedId)'));
  const native = read('web/js/native-player.js');
  assert.match(native, /const finishedId = session\.media\?\.id \|\| '';\n    finishSession\(session\.last\);/);
  assert.match(native, /askRatingAfterPlayback\(finishedId\)/);
  assert.match(read('web/js/app-fiche.js'), /\$\{ficheRatingHtml\(media\.id, histItem, userRating\)\}/);
  assert.match(read('web/js/app-tabs.js'), /ratingBadges\.push\(ratingBadgeHtml\(userRating\)\)/);
  assert.match(read('web/js/app-home.js'), /'homeRateSection', 'homeForYouSection'/, 'hidden without internet');
  for (const icon of ['thumb-up', 'thumb-up-fill', 'thumb-down', 'thumb-down-fill', 'heart', 'heart-fill']) {
    assert.match(read('web/icons.svg'), new RegExp(`<symbol id="${icon}"`));
  }
});

test('the hero logo no longer fails once the fiche is gone (journal, 2026-11-12)', () => {
  assert.match(read('web/js/actions.js'), /element\.closest\('\.detail-hero'\)\?\.classList\.add\('has-logo'\)/);
});
