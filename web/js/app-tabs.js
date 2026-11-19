// The Anime tab (rows, planning) lives in anime-home.js.

import { registerActions } from './actions.js';
import { EVENT, THIS, act, actOn, html, render, tHtml } from './html.js';
import {
  META_CLIENT_CACHE, activeGenre, activeTab, api, catalogHasMore, catalogItems, catalogSkip,
  catalogSort, checkConfig, getSeriesTargetEpisode, isInWatchlist, linkedAccounts,
  refreshUserLists, setActiveGenre, setActiveTab, setCatalogHasMore, setCatalogItems,
  setCatalogSkip, userHistory,
} from './app-core.js';
import {
  hideHomeRows, renderGenreChipsForTab, renderHeroSpotlight, renderHomeResume, selectGenre,
  setClassicsCatalogCache, updateClassicsStatsBadge,
} from './app-home.js';
import { hideWatchedInCatalog, loadMySeries, loadReleaseCalendar, renderActiveListTab } from './app-lists.js';
import { renderCustomListsTab, setOpenCustomListId } from './app-letterboxd.js';
import {
  cloudAsked, cloudGroups, loadBackups, loadSuggestionStatus, loadTvDevices, renderCloud,
  requestCloudMatches, setCloudGroups, triggerGdriveSync,
} from './app-cloud-settings.js';
import { checkTraktStatus, loadCommunityTab } from './app-social.js';
import {
  displayTitle, frenchGenre, frenchRuntime, hideDetailMore, personView, titleAttrs,
} from './app-fiche.js';
import { inAppAbsoluteTime, inAppCurrentMedia, inAppCurrentTitle, inAppDuration } from './app-player.js';
import { ratingBadgeHtml } from './ratings.js';
import {
  animeWeekTimer, applyAnimeSubTab, currentAnimeSubTab, setAnimeWeekTimer, switchAnimeSubTab,
} from './anime-home.js';
import { loadSourcesSettings } from './p2p-addons.js';
import { renderMangaTab } from './manga.js';
import { networkOnline, noteTab, pollDownloads, showOfflineCatalogue } from './app-start.js';
import { loadStats } from './stats.js';

// --- Discord Rich Presence (RPC) ---
let lastDiscordRpcSync = 0;
export function updateDiscordRpc(isPaused = false) {
  const video = document.getElementById('inAppVideo');
  if (!video || !inAppCurrentMedia) return;
  const now = Date.now();
  if (!isPaused && now - lastDiscordRpcSync < 4000) return;
  lastDiscordRpcSync = now;

  const curT = inAppAbsoluteTime(video);
  const durT = inAppDuration(video);
  const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(/** @type {HTMLSelectElement} */ (document.getElementById('seasonSelect')).value) : null);
  const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(/** @type {HTMLSelectElement} */ (document.getElementById('episodeSelect')).value) : null);

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

export function clearDiscordRpc() {
  api('/api/discord-rpc/clear', { method: 'POST' }).catch(() => {});
}

let lastActiveLibraryTab = 'watchlist';
export let previousTab = 'movies';
export function setPreviousTab(value) { previousTab = value; return value; }
export let refreshSettingsSectionNavigation = () => {};
export function setRefreshSettingsSectionNavigation(value) { refreshSettingsSectionNavigation = value; return value; }

// True while search results replace the tab's own content.
export let searchResultsShown = false;
export function setSearchResultsShown(value) { searchResultsShown = value; return value; }

