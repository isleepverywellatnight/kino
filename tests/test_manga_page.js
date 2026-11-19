// The Manga tab (manga.js): rows, a series with its chapters, and the reader
// that turns pages in the Japanese way and remembers where the user stopped.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const ROOT = path.join(__dirname, '..');
const html = require('../tools/interface_source.js').indexHtml();
const code = require('../tools/interface_source.js').scriptText('manga.js');
const { escapeHtml } = require('../tools/interface_source.js').templateHelpers();

const SERIES = { id: '118586', source: 'anilist', title: 'Frieren', cover: 'c.jpg', year: 2020 };
const CHAPTERS = [
  { id: 'md:c1', number: '1', volume: '1', title: 'Départ', pages: 3, group: 'Blue Solo', external: '', source: 'MangaDex', lang: 'fr' },
  { id: 'c2', number: '2', volume: '', title: '', pages: 0, group: '', external: 'https://mangaplus.example/2' },
  { id: 'wc:C3', number: '3', volume: '', title: '', pages: 0, group: '', external: '', source: 'WeebCentral', lang: 'en', date: '2026-11-15' },
];

function load(answers = {}) {
  const calls = [];
  const elements = {};
  const element = id => (elements[id] ||= { id, value: '', hidden: true, innerHTML: '', textContent: '', className: '', style: {},
    scrollIntoView() {}, querySelector: () => null, querySelectorAll: () => [] });
  const listeners = [];
  const timers = [];
  const store = {};
  const context = {
    document: { getElementById: element, addEventListener: (type, fn) => listeners.push(fn), body: { classList: { add() {}, remove() {} } },
      querySelector: selector => element(`q:${selector}`) },
    askP2PConsent: async () => { calls.push(['consent']); return true; }, offlineSize: bytes => `${bytes} o`,
    window: { KINO_LANG: 'fr', scrollTo() {}, open: url => calls.push(['open', url]) }, innerHeight: 800,
    localStorage: { getItem: key => store[key] ?? null, setItem: (key, value) => { store[key] = value; } },
    escapeHtml, encodeURIComponent, Promise,
    setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {}, alert() {}, prompt: () => null,
    api: async (url, options) => {
      calls.push([url, options?.body ? JSON.parse(options.body) : null]);
      const answer = answers[url.split('?')[0]];
      const value = typeof answer === 'function' ? answer(url) : answer;
      if (value instanceof Error) throw value;
      return value;
    },
  };
  vm.runInNewContext(`${code}; this.api_ = { renderMangaTab, openMangaSeries, openMangaReader, setMangaPage, turnMangaPage, showMangaVolumes, openMangaTorrent, downloadMangaVolume,
    toggleMangaMode, closeMangaReader, mangaCardHtml, state: () => ({ mangaSeries, mangaReader, mangaLang }) };`, context);
  return { ...context.api_, calls, elements, listeners, timers, store };
}

const ANSWERS = {
  '/api/manga/progress': { progress: [] },
  '/api/manga/row': { series: [SERIES] },
  '/api/manga/local': { folder: '', series: [] },
  '/api/manga/details': { ...SERIES, description: 'Une mage elfe.', authors: ['Yamada'], tags: ['Fantasy'] },
  '/api/manga/chapters': { chapters: CHAPTERS },
  '/api/manga/pages': { pages: ['p1.png', 'p2.png', 'p3.png'] },
};

