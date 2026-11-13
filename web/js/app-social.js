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

// JSON for an inline handler inside a double-quoted HTML attribute.
function attrJson(value) {
  return escapeHtml(JSON.stringify(value === undefined ? null : value));
}

function listMediaArg(m) {
  return attrJson({
    id: m.id, name: m.name || '', type: 'movie', poster: m.poster || '',
    year: String(m.year || ''), imdbRating: String(m.imdbRating || ''),
  });
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
    if (!/^tt\d+$/.test(String(media?.id || ''))) return false;
    const name = media.name || media.title || '';
    if (!name) return false;
    const s = media.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : null);
    const e = media.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : null);
    const result = await api('/api/trakt/scrobble', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        action,
        media: {imdb_id: media.id, title: name, year: media.year || '',
          type: media.type || 'movie', season: s, episode: e},
        progress: progressPct || 0
      })
    });
    return result.status === 'ok';
  } catch(err) { return false; }
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
        prompt('Adresse de la télécommande KINO (copiez avec Ctrl+C) :', url);
      });
    } else {
      prompt('Adresse de la télécommande KINO (copiez avec Ctrl+C) :', url);
    }
  }
}

function startRemotePolling() {
  if (remotePollingInterval) return;
  remotePollingInterval = setInterval(async () => {
    // The native player reports its own state and takes the same commands.
    if (window.NativePlayer && typeof window.NativePlayer.isOpen === 'function' && window.NativePlayer.isOpen()) {
      try {
        await fetch('/api/player/state', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(window.NativePlayer.remoteState())
        });
        const res = await fetch('/api/player/commands');
        const data = await res.json();
        for (const cmd of (data && data.commands) || []) window.NativePlayer.handleRemoteCommand(cmd);
      } catch(e) {}
      return;
    }
    const v = document.getElementById('inAppVideo');
    const overlay = document.getElementById('inAppPlayerOverlay');
    const isPlayerActive = Boolean(overlay && overlay.classList.contains('active') && v && v.src);

    if (isPlayerActive) {
      try {
        // Absolute position: a compatible (remuxed) stream restarts its clock at each seek.
        const curT = inAppAbsoluteTime(v);
        const durT = inAppDuration(v) || 0;
        const s = (inAppCurrentMedia && inAppCurrentMedia.season) ? inAppCurrentMedia.season : null;
        const e = (inAppCurrentMedia && inAppCurrentMedia.episode) ? inAppCurrentMedia.episode : null;
        await fetch('/api/player/state', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          // Field names match remote_controller._player_state. `playing` marks an open
          // Playback Session: KINO defers an update install while it is set.
          body: JSON.stringify({
            playing: true,
            paused: v.paused,
            title: inAppCurrentTitle || (inAppCurrentMedia ? inAppCurrentMedia.name : 'Lecture KINO'),
            sub_title: s && e ? `S${String(s).padStart(2, '0')}E${String(e).padStart(2, '0')}` : '',
            media_type: (inAppCurrentMedia && inAppCurrentMedia.type) || 'movie',
            poster: (inAppCurrentMedia && inAppCurrentMedia.poster) || '',
            current_time: curT,
            duration: durT,
            volume: v.muted ? 0 : v.volume
          })
        });
      } catch(e) {}
    }
    // Without playback there is nothing to control; the server drops stale commands.
    if (!isPlayerActive) return;

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

// Commands from the phone remote: remote_controller.py queues {action, value}.
function handleRemoteCommand(cmd) {
  const v = document.getElementById('inAppVideo');
  if (!v || !cmd) return;
  const pick = (...candidates) => {
    for (const candidate of candidates) {
      if (candidate === undefined || candidate === null || candidate === '') continue;
      const number = Number(candidate);
      if (Number.isFinite(number)) return number;
    }
    return NaN;
  };
  switch (cmd.action) {
    case 'play': v.play().catch(() => {}); break;
    case 'pause': v.pause(); break;
    case 'toggle_play': toggleInAppPlay(); break;
    case 'seek': {
      const target = pick(cmd.value, cmd.time);
      if (Number.isFinite(target)) inAppSeekTo(target);
      break;
    }
    case 'seek_rel': {
      const delta = pick(cmd.value, cmd.delta);
      if (Number.isFinite(delta)) inAppSeekRel(delta);
      break;
    }
    case 'volume': {
      const level = pick(cmd.value, cmd.level);
      if (!Number.isFinite(level)) break;
      if (level <= 0) {
        if (!v.muted) toggleInAppMute();
        showInAppToast('Son coupé');
      } else {
        setInAppVolume(level, true);
        showInAppToast(`Volume : ${Math.round(v.volume * 100)}%`);
      }
      break;
    }
    case 'mute':
      toggleInAppMute();
      showInAppToast(v.muted ? 'Son coupé' : `Volume : ${Math.round(v.volume * 100)}%`);
      break;
    case 'next': case 'next_ep': inAppNextTrack(); break;
    case 'prev': case 'prev_ep': inAppPrevTrack(); break;
    case 'cycle_subs': cycleInAppSubtitles(); break;
    case 'fullscreen': toggleInAppFullscreen(); break;
    default: break;
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

// PeerJS ships with KINO (web/js/vendor), but a Watch Party still meets through
// the internet. PeerJS reports in English: the failures a user can meet get
// their own words.
const WP_NEEDS_INTERNET = 'La Watch Party a besoin d’internet pour relier les participants. Vérifiez votre connexion, puis réessayez.';
const WP_NETWORK_ERRORS = ['network', 'server-error', 'socket-error', 'socket-closed', 'disconnected'];

function watchPartyErrorMessage(err, online = true) {
  const type = err?.type || '';
  if (!online || WP_NETWORK_ERRORS.includes(type)) return WP_NEEDS_INTERNET;
  if (type === 'peer-unavailable') return 'Aucune Watch Party ne répond à ce code. Vérifiez-le avec la personne qui l’a créée.';
  return `La Watch Party a rencontré un problème (${err?.message || type || 'erreur inconnue'}).`;
}

function initWpPeer(hostPeerId, joinHostPeerId) {
  if (typeof Peer === 'undefined') {
    alert('La Watch Party ne peut pas démarrer : un fichier de KINO manque. Réinstallez la dernière mise à jour (Paramètres → Mises à jour).');
    return;
  }
  if (!networkOnline) {
    alert(WP_NEEDS_INTERNET);
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
      conn.on('open', () => {
        const snapshot = wpPlayerSnapshot();
        if (snapshot) conn.send({ type: 'sync', ...snapshot });
      });
    });

    wpPeer.on('error', (err) => {
      console.warn('Watch Party error:', err);
      if (err.type === 'unavailable-id') {
        createWatchPartyRoom();
      } else {
        alert(watchPartyErrorMessage(err, networkOnline));
      }
    });
  } catch(e) {
    alert(watchPartyErrorMessage(e, networkOnline));
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

// What a friend joining the party receives: the playing player's position.
function wpPlayerSnapshot() {
  const native = window.NativePlayer?.isOpen?.() ? window.NativePlayer.remoteState?.() : null;
  if (native) return { time: native.current_time || 0, paused: Boolean(native.paused), title: native.title || '' };
  const v = document.getElementById('inAppVideo');
  return v ? { time: v.currentTime, paused: v.paused, title: inAppCurrentTitle || '' } : null;
}

function handleWpIncomingData(data, fromConn) {
  if (!data) return;
  if (data.type === 'chat') {
    appendWpChatMessage(String(data.user || 'Ami').slice(0, 40), String(data.text || '').slice(0, 500));
    return;
  }
  if ((data.type === 'sync' || data.type === 'event') && window.NativePlayer?.isOpen?.()) {
    window.NativePlayer.applySync(data);
    if (data.event === 'play' || (data.type === 'sync' && data.paused === false)) showInAppToast('Reprise synchronisée');
    else if (data.event === 'pause' || (data.type === 'sync' && data.paused === true)) showInAppToast('Pause synchronisée');
    return;
  }
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
    // Names and messages come from other people: always escaped.
    div.innerHTML = `<b style="color:${user === 'Moi' ? '#fafafa' : '#a3a3a3'}; font-size:0.75rem;">${escapeHtml(user)} :</b> <span style="color:#f4f4f5;">${escapeHtml(text)}</span>`;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  }
  showInAppToast(`<strong>${escapeHtml(user)} :</strong> ${escapeHtml(text.length > 25 ? text.substring(0, 25).trimEnd() + '…' : text)}`);
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
    container.innerHTML = `<div style="color:#ef4444; font-size:0.8rem; text-align:center; padding:20px;">Erreur : ${escapeHtml(e.message)}</div>`;
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

// --- Explorer: imported Letterboxd lists and curated selections, as shelves ---
let currentCommunityLists = [];
let openCommunityListId = null;
let communityStateFilter = 'all';

async function loadCommunityTab() {
  const grid = document.getElementById('communityListsGrid');
  if (!grid) return;
  openCommunityListId = null;
  communityStateFilter = 'all';
  if (!currentCommunityLists.length) grid.innerHTML = '<p class="list-shelf-empty">Chargement des sélections…</p>';
  renderCommunityPage();
  try {
    const data = await api('/api/community/curated');
    let curated = data.collections || [];

    // The Letterboxd lists imported by the user come first.
    try {
      const customData = await api('/api/letterboxd/custom-lists');
      const customLists = (customData.custom_lists || []).map(l => ({
        id: l.id,
        title: l.title || 'Ma liste Letterboxd',
        badge: 'IMPORTÉ',
        author: 'Vous',
        imported: true,
        description: l.description || '',
        count: l.items ? l.items.length : 0,
        items: l.items || [],
      }));
      curated = [...customLists, ...curated];
    } catch(err) {}

    currentCommunityLists = curated;
    renderCommunityListsGrid(currentCommunityLists);
  } catch(e) {
    grid.innerHTML = `<p class="list-shelf-empty">Erreur : ${escapeHtml(e.message)}</p>`;
  }
}

function communityShelfHtml(col) {
  const items = col.items || [];
  const progress = customListProgress(items);
  const label = escapeHtml(col.title || 'Sélection');
  const id = attrJson(col.id);
  const open = col.imported ? `openImportedList(${id})` : `openCommunityList(${id})`;
  const meta = [`${progress.total} titre${progress.total > 1 ? 's' : ''}`, `${progress.watched} vu${progress.watched > 1 ? 's' : ''}`,
    col.imported ? '' : (col.author ? `par ${col.author}` : '')].filter(Boolean).join(' · ');
  return `
    <section class="list-shelf" aria-label="${label}">
      <div class="list-shelf-head">
        <div class="list-shelf-info">
          <h3 class="list-shelf-title">${label}</h3>
          <div class="list-shelf-meta">${escapeHtml(meta)}</div>
          <div class="list-shelf-progress" role="progressbar" aria-label="Titres vus" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.pct}"><span style="width:${progress.pct}%"></span></div>
          ${col.description ? `<p class="list-shelf-desc">${escapeHtml(col.description)}</p>` : ''}
        </div>
        <div class="list-shelf-actions">
          <button type="button" class="btn" onclick="${open}">Tout voir</button>
          <button type="button" class="btn-surprise" onclick="surpriseMeFromCommunityList(${id})" title="Tirer un titre non vu de cette sélection">Roulette</button>
        </div>
      </div>
      ${items.length ? `<div class="list-shelf-row">${items.slice(0, LIST_SHELF_SIZE).map(m => posterCardHtml(m, 'movie')).join('')}${items.length > LIST_SHELF_SIZE ? `<button type="button" class="list-shelf-more" onclick="${open}">Voir les ${items.length} titres</button>` : ''}</div>` : '<p class="list-shelf-empty">Cette sélection est vide.</p>'}
    </section>`;
}

function renderCommunityListsGrid(lists) {
  const grid = document.getElementById('communityListsGrid');
  if (!grid) return;
  if (!lists || lists.length === 0) {
    grid.innerHTML = '<p class="list-shelf-empty">Aucune sélection disponible.</p>';
    return;
  }
  const imported = lists.filter(col => col.imported);
  const curated = lists.filter(col => !col.imported);
  grid.innerHTML = [
    imported.length ? `<h2 class="explore-section-title">Vos listes Letterboxd</h2>${imported.map(communityShelfHtml).join('')}` : '',
    curated.length ? `<h2 class="explore-section-title">Sélections</h2>${curated.map(communityShelfHtml).join('')}` : '',
  ].join('');
}

// Shelves, or one selection in full with a state filter.
function renderCommunityPage() {
  const grid = document.getElementById('communityListsGrid');
  const section = document.getElementById('communityActiveListSection');
  const col = openCommunityListId ? currentCommunityLists.find(c => c.id === openCommunityListId) : null;
  if (!col) openCommunityListId = null;
  if (grid) grid.hidden = Boolean(col);
  if (!section) return;
  section.hidden = !col;
  if (!col) return;
  const items = col.items || [];
  const progress = customListProgress(items);
  const counts = { all: items.length, todo: 0, watched: 0 };
  items.forEach(m => { counts[listItemState(m) === 'watched' ? 'watched' : 'todo']++; });
  const shown = items.filter(m => communityStateFilter === 'all' || (listItemState(m) === 'watched') === (communityStateFilter === 'watched'));
  document.getElementById('communityActiveListTitle').textContent = col.title || 'Sélection';
  document.getElementById('communityActiveListMeta').textContent = [`${progress.total} titre${progress.total > 1 ? 's' : ''}`,
    `${progress.watched} vu${progress.watched > 1 ? 's' : ''}`, col.author ? `par ${col.author}` : ''].filter(Boolean).join(' · ');
  const desc = document.getElementById('communityActiveListDesc');
  desc.textContent = col.description || '';
  desc.hidden = !col.description;
  const filters = document.getElementById('communityActiveListFilters');
  if (filters) {
    filters.innerHTML = [['all', 'Tout'], ['todo', 'À voir'], ['watched', 'Vus']].map(([state, label]) =>
      `<button type="button" class="chip${communityStateFilter === state ? ' active' : ''}" aria-pressed="${communityStateFilter === state}" onclick="setCommunityStateFilter('${state}')">${label} <span class="list-count">${counts[state]}</span></button>`).join('');
  }
  document.getElementById('communityActiveListGrid').innerHTML = shown.length
    ? shown.map(m => posterCardHtml(m, 'movie')).join('')
    : `<p class="list-shelf-empty">${items.length ? 'Aucun titre dans ce filtre.' : 'Cette sélection est vide.'}</p>`;
}

function openCommunityList(listId) {
  if (!currentCommunityLists.some(c => c.id === listId)) return;
  openCommunityListId = listId;
  communityStateFilter = 'all';
  renderCommunityPage();
  window.scrollTo?.({ top: 0, behavior: 'auto' });
}

function closeCommunityActiveList() {
  openCommunityListId = null;
  renderCommunityPage();
}

function setCommunityStateFilter(state) {
  communityStateFilter = state;
  renderCommunityPage();
}

// An imported list opens on its own page, with Ma Liste's tools.
async function openImportedList(listId) {
  await switchTab('customlists');
  openCustomList(listId);
}

function surpriseMeFromCommunityList(listId) {
  const col = currentCommunityLists.find(c => c.id === listId);
  if (!col || !(col.items || []).length) {
    alert('Cette sélection ne contient aucun titre.');
    return;
  }
  const unviewed = col.items.filter(m => listItemState(m) !== 'watched');
  surpriseMeMedia({ items: unviewed.length ? unviewed : col.items, title: col.title });
}

function surpriseMeFromOpenCommunityList() {
  if (openCommunityListId) surpriseMeFromCommunityList(openCommunityListId);
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
  const listInp = document.getElementById('lbxCustomListUrlInput');
  if (listInp) listInp.value = val;
  inp.value = '';
  submitLetterboxdCustomListImport();
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
  if (searchResultsShown) {
    // Search results and the actor page stay on screen; the actor page redraws its cards.
    if (personView) renderPersonPage();
  } else if (activeTab === 'watched' || activeTab === 'watchlist') {
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
  refreshSpoilerSurfaces();
  const histItem = userHistory.find(h => h.id === currentMedia.id);
  const mainPlayBtn = document.getElementById('detailMainPlayBtn');
  if (mainPlayBtn && histItem) {
    const targetEp = getSeriesTargetEpisode(histItem);
    const epCode = `S${String(targetEp.season).padStart(2,'0')}E${String(targetEp.episode).padStart(2,'0')}`;
    mainPlayBtn.textContent = targetEp.finished ? `Revoir (${epCode})` : (targetEp.isNext ? `Épisode suivant (${epCode})` : `Lancer ${epCode}`);
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
  refreshSpoilerSurfaces();
  const histItem = userHistory.find(h => h.id === currentMedia.id);
  const mainPlayBtn = document.getElementById('detailMainPlayBtn');
  if (mainPlayBtn && histItem) {
    const targetEp = getSeriesTargetEpisode(histItem);
    const epCode = `S${String(targetEp.season).padStart(2,'0')}E${String(targetEp.episode).padStart(2,'0')}`;
    mainPlayBtn.textContent = targetEp.finished ? `Revoir (${epCode})` : (targetEp.isNext ? `Épisode suivant (${epCode})` : `Lancer ${epCode}`);
  }
}

