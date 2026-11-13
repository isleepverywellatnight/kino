// Escape closes the dialog in front first. The remote QR code and Watch Party open over
// the player: closing the whole player there interrupted the Playback Session.
const ESCAPE_DIALOGS = [
  ['shortcutsModal', () => toggleShortcutsModal()],
  ['remoteModal', () => closeRemoteModal()],
  ['watchPartyModal', () => closeWatchPartyModal()],
  ['addonsModal', () => closeAddonsModal()],
];

function closeOpenDialogOnEscape() {
  if (closeInAppMoreMenu(true)) return true;
  const trailer = document.getElementById('detailTrailer');
  if (trailer && !trailer.hidden) {
    closeDetailTrailer();
    return true;
  }
  for (const [id, close] of ESCAPE_DIALOGS) {
    const dialog = document.getElementById(id);
    if (dialog && (dialog.style.display === 'flex' || dialog.classList.contains('active'))) {
      close();
      return true;
    }
  }
  return false;
}

window.addEventListener('keydown', (e) => {
  if (document.getElementById('playbackHelp')?.open) return;
  const overlay = document.getElementById('inAppPlayerOverlay');
  const isPlayerActive = overlay && overlay.classList.contains('active');
  const isInput = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement ? document.activeElement.tagName : '');

  if (!isPlayerActive) {
    // Global macOS & keyboard shortcuts
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      const si = document.getElementById('searchInput');
      if (si) { si.focus(); si.select(); openSearchPanel(); }
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === ',') {
      e.preventDefault();
      openConfig();
      return;
    }
    if (e.key === '/' && !isInput) {
      e.preventDefault();
      const si = document.getElementById('searchInput');
      if (si) { si.focus(); si.select(); openSearchPanel(); }
      return;
    }
    const csm = document.getElementById('caseModal');
    const isCaseOpen = Boolean(csm && (csm.classList.contains('active') || csm.style.display === 'flex'));
    if (isCaseOpen && !isInput) {
      if (e.key === ' ') {
        e.preventDefault();
        if (caseSpinState && !caseSpinState.finished) {
          skipCaseSpin();
        } else {
          surpriseMeMedia(activeCasePoolMode || 'catalog');
        }
        return;
      }
      if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        surpriseMeMedia(activeCasePoolMode || 'catalog');
        return;
      }
    }
    if (e.key === '?' && !isInput) {
      e.preventDefault();
      toggleShortcutsModal();
      return;
    }
    // ⌘[ (Ctrl+[) or Alt+←: one screen back.
    if (!isInput && (((e.metaKey || e.ctrlKey) && e.key === '[') || (e.altKey && e.key === 'ArrowLeft')) && canGoBack()) {
      e.preventDefault();
      goBack();
      return;
    }
    if (e.key === 'Escape') {
      if (closeOpenDialogOnEscape()) return;
      const tonight = document.getElementById('tonightModal');
      if (tonight && tonight.style.display === 'flex') {
        closeTonightModal();
        return;
      }
      const sm = document.getElementById('shortcutsModal');
      if (sm && sm.style.display === 'flex') {
        toggleShortcutsModal();
        return;
      }
      const lbm = document.getElementById('letterboxdModal');
      if (lbm && (lbm.classList.contains('active') || lbm.style.display === 'flex')) {
        closeLetterboxdModal();
        return;
      }
      if (isCaseOpen) {
        closeCaseModal();
        return;
      }
      const tm = document.getElementById('trailerModal');
      if (tm && (tm.classList.contains('active') || tm.style.display === 'flex')) {
        closeTrailerModal();
        return;
      }
      const setP = document.getElementById('settingsPanel');
      if (setP && setP.style.display === 'block') {
        closeConfig();
        return;
      }
      if (isInput && document.activeElement) {
        document.activeElement.blur();
        return;
      }
      const dp = document.getElementById('detailPanel');
      if (dp && dp.style.display === 'block') {
        closeFiche();
        return;
      }
      if (canGoBack()) {
        goBack();
        return;
      }
      (async () => {
        let isFs = document.body.classList.contains('is-fullscreen') || Boolean(document.fullscreenElement || document.webkitFullscreenElement);
        if (!isFs && window.pywebview && window.pywebview.api && typeof window.pywebview.api.is_fullscreen === 'function') {
          try { isFs = await window.pywebview.api.is_fullscreen(); } catch (_) {}
        }
        if (isFs) {
          await exitInAppFullscreen();
        }
      })();
      return;
    }
    return;
  }

  if (isInput) return;
  if (handleInAppMenuKey(e)) return;

  resetInAppIdleTimer();

  if (e.key === ' ' || e.key === 'k' || e.key === 'K') {
    e.preventDefault();
    toggleInAppPlay();
  } else if (e.key === 'ArrowLeft' || e.key === 'j' || e.key === 'J') {
    e.preventDefault();
    inAppSeekRel(-10);
  } else if (e.key === 'ArrowRight' || e.key === 'l' || e.key === 'L') {
    e.preventDefault();
    inAppSeekRel(10);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    const v = document.getElementById('inAppVideo');
    if (v) setInAppVolume(v.volume + 0.05, true);
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    const v = document.getElementById('inAppVideo');
    if (v) setInAppVolume(v.volume - 0.05, true);
  } else if (e.key === 'f' || e.key === 'F') {
    e.preventDefault();
    toggleInAppFullscreen();
  } else if (e.key === 'm' || e.key === 'M') {
    e.preventDefault();
    toggleInAppMute();
  } else if (e.key === 'Escape') {
    e.preventDefault();
    if (closeOpenDialogOnEscape()) return;
    (async () => {
      let isFs = document.body.classList.contains('is-fullscreen') || Boolean(document.fullscreenElement || document.webkitFullscreenElement);
      if (!isFs && window.pywebview && window.pywebview.api && typeof window.pywebview.api.is_fullscreen === 'function') {
        try { isFs = await window.pywebview.api.is_fullscreen(); } catch (_) {}
      }
      if (isFs) {
        await exitInAppFullscreen();
      } else {
        closeInAppPlayer();
      }
    })();
  } else if (e.key === '>' || e.key === 'n' || e.key === 'N') {
    e.preventDefault();
    inAppNextTrack();
  } else if (e.key === '<' || e.key === 'p' || e.key === 'P') {
    e.preventDefault();
    inAppPrevTrack();
  } else if (e.key === 'b' || e.key === 'B') {
    e.preventDefault();
    cycleInAppClarity();
  } else if (e.key === 'c' || e.key === 'C') {
    e.preventDefault();
    cycleInAppSubtitles();
  } else if (e.key === 'v' || e.key === 'V') {
    e.preventDefault();
    cycleInAppAudioBoost();
  } else if (e.key === 'i' || e.key === 'I') {
    e.preventDefault();
    toggleInAppPiP();
  } else if (e.key === 's' || e.key === 'S') {
    e.preventDefault();
    skipInAppIntro();
  } else if (e.key === ']') {
    e.preventDefault();
    cycleInAppSpeed(1);
  } else if (e.key === '[') {
    e.preventDefault();
    cycleInAppSpeed(-1);
  } else if (e.key === 'z' || e.key === 'Z') {
    e.preventDefault();
    adjustInAppSubDelay(-0.25);
  } else if (e.key === 'x' || e.key === 'X') {
    e.preventDefault();
    adjustInAppSubDelay(0.25);
  } else if (e.key === 'e' || e.key === 'E') {
    e.preventDefault();
    switchToExternalPlayer();
  } else if (e.key === '?') {
    e.preventDefault();
    toggleShortcutsModal();
  } else if (e.key === 'g' || e.key === 'G') {
    e.preventDefault();
    adjustInAppSubDelay(-0.5);
  } else if (e.key === 'h' || e.key === 'H') {
    e.preventDefault();
    adjustInAppSubDelay(0.5);
  }
});

