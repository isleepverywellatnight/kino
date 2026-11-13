let allTorrents = [];
let torrentRequestId = 0;
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
// Titles close to a title, from /api/related (TMDB with the user's key, else genres).
const RELATED_CLIENT_CACHE = new Map();
let spoilerState = Spoilers.empty();
let lastReadyDebrid = null;
let currentSelectMediaId = null;
let animeJourneyState = null;
let animeJourneyMatches = new Map();
let animeJourneyRevealed = new Set();
let animeJourneyRequestId = 0;

function spoilerSeriesId(media = currentMedia) {
  return String(media?.id || '').toLowerCase();
}

function spoilerConcealed(seriesId, season, episode) {
  return Spoilers.concealed(spoilerState, seriesId, season, episode,
    Spoilers.watched(userHistory, seriesId, season, episode));
}

function spoilerEpisodeLabel(seriesId, season, episode, title) {
  return spoilerConcealed(seriesId, season, episode)
    ? Spoilers.neutralTitle(episode) : String(title || Spoilers.neutralTitle(episode));
}

async function loadSpoilerState() {
  try { spoilerState = await api('/api/spoiler-protection'); }
  catch (error) { console.warn('Protection spoilers indisponible', error); }
  refreshSpoilerSurfaces();
}

async function changeSpoilerState(payload) {
  const state = await api('/api/spoiler-protection', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  });
  spoilerState = state;
  refreshSpoilerSurfaces();
}

async function setGlobalSpoilerProtection(enabled) {
  const status = document.getElementById('spoilerSettingsStatus');
  try {
    await changeSpoilerState({action: 'global', enabled});
    if (status) status.textContent = enabled ? 'Protection activée.' : 'Protection désactivée.';
  } catch (error) {
    if (status) status.textContent = 'Impossible de mémoriser ce réglage.';
    refreshSpoilerSurfaces();
  }
}

async function setSeriesSpoilerProtection(value) {
  if (!currentMedia?.id) return;
  try {
    await changeSpoilerState({action: 'series', series_id: currentMedia.id,
      enabled: value === 'inherit' ? null : value === 'on'});
  } catch (error) { refreshSpoilerSurfaces(); }
}

async function revealSpoilerEpisode(season, episode, seriesId = spoilerSeriesId()) {
  try { await changeSpoilerState({action: 'reveal', series_id: seriesId, season, episode}); }
  catch (error) { console.warn('Révélation impossible', error); }
}

function journeyProtectionEnabled() {
  const id = spoilerSeriesId();
  return Object.prototype.hasOwnProperty.call(spoilerState.overrides || {}, id)
    ? spoilerState.overrides[id] : spoilerState.enabled === true;
}

function renderAnimeJourney() {
  const panel = document.getElementById('animeJourneyPanel');
  if (!panel || !animeJourneyState) return;
  const current = animeJourneyState.works?.find(work => work.playback?.id === currentMedia?.id);
  panel.innerHTML = AnimeJourneyView.render(animeJourneyState, userHistory,
    journeyProtectionEnabled(), animeJourneyRevealed, animeJourneyMatches, current?.id);
}

async function resolveJourneyWork(id, requestId = animeJourneyRequestId) {
  if (!animeJourneyState?.ok || !animeJourneyState.works.some(work => work.id === id)) return null;
  const current = animeJourneyState.works.find(work => work.id === id);
  if (current?.playback) return {ok: true, media: {
    id: current.playback.id, name: current.title, type: current.playback.type,
    is_anime: true, year: String(current.year || ''), poster: currentMedia?.poster || ''
  }};
  const cached = animeJourneyMatches.get(id);
  if (cached && cached.reason !== 'unavailable') return cached;
  const originId = currentMedia?.id;
  try {
    const result = await api('/api/anime/journey/resolve', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({imdb_id: originId, type: currentMedia?.type, anilist_id: id}),
    });
    if (requestId === animeJourneyRequestId && currentMedia?.id === originId) {
      animeJourneyMatches.set(id, result);
      renderAnimeJourney();
    }
    return result;
  } catch (error) {
    return {ok: false, reason: 'unavailable', message: 'Vérification momentanément indisponible.'};
  }
}

