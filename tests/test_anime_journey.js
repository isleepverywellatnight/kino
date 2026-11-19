const test = require('node:test');
const assert = require('node:assert/strict');
// The page's template helpers (act, THIS…) are globals by the time anything is drawn.
Object.assign(globalThis, require('../tools/interface_source.js').templateHelpers());
const { withCalls } = require('../tools/interface_source.js');
const JourneyView = require('../web/js/anime-journey.js');

const journey = {
  ok: true, saga_id: 1, order_label: 'Ordre de sortie', source: 'AniList', relations_known: true,
  works: [
    {id: 1, title: 'Cowboy Bebop', format: 'TV', relation: 'SELF', year: 1998, episodes: 26,
      playback: {id: 'tt0213338', type: 'series'}},
    {id: 5, title: 'The Betrayal', format: 'MOVIE', relation: 'SIDE_STORY', year: 2001,
      playback: {id: 'tt0275277', type: 'movie'}},
  ],
};

test('journey hides an unseen work title from text and attributes while keeping its format and next step', () => {
  const history = [{id: 'tt0213338', type: 'series', watched_episodes: Array.from({length: 26}, (_, i) => `S01E${String(i + 1).padStart(2, '0')}`)}];
  const html = String(JourneyView.render(journey, history, true, new Set(), new Map(), 1));
  assert.match(html, /Cowboy Bebop/);
  assert.match(html, /Film · 2001/);
  assert.match(html, /Prochain selon l(?:'|&#39;)ordre de sortie/);
  assert.match(html, /Afficher le titre/);
  assert.doesNotMatch(html, /The Betrayal/);
  assert.doesNotMatch(html, /tt0275277/);

  const revealed = String(JourneyView.render(journey, history, true, new Set([5]), new Map(), 1));
  assert.match(revealed, /The Betrayal/);
  assert.match(revealed, /À voir/);
});

test('journey progress distinguishes completed films from episodes in progress', () => {
  const history = [{id: 'tt0213338', type: 'series', watched_episodes: ['S01E01', 'S01E02']},
                   {id: 'tt0275277', type: 'movie', completed: true}];
  const html = String(JourneyView.render(journey, history, false, new Set(), new Map(), 1));
  assert.match(html, /2\/26 épisodes vus/);
  assert.match(html, /Film vu/);
  assert.match(html, /Prochain selon l(?:'|&#39;)ordre de sortie/);
});

test('a corrected order is named as the user order, never as release order', () => {
  const custom = {...journey, order_label: 'Ordre personnalisé', works: [...journey.works].reverse()};
  const html = String(JourneyView.render(custom, [], false, new Set(), new Map(), 1));
  assert.match(html, /Prochain selon votre ordre/);
  assert.match(html, /Ordre personnalisé des œuvres reliées/);
  assert.doesNotMatch(html, /Prochain selon l&#39;ordre de sortie|Prochain selon l(?:'|&#39;)ordre de sortie/);
});

test('an unmatched OVA has no misleading play action', () => {
  const withOva = {...journey, works: [...journey.works, {id: 10863, title: 'Secret OVA', format: 'OVA',
    relation: 'SEQUEL', year: 2012, episodes: 1, source_url: 'https://anilist.co/anime/10863', playback: null}]};
  const matches = new Map([[10863, {ok: false, reason: 'unmatched', message: 'Lecture non identifiée.'}]]);
  const html = String(JourneyView.render(withOva, [], true, new Set(), matches, 1));
  const ovaCard = html.match(/<li class="journey-work" data-journey-id="10863">([\s\S]*?)<\/li>/)?.[1] || '';
  assert.match(ovaCard, /Lecture non identifiée/);
  assert.match(ovaCard, /Voir sur AniList/);
  assert.doesNotMatch(ovaCard, /playJourneyWork\(10863/);
});
