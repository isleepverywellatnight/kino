// --- « Nouveaux épisodes »: series with episodes released since the last viewing ---
let newEpisodes = [];
let newEpisodesToday = '';
let newEpisodesAt = 0;
let newEpisodesPending = false;
const NEW_EPISODES_EVERY_MS = 10 * 60 * 1000;

function refreshNewEpisodes(force = false) {
  if (newEpisodesPending || (!force && Date.now() - newEpisodesAt < NEW_EPISODES_EVERY_MS)) return;
  newEpisodesPending = true;
  api('/api/new-episodes').then(data => {
    newEpisodes = Array.isArray(data?.episodes) ? data.episodes : [];
    newEpisodesToday = data?.today || '';
    newEpisodesAt = Date.now();
    renderHomeResume();
  }).catch(() => {}).finally(() => { newEpisodesPending = false; });
}

// « Sorti aujourd'hui », « hier », « il y a 3 jours », else the date.
function releasedLabel(day, today) {
  const days = Math.round((Date.parse(`${today}T00:00:00Z`) - Date.parse(`${day}T00:00:00Z`)) / 86400000);
  if (!Number.isFinite(days)) return '';
  if (days <= 0) return 'Sorti aujourd’hui';
  if (days === 1) return 'Sorti hier';
  if (days < 7) return `Sorti il y a ${days} jours`;
  return `Sorti le ${episodeAirLabel(day)}`;
}

function newEpisodeCardHtml(item, today) {
  const code = episodeCode(item.season, item.episode);
  const hidden = typeof spoilerConcealed === 'function' && spoilerConcealed(String(item.id).toLowerCase(), item.season, item.episode);
  const episodeName = hidden ? '' : (item.episode_name && !/^(tba|tbd|episode \d+)$/i.test(item.episode_name) ? item.episode_name : '');
  const backdrop = resumeBackdrop(item);
  const still = hidden || item.kind === 'season' ? '' : item.still;
  const image = still || backdrop;
  const others = item.new_count - (item.is_new ? 1 : 0);
  const kind = item.kind || 'followed';
  const count = `${item.new_count} épisode${item.new_count > 1 ? 's' : ''}`;
  let status = item.in_progress ? `Reprendre · ${releasedLabel(item.released, today).toLowerCase()}` : releasedLabel(item.released, today);
  // The badge already says « Nouvelle série » / « Saison 3 »: the line gives the date and size.
  if (kind === 'premiere' || kind === 'season') status = `${releasedLabel(item.released, today)}${item.new_count > 1 ? ` · ${count}` : ''}`;
  else if (!item.is_new) status = `${item.new_count} ${item.new_count > 1 ? 'nouveaux épisodes' : 'nouvel épisode'} depuis votre dernier visionnage`;
  else if (others > 0) status += ` · +${others} autre${others > 1 ? 's' : ''}`;
  const badge = kind === 'premiere' ? 'Nouvelle série' : kind === 'season' ? `Saison ${item.season}` : 'Nouveau';
  // A new season of a series never started: no episode name or still, the card opens the fiche.
  const kicker = kind === 'season' ? `Saison ${item.season} disponible` : [code, episodeName].filter(Boolean).join(' · ');
  const play = { imdb_id: item.id, type: 'series', season: item.season, episode: item.episode,
    title: `${item.name} — ${code}`, name: item.name, poster: item.poster || '', year: item.year || '' };
  const media = { id: item.id, name: item.name, type: 'series', poster: item.poster || '', year: item.year || '' };
  const name = displayTitle(item);
  return `
    <article class="resume-card is-new${kind === 'season' ? ' opens-fiche' : ''}" role="listitem">
      <button class="resume-main" onclick="openResumeCard(this, ${attrJson(play)}, ${attrJson(media)})" aria-label="${kind === 'season' ? `Voir la fiche : ${escapeHtml(name)}, saison ${item.season} disponible` : `${item.in_progress ? 'Reprendre' : 'Lancer'} ${code} : ${escapeHtml(name)}`}">
        <span class="resume-still">${image ? `<img class="resume-still-img" src="${escapeHtml(image)}" data-fallback="${escapeHtml(still && backdrop !== still ? backdrop : '')}" data-poster="${escapeHtml(item.poster || '')}" alt="" onerror="resumeImageFallback(this)">` : ''}</span>
        <span class="resume-new-badge">${escapeHtml(badge)}</span>
        ${kind === 'season' ? '' : '<span class="resume-play" aria-hidden="true">▶</span>'}
        <span class="resume-info">
          <span class="resume-kicker">${escapeHtml(kicker)}</span>
          <span class="resume-title"${titleAttrs({ ...item, type: 'series' })}>${escapeHtml(name)}</span>
          <span class="resume-subinfo">${escapeHtml(status)}</span>
        </span>
      </button>
    </article>`;
}