async function loadAnimeJourney(media, meta) {
  const panel = document.getElementById('animeJourneyPanel');
  if (!panel) return;
  const requestId = ++animeJourneyRequestId;
  const originId = media.id;
  panel.innerHTML = '<p class="journey-empty">Chargement du parcours…</p>';
  try {
    const title = meta.name || media.name;
    const year = meta.releaseInfo || meta.year || media.year || '';
    const kind = media.type === 'movie' ? 'movie' : 'series';
    const result = await api(`/api/anime/journey?imdb_id=${encodeURIComponent(originId)}&title=${encodeURIComponent(title)}&year=${encodeURIComponent(year)}&type=${kind}`);
    if (requestId !== animeJourneyRequestId || currentMedia?.id !== originId) return;
    animeJourneyState = result;
    renderAnimeJourney();
    if (!result.ok) return;
    const unresolved = result.works.filter(work => !work.playback).slice(0, 12);
    let cursor = 0;
    await Promise.all(Array.from({length: 2}, async () => {
      while (cursor < unresolved.length && requestId === animeJourneyRequestId) {
        const work = unresolved[cursor++];
        await resolveJourneyWork(work.id, requestId);
      }
    }));
  } catch (error) {
    if (requestId === animeJourneyRequestId && currentMedia?.id === originId) {
      panel.innerHTML = '<p class="journey-empty">Parcours indisponible pour le moment.</p>';
    }
  }
}

function revealJourneyTitle(id) {
  animeJourneyRevealed.add(Number(id));
  renderAnimeJourney();
}

async function openJourneyWork(id) {
  if (!animeJourneyState?.ok) return;
  const requestId = animeJourneyRequestId;
  const result = await resolveJourneyWork(Number(id));
  if (requestId !== animeJourneyRequestId) return;
  if (!result?.ok) {
    const status = document.getElementById('journeyStatus');
    if (status) status.textContent = result?.message || 'Lecture non identifiée pour cette œuvre.';
    return;
  }
  await selectMedia(result.media);
}

async function playJourneyWork(id, button) {
  if (!animeJourneyState?.ok) return;
  const requestId = animeJourneyRequestId;
  const result = await resolveJourneyWork(Number(id));
  if (requestId !== animeJourneyRequestId) return;
  if (!result?.ok) {
    const status = document.getElementById('journeyStatus');
    if (status) status.textContent = result?.message || 'Lecture non identifiée pour cette œuvre.';
    return;
  }
  await selectMedia(result.media);
  await oneClickCard({stopPropagation() {}}, button, result.media);
}

async function moveJourneyWork(id, delta) {
  if (!animeJourneyState?.ok) return;
  const ids = animeJourneyState.works.map(work => work.id);
  const index = ids.indexOf(Number(id));
  const target = index + Number(delta);
  if (index < 0 || target < 0 || target >= ids.length) return;
  [ids[index], ids[target]] = [ids[target], ids[index]];
  try {
    animeJourneyState = await api('/api/anime/journey/order', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: 'save', imdb_id: currentMedia.id, type: currentMedia.type,
        saga_id: animeJourneyState.saga_id, ids}),
    });
    renderAnimeJourney();
  } catch (error) {
    const status = document.getElementById('journeyStatus');
    if (status) status.textContent = 'Impossible de mémoriser cet ordre.';
  }
}

async function resetJourneyOrder() {
  if (!animeJourneyState?.ok) return;
  try {
    animeJourneyState = await api('/api/anime/journey/order', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: 'reset', imdb_id: currentMedia.id, type: currentMedia.type,
        saga_id: animeJourneyState.saga_id}),
    });
    renderAnimeJourney();
  } catch (error) {
    const status = document.getElementById('journeyStatus');
    if (status) status.textContent = 'Impossible de rétablir l’ordre de sortie.';
  }
}

// The planning hides the artwork of an episode the user has not reached, for a
// series they follow only: the cover of any other series gives nothing away.
function scheduleSpoilerTarget(item) {
  const id = item?.kino?.id;
  const known = id ? userHistory.find(h => String(h.id).toLowerCase() === String(id).toLowerCase()) : null;
  if (!known) return null;
  const seasons = new Set((known.watched_episodes || []).map(code => Number(String(code).match(/^s(\d+)e/i)?.[1])).filter(Boolean));
  return { id: known.id, season: Number(item.kino.season) || (seasons.size === 1 ? [...seasons][0] : 0) };
}

