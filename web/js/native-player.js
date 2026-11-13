// Native player overlay (« Lecteur natif », macOS): mpv draws the video
// under the KINO window's web view; this page only shows the controls. The
// page turns transparent while it plays (html.native-playing).
(function () {
  const LANGUAGES = {
    fre: 'Français', fra: 'Français', fr: 'Français', eng: 'Anglais', en: 'Anglais', jpn: 'Japonais', ja: 'Japonais',
    ger: 'Allemand', deu: 'Allemand', de: 'Allemand', spa: 'Espagnol', es: 'Espagnol', ita: 'Italien', it: 'Italien',
    por: 'Portugais', pt: 'Portugais', kor: 'Coréen', ko: 'Coréen', chi: 'Chinois', zho: 'Chinois', rus: 'Russe', ru: 'Russe',
  };
  const CHANNELS = { 1: 'mono', 2: 'stéréo', 6: '5.1', 8: '7.1' };
  const SAVE_EVERY_MS = 10000;
  const DISCORD_EVERY_MS = 4000;
  const NEXT_DELAY_MS = 10000;
  // Same steps as the built-in player.
  const SPEEDS = [0.75, 1, 1.25, 1.5, 2];
  const SUB_SIZES = [['A-', 0.8], ['A', 1], ['A+', 1.25], ['A++', 1.5]];
  const CLARITY = ['Normal', 'Ombres +', 'Nuit ++', 'HDR Max'];

  function formatTime(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
  }

  function trackLabel(track) {
    const parts = [LANGUAGES[String(track.lang || '').toLowerCase()] || (track.lang ? track.lang.toUpperCase() : `Piste ${track.id}`)];
    if (track.title) parts.push(track.title);
    if (track.type === 'audio' && CHANNELS[track.channels]) parts.push(CHANNELS[track.channels]);
    return parts.join(' · ');
  }

  // Keyboard: same keys as the web player.
  function keyAction(event) {
    const key = event.key;
    if (key === ' ' || key === 'k' || key === 'K') return ['toggle'];
    if (key === 'ArrowRight') return ['seek', event.shiftKey ? 60 : 10];
    if (key === 'ArrowLeft') return ['seek', event.shiftKey ? -60 : -10];
    if (key === 'ArrowUp') return ['volumeStep', 5];
    if (key === 'ArrowDown') return ['volumeStep', -5];
    if (key === 'm' || key === 'M') return ['mute'];
    if (key === 'f' || key === 'F') return ['fullscreen'];
    if (key === 'Escape') return ['escape'];
    return null;
  }

  const session = { open: false, item: null, media: null, title: '', poll: null, lastSave: 0, recorded: false,
    last: null, hideTimer: null, dragging: false, onlineSubs: null, flash: '', flashUntil: 0,
    trakt: { started: false, playing: false, stopped: false }, discord: { at: 0, paused: null },
    source: null, filename: '', playlist: [], index: 0, fallback: null, loaded: false, openedAt: 0, nextAt: 0,
    nextCancelled: false, failing: false, switching: false, intro: null, skipDismissed: false, first: true,
    undo: null, autoSubs: false, subsOff: false };
  const UNDO_SKIP_MS = 7000;

  function element(id) { return document.getElementById(id); }

  // Same icons as the built-in player (SVG_ICONS lives in app-player.js).
  const ICONS = {
    back10: '<svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/><text x="12" y="15" font-size="7" font-weight="700" fill="currentColor" stroke="none" text-anchor="middle">10</text></svg>',
    fwd10: '<svg class="svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/><text x="12" y="15" font-size="7" font-weight="700" fill="currentColor" stroke="none" text-anchor="middle">10</text></svg>',
    fullscreen: '<svg class="svg-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>',
    prev: '<svg class="svg-icon" viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>',
    next: '<svg class="svg-icon" viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>',
  };
  function icon(name) {
    const shared = typeof SVG_ICONS === 'object' && SVG_ICONS ? SVG_ICONS[name] : '';
    return shared || ICONS[name] || '';
  }

  function buildOverlay() {
    if (element('nativePlayer')) return;
    const root = document.createElement('div');
    root.id = 'nativePlayer';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-label', 'Lecteur natif');
    root.innerHTML = `
      <div class="inapp-hud-top native-top">
        <div class="kp-top-left no-drag">
          <button type="button" class="inapp-btn kp-pill kp-back" id="nativeBack">← Retour</button>
        </div>
        <div class="kp-title"><span id="nativeTitle"></span><small class="kp-subtitle" id="nativeInfo">Lecteur natif</small></div>
        <div class="kp-top-actions no-drag">
          <select class="inapp-btn kp-pill kp-select" id="nativeAudio" aria-label="Piste audio" title="Choisir la piste audio"></select>
          <select class="inapp-btn kp-pill kp-select" id="nativeSubs" aria-label="Sous-titres" title="Choisir les sous-titres"></select>
          <div class="kp-more">
            <button type="button" class="inapp-btn kp-pill" id="nativeMoreBtn" aria-expanded="false" aria-controls="nativeMoreMenu" title="Réglages de l'image et du son, sous-titres en ligne, partage"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>Options</button>
            <div id="nativeMoreMenu" class="kp-menu" role="group" aria-label="Options de lecture" hidden>
              <div class="kp-menu-heading">Image et son</div>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeSubSize" data-name="Taille des sous-titres">A</button>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeVoice" data-name="Voix">Normal</button>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeClarity" data-name="Clarté">Normal</button>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeSpeed" data-name="Vitesse">1.0x</button>
              <div class="kp-menu-heading">Partager</div>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeTv" title="Continuer sur la TV, au même moment" hidden>Sur la TV</button>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeKeep" hidden>Garder hors ligne</button>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeShot">Capturer l'image</button>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeRemote">Télécommande</button>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeParty">Watch Party</button>
              <div class="kp-menu-heading" id="nativeWebHeading" hidden>Autre lecteur</div>
              <button type="button" class="inapp-btn kp-menu-item" id="nativeWeb" title="Image dans l'image" hidden>Ouvrir dans le lecteur intégré</button>
              <div class="kp-menu-heading">Sous-titres en ligne</div>
              <div id="nativeOnlineSubs" class="kp-menu-list"><p class="kp-menu-note">Ouvrez ce menu pour chercher.</p></div>
            </div>
          </div>
        </div>
      </div>
      <div class="native-center" id="nativeStatus" aria-live="polite">Chargement…</div>
      <div class="inapp-floating-card" id="nativeSkipCard">
        <button type="button" class="btn" id="nativeSkipGo">Passer l'intro</button>
        <button type="button" class="btn btn-secondary" id="nativeSkipClose" aria-label="Masquer « Passer l'intro »">✕</button>
      </div>
      <div class="inapp-floating-card" id="nativeUndoCard">
        <button type="button" class="btn btn-secondary" id="nativeUndoGo">↩ Revenir</button>
      </div>
      <div class="inapp-floating-card" id="nativeNextCard" role="status">
        <div><div class="kp-next-label">À suivre dans <span id="nativeNextCount">10</span> s</div><div class="kp-next-title" id="nativeNextTitle">Épisode suivant</div></div>
        <button type="button" class="btn" id="nativeNextGo">Lancer</button>
        <button type="button" class="btn btn-secondary" id="nativeNextCancel">Annuler</button>
      </div>
      <div class="inapp-hud-bottom native-bottom">
        <input type="range" class="kp-range kp-seek" id="nativeSeek" min="0" max="1000" value="0" aria-label="Position dans le film">
        <div class="inapp-controls-row">
          <div class="inapp-controls-group">
            <button type="button" class="inapp-btn inapp-btn-icon" id="nativePrev" aria-label="Épisode précédent" title="Épisode précédent" hidden>${icon('prev')}</button>
            <button type="button" class="inapp-btn inapp-btn-icon" id="nativeBack10" aria-label="Reculer de 10 secondes" title="Reculer 10 s (Flèche gauche)">${icon('back10')}</button>
            <button type="button" class="inapp-btn inapp-btn-icon" id="nativePlay" aria-label="Pause" title="Lecture / Pause (Espace)">${icon('pause')}</button>
            <button type="button" class="inapp-btn inapp-btn-icon" id="nativeFwd10" aria-label="Avancer de 10 secondes" title="Avancer 10 s (Flèche droite)">${icon('fwd10')}</button>
            <button type="button" class="inapp-btn inapp-btn-icon" id="nativeNext" aria-label="Épisode suivant" title="Épisode suivant" hidden>${icon('next')}</button>
            <span class="kp-time" id="nativeTime">0:00 / 0:00</span>
          </div>
          <div class="inapp-controls-group">
            <div class="inapp-vol-group">
              <button type="button" class="inapp-btn inapp-btn-icon" id="nativeMute" aria-label="Couper le son" title="Muet (M)">${icon('volHigh')}</button>
              <input type="range" class="kp-range kp-volume" id="nativeVolume" min="0" max="100" value="100" aria-label="Volume">
              <span class="inapp-vol-text" id="nativeVolumeText">100%</span>
            </div>
            <button type="button" class="inapp-btn inapp-btn-icon" id="nativeFullscreen" aria-label="Plein écran" title="Plein écran (F)">${icon('fullscreen')}</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(root);
    element('nativeBack').addEventListener('click', () => close());
    element('nativePlay').addEventListener('click', () => command('toggle'));
    element('nativeBack10').addEventListener('click', () => command('seek', -10));
    element('nativeFwd10').addEventListener('click', () => command('seek', 10));
    element('nativeMute').addEventListener('click', () => command('mute'));
    element('nativePrev').addEventListener('click', () => playIndex(session.index - 1));
    element('nativeNext').addEventListener('click', () => playIndex(session.index + 1));
    element('nativeNextGo').addEventListener('click', () => playIndex(session.index + 1));
    element('nativeNextCancel').addEventListener('click', () => {
      session.nextCancelled = true;
      if (session.last) render(session.last);
    });
    element('nativeFullscreen').addEventListener('click', () => toggleFullscreen());
    const seek = element('nativeSeek');
    seek.addEventListener('input', () => { session.dragging = true; paintRange(seek); });
    seek.addEventListener('change', () => {
      session.dragging = false;
      const duration = session.last?.duration || 0;
      if (duration > 0) command('goto', (Number(seek.value) / 1000) * duration);
    });
    element('nativeVolume').addEventListener('input', event => {
      const volume = Number(event.target.value);
      paintRange(event.target);
      element('nativeVolumeText').textContent = `${volume}%`;
      try { localStorage.setItem('kino_inapp_volume', String(volume / 100)); } catch (e) {}
      command('volume', volume);
    });
    element('nativeAudio').addEventListener('change', event => command('audio', event.target.value));
    element('nativeSubs').addEventListener('change', event => {
      // « Aucun » chosen by hand: no automatic subtitles for the rest of this viewing.
      if (event.target.value === 'no' || event.target.value === '') session.subsOff = true;
      command('subtitles', event.target.value);
    });
    element('nativeMoreBtn').addEventListener('click', event => toggleMenu(event.detail === 0));
    element('nativeSubSize').addEventListener('click', () => cycleSetting('sub_scale'));
    element('nativeVoice').addEventListener('click', () => cycleSetting('voice'));
    element('nativeClarity').addEventListener('click', () => cycleSetting('clarity'));
    element('nativeSpeed').addEventListener('click', () => cycleSetting('speed'));
    element('nativeShot').addEventListener('click', () => { closeMenu(false); screenshot(); });
    element('nativeKeep').addEventListener('click', () => { closeMenu(false); keepOffline(); });
    element('nativeTv').addEventListener('click', () => { closeMenu(false); sendToTv(); });
    element('nativeWeb').addEventListener('click', () => { closeMenu(false); handOver(); });
    element('nativeSkipGo').addEventListener('click', () => skipIntro());
    element('nativeUndoGo').addEventListener('click', () => undoSkip());
    element('nativeParty').addEventListener('click', () => {
      closeMenu(false);
      if (typeof openWatchPartyModal === 'function') openWatchPartyModal();
    });
    element('nativeSkipClose').addEventListener('click', () => {
      session.skipDismissed = true;
      element('nativeSkipCard').style.display = 'none';
    });
    element('nativeRemote').addEventListener('click', () => {
      closeMenu(false);
      if (typeof openRemoteModal === 'function') openRemoteModal();
    });
    root.addEventListener('mousemove', showControls);
    root.addEventListener('pointerdown', event => {
      if (isMenuOpen() && !event.target.closest?.('.kp-more')) closeMenu(false);
    });
  }

  // --- « Options » (same menu as the built-in player) ---
  function isMenuOpen() {
    const menu = element('nativeMoreMenu');
    return Boolean(menu && !menu.hidden);
  }

  function menuItems() {
    const menu = element('nativeMoreMenu');
    return menu ? [...menu.querySelectorAll('button')].filter(item => !item.disabled) : [];
  }

  function toggleMenu(focusFirst) {
    if (isMenuOpen()) {
      closeMenu(false);
      return;
    }
    element('nativeMoreMenu').hidden = false;
    element('nativeMoreBtn').setAttribute('aria-expanded', 'true');
    showControls();
    loadOnlineSubtitles();
    if (focusFirst) menuItems()[0]?.focus();
  }

  function closeMenu(returnFocus) {
    const menu = element('nativeMoreMenu');
    if (!menu || menu.hidden) return false;
    menu.hidden = true;
    element('nativeMoreBtn').setAttribute('aria-expanded', 'false');
    if (returnFocus) element('nativeMoreBtn').focus();
    showControls();
    return true;
  }

  // Arrows move between items; Space and Enter press them. True when handled.
  function menuKey(event) {
    const menu = element('nativeMoreMenu');
    if (!menu || menu.hidden || !menu.contains(document.activeElement)) return false;
    if (event.key === ' ' || event.key === 'Enter') return true;
    const items = menuItems();
    const index = items.indexOf(document.activeElement);
    let next = -1;
    if (event.key === 'ArrowDown') next = (index + 1) % items.length;
    else if (event.key === 'ArrowUp') next = (index - 1 + items.length) % items.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = items.length - 1;
    if (next < 0) return false;
    event.preventDefault();
    items[next]?.focus();
    return true;
  }

  function nearest(list, value) {
    let best = 0;
    list.forEach((item, index) => { if (Math.abs(item - value) < Math.abs(list[best] - value)) best = index; });
    return best;
  }

  function speedLabel(speed) {
    const value = Number(speed) || 1;
    return Number.isInteger(value) ? `${value.toFixed(1)}x` : `${value}x`;
  }

  // The next value of a cycling setting, from the player's current state.
  function nextSetting(name, state) {
    if (name === 'speed') return SPEEDS[(nearest(SPEEDS, Number(state?.speed) || 1) + 1) % SPEEDS.length];
    if (name === 'sub_scale') {
      const scales = SUB_SIZES.map(size => size[1]);
      return scales[(nearest(scales, Number(state?.sub_scale) || 1) + 1) % scales.length];
    }
    if (name === 'voice') return state?.voice === 'boost' ? 'normal' : 'boost';
    if (name === 'clarity') return ((Number(state?.clarity) || 0) + 1) % CLARITY.length;
    return null;
  }

  function cycleSetting(name) {
    const value = nextSetting(name, session.last);
    if (value !== null) command(name, value);
  }

  function renderSettings(state) {
    const scales = SUB_SIZES.map(size => size[1]);
    element('nativeSubSize').textContent = SUB_SIZES[nearest(scales, Number(state.sub_scale) || 1)][0];
    element('nativeVoice').textContent = state.voice === 'boost' ? 'Boost' : 'Normal';
    element('nativeClarity').textContent = CLARITY[Number(state.clarity) || 0] || CLARITY[0];
    element('nativeSpeed').textContent = speedLabel(state.speed);
  }

  // --- Online subtitles (OpenSubtitles, as in the built-in player) ---
  function onlineSubtitleChoices(subtitles) {
    const choices = [];
    for (const [lang, name] of [['fr', 'Français'], ['en', 'Anglais']]) {
      const list = (subtitles || []).filter(item => item.lang === lang).slice(0, 2);
      list.forEach((item, index) => {
        const variant = item.variant === 'sdh' ? ' · SDH' : item.variant === 'forced' ? ' · forcés' : '';
        choices.push({
          url: item.url, lang, variant: item.variant || 'standard',
          label: `${name}${list.length > 1 ? ` #${index + 1}` : ''}${variant}${item.partial ? ' · partiel' : ''}`,
          title: `OpenSubtitles${list.length > 1 ? ` #${index + 1}` : ''}${variant}`,
        });
      });
    }
    return choices;
  }

  function renderOnlineSubtitles() {
    const box = element('nativeOnlineSubs');
    if (!box) return;
    const subs = session.onlineSubs;
    if (subs === 'loading') {
      box.innerHTML = '<p class="kp-menu-note">Recherche…</p>';
    } else if (subs === 'none') {
      box.innerHTML = '<p class="kp-menu-note">Pas de recherche possible pour ce fichier.</p>';
    } else if (!Array.isArray(subs) || !subs.length) {
      box.innerHTML = '<p class="kp-menu-note">Aucun sous-titre trouvé en ligne.</p>';
    } else {
      box.innerHTML = subs.map((choice, index) => `<button type="button" class="inapp-btn kp-menu-item" data-online="${index}"${choice.added ? ' disabled' : ''}>${escapeHtml(choice.label)}${choice.added ? ' · ajouté' : ''}</button>`).join('');
      box.querySelectorAll('[data-online]').forEach(button => {
        button.addEventListener('click', () => addOnlineSubtitle(Number(button.dataset.online)));
      });
    }
  }

  async function loadOnlineSubtitles() {
    if (session.onlineSubs !== null) return;
    const media = session.media || {};
    if (!/^tt\d+$/.test(String(media.id || ''))) {
      session.onlineSubs = 'none';
      renderOnlineSubtitles();
      return;
    }
    session.onlineSubs = 'loading';
    renderOnlineSubtitles();
    const isSeries = media.type === 'series' || media.type === 'anime';
    const params = new URLSearchParams({
      imdb_id: media.id, type: isSeries ? 'series' : 'movie',
      season: String(media.season || 1), episode: String(media.episode || 1),
      release: String(session.filename || session.title || '').slice(0, 300),
    });
    try {
      const result = await api(`/api/subtitles?${params}`);
      session.onlineSubs = onlineSubtitleChoices(result.subtitles);
    } catch (error) {
      session.onlineSubs = null;
      element('nativeOnlineSubs').innerHTML = '<p class="kp-menu-note">Recherche impossible pour le moment.</p>';
      return;
    }
    renderOnlineSubtitles();
  }

  async function addOnlineSubtitle(index, message = '') {
    const choice = Array.isArray(session.onlineSubs) ? session.onlineSubs[index] : null;
    if (!choice || choice.added) return;
    flash('Chargement du sous-titre…', 8000);
    try {
      const result = await api('/api/native-player/subtitle', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ url: choice.url, label: choice.title }),
      });
      choice.added = true;
      renderOnlineSubtitles();
      flash(message || `Sous-titres : ${choice.label}`, message ? 3200 : 2200);
      render(result.state);
    } catch (error) {
      flash(error.message || 'Sous-titre indisponible.', 3000);
    }
  }

  // --- French subtitles for a film or an episode in its original language ---
  async function autoFrenchSubtitles(state) {
    if (session.autoSubs) return;
    session.autoSubs = true;
    if (window.kinoAutoFrSubs === false || session.subsOff || typeof SeriesPlayback === 'undefined') return;
    const plan = SeriesPlayback.frenchSubtitlePlan(state.tracks, session.filename || session.title);
    if (plan.action === 'select') {
      await command('subtitles', plan.id, { silent: true });
      flash('Version originale : sous-titres français activés', 2800);
    } else if (plan.action === 'online') {
      if (typeof networkOnline !== 'undefined' && !networkOnline) {
        flash('Sans internet : pas de sous-titres français en ligne', 3200);
        return;
      }
      await loadOnlineSubtitles();
      if (!Array.isArray(session.onlineSubs)) return;
      const choices = session.onlineSubs;
      const standard = choices.findIndex(choice => choice.lang === 'fr' && choice.variant === 'standard');
      const index = standard >= 0 ? standard : choices.findIndex(choice => choice.lang === 'fr' && choice.variant !== 'forced');
      if (index >= 0) await addOnlineSubtitle(index, 'Version originale : sous-titres français ajoutés (OpenSubtitles)');
      else flash('Version originale : aucun sous-titre français trouvé', 3200);
    }
  }

  // --- Capture, messages ---
  function flash(message, duration) {
    session.flash = message;
    session.flashUntil = Date.now() + duration;
    if (session.last) render(session.last);
  }

  async function screenshot() {
    const time = formatTime(session.last?.time || 0).replace(/:/g, 'm') + 's';
    try {
      const result = await api('/api/native-player/screenshot', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ title: session.title, time_str: time }),
      });
      flash(`Capture enregistrée : ${result.filename}`, 2600);
    } catch (error) {
      flash('Capture impossible.', 2200);
    }
  }

  // --- Discord and Trakt, like the built-in player ---
  function percent(state) {
    return state.duration > 0 ? Math.round(100 * state.time / state.duration) : 0;
  }

  function syncIntegrations(state) {
    const media = session.media || {};
    const playing = Boolean(!state.paused && !state.ended && state.time > 0);
    const pct = percent(state);
    if (typeof scrobbleTrakt === 'function' && media.id && !session.trakt.stopped) {
      const traktMedia = { ...media, name: media.name || session.title };
      if (pct >= 80 || state.ended) {
        session.trakt.stopped = true;
        scrobbleTrakt('stop', traktMedia, state.ended ? 100 : pct).then(sent => { if (sent) flash('Ajouté à Trakt', 2200); });
      } else if (playing !== session.trakt.playing && (playing || session.trakt.started)) {
        session.trakt.started = true;
        scrobbleTrakt(playing ? 'start' : 'pause', traktMedia, pct);
      }
      session.trakt.playing = playing;
    }
    const now = Date.now();
    const pausedChanged = session.discord.paused !== Boolean(state.paused);
    if (state.duration > 0 && (pausedChanged || (!state.paused && now - session.discord.at >= DISCORD_EVERY_MS))) {
      session.discord.at = now;
      session.discord.paused = Boolean(state.paused);
      api('/api/discord-rpc/update', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          title: media.name || session.title || 'KINO', media_type: media.type || 'movie',
          season: media.season || null, episode: media.episode || null,
          current_time: state.time || 0, duration: state.duration || 0, poster: media.poster || '',
          year: String(media.year || ''), imdb_id: media.id || '',
          is_paused: Boolean(state.paused),
        }),
      }).catch(() => {});
    }
  }

  // --- Phone remote: app-social.js polls and forwards its commands here ---
  function remoteState() {
    const state = session.last || {};
    const media = session.media || {};
    const s = media.season, e = media.episode;
    return {
      playing: true, paused: Boolean(state.paused), title: session.title || media.name || 'Lecture KINO',
      sub_title: s && e ? `S${String(s).padStart(2, '0')}E${String(e).padStart(2, '0')}` : '',
      media_type: media.type || 'movie', poster: media.poster || '', current_time: state.time || 0, duration: state.duration || 0,
      volume: state.muted ? 0 : Math.min(100, typeof state.volume === 'number' ? state.volume : 100) / 100,
    };
  }

  function nextSubtitleTrack(state) {
    const subs = (state?.tracks || []).filter(track => track.type === 'sub');
    if (!subs.length) return null;
    const current = subs.findIndex(track => track.selected);
    return current === subs.length - 1 ? 'no' : subs[current + 1].id;
  }

  function handleRemoteCommand(cmd) {
    if (!session.open || !cmd) return;
    const number = (...candidates) => {
      for (const candidate of candidates) {
        if (candidate === undefined || candidate === null || candidate === '') continue;
        const value = Number(candidate);
        if (Number.isFinite(value)) return value;
      }
      return NaN;
    };
    switch (cmd.action) {
      case 'play': command('play'); break;
      case 'pause': command('pause'); break;
      case 'toggle_play': command('toggle'); break;
      case 'seek': { const target = number(cmd.value, cmd.time); if (Number.isFinite(target)) command('goto', Math.max(0, target)); break; }
      case 'seek_rel': { const delta = number(cmd.value, cmd.delta); if (Number.isFinite(delta)) command('seek', delta); break; }
      case 'volume': {
        const level = number(cmd.value, cmd.level);
        if (!Number.isFinite(level)) break;
        if (level <= 0) { if (!session.last?.muted) command('mute'); break; }
        if (session.last?.muted) command('mute');
        command('volume', Math.round(Math.min(1, level) * 100));
        break;
      }
      case 'mute': command('mute'); break;
      case 'cycle_subs': { const next = nextSubtitleTrack(session.last); if (next !== null) command('subtitles', next); break; }
      case 'next': case 'next_ep': playIndex(session.index + 1); break;
      case 'prev': case 'prev_ep': playIndex(session.index - 1); break;
      case 'fullscreen': toggleFullscreen(); break;
      default: break;
    }
  }

  // The filled part of a range, drawn by CSS from --fill.
  function paintRange(input) {
    const min = Number(input.min) || 0, max = Number(input.max) || 100;
    const ratio = max > min ? (Number(input.value) - min) / (max - min) : 0;
    input.style.setProperty('--fill', `${Math.round(Math.max(0, Math.min(1, ratio)) * 1000) / 10}%`);
  }

  function showControls() {
    const root = element('nativePlayer');
    if (!root) return;
    root.classList.remove('native-idle');
    clearTimeout(session.hideTimer);
    session.hideTimer = setTimeout(() => {
      if (session.last && !session.last.paused && !session.last.ended && !isMenuOpen()) root.classList.add('native-idle');
    }, 3000);
  }

  function fillTracks(select, tracks, type, allowNone) {
    const wanted = tracks.filter(track => track.type === type);
    const signature = JSON.stringify(wanted.map(track => [track.id, track.selected]));
    if (select.dataset.signature === signature) return;
    select.dataset.signature = signature;
    // Subtitles read « CC : … » like the built-in player's button.
    const prefix = type === 'sub' ? 'CC : ' : '';
    const options = wanted.map(track => `<option value="${track.id}" ${track.selected ? 'selected' : ''}>${escapeHtml(prefix + trackLabel(track))}</option>`);
    if (allowNone) options.unshift(`<option value="no" ${wanted.some(track => track.selected) ? '' : 'selected'}>CC : Off</option>`);
    select.innerHTML = options.join('') || '<option>—</option>';
    select.disabled = wanted.length === 0;
  }

  function render(state) {
    if (!state || !session.open) return;
    session.last = state;
    // A source that cannot be opened (expired link, network) leaves mpv idle:
    // hand over to the built-in player, which can try another source.
    if (!session.loaded) {
      const waited = Date.now() - session.openedAt;
      if (state.duration > 0) {
        session.loaded = true;
        autoFrenchSubtitles(state);
      }
      else if ((state.idle && waited > 1200) || waited > 45000) {
        failed();
        return;
      }
    }
    element('nativeTime').textContent = `${formatTime(state.time)} / ${formatTime(state.duration)}`;
    const seek = element('nativeSeek');
    if (!session.dragging && state.duration > 0) seek.value = String(Math.round(1000 * state.time / state.duration));
    paintRange(seek);
    const play = element('nativePlay');
    const playState = state.paused ? 'paused' : 'playing';
    if (play.dataset.state !== playState) {
      play.dataset.state = playState;
      play.innerHTML = icon(state.paused ? 'play' : 'pause');
      play.setAttribute('aria-label', state.paused ? 'Lecture' : 'Pause');
    }
    const volumeInput = element('nativeVolume');
    if (typeof state.volume === 'number' && document.activeElement !== volumeInput) {
      volumeInput.value = String(Math.round(Math.min(100, state.volume)));
    }
    paintRange(volumeInput);
    element('nativeVolumeText').textContent = `${volumeInput.value}%`;
    const mute = element('nativeMute');
    const level = state.muted || Number(volumeInput.value) === 0 ? 'volMute' : Number(volumeInput.value) < 50 ? 'volLow' : 'volHigh';
    if (mute.dataset.level !== level) {
      mute.dataset.level = level;
      mute.innerHTML = icon(level);
      mute.setAttribute('aria-label', state.muted ? 'Remettre le son' : 'Couper le son');
    }
    const status = element('nativeStatus');
    const flashing = session.flash && Date.now() < session.flashUntil ? session.flash : '';
    status.textContent = state.ended ? 'Terminé' : flashing || (state.buffering ? 'Chargement…' : '');
    status.style.display = status.textContent ? 'block' : 'none';
    const info = [];
    if (state.width && state.height) info.push(`${state.width}×${state.height}`);
    info.push(state.hwdec && state.hwdec !== 'no' ? 'décodage matériel' : 'décodage logiciel');
    element('nativeInfo').textContent = `Lecteur natif · ${info.join(' · ')}`;
    fillTracks(element('nativeAudio'), state.tracks || [], 'audio', false);
    fillTracks(element('nativeSubs'), state.tracks || [], 'sub', true);
    renderSettings(state);
    renderSkipCard(state);
    renderUndoCard();
    renderNextCard(state);
    syncIntegrations(state);
    if (!session.recorded && state.time > 0) {
      session.recorded = true;
      saveProgress(state, true);
    } else {
      saveProgress(state, false);
    }
  }

  function saveProgress(state, force) {
    const media = session.media;
    if (!media?.id || !state || !(state.duration > 0)) return;
    const now = Date.now();
    if (!force && (now - session.lastSave < SAVE_EVERY_MS || state.paused)) return;
    session.lastSave = now;
    const position = state.ended ? state.duration : state.time;
    api('/api/history-record', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ media: {
        id: media.id, name: media.name || session.title, type: media.type || 'movie', year: media.year || '',
        poster: media.poster || '', season: media.season || null, episode: media.episode || null,
        filename: session.title, position: Math.round(position), duration: Math.round(state.duration),
      } }),
    }).catch(() => {});
  }

  async function command(cmd, value, options = {}) {
    if (!session.open) return;
    showControls();
    try {
      const result = await api('/api/native-player/command', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ cmd, value }),
      });
      render(result.state);
      if (!options.silent) shareWithParty(cmd, result.state);
    } catch (error) {}
  }

  // --- Watch Party: the same events as the built-in player ---
  function shareWithParty(cmd, state) {
    if (typeof broadcastWpEvent !== 'function' || !state) return;
    if (['toggle', 'play', 'pause'].includes(cmd)) broadcastWpEvent(state.paused ? 'pause' : 'play', state.time || 0);
    else if (['seek', 'goto'].includes(cmd)) broadcastWpEvent('seek', state.time || 0);
  }

  // An event from a friend: applied without being sent back.
  async function applySync(data) {
    if (!session.open || !data) return;
    const time = Number(data.time);
    if (Number.isFinite(time) && Math.abs((session.last?.time || 0) - time) > 1.5) {
      await command('goto', Math.max(0, time), { silent: true });
    }
    if (data.event === 'play' || (data.type === 'sync' && data.paused === false)) await command('play', undefined, { silent: true });
    else if (data.event === 'pause' || (data.type === 'sync' && data.paused === true)) await command('pause', undefined, { silent: true });
  }

  // The party chat lives in the built-in player; it follows whichever player is on screen.
  function adoptPartyChat(native) {
    const chat = document.getElementById('inAppWpChatBox');
    const home = native ? element('nativePlayer') : document.getElementById('inAppPlayerOverlay');
    if (chat && home && chat.parentNode !== home && typeof home.appendChild === 'function') home.appendChild(chat);
  }

  // Messages from the rest of KINO (built-in player toasts), as plain text.
  function notify(message, duration) {
    let text = String(message || '');
    if (typeof DOMParser === 'function') text = new DOMParser().parseFromString(text, 'text/html').body.textContent || '';
    else text = text.replace(/<[^>]*>/g, ' ');
    flash(text.replace(/\s+/g, ' ').trim(), duration || 3000);
  }

  async function poll() {
    if (!session.open) return;
    try {
      render(await api('/api/native-player/state'));
    } catch (error) {}
  }

  async function toggleFullscreen() {
    try {
      if (window.pywebview?.api?.toggle_fullscreen) await window.pywebview.api.toggle_fullscreen();
    } catch (error) {}
  }

  // --- Opening: offline films and Debrid sources share one path ---
  async function start(source, media, resumeSec, title, extra = {}) {
    buildOverlay();
    if (session.open) finishSession(session.last);  // next or previous episode
    clearInterval(session.poll);
    session.open = true;
    session.item = extra.item || null;
    session.source = source;
    session.media = media || {};
    session.title = title || extra.filename || '';
    session.filename = extra.filename || session.title;
    session.playlist = Array.isArray(extra.playlist) ? extra.playlist.filter(entry => entry && entry.url) : [];
    session.index = Number.isInteger(extra.index) && extra.index >= 0 ? extra.index : 0;
    session.fallback = typeof extra.fallback === 'function' ? extra.fallback : null;
    session.first = extra.first !== false;
    if (session.first) session.subsOff = false;
    session.autoSubs = false;
    session.intro = null;
    session.skipDismissed = false;
    session.undo = null;
    session.loaded = false;
    session.failing = false;
    session.openedAt = Date.now();
    session.nextAt = 0;
    session.nextCancelled = false;
    session.lastSave = Date.now();
    session.recorded = false;
    session.last = null;
    session.onlineSubs = null;
    session.flash = '';
    session.trakt = { started: false, playing: false, stopped: false };
    session.discord = { at: 0, paused: null };
    renderOnlineSubtitles();
    renderPlaylistButtons();
    element('nativeNextCard').style.display = 'none';
    element('nativeSkipCard').style.display = 'none';
    element('nativeUndoCard').style.display = 'none';
    adoptPartyChat(true);
    element('nativeKeep').hidden = !keepableSource();
    element('nativeTv').hidden = !tvSource() || typeof castToTv !== 'function';
    element('nativeWeb').hidden = !session.fallback;
    element('nativeWebHeading').hidden = !session.fallback;
    loadIntro();
    element('nativeTitle').textContent = session.title;
    element('nativeStatus').textContent = 'Chargement…';
    element('nativeStatus').style.display = 'block';
    document.documentElement.classList.add('native-playing');
    element('nativePlayer').classList.add('active');
    let volume = 100;
    try {
      const saved = parseFloat(localStorage.getItem('kino_inapp_volume'));
      if (saved > 0 && saved <= 1) volume = Math.round(saved * 100);
    } catch (e) {}
    try {
      const result = await api('/api/native-player/open', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ ...source, start: resumeSec > 15 ? resumeSec : 0, volume }),
      });
      render(result.state);
    } catch (error) {
      hideOverlay();
      throw error;
    }
    if (session.open) session.poll = setInterval(poll, 500);
    showControls();
  }

  // Offline film (« KINO Offline »).
  async function open(item, media, resumeSec, title, fallback) {
    if (!item?.id) throw new Error('Film hors ligne inconnu');
    return start({ offline_id: item.id }, media, resumeSec, title, { item, filename: item.filename, fallback });
  }

  // Debrid source: {url, title, media, resumeSec, filename, playlist: [{title, url}], fallback(info)}.
  async function openStream(options) {
    const settings = options || {};
    if (!settings.url) throw new Error('Source inconnue');
    const playlist = Array.isArray(settings.playlist) && settings.playlist.length ? settings.playlist : [{ title: settings.title, url: settings.url }];
    const index = Math.max(0, playlist.findIndex(entry => entry && entry.url === settings.url));
    return start({ stream_url: settings.url }, settings.media, settings.resumeSec || 0, settings.title,
      { filename: settings.filename, playlist, index, fallback: settings.fallback });
  }

  // --- Episodes ---
  function episodeOf(title) {
    const match = /S(\d{1,3})E(\d{1,4})/i.exec(String(title || ''));
    return match ? { season: Number(match[1]), episode: Number(match[2]) } : {};
  }

  function hasNext() {
    return session.playlist.length > 1 && session.index < session.playlist.length - 1;
  }

  function renderPlaylistButtons() {
    const several = session.playlist.length > 1;
    element('nativePrev').hidden = !several || session.index <= 0;
    element('nativeNext').hidden = !several || !hasNext();
  }

  // Shown at the end, in the last 22 s of a long episode, or during its ending.
  function nearEnd(state) {
    const remaining = (state.duration || 0) - (state.time || 0);
    const ending = session.intro?.ed;
    return Boolean(state.ended || (state.duration > 200 && remaining > 0.5 && remaining <= 22)
      || (ending && state.time >= ending.start && state.time <= ending.end + 2));
  }

  function renderNextCard(state) {
    const card = element('nativeNextCard');
    const show = Boolean(hasNext() && !session.nextCancelled && nearEnd(state));
    card.style.display = show ? 'flex' : 'none';
    if (!show) {
      session.nextAt = 0;
      return;
    }
    if (!session.nextAt) session.nextAt = Date.now() + NEXT_DELAY_MS;
    const left = Math.max(0, Math.ceil((session.nextAt - Date.now()) / 1000));
    element('nativeNextCount').textContent = String(left);
    element('nativeNextTitle').textContent = session.playlist[session.index + 1].title || 'Épisode suivant';
    if (left <= 0) playIndex(session.index + 1);
  }

  async function playIndex(index) {
    const entry = session.playlist[index];
    if (!session.open || !entry || session.switching) return;
    session.switching = true;
    const playlist = session.playlist;
    const fallback = session.fallback;
    const media = { ...session.media, ...episodeOf(entry.title) };
    try {
      await start({ stream_url: entry.url }, media, 0, entry.title, { playlist, index, fallback, first: false });
    } catch (error) {
      if (fallback) fallback({ url: entry.url, title: entry.title, playlist, index, media, first: false });
    } finally {
      session.switching = false;
    }
  }

  // The source never played: close quietly and let the built-in player take it.
  async function failed() {
    if (session.failing) return;
    session.failing = true;
    const info = { url: session.source?.stream_url || '', title: session.title, playlist: session.playlist, index: session.index,
      media: session.media, first: session.first, position: 0 };
    const fallback = session.fallback;
    hideOverlay();
    try {
      await api('/api/native-player/close', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    } catch (error) {}
    if (fallback) fallback(info);
  }

  // --- « Passer l'intro », as in the built-in player ---
  function isSeries() {
    const type = session.media?.type;
    return type === 'series' || type === 'anime' || session.playlist.length > 1;
  }

  async function loadIntro() {
    if (!isSeries()) return;
    const media = session.media || {};
    const source = session.source?.stream_url || '';
    const params = new URLSearchParams({
      title: media.name || session.title || '', type: media.type || 'series',
      season: String(media.season || 1), episode: String(media.episode || 1),
      url: /^https:\/\//.test(source) ? source : '', imdb_id: media.id || '',
    });
    const opened = session.openedAt;
    try {
      const intro = await api(`/api/intro-times?${params}`);
      if (session.openedAt === opened) session.intro = intro || null;
    } catch (error) {}
  }

  // What « Passer l'intro » would do now: {label, goto} or {label, seek}, or null.
  function introSkip(state) {
    if (!isSeries() || session.skipDismissed || !(state?.duration > 180)) return null;
    const intro = session.intro || {};
    const time = state.time || 0;
    if (intro.op) {
      return time >= Math.max(0, intro.op.start - 2) && time < intro.op.end
        ? { label: `Passer l'intro (→ ${formatTime(intro.op.end)})`, goto: intro.op.end + 0.5 } : null;
    }
    if (intro.recap && time >= Math.max(0, intro.recap.start - 2) && time < intro.recap.end) {
      return { label: `Passer le récap (→ ${formatTime(intro.recap.end)})`, goto: intro.recap.end + 0.5 };
    }
    if (time >= 10 && time <= 160) {
      const seconds = intro.default_skip || (session.media?.type === 'anime' ? 90 : 60);
      return { label: `Passer l'intro (+${seconds} s)`, seek: seconds };
    }
    return null;
  }

  function renderSkipCard(state) {
    const skip = introSkip(state);
    element('nativeSkipCard').style.display = skip ? 'flex' : 'none';
    if (skip) element('nativeSkipGo').textContent = skip.label;
  }

  function skipIntro() {
    const skip = introSkip(session.last);
    session.skipDismissed = true;
    element('nativeSkipCard').style.display = 'none';
    if (!skip) return;
    const from = Math.max(0, Math.floor(session.last?.time || 0));
    session.undo = { time: from, until: Date.now() + UNDO_SKIP_MS };
    element('nativeUndoGo').textContent = `↩ Revenir à ${formatTime(from)}`;
    if (skip.goto !== undefined) command('goto', skip.goto);
    else command('seek', skip.seek);
    flash('Intro passée', 1600);
  }

  // « Annuler le saut », for 7 s after « Passer l'intro ».
  function renderUndoCard() {
    const show = Boolean(session.undo && Date.now() < session.undo.until);
    if (!show) session.undo = null;
    element('nativeUndoCard').style.display = show ? 'flex' : 'none';
  }

  function undoSkip() {
    const undo = session.undo;
    session.undo = null;
    element('nativeUndoCard').style.display = 'none';
    if (!undo) return;
    command('goto', undo.time);
    flash(`Retour à ${formatTime(undo.time)}`, 1400);
  }

  // --- « Garder hors ligne » for a Debrid source ---
  function keepableSource() {
    return /^https:\/\//.test(session.source?.stream_url || '') && /\.(mkv|mp4|avi|m4v|mov|ts|wmv|webm)$/i.test(session.filename || '');
  }

  // The online subtitle shown now, kept with the film like in the built-in player.
  async function chosenOnlineSubtitle() {
    const selected = (session.last?.tracks || []).find(track => track.type === 'sub' && track.selected && track.external);
    const choice = Array.isArray(session.onlineSubs) && selected ? session.onlineSubs.find(item => item.added && item.title === selected.title) : null;
    if (!choice) return null;
    const data = await api(`/api/subtitle-cues?url=${encodeURIComponent(choice.url)}`);
    if (!data?.cues?.length) return null;
    return { label: `CC : ${choice.label}`, lang: choice.lang, kind: 'opensubtitles', variant: choice.variant, cues: data.cues };
  }

  async function keepOffline() {
    if (!keepableSource()) {
      flash('Ce fichier ne peut pas être gardé hors ligne d’ici.', 3000);
      return;
    }
    const media = typeof downloadMediaContext === 'function' ? downloadMediaContext(session.media) : (session.media || {});
    try {
      let subtitles = null;
      try { subtitles = await chosenOnlineSubtitle(); } catch (error) {}
      const result = await api('/api/download', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ url: session.source.stream_url, filename: session.filename, media, subtitles }),
      });
      flash(result?.duplicate ? 'Déjà dans la bibliothèque hors ligne.' : 'Ajouté à la bibliothèque hors ligne.', 3000);
      if (typeof pollDownloads === 'function') pollDownloads();
    } catch (error) {
      flash(error.message || 'Téléchargement impossible.', 4000);
    }
  }

  // --- « Sur la TV »: the TV goes on from this moment, the Mac stops ---
  function tvSource() {
    return session.source?.stream_url || session.item?.url || '';
  }

  async function sendToTv() {
    if (!session.open || typeof castToTv !== 'function') return;
    const position = Math.max(0, Math.floor(session.last?.time || 0));
    const media = session.media?.id && typeof tvMediaPayload === 'function'
      ? tvMediaPayload(session.media, session.media.season, session.media.episode) : null;
    command('pause');
    flash('Envoi à la TV…', 6000);
    try {
      await castToTv({ stream_url: tvSource(), title: typeof tvTitle === 'function' ? tvTitle(media, session.title) : session.title,
        filename: session.filename, media, resume_sec: position });
      await close();
    } catch (error) {
      flash(error.message || 'La TV ne répond pas.', 4000);
    }
  }

  // Watch Party and picture in picture live in the built-in player: switch there at the same moment.
  async function handOver() {
    const fallback = session.fallback;
    if (!fallback || !session.open) return;
    const last = session.last;
    const info = { url: session.source?.stream_url || '', title: session.title, playlist: session.playlist, index: session.index,
      media: session.media, first: session.first, position: Math.max(0, Math.floor(last?.time || 0)) };
    finishSession(last);
    hideOverlay();
    try {
      await api('/api/native-player/close', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    } catch (error) {}
    fallback(info);
  }

  // Progress and Trakt for the title that is ending (closing or changing episode).
  function finishSession(last) {
    if (!last || !session.loaded) return;
    saveProgress(last, true);
    if (session.trakt.started && !session.trakt.stopped && typeof scrobbleTrakt === 'function' && session.media?.id) {
      scrobbleTrakt('pause', { ...session.media, name: session.media.name || session.title }, percent(last));
    }
  }

  function hideOverlay() {
    closeMenu(false);
    session.open = false;
    adoptPartyChat(false);
    for (const id of ['nativeNextCard', 'nativeSkipCard', 'nativeUndoCard']) {
      const card = element(id);
      if (card) card.style.display = 'none';
    }
    clearInterval(session.poll);
    clearTimeout(session.hideTimer);
    element('nativePlayer')?.classList.remove('active', 'native-idle');
    document.documentElement.classList.remove('native-playing');
  }

  async function close() {
    if (!session.open) return;
    const finishedId = session.media?.id || '';
    finishSession(session.last);
    api('/api/discord-rpc/clear', { method: 'POST' }).catch(() => {});
    hideOverlay();
    try {
      await api('/api/native-player/close', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    } catch (error) {}
    if (typeof refreshUserLists === 'function') refreshUserLists();
    if (typeof askRatingAfterPlayback === 'function') askRatingAfterPlayback(finishedId);
  }

  window.addEventListener('keydown', event => {
    if (!session.open) return;
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target?.tagName) && event.key !== 'Escape') return;
    if (menuKey(event)) {
      event.stopImmediatePropagation();
      return;
    }
    const action = keyAction(event);
    if (!action) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const [name, value] = action;
    if (name === 'escape') {
      // The menu, then a dialog over the player (remote QR code), then the player.
      if (closeMenu(true)) return;
      if (typeof closeOpenDialogOnEscape === 'function' && closeOpenDialogOnEscape()) return;
      if (document.body.classList.contains('is-fullscreen')) toggleFullscreen();
      else close();
    } else if (name === 'fullscreen') {
      toggleFullscreen();
    } else if (name === 'volumeStep') {
      const current = typeof session.last?.volume === 'number' ? session.last.volume : 100;
      command('volume', Math.max(0, Math.min(100, Math.round(current + value))));
    } else {
      command(name, value);
    }
  }, true);

  window.NativePlayer = { available: false, enabled: false, open, openStream, close, isOpen: () => session.open,
    formatTime, trackLabel, keyAction, remoteState, handleRemoteCommand, episodeOf, introSkip, nearEnd, applySync, notify,
    nextSetting, speedLabel, onlineSubtitleChoices, nextSubtitleTrack };
})();
