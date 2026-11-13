// --- Glisser-Déposer (Drag & Drop) de fichiers .torrent et liens Magnet ---
async function handleTorrentFileSelect(files) {
  if (!files || !files.length) return;
  const file = files[0];
  const box = document.getElementById('debridResultPanel');
  if (box) {
    box.style.display = 'block';
    box.scrollIntoView({ behavior: 'smooth' });
    box.innerHTML = `<h3>Lecture du fichier ${escapeHtml(file.name)}...</h3><p style="color:var(--muted); font-size:0.83rem; margin-top:4px;">Extraction de l'empreinte BitTorrent et débridage Cloud...</p>`;
  }
  try {
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    const b64 = btoa(binary);
    const res = await api('/api/upload-torrent', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ filename: file.name, data_b64: b64 })
    });
    if (res && res.magnet) {
      currentMedia = { id: '', name: res.name || file.name, type: 'movie', year: '', poster: '' };
      await debridMagnet(res.magnet);
    }
  } catch (e) {
    if (box) box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur fichier .torrent : ${escapeHtml(e.message)}</p>`;
  }
}

let dragDepthCounter = 0;
window.addEventListener('dragenter', (e) => {
  e.preventDefault();
  dragDepthCounter++;
  const ov = document.getElementById('dropZoneOverlay');
  if (ov) ov.classList.add('active');
});
window.addEventListener('dragover', (e) => {
  e.preventDefault();
});
window.addEventListener('dragleave', (e) => {
  e.preventDefault();
  dragDepthCounter = Math.max(0, dragDepthCounter - 1);
  if (dragDepthCounter === 0) {
    const ov = document.getElementById('dropZoneOverlay');
    if (ov) ov.classList.remove('active');
  }
});
window.addEventListener('drop', async (e) => {
  e.preventDefault();
  dragDepthCounter = 0;
  const ov = document.getElementById('dropZoneOverlay');
  if (ov) ov.classList.remove('active');
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
    const f = e.dataTransfer.files[0];
    if (f.name.toLowerCase().endsWith('.torrent')) {
      await handleTorrentFileSelect(e.dataTransfer.files);
      return;
    }
  }
  const text = e.dataTransfer ? (e.dataTransfer.getData('text/plain') || e.dataTransfer.getData('text/uri-list') || '').trim() : '';
  if (text && text.startsWith('magnet:?')) {
    await debridMagnet(text);
  }
});

let searchSuggestItems = [];
let searchSuggestIndex = -1;

function toggleShortcutsModal() {
  const m = document.getElementById('shortcutsModal');
  if (!m) return;
  const isOpen = m.style.display === 'flex';
  m.style.display = isOpen ? 'none' : 'flex';
}

function initSearchPlatformShortcuts() {
  const isMac = (navigator.platform && navigator.platform.toUpperCase().indexOf('MAC') >= 0) || (navigator.userAgent && navigator.userAgent.toUpperCase().indexOf('MAC') >= 0);
  const kbd = document.getElementById('searchKbdBadge');
  if (kbd) kbd.textContent = isMac ? '⌘K' : 'Ctrl+K';
  const inp = document.getElementById('searchInput');
  if (inp && !inp.value) inp.placeholder = 'Rechercher un film, une série…';
}

function updateSearchClearBtn() {
  const inp = document.getElementById('searchInput');
  const btn = document.getElementById('searchClearBtn');
  if (btn && inp) {
    btn.style.display = inp.value.trim().length > 0 ? 'flex' : 'none';
  }
}

function clearSearchInput() {
  const inp = document.getElementById('searchInput');
  if (inp) {
    inp.value = '';
    inp.focus();
  }
  updateSearchClearBtn();
  hideSearchDropdown();
  if (searchResultsShown || activeTab === 'movies' || activeTab === 'series') {
    switchTab(activeTab);
  }
}

function toggleSearchMode() {
  const mode = document.getElementById('searchType').value;
  const inp = document.getElementById('searchInput');
  hideSearchDropdown();
  if (mode === 'magnet') inp.placeholder = 'Coller un lien magnet:?xt=urn:btih:…';
  else if (mode === 'raw') inp.placeholder = 'Recherche par mots-clés…';
  else inp.placeholder = 'Rechercher un film, une série…';
  updateSearchClearBtn();
}

function onSearchInput() {
  clearTimeout(searchDebounceTimer);
  updateSearchClearBtn();
  const mode = document.getElementById('searchType').value;
  const q = document.getElementById('searchInput').value.trim();
  if (mode === 'magnet' || mode === 'raw') {
    hideSearchDropdown();
    return;
  }
  searchPanelWanted = true;
  if (!q) {
    renderSearchPanel('');
    if (searchResultsShown || activeTab === 'movies' || activeTab === 'series') {
      switchTab(activeTab);
    }
    return;
  }
  renderSearchPanel(q, searchSuggestCache.query === q ? searchSuggestCache.titles : []);
  // Interroger immédiatement SQLite FTS5 pour autocomplétion instantanée
  if (q.length >= 2 && !/^magnet:/i.test(q)) fetchSearchSuggestions(q, mode);
}

async function fetchSearchSuggestions(q, mode) {
  if (!networkOnline) {
    hideSearchDropdown();
    return;
  }
  try {
    const res = await api(`/api/search-suggest?q=${encodeURIComponent(q)}&type=${encodeURIComponent(mode)}`);
    const results = (res && res.results) || [];
    rememberFrenchTitles(results);
    searchSuggestCache = { query: q, titles: results };
    // Only for what is still typed, and while the panel is wanted (not after Entrée).
    if (!searchPanelWanted || document.getElementById('searchInput').value.trim() !== q) return;
    renderSearchDropdown(results, q);
  } catch (e) {}
}

// --- Recherche ⌘K -------------------------------------------------
// One field in the top bar. Before typing, its panel offers the recent searches
// and KINO's main screens; while typing, the screens whose name fits, the titles,
// then « Tout voir » and the search in the sources. A pasted magnet link opens as such.
const SEARCH_PLACES = [
  { label: 'Films', icon: 'film', tab: 'movies', words: 'accueil catalogue' },
  { label: 'Séries', icon: 'series', tab: 'series' },
  { label: 'Anime', icon: 'anime', tab: 'anime' },
  { label: 'Ce soir', icon: 'tonight', run: () => openTonightModal(), words: 'idée hasard surprise' },
  { label: 'Explorer', icon: 'explore', tab: 'community', words: 'listes sélections' },
  { label: 'Ma Liste', icon: 'library', tab: 'watchlist', hint: 'Bibliothèque', words: 'favoris' },
  { label: 'En cours', icon: 'library', tab: 'history', hint: 'Bibliothèque', words: 'continuer reprendre historique' },
  { label: 'Mes séries', icon: 'series', tab: 'myseries', hint: 'Bibliothèque' },
  { label: 'Calendrier', icon: 'library', tab: 'calendar', hint: 'Bibliothèque', words: 'sorties épisodes' },
  { label: 'Déjà vus', icon: 'check', tab: 'watched', hint: 'Bibliothèque', words: 'vus' },
  { label: 'Hors ligne', icon: 'folder', tab: 'offline', hint: 'Bibliothèque', words: 'téléchargements fichiers' },
  { label: 'Listes', icon: 'library', tab: 'customlists', hint: 'Bibliothèque', words: 'letterboxd importées' },
  { label: 'Statistiques', icon: 'pulse', tab: 'stats', hint: 'Bibliothèque', words: 'stats mes statistiques chiffres' },
  { label: 'Cloud', icon: 'cloud', tab: 'rdcloud', hint: 'Bibliothèque', words: 'debrid' },
  { label: 'Paramètres', icon: 'settings', tab: 'settings', words: 'réglages configuration préférences options' },
  { label: 'Ouvrir un fichier .torrent', icon: 'plus', run: () => openTorrentFile(), words: 'torrent' },
  { label: 'Diagnostic', icon: 'pulse', run: () => openDiagnostic(), words: 'vérifier problème' },
  { label: 'Raccourcis clavier', icon: 'keyboard', run: () => toggleShortcutsModal(), words: 'aide touches' },
];
const SEARCH_PLACES_FIRST = ['Ma Liste', 'En cours', 'Ce soir', 'Paramètres'];
const SEARCH_MODE_LABELS = { movie: 'Films', series: 'Séries', anime: 'Anime', raw: 'Mots-clés' };
const RECENT_SEARCHES_KEY = 'kino_recent_searches';
let searchSuggestCache = { query: '', titles: [] };
let searchPanelWanted = false;

function searchFold(text) {
  return String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
}

function matchingPlaces(query) {
  const q = searchFold(query);
  if (!q) return SEARCH_PLACES.filter(place => SEARCH_PLACES_FIRST.includes(place.label));
  return SEARCH_PLACES.filter(place => searchFold(place.label).startsWith(q)
    || searchFold(`${place.label} ${place.words || ''}`).split(/[\s.]+/).some(word => word && word.startsWith(q))).slice(0, 3);
}

function recentSearches() {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_SEARCHES_KEY) || '[]');
    return Array.isArray(list) ? list.filter(r => r && typeof r.query === 'string' && r.query).slice(0, 5) : [];
  } catch (e) {
    return [];
  }
}

function rememberSearch(query, mode) {
  const q = String(query || '').trim().slice(0, 120);
  if (!q || mode === 'magnet' || /^magnet:/i.test(q)) return;
  const list = [{ query: q, mode: mode || 'all' }, ...recentSearches().filter(r => searchFold(r.query) !== searchFold(q))].slice(0, 5);
  try { localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(list)); } catch (e) {}
}

function forgetRecentSearches(event) {
  if (event) event.stopPropagation();
  try { localStorage.removeItem(RECENT_SEARCHES_KEY); } catch (e) {}
  renderSearchPanel(document.getElementById('searchInput')?.value || '');
  document.getElementById('searchInput')?.focus();
}

function searchPanelEntries(query, titles = []) {
  const q = String(query || '').trim();
  if (/^magnet:\?/i.test(q)) return [{ kind: 'magnet', query: q, group: 'Lien magnet' }];
  const entries = [];
  if (!q) {
    for (const recent of recentSearches()) entries.push({ kind: 'recent', query: recent.query, mode: recent.mode || 'all', group: 'Recherches récentes' });
    for (const place of matchingPlaces('')) entries.push({ kind: 'place', place, group: 'Aller à' });
    return entries;
  }
  for (const place of matchingPlaces(q)) entries.push({ kind: 'place', place, group: 'Aller à' });
  for (const item of (titles || []).slice(0, 6)) entries.push({ kind: 'title', item, group: 'Titres' });
  entries.push({ kind: 'all', query: q, group: 'Rechercher' });
  entries.push({ kind: 'raw', query: q, group: 'Rechercher' });
  return entries;
}

function searchEntryHtml(entry, idx) {
  const attrs = `class="search-suggest-item${entry.kind === 'title' ? '' : ' search-suggest-row'}" id="suggestItem_${idx}" role="option" tabindex="-1" onmousedown="event.preventDefault()" onclick="selectSearchSuggest(${idx})"`;
  if (entry.kind === 'title') {
    const it = entry.item;
    const typeLabel = it.type === 'series' ? 'Série' : 'Film';
    return `<div ${attrs}>
        <img class="search-suggest-poster" src="${escapeHtml(it.poster || '')}" alt="" onerror="this.style.opacity=0.08">
        <div class="search-suggest-info">
          <div class="search-suggest-title"${titleAttrs(it)}>${escapeHtml(displayTitle(it))}</div>
          <div class="search-suggest-meta"><span>${typeLabel}</span>${it.year ? `<span>${escapeHtml(it.year)}</span>` : ''}${it.imdbRating ? `<span>★ ${escapeHtml(it.imdbRating)}</span>` : ''}</div>
        </div>
      </div>`;
  }
  const icon = entry.kind === 'place' ? entry.place.icon : entry.kind === 'magnet' ? 'link' : entry.kind === 'raw' ? 'grid' : 'search';
  const label = entry.kind === 'place' ? entry.place.label
    : entry.kind === 'recent' ? entry.query
    : entry.kind === 'all' ? `Tout voir pour « ${entry.query} »`
    : entry.kind === 'raw' ? `Chercher « ${entry.query} » dans les sources`
    : 'Ouvrir ce lien magnet';
  const hint = entry.kind === 'place' ? (entry.place.hint || '')
    : entry.kind === 'recent' ? (SEARCH_MODE_LABELS[entry.mode] || '')
    : entry.kind === 'all' ? 'Entrée' : '';
  return `<div ${attrs}><span class="search-row-icon"><svg class="ui-icon ui-icon-sm" aria-hidden="true"><use href="/icons.svg#${icon}"></use></svg></span><span class="search-suggest-title">${escapeHtml(label)}</span>${hint ? `<span class="search-row-hint">${escapeHtml(hint)}</span>` : ''}</div>`;
}

function renderSearchPanel(query, titles = []) {
  const dd = document.getElementById('searchDropdown');
  if (!dd) return;
  const entries = searchPanelEntries(query, titles);
  searchSuggestItems = entries;
  searchSuggestIndex = -1;
  if (!entries.length) {
    hideSearchDropdown();
    return;
  }
  let group = '';
  dd.innerHTML = entries.map((entry, idx) => {
    const head = entry.group === group ? ''
      : `<div class="search-panel-group" role="presentation"><span>${escapeHtml(entry.group)}</span>${entry.kind === 'recent'
        ? '<button type="button" class="search-panel-clear" onmousedown="event.preventDefault()" onclick="forgetRecentSearches(event)">Effacer</button>' : ''}</div>`;
    group = entry.group;
    return head + searchEntryHtml(entry, idx);
  }).join('');
  dd.style.display = 'block';
  document.getElementById('searchInput')?.setAttribute('aria-expanded', 'true');
}

// The titles found while typing (kept as the panel's « Titres »).
function renderSearchDropdown(items, query) {
  renderSearchPanel(query, items || []);
}

function openSearchPanel() {
  const mode = document.getElementById('searchType')?.value;
  if (mode === 'raw' || mode === 'magnet') return;
  if (document.getElementById('searchDropdown')?.style.display === 'block') return;  // already open: keep the choice
  searchPanelWanted = true;
  const q = (document.getElementById('searchInput')?.value || '').trim();
  renderSearchPanel(q, searchSuggestCache.query === q ? searchSuggestCache.titles : []);
  if (q.length >= 2 && searchSuggestCache.query !== q && !/^magnet:/i.test(q)) fetchSearchSuggestions(q, mode);
}

function openTorrentFile() {
  document.getElementById('torrentFileInput')?.click();
}

function hideSearchDropdown() {
  const dd = document.getElementById('searchDropdown');
  if (dd) {
    dd.style.display = 'none';
    dd.innerHTML = '';
  }
  searchSuggestItems = [];
  searchSuggestIndex = -1;
  searchPanelWanted = false;
  document.getElementById('searchInput')?.setAttribute('aria-expanded', 'false');
}

function selectSearchSuggest(idx) {
  const entry = searchSuggestItems[idx];
  hideSearchDropdown();
  if (!entry) return;
  const inp = document.getElementById('searchInput');
  const type = document.getElementById('searchType');
  if (entry.kind === 'place') {
    if (inp) { inp.value = ''; inp.blur(); }
    updateSearchClearBtn();
    if (entry.place.tab) switchTab(entry.place.tab);
    else entry.place.run();
    return;
  }
  if (entry.kind === 'magnet') {
    debridMagnet(entry.query);
    return;
  }
  if (entry.kind === 'recent') {
    if (inp) inp.value = entry.query;
    if (type && [...type.options].some(option => option.value === entry.mode)) type.value = entry.mode;
    toggleSearchMode();
    runSearch();
    return;
  }
  if (entry.kind === 'all') {
    runSearch();
    return;
  }
  if (entry.kind === 'raw') {
    const before = type ? type.value : 'all';
    if (type) type.value = 'raw';
    runSearch();  // reads the mode before its first wait
    if (type) type.value = before;
    return;
  }
  const it = entry.item || entry;
  if (inp) inp.value = displayTitle(it);
  updateSearchClearBtn();
  selectMedia(it);
}

function onSearchKeyDown(e) {
  const dd = document.getElementById('searchDropdown');
  const isDropdownVisible = dd && dd.style.display === 'block' && searchSuggestItems.length > 0;

  if (e.key === 'Escape') {
    // First the panel, then the field; the screen behind stays as it is.
    e.preventDefault();
    e.stopPropagation();
    if (isDropdownVisible) hideSearchDropdown();
    else e.target.blur();
    return;
  }
  if (isDropdownVisible) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      searchSuggestIndex = (searchSuggestIndex + 1) % searchSuggestItems.length;
      updateSelectedSuggest();
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      searchSuggestIndex = (searchSuggestIndex - 1 + searchSuggestItems.length) % searchSuggestItems.length;
      updateSelectedSuggest();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (searchSuggestIndex >= 0 && searchSuggestIndex < searchSuggestItems.length) {
        selectSearchSuggest(searchSuggestIndex);
      } else {
        hideSearchDropdown();
        runSearch();
      }
      return;
    }
  } else if (e.key === 'Enter') {
    hideSearchDropdown();
    runSearch();
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    openSearchPanel();
  }
}

function updateSelectedSuggest() {
  document.querySelectorAll('.search-suggest-item').forEach((el, idx) => {
    const selected = idx === searchSuggestIndex;
    el.classList.toggle('selected', selected);
    el.setAttribute('aria-selected', String(selected));
    if (selected && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' });
  });
  const input = document.getElementById('searchInput');
  if (input) {
    if (searchSuggestIndex >= 0) input.setAttribute('aria-activedescendant', `suggestItem_${searchSuggestIndex}`);
    else input.removeAttribute('aria-activedescendant');
  }
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('.search-box')) {
    hideSearchDropdown();
  }
});

async function runSearch() {
  clearTimeout(searchDebounceTimer);
  const mode = document.getElementById('searchType').value;
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;

  hideSearchDropdown();
  if (mode === 'magnet' || /^magnet:\?/i.test(q)) {
    debridMagnet(q);
    return;
  }
  noteSearch(q, mode);
  rememberSearch(q, mode);

  // Results stand alone: nothing of the tab they were searched from stays around them.
  searchResultsShown = true;
  personView = null;
  for (const id of ['communityPanel', 'animeSchedulePanel', 'animeHomePanel', 'librarySubNav', 'animeSubNav', 'watchlistActionsWrap', 'listStatsBadge']) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  }
  const listTools = document.getElementById('listFilters');
  if (listTools) listTools.hidden = true;

  document.getElementById('detailPanel').style.display = 'none';
  hideDetailMore();
  document.getElementById('rdCloudPanel').style.display = 'none';
  document.getElementById('viewingStatsPanel').style.display = 'none';
  hideHomeRows();
  const hs = document.getElementById('heroSpotlight');
  if (hs) hs.style.display = 'none';
  const gf = document.getElementById('genreFilters');
  const sw = document.getElementById('catalogSortWrap');
  const lm = document.getElementById('loadMoreWrap');
  if (gf) gf.style.display = 'none';
  if (sw) sw.style.display = 'none';
  if (lm) lm.style.display = 'none';
  const setP = document.getElementById('settingsPanel');
  if (setP) setP.style.display = 'none';
  const setTab = document.getElementById('tab-settings');
  if (setTab) setTab.classList.remove('active');

  if (mode === 'raw') {
    document.getElementById('postersGrid').innerHTML = '';
    document.getElementById('seriesControls').style.display = 'none';
    loadTorrents({q, title: q});
    return;
  }

  document.getElementById('postersGrid').style.display = 'grid';
  document.getElementById('catalogHeader').style.display = 'flex';
  document.getElementById('catalogTitle').textContent = `Résultats pour « ${q} »`;
  const grid = document.getElementById('postersGrid');
  grid.innerHTML = posterSkeletons();
  document.getElementById('torrentsPanel').style.display = 'none';
  if (!networkOnline) {
    grid.innerHTML = offlineSearchHtml(q);
    return;
  }

  try {
    const data = await api(`/api/search?type=${mode}&q=${encodeURIComponent(q)}`);
    rememberFrenchTitles(data.metas);
    if (!data.metas || !data.metas.length) {
      grid.innerHTML = '';
      loadTorrents({q, title: q});
      return;
    }
    if (mode === 'all') {
      lastSearch = { query: q, metas: data.metas, filter: 'all' };
      renderSearchResults();
      return;
    }
    renderPosterCards(data.metas.slice(0, 36), mode);
  } catch (e) {
    grid.innerHTML = e.code === 'offline' ? offlineSearchHtml(q)
      : `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${escapeHtml(e.message)}</p>`;
  }
}

// « Tout »: films and series of one search, the group holding the closest
// title first; seen titles stay (their cards say so).
let lastSearch = { query: '', metas: [], filter: 'all' };
const SEARCH_GROUP_SIZE = 36;

function searchKind(m) {
  return m?.type === 'series' ? 'series' : 'movie';
}

function renderSearchResults() {
  const grid = document.getElementById('postersGrid');
  if (!grid) return;
  const metas = lastSearch.metas || [];
  const counts = { all: metas.length, movie: 0, series: 0 };
  metas.forEach(m => { counts[searchKind(m)]++; });
  if (!counts[lastSearch.filter]) lastSearch.filter = 'all';
  const order = [...new Set(metas.map(searchKind))];
  const several = order.length > 1;
  const label = { movie: 'Films', series: 'Séries' };
  const chips = several ? `<div class="search-results-tools" role="group" aria-label="Type de résultat">${
    [['all', 'Tout'], ...order.map(kind => [kind, label[kind]])].map(([kind, text]) =>
      `<button type="button" class="chip${lastSearch.filter === kind ? ' active' : ''}" aria-pressed="${lastSearch.filter === kind}" onclick="setSearchFilter('${kind}')">${text} <span class="list-count">${counts[kind]}</span></button>`).join('')}</div>` : '';
  const groups = lastSearch.filter === 'all' ? order : [lastSearch.filter];
  grid.innerHTML = chips + groups.map(kind => {
    const items = metas.filter(m => searchKind(m) === kind).slice(0, SEARCH_GROUP_SIZE);
    const heading = several && lastSearch.filter === 'all'
      ? `<h3 class="search-group-title">${label[kind]} <span class="list-count">${counts[kind]}</span></h3>` : '';
    return heading + items.map(m => posterCardHtml(m, kind)).join('');
  }).join('');
}

function setSearchFilter(kind) {
  lastSearch.filter = kind;
  renderSearchResults();
}

async function oneClickCard(ev, btn, media, options = {}) {
  ev.stopPropagation();
  // « Sur la TV »: the same source, sent to the TV instead of a player.
  const target = options.target === 'tv' ? { target: 'tv' } : {};
  currentMedia = media;
  const histItem = userHistory.find(h => h.id === media.id);
  const isSeries = Boolean(media.type === 'series' || (media.is_anime && media.type !== 'movie'));
  if (isSeries) {
    const targetEp = getSeriesTargetEpisode(histItem);
    const s = targetEp.season;
    const e = targetEp.episode;
    await oneClickPlay({
      imdb_id: media.id,
      type: 'series',
      season: s,
      episode: e,
      title: `${media.name} — S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`,
      name: media.name,
      poster: media.poster || '',
      year: media.year || '',
      ...target
    }, btn);
  } else {
    await oneClickPlay({
      imdb_id: media.id,
      type: 'movie',
      title: `${media.name}${media.year ? ' (' + media.year + ')' : ''}`,
      name: media.name,
      poster: media.poster || '',
      year: media.year || '',
      ...target
    }, btn);
  }
}

async function oneClickSeriesEpisode(btn, customSeason = null, customEpisode = null) {
  if (!currentMedia) return;
  const s = customSeason || document.getElementById('seasonSelect').value || 1;
  const e = customEpisode || document.getElementById('episodeSelect').value || 1;
  await oneClickPlay({
    imdb_id: currentMedia.id,
    type: 'series',
    season: s,
    episode: e,
    title: `${currentMedia.name} — S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`,
    name: currentMedia.name,
    poster: currentMedia.poster || '',
    year: currentMedia.year || ''
  }, btn);
}

// « Lecteur natif » (default on macOS) also plays Debrid sources: true when it took the
// source. If the source never starts, `fallback` hands it to the built-in player.
async function openNativeStream(options) {
  const native = window.NativePlayer;
  if (!native?.available || !native.enabled || typeof native.openStream !== 'function') return false;
  try {
    await native.openStream(options);
    return true;
  } catch (error) {
    return false;
  }
}

let _isGlobalPlayLaunching = false;
async function oneClickPlay(params, btn) {
  if (_isGlobalPlayLaunching) return;
  _isGlobalPlayLaunching = true;
  stopPreparation();  // a new play replaces a preparation being waited for
  setTimeout(() => { _isGlobalPlayLaunching = false; }, 3000);

  if (btn && btn.disabled) return;
  const origHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = 'Play...';
  }

  const isMacPlatform = /Mac/i.test(navigator.platform || navigator.userAgent);
  // For the TV the server only resolves the source: no player opens on the Mac.
  if (params.target === 'tv') params.player_mode = 'integrated';
  if (!params.player_mode) {
    params.player_mode = window.kinoPlayerMode || (isMacPlatform ? 'integrated' : 'kino');
  }

  const box = document.getElementById('debridResultPanel');
  box.style.display = 'block';
  box.scrollIntoView({behavior: 'smooth'});
  box.innerHTML = `
    <h3>${escapeHtml(params.title)}</h3>
    <p style="color:var(--muted); font-size:0.83rem; margin-top:4px;">Lancement du flux...</p>
  `;

  try {
    const res = await api('/api/one-click-play', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params)
    });
    if (res.preparing) {
      showOneClickPreparation(res, params);
      return;
    }
    if (btn) btn.innerHTML = 'Lancé';
    const plCount = (res.mpv && res.mpv.playlist_count) ? res.mpv.playlist_count : 1;
    renderDebridState(res.debrid, params.season, params.episode, res.chosen_torrent, plCount);
    refreshUserLists();

    if (params.target === 'tv') {
      await sendToTv(res, params, box);
      return;
    }
    const isIntegrated = res.mpv?.mode === 'integrated' || params.player_mode === 'integrated' || (params.player_mode !== 'external' && isMacPlatform);
    if (res.stream_url && isIntegrated) {
      const pl = (res.playlist && res.playlist.length) ? res.playlist : [{ title: params.title, url: res.stream_url }];
      const recovery = { sourceKey: res.source_key, params: { ...params }, excluded: new Set(res.attempted_source_keys || [res.source_key].filter(Boolean)), attempts: 0, pending: false };
      const releaseName = res.file?.filename || res.chosen_torrent || '';
      const openWeb = info => (info && !info.first
        ? openInAppPlayer(info.url, info.title, pl, info.media, info.position || 0)
        : openInAppPlayer(res.stream_url, params.title, pl, currentMedia, info?.position || res.resume_sec || 0, releaseName, recovery));
      const isSeries = params.type === 'series' || params.type === 'anime';
      const nativeMedia = { ...(currentMedia || {}), id: params.imdb_id || currentMedia?.id, type: currentMedia?.type || params.type,
        name: params.name || currentMedia?.name, ...(isSeries ? { season: Number(params.season) || 1, episode: Number(params.episode) || 1 } : {}) };
      const native = await openNativeStream({ url: res.stream_url, title: params.title, media: nativeMedia,
        resumeSec: res.resume_sec || 0, filename: releaseName, playlist: pl, fallback: openWeb });
      if (!native) openWeb(null);
    }

  } catch (e) {
    box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${escapeHtml(e.message)}</p>`;
  } finally {
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }, 2500);
    }
  }
}

