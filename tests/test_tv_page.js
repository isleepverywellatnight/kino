// « Lire sur la TV » in the page: the button beside « Lancer », what
// is sent to KINO, and the bar that shows what the TV plays.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const tvCode = slice('// --- « Lire sur la TV » (tv_cast.py)', '// --- « Préparer en un clic »');

function load(answer = () => ({ active: true })) {
  const elements = {};
  const el = id => elements[id] || (elements[id] = { id, hidden: true, textContent: '', innerHTML: '', style: {}, attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; } });
  const icon = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
  const calls = [];
  const context = {
    document: { getElementById: el, querySelector: sel => (sel === '#tvBarToggle use' ? icon : null) },
    api: async (url, options) => { calls.push([url, options && JSON.parse(options.body || 'null')]); return answer(url); },
    escapeHtml: s => String(s), currentMedia: { id: 'tt0903747', name: 'Breaking Bad', poster: 'https://p/bb.jpg', type: 'series' },
    setInterval: () => 1, clearInterval: () => {},
  };
  vm.runInNewContext(`${tvCode}\nthis.api_ = { sendToTv, renderTvBar, tvControl };`, context);
  return { ...context.api_, el, icon, calls };
}

test('the bar says where, what and when', () => {
  const page = load();
  page.renderTvBar({ active: true, device: 'Samsung Q60BA 55 TV', title: 'Les Évadés (1994)', paused: false, position: 754, duration: 8520 });
  assert.equal(page.el('tvBar').hidden, false);
  assert.equal(page.el('tvBarTitle').textContent, 'Les Évadés (1994)');
  assert.equal(page.el('tvBarSub').textContent, 'Sur Samsung Q60BA 55 TV · En lecture · 12:34 / 2:22:00');
  assert.equal(page.icon.attrs.href, '/icons.svg#pause');
  page.renderTvBar({ active: true, device: 'TV', title: 'X', paused: true, position: 5, duration: 60, starting: false });
  assert.equal(page.el('tvBarSub').textContent, 'Sur TV · En pause · 0:05 / 1:00');
  assert.equal(page.icon.attrs.href, '/icons.svg#play-solid');
  page.renderTvBar({ active: true, device: 'TV', title: 'X', starting: true });
  assert.equal(page.el('tvBarSub').textContent, 'Sur TV · Démarrage…');
  page.renderTvBar({ active: false });
  assert.equal(page.el('tvBar').hidden, true);
});

test('between two episodes: a countdown, « Lancer maintenant » and « Annuler »', () => {
  const page = load();
  page.renderTvBar({ active: true, device: 'TV', title: 'Lupin — S01E02', paused: true, position: 3000, duration: 3000,
    next: { code: 'S01E03', in: 8, finding: false } });
  assert.equal(page.el('tvBarSub').textContent, 'Épisode suivant · S01E03 dans 8 s');
  assert.equal(page.el('tvBar').attrs['data-next'], 'yes', 'the ±10 s buttons are hidden');
  assert.equal(page.el('tvBarStop').textContent, 'Annuler');
  assert.equal(page.el('tvBarToggle').attrs['aria-label'], 'Lancer l’épisode suivant maintenant');
  page.renderTvBar({ active: true, device: 'TV', title: 'Lupin — S01E02', next: { code: 'S01E03', in: 0, finding: true } });
  assert.equal(page.el('tvBarSub').textContent, 'Épisode suivant · Recherche de S01E03…');
  page.renderTvBar({ active: true, device: 'TV', title: 'Lupin — S01E03', starting: true });
  assert.equal(page.el('tvBar').attrs['data-next'], 'no');
  assert.equal(page.el('tvBarStop').textContent, 'Arrêter');
  page.renderTvBar({ active: true, device: 'TV', title: 'Lupin — S01E02', error: 'S01E03 introuvable ou pas encore sorti.' });
  assert.equal(page.el('tvBarSub').textContent, 'S01E03 introuvable ou pas encore sorti.');
  assert.match(html, /id="tvBarStop" onclick="tvControl\('stop'\)"/);
  assert.equal((html.match(/class="btn btn-secondary btn-small tv-seek"/g) || []).length, 2);
});

