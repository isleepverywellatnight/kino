const test = require('node:test');
const assert = require('node:assert/strict');
const playback = require('../web/js/series-playback.js');

test('an audio language survives inverted track order and codec changes', () => {
  const preference = playback.describeAudio({index: 4, language: 'fra', codec: 'ac3', title: 'Français'});
  const nextEpisode = [
    {index: 1, language: 'eng', codec: 'aac', default: true},
    {index: 2, language: 'fre', codec: 'aac', title: 'French'},
  ];
  const chosen = playback.chooseAudio(nextEpisode, preference, nextEpisode[0]);
  assert.equal(chosen.track.index, 2);
  assert.equal(chosen.missing, false);
  assert.deepEqual(Object.keys(preference), ['language', 'role', 'original']);
});

test('a missing language falls back to the global selection', () => {
  const tracks = [{index: 1, language: 'eng', default: true}, {index: 2, language: 'jpn'}];
  const chosen = playback.chooseAudio(tracks, {language: 'fr', role: 'main', original: false}, tracks[1]);
  assert.equal(chosen.track.index, 2);
  assert.equal(chosen.missing, true);
});

test('current choice takes priority, then series, then global', () => {
  const tracks = [
    {index: 1, language: 'eng', default: true},
    {index: 2, language: 'fra'},
    {index: 3, language: 'jpn'},
  ];
  const current = {language: 'ja', role: 'main', original: false};
  const series = {language: 'fr', role: 'main', original: false};
  assert.equal(playback.resolveAudio(tracks, current, series, tracks[0]).track.index, 3);
  const fallback = playback.resolveAudio(tracks.slice(0, 2), current, series, tracks[0]);
  assert.equal(fallback.track.index, 2);
  assert.equal(fallback.source, 'series');
  assert.equal(fallback.missing, true);
  assert.equal(playback.resolveAudio([tracks[0]], current, series, tracks[0]).source, 'global');
});

test('commentary is not selected in place of the main track', () => {
  const tracks = [
    {index: 0, language: 'eng', title: 'Director commentary'},
    {index: 1, language: 'eng', title: 'Main audio'},
  ];
  const chosen = playback.chooseAudio(tracks, {language: 'en', role: 'main', original: false}, tracks[0]);
  assert.equal(chosen.track.index, 1);
});

test('subtitle preference uses language and kind, with an explicit off fallback', () => {
  const off = {kind: '', lang: 'off'};
  const en = {kind: 'opensubtitles', lang: 'en', url: 'https://example.invalid/second'};
  const fr = {kind: 'opensubtitles', lang: 'fr', url: 'https://example.invalid/first'};
  const preference = {enabled: true, kind: 'opensubtitles', language: 'fr', variant: 'standard'};
  assert.deepEqual(playback.chooseSubtitles([off, en, fr], preference), {index: 2, missing: false});
  assert.deepEqual(playback.chooseSubtitles([off, en], preference), {index: 0, missing: true});
  assert.deepEqual(playback.globalSubtitles('vostfr'), preference);
  assert.equal(playback.globalSubtitles('vf').enabled, false);
  const fallback = playback.resolveSubtitles([off, fr],
    {enabled: true, kind: 'opensubtitles', language: 'en'}, null,
    playback.globalSubtitles('vostfr'));
  assert.deepEqual(fallback, {index: 1, missing: true, source: 'global'});
  const forced = {kind: 'opensubtitles', lang: 'fr', variant: 'forced'};
  assert.deepEqual(playback.chooseSubtitles([off, forced, fr], preference), {index: 2, missing: false});
});