// --- « Lire sur la TV » (tv_cast.py): the Mac relays the source, the TV plays it ---
let tvPollTimer = null;

// Any source to the TV: the fiche's « Sur la TV », a file chosen in
// « Fichier prêt », or what a player plays now (the Mac then stops).
function tvSourceUrl(url) {
  const text = String(url || '');
  if (/^https?:\/\//i.test(text)) return text;
  // A KINO address (an offline film, « épisode suivant »): the Mac relays it to the TV as well.
  if (text.startsWith('/') && !text.startsWith('//') && typeof window !== 'undefined') return `${window.location.origin}${text}`;
  return '';
}

function tvMediaPayload(media, season, episode) {
  const base = media || {};
  const code = season && episode ? { season: Number(season), episode: Number(episode) } : {};
  const isSeries = base.type === 'series' || base.type === 'anime' || Boolean(code.season);
  return { id: base.id || '', type: isSeries ? 'series' : 'movie', name: base.name || '', poster: base.poster || '',
    year: base.year || '', ...(isSeries ? code : {}) };
}

function tvTitle(media, fallback = '') {
  if (!media?.name) return fallback || 'KINO';
  if (media.season && media.episode) return `${media.name} — S${String(media.season).padStart(2, '0')}E${String(media.episode).padStart(2, '0')}`;
  return `${media.name}${media.year ? ` (${media.year})` : ''}`;
}

async function castToTv({ stream_url, title = '', filename = '', media = null, resume_sec = 0 }) {
  const source = tvSourceUrl(stream_url);
  if (!source) throw new Error('Aucune source à envoyer à la TV.');
  const status = await api('/api/tv/play', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ stream_url: source, title, filename, media, resume_sec: Math.max(0, Math.floor(Number(resume_sec) || 0)) }),
  });
  renderTvBar(status);
  watchTv();
  return status;
}

