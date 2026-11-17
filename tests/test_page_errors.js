// Errors of the page reach KINO's error log: window errors and
// rejected promises, each once a minute, at most 20 a minute.
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const code = source.slice(source.indexOf('// Errors of the page go to KINO\'s error log'), source.indexOf('async function api(path, opts) {'));

function load(now = () => 1000) {
  const listeners = {};
  const sent = [];
  const context = {
    window: { addEventListener: (type, fn) => { listeners[type] = fn; } },
    fetch: (url, options) => { sent.push([url, JSON.parse(options.body)]); return Promise.resolve(); },
    Date: { now }, activeTab: 'movies', String, JSON, Map,
  };
  vm.runInNewContext(code, context);
  return { listeners, sent };
}

test('a window error and a rejected promise are sent with where they happened', () => {
  const page = load();
  page.listeners.error({ message: 'x is undefined', filename: 'http://127.0.0.1:8080/js/app-home.js', lineno: 42, error: { stack: 'at renderHome' } });
  page.listeners.unhandledrejection({ reason: { message: 'Erreur serveur', stack: 'at api' } });
  assert.deepEqual(page.sent.map(([url]) => url), ['/api/client-error', '/api/client-error']);
  assert.deepEqual(page.sent[0][1], { kind: 'Erreur de la page', message: 'x is undefined', source: 'app-home.js:42', stack: 'at renderHome', tab: 'movies' });
  assert.equal(page.sent[1][1].kind, 'Promesse rejetée');
});

test('the same error once a minute, at most 20 a minute, and not a picture that fails', () => {
  let now = 1000;
  const page = load(() => now);
  for (let i = 0; i < 3; i++) page.listeners.error({ message: 'same', filename: 'a.js', lineno: 1 });
  assert.equal(page.sent.length, 1);
  for (let i = 0; i < 40; i++) page.listeners.error({ message: `e${i}`, filename: 'a.js', lineno: 1 });
  assert.equal(page.sent.length, 20);
  page.listeners.error({ target: 'img' });
  assert.equal(page.sent.length, 20, 'a failed image has no message');
  now += 61000;
  page.listeners.error({ message: 'ResizeObserver loop completed with undelivered notifications.' });
  assert.equal(page.sent.length, 20, 'a harmless notice of WebKit is not logged');
  page.listeners.error({ message: 'same', filename: 'a.js', lineno: 1 });
  assert.equal(page.sent.length, 21);
});