function refreshSpoilerSurfaces() {
  const global = document.getElementById('cfgSpoilerProtection');
  if (global) global.value = spoilerState.enabled ? 'on' : 'off';
  const series = document.getElementById('detailSpoilerChoice');
  if (series && currentMedia?.id) {
    const choice = spoilerState.overrides?.[spoilerSeriesId()];
    series.value = choice === true ? 'on' : choice === false ? 'off' : 'inherit';
  }
  const offer = document.getElementById('detailSpoilerOffer');
  if (offer) offer.hidden = spoilerState.enabled || spoilerState.overrides?.[spoilerSeriesId()] === true;
  if (animeWeekData) renderAnimeWeek();
  const season = Number(document.getElementById('seasonSelect')?.value || 1);
  const selected = document.getElementById('episodeSelect')?.value;
  if (currentMedia && document.getElementById('detailEpisodesList')) {
    onSeasonChange(false);
    if (selected && document.querySelector(`#episodeSelect option[value="${Number(selected)}"]`))
      document.getElementById('episodeSelect').value = selected;
    renderDetailEpisodes(season);
  }
  refreshLatestAiredSpoiler();
  if (currentMedia && allTorrents.length) renderTorrents();
  if (lastReadyDebrid) renderDebridState(...lastReadyDebrid);
  refreshPlayerSpoilerTitles();
  renderAnimeJourney();
}

function refreshLatestAiredSpoiler() {
  const label = document.getElementById('latestAiredText');
  const latest = currentMedia && META_CLIENT_CACHE.get(currentMedia.id)?.latest_aired;
  if (!label || !latest) return;
  const hidden = spoilerConcealed(spoilerSeriesId(), latest.season, latest.episode);
  const watched = Spoilers.watched(userHistory, spoilerSeriesId(), latest.season, latest.episode);
  label.textContent = `${watched ? '✓' : '●'} Dernier épisode diffusé${watched ? '' : ' (non vu)'} : ${latest.code} — ${hidden ? Spoilers.neutralTitle(latest.episode) : latest.title} (${latest.released})`;
}

function refreshPlayerSpoilerTitles() {
  if (!inAppCurrentMedia || !inAppSeriesId) return;
  const current = Spoilers.episodeNumber(inAppCurrentTitle);
  const season = Number(current?.season || inAppCurrentMedia.season || document.getElementById('seasonSelect')?.value || 0);
  const episode = Number(current?.episode || inAppCurrentMedia.episode || document.getElementById('episodeSelect')?.value || 0);
  const title = document.getElementById('inAppTitle');
  if (title) title.textContent = spoilerConcealed(inAppSeriesId, season, episode)
    ? Spoilers.neutralTitle(episode) : inAppCurrentTitle || 'Lecture';
  const next = inAppPlaylist[inAppPlaylistIndex + 1];
  const nextTitle = document.getElementById('inAppNextEpTitle');
  if (next && nextTitle) {
    const parsed = Spoilers.episodeNumber(next.title);
    const nextSeason = parsed?.season || season;
    const nextEpisode = parsed?.episode || (episode > 0 ? episode + 1 : 0);
    nextTitle.textContent = spoilerEpisodeLabel(inAppSeriesId, nextSeason, nextEpisode, next.title);
  }
}

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