// Returns the series shown, so « À continuer » does not show them twice.
function renderHomeNew() {
  const sec = document.getElementById('homeNewSection');
  const grid = document.getElementById('homeNewGrid');
  if (!sec || !grid) return new Set();
  // New episodes are series: the row belongs to the Séries tab.
  const items = activeTab === 'series' ? newEpisodes : [];
  sec.style.display = items.length ? 'block' : 'none';
  grid.innerHTML = items.map(item => newEpisodeCardHtml(item, newEpisodesToday || new Date().toISOString().slice(0, 10))).join('');
  return new Set(items.map(item => item.id));
}

// --- Accueil: personal rows first (À continuer, new episodes, Ma Liste), then
// « À la une », titles like the last one seen, and the catalogue. ---
const HOME_SHELF_SIZE = 12;
const homeBecauseRequests = new Set();

function onHomeTab() {
  return activeTab === 'movies' || activeTab === 'series';
}

function homeKind() {
  return activeTab === 'series' ? 'series' : 'movie';
}

// Ma Liste titles of the tab's type, newest first, neither seen nor in progress.
function homeListItems(kind = homeKind()) {
  const inProgress = new Set(getInProgressHistory().map(h => h.id));
  return (userWatchlist || []).filter(m => m?.id && listItemKind(m) === kind && listItemState(m) !== 'watched' && !inProgress.has(m.id));
}

function renderHomeList() {
  const sec = document.getElementById('homeListSection');
  const row = document.getElementById('homeListRow');
  if (!sec || !row) return;
  const kind = homeKind();
  const items = onHomeTab() ? homeListItems(kind) : [];
  sec.hidden = !items.length;
  if (!items.length) {
    row.innerHTML = '';
    return;
  }
  const count = document.getElementById('homeListCount');
  if (count) count.textContent = String(items.length);
  row.innerHTML = items.slice(0, HOME_SHELF_SIZE).map(m => posterCardHtml(m, kind)).join('')
    + (items.length > HOME_SHELF_SIZE ? `<button type="button" class="list-shelf-more" onclick="switchTab('watchlist')">Voir les ${items.length} titres</button>` : '');
}

// The last title of the tab's type seen to the end.
function homeBecauseSource(kind = homeKind()) {
  return getWatchedHistory()
    .filter(h => listItemKind(h) === kind && /^tt\d{1,12}$/.test(String(h.id || '')))
    .sort((a, b) => Number(b.last_watched || 0) - Number(a.last_watched || 0))[0] || null;
}

// Titles close to it (TMDB's recommendations with the user's key, else the same
// genres, best rated, from KINO's catalogue; /api/related), not seen yet.
function renderHomeBecause() {
  const sec = document.getElementById('homeBecauseSection');
  const row = document.getElementById('homeBecauseRow');
  if (!sec || !row) return;
  const kind = homeKind();
  const source = onHomeTab() ? homeBecauseSource(kind) : null;
  const related = source ? RELATED_CLIENT_CACHE.get(source.id) : null;
  if (source && !related && !homeBecauseRequests.has(source.id)) {
    homeBecauseRequests.add(source.id);
    api(`/api/related?type=${kind}&imdb_id=${encodeURIComponent(source.id)}`)
      .then(data => { RELATED_CLIENT_CACHE.set(source.id, data || { items: [] }); renderHomeBecause(); })
      .catch(() => {});
  }
  const seen = new Set(getWatchedHistory().map(h => h.id));
  const items = (Array.isArray(related?.items) ? related.items : [])
    .filter(m => m?.id && m.id !== source.id && !seen.has(m.id));
  sec.hidden = items.length < 4;
  if (sec.hidden) {
    row.innerHTML = '';
    return;
  }
  const title = document.getElementById('homeBecauseTitle');
  if (title) title.innerHTML = `Si vous avez aimé <span${titleAttrs(source, kind)}>${escapeHtml(displayTitle(source))}</span>`;
  row.innerHTML = items.slice(0, HOME_SHELF_SIZE).map(m => posterCardHtml(m, kind)).join('');
}

// « Pour vous »: KINO's own suggestions, each with its reason, and
// « Pas pour moi » to take one out (and teach the next ones).
const FOR_YOU_CACHE = new Map();
const forYouRequests = new Set();