export async function switchTab(tab) {
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
  setActiveTab(tab);
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
  const mangaP = document.getElementById('mangaPanel');
  if (mangaP) mangaP.style.display = 'none';
  // The pages of the Anime tab leave with it; switchAnimeSubTab brings the right one back.
  for (const id of ['animeSchedulePanel', 'animeHomePanel']) {
    const animePage = document.getElementById(id);
    if (animePage) animePage.style.display = 'none';
  }
  if (tab !== 'anime') { clearInterval(animeWeekTimer); setAnimeWeekTimer(null); }
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
    loadSourcesSettings();
    requestAnimationFrame(refreshSettingsSectionNavigation);
    return;
  }

  await refreshUserLists();
  const listFiltersBox = document.getElementById('listFilters');
  if (listFiltersBox) listFiltersBox.hidden = !(tab === 'watchlist' || tab === 'watched');
  if (tab === 'customlists') setOpenCustomListId(null);

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
    setActiveGenre('');
    selectGenre('', document.querySelector('#genreFilters .chip'));
    await switchAnimeSubTab(currentAnimeSubTab);
  } else if (tab === 'watchlist') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'flex';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (surpriseLbl) surpriseLbl.textContent = KINO_I18N.t('page.tirage_sur_ma_liste');
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogTitle').textContent = KINO_I18N.t('page.ma_liste_2');
    renderActiveListTab();
  } else if (tab === 'watched') {
    if (gf) gf.style.display = 'none';
    if (sw) sw.style.display = 'none';
    if (wlw) wlw.style.display = 'flex';
    if (lm) lm.style.display = 'none';
    if (hs) hs.style.display = 'none';
    if (surpriseLbl) surpriseLbl.textContent = KINO_I18N.t('tabs.tirage_rewatch');
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('catalogTitle').textContent = KINO_I18N.t('page.deja_vus');
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
    document.getElementById('catalogTitle').textContent = KINO_I18N.t('page.mes_series');
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
    document.getElementById('catalogTitle').textContent = KINO_I18N.t('page.calendrier');
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
    document.getElementById('catalogTitle').textContent = KINO_I18N.t('page.hors_ligne');
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
  } else if (tab === 'manga') {
    for (const el of [gf, sw, wlw, lm, hs, statsEl]) if (el) el.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('postersGrid').style.display = 'none';
    document.getElementById('catalogHeader').style.display = 'none';
    if (mangaP) mangaP.style.display = 'block';
    await renderMangaTab();
  }
}

export async function loadCatalog(type, genre = '', reset = true) {
  const grid = document.getElementById('postersGrid');
  const lm = document.getElementById('loadMoreWrap');
  if (reset) {
    setCatalogSkip(0);
    setCatalogItems([]);
    setCatalogHasMore(false);
    if (!networkOnline) {
      showOfflineCatalogue();
      return;
    }
    render(grid, posterSkeletons());
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
      setClassicsCatalogCache([...catalogItems]);
    }
    if (reset) {
      renderHeroSpotlight(catalogItems, type === 'classics' ? 'movie' : type);
    }
    if (type === 'classics') {
      updateClassicsStatsBadge();
    }
    renderPosterCards(catalogItems, type === 'classics' ? 'movie' : type);
    setCatalogHasMore(type !== 'classics' && incoming.length >= 20);
    if (lm) lm.style.display = catalogHasMore ? 'flex' : 'none';
    // The banner and « Voir plus » belong to one page of the Anime tab each.
    if (activeTab === 'anime' && !searchResultsShown && !personView) applyAnimeSubTab();
  } catch (e) {
    if (reset && (e.code === 'offline' || !networkOnline)) {
      showOfflineCatalogue();
    } else if (reset) {
      render(grid, html`<p style="color:var(--muted); font-size:0.84rem;">${tHtml('settings.erreur_x', e.message)}</p>`);
    }
  }
}