test('a new tab in the sidebar, its panel and its reader', () => {
  assert.match(withCalls(html), /id="tab-manga" onclick="switchTab\('manga'\)">[\s\S]*?icons\.svg#manga"[\s\S]*?icons\.svg#manga-fill"[\s\S]*?Manga<\/button>/);
  assert.match(html, /<div id="mangaPanel" class="manga-panel" style="display:none;">/);
  assert.match(html, /<div id="mangaReader" class="manga-reader" role="dialog" aria-modal="true" aria-labelledby="mangaReaderTitle" hidden>/);
  const scripts = require('../tools/interface_source.js').interfaceScripts();
  assert.equal(scripts[scripts.indexOf('p2p-addons.js') + 1], 'manga.js');
});

test('the tab: rows of the catalogue, and an invitation for the user\'s own files', async () => {
  const lib = load(ANSWERS);
  await lib.renderMangaTab();
  assert.deepEqual(lib.calls.map(([url]) => url).filter(url => url.startsWith('/api/manga/row')),
    ['/api/manga/row?name=trending', '/api/manga/row?name=popular', '/api/manga/row?name=webtoon']);
  const home = lib.elements.mangaHome.innerHTML;
  assert.match(withCalls(home), /Tendances[\s\S]*class="manga-card" onclick="openMangaSeries\('anilist', '118586'\)"[\s\S]*Les plus populaires[\s\S]*Manhwa et webtoons/);
  assert.match(withCalls(home), /Sur cet ordinateur[\s\S]*onclick="chooseMangaFolder\(\)">Choisir un dossier[\s\S]*Choisissez le dossier qui contient vos mangas\./);
  assert.doesNotMatch(home, /Continuer la lecture/, 'nothing read yet');
  const hostile = lib.mangaCardHtml({ ...SERIES, title: '<img src=x onerror=alert(1)>' });
  assert.doesNotMatch(String(hostile), /<img src=x/);
});

test('a series: each chapter says where it comes from, an external one opens at its publisher', async () => {
  const lib = load(ANSWERS);
  await lib.openMangaSeries('anilist', '118586');
  const page = lib.elements.mangaSeries.innerHTML;
  assert.match(withCalls(page), /<h2>Frieren<\/h2>[\s\S]*3 chapitres · dernier : 3 \(2026-11-15\)[\s\S]*Une mage elfe\.[\s\S]*onclick="openMangaReader\(0, 0\)">Commencer la lecture[\s\S]*onclick="showMangaVolumes\(\)">Volumes HD/);
  assert.match(page, /Vol\. 1 · Chapitre 1<\/strong><span>Départ · Blue Solo · 3 pages · MangaDex · FR/);
  assert.match(page, /Chapitre 3<\/strong><span>WeebCentral · EN · 2026-11-15/);
  assert.match(page, /href="https:\/\/mangaplus\.example\/2" target="_blank" rel="noopener noreferrer">Chez l’éditeur ↗/);
});

test('the reader turns pages right to left, then goes on to the next chapter it can read', async () => {
  const lib = load(ANSWERS);
  await lib.openMangaSeries('anilist', '118586');
  await lib.openMangaReader(0, 0);
  assert.equal(lib.elements.mangaReader.hidden, false);
  assert.match(lib.elements.mangaReaderPages.innerHTML, /<img src="p1\.png" alt="Page 1">/);
  assert.equal(lib.elements.mangaReaderCount.textContent, '1 / 3');
  lib.turnMangaPage(true);
  assert.equal(lib.state().mangaReader.page, 1, 'left goes forward in the Japanese way');
  lib.turnMangaPage(false);
  assert.equal(lib.state().mangaReader.page, 0);
  lib.setMangaPage(2);
  assert.match(lib.elements.mangaReaderPages.innerHTML, /Chapitre suivant · Chapitre 3/, 'chapter 2 is hosted elsewhere: skipped');
  lib.timers.at(-1)();
  await new Promise(resolve => setImmediate(resolve));
  const saved = lib.calls.filter(([url]) => url === '/api/manga/progress' && lib.calls).map(([, body]) => body).filter(Boolean).at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(saved)), { source: 'anilist', series: '118586', title: 'Frieren', cover: 'c.jpg', chapter: 'md:c1',
    number: '1', lang: 'fr', page: 2, pages: 3 });
  lib.setMangaPage(3);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(lib.state().mangaReader.chapterIndex, 2);
});

