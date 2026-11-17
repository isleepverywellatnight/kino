/* ============================================================================
 * IMPORTATION WATCHLIST & FILMS DÉJÀ VUS LETTERBOXD
 * ============================================================================ */
let letterboxdCsvContent = '';
let letterboxdCsvName = '';

function openLetterboxdModal() {
  const m = document.getElementById('letterboxdModal');
  const st = document.getElementById('lbxImportStatus');
  if (st) { st.style.display = 'none'; st.textContent = ''; }
  const fnEl = document.getElementById('lbxCsvFileName');
  const clrBtn = document.getElementById('lbxCsvClearBtn');
  if (fnEl) fnEl.textContent = '';
  if (clrBtn) clrBtn.style.display = 'none';
  letterboxdCsvContent = '';
  letterboxdCsvName = '';

  const inp = document.getElementById('lbxUrlInput');
  if (inp && !inp.value && window.savedLetterboxdUser) {
    inp.value = window.savedLetterboxdUser;
  }

  if (m) {
    m.style.display = 'flex';
    m.classList.add('active');
    if (inp) setTimeout(() => { inp.focus(); inp.select(); }, 50);
  }
}

function closeLetterboxdModal() {
  const m = document.getElementById('letterboxdModal');
  if (m) {
    m.style.display = 'none';
    m.classList.remove('active');
  }
}

function handleLetterboxdCsvFile(files) {
  if (!files || !files.length) return;
  const file = files[0];
  letterboxdCsvName = file.name;
  const fnEl = document.getElementById('lbxCsvFileName');
  const clrBtn = document.getElementById('lbxCsvClearBtn');
  if (fnEl) fnEl.textContent = '✓ ' + file.name;
  if (clrBtn) clrBtn.style.display = 'inline';
  const reader = new FileReader();
  reader.onload = () => {
    letterboxdCsvContent = String(reader.result || '');
  };
  reader.readAsText(file, 'utf-8');
}

function clearLetterboxdCsv(ev) {
  if (ev) ev.stopPropagation();
  letterboxdCsvContent = '';
  letterboxdCsvName = '';
  const fileInp = document.getElementById('lbxCsvFileInput');
  if (fileInp) fileInp.value = '';
  const fnEl = document.getElementById('lbxCsvFileName');
  const clrBtn = document.getElementById('lbxCsvClearBtn');
  if (fnEl) fnEl.textContent = '';
  if (clrBtn) clrBtn.style.display = 'none';
}

function updateListBadges() {
  const wlEl = document.getElementById('wlCount');
  if (wlEl) wlEl.textContent = userWatchlist.length ? String(userWatchlist.length) : '';
  const inProg = getInProgressHistory();
  const histEl = document.getElementById('histCount');
  if (histEl) histEl.textContent = inProg.length ? String(inProg.length) : '';
  const watchedList = getWatchedHistory();
  const watchedEl = document.getElementById('watchedCount');
  if (watchedEl) watchedEl.textContent = watchedList.length ? String(watchedList.length) : '';
  const clEl = document.getElementById('customListsCount');
  if (clEl && window.customLists) clEl.textContent = window.customLists.length ? String(window.customLists.length) : '';
  // The sidebar badge says what waits to be continued, not a total of everything.
  const libTotalEl = document.getElementById('libraryTotalBadge');
  if (libTotalEl) {
    libTotalEl.textContent = inProg.length ? String(inProg.length) : '';
    libTotalEl.title = inProg.length ? `${inProg.length} lecture${inProg.length > 1 ? 's' : ''} en cours` : '';
  }
}

