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
  const listeners = {};
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
      addEventListener: (type, fn) => { listeners[type] = fn; },
    },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'web/js/i18n.js'), 'utf8'), context);
  for (const file of written) vm.runInContext(fs.readFileSync(path.join(ROOT, `web/js/i18n/${file}.js`), 'utf8'), context);
  return { I18N: context.KINO_I18N, written, listeners };
}

test('the page loads the French texts first, then English, then its language', () => {
  assert.deepEqual(page('ja').written, ['fr', 'en', 'ja']);
  assert.deepEqual(page('en').written, ['fr', 'en']);
  assert.deepEqual(page('fr').written, ['fr']);
});

test('a French text shows in the language through its key, variable parts included', () => {
  const en = i18n.readLanguage('en');
  const plain = Object.keys(en).find(key => !source[key].includes('{}'));
  assert.equal(page('en').I18N.translate(source[plain]), en[plain]);
  const key = Object.keys(en).find(k => /^\{\} \p{L}+$/u.test(source[k]) && /^\{\} /.test(en[k]));
  assert.ok(key, 'a text with a variable part');
  assert.equal(page('en').I18N.translate(source[key].replace('{}', '12')), en[key].replace('{}', '12'));
  assert.equal(page('fr').I18N.translate(source[plain]), source[plain], 'French stays French');
});

test('t(key) gives the text in the language, else in English, else in French, with its values', () => {
  const en = i18n.readLanguage('en');
  const ja = i18n.readLanguage('ja');
  const both = Object.keys(ja).find(key => en[key] && ja[key] !== en[key] && !source[key].includes('{}'));
  assert.equal(page('ja').I18N.t(both), ja[both]);
  assert.equal(page('fr').I18N.t(both), source[both]);
  const neither = Object.keys(source).find(key => !Object.hasOwn(en, key) && !Object.hasOwn(ja, key) && !source[key].includes('{}'));
  assert.equal(page('ja').I18N.t(neither), source[neither], 'what no dictionary has shows in French');
  // English stands in for what the language lacks.
  const de = i18n.readLanguage('de');
  const englishOnly = Object.keys(en).find(key => !Object.hasOwn(de, key) && !source[key].includes('{}'));
  assert.ok(englishOnly, 'German lacks a text English has');
  assert.equal(page('de').I18N.t(englishOnly), en[englishOnly], 'what German lacks shows in English');
  const counted = Object.keys(en).find(key => /^\{\} \p{L}+$/u.test(source[key]) && /^\{\} /.test(en[key]));
  assert.equal(page('en').I18N.t(counted, 12), en[counted].replace('{}', '12'));
  assert.throws(() => page('en').I18N.t('page.no_such_text'), /unknown text key « page\.no_such_text »/);
});

test('data-i18n gives an element its text and attributes, while they hold its French', () => {
  const en = i18n.readLanguage('en');
  const key = Object.keys(en).find(k => !source[k].includes('{}') && en[k] !== source[k]);
  const el = (dataset, text, attributes) => ({
    nodeType: 1, dataset, textContent: text, attributes: { ...attributes },
    getAttribute(n) { return Object.hasOwn(this.attributes, n) ? this.attributes[n] : null; },
    setAttribute(n, v) { this.attributes[n] = v; },
  });
  const french = source[key];
  const button = el({ i18n: key, i18nTitle: key }, french, { title: french });
  const field = el({ i18nPlaceholder: key, i18nAriaLabel: key }, 'avant', { placeholder: french, 'aria-label': french });
  const written = el({ i18n: key }, 'Un texte écrit par le code', {});
  const root = { nodeType: 9, querySelectorAll: () => [button, field, written] };
  page('en').I18N.apply(root);
  assert.equal(button.textContent, en[key]);
  assert.deepEqual(button.attributes, { title: en[key] });
  assert.deepEqual(field.attributes, { placeholder: en[key], 'aria-label': en[key] });
  assert.equal(field.textContent, 'avant', 'no data-i18n: its text is left');
  assert.equal(written.textContent, 'Un texte écrit par le code', 'what the code wrote is left to the observer');
  page('en').I18N.apply(root);
  assert.equal(button.textContent, en[key], 'applied twice: still the translation');
});

test('the observer counts the French texts it still translates', () => {
  const en = page('en');
  assert.equal(en.I18N.remaining().length, 0, 'nothing shown yet');
});

test('every key the code names exists', () => {
  const dir = path.join(ROOT, 'web/js');
  const texts = [fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8'),
    ...fs.readdirSync(dir).filter(f => f.endsWith('.js')).map(f => fs.readFileSync(path.join(dir, f), 'utf8'))];
  const named = new Set();
  for (const text of texts) {
    for (const m of text.matchAll(/KINO_I18N\.t\(\s*'([^']+)'/g)) named.add(m[1]);
    for (const m of text.matchAll(/\bdata-i18n(?:-title|-placeholder|-aria-label)?="([^"$]+)"/g)) named.add(m[1]);
  }
  assert.deepEqual([...named].filter(key => !Object.hasOwn(source, key)), []);
});

test('a text no dictionary has as such still takes a rule with a variable part', () => {
  const ja = page('ja').I18N;
  const en = i18n.readLanguage('en');
  const jaTable = i18n.readLanguage('ja');
  const ruled = Object.keys(source).find(key => !Object.hasOwn(jaTable, key) && !Object.hasOwn(en, key)
    && !source[key].includes('{}') && ja.translate(source[key]) !== source[key]);
  assert.ok(ruled, 'a text only a rule translates');
  assert.equal(ja.t(ruled), ja.translate(source[ruled]));
});

test('in the page, a keyed element holds the French of its key', () => {
  const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
  const decode = text => text.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  const norm = text => decode(text).split(/\s+/).filter(Boolean).join(' ');
  let keyed = 0;
  for (const m of html.matchAll(/<([a-z][\w-]*)\b([^<>]*?) data-i18n="([^"]+)"[^<>]*>([^<>]*)<\/\1>/g)) {
    assert.equal(norm(m[4]), source[m[3]], m[3]);
    keyed += 1;
  }
  for (const m of html.matchAll(/\s(title|placeholder|aria-label)="([^"]*)"[^<>]*?\sdata-i18n-\1="([^"]+)"/g)) {
    assert.equal(norm(m[2]), source[m[3]], m[3]);
    keyed += 1;
  }
  assert.ok(keyed > 300, `${keyed} keyed texts`);
});
