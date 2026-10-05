let allTorrents = [];
let activeFilter = '';
let activeGenre = '';
let searchDebounceTimer = null;
let currentMedia = null;
let seriesMetaVideos = [];
let userWatchlist = [];
let userHistory = [];
let activeTab = 'movies';
let configuredProviders = {};
const META_CLIENT_CACHE = new Map();
let currentSelectMediaId = null;

const PROVIDER_META = {
  realdebrid: { name: 'Real-Debrid', short: 'RD', url: 'https://real-debrid.com/apitoken', label: 'real-debrid.com/apitoken', hint: 'Clé API Real-Debrid' },
  alldebrid:  { name: 'AllDebrid',   short: 'AD', url: 'https://alldebrid.fr/apikeys/',   label: 'alldebrid.fr/apikeys',   hint: 'Clé API AllDebrid' },
  torbox:     { name: 'TorBox',      short: 'TB', url: 'https://torbox.app/settings',     label: 'torbox.app/settings',     hint: 'Clé API TorBox' },
  debridlink: { name: 'Debrid-Link', short: 'DL', url: 'https://debrid-link.fr/webapp/apikey', label: 'debrid-link.fr/webapp/apikey', hint: 'Clé API Debrid-Link' },
  premiumize: { name: 'Premiumize',  short: 'PM', url: 'https://www.premiumize.me/account', label: 'premiumize.me/account', hint: 'Clé API Premiumize' },
  megadebrid: { name: 'Mega-Debrid', short: 'MD', url: 'https://www.mega-debrid.eu/index.php?page=account', label: 'mega-debrid.eu', hint: 'Token API ou identifiant:motdepasse Mega-Debrid' }
};

function onConfigProviderChange() {
  const prov = document.getElementById('cfgProvider') ? document.getElementById('cfgProvider').value : 'realdebrid';
  const meta = PROVIDER_META[prov] || PROVIDER_META.realdebrid;
  const linkEl = document.getElementById('cfgProviderLink');
  const lblEl = document.getElementById('cfgTokenLabel');
  const inpEl = document.getElementById('cfgToken');
  if (linkEl) {
    linkEl.href = meta.url;
    linkEl.textContent = meta.label;
  }
  const hasSaved = Boolean(configuredProviders && configuredProviders[prov]);
  if (lblEl) {
    lblEl.textContent = `${meta.hint}${hasSaved ? ' (✓ enregistrée)' : ''}`;
  }
  if (inpEl) {
    inpEl.placeholder = hasSaved
      ? `Laisser vide pour conserver votre clé ${meta.name}`
      : `Collez votre ${meta.hint.toLowerCase()}...`;
  }
}

function resolveKinoUrl(url) {
  if (!url) return '';
  const base = (window.KINO_SERVER_HOST || '').replace(/\/+$/, '');
  if (base && url.startsWith('/')) {
    return `${base}${url}`;
  }
  return url;
}

async function api(path, opts) {
  if (window.StandaloneEngine && window.StandaloneEngine.active) {
    return await window.StandaloneEngine.handleApi(path, opts);
  }

  const url = resolveKinoUrl(path);
  try {
    const res = await fetch(url, opts);
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Erreur serveur');
    return data;
  } catch (err) {
    // Si le serveur local Python n'est pas joignable, basculer instantanément en mode 100% autonome
    if (window.StandaloneEngine && !window.StandaloneEngine.active) {
      window.StandaloneEngine.enableStandaloneMode();
      return await window.StandaloneEngine.handleApi(path, opts);
    }
    throw err;
  }
}

async function checkConfig() {
  try {
    const cfg = await api('/api/config');
    configuredProviders = cfg.configured_providers || {};
    const prov = cfg.debrid_provider || 'realdebrid';
    const pmeta = PROVIDER_META[prov] || PROVIDER_META.realdebrid;
    window.activeDebridProvider = prov;
    window.activeDebridName = pmeta.name;

    if (document.getElementById('cfgProvider')) {
      document.getElementById('cfgProvider').value = prov;
    }
    onConfigProviderChange();

    if (document.getElementById('logoProviderSub')) {
      document.getElementById('logoProviderSub').textContent = pmeta.name;
    }
    if (document.getElementById('tab-rdcloud')) {
      document.getElementById('tab-rdcloud').textContent = `Cloud ${pmeta.short}`;
    }
    if (document.getElementById('rdCloudHeading')) {
      document.getElementById('rdCloudHeading').textContent = `Derniers fichiers débridés sur ${pmeta.name}`;
    }

    document.getElementById('cfgDir').value = cfg.download_dir || '';
    const isMacPlatform = /Mac/i.test(navigator.platform || navigator.userAgent);
    const effPlayerMode = (cfg.player_mode && cfg.player_mode !== 'kino') ? cfg.player_mode : (isMacPlatform ? 'integrated' : 'kino');
    if (document.getElementById('cfgPlayerMode')) {
      document.getElementById('cfgPlayerMode').value = (effPlayerMode === 'external') ? 'external' : 'integrated';
    }
    if (document.getElementById('cfgPrefLang')) {
      document.getElementById('cfgPrefLang').value = cfg.pref_lang || 'vf';
    }
    if (document.getElementById('cfgPrefQuality')) {
      document.getElementById('cfgPrefQuality').value = cfg.pref_quality || '4k';
    }
    if (document.getElementById('cfgHdrMode')) {
      const hMode = cfg.hdr_mode || 'sdr_pref';
      document.getElementById('cfgHdrMode').value = (hMode === 'hdr_native') ? 'hdr_native' : 'sdr_pref';
    }
    if (document.getElementById('cfgAudioMode')) {
      document.getElementById('cfgAudioMode').value = cfg.audio_mode || 'voice_boost';
    }
    window.kinoHdrMode = cfg.hdr_mode || 'sdr_pref';
    window.kinoAudioMode = cfg.audio_mode || 'voice_boost';
    const retVal = String(cfg.rd_retention_days || 0);
    if (document.getElementById('cfgRdRetention')) {
      document.getElementById('cfgRdRetention').value = retVal;
    }
    if (document.getElementById('rdCloudRetentionSelect')) {
      document.getElementById('rdCloudRetentionSelect').value = retVal;
    }
    if (document.getElementById('cfgDiscordRpc')) {
      document.getElementById('cfgDiscordRpc').checked = cfg.discord_rpc !== false;
    }
    const currentCid = String(cfg.discord_client_id || '631379801826918400').trim();
    const presetEl = document.getElementById('cfgDiscordPreset');
    const customInp = document.getElementById('cfgDiscordClientId');
    const customBox = document.getElementById('cfgDiscordCustomBox');
    if (presetEl) {
      const known = ['631379801826918400', '645028677033132033', '968880591003082783', '938732156346314795', '608065709741965327', '926541425682829352'];
      if (known.includes(currentCid)) {
        presetEl.value = currentCid;
        if (customBox) customBox.style.display = 'none';
      } else {
        presetEl.value = 'custom';
        if (customInp) customInp.value = currentCid;
        if (customBox) customBox.style.display = 'flex';
      }
    }
    toggleDiscordRpcFields();
    window.kinoPlayerMode = effPlayerMode;
    const cfgAccTitle = document.getElementById('cfgAccountTitle');
    const cfgAccSub = document.getElementById('cfgAccountSub');
    const cfgAccBadge = document.getElementById('cfgAccountBadge');
    if (cfgAccTitle && cfgAccBadge) {
      if (cfg.user) {
        const exp = cfg.user.premium > 0 ? Math.ceil(cfg.user.premium / 86400) + ' jours' : 'Gratuit';
        cfgAccTitle.textContent = `${pmeta.name} - Connecté (@${cfg.user.username})`;
        cfgAccSub.textContent = `Abonnement premium valide (${exp} restants).`;
        cfgAccBadge.textContent = 'Actif';
        cfgAccBadge.style.color = '#4ade80';
        cfgAccBadge.style.borderColor = 'rgba(74,222,128,0.4)';
        cfgAccBadge.style.background = 'rgba(74,222,128,0.1)';
      } else if (cfg.has_token) {
        cfgAccTitle.textContent = `${pmeta.name} - Clé invalide`;
        cfgAccSub.textContent = 'La clé API renseignée est invalide ou expirée.';
        cfgAccBadge.textContent = 'Erreur Clé';
        cfgAccBadge.style.color = '#ef4444';
        cfgAccBadge.style.borderColor = 'rgba(239,68,68,0.4)';
        cfgAccBadge.style.background = 'rgba(239,68,68,0.1)';
      } else {
        cfgAccTitle.textContent = `${pmeta.name} - Non configuré`;
        cfgAccSub.textContent = 'Veuillez saisir votre clé API pour débrider vos contenus.';
        cfgAccBadge.textContent = 'Non connecté';
        cfgAccBadge.style.color = '#eab308';
        cfgAccBadge.style.borderColor = 'rgba(234,179,8,0.4)';
        cfgAccBadge.style.background = 'rgba(234,179,8,0.1)';
      }
    }

    const badge = document.getElementById('userBadge');
    if (badge) {
      if (cfg.user) {
        const exp = cfg.user.premium > 0 ? Math.ceil(cfg.user.premium / 86400) + 'j' : 'Gratuit';
        badge.innerHTML = `
          <span class="user-status-dot"></span>
          <span style="font-weight:600; color:#fafafa;">${pmeta.short}</span>
          <span style="color:var(--muted); opacity:0.8;">•</span>
          <span style="color:var(--text);">${escapeHtml(cfg.user.username)}</span>
          <span class="user-badge-exp">${exp}</span>
        `;
        badge.title = `Connecté à ${pmeta.name} — Compte actif (${exp} restants). Cliquer pour ouvrir la configuration.`;
      } else if (cfg.has_token) {
        badge.innerHTML = `
          <span class="user-status-dot" style="background:#ef4444; box-shadow:0 0 8px rgba(239,68,68,0.6);"></span>
          <span style="color:#ef4444; font-weight:600;">${pmeta.short} • Clé invalide</span>
        `;
        badge.title = 'Clé API invalide ou expirée. Cliquer pour reconfigurer.';
      } else {
        badge.innerHTML = `
          <span class="user-status-dot" style="background:#eab308; box-shadow:0 0 8px rgba(234,179,8,0.6);"></span>
          <span style="color:#eab308; font-weight:600;">Non configuré</span>
        `;
        badge.title = 'Cliquez pour configurer votre clé débrideur';
        openConfig();
      }
    }
  } catch (e) {
    console.error(e);
  }
}

function getInProgressHistory() {
  return (userHistory || []).filter(h => {
    if (h.imported_watched) return false;
    const pct = Number(h.progress_pct || 0);
    const isDone = Boolean(h.completed || pct >= 85);
    if (h.type === 'series') return true;
    return !isDone;
  });
}

function getWatchedHistory() {
  return (userHistory || []).filter(h => {
    const pct = Number(h.progress_pct || 0);
    return Boolean(h.completed || pct >= 85 || h.imported_watched);
  });
}

async function refreshUserLists() {
  try {
    const data = await api('/api/user-lists');
    userWatchlist = data.watchlist || [];
    userHistory = data.history || [];
    if (data.letterboxd_user) {
      window.savedLetterboxdUser = data.letterboxd_user;
    }
    await fetchCustomLists();
    updateListBadges();
    renderHomeResume();
  } catch (e) {
    console.warn(e);
  }
}

function isInWatchlist(id) {
  return userWatchlist.some(x => x.id === id);
}

async function toggleWatchlist(ev, media) {
  if (ev) ev.stopPropagation();
  const res = await api('/api/watchlist', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(media)
  });
  userWatchlist = res.watchlist || [];
  updateListBadges();
  if (activeTab === 'watchlist') {
    renderActiveListTab();
  } else {
    document.querySelectorAll(`[data-wl-id="${media.id}"]`).forEach(btn => {
      const inList = isInWatchlist(media.id);
      btn.classList.toggle('in-list', inList);
      btn.textContent = inList ? '✓' : '+';
      btn.title = inList ? 'Retirer de Ma Liste' : 'Ajouter à Ma Liste';
    });
  }
  const detailBtn = document.getElementById('detailWlBtn');
  if (detailBtn && currentMedia && currentMedia.id === media.id) {
    const inList = isInWatchlist(media.id);
    detailBtn.textContent = inList ? '✓ Dans ma liste' : '+ Ma Liste';
  }
}

function formatRemaining(pos, dur) {
  if (!pos || !dur || dur <= 30) return '';
  const remMin = Math.max(1, Math.round((dur - pos) / 60));
  if (remMin >= 60) {
    const h = Math.floor(remMin / 60);
    const m = remMin % 60;
    return `Il reste ${h}h${m > 0 ? String(m).padStart(2, '0') : ''}`;
  }
  return `Il reste ${remMin} min`;
}

let catalogSort = 'top';
let catalogSkip = 0;
let catalogItems = [];
let currentTrailerCtx = {trailerId: '', title: '', year: '', lang: 'vf'};

function getSeriesTargetEpisode(h) {
  if (!h || h.type !== 'series') return {season: 1, episode: 1, isNext: false};
  const s = Number(h.season || 1);
  const e = Number(h.episode || 1);
  const pct = Number(h.progress_pct || 0);
  const isDone = Boolean(h.completed || pct >= 85);
  if (isDone) {
    return {
      season: Number(h.next_season || s),
      episode: Number(h.next_episode || (e + 1)),
      isNext: true,
      prevSeason: s,
      prevEpisode: e
    };
  }
  return {
    season: s,
    episode: e,
    isNext: false,
    nextSeason: Number(h.next_season || s),
    nextEpisode: Number(h.next_episode || (e + 1))
  };
}

function resolveMediaTitle(h) {
  let n = (h.name || '').trim();
  const generic = !n || ['série', 'film', 'séries', 'films', 'média', 'media', 'movie', 'series'].includes(n.toLowerCase());
  if (generic) {
    const ref = (userWatchlist || []).find(x => x.id === h.id) || (catalogItems || []).find(x => x.id === h.id);
    if (ref && ref.name && !['série', 'film', 'séries', 'films', 'média', 'media'].includes(ref.name.toLowerCase())) {
      return ref.name;
    }
    if (h.title && !['série', 'film', 'séries', 'films', 'média', 'media'].includes(h.title.toLowerCase())) {
      n = h.title;
    } else if (h.filename) {
      n = h.filename;
    }
  }
  if (n) {
    n = n.replace(/\.(mkv|mp4|avi|ts|mov)$/i, '');
    n = n.replace(/\s*[—–-]\s*S\d+E\d+.*$/i, '');
    n = n.replace(/\b(1080p|720p|2160p|4k|uhd|bluray|bdrip|web-dl|webrip|hdlight|x264|x265|hevc|multi|vostfr|vf|french|truefrench)\b.*$/gi, '').trim();
    n = n.replace(/[\._]/g, ' ').replace(/\s+/g, ' ').trim();
  }
  return n || (h.type === 'series' ? 'Série' : 'Film');
}

