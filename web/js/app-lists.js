/* ============================================================================
 * ROULETTE HORIZONTALE KINO (SÉLECTION ALÉATOIRE)
 * ============================================================================ */
let caseAudioCtx = null;
let caseSoundEnabled = true;
let caseSpinRaf = null;
let caseSpinState = null;
let caseNoiseBuffer = null;

function ensureCaseAudio() {
  if (!caseSoundEnabled) return null;
  try {
    if (!caseAudioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) caseAudioCtx = new Ctx();
    }
    if (caseAudioCtx && caseAudioCtx.state === 'suspended') {
      caseAudioCtx.resume();
    }
    if (caseAudioCtx && !caseNoiseBuffer) {
      const sr = caseAudioCtx.sampleRate;
      const len = Math.floor(sr * 0.08);
      caseNoiseBuffer = caseAudioCtx.createBuffer(1, len, sr);
      const data = caseNoiseBuffer.getChannelData(0);
      for (let i = 0; i < len; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.exp(-i / (sr * 0.014));
      }
    }
    return caseAudioCtx;
  } catch (e) {
    return null;
  }
}

function playCaseLaunchSound() {
  const ctx = ensureCaseAudio();
  if (!ctx) return;
  try {
    const now = ctx.currentTime;

    // 1. Impact grave d'ouverture (Sub-Thump)
    const subOsc = ctx.createOscillator();
    const subGain = ctx.createGain();
    subOsc.type = 'sine';
    subOsc.frequency.setValueAtTime(135, now);
    subOsc.frequency.exponentialRampToValueAtTime(38, now + 0.18);
    subGain.gain.setValueAtTime(0.24, now);
    subGain.gain.exponentialRampToValueAtTime(0.001, now + 0.20);
    subOsc.connect(subGain);
    subGain.connect(ctx.destination);
    subOsc.start(now);
    subOsc.stop(now + 0.21);

    // 2. Déclic mécanique métallique
    if (caseNoiseBuffer) {
      const noise = ctx.createBufferSource();
      noise.buffer = caseNoiseBuffer;
      const filter = ctx.createBiquadFilter();
      filter.type = 'bandpass';
      filter.frequency.setValueAtTime(2400, now + 0.02);
      filter.Q.setValueAtTime(3.2, now + 0.02);
      const nGain = ctx.createGain();
      nGain.gain.setValueAtTime(0.18, now + 0.02);
      nGain.gain.exponentialRampToValueAtTime(0.001, now + 0.065);
      noise.connect(filter);
      filter.connect(nGain);
      nGain.connect(ctx.destination);
      noise.start(now + 0.02);
    }

    // 3. Sweep harmonique ascendant (mise sous tension de la roue)
    const sweepOsc = ctx.createOscillator();
    const sweepGain = ctx.createGain();
    sweepOsc.type = 'triangle';
    sweepOsc.frequency.setValueAtTime(196, now + 0.03);
    sweepOsc.frequency.exponentialRampToValueAtTime(587.33, now + 0.26);
    sweepGain.gain.setValueAtTime(0.001, now + 0.03);
    sweepGain.gain.linearRampToValueAtTime(0.11, now + 0.09);
    sweepGain.gain.exponentialRampToValueAtTime(0.001, now + 0.28);
    sweepOsc.connect(sweepGain);
    sweepGain.connect(ctx.destination);
    sweepOsc.start(now + 0.03);
    sweepOsc.stop(now + 0.29);
  } catch (e) {}
}

function playCaseTickSound(progress = 0, imdbRating = 0) {
  const ctx = ensureCaseAudio();
  if (!ctx) return;
  try {
    const now = ctx.currentTime;

    // Couche 1 : Clac physique du cran (bruit filtré court + boisé grave)
    if (caseNoiseBuffer) {
      const noise = ctx.createBufferSource();
      noise.buffer = caseNoiseBuffer;
      const bp = ctx.createBiquadFilter();
      bp.type = 'bandpass';
      bp.frequency.setValueAtTime(1950 - (progress * 450), now);
      bp.Q.setValueAtTime(4.0, now);
      const nGain = ctx.createGain();
      const clickVol = 0.14 + (progress * 0.08);
      nGain.gain.setValueAtTime(clickVol, now);
      nGain.gain.exponentialRampToValueAtTime(0.001, now + 0.022);
      noise.connect(bp);
      bp.connect(nGain);
      nGain.connect(ctx.destination);
      noise.start(now);
    }

    // Corps percussif boisé/carbone du cran
    const bodyOsc = ctx.createOscillator();
    const bodyGain = ctx.createGain();
    bodyOsc.type = 'triangle';
    const bodyStart = 310 - (progress * 70);
    bodyOsc.frequency.setValueAtTime(bodyStart, now);
    bodyOsc.frequency.exponentialRampToValueAtTime(68, now + 0.030);
    bodyGain.gain.setValueAtTime(0.16 + (progress * 0.06), now);
    bodyGain.gain.exponentialRampToValueAtTime(0.001, now + 0.034);
    bodyOsc.connect(bodyGain);
    bodyGain.connect(ctx.destination);
    bodyOsc.start(now);
    bodyOsc.stop(now + 0.036);

    // Couche 2 : Note cristalline de tension qui monte subtilement
    const pingOsc = ctx.createOscillator();
    const pingGain = ctx.createGain();
    pingOsc.type = 'sine';
    const ratingVal = parseFloat(imdbRating || '0') || 0;
    const ratingBoost = ratingVal >= 8.5 ? 120 : (ratingVal >= 8.0 ? 60 : 0);
    const pingFreq = 380 + (progress * 260) + ratingBoost;
    pingOsc.frequency.setValueAtTime(pingFreq, now);
    pingOsc.frequency.exponentialRampToValueAtTime(pingFreq * 0.96, now + 0.045);
    const pingDur = progress > 0.65 ? 0.075 : 0.042;
    pingGain.gain.setValueAtTime(0.068, now);
    pingGain.gain.exponentialRampToValueAtTime(0.0008, now + pingDur);
    pingOsc.connect(pingGain);
    pingGain.connect(ctx.destination);
    pingOsc.start(now);
    pingOsc.stop(now + pingDur + 0.005);

    // Couche 3 : Pulsation sub-bass dramatique sur les derniers crans au ralenti
    if (progress > 0.66) {
      const sub = ctx.createOscillator();
      const sGain = ctx.createGain();
      sub.type = 'sine';
      sub.frequency.setValueAtTime(95, now);
      sub.frequency.exponentialRampToValueAtTime(42, now + 0.07);
      sGain.gain.setValueAtTime(0.15 * ((progress - 0.66) / 0.34), now);
      sGain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);
      sub.connect(sGain);
      sGain.connect(ctx.destination);
      sub.start(now);
      sub.stop(now + 0.085);
    }
  } catch (e) {}
}

