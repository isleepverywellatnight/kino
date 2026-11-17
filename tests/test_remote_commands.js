// Every button of the phone remote must do something in the in-app player.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const remotePage = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/remote.html'), 'utf8');
const handler = 'function handleRemoteCommand(cmd)' + source
  .split('function handleRemoteCommand(cmd)', 2)[1]
  .split('// --- Watch Party P2P (PeerJS) ---', 1)[0];

function player() {
  const calls = [];
  const video = { muted: false, volume: 0.6, play: async () => calls.push(['play']), pause: () => calls.push(['pause']) };
  const record = name => (...args) => calls.push([name, ...args]);
  const context = {
    document: { getElementById: () => video },
    toggleInAppPlay: record('toggle'), inAppSeekTo: record('seekTo'), inAppSeekRel: record('seekRel'),
    setInAppVolume: (level, unmute) => { video.volume = level; calls.push(['volume', level, unmute]); },
    toggleInAppMute: () => { video.muted = !video.muted; calls.push(['mute', video.muted]); },
    inAppNextTrack: record('next'), inAppPrevTrack: record('prev'),
    cycleInAppSubtitles: record('subs'), toggleInAppFullscreen: record('fullscreen'),
    showInAppToast: () => {},
  };
  vm.runInNewContext(handler, context);
  return { calls, video, send: cmd => vm.runInNewContext('handleRemoteCommand(cmd)', Object.assign(context, { cmd })) };
}

test('the remote protocol {action, value} drives the player', () => {
  const { calls, send } = player();
  send({ action: 'seek_rel', value: -10 });
  send({ action: 'seek', value: 1234.5 });
  send({ action: 'volume', value: '0.25' });
  send({ action: 'next' });
  send({ action: 'prev' });
  send({ action: 'cycle_subs' });
  send({ action: 'fullscreen' });
  send({ action: 'pause' });
  assert.deepEqual(calls, [
    ['seekRel', -10], ['seekTo', 1234.5], ['volume', 0.25, true], ['next'], ['prev'],
    ['subs'], ['fullscreen'], ['pause'],
  ]);
});

test('the mute button sends volume 0, then 1', () => {
  const { calls, video, send } = player();
  send({ action: 'volume', value: 0 });
  assert.equal(video.muted, true);
  send({ action: 'volume', value: 1 });
  assert.deepEqual(calls.at(-1), ['volume', 1, true]);
});

test('every action the phone page sends is handled', () => {
  const actions = new Set([...remotePage.matchAll(/sendCommand\('([a-z_]+)'/g)].map(([, action]) => action));
  for (const ternary of remotePage.matchAll(/sendCommand\(\w+ \? '([a-z_]+)' : '([a-z_]+)'\)/g)) {
    actions.add(ternary[1]);
    actions.add(ternary[2]);
  }
  assert.ok(actions.size >= 8, `only ${actions.size} actions found in the remote page`);
  for (const action of actions) {
    const { calls, send } = player();
    send({ action, value: 5 });
    assert.ok(calls.length > 0, `the remote action "${action}" does nothing`);
  }
});

test('the in-app player reports the fields the remote and the update guard read', () => {
  const polling = source.split('function startRemotePolling()', 2)[1].split('function handleRemoteCommand(cmd)', 1)[0];
  // The native player's branch comes first; the built-in player's report follows.
  const inApp = polling.split("const v = document.getElementById('inAppVideo');", 2)[1];
  const body = inApp.split("fetch('/api/player/state'", 2)[1].split('JSON.stringify({', 2)[1].split('})', 1)[0];
  const sent = new Set([...body.matchAll(/^\s+([a-z_]+):/gm)].map(([, key]) => key));
  // The remote's state as the server keeps it (crates/kino/src/remote.rs).
  const remoteState = fs.readFileSync(path.join(path.join(__dirname, '..'), 'crates/kino/src/remote.rs'), 'utf8');
  const stateBlock = remoteState.split('let state = json!({', 2)[1].split('});', 1)[0];
  const known = new Set([...stateBlock.matchAll(/"([a-z_]+)":/g)].map(([, key]) => key));
  for (const key of ['playing', 'paused', 'current_time', 'duration']) assert.ok(sent.has(key), `${key} is not reported`);
  for (const key of sent) assert.ok(known.has(key), `${key} is not part of the remote state`);
  assert.match(source, /function closeInAppPlayer\(\) \{\n(?:  const finishedId = [^\n]*\n)?  reportInAppPlaybackClosed\(\);/);
  const nativeSource = require('../tools/interface_source.js').inFrench(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/native-player.js'), 'utf8'));
  const nativeBody = nativeSource.split('function remoteState()', 2)[1].split('return {', 2)[1].split('};', 1)[0];
  const nativeSent = new Set([...nativeBody.matchAll(/(?:^|[\s,])([a-z_]+):/g)].map(([, key]) => key));
  for (const key of ['playing', 'paused', 'current_time', 'duration']) assert.ok(nativeSent.has(key), `native ${key} is not reported`);
  for (const key of nativeSent) assert.ok(known.has(key), `native ${key} is not part of the remote state`);
});