test('the reading mode is remembered, and Escape closes the reader first', async () => {
  const lib = load(ANSWERS);
  await lib.openMangaSeries('anilist', '118586');
  await lib.openMangaReader(0, 0);
  lib.toggleMangaMode();
  assert.equal(lib.store['kino.mangaReader'], 'scroll|rtl');
  assert.match(lib.elements.mangaReaderPages.innerHTML, /data-page="0"[\s\S]*data-page="2"/);
  let stopped = false;
  lib.listeners[0]({ key: 'Escape', preventDefault() {}, stopImmediatePropagation() { stopped = true; } });
  assert.equal(lib.elements.mangaReader.hidden, true);
  assert.ok(stopped, 'KINO\'s own Escape (closing other windows) does not run too');
  const reopened = load({ ...ANSWERS });
  reopened.store['kino.mangaReader'] = 'scroll|ltr';
  await reopened.openMangaSeries('anilist', '118586');
  await reopened.openMangaReader(0, 0);
  assert.deepEqual([reopened.state().mangaReader.mode, reopened.state().mangaReader.rtl], ['scroll', false]);
});

test('a chapter hosted by its publisher opens there', async () => {
  const lib = load(ANSWERS);
  await lib.openMangaSeries('anilist', '118586');
  await lib.openMangaReader(1, 0);
  assert.deepEqual(lib.calls.at(-1), ['open', 'https://mangaplus.example/2']);
  assert.equal(lib.elements.mangaReader.hidden, true);
});

test('whole volumes: the torrents, the consent asked once, then the download until « Lire »', async () => {
  let asked = 0;
  const consent = Object.assign(new Error('Acceptez la lecture P2P'), { code: 'p2p_consent' });
  const lib = load({ ...ANSWERS,
    '/api/manga/volumes': { torrents: [{ hash: 'a'.repeat(40), name: 'Frieren v01-13 (Digital)', size: '2.1 GiB', seeders: 12, date: '01 Oct 2026' }] },
    '/api/manga/volume/files': () => (asked++ ? { files: [{ index: 4, name: 'Frieren v01.cbz', size: 120 }] } : consent),
    '/api/manga/volume': { progress: 0.25, done: false, error: '' },
    '/api/manga/volume/status': { progress: 1, done: true, error: '', local: 'b'.repeat(20) },
  });
  await lib.openMangaSeries('anilist', '118586');
  await lib.showMangaVolumes();
  assert.match(lib.elements.mangaVolumes.innerHTML, /Frieren v01-13 \(Digital\)<\/strong><span>2\.1 GiB · 12 sources · 01 Oct 2026/);
  await lib.openMangaTorrent('a'.repeat(40), 'Frieren v01-13 (Digital)');
  assert.ok(lib.calls.some(([what]) => what === 'consent'), 'P2P is agreed to before the torrent opens');
  assert.match(lib.elements.mangaVolumes.innerHTML, /Frieren v01\.cbz<\/strong><span>120 o/);
  await lib.downloadMangaVolume('a'.repeat(40), 4);
  assert.deepEqual(JSON.parse(JSON.stringify(lib.calls.find(([url]) => url === '/api/manga/volume')[1])), { hash: 'a'.repeat(40), index: 4, series: 'Frieren' });
  assert.match(lib.elements['q:[data-volume="4"]'].innerHTML, /25 %/);
  await lib.timers.at(-1)();
  assert.match(withCalls(lib.elements['q:[data-volume="4"]'].innerHTML), /onclick="openMangaSeries\('local', 'b{20}'\)">Lire/);
});

test('a series opened before the catalogue moved comes back under its AniList number', async () => {
  const lib = load({ ...ANSWERS, '/api/manga/progress': { progress: [{ source: 'mangadex', series: 'uuid-1', chapter: 'c1', number: '1', page: 2 }] } });
  await lib.openMangaSeries('mangadex', 'uuid-1');
  assert.deepEqual([lib.state().mangaSeries.source, lib.state().mangaSeries.id], ['anilist', '118586']);
  assert.match(lib.elements.mangaSeries.innerHTML, /Reprendre · Vol\. 1 · Chapitre 1/, 'the old chapter is found again');
});
