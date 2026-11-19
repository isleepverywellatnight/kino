// @ts-check
// The Manga tab: AniList's catalogue and the user's own files (src/manga.py), a
// page per series with its chapters gathered from several sites and its whole
// volumes by torrent (manga_volumes.py), and a reader (page by page, or one long
// scroll for webtoons) that remembers where the user stopped.

const MANGA_LANG_KEY = 'kino.mangaLang';
const MANGA_READER_KEY = 'kino.mangaReader';
const MANGA_FROM_UI = { fr: 'fr', en: 'en', es: 'es', de: 'de', it: 'it', pt: 'pt-br', ru: 'ru', zh: 'zh', ja: 'ja' };
let mangaLang = 'fr';
let mangaSeries = null;   // { source, id, title, cover, chapters }
let mangaReader = null;   // { chapterIndex, pages, page, mode, rtl }
let mangaSaveTimer = null;
let mangaVolumeTimers = {};
const MANGA_ROWS = [['trending', 'Tendances', 'Les séries en cours de parution les plus lues en ce moment'],
  ['popular', 'Les plus populaires', ''], ['webtoon', 'Manhwa et webtoons', 'Séries coréennes']];
const MANGA_LAST_CHAPTER = 'Dernier chapitre disponible.';
const MANGA_NO_ARCHIVE = 'Ce torrent ne contient pas de fichier CBZ.';

function mangaStored(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (error) { return fallback; }
}

function mangaStore(key, value) {
  try { localStorage.setItem(key, value); } catch (error) {}
}

function initMangaLang() {
  mangaLang = mangaStored(MANGA_LANG_KEY, MANGA_FROM_UI[window.KINO_LANG] || 'en');
  const select = /** @type {HTMLSelectElement | null} */ (document.getElementById('mangaLang'));
  if (select) select.value = mangaLang;
}

function setMangaLang(value) {
  mangaLang = value;
  mangaStore(MANGA_LANG_KEY, value);
  if (mangaSeries) openMangaSeries(mangaSeries.source, mangaSeries.id);
  else renderMangaTab();
}

// ---------- The tab ----------

/** @param {any} series @param {unknown} [extra] */
function mangaCardHtml(series, extra = '') {
  return html`<button type="button" class="manga-card" ${act('openMangaSeries', series.source, series.id)} title="${series.title}">
      <span class="manga-cover">${series.cover ? html`<img src="${series.cover}" alt="" loading="lazy" ${actOn('error', 'kinoRemove', THIS)}>` : ''}</span>
      <span class="manga-card-title">${series.title}</span>
      ${extra ? html`<span class="manga-card-meta">${extra}</span>` : ''}
    </button>`;
}

/** @param {unknown} body @param {unknown} [actions] */
function mangaShelf(title, meta, body, actions = '') {
  return html`<section class="list-shelf manga-shelf" aria-label="${title}">
      <div class="list-shelf-head">
        <div class="list-shelf-info"><h3 class="list-shelf-title">${title}</h3>${meta ? html`<div class="list-shelf-meta">${meta}</div>` : ''}</div>
        ${actions ? html`<div class="list-shelf-actions">${actions}</div>` : ''}
      </div>
      ${body}
    </section>`;
}

/** @param {(item: any) => unknown} [extra] */
function mangaRow(items, empty, extra = () => '') {
  return items.length ? html`<div class="list-shelf-row">${items.map(item => mangaCardHtml(item, extra(item)))}</div>`
    : html`<p class="list-shelf-empty">${empty}</p>`;
}