function playCaseWinSound(imdbRating = 0) {
  const ctx = ensureCaseAudio();
  if (!ctx) return;
  try {
    const now = ctx.currentTime;

    // 1. Sub-drop d'impact de verrouillage
    const dropOsc = ctx.createOscillator();
    const dropGain = ctx.createGain();
    dropOsc.type = 'sine';
    dropOsc.frequency.setValueAtTime(140, now);
    dropOsc.frequency.exponentialRampToValueAtTime(36, now + 0.38);
    dropGain.gain.setValueAtTime(0.28, now);
    dropGain.gain.exponentialRampToValueAtTime(0.001, now + 0.42);
    dropOsc.connect(dropGain);
    dropGain.connect(ctx.destination);
    dropOsc.start(now);
    dropOsc.stop(now + 0.44);

    // 2. Accord arpégé de révélation
    const rVal = parseFloat(imdbRating || '0') || 0;
    const notes = rVal >= 8.2
      ? [523.25, 659.25, 783.99, 987.77, 1174.66]
      : [440.0, 554.37, 659.25, 880.0];
    notes.forEach((freq, idx) => {
      const delay = idx * 0.048;
      const osc1 = ctx.createOscillator();
      const osc2 = ctx.createOscillator();
      const gain = ctx.createGain();
      osc1.type = 'triangle';
      osc2.type = 'sine';
      osc1.frequency.setValueAtTime(freq, now + delay);
      osc2.frequency.setValueAtTime(freq * 1.003, now + delay);
      gain.gain.setValueAtTime(0.001, now + delay);
      gain.gain.linearRampToValueAtTime(0.11, now + delay + 0.018);
      gain.gain.exponentialRampToValueAtTime(0.0006, now + delay + 0.72);
      osc1.connect(gain);
      osc2.connect(gain);
      gain.connect(ctx.destination);
      osc1.start(now + delay);
      osc2.start(now + delay);
      osc1.stop(now + delay + 0.75);
      osc2.stop(now + delay + 0.75);
    });
  } catch (e) {}
}

function toggleCaseSound() {
  caseSoundEnabled = !caseSoundEnabled;
  const btn = document.getElementById('caseSoundBtn');
  if (btn) {
    btn.textContent = caseSoundEnabled ? 'Son : Activé' : 'Son : Muet';
  }
}

function closeCaseModal() {
  if (caseSpinRaf) {
    cancelAnimationFrame(caseSpinRaf);
    caseSpinRaf = null;
  }
  caseSpinState = null;
  const modal = document.getElementById('caseModal');
  if (modal) {
    modal.style.display = 'none';
    modal.classList.remove('active');
  }
}

let activeCasePoolMode = 'catalog';
let hideWatchedInCatalog = false;

// « Masquer déjà vus » and the catalog sort are remembered for this viewer (convenience only).
const CATALOG_VIEW_KEY = 'kino_catalog_view';
const CATALOG_SORTS = ['top', 'imdbRating', 'recent', 'oldest'];

function restoreCatalogView() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(CATALOG_VIEW_KEY) || '{}') || {}; } catch (e) { saved = {}; }
  hideWatchedInCatalog = saved.hideWatched === true;
  if (CATALOG_SORTS.includes(saved.sort)) catalogSort = saved.sort;
  const chip = document.getElementById('hideWatchedChip');
  if (chip) chip.classList.toggle('active', hideWatchedInCatalog);
  const select = document.getElementById('catalogSortSelect');
  if (select) select.value = catalogSort;
}

function saveCatalogView() {
  try {
    localStorage.setItem(CATALOG_VIEW_KEY, JSON.stringify({ hideWatched: hideWatchedInCatalog, sort: catalogSort }));
  } catch (e) {}
}

function toggleHideWatchedCatalog() {
  hideWatchedInCatalog = !hideWatchedInCatalog;
  saveCatalogView();
  const chip = document.getElementById('hideWatchedChip');
  if (chip) {
    chip.classList.toggle('active', hideWatchedInCatalog);
  }
  if (['movies', 'series', 'classics', 'anime'].includes(activeTab)) {
    renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
  }
}

function surpriseMeFromCurrentList() {
  if (activeTab === 'customlists' && openCustomListId) {
    surpriseMeFromCustomList(openCustomListId);
  } else if (activeTab === 'watched') {
    surpriseMeMedia('watched');
  } else {
    surpriseMeMedia('watchlist');
  }
}

// --- Ma Liste / Déjà vus: type and state filters, French-title search, remembered per page ---
const LIST_VIEW_KEY = 'kino_list_view_v1';
const LIST_TYPES = [['all', 'Tout'], ['movie', 'Films'], ['series', 'Séries']];
const LIST_STATES = [['all', 'Tout'], ['todo', 'À voir'], ['started', 'Commencés'], ['watched', 'Vus']];
const LIST_SORTS = ['added', 'user_rating_desc', 'rating', 'year_desc', 'year_asc', 'alpha'];
let listView = loadListView();

function loadListView() {
  const view = { watchlist: { type: 'all', state: 'all', sort: 'added' }, watched: { type: 'all', state: 'all', sort: 'added' },
    customlists: { type: 'all', state: 'all', sort: 'added' } };
  try {
    const saved = JSON.parse(localStorage.getItem(LIST_VIEW_KEY) || '{}') || {};
    for (const tab of ['watchlist', 'watched', 'customlists']) {
      const entry = saved[tab] || {};
      if (LIST_TYPES.some(([key]) => key === entry.type)) view[tab].type = entry.type;
      if (tab !== 'watched' && LIST_STATES.some(([key]) => key === entry.state)) view[tab].state = entry.state;
      if (LIST_SORTS.includes(entry.sort)) view[tab].sort = entry.sort;
    }
  } catch (e) {}
  return view;
}

function saveListView() {
  try { localStorage.setItem(LIST_VIEW_KEY, JSON.stringify(listView)); } catch (e) {}
}

