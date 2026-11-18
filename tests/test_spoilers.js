const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const Spoilers = require('../web/js/spoilers.js');

const source = require('../tools/interface_source.js').appSource();
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const functionSource = (start, end) => source.slice(source.indexOf(`function ${start}(`), source.indexOf(`function ${end}(`)).replace(/async\s*$/, '');

test('watched episode formats, overrides and one-episode reveal stay independent', () => {
  const history = [{id: 'tt12345', watched_episodes: ['s1e1'], ep_positions: {S01E03: {pct: 100}}}];
  const state = {enabled: true, overrides: {tt99999: false}, revealed: {tt12345: ['S01E02']}};
  assert.equal(Spoilers.watched(history, 'tt12345', 1, 1), true);
  assert.equal(Spoilers.watched(history, 'tt12345', 1, 3), true);
  assert.equal(Spoilers.concealed(state, 'tt12345', 1, 1, true), false);
  assert.equal(Spoilers.concealed(state, 'tt12345', 1, 2, false), false);
  assert.equal(Spoilers.concealed(state, 'tt12345', 1, 4, false), true);
  assert.equal(Spoilers.concealed(state, 'tt99999', 1, 4, false), false);
  assert.equal(Spoilers.concealed(state, 'tt77777', 1, 4, undefined), true);
});

test('hidden episode details omit title, summary, image URL and accessible labels', () => {
  const elements = {detailEpisodesList: {innerHTML: ''}, detailSeasonAllWatchedBtn: {style: {}}, markSeasonWatchedBtn: {style: {}}};
  const state = {enabled: true, overrides: {}, revealed: {tt12345: ['S01E02']}};
  const videos = [
    {season: 1, episode: 1, name: 'Titre vu', overview: 'Résumé vu', thumbnail: 'https://test/seen.jpg'},
    {season: 1, episode: 2, name: 'Titre révélé', overview: 'Résumé révélé', thumbnail: 'https://test/revealed.jpg'},
    {season: 1, episode: 3, name: 'Secret héritier', overview: 'Secret synopsis', thumbnail: 'https://test/secret.jpg'},
  ];
  const context = {
    document: {getElementById: id => elements[id] || null},
    seriesMetaVideos: videos, currentMedia: {id: 'tt12345', type: 'series', name: 'Fixture'},
    userHistory: [{id: 'tt12345', watched_episodes: ['s1e1']}],
    activeTab: 'series', Spoilers, escapeHtml,
    spoilerSeriesId: () => 'tt12345',
    spoilerConcealed: (id, season, episode) => Spoilers.concealed(state, id, season, episode,
      Spoilers.watched(context.userHistory, id, season, episode)),
    api: () => Promise.resolve({}), encodeURIComponent,
  };
  vm.runInNewContext(`${functionSource('renderDetailEpisodes', 'pickSeriesEpisodeSources')}; renderDetailEpisodes(1);`, context);
  const html = elements.detailEpisodesList.innerHTML;
  assert.match(html, /Titre vu/);
  assert.match(html, /Titre révélé/);
  assert.doesNotMatch(html, /Secret héritier|Secret synopsis|secret\.jpg/);
  assert.match(html, /Afficher pour cet épisode/);
  assert.match(html, /Image masquée pour l&#39;épisode 3|Image masquée pour l'épisode 3/);
});

test('source and ready-file results contain no concealed filename in DOM attributes', () => {
  const elements = {torrentsList: {innerHTML: ''}, torrentsSub: {textContent: ''},
    seasonSelect: {value: '1'}, episodeSelect: {value: '3'}, debridResultPanel: {innerHTML: ''}};
  const context = {
    document: {getElementById: id => elements[id] || null},
    currentMedia: {id: 'tt12345', type: 'series'}, allTorrents: [{title: 'SECRET-SOURCE', meta: 'SECRET-META', source: 'Fixture', qualities: [], langs: [], score: 130}],
    getFilteredTorrents: () => context.allTorrents, scoreTorrent: t => t.score,
    spoilerConcealed: () => true, spoilerSeriesId: () => 'tt12345', Spoilers, escapeHtml,
    lastReadyDebrid: null, window: {},
  };
  vm.runInNewContext(`${source.slice(source.indexOf('// --- Sources: the best one first'), source.indexOf('async function copyTorrentMagnet('))}; ${functionSource('renderDebridState', 'openDebridFile')}; renderTorrents(); renderDebridState({ready:true,files:[{filename:'SECRET-FILE-S01E03.mkv',download:'https://test/SECRET-URL',is_target_ep:true}]},1,3,'SECRET-CHOSEN');`, context);
  const html = elements.torrentsList.innerHTML + elements.debridResultPanel.innerHTML;
  assert.doesNotMatch(html, /SECRET-SOURCE|SECRET-META|SECRET-FILE|SECRET-URL|SECRET-CHOSEN/);
  assert.match(html, /Épisode 3/);
  assert.match(html, /Afficher pour cet épisode/);
});