async function sendToTv(res, params, box) {
  if (!res.stream_url) {
    if (box) box.innerHTML = '<p style="color:var(--muted); font-size:0.85rem;">Aucune source à envoyer à la TV.</p>';
    return;
  }
  const isSeries = params.type === 'series' || params.type === 'anime';
  const media = { ...(currentMedia || {}), id: params.imdb_id || currentMedia?.id, type: isSeries ? 'series' : 'movie',
    name: params.name || currentMedia?.name, poster: params.poster || currentMedia?.poster || '', year: params.year || currentMedia?.year || '',
    ...(isSeries ? { season: Number(params.season) || 1, episode: Number(params.episode) || 1 } : {}) };
  if (box) box.innerHTML = `<h3>${escapeHtml(params.title)}</h3><p style="color:var(--muted); font-size:0.83rem; margin-top:4px;">Envoi à la TV…</p>`;
  try {
    await castToTv({ stream_url: res.stream_url, title: params.title, filename: res.file?.filename || res.chosen_torrent || '',
      media, resume_sec: res.resume_sec || 0 });
    if (box) box.style.display = 'none';
  } catch (e) {
    if (box) box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">${escapeHtml(e.message)}</p>`;
  }
}

function renderTvBar(status) {
  const bar = document.getElementById('tvBar');
  if (!bar) return;
  if (!status || !status.active) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  document.getElementById('tvBarTitle').textContent = status.title || 'KINO';
  const time = status.duration ? `${formatTvTime(status.position)} / ${formatTvTime(status.duration)}` : '';
  const where = `Sur ${status.device || 'la TV'}`;
  // Between two episodes: a countdown, « Lancer maintenant » and « Annuler ».
  const next = status.next || null;
  const nextText = next && (next.finding ? `Recherche de ${next.code}…` : `${next.code} dans ${next.in} s`);
  document.getElementById('tvBarSub').textContent = status.error
    || (next ? `Épisode suivant · ${nextText}` : status.starting ? `${where} · Démarrage…`
      : [where, status.paused ? 'En pause' : 'En lecture', time].filter(Boolean).join(' · '));
  bar.setAttribute('data-next', next ? 'yes' : 'no');
  const icon = document.querySelector('#tvBarToggle use');
  if (icon) icon.setAttribute('href', `/icons.svg#${status.paused || next ? 'play-solid' : 'pause'}`);
  document.getElementById('tvBarToggle').setAttribute('aria-label',
    next ? 'Lancer l’épisode suivant maintenant' : status.paused ? 'Reprendre sur la TV' : 'Mettre en pause sur la TV');
  const stop = document.getElementById('tvBarStop');
  if (stop) stop.textContent = next ? 'Annuler' : 'Arrêter';
}