function listItemKind(m) {
  return ['series', 'anime', 'tv'].includes(m?.type) || (m?.is_anime && m?.type !== 'movie') ? 'series' : 'movie';
}

// 'watched', 'started' or 'todo', from the history. A series counts as watched
// once its last known episode was seen.
function listItemState(m) {
  const h = (userHistory || []).find(x => x.id === m?.id);
  if (!h) return 'todo';
  const pct = Number(h.progress_pct || 0);
  const done = Boolean(h.completed || pct >= 85 || h.imported_watched);
  if (listItemKind(m) === 'series' || h.type === 'series') return done && h.series_finished ? 'watched' : 'started';
  if (done) return 'watched';
  return pct > 1 ? 'started' : 'todo';
}

function foldSearchText(value) {
  return String(value || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function listMatches(m, query) {
  if (!query) return true;
  const shown = typeof displayTitle === 'function' ? displayTitle(m) : '';
  return foldSearchText(`${shown} ${m.name || ''} ${m.name_fr || ''} ${m.year || m.releaseInfo || ''}`).includes(query);
}

function setListFilter(group, value) {
  const view = listView[activeTab];
  if (!view) return;
  view[group] = value;
  saveListView();
  renderActiveListTab();
}

function resetListFilters() {
  const view = listView[activeTab];
  if (view) {
    view.type = 'all';
    view.state = 'all';
    saveListView();
  }
  const input = document.getElementById('listFilterInput');
  if (input) input.value = '';
  renderActiveListTab();
}

function onChangeListSort(value) {
  const view = listView[activeTab];
  if (view && LIST_SORTS.includes(value)) {
    view.sort = value;
    saveListView();
  }
  renderActiveListTab();
}

function renderListFilters(rawList, view) {
  const box = document.getElementById('listFilters');
  if (!box) return;
  box.hidden = !rawList.length;
  // Empty choices are left out (unless chosen); a group with one real choice is not shown.
  const group = (name, label, options, count, current) => {
    const shown = options.filter(([key]) => key === 'all' || key === current || count(key) > 0);
    if (shown.length <= 2 && current === 'all') return '';
    const chips = shown.map(([key, text]) => {
      const active = current === key;
      return `<button type="button" class="chip${active ? ' active' : ''}" aria-pressed="${active}" onclick="setListFilter('${name}', '${key}')">${text} <span class="list-count">${count(key)}</span></button>`;
    }).join('');
    return `<div class="list-filter-group" role="group" aria-label="${label}"><span class="list-filter-label" aria-hidden="true">${label}</span>${chips}</div>`;
  };
  const ofType = rawList.filter(m => view.type === 'all' || listItemKind(m) === view.type);
  const typeGroup = group('type', 'Type', LIST_TYPES,
    key => key === 'all' ? rawList.length : rawList.filter(m => listItemKind(m) === key).length, view.type);
  const stateGroup = activeTab !== 'watched' ? group('state', 'État', LIST_STATES,
    key => key === 'all' ? ofType.length : ofType.filter(m => listItemState(m) === key).length, view.state) : '';
  box.innerHTML = typeGroup + stateGroup;
  box.hidden = !box.innerHTML;
}

// The list shown now: Ma Liste, Déjà vus, or an open Letterboxd list.
function activeListItems() {
  if (activeTab === 'watchlist') return userWatchlist || [];
  if (activeTab === 'watched') return getWatchedHistory();
  if (activeTab === 'customlists' && typeof openCustomListId !== 'undefined' && openCustomListId) {
    return ((userCustomLists || []).find(list => list.id === openCustomListId) || {}).items || [];
  }
  return null;
}

function renderActiveListTab() {
  const rawList = activeListItems();
  if (!rawList) return;
  const view = listView[activeTab];
  const q = foldSearchText((document.getElementById('listFilterInput')?.value || '').trim());
  const sortSelect = document.getElementById('listSortSelect');
  if (sortSelect) {
    sortSelect.value = view.sort;
    const added = sortSelect.querySelector?.('option[value="added"]');
    if (added) added.textContent = activeTab === 'watched' ? 'Vu récemment' : activeTab === 'customlists' ? 'Ordre de la liste' : 'Ajout récent';
  }
  const sortMode = view.sort;
  const statsEl = document.getElementById('listStatsBadge');

  // Calcul des statistiques de la liste
  if (statsEl) {
    if (rawList.length > 0) {
      const myRated = rawList.map(x => parseFloat(x.user_rating || '0')).filter(r => r > 0);
      const rated = rawList.map(x => parseFloat(x.imdbRating || '0')).filter(r => r > 0);
      const average = values => (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1).replace('.', ',');
      let avgTxt = '';
      if (myRated.length) {
        avgTxt = ` · votre note ${average(myRated)}/5 en moyenne`;
      } else if (rated.length) {
        avgTxt = ` · IMDb ${average(rated)} en moyenne`;
      }
      if (activeTab === 'watchlist' || activeTab === 'customlists') {
        const unwatchedCount = rawList.filter(m => listItemState(m) !== 'watched').length;
        statsEl.textContent = `${rawList.length} titre${rawList.length > 1 ? 's' : ''} · ${unwatchedCount} à voir${avgTxt}`;
      } else {
        statsEl.textContent = `${rawList.length} titre${rawList.length > 1 ? 's' : ''} vu${rawList.length > 1 ? 's' : ''}${avgTxt}`;
      }
      statsEl.style.display = 'inline-flex';
    } else {
      statsEl.style.display = 'none';
    }
  }

  renderListFilters(rawList, view);
  let filtered = rawList.filter(m => listMatches(m, q)
    && (view.type === 'all' || listItemKind(m) === view.type)
    && (activeTab === 'watched' || view.state === 'all' || listItemState(m) === view.state));

  if (sortMode === 'user_rating_desc') {
    filtered = [...filtered].sort((a, b) => {
      const ra = parseFloat(a.user_rating || '0') || 0;
      const rb = parseFloat(b.user_rating || '0') || 0;
      if (rb !== ra) return rb - ra;
      return (parseFloat(b.imdbRating || '0') || 0) - (parseFloat(a.imdbRating || '0') || 0);
    });
  } else if (sortMode === 'rating') {
    filtered = [...filtered].sort((a, b) => (parseFloat(b.imdbRating || '0') || 0) - (parseFloat(a.imdbRating || '0') || 0));
  } else if (sortMode === 'year_desc') {
    filtered = [...filtered].sort((a, b) => (parseInt(b.year || b.releaseInfo || '0', 10) || 0) - (parseInt(a.year || a.releaseInfo || '0', 10) || 0));
  } else if (sortMode === 'year_asc') {
    filtered = [...filtered].sort((a, b) => (parseInt(a.year || a.releaseInfo || '9999', 10) || 9999) - (parseInt(b.year || b.releaseInfo || '9999', 10) || 9999));
  } else if (sortMode === 'alpha') {
    filtered = [...filtered].sort((a, b) => String(typeof displayTitle === 'function' ? displayTitle(a) : a.name || '')
      .localeCompare(String(typeof displayTitle === 'function' ? displayTitle(b) : b.name || ''), 'fr'));
  }

  if (rawList.length && !filtered.length) {
    const grid = document.getElementById('postersGrid');
    if (grid) grid.innerHTML = '<div class="library-empty"><h3>Aucun titre ne correspond</h3><p>Changez de filtre ou de recherche.</p><button type="button" class="btn" onclick="resetListFilters()">Tout afficher</button></div>';
    return;
  }
  renderPosterCards(filtered, 'movie');
}

async function surpriseMeMedia(poolArg = null) {
  let poolMode = 'catalog';
  let customListItems = null;

  if (typeof poolArg === 'object' && poolArg && poolArg.items) {
    poolMode = 'customlist';
    customListItems = poolArg.items;
    window._customListRouletteTitle = poolArg.title || 'Liste Letterboxd';
  } else if (typeof poolArg === 'string' && poolArg.startsWith('customlist:')) {
    poolMode = 'customlist';
    const lid = poolArg.split(':')[1];
    const l = userCustomLists.find(x => x.id === lid);
    customListItems = l?.items || [];
    window._customListRouletteTitle = l?.title || 'Liste Letterboxd';
  } else if (poolArg === true || poolArg === 'watchlist') {
    poolMode = 'watchlist';
  } else if (poolArg === 'watched' || poolArg === 'classics' || poolArg === 'prestige' || poolArg === 'catalog') {
    poolMode = poolArg === 'prestige' ? 'classics' : poolArg;
  } else if (activeTab === 'watchlist') {
    poolMode = 'watchlist';
  } else if (activeTab === 'watched') {
    poolMode = 'watched';
  } else if (activeTab === 'classics') {
    poolMode = 'classics';
  }
  activeCasePoolMode = poolMode;

  document.querySelectorAll('#casePoolChips .chip').forEach(c => {
    c.classList.toggle('active', c.getAttribute('data-pool') === poolMode);
  });

  let sourceItems = [];
  if (poolMode === 'customlist') {
    sourceItems = customListItems || [];
  } else if (poolMode === 'watchlist') {
    sourceItems = (userWatchlist && userWatchlist.length) ? userWatchlist : catalogItems;
  } else if (poolMode === 'watched') {
    const wList = getWatchedHistory();
    sourceItems = wList.length ? wList : catalogItems;
  } else if (poolMode === 'classics') {
    if (activeTab === 'classics' && catalogItems && catalogItems.length) {
      sourceItems = catalogItems;
    } else {
      if (!classicsCatalogCache || !classicsCatalogCache.length) {
        try {
          const data = await api('/api/catalog?type=classics&sort=top');
          classicsCatalogCache = data.metas || [];
        } catch (e) {}
      }
      sourceItems = (classicsCatalogCache && classicsCatalogCache.length) ? classicsCatalogCache : catalogItems;
    }
  } else {
    sourceItems = catalogItems;
  }

  if (!sourceItems || !sourceItems.length) return;
  const fallbackType = (activeTab === 'series' || activeTab === 'anime') ? 'series' : 'movie';

  const modal = document.getElementById('caseModal');
  const strip = document.getElementById('caseRollerStrip');
  const viewport = document.getElementById('caseRollerViewport');
  const winPanel = document.getElementById('caseWinnerPanel');
  const skipBtn = document.getElementById('caseSkipBtn');
  const titleEl = document.getElementById('caseModalTitle');
  const kickerEl = document.getElementById('caseModalKicker');
  if (!modal || !strip || !viewport) return;

  if (caseSpinRaf) {
    cancelAnimationFrame(caseSpinRaf);
    caseSpinRaf = null;
  }

  // Son d'ouverture au clic
  playCaseLaunchSound();

  // Filtrer les œuvres déjà vues (sauf si on est volontairement en mode Rewatch 'watched')
  let rawPool = sourceItems;
  if (poolMode !== 'watched') {
    const unwatchedItems = sourceItems.filter(m => {
      const h = userHistory.find(x => x.id === m.id);
      const isWatched = Boolean(h && (h.completed || Number(h.progress_pct || 0) >= 85));
      return !isWatched;
    });
    if (unwatchedItems.length) rawPool = unwatchedItems;
  }

  // Dédoublonner le pool par identifiant ou titre pour éviter les doublons
  const getItemKey = (item) => String((item && (item.id || item.name)) || '').trim().toLowerCase();
  const seenPoolKeys = new Set();
  const stripPool = [];
  for (const item of rawPool) {
    const k = getItemKey(item);
    if (!k || seenPoolKeys.has(k)) continue;
    seenPoolKeys.add(k);
    stripPool.push(item);
  }
  if (!stripPool.length) return;

  // Filtrer le lot gagnant selon la source choisie
  let winnerPool = stripPool;
  if (poolMode === 'catalog') {
    const goodRated = stripPool.filter(m => (parseFloat(m.imdbRating || '0') || 0) >= 7.4);
    if (goodRated.length) winnerPool = goodRated;
  }

  const winnerRaw = winnerPool[Math.floor(Math.random() * winnerPool.length)];
  if (!winnerRaw) return;
  const winnerKey = getItemKey(winnerRaw);

  // Construire un ruban de 50 cartes sans jamais avoir 2 films identiques de suite (ni autour du gagnant)
  const TOTAL_SLOTS = 50;
  const WINNER_INDEX = 40;
  const slots = [];
  const windowSize = Math.min(4, Math.max(1, stripPool.length - 1));
  const winnerGuardRadius = Math.min(3, Math.max(1, stripPool.length - 1));

  function makeShuffledDeck(arr) {
    const copy = [...arr];
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }

  let deck = makeShuffledDeck(stripPool);

  for (let i = 0; i < TOTAL_SLOTS; i++) {
    if (i === WINNER_INDEX) {
      slots.push(winnerRaw);
      deck = deck.filter(x => getItemKey(x) !== winnerKey);
      continue;
    }

    const forbidden = new Set();
    for (let back = 1; back <= windowSize; back++) {
      if (i - back >= 0 && slots[i - back]) {
        forbidden.add(getItemKey(slots[i - back]));
      }
    }
    if (Math.abs(i - WINNER_INDEX) <= winnerGuardRadius && stripPool.length > 1) {
      forbidden.add(winnerKey);
    }

    let pickIdx = deck.findIndex(x => !forbidden.has(getItemKey(x)));
    if (pickIdx === -1) {
      deck = makeShuffledDeck(stripPool);
      pickIdx = deck.findIndex(x => !forbidden.has(getItemKey(x)));
    }
    if (pickIdx === -1 && i > 0 && stripPool.length > 1) {
      const prevKey = getItemKey(slots[i - 1]);
      pickIdx = deck.findIndex(x => getItemKey(x) !== prevKey);
    }
    const chosen = pickIdx !== -1 ? deck.splice(pickIdx, 1)[0] : deck.shift() || stripPool[0];
    slots.push(chosen);
  }

  strip.innerHTML = slots.map((m, idx) => {
    const ratingVal = parseFloat(m.imdbRating || '0') || 0;
    const ratingBadge = ratingVal > 0 ? `<span class="case-slot-rating">★ ${m.imdbRating}</span>` : '';
    const yearTxt = m.releaseInfo || m.year || (m.type === 'series' ? 'Série' : 'Film');
    const poster = m.poster || '';
    const safeName = escapeHtml(m.name || 'Titre');
    return `
      <div class="case-slot-card" id="caseSlot_${idx}">
        ${ratingBadge}
        ${poster
          ? `<img class="case-slot-poster" src="${escapeHtml(poster)}" alt="${safeName}" loading="eager" onerror="this.style.visibility='hidden'">`
          : `<div class="case-slot-poster" style="display:flex;align-items:center;justify-content:center;color:var(--dim);font-size:0.75rem;">KINO</div>`
        }
        <div class="case-slot-info">
          <div class="case-slot-title" title="${safeName}">${escapeHtml(m.name || 'Sans titre')}</div>
          <div class="case-slot-sub">${escapeHtml(yearTxt)}</div>
        </div>
      </div>
    `;
  }).join('');

  winPanel.style.display = 'none';
  winPanel.innerHTML = '';
  if (skipBtn) skipBtn.style.display = 'inline-flex';
  if (kickerEl) {
    if (poolMode === 'watchlist') kickerEl.textContent = 'TIRAGE · MA LISTE';
    else if (poolMode === 'watched') kickerEl.textContent = 'TIRAGE · DÉJÀ VUS';
    else if (poolMode === 'classics') kickerEl.textContent = 'TIRAGE · CLASSIQUES À VOIR DANS SA VIE';
    else if (poolMode === 'customlist') kickerEl.textContent = `TIRAGE · ${(window._customListRouletteTitle || 'LISTE LETTERBOXD').toUpperCase()}`;
    else kickerEl.textContent = activeGenre ? `TIRAGE · ${activeGenre.toUpperCase()}` : (fallbackType === 'series' ? 'TIRAGE · SÉRIES' : 'TIRAGE · FILMS');
  }
  if (titleEl) titleEl.textContent = 'Sélection en cours...';

  modal.style.display = 'flex';
  modal.classList.add('active');

  // Dimensions géométriques du ruban
  const CARD_W = 152;
  const GAP = 10;
  const STEP = CARD_W + GAP; // 162px par carte
  const PAD_LEFT = 12;
  const vpWidth = viewport.clientWidth || 880;
  const centerOffset = vpWidth / 2;

  // Position exacte du centre de la carte gagnante (index 40)
  const winnerCenterPx = PAD_LEFT + (WINNER_INDEX * STEP) + (CARD_W / 2);
  // Léger décalage aléatoire à l'intérieur de la carte pour garder le suspense sur la décélération
  const jitterPx = (Math.random() - 0.5) * (CARD_W * 0.72);
  const startTranslate = 0;
  const targetTranslate = -(winnerCenterPx + jitterPx - centerOffset);
  const exactCenteredTranslate = -(winnerCenterPx - centerOffset);

  strip.style.transition = 'none';
  strip.style.transform = `translate3d(${startTranslate}px, 0, 0)`;

  const DURATION = 5100;
  const startTime = performance.now();
  let lastTickSlot = 0;

  caseSpinState = {
    winnerRaw,
    fallbackType,
    targetTranslate,
    exactCenteredTranslate,
    winnerIdx: WINNER_INDEX,
    poolMode,
    finished: false
  };

  function stepSpin(now) {
    if (!caseSpinState || caseSpinState.finished) return;
    const elapsed = Math.min(DURATION, now - startTime);
    const t = elapsed / DURATION;
    const ease = 1 - Math.pow(1 - t, 4.7);
    const currentX = startTranslate + (targetTranslate - startTranslate) * ease;
    strip.style.transform = `translate3d(${currentX.toFixed(2)}px, 0, 0)`;

    const distUnderLaser = Math.abs(currentX) + centerOffset - PAD_LEFT;
    const currentSlot = Math.floor(distUnderLaser / STEP);
    if (currentSlot !== lastTickSlot && currentSlot >= 0 && currentSlot < TOTAL_SLOTS) {
      lastTickSlot = currentSlot;
      playCaseTickSound(t, slots[currentSlot]?.imdbRating);
    }

    if (t < 1) {
      caseSpinRaf = requestAnimationFrame(stepSpin);
    } else {
      finishCaseSpin();
    }
  }

  caseSpinRaf = requestAnimationFrame(stepSpin);
}

function skipCaseSpin() {
  if (!caseSpinState || caseSpinState.finished) return;
  if (caseSpinRaf) {
    cancelAnimationFrame(caseSpinRaf);
    caseSpinRaf = null;
  }
  finishCaseSpin(true);
}

function finishCaseSpin(skipped = false) {
  if (!caseSpinState || caseSpinState.finished) return;
  caseSpinState.finished = true;
  const { winnerRaw, fallbackType, exactCenteredTranslate, winnerIdx, poolMode } = caseSpinState;

  const strip = document.getElementById('caseRollerStrip');
  const winCard = document.getElementById(`caseSlot_${winnerIdx}`);
  const winPanel = document.getElementById('caseWinnerPanel');
  const skipBtn = document.getElementById('caseSkipBtn');
  const titleEl = document.getElementById('caseModalTitle');

  if (skipBtn) skipBtn.style.display = 'none';

  // Recentrage magnétique doux sur le film sélectionné
  if (strip) {
    strip.style.transition = skipped ? 'transform 0.18s ease-out' : 'transform 0.38s cubic-bezier(0.22, 1, 0.36, 1)';
    strip.style.transform = `translate3d(${exactCenteredTranslate.toFixed(2)}px, 0, 0)`;
  }
  if (winCard) {
    winCard.classList.add('winner-locked');
  }

  playCaseWinSound(winnerRaw.imdbRating);

  const mtype = winnerRaw.type || fallbackType || 'movie';
  const year = String(winnerRaw.releaseInfo || winnerRaw.year || '');
  const rating = String(winnerRaw.imdbRating || '');
  const genres = Array.isArray(winnerRaw.genres) ? winnerRaw.genres.slice(0, 3) : [];
  const inWl = isInWatchlist(winnerRaw.id);

  if (titleEl) {
    titleEl.textContent = winnerRaw.name || 'Sélection aléatoire';
  }

  const mediaObj = {
    id: winnerRaw.id,
    name: winnerRaw.name,
    type: mtype,
    year,
    poster: winnerRaw.poster || '',
    imdbRating: rating,
    poolMode: poolMode || 'catalog'
  };
  window._lastCaseWinnerMedia = mediaObj;

  if (winPanel) {
    winPanel.style.borderColor = '';
    winPanel.style.boxShadow = '';
    winPanel.innerHTML = `
      <div style="display:flex; align-items:center; gap:14px; min-width:240px; flex:1;">
        ${winnerRaw.poster ? `<img src="${escapeHtml(winnerRaw.poster)}" style="width:54px; height:80px; object-fit:cover; border-radius:6px; border:1px solid rgba(255,255,255,0.12);">` : ''}
        <div>
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
            ${rating ? `<span class="badge" style="background:rgba(255,255,255,0.09); color:#fafafa; border-color:rgba(255,255,255,0.18); font-weight:600;">★ ${rating} IMDb</span>` : ''}
            ${year ? `<span style="font-size:0.78rem; color:var(--muted);">${escapeHtml(year)}</span>` : ''}
            ${genres.map(g => `<span class="badge">${escapeHtml(frenchGenre(g))}</span>`).join('')}
          </div>
          <div style="font-size:1.12rem; font-weight:700; color:#fafafa; margin-top:5px;"${titleAttrs({...winnerRaw, type: mtype})}>${escapeHtml(displayTitle(winnerRaw))}</div>
          ${winnerRaw.description ? `<div style="font-size:0.76rem; color:var(--muted); margin-top:3px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; max-width:470px;">${escapeHtml(winnerRaw.description)}</div>` : ''}
        </div>
      </div>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <button class="btn" onclick="casePlayOneClick(event, this)">Lancer</button>
        <button class="btn btn-secondary" onclick="caseOpenDetails()">Fiche &amp; Sources</button>
        <button class="btn btn-secondary" onclick="caseOpenTrailer()"><svg class="ui-icon ui-icon-xs" aria-hidden="true"><use href="/icons.svg#trailer"></use></svg>Bande-annonce</button>
        <button class="btn btn-secondary" id="caseWlBtn" onclick="caseToggleWatchlist(this)" title="Ajouter ou retirer ce titre de Ma Liste">${inWl ? '✓ Dans Ma Liste' : '+ Ma Liste'}</button>
        ${poolMode !== 'watched' ? `<button class="btn btn-secondary" onclick="caseMarkWatchedAndRespin(this)" title="Marquer ce titre comme Déjà vu, le retirer de la roulette et relancer un tirage">✓ Déjà vu</button>` : ''}
        <button class="btn-surprise" onclick="surpriseMeMedia('${poolMode || 'catalog'}')">Relancer</button>
      </div>
    `;
    winPanel.style.display = 'flex';
  }
}

async function caseToggleWatchlist(btnEl) {
  const m = window._lastCaseWinnerMedia;
  if (!m || !m.id) return;
  await toggleWatchlist(null, m);
  if (btnEl) {
    const inWl = isInWatchlist(m.id);
    btnEl.textContent = inWl ? '✓ Dans Ma Liste' : '+ Ma Liste';
  }
}

async function caseMarkWatchedAndRespin(btnEl) {
  const m = window._lastCaseWinnerMedia;
  if (!m || !m.id) return;
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '✓ Ajouté aux déjà vus...';
  }
  try {
    const res = await api('/api/watched-toggle', {
      method: 'POST',
      body: JSON.stringify({
        id: m.id,
        name: m.name || '',
        type: m.type || 'movie',
        year: m.year || '',
        poster: m.poster || '',
        imdbRating: m.imdbRating || '',
        force_watched: true
      })
    });
    userHistory = res.history || [];
    updateListBadges();
    renderHomeResume();
    if (activeTab === 'watchlist' || activeTab === 'watched') {
      renderActiveListTab();
    } else if (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics') {
      updateClassicsStatsBadge();
      renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
    }
    // Relancer immédiatement la roulette dans le même mode sans ce film
    surpriseMeMedia(m.poolMode || 'catalog');
  } catch (e) {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = '✓ Déjà vu';
    }
  }
}