function renderHomeResume() {
  const sec = document.getElementById('homeResumeSection');
  const grid = document.getElementById('homeResumeGrid');
  const countBadge = document.getElementById('resumeCountBadge');
  const inProg = getInProgressHistory();
  if (activeTab !== 'movies' && activeTab !== 'series' && activeTab !== 'history') {
    sec.style.display = 'none';
    return;
  }
  const items = activeTab === 'history' ? inProg : inProg.slice(0, 4);
  if (!items.length) {
    if (countBadge) countBadge.style.display = 'none';
    if (activeTab === 'history') {
      sec.style.display = 'block';
      grid.innerHTML = '<p style="color:var(--dim); font-size:0.84rem; grid-column:1/-1;">Aucune lecture en cours pour le moment. Vos films terminés se trouvent dans l\'onglet <strong>Déjà vus</strong>.</p>';
    } else {
      sec.style.display = 'none';
    }
    return;
  }
  sec.style.display = 'block';
  if (countBadge) {
    countBadge.textContent = items.length;
    countBadge.style.display = 'inline-flex';
  }
  grid.innerHTML = items.map(h => {
    const isSeries = h.type === 'series';
    const cleanName = resolveMediaTitle(h);
    const epTag = isSeries && h.season && h.episode
      ? `S${String(h.season).padStart(2,'0')}E${String(h.episode).padStart(2,'0')}`
      : '';
    const nextS = isSeries ? Number(h.next_season || h.season || 1) : null;
    const nextEp = isSeries && h.episode ? Number(h.next_episode || (Number(h.episode) + 1)) : null;
    const nextTag = isSeries && nextS && nextEp
      ? `S${String(nextS).padStart(2,'0')}E${String(nextEp).padStart(2,'0')}`
      : '';
    const pct = Number(h.progress_pct || 0);
    const isEpDone = Boolean(h.completed || pct >= 85);
    const remTxt = (pct > 0 && !isEpDone)
      ? formatRemaining(h.position, h.duration)
      : (isEpDone ? (isSeries ? `✓ ${epTag} terminé · Prêt : ${nextTag}` : '✓ Terminé') : '');
    const subInfo = [(!isEpDone ? epTag : '') || h.year || 'En cours', remTxt].filter(Boolean).join(' • ');
    const progBar = pct > 0 ? `
      <div class="resume-progress-bar">
        <div class="resume-progress-fill" style="width:${Math.min(100, pct)}%;"></div>
      </div>
    ` : '';
    const mediaPayload = JSON.stringify({id: h.id, name: cleanName, type: h.type || 'movie', year: h.year || '', poster: h.poster || ''}).replace(/'/g, "&#39;");
    const resumePayload = JSON.stringify({
      imdb_id: h.id,
      type: h.type || 'movie',
      season: h.season || 1,
      episode: h.episode || 1,
      title: isSeries ? `${cleanName} — ${epTag}` : cleanName,
      name: cleanName,
      poster: h.poster || '',
      year: h.year || ''
    }).replace(/'/g, "&#39;");
    const nextPayload = isSeries ? JSON.stringify({
      imdb_id: h.id,
      type: 'series',
      season: nextS || 1,
      episode: nextEp || 1,
      title: `${cleanName} — ${nextTag}`,
      name: cleanName,
      poster: h.poster || '',
      year: h.year || ''
    }).replace(/'/g, "&#39;") : '';

    const thumbUrl = h.backdrop || h.background || (h.id && String(h.id).startsWith('tt') ? `https://images.metahub.space/background/medium/${h.id}/img` : (h.poster || ''));
    const primaryPlayPayload = (isSeries && isEpDone) ? nextPayload : resumePayload;

    const buttonsHtml = (isSeries && isEpDone)
      ? `
        <button class="resume-btn resume-btn-primary" onclick='oneClickPlay(${nextPayload}, this)'>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          <span>Épisode suivant (${nextTag})</span>
        </button>
        <button class="resume-btn resume-btn-secondary" onclick='oneClickPlay(${resumePayload}, this)' title="Revoir ${epTag}">
          <span>Revoir ${epTag}</span>
        </button>
      `
      : `
        <button class="resume-btn resume-btn-primary" onclick='oneClickPlay(${resumePayload}, this)'>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          <span>Reprendre ${epTag ? `(${epTag})` : ''}</span>
        </button>
        ${isSeries && nextTag ? `
          <button class="resume-btn resume-btn-secondary" onclick='oneClickPlay(${nextPayload}, this)' title="Passer directement au prochain épisode">
            <span>Suivant (${nextTag})</span>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
          </button>
        ` : ''}
      `;

    return `
      <div class="resume-card">
        <div class="resume-thumb-wrap" onclick='oneClickPlay(${primaryPlayPayload}, this)' title="Reprendre la lecture">
          <img class="resume-thumb-img" src="${thumbUrl}" alt="${cleanName}" onerror="if(this.src !== '${h.poster || ''}' && '${h.poster || ''}') { this.src='${h.poster}'; } else { this.style.opacity=0.08; }">
          <div class="resume-thumb-overlay">
            <div class="resume-play-bubble">
              <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            </div>
          </div>
          ${epTag ? `<span class="resume-badge-tag">${epTag}</span>` : (h.type === 'movie' ? `<span class="resume-badge-tag">Film</span>` : '')}
          <button class="resume-dismiss-btn" onclick='event.stopPropagation(); removeHistoryItem(${JSON.stringify(h.id)})' title="Retirer des lectures en cours">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
          ${progBar}
        </div>
        <div class="resume-card-body">
          <div class="resume-title" onclick='selectMedia(${mediaPayload})' title="${cleanName}">${cleanName}</div>
          <div class="resume-subinfo">${subInfo}</div>
          <div class="resume-actions-row">
            ${buttonsHtml}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function removeHistoryItem(id) {
  await api('/api/history-remove', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id})
  });
  await refreshUserLists();
}

async function clearAllHistory() {
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
      <span class="chip" data-genre="Action" onclick="selectGenre('Action', this)">Action</span>
      <span class="chip" data-genre="Adventure" onclick="selectGenre('Adventure', this)">Aventure</span>
      <span class="chip" data-genre="Fantasy" onclick="selectGenre('Fantasy', this)">Fantastique</span>
      <span class="chip" data-genre="Sci-Fi" onclick="selectGenre('Sci-Fi', this)">Sci-Fi</span>
      <span class="chip" data-genre="Comedy" onclick="selectGenre('Comedy', this)">Comédie</span>
      <span class="chip" data-genre="Drama" onclick="selectGenre('Drama', this)">Drame</span>
      <span class="chip" data-genre="Romance" onclick="selectGenre('Romance', this)">Romance</span>
      <span class="chip" data-genre="Mystery" onclick="selectGenre('Mystery', this)">Mystère</span>
      <span class="chip" data-genre="Films d'Animation" onclick="selectGenre('Films d\\'Animation', this)">Films d'Animation</span>
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

function selectGenre(genre, el) {
  activeGenre = genre;
  document.querySelectorAll('#genreFilters .chip').forEach(c => c.classList.remove('active'));
  if (el) el.classList.add('active');
  const type = activeTab === 'series' ? 'series' : (activeTab === 'classics' ? 'classics' : (activeTab === 'anime' ? 'anime' : 'movie'));
  updateCatalogHeading();
  loadCatalog(type, genre, true);
}

function onChangeCatalogSort(sortVal) {
  catalogSort = sortVal || 'top';
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
  const desc = item.description || '';
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
  const kickerTxt = activeTab === 'classics'
    ? `★ PANTHÉON DU CINÉMA · CLASSIQUE INCONTOURNABLE #${heroSpotlightIdx + 1}`
    : (activeTab === 'anime'
        ? `★ ANIMATION JAPONAISE · POPULAIRE #${heroSpotlightIdx + 1}`
        : `★ À LA UNE · ${mtype === 'series' ? 'SÉRIE' : 'FILM'} #${heroSpotlightIdx + 1}`);

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
    box.style.backgroundImage = `url('${bgCandidate}')`;
    const heroBgImg = new Image();
    heroBgImg.onerror = function() {
      if (item.id === 'tt22248376') {
        box.style.backgroundImage = "url('https://media.kitsu.app/anime/46474/cover_image/large-167edf3e01fac59ce6aacfeb47df5634.jpeg')";
      } else if (item.id === 'tt1355642' || item.id === 'tt1370601') {
        box.style.backgroundImage = "url('https://media.kitsu.app/anime/cover_images/3936/large.jpg')";
      } else if (item.id === 'tt21209876') {
        box.style.backgroundImage = "url('https://media.kitsu.app/anime/46231/cover_image/large-33273dc297cdc8b10cc1140de07d3dae.jpeg')";
      } else if (posterUrl && posterUrl !== bgCandidate) {
        box.style.backgroundImage = `url('${posterUrl}')`;
      }
    };
    heroBgImg.src = bgCandidate;
  }
  box.innerHTML = `
    <div class="hero-content">
      <div class="hero-kicker">${kickerTxt}</div>
      <div class="hero-title">${item.name}</div>
      <div class="hero-meta">
        ${year ? `<span>${year}</span>` : ''}
        ${rating ? `<span style="color:#fafafa; font-weight:600;">★ ${rating} IMDb</span>` : ''}
        ${genres.map(g => `<span class="badge">${g}</span>`).join('')}
      </div>
      ${desc ? `<div class="hero-desc">${desc}</div>` : ''}
      <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:6px;">
        <button class="btn" onclick='oneClickCard(event, this, ${payload})'>Play</button>
        <button class="btn btn-secondary" onclick='selectMedia(${payload})'>Fiche &amp; Sources</button>
        <button class="btn btn-secondary" onclick='openTrailerModal("", ${safeNameJs}, ${safeYearJs}, "vf")'><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px; margin-right:4px;"><polygon points="5 3 19 12 5 21 5 3"/></svg>Bande-annonce</button>
        <button class="btn-surprise" onclick="surpriseMeMedia()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="4" width="20" height="16" rx="3"/>
            <line x1="12" y1="4" x2="12" y2="20"/>
          </svg>
          <span>Surprends-moi</span>
        </button>
      </div>
    </div>
    <div class="hero-dots">
      ${heroSpotlightItems.map((_, i) => `<button class="hero-dot ${i === heroSpotlightIdx ? 'active' : ''}" onclick="setHeroSpotlightIdx(${i})" title="Titre ${i + 1}"></button>`).join('')}
    </div>
  `;
}

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

function toggleHideWatchedCatalog() {
  hideWatchedInCatalog = !hideWatchedInCatalog;
  const chip = document.getElementById('hideWatchedChip');
  if (chip) {
    chip.classList.toggle('active', hideWatchedInCatalog);
  }
  if (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics') {
    renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
  }
}

function surpriseMeFromCurrentList() {
  if (activeTab === 'watched') {
    surpriseMeMedia('watched');
  } else {
    surpriseMeMedia('watchlist');
  }
}

function renderActiveListTab() {
  if (activeTab !== 'watchlist' && activeTab !== 'watched') return;
  const rawList = activeTab === 'watchlist' ? (userWatchlist || []) : getWatchedHistory();
  const q = (document.getElementById('listFilterInput')?.value || '').trim().toLowerCase();
  const sortMode = document.getElementById('listSortSelect')?.value || 'added';
  const statsEl = document.getElementById('listStatsBadge');

  // Calcul des statistiques de la liste
  if (statsEl) {
    if (rawList.length > 0) {
      const myRated = rawList.map(x => parseFloat(x.user_rating || '0')).filter(r => r > 0);
      const rated = rawList.map(x => parseFloat(x.imdbRating || '0')).filter(r => r > 0);
      let avgTxt = '';
      if (myRated.length) {
        avgTxt = ` • ★ ${(myRated.reduce((a, b) => a + b, 0) / myRated.length).toFixed(1)}/5 (ma note moy.)`;
      } else if (rated.length) {
        avgTxt = ` • ★ ${(rated.reduce((a, b) => a + b, 0) / rated.length).toFixed(1)} moy.`;
      }
      if (activeTab === 'watchlist') {
        const unwatchedCount = rawList.filter(m => {
          const h = userHistory.find(x => x.id === m.id);
          return !(h && (h.completed || Number(h.progress_pct || 0) >= 85));
        }).length;
        statsEl.textContent = `${rawList.length} titre${rawList.length > 1 ? 's' : ''} (${unwatchedCount} à voir)${avgTxt}`;
      } else {
        statsEl.textContent = `${rawList.length} film${rawList.length > 1 ? 's' : ''} vu${rawList.length > 1 ? 's' : ''}${avgTxt}`;
      }
      statsEl.style.display = 'inline-flex';
    } else {
      statsEl.style.display = 'none';
    }
  }

  let filtered = rawList.filter(m => {
    if (!q) return true;
    const hay = `${m.name || ''} ${m.year || m.releaseInfo || ''}`.toLowerCase();
    return hay.includes(q);
  });

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
    filtered = [...filtered].sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'fr'));
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
    const safeName = String(m.name || 'Titre').replace(/"/g, '&quot;');
    return `
      <div class="case-slot-card" id="caseSlot_${idx}">
        ${ratingBadge}
        ${poster
          ? `<img class="case-slot-poster" src="${poster}" alt="${safeName}" loading="eager" onerror="this.style.visibility='hidden'">`
          : `<div class="case-slot-poster" style="display:flex;align-items:center;justify-content:center;color:var(--dim);font-size:0.75rem;">KINO</div>`
        }
        <div class="case-slot-info">
          <div class="case-slot-title" title="${safeName}">${m.name || 'Sans titre'}</div>
          <div class="case-slot-sub">${yearTxt}</div>
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
        ${winnerRaw.poster ? `<img src="${winnerRaw.poster}" style="width:54px; height:80px; object-fit:cover; border-radius:6px; border:1px solid rgba(255,255,255,0.12);">` : ''}
        <div>
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
            ${rating ? `<span class="badge" style="background:rgba(255,255,255,0.09); color:#fafafa; border-color:rgba(255,255,255,0.18); font-weight:600;">★ ${rating} IMDb</span>` : ''}
            ${year ? `<span style="font-size:0.78rem; color:var(--muted);">${year}</span>` : ''}
            ${genres.map(g => `<span class="badge">${g}</span>`).join('')}
          </div>
          <div style="font-size:1.12rem; font-weight:700; color:#fafafa; margin-top:5px;">${winnerRaw.name}</div>
          ${winnerRaw.description ? `<div style="font-size:0.76rem; color:var(--muted); margin-top:3px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; max-width:470px;">${winnerRaw.description}</div>` : ''}
        </div>
      </div>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <button class="btn" onclick="casePlayOneClick(event, this)">Play</button>
        <button class="btn btn-secondary" onclick="caseOpenDetails()">Fiche &amp; Sources</button>
        <button class="btn btn-secondary" onclick="caseOpenTrailer()"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px; margin-right:4px;"><polygon points="5 3 19 12 5 21 5 3"/></svg>Bande-annonce</button>
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
  if (wlEl) wlEl.textContent = userWatchlist.length ? `(${userWatchlist.length})` : '';
  const inProg = getInProgressHistory();
  const histEl = document.getElementById('histCount');
  if (histEl) histEl.textContent = inProg.length ? `(${inProg.length})` : '';
  const watchedList = getWatchedHistory();
  const watchedEl = document.getElementById('watchedCount');
  if (watchedEl) watchedEl.textContent = watchedList.length ? `(${watchedList.length})` : '';
  const clEl = document.getElementById('customListsCount');
  if (clEl && window.customLists) clEl.textContent = window.customLists.length ? `(${window.customLists.length})` : '';
  const total = userWatchlist.length + inProg.length + watchedList.length;
  const libTotalEl = document.getElementById('libraryTotalBadge');
  if (libTotalEl) libTotalEl.textContent = total > 0 ? `(${total})` : '';
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
    if (activeTab === 'watched' || activeTab === 'watchlist') {
      renderActiveListTab();
    } else if (activeTab === 'movies' || activeTab === 'series' || activeTab === 'classics') {
      updateClassicsStatsBadge();
      renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
    }
  } catch (e) {
    console.warn(e);
  }
}

async function submitLetterboxdImport() {
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
        mode: 'both'
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
      st.textContent = `✓ Synchronisation réussie : ${parts.join(', ') || (res.found_count + ' films')} !`;
      st.style.display = 'block';
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
    if (cntEl) cntEl.textContent = userCustomLists.length ? `(${userCustomLists.length})` : '';
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
        <span style="font-size:0.78rem; font-weight:600; color:#fafafa;">${l.title}</span>
        <span style="font-size:0.72rem; color:var(--muted); margin-left:6px;">(${l.count} films)</span>
      </div>
      <button type="button" class="btn btn-secondary" style="padding:2px 7px; font-size:0.7rem; color:#f87171;" onclick="deleteLetterboxdCustomList('${l.id}')">✕</button>
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
    if (cntEl) cntEl.textContent = userCustomLists.length ? `(${userCustomLists.length})` : '';

    if (st) {
      st.style.color = '#00e054';
      st.textContent = `✓ Liste "${res.list.title}" importée avec succès (${res.total_resolved} films résolus) !`;
      st.style.display = 'block';
    }
    renderSavedListsInModal();
    if (urlInp) urlInp.value = '';

    if (activeTab === 'customlists') {
      renderCustomListsTab();
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
    if (cntEl) cntEl.textContent = userCustomLists.length ? `(${userCustomLists.length})` : '';
    renderSavedListsInModal();
    if (activeTab === 'customlists') {
      renderCustomListsTab();
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
  if (wlw) wlw.style.display = 'none';
  if (lm) lm.style.display = 'none';
  if (hs) hs.style.display = 'none';
  document.getElementById('homeResumeSection').style.display = 'none';

  if (catTitle) {
    catTitle.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; width:100%; flex-wrap:wrap; gap:10px;">
        <span>Listes Letterboxd importées</span>
        <button class="btn btn-secondary" onclick="openLetterboxdModal(); switchLbxModalTab('list');" style="display:inline-flex; align-items:center; gap:6px; padding:5px 12px; font-size:0.78rem; border-color:rgba(0,224,84,0.4);">
          <span>+</span> Importer une nouvelle liste
        </button>
      </div>
    `;
  }

  if (!userCustomLists.length) {
    grid.style.display = 'block';
    grid.innerHTML = `
      <div style="padding:48px 24px; text-align:center; max-width:540px; margin:0 auto; background:rgba(255,255,255,0.02); border:1px dashed var(--border); border-radius:10px;">
        <svg width="40" height="20" viewBox="0 0 30 12" fill="none" style="margin-bottom:12px;">
          <circle cx="6" cy="6" r="5" fill="#ff8000"/>
          <circle cx="15" cy="6" r="5" fill="#00e054"/>
          <circle cx="24" cy="6" r="5" fill="#40bcf4"/>
        </svg>
        <h3 style="font-size:1.05rem; font-weight:600; margin:0 0 8px 0; color:#fafafa;">Aucune liste Letterboxd importée</h3>
        <p style="color:var(--muted); font-size:0.82rem; line-height:1.5; margin:0 0 18px 0;">
          Collez le lien de n'importe quelle liste Letterboxd publique (Top 250, A24, films par ambiance ou réalisateur) pour la transformer en collection streamable avec tirage Roulette dédié !
        </p>
        <button class="btn" onclick="openLetterboxdModal(); switchLbxModalTab('list');" style="padding:8px 18px; font-size:0.82rem;">
          Importer une liste Letterboxd
        </button>
      </div>
    `;
    return;
  }

  grid.style.display = 'flex';
  grid.style.flexDirection = 'column';
  grid.style.gap = '32px';

  grid.innerHTML = userCustomLists.map(list => {
    const items = list.items || [];
    return `
      <div class="custom-list-shelf" style="display:flex; flex-direction:column; gap:12px; border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:24px;">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
          <div>
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:1.02rem; font-weight:700; color:#fafafa;">${list.title}</span>
              <span class="badge" style="font-size:0.7rem; background:rgba(0,224,84,0.12); color:#00e054; border-color:rgba(0,224,84,0.25);">${items.length} films</span>
            </div>
            ${list.description ? `<p style="color:var(--muted); font-size:0.76rem; margin:3px 0 0 0; max-width:650px;">${list.description}</p>` : ''}
          </div>
          <div style="display:flex; gap:8px; align-items:center;">
            <button class="btn-surprise" onclick="surpriseMeFromCustomList('${list.id}')" title="Lancer la roulette KINO sur cette liste">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="4" width="20" height="16" rx="3"/>
                <line x1="12" y1="4" x2="12" y2="20"/>
              </svg>
              <span>Roulette sur cette liste</span>
            </button>
            <a href="${list.url}" target="_blank" class="btn btn-secondary" style="padding:4px 9px; font-size:0.74rem;">Letterboxd ↗</a>
            <button class="btn btn-secondary" style="padding:4px 8px; font-size:0.74rem; color:#f87171;" onclick="deleteLetterboxdCustomList('${list.id}')" title="Retirer cette liste">✕</button>
          </div>
        </div>

        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(135px, 1fr)); gap:12px;">
          ${items.map(m => {
            const inWl = isInWatchlist(m.id);
            const isW = isWatchedMedia(m.id);
            const ratingVal = parseFloat(m.imdbRating || '0') || 0;
            return `
              <div class="card" onclick="selectMedia({id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})">
                <div class="card-thumb">
                  ${m.poster ? `<img class="card-img" src="${m.poster}" alt="${escapeJsString(m.name)}" loading="lazy">` : `<div style="width:100%; height:100%; display:flex; align-items:center; justify-content:center; color:var(--dim); font-size:0.75rem;">KINO</div>`}
                  ${ratingVal > 0 ? `<div class="card-rating">★ ${m.imdbRating}</div>` : ''}
                  <div class="card-actions">
                    <button class="card-action-btn ${isW ? 'active' : ''}" onclick="toggleCardWatched(event, {id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})" title="${isW ? 'Marqué comme vu' : 'Marquer comme vu'}">${isW ? '✓' : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px;"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'}</button>
                    <button class="card-action-btn ${inWl ? 'active' : ''}" onclick="toggleWatchlist(event, {id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})" title="${inWl ? 'Dans Ma Liste' : 'Ajouter à Ma Liste'}">${inWl ? '✓' : '+'}</button>
                  </div>
                  <button class="card-play-btn" onclick="oneClickCard(event, this, {id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})" title="Lecture 1-Clic">Play</button>
                </div>
                <div class="card-title" title="${escapeJsString(m.name)}">${m.name || 'Sans titre'}</div>
                <div class="card-sub">${m.year || 'Film'}</div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }).join('');
}

function surpriseMeFromCustomList(listId) {
  const l = userCustomLists.find(x => x.id === listId);
  if (!l || !l.items || !l.items.length) {
    alert('Cette liste ne contient aucun film.');
    return;
  }
  const unviewed = l.items.filter(m => !isWatchedMedia(m.id));
  const pool = unviewed.length ? unviewed : l.items;
  surpriseMeMedia({ items: pool, title: l.title });
}

// ==========================================
// PLANNING SIMULCAST ANIME & DISCORD RPC
// ==========================================

let currentAnimeSubTab = 'catalog';
let animeScheduleData = null;
let activeScheduleDay = "Aujourd'hui";
let scheduleCountdownTimer = null;

async function switchAnimeSubTab(subTab, doFetch = true) {
  currentAnimeSubTab = subTab;
  const btnCatalog = document.getElementById('animeSubTabCatalog');
  const btnSchedule = document.getElementById('animeSubTabSchedule');
  const catSortWrap = document.getElementById('catalogSortWrap');
  const genreFilters = document.getElementById('genreFilters');
  const postersGrid = document.getElementById('postersGrid');
  const loadMoreWrap = document.getElementById('loadMoreWrap');
  const schedulePanel = document.getElementById('animeSchedulePanel');
  const catTitle = document.getElementById('catalogTitle');

  if (subTab === 'catalog') {
    if (btnCatalog) btnCatalog.classList.add('active');
    if (btnSchedule) btnSchedule.classList.remove('active');
    if (catSortWrap) catSortWrap.style.display = 'flex';
    if (genreFilters) genreFilters.style.display = 'flex';
    if (postersGrid) postersGrid.style.display = 'grid';
    if (loadMoreWrap) loadMoreWrap.style.display = 'block';
    if (schedulePanel) schedulePanel.style.display = 'none';
    if (catTitle) catTitle.textContent = 'Animation Japonaise & Pépites';
  } else {
    if (btnCatalog) btnCatalog.classList.remove('active');
    if (btnSchedule) btnSchedule.classList.add('active');
    if (catSortWrap) catSortWrap.style.display = 'none';
    if (genreFilters) genreFilters.style.display = 'none';
    if (postersGrid) postersGrid.style.display = 'none';
    if (loadMoreWrap) loadMoreWrap.style.display = 'none';
    if (schedulePanel) schedulePanel.style.display = 'block';
    if (catTitle) catTitle.textContent = 'Planning Simulcast';

    if (doFetch || !animeScheduleData) {
      await loadAnimeSchedule();
    } else {
      renderAnimeScheduleDays();
      renderAnimeScheduleGrid();
    }
  }
}

async function loadAnimeSchedule(force = false) {
  const grid = document.getElementById('animeScheduleGrid');
  if (grid) {
    grid.innerHTML = '<div style="grid-column: 1/-1; padding: 40px 20px; text-align: center; color: var(--dim);"><span class="spinner" style="display:inline-block; margin-bottom:10px;"></span><p>Chargement des diffusions japonaises de la semaine...</p></div>';
  }
  try {
    const res = await api(`/api/anime/schedule${force ? '?force=1' : ''}`);
    if (res && res.ok && res.days) {
      animeScheduleData = res.days;
      const dayKeys = Object.keys(animeScheduleData);
      if (dayKeys.includes("Aujourd'hui") && animeScheduleData["Aujourd'hui"].length > 0) {
        activeScheduleDay = "Aujourd'hui";
      } else if (dayKeys.length > 0) {
        activeScheduleDay = dayKeys[0];
      }
      renderAnimeScheduleDays();
      renderAnimeScheduleGrid();

      if (!scheduleCountdownTimer) {
        scheduleCountdownTimer = setInterval(updateScheduleCountdowns, 30000);
      }
    } else {
      if (grid) grid.innerHTML = '<div style="grid-column: 1/-1; padding: 30px; text-align: center; color: var(--muted);"><p>Impossible de charger le planning simulcast. Réessayez dans quelques instants.</p></div>';
    }
  } catch (e) {
    if (grid) grid.innerHTML = `<div style="grid-column: 1/-1; padding: 30px; text-align: center; color: var(--muted);"><p>Erreur: ${e.message}</p></div>`;
  }
}

function renderAnimeScheduleDays() {
  const daysBar = document.getElementById('animeScheduleDays');
  if (!daysBar || !animeScheduleData) return;

  const dayKeys = Object.keys(animeScheduleData);
  if (dayKeys.length === 0) {
    daysBar.innerHTML = '';
    return;
  }

  daysBar.innerHTML = dayKeys.map(day => {
    const items = animeScheduleData[day] || [];
    const isActive = day === activeScheduleDay;
    const isToday = day === "Aujourd'hui";
    const safeDay = day.replace(/'/g, "\\'");
    return `
      <button class="chip ${isActive ? 'active' : ''}" onclick="selectScheduleDay('${safeDay}')" style="white-space:nowrap; padding: 6px 14px; font-weight:${isToday ? '700' : '500'};">
        ${day} <span style="opacity:0.75; font-size:0.72rem; margin-left:4px;">(${items.length})</span>
      </button>
    `;
  }).join('');
}

function selectScheduleDay(day) {
  activeScheduleDay = day;
  renderAnimeScheduleDays();
  renderAnimeScheduleGrid();
}

function formatCountdown(targetSeconds) {
  const now = Math.floor(Date.now() / 1000);
  const diff = targetSeconds - now;
  if (diff <= 0) return { text: "DISPONIBLE", isAvailable: true };
  const hours = Math.floor(diff / 3600);
  const minutes = Math.floor((diff % 3600) / 60);
  if (hours > 24) {
    const days = Math.floor(hours / 24);
    const remH = hours % 24;
    return { text: `Dans ${days}j ${remH}h`, isAvailable: false };
  }
  if (hours > 0) {
    return { text: `Dans ${hours}h ${minutes}m`, isAvailable: false };
  }
  return { text: `Dans ${minutes}m`, isAvailable: false };
}

function updateScheduleCountdowns() {
  const badges = document.querySelectorAll('[data-schedule-airing]');
  badges.forEach(el => {
    const airTs = parseInt(el.getAttribute('data-schedule-airing'), 10);
    if (!isNaN(airTs)) {
      const cd = formatCountdown(airTs);
      if (cd.isAvailable) {
        el.className = 'schedule-card-status-badge badge-status-available';
        el.innerHTML = 'DISPONIBLE';
      } else {
        el.className = 'schedule-card-status-badge badge-status-countdown';
        el.innerHTML = cd.text;
      }
    }
  });
}

function renderAnimeScheduleGrid() {
  const grid = document.getElementById('animeScheduleGrid');
  if (!grid || !animeScheduleData) return;

  const items = animeScheduleData[activeScheduleDay] || [];
  if (items.length === 0) {
    grid.innerHTML = '<div style="grid-column: 1/-1; padding: 40px 20px; text-align: center; color: var(--muted);"><p>Aucune diffusion répertoriée pour ce jour.</p></div>';
    return;
  }

  grid.innerHTML = items.map(item => {
    const cd = formatCountdown(item.airing_at);
    const safeTitle = (item.title || 'Anime').replace(/"/g, '&quot;');
    const safeTitleJs = JSON.stringify(item.title || '');
    const safeRomaji = (item.title_romaji || '').replace(/"/g, '&quot;');
    const posterUrl = item.poster || item.banner || '';
    const epNum = item.episode || 1;
    const scoreBadge = item.score ? `<span class="schedule-score">${item.score}</span>` : '';
    const genresHtml = (item.genres || []).map(g => `<span class="schedule-tag">${g}</span>`).join('');
    const timeDisplay = item.time_str ? item.time_str : '';

    return `
      <div class="schedule-card" onclick='openAnimeFromSchedule(${safeTitleJs}, ${epNum})'>
        <div class="schedule-card-header">
          <img class="schedule-card-img" src="${posterUrl}" alt="${safeTitle}" loading="lazy" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'300\\' height=\\'160\\' fill=\\'%23222\\'><rect width=\\'100%\\' height=\\'100%\\'/></svg>'">
          <div class="schedule-card-overlay"></div>
          <span class="schedule-card-ep-badge">EP ${epNum < 10 ? '0' + epNum : epNum}</span>
          <span class="schedule-card-status-badge ${cd.isAvailable ? 'badge-status-available' : 'badge-status-countdown'}" data-schedule-airing="${item.airing_at}">
            ${cd.isAvailable ? 'DISPONIBLE' : cd.text}
          </span>
          ${timeDisplay ? `<span class="schedule-card-time">${timeDisplay}</span>` : ''}
        </div>
        <div class="schedule-card-body">
          <div>
            <div class="schedule-card-title" title="${safeTitle}">${safeTitle}</div>
            ${safeRomaji && safeRomaji !== item.title ? `<div class="schedule-card-romaji" title="${safeRomaji}">${safeRomaji}</div>` : ''}
            <div class="schedule-card-tags">
              ${scoreBadge}
              ${genresHtml}
            </div>
          </div>
          <div class="schedule-card-actions" onclick="event.stopPropagation()">
            <button class="btn" style="padding:7px 10px; font-size:0.76rem; display:inline-flex; align-items:center; justify-content:center; gap:5px;" onclick='openAnimeFromSchedule(${safeTitleJs}, ${epNum})'>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
              <span>Épisode ${epNum}</span>
            </button>
            <button class="btn btn-secondary" style="padding:7px 10px; font-size:0.76rem;" onclick='searchScheduleAnimeDetail(${safeTitleJs})'>
              Fiche
            </button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function openAnimeFromSchedule(title, epNum) {
  const searchInput = document.getElementById('searchInput');
  const searchType = document.getElementById('searchType');
  if (searchInput && searchType) {
    searchInput.value = title;
    searchType.value = 'anime';
    await switchAnimeSubTab('catalog', false);
    await runSearch();
    const resultsPanel = document.getElementById('postersGrid');
    if (resultsPanel) resultsPanel.scrollIntoView({ behavior: 'smooth' });
  }
}

async function searchScheduleAnimeDetail(title) {
  const searchInput = document.getElementById('searchInput');
  const searchType = document.getElementById('searchType');
  if (searchInput && searchType) {
    searchInput.value = title;
    searchType.value = 'anime';
    await switchAnimeSubTab('catalog', false);
    await runSearch();
  }
}

// --- Discord Rich Presence (RPC) ---
let lastDiscordRpcSync = 0;
function updateDiscordRpc(isPaused = false) {
  const video = document.getElementById('inAppVideo');
  if (!video || !inAppCurrentMedia) return;
  const now = Date.now();
  if (!isPaused && now - lastDiscordRpcSync < 4000) return;
  lastDiscordRpcSync = now;

  const curT = video.currentTime || 0;
  const durT = video.duration || 0;
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
      is_paused: isPaused
    })
  }).catch(() => {});
}

function clearDiscordRpc() {
  api('/api/discord-rpc/clear', { method: 'POST' }).catch(() => {});
}

let lastActiveLibraryTab = 'watchlist';
let previousTab = 'movies';

async function switchTab(tab) {
  if (activeTab && activeTab !== 'settings') {
    previousTab = activeTab;
  }
  const isLibraryTab = ['watchlist', 'history', 'watched', 'customlists', 'rdcloud', 'library'].includes(tab);
  if (isLibraryTab) {
    if (tab === 'library') {
      tab = lastActiveLibraryTab || 'watchlist';
    } else {
      lastActiveLibraryTab = tab;
    }
  }

  activeTab = tab;

  // Active state for primary tabs
  document.querySelectorAll('.nav-tabs-primary .nav-tab').forEach(t => t.classList.remove('active'));
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

  // Subnav visibility & active state
  const libSubNav = document.getElementById('librarySubNav');
  if (libSubNav) {
    libSubNav.style.display = isLibraryTab ? 'flex' : 'none';
  }
  if (isLibraryTab) {
    document.querySelectorAll('.lib-subtab').forEach(t => t.classList.remove('active'));
    const activeSub = document.getElementById('subtab-' + tab);
    if (activeSub) activeSub.classList.add('active');
  }

  document.getElementById('detailPanel').style.display = 'none';
  document.getElementById('torrentsPanel').style.display = 'none';
  document.getElementById('rdCloudPanel').style.display = 'none';
  const comP = document.getElementById('communityPanel');
  if (comP) comP.style.display = 'none';
  const aniP = document.getElementById('animePanel');
  if (aniP) aniP.style.display = 'none';
  const asPanel = document.getElementById('animeSchedulePanel');
  if (asPanel) asPanel.style.display = 'none';
  const asNav = document.getElementById('animeSubNav');
  if (asNav) asNav.style.display = (tab === 'anime') ? 'flex' : 'none';
  const setP = document.getElementById('settingsPanel');
  if (setP) setP.style.display = (tab === 'settings') ? 'block' : 'none';

  if (tab !== 'settings') {
    document.getElementById('postersGrid').style.display = 'grid';
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
    checkTraktStatus();
    checkConfig();
    window.scrollTo({top: 0, behavior: 'smooth'});
    return;
  }

  await refreshUserLists();

  if (tab === 'movies') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('searchType').value = 'movie';
    renderGenreChipsForTab('movies');
    selectGenre(activeGenre, document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`) || document.querySelector('#genreFilters .chip'));
  } else if (tab === 'series') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('searchType').value = 'series';
    renderGenreChipsForTab('series');
    selectGenre(activeGenre, document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`) || document.querySelector('#genreFilters .chip'));
  } else if (tab === 'classics') {
    if (gf) gf.style.display = 'flex';
    if (sw) sw.style.display = 'flex';
    if (wlw) wlw.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('searchType').value = 'movie';
    renderGenreChipsForTab('classics');
    selectGenre(activeGenre, document.querySelector(`#genreFilters .chip[data-genre="${activeGenre}"]`) || document.querySelector('#genreFilters .chip'));
  } else if (tab === 'anime') {
    if (statsEl) statsEl.style.display = 'none';
    document.getElementById('homeResumeSection').style.display = 'none';
    document.getElementById('searchType').value = 'anime';
    if (currentAnimeSubTab === 'schedule') {
      await switchAnimeSubTab('schedule');
    } else {
      await switchAnimeSubTab('catalog', false);
      if (gf) gf.style.display = 'flex';
      if (sw) sw.style.display = 'flex';
      renderGenreChipsForTab('anime');
      activeGenre = '';
      selectGenre('', document.querySelector('#genreFilters .chip'));
    }
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
    grid.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Chargement du catalogue...</p>';
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
    if (lm) {
      lm.style.display = (type !== 'classics' && incoming.length >= 20) ? 'flex' : 'none';
    }
  } catch (e) {
    if (reset) {
      grid.innerHTML = `<p style="color:var(--muted); font-size:0.84rem;">Erreur : ${e.message}</p>`;
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
    grid.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Aucun élément à afficher.</p>';
    return;
  }
  grid.innerHTML = displayList.map(m => {
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
      ? `<button type="button" onclick='toggleCardWatched(event, ${payload})' title="Cliquer pour retirer des Déjà vus" style="position:absolute; top:8px; left:8px; background:rgba(9,9,11,0.92); color:#4ade80; border:1px solid rgba(74,222,128,0.55); font-size:0.68rem; font-weight:600; padding:2px 7px; border-radius:4px; z-index:2; cursor:pointer;">✓ Vu</button>`
      : (watchedEpsCount > 0 ? `<span style="position:absolute; top:8px; left:8px; background:rgba(9,9,11,0.88); color:#fafafa; border:1px solid var(--border-hover); font-size:0.68rem; font-weight:600; padding:2px 6px; border-radius:4px; z-index:2; pointer-events:none;">✓ ${watchedEpsCount} ép.</span>` : '');
    let btnLabel = 'Play';
    if (histItem && mtype === 'series' && histItem.season && histItem.episode) {
      const targetEp = getSeriesTargetEpisode(histItem);
      const code = `S${String(targetEp.season).padStart(2,'0')}E${String(targetEp.episode).padStart(2,'0')}`;
      btnLabel = targetEp.isNext ? `Suivant ${code}` : `Play ${code}`;
    } else if (histItem && histItem.progress_pct > 1 && histItem.progress_pct < 85 && !isMovieWatched) {
      btnLabel = `Reprendre (${Math.round(histItem.progress_pct)}%)`;
    } else if (isMovieWatched) {
      btnLabel = 'Revoir';
    }
    const cardProg = (histItem && histItem.progress_pct > 0 && !isMovieWatched) ? `
      <div style="height:3px; background:var(--surface-2); width:100%;">
        <div style="height:100%; width:${Math.min(100, histItem.progress_pct)}%; background:var(--text);"></div>
      </div>
    ` : '';
    const ratingBadges = [];
    if (userRating) {
      ratingBadges.push(`<span style="color:#fbbf24; font-weight:700;" title="Ma note Letterboxd">★ ${userRating}/5</span>`);
    }
    if (ratingVal) {
      ratingBadges.push(`★ ${ratingVal}`);
    }
    const ratingLine = ratingBadges.length ? ' • ' + ratingBadges.join(' ') : '';
    return `
      <div class="poster-card" onclick='selectMedia(${payload})'>
        ${watchedPill}
        <button class="wl-btn ${inList ? 'in-list' : ''}" data-wl-id="${m.id}" title="${inList ? 'Retirer de Ma Liste' : 'Ajouter à Ma Liste'}" onclick='toggleWatchlist(event, ${payload})'>${inList ? '✓' : '+'}</button>
        <div style="position:relative; width:100%; aspect-ratio:2/3; overflow:hidden;">
          <img src="${m.poster || ''}" alt="${m.name}" loading="lazy" onerror="handlePosterCardError(this, '${m.id}')">
          <div class="poster-play-bubble">
            <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </div>
        </div>
        ${cardProg}
        <div class="poster-info">
          <div class="poster-title">${m.name}</div>
          <div class="poster-year">${year}${ratingLine}${(activeTab === 'anime' || m.is_anime) ? ' • <span style="color:#38bdf8; font-weight:600;">VOSTFR</span>' : ''}</div>
          <button class="btn-oneclick" onclick='oneClickCard(event, this, ${payload})'>${btnLabel}</button>
        </div>
      </div>
    `;
  }).join('');
}

async function loadRdCloud() {
  const list = document.getElementById('rdCloudList');
  const pName = window.activeDebridName || 'votre débrideur';
  list.innerHTML = `<p style="color:var(--dim); font-size:0.84rem;">Récupération de votre historique ${pName}...</p>`;
  try {
    const data = await api('/api/rd-history');
    const items = data.items || [];
    const noteEl = document.getElementById('rdCloudStatusNote');
    if (noteEl && data.cleaned && (data.cleaned.deleted_downloads > 0 || data.cleaned.deleted_torrents > 0)) {
      noteEl.textContent = `Nettoyage auto effectué : ${data.cleaned.deleted_downloads} fichier(s) et ${data.cleaned.deleted_torrents} torrent(s) expirés supprimés de ${pName}.`;
    }
    if (!items.length) {
      list.innerHTML = `<p style="color:var(--dim); font-size:0.84rem;">Aucun fichier récent sur votre compte ${pName}.</p>`;
      return;
    }
    list.innerHTML = items.map(f => {
      const idsPayload = JSON.stringify(f.ids || [f.id]);
      return `
      <div class="torrent-item">
        <div style="flex:1; min-width:240px;">
          <div class="torrent-title">${f.filename}</div>
          <div class="torrent-meta">${f.filesize}${f.generated ? ' • ' + f.generated : ''}</div>
        </div>
        <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
          <button class="btn" onclick='openMpv(this, ${JSON.stringify(f.download)}, ${JSON.stringify(f.filename)})'>Play</button>
          <button class="btn btn-secondary" onclick='startPcDownload(${JSON.stringify(f.download)}, ${JSON.stringify(f.filename)})'>Télécharger</button>
          <a class="btn btn-secondary" href="${f.download}" target="_blank">Lien direct</a>
          <button class="btn btn-secondary" style="padding:6px 10px;" title="Supprimer du Cloud" onclick='deleteRdCloudItem(this, ${idsPayload})'>✕</button>
        </div>
      </div>
    `}).join('');
  } catch (e) {
    list.innerHTML = `<p style="color:var(--muted); font-size:0.84rem;">Erreur : ${e.message}</p>`;
  }
}

async function deleteRdCloudItem(btn, ids) {
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    await api('/api/rd-delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ids})
    });
    await loadRdCloud();
  } catch (e) {
    alert('Erreur suppression : ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = '✕'; }
  }
}

async function onChangeRdRetention(daysStr) {
  const rd_retention_days = parseInt(daysStr || '0', 10);
  if (document.getElementById('cfgRdRetention')) {
    document.getElementById('cfgRdRetention').value = String(rd_retention_days);
  }
  await api('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rd_retention_days})
  });
  await loadRdCloud();
}

async function purgeRdCloud(mode, btn) {
  const orig = btn ? btn.textContent : '';
  const pName = window.activeDebridName || 'votre débrideur';
  if (btn) { btn.disabled = true; btn.textContent = 'Suppression...'; }
  try {
    const res = await api('/api/rd-cleanup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({max_age_days: mode === 'all' ? -1 : null})
    });
    const noteEl = document.getElementById('rdCloudStatusNote');
    if (noteEl && res.cleaned) {
      noteEl.textContent = `${res.cleaned.deleted_downloads} fichier(s) et ${res.cleaned.deleted_torrents} torrent(s) supprimés du Cloud ${pName}.`;
    }
    await loadRdCloud();
  } catch (e) {
    alert('Erreur nettoyage Cloud : ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

async function openTrailerModal(trailerId, title, year = '', lang = 'vf') {
  currentTrailerCtx = {trailerId: trailerId || '', title: title || '', year: year || '', lang: lang || 'vf'};
  const modal = document.getElementById('trailerModal');
  const video = document.getElementById('trailerVideo');
  const iframe = document.getElementById('trailerIframe');
  const loading = document.getElementById('trailerLoading');
  const titleEl = document.getElementById('trailerModalTitle');
  const extLink = document.getElementById('trailerExternalLink');
  const btnVf = document.getElementById('trailerBtnVf');
  const btnVo = document.getElementById('trailerBtnVo');
  if (!modal) return;

  if (btnVf) btnVf.classList.toggle('active', currentTrailerCtx.lang === 'vf');
  if (btnVo) btnVo.classList.toggle('active', currentTrailerCtx.lang === 'vo');

  titleEl.textContent = `Bande-annonce (${currentTrailerCtx.lang.toUpperCase()}) — ${title || 'KINO'}`;
  if (video) { video.pause(); video.removeAttribute('src'); video.style.display = 'none'; }
  if (iframe) { iframe.src = ''; iframe.style.display = 'none'; }
  if (loading) loading.style.display = 'flex';
  modal.style.display = 'flex';

  try {
    const qs = new URLSearchParams({
      title: currentTrailerCtx.title,
      year: currentTrailerCtx.year,
      yt_id: currentTrailerCtx.trailerId,
      lang: currentTrailerCtx.lang
    });
    const data = await api(`/api/trailer?${qs.toString()}`);
    if (extLink && data.watch_url) extLink.href = data.watch_url;
    if (loading) loading.style.display = 'none';
    if (data.stream_url && video) {
      video.src = data.stream_url;
      video.style.display = 'block';
      video.play().catch(() => {});
    } else if (data.embed_url && iframe) {
      iframe.src = data.embed_url;
      iframe.style.display = 'block';
    }
  } catch (e) {
    if (loading) loading.style.display = 'none';
    if (trailerId && iframe) {
      extLink.href = `https://www.youtube.com/watch?v=${encodeURIComponent(trailerId)}`;
      iframe.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(trailerId)}?autoplay=1&rel=0`;
      iframe.style.display = 'block';
    }
  }
}

function switchTrailerLang(lang) {
  if (!currentTrailerCtx) return;
  openTrailerModal(currentTrailerCtx.trailerId, currentTrailerCtx.title, currentTrailerCtx.year, lang);
}

function closeTrailerModal() {
  const modal = document.getElementById('trailerModal');
  const video = document.getElementById('trailerVideo');
  const iframe = document.getElementById('trailerIframe');
  if (video) { video.pause(); video.removeAttribute('src'); video.style.display = 'none'; }
  if (iframe) { iframe.src = ''; iframe.style.display = 'none'; }
  if (modal) modal.style.display = 'none';
}

function toggleHeaderMenu(e) {
  if (e) e.stopPropagation();
  const wrap = document.querySelector('.header-menu-wrap');
  const menu = document.getElementById('headerDropdownMenu');
  if (!menu) return;
  const isShown = (menu.style.display === 'flex');
  if (isShown) {
    menu.style.display = 'none';
    if (wrap) wrap.classList.remove('active');
  } else {
    menu.style.display = 'flex';
    if (wrap) wrap.classList.add('active');
  }
}

function closeHeaderMenu() {
  const wrap = document.querySelector('.header-menu-wrap');
  const menu = document.getElementById('headerDropdownMenu');
  if (menu) menu.style.display = 'none';
  if (wrap) wrap.classList.remove('active');
}

window.addEventListener('click', (e) => {
  const wrap = document.querySelector('.header-menu-wrap');
  if (wrap && !wrap.contains(e.target)) {
    closeHeaderMenu();
  }
});

function openConfig() { 
  if (activeTab && activeTab !== 'settings') {
    previousTab = activeTab;
  }
  switchTab('settings');
}

function closeConfig() {
  switchTab(previousTab || 'movies');
}

function togglePasswordVisibility(inputId, btn) {
  const inp = document.getElementById(inputId);
  if (!inp) return;
  if (inp.type === 'password') {
    inp.type = 'text';
    if (btn) btn.style.color = 'var(--text)';
  } else {
    inp.type = 'password';
    if (btn) btn.style.color = 'var(--muted)';
  }
}


function toggleDiscordRpcFields() {
  const chk = document.getElementById('cfgDiscordRpc');
  const opt = document.getElementById('discordRpcOptions');
  if (opt) opt.style.display = (chk && chk.checked) ? 'flex' : 'none';
}

function onDiscordPresetChange() {
  const p = document.getElementById('cfgDiscordPreset');
  const box = document.getElementById('cfgDiscordCustomBox');
  if (box && p) {
    box.style.display = (p.value === 'custom') ? 'flex' : 'none';
  }
}

async function saveConfig() {
  const debrid_provider = document.getElementById('cfgProvider') ? document.getElementById('cfgProvider').value : 'realdebrid';
  const token = document.getElementById('cfgToken') ? document.getElementById('cfgToken').value.trim() : '';
  const dir = document.getElementById('cfgDir') ? document.getElementById('cfgDir').value.trim() : '';
  const isMacPlatform = /Mac/i.test(navigator.platform || navigator.userAgent);
  const player_mode = document.getElementById('cfgPlayerMode') ? document.getElementById('cfgPlayerMode').value : (window.kinoPlayerMode || (isMacPlatform ? 'integrated' : 'kino'));
  const pref_lang = document.getElementById('cfgPrefLang') ? document.getElementById('cfgPrefLang').value : 'vf';
  const pref_quality = document.getElementById('cfgPrefQuality') ? document.getElementById('cfgPrefQuality').value : '4k';
  const hdr_mode = document.getElementById('cfgHdrMode') ? document.getElementById('cfgHdrMode').value : 'sdr_pref';
  const audio_mode = document.getElementById('cfgAudioMode') ? document.getElementById('cfgAudioMode').value : 'voice_boost';
  const rd_retention_days = document.getElementById('cfgRdRetention') ? parseInt(document.getElementById('cfgRdRetention').value || '0', 10) : 0;
  const discord_rpc = document.getElementById('cfgDiscordRpc') ? document.getElementById('cfgDiscordRpc').checked : true;
  let discord_client_id = '631379801826918400';
  const presetEl = document.getElementById('cfgDiscordPreset');
  if (presetEl) {
    if (presetEl.value === 'custom') {
      const customVal = (document.getElementById('cfgDiscordClientId') ? document.getElementById('cfgDiscordClientId').value : '').trim();
      discord_client_id = customVal || '631379801826918400';
    } else {
      discord_client_id = presetEl.value;
    }
  }

  const saveBtn = document.getElementById('settingsSaveBtn');
  const saveStatus = document.getElementById('settingsSaveStatus');
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Enregistrement...';
  }
  if (saveStatus) {
    saveStatus.innerHTML = '<span style="color:var(--muted);">Enregistrement des configurations...</span>';
  }

  try {
    const resp = await api('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({debrid_provider, rd_token: token, download_dir: dir, player_mode, pref_lang, pref_quality, hdr_mode, audio_mode, rd_retention_days, discord_rpc, discord_client_id})
    });
    window.kinoPlayerMode = player_mode;
    window.kinoHdrMode = hdr_mode;
    window.kinoAudioMode = audio_mode;
    if (document.getElementById('cfgToken')) document.getElementById('cfgToken').value = '';
    await checkConfig();
    if (saveStatus) {
      if (resp && resp.token_check && resp.token_check.error) {
        saveStatus.innerHTML = `<span style="color:#ef4444; font-weight:600;">⚠ Paramètres enregistrés mais la clé a été rejetée par ${debrid_provider} (${escapeHtml(resp.token_check.error)})</span>`;
      } else if (resp && resp.token_check && resp.token_check.username) {
        saveStatus.innerHTML = `<span style="color:#4ade80; font-weight:600;">✓ Compte débrideur connecté (@${escapeHtml(resp.token_check.username)})</span>`;
      } else {
        saveStatus.innerHTML = '<span style="color:#4ade80; font-weight:600;">✓ Paramètres enregistrés avec succès</span>';
      }
      setTimeout(() => {
        if (saveStatus && saveStatus.innerHTML.includes('succès')) {
          saveStatus.innerHTML = '<span style="color:var(--muted);">Tous les paramètres sont à jour.</span>';
        }
      }, 5000);
    }
    if (activeTab === 'rdcloud') loadRdCloud();
  } catch (e) {
    if (saveStatus) {
      saveStatus.innerHTML = `<span style="color:#ef4444; font-weight:600;">Erreur : ${escapeHtml(e.message)}</span>`;
    }
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Enregistrer les paramètres';
    }
  }
}

async function testCurrentToken(btn) {
  const inp = document.getElementById('cfgToken');
  const resEl = document.getElementById('cfgTokenTestResult');
  const provEl = document.getElementById('cfgProvider');
  const prov = provEl ? provEl.value : 'realdebrid';
  const tok = inp ? inp.value.trim() : '';

  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Test...';
  }
  if (resEl) {
    resEl.style.display = 'block';
    resEl.style.background = 'rgba(255,255,255,0.05)';
    resEl.style.color = 'var(--muted)';
    resEl.style.border = '1px solid var(--border)';
    resEl.innerHTML = 'Vérification de la clé en cours auprès du serveur...';
  }

  try {
    const url = resolveKinoUrl('/api/test-token');
    const resp = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider: prov, token: tok})
    });
    const res = await resp.json().catch(() => ({}));
    if (resp.ok && res.ok && res.user) {
      const exp = res.user.premium > 0 ? `${Math.ceil(res.user.premium / 86400)} jours` : 'Gratuit';
      resEl.style.background = 'rgba(74, 222, 128, 0.12)';
      resEl.style.color = '#4ade80';
      resEl.style.borderColor = 'rgba(74, 222, 128, 0.35)';
      resEl.innerHTML = `<strong>✓ Clé valide !</strong> Compte <strong>@${escapeHtml(res.user.username)}</strong> (${exp} Premium restants). Cliquez sur <em>Enregistrer les paramètres</em> en bas pour la conserver.`;
      checkConfig();
    } else {
      const errMsg = res.error || (resp.status === 404 ? "Moteur de test en cours de rechargement. Vous pouvez enregistrer directement votre clé ci-dessous." : `Erreur serveur (${resp.status})`);
      const isBadToken = /bad_token|401|invalide/i.test(errMsg);
      resEl.style.background = 'rgba(239, 68, 68, 0.12)';
      resEl.style.color = '#ef4444';
      resEl.style.borderColor = 'rgba(239, 68, 68, 0.35)';
      if (isBadToken) {
        resEl.innerHTML = `<strong>✗ Clé refusée par Real-Debrid ("bad_token")</strong><br>
          <span style="font-size:0.75rem; color:#fca5a5; line-height:1.4; display:block; margin-top:3px;">
            Real-Debrid indique que cette clé est invalide, a été révoquée ou que votre abonnement Premium est expiré.<br>
            1. Rendez-vous sur <a href="https://real-debrid.com/apitoken" target="_blank" style="color:#fff; text-decoration:underline;">real-debrid.com/apitoken</a> pour copier votre clé active.<br>
            2. Collez-la dans ce champ puis cliquez sur <strong>Enregistrer les paramètres</strong> en bas.
          </span>`;
      } else {
        resEl.innerHTML = `<strong>✗ Erreur :</strong> ${escapeHtml(errMsg)}`;
      }
    }
  } catch (e) {
    if (resEl) {
      resEl.style.display = 'block';
      resEl.style.background = 'rgba(239, 68, 68, 0.12)';
      resEl.style.color = '#ef4444';
      resEl.style.borderColor = 'rgba(239, 68, 68, 0.35)';
      resEl.innerHTML = `<strong>✗ Erreur :</strong> ${escapeHtml(e.message)}`;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Tester la clé';
    }
  }
}

async function openFolder() {
  await api('/api/open-folder', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
}

// ==========================================
// --- KINO SOCIAL, REMOTE, TRAKT & ADDONS ---
// ==========================================

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeJsString(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\"');
}

// --- Trakt.tv Integration ---
let traktAuthPollTimer = null;
let traktScrobbledStarted = false;
let traktScrobbledStop = false;

async function checkTraktStatus() {
  try {
    const res = await api('/api/trakt/status');
    const statusEl = document.getElementById('traktSyncStatus');
    const connectBtn = document.getElementById('traktConnectBtn');
    const syncBtn = document.getElementById('traktSyncBtn');
    const authBox = document.getElementById('traktAuthBox');
    
    if (res && res.authenticated) {
      if (statusEl) statusEl.innerHTML = `<span style="color:#4ade80;">✓ Connecté (${res.username ? '@' + res.username : 'Actif'})</span>`;
      if (connectBtn) { connectBtn.textContent = 'Déconnecter'; connectBtn.style.color = '#ef4444'; }
      if (syncBtn) syncBtn.style.display = 'inline-block';
      if (authBox) authBox.style.display = 'none';
      if (traktAuthPollTimer) { clearInterval(traktAuthPollTimer); traktAuthPollTimer = null; }
    } else {
      if (statusEl) statusEl.textContent = 'Non connecté';
      if (connectBtn) { connectBtn.textContent = 'Connecter'; connectBtn.style.color = ''; }
      if (syncBtn) syncBtn.style.display = 'none';
    }
    return Boolean(res && res.authenticated);
  } catch (e) {
    return false;
  }
}

async function toggleTraktAuth(btn) {
  const statusEl = document.getElementById('traktSyncStatus');
  const authBox = document.getElementById('traktAuthBox');
  const codeEl = document.getElementById('traktUserCode');
  const cdEl = document.getElementById('traktAuthCountdown');

  if (btn && btn.textContent.trim() === 'Déconnecter') {
    if (!confirm('Voulez-vous déconnecter votre compte Trakt.tv ?')) return;
    try {
      await api('/api/trakt/disconnect', {method: 'POST'});
      await checkTraktStatus();
    } catch(e) {
      alert('Erreur déconnexion Trakt : ' + e.message);
    }
    return;
  }

  try {
    if (btn) { btn.disabled = true; btn.textContent = 'Connexion...'; }
    const res = await api('/api/trakt/auth/start', {method: 'POST'});
    if (res && res.user_code) {
      if (codeEl) codeEl.textContent = res.user_code;
      if (authBox) authBox.style.display = 'block';
      let expires = res.expires_in || 600;
      if (cdEl) cdEl.textContent = `En attente de validation (expire dans ${Math.round(expires/60)} min)...`;

      if (traktAuthPollTimer) clearInterval(traktAuthPollTimer);
      traktAuthPollTimer = setInterval(async () => {
        try {
          const pollRes = await api('/api/trakt/auth/poll', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({device_code: res.device_code})
          });
          if (pollRes && pollRes.status === 'authenticated') {
            clearInterval(traktAuthPollTimer);
            traktAuthPollTimer = null;
            if (authBox) authBox.style.display = 'none';
            await checkTraktStatus();
            alert('Compte Trakt.tv connecté avec succès ! Scrobble automatique et synchronisation activés.');
          } else if (pollRes && (pollRes.status === 'expired' || pollRes.status === 'denied')) {
            clearInterval(traktAuthPollTimer);
            traktAuthPollTimer = null;
            if (cdEl) cdEl.textContent = 'Code expiré ou refusé. Réessayez.';
          }
        } catch(err) {}
      }, (res.interval || 5) * 1000);
    }
  } catch(e) {
    alert('Erreur initialisation Trakt : ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Connecter'; }
  }
}

async function triggerTraktSync(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Sync...'; }
  try {
    const res = await api('/api/trakt/sync', {method: 'POST'});
    if (res && res.success) {
      alert(`Synchronisation Trakt terminée !\n${res.added_to_kino || 0} éléments ajoutés à votre liste KINO.\n${res.synced_to_trakt || 0} éléments synchronisés vers Trakt.`);
      await refreshUserLists();
    } else {
      alert('Erreur lors de la synchronisation Trakt : ' + (res.error || 'inconnue'));
    }
  } catch(e) {
    alert('Erreur Trakt sync : ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Synchroniser'; }
  }
}

async function scrobbleTrakt(action, media, progressPct) {
  try {
    if (!media) return;
    const name = media.name || media.title || '';
    if (!name) return;
    const s = media.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : null);
    const e = media.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : null);
    await api('/api/trakt/scrobble', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        action: action,
        title: name,
        year: media.year || '',
        type: media.type || 'movie',
        season: s,
        episode: e,
        progress: progressPct || 0
      })
    });
  } catch(err) {}
}

// --- KINO Remote Smartphone ---
let remotePollingInterval = null;

async function openRemoteModal() {
  const modal = document.getElementById('remoteModal');
  const qrBox = document.getElementById('remoteQrContainer');
  const urlBox = document.getElementById('remoteDirectUrl');
  if (!modal) return;
  modal.style.display = 'flex';
  
  if (qrBox) {
    qrBox.innerHTML = '<div style="color:#71717a; font-size:0.8rem; margin:auto;">Génération du QR Code local...</div>';
  }
  try {
    const res = await api('/api/remote/qr');
    const remoteUrl = (res && res.url) ? res.url : (`http://${window.location.hostname || '127.0.0.1'}:${window.location.port || '8080'}/remote`);
    if (urlBox) {
      urlBox.textContent = remoteUrl;
    }
    if (qrBox) {
      qrBox.innerHTML = '';
      if (typeof QRCode !== 'undefined') {
        new QRCode(qrBox, {
          text: remoteUrl,
          width: 196,
          height: 196,
          colorDark: '#09090b',
          colorLight: '#ffffff',
          correctLevel: QRCode.CorrectLevel.M
        });
      } else {
        qrBox.innerHTML = `<div style="padding:20px; text-align:center;"><a href="${remoteUrl}" target="_blank" style="color:#09090b; font-weight:600; font-size:0.85rem; word-break:break-all;">${remoteUrl}</a></div>`;
      }
    }
  } catch(e) {
    const fallbackUrl = `http://${window.location.hostname || '127.0.0.1'}:${window.location.port || '8080'}/remote`;
    if (urlBox) urlBox.textContent = fallbackUrl;
    if (qrBox) {
      qrBox.innerHTML = '';
      if (typeof QRCode !== 'undefined') {
        new QRCode(qrBox, {
          text: fallbackUrl,
          width: 196,
          height: 196,
          colorDark: '#09090b',
          colorLight: '#ffffff',
          correctLevel: QRCode.CorrectLevel.M
        });
      }
    }
  }
}

function closeRemoteModal() {
  const modal = document.getElementById('remoteModal');
  if (modal) modal.style.display = 'none';
}

function copyRemoteUrl() {
  const urlBox = document.getElementById('remoteDirectUrl');
  if (!urlBox || !urlBox.textContent) return;
  const url = urlBox.textContent.trim();
  if (url.startsWith('http')) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(() => {
        showInAppToast('Adresse de la télécommande copiée !', 2000);
      }).catch(() => {
        prompt('Adresse KINO Remote (copiez avec Ctrl+C) :', url);
      });
    } else {
      prompt('Adresse KINO Remote (copiez avec Ctrl+C) :', url);
    }
  }
}

function startRemotePolling() {
  if (remotePollingInterval) return;
  remotePollingInterval = setInterval(async () => {
    const v = document.getElementById('inAppVideo');
    const overlay = document.getElementById('inAppPlayerOverlay');
    const isPlayerActive = Boolean(overlay && overlay.classList.contains('active') && v && v.src);

    if (isPlayerActive) {
      try {
        const curT = v.currentTime || 0;
        const durT = v.duration || 0;
        const s = (inAppCurrentMedia && inAppCurrentMedia.season) ? inAppCurrentMedia.season : null;
        const e = (inAppCurrentMedia && inAppCurrentMedia.episode) ? inAppCurrentMedia.episode : null;
        await fetch('/api/player/state', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            title: inAppCurrentTitle || (inAppCurrentMedia ? inAppCurrentMedia.name : 'Lecture KINO'),
            season: s,
            episode: e,
            currentTime: curT,
            duration: durT,
            paused: v.paused,
            volume: v.volume,
            muted: v.muted
          })
        });
      } catch(e) {}
    }

    try {
      const res = await fetch('/api/player/commands');
      const data = await res.json();
      if (data && data.commands && data.commands.length > 0) {
        for (const cmd of data.commands) {
          handleRemoteCommand(cmd);
        }
      }
    } catch(e) {}
  }, 1000);
}

function handleRemoteCommand(cmd) {
  const v = document.getElementById('inAppVideo');
  if (!v) return;
  const action = cmd.action;
  if (action === 'play') {
    v.play().catch(() => {});
  } else if (action === 'pause') {
    v.pause();
  } else if (action === 'toggle_play') {
    toggleInAppPlay();
  } else if (action === 'seek') {
    if (cmd.delta !== undefined) {
      v.currentTime = Math.max(0, Math.min(v.duration || 0, v.currentTime + Number(cmd.delta)));
    } else if (cmd.time !== undefined) {
      v.currentTime = Math.max(0, Math.min(v.duration || 0, Number(cmd.time)));
    }
  } else if (action === 'volume') {
    if (cmd.level !== undefined) {
      v.volume = Math.max(0, Math.min(1, Number(cmd.level)));
      v.muted = false;
      showInAppToast(`Volume : ${Math.round(v.volume * 100)}%`);
    }
  } else if (action === 'mute') {
    v.muted = !v.muted;
    showInAppToast(v.muted ? 'Son coupé' : `Volume : ${Math.round(v.volume * 100)}%`);
  } else if (action === 'next_ep') {
    if (typeof inAppNextTrack === 'function') inAppNextTrack();
  } else if (action === 'prev_ep') {
    if (typeof inAppPrevTrack === 'function') inAppPrevTrack();
  }
}

// --- Watch Party P2P (PeerJS) ---
let wpPeer = null;
let wpConnections = [];
let wpRoomCode = null;
let wpIsSyncing = false;

function openWatchPartyModal() {
  const modal = document.getElementById('watchPartyModal');
  if (modal) modal.style.display = 'flex';
  updateWpModalUI();
}

function closeWatchPartyModal() {
  const modal = document.getElementById('watchPartyModal');
  if (modal) modal.style.display = 'none';
}

function updateWpModalUI() {
  const setupView = document.getElementById('wpSetupView');
  const activeView = document.getElementById('wpActiveView');
  const codeDisplay = document.getElementById('wpRoomCodeDisplay');
  const countEl = document.getElementById('wpPeersCount');
  
  if (wpRoomCode) {
    if (setupView) setupView.style.display = 'none';
    if (activeView) activeView.style.display = 'block';
    if (codeDisplay) codeDisplay.textContent = wpRoomCode;
    if (countEl) countEl.textContent = String(wpConnections.length + 1);
  } else {
    if (setupView) setupView.style.display = 'block';
    if (activeView) activeView.style.display = 'none';
  }
}

function createWatchPartyRoom() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  for (let i = 0; i < 6; i++) {
    code += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  wpRoomCode = code;
  initWpPeer('kino-room-' + code.toLowerCase());
}

function showJoinWatchParty() {
  const box = document.getElementById('wpJoinBox');
  if (box) box.style.display = (box.style.display === 'none' ? 'block' : 'none');
  const inp = document.getElementById('wpJoinCodeInput');
  if (inp) inp.focus();
}

function joinWatchPartyRoom() {
  const inp = document.getElementById('wpJoinCodeInput');
  const code = (inp ? inp.value.trim().toUpperCase() : '');
  if (!code || code.length < 4) {
    alert('Veuillez entrer un code de salle valide (ex: KINO9X).');
    return;
  }
  wpRoomCode = code;
  initWpPeer(null, 'kino-room-' + code.toLowerCase());
}

function copyWpRoomCode() {
  if (!wpRoomCode) return;
  navigator.clipboard.writeText(wpRoomCode).then(() => {
    alert(`Code de salle [ ${wpRoomCode} ] copié ! Partagez-le avec vos amis.`);
  }).catch(() => {});
}

function leaveWatchPartyRoom() {
  if (wpPeer) {
    wpPeer.destroy();
    wpPeer = null;
  }
  wpConnections = [];
  wpRoomCode = null;
  updateWpModalUI();
  const chatBox = document.getElementById('inAppWpChatBox');
  if (chatBox) chatBox.style.display = 'none';
  showInAppToast('Vous avez quitté la Watch Party');
}

function initWpPeer(hostPeerId, joinHostPeerId) {
  if (typeof Peer === 'undefined') {
    alert("PeerJS n'a pas pu être chargé depuis le CDN. Vérifiez votre connexion internet.");
    return;
  }
  if (wpPeer) {
    wpPeer.destroy();
    wpPeer = null;
  }
  wpConnections = [];

  const peerOpts = {
    debug: 1,
    config: {
      iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:global.stun.twilio.com:3478' }
      ]
    }
  };

  try {
    if (hostPeerId) {
      wpPeer = new Peer(hostPeerId, peerOpts);
    } else {
      wpPeer = new Peer(peerOpts);
    }

    wpPeer.on('open', (id) => {
      updateWpModalUI();
      showInAppToast(`Watch Party prête ! Code : ${wpRoomCode}`);
      if (joinHostPeerId) {
        const conn = wpPeer.connect(joinHostPeerId, { reliable: true });
        setupWpConnection(conn);
      }
    });

    wpPeer.on('connection', (conn) => {
      setupWpConnection(conn);
      const v = document.getElementById('inAppVideo');
      if (v) {
        conn.on('open', () => {
          conn.send({
            type: 'sync',
            time: v.currentTime,
            paused: v.paused,
            title: inAppCurrentTitle || ''
          });
        });
      }
    });

    wpPeer.on('error', (err) => {
      console.warn('Watch Party error:', err);
      if (err.type === 'unavailable-id') {
        createWatchPartyRoom();
      } else {
        alert('Erreur Watch Party P2P : ' + err.message);
      }
    });
  } catch(e) {
    alert('Erreur Watch Party : ' + e.message);
  }
}

