// Sans internet: KINO opens on the offline files, says plainly what waits for
// the connection, searches the files kept on this Mac, and comes back on its
// own. The real interface scripts run on a stand-in page (tools/page_harness.js).
const assert = require('node:assert/strict');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const { loadPage, settle, shown } = require('../tools/page_harness.js');

const FILM = { id: 'a'.repeat(32), filename: 'Le.Samourai.1967.1080p.mkv', status: 'completed', available: true,
  bytes_done: 5e9, bytes_total: 5e9, media: { id: 'tt0062229', name: 'Le Samouraï', type: 'movie', year: '1967' } };
const DOWNLOADS = { downloads: { [FILM.id]: FILM }, used_bytes: 5e9, free_bytes: 60e9 };

// state.online decides what /api/network says; « offline » makes the catalogue fail like the server does.
function server(state) {
  return url => {
    const path = url.split('?')[0];
    if (path === '/api/network') return { online: state.online };
    if (path === '/api/downloads') return DOWNLOADS;
    if (!state.online && ['/api/catalog', '/api/search'].includes(path)) return { error: 'Pas de connexion à internet.', code: 'offline' };
    if (path === '/api/catalog') return { metas: [{ id: 'tt1', name: 'Film', type: 'movie' }] };
    return {};
  };
}

const asked = (page, path) => page.requests.filter(url => url.split('?')[0] === path).length;
const text = (page, id) => page.element(id).textContent;
// Script-level « let » values are not properties of the page's global object.
const tabOf = page => vm.runInContext('activeTab', page.context);

test('without internet KINO opens on the offline files, with the reason above', async () => {
  const page = await loadPage(server({ online: false }));
  assert.equal(tabOf(page), 'offline');
  assert.equal(shown(page.element('networkBanner')), true);
  assert.equal(text(page, 'networkBannerTitle'), 'Sans internet');
  assert.match(text(page, 'networkBannerText'), /^Vos fichiers hors ligne restent disponibles\. Le catalogue, la recherche, les sources Debrid/);
  assert.match(page.element('postersGrid').innerHTML, /Le Samouraï/);
  assert.match(withCalls(page.element('postersGrid').innerHTML), /playOfflineDownload\('a{32}'\)">Lancer/);
  assert.equal(asked(page, '/api/catalog'), 0, 'the catalogue is not asked for');
  assert.equal(page.context.document.body.classList.contains('is-offline'), true);
});

test('with internet KINO opens on Films as before, without the banner', async () => {
  const page = await loadPage(server({ online: true }));
  assert.equal(tabOf(page), 'movies');
  assert.equal(shown(page.element('networkBanner')), false);
  assert.equal(asked(page, '/api/catalog'), 1);
  assert.equal(page.context.document.body.classList.contains('is-offline'), false);
});

test('offline, a catalogue tab says it needs internet and offers the offline files', async () => {
  const page = await loadPage(server({ online: false }));
  for (const tab of ['movies', 'series']) {
    await page.context.switchTab(tab);
    await settle();
    const grid = page.element('postersGrid').innerHTML;
    assert.match(grid, /<h3>Le catalogue a besoin d’internet<\/h3>/, tab);
    assert.match(withCalls(grid), /onclick="switchTab\('offline'\)">Voir mes fichiers hors ligne/, tab);
    for (const id of ['heroSpotlight', 'genreFilters', 'homeResumeSection', 'homeNewSection']) {
      assert.equal(shown(page.element(id)), false, `${tab}: ${id}`);
    }
  }
  assert.equal(asked(page, '/api/catalog'), 0);
  assert.equal(asked(page, '/api/new-episodes'), 0, 'the home rows ask nothing either');
});

test('offline, a search looks in the files kept on this Mac', async () => {
  const page = await loadPage(server({ online: false }));
  page.element('searchType').value = 'all';
  page.element('searchInput').value = 'samourai';
  await page.context.runSearch();
  const grid = page.element('postersGrid').innerHTML;
  assert.match(grid, /Sans internet : dans vos fichiers hors ligne/);
  assert.match(withCalls(grid), /Le Samouraï[\s\S]*playOfflineDownload\('a{32}'\)">Lancer/);
  page.element('searchInput').value = 'Dune';
  await page.context.runSearch();
  assert.match(page.element('postersGrid').innerHTML, /<h3>La recherche a besoin d’internet<\/h3>/);
  assert.equal(asked(page, '/api/search'), 0);
  assert.equal(asked(page, '/api/search-suggest'), 0);
});

test('a request that fails for lack of internet switches KINO to « Sans internet »', async () => {
  const state = { online: true };
  const page = await loadPage(server(state));
  assert.equal(shown(page.element('networkBanner')), false);
  state.online = false;
  await page.context.switchTab('series');
  await settle();
  assert.equal(shown(page.element('networkBanner')), true);
  assert.equal(text(page, 'networkBannerTitle'), 'Sans internet');
  assert.match(page.element('postersGrid').innerHTML, /Le catalogue a besoin d’internet/);
  assert.equal(tabOf(page), 'series', 'the user stays where they were');
});

test('when the connection comes back, the banner says so and the catalogue loads', async () => {
  const state = { online: false };
  const page = await loadPage(server(state));
  await page.context.switchTab('movies');
  await settle();
  state.online = true;
  await page.context.retryNetwork();
  await settle();
  assert.equal(text(page, 'networkBannerTitle'), 'Connexion rétablie');
  assert.equal(page.element('networkBanner').classList.contains('is-back'), true);
  assert.equal(asked(page, '/api/catalog'), 1, 'the catalogue that could not load loads now');
  assert.ok(page.requests.includes('/api/network?refresh=1'), '« Réessayer » checks again');
  assert.equal(page.context.document.body.classList.contains('is-offline'), false);
});

test('offline, KINO checks again by itself, but never while its window is hidden', async () => {
  const page = await loadPage(server({ online: false }));
  const checks = () => page.requests.filter(url => url === '/api/network?refresh=1').length;
  await settle();
  assert.ok(checks() >= 1, 'checked again while shown');
  page.context.document.hidden = true;
  await settle();
  const before = checks();
  await settle();
  assert.equal(checks(), before, 'nothing while hidden');
});

test('offline, « Ce soir » keeps to the offline files and the empty library says why', async () => {
  const page = await loadPage(server({ online: false }));
  page.context.openTonightModal();
  assert.equal(page.element('tonightOfflineOnly').checked, true);
  page.context.closeTonightModal();
  page.context.renderOfflineLibraryData({ downloads: {}, used_bytes: 0, free_bytes: 1e9 });
  assert.match(page.element('postersGrid').innerHTML, /Sans internet, KINO ne peut rien lire pour le moment/);
});
