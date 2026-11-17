const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const tonightCode = source.slice(source.indexOf('let tonightChoices = [];'), source.indexOf('function isInWatchlist(id)'));

test('Ce soir shows three distinct feedback actions and escapes media titles', () => {
  const elements = Object.fromEntries(['tonightChoices', 'tonightStatus', 'tonightReview', 'tonightUndo'].map(id => [id, {
    innerHTML: '', textContent: '', style: {},
  }]));
  const context = {
    document: {getElementById: id => elements[id] || null},
    navigator: {onLine: true},
    escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;'),
  };
  vm.runInNewContext(`${tonightCode}; renderTonightResult({choices:[{item:{id:'tt1', name:'<film>'}, reasons:['80 min'], offline_id:null}], review:[], coverage:{total:1,partial:false}});`, context);
  assert.match(elements.tonightChoices.innerHTML, /Pas ce soir/);
  assert.match(elements.tonightChoices.innerHTML, /Déjà vu/);
  assert.match(elements.tonightChoices.innerHTML, /Ça ne m’intéresse pas/);
  assert.match(elements.tonightChoices.innerHTML, /&lt;film&gt;/);
  assert.doesNotMatch(elements.tonightChoices.innerHTML, /<film>/);
});