test('the source goes to KINO with the episode, not to a player', async () => {
  const page = load();
  await page.sendToTv({ stream_url: 'https://dl/x.mkv', file: { filename: 'BB.S01E02.mkv' }, resume_sec: 300 },
    { title: 'Breaking Bad — S01E02', type: 'series', season: '1', episode: '2', imdb_id: 'tt0903747', name: 'Breaking Bad' }, null);
  const [url, body] = page.calls[0];
  assert.equal(url, '/api/tv/play');
  assert.deepEqual({ ...body, media: { ...body.media } }, {
    stream_url: 'https://dl/x.mkv', title: 'Breaking Bad — S01E02', filename: 'BB.S01E02.mkv', resume_sec: 300,
    media: { id: 'tt0903747', name: 'Breaking Bad', poster: 'https://p/bb.jpg', type: 'series', year: '', season: 1, episode: 2 },
  });
});

test('« Sur la TV » sits beside « Lancer » and shares its source', () => {
  assert.match(source, /id="detailTvBtn" onclick='oneClickCard\(event, this, \$\{mediaPayload\}, \{ target: "tv" \}\)'/);
  assert.match(source, /if \(params\.target === 'tv'\) params\.player_mode = 'integrated';/);
  assert.match(source, /if \(params\.target === 'tv'\) \{\n\s+await sendToTv\(res, params, box\);\n\s+return;\n\s+\}/);
  assert.match(html, /<div id="tvBar" class="tv-bar" role="region" aria-label="Lecture sur la TV" hidden>/);
  assert.match(html, /<div class="set-block" id="settings-tv" aria-labelledby="settings-tv-title">/);
});

// a file chosen in the list, or what a player plays, can go to the TV too.
function loadCast(answer = () => ({ active: true }), origin = 'http://127.0.0.1:8080') {
  const calls = [];
  const elements = {};
  const el = id => elements[id] || (elements[id] = { id, hidden: true, textContent: '', innerHTML: '', style: {}, attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; } });
  const context = {
    document: { getElementById: el, querySelector: () => null },
    window: { location: { origin } },
    api: async (url, options) => { calls.push([url, JSON.parse(options.body)]); return answer(url); },
    escapeHtml: s => String(s), currentMedia: null, setInterval: () => 1, clearInterval: () => {},
  };
  vm.runInNewContext(`${tvCode}\nthis.api_ = { castToTv, tvSourceUrl, tvMediaPayload, tvTitle };`, context);
  return { ...context.api_, calls, el, context };
}
const plain = value => JSON.parse(JSON.stringify(value));

test('one call sends any source; a KINO address is relayed too, nothing else', async () => {
  const page = loadCast();
  await page.castToTv({ stream_url: '/api/offline/media/0123456789abcdef0123456789abcdef', title: 'Dune (2021)',
    filename: 'Dune.mkv', media: { id: 'tt1160419' }, resume_sec: 61.7 });
  assert.deepEqual(plain(page.calls[0]), ['/api/tv/play', { stream_url: 'http://127.0.0.1:8080/api/offline/media/0123456789abcdef0123456789abcdef',
    title: 'Dune (2021)', filename: 'Dune.mkv', media: { id: 'tt1160419' }, resume_sec: 61 }]);
  assert.equal(page.el('tvBar').hidden, false, 'the TV bar shows what the TV plays');
  for (const bad of ['file:///etc/passwd', '//evil.example/x.mkv', 'javascript:alert(1)', '']) {
    await assert.rejects(page.castToTv({ stream_url: bad }), /Aucune source à envoyer à la TV\./);
  }
  assert.equal(page.calls.length, 1);
});

test('the TV gets the title and the episode, not the file name', () => {
  const page = loadCast();
  const episode = page.tvMediaPayload({ id: 'tt2531336', name: 'Lupin', type: 'series', poster: 'p', year: '2021', offline_id: 'x' }, '1', '3');
  assert.deepEqual(plain(episode), { id: 'tt2531336', type: 'series', name: 'Lupin', poster: 'p', year: '2021', season: 1, episode: 3 });
  assert.equal(page.tvTitle(episode), 'Lupin — S01E03');
  assert.equal(page.tvTitle(page.tvMediaPayload({ id: 'tt1', name: 'Heat', type: 'movie', year: '1995' })), 'Heat (1995)');
  assert.equal(page.tvTitle(null, 'Heat.1995.mkv'), 'Heat.1995.mkv');
});

