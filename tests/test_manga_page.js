// The Manga tab (manga.js): rows, a series with its chapters, and the reader
// that turns pages in the Japanese way and remembers where the user stopped.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const code = fs.readFileSync(path.join(ROOT, 'web/js/manga.js'), 'utf8');
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

const SERIES = { id: 's1', source: 'mangadex', title: 'Frieren', cover: 'c.jpg', year: 2020, languages: ['fr', 'en'] };
const CHAPTERS = [
  { id: 'c1', number: '1', volume: '1', title: 'Départ', pages: 3, group: 'Blue Solo', external: '' },
  { id: 'c2', number: '2', volume: '', title: '', pages: 0, group: '', external: 'https://mangaplus.example/2' },
  { id: 'c3', number: '3', volume: '', title: 'Fin', pages: 2, group: 'Blue Solo', external: '' },
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
    document: { getElementById: element, addEventListener: (type, fn) => listeners.push(fn), body: { classList: { add() {}, remove() {} } } },
    window: { KINO_LANG: 'fr', scrollTo() {}, open: url => calls.push(['open', url]) }, innerHeight: 800,
    localStorage: { getItem: key => store[key] ?? null, setItem: (key, value) => { store[key] = value; } },
    escapeHtml, attrJson: value => escapeHtml(JSON.stringify(value)), encodeURIComponent, Promise,
    setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {}, alert() {}, prompt: () => null,
    api: async (url, options) => {
      calls.push([url, options?.body ? JSON.parse(options.body) : null]);
      const answer = answers[url.split('?')[0]];
      return typeof answer === 'function' ? answer(url) : answer;
    },
  };
  vm.runInNewContext(`${code}; this.api_ = { renderMangaTab, openMangaSeries, openMangaReader, setMangaPage, turnMangaPage,
    toggleMangaMode, closeMangaReader, mangaCardHtml, state: () => ({ mangaSeries, mangaReader, mangaLang }) };`, context);
  return { ...context.api_, calls, elements, listeners, timers, store };
}

const ANSWERS = {
  '/api/manga/progress': { progress: [] },
  '/api/manga/popular': { series: [SERIES] },
  '/api/manga/local': { folder: '', series: [] },
  '/api/manga/details': { ...SERIES, description: 'Une mage elfe.', authors: ['Yamada'], tags: ['Fantasy'] },
  '/api/manga/chapters': { chapters: CHAPTERS },
  '/api/manga/pages': { pages: ['p1.png', 'p2.png', 'p3.png'] },
};

test('a new tab in the sidebar, its panel and its reader', () => {
  assert.match(html, /id="tab-manga" onclick="switchTab\('manga'\)">[\s\S]*?icons\.svg#manga"[\s\S]*?icons\.svg#manga-fill"[\s\S]*?Manga<\/button>/);
  assert.match(html, /<div id="mangaPanel" class="manga-panel" style="display:none;">/);
  assert.match(html, /<div id="mangaReader" class="manga-reader" role="dialog" aria-modal="true" aria-labelledby="mangaReaderTitle" hidden>/);
  assert.match(html, /<script src="\/js\/p2p-addons\.js"><\/script>\n\s+<script src="\/js\/manga\.js"><\/script>/);
});

test('the tab: popular series in the user language, and an invitation for their own files', async () => {
  const lib = load(ANSWERS);
  await lib.renderMangaTab();
  assert.ok(lib.calls.some(([url]) => url === '/api/manga/popular?lang=fr'), 'the interface language by default');
  const home = lib.elements.mangaHome.innerHTML;
  assert.match(home, /Populaires[\s\S]*class="manga-card" onclick="openMangaSeries\(&quot;mangadex&quot;, &quot;s1&quot;\)"/);
  assert.match(home, /Sur ce Mac[\s\S]*onclick="chooseMangaFolder\(\)">Choisir un dossier[\s\S]*Choisissez le dossier qui contient vos mangas\./);
  assert.doesNotMatch(home, /Continuer la lecture/, 'nothing read yet');
  const hostile = lib.mangaCardHtml({ ...SERIES, title: '<img src=x onerror=alert(1)>' });
  assert.doesNotMatch(hostile, /<img src=x/);
});

test('a series: its chapters with their group, an external one opens at its publisher', async () => {
  const lib = load(ANSWERS);
  await lib.openMangaSeries('mangadex', 's1');
  const page = lib.elements.mangaSeries.innerHTML;
  assert.match(page, /<h2>Frieren<\/h2>[\s\S]*Une mage elfe\.[\s\S]*onclick="openMangaReader\(0, 0\)">Commencer la lecture/);
  assert.match(page, /Vol\. 1 · Chapitre 1<\/strong><span>Départ · Blue Solo · 3 pages/);
  assert.match(page, /href="https:\/\/mangaplus\.example\/2" target="_blank" rel="noopener noreferrer">Chez l’éditeur ↗/);
});

test('the reader turns pages right to left, then goes on to the next chapter it can read', async () => {
  const lib = load(ANSWERS);
  await lib.openMangaSeries('mangadex', 's1');
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
  assert.deepEqual(JSON.parse(JSON.stringify(saved)), { source: 'mangadex', series: 's1', title: 'Frieren', cover: 'c.jpg', chapter: 'c1',
    number: '1', lang: 'fr', page: 2, pages: 3 });
  lib.setMangaPage(3);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(lib.state().mangaReader.chapterIndex, 2);
});

test('the reading mode is remembered, and Escape closes the reader first', async () => {
  const lib = load(ANSWERS);
  await lib.openMangaSeries('mangadex', 's1');
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
  await reopened.openMangaSeries('mangadex', 's1');
  await reopened.openMangaReader(0, 0);
  assert.deepEqual([reopened.state().mangaReader.mode, reopened.state().mangaReader.rtl], ['scroll', false]);
});

test('a chapter hosted by its publisher opens there', async () => {
  const lib = load(ANSWERS);
  await lib.openMangaSeries('mangadex', 's1');
  await lib.openMangaReader(1, 0);
  assert.deepEqual(lib.calls.at(-1), ['open', 'https://mangaplus.example/2']);
  assert.equal(lib.elements.mangaReader.hidden, true);
});