function setupWpConnection(conn) {
  conn.on('open', () => {
    if (!wpConnections.includes(conn)) wpConnections.push(conn);
    updateWpModalUI();
    showInAppToast('Un ami a rejoint la Watch Party !');
    const chatBox = document.getElementById('inAppWpChatBox');
    if (chatBox) chatBox.style.display = 'flex';
  });

  conn.on('data', (data) => {
    handleWpIncomingData(data, conn);
  });

  conn.on('close', () => {
    wpConnections = wpConnections.filter(c => c !== conn);
    updateWpModalUI();
    showInAppToast('Un participant a quitté la session');
  });
}

function handleWpIncomingData(data, fromConn) {
  if (!data) return;
  const v = document.getElementById('inAppVideo');
  if (!v) return;

  if (data.type === 'sync' || data.type === 'event') {
    wpIsSyncing = true;
    if (data.time !== undefined && Math.abs(v.currentTime - data.time) > 1.5) {
      v.currentTime = data.time;
    }
    if (data.event === 'play' || (data.type === 'sync' && data.paused === false)) {
      v.play().catch(() => {});
      showInAppToast('Reprise synchronisée');
    } else if (data.event === 'pause' || (data.type === 'sync' && data.paused === true)) {
      v.pause();
      showInAppToast('Pause synchronisée');
    }
    setTimeout(() => { wpIsSyncing = false; }, 300);
  } else if (data.type === 'chat') {
    appendWpChatMessage(data.user || 'Ami', data.text || '');
  }
}