async function toggleCardWatched(ev, media) {
  if (ev) ev.stopPropagation();
  if (!media || !media.id) return;
  try {
    const res = await api('/api/watched-toggle', {
      method: 'POST',
      body: JSON.stringify({
        id: media.id,
        name: media.name || '',
        type: media.type || 'movie',
        year: media.year || '',
        poster: media.poster || '',
        imdbRating: media.imdbRating || ''
      })
    });
    userHistory = res.history || [];
    updateListBadges();
    renderHomeResume();
    if (searchResultsShown) {
      // Search results and the actor page stay on screen; the actor page redraws its cards.
      if (personView) renderPersonPage();
    } else if (activeTab === 'watched' || activeTab === 'watchlist') {
      renderActiveListTab();
    } else if (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics') {
      updateClassicsStatsBadge();
      renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
    }
  } catch (e) {
    console.warn(e);
  }
}

// Films removed in KINO stay out of an import; the result says how many, and
// « Les réimporter aussi » runs the same import again with restore_removed.
function letterboxdRemovedText(res) {
  const count = Number(res.removed_watched_count || 0) + Number(res.removed_wl_count || 0);
  if (!count) return '';
  return count === 1
    ? '1 film que vous aviez retiré de KINO n’a pas été réimporté.'
    : `${count} films que vous aviez retirés de KINO n’ont pas été réimportés.`;
}

// What else the import did: wrong films of an earlier import taken back, titles not found.
function letterboxdNotesText(res) {
  const notes = [];
  const corrected = Number(res.corrected_watched_count || 0);
  const unmatched = Number(res.unmatched_count || 0);
  if (corrected) notes.push(corrected === 1
    ? '1 film mal reconnu par un import précédent a été retiré.'
    : `${corrected} films mal reconnus par un import précédent ont été retirés.`);
  if (unmatched) notes.push(unmatched === 1
    ? '1 titre n’a pas été trouvé dans le catalogue.'
    : `${unmatched} titres n’ont pas été trouvés dans le catalogue.`);
  return notes.join(' ');
}

async function submitLetterboxdImport(options = {}) {
  const restoreRemoved = options.restoreRemoved === true;
  const restoreBtn = document.getElementById('lbxRestoreRemovedBtn');
  if (restoreBtn) restoreBtn.hidden = true;
  const urlOrUser = (document.getElementById('lbxUrlInput')?.value || '').trim();
  const csvText = (letterboxdCsvContent || '').trim();
  const st = document.getElementById('lbxImportStatus');
  const btn = document.getElementById('lbxSubmitBtn');

  if (!urlOrUser && !csvText) {
    if (st) {
      st.style.color = '#f87171';
      st.textContent = 'Indiquez votre pseudo Letterboxd ou choisissez un fichier .csv.';
      st.style.display = 'block';
    }
    return;
  }

  const origTxt = btn ? btn.textContent : 'Synchroniser';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Synchronisation...';
  }
  if (st) {
    st.style.color = '#fbbf24';
    st.textContent = 'Récupération de Letterboxd et mise à jour de la bibliothèque...';
    st.style.display = 'block';
  }

  try {
    const res = await api('/api/import-letterboxd', {
      method: 'POST',
      body: JSON.stringify({
        url_or_user: urlOrUser,
        csv_text: csvText,
        csv_filename: letterboxdCsvName,
        mode: 'both',
        restore_removed: restoreRemoved
      })
    });
    userWatchlist = res.watchlist || [];
    if (res.history) userHistory = res.history;
    if (urlOrUser && !urlOrUser.startsWith('http') && !urlOrUser.includes('/')) {
      window.savedLetterboxdUser = urlOrUser.replace(/^@/, '');
    }
    updateListBadges();
    renderHomeResume();
    if (activeTab === 'watchlist' || activeTab === 'watched') {
      renderActiveListTab();
    } else if (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics') {
      updateClassicsStatsBadge();
      renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
    }
    if (st) {
      st.style.color = '#00e054';
      const parts = [];
      if (res.added_wl_count > 0 || res.found_wl_count > 0) {
        parts.push(`${res.added_wl_count} dans Ma Liste`);
      }
      if (res.added_watched_count > 0 || res.found_watched_count > 0) {
        parts.push(`${res.added_watched_count} dans Déjà vus`);
      }
      const removedText = letterboxdRemovedText(res);
      const notesText = letterboxdNotesText(res);
      st.textContent = `✓ Synchronisation réussie : ${parts.join(', ') || (res.found_count + ' films')}.${[notesText, removedText].filter(Boolean).map(x => ' ' + x).join('')}`;
      st.style.display = 'block';
      if (restoreBtn) restoreBtn.hidden = !removedText;
      if (removedText || notesText) return;
    }
    setTimeout(() => {
      closeLetterboxdModal();
    }, 1200);
  } catch (e) {
    if (st) {
      st.style.color = '#f87171';
      st.textContent = 'Erreur : ' + e.message;
      st.style.display = 'block';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origTxt;
    }
  }
}