function formatTvTime(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor(total / 60) % 60;
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

function watchTv() {
  clearInterval(tvPollTimer);
  tvPollTimer = setInterval(async () => {
    try {
      const status = await api('/api/tv/status');
      renderTvBar(status);
      if (!status.active) clearInterval(tvPollTimer);
    } catch (e) {}
  }, 2000);
}

async function tvControl(action, value = null) {
  try {
    const status = await api('/api/tv/control', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action, value }),
    });
    renderTvBar(status);
  } catch (e) {
    const sub = document.getElementById('tvBarSub');
    if (sub) sub.textContent = e.message;
  }
}

// --- « Préparer en un clic »: Debrid prepares the chosen quality, then it plays ---
// The server answers « preparing » only with « La qualité choisie d'abord » when no
// source in that quality is ready (docs/playback.md).
const PREPARATION_POLL_MS = 3000;
let oneClickPreparation = null;

function preparationHtml(prep) {
  const res = prep.last;
  const progress = Math.max(0, Math.min(100, Number(res.progress) || 0));
  const facts = [res.status ? `Statut : ${escapeHtml(res.status)}` : '', res.speed ? `Vitesse : ${escapeHtml(res.speed)}` : '',
    `Pairs : ${Number(res.seeders) || 0}`].filter(Boolean).join(' • ');
  const ready = prep.readyAlternative
    ? '<button type="button" class="btn" onclick="playReadyInstead()">Lancer une source prête à la place</button>' : '';
  return `<h3>Préparation de la qualité choisie : ${progress} %</h3>
    <p class="preparation-note">${escapeHtml(prep.params.title || '')} : Debrid télécharge la source recommandée, la lecture démarre dès qu’elle est prête.</p>
    <p class="preparation-note">${facts}</p>
    <div class="progress-bar"><div class="progress-fill" style="width:${progress}%"></div></div>
    <div class="preparation-actions">${ready}<button type="button" class="btn btn-secondary" onclick="stopWaitingPreparation()">Arrêter d’attendre</button></div>`;
}

function renderPreparation(prep, html) {
  const box = document.getElementById('debridResultPanel');
  if (!box || oneClickPreparation !== prep) return;
  box.style.display = 'block';
  box.innerHTML = html || preparationHtml(prep);
}

function showOneClickPreparation(res, params) {
  stopPreparation();
  const prep = oneClickPreparation = { torrentId: res.torrent_id, sourceKey: res.source_key, params: { ...params, prepared: undefined },
    last: res, readyAlternative: Boolean(res.ready_alternative), timer: null, waitingForWindow: false };
  renderPreparation(prep);
  prep.timer = setTimeout(() => pollPreparation(prep), PREPARATION_POLL_MS);
}

async function pollPreparation(prep) {
  if (oneClickPreparation !== prep) return;
  // A hidden window does no periodic work: it checks again when it shows.
  if (document.hidden) {
    prep.waitingForWindow = true;
    return;
  }
  try {
    const next = await api('/api/debrid-status', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ torrent_id: prep.torrentId, season: prep.params.season, episode: prep.params.episode }) });
    if (oneClickPreparation !== prep) return;
    if (next.ready) {
      oneClickPreparation = null;
      _isGlobalPlayLaunching = false;
      await oneClickPlay({ ...prep.params, prepared: { torrent_id: prep.torrentId, source_key: prep.sourceKey } });
      return;
    }
    prep.last = { ...prep.last, ...next };
    renderPreparation(prep);
    prep.timer = setTimeout(() => pollPreparation(prep), PREPARATION_POLL_MS);
  } catch (error) {
    if (oneClickPreparation !== prep) return;
    const ready = prep.readyAlternative
      ? '<button type="button" class="btn" onclick="playReadyInstead()">Lancer une source prête à la place</button>' : '';
    renderPreparation(prep, `<p class="preparation-note">Préparation interrompue : ${escapeHtml(error.message)}</p><div class="preparation-actions">${ready}</div>`);
  }
}

document.addEventListener('visibilitychange', () => {
  const prep = oneClickPreparation;
  if (prep?.waitingForWindow && !document.hidden) {
    prep.waitingForWindow = false;
    pollPreparation(prep);
  }
});

function stopPreparation() {
  if (oneClickPreparation) clearTimeout(oneClickPreparation.timer);
  oneClickPreparation = null;
}

function playReadyInstead() {
  const prep = oneClickPreparation;
  if (!prep) return;
  stopPreparation();
  _isGlobalPlayLaunching = false;
  oneClickPlay({ ...prep.params, prefer_ready: true });
}

function stopWaitingPreparation() {
  const prep = oneClickPreparation;
  if (!prep) return;
  renderPreparation(prep, '<p class="preparation-note">Vous n’attendez plus : Debrid continue la préparation, et la source sera prête plus tard dans votre Cloud.</p>');
  stopPreparation();
}

// --- French display titles --------------------------------------------------
// Catalogue names are English (and stay so for source searches). Elements that
// show a title carry data-title-*; the French title replaces the text as soon
// as it is known: at once from this cache, otherwise after one server request.
const FRENCH_TITLES = new Map();
// v2: v1 could hold a famous film's French title on a homonym listing.
const FRENCH_TITLES_KEY = 'kino_french_titles_v2';
const FRENCH_TITLES_MAX = 3000;
try { localStorage.removeItem('kino_french_titles_v1'); } catch (error) {}
try {
  for (const [id, title] of Object.entries(JSON.parse(localStorage.getItem(FRENCH_TITLES_KEY) || '{}'))) {
    if (typeof title === 'string') FRENCH_TITLES.set(id, title);
  }
} catch (error) {}
const frenchTitleQueue = new Map();
let frenchTitleTimer = null;

function titleAttrs(m, type) {
  const id = String(m?.id || '');
  if (!/^tt\d{1,12}$/.test(id)) return '';
  const year = String(m.year || m.releaseInfo || '').match(/\d{4}/)?.[0] || '';
  const kind = (type || m.type) === 'series' || (m.is_anime && m.type !== 'movie') ? 'series' : 'movie';
  return ` data-title-id="${escapeHtml(id)}" data-title-name="${escapeHtml(m.name || '')}" data-title-year="${year}" data-title-type="${kind}"`;
}

function displayTitle(m) {
  return FRENCH_TITLES.get(String(m?.id || '')) || m?.name_fr || m?.name || '';
}

// Search results found by their French title carry it (name_fr): remember it.
function rememberFrenchTitles(items) {
  let added = false;
  for (const item of items || []) {
    if (item?.id && item.name_fr && FRENCH_TITLES.get(item.id) !== item.name_fr) {
      FRENCH_TITLES.set(item.id, item.name_fr);
      added = true;
    }
  }
  if (added) {
    try { localStorage.setItem(FRENCH_TITLES_KEY, JSON.stringify(Object.fromEntries(FRENCH_TITLES))); } catch (error) {}
  }
}

function applyFrenchTitle(el) {
  const french = FRENCH_TITLES.get(el.dataset.titleId);
  if (french === undefined) return false;
  if (french && el.textContent !== french) {
    const original = el.dataset.titleName || el.textContent;
    el.textContent = french;
    if (el.hasAttribute('title')) el.title = french;
    const card = el.closest('[aria-label]');
    if (card && card.getAttribute('aria-label') === original) card.setAttribute('aria-label', french);
  }
  const hero = el.closest('.detail-hero');
  if (hero && french && french.toLowerCase() !== String(el.dataset.titleName || '').toLowerCase()) {
    hero.classList.add('has-french-title');
  }
  return true;
}

function scanFrenchTitles(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return;
  const elements = [...root.querySelectorAll('[data-title-id]')];
  if (typeof root.matches === 'function' && root.matches('[data-title-id]')) elements.unshift(root);
  for (const el of elements) {
    if (applyFrenchTitle(el)) continue;
    const id = el.dataset.titleId;
    if (!frenchTitleQueue.has(id)) {
      frenchTitleQueue.set(id, {id, name: el.dataset.titleName, year: el.dataset.titleYear, type: el.dataset.titleType});
    }
  }
  if (frenchTitleQueue.size) {
    clearTimeout(frenchTitleTimer);
    frenchTitleTimer = setTimeout(fetchFrenchTitles, 120);
  }
}

async function fetchFrenchTitles() {
  const items = [...frenchTitleQueue.values()].slice(0, 60);
  items.forEach(item => frenchTitleQueue.delete(item.id));
  if (!items.length) return;
  try {
    const result = await api('/api/french-titles', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({items}),
    });
    for (const [id, title] of Object.entries(result.titles || {})) FRENCH_TITLES.set(id, String(title || ''));
    while (FRENCH_TITLES.size > FRENCH_TITLES_MAX) FRENCH_TITLES.delete(FRENCH_TITLES.keys().next().value);
    try { localStorage.setItem(FRENCH_TITLES_KEY, JSON.stringify(Object.fromEntries(FRENCH_TITLES))); } catch (error) {}
    document.querySelectorAll('[data-title-id]').forEach(applyFrenchTitle);
  } catch (error) {}
  if (frenchTitleQueue.size) frenchTitleTimer = setTimeout(fetchFrenchTitles, 120);
}

function initFrenchTitles() {
  scanFrenchTitles(document.body);
  new MutationObserver(records => {
    for (const record of records) {
      for (const node of record.addedNodes) if (node.nodeType === 1) scanFrenchTitles(node);
    }
  }).observe(document.body, { childList: true, subtree: true });
}

const GENRE_FR = {
  'Action': 'Action', 'Adventure': 'Aventure', 'Animation': 'Animation', 'Biography': 'Biopic', 'Comedy': 'Comédie',
  'Crime': 'Policier', 'Documentary': 'Documentaire', 'Drama': 'Drame', 'Family': 'Famille', 'Fantasy': 'Fantastique',
  'History': 'Histoire', 'Horror': 'Horreur', 'Mystery': 'Mystère', 'Romance': 'Romance', 'Sci-Fi': 'Science-Fiction',
  'Thriller': 'Thriller', 'War': 'Guerre', 'Western': 'Western', 'Music': 'Musique', 'Musical': 'Comédie musicale',
  'Sport': 'Sport', 'Film-Noir': 'Film noir', 'Short': 'Court métrage', 'Reality-TV': 'Téléréalité',
};