async function renderMangaTab() {
  const home = document.getElementById('mangaHome');
  const page = document.getElementById('mangaSeries');
  if (!home) return;
  initMangaLang();
  mangaSeries = null;
  if (page) page.hidden = true;
  home.hidden = false;
  render(home, html`<p class="list-shelf-empty">Chargement…</p>`);
  const [progress, local, ...rows] = await Promise.all([
    api('/api/manga/progress').catch(() => ({ progress: [] })),
    api('/api/manga/local').catch(() => ({ folder: '', series: [] })),
    ...MANGA_ROWS.map(([name]) => api(`/api/manga/row?name=${name}`).catch(error => ({ error: error.message, series: [] }))),
  ]);
  const reading = (progress.progress || []).map(p => ({ source: p.source, id: p.series, title: p.title, cover: p.cover, progress: p }));
  const folderLabel = local.folder ? 'Changer de dossier' : 'Choisir un dossier';
  const folderButton = html`<button type="button" class="btn btn-secondary btn-small" ${act('chooseMangaFolder')}>${folderLabel}</button>`;
  render(home, [(reading.length ? mangaShelf('Continuer la lecture', '', mangaRow(reading, '', item =>
      item.progress.number ? `Chapitre ${item.progress.number}` : '')) : ''),
    MANGA_ROWS.map(([, title, meta], index) => {
      const data = rows[index] || {};
      return mangaShelf(title, meta, data.error ? html`<p class="list-shelf-empty">${data.error}</p>`
        : mangaRow(data.series || [], 'Aucune série pour l’instant.'));
    }),
    mangaShelf('Sur ce Mac', local.folder || 'Vos fichiers CBZ et dossiers d’images',
      mangaRow(local.series || [], local.folder ? 'Aucune série trouvée dans ce dossier.' : 'Choisissez le dossier qui contient vos mangas.',
        item => `${item.chapters} chapitre${item.chapters > 1 ? 's' : ''}`), folderButton)]);
}

async function searchManga() {
  const query = /** @type {HTMLInputElement | null} */ (document.getElementById('mangaSearchInput'))?.value.trim();
  const home = document.getElementById('mangaHome');
  if (!query || !home) return renderMangaTab();
  document.getElementById('mangaSeries').hidden = true;
  home.hidden = false;
  render(home, html`<p class="list-shelf-empty">Recherche…</p>`);
  try {
    const data = await api(`/api/manga/search?lang=${encodeURIComponent(mangaLang)}&q=${encodeURIComponent(query)}`);
    render(home, mangaShelf(`Résultats pour « ${query} »`, '',
      (data.series || []).length ? html`<div class="manga-grid">${data.series.map(s => mangaCardHtml(s, s.year ? String(s.year) : ''))}</div>`
        : html`<p class="list-shelf-empty">Aucune série trouvée.</p>`,
      html`<button type="button" class="btn btn-secondary btn-small" ${act('renderMangaTab')}>← Manga</button>`));
  } catch (error) {
    render(home, html`<p class="list-shelf-empty">${error.message}</p>`);
  }
}

