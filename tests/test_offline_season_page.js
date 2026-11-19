// « Saison hors ligne » in the series fiche: the episodes already out, a clear
// confirmation, then the queue shows in Hors ligne.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const seasonCode = slice('// --- A whole season for offline', 'async function playOfflineDownload(id) {');
const sizeCode = slice('function offlineSize(bytes) {', 'function offlineStatus(item) {');
const statusCode = slice('function offlineStatus(item) {', '// What an offline file shows');

const videos = [
  { season: 1, episode: 2, released: '2026-10-08T00:00:00.000Z' }, { season: 1, episode: 1, released: '2026-10-01T00:00:00.000Z' },
  { season: 1, episode: 2, released: '2026-10-08T00:00:00.000Z' }, { season: 1, episode: 3, released: '2026-12-01T00:00:00.000Z' },
  { season: 2, episode: 1, released: '2026-10-01' }, { season: 1, episode: 4, released: '' },
];

function load({ confirmAnswer = true, answer = { queued: 2, duplicates: 0 } } = {}) {
  const calls = [];
  const context = {
    seriesMetaVideos: videos, currentMedia: { id: 'tt1', name: 'The Bear', year: '2022', poster: 'p' },
    document: { getElementById: id => (id === 'seasonSelect' ? { value: '1' } : null) },
    displayTitle: m => m.name, Date,
    window: { confirm: message => { calls.push(['confirm', message]); return confirmAnswer; }, alert: message => calls.push(['alert', message]) },
    api: async (url, options) => {
      calls.push([url, options?.body ? JSON.parse(options.body) : null]);
      return url === '/api/downloads' ? { free_bytes: 50 * 1024 ** 3 } : answer;
    },
    pollDownloads: () => calls.push(['poll']), setTimeout: () => 0,
  };
  vm.runInNewContext(`${sizeCode}\n${statusCode}\n${seasonCode}\nthis.out = seasonEpisodesOut; this.go = downloadSeasonOffline; this.status = offlineStatus;`, context);
  return { context, calls };
}

test('only the episodes already out, once each, in order', () => {
  const { context } = load();
  assert.deepEqual(Array.from(context.out(1, '2026-11-11')), [1, 2]);
  assert.deepEqual(Array.from(context.out(2, '2026-11-11')), [1]);
});

test('a clear confirmation, then the episodes are queued', async () => {
  const { context, calls } = load();
  const button = { textContent: '⤓ Saison hors ligne', disabled: false };
  await context.go(button);
  const asked = calls.find(call => call[0] === 'confirm')[1];
  assert.match(asked, /^Télécharger les 2 épisodes sortis de la saison 1 de « The Bear » pour les regarder hors ligne \?/);
  assert.match(asked, /l’un après l’autre, même si KINO est fermé puis rouvert\. Un épisode pèse souvent 1 à 4 Go\. Il reste 50 Go sur cet ordinateur\./);
  assert.deepEqual(calls.find(call => call[0] === '/api/download-season')[1],
    { imdb_id: 'tt1', season: 1, episodes: [1, 2], name: 'The Bear', year: '2022', poster: 'p' });
  assert.equal(button.textContent, '✓ 2 en file d’attente');
  assert.ok(calls.some(call => call[0] === 'poll'));
});

test('nothing is queued when the user says no', async () => {
  const { context, calls } = load({ confirmAnswer: false });
  await context.go(null);
  assert.equal(calls.some(call => call[0] === '/api/download-season'), false);
});

test('statuses and actions in Hors ligne', () => {
  const { context } = load();
  assert.equal(context.status({ status: 'waiting' }), 'En file d’attente');
  assert.equal(context.status({ status: 'failed', error_code: 'source_unavailable' }), 'Aucune source disponible pour cet épisode');
  assert.match(withCalls(source), /\['queued', 'downloading', 'waiting'\]\.includes\(item\.status\)\n\s+\? html`<button class=\"btn btn-secondary\" \$\{act\('downloadAction', String\(id\), 'pause'\)\}>Pause<\/button>`/);
  assert.match(withCalls(source), /\$\{act\('downloadSeasonOffline', THIS\)\} title="Télécharger les épisodes sortis de cette saison, l’un après l’autre">⤓ Saison hors ligne<\/button>/);
});