function casePlayOneClick(ev, btnEl) {
  const m = window._lastCaseWinnerMedia;
  if (!m) return;
  closeCaseModal();
  oneClickCard(ev, btnEl, m);
}

function caseOpenDetails() {
  const m = window._lastCaseWinnerMedia;
  if (!m) return;
  closeCaseModal();
  selectMedia(m);
}

function caseOpenTrailer() {
  const m = window._lastCaseWinnerMedia;
  if (!m) return;
  closeCaseModal();
  openTrailerModal('', m.name || 'KINO', m.year || '', 'vf');
}

// --- Mes séries: where each followed series stands (series_progress.py) ---
// Numbers and dates only: no episode title, so nothing is spoiled.
const MY_SERIES_GROUPS = [['continue', 'À continuer'], ['uptodate', 'À jour'], ['start', 'À commencer']];
let mySeriesData = [];
let mySeriesRequest = 0;

function seriesEpisodeLabel(season, episode) {
  return `S${Number(season) || 1} · É${Number(episode) || 1}`;
}

function mySeriesFacts(item) {
  const plural = (n, word) => `${n} ${word}${n > 1 ? 's' : ''}`;
  const upcoming = item.upcoming
    ? `Prochain à sortir : ${seriesEpisodeLabel(item.upcoming.season, item.upcoming.episode)} le ${episodeAirLabel(item.upcoming.released)}` : '';
  if (item.group === 'start') {
    return [item.aired ? `${plural(item.aired, 'épisode')} à découvrir` : 'Pas encore d’épisode sorti', upcoming];
  }
  const current = item.current || {};
  const where = item.next?.resume
    ? `${seriesEpisodeLabel(current.season, current.episode)} en cours (${item.next.progress_pct} %)`
    : current.progress_pct === 100 ? `Vu jusqu’à ${seriesEpisodeLabel(current.season, current.episode)}`
      : `Arrivé à ${seriesEpisodeLabel(current.season, current.episode)}`;
  if (!item.known) return [where, 'Liste des épisodes indisponible pour le moment'];
  const left = item.group === 'uptodate'
    ? (item.finished ? 'Série terminée, tout est vu' : 'À jour')
    : `${plural(item.remaining, 'épisode')} à voir`;
  return [where, left, upcoming];
}