let userCustomLists = [];

function switchLbxModalTab(tab) {
  const btnProf = document.getElementById('lbxTabBtnProfile');
  const btnList = document.getElementById('lbxTabBtnList');
  const secProf = document.getElementById('lbxSectionProfile');
  const secList = document.getElementById('lbxSectionList');
  if (tab === 'profile') {
    if (btnProf) btnProf.classList.add('active');
    if (btnList) btnList.classList.remove('active');
    if (secProf) secProf.style.display = 'block';
    if (secList) secList.style.display = 'none';
  } else {
    if (btnList) btnList.classList.add('active');
    if (btnProf) btnProf.classList.remove('active');
    if (secList) secList.style.display = 'block';
    if (secProf) secProf.style.display = 'none';
    renderSavedListsInModal();
  }
}

async function fetchCustomLists() {
  try {
    const data = await api('/api/letterboxd/custom-lists');
    userCustomLists = data.custom_lists || [];
    const cntEl = document.getElementById('customListsCount');
    if (cntEl) cntEl.textContent = userCustomLists.length ? String(userCustomLists.length) : '';
  } catch (e) {
    console.warn(e);
  }
}

function renderSavedListsInModal() {
  const wrap = document.getElementById('lbxSavedListsWrap');
  const container = document.getElementById('lbxSavedListsContainer');
  if (!wrap || !container) return;
  if (!userCustomLists.length) {
    wrap.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  wrap.style.display = 'block';
  container.innerHTML = userCustomLists.map(l => `
    <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.03); border:1px solid var(--border); padding:6px 10px; border-radius:6px;">
      <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:320px;">
        <span style="font-size:0.78rem; font-weight:600; color:#fafafa;">${escapeHtml(l.title)}</span>
        <span style="font-size:0.72rem; color:var(--muted); margin-left:6px;">(${l.count} films)</span>
      </div>
      <button type="button" class="btn btn-secondary" style="padding:2px 7px; font-size:0.7rem; color:#f87171;" onclick="deleteLetterboxdCustomList(${attrJson(l.id)})">✕</button>
    </div>
  `).join('');
}

async function submitLetterboxdCustomListImport() {
  const urlInp = document.getElementById('lbxCustomListUrlInput');
  const url = (urlInp?.value || '').trim();
  const st = document.getElementById('lbxCustomListStatus');
  const btn = document.getElementById('lbxCustomListSubmitBtn');

  if (!url) {
    if (st) {
      st.style.color = '#f87171';
      st.textContent = 'Veuillez coller une URL de liste Letterboxd valide.';
      st.style.display = 'block';
    }
    return;
  }

  const origTxt = btn ? btn.textContent : 'Importer cette liste';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Importation en cours...';
  }
  if (st) {
    st.style.color = '#fbbf24';
    st.textContent = 'Analyse de la liste et résolution des métadonnées (1-2 min max)...';
    st.style.display = 'block';
  }

  try {
    const res = await api('/api/letterboxd/import-list', {
      method: 'POST',
      body: JSON.stringify({ url })
    });
    userCustomLists = res.custom_lists || [];
    const cntEl = document.getElementById('customListsCount');
    if (cntEl) cntEl.textContent = userCustomLists.length ? String(userCustomLists.length) : '';

    if (st) {
      st.style.color = '#00e054';
      st.textContent = `✓ Liste "${res.list.title}" importée avec succès (${res.total_resolved} films résolus) !`;
      st.style.display = 'block';
    }
    renderSavedListsInModal();
    if (urlInp) urlInp.value = '';

    if (activeTab === 'customlists') {
      renderCustomListsTab();
    } else if (activeTab === 'community') {
      loadCommunityTab();
    }
  } catch (e) {
    if (st) {
      st.style.color = '#f87171';
      st.textContent = 'Erreur : ' + e.message;
      st.style.display = 'block';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origTxt;
    }
  }
}