test('« Fichier prêt »: each file of a chosen source has « Sur la TV », from where it was left', async () => {
  const start = source.indexOf('// Where this title (or episode) was left');
  const fileCode = source.slice(start, source.indexOf('function openDebridFile(', start));
  const sent = [];
  const elements = { debridTvStatus: { textContent: '' } };
  const context = {
    document: { getElementById: id => elements[id] },
    window: { lastDebridFiles: [{ filename: 'Lupin.S01E03.1080p.mkv', download: 'https://dl.example/d/AB/Lupin.S01E03.mkv' }] },
    lastReadyDebrid: [{}, '1', '2'], currentMedia: { id: 'tt2531336', name: 'Lupin', type: 'series', year: '2021', poster: 'p' },
    userHistory: [{ id: 'tt2531336', ep_positions: { s1e3: { position: 600 } } }],
    Spoilers: { episodeNumber: name => { const m = /S(\d+)E(\d+)/i.exec(name); return m ? { season: +m[1], episode: +m[2] } : null; } },
    castToTv: async args => { sent.push(args); if (context.fail) throw new Error('Aucune TV choisie.'); },
    tvMediaPayload: (m, s, e) => ({ id: m.id, name: m.name, type: 'series', season: s, episode: e }),
    tvTitle: (m, fallback) => (m ? `${m.name} — S0${m.season}E0${m.episode}` : fallback),
  };
  vm.runInNewContext(`${fileCode}\nthis.send = sendDebridFileToTv;`, context);
  const button = { innerHTML: 'Sur la TV', disabled: false, textContent: '' };
  await context.send(button, 0);
  assert.deepEqual(plain(sent[0]), { stream_url: 'https://dl.example/d/AB/Lupin.S01E03.mkv', title: 'Lupin — S01E03',
    filename: 'Lupin.S01E03.1080p.mkv', media: { id: 'tt2531336', name: 'Lupin', type: 'series', season: 1, episode: 3 }, resume_sec: 600 });
  assert.equal(button.innerHTML, 'Sur la TV');
  context.fail = true;
  await context.send(button, 0);
  assert.equal(elements.debridTvStatus.textContent, 'Aucune TV choisie.', 'the reason shows under the files');
  assert.match(source, /onclick="sendDebridFileToTv\(this, \$\{index\}\)" title="Lire ce fichier sur la TV">/);
});

test('both players offer « Sur la TV »: pause, send at this moment, then stop on the Mac', () => {
  const native = require('../tools/interface_source.js').inFrench(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/native-player.js'), 'utf8'));
  const menu = html.slice(html.indexOf('id="inAppMoreMenu"'), html.indexOf('id="inAppWpChatBox"'));
  assert.match(menu, /id="inAppTvBtn" onclick="sendInAppPlaybackToTv\(\)"[^>]*>Sur la TV<\/button>/);
  const inApp = source.slice(source.indexOf('async function sendInAppPlaybackToTv() {'), source.indexOf('function toggleInAppMoreMenu('));
  assert.match(inApp, /const position = Math\.floor\(inAppAbsoluteTime\(video\)\);[\s\S]*video\.pause\(\);[\s\S]*await castToTv\(\{ stream_url: inAppCurrentUrl,[\s\S]*resume_sec: position \}\);[\s\S]*closeInAppPlayer\(\);/);
  assert.match(native, /id="nativeTv" title="Continuer sur la TV, au même moment" hidden>Sur la TV<\/button>/);
  assert.match(native, /element\('nativeTv'\)\.hidden = !tvSource\(\) \|\| typeof castToTv !== 'function';/);
  const nativeSend = native.slice(native.indexOf('async function sendToTv() {'), native.indexOf('// Watch Party and picture in picture'));
  assert.match(nativeSend, /command\('pause'\);[\s\S]*await castToTv\([\s\S]*resume_sec: position \}\);\n\s+await close\(\);/);
  assert.match(native, /return session\.source\?\.stream_url \|\| session\.item\?\.url \|\| '';/, 'an offline film goes to the TV too');
});