function mySeriesItemHtml(item) {
  const id = /^tt\d{1,12}$/.test(item.id) ? item.id : '';
  if (!id) return '';
  const facts = mySeriesFacts(item).filter(Boolean);
  const share = item.aired ? Math.round(100 * item.seen / item.aired) : 0;
  const next = item.next;
  const play = next
    ? `<button type="button" class="btn" onclick="playMySeries('${id}', this)">${next.resume ? 'Reprendre' : 'Lancer'} ${seriesEpisodeLabel(next.season, next.episode)}</button>` : '';
  return `<article class="offline-item myseries-item">
      <div class="offline-poster">${item.poster ? `<img src="${escapeHtml(item.poster)}" alt="" loading="lazy" onerror="this.remove()">` : ''}<span aria-hidden="true">▷</span></div>
      <div class="offline-info">
        <strong${titleAttrs(item, 'series')}>${escapeHtml(displayTitle(item))}</strong>
        ${facts.map(fact => `<span class="myseries-fact">${escapeHtml(fact)}</span>`).join('')}
        ${item.aired && item.group !== 'start' ? `<div class="progress-bar" role="img" aria-label="${item.seen} épisodes vus sur ${item.aired}"><div class="progress-fill" style="width:${share}%"></div></div>` : ''}
      </div>
      <div class="offline-actions">${play}<button type="button" class="btn btn-secondary" onclick="openMySeries('${id}')">Fiche</button></div>
    </article>`;
}

