const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const offlineFunctions = 'function offlineFreeText(bytes)' + source
  .split('function offlineFreeText(bytes)', 2)[1]
  .split('async function playOfflineDownload(', 1)[0];
const scrobbleFunction = 'async function scrobbleTrakt(action, media, progressPct)' + source
  .split('async function scrobbleTrakt(action, media, progressPct)', 2)[1]
  .split('// --- KINO Remote Smartphone ---', 1)[0];

test('incomplete local files never offer to start playback in the offline library', () => {
  const grid = {innerHTML: ''};
  const badge = {textContent: ''};
  const context = {
    activeTab: 'offline',
    networkOnline: true,
    document: {getElementById: id => id === 'postersGrid' ? grid : id === 'offlineCount' ? badge : null},
      };
  vm.runInNewContext(`${offlineFunctions}; renderOfflineLibraryData({downloads: {
    abc: {id:'abc', filename:'<script>.mp4', title:'Test <script>', status:'interrupted',
      error_code:'app_closed', bytes_done:42, bytes_total:100, progress:42, available:false, media:{}}
  }, used_bytes:42});`, context);
  assert.doesNotMatch(grid.innerHTML, />(Play|Lancer)</);
  assert.match(grid.innerHTML, />Reprendre</);
  assert.match(grid.innerHTML, />Recommencer</);
  assert.match(grid.innerHTML, /Test &lt;script&gt;/);
  assert.equal(badge.textContent, '');
});

test('an unidentified offline file is not sent to Trakt; identified media uses the expected payload', async () => {
  const requests = [];
  const context = {
    document: {getElementById: () => null},
    api: async (route, options) => { requests.push({route, body: JSON.parse(options.body)}); return {status: 'ok'}; },
  };
  vm.createContext(context);
  vm.runInContext(scrobbleFunction, context);
  assert.equal(await vm.runInContext("scrobbleTrakt('stop', {name:'Local test'}, 100)", context), false);
  assert.equal(requests.length, 0);
  assert.equal(await vm.runInContext("scrobbleTrakt('stop', {id:'tt1234567', name:'Film'}, 80)", context), true);
  assert.equal(requests[0].body.media.imdb_id, 'tt1234567');
  assert.equal(requests[0].body.media.title, 'Film');
  assert.equal(requests[0].body.progress, 80);
});

test('the offline summary shows the free space left for downloads', () => {
  const grid = {innerHTML: ''};
  const context = {
    activeTab: 'offline',
    networkOnline: true,
    document: {getElementById: id => id === 'postersGrid' ? grid : null},
      };
  vm.runInNewContext(`${offlineFunctions}; renderOfflineLibraryData({downloads: {}, used_bytes: 21.44 * 1024 ** 3, free_bytes: 15 * 1024 ** 3});`, context);
  assert.match(grid.innerHTML, /Utilisé sur cet ordinateur : 21 Go · Libre : 15 Go/);
  vm.runInNewContext(`${offlineFunctions}; renderOfflineLibraryData({downloads: {}, used_bytes: 0, free_bytes: null});`, context);
  assert.doesNotMatch(grid.innerHTML, /Libre/);
  assert.match(grid.innerHTML, /Utilisé sur cet ordinateur : 0 octet/);
});