async function chooseMangaFolder() {
  try {
    if (window.pywebview?.api?.choose_manga_folder) {
      await window.pywebview.api.choose_manga_folder();
    } else {
      const path = prompt('Dossier qui contient vos mangas (chemin complet) :');
      if (!path) return;
      await api('/api/manga/folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) });
    }
  } catch (error) {
    alert(error.message || String(error));
  }
  renderMangaTab();
}

// ---------- A series ----------

function mangaChapterLabel(chapter) {
  return [chapter.volume ? `Vol. ${chapter.volume}` : '', chapter.number ? `Chapitre ${chapter.number}` : 'Chapitre unique'].filter(Boolean).join(' · ');
}

async function openMangaSeries(source, id) {
  const page = document.getElementById('mangaSeries');
  const home = document.getElementById('mangaHome');
  if (!page) return;
  home.hidden = true;
  page.hidden = false;
  render(page, html`<p class="list-shelf-empty">Chargement…</p>`);
  window.scrollTo({ top: 0, behavior: 'auto' });
  try {
    const saved = (await api('/api/manga/progress')).progress || [];
    let progress = saved.find(p => p.source === source && p.series === id);
    let info, chapters;
    if (source === 'local') {
      const data = await api(`/api/manga/local/chapters?series=${encodeURIComponent(id)}`);
      info = { title: data.title, cover: `/api/manga/local/page?series=${encodeURIComponent(id)}&chapter=0&page=0`, description: '', authors: [], tags: [] };
      chapters = data.chapters;
    } else {
      [info, chapters] = await Promise.all([
        api(`/api/manga/details?lang=${encodeURIComponent(mangaLang)}&id=${encodeURIComponent(id)}`),
        api(`/api/manga/chapters?lang=${encodeURIComponent(mangaLang)}&id=${encodeURIComponent(id)}`).then(d => d.chapters || []),
      ]);
    }
    // A series opened before the catalogue moved to AniList comes back under its AniList number.
    if (source === 'mangadex' && info.source === 'anilist') {
      progress = saved.find(p => p.source === 'anilist' && p.series === info.id) || progress;
      source = info.source;
      id = info.id;
    }
    mangaSeries = { source, id, title: info.title, cover: info.cover, chapters, progress, info };
    renderMangaSeries(info);
  } catch (error) {
    render(page, html`<button type="button" class="btn btn-secondary lists-back" ${act('renderMangaTab')}>← Manga</button><p class="list-shelf-empty">${error.message}</p>`);
  }
}

function renderMangaSeries(info) {
  const page = document.getElementById('mangaSeries');
  const { chapters, progress } = mangaSeries;
  const resumeIndex = progress ? chapters.findIndex(c => c.id === progress.chapter || c.id === `md:${progress.chapter}`
    || (progress.number && c.number === progress.number)) : -1;
  const first = chapters.findIndex(c => !c.external);
  const start = resumeIndex >= 0
    ? html`<button type="button" class="btn" ${act('openMangaReader', resumeIndex, Number(progress.page) || 0)}>Reprendre · ${mangaChapterLabel(chapters[resumeIndex])}</button>`
    : (first >= 0 ? html`<button type="button" class="btn" ${act('openMangaReader', first, 0)}>Commencer la lecture</button>` : '');
  const online = mangaSeries.source === 'anilist';
  const empty = online && !chapters.length ? html`<p class="list-shelf-empty">Aucun chapitre trouvé pour cette série pour l’instant.</p>` : '';
  const last = chapters.length ? chapters[chapters.length - 1] : null;
  const count = online && last ? `${chapters.length} chapitre${chapters.length > 1 ? 's' : ''} · dernier : ${last.number || '?'}${last.date ? ` (${last.date})` : ''}` : '';
  const volumes = online ? html`<button type="button" class="btn btn-secondary" ${act('showMangaVolumes')}>Volumes HD (torrent)</button>` : '';
  render(page, html`
    <button type="button" class="btn btn-secondary lists-back" ${act('renderMangaTab')}>← Manga</button>
    <div class="manga-series-head">
      <div class="manga-cover manga-cover-large">${info.cover ? html`<img src="${info.cover}" alt="">` : ''}</div>
      <div class="manga-series-text">
        <h2>${info.title}</h2>
        <p class="manga-series-meta">${[info.authors?.join(', '), info.year, info.tags?.slice(0, 4).join(' · ')].filter(Boolean).join(' · ')}</p>
        ${count ? html`<p class="manga-series-meta">${count}</p>` : ''}
        ${info.description ? html`<p class="manga-series-desc">${info.description}</p>` : ''}
        <div class="manga-series-actions">${start}${volumes}</div>
      </div>
    </div>
    <div id="mangaVolumes" class="manga-volumes" hidden></div>
    ${empty}
    <ol class="manga-chapters">${chapters.map((chapter, index) => {
      const read = resumeIndex >= 0 && index < resumeIndex;
      const meta = [chapter.title, chapter.group, chapter.pages ? `${chapter.pages} pages` : '',
        chapter.source ? `${chapter.source}${chapter.lang ? ` · ${chapter.lang.toUpperCase()}` : ''}` : '', chapter.date].filter(Boolean).join(' · ');
      const action = chapter.external
        ? html`<a class="btn btn-secondary btn-small" href="${safeUrl(chapter.external)}" target="_blank" rel="noopener noreferrer">Chez l’éditeur ↗</a>`
        : html`<button type="button" class="btn btn-secondary btn-small" ${act('openMangaReader', index, 0)}>Lire</button>`;
      return html`<li class="manga-chapter${read ? ' is-read' : ''}${index === resumeIndex ? ' is-current' : ''}">
          <div><strong>${mangaChapterLabel(chapter)}</strong>${meta ? html`<span>${meta}</span>` : ''}</div>${action}</li>`;
    })}</ol>`);
}

// ---------- Whole volumes by torrent ----------

function mangaVolumesBox(markup) {
  const box = document.getElementById('mangaVolumes');
  if (box) {
    box.hidden = false;
    render(box, markup);
  }
  return box;
}

async function showMangaVolumes() {
  if (!mangaSeries) return;
  mangaVolumesBox(html`<p class="list-shelf-empty">Recherche des volumes…</p>`);
  try {
    const data = await api(`/api/manga/volumes?id=${encodeURIComponent(mangaSeries.id)}`);
    const torrents = data.torrents || [];
    mangaVolumesBox(html`<h3 class="list-shelf-title">Volumes HD</h3>
      <p class="manga-volumes-note">Volumes complets partagés en torrent, souvent les éditions numériques officielles. Ils se téléchargent en P2P dans votre dossier manga, puis se lisent hors ligne.</p>
      ${torrents.length ? html`<ul class="manga-torrents">${torrents.map(t => html`<li>
          <div><strong>${t.name}</strong><span>${[t.size, `${t.seeders} source${t.seeders > 1 ? 's' : ''}`, t.date].filter(Boolean).join(' · ')}</span></div>
          <button type="button" class="btn btn-secondary btn-small" ${act('openMangaTorrent', t.hash, t.name)}>Voir les volumes</button>
        </li>`)}</ul>` : html`<p class="list-shelf-empty">Aucun volume en torrent pour cette série.</p>`}`);
  } catch (error) {
    mangaVolumesBox(html`<p class="list-shelf-empty">${error.message}</p>`);
  }
}

async function openMangaTorrent(hash, name) {
  mangaVolumesBox(html`<p class="list-shelf-empty">Ouverture du torrent… (jusqu’à 30 secondes)</p>`);
  let data;
  try {
    data = await api(`/api/manga/volume/files?hash=${encodeURIComponent(hash)}&name=${encodeURIComponent(name)}`);
  } catch (error) {
    if (error.code === 'p2p_consent' && typeof askP2PConsent === 'function' && await askP2PConsent()) return openMangaTorrent(hash, name);
    mangaVolumesBox(html`<p class="list-shelf-empty">${error.message}</p><button type="button" class="btn btn-secondary btn-small" ${act('showMangaVolumes')}>← Volumes</button>`);
    return;
  }
  const files = data.files || [];
  mangaVolumesBox(html`<h3 class="list-shelf-title">${name}</h3>
    <button type="button" class="btn btn-secondary btn-small" ${act('showMangaVolumes')}>← Volumes</button>
    ${files.length ? html`<ul class="manga-torrents">${files.map(f => html`<li>
        <div><strong>${f.name}</strong><span>${typeof offlineSize === 'function' ? offlineSize(f.size) : `${f.size}`}</span></div>
        <span class="manga-volume-state" data-volume="${f.index}"><button type="button" class="btn btn-secondary btn-small" ${act('downloadMangaVolume', hash, f.index)}>Télécharger</button></span>
      </li>`)}</ul>` : html`<p class="list-shelf-empty">${MANGA_NO_ARCHIVE}</p>`}`);
}

function mangaVolumeState(index, markup) {
  const cell = document.querySelector(`[data-volume="${index}"]`);
  if (cell) render(cell, markup);
}

async function downloadMangaVolume(hash, index) {
  mangaVolumeState(index, html`<span class="manga-volume-progress">Démarrage…</span>`);
  try {
    const state = await api('/api/manga/volume', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hash, index, series: mangaSeries?.title || '' }) });
    followMangaVolume(hash, index, state);
  } catch (error) {
    if (error.code === 'p2p_consent' && typeof askP2PConsent === 'function' && await askP2PConsent()) return downloadMangaVolume(hash, index);
    mangaVolumeState(index, html`<span class="manga-volume-progress">${error.message}</span>`);
  }
}