// Errors of the page go to KINO's error log (error_log.py).
(function reportPageErrors() {
  if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
  const lastSent = new Map();
  let minute = 0;
  let sentThisMinute = 0;
  function send(kind, message, source, stack) {
    const now = Date.now();
    if (!minute || now - minute > 60000) { minute = now; sentThisMinute = 0; }
    const key = `${kind}|${message}|${source}`;
    if (sentThisMinute >= 20 || (lastSent.has(key) && now - lastSent.get(key) < 60000)) return;
    lastSent.set(key, now);
    sentThisMinute++;
    try {
      fetch('/api/client-error', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, message: String(message || '').slice(0, 500), source: String(source || '').slice(0, 200),
          stack: String(stack || '').slice(0, 2000), tab: typeof activeTab === 'string' ? activeTab : '' }) }).catch(() => {});
    } catch (e) {}
  }
  window.addEventListener('error', event => {
    if (!event || !event.message) return;  // a picture that fails to load is not an error of KINO
    if (/^ResizeObserver loop/.test(event.message)) return;  // WebKit's notice of a late layout, harmless
    const where = event.filename ? `${String(event.filename).split('/').pop()}:${event.lineno || 0}` : '';
    send('Erreur de la page', event.message, where, event.error && event.error.stack);
  });
  window.addEventListener('unhandledrejection', event => {
    const reason = event && event.reason;
    send('Promesse rejetée', (reason && reason.message) || String(reason), '', reason && reason.stack);
  });
})();

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok || data.error) {
    const error = new Error(data.error || 'Erreur serveur');
    error.code = data.code || '';
    if (error.code === 'offline') noteNetworkOffline();
    throw error;
  }
  return data;
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
    const cloudTab = document.getElementById('subtab-rdcloud');
    if (cloudTab) {
      cloudTab.textContent = `Cloud ${pmeta.short}`;
      cloudTab.setAttribute('aria-label', `Cloud ${pmeta.name}`);
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
    window.kinoPrefLang = cfg.pref_lang || 'vf';
    if (typeof showTmdbStatus === 'function') showTmdbStatus(cfg.has_tmdb_key === true);
    window.kinoAutoFrSubs = cfg.auto_fr_subs !== false;
    const notifyField = document.getElementById('cfgEpisodeNotificationsField');
    if (notifyField) notifyField.style.display = cfg.notifications_available === true ? 'flex' : 'none';
    const notifyBox = document.getElementById('cfgEpisodeNotifications');
    if (notifyBox) notifyBox.checked = cfg.episode_notifications !== false;
    const autoSubs = document.getElementById('cfgAutoFrSubs');
    if (autoSubs) autoSubs.checked = window.kinoAutoFrSubs;
    if (document.getElementById('cfgPrefQuality')) {
      document.getElementById('cfgPrefQuality').value = cfg.pref_quality || '4k';
    }
    const priority = document.getElementById('cfgSourcePriority');
    if (priority) priority.value = cfg.source_priority === 'quality' ? 'quality' : 'speed';
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
    if (window.NativePlayer) {
      window.NativePlayer.available = cfg.native_player_available === true;
      window.NativePlayer.enabled = cfg.native_player === true;
    }
    const nativeField = document.getElementById('cfgNativePlayerField');
    if (nativeField) nativeField.style.display = cfg.native_player_available === true ? 'flex' : 'none';
    if (document.getElementById('cfgNativePlayer')) {
      document.getElementById('cfgNativePlayer').checked = cfg.native_player === true;
    }
    const sharedDiscordId = '1556492442415398973';
    const currentCid = String(cfg.discord_client_id || sharedDiscordId).trim();
    const presetEl = document.getElementById('cfgDiscordPreset');
    const customInp = document.getElementById('cfgDiscordClientId');
    const customBox = document.getElementById('cfgDiscordCustomBox');
    if (presetEl) {
      if (currentCid === sharedDiscordId) {
        presetEl.value = sharedDiscordId;
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
    const setupNotice = document.getElementById('settingsSetupNotice');
    if (setupNotice) setupNotice.hidden = Boolean(cfg.has_token);
    if (cfgAccTitle && cfgAccBadge) {
      if (cfg.user) {
        const exp = cfg.user.premium > 0 ? Math.ceil(cfg.user.premium / 86400) + ' jours' : 'Gratuit';
        cfgAccTitle.textContent = `${pmeta.name} · Connecté (${cfg.user.username})`;
        cfgAccSub.textContent = cfg.user.premium > 0 ? `Abonnement valide encore ${exp}.` : 'Compte gratuit, sans abonnement.';
        cfgAccBadge.textContent = 'Actif';
        cfgAccBadge.style.color = '#4ade80';
        cfgAccBadge.style.borderColor = 'rgba(74,222,128,0.4)';
        cfgAccBadge.style.background = 'rgba(74,222,128,0.1)';
      } else if (cfg.has_token && cfg.user_status === 'invalid') {
        cfgAccTitle.textContent = `${pmeta.name} · Clé refusée`;
        cfgAccSub.textContent = 'La clé est invalide ou expirée. Collez-en une nouvelle, puis Vérifier.';
        cfgAccBadge.textContent = 'Clé refusée';
        cfgAccBadge.style.color = '#ef4444';
        cfgAccBadge.style.borderColor = 'rgba(239,68,68,0.4)';
        cfgAccBadge.style.background = 'rgba(239,68,68,0.1)';
      } else if (cfg.has_token) {
        cfgAccTitle.textContent = `${pmeta.name} · Vérification impossible`;
        cfgAccSub.textContent = 'Le service ne répond pas pour le moment. Réessayez plus tard.';
        cfgAccBadge.textContent = 'Indisponible';
        cfgAccBadge.style.color = '#eab308';
        cfgAccBadge.style.borderColor = 'rgba(234,179,8,0.4)';
        cfgAccBadge.style.background = 'rgba(234,179,8,0.1)';
      } else {
        cfgAccTitle.textContent = `${pmeta.name} · Pas de clé`;
        cfgAccSub.textContent = 'Collez votre clé ci-dessous, puis Vérifier.';
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
      } else if (cfg.has_token && cfg.user_status === 'invalid') {
        badge.innerHTML = `
          <span class="user-status-dot" style="background:#ef4444; box-shadow:0 0 8px rgba(239,68,68,0.6);"></span>
          <span style="color:#ef4444; font-weight:600;">${pmeta.short} • Clé invalide</span>
        `;
        badge.title = 'Clé API invalide ou expirée. Cliquer pour reconfigurer.';
      } else if (cfg.has_token) {
        badge.innerHTML = `
          <span class="user-status-dot" style="background:#eab308; box-shadow:0 0 8px rgba(234,179,8,0.6);"></span>
          <span style="color:#eab308; font-weight:600;">${pmeta.short} • Vérification impossible</span>
        `;
        badge.title = 'Le service débrideur ne répond pas. Réessayez plus tard.';
      } else {
        badge.innerHTML = `
          <span class="user-status-dot" style="background:#eab308; box-shadow:0 0 8px rgba(234,179,8,0.6);"></span>
          <span style="color:#eab308; font-weight:600;">Non configuré</span>
        `;
        badge.title = 'Cliquez pour configurer votre clé débrideur';
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
    // A series whose last known episode was watched has nothing left to continue.
    if (h.type === 'series') return !(h.series_finished && isDone);
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
    refreshSpoilerSurfaces();
    renderAnimeJourney();
  } catch (e) {
    console.warn(e);
  }
}

let tonightChoices = [];
let tonightReviewItems = [];
let tonightUndoAction = null;
let tonightRequestId = 0;
let tonightOfflineInitialized = false;

function openTonightModal() {
  const modal = document.getElementById('tonightModal');
  if (!modal) return;
  modal.style.display = 'flex';
  const offline = document.getElementById('tonightOfflineOnly');
  if (offline && !tonightOfflineInitialized) {
    offline.checked = !networkOnline || navigator.onLine === false;
    tonightOfflineInitialized = true;
  }
  document.getElementById('tonightMinutes')?.focus();
  findTonightChoices();
}

function closeTonightModal() {
  tonightRequestId++;
  const modal = document.getElementById('tonightModal');
  if (modal) modal.style.display = 'none';
  const button = document.getElementById('tonightSearchButton');
  if (button) button.disabled = false;
  document.getElementById('tonightOpenButton')?.focus();
}

function renderTonightResult(result) {
  tonightChoices = (result.choices || []).slice(0, 3);
  tonightReviewItems = result.review || [];
  const status = document.getElementById('tonightStatus');
  const choices = document.getElementById('tonightChoices');
  if (!status || !choices) return;
  const emptyMessages = {
    empty_watchlist: 'Ajoutez des titres à Ma Liste pour recevoir des idées.',
    no_offline: 'Aucun titre de Ma Liste n’est prêt hors ligne. Préparez un fichier avant de couper le réseau.',
    all_watched: 'Tous les titres de Ma Liste sont déjà vus.',
    all_deferred: 'Tous les titres disponibles sont reportés ou écartés. Consultez Mes choix ci-dessous.',
    no_match: 'Aucun titre ne correspond. Essayez une autre durée ou une autre ambiance.',
  };
  status.textContent = tonightChoices.length
    ? result.offline_only ? `${tonightChoices.length} idée${tonightChoices.length > 1 ? 's' : ''} prête${tonightChoices.length > 1 ? 's' : ''} hors ligne.`
      : result.coverage?.partial ? 'Certaines durées ou ambiances restent inconnues ; les titres sont classés avec les données disponibles.'
        : `${tonightChoices.length} idée${tonightChoices.length > 1 ? 's' : ''} dans Ma Liste, selon vos choix.`
    : (emptyMessages[result.empty_reason] || emptyMessages.no_match);
  choices.innerHTML = tonightChoices.map((choice, index) => `
    <article class="tonight-choice">
      <div class="tonight-choice-main">
        ${choice.item?.poster ? `<img class="tonight-poster" src="${escapeHtml(choice.item.poster)}" alt="" loading="lazy">` : '<div class="tonight-poster-placeholder" aria-hidden="true">▷</div>'}
        <div><span class="tonight-choice-index">IDÉE ${String(index + 1).padStart(2, '0')}</span>
          <h3>${escapeHtml(choice.item?.name || 'Titre inconnu')}</h3>
          <p>${(choice.reasons || []).map(escapeHtml).join(' · ')}</p></div>
      </div>
      <div class="tonight-choice-actions">
        <button type="button" class="btn" onclick="playTonightChoice(${index}, event)">${choice.offline_id ? 'Lire hors ligne' : 'Regarder'}</button>
        <button type="button" class="btn btn-secondary" onclick="openTonightChoice(${index})">Voir la fiche</button>
      </div>
      <div class="tonight-feedback-actions" aria-label="Votre avis sur cette idée">
        <button type="button" onclick="sendTonightFeedback(${index}, 'defer')">Pas ce soir</button>
        <button type="button" onclick="sendTonightFeedback(${index}, 'seen')">Déjà vu</button>
        <button type="button" onclick="sendTonightFeedback(${index}, 'exclude')">Ça ne m’intéresse pas</button>
      </div>
    </article>
  `).join('');
  const review = document.getElementById('tonightReview');
  if (review) review.innerHTML = tonightReviewItems.length
    ? tonightReviewItems.map((item, index) => `<div class="tonight-review-row"><span><strong>${escapeHtml(item.name || 'Titre')}</strong><small>${item.decision === 'deferred' ? 'Reporté 24 h' : item.decision === 'seen' ? 'Marqué déjà vu' : 'Écarté de Ce soir'}</small></span><button type="button" class="btn btn-secondary" onclick="restoreTonightChoice(${index})">${item.decision === 'seen' ? 'Annuler' : 'Réintégrer'}</button></div>`).join('')
    : '<p class="tonight-review-empty">Aucun titre reporté ou écarté.</p>';
  const reviewSummary = document.getElementById('tonightReviewSummary');
  if (reviewSummary) reviewSummary.textContent = `Mes choix${tonightReviewItems.length ? ` (${tonightReviewItems.length})` : ''}`;
  const undo = document.getElementById('tonightUndo');
  if (undo) {
    undo.style.display = tonightUndoAction ? 'flex' : 'none';
    undo.innerHTML = tonightUndoAction
      ? `<span>${escapeHtml(tonightUndoAction.label)}</span><button type="button" class="btn btn-secondary" onclick="undoTonightFeedback()">Annuler</button>` : '';
  }
}

async function findTonightChoices() {
  const request = ++tonightRequestId;
  const status = document.getElementById('tonightStatus');
  const choices = document.getElementById('tonightChoices');
  const button = document.getElementById('tonightSearchButton');
  if (!status || !choices || !button) return;
  button.disabled = true;
  choices.innerHTML = '';
  status.textContent = 'Recherche dans Ma Liste…';
  try {
    const result = await api('/api/tonight', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({minutes: Number(document.getElementById('tonightMinutes').value),
        mood: document.getElementById('tonightMood').value,
        offline_only: document.getElementById('tonightOfflineOnly')?.checked === true}),
    });
    if (request !== tonightRequestId) return;
    renderTonightResult(result);
  } catch (error) {
    if (request === tonightRequestId) status.textContent = 'Impossible de préparer les suggestions. Réessayez.';
  } finally {
    if (request === tonightRequestId) button.disabled = false;
  }
}

async function playTonightChoice(index, event) {
  const choice = tonightChoices[index];
  const item = choice?.item;
  if (!item) return;
  closeTonightModal();
  if (choice.offline_id) {
    await pollDownloads();
    await playOfflineDownload(choice.offline_id);
  } else {
    oneClickCard(event, null, item);
  }
}

function openTonightChoice(index) {
  const item = tonightChoices[index]?.item;
  if (!item) return;
  closeTonightModal();
  selectMedia(item);
}

async function sendTonightFeedback(index, action) {
  const item = tonightChoices[index]?.item;
  if (!item?.id) return;
  try {
    const result = await api('/api/tonight/feedback', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: item.id, action}),
    });
    const labels = {defer: 'Titre reporté pendant 24 h.', seen: 'Titre marqué déjà vu.',
      exclude: 'Titre écarté de Ce soir.'};
    tonightUndoAction = {id: item.id, token: result.undo_token, action, label: labels[action]};
    if (action === 'seen') await refreshUserLists();
    await findTonightChoices();
  } catch (error) {
    document.getElementById('tonightStatus').textContent = error.message;
  }
}