function renderMySeries() {
  const grid = document.getElementById('postersGrid');
  if (!grid || activeTab !== 'myseries') return;
  if (!mySeriesData.length) {
    grid.innerHTML = '<div class="library-empty"><h3>Aucune série suivie pour l’instant</h3><p>Lancez un épisode, ou ajoutez une série à Ma Liste : elle apparaîtra ici, avec les épisodes qui restent et les prochains à sortir.</p></div>';
    return;
  }
  grid.innerHTML = MY_SERIES_GROUPS.map(([group, label]) => {
    const items = mySeriesData.filter(item => item.group === group);
    return items.length
      ? `<h3 class="search-group-title">${label} <span class="list-count">${items.length}</span></h3>${items.map(mySeriesItemHtml).join('')}` : '';
  }).join('');
}

async function loadMySeries() {
  const grid = document.getElementById('postersGrid');
  const request = ++mySeriesRequest;
  if (grid) grid.innerHTML = '<p class="myseries-fact">Chargement de vos séries…</p>';
  try {
    const data = await api('/api/my-series');
    if (request !== mySeriesRequest) return;
    mySeriesData = Array.isArray(data.series) ? data.series : [];
    rememberFrenchTitles(mySeriesData);
    renderMySeries();
  } catch (error) {
    if (request !== mySeriesRequest || !grid || activeTab !== 'myseries') return;
    grid.innerHTML = error.code === 'offline' ? needsInternetHtml('Mes séries')
      : `<p class="myseries-fact">Impossible d’afficher vos séries : ${escapeHtml(error.message)}</p>`;
  }
}