async function deleteLetterboxdCustomList(listId) {
  if (!confirm('Voulez-vous vraiment retirer cette liste importée ?')) return;
  try {
    const res = await api('/api/letterboxd/delete-list', {
      method: 'POST',
      body: JSON.stringify({ id: listId })
    });
    userCustomLists = res.custom_lists || [];
    const cntEl = document.getElementById('customListsCount');
    if (cntEl) cntEl.textContent = userCustomLists.length ? String(userCustomLists.length) : '';
    renderSavedListsInModal();
    if (activeTab === 'customlists') {
      renderCustomListsTab();
    } else if (activeTab === 'community') {
      loadCommunityTab();
    }
  } catch (e) {
    console.warn(e);
  }
}

function renderCustomListsTab() {
  const grid = document.getElementById('postersGrid');
  const catTitle = document.getElementById('catalogTitle');
  const lm = document.getElementById('loadMoreWrap');
  const gf = document.getElementById('genreFilters');
  const sw = document.getElementById('catalogSortWrap');
  const wlw = document.getElementById('watchlistActionsWrap');
  const hs = document.getElementById('heroSpotlight');

  if (gf) gf.style.display = 'none';
  if (sw) sw.style.display = 'none';
  if (lm) lm.style.display = 'none';
  if (hs) hs.style.display = 'none';
  document.getElementById('homeResumeSection').style.display = 'none';
  const newSection = document.getElementById('homeNewSection');
  if (newSection) newSection.style.display = 'none';

  const open = userCustomLists.find(list => list.id === openCustomListId);
  if (open) {
    renderOpenCustomList(open);
    return;
  }
  openCustomListId = null;
  if (wlw) wlw.style.display = 'none';
  const filters = document.getElementById('listFilters');
  if (filters) filters.hidden = true;
  const stats = document.getElementById('listStatsBadge');
  if (stats) stats.style.display = 'none';

  if (catTitle) {
    catTitle.innerHTML = `
      <div class="lists-title">
        <span>Listes Letterboxd</span>
        <button type="button" class="btn btn-secondary" onclick="openLetterboxdModal(); switchLbxModalTab('list');">+ Importer une liste</button>
      </div>`;
  }

  grid.style.display = 'block';
  grid.style.flexDirection = '';
  grid.style.gap = '';
  if (!userCustomLists.length) {
    grid.innerHTML = `
      <div class="library-empty">
        <span class="library-empty-icon" aria-hidden="true">≡</span>
        <h3>Aucune liste Letterboxd importée</h3>
        <p>Collez le lien d'une liste Letterboxd publique (Top 250, A24, un réalisateur, une ambiance) pour la retrouver ici, prête à lancer, avec sa roulette.</p>
        <button type="button" class="btn" onclick="openLetterboxdModal(); switchLbxModalTab('list');">Importer une liste Letterboxd</button>
      </div>`;
    return;
  }
  grid.innerHTML = userCustomLists.map(customListShelfHtml).join('');
}

// --- Letterboxd lists: one shelf per list, then a full page with filters ---
let openCustomListId = null;
const LIST_SHELF_SIZE = 12;