async function undoTonightFeedback() {
  const action = tonightUndoAction;
  if (!action) return;
  try {
    await api('/api/tonight/undo', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: action.id, undo_token: action.token})});
    tonightUndoAction = null;
    if (action.action === 'seen') await refreshUserLists();
    await findTonightChoices();
  } catch (error) {
    document.getElementById('tonightStatus').textContent = error.message;
  }
}

async function restoreTonightChoice(index) {
  const item = tonightReviewItems[index];
  if (!item) return;
  try {
    if (item.decision === 'seen') {
      await api('/api/tonight/undo', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: item.id, undo_token: item.undo_token})});
      await refreshUserLists();
    } else {
      await api('/api/tonight/feedback', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: item.id, action: 'restore'})});
    }
    tonightUndoAction = null;
    await findTonightChoices();
  } catch (error) {
    document.getElementById('tonightStatus').textContent = error.message;
  }
}

async function resetTonightPreferences() {
  if (!window.confirm('Effacer les reports, exclusions et fréquences de Ce soir ? Les titres marqués déjà vus resteront dans votre historique.')) return;
  try {
    await api('/api/tonight/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    tonightUndoAction = null;
    await findTonightChoices();
  } catch (error) {
    document.getElementById('tonightStatus').textContent = error.message;
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
// The last page of the catalogue was full: « Voir plus » has more to bring.
let catalogHasMore = false;
let currentTrailerCtx = {trailerId: '', title: '', year: '', lang: 'vf'};

function getSeriesTargetEpisode(h) {
  if (!h || h.type !== 'series') return {season: 1, episode: 1, isNext: false};
  const s = Number(h.season || 1);
  const e = Number(h.episode || 1);
  const pct = Number(h.progress_pct || 0);
  const isDone = Boolean(h.completed || pct >= 85);
  if (isDone && h.series_finished) {
    return {season: 1, episode: 1, isNext: false, finished: true, prevSeason: s, prevEpisode: e};
  }
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

const FRENCH_MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];

function episodeCode(season, episode) {
  return `S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`;
}

// « demain », « le 14 novembre », « le 3 janvier 2027 ».
function frenchReleaseDay(isoDay, todayIso) {
  const [year, month, day] = String(isoDay || '').split('-').map(Number);
  if (!year || !month || !day) return '';
  const tomorrow = new Date(Date.parse(`${todayIso}T00:00:00Z`) + 86400000).toISOString().slice(0, 10);
  if (isoDay === tomorrow) return 'demain';
  const sameYear = String(todayIso).slice(0, 4) === String(year);
  return `le ${day === 1 ? '1er' : day} ${FRENCH_MONTHS[month - 1]}${sameYear ? '' : ` ${year}`}`;
}

// Name, still and release of one episode, from the series meta (Cinemeta videos).
function resumeEpisodeInfo(meta, season, episode, todayIso) {
  const videos = Array.isArray(meta?.videos) ? meta.videos : [];
  const video = videos.find(v => Number(v.season) === Number(season) && Number(v.episode || v.number) === Number(episode));
  if (!video) return null;
  const rawName = String(video.name || video.title || '').trim();
  const name = /^(episode|épisode)\s*\d+$/i.test(rawName) ? '' : rawName;
  const still = /^https:\/\//.test(String(video.thumbnail || '')) ? String(video.thumbnail) : '';
  const released = String(video.released || '').slice(0, 10);
  return { name, still, released, upcoming: /^\d{4}-\d{2}-\d{2}$/.test(released) && released > todayIso };
}

function resumeBackdrop(h) {
  if (h.backdrop || h.background) return h.backdrop || h.background;
  if (/^tt\d{1,12}$/.test(String(h.id || ''))) return `https://images.metahub.space/background/medium/${encodeURIComponent(h.id)}/img`;
  return h.poster || '';
}

// Everything a « À continuer » card shows, from a history entry and, for a
// series, its cached meta (episode name, still, release date).
function resumeCardView(h, meta, todayIso) {
  const isSeries = h.type === 'series';
  const pct = Math.min(100, Math.max(0, Number(h.progress_pct || 0)));
  const target = isSeries ? getSeriesTargetEpisode(h) : null;
  const isNext = Boolean(target && target.isNext);
  const code = isSeries ? episodeCode(target.season, target.episode) : '';
  const episode = isSeries && meta ? resumeEpisodeInfo(meta, target.season, target.episode, todayIso) : null;
  const upcoming = Boolean(isNext && episode && episode.upcoming);
  const remaining = !isNext && pct > 0 ? formatRemaining(h.position, h.duration) : '';
  let status = remaining || 'Reprendre la lecture';
  if (isNext) status = upcoming ? `Prochain épisode ${frenchReleaseDay(episode.released, todayIso)}` : 'Épisode suivant';
  const kicker = isSeries
    ? [code, episode && episode.name].filter(Boolean).join(' · ')
    : ['Film', h.year].filter(Boolean).join(' · ');
  let action = 'Continuer';
  if (isNext) action = upcoming ? `Voir la fiche, ${status.toLowerCase()}` : `Épisode suivant · ${code}`;
  // An episode not aired yet has no image: the series backdrop stands in.
  const still = episode && !upcoming ? episode.still : '';
  const backdrop = resumeBackdrop(h);
  return {
    isSeries, isNext, upcoming, code, kicker, status, action,
    progress: !isNext && pct > 0 ? pct : 0,
    image: still || backdrop,
    fallback: still && backdrop !== still ? backdrop : '',
    poster: h.poster || '',
    play: {
      imdb_id: h.id,
      type: h.type || 'movie',
      season: isSeries ? target.season : (h.season || 1),
      episode: isSeries ? target.episode : (h.episode || 1),
      title: isSeries ? `${h.name} — ${code}` : h.name,
      name: h.name,
      poster: h.poster || '',
      year: h.year || '',
    },
    media: { id: h.id, name: h.name, type: h.type || 'movie', poster: h.poster || '', year: h.year || '' },
  };
}

function openResumeCard(button, play, media) {
  const card = button.closest('.resume-card');
  // An episode not out yet, or a new season of a series never started: the fiche.
  if (card?.classList.contains('is-upcoming') || card?.classList.contains('opens-fiche')) selectMedia(media);
  else oneClickPlay(play, button);
}

// A missing episode still falls back to the backdrop, then to the (blurred)
// poster, then to the plain dark tile.
function resumeImageFallback(img) {
  const src = img.getAttribute('src');
  const fallback = img.dataset.fallback || '';
  const poster = img.dataset.poster || '';
  if (fallback && src !== fallback && src !== poster) {
    img.src = fallback;
  } else if (poster && src !== poster) {
    img.classList.add('is-poster');
    img.src = poster;
  } else {
    img.remove();
  }
}

// Series metas (episode names and stills) are fetched once, then the section
// is drawn again from the cache.
const resumeMetaRequests = new Map();
function requestResumeMetas(items) {
  const missing = items
    .filter(h => h.type === 'series' && h.id && !META_CLIENT_CACHE.has(h.id) && !resumeMetaRequests.has(h.id))
    .slice(0, 12);
  if (!missing.length) return;
  Promise.allSettled(missing.map(h => {
    const request = api(`/api/meta?type=series&imdb_id=${encodeURIComponent(h.id)}`)
      .then(data => { META_CLIENT_CACHE.set(h.id, (data && data.meta) || {}); });
    resumeMetaRequests.set(h.id, request);
    return request;
  })).then(results => {
    if (results.some(result => result.status === 'fulfilled')) renderHomeResume();
  });
}