async function loadMoreCatalog() {
  const btn = /** @type {HTMLButtonElement} */ (document.getElementById('loadMoreBtn'));
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = KINO_I18N.t('page.chargement'); }
  setCatalogSkip(catalogSkip + (50));
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
export function posterSkeletons(count = 12) {
  return [html`<div class="poster-skeleton-status" role="status">${KINO_I18N.t('anime.chargement')}</div>`, Array.from({length: count}, () => html`
    <div class="poster-card is-skeleton" aria-hidden="true">
      <div class="poster-art"></div>
      <div class="poster-info"><span class="skeleton-line" style="width:82%"></span><span class="skeleton-line" style="width:44%"></span></div>
    </div>`)];
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
  /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('.poster-preview-desc[data-preview-id]')).forEach(el => {
    if (el.dataset.previewId !== id) return;
    el.textContent = text;
    el.dataset.loaded = 'done';
  });
}

export function initPosterPreviews() {
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
export function posterCardHtml(m, fallbackType) {
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
      ? html`<button type="button" class="poster-watched" ${act('toggleCardWatched', EVENT, mediaObj)} title="${KINO_I18N.t('tabs.cliquer_pour_retirer_des_deja_vus')}">${KINO_I18N.t('anime.vu')}</button>`
      : (watchedEpsCount > 0 ? html`<span class="poster-watched is-static">${tHtml('tabs.x_ep', watchedEpsCount)}</span>` : '');
    let btnLabel = KINO_I18N.t('page.lancer');
    if (histItem && mtype === 'series' && histItem.season && histItem.episode) {
      const targetEp = getSeriesTargetEpisode(histItem);
      const code = `${KINO_I18N.t('settings.s_x_e_x', String(targetEp.season).padStart(2,'0'), String(targetEp.episode).padStart(2,'0'))}`;
      btnLabel = targetEp.finished ? KINO_I18N.t('tabs.revoir') : (targetEp.isNext ? `${KINO_I18N.t('tabs.suivant_x', code)}` : `${KINO_I18N.t('settings.lancer_x', code)}`);
    } else if (histItem && histItem.progress_pct > 1 && histItem.progress_pct < 85 && !isMovieWatched) {
      btnLabel = `${KINO_I18N.t('fiche.reprendre_x', Math.round(histItem.progress_pct))}`;
    } else if (isMovieWatched) {
      btnLabel = KINO_I18N.t('tabs.revoir');
    }
    const cardProg = (histItem && histItem.progress_pct > 0 && !isMovieWatched)
      ? html`<div class="poster-progress" aria-label="${tHtml('tabs.x_vus', Math.round(histItem.progress_pct))}"><span style="width:${Math.min(100, Number(histItem.progress_pct))}%"></span></div>` : '';
    // Hover preview: genres, length and rating now; the French synopsis on demand.
    const previewGenres = (Array.isArray(m.genres) ? m.genres : []).slice(0, 2).map(frenchGenre);
    const previewMeta = [...previewGenres, m.runtime ? frenchRuntime(m.runtime) : '', ratingVal ? `★ ${ratingVal}` : '']
      .filter(Boolean).join(' · ');
    const previewHtml = html`
      <div class="poster-preview">
        ${previewMeta ? html`<div class="poster-preview-meta">${previewMeta}</div>` : ''}
        <p class="poster-preview-desc" data-preview-id="${m.id}" data-preview-type="${mtype}"></p>
        <div class="poster-preview-actions">
          <button type="button" class="poster-preview-play" ${act('oneClickCard', EVENT, THIS, mediaObj)}>▶ ${btnLabel}</button>
          ${mtype !== 'series' ? html`<button type="button" ${act('toggleCardWatched', EVENT, mediaObj)}>${isMovieWatched ? KINO_I18N.t('anime.vu') : KINO_I18N.t('tabs.marquer_vu')}</button>` : ''}
        </div>
      </div>`;
    const ratingBadges = [];
    if (userRating) {
      ratingBadges.push(ratingBadgeHtml(userRating));
    }
    if (ratingVal) {
      ratingBadges.push(`★ ${ratingVal}`);
    }
    // Markup pieces, kept apart: joined into a string they would show as text.
    const ratingLine = ratingBadges.length ? html` • ${ratingBadges.flatMap((badge, i) => i ? [' ', badge] : [badge])}` : '';
    return html`
      <div class="poster-card" ${act('selectMedia', mediaObj)}>
        ${watchedPill}
        <button class="wl-btn ${inList ? 'in-list' : ''}" data-wl-id="${m.id}" title="${inList ? 'Retirer de Ma Liste' : 'Ajouter à Ma Liste'}" ${act('toggleWatchlist', EVENT, mediaObj)}>${inList ? '✓' : '+'}</button>
        <div class="poster-art">
          <img src="${m.poster || ''}" alt="${displayTitle(m)}" loading="lazy" ${actOn('load', 'kinoLoaded', THIS)} ${actOn('error', 'kinoPosterFailed', THIS, m.id)}>
          ${cardProg}
          ${previewHtml}
        </div>
        <div class="poster-info">
          <div class="poster-title"${titleAttrs(m)}>${displayTitle(m)}</div>
          <div class="poster-year">${year}${ratingLine}${(activeTab === 'anime' || m.is_anime) ? html` • <span class="poster-vostfr">VOSTFR</span>` : ''}</div>
        </div>
      </div>
    `;
}

export function renderPosterCards(metas, fallbackType) {
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
      render(grid, html`<div class="library-empty"><span class="library-empty-icon" aria-hidden="true">＋</span><h3>${KINO_I18N.t('tabs.votre_liste_commence_ici')}</h3><p>${KINO_I18N.t('tabs.ajoutez_un_film_ou_une_serie_depuis_sa')}</p><button class="btn" ${act('switchTab', 'movies')}>${KINO_I18N.t('home.decouvrir_les_films')}</button></div>`);
    } else if (activeTab === 'watched') {
      render(grid, html`<div class="library-empty"><span class="library-empty-icon" aria-hidden="true">✓</span><h3>${KINO_I18N.t('tabs.aucun_titre_termine')}</h3><p>${KINO_I18N.t('tabs.les_films_et_episodes_que_vous_avez_vus')}</p><button class="btn" ${act('switchTab', 'movies')}>${KINO_I18N.t('home.decouvrir_les_films')}</button></div>`);
    } else {
      render(grid, html`<div class="library-empty"><h3>${KINO_I18N.t('tabs.aucun_resultat')}</h3><p>${KINO_I18N.t('tabs.essayez_un_autre_filtre_ou_une_autre')}</p></div>`);
    }
    return;
  }
  render(grid, displayList.map(m => posterCardHtml(m, fallbackType)));
}