function customListProgress(items) {
  const watched = items.filter(m => listItemState(m) === 'watched').length;
  return { watched, total: items.length, pct: items.length ? Math.round((watched / items.length) * 100) : 0 };
}

function customListShelfHtml(list) {
  const items = list.items || [];
  const progress = customListProgress(items);
  const label = escapeHtml(list.title || 'Liste');
  return `
    <section class="list-shelf" aria-label="${label}">
      <div class="list-shelf-head">
        <div class="list-shelf-info">
          <h3 class="list-shelf-title">${label}</h3>
          <div class="list-shelf-meta">${progress.total} titre${progress.total > 1 ? 's' : ''} · ${progress.watched} vu${progress.watched > 1 ? 's' : ''}</div>
          <div class="list-shelf-progress" role="progressbar" aria-label="Titres vus" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.pct}"><span style="width:${progress.pct}%"></span></div>
          ${list.description ? `<p class="list-shelf-desc">${escapeHtml(list.description)}</p>` : ''}
        </div>
        <div class="list-shelf-actions">
          <button type="button" class="btn" onclick="openCustomList(${attrJson(list.id)})">Tout voir</button>
          <button type="button" class="btn-surprise" onclick="surpriseMeFromCustomList(${attrJson(list.id)})" title="Tirer un titre non vu de cette liste">Roulette</button>
          ${/^https:\/\/letterboxd\.com\//.test(String(list.url || '')) ? `<a href="${escapeHtml(list.url)}" target="_blank" rel="noopener" class="btn btn-secondary">Letterboxd ↗</a>` : ''}
          <button type="button" class="btn btn-secondary" onclick="deleteLetterboxdCustomList(${attrJson(list.id)})" aria-label="Retirer la liste ${label}">Retirer</button>
        </div>
      </div>
      ${items.length ? `<div class="list-shelf-row">${items.slice(0, LIST_SHELF_SIZE).map(m => posterCardHtml(m, 'movie')).join('')}${items.length > LIST_SHELF_SIZE ? `<button type="button" class="list-shelf-more" onclick="openCustomList(${attrJson(list.id)})">Voir les ${items.length} titres</button>` : ''}</div>` : '<p class="list-shelf-empty">Cette liste est vide.</p>'}
    </section>`;
}

function openCustomList(listId) {
  openCustomListId = listId;
  const input = document.getElementById('listFilterInput');
  if (input) input.value = '';
  renderCustomListsTab();
  window.scrollTo?.({ top: 0, behavior: 'auto' });
}

function closeCustomList() {
  openCustomListId = null;
  renderCustomListsTab();
}

// The open list reuses Ma Liste's tools: state filter, French search, sort.
function renderOpenCustomList(list) {
  const grid = document.getElementById('postersGrid');
  grid.style.display = '';
  grid.style.flexDirection = '';
  grid.style.gap = '';
  const catTitle = document.getElementById('catalogTitle');
  if (catTitle) {
    catTitle.innerHTML = `<span class="lists-title"><button type="button" class="btn btn-secondary lists-back" onclick="closeCustomList()" aria-label="Revenir aux listes">← Listes</button><span>${escapeHtml(list.title || 'Liste')}</span></span>`;
  }
  const wlw = document.getElementById('watchlistActionsWrap');
  if (wlw) wlw.style.display = 'flex';
  const surpriseLbl = document.getElementById('listSurpriseBtnLabel');
  if (surpriseLbl) surpriseLbl.textContent = 'Roulette sur cette liste';
  renderActiveListTab();
}

function surpriseMeFromCustomList(listId) {
  const l = userCustomLists.find(x => x.id === listId);
  if (!l || !l.items || !l.items.length) {
    alert('Cette liste ne contient aucun film.');
    return;
  }
  const unviewed = l.items.filter(m => listItemState(m) !== 'watched');
  const pool = unviewed.length ? unviewed : l.items;
  surpriseMeMedia({ items: pool, title: l.title });
}

