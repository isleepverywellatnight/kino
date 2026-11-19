// The interface's main code is split by theme (docs/interface-files.md). Its
// parts, in the order web/js/main.js imports them, join back into one text:
// tests read that text, so a part can move between files without them noticing.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');

// The parts are ES modules. On one page every top-level name is unique, and
// TypeScript (jsconfig.json) makes sure each module imports what it uses; so tests
// run the parts as one script, without their import and export words. Each
// line keeps its number.
function asScript(text) {
  return String(text)
    .replace(/^import (?:[^;'"]*? from )?'[^']+';\n/gm, statement => '\n'.repeat(statement.split('\n').length - 1))
    .replace(/^export (?=(?:async )?function\b|const\b|let\b|var\b|class\b)/gm, '');
}

// One file of web/js, as a script (asScript).
function scriptText(name, root = ROOT) {
  return asScript(fs.readFileSync(path.join(root, 'web', 'js', name), 'utf8'));
}

function appParts(root = ROOT) {
  return interfaceScripts(root).filter(name => /^app-[\w-]+\.js$/.test(name));
}

// The interface is written in French; web/js/i18n.js translates it as it shows.
// Tests run the code without that script: the month names it would give are
// French, as the interface's own.
const FRENCH_MONTHS = {
  long: ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'],
  short: ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'],
  narrow: 'JFMAMJJASOND'.split(''),
};

function inFrench(text) {
  return text.replace(/KINO_I18N\.months\('(long|short|narrow)'\)/g, (_, kind) => JSON.stringify(FRENCH_MONTHS[kind]))
    .replace(/window\.KINO_LANG\b/g, "'fr'").replace(/window\.KINO_LOCALE\b/g, "'fr-FR'");
}

// Each file ends with the list of actions it gives the markup (registerActions({ … })):
// names only, checked by tools/frontend_check.js. Tests that cut a file's text up to
// its end leave the list out, so that it does not name functions they did not take.
const REGISTRATION = /\n\/\/ What the markup may run from this file \(web\/js\/actions\.js\)\.\nregisterActions\(\{\n[\s\S]*?\n\}\);\n/g;
function withoutRegistrations(text) {
  return text.replace(REGISTRATION, '\n');
}

function appSource(root = ROOT) {
  return inFrench(withoutRegistrations(appParts(root).map(name => scriptText(name, root)).join('')));
}

// Every interface script, in loading order: the page's own classic scripts
// (i18n.js), then the modules web/js/main.js imports, in its order, and main.js.
function interfaceScripts(root = ROOT) {
  const html = fs.readFileSync(path.join(root, 'web', 'index.html'), 'utf8');
  const names = [];
  for (const match of html.matchAll(/<script (type="module" )?src="\/js\/([\w-]+\.js)"><\/script>/g)) {
    if (!match[1]) { names.push(match[2]); continue; }
    const entry = fs.readFileSync(path.join(root, 'web', 'js', match[2]), 'utf8');
    for (const im of entry.matchAll(/^import (?:[^;'"]*? from )?'\.\/([\w-]+\.js)';$/gm)) names.push(im[1]);
    names.push(match[2]);
  }
  return names;
}

// index.html declares what its controls do as data attributes read by
// web/js/actions.js (data-on-click="switchTab" data-click-args='["movies", {"$": "this"}]').
// Tests read each one as the call it makes: onclick="switchTab('movies', this)".
const TOKENS = { this: 'this', event: 'event', value: 'this.value', checked: 'this.checked', files: 'this.files' };
function asCall(name, args) {
  const js = arg => {
    if (arg && typeof arg === 'object' && !Array.isArray(arg) && '$' in arg) return TOKENS[arg.$];
    if (typeof arg === 'string') return `'${arg.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
    if (arg && typeof arg === 'object') return `{ ${Object.entries(arg).map(([k, v]) => `${k}: ${js(v)}`).join(', ')} }`;
    return String(arg);
  };
  return `${name}(${args.map(js).join(', ')})`;
}

const unescape = text => text.replaceAll('&quot;', '"').replaceAll('&#39;', "'").replaceAll('&lt;', '<')
  .replaceAll('&gt;', '>').replaceAll('&amp;', '&');

// Both forms: index.html's single-quoted JSON, the templates' escaped one.
function withCalls(html) {
  return String(html).replace(/ data-on-([a-z]+)="([^"]+)"(?: data-\1-args=(?:'([^']*)'|"([^"]*)"))?( data-\1-stop)?/g, (_, type, name, single, double, stop) => {
    const raw = single ?? (double === undefined ? undefined : unescape(double));
    const call = asCall(unescape(name), raw ? JSON.parse(raw) : []);
    return ` on${type}="${(stop ? 'event.stopPropagation(); ' : '') + call.replace(/"/g, '&quot;')}"`;
  });
}

// The page's text keys (data-i18n="page.parametres"…) name the translation of
// the French beside them; tests/test_i18n_catalogs.js checks them. Other tests
// read the page without them.
function withoutKeys(html) {
  return String(html).replace(/ data-i18n(?:-title|-placeholder|-aria-label)?="[^"]*"/g, '');
}

function indexHtml(root = ROOT) {
  return withCalls(withoutKeys(fs.readFileSync(path.join(root, 'web', 'index.html'), 'utf8')));
}

// What templates use to declare an action (act, actOn, THIS, EVENT…), taken from
// the page itself, for tests that run a template alone.
// One set for the whole test run: a fragment made in one context is recognised in another.
let helpersOnce = null;
function templateHelpers(root = ROOT) {
  if (helpersOnce) return helpersOnce;
  const code = scriptText('html.js', root);
  const context = {};
  require('node:vm').runInNewContext(`${code}; this.out = { act, actOn, THIS, EVENT, VALUE, CHECKED, escapeHtml, escapeText, attrJson, html, raw, render, replaceWith, renderAdjacent, safeUrl, SafeHtml };`, context);
  helpersOnce = context.out;
  return helpersOnce;
}

// A test's context with those helpers, unless it brings its own. A file run alone
// registers its actions (web/js/actions.js) into the context's own list,
// context.actions; tools/frontend_check.js checks that every declared one is there.
// KINO_I18N in French, as web/js/i18n.js gives it with web/js/i18n/fr.js: t(key)
// is the key's French, « {} » filled with the values that follow.
let frenchOnce = null;
function frenchTexts(root = ROOT) {
  if (frenchOnce) return frenchOnce;
  const raw = fs.readFileSync(path.join(root, 'web', 'js', 'i18n', 'fr.js'), 'utf8');
  frenchOnce = JSON.parse(raw.slice(raw.indexOf('KINO_I18N.source(') + 'KINO_I18N.source('.length, raw.lastIndexOf(')')));
  return frenchOnce;
}
function frenchI18n() {
  const texts = frenchTexts();
  const fill = (text, values) => {
    let next = 0;
    return text.replace(/\{(\d*)\}/g, (_, index) => {
      const value = index ? values[Number(index) - 1] : values[next++];
      return value === undefined ? '' : String(value);
    });
  };
  return {
    lang: 'fr',
    t(key, ...values) {
      if (!Object.hasOwn(texts, key)) throw new Error(`KINO: unknown text key « ${key} »`);
      return fill(texts[key], values);
    },
    translate: text => text,
    months: style => FRENCH_MONTHS[style].slice(),
    apply() {},
    remaining: () => [],
  };
}

function withHelpers(context = {}) {
  for (const [name, value] of Object.entries(templateHelpers())) if (!(name in context)) context[name] = value;
  if (!('KINO_I18N' in context)) context.KINO_I18N = frenchI18n();
  if (!('registerActions' in context)) {
    const actions = {};
    Object.defineProperty(context, 'actions', { value: actions, enumerable: false, configurable: true });
    context.registerActions = handlers => { Object.assign(actions, handlers); };
  }
  return context;
}

module.exports = { frenchTexts, frenchI18n, appParts, appSource, asScript, scriptText, withoutRegistrations, withoutKeys, interfaceScripts, inFrench, FRENCH_MONTHS, indexHtml, withCalls, templateHelpers, withHelpers };
