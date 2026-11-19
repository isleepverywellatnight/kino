// The translations: each French text of the interface has a key (web/js/i18n/fr.js),
// each language's dictionary gives the translation of keys that exist, with the
// same variable parts, and web/js/i18n.js puts them together. See tools/i18n.js.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const i18n = require('../tools/i18n.js');

const ROOT = path.join(__dirname, '..');
const source = i18n.readSource();
const languages = i18n.languages();

test('nine languages: French gives the keys, eight dictionaries translate them', () => {
  assert.deepEqual(languages, ['de', 'en', 'es', 'it', 'ja', 'pt', 'ru', 'zh']);
  assert.ok(Object.keys(source).length > 2000);
  assert.equal(fs.existsSync(path.join(ROOT, 'web/js/i18n/strings.json')), false, 'fr.js is the list of texts');
});

test('a key names one French text, and reads as where it is and what it says', () => {
  const seen = new Map();
  for (const [key, text] of Object.entries(source)) {
    assert.match(key, /^[a-z0-9]+\.[a-z0-9_]+$/, key);
    assert.ok(text.trim(), key);
    assert.ok(!seen.has(text), `« ${text} » has two keys: ${seen.get(text)} and ${key}`);
    seen.set(text, key);
  }
});

test('a dictionary translates only keys that exist, using no variable part the French lacks', () => {
  const parts = text => {
    const all = [...text.matchAll(/\{(\d*)\}/g)].map(m => m[1]);
    return all.every(n => n === '') ? all.length : Math.max(...all.map(Number));
  };
  for (const lang of languages) {
    const table = i18n.readLanguage(lang);
    for (const [key, translation] of Object.entries(table)) {
      assert.ok(Object.hasOwn(source, key), `${lang}: unknown key ${key}`);
      assert.equal(typeof translation, 'string', `${lang}: ${key}`);
      // Fewer is fine: « épisode{} » gives its plural ending, German says « Folge(n) ».
      assert.ok(parts(translation) <= parts(source[key]), `${lang}: ${key} « ${translation} » / « ${source[key]} »`);
    }
  }
});

test('every French text of the code has its key (node tools/i18n.js extract adds the new ones)', () => {
  const known = new Set(Object.values(source));
  const missing = i18n.extract().filter(text => !known.has(text));
  assert.deepEqual(missing, []);
});

test('a key gives its area and its words; a second text with the same words gets a number', () => {
  const taken = new Set();
  assert.equal(i18n.keyFor('Aucun synopsis disponible.', 'fiche', taken), 'fiche.aucun_synopsis_disponible');
  assert.equal(i18n.keyFor('Aucun synopsis disponible !', 'fiche', taken), 'fiche.aucun_synopsis_disponible_2');
  assert.equal(i18n.keyFor('{} épisodes vus', 'stats', taken), 'stats.x_episodes_vus');
  assert.equal(i18n.keyFor('✓', 'page', taken), 'page.symbol');
  assert.equal(i18n.areaOf('web/js/app-player.js'), 'player');
  assert.equal(i18n.areaOf('crates/kino/src/routes/play.rs'), 'server');
});

// i18n.js with fr.js and a dictionary, as the page loads them.
function page(lang) {
  const written = [];
  const context = {
    localStorage: { getItem: () => lang, setItem() {} },
    navigator: { languages: [lang] },
    location: { reload() {} },
    Intl,
    Node: { TEXT_NODE: 3, ELEMENT_NODE: 1 },
    NodeFilter: { SHOW_ELEMENT: 1, SHOW_TEXT: 4, FILTER_ACCEPT: 1, FILTER_REJECT: 2 },
    MutationObserver: class { observe() {} },
    document: {
      documentElement: { nodeType: 1, nodeName: 'HTML', lang: '', getAttribute: () => null },
      querySelector: () => null,
      createTreeWalker: () => ({ nextNode: () => null }),
      write: html => written.push(/src="\/js\/i18n\/(\w+)\.js"/.exec(html)[1]),
    },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'web/js/i18n.js'), 'utf8'), context);
  for (const file of written) vm.runInContext(fs.readFileSync(path.join(ROOT, `web/js/i18n/${file}.js`), 'utf8'), context);
  return { I18N: context.KINO_I18N, written };
}

test('the page loads the French texts first, then English, then its language', () => {
  assert.deepEqual(page('ja').written, ['fr', 'en', 'ja']);
  assert.deepEqual(page('en').written, ['fr', 'en']);
  assert.deepEqual(page('fr').written, ['fr']);
});

test('a French text shows in the language through its key, variable parts included', () => {
  const en = i18n.readLanguage('en');
  const plain = Object.keys(en).find(key => !source[key].includes('{}'));
  assert.equal(page('en').I18N.t(source[plain]), en[plain]);
  const key = Object.keys(en).find(k => /^\{\} \p{L}+$/u.test(source[k]) && /^\{\} /.test(en[k]));
  assert.ok(key, 'a text with a variable part');
  assert.equal(page('en').I18N.t(source[key].replace('{}', '12')), en[key].replace('{}', '12'));
  assert.equal(page('fr').I18N.t(source[plain]), source[plain], 'French stays French');
});
