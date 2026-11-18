// A series watched to its last episode leaves "À continuer" and offers to start again.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const helpers = 'function getInProgressHistory()' + source
  .split('function getInProgressHistory()', 2)[1].split('async function refreshUserLists()', 1)[0]
  + 'function getSeriesTargetEpisode(h)' + source
  .split('function getSeriesTargetEpisode(h)', 2)[1].split('function renderHomeResume()', 1)[0];

const finished = { id: 'tt1', type: 'series', season: 2, episode: 2, completed: true, series_finished: true, next_season: 2, next_episode: 3 };
const caughtUpMidSeries = { id: 'tt2', type: 'series', season: 1, episode: 4, completed: true, next_season: 1, next_episode: 5 };
const inProgressFinale = { id: 'tt3', type: 'series', season: 2, episode: 2, progress_pct: 40, series_finished: true };

test('finished series leave the continue row, others stay', () => {
  const context = { userHistory: [finished, caughtUpMidSeries, inProgressFinale] };
  vm.runInNewContext(`${helpers}; result = getInProgressHistory().map(h => h.id);`, context);
  assert.deepEqual(Array.from(context.result), ['tt2', 'tt3']);
});

test('a finished series starts again from the first episode', () => {
  const context = { finished, caughtUpMidSeries };
  vm.runInNewContext(`${helpers}; a = getSeriesTargetEpisode(finished); b = getSeriesTargetEpisode(caughtUpMidSeries);`, context);
  assert.equal(context.a.finished, true);
  assert.deepEqual([context.a.season, context.a.episode, context.a.isNext], [1, 1, false]);
  assert.deepEqual([context.b.season, context.b.episode, context.b.isNext], [1, 5, true]);
  assert.match(source, /btnLabel = targetEp\.finished \? 'Revoir'/);
});