function frenchGenre(genre) {
  return GENRE_FR[genre] || genre;
}

// --- Immersive fiche: French labels for catalogue data (Cinemeta is in English) ---
const COUNTRY_FR = {
  'United States': 'États-Unis', 'USA': 'États-Unis', 'United Kingdom': 'Royaume-Uni', 'UK': 'Royaume-Uni',
  'France': 'France', 'Canada': 'Canada', 'Japan': 'Japon', 'Germany': 'Allemagne', 'West Germany': 'Allemagne de l’Ouest',
  'Italy': 'Italie', 'Spain': 'Espagne', 'South Korea': 'Corée du Sud', 'Korea, South': 'Corée du Sud', 'China': 'Chine',
  'Hong Kong': 'Hong Kong', 'Taiwan': 'Taïwan', 'India': 'Inde', 'Australia': 'Australie', 'New Zealand': 'Nouvelle-Zélande',
  'Belgium': 'Belgique', 'Switzerland': 'Suisse', 'Netherlands': 'Pays-Bas', 'Sweden': 'Suède', 'Norway': 'Norvège',
  'Denmark': 'Danemark', 'Finland': 'Finlande', 'Ireland': 'Irlande', 'Mexico': 'Mexique', 'Brazil': 'Brésil',
  'Argentina': 'Argentine', 'Russia': 'Russie', 'Soviet Union': 'Union soviétique', 'Poland': 'Pologne',
  'Czech Republic': 'République tchèque', 'Czechia': 'Tchéquie', 'Austria': 'Autriche', 'Hungary': 'Hongrie',
  'Greece': 'Grèce', 'Turkey': 'Turquie', 'Israel': 'Israël', 'South Africa': 'Afrique du Sud', 'Thailand': 'Thaïlande',
  'Iceland': 'Islande', 'Portugal': 'Portugal', 'Luxembourg': 'Luxembourg', 'Morocco': 'Maroc', 'Egypt': 'Égypte',
  'Chile': 'Chili', 'Colombia': 'Colombie', 'Indonesia': 'Indonésie', 'Romania': 'Roumanie', 'Ukraine': 'Ukraine',
  'United Arab Emirates': 'Émirats arabes unis', 'Malta': 'Malte', 'Bulgaria': 'Bulgarie', 'Serbia': 'Serbie',
  'Croatia': 'Croatie', 'Lebanon': 'Liban', 'Jordan': 'Jordanie', 'Saudi Arabia': 'Arabie saoudite', 'Vietnam': 'Viêt Nam',
  'Singapore': 'Singapour', 'Malaysia': 'Malaisie', 'Peru': 'Pérou', 'Estonia': 'Estonie', 'Latvia': 'Lettonie',
  'Lithuania': 'Lituanie', 'Slovakia': 'Slovaquie', 'Slovenia': 'Slovénie', 'Georgia': 'Géorgie', 'Algeria': 'Algérie',
  'Tunisia': 'Tunisie', 'Senegal': 'Sénégal', 'Cyprus': 'Chypre', 'Philippines': 'Philippines', 'Iran': 'Iran',
};

function frenchCountries(value, max = 2) {
  return String(value || '').split(',').map(part => part.trim()).filter(Boolean).slice(0, max)
    .map(country => COUNTRY_FR[country] || country).join(', ');
}

function frenchRuntime(value) {
  const match = String(value || '').match(/(\d+)\s*min/i);
  if (!match) return String(value || '');
  const minutes = Number(match[1]);
  if (minutes < 60) return `${minutes} min`;
  const rest = minutes % 60;
  return `${Math.floor(minutes / 60)} h${rest ? ' ' + String(rest).padStart(2, '0') : ''}`;
}

// « Won 2 Oscars. 7 wins & 17 nominations total » → French; unknown sentences are left out.
function frenchAwards(text) {
  const plural = (count, word) => `${count} ${word}${Number(count) > 1 ? 's' : ''}`;
  const prizes = { 'Oscar': 'Oscar', 'Golden Globe': 'Golden Globe', 'Primetime Emmy': 'Emmy', 'BAFTA Award': 'BAFTA', 'BAFTA Film Award': 'BAFTA' };
  const parts = [];
  for (const sentence of String(text || '').split(/\.\s*/)) {
    let m;
    if ((m = sentence.match(/^Won (\d+) (Oscar|Golden Globe|Primetime Emmy|BAFTA Film Award|BAFTA Award)s?$/i))) {
      parts.push(`Lauréat de ${plural(m[1], prizes[Object.keys(prizes).find(k => k.toLowerCase() === m[2].toLowerCase())] || m[2])}`);
    } else if ((m = sentence.match(/^Nominated for (\d+) (Oscar|Golden Globe|Primetime Emmy|BAFTA Film Award|BAFTA Award)s?$/i))) {
      parts.push(`Nommé à ${plural(m[1], prizes[Object.keys(prizes).find(k => k.toLowerCase() === m[2].toLowerCase())] || m[2])}`);
    } else if ((m = sentence.match(/^(\d+) wins? & (\d+) nominations? total$/i))) {
      parts.push(`${plural(m[1], 'récompense')} et ${plural(m[2], 'nomination')} au total`);
    } else if ((m = sentence.match(/^(\d+) wins? total$/i))) {
      parts.push(`${plural(m[1], 'récompense')} au total`);
    } else if ((m = sentence.match(/^(\d+) nominations? total$/i))) {
      parts.push(`${plural(m[1], 'nomination')} au total`);
    }
  }
  return parts.join(' · ');
}

function personInitials(name) {
  const words = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '?';
  return (words[0][0] + (words.length > 1 ? words[words.length - 1][0] : '')).toUpperCase();
}

function largeBackdrop(url) {
  return String(url || '').replace('images.metahub.space/background/medium/', 'images.metahub.space/background/large/');
}