export async function loadRdCloud() {
  const list = document.getElementById('rdCloudList');
  const pName = linkedAccounts.debridName || 'votre débrideur';
  if (!cloudGroups.length) render(list, html`<p class="cloud-empty">${tHtml('tabs.recuperation_de_votre_cloud_x', pName)}</p>`);
  try {
    const data = await api('/api/rd-history');
    setCloudGroups(Array.isArray(data.groups) ? data.groups : []);
    cloudAsked.clear();
    const noteEl = document.getElementById('rdCloudStatusNote');
    if (noteEl && data.cleaned && (data.cleaned.deleted_downloads > 0 || data.cleaned.deleted_torrents > 0)) {
      noteEl.textContent = `${KINO_I18N.t('tabs.nettoyage_auto_effectue_x_fichier_s_et_x', data.cleaned.deleted_downloads, data.cleaned.deleted_torrents, pName)}`;
    }
    renderCloud();
    requestCloudMatches();
  } catch (e) {
    render(list, html`<p class="cloud-empty">${tHtml('settings.erreur_x', e.message)}</p>`);
  }
}

// A poster that does not load: the card shows the title instead.
function kinoPosterFailed(element, id) {
  element.classList.add('is-loaded');
  handlePosterCardError(element, id);
}

// What the markup may run from this file (web/js/actions.js).
registerActions({
  kinoPosterFailed, loadMoreCatalog, loadRdCloud, switchTab,
});
