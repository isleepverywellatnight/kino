// A hidden or minimised KINO window does no periodic work.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const hero = 'function renderHeroSpotlight(metas, fallbackType)' + source
  .split('function renderHeroSpotlight(metas, fallbackType)', 2)[1]
  .split('function setHeroSpotlightIdx(idx)', 1)[0];

test('the hero banner does not rotate while the window is hidden', () => {
  let tick = null;
  let drawn = 0;
  const box = { style: {} };
  const context = {
    activeTab: 'movies', userHistory: [], heroSpotlightItems: [], heroSpotlightIdx: 0, heroSpotlightTimer: null,
    document: {
      hidden: true,
      getElementById: id => (id === 'heroSpotlight' ? box : { classList: { contains: () => false } }),
    },
    clearInterval: () => {}, setInterval: callback => { tick = callback; return 1; },
    drawHeroSpotlightSlide: () => { drawn += 1; },
  };
  vm.runInNewContext(`${hero}; renderHeroSpotlight([{id: 'tt1'}, {id: 'tt2'}, {id: 'tt3'}], 'movie');`, context);
  assert.equal(drawn, 1, 'first slide drawn on render');
  tick(); tick();
  assert.equal(drawn, 1);
  assert.equal(context.heroSpotlightIdx, 0);
  context.document.hidden = false;
  tick();
  assert.equal(drawn, 2);
  assert.equal(context.heroSpotlightIdx, 1);
});

test('window state polling skips hidden windows', () => {
  assert.match(source, /setInterval\(\(\) => \{ if \(!document\.hidden\) syncWindowState\(\); \}, 1500\);/);
  assert.match(source, /addEventListener\('visibilitychange', \(\) => \{ if \(!document\.hidden\) syncWindowState\(\); \}\)/);
});
