// The Manga tab: series from MangaDex and from the user's own files (src/manga.py),
// a page per series with its chapters, and a reader (page by page, or one long
// scroll for webtoons), that remembers where the user stopped.

const MANGA_LANG_KEY = 'kino.mangaLang';
const MANGA_READER_KEY = 'kino.mangaReader';
const MANGA_FROM_UI = { fr: 'fr', en: 'en', es: 'es', de: 'de', it: 'it', pt: 'pt-br', ru: 'ru', zh: 'zh', ja: 'ja' };
let mangaLang = 'fr';
let mangaSeries = null;   // { source, id, title, cover, chapters }
let mangaReader = null;   // { chapterIndex, pages, page, mode, rtl }
let mangaSaveTimer = null;
const MANGA_LAST_CHAPTER = 'Dernier chapitre disponible.';

function mangaStored(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (error) { return fallback; }
}

function mangaStore(key, value) {
  try { localStorage.setItem(key, value); } catch (error) {}
}

function initMangaLang() {
  mangaLang = mangaStored(MANGA_LANG_KEY, MANGA_FROM_UI[window.KINO_LANG] || 'en');
  const select = document.getElementById('mangaLang');
  if (select) select.value = mangaLang;
}

function setMangaLang(value) {
  mangaLang = value;
  mangaStore(MANGA_LANG_KEY, value);
  if (mangaSeries) openMangaSeries(mangaSeries.source, mangaSeries.id);
  else renderMangaTab();
}

// ---------- The tab ----------

function mangaCardHtml(series, extra = '') {
  return `<button type="button" class="manga-card" onclick="openMangaSeries(${attrJson(series.source)}, ${attrJson(series.id)})" title="${escapeHtml(series.title)}">
      <span class="manga-cover">${series.cover ? `<img src="${escapeHtml(series.cover)}" alt="" loading="lazy" onerror="this.remove()">` : ''}</span>
      <span class="manga-card-title">${escapeHtml(series.title)}</span>
      ${extra ? `<span class="manga-card-meta">${escapeHtml(extra)}</span>` : ''}
    </button>`;
}

function mangaShelf(title, meta, body, actions = '') {
  return `<section class="list-shelf manga-shelf" aria-label="${escapeHtml(title)}">
      <div class="list-shelf-head">
        <div class="list-shelf-info"><h3 class="list-shelf-title">${escapeHtml(title)}</h3>${meta ? `<div class="list-shelf-meta">${escapeHtml(meta)}</div>` : ''}</div>
        ${actions ? `<div class="list-shelf-actions">${actions}</div>` : ''}
      </div>
      ${body}
    </section>`;
}

function mangaRow(items, empty, extra = () => '') {
  return items.length ? `<div class="list-shelf-row">${items.map(item => mangaCardHtml(item, extra(item))).join('')}</div>`
    : `<p class="list-shelf-empty">${escapeHtml(empty)}</p>`;
}

async function renderMangaTab() {
  const home = document.getElementById('mangaHome');
  const page = document.getElementById('mangaSeries');
  if (!home) return;
  initMangaLang();
  mangaSeries = null;
  if (page) page.hidden = true;
  home.hidden = false;
  home.innerHTML = '<p class="list-shelf-empty">Chargement…</p>';
  const [progress, popular, local] = await Promise.all([
    api('/api/manga/progress').catch(() => ({ progress: [] })),
    api(`/api/manga/popular?lang=${encodeURIComponent(mangaLang)}`).catch(error => ({ error: error.message, series: [] })),
    api('/api/manga/local').catch(() => ({ folder: '', series: [] })),
  ]);
  const reading = (progress.progress || []).map(p => ({ source: p.source, id: p.series, title: p.title, cover: p.cover, progress: p }));
  const folderLabel = local.folder ? 'Changer de dossier' : 'Choisir un dossier';
  const folderButton = `<button type="button" class="btn btn-secondary btn-small" onclick="chooseMangaFolder()">${folderLabel}</button>`;
  home.innerHTML = (reading.length ? mangaShelf('Continuer la lecture', '', mangaRow(reading, '', item =>
      item.progress.number ? `Chapitre ${item.progress.number}` : '')) : '')
    + mangaShelf('Populaires', 'MangaDex · chapitres dans la langue choisie',
      popular.error ? `<p class="list-shelf-empty">${escapeHtml(popular.error)}</p>` : mangaRow(popular.series || [], 'Aucune série dans cette langue.'))
    + mangaShelf('Sur ce Mac', local.folder || 'Vos fichiers CBZ et dossiers d’images',
      mangaRow(local.series || [], local.folder ? 'Aucune série trouvée dans ce dossier.' : 'Choisissez le dossier qui contient vos mangas.',
        item => `${item.chapters} chapitre${item.chapters > 1 ? 's' : ''}`), folderButton);
}