function buildDetailHtml(media, meta, isPreview) {
  const isSeries = Boolean(media.type === 'series' || meta.type === 'series' || (meta.videos && meta.videos.length > 0) || (media.is_anime && media.type !== 'movie'));
  const poster = meta.poster || media.poster || '';
  const year = meta.releaseInfo || meta.year || media.year || '';
  const rating = meta.imdbRating || media.imdbRating || '';
  const runtime = meta.runtime || '';
  const rawGenres = (meta.genres || meta.genre || media.genres || []).slice(0, 5);
  const genres = (meta.genres_fr || rawGenres).slice(0, 5);

  // Synopsis text comes from external catalogs: escape it; only the skeleton is markup.
  let desc = escapeHtml(meta.description_fr || meta.description || media.overview || media.description || '');
  if (!desc && isPreview) {
    desc = '<div class="detail-skeleton-line" style="width:90%;"></div><div class="detail-skeleton-line" style="width:75%; margin-top:6px;"></div>';
  } else if (!desc) {
    desc = 'Aucun synopsis disponible.';
  }

  const castArr = (meta.cast || []).slice(0, 7);
  const dirArr = Array.isArray(meta.director) ? meta.director : (meta.director ? [meta.director] : []);
  const mtypeSafe = JSON.stringify(media.type || 'movie').replace(/'/g, "&#39;");
  const country = meta.country || '';
  const awards = meta.awards || '';
  const trailerId = (meta.trailers && meta.trailers[0] && meta.trailers[0].source) ? meta.trailers[0].source : '';
  const inList = isInWatchlist(media.id);
  const mediaPayload = JSON.stringify({id: media.id, name: media.name, type: media.type, year: String(year), poster, imdbRating: String(rating)}).replace(/'/g, "&#39;");
  const safeNameJs = JSON.stringify(meta.name || media.name).replace(/'/g, "&#39;");
  const safeYearJs = JSON.stringify(String(year)).replace(/'/g, "&#39;");

  const histItem = userHistory.find(h => h.id === media.id);
  const userRating = (histItem && histItem.user_rating) || media.user_rating || '';
  const isMovieDone = Boolean(histItem && !isSeries && (histItem.completed || Number(histItem.progress_pct || 0) >= 85));
  const latestAired = meta.latest_aired || null;
  const latestAiredUnwatched = Boolean(latestAired && latestAired.code && !Spoilers.watched(userHistory, media.id, latestAired.season, latestAired.episode));
  const latestAiredTitle = latestAired ? spoilerEpisodeLabel(media.id, latestAired.season, latestAired.episode, latestAired.title) : '';

  let targetSeason = 1;
  let targetEpisode = 1;
  let playBtnLabel = 'Lancer';
  if (isSeries) {
    const targetEp = getSeriesTargetEpisode(histItem);
    targetSeason = targetEp.season;
    targetEpisode = targetEp.episode;
    // A series not started yet opens on the season it was shown for (anime rows, planning).
    if (!histItem && Number(media.open_season) > 1) {
      targetSeason = Number(media.open_season);
      targetEpisode = 1;
    }
    const epCode = `S${String(targetSeason).padStart(2,'0')}E${String(targetEpisode).padStart(2,'0')}`;
    playBtnLabel = targetEp.finished ? `Revoir (${epCode})` : (targetEp.isNext ? `Épisode suivant (${epCode})` : `Lancer ${epCode}`);
  } else if (histItem && histItem.progress_pct > 1 && histItem.progress_pct < 85) {
    playBtnLabel = `Reprendre (${Math.round(histItem.progress_pct)}%)`;
  } else if (isMovieDone) {
    playBtnLabel = 'Revoir le film';
  }

  const backdropUrl = meta.background || meta.backdrop || media.backdrop || media.background || (media.id && String(media.id).startsWith('tt') ? `https://images.metahub.space/background/medium/${media.id}/img` : (poster || ''));
  const isImdbId = Boolean(media.id && String(media.id).startsWith('tt'));
  const logoUrl = meta.logo || (isImdbId ? `https://images.metahub.space/logo/medium/${media.id}/img` : '');
  const awardsFr = frenchAwards(awards);
  const people = [...dirArr.slice(0, 2).map(name => ({name, role: 'Réalisation'})),
    ...castArr.map(name => ({name, role: 'Distribution'}))];
  const castHtml = people.length && !isPreview ? `
    <section class="detail-extra-section detail-cast" aria-label="Distribution">
      <div class="detail-section-title">Distribution</div>
      <div class="detail-cast-row">
        ${people.map(person => {
          const nameJs = JSON.stringify(person.name).replace(/'/g, "&#39;");
          return `<button type="button" class="detail-person" data-person="${escapeHtml(person.name)}" onclick='openPerson(${nameJs})' title="Films et séries de ${escapeHtml(person.name)}">
            <span class="detail-person-photo" aria-hidden="true">${escapeHtml(personInitials(person.name))}</span>
            <span class="detail-person-name">${escapeHtml(person.name)}</span>
            <span class="detail-person-role">${person.role}</span>
          </button>`;
        }).join('')}
      </div>
    </section>` : '';

  let episodesHtml = '';
  if (isSeries) {
    if (isPreview) {
      episodesHtml = `
        <div class="detail-extra-section">
          <div style="font-size:0.8rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted); margin-bottom:8px;">Épisodes</div>
          <div class="detail-skeleton-line" style="width:45%; margin-bottom:12px;"></div>
          <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:10px;">
            <div class="detail-skeleton-line" style="height:48px;"></div>
            <div class="detail-skeleton-line" style="height:48px;"></div>
            <div class="detail-skeleton-line" style="height:48px;"></div>
          </div>
        </div>
      `;
    } else {
      seriesMetaVideos = (meta.videos || []).filter(v => v.season > 0);
      const seasons = [...new Set(seriesMetaVideos.map(v => v.season))].sort((a,b) => a - b);
      if (seasons.length) {
        if (!seasons.includes(targetSeason)) targetSeason = seasons[0];
        episodesHtml = `
          <div class="detail-extra-section detail-episodes">
            <div class="detail-season-bar">
              <div class="detail-season-pills" role="group" aria-label="Saisons">
                ${seasons.map(s => `<button class="season-pill ${s===targetSeason?'active':''}" data-season-chip="${s}" onclick="selectDetailSeason(${s})">Saison ${s}</button>`).join('')}
              </div>
              <div class="detail-episodes-tools">
                <div class="spoiler-series-setting"><label for="detailSpoilerChoice">Spoilers</label><select id="detailSpoilerChoice" aria-label="Protection des spoilers pour cette série" onchange="setSeriesSpoilerProtection(this.value)"><option value="inherit">Réglage général</option><option value="on">Masquer les épisodes non vus</option><option value="off">Tout afficher</option></select></div>
                <button class="btn btn-secondary btn-small" id="detailSeasonAllWatchedBtn" onclick="toggleWatchedWholeSeason()">✓ Marquer la saison comme vue</button>
                <button class="btn btn-secondary btn-small" id="detailSeasonOfflineBtn" onclick="downloadSeasonOffline(this)" title="Télécharger les épisodes sortis de cette saison, l’un après l’autre">⤓ Saison hors ligne</button>
              </div>
            </div>
            <div id="detailSpoilerOffer" class="spoiler-offer">Masquer les titres et images des épisodes non vus ? <button type="button" class="btn btn-secondary" onclick="setGlobalSpoilerProtection(true)">Activer la protection</button></div>
            <p id="detailSeasonSummary" class="ep-summary" aria-live="polite"></p>
            <div class="ep-strip" id="detailEpisodesStrip">
              <button type="button" class="ep-scroll ep-scroll-prev" aria-label="Épisodes précédents" onclick="scrollEpisodes(-1)" hidden>‹</button>
              <div id="detailEpisodesList" class="ep-row" role="list" aria-label="Épisodes" onscroll="updateEpisodeArrows()"></div>
              <button type="button" class="ep-scroll ep-scroll-next" aria-label="Épisodes suivants" onclick="scrollEpisodes(1)" hidden>›</button>
            </div>
          </div>
        `;
      }
    }
  }

  const journeyHtml = (media.is_anime || activeTab === 'anime')
    ? '<section class="detail-extra-section anime-journey-section" aria-label="Parcours de la saga"><div id="animeJourneyPanel"><p class="journey-empty">Chargement du parcours…</p></div></section>'
    : '';

  const similarHtml = similarSectionHtml(Array.isArray(meta.similar) ? meta.similar : [], isSeries);

  const linksHtml = isImdbId && !isPreview ? `
    <section class="detail-extra-section detail-links" aria-label="Ailleurs">
      <div class="detail-section-title">Ailleurs</div>
      <div class="detail-links-row">
        <a class="btn btn-secondary" href="https://www.imdb.com/title/${encodeURIComponent(media.id)}/" target="_blank" rel="noopener" title="Voir la fiche sur IMDb">IMDb ↗</a>
        ${!isSeries ? `<a class="btn btn-secondary" href="https://letterboxd.com/imdb/${encodeURIComponent(media.id)}/" target="_blank" rel="noopener" title="Voir sur Letterboxd">Letterboxd ↗</a>` : ''}
      </div>
    </section>` : '';

  const latestAiredBanner = (isSeries && latestAired) ? `
    <div class="detail-latest${latestAiredUnwatched ? ' is-new' : ''}">
      <span id="latestAiredText">${latestAiredUnwatched ? '● Dernier épisode diffusé (non vu) :' : '✓ Dernier épisode diffusé :'} <strong>${escapeHtml(latestAired.code)}</strong> — ${escapeHtml(latestAiredTitle)} (${escapeHtml(latestAired.released || '')})</span>
      ${latestAiredUnwatched ? `<button class="btn btn-secondary btn-small" onclick="oneClickSeriesEpisode(this, ${latestAired.season}, ${latestAired.episode})">Lancer</button>` : ''}
    </div>
  ` : '';

  return {
    isSeries,
    targetSeason,
    targetEpisode,
    html: `
      <section class="detail-hero" id="detailHero">
        <img class="detail-hero-backdrop" src="${escapeHtml(largeBackdrop(backdropUrl))}" alt="" onload="ficheGlow(this)" onerror="detailBackdropFallback(this, ${escapeHtml(JSON.stringify(poster))})">
        <div class="detail-hero-shade"></div>
        <div class="detail-trailer" id="detailTrailer" hidden>
          <div class="detail-trailer-frame" id="detailTrailerFrame"></div>
          <div class="detail-trailer-bar">
            <button type="button" class="btn btn-secondary" data-trailer-lang="vf" onclick="switchDetailTrailer('vf')">VF</button>
            <button type="button" class="btn btn-secondary" data-trailer-lang="vo" onclick="switchDetailTrailer('vo')">VO</button>
            <a class="btn btn-secondary" id="detailTrailerLink" href="#" target="_blank" hidden>Ouvrir sur YouTube ↗</a>
            <button type="button" class="btn" onclick="closeDetailTrailer()">Fermer la bande-annonce</button>
          </div>
        </div>
        <button class="detail-close-btn" onclick="closeFiche()" title="Fermer la fiche (Échap)" aria-label="Fermer la fiche">
          <svg class="ui-icon ui-icon-sm" aria-hidden="true"><use href="/icons.svg#close"></use></svg>
        </button>
        <div class="detail-hero-body">
          <h2 class="detail-hero-title">
            ${logoUrl ? `<img class="detail-hero-logo" src="${escapeHtml(logoUrl)}" alt="" onload="this.closest('.detail-hero')?.classList.add('has-logo')" onerror="this.remove()">` : ''}
            <span class="detail-hero-name"${titleAttrs({...media, name: meta.name || media.name, year: String(year)}, isSeries ? 'series' : 'movie')}>${escapeHtml(displayTitle({id: media.id, name: meta.name || media.name}))}</span>
          </h2>
          <div class="detail-hero-original">Titre original : ${escapeHtml(meta.name || media.name)}</div>
          <div class="detail-meta-row">
            ${ficheRatingHtml(media.id, histItem, userRating)}
            ${rating ? `<span class="detail-chip detail-chip-strong">★ ${escapeHtml(rating)} IMDb</span>` : ''}
            ${year ? `<span class="detail-chip">${escapeHtml(year)}</span>` : ''}
            ${runtime ? `<span class="detail-meta-text">${escapeHtml(frenchRuntime(runtime))}${isSeries ? ' par épisode' : ''}</span>` : ''}
            ${country ? `<span class="detail-meta-text">${escapeHtml(frenchCountries(country))}</span>` : ''}
            ${genres.map((g, idx) => {
              const rawG = rawGenres[idx] || g;
              const rawGJs = JSON.stringify(rawG).replace(/'/g, "&#39;");
              return `<span class="badge" style="cursor:pointer;" title="Filtrer par ${escapeHtml(g)}" onclick='selectGenre(${rawGJs}, document.querySelector("#genreFilters .chip[data-genre=\\"" + ${rawGJs} + "\\"]"))'>${escapeHtml(g)}</span>`;
            }).join('')}
          </div>
          ${latestAiredBanner}
          <div class="detail-desc">${desc}</div>
          ${awardsFr ? `<div class="detail-awards">${escapeHtml(awardsFr)}</div>` : ''}
          <div class="detail-actions">
            <button class="btn btn-play-hero" id="detailMainPlayBtn" onclick='oneClickCard(event, this, ${mediaPayload})'>
              <svg class="ui-icon ui-icon-sm" aria-hidden="true"><use href="/icons.svg#play-solid"></use></svg>
              ${playBtnLabel}
            </button>
            <button class="btn btn-secondary" id="detailTvBtn" onclick='oneClickCard(event, this, ${mediaPayload}, { target: "tv" })' title="Lire sur la TV du salon">
              <svg class="ui-icon ui-icon-sm" aria-hidden="true"><use href="/icons.svg#cast"></use></svg>
              Sur la TV
            </button>
            <button class="btn btn-secondary" onclick='playDetailTrailer(${JSON.stringify(trailerId)}, ${safeNameJs}, ${safeYearJs}, "vf")'>
              <svg class="ui-icon ui-icon-xs" aria-hidden="true"><use href="/icons.svg#trailer"></use></svg>
              Bande-annonce
            </button>
            <button class="btn btn-secondary" id="detailWlBtn" onclick='toggleWatchlist(event, ${mediaPayload})'>
              ${inList ? '<svg class="ui-icon ui-icon-xs" aria-hidden="true"><use href="/icons.svg#tick"></use></svg> Dans ma liste' : '+ Ma Liste'}
            </button>
            ${!isSeries ? `<button class="btn btn-secondary" style="${isMovieDone ? 'border-color:var(--text); color:var(--text);' : ''}" onclick='toggleWatchedItem(${mediaPayload})'>${isMovieDone ? '✓ Vu' : '✓ Marquer comme vu'}</button>` : ''}
          </div>
        </div>
      </section>
      ${episodesHtml}
      ${journeyHtml}
    `,
    moreHtml: `${castHtml}${similarHtml}${linksHtml}`,
  };
}

// The fiche glows with its own image (design.css), as the « À la une » banner: set
// once the backdrop has loaded, so WebKit paints the whole glow (see setHeroImage).
function ficheGlow(img) {
  const panel = document.getElementById('detailPanel');
  const src = img && (img.currentSrc || img.src);
  if (panel && src && panel.contains(img)) panel.style.setProperty('--hero-glow', `url("${String(src).replace(/["\\\n]/g, encodeURIComponent)}")`);
}

function similarSectionHtml(list, isSeries) {
  return list.length ? `
    <section class="detail-extra-section detail-similar" aria-label="Vous aimerez aussi">
      <div class="detail-section-title">Vous aimerez aussi</div>
      <div class="list-shelf-row">${list.map(sm => posterCardHtml(sm, sm.type === 'series' || (!sm.type && isSeries) ? 'series' : 'movie')).join('')}</div>
    </section>` : '';
}

// « Vous aimerez aussi » from TMDB's recommendations, once they are known:
// the fiche opens with the genre match, then this replaces it for the same title.
async function loadRelatedForFiche(media, isSeries) {
  const id = String(media?.id || '');
  if (!/^tt\d{1,12}$/.test(id)) return;
  let related = RELATED_CLIENT_CACHE.get(id);
  if (!related) {
    try {
      related = await api(`/api/related?type=${isSeries ? 'series' : 'movie'}&imdb_id=${encodeURIComponent(id)}`);
      RELATED_CLIENT_CACHE.set(id, related || { items: [] });
    } catch (e) {
      return;
    }
  }
  if (currentMedia?.id !== id || related?.source !== 'tmdb') return;
  const seen = new Set(getWatchedHistory().map(h => h.id));
  const items = (related.items || []).filter(m => m?.id && m.id !== id && !seen.has(m.id));
  const more = document.getElementById('detailMore');
  if (!more || items.length < 4) return;
  const html = similarSectionHtml(items, isSeries);
  const existing = more.querySelector('.detail-similar');
  if (existing) existing.outerHTML = html;
  else {
    const links = more.querySelector('.detail-links');
    if (links) links.insertAdjacentHTML('beforebegin', html);
    else more.insertAdjacentHTML('beforeend', html);
  }
  more.hidden = false;
}

// No backdrop for this title: the poster, blurred, fills the hero instead.
function detailBackdropFallback(img, poster) {
  img.onerror = null;
  if (!poster || img.dataset.fallback) { img.style.opacity = '0'; return; }
  img.dataset.fallback = '1';
  img.classList.add('is-poster-fallback');
  img.src = poster;
}

async function loadDetailPeoplePhotos(mediaId) {
  const buttons = [...document.querySelectorAll('#detailMore .detail-person[data-person]')];
  if (!buttons.length) return;
  const names = buttons.map(button => button.dataset.person);
  let people = {};
  try {
    people = (await api(`/api/people?names=${encodeURIComponent(names.join('|'))}`)).people || {};
  } catch (error) {
    return;
  }
  if (currentSelectMediaId !== mediaId) return;
  for (const button of buttons) {
    const url = people[button.dataset.person];
    const slot = button.querySelector('.detail-person-photo');
    if (!url || !slot) continue;
    // Not lazy: a lazy image that is not yet in the page is never fetched.
    const img = new Image();
    img.alt = '';
    img.decoding = 'async';
    img.onload = () => { slot.textContent = ''; slot.appendChild(img); slot.classList.add('has-photo'); };
    img.src = url;
  }
}

// --- Page acteur: films and series confirmed by their credits (person_page.py) ---
let personView = null;
const PERSON_HIDDEN = ['communityPanel', 'animeSchedulePanel', 'animeHomePanel', 'librarySubNav', 'animeSubNav', 'watchlistActionsWrap',
  'listStatsBadge', 'rdCloudPanel', 'viewingStatsPanel', 'heroSpotlight', 'genreFilters', 'catalogSortWrap', 'loadMoreWrap',
  'settingsPanel'];

// Like a search: only the page heading and the grid stay.
function showPersonScreen(name) {
  closeDetailPanel();
  hideHomeRows();
  searchResultsShown = true;
  for (const id of PERSON_HIDDEN) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  }
  const listTools = document.getElementById('listFilters');
  if (listTools) listTools.hidden = true;
  document.getElementById('tab-settings')?.classList.remove('active');
  const grid = document.getElementById('postersGrid');
  grid.style.display = 'grid';
  grid.style.flexDirection = '';
  document.getElementById('catalogHeader').style.display = 'flex';
  document.getElementById('catalogTitle').textContent = name;
  grid.innerHTML = posterSkeletons();
  window.scrollTo({ top: 0, behavior: 'auto' });
}

async function openPerson(name) {
  name = String(name || '').trim();
  if (!name) return;
  const from = currentMedia && currentMedia.id ? { ...currentMedia } : null;
  notePerson(name);
  const view = personView = { name, from, data: null, filter: 'all', others: false };
  showPersonScreen(name);
  try {
    const data = await api(`/api/person?name=${encodeURIComponent(name)}`);
    if (personView !== view || !searchResultsShown) return;  // another page was opened meanwhile
    view.data = data;
    rememberFrenchTitles([...(data.movies || []), ...(data.series || []), ...(data.others || [])]);
    renderPersonPage();
  } catch (error) {
    if (personView !== view || !searchResultsShown) return;
    document.getElementById('postersGrid').innerHTML = error.code === 'offline'
      ? needsInternetHtml('La page de cette personne')
      : `<p class="person-note">Impossible d’afficher cette page : ${escapeHtml(error.message)}</p>`;
  }
}

// Seen like the cards say it: a film watched or marked, a series with episodes seen.
function personSeen(m) {
  const hist = (userHistory || []).find(h => h.id === m.id);
  if (!hist) return false;
  if (m.type === 'series') return Array.isArray(hist.watched_episodes) && hist.watched_episodes.length > 0;
  return Boolean(hist.completed || Number(hist.progress_pct || 0) >= 85 || hist.imported_watched);
}

// « Joue dans 17 films et 3 séries · Réalise 2 films ».
function personRolesText(data) {
  const plural = (n, word) => `${n} ${word}${n > 1 ? 's' : ''}`;
  return [['cast', 'Joue dans'], ['director', 'Réalise'], ['writer', 'Écrit']].map(([role, verb]) => {
    const films = (data.movies || []).filter(m => (m.roles || []).includes(role)).length;
    const series = (data.series || []).filter(m => (m.roles || []).includes(role)).length;
    const what = [films ? plural(films, 'film') : '', series ? plural(series, 'série') : ''].filter(Boolean).join(' et ');
    return what ? `${verb} ${what}` : '';
  }).filter(Boolean).join(' · ');
}

function personHeaderHtml(view) {
  const data = view.data;
  const seen = [...(data.movies || []), ...(data.series || [])].filter(personSeen).length;
  const facts = [personRolesText(data), seen ? `${seen} déjà vu${seen > 1 ? 's' : ''}` : ''].filter(Boolean).join(' · ');
  const photo = data.photo ? `<img src="${escapeHtml(data.photo)}" alt="">` : escapeHtml(personInitials(data.name || view.name));
  const back = view.from
    ? `<button type="button" class="btn btn-secondary" onclick="backFromPerson()">Retour à « ${escapeHtml(displayTitle(view.from))} »</button>` : '';
  return `<header class="person-head">
      <div class="person-photo${data.photo ? ' has-photo' : ''}" aria-hidden="true">${photo}</div>
      <div class="person-text">
        ${data.description ? `<p class="person-description">${escapeHtml(data.description)}</p>` : ''}
        ${data.bio ? `<p class="person-bio">${escapeHtml(data.bio)}</p>` : ''}
        ${facts ? `<p class="person-facts">${escapeHtml(facts)}</p>` : ''}
        ${back}
      </div>
    </header>`;
}

function renderPersonPage() {
  const view = personView;
  const grid = document.getElementById('postersGrid');
  if (!view?.data || !grid) return;
  const data = view.data;
  const groups = [['movie', 'Films', data.movies || []], ['series', 'Séries', data.series || []]].filter(([, , items]) => items.length);
  const all = groups.flatMap(([, , items]) => items);
  const unseen = all.filter(m => !personSeen(m));
  const filters = [['all', 'Tout', all.length], ...groups.map(([kind, label, items]) => [kind, label, items.length]),
    ...(unseen.length && unseen.length < all.length ? [['unseen', 'Pas encore vus', unseen.length]] : [])];
  if (!filters.some(([kind]) => kind === view.filter)) view.filter = 'all';
  const chips = filters.length > 2 ? `<div class="search-results-tools" role="group" aria-label="Filtrer les titres">${filters.map(([kind, label, count]) =>
    `<button type="button" class="chip${view.filter === kind ? ' active' : ''}" aria-pressed="${view.filter === kind}" onclick="setPersonFilter('${kind}')">${label} <span class="list-count">${count}</span></button>`).join('')}</div>` : '';
  const body = groups.map(([kind, label, items]) => {
    if (view.filter !== 'all' && view.filter !== 'unseen' && view.filter !== kind) return '';
    const shown = view.filter === 'unseen' ? items.filter(m => !personSeen(m)) : items;
    if (!shown.length) return '';
    return `<h3 class="search-group-title">${label} <span class="list-count">${shown.length}</span></h3>${shown.map(m => posterCardHtml(m, kind)).join('')}`;
  }).join('');
  const empty = all.length ? '' : `<div class="library-empty"><h3>Aucun titre confirmé</h3><p>Le catalogue ne relie encore aucun film ni série à ${escapeHtml(view.name)}.</p></div>`;
  const others = data.others || [];
  const otherHtml = !others.length ? '' : view.others
    ? `<h3 class="search-group-title">Autres résultats pour ce nom <span class="list-count">${others.length}</span></h3>`
      + `<p class="person-note">Le nom apparaît dans ces titres sans figurer dans les premiers noms de leur distribution : un petit rôle, ou un homonyme.</p>`
      + others.map(m => posterCardHtml(m, m.type)).join('')
      + `<button type="button" class="btn btn-secondary person-more" onclick="togglePersonOthers()" aria-expanded="true">Masquer les autres résultats</button>`
    : `<button type="button" class="btn btn-secondary person-more" onclick="togglePersonOthers()" aria-expanded="false">Autres résultats pour ce nom (${others.length})</button>`;
  grid.innerHTML = personHeaderHtml(view) + chips + body + empty + otherHtml;
}

function setPersonFilter(kind) {
  if (!personView) return;
  personView.filter = kind;
  renderPersonPage();
}

function togglePersonOthers() {
  if (!personView) return;
  personView.others = !personView.others;
  renderPersonPage();
}

function backFromPerson() {
  if (canGoBack()) {
    goBack();
    return;
  }
  const from = personView?.from;
  if (from) selectMedia(from);
}

// The trailer plays inside the fiche's hero instead of a separate window.
let detailTrailerCtx = null;
async function playDetailTrailer(trailerId, title, year = '', lang = 'vf') {
  const hero = document.getElementById('detailHero');
  const box = document.getElementById('detailTrailer');
  const frame = document.getElementById('detailTrailerFrame');
  if (!hero || !box || !frame) { openTrailerModal(trailerId, title, year, lang); return; }
  const ctx = {trailerId: trailerId || '', title: title || '', year: year || '', lang};
  detailTrailerCtx = ctx;
  hero.classList.add('playing-trailer');
  box.hidden = false;
  box.querySelectorAll('[data-trailer-lang]').forEach(button => button.classList.toggle('active', button.dataset.trailerLang === lang));
  frame.innerHTML = '<div class="detail-trailer-message">Recherche de la bande-annonce…</div>';
  const link = document.getElementById('detailTrailerLink');
  if (link) link.hidden = true;
  try {
    const qs = new URLSearchParams({title: ctx.title, year: ctx.year, yt_id: ctx.trailerId, lang});
    const data = await api(`/api/trailer?${qs.toString()}`);
    if (detailTrailerCtx !== ctx) return;
    if (link && data.watch_url) { link.href = data.watch_url; link.hidden = false; }
    if (data.stream_url) {
      frame.innerHTML = `<video src="${escapeHtml(data.stream_url)}" controls autoplay playsinline></video>`;
    } else if (data.embed_url) {
      frame.innerHTML = `<iframe src="${escapeHtml(data.embed_url)}" title="Bande-annonce" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>`;
    } else {
      frame.innerHTML = '<div class="detail-trailer-message">Aucune bande-annonce trouvée.</div>';
    }
  } catch (error) {
    if (detailTrailerCtx === ctx) frame.innerHTML = '<div class="detail-trailer-message">Bande-annonce indisponible pour le moment.</div>';
  }
}

function switchDetailTrailer(lang) {
  if (detailTrailerCtx) playDetailTrailer(detailTrailerCtx.trailerId, detailTrailerCtx.title, detailTrailerCtx.year, lang);
}

function closeDetailTrailer() {
  detailTrailerCtx = null;
  const frame = document.getElementById('detailTrailerFrame');
  if (frame) frame.innerHTML = '';
  const box = document.getElementById('detailTrailer');
  if (box) box.hidden = true;
  document.getElementById('detailHero')?.classList.remove('playing-trailer');
}

function applyMediaDetails(media, meta) {
  const detail = document.getElementById('detailPanel');
  const sc = document.getElementById('seriesControls');
  detailTrailerCtx = null;
  const rendered = buildDetailHtml(media, meta, false);
  detail.innerHTML = rendered.html;
  showDetailMore(rendered.moreHtml);
  loadDetailPeoplePhotos(media.id);

  if (rendered.isSeries) {
    media.type = 'series';
    currentMedia.type = 'series';
    const sSel = document.getElementById('seasonSelect');
    const seasons = [...new Set(seriesMetaVideos.map(v => v.season))].sort((a,b) => a - b);
    // The episode row of the fiche is the one season and episode chooser;
    // the menus above the sources stay for a series without its episode list.
    sc.style.display = seasons.length ? 'none' : 'flex';
    if (seasons.length) {
      sSel.innerHTML = seasons.map(s => `<option value="${s}">Saison ${s}</option>`).join('');
      sSel.value = String(rendered.targetSeason);
      onSeasonChange(false);
      const eSel = document.getElementById('episodeSelect');
      if (eSel && [...eSel.options].some(o => Number(o.value) === rendered.targetEpisode)) {
        eSel.value = String(rendered.targetEpisode);
      }
      renderDetailEpisodes(rendered.targetSeason);
    } else {
      sSel.innerHTML = '<option value="1">Saison 1</option>';
      onSeasonChange(false);
    }
    reloadSeriesEpisode(false);
  } else {
    media.type = 'movie';
    currentMedia.type = 'movie';
    sc.style.display = 'none';
    const year = meta.releaseInfo || meta.year || media.year || '';
    loadTorrents({
      imdb_id: media.id,
      type: 'movie',
      title: `${media.name}${year ? ' (' + year + ')' : ''}`,
      runtime: media.runtime || (meta && meta.runtime) || ''
    }, false);
  }
  refreshSpoilerSurfaces();
  loadRelatedForFiche(media, rendered.isSeries);
  if (media.is_anime || activeTab === 'anime') loadAnimeJourney(media, meta);
}

async function selectMedia(media) {
  noteFiche(media);
  animeJourneyRequestId++;
  animeJourneyState = null;
  animeJourneyMatches = new Map();
  animeJourneyRevealed = new Set();
  currentMedia = media;
  torrentRequestId++;
  lastReadyDebrid = null;
  allTorrents = [];
  document.getElementById('torrentsPanel').style.display = 'none';
  document.getElementById('debridResultPanel').style.display = 'none';
  hideDetailMore();
  const reqId = media.id;
  currentSelectMediaId = reqId;

  const setP = document.getElementById('settingsPanel');
  if (setP) setP.style.display = 'none';
  const setTab = document.getElementById('tab-settings');
  if (setTab) setTab.classList.remove('active');
  const detail = document.getElementById('detailPanel');
  detail.style.display = 'block';
  detail.style.removeProperty('--hero-glow');

  // 1. Verifier si les metadonnees completes sont deja en cache memoire client (0ms)
  const cachedMeta = META_CLIENT_CACHE.get(reqId);
  if (cachedMeta) {
    applyMediaDetails(media, cachedMeta);
    detail.scrollIntoView({behavior: 'smooth'});
    return;
  }

  // 2. Affichage instantane optimiste (0ms de latence percue)
  const preview = buildDetailHtml(media, {}, true);
  detail.innerHTML = preview.html;
  detail.scrollIntoView({behavior: 'smooth'});

  // 3. Requete asynchrone des metadonnees enrichies
  let meta = {};
  const queryType = (media.type === 'movie') ? 'movie' : (media.type === 'series' ? 'series' : (media.is_anime ? 'anime' : (media.type || 'movie')));
  try {
    const res = await api(`/api/meta?type=${encodeURIComponent(queryType)}&imdb_id=${encodeURIComponent(media.id)}`);
    meta = res.meta || {};
    if (meta.type && !media.type) media.type = meta.type;
    META_CLIENT_CACHE.set(reqId, meta);
  } catch (e) {
    console.warn(e);
  }

  // Si l'utilisateur a clique sur un autre media entre-temps, ignorer le retour
  if (currentSelectMediaId !== reqId) return;

  // 4. Injection fluide des donnees completes (synopsis traduit, episodes, similaires)
  applyMediaDetails(media, meta);
}

// Cast, similar titles and links: below the sources, shown and hidden with the fiche.
function showDetailMore(html) {
  const more = document.getElementById('detailMore');
  if (!more) return;
  more.innerHTML = html || '';
  more.hidden = !html;
}

function hideDetailMore() {
  showDetailMore('');
}

function closeDetailPanel() {
  closeDetailTrailer();
  hideDetailMore();
  animeJourneyRequestId++;
  animeJourneyState = null;
  const p = document.getElementById('detailPanel');
  if (p) p.style.display = 'none';
  const tor = document.getElementById('torrentsPanel');
  if (tor) tor.style.display = 'none';
  const deb = document.getElementById('debridResultPanel');
  if (deb) deb.style.display = 'none';
  currentMedia = null;
}

function selectDetailSeason(seasonNum) {
  document.querySelectorAll('[data-season-chip]').forEach(c => {
    c.classList.toggle('active', Number(c.getAttribute('data-season-chip')) === seasonNum);
  });
  const sSel = document.getElementById('seasonSelect');
  if (sSel) {
    sSel.value = String(seasonNum);
    onSeasonChange(false);
  }
  renderDetailEpisodes(seasonNum);
}

function renderDetailEpisodes(seasonNum) {
  const container = document.getElementById('detailEpisodesList');
  if (!container) return;
  detailEpisodeSeason = seasonNum;
  const eps = seriesMetaVideos.filter(v => v.season === seasonNum).sort((a,b) => (a.episode || a.number) - (b.episode || b.number));
  const summary = document.getElementById('detailSeasonSummary');
  if (!eps.length) {
    container.innerHTML = '<p class="ep-empty">Aucun détail d\'épisode disponible.</p>';
    if (summary) summary.textContent = '';
    updateEpisodeArrows();
    return;
  }
  const seriesId = spoilerSeriesId();
  const histItem = currentMedia ? userHistory.find(h => h.id === currentMedia.id) : null;
  const epPosMap = (histItem && histItem.ep_positions) ? histItem.ep_positions : {};
  const today = new Date().toISOString().slice(0, 10);

  const isAllSeasonWatched = eps.every(v => Spoilers.watched(userHistory, seriesId, seasonNum, v.episode || v.number || 1));
  for (const btnId of ['detailSeasonAllWatchedBtn', 'markSeasonWatchedBtn']) {
    const sBtn = document.getElementById(btnId);
    if (sBtn) {
      sBtn.textContent = isAllSeasonWatched ? `✓ Saison ${seasonNum} vue` : `✓ Marquer saison ${seasonNum} vue`;
      sBtn.style.borderColor = isAllSeasonWatched ? '#fafafa' : '';
      sBtn.style.color = isAllSeasonWatched ? '#fafafa' : '';
    }
  }

  const ctx = { seasonNum, seriesId, epPosMap, today, fr: EPISODE_OVERVIEWS_FR.get(`${seriesId}:${seasonNum}`) || {} };
  ctx.target = episodeToWatch(seasonNum, eps, histItem, today, seriesId);
  const watched = eps.filter(v => Spoilers.watched(userHistory, seriesId, seasonNum, v.episode || v.number || 1)).length;
  const upcoming = eps.filter(v => episodeIsUpcoming(v, today)).length;
  if (summary) {
    const parts = [`${eps.length} épisode${eps.length > 1 ? 's' : ''}`, `${watched} vu${watched > 1 ? 's' : ''}`];
    if (upcoming) parts.push(`${upcoming} à venir`);
    const filler = currentMedia?.is_anime || activeTab === 'anime' ? ' · Classement canon / hors-série indisponible sans source vérifiée.' : '';
    summary.textContent = `Saison ${seasonNum} · ${parts.join(' · ')}${filler}`;
  }
  container.innerHTML = eps.map(v => episodeTileHtml(v, ctx)).join('');
  revealEpisodeToWatch(container);
  updateEpisodeArrows();
  requestEpisodeOverviews(seriesId, seasonNum);
}