function renderHomeForYou() {
  const sec = document.getElementById('homeForYouSection');
  const row = document.getElementById('homeForYouRow');
  if (!sec || !row) return;
  const kind = homeKind();
  if (!onHomeTab()) { sec.hidden = true; return; }
  const data = FOR_YOU_CACHE.get(kind);
  if (!data && !forYouRequests.has(kind)) {
    forYouRequests.add(kind);
    api(`/api/for-you?type=${kind}`)
      .then(res => { FOR_YOU_CACHE.set(kind, res || { items: [] }); renderHomeForYou(); })
      .catch(() => {})
      .finally(() => forYouRequests.delete(kind));
  }
  const seen = new Set(getWatchedHistory().map(h => h.id));
  const items = (data?.items || []).filter(m => m?.id && !seen.has(m.id)).slice(0, HOME_SHELF_SIZE);
  sec.hidden = items.length < 4;
  if (sec.hidden) { row.innerHTML = ''; return; }
  row.innerHTML = items.map(m => `<div class="foryou-item" data-foryou="${escapeHtml(m.id)}">${posterCardHtml(m, kind)}`
    + `<p class="foryou-why">${escapeHtml(m.why || '')}</p>`
    + `<button type="button" class="foryou-no" onclick='notForMe(event, ${JSON.stringify(m.id)})'>Pas pour moi</button></div>`).join('');
}

async function notForMe(event, id) {
  if (event) event.stopPropagation();
  const kind = homeKind();
  const data = FOR_YOU_CACHE.get(kind);
  if (data) data.items = (data.items || []).filter(m => m.id !== id);
  renderHomeForYou();
  try {
    await api('/api/for-you/not-for-me', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id }) });
    // The next suggestions take it into account.
    FOR_YOU_CACHE.clear();
    RELATED_CLIENT_CACHE.clear();
  } catch (e) {}
}

function hideHomeRows() {
  const resume = document.getElementById('homeResumeSection');
  const fresh = document.getElementById('homeNewSection');
  if (resume) resume.style.display = 'none';
  if (fresh) fresh.style.display = 'none';
  for (const id of ['homeListSection', 'homeRateSection', 'homeForYouSection', 'homeBecauseSection']) {
    const section = document.getElementById(id);
    if (section) section.hidden = true;
  }
}

// Each tab continues its own kind: films under Films, series under Séries, the
// anime titles on « À la une » of the Anime tab. « En cours » (Bibliothèque)
// holds them all.
function resumeBelongsToTab(entry) {
  if (activeTab === 'history') return true;
  if (activeTab === 'anime') return isAnimeTitle(entry);
  return listItemKind(entry) === homeKind();
}