function followMangaVolume(hash, index, state) {
  const key = `${hash}:${index}`;
  clearTimeout(mangaVolumeTimers[key]);
  if (state.error) {
    mangaVolumeState(index, html`<span class="manga-volume-progress">${state.error}</span>`);
    return;
  }
  if (state.done) {
    mangaVolumeState(index, state.local
      ? html`<button type="button" class="btn btn-small" ${act('openMangaSeries', 'local', state.local)}>Lire</button>`
      : html`<span class="manga-volume-progress">Téléchargé</span>`);
    return;
  }
  mangaVolumeState(index, html`<span class="manga-volume-progress">${Math.floor((state.progress || 0) * 100)} %</span>`);
  mangaVolumeTimers[key] = setTimeout(async () => {
    try {
      followMangaVolume(hash, index, await api(`/api/manga/volume/status?hash=${encodeURIComponent(hash)}&index=${index}`));
    } catch (error) {
      mangaVolumeState(index, html`<span class="manga-volume-progress">${error.message}</span>`);
    }
  }, 2000);
}

// ---------- The reader ----------

async function mangaPages(chapter) {
  if (mangaSeries.source === 'local') {
    return Array.from({ length: chapter.pages }, (_, page) =>
      `/api/manga/local/page?series=${encodeURIComponent(mangaSeries.id)}&chapter=${encodeURIComponent(chapter.id)}&page=${page}`);
  }
  return (await api(`/api/manga/pages?chapter=${encodeURIComponent(chapter.id)}`)).pages || [];
}