function mySeriesMedia(item) {
  return { id: item.id, name: item.name, type: 'series', year: item.year || '', poster: item.poster || '' };
}

function openMySeries(id) {
  const item = mySeriesData.find(entry => entry.id === id);
  if (item) selectMedia(mySeriesMedia(item));
}

async function playMySeries(id, btn) {
  const item = mySeriesData.find(entry => entry.id === id);
  if (!item?.next) return;
  currentMedia = mySeriesMedia(item);
  const { season, episode } = item.next;
  await oneClickPlay({
    imdb_id: item.id, type: 'series', season, episode,
    title: `${item.name} — S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`,
    name: item.name, poster: item.poster || '', year: item.year || '',
  }, btn);
}


// --- Calendrier: this week's and next week's episodes of the series followed
// (release_calendar.py), day by day, then « Plus tard ». Numbers and dates only.
const CALENDAR_DAYS = ['dimanche', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'];
let releaseCalendarData = null;
let releaseCalendarRequest = 0;

function calendarDate(day) {
  const [year, month, date] = String(day || '').split('-').map(Number);
  return year && month && date ? new Date(Date.UTC(year, month - 1, date)) : null;
}

function calendarShift(day, days) {
  const date = calendarDate(day);
  if (!date) return '';
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function calendarShort(day) {
  return episodeAirLabel(day).replace(/ \d{4}$/, '');
}

function calendarDayTitle(day, today) {
  const date = calendarDate(day);
  if (!date) return { label: '', near: '' };
  const name = CALENDAR_DAYS[date.getUTCDay()];
  const near = day === today ? 'Aujourd’hui' : day === calendarShift(today, 1) ? 'Demain'
    : day === calendarShift(today, -1) ? 'Hier' : '';
  return { label: `${name.charAt(0).toUpperCase()}${name.slice(1)} ${calendarShort(day)}`, near };
}

function calendarRange(start) {
  const end = calendarShift(start, 6);
  const sameMonth = start.slice(0, 7) === end.slice(0, 7);
  return `${sameMonth ? calendarShort(start).split(' ')[0] : calendarShort(start)} – ${calendarShort(end)}`;
}

function calendarEpisodes(item) {
  const label = seriesEpisodeLabel(item.season, item.episode);
  const last = Number(item.last_episode) || Number(item.episode);
  const range = last > item.episode ? `${label} à É${last} (${last - item.episode + 1} épisodes)` : label;
  const tag = item.premiere === 'series' ? ' · Première' : item.premiere === 'season' ? ' · Nouvelle saison' : '';
  return range + tag;
}

function calendarState(item) {
  if (item.seen) return 'Vu';
  if (item.resume_pct) return `En cours (${item.resume_pct} %)`;
  const before = item.before ? `${item.before} épisode${item.before > 1 ? 's' : ''} à voir avant` : '';
  if (item.aired) return ['Disponible', before].filter(Boolean).join(' · ');
  if (before) return before;
  return item.followed ? 'Vous êtes à jour' : 'Dans Ma Liste';
}

function calendarItemHtml(item, later = false) {
  const id = /^tt\d{1,12}$/.test(item.id) ? item.id : '';
  if (!id) return '';
  const season = Number(item.season) || 1;
  const episode = Number(item.episode) || 1;
  const facts = later ? [calendarEpisodes(item), `Le ${episodeAirLabel(item.released)}`] : [calendarEpisodes(item), calendarState(item)];
  const play = !later && item.aired && !item.seen && !item.before
    ? `<button type="button" class="btn" onclick="playCalendarEpisode('${id}', ${season}, ${episode}, this)">${item.resume_pct ? 'Reprendre' : 'Lancer'} ${seriesEpisodeLabel(season, episode)}</button>` : '';
  return `<article class="offline-item myseries-item calendar-item${item.seen ? ' is-seen' : ''}">
      <div class="offline-poster">${item.poster ? `<img src="${escapeHtml(item.poster)}" alt="" loading="lazy" onerror="this.remove()">` : ''}<span aria-hidden="true">▷</span></div>
      <div class="offline-info">
        <strong${titleAttrs(item, 'series')}>${escapeHtml(displayTitle(item))}</strong>
        ${facts.filter(Boolean).map(fact => `<span class="myseries-fact">${escapeHtml(fact)}</span>`).join('')}
      </div>
      <div class="offline-actions">${play}<button type="button" class="btn btn-secondary" onclick="openCalendarSeries('${id}')">Fiche</button></div>
    </article>`;
}

function calendarWeekHtml(data, start, title, none) {
  const end = calendarShift(start, 7);
  const days = (data.days || []).filter(day => day.date >= start && day.date < end);
  const today = data.local_today || data.today;
  const body = days.length ? days.map(day => {
    const { label, near } = calendarDayTitle(day.date, today);
    return `<section class="calendar-day${day.date === today ? ' is-today' : ''}">
        <h4 class="calendar-day-title">${escapeHtml(label)}${near ? ` <span class="calendar-near">${near}</span>` : ''}</h4>
        ${(day.items || []).map(item => calendarItemHtml(item)).join('')}
      </section>`;
  }).join('') : `<p class="myseries-fact calendar-none">${none}</p>`;
  return `<h3 class="search-group-title">${title} <span class="calendar-range">${escapeHtml(calendarRange(start))}</span></h3>${body}`;
}

function renderReleaseCalendar() {
  const grid = document.getElementById('postersGrid');
  const data = releaseCalendarData;
  if (!grid || activeTab !== 'calendar' || !data) return;
  if (!data.series) {
    grid.innerHTML = '<div class="library-empty"><h3>Aucune série suivie pour l’instant</h3><p>Lancez un épisode, ou ajoutez une série à Ma Liste : ses prochaines sorties apparaîtront ici, jour par jour.</p></div>';
    return;
  }
  const missing = data.unavailable
    ? `<p class="myseries-fact calendar-none">${data.unavailable} série${data.unavailable > 1 ? 's' : ''} sans liste d’épisodes pour le moment : le calendrier est incomplet.</p>` : '';
  const later = (data.later || []).length
    ? `<h3 class="search-group-title">Plus tard <span class="list-count">${data.later.length}</span></h3>${data.later.map(item => calendarItemHtml(item, true)).join('')}` : '';
  grid.innerHTML = missing
    + calendarWeekHtml(data, data.start, 'Cette semaine', 'Rien ne sort cette semaine pour vos séries.')
    + calendarWeekHtml(data, calendarShift(data.start, 7), 'Semaine prochaine', 'Rien ne sort la semaine prochaine pour vos séries.')
    + later;
}

async function loadReleaseCalendar() {
  const grid = document.getElementById('postersGrid');
  const request = ++releaseCalendarRequest;
  if (grid) grid.innerHTML = '<p class="myseries-fact">Chargement du calendrier…</p>';
  try {
    const data = await api('/api/calendar');
    if (request !== releaseCalendarRequest) return;
    releaseCalendarData = data && typeof data === 'object' ? data : null;
    rememberFrenchTitles([...(data?.days || []).flatMap(day => day.items || []), ...(data?.later || [])]);
    renderReleaseCalendar();
  } catch (error) {
    if (request !== releaseCalendarRequest || !grid || activeTab !== 'calendar') return;
    grid.innerHTML = error.code === 'offline' ? needsInternetHtml('Le calendrier')
      : `<p class="myseries-fact">Impossible d’afficher le calendrier : ${escapeHtml(error.message)}</p>`;
  }
}

function calendarEntry(id) {
  const data = releaseCalendarData || {};
  return [...(data.days || []).flatMap(day => day.items || []), ...(data.later || [])].find(item => item.id === id);
}

function openCalendarSeries(id) {
  const item = calendarEntry(id);
  if (item) selectMedia(mySeriesMedia(item));
}

async function playCalendarEpisode(id, season, episode, btn) {
  const item = calendarEntry(id);
  if (!item || !Number(season) || !Number(episode)) return;
  currentMedia = mySeriesMedia(item);
  await oneClickPlay({
    imdb_id: item.id, type: 'series', season: Number(season), episode: Number(episode),
    title: `${item.name} — S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`,
    name: item.name, poster: item.poster || '', year: item.year || '',
  }, btn);
}