function broadcastWpEvent(event, time) {
  if (!wpRoomCode || wpConnections.length === 0 || wpIsSyncing) return;
  const payload = {
    type: 'event',
    event: event,
    time: time
  };
  wpConnections.forEach(conn => {
    if (conn.open) conn.send(payload);
  });
}

function toggleWpChat() {
  const box = document.getElementById('inAppWpChatBox');
  if (!box) return;
  box.style.display = (box.style.display === 'none' ? 'flex' : 'none');
}

function sendWpMessage() {
  const inp = document.getElementById('inAppWpInput');
  if (!inp) return;
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  
  appendWpChatMessage('Moi', text);
  const payload = { type: 'chat', user: 'Ami', text: text };
  wpConnections.forEach(conn => {
    if (conn.open) conn.send(payload);
  });
}

function appendWpChatMessage(user, text) {
  const box = document.getElementById('inAppWpChatBox');
  const msgs = document.getElementById('inAppWpMessages');
  if (box) box.style.display = 'flex';
  if (msgs) {
    const div = document.createElement('div');
    div.style.background = 'rgba(255,255,255,0.06)';
    div.style.padding = '4px 8px';
    div.style.borderRadius = '6px';
    div.style.wordBreak = 'break-word';
    div.innerHTML = `<b style="color:${user === 'Moi' ? '#4ade80' : '#38bdf8'}; font-size:0.75rem;">${user} :</b> <span style="color:#f4f4f5;">${escapeHtml(text)}</span>`;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  }
  showInAppToast(`<strong>${escapeHtml(user)} :</strong> ${text.length > 25 ? text.substring(0,25) + '...' : text}`);
}

// --- Gestionnaire d'Add-ons ---
async function openAddonsModal() {
  const modal = document.getElementById('addonsModal');
  if (modal) modal.style.display = 'flex';
  await loadAddonsList();
}

function closeAddonsModal() {
  const modal = document.getElementById('addonsModal');
  if (modal) modal.style.display = 'none';
}

async function loadAddonsList() {
  const container = document.getElementById('addonsListContainer');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--dim); font-size:0.8rem; text-align:center; padding:20px;">Chargement des add-ons...</div>';

  try {
    const data = await api('/api/addons');
    const addons = data.addons || [];
    if (addons.length === 0) {
      container.innerHTML = '<div style="color:var(--muted); font-size:0.82rem; text-align:center; padding:30px;">Aucun add-on installé. Cliquez sur "+ Installer un Add-on" pour en ajouter.</div>';
      return;
    }

    container.innerHTML = addons.map(addon => {
      const isEnabled = Boolean(addon.enabled);
      const isBuiltin = Boolean(addon.builtin);
      return `
        <div style="background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px 14px; display:flex; justify-content:space-between; align-items:center; gap:12px;">
          <div style="flex:1;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:0.92rem; font-weight:700; color:var(--text);">${escapeHtml(addon.name || addon.id)}</span>
              <span class="badge" style="font-size:0.68rem; background:rgba(255,255,255,0.06);">${escapeHtml(addon.version || '1.0.0')}</span>
              ${isBuiltin ? `<span class="badge" style="font-size:0.68rem; background:rgba(56,189,248,0.15); color:#38bdf8;">Officiel</span>` : ''}
              <span class="badge" style="font-size:0.68rem; background:rgba(74,222,128,0.15); color:#4ade80;">${escapeHtml(addon.type || 'scraper')}</span>
            </div>
            <p style="color:var(--muted); font-size:0.77rem; margin:4px 0 0; line-height:1.4;">${escapeHtml(addon.description || 'Add-on de recherche')}</p>
            ${addon.author ? `<span style="font-size:0.7rem; color:var(--dim); margin-top:2px; display:block;">Par ${escapeHtml(addon.author)}</span>` : ''}
          </div>
          <div style="display:flex; align-items:center; gap:10px;">
            <label style="position:relative; display:inline-block; width:38px; height:20px; cursor:pointer;">
              <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleAddonSwitch('${addon.id}', this.checked)" style="opacity:0; width:0; height:0;">
              <span style="position:absolute; cursor:pointer; top:0; left:0; right:0; bottom:0; background:${isEnabled ? '#4ade80' : 'rgba(255,255,255,0.15)'}; border-radius:20px; transition:.2s;"></span>
              <span style="position:absolute; content:''; height:14px; width:14px; left:${isEnabled ? '20px' : '3px'}; bottom:3px; background:white; border-radius:50%; transition:.2s;"></span>
            </label>
          </div>
        </div>
      `;
    }).join('');
  } catch(e) {
    container.innerHTML = `<div style="color:#ef4444; font-size:0.8rem; text-align:center; padding:20px;">Erreur : ${e.message}</div>`;
  }
}

async function toggleAddonSwitch(addonId, enabled) {
  try {
    await api('/api/addons/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: addonId, enabled: enabled})
    });
    await loadAddonsList();
  } catch(e) {
    alert('Erreur activation add-on : ' + e.message);
    await loadAddonsList();
  }
}

async function uploadAddonFile(input) {
  if (!input || !input.files || input.files.length === 0) return;
  const file = input.files[0];
  const reader = new FileReader();
  reader.onload = async (e) => {
    try {
      const content = e.target.result;
      const base64 = btoa(new Uint8Array(content).reduce((data, byte) => data + String.fromCharCode(byte), ''));
      await api('/api/addons/install', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({filename: file.name, data: base64})
      });
      alert(`Add-on [ ${file.name} ] installé avec succès !`);
      await loadAddonsList();
    } catch(err) {
      alert("Erreur lors de l'installation de l'add-on : " + err.message);
    } finally {
      input.value = '';
    }
  };
  reader.readAsArrayBuffer(file);
}

// --- Animation Japonaise (Intégré dans le catalogue unifié KINO) ---

// --- Collections Communauté & Letterboxd Curated ---
let currentCommunityLists = [];

async function loadCommunityTab() {
  const grid = document.getElementById('communityListsGrid');
  if (!grid) return;
  grid.innerHTML = '<p style="color:var(--dim); font-size:0.84rem; grid-column:1/-1; text-align:center; padding:30px;">Chargement des tendances communautaires...</p>';

  try {
    const data = await api('/api/community/curated');
    let curated = data.collections || [];

    // Ajouter également les listes Letterboxd personnalisées déjà importées par l'utilisateur
    try {
      const customData = await api('/api/letterboxd/custom-lists');
      const customLists = (customData.lists || []).map(l => ({
        id: l.id,
        title: l.title || 'Ma liste Letterboxd',
        badge: 'IMPORTÉ',
        author: l.user ? `@${l.user}` : 'Vous',
        description: l.description || `${l.items ? l.items.length : 0} films importés depuis Letterboxd.`,
        count: l.items ? l.items.length : 0,
        items: l.items || [],
      }));
      curated = [...customLists, ...curated];
    } catch(err) {}

    currentCommunityLists = curated;
    renderCommunityListsGrid(currentCommunityLists);
  } catch(e) {
    grid.innerHTML = `<p style="color:#ef4444; font-size:0.84rem; grid-column:1/-1; text-align:center; padding:30px;">Erreur : ${e.message}</p>`;
  }
}

function renderCommunityListsGrid(lists) {
  const grid = document.getElementById('communityListsGrid');
  if (!grid) return;
  if (!lists || lists.length === 0) {
    grid.innerHTML = '<p style="color:var(--muted); font-size:0.84rem; grid-column:1/-1; text-align:center; padding:40px;">Aucune sélection disponible.</p>';
    return;
  }

  grid.innerHTML = lists.map(col => {
    return `
      <div style="background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; display:flex; flex-direction:column; justify-content:space-between; gap:12px; cursor:pointer; transition:transform 0.15s, border-color 0.15s;" onmouseover="this.style.borderColor='rgba(255,255,255,0.25)'; this.style.transform='translateY(-2px)';" onmouseout="this.style.borderColor='var(--border)'; this.style.transform='translateY(0)';" onclick="openCommunityList('${col.id}')">
        <div>
          <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
            <span class="badge" style="font-size:0.68rem; font-weight:700; background:rgba(255,255,255,0.08); color:var(--text); letter-spacing:0.5px;">${escapeHtml(col.badge || 'COLLECTION')}</span>
            <span class="badge" style="font-size:0.7rem; background:rgba(0,224,84,0.12); color:#00e054; border-color:rgba(0,224,84,0.25);">${col.count || (col.items ? col.items.length : 0)} films</span>
          </div>
          <h4 style="font-size:1.02rem; font-weight:700; margin:0 0 6px 0; color:#fafafa;">${escapeHtml(col.title)}</h4>
          <p style="color:var(--muted); font-size:0.78rem; line-height:1.4; margin:0;">${escapeHtml(col.description)}</p>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid rgba(255,255,255,0.06); padding-top:10px; margin-top:4px;">
          <span style="font-size:0.72rem; color:var(--dim);">Par ${escapeHtml(col.author || 'Communauté')}</span>
          <span style="font-size:0.75rem; color:#4ade80; font-weight:600;">Découvrir →</span>
        </div>
      </div>
    `;
  }).join('');
}

function openCommunityList(listId) {
  const col = currentCommunityLists.find(c => c.id === listId);
  if (!col) return;
  const activeSec = document.getElementById('communityActiveListSection');
  const titleEl = document.getElementById('communityActiveListTitle');
  const descEl = document.getElementById('communityActiveListDesc');
  const grid = document.getElementById('communityActiveListGrid');

  if (titleEl) titleEl.textContent = col.title;
  if (descEl) descEl.textContent = col.description;
  if (activeSec) {
    activeSec.style.display = 'block';
    activeSec.scrollIntoView({ behavior: 'smooth' });
  }

  if (grid) {
    grid.innerHTML = (col.items || []).map(m => {
      const inWl = isInWatchlist(m.id);
      const rating = m.imdbRating ? `★ ${m.imdbRating}` : '';
      return `
        <div class="card" onclick="selectMedia({id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})">
          <div class="card-poster-wrap">
            <img src="${m.poster || 'data:image/svg+xml,<svg xmlns=\"http://www.w3.org/2000/svg\"/>'}" alt="${escapeHtml(m.name)}" loading="lazy">
            ${rating ? `<span class="card-badge">${rating}</span>` : ''}
            <button class="card-wl-btn ${inWl ? 'in-list' : ''}" data-wl-id="${m.id}" onclick="toggleWatchlist(event, {id:'${m.id}', name:'${escapeJsString(m.name)}', type:'movie', poster:'${m.poster || ''}', year:'${m.year || ''}', imdbRating:'${m.imdbRating || ''}'})" title="${inWl ? 'Retirer' : 'Ajouter'}">${inWl ? '✓' : '+'}</button>
          </div>
          <div class="card-info">
            <div class="card-title">${escapeHtml(m.name)}</div>
            <div class="card-meta"><span>${m.year || ''}</span></div>
          </div>
        </div>
      `;
    }).join('');
  }
}

function closeCommunityActiveList() {
  const activeSec = document.getElementById('communityActiveListSection');
  if (activeSec) activeSec.style.display = 'none';
}

async function importCustomLetterboxdFromInput() {
  const inp = document.getElementById('communityLetterboxdInput');
  const val = inp ? inp.value.trim() : '';
  if (!val) {
    alert("Veuillez coller l'URL d'une liste Letterboxd.");
    return;
  }
  openLetterboxdModal();
  switchLbxModalTab('list');
  const listInp = document.getElementById('lbxListInput');
  if (listInp) listInp.value = val;
  importLetterboxdList();
}

function searchByPerson(name, mtype) {
  if (!name) return;
  const targetType = (mtype === 'series') ? 'series' : 'movie';
  document.getElementById('searchType').value = targetType;
  toggleSearchMode();
  const inp = document.getElementById('searchInput');
  inp.value = name;
  runSearch();
  window.scrollTo({top: 0, behavior: 'smooth'});
}

async function toggleWatchedItem(mediaObj) {
  if (!mediaObj || !mediaObj.id) return;
  const res = await api('/api/watched-toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(mediaObj)
  });
  userHistory = res.history || [];
  updateListBadges();
  renderHomeResume();
  if (activeTab === 'watched' || activeTab === 'watchlist') {
    renderActiveListTab();
  } else if (catalogItems.length && (activeTab === 'movies' || activeTab === 'series')) {
    renderPosterCards(catalogItems, activeTab === 'series' ? 'series' : 'movie');
  }
  if (currentMedia && currentMedia.id === mediaObj.id) {
    selectMedia(currentMedia);
  }
}

async function toggleWatchedSeriesEp(seasonNum, epNum) {
  if (!currentMedia || !currentMedia.id) return;
  const res = await api('/api/watched-toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      id: currentMedia.id,
      name: currentMedia.name,
      type: 'series',
      year: currentMedia.year || '',
      poster: currentMedia.poster || '',
      season: seasonNum,
      episode: epNum
    })
  });
  userHistory = res.history || [];
  updateListBadges();
  renderHomeResume();
  renderDetailEpisodes(seasonNum);
  const histItem = userHistory.find(h => h.id === currentMedia.id);
  const mainPlayBtn = document.getElementById('detailMainPlayBtn');
  if (mainPlayBtn && histItem) {
    const targetEp = getSeriesTargetEpisode(histItem);
    const epCode = `S${String(targetEp.season).padStart(2,'0')}E${String(targetEp.episode).padStart(2,'0')}`;
    mainPlayBtn.textContent = targetEp.isNext ? `Épisode suivant (${epCode})` : `Lancer ${epCode}`;
  }
}

async function toggleWatchedWholeSeason(customSeason = null) {
  if (!currentMedia || !currentMedia.id) return;
  const s = Number(customSeason || (document.getElementById('seasonSelect') ? document.getElementById('seasonSelect').value : 1) || 1);
  const eps = seriesMetaVideos.filter(v => v.season === s).map(v => Number(v.episode || v.number || 0)).filter(n => n > 0);
  const res = await api('/api/watched-toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      id: currentMedia.id,
      name: currentMedia.name,
      type: 'series',
      year: currentMedia.year || '',
      poster: currentMedia.poster || '',
      season: s,
      season_all: true,
      episodes: eps
    })
  });
  userHistory = res.history || [];
  updateListBadges();
  renderHomeResume();
  renderDetailEpisodes(s);
  const histItem = userHistory.find(h => h.id === currentMedia.id);
  const mainPlayBtn = document.getElementById('detailMainPlayBtn');
  if (mainPlayBtn && histItem) {
    const targetEp = getSeriesTargetEpisode(histItem);
    const epCode = `S${String(targetEp.season).padStart(2,'0')}E${String(targetEp.episode).padStart(2,'0')}`;
    mainPlayBtn.textContent = targetEp.isNext ? `Épisode suivant (${epCode})` : `Lancer ${epCode}`;
  }
}