function renderHomeResume() {
  // Without internet the home rows could not open anything: the catalogue says so instead.
  if (!networkOnline && activeTab !== 'history') {
    hideHomeRows();
    return;
  }
  renderHomeList();
  if (typeof renderHomeRate === 'function') renderHomeRate();
  renderHomeForYou();
  renderHomeBecause();
  const sec = document.getElementById('homeResumeSection');
  const grid = document.getElementById('homeResumeGrid');
  const shownAsNew = renderHomeNew();
  if (activeTab === 'series') refreshNewEpisodes();
  const animeHome = activeTab === 'anime' && currentAnimeSubTab === 'home';
  const inProg = getInProgressHistory().filter(h => (activeTab === 'history' || !shownAsNew.has(h.id)) && resumeBelongsToTab(h));
  if (activeTab !== 'movies' && activeTab !== 'series' && activeTab !== 'history' && !animeHome) {
    sec.style.display = 'none';
    return;
  }
  const isHistoryPage = activeTab === 'history';
  const items = isHistoryPage ? inProg : inProg.slice(0, 4);
  const title = document.getElementById('resumeSectionTitle');
  const allResumeBtn = document.getElementById('homeResumeAll');
  const clearResumeBtn = document.getElementById('homeResumeClear');
  if (title) title.textContent = isHistoryPage ? `${inProg.length} lecture${inProg.length > 1 ? 's' : ''} en cours` : 'À continuer';
  if (allResumeBtn) allResumeBtn.style.display = !isHistoryPage && inProg.length > 4 ? 'inline-flex' : 'none';
  if (clearResumeBtn) clearResumeBtn.style.display = isHistoryPage && inProg.length ? 'inline-flex' : 'none';
  grid.classList.toggle('resume-row', !isHistoryPage);
  if (!items.length) {
    if (isHistoryPage) {
      sec.style.display = 'block';
      grid.innerHTML = '<div class="library-empty"><span class="library-empty-icon" aria-hidden="true">▷</span><h3>Rien en cours de lecture</h3><p>Commencez un film ou une série, et retrouvez-le ici pour continuer plus tard.</p><button class="btn" onclick="switchTab(\'movies\')">Découvrir les films</button></div>';
    } else {
      sec.style.display = 'none';
    }
    return;
  }
  sec.style.display = 'block';
  const today = new Date().toISOString().slice(0, 10);
  grid.innerHTML = items.map(h => {
    const view = resumeCardView(h, META_CLIENT_CACHE.get(h.id), today);
    const name = displayTitle(h);
    return `
      <article class="resume-card${view.upcoming ? ' is-upcoming' : ''}">
        <button class="resume-main" onclick="openResumeCard(this, ${attrJson(view.play)}, ${attrJson(view.media)})" aria-label="${escapeHtml(view.action)} : ${escapeHtml(name)}">
          <span class="resume-still">${view.image ? `<img class="resume-still-img" src="${escapeHtml(view.image)}" data-fallback="${escapeHtml(view.fallback)}" data-poster="${escapeHtml(view.poster)}" alt=""${isHistoryPage ? ' loading="lazy"' : ''} onerror="resumeImageFallback(this)">` : ''}</span>
          <span class="resume-play" aria-hidden="true">▶</span>
          <span class="resume-info">
            <span class="resume-kicker">${escapeHtml(view.kicker)}</span>
            <span class="resume-title"${titleAttrs(h)}>${escapeHtml(name)}</span>
            <span class="resume-subinfo">${escapeHtml(view.status)}</span>
          </span>
          ${view.progress ? `<span class="resume-progress-bar"><span class="resume-progress-fill" style="width:${view.progress}%;"></span></span>` : ''}
        </button>
        <button class="resume-dismiss-btn" onclick="removeHistoryItem(${attrJson(h.id)})" title="Retirer des lectures en cours" aria-label="Retirer ${escapeHtml(name)} des lectures en cours">×</button>
      </article>
    `;
  }).join('');
  requestResumeMetas(items);
}

async function removeHistoryItem(id) {
  await api('/api/history-remove', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id})
  });
  await refreshUserLists();
}

// « Effacer l'historique » empties the whole history, watched films included,
// and Drive sync carries the deletion to other devices: ask first, with the count.
async function clearAllHistory() {
  const count = (userHistory || []).length;
  if (!count) return;
  const what = count > 1 ? `les ${count} titres de l’historique` : 'le titre de l’historique';
  if (!window.confirm(`Effacer ${what} ? Les films vus et les lectures en cours seront retirés, y compris sur les appareils synchronisés.`)) return;
  await removeHistoryItem('__all__');
}

let classicsCatalogCache = [];

function updateClassicsStatsBadge() {
  const statsEl = document.getElementById('listStatsBadge');
  if (!statsEl) return;
  if (activeTab !== 'classics') return;
  const pool = catalogItems && catalogItems.length ? catalogItems : classicsCatalogCache;
  if (!pool.length) {
    statsEl.style.display = 'none';
    return;
  }
  const seenCount = pool.filter(m => {
    const h = userHistory.find(x => x.id === m.id);
    return Boolean(h && (h.completed || Number(h.progress_pct || 0) >= 85));
  }).length;
  const pct = Math.round((seenCount / pool.length) * 100);
  const rem = pool.length - seenCount;
  statsEl.textContent = `${seenCount} / ${pool.length} vus (${pct}%) • ${rem} à découvrir`;
  statsEl.style.display = 'inline-flex';
}

