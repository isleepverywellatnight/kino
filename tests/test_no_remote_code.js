// KINO's page runs no code fetched from the internet. PeerJS (Watch
// Party) ships with the app, pinned to the exact file npm publishes.
//
// To update PeerJS: read https://registry.npmjs.org/peerjs/<version>, download
// its tarball, check it against `dist.integrity`, copy package/dist/peerjs.min.js
// and package/LICENSE to web/js/vendor/, then change PEERJS below.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.join(path.join(__dirname, '..'), name), 'utf8');
const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();

const PEERJS = {
  version: '1.5.4',
  bytes: 92865,
  sha256: 'ad5d8870d1e389914f9cba8d35be313c4327c69ee0a221e482e9bf7621136fe5',
};

test('every script and style of the page comes from KINO itself', () => {
  const scripts = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map(match => match[1]);
  assert.ok(scripts.length > 15);
  assert.deepEqual(scripts.filter(src => !/^\/[\w./-]+\.js$/.test(src) || src.startsWith('//')), [], 'no script from another origin');
  assert.ok(scripts.includes('/js/vendor/peerjs.min.js'));
  assert.deepEqual([...html.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g)].map(match => match[1]).filter(href => !href.startsWith('/')), []);
  assert.doesNotMatch(html, /unpkg|cdn\.|jsdelivr|cdnjs|integrity="/i);
  for (const name of fs.readdirSync(path.join(path.join(__dirname, '..'), 'web/css'))) {
    assert.doesNotMatch(read(`web/css/${name}`), /@import|url\(\s*["']?(?:https?:)?\/\//, `${name} fetches nothing`);
  }
  // No script added while the app runs either.
  const scriptsDir = path.join(path.join(__dirname, '..'), 'web/js');
  for (const name of fs.readdirSync(scriptsDir).filter(file => file.endsWith('.js'))) {
    assert.doesNotMatch(read(`web/js/${name}`), /createElement\(\s*["']script["']\s*\)|importScripts\(|\bimport\(/, `${name} loads no script`);
  }
});

test('PeerJS is the exact file npm publishes, with its licence beside it', () => {
  const file = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/vendor/peerjs.min.js'));
  assert.equal(file.length, PEERJS.bytes);
  assert.equal(crypto.createHash('sha256').update(file).digest('hex'), PEERJS.sha256, 'not a byte changed');
  assert.match(file.toString('utf8'), /window\.Peer=/);
  assert.match(html, new RegExp(`<!-- PeerJS ${PEERJS.version.replaceAll('.', '\\.')} \\(Watch Party\\), embarqué[^>]*-->\\n<script src="/js/vendor/peerjs\\.min\\.js"></script>`));
  const licence = read('web/js/vendor/peerjs.LICENSE.txt');
  assert.match(licence, /Copyright \(c\) 2015 Michelle Bu and Eric Zhang/);
  assert.match(licence, /The MIT License/);
  assert.deepEqual(fs.readdirSync(path.join(path.join(__dirname, '..'), 'web/js/vendor')).sort(), ['peerjs.LICENSE.txt', 'peerjs.min.js', 'qrcode.min.js'], 'nothing else is vendored without a pin here');
});

test('the QR code script (phone remote) is the one KINO has always shipped', () => {
  const file = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/vendor/qrcode.min.js'));
  assert.equal(crypto.createHash('sha256').update(file).digest('hex'), 'c541ef06327885a8415bca8df6071e14189b4855336def4f36db54bde8484f36');
});

test('the app ships the file, serves it, and refuses to start a build without it', () => {
  assert.match(read('packaging/build_macos_app.sh'), /ditto web "\$APP\/Contents\/Resources\/web"/, 'the whole web folder is in the Mac app');
  assert.match(read('packaging/build_windows_app.ps1'), /Copy-Item web "\$app\\web" -Recurse/, 'and in the Windows one');
  assert.match(read('crates/kino/src/server.rs'), /\["\/css\/", "\/js\/", "\/static\/", "\/web\/"\]/);
  assert.match(read('crates/kino-desktop/src/main.rs'), /"js\/vendor\/peerjs\.min\.js"/, 'the self-check refuses a build without it');
});

function watchParty({ online = true, peer = true } = {}) {
  const alerts = [];
  const peers = [];
  class Peer {
    constructor(...args) { this.args = args; this.handlers = {}; peers.push(this); }
    on(name, handler) { this.handlers[name] = handler; }
    destroy() {}
  }
  const context = {
    networkOnline: online, alert: text => alerts.push(text), console: { warn() {} },
    wpPeer: null, wpConnections: [], wpRoomCode: 'ABCD',
    updateWpModalUI() {}, showInAppToast() {}, setupWpConnection() {}, wpPlayerSnapshot: () => null,
    createWatchPartyRoom: () => alerts.push('new room'),
  };
  if (peer) context.Peer = Peer;
  const code = source.slice(source.indexOf('const WP_NEEDS_INTERNET ='), source.indexOf('function setupWpConnection(conn) {'));
  vm.runInNewContext(`${code}; this.initWpPeer = initWpPeer; this.message = watchPartyErrorMessage;`, context);
  return { context, alerts, peers };
}

test('a Watch Party says in French what went wrong, and what to do', () => {
  const { context } = watchParty();
  const internet = 'La Watch Party a besoin d’internet pour relier les participants. Vérifiez votre connexion, puis réessayez.';
  for (const type of ['network', 'server-error', 'socket-error', 'socket-closed', 'disconnected']) {
    assert.equal(context.message({ type, message: 'Lost connection to server.' }), internet, type);
  }
  assert.equal(context.message({ type: 'webrtc', message: 'x' }, false), internet, 'offline, whatever PeerJS says');
  assert.equal(context.message({ type: 'peer-unavailable', message: 'Could not connect to peer kino-room-abcd' }),
    'Aucune Watch Party ne répond à ce code. Vérifiez-le avec la personne qui l’a créée.');
  assert.equal(context.message({ type: 'webrtc', message: 'ICE failed' }), 'La Watch Party a rencontré un problème (ICE failed).');
  assert.equal(context.message(null), 'La Watch Party a rencontré un problème (erreur inconnue).');
  assert.doesNotMatch(source, /depuis le CDN|Erreur Watch Party/);
});

test('without internet nothing is tried; with it, PeerJS errors reach the user in French', () => {
  const offline = watchParty({ online: false });
  offline.context.initWpPeer('kino-room-abcd');
  assert.equal(offline.peers.length, 0);
  assert.match(offline.alerts[0], /a besoin d’internet/);

  const missing = watchParty({ peer: false });
  missing.context.initWpPeer('kino-room-abcd');
  assert.match(missing.alerts[0], /un fichier de KINO manque\. Réinstallez la dernière mise à jour/);

  const online = watchParty();
  online.context.initWpPeer('kino-room-abcd');
  assert.equal(online.peers.length, 1);
  assert.equal(online.peers[0].args[0], 'kino-room-abcd');
  online.peers[0].handlers.error({ type: 'network', message: 'Lost connection to server.' });
  online.peers[0].handlers.error({ type: 'unavailable-id', message: 'ID taken' });
  assert.deepEqual(online.alerts, ['La Watch Party a besoin d’internet pour relier les participants. Vérifiez votre connexion, puis réessayez.', 'new room'],
    'a room code already taken draws another, without a message');
});