async function openMangaReader(chapterIndex, startPage = 0) {
  const chapter = mangaSeries?.chapters?.[chapterIndex];
  const box = document.getElementById('mangaReader');
  if (!chapter || !box) return;
  if (chapter.external) { window.open(chapter.external, '_blank', 'noopener'); return; }
  const settings = mangaStored(MANGA_READER_KEY, 'paged|rtl').split('|');
  mangaReader = { chapterIndex, pages: [], page: 0, mode: settings[0] === 'scroll' ? 'scroll' : 'paged', rtl: settings[1] !== 'ltr' };
  box.hidden = false;
  document.body.classList.add('manga-reading');
  document.getElementById('mangaReaderTitle').textContent = mangaSeries.title;
  document.getElementById('mangaReaderChapter').textContent = mangaChapterLabel(chapter);
  render(document.getElementById('mangaReaderPages'), html`<p class="manga-reader-note">Chargement du chapitre…</p>`);
  try {
    mangaReader.pages = await mangaPages(chapter);
  } catch (error) {
    render(document.getElementById('mangaReaderPages'), html`<p class="manga-reader-note">${error.message}</p>`);
    return;
  }
  mangaReader.page = Math.min(Math.max(0, startPage), Math.max(0, mangaReader.pages.length - 1));
  renderMangaReader();
}

function renderMangaReader() {
  const box = document.getElementById('mangaReaderPages');
  if (!mangaReader || !box) return;
  const { pages, page, mode, rtl } = mangaReader;
  document.getElementById('mangaReaderMode').textContent = mode === 'scroll' ? 'Page par page' : 'Défilement';
  const direction = document.getElementById('mangaReaderDirection');
  direction.textContent = rtl ? 'Sens japonais' : 'Sens occidental';
  // The player buttons' style shows them whatever « hidden » says.
  direction.style.display = mode === 'scroll' ? 'none' : '';
  box.className = `manga-reader-pages is-${mode}`;
  if (!pages.length) {
    render(box, html`<p class="manga-reader-note">Ce chapitre n’a pas de pages.</p>`);
    return;
  }
  if (mode === 'scroll') {
    render(box, [pages.map((src, index) => html`<img src="${src}" alt="Page ${index + 1}" loading="lazy" data-page="${index}">`),
      mangaEndHtml()]);
    const current = box.querySelector(`[data-page="${page}"]`);
    if (current && typeof current.scrollIntoView === 'function') current.scrollIntoView({ block: 'start' });
    box.onscroll = () => {
      const seen = [.../** @type {NodeListOf<HTMLImageElement>} */ (box.querySelectorAll('img[data-page]'))].filter(img => img.getBoundingClientRect().top < innerHeight / 2).pop();
      if (seen) setMangaPage(Number(seen.dataset.page), false);
    };
  } else {
    box.onscroll = null;
    render(box, [html`<img src="${pages[page]}" alt="Page ${page + 1}">`,
      pages.slice(page + 1, page + 3).map(src => html`<link rel="prefetch" href="${safeUrl(src)}">`),
      (page === pages.length - 1 ? mangaEndHtml() : '')]);
  }
  updateMangaCount();
}

function mangaEndHtml() {
  const next = mangaSeries.chapters.slice(mangaReader.chapterIndex + 1).findIndex(c => !c.external);
  return html`<div class="manga-reader-end">${next >= 0
    ? html`<button type="button" class="btn" ${act('openMangaReader', mangaReader.chapterIndex + 1 + next, 0)} data-click-stop>Chapitre suivant · ${mangaChapterLabel(mangaSeries.chapters[mangaReader.chapterIndex + 1 + next])}</button>`
    : html`<p>${MANGA_LAST_CHAPTER}</p>`}</div>`;
}