// --- Glisser-Déposer (Drag & Drop) de fichiers .torrent et liens Magnet ---
async function handleTorrentFileSelect(files) {
  if (!files || !files.length) return;
  const file = files[0];
  const box = document.getElementById('debridResultPanel');
  if (box) {
    box.style.display = 'block';
    box.scrollIntoView({ behavior: 'smooth' });
    box.innerHTML = `<h3>Lecture du fichier ${file.name}...</h3><p style="color:var(--muted); font-size:0.83rem; margin-top:4px;">Extraction de l'empreinte BitTorrent et débridage Cloud...</p>`;
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
    if (box) box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur fichier .torrent : ${e.message}</p>`;
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

function toggleShortcutsModal(initialTab = 'keyboard') {
  const m = document.getElementById('shortcutsModal');
  if (!m) return;
  const isOpen = m.style.display === 'flex';
  m.style.display = isOpen ? 'none' : 'flex';
  if (!isOpen) {
    switchShortcutTab(initialTab);
  }
}

function switchShortcutTab(tab) {
  const btnKbd = document.getElementById('btnShortcutKeyboard');
  const btnGp = document.getElementById('btnShortcutGamepad');
  const secKbd = document.getElementById('shortcutSectionKeyboard');
  const secGp = document.getElementById('shortcutSectionGamepad');

  if (tab === 'gamepad') {
    if (btnGp) btnGp.classList.add('active');
    if (btnKbd) btnKbd.classList.remove('active');
    if (secGp) secGp.style.display = 'grid';
    if (secKbd) secKbd.style.display = 'none';
  } else {
    if (btnKbd) btnKbd.classList.add('active');
    if (btnGp) btnGp.classList.remove('active');
    if (secKbd) secKbd.style.display = 'grid';
    if (secGp) secGp.style.display = 'none';
  }
}

function initSearchPlatformShortcuts() {
  const isMac = (navigator.platform && navigator.platform.toUpperCase().indexOf('MAC') >= 0) || (navigator.userAgent && navigator.userAgent.toUpperCase().indexOf('MAC') >= 0);
  const kbd = document.getElementById('searchKbdBadge');
  if (kbd) kbd.textContent = isMac ? '⌘K' : 'Ctrl+K';
  const inp = document.getElementById('searchInput');
  if (inp && !inp.value) {
    inp.placeholder = `Rechercher un film, une série ou un anime... (${isMac ? '⌘K' : 'Ctrl+K'})`;
  }
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
  if (activeTab === 'movies' || activeTab === 'series') {
    switchTab(activeTab);
  }
}

function toggleSearchMode() {
  const mode = document.getElementById('searchType').value;
  const inp = document.getElementById('searchInput');
  hideSearchDropdown();
  const isMac = (navigator.platform && navigator.platform.toUpperCase().indexOf('MAC') >= 0) || (navigator.userAgent && navigator.userAgent.toUpperCase().indexOf('MAC') >= 0);
  const kbdTxt = isMac ? '⌘K' : 'Ctrl+K';
  if (mode === 'magnet') inp.placeholder = 'Coller un lien magnet:?xt=urn:btih:...';
  else if (mode === 'raw') inp.placeholder = 'Recherche par mots-clés...';
  else inp.placeholder = `Rechercher un film, une série ou un anime... (${kbdTxt})`;
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
  if (!q) {
    hideSearchDropdown();
    if (activeTab === 'movies' || activeTab === 'series') {
      switchTab(activeTab);
    }
    return;
  }
  if (q.length < 2) {
    hideSearchDropdown();
    return;
  }
  // Interroger immédiatement SQLite FTS5 pour autocomplétion instantanée
  fetchSearchSuggestions(q, mode);
}

async function fetchSearchSuggestions(q, mode) {
  try {
    const res = await api(`/api/search-suggest?q=${encodeURIComponent(q)}&type=${encodeURIComponent(mode)}`);
    const results = (res && res.results) || [];
    renderSearchDropdown(results, q);
  } catch (e) {
    hideSearchDropdown();
  }
}

function renderSearchDropdown(items, query) {
  const dd = document.getElementById('searchDropdown');
  if (!dd) return;
  searchSuggestItems = items || [];
  searchSuggestIndex = -1;
  if (!items.length) {
    dd.style.display = 'none';
    dd.innerHTML = '';
    return;
  }
  dd.innerHTML = items.map((it, idx) => {
    const poster = it.poster || '';
    const typeLabel = it.type === 'series' ? 'Série' : 'Film';
    const rating = it.imdbRating ? `★ ${it.imdbRating}` : '';
    return `
      <div class="search-suggest-item" id="suggestItem_${idx}" onclick="selectSearchSuggest(${idx})">
        <img class="search-suggest-poster" src="${poster}" alt="" onerror="this.style.opacity=0.08">
        <div class="search-suggest-info">
          <div class="search-suggest-title">${it.name}</div>
          <div class="search-suggest-meta">
            <span class="badge" style="font-size:0.68rem; padding:1px 5px;">${typeLabel}</span>
            ${it.year ? `<span>${it.year}</span>` : ''}
            ${rating ? `<span style="color:#fafafa; font-weight:600;">${rating}</span>` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');
  dd.style.display = 'block';
}

function hideSearchDropdown() {
  const dd = document.getElementById('searchDropdown');
  if (dd) {
    dd.style.display = 'none';
    dd.innerHTML = '';
  }
  searchSuggestItems = [];
  searchSuggestIndex = -1;
}

function selectSearchSuggest(idx) {
  const it = searchSuggestItems[idx];
  hideSearchDropdown();
  if (!it) return;
  const inp = document.getElementById('searchInput');
  if (inp) inp.value = it.name;
  selectMedia(it);
}

function onSearchKeyDown(e) {
  const dd = document.getElementById('searchDropdown');
  const isDropdownVisible = dd && dd.style.display === 'block';

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
    if (e.key === 'Escape') {
      e.preventDefault();
      hideSearchDropdown();
      return;
    }
  } else if (e.key === 'Enter') {
    hideSearchDropdown();
    runSearch();
  }
}

function updateSelectedSuggest() {
  document.querySelectorAll('.search-suggest-item').forEach((el, idx) => {
    el.classList.toggle('selected', idx === searchSuggestIndex);
  });
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

  if (mode === 'magnet') {
    debridMagnet(q);
    return;
  }

  document.getElementById('detailPanel').style.display = 'none';
  document.getElementById('rdCloudPanel').style.display = 'none';
  document.getElementById('homeResumeSection').style.display = 'none';
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
  document.getElementById('catalogTitle').textContent = `Résultats pour "${q}"`;
  const grid = document.getElementById('postersGrid');
  grid.innerHTML = '<p style="color:var(--dim); font-size:0.85rem;">Recherche en cours...</p>';
  document.getElementById('torrentsPanel').style.display = 'none';

  try {
    const data = await api(`/api/search?type=${mode}&q=${encodeURIComponent(q)}`);
    if (!data.metas || !data.metas.length) {
      grid.innerHTML = '';
      loadTorrents({q, title: q});
      return;
    }
    renderPosterCards(data.metas.slice(0, 36), mode);
  } catch (e) {
    grid.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${e.message}</p>`;
  }
}

async function oneClickCard(ev, btn, media) {
  ev.stopPropagation();
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
      year: media.year || ''
    }, btn);
  } else {
    await oneClickPlay({
      imdb_id: media.id,
      type: 'movie',
      title: `${media.name}${media.year ? ' (' + media.year + ')' : ''}`,
      name: media.name,
      poster: media.poster || '',
      year: media.year || ''
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

let _isGlobalPlayLaunching = false;
async function oneClickPlay(params, btn) {
  if (_isGlobalPlayLaunching) return;
  _isGlobalPlayLaunching = true;
  setTimeout(() => { _isGlobalPlayLaunching = false; }, 3000);

  if (btn && btn.disabled) return;
  const origHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = 'Play...';
  }

  const isMacPlatform = /Mac/i.test(navigator.platform || navigator.userAgent);
  if (!params.player_mode) {
    params.player_mode = window.kinoPlayerMode || (isMacPlatform ? 'integrated' : 'kino');
  }

  const box = document.getElementById('debridResultPanel');
  box.style.display = 'block';
  box.scrollIntoView({behavior: 'smooth'});
  box.innerHTML = `
    <h3>${params.title}</h3>
    <p style="color:var(--muted); font-size:0.83rem; margin-top:4px;">Lancement du flux...</p>
  `;

  try {
    const res = await api('/api/one-click-play', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params)
    });
    if (btn) btn.innerHTML = 'Lancé';
    const plCount = (res.mpv && res.mpv.playlist_count) ? res.mpv.playlist_count : 1;
    renderDebridState(res.debrid, params.season, params.episode, res.chosen_torrent, plCount);
    refreshUserLists();

    const isIntegrated = res.mpv?.mode === 'integrated' || params.player_mode === 'integrated' || (params.player_mode !== 'external' && isMacPlatform);
    if (res.stream_url && isIntegrated) {
      const pl = (res.playlist && res.playlist.length) ? res.playlist : [{ title: params.title, url: res.stream_url }];
      openInAppPlayer(res.stream_url, params.title, pl, currentMedia, res.resume_sec || 0);
    }

  } catch (e) {
    box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${e.message}</p>`;
  } finally {
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }, 2500);
    }
  }
}

