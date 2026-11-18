// The interface's main code is split by theme (docs/interface-files.md). Its
// parts, in the order web/index.html loads them, join back into one text:
// tests read that text, so a part can move between files without them noticing.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');

function appParts(root = ROOT) {
  const html = fs.readFileSync(path.join(root, 'web', 'index.html'), 'utf8');
  return [...html.matchAll(/<script src="\/js\/(app-[\w-]+\.js)"><\/script>/g)].map(match => match[1]);
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

function appSource(root = ROOT) {
  return inFrench(appParts(root).map(name => fs.readFileSync(path.join(root, 'web', 'js', name), 'utf8')).join(''));
}

// Every interface script, in loading order (app parts, helpers, players).
function interfaceScripts(root = ROOT) {
  const html = fs.readFileSync(path.join(root, 'web', 'index.html'), 'utf8');
  return [...html.matchAll(/<script src="\/js\/([\w-]+\.js)"><\/script>/g)].map(match => match[1]);
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

function withCalls(html) {
  return html.replace(/ data-on-([a-z]+)="([^"]+)"(?: data-\1-args='([^']*)')?( data-\1-stop)?/g, (_, type, name, args, stop) => {
    const call = asCall(name, args ? JSON.parse(args) : []);
    return ` on${type}="${(stop ? 'event.stopPropagation(); ' : '') + call.replace(/"/g, '&quot;')}"`;
  });
}

function indexHtml(root = ROOT) {
  return withCalls(fs.readFileSync(path.join(root, 'web', 'index.html'), 'utf8'));
}

module.exports = { appParts, appSource, interfaceScripts, inFrench, FRENCH_MONTHS, indexHtml, withCalls };