function renderGenreChipsForTab(tab) {
  const gf = document.getElementById('genreFilters');
  if (!gf) return;
  if (tab === 'anime') {
    gf.innerHTML = `
      <span class="chip active" data-genre="" onclick="selectGenre('', this)">Tous</span>
      <span class="chip" data-genre="Action" onclick="selectGenre('Action', this)">Action &amp; combats</span>
      <span class="chip" data-genre="Fantasy" onclick="selectGenre('Fantasy', this)">Fantasy &amp; magie</span>
      <span class="chip" data-genre="Isekai" onclick="selectGenre('Isekai', this)">Isekai</span>
      <span class="chip" data-genre="Sci-Fi" onclick="selectGenre('Sci-Fi', this)">SF &amp; mecha</span>
      <span class="chip" data-genre="Slice of Life" onclick="selectGenre('Slice of Life', this)">Tranche de vie</span>
      <span class="chip" data-genre="Romance" onclick="selectGenre('Romance', this)">Romance</span>
      <span class="chip" data-genre="Comedy" onclick="selectGenre('Comedy', this)">Comédie</span>
      <span class="chip" data-genre="Sport" onclick="selectGenre('Sport', this)">Sport</span>
      <span class="chip" data-genre="Films d'Animation" onclick="selectGenre('Films d\\'Animation', this)">Films anime</span>
    `;
  } else if (tab === 'series') {
    gf.innerHTML = `
      <span class="chip active" data-genre="" onclick="selectGenre('', this)">Toutes</span>
      <span class="chip" data-genre="Drama" onclick="selectGenre('Drama', this)">Drame</span>
      <span class="chip" data-genre="Crime" onclick="selectGenre('Crime', this)">Policier</span>
      <span class="chip" data-genre="Thriller" onclick="selectGenre('Thriller', this)">Thriller</span>
      <span class="chip" data-genre="Action" onclick="selectGenre('Action', this)">Action</span>
      <span class="chip" data-genre="Sci-Fi" onclick="selectGenre('Sci-Fi', this)">Sci-Fi</span>
      <span class="chip" data-genre="Comedy" onclick="selectGenre('Comedy', this)">Comédie</span>
      <span class="chip" data-genre="Adventure" onclick="selectGenre('Adventure', this)">Aventure</span>
      <span class="chip" data-genre="Fantasy" onclick="selectGenre('Fantasy', this)">Fantastique</span>
      <span class="chip" data-genre="Horror" onclick="selectGenre('Horror', this)">Horreur</span>
      <span class="chip" data-genre="Mystery" onclick="selectGenre('Mystery', this)">Mystère</span>
      <span class="chip" data-genre="War" onclick="selectGenre('War', this)">Guerre</span>
      <span class="chip" data-genre="Western" onclick="selectGenre('Western', this)">Western</span>
      <span class="chip" data-genre="Documentary" onclick="selectGenre('Documentary', this)">Documentaire</span>
    `;
  } else {
    gf.innerHTML = `
      <span class="chip active" data-genre="" onclick="selectGenre('', this)">Tous</span>
      <span class="chip" data-genre="Action" onclick="selectGenre('Action', this)">Action</span>
      <span class="chip" data-genre="Sci-Fi" onclick="selectGenre('Sci-Fi', this)">Sci-Fi</span>
      <span class="chip" data-genre="Thriller" onclick="selectGenre('Thriller', this)">Thriller</span>
      <span class="chip" data-genre="Crime" onclick="selectGenre('Crime', this)">Policier</span>
      <span class="chip" data-genre="Adventure" onclick="selectGenre('Adventure', this)">Aventure</span>
      <span class="chip" data-genre="Animation" onclick="selectGenre('Animation', this)">Animation</span>
      <span class="chip" data-genre="Comedy" onclick="selectGenre('Comedy', this)">Comédie</span>
      <span class="chip" data-genre="Drama" onclick="selectGenre('Drama', this)">Drame</span>
      <span class="chip" data-genre="Fantasy" onclick="selectGenre('Fantasy', this)">Fantastique</span>
      <span class="chip" data-genre="Horror" onclick="selectGenre('Horror', this)">Horreur</span>
      <span class="chip" data-genre="War" onclick="selectGenre('War', this)">Guerre</span>
      <span class="chip" data-genre="Western" onclick="selectGenre('Western', this)">Western</span>
      <span class="chip" data-genre="Documentary" onclick="selectGenre('Documentary', this)">Documentaire</span>
    `;
  }
}