function buildDetailHtml(media, meta, isPreview) {
  const isSeries = Boolean(media.type === 'series' || meta.type === 'series' || (meta.videos && meta.videos.length > 0) || (media.is_anime && media.type !== 'movie'));
  const poster = meta.poster || media.poster || '';
  const year = meta.releaseInfo || meta.year || media.year || '';
  const rating = meta.imdbRating || media.imdbRating || '';
  const runtime = meta.runtime || '';
  const rawGenres = (meta.genres || meta.genre || media.genres || []).slice(0, 5);
  const genres = (meta.genres_fr || rawGenres).slice(0, 5);

  let desc = meta.description_fr || meta.description || media.overview || media.description || '';
  if (!desc && isPreview) {
    desc = '<div class="detail-skeleton-line" style="width:90%;"></div><div class="detail-skeleton-line" style="width:75%; margin-top:6px;"></div>';
  } else if (!desc) {
    desc = 'Aucun synopsis disponible.';
  }

  const castArr = (meta.cast || []).slice(0, 7);
  const dirArr = Array.isArray(meta.director) ? meta.director : (meta.director ? [meta.director] : []);
  const mtypeSafe = JSON.stringify(media.type || 'movie').replace(/'/g, "&#39;");
  const castLinks = castArr.map(p => {
    const pJs = JSON.stringify(p).replace(/'/g, "&#39;");
    return `<span onclick='searchByPerson(${pJs}, ${mtypeSafe})' title="Voir les titres avec ${p}" style="cursor:pointer; color:var(--text); text-decoration:underline; text-decoration-color:var(--border-hover); text-underline-offset:3px;">${p}</span>`;
  }).join(', ');
  const dirLinks = dirArr.map(d => {
    const dJs = JSON.stringify(d).replace(/'/g, "&#39;");
    return `<span onclick='searchByPerson(${dJs}, ${mtypeSafe})' title="Voir les réalisations de ${d}" style="cursor:pointer; color:var(--text); text-decoration:underline; text-decoration-color:var(--border-hover); text-underline-offset:3px;">${d}</span>`;
  }).join(', ');
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
  const watchedEpsList = (histItem && Array.isArray(histItem.watched_episodes)) ? histItem.watched_episodes : [];
  const latestAired = meta.latest_aired || null;
  const latestAiredUnwatched = Boolean(latestAired && latestAired.code && !watchedEpsList.includes(latestAired.code));

  let targetSeason = 1;
  let targetEpisode = 1;
  let playBtnLabel = 'Play';
  if (isSeries) {
    const targetEp = getSeriesTargetEpisode(histItem);
    targetSeason = targetEp.season;
    targetEpisode = targetEp.episode;
    const epCode = `S${String(targetSeason).padStart(2,'0')}E${String(targetEpisode).padStart(2,'0')}`;
    playBtnLabel = targetEp.isNext ? `Épisode suivant (${epCode})` : `Lancer ${epCode}`;
  } else if (histItem && histItem.progress_pct > 1 && histItem.progress_pct < 85) {
    playBtnLabel = `Reprendre (${Math.round(histItem.progress_pct)}%)`;
  } else if (isMovieDone) {
    playBtnLabel = 'Revoir le film';
  }

  const backdropUrl = meta.background || meta.backdrop || media.backdrop || media.background || (media.id && String(media.id).startsWith('tt') ? `https://images.metahub.space/background/medium/${media.id}/img` : (poster || ''));

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
          <div class="detail-extra-section">
            <div style="display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:center; margin-bottom:12px;">
              <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
                <span style="font-size:0.82rem; font-weight:600; color:var(--muted); margin-right:4px;">Saisons :</span>
                ${seasons.map(s => `<button class="season-pill ${s===targetSeason?'active':''}" data-season-chip="${s}" onclick="selectDetailSeason(${s})">Saison ${s}</button>`).join('')}
              </div>
              <button class="btn btn-secondary" id="detailSeasonAllWatchedBtn" style="padding:6px 12px; font-size:0.75rem;" onclick="toggleWatchedWholeSeason()">✓ Marquer la saison comme vue</button>
            </div>
            <div id="detailEpisodesList" class="episodes-grid"></div>
          </div>
        `;
      }
    }
  }

  const similarList = Array.isArray(meta.similar) ? meta.similar : [];
  const similarHtml = similarList.length ? `
    <div class="detail-extra-section">
      <div style="font-size:0.8rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted); margin-bottom:14px;">Vous aimerez aussi</div>
      <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(130px, 1fr)); gap:12px;">
        ${similarList.map(sm => {
          const smPayload = JSON.stringify(sm).replace(/'/g, "&#39;");
          return `
            <div class="poster-card" onclick='selectMedia(${smPayload})'>
              <div style="position:relative; width:100%; aspect-ratio:2/3; overflow:hidden;">
                <img src="${sm.poster || ''}" alt="${sm.name}" loading="lazy" onerror="this.style.opacity=0.08">
                <div class="poster-play-hint">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="#fff"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                </div>
              </div>
              <div class="poster-info" style="padding:8px;">
                <div class="poster-title" style="font-size:0.78rem;">${sm.name}</div>
                <div class="poster-year" style="font-size:0.7rem; margin-top:2px;">${sm.year || ''}${sm.imdbRating ? ' • ★ ' + sm.imdbRating : ''}</div>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  ` : '';

  const latestAiredBanner = (isSeries && latestAired) ? `
    <div style="display:inline-flex; align-items:center; gap:8px; background:rgba(255,255,255,0.06); border:1px solid ${latestAiredUnwatched ? 'rgba(255,255,255,0.4)' : 'var(--border)'}; padding:5px 12px; border-radius:6px; font-size:0.76rem; color:${latestAiredUnwatched ? '#fafafa' : 'var(--muted)'}; margin-top:2px; width:fit-content;">
      <span>${latestAiredUnwatched ? '● Dernier épisode diffusé (non vu) :' : '✓ Dernier épisode diffusé :'} <strong>${latestAired.code}</strong> — ${latestAired.title} (${latestAired.released})</span>
      ${latestAiredUnwatched ? `<button class="btn" style="padding:2px 8px; font-size:0.7rem;" onclick="oneClickSeriesEpisode(this, ${latestAired.season}, ${latestAired.episode})">Play</button>` : ''}
    </div>
  ` : '';

  return {
    isSeries,
    targetSeason,
    targetEpisode,
    html: `
      <div class="detail-backdrop-wrap">
        <img class="detail-backdrop-img" src="${backdropUrl}" alt="" onerror="this.style.opacity='0';">
        <div class="detail-backdrop-gradient"></div>
        <button class="detail-close-btn" onclick="closeDetailPanel()" title="Fermer la fiche">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
        <div class="detail-hero-content">
          <img class="detail-poster-cinematic" src="${poster}" alt="${media.name}" onerror="this.style.opacity=0.08">
          <div class="detail-info-block">
            <div class="detail-title-cinematic">${meta.name || media.name}</div>
            <div class="detail-meta-row">
              ${userRating ? `<span class="badge" style="background:rgba(245,158,11,0.16); border:1px solid rgba(245,158,11,0.4); color:#fbbf24; font-weight:700;"><svg width="12" height="12" viewBox="0 0 24 24" fill="#fbbf24" style="vertical-align:-1px; margin-right:3px;"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg> ${userRating}/5 (Ma note)</span>` : ''}
              ${rating ? `<span class="badge-imdb-gold"><svg width="12" height="12" viewBox="0 0 24 24" fill="#f5c518"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg> ${rating} IMDb</span>` : ''}
              ${year ? `<span class="badge" style="font-weight:600;">${year}</span>` : ''}
              ${runtime ? `<span>• ${runtime}</span>` : ''}
              ${country ? `<span>• ${country.split(',').slice(0,2).join(', ')}</span>` : ''}
              ${genres.map((g, idx) => {
                const rawG = rawGenres[idx] || g;
                const rawGJs = JSON.stringify(rawG).replace(/'/g, "&#39;");
                return `<span class="badge" style="cursor:pointer;" title="Filtrer par ${g}" onclick='selectGenre(${rawGJs}, document.querySelector("#genreFilters .chip[data-genre=\\"" + ${rawGJs} + "\\"]"))'>${g}</span>`;
              }).join('')}
            </div>
            ${latestAiredBanner}
            <div class="detail-desc">${desc}</div>
            <div class="detail-credits">
              ${dirLinks ? `<div><strong>Réalisation :</strong> ${dirLinks}</div>` : ''}
              ${castLinks ? `<div><strong>Distribution :</strong> ${castLinks}</div>` : ''}
              ${awards ? `<div style="color:var(--dim); margin-top:2px;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px; margin-right:4px;"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.45 1-1 1H8v2h8v-2h-1c-.55 0-1-.45-1-1v-2.34c3.27-.47 5.73-3.23 6-6.66H4c.27 3.43 2.73 6.19 6 6.66z"/></svg>${awards}</div>` : ''}
            </div>
            <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px; align-items:center;">
              <button class="btn btn-play-hero" id="detailMainPlayBtn" onclick='oneClickCard(event, this, ${mediaPayload})'>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" style="margin-right:2px;"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                ${playBtnLabel}
              </button>
              <button class="btn btn-secondary" onclick='openTrailerModal(${JSON.stringify(trailerId)}, ${safeNameJs}, ${safeYearJs}, "vf")'>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px; margin-right:4px;"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                Bande-annonce
              </button>
              <button class="btn btn-secondary" id="detailWlBtn" onclick='toggleWatchlist(event, ${mediaPayload})'>
                ${inList ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="margin-right:4px;"><polyline points="20 6 9 17 4 12"/></svg> Dans ma liste' : '+ Ma Liste'}
              </button>
              ${!isSeries ? `<button class="btn btn-secondary" style="${isMovieDone ? 'border-color:var(--text); color:var(--text);' : ''}" onclick='toggleWatchedItem(${mediaPayload})'>${isMovieDone ? '✓ Vu' : '✓ Marquer comme vu'}</button>` : ''}
              ${media.id && String(media.id).startsWith('tt') ? `<a class="btn btn-secondary" href="https://www.imdb.com/title/${encodeURIComponent(media.id)}/" target="_blank" style="padding:7px 11px; font-size:0.76rem;" title="Voir la fiche sur IMDb">IMDb ↗</a>` : ''}
              ${media.id && String(media.id).startsWith('tt') && !isSeries ? `<a class="btn btn-secondary" href="https://letterboxd.com/imdb/${encodeURIComponent(media.id)}/" target="_blank" style="padding:7px 11px; font-size:0.76rem; border-color:rgba(0,224,84,0.35);" title="Voir sur Letterboxd">Letterboxd ↗</a>` : ''}
            </div>
          </div>
        </div>
      </div>
      ${episodesHtml}
      ${similarHtml}
    `
  };
}

function applyMediaDetails(media, meta) {
  const detail = document.getElementById('detailPanel');
  const sc = document.getElementById('seriesControls');
  const rendered = buildDetailHtml(media, meta, false);
  detail.innerHTML = rendered.html;

  if (rendered.isSeries) {
    media.type = 'series';
    currentMedia.type = 'series';
    sc.style.display = 'flex';
    const sSel = document.getElementById('seasonSelect');
    const seasons = [...new Set(seriesMetaVideos.map(v => v.season))].sort((a,b) => a - b);
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
}

async function selectMedia(media) {
  currentMedia = media;
  const reqId = media.id;
  currentSelectMediaId = reqId;

  const setP = document.getElementById('settingsPanel');
  if (setP) setP.style.display = 'none';
  const setTab = document.getElementById('tab-settings');
  if (setTab) setTab.classList.remove('active');
  const detail = document.getElementById('detailPanel');
  detail.style.display = 'block';

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

function closeDetailPanel() {
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
  const eps = seriesMetaVideos.filter(v => v.season === seasonNum).sort((a,b) => (a.episode || a.number) - (b.episode || b.number));
  if (!eps.length) {
    container.innerHTML = '<p style="color:var(--dim); font-size:0.82rem;">Aucun détail d\'épisode disponible.</p>';
    return;
  }
  const histItem = currentMedia ? userHistory.find(h => h.id === currentMedia.id) : null;
  const watchedList = (histItem && Array.isArray(histItem.watched_episodes)) ? histItem.watched_episodes : [];
  const epPosMap = (histItem && histItem.ep_positions) ? histItem.ep_positions : {};

  const seasonCodes = eps.map(v => `S${String(seasonNum).padStart(2,'0')}E${String(v.episode || v.number || 1).padStart(2,'0')}`);
  const isAllSeasonWatched = seasonCodes.length > 0 && seasonCodes.every(c => watchedList.includes(c));
  for (const btnId of ['detailSeasonAllWatchedBtn', 'markSeasonWatchedBtn']) {
    const sBtn = document.getElementById(btnId);
    if (sBtn) {
      sBtn.textContent = isAllSeasonWatched ? `✓ Saison ${seasonNum} vue` : `✓ Marquer saison ${seasonNum} vue`;
      sBtn.style.borderColor = isAllSeasonWatched ? '#fafafa' : '';
      sBtn.style.color = isAllSeasonWatched ? '#fafafa' : '';
    }
  }

  container.innerHTML = eps.map(v => {
    const epNum = v.episode || v.number || 1;
    const epTitle = v.name || v.title || `Épisode ${epNum}`;
    const epOverview = v.overview || v.description || '';
    const thumb = v.thumbnail || (currentMedia && currentMedia.poster) || '';
    const code = `S${String(seasonNum).padStart(2,'0')}E${String(epNum).padStart(2,'0')}`;
    const isWatched = watchedList.includes(code);
    const epProg = epPosMap[code] ? Number(epPosMap[code].pct || 0) : 0;
    const watchedBadge = isWatched
      ? '<span class="badge" style="border-color:var(--text); color:var(--text); font-weight:600;">✓ Vu</span>'
      : (epProg > 1 ? `<span class="badge badge-hi">${Math.round(epProg)}%</span>` : '');
    const progBar = epProg > 0 ? `
      <div style="height:3px; background:var(--surface-2); border-radius:2px; overflow:hidden; margin-top:6px; max-width:260px;">
        <div style="height:100%; width:${Math.min(100, epProg)}%; background:#ffffff;"></div>
      </div>
    ` : '';
    return `
      <div class="ep-card" style="${isWatched ? 'opacity:0.75;' : ''}">
        <div class="ep-thumb-wrap" onclick="oneClickSeriesEpisode(this, ${seasonNum}, ${epNum})" style="cursor:pointer;" title="Lancer ${code}">
          <img class="ep-thumb" src="${thumb}" alt="" loading="lazy" onerror="this.style.opacity=0.08">
          <div class="ep-thumb-overlay">
            <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </div>
        </div>
        <div class="ep-info">
          <div class="ep-title"><span class="badge badge-hi">${code}</span> <span id="filler-badge-${seasonNum}-${epNum}"></span> ${watchedBadge}${epTitle}</div>
          ${epOverview ? `<div class="ep-desc">${epOverview}</div>` : ''}
          ${progBar}
        </div>
        <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
          <button class="btn" style="padding:6px 12px; font-size:0.78rem;" onclick="oneClickSeriesEpisode(this, ${seasonNum}, ${epNum})">${epProg > 1 && epProg < 92 ? 'Reprendre' : 'Play'}</button>
          <button class="btn btn-secondary" style="padding:6px 10px; font-size:0.78rem;" onclick="pickSeriesEpisodeSources(${seasonNum}, ${epNum})">Sources</button>
          <button class="btn btn-secondary" style="padding:6px 9px; font-size:0.75rem; ${isWatched ? 'border-color:var(--text); color:var(--text);' : ''}" title="${isWatched ? 'Démarquer comme vu' : 'Marquer cet épisode comme vu'}" onclick="toggleWatchedSeriesEp(${seasonNum}, ${epNum})">${isWatched ? '✓ Vu' : '✓'}</button>
        </div>
      </div>
    `;
  }).join('');

  if (currentMedia && (currentMedia.type === 'series' || currentMedia.is_anime || activeTab === 'anime')) {
    const title = currentMedia.name || '';
    eps.forEach(v => {
      const epNum = v.episode || v.number || 1;
      api(`/api/anime/filler?title=${encodeURIComponent(title)}&episode=${epNum}`).then(res => {
        const badgeEl = document.getElementById(`filler-badge-${seasonNum}-${epNum}`);
        if (badgeEl && res && res.filler) {
          const f = res.filler;
          if (f.is_filler) {
            badgeEl.innerHTML = `<span class="badge" style="background:rgba(249,115,22,0.18); color:#f97316; border-color:rgba(249,115,22,0.4);" title="${f.type || 'Filler'}">Hors-Série</span>`;
          } else {
            badgeEl.innerHTML = `<span class="badge" style="background:rgba(74,222,128,0.18); color:#4ade80; border-color:rgba(74,222,128,0.4);" title="Manga Canon">Canon</span>`;
          }
        }
      }).catch(() => {});
    });
  }
}

function pickSeriesEpisodeSources(s, e) {
  document.getElementById('seasonSelect').value = String(s);
  onSeasonChange(false);
  document.getElementById('episodeSelect').value = String(e);
  reloadSeriesEpisode(true);
}

function onSeasonChange(triggerReload = true) {
  const s = parseInt(document.getElementById('seasonSelect').value || '1', 10);
  const eSel = document.getElementById('episodeSelect');
  const eps = seriesMetaVideos.filter(v => v.season === s).sort((a,b) => (a.episode || a.number) - (b.episode || b.number));
  if (eps.length) {
    eSel.innerHTML = eps.map(v => {
      const epNum = v.episode || v.number;
      const epTitle = v.name || v.title || `Épisode ${epNum}`;
      return `<option value="${epNum}">E${String(epNum).padStart(2,'0')} — ${epTitle}</option>`;
    }).join('');
  } else {
    eSel.innerHTML = Array.from({length: 24}, (_, i) => `<option value="${i+1}">Épisode ${i+1}</option>`).join('');
  }
  if (triggerReload) reloadSeriesEpisode(false);
}

function reloadSeriesEpisode(scrollToTorrents = false) {
  if (!currentMedia) return;
  const s = document.getElementById('seasonSelect').value || 1;
  const e = document.getElementById('episodeSelect').value || 1;
  loadTorrents({
    imdb_id: currentMedia.id,
    type: 'series',
    season: s,
    episode: e,
    title: `${currentMedia.name} — S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`
  }, scrollToTorrents);
}

function extractTorrentSizeGb(t) {
  if (t.size_gb !== undefined && t.size_gb !== null && !isNaN(t.size_gb) && Number(t.size_gb) > 0) {
    return Number(t.size_gb);
  }
  const text = (t.meta || '') + ' ' + (t.title || '');
  const mGb = text.match(/(\d+(?:\.\d+)?)\s*(?:GB|GiB)/i);
  if (mGb) return parseFloat(mGb[1]);
  const mMb = text.match(/(\d+(?:\.\d+)?)\s*(?:MB|MiB)/i);
  if (mMb) return parseFloat(mMb[1]) / 1024.0;
  return 0;
}

function extractTorrentSeeders(t) {
  if (t.seeders !== undefined && t.seeders !== null && !isNaN(t.seeders) && Number(t.seeders) > 0) {
    return Number(t.seeders);
  }
  const text = (t.meta || '') + ' ' + (t.title || '');
  const mSeed = text.match(/(\d+)\s*(?:seeders?|seeds?|pairs?)/i) || text.match(/(?:👤|peers?|seeds?|seeders?)\s*(\d+)/i);
  if (mSeed) return parseInt(mSeed[1], 10);
  return 0;
}

async function loadTorrents(params, scroll = true) {
  const panel = document.getElementById('torrentsPanel');
  const list = document.getElementById('torrentsList');
  panel.style.display = 'block';
  document.getElementById('torrentsHeading').textContent = params.title;
  document.getElementById('torrentsSub').textContent = 'Recherche des sources...';
  list.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Chargement...</p>';
  if (scroll) panel.scrollIntoView({behavior: 'smooth'});

  if (!params.runtime && currentMedia && currentMedia.runtime) {
    params.runtime = currentMedia.runtime;
  }
  if (!params.year && currentMedia && (currentMedia.year || currentMedia.releaseInfo)) {
    params.year = currentMedia.year || currentMedia.releaseInfo;
  }
  if (!params.q && currentMedia && currentMedia.name) {
    params.q = currentMedia.name;
  }

  const qs = new URLSearchParams(params).toString();
  try {
    const data = await api('/api/torrents?' + qs);
    allTorrents = data.torrents || [];
    renderTorrents();
  } catch (e) {
    list.innerHTML = `<p style="color:var(--muted); font-size:0.84rem;">Erreur : ${e.message}</p>`;
  }
}

function setFilter(flt, el) {
  activeFilter = flt;
  document.querySelectorAll('.filters .chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  renderTorrents();
}

function getFilteredTorrents() {
  const custom = document.getElementById('customFilter')?.value.trim().toLowerCase() || '';
  const words = custom ? custom.split(/\s+/).filter(Boolean) : [];
  const isMovie = !currentMedia || currentMedia.type !== 'series';
  const rm = currentMedia && currentMedia.runtime ? parseInt(currentMedia.runtime, 10) : 0;

  let filtered = allTorrents.filter(t => {
    const quals = t.qualities || [];
    const hay = (t.title + ' ' + t.meta + ' ' + t.source + ' ' + quals.join(' ') + ' ' + (t.langs || []).join(' ')).toLowerCase();

    // Filtre des torrents frauduleux / CAM réencodés trop légers pour leurs caractéristiques
    const size = extractTorrentSizeGb(t);
    const is4k = quals.includes('4K') || /\b(2160p|4k|uhd)\b/i.test(t.title);
    const is1080 = quals.includes('1080p') || /\b(1080p|fhd)\b/i.test(t.title);

    if (/\b(CAM|HDCAM|CAMRIP|TS|HDTS|TELESYNC|TELECINE|SCR|SCREENER|DVDSCREENER|WP|WORKPRINT)\b/i.test(t.title)) {
      return false;
    }

    if (isMovie && size > 0) {
      let min4k = 4.8;
      if (rm > 70) min4k = Math.max(4.8, (rm * 60 * 6.0) / (8 * 1024));
      if (is4k && size < min4k) return false;

      let min1080 = 0.75;
      if (rm > 70) min1080 = Math.max(0.75, (rm * 60 * 1.5) / (8 * 1024));
      if (is1080 && size < min1080) return false;

      if (size < 0.15) return false;
    } else if (!isMovie && size > 0) {
      if (is4k && size < 1.3) return false;
      if (is1080 && size < 0.22) return false;
      if (size < 0.08) return false;
    }

    if (activeFilter === 'fr') {
      if (!(t.langs && t.langs.length) && !/(multi|french|vff|vfq|vostfr|truefrench|\bfr\b)/i.test(hay)) return false;
    } else if (activeFilter === 'sdr') {
      if (quals.includes('HDR') || quals.includes('DV') || /\b(hdr|hdr10|dv|dovi|dolby[\s\.\-]*vision)\b/i.test(t.title || '')) return false;
    } else if (activeFilter === '4k') {
      if (!quals.includes('4K') && !/(2160p|4k|uhd)/i.test(hay)) return false;
    } else if (activeFilter === '1080p') {
      if (!quals.includes('1080p') && !/(1080p|fhd)/i.test(hay)) return false;
    } else if (activeFilter && !hay.includes(activeFilter)) {
      return false;
    }
    if (words.length && !words.every(w => hay.includes(w))) return false;
    return true;
  });

  const sortMode = document.getElementById('torrentSortSelect')?.value || 'default';
  if (sortMode === 'seeds') {
    filtered.sort((a, b) => extractTorrentSeeders(b) - extractTorrentSeeders(a));
  } else if (sortMode === 'size_desc') {
    filtered.sort((a, b) => extractTorrentSizeGb(b) - extractTorrentSizeGb(a));
  } else if (sortMode === 'size_asc') {
    filtered.sort((a, b) => {
      const sa = extractTorrentSizeGb(a);
      const sb = extractTorrentSizeGb(b);
      if (!sa && !sb) return 0;
      if (!sa) return 1;
      if (!sb) return -1;
      return sa - sb;
    });
  }

  return filtered;
}

function scoreTorrent(t) {
  let score = 0;
  const seeds = extractTorrentSeeders(t);
  score += Math.min(seeds * 2, 80);

  const isCached = (t.source && t.source.includes('+')) || (t.qualities && t.qualities.some(q => q.endsWith('+')));
  if (isCached) score += 150;

  const titleUpper = (t.title || '').toUpperCase();
  const langs = (t.langs || []).map(l => l.toUpperCase());
  if (langs.includes('MULTI') || titleUpper.includes('MULTI') || langs.includes('VFF') || langs.includes('VF') || titleUpper.includes('FRENCH')) {
    score += 60;
  } else if (langs.includes('VOSTFR') || titleUpper.includes('VOSTFR')) {
    score += 40;
  }

  if (t.qualities && (t.qualities.includes('1080p') || t.qualities.includes('4K'))) {
    score += 30;
  }

  return score;
}

function renderTorrents() {
  const list = document.getElementById('torrentsList');
  const filtered = getFilteredTorrents();

  const subEl = document.getElementById('torrentsSub');
  if (subEl) {
    subEl.textContent = `${filtered.length} source${filtered.length > 1 ? 's' : ''} disponible${filtered.length > 1 ? 's' : ''}${filtered.length !== allTorrents.length ? ` (sur ${allTorrents.length})` : ''}`;
  }

  if (!filtered.length) {
    list.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Aucun résultat pour ce filtre.</p>';
    return;
  }

  let bestIdx = -1;
  let maxScore = -1;
  filtered.forEach((t, i) => {
    const sc = scoreTorrent(t);
    if (sc > maxScore) {
      maxScore = sc;
      bestIdx = i;
    }
  });

  list.innerHTML = filtered.slice(0, 75).map((t, idx) => {
    const isBest = (idx === bestIdx && maxScore >= 120);
    const recBadge = isBest ? '<span class="badge badge-recommended" title="Optimal : Meilleure combinaison de seeders, résolution, langue et cache">Optimal Recommandé</span>' : '';
    const isCached = (t.source && t.source.includes('+')) || (t.qualities && t.qualities.some(q => q.endsWith('+')));
    const cachedBadge = isCached ? '<span class="badge badge-cached" title="Fichier disponible instantanément sur Real-Debrid">RD+ En cache</span>' : '';

    const qualBadges = (t.qualities || []).map(q => {
      const qClean = q.replace(/\+$/, '');
      let cls = 'badge';
      let tip = '';
      if (qClean === '4K' || qClean === '2160p') {
        cls = 'badge badge-res-4k';
        tip = ' title="Ultra Haute Définition 4K"';
      } else if (qClean === '1080p') {
        cls = 'badge badge-res-1080p';
        tip = ' title="Haute Définition 1080p"';
      } else if (qClean === 'DV' || qClean === 'Dolby Vision') {
        cls = 'badge badge-codec-dv';
        tip = ' title="Dolby Vision (Tone-Mapping anti-noirs bouchés actif)"';
      } else if (qClean === 'HDR' || qClean === 'HDR10' || qClean === 'HDR10+') {
        cls = 'badge badge-codec-hdr';
        tip = ' title="High Dynamic Range (HDR10)"';
      } else if (qClean === 'REMUX') {
        cls = 'badge badge-codec-remux';
        tip = ' title="Qualité Blu-Ray intégrale sans recompression"';
      } else if (['Atmos', 'DTS', 'DTS-HD', 'TrueHD', '5.1', '7.1'].includes(qClean)) {
        cls = 'badge badge-codec-audio';
        tip = ` title="Audio spatial / multicanal ${qClean}"`;
      } else {
        cls = 'badge';
      }
      return `<span class="${cls}"${tip}>${qClean}</span>`;
    }).join('');

    const langBadges = (t.langs || []).map(l => {
      const isFr = ['MULTI', 'TRUEFRENCH', 'VFF', 'VF', 'VOSTFR'].includes(l.toUpperCase());
      return `<span class="badge ${isFr ? 'badge-lang-fr' : 'badge-hi'}">${l}</span>`;
    }).join('');

    return `
      <div class="torrent-item ${isBest ? 'is-recommended' : ''}">
        <div style="flex:1; min-width:260px;">
          <div class="torrent-title">
            ${recBadge}${cachedBadge}${qualBadges}${langBadges}${t.title}
          </div>
          <div class="torrent-meta">
            <span style="color:var(--text); font-weight:500;">${t.source}</span>
            ${t.meta ? '<span>• ' + t.meta + '</span>' : ''}
          </div>
        </div>
        <div style="display:flex; gap:8px; align-items:center;">
          ${t.magnet ? `<button class="btn btn-secondary" style="padding:6px 11px; font-size:0.75rem;" onclick="copyTorrentMagnet(event, this, ${idx})" title="Copier le lien Magnet"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px; margin-right:3px;"><path d="M6 3v7a6 6 0 0 0 12 0V3"/><line x1="4" y1="3" x2="8" y2="3"/><line x1="16" y1="3" x2="20" y2="3"/></svg>Magnet</button>` : ''}
          ${isBest
            ? `<button class="btn btn-play-hero" style="padding:7px 16px !important; font-size:0.82rem !important;" onclick="debridFromIndex(${idx})"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" style="margin-right:2px;"><polygon points="5 3 19 12 5 21 5 3"/></svg> Lancer</button>`
            : `<button class="btn btn-secondary" style="padding:6px 12px; font-size:0.78rem;" onclick="debridFromIndex(${idx})">Sélectionner</button>`
          }
        </div>
      </div>
    `;
  }).join('');
}

async function copyTorrentMagnet(ev, btnEl, idx) {
  if (ev) ev.stopPropagation();
  const filtered = getFilteredTorrents();
  const item = filtered[idx];
  if (!item || !item.magnet) return;
  try {
    await navigator.clipboard.writeText(item.magnet);
    if (btnEl) {
      const orig = btnEl.textContent;
      btnEl.textContent = '✓ Copié';
      setTimeout(() => { btnEl.textContent = orig; }, 1400);
    }
  } catch (e) {}
}

async function debridFromIndex(startIdx) {
  const filtered = getFilteredTorrents();
  const candidates = filtered.slice(startIdx);
  await debridCandidates(candidates);
}

async function debridMagnet(magnet) {
  await debridCandidates([{title: 'Magnet direct', magnet}]);
}

async function debridCandidates(candidates) {
  const box = document.getElementById('debridResultPanel');
  box.style.display = 'block';
  box.scrollIntoView({behavior: 'smooth'});

  const season = currentMedia && currentMedia.type === 'series' ? document.getElementById('seasonSelect').value : null;
  const episode = currentMedia && currentMedia.type === 'series' ? document.getElementById('episodeSelect').value : null;

  for (let i = 0; i < candidates.length; i++) {
    const item = candidates[i];
    box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Débridage en cours (${i + 1}/${candidates.length}) : <strong style="color:var(--text);">${item.title || 'Magnet'}</strong>...</p>`;
    try {
      const res = await api('/api/debrid', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({magnet: item.magnet, resolve_url: item.resolve_url || '', season, episode})
      });
      renderDebridState(res, season, episode);
      return;
    } catch (e) {
      if (i + 1 < candidates.length && (e.message.includes('451') || e.message.includes('infringing_file') || e.message.includes('refusé'))) {
        box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Source #${i + 1} indisponible (451), passage à la suivante...</p>`;
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${e.message}</p>`;
      return;
    }
  }
}

function renderDebridState(res, season, episode, chosenTorrentTitle = '', playlistCount = 1) {
  const box = document.getElementById('debridResultPanel');
  if (!res.ready) {
    box.innerHTML = `
      <h3>Mise en cache Real-Debrid (${res.progress}%)</h3>
      <p style="color:var(--muted); font-size:0.82rem; margin-top:4px;">Statut : ${res.status} • Vitesse : ${res.speed} • Pairs : ${res.seeders || 0}</p>
      <div class="progress-bar"><div class="progress-fill" style="width:${res.progress}%"></div></div>
    `;
    setTimeout(async () => {
      try {
        const next = await api('/api/debrid-status', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({torrent_id: res.torrent_id, season, episode})
        });
        renderDebridState(next, season, episode, chosenTorrentTitle, playlistCount);
      } catch (e) {
        box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${e.message}</p>`;
      }
    }, 2500);
    return;
  }

  window.lastDebridFiles = res.files || [];

  const subBanner = chosenTorrentTitle
    ? `<p style="color:var(--muted); font-size:0.8rem; margin-bottom:6px;">Lecture : ${chosenTorrentTitle}</p>`
    : '';
  const playlistNote = (playlistCount > 1 || (currentMedia && currentMedia.type === 'series'))
    ? `<p style="color:var(--dim); font-size:0.77rem; margin-bottom:10px;">Playlist active${playlistCount > 1 ? ` (${playlistCount} épisodes)` : ''} — Touche <code style="color:var(--text); background:var(--surface-2); padding:1px 5px; border-radius:3px; border:1px solid var(--border);">&gt;</code> pour l'épisode suivant</p>`
    : '';

  box.innerHTML = `
    <h3 style="margin-bottom:6px;">Fichier prêt</h3>
    ${subBanner}
    ${playlistNote}
    <div style="display:flex; flex-direction:column; gap:8px;">
      ${res.files.map(f => `
        <div class="torrent-item" style="${f.is_target_ep ? 'border-color: var(--border-hover);' : ''}">
          <div style="flex:1; min-width:240px;">
            <div class="torrent-title">
              ${f.is_target_ep ? '<span class="badge badge-hi">Épisode</span>' : ''}
              ${f.filename}
            </div>
            <div class="torrent-meta">${f.filesize}</div>
          </div>
          <div style="display:flex; gap:6px; flex-wrap:wrap;">
            <button class="btn" onclick='openMpv(this, ${JSON.stringify(f.download)}, ${JSON.stringify(f.filename)})'>Play</button>
            <button class="btn btn-secondary" onclick='startPcDownload(${JSON.stringify(f.download)}, ${JSON.stringify(f.filename)})'>Télécharger</button>
            <a class="btn btn-secondary" href="${f.download}" target="_blank">Lien direct</a>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

async function startPcDownload(url, filename) {
  await api('/api/download', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url, filename})
  });
  pollDownloads();
}

async function cancelDownload(dl_id) {
  await api('/api/download-cancel', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dl_id})
  });
  pollDownloads();
}

async function openMpv(btn, url, filename) {
  if (_isGlobalPlayLaunching) return;
  _isGlobalPlayLaunching = true;
  setTimeout(() => { _isGlobalPlayLaunching = false; }, 3000);

  if (btn && btn.disabled) return;
  const origText = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = 'Play...';
  }
  const s = currentMedia && currentMedia.type === 'series' ? document.getElementById('seasonSelect')?.value : null;
  const e = currentMedia && currentMedia.type === 'series' ? document.getElementById('episodeSelect')?.value : null;
  let packFiles = null;
  if (window.lastDebridFiles && window.lastDebridFiles.length > 1 && window.lastDebridFiles.some(f => f.download === url)) {
    const sorted = [...window.lastDebridFiles].sort((a, b) => (a.filename || '').localeCompare(b.filename || ''));
    const idx = sorted.findIndex(f => f.download === url);
    if (idx >= 0) packFiles = sorted.slice(idx);
  }

  const isMacPlatform = /Mac/i.test(navigator.platform || navigator.userAgent);
  const isIntegrated = window.kinoPlayerMode === 'integrated' || (window.kinoPlayerMode !== 'external' && isMacPlatform);
  if (isIntegrated) {
    const pl = (packFiles && packFiles.length > 1)
      ? packFiles.map(p => ({ title: p.filename, url: p.download }))
      : [{ title: filename, url: url }];
    const histItem = userHistory.find(h => h.id === currentMedia?.id);
    let resumeSec = 0;
    if (histItem) {
      if (s && e && histItem.ep_positions) {
        resumeSec = histItem.ep_positions[`s${s}e${e}`]?.position || 0;
      } else {
        resumeSec = histItem.position || 0;
      }
    }
    openInAppPlayer(url, filename, pl, currentMedia, resumeSec);
    if (btn) btn.innerHTML = 'Lancé';
    refreshUserLists();
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origText;
      }, 2500);
    }
    return;
  }


  try {
    await api('/api/mpv', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        url,
        filename,
        pack_files: packFiles,
        media: currentMedia ? {
          id: currentMedia.id,
          name: currentMedia.name,
          type: currentMedia.type,
          year: currentMedia.year || '',
          poster: currentMedia.poster || '',
          season: s,
          episode: e
        } : null
      })
    });
    if (btn) btn.innerHTML = 'Lancé';
    refreshUserLists();
  } catch (err) {
    alert(err.message);
  } finally {
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origText;
      }, 2500);
    }
  }
}

(function initMacNativeWindow() {
  const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);
  const hasWkBridge = Boolean(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.jsBridge);
  if (isMac && (hasWkBridge || window.pywebview)) {
    document.body.classList.add('is-mac-native');
  }
  window.addEventListener('pywebviewready', () => {
    if (isMac) document.body.classList.add('is-mac-native');
  });

  document.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (!document.body.classList.contains('is-mac-native')) return;
    const dragRegion = e.target.closest('.pywebview-drag-region');
    if (!dragRegion) return;
    if (e.target.closest('.no-drag, button, input, select, textarea, a, label')) return;
    try {
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.jsBridge) {
        window.webkit.messageHandlers.jsBridge.postMessage(JSON.stringify({
          funcName: 'start_window_drag',
          params: [],
          id: 'mac_drag'
        }));
        return;
      }
      if (window.pywebview && window.pywebview.api && window.pywebview.api.start_window_drag) {
        window.pywebview.api.start_window_drag();
      }
    } catch (err) {}
  });
})();

async function windowAction(action) {
  if (action === 'maximize' && document.body.classList.contains('is-mac-native') && window.event && window.event.type === 'dblclick') {
    return;
  }
  try {
    if (window.pywebview && window.pywebview.api) {
      if (action === 'minimize' && window.pywebview.api.minimize) {
        window.pywebview.api.minimize();
        return;
      }
      if (action === 'maximize' && window.pywebview.api.toggle_maximize) {
        window.pywebview.api.toggle_maximize();
        setTimeout(syncWindowState, 80);
        return;
      }
      if (action === 'fullscreen' && window.pywebview.api.toggle_fullscreen) {
        const isFs = await window.pywebview.api.toggle_fullscreen();
        document.body.classList.toggle('is-fullscreen', Boolean(isFs));
        updateInAppFsBtnUI();
        setTimeout(syncWindowState, 80);
        return;
      }
      if (action === 'close' && window.pywebview.api.close) {
        window.pywebview.api.close();
        return;
      }
    }
  } catch (e) {}

  await api('/api/window/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action})
  }).catch(() => {});
}

function initResizeHandles() {
  const handles = document.querySelectorAll('.win-resize-handle');
  const isWin = /Win/i.test(navigator.platform || navigator.userAgent);
  handles.forEach(h => {
    h.addEventListener('mousedown', async (e) => {
      if (e.button !== 0) return;
      const dir = h.dataset.dir;
      if (!window.pywebview || !window.pywebview.api) return;
      e.preventDefault();
      e.stopPropagation();

      if (window.pywebview.api.get_state && window.pywebview.api.set_bounds) {
        const st = await window.pywebview.api.get_state();
        if (!st || st.maximized) return;
        const startX = e.screenX;
        const startY = e.screenY;
        const origX = st.x;
        const origY = st.y;
        const origW = st.width;
        const origH = st.height;
        const minW = 640;
        const minH = 420;
        let rafPending = false;

        const onMove = (ev) => {
          if (rafPending) return;
          rafPending = true;
          requestAnimationFrame(() => {
            rafPending = false;
            const dx = ev.screenX - startX;
            const dy = ev.screenY - startY;
            let nx = origX;
            let ny = origY;
            let nw = origW;
            let nh = origH;

            if (dir.includes('right')) {
              nw = Math.max(minW, origW + dx);
            }
            if (dir.includes('left')) {
              const candW = origW - dx;
              if (candW >= minW) {
                nw = candW;
                nx = origX + dx;
              } else {
                nw = minW;
                nx = origX + (origW - minW);
              }
            }
            if (dir.includes('bottom')) {
              nh = Math.max(minH, origH + dy);
            }
            if (dir.includes('top')) {
              const candH = origH - dy;
              if (candH >= minH) {
                nh = candH;
                ny = origY + dy;
              } else {
                nh = minH;
                ny = origY + (origH - minH);
              }
            }
            window.pywebview.api.set_bounds(nx, ny, nw, nh);
          });
        };

        const onUp = () => {
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
        };

        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      }
    });
  });
}

function updateInAppFsBtnUI() {
  const fsBtn = document.getElementById('inAppFsBtn');
  if (!fsBtn) return;
  const isFs = document.body.classList.contains('is-fullscreen') || Boolean(document.fullscreenElement || document.webkitFullscreenElement);
  if (isFs) {
    fsBtn.title = "Quitter le plein écran (F ou Échap)";
    fsBtn.innerHTML = `
      <svg class="svg-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/>
      </svg>
    `;
  } else {
    fsBtn.title = "Plein écran (F)";
    fsBtn.innerHTML = `
      <svg class="svg-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
      </svg>
    `;
  }
}

