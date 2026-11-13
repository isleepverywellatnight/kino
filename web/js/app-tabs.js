// The Anime tab (rows, planning) lives in anime-home.js.

// --- Discord Rich Presence (RPC) ---
let lastDiscordRpcSync = 0;
function updateDiscordRpc(isPaused = false) {
  const video = document.getElementById('inAppVideo');
  if (!video || !inAppCurrentMedia) return;
  const now = Date.now();
  if (!isPaused && now - lastDiscordRpcSync < 4000) return;
  lastDiscordRpcSync = now;

  const curT = inAppAbsoluteTime(video);
  const durT = inAppDuration(video);
  const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : null);
  const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : null);

  api('/api/discord-rpc/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: inAppCurrentMedia.name || inAppCurrentTitle || 'KINO',
      media_type: inAppCurrentMedia.type || 'movie',
      season: s,
      episode: e,
      current_time: curT,
      duration: durT,
      poster: inAppCurrentMedia.poster || '',
      year: String(inAppCurrentMedia.year || ''),
      imdb_id: inAppCurrentMedia.id || '',
      is_paused: isPaused
    })
  }).catch(() => {});
}

function clearDiscordRpc() {
  api('/api/discord-rpc/clear', { method: 'POST' }).catch(() => {});
}

let lastActiveLibraryTab = 'watchlist';
let previousTab = 'movies';
let refreshSettingsSectionNavigation = () => {};

// True while search results replace the tab's own content.
let searchResultsShown = false;

