// « Pour vous » on the home: each suggestion with its reason, the
// titles seen left out, and « Pas pour moi » that takes one out at once.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const code = source.slice(source.indexOf('// « Pour vous »'), source.indexOf('function hideHomeRows() {'));

function load(answer) {
  const elements = {};
  const el = id => elements[id] || (elements[id] = { id, innerHTML: '', hidden: true });
  const requests = [];
  const context = {
    document: { getElementById: el }, homeKind: () => 'movie', onHomeTab: () => true, HOME_SHELF_SIZE: 12,
    getWatchedHistory: () => [{ id: 'tt3' }], posterCardHtml: m => `<card ${m.id}>`, escapeHtml: s => String(s),
    RELATED_CLIENT_CACHE: new Map([['x', 1]]),
    api: async (url, options) => { requests.push([url, options && options.body]); return answer(url); },
  };
  vm.runInNewContext(`${code}\nthis.api_ = { renderHomeForYou, notForMe, FOR_YOU_CACHE };`, context);
  return { ...context.api_, el, requests, context };
}

const items = [1, 2, 3, 4, 5, 6].map(i => ({ id: `tt${i}`, name: `F${i}`, type: 'movie', why: `Parce que vous avez aimé A${i}` }));

test('the row asks once, then shows each poster with its reason, without the titles seen', async () => {
  const page = load(() => ({ ready: true, items }));
  page.renderHomeForYou();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(page.requests.map(r => r[0]), ['/api/for-you?type=movie']);
  const row = page.el('homeForYouRow').innerHTML;
  assert.equal(page.el('homeForYouSection').hidden, false);
  assert.match(row, /<div class="foryou-item" data-foryou="tt1"><card tt1><p class="foryou-why">Parce que vous avez aimé A1<\/p><button type="button" class="foryou-no" onclick='notForMe\(event, "tt1"\)'>Pas pour moi<\/button><\/div>/);
  assert.doesNotMatch(row, /tt3/, 'a title seen since is left out');
});

test('« Pas pour moi » takes it out at once and tells KINO', async () => {
  const page = load(url => (url.startsWith('/api/for-you?') ? { ready: true, items } : { ok: true }));
  page.renderHomeForYou();
  await new Promise(resolve => setImmediate(resolve));
  await page.notForMe(null, 'tt1');
  assert.deepEqual(page.requests[1], ['/api/for-you/not-for-me', '{"id":"tt1"}']);
  assert.equal(page.context.RELATED_CLIENT_CACHE.size, 0, 'the close titles are asked again with it');
});

test('fewer than four suggestions hide the row; the settings say it is computed here', async () => {
  const page = load(() => ({ ready: false, items: [] }));
  page.renderHomeForYou();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.el('homeForYouSection').hidden, true);
  assert.match(html, /<section id="homeForYouSection" class="home-shelf" aria-labelledby="homeForYouTitle" hidden>/);
  assert.match(html, /Rien de vous ne quitte ce Mac\./);
  assert.match(html, /onclick="resetNotForMe\(this\)">Oublier mes « Pas pour moi »<\/button>/);
});