async function syncWindowState() {
  try {
    let isFs = false;
    if (window.pywebview && window.pywebview.api && window.pywebview.api.get_state) {
      const state = await window.pywebview.api.get_state();
      if (state) {
        if (state.maximized) {
          document.body.classList.add('is-maximized');
        } else {
          document.body.classList.remove('is-maximized');
        }
        if (state.fullscreen) {
          isFs = true;
          document.body.classList.add('is-fullscreen');
        } else {
          document.body.classList.remove('is-fullscreen');
        }
      }
    }
    const isHtmlFs = Boolean(document.fullscreenElement || document.webkitFullscreenElement);
    if (isHtmlFs) {
      isFs = true;
      document.body.classList.add('is-fullscreen');
    } else if (!window.pywebview) {
      document.body.classList.remove('is-fullscreen');
    }
    updateInAppFsBtnUI();
  } catch (e) {}
}

window.addEventListener('resize', syncWindowState);
document.addEventListener('fullscreenchange', syncWindowState);
document.addEventListener('webkitfullscreenchange', syncWindowState);
setInterval(syncWindowState, 1500);

const SVG_ICONS = {
  play: '<svg class="svg-icon" viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>',
  pause: '<svg class="svg-icon" viewBox="0 0 24 24" width="17" height="17" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>',
  volHigh: '<svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>',
  volLow: '<svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>',
  volMute: '<svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>'
};

let inAppPlaylist = [];
let inAppPlaylistIndex = 0;
let inAppCurrentMedia = null;
let inAppCurrentUrl = '';
let inAppCurrentTitle = '';
let inAppIdleTimeout = null;
let inAppFallbackTimer = null;
let inAppIsDraggingVol = false;
let inAppIntroData = null;
let inAppUndoTimer = null;
let inAppUndoTargetSec = 0;

function formatTime(sec) {
  if (!sec || isNaN(sec) || sec < 0) return '00:00';
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const remSec = s % 60;
  if (m < 60) {
    return `${String(m).padStart(2, '0')}:${String(remSec).padStart(2, '0')}`;
  }
  const h = Math.floor(m / 60);
  const remMin = m % 60;
  return `${String(h).padStart(2, '0')}:${String(remMin).padStart(2, '0')}:${String(remSec).padStart(2, '0')}`;
}

function resetInAppIdleTimer() {
  const overlay = document.getElementById('inAppPlayerOverlay');
  if (!overlay || !overlay.classList.contains('active')) return;
  overlay.classList.remove('idle');
  clearTimeout(inAppIdleTimeout);
  inAppIdleTimeout = setTimeout(() => {
    const video = document.getElementById('inAppVideo');
    if (video && !video.paused && !inAppIsDraggingVol) {
      overlay.classList.add('idle');
    }
  }, 2500);
}

function showInAppToast(msg, duration = 3000) {
  const toast = document.getElementById('inAppStatusToast');
  if (!toast) return;
  toast.innerHTML = msg;
  toast.style.display = 'block';
  if (duration > 0) {
    setTimeout(() => {
      toast.style.display = 'none';
    }, duration);
  }
}

function hideInAppToast() {
  const toast = document.getElementById('inAppStatusToast');
  if (toast) toast.style.display = 'none';
}

function updatePlayPauseBtn(isPlaying) {
  const btn = document.getElementById('inAppPlayPauseBtn');
  if (btn) {
    btn.innerHTML = isPlaying ? SVG_ICONS.pause : SVG_ICONS.play;
  }
}

function setInAppVolume(vol, unmute = true) {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  const clamped = Math.max(0, Math.min(1, vol));
  video.volume = clamped;
  if (unmute && video.muted && clamped > 0) {
    video.muted = false;
    video._userMuted = false;
  }
  try {
    localStorage.setItem('kino_inapp_volume', String(clamped));
    localStorage.setItem('kino_inapp_muted', String(video.muted));
  } catch (e) {}
  updateInAppVolUI();
}

function updateInAppVolUI() {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  const fill = document.getElementById('inAppVolFill');
  const txt = document.getElementById('inAppVolText');
  const icon = document.getElementById('inAppVolIcon');

  const isMuted = video.muted || video.volume === 0;
  const displayPct = isMuted ? 0 : Math.round(video.volume * 100);

  if (fill) fill.style.width = displayPct + '%';
  if (txt) txt.textContent = displayPct + '%';
  if (icon) {
    if (isMuted) {
      icon.innerHTML = SVG_ICONS.volMute;
    } else if (video.volume < 0.5) {
      icon.innerHTML = SVG_ICONS.volLow;
    } else {
      icon.innerHTML = SVG_ICONS.volHigh;
    }
  }
}

function toggleInAppMute() {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  if (video.muted) {
    video.muted = false;
    video._userMuted = false;
    if (video.volume === 0) video.volume = 0.5;
  } else {
    video.muted = true;
    video._userMuted = true;
  }
  try {
    localStorage.setItem('kino_inapp_volume', String(video.volume));
    localStorage.setItem('kino_inapp_muted', String(video.muted));
  } catch (e) {}
  updateInAppVolUI();
}

function fixInAppAudio() {
  const video = document.getElementById('inAppVideo');
  if (!video || !inAppCurrentUrl) return;
  const curSec = Math.floor(video.currentTime || 0);
  const remuxUrl = `/api/remux?url=${encodeURIComponent(inAppCurrentUrl)}&ss=${curSec}`;
  showInAppToast('<strong>Conversion Audio AAC compatible en cours...</strong><br><span style="font-size:0.78rem; color:var(--muted);">Remuxing audio instantané stéréo sans perte vidéo</span>', 2800);
  video.src = resolveKinoUrl(remuxUrl);
  video.load();
  video.muted = false;
  video._userMuted = false;
  if (!video.volume || video.volume < 0.1) video.volume = 1.0;
  video.play().catch(() => {});
  updateInAppVolUI();
  const btn = document.getElementById('inAppAudioFixBtn');
  if (btn) btn.innerHTML = '✓ Son AAC Actif';
}

function handleVolScrub(e) {
  const wrap = document.getElementById('inAppVolSliderWrap');
  if (!wrap) return;
  const rect = wrap.getBoundingClientRect();
  const clickX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
  const ratio = clickX / rect.width;
  setInAppVolume(ratio, true);
  resetInAppIdleTimer();
}

function onVolMouseDown(e) {
  e.preventDefault();
  inAppIsDraggingVol = true;
  handleVolScrub(e);

  const onMouseMove = (ev) => {
    if (!inAppIsDraggingVol) return;
    handleVolScrub(ev);
  };
  const onMouseUp = () => {
    inAppIsDraggingVol = false;
    window.removeEventListener('mousemove', onMouseMove);
    window.removeEventListener('mouseup', onMouseUp);
    resetInAppIdleTimer();
  };

  window.addEventListener('mousemove', onMouseMove);
  window.addEventListener('mouseup', onMouseUp);
}

function inAppWheelVolume(delta) {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  const step = delta > 0 ? -0.05 : 0.05;
  setInAppVolume(video.volume + step, true);
  resetInAppIdleTimer();
}

const INAPP_CLARITY_PRESETS = [
  { id: 'normal', label: 'Clarté : Normal', filter: 'none', note: 'Étalonnage standard' },
  { id: 'boost1', label: 'Clarté : Ombres +', filter: 'url(#kinoShadowBoost1) brightness(1.08) contrast(1.03) saturate(1.04)', note: 'Débouche les scènes sombres (Gamma +25%)' },
  { id: 'boost2', label: 'Clarté : Nuit ++', filter: 'url(#kinoShadowBoost2) brightness(1.16) contrast(1.05) saturate(1.07)', note: 'Anti-noirs bouchés intensif (Gamma +45%)' },
  { id: 'boost3', label: 'Clarté : HDR Max', filter: 'url(#kinoShadowBoost3) brightness(1.26) contrast(1.07) saturate(1.10)', note: 'Correction maximale flux HDR / Dolby Vision très sombres' }
];
let inAppClarityIdx = 0;

function applyInAppClarity(idx, showToast = false) {
  inAppClarityIdx = ((idx % INAPP_CLARITY_PRESETS.length) + INAPP_CLARITY_PRESETS.length) % INAPP_CLARITY_PRESETS.length;
  const preset = INAPP_CLARITY_PRESETS[inAppClarityIdx];
  const video = document.getElementById('inAppVideo');
  const btn = document.getElementById('inAppClarityBtn');
  if (video) {
    video.style.filter = preset.filter;
  }
  if (btn) {
    btn.textContent = preset.label;
    btn.style.borderColor = inAppClarityIdx > 0 ? '#fafafa' : '';
  }
  if (showToast) {
    showInAppToast(`<strong>${preset.label}</strong><br><span style="font-size:0.78rem; color:var(--muted);">${preset.note}</span>`, 1800);
  }
}

function cycleInAppClarity() {
  applyInAppClarity(inAppClarityIdx + 1, true);
  resetInAppIdleTimer();
}

// --- Sous-titres OpenSubtitles v3 (FR / EN) ---
let inAppAvailableSubs = [{ label: 'CC : Off', lang: 'off', url: '' }];
let inAppActiveSubIdx = 0;
let inAppActiveCues = [];
let inAppSubDelaySec = 0;
const inAppSubCuesCache = {};

async function loadInAppSubtitlesForCurrentMedia() {
  inAppAvailableSubs = [{ label: 'CC : Off', lang: 'off', url: '' }];
  inAppActiveSubIdx = 0;
  inAppActiveCues = [];
  inAppSubDelaySec = 0;
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (subOverlay) { subOverlay.style.display = 'none'; subOverlay.innerHTML = ''; }
  const btn = document.getElementById('inAppSubsBtn');
  if (btn) { btn.textContent = 'CC : Off'; btn.style.borderColor = ''; }

  if (!inAppCurrentMedia || !inAppCurrentMedia.id) return;
  const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : 1);
  const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : 1);
  const mtype = inAppCurrentMedia.type || 'movie';
  try {
    const res = await api(`/api/subtitles?imdb_id=${encodeURIComponent(inAppCurrentMedia.id)}&type=${encodeURIComponent(mtype)}&season=${s || 1}&episode=${e || 1}`);
    const subs = (res && res.subtitles) || [];
    const frList = subs.filter(x => x.lang === 'fr').slice(0, 2);
    const enList = subs.filter(x => x.lang === 'en').slice(0, 2);
    frList.forEach((item, idx) => {
      inAppAvailableSubs.push({
        label: frList.length > 1 ? `CC : Français #${idx + 1}` : 'CC : Français',
        lang: 'fr',
        url: item.url
      });
    });
    enList.forEach((item, idx) => {
      inAppAvailableSubs.push({
        label: enList.length > 1 ? `CC : English #${idx + 1}` : 'CC : English',
        lang: 'en',
        url: item.url
      });
    });
    // Si le flux est VOSTFR ou VO et qu'une piste FR existe, on peut pré-charger la piste FR
    if (/\b(vostfr|vost|multi|vo|eng)\b/i.test(inAppCurrentTitle || '') && frList.length > 0) {
      // Préchargement silencieux en cache
      api(`/api/subtitle-cues?url=${encodeURIComponent(frList[0].url)}`).then(d => {
        if (d && d.cues) inAppSubCuesCache[frList[0].url] = d.cues;
      }).catch(() => {});
    }
  } catch (err) {}
}

async function cycleInAppSubtitles() {
  resetInAppIdleTimer();
  if (inAppAvailableSubs.length <= 1) {
    showInAppToast(`<strong>Sous-titres</strong><br><span style="font-size:0.78rem; color:var(--muted);">Aucun sous-titre OpenSubtitles trouvé pour ce titre</span>`, 1800);
    return;
  }
  inAppActiveSubIdx = (inAppActiveSubIdx + 1) % inAppAvailableSubs.length;
  const sub = inAppAvailableSubs[inAppActiveSubIdx];
  const btn = document.getElementById('inAppSubsBtn');
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (btn) {
    btn.textContent = sub.label;
    btn.style.borderColor = sub.lang !== 'off' ? '#fafafa' : '';
  }
  if (sub.lang === 'off' || !sub.url) {
    inAppActiveCues = [];
    if (subOverlay) { subOverlay.style.display = 'none'; subOverlay.innerHTML = ''; }
    showInAppToast(`<strong>Sous-titres désactivés</strong>`, 1400);
    return;
  }
  showInAppToast(`<strong>${sub.label}</strong><br><span style="font-size:0.78rem; color:var(--muted);">Chargement OpenSubtitles... (G / H pour décaler ±0.5s)</span>`, 1800);
  if (inAppSubCuesCache[sub.url]) {
    inAppActiveCues = inAppSubCuesCache[sub.url];
    const v = document.getElementById('inAppVideo');
    if (v) updateInAppSubtitleOverlay(v.currentTime);
    return;
  }
  try {
    const data = await api(`/api/subtitle-cues?url=${encodeURIComponent(sub.url)}`);
    if (data && Array.isArray(data.cues)) {
      inAppSubCuesCache[sub.url] = data.cues;
      inAppActiveCues = data.cues;
      const v = document.getElementById('inAppVideo');
      if (v) updateInAppSubtitleOverlay(v.currentTime);
    }
  } catch (err) {
    showInAppToast(`<strong>Erreur sous-titres</strong><br><span style="font-size:0.78rem; color:var(--muted);">${err.message}</span>`, 1800);
  }
}

let inAppSubSizeIdx = 1;
const SUB_SIZES = [
  { label: 'A-', size: '1.1rem', name: 'Petite' },
  { label: 'A', size: '1.32rem', name: 'Standard' },
  { label: 'A+', size: '1.6rem', name: 'Grande' },
  { label: 'A++', size: '1.9rem', name: 'Très grande' }
];

function cycleInAppSubSize() {
  resetInAppIdleTimer();
  inAppSubSizeIdx = (inAppSubSizeIdx + 1) % SUB_SIZES.length;
  const cfg = SUB_SIZES[inAppSubSizeIdx];
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (subOverlay) subOverlay.style.fontSize = cfg.size;
  const btn = document.getElementById('inAppSubSizeBtn');
  if (btn) btn.textContent = cfg.label;
  showInAppToast(`<strong>Taille sous-titres : ${cfg.name}</strong>`, 1400);
}

function adjustInAppSubDelay(deltaSec) {
  if (!inAppActiveCues.length) return;
  inAppSubDelaySec = Math.round((inAppSubDelaySec + deltaSec) * 100) / 100;
  const sign = inAppSubDelaySec >= 0 ? '+' : '';
  showInAppToast(`<strong>Décalage sous-titres : ${sign}${inAppSubDelaySec}s</strong> (Z / X)`, 1400);
  const v = document.getElementById('inAppVideo');
  if (v) updateInAppSubtitleOverlay(v.currentTime);
}

function updateInAppSubtitleOverlay(currentTime) {
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (!subOverlay) return;
  if (!inAppActiveCues || !inAppActiveCues.length) {
    subOverlay.style.display = 'none';
    return;
  }
  const t = currentTime - inAppSubDelaySec;
  const active = [];
  for (let i = 0; i < inAppActiveCues.length; i++) {
    const c = inAppActiveCues[i];
    if (t >= c.start && t <= c.end) {
      active.push(c.text);
    } else if (c.start > t + 2) {
      break;
    }
  }
  if (!active.length) {
    subOverlay.style.display = 'none';
    subOverlay.innerHTML = '';
    return;
  }
  const escaped = active.join('\n').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
  subOverlay.innerHTML = `<span class="sub-box">${escaped}</span>`;
  subOverlay.style.display = 'block';
}

// --- Mode Boost Voix / Audio Nuit ---
function applyInAppAudioUI(mode, showToast = false) {
  window.kinoAudioMode = mode || 'voice_boost';
  const btn = document.getElementById('inAppAudioBtn');
  const isBoost = window.kinoAudioMode === 'voice_boost';
  if (btn) {
    btn.textContent = isBoost ? 'Voix : Boost' : 'Voix : Normal';
    btn.style.borderColor = isBoost ? '#fafafa' : '';
  }
  const sel = document.getElementById('cfgAudioMode');
  if (sel) sel.value = window.kinoAudioMode;
  if (showToast) {
    showInAppToast(
      isBoost
        ? `<strong>Boost Voix / Audio Nuit activé</strong><br><span style="font-size:0.78rem; color:var(--muted);">Dialogues rehaussés & explosions adoucies (normalisation dynamique)</span>`
        : `<strong>Audio : Standard</strong><br><span style="font-size:0.78rem; color:var(--muted);">Plage dynamique cinéma originale sans compression</span>`,
      1900
    );
  }
}

function cycleInAppAudioBoost() {
  resetInAppIdleTimer();
  const nextMode = (window.kinoAudioMode || 'voice_boost') === 'voice_boost' ? 'normal' : 'voice_boost';
  applyInAppAudioUI(nextMode, true);
  api('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ audio_mode: nextMode })
  }).catch(() => {});
}

// --- Vitesse de lecture (0.75x à 2.0x) & Capture d'écran ---
const INAPP_SPEED_PRESETS = [0.75, 1.0, 1.25, 1.5, 2.0];
let inAppSpeedIdx = 1;
let inAppSkipIntroDismissed = false;
let inAppNextEpCancelled = false;

function applyInAppSpeed(idx, showToast = false) {
  inAppSpeedIdx = ((idx % INAPP_SPEED_PRESETS.length) + INAPP_SPEED_PRESETS.length) % INAPP_SPEED_PRESETS.length;
  const spd = INAPP_SPEED_PRESETS[inAppSpeedIdx];
  const video = document.getElementById('inAppVideo');
  const btn = document.getElementById('inAppSpeedBtn');
  if (video) {
    video.playbackRate = spd;
    if ('preservesPitch' in video) video.preservesPitch = true;
    else if ('webkitPreservesPitch' in video) video.webkitPreservesPitch = true;
  }
  if (btn) {
    btn.textContent = `${spd}x`;
    btn.style.borderColor = spd !== 1.0 ? '#fafafa' : '';
  }
  if (showToast) {
    showInAppToast(`<strong>Vitesse : ${spd}x</strong>`, 1300);
  }
}

function cycleInAppSpeed(step = 1) {
  resetInAppIdleTimer();
  applyInAppSpeed(inAppSpeedIdx + step, true);
}

async function captureInAppScreenshot() {
  resetInAppIdleTimer();
  const video = document.getElementById('inAppVideo');
  if (!video || !video.videoWidth || !video.videoHeight) {
    showInAppToast(`<strong>Capture impossible</strong><br><span style="font-size:0.78rem; color:var(--muted);">Aucune image vidéo active</span>`, 1600);
    return;
  }
  try {
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL('image/png');
    const timeStr = formatTime(video.currentTime).replace(/:/g, 'm') + 's';
    const res = await api('/api/save-screenshot', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        title: inAppCurrentTitle || 'KINO',
        time_str: timeStr,
        data_url: dataUrl
      })
    });
    if (res && res.filename) {
      showInAppToast(`<strong>Capture enregistrée</strong><br><span style="font-size:0.78rem; color:var(--muted);">${res.filename}</span>`, 2200);
    }
  } catch (e) {
    showInAppToast(`<strong>Capture protégée (CORS)</strong><br><span style="font-size:0.78rem; color:var(--muted);">Utilisez Shift+Cmd+4 / Win+Shift+S ou le Moteur KINO</span>`, 2200);
  }
}

async function loadInAppIntroData(streamUrl, media) {
  inAppIntroData = null;
  dismissUndoSkip();
  if (!media) return;
  const isSeriesOrAnime = Boolean((media.type === 'series' || media.type === 'anime') || (inAppPlaylist && inAppPlaylist.length > 1));
  if (!isSeriesOrAnime) return;

  const s = media.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : 1);
  const e = media.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : 1);
  const title = media.name || media.title || inAppCurrentTitle || '';
  const mtype = media.type || 'series';
  const imdbId = media.id || '';

  try {
    const params = new URLSearchParams({
      title: title,
      type: mtype,
      season: s || 1,
      episode: e || 1,
      url: streamUrl || '',
      imdb_id: imdbId || ''
    });
    const res = await api(`/api/intro-times?${params.toString()}`);
    if (res) {
      inAppIntroData = res;
    }
  } catch (err) {
    console.debug('[KINO] Intro data non dispo:', err);
  }
}

function showUndoSkip(origSec) {
  clearTimeout(inAppUndoTimer);
  inAppUndoTargetSec = Math.max(0, origSec);
  const undoCard = document.getElementById('inAppUndoSkipCard');
  const undoBtn = document.getElementById('inAppUndoSkipBtn');
  if (undoCard) {
    if (undoBtn) undoBtn.textContent = `↩ Revenir à ${formatTime(origSec)}`;
    undoCard.style.display = 'flex';
  }
  inAppUndoTimer = setTimeout(() => {
    dismissUndoSkip();
  }, 7000);
}

function dismissUndoSkip() {
  clearTimeout(inAppUndoTimer);
  const undoCard = document.getElementById('inAppUndoSkipCard');
  if (undoCard) undoCard.style.display = 'none';
}

function undoInAppSkip() {
  dismissUndoSkip();
  const video = document.getElementById('inAppVideo');
  if (video && inAppUndoTargetSec >= 0) {
    video.currentTime = inAppUndoTargetSec;
    showInAppToast(`<strong>↩ Retour à ${formatTime(inAppUndoTargetSec)}</strong>`, 1400);
  }
}

function skipInAppIntro() {
  resetInAppIdleTimer();
  inAppSkipIntroDismissed = true;
  const card = document.getElementById('inAppSkipIntroCard');
  if (card) card.style.display = 'none';

  const video = document.getElementById('inAppVideo');
  if (!video) return;

  const curT = video.currentTime || 0;
  showUndoSkip(curT);

  if (inAppIntroData && inAppIntroData.op && curT < inAppIntroData.op.end) {
    const targetT = inAppIntroData.op.end + 0.5;
    video.currentTime = targetT;
    const prov = inAppIntroData.provider ? `<br><span style="font-size:0.75rem; color:var(--muted);">${inAppIntroData.provider}</span>` : '';
    showInAppToast(`<strong>Intro passée (→ ${formatTime(inAppIntroData.op.end)})</strong>${prov}`, 2000);
  } else if (inAppIntroData && inAppIntroData.recap && curT < inAppIntroData.recap.end) {
    const targetT = inAppIntroData.recap.end + 0.5;
    video.currentTime = targetT;
    showInAppToast(`<strong>Récap passé (→ ${formatTime(inAppIntroData.recap.end)})</strong>`, 1800);
  } else {
    const isAnime = Boolean(inAppCurrentMedia && inAppCurrentMedia.type === 'anime');
    const skipAmount = (inAppIntroData && inAppIntroData.default_skip) ? inAppIntroData.default_skip : (isAnime ? 90 : 60);
    inAppSeekRel(skipAmount);
    showInAppToast(`<strong>Intro passée (+${skipAmount}s)</strong>`, 1600);
  }
}

function dismissSkipIntro() {
  inAppSkipIntroDismissed = true;
  const card = document.getElementById('inAppSkipIntroCard');
  if (card) card.style.display = 'none';
}

function cancelNextEpAuto() {
  inAppNextEpCancelled = true;
  const card = document.getElementById('inAppNextEpCard');
  if (card) card.style.display = 'none';
}

// --- Mode Picture-in-Picture (PiP — Fenêtre flottante macOS) ---
async function toggleInAppPiP() {
  resetInAppIdleTimer();
  const video = document.getElementById('inAppVideo');
  const btn = document.getElementById('inAppPipBtn');
  if (!video) return;
  try {
    if (video.webkitSupportsPresentationMode && typeof video.webkitSetPresentationMode === 'function') {
      const nextMode = video.webkitPresentationMode === 'picture-in-picture' ? 'inline' : 'picture-in-picture';
      video.webkitSetPresentationMode(nextMode);
      if (btn) btn.style.borderColor = nextMode === 'picture-in-picture' ? '#fafafa' : '';
      showInAppToast(nextMode === 'picture-in-picture' ? `<strong>⧉ Picture-in-Picture activé</strong>` : `<strong>⧉ Retour à la fenêtre KINO</strong>`, 1400);
      return;
    }
    if (document.pictureInPictureElement) {
      await document.exitPictureInPicture();
      if (btn) btn.style.borderColor = '';
    } else if (video.requestPictureInPicture) {
      await video.requestPictureInPicture();
      if (btn) btn.style.borderColor = '#fafafa';
      showInAppToast(`<strong>⧉ Picture-in-Picture activé</strong>`, 1400);
    }
  } catch (e) {
    showInAppToast(`<strong>⧉ PiP indisponible sur ce flux</strong><br><span style="font-size:0.78rem; color:var(--muted);">Utilisez « Moteur KINO » pour le mode flottant natif</span>`, 1800);
  }
}

