// « Préparer en un clic » in the page: the preparation shows with its progress
// and two choices, is followed while the window shows, then plays the prepared
// torrent. The real interface scripts run on a stand-in page.
const assert = require('node:assert/strict');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const { loadPage, settle } = require('../tools/page_harness.js');

const source = require('../tools/interface_source.js').appSource();
const KEY = '0123456789abcdef0123456789abcdef';
const PREPARING = { preparing: true, torrent_id: 'RD123', progress: 12, status: 'downloading', speed: '8 MB/s', seeders: 40,
  source_key: KEY, chosen_torrent: 'Torrentio Film.2160p', ready_alternative: true };
const PLAYED = { ok: true, source_key: KEY, debrid: { ready: true, files: [] }, stream_url: '', playlist: [], mpv: { mode: 'integrated' } };

async function start(statuses) {
  const bodies = [];
  const polls = [];
  const page = await loadPage((url, options = {}) => {
    if (url === '/api/one-click-play') {
      const body = JSON.parse(options.body);
      bodies.push(body);
      return body.prepared || body.prefer_ready ? PLAYED : PREPARING;
    }
    if (url === '/api/debrid-status') {
      polls.push(JSON.parse(options.body));
      return statuses.shift() || { ready: false, progress: 99 };
    }
    return {};
  });
  return { page, bodies, polls, box: () => page.element('debridResultPanel').innerHTML };
}

const params = () => ({ imdb_id: 'tt1', type: 'movie', title: 'Dune : Deuxième partie', player_mode: 'integrated' });

test('the preparation shows its progress and the two choices', async () => {
  const run = await start([]);
  run.page.context.document.hidden = true;  // no polling yet
  await run.page.context.oneClickPlay(params());
  const html = run.box();
  assert.match(html, /<h3>Préparation de la qualité choisie : 12 %<\/h3>/);
  assert.match(html, /Dune : Deuxième partie : Debrid télécharge la source recommandée, la lecture démarre dès qu’elle est prête\./);
  assert.match(html, /Statut : downloading • Vitesse : 8 MB\/s • Pairs : 40/);
  assert.match(html, /<div class="progress-fill" style="width:12%">/);
  assert.match(withCalls(html), /onclick="playReadyInstead\(\)">Lancer une source prête à la place<\/button>[\s\S]*onclick="stopWaitingPreparation\(\)">Arrêter d’attendre<\/button>/);
  assert.doesNotMatch(html, /Film\.2160p/, 'no release name, which could name an episode');
});

test('KINO follows the preparation, then plays the prepared torrent', async () => {
  const run = await start([{ ready: false, progress: 55, status: 'downloading', speed: '9 MB/s', seeders: 41 }, { ready: true }]);
  await run.page.context.oneClickPlay(params());
  await settle(12);
  assert.deepEqual(run.polls[0], { torrent_id: 'RD123' });
  assert.equal(run.polls.length, 2);
  assert.deepEqual(run.bodies.map(body => body.prepared || null), [null, { torrent_id: 'RD123', source_key: KEY }]);
  assert.equal(vm.runInContext('oneClickPreparation', run.page.context), null);
});

test('a hidden window does not poll; it waits to show again', async () => {
  const run = await start([]);
  run.page.context.document.hidden = true;
  await run.page.context.oneClickPlay(params());
  await settle(8);
  assert.equal(run.polls.length, 0);
  assert.equal(vm.runInContext('oneClickPreparation.waitingForWindow', run.page.context), true);
});

test('« Lancer une source prête à la place » asks for a ready source at once', async () => {
  const run = await start([]);
  run.page.context.document.hidden = true;
  await run.page.context.oneClickPlay(params());
  await run.page.context.playReadyInstead();
  await settle();
  assert.equal(run.bodies.length, 2);
  assert.equal(run.bodies[1].prefer_ready, true);
  assert.equal(run.bodies[1].prepared, undefined);
});

test('« Arrêter d’attendre » says the preparation goes on, and stops following it', async () => {
  const run = await start([]);
  await run.page.context.oneClickPlay(params());
  run.page.context.stopWaitingPreparation();
  const polled = run.polls.length;
  await settle(8);
  assert.equal(run.polls.length, polled);
  assert.match(run.box(), /Vous n’attendez plus : Debrid continue la préparation, et la source sera prête plus tard dans votre Cloud\./);
});

test('a playback recovery never waits for a preparation', () => {
  assert.match(source, /body: JSON\.stringify\(\{ \.\.\.recovery\.params, prepared: undefined, prefer_ready: true, exclude_source_keys: \[\.\.\.recovery\.excluded\] \}\)/);
});