let offlineDownloads = {};
let downloadPollTimer = null;

// A 4K film often weighs 20 Go or more: show what the disk can still take.
function offlineFreeText(bytes) {
  const free = Number(bytes);
  if (bytes === null || bytes === undefined || !Number.isFinite(free) || free < 0) return '';
  return ` · Libre : ${offlineSize(free)}`;
}

function offlineSize(bytes) {
  if (!Number.isFinite(Number(bytes)) || Number(bytes) <= 0) return (window.KINO_LANG || 'fr') === 'fr' ? '0 octet' : '0 B';
  const units = (window.KINO_LANG || 'fr') === 'fr' ? ['octets', 'Ko', 'Mo', 'Go', 'To'] : ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Number(bytes), unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${unit === 0 || value >= 10 ? value.toFixed(0) : value.toFixed(1).replace('.', ',')} ${units[unit]}`;
}

function offlineStatus(item) {
  const errors = {
    app_closed: 'Interrompu par la fermeture de KINO', network_interrupted: 'Connexion interrompue',
    disk_full: 'Espace disque insuffisant', missing_file: 'Fichier déplacé ou supprimé',
    missing_partial: 'Fichier partiel introuvable', url_expired: 'Lien expiré : choisissez une nouvelle source',
    resume_unavailable: 'Reprise refusée par le serveur : recommencez',
    restart_required: 'Le fichier distant a changé : recommencez',
    file_error: 'Erreur du fichier local', invalid_response: 'Réponse vidéo invalide',
    source_unavailable: 'Aucune source disponible pour cet épisode',
    unknown_error: 'Erreur de téléchargement'
  };
  if (item.error_code) return errors[item.error_code] || 'Téléchargement interrompu';
  return {
    queued: 'En attente', waiting: 'En file d’attente', downloading: 'Téléchargement en cours',
    paused: 'En pause', interrupted: 'Interrompu', completed: 'Disponible hors ligne', failed: 'Échec'
  }[item.status] || 'État inconnu';
}

// What an offline file shows: French title, film or episode, the real file name
// (KINO stores it behind the download id), poster and how far it was watched.
function offlineDisplay(item) {
  const media = item.media || {};
  const hasTitle = Boolean(media.id && media.name);
  const title = hasTitle && typeof displayTitle === 'function' ? displayTitle(media) : (item.title || item.filename || '');
  const code = media.season && media.episode ? `S${String(media.season).padStart(2, '0')}E${String(media.episode).padStart(2, '0')}` : '';
  const kind = code ? `Série · ${code}` : (media.type === 'series' ? 'Série' : `Film${media.year ? ` · ${media.year}` : ''}`);
  const filename = String(item.filename || '').replace(new RegExp(`^${String(item.id || '').replace(/[^0-9a-f]/gi, '')}_`), '');
  let watch = '';
  const history = typeof userHistory !== 'undefined' && media.id ? (userHistory || []).find(entry => entry.id === media.id) : null;
  if (history) {
    const position = code ? (history.ep_positions?.[code] || history.ep_positions?.[`s${media.season}e${media.episode}`]) : null;
    const pct = Number(code ? (position?.pct || 0) : (history.progress_pct || 0));
    const done = code ? pct >= 85 || (history.watched_episodes || []).includes(`s${media.season}e${media.episode}`) : Boolean(history.completed || pct >= 85);
    watch = done ? '✓ Vu' : pct > 1 ? `${Math.round(pct)} % vu` : '';
  }
  return {
    title, kind, filename, watch,
    poster: item.poster || media.poster || '',
    titleAttrs: hasTitle && typeof titleAttrs === 'function' ? titleAttrs(media) : '',
  };
}

function renderOfflineLibraryData(data) {
  const grid = document.getElementById('postersGrid');
  if (!grid || activeTab !== 'offline') return;
  const items = Object.values(data.downloads || {});
  const available = items.filter(item => item.available).length;
  const badge = document.getElementById('offlineCount');
  if (badge) badge.textContent = available ? String(available) : '';
  grid.innerHTML = `<div class="offline-summary"><strong>${available} fichier${available > 1 ? 's' : ''} prêt${available > 1 ? 's' : ''}</strong><span>Utilisé sur ce Mac : ${offlineSize(data.used_bytes)}${offlineFreeText(data.free_bytes)}</span></div>` +
    (items.length ? items.map(item => {
      const id = item.id;
      const status = escapeHtml(offlineStatus(item));
      const actions = item.available
        ? `<button class="btn" onclick="playOfflineDownload('${id}')">Lancer</button>`
        : ['queued', 'downloading', 'waiting'].includes(item.status)
          ? `<button class="btn btn-secondary" onclick="downloadAction('${id}', 'pause')">Pause</button>`
          : `<button class="btn btn-secondary" onclick="downloadAction('${id}', 'resume')">Reprendre</button><button class="btn btn-secondary" onclick="downloadAction('${id}', 'restart')">Recommencer</button>`;
      const canRemove = !['queued', 'downloading'].includes(item.status);
      const shown = offlineDisplay(item);
      return `<article class="offline-item">
        <div class="offline-poster">${shown.poster ? `<img src="${escapeHtml(shown.poster)}" alt="" loading="lazy" onerror="this.remove()">` : ''}<span aria-hidden="true">▷</span></div>
        <div class="offline-info"><strong${shown.titleAttrs}>${escapeHtml(shown.title)}</strong><span class="offline-kind">${escapeHtml(shown.kind)}${shown.watch ? ` · ${escapeHtml(shown.watch)}` : ''}</span><span class="offline-file">${escapeHtml(shown.filename)}</span>
          <span class="offline-state">${status}${item.has_subtitles ? ' · Sous-titres sauvegardés' : ''}</span>
          <span>${offlineSize(item.bytes_done)}${item.bytes_total ? ' / ' + offlineSize(item.bytes_total) : ''}</span>
          ${item.status === 'downloading' ? `<div class="progress-bar"><div class="progress-fill" style="width:${Math.max(0, Math.min(100, item.progress || 0))}%"></div></div>` : ''}
        </div>
        <div class="offline-actions">${actions}${canRemove ? `<button class="btn btn-secondary" onclick="removeOfflineDownload('${id}')">Retirer</button>` : ''}</div>
      </article>`;
    }).join('') : (networkOnline
      ? '<div class="library-empty"><h3>Aucun fichier hors ligne</h3><p>Depuis une source prête à lire ou le lecteur, choisissez « Garder hors ligne ». Les fichiers terminés apparaîtront ici.</p></div>'
      : '<div class="library-empty"><h3>Aucun fichier hors ligne sur ce Mac</h3><p>Sans internet, KINO ne peut rien lire pour le moment. Quand la connexion reviendra, choisissez « Garder hors ligne » sur une source prête à lire ou dans le lecteur.</p></div>'));
}

// --- A whole season for offline: the episodes already out, one after another ---
function seasonEpisodesOut(season, today = new Date().toISOString().slice(0, 10)) {
  const seen = new Set();
  return (seriesMetaVideos || [])
    .filter(video => Number(video.season) === Number(season) && Number(video.episode) > 0
      && String(video.released || '').slice(0, 10) && String(video.released).slice(0, 10) <= today)
    .map(video => Number(video.episode))
    .filter(episode => !seen.has(episode) && seen.add(episode))
    .sort((a, b) => a - b);
}

async function downloadSeasonOffline(button) {
  if (!currentMedia?.id) return;
  const season = Number(document.getElementById('seasonSelect')?.value || 1);
  const episodes = seasonEpisodesOut(season);
  const name = displayTitle(currentMedia) || currentMedia.name || 'cette série';
  if (!episodes.length) {
    window.alert(`Aucun épisode de la saison ${season} n’est encore sorti.`);
    return;
  }
  let free = '';
  try {
    const data = await api('/api/downloads');
    if (Number(data.free_bytes) > 0) free = ` Il reste ${offlineSize(data.free_bytes)} sur ce Mac.`;
  } catch (error) {}
  const count = `${episodes.length} épisode${episodes.length > 1 ? 's' : ''}`;
  if (!window.confirm(`Télécharger ${episodes.length > 1 ? 'les ' : 'l’'}${count} sorti${episodes.length > 1 ? 's' : ''} de la saison ${season} de « ${name} » pour les regarder hors ligne ?\n\nIls se téléchargent l’un après l’autre, même si KINO est fermé puis rouvert. Un épisode pèse souvent 1 à 4 Go.${free}`)) return;
  const label = button ? button.textContent : '';
  if (button) { button.disabled = true; button.textContent = 'Ajout…'; }
  try {
    const result = await api('/api/download-season', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ imdb_id: currentMedia.id, season, episodes, name: currentMedia.name || name,
        year: currentMedia.year || '', poster: currentMedia.poster || '' }),
    });
    const parts = [];
    if (result.queued) parts.push(`${result.queued} en file d’attente`);
    if (result.duplicates) parts.push(`${result.duplicates} déjà hors ligne ou en cours`);
    if (button) button.textContent = `✓ ${parts.join(' · ') || 'Rien à ajouter'}`;
    if (typeof pollDownloads === 'function') pollDownloads();
  } catch (error) {
    window.alert(`Saison hors ligne impossible : ${error.message}`);
    if (button) button.textContent = label;
  } finally {
    if (button) {
      button.disabled = false;
      setTimeout(() => { button.textContent = label; }, 4000);
    }
  }
}

async function playOfflineDownload(id) {
  const item = offlineDownloads[id];
  if (!item?.available) { await pollDownloads(); return; }
  const media = {...item.media, offline_id: id};
  const history = userHistory.find(entry => entry.id === media.id);
  const s = media.season, e = media.episode;
  const resumeSec = s && e ? (history?.ep_positions?.[`s${s}e${e}`]?.position || 0) : (history?.position || 0);
  const title = media.name ? `${media.name}${s && e ? ` — S${String(s).padStart(2, '0')}E${String(e).padStart(2, '0')}` : ''}` : item.filename;
  if (window.NativePlayer?.available && window.NativePlayer.enabled) {
    try {
      await window.NativePlayer.open(item, media, resumeSec, title,
        info => openInAppPlayer(item.url, title, null, media, info?.position || resumeSec, item.filename));
      return;
    } catch (error) {
      // The native player could not start: the web player takes over.
    }
  }
  await openInAppPlayer(item.url, title, null, media, resumeSec, item.filename);
}

async function downloadAction(id, action) {
  const route = {pause: '/api/download-cancel', resume: '/api/download-resume', restart: '/api/download-restart'}[action];
  if (!route) return;
  try {
    await api(route, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({dl_id: id})});
  } catch (error) {
    window.alert(error.message);
  }
  await pollDownloads();
}

async function removeOfflineDownload(id) {
  const item = offlineDownloads[id];
  if (!item) return;
  const description = item.available ? 'le fichier vidéo, son affiche et les sous-titres sauvegardés' : 'le fichier partiel et ses données hors ligne';
  if (!window.confirm(`Retirer « ${item.title || item.filename} » ? KINO supprimera ${description}. La fiche, les favoris et la progression resteront.`)) return;
  try {
    await api('/api/download-remove', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({dl_id: id})});
  } catch (error) {
    window.alert(error.message);
  }
  await pollDownloads();
}

async function pollDownloads() {
  clearTimeout(downloadPollTimer);
  let data;
  try { data = await api('/api/downloads'); }
  catch (error) { return; }
  offlineDownloads = data.downloads || {};
  const items = Object.values(offlineDownloads);
  const active = items.filter(item => ['queued', 'downloading'].includes(item.status));
  const badge = document.getElementById('offlineCount');
  if (badge && activeTab !== 'offline') {
    const count = items.filter(item => item.available).length;
    badge.textContent = count ? String(count) : '';
  }
  const panel = document.getElementById('downloadsPanel');
  if (panel) panel.style.display = active.length && activeTab !== 'offline' ? 'block' : 'none';
  const list = document.getElementById('downloadsList');
  if (list) list.innerHTML = active.map(item => `<div class="offline-active-row"><strong>${escapeHtml(item.title || item.filename)}</strong><span>${offlineSize(item.bytes_done)}${item.bytes_total ? ' / ' + offlineSize(item.bytes_total) : ''} · ${Math.round(item.progress || 0)}%</span><button class="btn btn-secondary" onclick="downloadAction('${item.id}', 'pause')">Pause</button></div>`).join('');
  renderOfflineLibraryData(data);
  if (active.length) downloadPollTimer = setTimeout(pollDownloads, 1000);
}

function initSettingsSectionNavigation() {
  const links = [...document.querySelectorAll('.settings-index-item')];
  const sections = links.map(link => document.getElementById(link.dataset.settingsTarget));
  if (!links.length || sections.some(section => !section)) return;
  const select = selected => {
    links.forEach(link => {
      const active = link === selected;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  };
  links.forEach(link => link.addEventListener('click', () => {
    const section = document.getElementById(link.dataset.settingsTarget);
    if (!section) return;
    select(link);
    section.scrollIntoView({ block: 'start', behavior: 'auto' });
  }));
  refreshSettingsSectionNavigation = () => {
    if (document.body.dataset.activeTab !== 'settings') return;
    const topLimit = window.innerWidth <= 760 ? 180 : 105;
    let current = 0;
    sections.forEach((section, index) => {
      if (section.getBoundingClientRect().top <= topLimit) current = index;
    });
    const pageHeight = document.documentElement.scrollHeight;
    if (pageHeight > window.innerHeight && window.scrollY + window.innerHeight >= pageHeight - 4) {
      current = sections.length - 1;
    }
    select(links[current]);
  };
  let pending = false;
  window.addEventListener('scroll', () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      refreshSettingsSectionNavigation();
    });
  }, { passive: true });
  window.addEventListener('resize', refreshSettingsSectionNavigation);
}

let kinoUpdatePendingAction = '';
let kinoUpdatePollTimer = null;
let kinoUpdateRetryTimer = null;

function kinoUpdateBridge() {
  return window.pywebview?.api || null;
}

function kinoUpdateMessage(message) {
  const el = document.getElementById('kinoUpdateMessage');
  if (el) el.textContent = message || '';
}

function renderKinoUpdate(state) {
  if (!state) return;
  document.getElementById('kinoUpdateVersion').textContent = state.version || '—';
  const candidate = document.getElementById('kinoUpdateCandidate');
  candidate.hidden = !state.candidate;
  if (state.candidate) {
    document.getElementById('kinoUpdateCandidateTitle').textContent = `KINO ${state.candidate.version} prêt à installer`;
    document.getElementById('kinoUpdateNotes').textContent = state.candidate.notes || 'Aucune note de version.';
  }
  document.getElementById('kinoUpdateChooseBtn').hidden = !state.local_packages;
  document.getElementById('kinoUpdatePageBtn').hidden = !state.latest;
  document.getElementById('kinoUpdateRollbackRow').hidden = !state.rollback_supported;
  const rollback = document.getElementById('kinoUpdateRollbackBtn');
  rollback.disabled = !state.rollback_available;
  document.getElementById('kinoUpdateRollbackDescription').textContent = state.rollback_available
    ? `KINO ${state.rollback_version || 'précédent'} est conservé sur ce Mac.`
    : 'Aucune sauvegarde disponible.';
  const progress = Math.max(0, Math.min(100, Number(state.progress) || 0));
  document.getElementById('kinoUpdateProgressBox').hidden = progress === 0 || progress === 100;
  document.getElementById('kinoUpdateProgressBar').style.width = `${progress}%`;
  document.getElementById('kinoUpdateProgressLabel').textContent = `${state.progress_label || 'Vérification du paquet'} : ${progress}%`;
  kinoUpdateMessage(state.message || (state.latest && !state.candidate
    ? `KINO ${state.latest.version} est disponible : cliquez sur « Rechercher une mise à jour ».` : ''));
}

async function refreshKinoUpdate() {
  const bridge = kinoUpdateBridge();
  if (!bridge) {
    kinoUpdateMessage('Les mises à jour se font depuis l’application KINO.');
    return;
  }
  try { renderKinoUpdate(await bridge.update_status()); }
  catch (error) { kinoUpdateMessage(error.message || String(error)); }
}

async function checkKinoUpdates() {
  const bridge = kinoUpdateBridge();
  if (!bridge) {
    kinoUpdateMessage('La recherche est disponible dans l’application KINO.');
    return;
  }
  kinoUpdateMessage('Recherche d’une nouvelle version…');
  clearInterval(kinoUpdatePollTimer);
  kinoUpdatePollTimer = setInterval(refreshKinoUpdate, 400);
  try {
    const result = await bridge.check_for_updates();
    renderKinoUpdate(result);
  } catch (error) {
    kinoUpdateMessage(error.message || String(error));
  } finally {
    clearInterval(kinoUpdatePollTimer);
    kinoUpdatePollTimer = null;
    await refreshKinoUpdate();
  }
}

async function chooseKinoUpdate() {
  const bridge = kinoUpdateBridge();
  if (!bridge) {
    kinoUpdateMessage('Ouvrez KINO.app pour choisir un paquet local.');
    return;
  }
  kinoUpdateMessage('Vérification du paquet signé…');
  clearInterval(kinoUpdatePollTimer);
  kinoUpdatePollTimer = setInterval(refreshKinoUpdate, 400);
  try {
    const result = await bridge.choose_update_package();
    if (!result.cancelled) renderKinoUpdate(result);
    else kinoUpdateMessage('Sélection annulée.');
  } catch (error) {
    kinoUpdateMessage(error.message || String(error));
  } finally {
    clearInterval(kinoUpdatePollTimer);
    kinoUpdatePollTimer = null;
    await refreshKinoUpdate();
  }
}

async function requestKinoUpdateAction(action) {
  const bridge = kinoUpdateBridge();
  if (!bridge) {
    kinoUpdateMessage('Ouvrez KINO.app pour cette opération.');
    return;
  }
  if (document.getElementById('inAppPlayerOverlay')?.classList.contains('active')) {
    kinoUpdatePendingAction = action;
    kinoUpdateMessage('Installation programmée à la fermeture du lecteur.');
    return;
  }
  try {
    const result = action === 'install' ? await bridge.install_update() : await bridge.rollback_update();
    kinoUpdateMessage(result.message || 'KINO va redémarrer.');
    if (result.deferred) {
      kinoUpdatePendingAction = action;
      clearTimeout(kinoUpdateRetryTimer);
      kinoUpdateRetryTimer = setTimeout(() => {
        if (kinoUpdatePendingAction === action
            && !document.getElementById('inAppPlayerOverlay')?.classList.contains('active')) {
          kinoUpdatePendingAction = '';
          requestKinoUpdateAction(action);
        }
      }, 5500);
    }
  } catch (error) {
    kinoUpdateMessage(error.message || String(error));
  }
}

function installKinoUpdate() { return requestKinoUpdateAction('install'); }
async function openKinoReleasePage() {
  const bridge = kinoUpdateBridge();
  if (bridge?.open_release_page) {
    try { await bridge.open_release_page(); } catch (error) { kinoUpdateMessage(error.message || String(error)); }
  }
}
function rollbackKinoUpdate() { return requestKinoUpdateAction('rollback'); }

function initHeaderMenuKeyboard() {
  document.querySelectorAll('#headerDropdownMenu .header-dropdown-item').forEach(item => {
    item.setAttribute('role', 'button');
    item.setAttribute('tabindex', '0');
    item.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        item.click();
      }
    });
  });
  document.getElementById('headerDropdownMenu')?.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      closeHeaderMenu();
      document.getElementById('headerSettingsBtn')?.focus();
    }
  });
}

initSettingsSectionNavigation();
initSettingsAutosave();
window.addEventListener('pywebviewready', refreshKinoUpdate);
setTimeout(refreshKinoUpdate, 800);
// KINO asks GitHub for a new version 20 s after start: show the answer in Settings.
setTimeout(refreshKinoUpdate, 30000);
// Clickable cards are <div onclick> in many templates: reach them with Tab and open
// them with Enter or Space, including cards rendered later.
const KEYBOARD_CARD_SELECTOR = '.poster-card, .card, .search-suggest-item';
const KEYBOARD_CARD_LABEL = '.poster-title, .card-title, .search-suggest-title';

function enhanceKeyboardCards(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return;
  const cards = [...root.querySelectorAll(KEYBOARD_CARD_SELECTOR)];
  if (typeof root.matches === 'function' && root.matches(KEYBOARD_CARD_SELECTOR)) cards.unshift(root);
  for (const card of cards) {
    if (card.hasAttribute('tabindex') || !card.hasAttribute('onclick')) continue;
    card.setAttribute('tabindex', '0');
    card.setAttribute('role', 'button');
    const label = card.querySelector(KEYBOARD_CARD_LABEL);
    const text = label ? label.textContent.trim() : '';
    if (text) card.setAttribute('aria-label', text);
  }
}

// Arrow keys move between cards of the same grid: left/right follow card order,
// up/down pick the nearest card of the adjacent row on screen.
function neighbourKeyboardCard(card, key) {
  const container = card.parentElement;
  if (!container) return null;
  const cards = [...container.children].filter(el => el !== null && typeof el.matches === 'function'
    && el.matches(KEYBOARD_CARD_SELECTOR) && el.getAttribute('role') === 'button');
  const index = cards.indexOf(card);
  if (index < 0) return null;
  if (key === 'ArrowRight') return cards[index + 1] || null;
  if (key === 'ArrowLeft') return cards[index - 1] || null;
  const from = card.getBoundingClientRect();
  const fromX = from.left + from.width / 2;
  const fromY = from.top + from.height / 2;
  let best = null;
  let bestScore = Infinity;
  for (const other of cards) {
    if (other === card) continue;
    const box = other.getBoundingClientRect();
    const dy = (box.top + box.height / 2) - fromY;
    if ((key === 'ArrowDown' && dy <= from.height / 2) || (key === 'ArrowUp' && dy >= -from.height / 2)) continue;
    const score = Math.abs(dy) * 10000 + Math.abs((box.left + box.width / 2) - fromX);
    if (score < bestScore) { best = other; bestScore = score; }
  }
  return best;
}

// Smaller clickable labels (genre and source filters, cast names, account pill)
// are <span>/<div onclick> too: same treatment, without arrow navigation.
const KEYBOARD_CONTROL_SELECTOR = '.chip[onclick], .badge[onclick], .person-link, #userBadge, .logo[onclick]';

function enhanceKeyboardControls(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return;
  const controls = [...root.querySelectorAll(KEYBOARD_CONTROL_SELECTOR)];
  if (typeof root.matches === 'function' && root.matches(KEYBOARD_CONTROL_SELECTOR)) controls.unshift(root);
  for (const control of controls) {
    if (control.hasAttribute('tabindex') || /^(BUTTON|A|INPUT|SELECT)$/.test(control.tagName || '')) continue;
    control.setAttribute('tabindex', '0');
    control.setAttribute('role', 'button');
    if (control.classList && control.classList.contains('chip')) {
      control.setAttribute('aria-pressed', String(control.classList.contains('active')));
    }
  }
}

// Filter chips are toggles: keep aria-pressed in step with the visible state.
function syncChipPressed(chips, active) {
  for (const chip of chips) {
    if (chip.getAttribute('role') === 'button') chip.setAttribute('aria-pressed', String(chip === active));
  }
}

function activateKeyboardCard(event) {
  const target = event.target;
  if (target && typeof target.matches === 'function' && target.matches(KEYBOARD_CONTROL_SELECTOR)
      && !target.matches(KEYBOARD_CARD_SELECTOR) && target.getAttribute('role') === 'button') {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      target.click();
    }
    return;
  }
  const card = target;
  if (!card || typeof card.matches !== 'function' || !card.matches(KEYBOARD_CARD_SELECTOR)) return;
  if (card.getAttribute('role') !== 'button') return;
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    card.click();
    return;
  }
  if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
  const next = neighbourKeyboardCard(card, event.key);
  if (!next) return;
  event.preventDefault();
  next.focus();
  if (typeof next.scrollIntoView === 'function') next.scrollIntoView({ block: 'nearest' });
}

function initKeyboardCards() {
  enhanceKeyboardCards(document.body);
  enhanceKeyboardControls(document.body);
  new MutationObserver(records => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1) continue;
        enhanceKeyboardCards(node);
        enhanceKeyboardControls(node);
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
  document.addEventListener('keydown', activateKeyboardCard);
}

// --- Retour: the screens visited, to come back one step (button, Échap, ⌘[, Alt+←, mouse) ---
// A screen is a base — a tab, a search, an actor's page — and maybe an open fiche,
// with how far the page was scrolled when it was left.
const NAV_LIMIT = 50;
const NAV_TAB_LABELS = {
  movies: 'Films', series: 'Séries', anime: 'Anime', classics: 'Classiques', community: 'Explorer', watchlist: 'Ma Liste',
  history: 'En cours', myseries: 'Mes séries', calendar: 'Calendrier', watched: 'Déjà vus', offline: 'Hors ligne', customlists: 'Listes', rdcloud: 'Cloud',
  stats: 'Statistiques', settings: 'Paramètres',
};
let navStack = [];
let navRestoring = false;

function navScreen(base, fiche = null) {
  const { kind, key, tab, query, mode, name } = base;
  return { kind, key, tab, query, mode, name, fiche: fiche ? { ...fiche } : null, scrollY: 0 };
}

function sameBase(a, b) {
  return Boolean(a && b) && a.kind === b.kind && a.key === b.key;
}

function sameScreen(a, b) {
  return sameBase(a, b) && (a.fiche?.id || '') === (b.fiche?.id || '');
}

// A screen was opened: a new step, the same screen again, or the previous one by another way.
function navNote(screen) {
  if (navRestoring) return;
  const top = navStack[navStack.length - 1];
  if (top) top.scrollY = window.scrollY || 0;
  if (sameScreen(screen, top)) {
    navStack[navStack.length - 1] = { ...screen, scrollY: top.scrollY };
  } else if (sameScreen(screen, navStack[navStack.length - 2])) {
    navStack.pop();  // closing a fiche, leaving Paramètres: one step back
  } else {
    navStack.push(screen);
    if (navStack.length > NAV_LIMIT) navStack.shift();
  }
  renderNavBack();
}

function noteTab(tab) { navNote(navScreen({ kind: 'tab', key: tab, tab })); }
function noteSearch(query, mode) { navNote(navScreen({ kind: 'search', key: `${mode}:${query}`, query, mode })); }
function notePerson(name) { navNote(navScreen({ kind: 'person', key: name, name })); }

function noteFiche(media) {
  if (!media || !media.id) return;
  const top = navStack[navStack.length - 1];
  navNote(navScreen(top || { kind: 'tab', key: activeTab, tab: activeTab }, media));
}

function navLabel(screen) {
  if (!screen) return '';
  if (screen.fiche) return displayTitle(screen.fiche);
  if (screen.kind === 'search') return `Résultats pour « ${screen.query} »`;
  if (screen.kind === 'person') return screen.name;
  return NAV_TAB_LABELS[screen.tab] || 'l’écran précédent';
}

function canGoBack() {
  return navStack.length > 1 && !navRestoring;
}

function renderNavBack() {
  const button = document.getElementById('navBackBtn');
  if (!button) return;
  const previous = navStack[navStack.length - 2];
  button.hidden = !previous;
  if (!previous) return;
  const label = `Retour : ${navLabel(previous)}`;
  button.title = `${label} (Échap)`;
  button.setAttribute('aria-label', label);
}

async function showNavBase(screen) {
  if (screen.kind === 'search') {
    document.getElementById('searchType').value = screen.mode;
    document.getElementById('searchInput').value = screen.query;
    updateSearchClearBtn();
    await runSearch();
  } else if (screen.kind === 'person') {
    await openPerson(screen.name);
  } else {
    await switchTab(screen.tab);
  }
}

// A grid that loads again grows after a moment: the scroll is set again once.
function restoreScroll(y) {
  const top = Math.max(0, Number(y) || 0);
  window.scrollTo({ top, behavior: 'auto' });
  setTimeout(() => { if (Math.abs((window.scrollY || 0) - top) > 4) window.scrollTo({ top, behavior: 'auto' }); }, 450);
}

async function goBack() {
  if (!canGoBack()) return false;
  const leaving = navStack.pop();
  const target = navStack[navStack.length - 1];
  navRestoring = true;
  try {
    const baseShown = sameBase(leaving, target) && !(target.kind === 'tab' && activeTab !== target.tab);
    if (!baseShown) await showNavBase(target);
    if (target.fiche) await selectMedia(target.fiche);
    else if (document.getElementById('detailPanel')?.style.display === 'block') closeDetailPanel();
  } finally {
    navRestoring = false;
    renderNavBack();
  }
  if (!target.fiche) restoreScroll(target.scrollY);
  return true;
}

// The fiche's × and Échap: back to the screen under it, where it was scrolled.
function closeFiche() {
  const top = navStack[navStack.length - 1];
  if (top?.fiche && canGoBack() && sameScreen(navStack[navStack.length - 2], navScreen(top))) {
    goBack();
    return;
  }
  closeDetailPanel();
  if (top?.fiche) navNote(navScreen(top));
}

window.addEventListener('mouseup', event => {
  // The « back » button of a mouse.
  if (event.button !== 3 || document.getElementById('inAppPlayerOverlay')?.classList.contains('active')) return;
  if (canGoBack()) {
    event.preventDefault();
    goBack();
  }
});

// --- Sans internet: the offline files first, plain words for the rest ---
let networkOnline = true;
let networkRetryTimer = null;
let networkBackTimer = null;
const NETWORK_RETRY_MS = 20000;
const NETWORK_OFFLINE_TEXT = 'Vos fichiers hors ligne restent disponibles. Le catalogue, la recherche, les sources Debrid, le Cloud, Trakt et la synchronisation reviendront avec la connexion.';

// KINO's server knows (it checked at launch); the browser's own view is the fallback.
async function refreshNetwork(force = false) {
  let online = navigator.onLine !== false;
  try {
    const state = await api(`/api/network${force ? '?refresh=1' : ''}`);
    if (typeof state.online === 'boolean') online = state.online;
  } catch (error) {
    // KINO's server did not answer: keep the browser's view.
  }
  setNetworkOnline(online);
  return online;
}

function setNetworkOnline(online) {
  const changed = online !== networkOnline;
  networkOnline = online;
  document.body.classList.toggle('is-offline', !online);
  clearTimeout(networkRetryTimer);
  // Offline: check again now and then, never while the window is hidden.
  if (!online) networkRetryTimer = setTimeout(() => { if (!document.hidden) refreshNetwork(true); }, NETWORK_RETRY_MS);
  if (!changed) return;
  renderNetworkBanner(online ? 'back' : 'offline');
  if (online) networkCameBack();
}

function noteNetworkOffline() {
  if (networkOnline) setNetworkOnline(false);
}

function renderNetworkBanner(mode) {
  const banner = document.getElementById('networkBanner');
  if (!banner) return;
  clearTimeout(networkBackTimer);
  banner.classList.toggle('is-back', mode === 'back');
  document.getElementById('networkBannerTitle').textContent = mode === 'back' ? 'Connexion rétablie' : 'Sans internet';
  document.getElementById('networkBannerText').textContent = mode === 'back'
    ? 'Le catalogue, la recherche et les sources sont de retour.'
    : NETWORK_OFFLINE_TEXT;
  banner.hidden = false;
  if (mode === 'back') networkBackTimer = setTimeout(() => { banner.hidden = true; }, 5000);
}

async function retryNetwork() {
  const button = document.getElementById('networkRetryButton');
  if (button) { button.disabled = true; button.textContent = 'Vérification…'; }
  try {
    const online = await refreshNetwork(true);
    if (!online && button) button.textContent = 'Toujours sans internet';
    await new Promise(resolve => setTimeout(resolve, online ? 0 : 1500));
  } finally {
    if (button) { button.disabled = false; button.textContent = 'Réessayer'; }
  }
}

// Back online: what could not load loads now; the user stays where they are.
function networkCameBack() {
  checkConfig();
  if (['movies', 'series', 'anime', 'classics'].includes(activeTab) && !searchResultsShown) switchTab(activeTab);
  else if (activeTab === 'offline') pollDownloads();
}

function needsInternetHtml(what) {
  return `<div class="library-empty"><h3>${escapeHtml(what)} a besoin d’internet</h3><p>Vos fichiers hors ligne restent disponibles sur ce Mac.</p><button type="button" class="btn" onclick="switchTab('offline')">Voir mes fichiers hors ligne</button></div>`;
}

function showOfflineCatalogue() {
  hideHomeRows();
  for (const id of ['heroSpotlight', 'genreFilters', 'catalogSortWrap', 'loadMoreWrap']) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  }
  const grid = document.getElementById('postersGrid');
  if (grid) grid.innerHTML = needsInternetHtml('Le catalogue');
}

// Offline, a search looks in the files kept on this Mac.
function offlineSearchHtml(q) {
  const wanted = foldSearchText(q);
  const found = Object.entries(offlineDownloads).filter(([, item]) => item.available
    && foldSearchText(`${offlineDisplay(item).title} ${item.filename || ''}`).includes(wanted));
  if (!found.length) return needsInternetHtml('La recherche');
  return `<div class="network-results"><h3>Sans internet : dans vos fichiers hors ligne</h3>${found.map(([id, item]) => {
    const shown = offlineDisplay(item);
    return `<article class="offline-item"><div class="offline-info"><strong${shown.titleAttrs}>${escapeHtml(shown.title)}</strong><span class="offline-kind">${escapeHtml(shown.kind)}</span></div><div class="offline-actions"><button class="btn" onclick="playOfflineDownload('${id}')">Lancer</button></div></article>`;
  }).join('')}</div>`;
}

// The first tab: the catalogue, or the offline files when there is no internet.
async function openFirstTab() {
  const online = await refreshNetwork();
  switchTab(online ? 'movies' : 'offline');
}

window.addEventListener('online', () => refreshNetwork(true));
window.addEventListener('offline', () => refreshNetwork(true));
document.addEventListener('visibilitychange', () => { if (!document.hidden && !networkOnline) refreshNetwork(true); });

initHeaderMenuKeyboard();
initKeyboardCards();
initFrenchTitles();
initPosterPreviews();
initGenreStrip();
initInAppMoreMenu();
window.addEventListener("resize", updateEpisodeArrows);
checkConfig();
checkTraktStatus();
startRemotePolling();
pollDownloads();
initSearchPlatformShortcuts();
restoreCatalogView();
loadSpoilerState().finally(openFirstTab);
initResizeHandles();
syncWindowState();

// Paramètres → Langue de l'interface: each language under its own name.
(function initUiLanguageSelect() {
  const select = document.getElementById('cfgUiLanguage');
  if (!select || !window.KINO_LANGUAGES) return;
  for (const [code, name] of Object.entries(window.KINO_LANGUAGES)) select.add(new Option(name, code));
  select.value = window.KINO_LANG;
})();