function openInAppPlayer(streamUrl, title, playlist = null, media = null, resumeSec = 0) {
  const overlay = document.getElementById('inAppPlayerOverlay');
  const video = document.getElementById('inAppVideo');
  const titleEl = document.getElementById('inAppTitle');

  clearTimeout(inAppFallbackTimer);
  hideInAppToast();

  inAppCurrentUrl = streamUrl;
  inAppCurrentTitle = title;
  inAppCurrentMedia = media || currentMedia;
  inAppSkipIntroDismissed = false;
  inAppNextEpCancelled = false;
  traktScrobbledStarted = false;
  traktScrobbledStop = false;
  startRemotePolling();
  dismissUndoSkip();
  loadInAppIntroData(streamUrl, inAppCurrentMedia);
  updateDiscordRpc(false);
  const skipCard = document.getElementById('inAppSkipIntroCard');
  if (skipCard) skipCard.style.display = 'none';
  const nextCard = document.getElementById('inAppNextEpCard');
  if (nextCard) nextCard.style.display = 'none';

  if (playlist && Array.isArray(playlist) && playlist.length > 0) {
    inAppPlaylist = playlist;
    const foundIdx = playlist.findIndex(p => p.url === streamUrl);
    inAppPlaylistIndex = foundIdx >= 0 ? foundIdx : 0;
  } else {
    inAppPlaylist = [{ title: title, url: streamUrl }];
    inAppPlaylistIndex = 0;
  }

  titleEl.textContent = title || 'Lecture';
  overlay.classList.add('active');
  document.body.style.overflow = 'hidden';
  resetInAppIdleTimer();

  const isHdrStream = /\b(hdr|hdr10|dv|dovi|dolby[\s\.\-]*vision)\b/i.test(`${title || ''} ${streamUrl || ''}`);
  if ((window.kinoHdrMode || 'sdr_pref') === 'hdr_boost') {
    applyInAppClarity(2, false);
  } else if (isHdrStream && (window.kinoHdrMode || 'sdr_pref') !== 'hdr_native' && inAppClarityIdx === 0) {
    applyInAppClarity(1, true);
  } else {
    applyInAppClarity(inAppClarityIdx, false);
  }

  // Détection des formats exigeants (HEVC / 10-bit / DTS) et proposition MPV / Audio AAC
  const isHeavyCodec = /\b(hevc|h\.?265|10bit|hdr|dv|dovi|dts|truehd|remux)\b/i.test(`${title || ''} ${streamUrl || ''}`);
  const hasTrickyAudio = /\b(dts|dts-hd|truehd|atmos|eac3|ac3|ddp|dd\+|flac|5\.1|7\.1)\b/i.test(`${title || ''} ${streamUrl || ''}`);
  const mpvBtn = document.getElementById('inAppMpvSuggestBtn');
  if (mpvBtn) {
    mpvBtn.style.display = isHeavyCodec ? 'inline-flex' : 'none';
  }
  const audioFixBtn = document.getElementById('inAppAudioFixBtn');
  if (audioFixBtn) {
    audioFixBtn.style.display = hasTrickyAudio ? 'inline-flex' : 'none';
    audioFixBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px; margin-right:4px;"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><line x1="1" y1="1" x2="23" y2="23"/></svg>Son AAC';
  }
  if (hasTrickyAudio) {
    setTimeout(() => {
      showInAppToast(`<strong>Piste audio multi-canal / cinéma détectée</strong><br><span style="font-size:0.78rem; color:var(--muted);">Pas de son ? Cliquez sur <strong>Son AAC</strong> en haut ou <strong>Basculer MPV</strong></span>`, 3200);
    }, 1400);
  } else if (isHeavyCodec) {
    setTimeout(() => {
      showInAppToast(`<strong>Format 4K HDR / DTS détecté</strong><br><span style="font-size:0.78rem; color:var(--muted);">Touche E ou bouton dédié pour basculer sur MPV sans perte</span>`, 2800);
    }, 1200);
  }

  // Déverrouillage forcé du son & restauration du volume sauvegardé
  video.muted = false;
  video._userMuted = false;
  let savedVol = null;
  try {
    savedVol = localStorage.getItem('kino_inapp_volume');
    if (localStorage.getItem('kino_inapp_muted') === 'true') {
      video.muted = true;
      video._userMuted = true;
    }
  } catch(e) {}
  video.volume = (savedVol !== null && !isNaN(savedVol) && parseFloat(savedVol) > 0) ? Math.max(0.05, parseFloat(savedVol)) : 1.0;

  applyInAppAudioUI(window.kinoAudioMode || 'voice_boost', false);
  applyInAppSpeed(1, false);
  loadInAppSubtitlesForCurrentMedia();

  updateInAppVolUI();
  updatePlayPauseBtn(false);

  let playbackStarted = false;
  let fallbackTriggered = false;

  const onPlaying = () => {
    playbackStarted = true;
    clearTimeout(inAppFallbackTimer);
    hideInAppToast();
    if (resumeSec > 15 && video.duration && resumeSec < video.duration * 0.95) {
      video.currentTime = resumeSec;
    }
  };

  const onFallbackRequired = (reason) => {
    if (playbackStarted || fallbackTriggered) return;
    clearTimeout(inAppFallbackTimer);
    if (!video.src.includes('/api/remux')) {
      showInAppToast(`Format conteneur MKV/Audio non natif.<br><strong>Remuxing vidéo/audio instantané...</strong>`, 2200);
      video.removeEventListener('playing', video._onPlayingHandler || (() => {}));
      video.removeEventListener('error', video._onErrorHandler || (() => {}));
      const remuxPlaying = () => {
        playbackStarted = true;
        clearTimeout(inAppFallbackTimer);
        hideInAppToast();
      };
      const remuxError = () => {
        fallbackTriggered = true;
        showInAppToast(`Flux non décodable dans le navigateur.<br><strong>Lancement du lecteur externe...</strong>`, 2600);
        setTimeout(switchToExternalPlayer, 600);
      };
      video.addEventListener('playing', remuxPlaying, { once: true });
      video.addEventListener('error', remuxError, { once: true });
      const curSec = Math.floor(video.currentTime || resumeSec || 0);
      video.src = resolveKinoUrl(`/api/remux?url=${encodeURIComponent(streamUrl)}&ss=${curSec}`);
      video.load();
      video.play().catch(() => {});
      inAppFallbackTimer = setTimeout(() => {
        if (!playbackStarted && (video.paused || video.readyState === 0)) {
          remuxError();
        }
      }, 5000);
      return;
    }
    fallbackTriggered = true;
    showInAppToast(`Flux 4K/MKV non décodable dans le navigateur.<br><strong>Lancement du lecteur externe...</strong>`, 2600);
    setTimeout(() => {
      switchToExternalPlayer();
    }, 600);
  };

  video.removeEventListener('playing', video._onPlayingHandler || (() => {}));
  video.removeEventListener('error', video._onErrorHandler || (() => {}));

  video._onPlayingHandler = onPlaying;
  video._onErrorHandler = () => onFallbackRequired('error');

  video.addEventListener('playing', video._onPlayingHandler, { once: true });
  video.addEventListener('error', video._onErrorHandler, { once: true });

  video.src = resolveKinoUrl(streamUrl);
  video.load();

  const playPromise = video.play();
  if (playPromise !== undefined) {
    playPromise.catch(e => {
      if (e && e.name === 'NotAllowedError') {
        video.muted = true;
        updateInAppVolUI();
        video.play().then(() => {
          showInAppToast(`<strong>Cliquez sur l'écran ou appuyez sur M pour activer le son</strong>`, 3500);
        }).catch(() => {});
      } else if (video.error || video.readyState === 0) {
        onFallbackRequired('play_reject');
      }
    });
  }

  inAppFallbackTimer = setTimeout(() => {
    if (!playbackStarted && (video.paused || video.readyState === 0 || video.currentTime === 0)) {
      onFallbackRequired('timeout');
    }
  }, 4500);

  if (inAppCurrentMedia && inAppCurrentMedia.id) {
    const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : null);
    const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : null);
    api('/api/history-record', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        media: {
          id: inAppCurrentMedia.id,
          name: inAppCurrentMedia.name || title,
          type: inAppCurrentMedia.type || 'movie',
          year: inAppCurrentMedia.year || '',
          poster: inAppCurrentMedia.poster || '',
          season: s,
          episode: e,
          filename: title
        }
      })
    }).then(refreshUserLists).catch(() => {});
  }
}

function closeInAppPlayer() {
  clearTimeout(inAppFallbackTimer);
  exitInAppFullscreen();
  const overlay = document.getElementById('inAppPlayerOverlay');
  const video = document.getElementById('inAppVideo');
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (subOverlay) { subOverlay.style.display = 'none'; subOverlay.innerHTML = ''; }
  const skipCard = document.getElementById('inAppSkipIntroCard');
  if (skipCard) skipCard.style.display = 'none';
  const nextCard = document.getElementById('inAppNextEpCard');
  if (nextCard) nextCard.style.display = 'none';
  dismissUndoSkip();
  inAppIntroData = null;
  clearDiscordRpc();
  if (video) {
    if (video._onPlayingHandler) {
      video.removeEventListener('playing', video._onPlayingHandler);
      video._onPlayingHandler = null;
    }
    if (video._onErrorHandler) {
      video.removeEventListener('error', video._onErrorHandler);
      video._onErrorHandler = null;
    }
    if (inAppCurrentMedia) {
      const pct = (video.duration > 0) ? Math.round((video.currentTime / video.duration) * 100) : 0;
      scrobbleTrakt('pause', inAppCurrentMedia, pct);
    }
    if (document.pictureInPictureElement) {
      document.exitPictureInPicture().catch(() => {});
    }
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  if (overlay) {
    overlay.classList.remove('active', 'idle');
  }
  hideInAppToast();
  document.body.style.overflow = '';
  clearTimeout(inAppIdleTimeout);
  refreshUserLists();
}

function toggleInAppPlay() {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  if (video.paused) {
    video.play().then(() => updatePlayPauseBtn(true)).catch(() => {});
  } else {
    video.pause();
    updatePlayPauseBtn(false);
  }
}

function inAppSeekRel(delta) {
  const video = document.getElementById('inAppVideo');
  if (!video || !video.duration) return;
  video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + delta));
  resetInAppIdleTimer();
}

function seekInApp(event) {
  const video = document.getElementById('inAppVideo');
  const seekbar = document.getElementById('inAppSeekbar');
  if (!video || !video.duration || !seekbar) return;
  const rect = seekbar.getBoundingClientRect();
  const clickX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  const pct = clickX / rect.width;
  video.currentTime = pct * video.duration;
  resetInAppIdleTimer();
}

function inAppPrevTrack() {
  if (inAppPlaylist.length <= 1) return;
  if (inAppPlaylistIndex > 0) {
    inAppPlaylistIndex--;
    const item = inAppPlaylist[inAppPlaylistIndex];
    openInAppPlayer(item.url, item.title, inAppPlaylist, inAppCurrentMedia);
  }
}

function inAppNextTrack() {
  if (inAppPlaylist.length <= 1) return;
  if (inAppPlaylistIndex < inAppPlaylist.length - 1) {
    inAppPlaylistIndex++;
    const item = inAppPlaylist[inAppPlaylistIndex];
    openInAppPlayer(item.url, item.title, inAppPlaylist, inAppCurrentMedia);
  }
}

async function exitInAppFullscreen() {
  let wasFs = false;
  try {
    if (window.pywebview && window.pywebview.api) {
      if (typeof window.pywebview.api.exit_fullscreen === 'function') {
        await window.pywebview.api.exit_fullscreen();
        wasFs = true;
      } else if (typeof window.pywebview.api.toggle_fullscreen === 'function') {
        let isFs = false;
        if (typeof window.pywebview.api.is_fullscreen === 'function') {
          isFs = await window.pywebview.api.is_fullscreen();
        } else {
          isFs = document.body.classList.contains('is-fullscreen');
        }
        if (isFs) {
          await window.pywebview.api.toggle_fullscreen();
        }
        wasFs = true;
      }
    }
  } catch (e) {}

  if (document.fullscreenElement || document.webkitFullscreenElement) {
    try {
      if (document.exitFullscreen) await document.exitFullscreen();
      else if (document.webkitExitFullscreen) await document.webkitExitFullscreen();
      wasFs = true;
    } catch (e) {}
  }

  if (!wasFs) {
    try {
      await api('/api/window/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'exit_fullscreen'})
      });
    } catch (e) {}
  }

  document.body.classList.remove('is-fullscreen');
  updateInAppFsBtnUI();
  setTimeout(syncWindowState, 50);
  return wasFs;
}

async function toggleInAppFullscreen() {
  resetInAppIdleTimer();

  // 1. Application de bureau pywebview native (Windows & macOS)
  if (window.pywebview && window.pywebview.api) {
    try {
      if (typeof window.pywebview.api.toggle_fullscreen === 'function') {
        const isFs = await window.pywebview.api.toggle_fullscreen();
        document.body.classList.toggle('is-fullscreen', Boolean(isFs));
        updateInAppFsBtnUI();
        setTimeout(syncWindowState, 50);
        return;
      }
    } catch (e) {
      console.debug('pywebview toggle_fullscreen fallback:', e);
    }
  }

  // 2. Mode Web App sans pywebview
  const isCurrentlyFs = document.body.classList.contains('is-fullscreen') || Boolean(document.fullscreenElement || document.webkitFullscreenElement);
  if (isCurrentlyFs) {
    await exitInAppFullscreen();
    return;
  }

  try {
    const res = await api('/api/window/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: 'fullscreen'})
    });
    if (res && res.ok) {
      document.body.classList.add('is-fullscreen');
      updateInAppFsBtnUI();
      setTimeout(syncWindowState, 50);
      return;
    }
  } catch (e) {}

  // 3. Fallback Web Standard (HTML5 Fullscreen API pour navigateur normal)
  const docEl = document.documentElement;
  const overlay = document.getElementById('inAppPlayerOverlay');
  const target = overlay || docEl;
  const fsEl = document.fullscreenElement || document.webkitFullscreenElement || document.mozFullScreenElement || document.msFullscreenElement;

  try {
    if (!fsEl) {
      if (target.requestFullscreen) {
        await target.requestFullscreen();
      } else if (target.webkitRequestFullscreen) {
        target.webkitRequestFullscreen();
      } else if (docEl.requestFullscreen) {
        await docEl.requestFullscreen();
      }
      document.body.classList.add('is-fullscreen');
    } else {
      if (document.exitFullscreen) {
        await document.exitFullscreen();
      } else if (document.webkitExitFullscreen) {
        document.webkitExitFullscreen();
      }
      document.body.classList.remove('is-fullscreen');
    }
    updateInAppFsBtnUI();
    setTimeout(syncWindowState, 50);
  } catch (err) {
    console.debug('HTML5 fullscreen error:', err);
  }
}

let _isSwitchingToExternal = false;
async function switchToExternalPlayer() {
  if (_isSwitchingToExternal) return;
  _isSwitchingToExternal = true;
  setTimeout(() => { _isSwitchingToExternal = false; }, 3500);

  const url = inAppCurrentUrl;
  const title = inAppCurrentTitle;
  const media = inAppCurrentMedia;
  const playlist = inAppPlaylist;
  closeInAppPlayer();

  try {
    await api('/api/mpv', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        url,
        filename: title,
        pack_files: playlist && playlist.length > 1 ? playlist.map(p => ({ filename: p.title, download: p.url })) : null,
        media: media ? {
          id: media.id,
          name: media.name || title,
          type: media.type || 'movie',
          year: media.year || '',
          poster: media.poster || '',
          season: media.season || null,
          episode: media.episode || null,
        } : null
      })
    });
  } catch (e) {
    alert("Impossible d'ouvrir le lecteur KINO : " + e.message);
  }
}

const inAppVideoEl = document.getElementById('inAppVideo');
const inAppOverlayEl = document.getElementById('inAppPlayerOverlay');
const inAppPlayPauseBtnEl = document.getElementById('inAppPlayPauseBtn');
const inAppSeekFillEl = document.getElementById('inAppSeekFill');
const inAppTimeTextEl = document.getElementById('inAppTimeText');

let inAppLastSaveTime = 0;

if (inAppVideoEl) {
  inAppVideoEl.addEventListener('play', () => {
    updatePlayPauseBtn(true);
    updateDiscordRpc(false);
    if (!traktScrobbledStarted && inAppCurrentMedia) {
      traktScrobbledStarted = true;
      const pct = (inAppVideoEl.duration > 0) ? Math.round((inAppVideoEl.currentTime / inAppVideoEl.duration) * 100) : 0;
      scrobbleTrakt('start', inAppCurrentMedia, pct);
    }
    broadcastWpEvent('play', inAppVideoEl.currentTime);
  });
  inAppVideoEl.addEventListener('pause', () => {
    updatePlayPauseBtn(false);
    updateDiscordRpc(true);
    if (inAppOverlayEl) inAppOverlayEl.classList.remove('idle');
    if (inAppCurrentMedia) {
      const pct = (inAppVideoEl.duration > 0) ? Math.round((inAppVideoEl.currentTime / inAppVideoEl.duration) * 100) : 0;
      scrobbleTrakt('pause', inAppCurrentMedia, pct);
    }
    broadcastWpEvent('pause', inAppVideoEl.currentTime);
  });
  inAppVideoEl.addEventListener('seeked', () => {
    broadcastWpEvent('seek', inAppVideoEl.currentTime);
    updateDiscordRpc(inAppVideoEl.paused);
  });
  inAppVideoEl.addEventListener('timeupdate', () => {
    updateDiscordRpc(inAppVideoEl.paused);
    if (inAppActiveCues.length > 0) {
      updateInAppSubtitleOverlay(inAppVideoEl.currentTime);
    }
    if (inAppVideoEl.duration) {
      const curT = inAppVideoEl.currentTime;
      const durT = inAppVideoEl.duration;
      const pct = (curT / durT) * 100;
      if (inAppSeekFillEl) inAppSeekFillEl.style.width = pct + '%';
      if (inAppTimeTextEl) inAppTimeTextEl.textContent = `${formatTime(curT)} / ${formatTime(durT)}`;

      // Scrobble Trakt.tv automatique dès 80% de visionnage
      if (pct >= 80 && !traktScrobbledStop && inAppCurrentMedia) {
        traktScrobbledStop = true;
        scrobbleTrakt('stop', inAppCurrentMedia, Math.round(pct));
        showInAppToast('✓ Scrobblé sur Trakt.tv (80%)', 2200);
      }

      // Bouton Skip Intro Intelligent & Adaptatif
      const isSeriesNow = Boolean((inAppCurrentMedia && (inAppCurrentMedia.type === 'series' || inAppCurrentMedia.type === 'anime')) || inAppPlaylist.length > 1);
      const skipCard = document.getElementById('inAppSkipIntroCard');
      const skipBtn = document.getElementById('inAppSkipIntroBtn');
      if (skipCard) {
        if (isSeriesNow && !inAppSkipIntroDismissed && durT > 180) {
          let shouldShow = false;
          let btnText = "Passer l'intro";

          if (inAppIntroData && inAppIntroData.op) {
            const op = inAppIntroData.op;
            if (curT >= Math.max(0, op.start - 2) && curT < op.end) {
              shouldShow = true;
              btnText = `Passer l'intro (→ ${formatTime(op.end)})`;
            }
          } else if (inAppIntroData && inAppIntroData.recap && curT >= Math.max(0, inAppIntroData.recap.start - 2) && curT < inAppIntroData.recap.end) {
            shouldShow = true;
            btnText = `Passer le récap (→ ${formatTime(inAppIntroData.recap.end)})`;
          } else {
            // Heuristique : entre 10s et 160s
            if (curT >= 10 && curT <= 160) {
              shouldShow = true;
              const isAnime = Boolean(inAppCurrentMedia && inAppCurrentMedia.type === 'anime');
              const skipSec = (inAppIntroData && inAppIntroData.default_skip) ? inAppIntroData.default_skip : (isAnime ? 90 : 60);
              btnText = `Passer l'intro (+${skipSec}s)`;
            }
          }

          if (shouldShow) {
            if (skipBtn) skipBtn.innerHTML = btnText;
            skipCard.style.display = 'flex';
          } else {
            skipCard.style.display = 'none';
          }
        } else {
          skipCard.style.display = 'none';
        }
      }

      // Carte Prochain épisode (fin de vidéo ou début de l'Ending détecté)
      const nextCard = document.getElementById('inAppNextEpCard');
      const hasNextTrack = inAppPlaylist.length > 1 && inAppPlaylistIndex < inAppPlaylist.length - 1;
      if (nextCard) {
        const remSec = durT - curT;
        const isNearEnd = (hasNextTrack && !inAppNextEpCancelled && durT > 200 && remSec > 0.5 && remSec <= 22);
        const isEndingIntro = (hasNextTrack && !inAppNextEpCancelled && inAppIntroData && inAppIntroData.ed && curT >= inAppIntroData.ed.start && curT <= inAppIntroData.ed.end + 2);

        if (isNearEnd || isEndingIntro) {
          nextCard.style.display = 'flex';
          const nextItem = inAppPlaylist[inAppPlaylistIndex + 1];
          const nextTitleEl = document.getElementById('inAppNextEpTitle');
          const nextCountEl = document.getElementById('inAppNextEpCountdown');
          if (nextTitleEl && nextItem) nextTitleEl.textContent = nextItem.title || 'Épisode suivant';
          if (nextCountEl) nextCountEl.textContent = String(Math.max(1, Math.ceil(remSec)));
        } else {
          nextCard.style.display = 'none';
        }
      }

      const now = Date.now();
      if (inAppCurrentMedia && inAppCurrentMedia.id && now - inAppLastSaveTime > 10000 && curT > 5) {
        inAppLastSaveTime = now;
        const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : null);
        const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : null);
        api('/api/history-record', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            media: {
              id: inAppCurrentMedia.id,
              name: inAppCurrentMedia.name || inAppCurrentTitle,
              type: inAppCurrentMedia.type || 'movie',
              year: inAppCurrentMedia.year || '',
              poster: inAppCurrentMedia.poster || '',
              season: s,
              episode: e,
              filename: inAppCurrentTitle,
              position: Math.round(curT),
              duration: Math.round(durT)
            }
          })
        }).catch(() => {});
      }
    }
  });
  inAppVideoEl.addEventListener('ended', () => {
    if (inAppCurrentMedia) {
      scrobbleTrakt('stop', inAppCurrentMedia, 100);
    }
    if (inAppPlaylist.length > 1 && inAppPlaylistIndex < inAppPlaylist.length - 1) {
      inAppNextTrack();
    }
  });
  let inAppVideoClickTimer = null;
  inAppVideoEl.addEventListener('click', () => {
    if (inAppVideoClickTimer) {
      clearTimeout(inAppVideoClickTimer);
      inAppVideoClickTimer = null;
      return;
    }
    inAppVideoClickTimer = setTimeout(() => {
      inAppVideoClickTimer = null;
      if (inAppVideoEl.muted && !inAppVideoEl._userMuted) {
        inAppVideoEl.muted = false;
        updateInAppVolUI();
      }
      toggleInAppPlay();
    }, 220);
  });
  inAppVideoEl.addEventListener('dblclick', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (inAppVideoClickTimer) {
      clearTimeout(inAppVideoClickTimer);
      inAppVideoClickTimer = null;
    }
    toggleInAppFullscreen();
  });
}

if (inAppOverlayEl) {
  inAppOverlayEl.addEventListener('mousemove', resetInAppIdleTimer);
  inAppOverlayEl.addEventListener('wheel', (e) => {
    e.preventDefault();
    inAppWheelVolume(e.deltaY);
  }, { passive: false });
}

window.addEventListener('keydown', (e) => {
  const overlay = document.getElementById('inAppPlayerOverlay');
  const isPlayerActive = overlay && overlay.classList.contains('active');
  const isInput = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement ? document.activeElement.tagName : '');

  if (!isPlayerActive) {
    // Global macOS & keyboard shortcuts
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      const si = document.getElementById('searchInput');
      if (si) { si.focus(); si.select(); }
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
      if (si) { si.focus(); si.select(); }
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
    if (e.key === 'Escape') {
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
        dp.style.display = 'none';
        document.getElementById('torrentsPanel').style.display = 'none';
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
    const sm = document.getElementById('shortcutsModal');
    if (sm && sm.style.display === 'flex') {
      toggleShortcutsModal();
      return;
    }
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

async function pollDownloads() {
  const data = await api('/api/downloads');
  const items = Object.values(data.downloads || {});
  const panel = document.getElementById('downloadsPanel');
  if (!items.length) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  document.getElementById('downloadsList').innerHTML = items.map(d => `
    <div style="margin-bottom:8px; background:var(--bg); padding:12px; border-radius:6px; border:1px solid var(--border);">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; font-size:0.86rem;">
        <strong>${d.filename}</strong>
        <div style="display:flex; align-items:center; gap:10px; color:var(--muted); font-size:0.8rem;">
          <span>${
            d.status === 'completed' ? 'Terminé' :
            d.status === 'cancelled' ? 'Annulé' :
            d.status.startsWith('error') ? d.status :
            `${d.progress}% (${d.speed})`
          }</span>
          ${d.status === 'downloading' ? `<button class="btn btn-secondary" style="padding:4px 8px; font-size:0.75rem;" onclick='cancelDownload(${JSON.stringify(d.id)})'>Annuler</button>` : ''}
        </div>
      </div>
      <div style="font-size:0.76rem; color:var(--dim); margin-top:4px;">${d.downloaded} / ${d.total} — ${d.path}</div>
      <div class="progress-bar"><div class="progress-fill" style="width:${d.progress}%"></div></div>
    </div>
  `).join('');

  if (items.some(d => d.status === 'downloading')) {
    setTimeout(pollDownloads, 1000);
  }
}

checkConfig();
checkTraktStatus();
startRemotePolling();
pollDownloads();
initSearchPlatformShortcuts();
switchTab('movies');
initResizeHandles();
syncWindowState();