function updateMangaCount() {
  const count = document.getElementById('mangaReaderCount');
  if (count && mangaReader) count.textContent = `${mangaReader.page + 1} / ${mangaReader.pages.length}`;
}

function setMangaPage(page, redraw = true) {
  if (!mangaReader?.pages.length) return;
  const last = mangaReader.pages.length - 1;
  if (page > last && render) {
    const next = mangaSeries.chapters.slice(mangaReader.chapterIndex + 1).findIndex(c => !c.external);
    if (next >= 0) openMangaReader(mangaReader.chapterIndex + 1 + next, 0);
    return;
  }
  if (page < 0 && redraw) {
    if (mangaReader.chapterIndex > 0) openMangaReader(mangaReader.chapterIndex - 1, 0);
    return;
  }
  mangaReader.page = Math.min(Math.max(0, page), last);
  if (redraw) renderMangaReader();
  else updateMangaCount();
  saveMangaProgressSoon();
}

// Forward follows the reading direction: right to left in the Japanese way.
function turnMangaPage(towardsLeft) {
  if (!mangaReader || mangaReader.mode !== 'paged') return;
  const forward = mangaReader.rtl ? towardsLeft : !towardsLeft;
  setMangaPage(mangaReader.page + (forward ? 1 : -1));
}

function mangaReaderClick(event) {
  if (!mangaReader || mangaReader.mode !== 'paged' || event.target.closest('button')) return;
  const box = event.currentTarget.getBoundingClientRect();
  turnMangaPage(event.clientX < box.left + box.width / 2);
}

function toggleMangaMode() {
  mangaReader.mode = mangaReader.mode === 'scroll' ? 'paged' : 'scroll';
  mangaStore(MANGA_READER_KEY, `${mangaReader.mode}|${mangaReader.rtl ? 'rtl' : 'ltr'}`);
  renderMangaReader();
}

function toggleMangaDirection() {
  mangaReader.rtl = !mangaReader.rtl;
  mangaStore(MANGA_READER_KEY, `${mangaReader.mode}|${mangaReader.rtl ? 'rtl' : 'ltr'}`);
  renderMangaReader();
}

function saveMangaProgressSoon() {
  clearTimeout(mangaSaveTimer);
  mangaSaveTimer = setTimeout(saveMangaProgress, 800);
}

async function saveMangaProgress() {
  if (!mangaReader || !mangaSeries) return;
  const chapter = mangaSeries.chapters[mangaReader.chapterIndex];
  const entry = { source: mangaSeries.source, series: mangaSeries.id, title: mangaSeries.title, cover: mangaSeries.cover,
    chapter: chapter.id, number: chapter.number, lang: mangaLang, page: mangaReader.page, pages: mangaReader.pages.length };
  mangaSeries.progress = entry;
  try {
    await api('/api/manga/progress', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(entry) });
  } catch (error) {}
}

function closeMangaReader() {
  const box = document.getElementById('mangaReader');
  if (!box || box.hidden) return false;
  clearTimeout(mangaSaveTimer);
  saveMangaProgress();
  box.hidden = true;
  document.body.classList.remove('manga-reading');
  render(document.getElementById('mangaReaderPages'), '');
  mangaReader = null;
  if (mangaSeries) openMangaSeries(mangaSeries.source, mangaSeries.id);
  return true;
}

// The reader's keys come before the rest of KINO's.
document.addEventListener('keydown', event => {
  const box = document.getElementById('mangaReader');
  if (!box || box.hidden) return;
  if (event.key === 'Escape') closeMangaReader();
  else if (event.key === 'ArrowLeft') turnMangaPage(true);
  else if (event.key === 'ArrowRight') turnMangaPage(false);
  else return;
  event.preventDefault();
  event.stopImmediatePropagation();
}, true);

// What the markup may run from this file (web/js/actions.js).
registerActions({
  chooseMangaFolder, closeMangaReader, downloadMangaVolume, mangaReaderClick, openMangaReader,
  openMangaSeries, openMangaTorrent, renderMangaTab, searchManga, setMangaLang, showMangaVolumes,
  toggleMangaDirection, toggleMangaMode,
});