function updateCatalogHeading() {
  const activeChip = document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`);
  const genreLabel = (activeGenre && activeChip) ? activeChip.textContent : '';
  if (activeTab === 'classics') {
    const sortSuffix = catalogSort === 'imdbRating'
      ? ' — ★ Mieux notés IMDb'
      : (catalogSort === 'recent' ? ' — Plus récents' : (catalogSort === 'oldest' ? ' — Chronologique' : ' · Les films à voir dans sa vie'));
    document.getElementById('catalogTitle').textContent = genreLabel
      ? `Classiques · ${genreLabel}`
      : `Classiques${sortSuffix}`;
    return;
  }
  if (activeTab === 'anime') {
    const sortSuffix = catalogSort === 'imdbRating'
      ? ' · Mieux notés (★ IMDb)'
      : (catalogSort === 'recent' ? ' · Plus récents' : (catalogSort === 'oldest' ? ' · Chronologique' : ''));
    document.getElementById('catalogTitle').textContent = genreLabel
      ? `Animation Japonaise · ${genreLabel}`
      : `Animation Japonaise${sortSuffix}`;
    return;
  }
  const type = activeTab === 'series' ? 'series' : 'movie';
  const baseLabel = type === 'series' ? 'Séries' : 'Films';
  const sortSuffix = catalogSort === 'imdbRating'
    ? ' — ★ Mieux notés IMDb'
    : (catalogSort === 'recent' ? ' — Plus récents' : (catalogSort === 'oldest' ? ' — Chronologique' : ' populaires du moment'));
  document.getElementById('catalogTitle').textContent = genreLabel
    ? `${baseLabel} · ${genreLabel}${catalogSort === 'imdbRating' ? ' (★ IMDb)' : ''}`
    : `${baseLabel}${sortSuffix}`;
}

// Genres on one scrolling line: arrows only when some genres are hidden, the
// mouse wheel scrolls sideways, and the selected genre stays in view.
function updateGenreArrows() {
  const row = document.getElementById('genreFilters');
  const strip = document.getElementById('genreStrip');
  if (!row || !strip) return;
  const max = row.scrollWidth - row.clientWidth;
  const prev = strip.querySelector('.genre-scroll-prev');
  const next = strip.querySelector('.genre-scroll-next');
  if (prev) prev.hidden = !(max > 2 && row.scrollLeft > 2);
  if (next) next.hidden = !(max > 2 && row.scrollLeft < max - 2);
  strip.classList.toggle('fade-left', Boolean(prev && !prev.hidden));
  strip.classList.toggle('fade-right', Boolean(next && !next.hidden));
}

function scrollGenres(direction) {
  const row = document.getElementById('genreFilters');
  if (!row) return;
  row.scrollBy({ left: direction * Math.max(160, row.clientWidth * 0.7), behavior: 'smooth' });
}

// Scrolls only the genre row (never the page), leaving room for the faded edge.
function revealActiveGenre() {
  const row = document.getElementById('genreFilters');
  const active = row && row.querySelector('.chip.active');
  if (active && row.scrollWidth > row.clientWidth) {
    const box = row.getBoundingClientRect();
    const chip = active.getBoundingClientRect();
    const margin = 40;
    let delta = 0;
    if (chip.left < box.left + margin) delta = chip.left - box.left - margin;
    else if (chip.right > box.right - margin) delta = chip.right - box.right + margin;
    if (delta) row.scrollBy({ left: delta });
  }
  updateGenreArrows();
}

function initGenreStrip() {
  const row = document.getElementById('genreFilters');
  if (!row) return;
  row.addEventListener('scroll', updateGenreArrows, { passive: true });
  row.addEventListener('wheel', event => {
    if (Math.abs(event.deltaY) <= Math.abs(event.deltaX) || row.scrollWidth <= row.clientWidth) return;
    event.preventDefault();
    row.scrollLeft += event.deltaY;
  }, { passive: false });
  window.addEventListener('resize', updateGenreArrows);
  if (typeof ResizeObserver === 'function') new ResizeObserver(() => revealActiveGenre()).observe(row);
  // Each section (films, séries, anime…) rebuilds the genres; the library,
  // search and anime schedule hide the row through its style attribute.
  new MutationObserver(records => {
    if (records.some(record => record.type === 'childList')) revealActiveGenre();
    else updateGenreArrows();
  }).observe(row, { childList: true, attributes: true, attributeFilter: ['style'] });
  requestAnimationFrame(updateGenreArrows);
}

function selectGenre(genre, el) {
  activeGenre = genre;
  const genreChips = [...document.querySelectorAll('#genreFilters .chip')];
  genreChips.forEach(c => c.classList.remove('active'));
  if (el) el.classList.add('active');
  syncChipPressed(genreChips, el);
  revealActiveGenre();
  const type = activeTab === 'series' ? 'series' : (activeTab === 'classics' ? 'classics' : (activeTab === 'anime' ? 'anime' : 'movie'));
  updateCatalogHeading();
  loadCatalog(type, genre, true);
}

function onChangeCatalogSort(sortVal) {
  catalogSort = CATALOG_SORTS.includes(sortVal) ? sortVal : 'top';
  saveCatalogView();
  const type = activeTab === 'series' ? 'series' : (activeTab === 'classics' ? 'classics' : (activeTab === 'anime' ? 'anime' : 'movie'));
  updateCatalogHeading();
  loadCatalog(type, activeGenre, true);
}

let heroSpotlightItems = [];
let heroSpotlightIdx = 0;
let heroSpotlightTimer = null;

function renderHeroSpotlight(metas, fallbackType) {
  const box = document.getElementById('heroSpotlight');
  if (!box) return;
  clearInterval(heroSpotlightTimer);
  if (!metas || !metas.length || (activeTab !== 'movies' && activeTab !== 'series' && activeTab !== 'classics' && activeTab !== 'anime')) {
    box.style.display = 'none';
    return;
  }
  const unwatched = metas.filter(m => {
    const h = userHistory.find(x => x.id === m.id);
    if (!h) return true;
    return !(h.completed || Number(h.progress_pct || 0) >= 85);
  });
  heroSpotlightItems = (unwatched.length >= 3 ? unwatched : metas).slice(0, 5).map(m => ({
    ...m,
    type: (m.type && m.type !== 'classics') ? m.type : (fallbackType === 'series' ? 'series' : 'movie')
  }));
  heroSpotlightIdx = 0;
  drawHeroSpotlightSlide();
  box.style.display = 'flex';
  heroSpotlightTimer = setInterval(() => {
    // Hidden or minimised window: no slide change, no image or translation download.
    if (document.hidden) return;
    if (document.getElementById('inAppPlayerOverlay').classList.contains('active')) return;
    heroSpotlightIdx = (heroSpotlightIdx + 1) % heroSpotlightItems.length;
    drawHeroSpotlightSlide();
  }, 9000);
}

function setHeroSpotlightIdx(idx) {
  clearInterval(heroSpotlightTimer);
  heroSpotlightIdx = ((idx % heroSpotlightItems.length) + heroSpotlightItems.length) % heroSpotlightItems.length;
  drawHeroSpotlightSlide();
}

function drawHeroSpotlightSlide() {
  const box = document.getElementById('heroSpotlight');
  if (!box || !heroSpotlightItems.length) return;
  const item = heroSpotlightItems[heroSpotlightIdx] || heroSpotlightItems[0];
  const mtype = item.type || 'movie';
  const year = String(item.releaseInfo || item.year || '');
  const rating = item.imdbRating || '';
  let bgUrl = item.background;
  if (item.id === 'tt22248376' && (!bgUrl || bgUrl.includes('metahub.space'))) {
    bgUrl = 'https://media.kitsu.app/anime/46474/cover_image/large-167edf3e01fac59ce6aacfeb47df5634.jpeg';
  } else if ((item.id === 'tt1355642' || item.id === 'tt1370601') && (!bgUrl || bgUrl.includes('metahub.space'))) {
    bgUrl = 'https://media.kitsu.app/anime/cover_images/3936/large.jpg';
  } else if (item.id === 'tt21209876' && (!bgUrl || bgUrl.includes('metahub.space'))) {
    bgUrl = 'https://media.kitsu.app/anime/46231/cover_image/large-33273dc297cdc8b10cc1140de07d3dae.jpeg';
  }
  if (!bgUrl) {
    bgUrl = `https://images.metahub.space/background/medium/${item.id}/img`;
  }
  const genres = Array.isArray(item.genres) ? item.genres.slice(0, 3) : [];
  const desc = item.description_fr || item.description || '';
  const mediaObj = {
    id: item.id,
    name: item.name,
    type: mtype,
    year,
    poster: item.poster || '',
    imdbRating: String(rating)
  };
  const payload = JSON.stringify(mediaObj).replace(/'/g, "&#39;");
  const safeNameJs = JSON.stringify(item.name || 'KINO').replace(/'/g, "&#39;");
  const safeYearJs = JSON.stringify(year).replace(/'/g, "&#39;");
  const kickerTxt = activeTab === 'classics' ? 'Classique incontournable'
    : (activeTab === 'anime' ? 'Animation japonaise · Populaire' : `À la une · ${mtype === 'series' ? 'Série' : 'Film'}`);

  let posterUrl = item.poster;
  if (item.id === 'tt22248376' && (!posterUrl || posterUrl.includes('metahub.space'))) {
    posterUrl = 'https://media.kitsu.app/anime/46474/poster_image/large-ec9b98dd5fbf8f92532d1edb45f9e882.jpeg';
  } else if ((item.id === 'tt1355642' || item.id === 'tt1370601') && (!posterUrl || posterUrl.includes('metahub.space'))) {
    posterUrl = 'https://media.kitsu.app/anime/3936/poster_image/large-a94f61b0c0f8623b371cf78696ecdd44.jpeg';
  } else if (item.id === 'tt21209876' && (!posterUrl || posterUrl.includes('metahub.space'))) {
    posterUrl = 'https://media.kitsu.app/anime/46231/poster_image/large-22cba102be8d90ca0d6eec4d57cff5b6.jpeg';
  } else if (!posterUrl) {
    posterUrl = item.id ? `https://images.metahub.space/poster/medium/${item.id}/img` : '';
  }

  const bgCandidate = bgUrl || posterUrl;
  if (bgCandidate) {
    setHeroImage(box, `url('${bgCandidate}')`);
    const heroBgImg = new Image();
    heroBgImg.onerror = function() {
      if (item.id === 'tt22248376') {
        setHeroImage(box, "url('https://media.kitsu.app/anime/46474/cover_image/large-167edf3e01fac59ce6aacfeb47df5634.jpeg')");
      } else if (item.id === 'tt1355642' || item.id === 'tt1370601') {
        setHeroImage(box, "url('https://media.kitsu.app/anime/cover_images/3936/large.jpg')");
      } else if (item.id === 'tt21209876') {
        setHeroImage(box, "url('https://media.kitsu.app/anime/46231/cover_image/large-33273dc297cdc8b10cc1140de07d3dae.jpeg')");
      } else if (posterUrl && posterUrl !== bgCandidate) {
        setHeroImage(box, `url('${posterUrl}')`);
      }
    };
    heroBgImg.src = bgCandidate;
  }
  box.innerHTML = `
    <div class="hero-content">
      <div class="hero-kicker">${kickerTxt}</div>
      <div class="hero-title"${titleAttrs({...item, type: mtype})}>${escapeHtml(displayTitle(item))}</div>
      <div class="hero-meta">
        ${year ? `<span>${year}</span>` : ''}
        ${rating ? `<span class="hero-rating">★ ${rating} IMDb</span>` : ''}
        ${genres.map(g => `<span class="badge">${escapeHtml(frenchGenre(g))}</span>`).join('')}
      </div>
      ${desc ? `<div class="hero-desc">${escapeHtml(desc)}</div>` : ''}
      <div class="hero-actions">
        <button class="btn" onclick='oneClickCard(event, this, ${payload})'>Regarder</button>
        <button class="btn btn-secondary" onclick='selectMedia(${payload})'>Voir la fiche</button>
        <button class="btn btn-secondary" onclick='openTrailerModal("", ${safeNameJs}, ${safeYearJs}, "vf")'><svg class="ui-icon ui-icon-xs" aria-hidden="true"><use href="/icons.svg#trailer"></use></svg>Bande-annonce</button>
      </div>
    </div>
    <div class="hero-dots">
      ${heroSpotlightItems.map((_, i) => `<button class="hero-dot ${i === heroSpotlightIdx ? 'active' : ''}" onclick="setHeroSpotlightIdx(${i})" title="Titre ${i + 1}"></button>`).join('')}
    </div>
  `;
  ensureHeroFrenchDescription(item);
}

// The slide's image, also handed to the soft glow around the banner (design.css):
// the banner takes the colours of its title without reading any pixel.
// The glow (--hero-glow) only gets the image once it has loaded: when WebKit paints
// a late image it redraws only the banner's box, so the blur spilling below it
// stayed cut at that edge until the next slide.
function setHeroImage(box, css) {
  box.style.backgroundImage = css;
  box.style.setProperty('--hero-image', css);
  box._heroGlowFor = css;
  const src = (String(css).match(/^url\('(.*)'\)$/) || [])[1];
  if (!src) return;
  const img = new Image();
  img.onload = () => { if (box._heroGlowFor === css) box.style.setProperty('--hero-glow', css); };
  img.src = src;
}

// Catalog synopses are English; the detail endpoint returns a cached French translation.
const heroFrenchRequests = new Set();
function ensureHeroFrenchDescription(item) {
  if (!item || item.description_fr !== undefined || !item.description) return;
  if (!/^tt\d+$/.test(String(item.id || '')) || heroFrenchRequests.has(item.id)) return;
  heroFrenchRequests.add(item.id);
  const type = item.type === 'series' ? 'series' : 'movie';
  api(`/api/meta?type=${type}&imdb_id=${encodeURIComponent(item.id)}`)
    .then(res => {
      const french = String((res && res.meta && res.meta.description_fr) || '').trim();
      item.description_fr = french;
      const descEl = document.querySelector('#heroSpotlight .hero-desc');
      if (french && descEl && heroSpotlightItems[heroSpotlightIdx] === item) descEl.textContent = french;
    })
    .catch(() => { item.description_fr = ''; })
    .finally(() => heroFrenchRequests.delete(item.id));
}