async function switchTab(tab) {
  searchResultsShown = false;
  if (activeTab && activeTab !== 'settings') {
    previousTab = activeTab;
  }
  const isLibraryTab = ['watchlist', 'history', 'myseries', 'calendar', 'watched', 'offline', 'customlists', 'rdcloud', 'stats', 'library'].includes(tab);
  if (isLibraryTab) {
    if (tab === 'library') {
      tab = lastActiveLibraryTab || 'watchlist';
    } else {
      lastActiveLibraryTab = tab;
    }
  }

  noteTab(tab);
  const tabChanged = activeTab !== tab;
  activeTab = tab;
  document.body.dataset.activeTab = tab;
  if (tabChanged) window.scrollTo({ top: 0, behavior: 'auto' });

  // Active state for primary tabs
  document.querySelectorAll('.nav-tabs .nav-tab').forEach(t => {
    t.classList.remove('active');
    t.removeAttribute('aria-current');
  });
  if (isLibraryTab) {
    const libEl = document.getElementById('tab-library');
    if (libEl) libEl.classList.add('active');
  } else if (tab === 'classics') {
    const movEl = document.getElementById('tab-movies');
    if (movEl) movEl.classList.add('active');
  } else {
    const activeEl = document.getElementById('tab-' + tab);
    if (activeEl) activeEl.classList.add('active');
  }
  const activeNav = document.querySelector('.nav-tabs .nav-tab.active');
  if (activeNav) activeNav.setAttribute('aria-current', 'page');

  // Subnav visibility & active state
  const libSubNav = document.getElementById('librarySubNav');
  if (libSubNav) {
    libSubNav.style.display = isLibraryTab ? 'flex' : 'none';
  }
  if (isLibraryTab) {
    document.querySelectorAll('.lib-subtab').forEach(t => {
      t.classList.remove('active');
      t.removeAttribute('aria-current');
    });
    const activeSub = document.getElementById('subtab-' + tab);
    if (activeSub) {
      activeSub.classList.add('active');
      activeSub.setAttribute('aria-current', 'page');
      // On a narrow window the line of tabs scrolls: keep the active one in view.
      if (typeof activeSub.scrollIntoView === 'function') activeSub.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  }

  document.getElementById('detailPanel').style.display = 'none';
  document.getElementById('torrentsPanel').style.display = 'none';
  hideDetailMore();
  // The home rows belong to the Films and Séries tabs only.
  if (tab === 'movies' || tab === 'series') renderHomeResume();
  else hideHomeRows();
  document.getElementById('rdCloudPanel').style.display = 'none';
  document.getElementById('viewingStatsPanel').style.display = 'none';
  const comP = document.getElementById('communityPanel');
  if (comP) comP.style.display = 'none';
  // The pages of the Anime tab leave with it; switchAnimeSubTab brings the right one back.
  for (const id of ['animeSchedulePanel', 'animeHomePanel']) {
    const animePage = document.getElementById(id);
    if (animePage) animePage.style.display = 'none';
  }
  if (tab !== 'anime') { clearInterval(animeWeekTimer); animeWeekTimer = null; }
  const asNav = document.getElementById('animeSubNav');
  if (asNav) asNav.style.display = (tab === 'anime') ? 'flex' : 'none';
  const setP = document.getElementById('settingsPanel');
  if (setP) setP.style.display = (tab === 'settings') ? 'block' : 'none';

  if (tab !== 'settings') {
    document.getElementById('postersGrid').style.display = 'grid';
    document.getElementById('postersGrid').style.flexDirection = '';
    document.getElementById('catalogHeader').style.display = 'flex';
  } else {
    document.getElementById('postersGrid').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'none';
  }

  const gf = document.getElementById('genreFilters');
  const sw = document.getElementById('catalogSortWrap');
  const wlw = document.getElementById('watchlistActionsWrap');
  const lm = document.getElementById('loadMoreWrap');
  const hs = document.getElementById('heroSpotlight');
  const statsEl = document.getElementById('listStatsBadge');
  const surpriseLbl = document.getElementById('listSurpriseBtnLabel');

  if (tab === 'settings') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    triggerGdriveSync();
    checkTraktStatus();
    checkConfig();
    loadTvDevices();
    loadSuggestionStatus();
    loadBackups();
    requestAnimationFrame(refreshSettingsSectionNavigation);
    return;
  }

  await refreshUserLists();
  const listFiltersBox = document.getElementById('listFilters');
  if (listFiltersBox) listFiltersBox.hidden = !(tab === 'watchlist' || tab === 'watched');
  if (tab === 'customlists') openCustomListId = null;

  if (tab === 'movies') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    renderGenreChipsForTab('movies');
    selectGenre(activeGenre, document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`) || document.querySelector('#genreFilters .chip'));
  } else if (tab === 'series') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    renderGenreChipsForTab('series');
    selectGenre(activeGenre, document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`) || document.querySelector('#genreFilters .chip'));
  } else if (tab === 'classics') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    renderGenreChipsForTab('classics');
    selectGenre(activeGenre, document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`) || document.querySelector('#genreFilters .chip'));
  } else if (tab === 'anime') {
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    // The catalogue loads behind every page of the tab: its first titles are
    // the banner of « À la une ».
    renderGenreChipsForTab('anime');
    activeGenre = '';
    selectGenre('', document.querySelector('#genreFilters .chip'));
    await switchAnimeSubTab(currentAnimeSubTab);
  } else if (tab === 'watchlist') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'flex';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (surpriseLbl) surpriseLbl.textContent = 'Tirage sur Ma Liste';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogTitle').textContent = 'Ma Liste';
    renderActiveListTab();
  } else if (tab === 'watched') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'flex';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (surpriseLbl) surpriseLbl.textContent = 'Tirage Rewatch';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogTitle').textContent = 'Déjà vus';
    renderActiveListTab();
  } else if (tab === 'customlists') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'flex';
    document.getElementById('postersGrid').style.display = 'flex';
    renderCustomListsTab();
  } else if (tab === 'history') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('postersGrid').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'none';
    renderHomeResume();
  } else if (tab === 'myseries') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogTitle').textContent = 'Mes séries';
    document.getElementById('postersGrid').style.display = 'flex';
    document.getElementById('postersGrid').style.flexDirection = 'column';
    await loadMySeries();
  } else if (tab === 'calendar') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogTitle').textContent = 'Calendrier';
    document.getElementById('postersGrid').style.display = 'flex';
    document.getElementById('postersGrid').style.flexDirection = 'column';
    await loadReleaseCalendar();
  } else if (tab === 'offline') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogTitle').textContent = 'Hors ligne';
    document.getElementById('postersGrid').style.display = 'flex';
    document.getElementById('postersGrid').style.flexDirection = 'column';
    await pollDownloads();
  } else if (tab === 'rdcloud') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('postersGrid').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'none';
    document.getElementById('rdCloudPanel').style.display = 'block';
    await loadRdCloud();
  } else if (tab === 'stats') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('postersGrid').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'none';
    document.getElementById('viewingStatsPanel').style.display = 'block';
    await loadStats();
  } else if (tab === 'community') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'none';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('postersGrid').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'none';
    if (comP) comP.style.display = 'block';
    await loadCommunityTab();
  }
}

async function loadCatalog(type, genre = '', reset = true) {
  const grid = document.getElementById('postersGrid');
  const lm = document.getElementById('loadMoreWrap');
  if (reset) {
    catalogSkip = 0;
    catalogItems = [];
    catalogHasMore = false;
    if (!networkOnline) {
      showOfflineCatalogue();
      return;
    }
    grid.innerHTML = posterSkeletons();
    if (lm) lm.style.display = 'none';
  }
  try {
    const data = await api(`/api/catalog?type=${encodeURIComponent(type)}&genre=${encodeURIComponent(genre || '')}&sort=${encodeURIComponent(catalogSort)}&skip=${catalogSkip}`);
    const incoming = data.metas || [];
    const seenIds = new Set(catalogItems.map(x => x.id));
    for (const m of incoming) {
      if (m && m.id && !seenIds.has(m.id)) {
        seenIds.add(m.id);
        catalogItems.push(m);
      }
    }
    if (type === 'classics' && !genre) {
      classicsCatalogCache = [...catalogItems];
    }
    if (reset) {
      renderHeroSpotlight(catalogItems, type === 'classics' ? 'movie' : type);
    }
    if (type === 'classics') {
      updateClassicsStatsBadge();
    }
    renderPosterCards(catalogItems, type === 'classics' ? 'movie' : type);
    catalogHasMore = type !== 'classics' && incoming.length >= 20;
    if (lm) lm.style.display = catalogHasMore ? 'flex' : 'none';
    // The banner and « Voir plus » belong to one page of the Anime tab each.
    if (activeTab === 'anime' && !searchResultsShown && !personView) applyAnimeSubTab();
  } catch (e) {
    if (reset && (e.code === 'offline' || !networkOnline)) {
      showOfflineCatalogue();
    } else if (reset) {
      grid.innerHTML = `<p style="color:var(--muted); font-size:0.84rem;">Erreur : ${escapeHtml(e.message)}</p>`;
    }
  }
}

async function loadMoreCatalog() {
  const btn = document.getElementById('loadMoreBtn');
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Chargement...'; }
  catalogSkip += 50;
  const type = activeTab === 'series' ? 'series' : (activeTab === 'classics' ? 'classics' : (activeTab === 'anime' ? 'anime' : 'movie'));
  try {
    await loadCatalog(type, activeGenre, false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig || 'Voir plus de titres'; }
  }
}

function handlePosterCardError(img, id) {
  if (!img) return;
  if (!img.dataset.tried) {
    img.dataset.tried = '1';
    if (img.src && img.src.includes('/poster/medium/')) {
      img.src = img.src.replace('/poster/medium/', '/poster/small/');
      return;
    }
  }
  img.style.display = 'none';
  if (img.parentElement) {
    img.parentElement.style.background = 'linear-gradient(145deg, #18181b, #09090b)';
  }
}

// Grey cards while a grid loads: the page keeps its shape instead of jumping.
function posterSkeletons(count = 12) {
  return `<div class="poster-skeleton-status" role="status">Chargement…</div>` + Array.from({length: count}, () => `
    <div class="poster-card is-skeleton" aria-hidden="true">
      <div class="poster-art"></div>
      <div class="poster-info"><span class="skeleton-line" style="width:82%"></span><span class="skeleton-line" style="width:44%"></span></div>
    </div>`).join('');
}

// The French synopsis of a hovered (or focused) card is fetched after a short
// pause, once; the fiche then opens from the same cache, without waiting.
const POSTER_PREVIEW_DELAY = 350;
let posterPreviewTimer = null;

function schedulePosterPreview(card) {
  clearTimeout(posterPreviewTimer);
  const desc = card?.querySelector?.('.poster-preview-desc[data-preview-id]');
  if (!desc || desc.dataset.loaded) return;
  posterPreviewTimer = setTimeout(() => loadPosterPreview(desc), POSTER_PREVIEW_DELAY);
}

async function loadPosterPreview(desc) {
  const id = desc.dataset.previewId;
  desc.dataset.loaded = 'pending';
  let meta = META_CLIENT_CACHE.get(id);
  if (!meta) {
    try {
      meta = (await api(`/api/meta?type=${encodeURIComponent(desc.dataset.previewType || 'movie')}&imdb_id=${encodeURIComponent(id)}`)).meta || {};
      META_CLIENT_CACHE.set(id, meta);
    } catch (error) {
      delete desc.dataset.loaded;
      return;
    }
  }
  const text = meta.description_fr || meta.description || '';
  document.querySelectorAll('.poster-preview-desc[data-preview-id]').forEach(el => {
    if (el.dataset.previewId !== id) return;
    el.textContent = text;
    el.dataset.loaded = 'done';
  });
}

function initPosterPreviews() {
  const grid = document.getElementById('postersGrid');
  if (!grid) return;
  const cardOf = event => event.target?.closest?.('.poster-card');
  grid.addEventListener('mouseover', event => { const card = cardOf(event); if (card) schedulePosterPreview(card); });
  grid.addEventListener('focusin', event => { const card = cardOf(event); if (card) schedulePosterPreview(card); });
  grid.addEventListener('mouseout', event => {
    const card = cardOf(event);
    if (card && !card.contains(event.relatedTarget)) clearTimeout(posterPreviewTimer);
  });
}

// One catalogue card: the poster, then its title and year. On hover or keyboard
// focus the preview brings the synopsis and the actions (play, Vu); there is no
// play button repeated under every poster.
function posterCardHtml(m, fallbackType) {
    const isAnime = Boolean(activeTab === 'anime' || m.is_anime);
    const mtype = (m.type === 'movie' || (!m.type && isAnime && m.is_movie)) ? 'movie' : (isAnime ? 'series' : (m.type || fallbackType || 'movie'));
    const year = m.releaseInfo || m.year || '';
    const histItem = userHistory.find(h => h.id === m.id);
    const ratingVal = m.imdbRating || (histItem && histItem.imdbRating) || '';
    const userRating = (histItem && histItem.user_rating) || m.user_rating || '';
    const mediaObj = {
      id: m.id,
      name: m.name,
      type: mtype,
      is_anime: isAnime,
      year: String(year),
      poster: m.poster || '',
      imdbRating: String(ratingVal),
      user_rating: String(userRating || '')
    };
    const payload = JSON.stringify(mediaObj).replace(/'/g, "&#39;");
    const inList = isInWatchlist(m.id);
    const isMovieWatched = Boolean(histItem && mtype !== 'series' && (histItem.completed || Number(histItem.progress_pct || 0) >= 85));
    const watchedEpsCount = (histItem && mtype === 'series' && Array.isArray(histItem.watched_episodes)) ? histItem.watched_episodes.length : 0;
    const watchedPill = isMovieWatched
      ? `<button type="button" class="poster-watched" onclick='toggleCardWatched(event, ${payload})' title="Cliquer pour retirer des Déjà vus">✓ Vu</button>`
      : (watchedEpsCount > 0 ? `<span class="poster-watched is-static">✓ ${watchedEpsCount} ép.</span>` : '');
    let btnLabel = 'Lancer';
    if (histItem && mtype === 'series' && histItem.season && histItem.episode) {
      const targetEp = getSeriesTargetEpisode(histItem);
      const code = `S${String(targetEp.season).padStart(2,'0')}E${String(targetEp.episode).padStart(2,'0')}`;
      btnLabel = targetEp.finished ? 'Revoir' : (targetEp.isNext ? `Suivant ${code}` : `Lancer ${code}`);
    } else if (histItem && histItem.progress_pct > 1 && histItem.progress_pct < 85 && !isMovieWatched) {
      btnLabel = `Reprendre (${Math.round(histItem.progress_pct)}%)`;
    } else if (isMovieWatched) {
      btnLabel = 'Revoir';
    }
    const cardProg = (histItem && histItem.progress_pct > 0 && !isMovieWatched)
      ? `<div class="poster-progress" aria-label="${Math.round(histItem.progress_pct)} % vus"><span style="width:${Math.min(100, Number(histItem.progress_pct))}%"></span></div>` : '';
    // Hover preview: genres, length and rating now; the French synopsis on demand.
    const previewGenres = (Array.isArray(m.genres) ? m.genres : []).slice(0, 2).map(frenchGenre);
    const previewMeta = [...previewGenres, m.runtime ? frenchRuntime(m.runtime) : '', ratingVal ? `★ ${ratingVal}` : '']
      .filter(Boolean).join(' · ');
    const previewHtml = `
      <div class="poster-preview">
        ${previewMeta ? `<div class="poster-preview-meta">${escapeHtml(previewMeta)}</div>` : ''}
        <p class="poster-preview-desc" data-preview-id="${escapeHtml(m.id)}" data-preview-type="${mtype}"></p>
        <div class="poster-preview-actions">
          <button type="button" class="poster-preview-play" onclick='oneClickCard(event, this, ${payload})'>▶ ${btnLabel}</button>
          ${mtype !== 'series' ? `<button type="button" onclick='toggleCardWatched(event, ${payload})'>${isMovieWatched ? '✓ Vu' : 'Marquer vu'}</button>` : ''}
        </div>
      </div>`;
    const ratingBadges = [];
    if (userRating) {
      ratingBadges.push(ratingBadgeHtml(userRating));
    }
    if (ratingVal) {
      ratingBadges.push(`★ ${ratingVal}`);
    }
    const ratingLine = ratingBadges.length ? ' • ' + ratingBadges.join(' ') : '';
    return `
      <div class="poster-card" onclick='selectMedia(${payload})'>
        ${watchedPill}
        <button class="wl-btn ${inList ? 'in-list' : ''}" data-wl-id="${escapeHtml(m.id)}" title="${inList ? 'Retirer de Ma Liste' : 'Ajouter à Ma Liste'}" onclick='toggleWatchlist(event, ${payload})'>${inList ? '✓' : '+'}</button>
        <div class="poster-art">
          <img src="${escapeHtml(m.poster || '')}" alt="${escapeHtml(displayTitle(m))}" loading="lazy" onload="this.classList.add('is-loaded')" onerror="this.classList.add('is-loaded'); handlePosterCardError(this, '${escapeHtml(escapeJsString(m.id))}')">
          ${cardProg}
          ${previewHtml}
        </div>
        <div class="poster-info">
          <div class="poster-title"${titleAttrs(m)}>${escapeHtml(displayTitle(m))}</div>
          <div class="poster-year">${escapeHtml(year)}${ratingLine}${(activeTab === 'anime' || m.is_anime) ? ' • <span class="poster-vostfr">VOSTFR</span>' : ''}</div>
        </div>
      </div>
    `;
}

function renderPosterCards(metas, fallbackType) {
  const grid = document.getElementById('postersGrid');
  let displayList = metas || [];
  if (hideWatchedInCatalog && (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics' || activeTab === 'anime')) {
    displayList = displayList.filter(m => {
      const h = userHistory.find(x => x.id === m.id);
      return !(h && (h.completed || Number(h.progress_pct || 0) >= 85));
    });
  }
  if (!displayList.length) {
    if (activeTab === 'watchlist') {
      grid.innerHTML = '<div class="library-empty"><span class="library-empty-icon" aria-hidden="true">＋</span><h3>Votre liste commence ici</h3><p>Ajoutez un film ou une série depuis sa fiche pour le retrouver facilement.</p><button class="btn" onclick="switchTab(\'movies\')">Découvrir les films</button></div>';
    } else if (activeTab === 'watched') {
      grid.innerHTML = '<div class="library-empty"><span class="library-empty-icon" aria-hidden="true">✓</span><h3>Aucun titre terminé</h3><p>Les films et épisodes que vous avez vus apparaîtront ici.</p><button class="btn" onclick="switchTab(\'movies\')">Découvrir les films</button></div>';
    } else {
      grid.innerHTML = '<div class="library-empty"><h3>Aucun résultat</h3><p>Essayez un autre filtre ou une autre recherche.</p></div>';
    }
    return;
  }
  grid.innerHTML = displayList.map(m => posterCardHtml(m, fallbackType)).join('');
}

async function loadRdCloud() {
  const list = document.getElementById('rdCloudList');
  const pName = window.activeDebridName || 'votre débrideur';
  if (!cloudGroups.length) list.innerHTML = `<p class="cloud-empty">Récupération de votre Cloud ${escapeHtml(pName)}…</p>`;
  try {
    const data = await api('/api/rd-history');
    cloudGroups = Array.isArray(data.groups) ? data.groups : [];
    cloudAsked.clear();
    const noteEl = document.getElementById('rdCloudStatusNote');
    if (noteEl && data.cleaned && (data.cleaned.deleted_downloads > 0 || data.cleaned.deleted_torrents > 0)) {
      noteEl.textContent = `Nettoyage auto effectué : ${data.cleaned.deleted_downloads} fichier(s) et ${data.cleaned.deleted_torrents} torrent(s) expirés supprimés de ${pName}.`;
    }
    renderCloud();
    requestCloudMatches();
  } catch (e) {
    list.innerHTML = `<p class="cloud-empty">Erreur : ${escapeHtml(e.message)}</p>`;
  }
}