async function searchManga() {
  const query = document.getElementById('mangaSearchInput')?.value.trim();
  const home = document.getElementById('mangaHome');
  if (!query || !home) return renderMangaTab();
  document.getElementById('mangaSeries').hidden = true;
  home.hidden = false;
  home.innerHTML = '<p class="list-shelf-empty">Recherche…</p>';
  try {
    const data = await api(`/api/manga/search?lang=${encodeURIComponent(mangaLang)}&q=${encodeURIComponent(query)}`);
    home.innerHTML = mangaShelf(`Résultats pour « ${query} »`, 'MangaDex',
      (data.series || []).length ? `<div class="manga-grid">${data.series.map(s => mangaCardHtml(s, s.year ? String(s.year) : '')).join('')}</div>`
        : '<p class="list-shelf-empty">Aucune série trouvée.</p>',
      '<button type="button" class="btn btn-secondary btn-small" onclick="renderMangaTab()">← Manga</button>');
  } catch (error) {
    home.innerHTML = `<p class="list-shelf-empty">${escapeHtml(error.message)}</p>`;
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
  page.innerHTML = '<p class="list-shelf-empty">Chargement…</p>';
  window.scrollTo({ top: 0, behavior: 'auto' });
  try {
    const progress = ((await api('/api/manga/progress')).progress || []).find(p => p.source === source && p.series === id);
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
    mangaSeries = { source, id, title: info.title, cover: info.cover, chapters, progress };
    renderMangaSeries(info);
  } catch (error) {
    page.innerHTML = `<button type="button" class="btn btn-secondary lists-back" onclick="renderMangaTab()">← Manga</button><p class="list-shelf-empty">${escapeHtml(error.message)}</p>`;
  }
}

function renderMangaSeries(info) {
  const page = document.getElementById('mangaSeries');
  const { chapters, progress } = mangaSeries;
  const resumeIndex = progress ? chapters.findIndex(c => c.id === progress.chapter) : -1;
  const first = chapters.findIndex(c => !c.external);
  const start = resumeIndex >= 0
    ? `<button type="button" class="btn" onclick="openMangaReader(${resumeIndex}, ${Number(progress.page) || 0})">Reprendre · ${escapeHtml(mangaChapterLabel(chapters[resumeIndex]))}</button>`
    : (first >= 0 ? `<button type="button" class="btn" onclick="openMangaReader(${first}, 0)">Commencer la lecture</button>` : '');
  const languages = mangaSeries.source === 'mangadex' && !chapters.length
    ? `<p class="list-shelf-empty">Aucun chapitre dans cette langue. Choisissez-en une autre en haut de la page${info.languages?.length ? ` (disponible : ${escapeHtml(info.languages.slice(0, 8).join(', '))})` : ''}.</p>` : '';
  page.innerHTML = `
    <button type="button" class="btn btn-secondary lists-back" onclick="renderMangaTab()">← Manga</button>
    <div class="manga-series-head">
      <div class="manga-cover manga-cover-large">${info.cover ? `<img src="${escapeHtml(info.cover)}" alt="">` : ''}</div>
      <div class="manga-series-text">
        <h2>${escapeHtml(info.title)}</h2>
        <p class="manga-series-meta">${escapeHtml([info.authors?.join(', '), info.year, info.tags?.slice(0, 4).join(' · ')].filter(Boolean).join(' · '))}</p>
        ${info.description ? `<p class="manga-series-desc">${escapeHtml(info.description)}</p>` : ''}
        <div class="manga-series-actions">${start}</div>
      </div>
    </div>
    ${languages}
    <ol class="manga-chapters">${chapters.map((chapter, index) => {
      const read = resumeIndex >= 0 && index < resumeIndex;
      const meta = [chapter.title, chapter.group, chapter.pages ? `${chapter.pages} pages` : ''].filter(Boolean).join(' · ');
      const action = chapter.external
        ? `<a class="btn btn-secondary btn-small" href="${escapeHtml(chapter.external)}" target="_blank" rel="noopener noreferrer">Chez l’éditeur ↗</a>`
        : `<button type="button" class="btn btn-secondary btn-small" onclick="openMangaReader(${index}, 0)">Lire</button>`;
      return `<li class="manga-chapter${read ? ' is-read' : ''}${index === resumeIndex ? ' is-current' : ''}">
          <div><strong>${escapeHtml(mangaChapterLabel(chapter))}</strong>${meta ? `<span>${escapeHtml(meta)}</span>` : ''}</div>${action}</li>`;
    }).join('')}</ol>`;
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
  document.getElementById('mangaReaderPages').innerHTML = '<p class="manga-reader-note">Chargement du chapitre…</p>';
  try {
    mangaReader.pages = await mangaPages(chapter);
  } catch (error) {
    document.getElementById('mangaReaderPages').innerHTML = `<p class="manga-reader-note">${escapeHtml(error.message)}</p>`;
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
    box.innerHTML = '<p class="manga-reader-note">Ce chapitre n’a pas de pages.</p>';
    return;
  }
  if (mode === 'scroll') {
    box.innerHTML = pages.map((src, index) => `<img src="${escapeHtml(src)}" alt="Page ${index + 1}" loading="lazy" data-page="${index}">`).join('')
      + mangaEndHtml();
    const current = box.querySelector(`[data-page="${page}"]`);
    if (current && typeof current.scrollIntoView === 'function') current.scrollIntoView({ block: 'start' });
    box.onscroll = () => {
      const seen = [...box.querySelectorAll('img[data-page]')].filter(img => img.getBoundingClientRect().top < innerHeight / 2).pop();
      if (seen) setMangaPage(Number(seen.dataset.page), false);
    };
  } else {
    box.onscroll = null;
    box.innerHTML = `<img src="${escapeHtml(pages[page])}" alt="Page ${page + 1}">`
      + pages.slice(page + 1, page + 3).map(src => `<link rel="prefetch" href="${escapeHtml(src)}">`).join('')
      + (page === pages.length - 1 ? mangaEndHtml() : '');
  }
  updateMangaCount();
}

function mangaEndHtml() {
  const next = mangaSeries.chapters.slice(mangaReader.chapterIndex + 1).findIndex(c => !c.external);
  return `<div class="manga-reader-end">${next >= 0
    ? `<button type="button" class="btn" onclick="event.stopPropagation(); openMangaReader(${mangaReader.chapterIndex + 1 + next}, 0)">Chapitre suivant · ${escapeHtml(mangaChapterLabel(mangaSeries.chapters[mangaReader.chapterIndex + 1 + next]))}</button>`
    : `<p>${MANGA_LAST_CHAPTER}</p>`}</div>`;
}

function updateMangaCount() {
  const count = document.getElementById('mangaReaderCount');
  if (count && mangaReader) count.textContent = `${mangaReader.page + 1} / ${mangaReader.pages.length}`;
}

function setMangaPage(page, render = true) {
  if (!mangaReader?.pages.length) return;
  const last = mangaReader.pages.length - 1;
  if (page > last && render) {
    const next = mangaSeries.chapters.slice(mangaReader.chapterIndex + 1).findIndex(c => !c.external);
    if (next >= 0) openMangaReader(mangaReader.chapterIndex + 1 + next, 0);
    return;
  }
  if (page < 0 && render) {
    if (mangaReader.chapterIndex > 0) openMangaReader(mangaReader.chapterIndex - 1, 0);
    return;
  }
  mangaReader.page = Math.min(Math.max(0, page), last);
  if (render) renderMangaReader();
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
  document.getElementById('mangaReaderPages').innerHTML = '';
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
