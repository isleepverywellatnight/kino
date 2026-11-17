// Offline films and HEVC sources in the integrated player.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();

function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} is defined`);
  const end = source.indexOf('\n}\n', start);
  return source.slice(start, end + 2);
}

function load(state) {
  const context = { URLSearchParams, ...state };
  vm.runInNewContext(`const INAPP_STALL_TICKS = 3;\n${extract('inAppStallStep')}\n${extract('inAppCompatibleUrl')}\n${extract('inAppMetadataDuration')}\n`
    + 'this.compatibleUrl = inAppCompatibleUrl; this.metadataDuration = inAppMetadataDuration; this.stallStep = inAppStallStep;', context);
  return context;
}

const base = { inAppNeedsVideoConversion: false, inAppVideoCodec: null, inAppSelectedAudioIndex: null,
  inAppNeedsAudioConversion: false, inAppIsRemux: false, inAppTotalDuration: 0, inAppStreamOffset: 0,
  inAppDownscaleVideo: false };

test('a copied HEVC video asks the server for the hvc1 label', () => {
  const url = new URL(load({ ...base, inAppVideoCodec: 'hevc', inAppSelectedAudioIndex: 2 })
    .compatibleUrl('/api/offline/media/8562009d895e48baa64b51e301d49556', 0), 'http://127.0.0.1');
  assert.equal(url.pathname, '/api/remux');
  assert.equal(url.searchParams.get('url'), '/api/offline/media/8562009d895e48baa64b51e301d49556');
  assert.equal(url.searchParams.get('hvc1'), '1');
  assert.equal(url.searchParams.get('audio'), '2');
  assert.equal(url.searchParams.get('video'), null);
});

test('no hvc1 label when the video is converted or is not HEVC', () => {
  const converted = new URL(load({ ...base, inAppVideoCodec: 'hevc', inAppNeedsVideoConversion: true })
    .compatibleUrl('https://example.invalid/film.mkv', 0), 'http://127.0.0.1');
  assert.equal(converted.searchParams.get('video'), 'h264');
  assert.equal(converted.searchParams.get('hvc1'), null);
  const h264 = new URL(load({ ...base, inAppVideoCodec: 'h264' })
    .compatibleUrl('https://example.invalid/film.mkv', 0), 'http://127.0.0.1');
  assert.equal(h264.searchParams.get('hvc1'), null);
});

test('a converted stream keeps the analysed duration instead of the part received so far', () => {
  assert.equal(load({ ...base, inAppIsRemux: true, inAppTotalDuration: 4599.9 }).metadataDuration(1.017), 4599.9);
  assert.equal(load({ ...base, inAppIsRemux: false, inAppTotalDuration: 0 }).metadataDuration(5400), 5400);
  assert.equal(load({ ...base, inAppIsRemux: true, inAppTotalDuration: 0, inAppStreamOffset: 600 })
    .metadataDuration(30), 630, 'without analysis, the element duration is all there is');
  assert.equal(load({ ...base, inAppTotalDuration: 120 }).metadataDuration(Infinity), 120);
});

test('4K stays 4K until the freeze guard asks for 1080p', () => {
  const native = new URL(load({ ...base, inAppVideoCodec: 'hevc' })
    .compatibleUrl('/api/offline/media/8562009d895e48baa64b51e301d49556', 600), 'http://127.0.0.1');
  assert.equal(native.searchParams.get('video'), null);
  assert.equal(native.searchParams.get('hvc1'), '1');
  const guarded = new URL(load({ ...base, inAppVideoCodec: 'hevc', inAppDownscaleVideo: true })
    .compatibleUrl('/api/offline/media/8562009d895e48baa64b51e301d49556', 600), 'http://127.0.0.1');
  assert.equal(guarded.searchParams.get('video'), 'h264');
  assert.equal(guarded.searchParams.get('hvc1'), null);
});

function run(step, samples, downscaled = false) {
  let state = { lastTime: -1, ticks: 0, downscaled };
  const actions = [];
  for (const sample of samples) {
    const result = step(state, { paused: false, seeking: false, hidden: false, readyState: 4, ahead: 30, ...sample });
    state = result.state;
    actions.push(result.action);
  }
  return actions;
}

test('a picture frozen with data ahead switches to 1080p after about two seconds, then stops', () => {
  const { stallStep } = load(base);
  const frozen = [{ time: 4.9 }, { time: 4.9 }, { time: 4.9 }, { time: 4.9 }];
  assert.deepEqual([...run(stallStep, frozen)], [null, null, null, 'downscale']);
  assert.deepEqual([...run(stallStep, frozen, true)], [null, null, null, 'stop'], 'already 1080p: give up');
});

test('normal playback, pauses, seeks, rebuffering and hidden windows never trigger the guard', () => {
  const { stallStep } = load(base);
  const playing = [0, 0.7, 1.4, 2.1, 2.8, 3.5].map(time => ({ time }));
  assert.deepEqual([...run(stallStep, playing)], [null, null, null, null, null, null]);
  for (const condition of [{ paused: true }, { seeking: true }, { hidden: true }, { ahead: 0.5 }, { readyState: 2 }]) {
    const stuck = [1, 1, 1, 1, 1, 1].map(time => ({ time, ...condition }));
    assert.ok(run(stallStep, stuck).every(action => action === null), JSON.stringify(condition));
  }
  const recovers = [{ time: 2 }, { time: 2 }, { time: 2 }, { time: 2.7 }, { time: 2.7 }, { time: 2.7 }];
  assert.ok(run(stallStep, recovers).every(action => action === null), 'progress resets the count');
});
