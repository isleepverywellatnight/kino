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
setInterval(() => { if (!document.hidden) syncWindowState(); }, 1500);
document.addEventListener('visibilitychange', () => { if (!document.hidden) syncWindowState(); });

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
let inAppCurrentFilename = '';
let inAppVideoFps = null;
let inAppVideoCodec = null; // codec of the first video track, from the probe
let inAppDownscaleVideo = false; // set by the freeze guard: 1080p for the rest of this playback
let inAppStallTimer = null;
let inAppStallState = null;
let inAppStallGeneration = -1;
let inAppStreamOffset = 0;
let inAppTotalDuration = 0;
let inAppNeedsVideoConversion = false;
let inAppNeedsAudioConversion = true;
let inAppSelectedAudioIndex = null;
let inAppAudioTracks = [];
let inAppGlobalAudioTrack = null;
let inAppSeriesId = '';
let inAppSeriesPreference = null;
let inAppSessionScope = '';
let inAppSessionAudioChoice = null;
let inAppSessionSubtitleChoice = null;
let inAppPlaybackGeneration = 0;
let inAppRecovery = null;
let inAppRemuxStream = null;
let inAppIsRemux = false;
let inAppStreamErrorHandler = null;
let inAppIdleTimeout = null;
let inAppFallbackTimer = null;
let inAppToastTimer = null;
let inAppIsDraggingVol = false;
let inAppIntroData = null;
let inAppUndoTimer = null;
let inAppUndoTargetSec = 0;

function inAppAbsoluteTime(video) {
  return inAppStreamOffset + (video?.currentTime || 0);
}

function inAppDuration(video) {
  if (inAppTotalDuration > 0) return inAppTotalDuration;
  const duration = video?.duration;
  if (Number.isFinite(duration) && duration > 0) {
    inAppTotalDuration = inAppStreamOffset + duration;
  }
  return inAppTotalDuration;
}

// A converted stream (/api/remux) only reports what it has received so far:
// keep the duration found by the analysis, or the seek bar ends after a second.
function inAppMetadataDuration(elementDuration) {
  if (inAppIsRemux && inAppTotalDuration > 0) return inAppTotalDuration;
  if (Number.isFinite(elementDuration) && elementDuration > 0) return inAppStreamOffset + elementDuration;
  return inAppTotalDuration;
}

function inAppSeriesScope(media, title, recovery) {
  const isSeries = media && (media.type === 'series' || media.type === 'anime' || (media.is_anime && media.type !== 'movie'));
  const id = isSeries ? String(media.id || '').trim().toLowerCase() : '';
  if (!/^[a-z0-9][a-z0-9:_-]{1,95}$/.test(id)) return { id: '', scope: '' };
  const episodeMatch = String(title || '').match(/\bS(\d{1,3})[ ._-]*E(\d{1,3})\b/i);
  const season = Number(recovery?.params?.season || episodeMatch?.[1] || media.season || document.getElementById('seasonSelect')?.value || 1);
  const episode = Number(recovery?.params?.episode || episodeMatch?.[2] || media.episode || document.getElementById('episodeSelect')?.value || 1);
  return { id, scope: `${id}:s${season}:e${episode}` };
}

function inAppSeriesPreferenceStatus(message) {
  const status = document.getElementById('inAppSeriesPreferenceStatus');
  if (status) status.textContent = message;
}

function updateInAppSeriesPreferenceUI() {
  const button = document.getElementById('inAppSeriesPreferenceBtn');
  if (button) button.style.display = inAppSeriesId ? 'inline-flex' : 'none';
  const reset = document.getElementById('inAppSeriesPreferenceReset');
  if (reset) reset.disabled = !inAppSeriesPreference;
  const audio = inAppAudioTracks.find(track => track.index === inAppSelectedAudioIndex);
  const subtitle = inAppAvailableSubs[inAppActiveSubIdx];
  const summary = document.getElementById('inAppSeriesPreferenceSummary');
  if (summary) summary.textContent = `Audio : ${audio ? (audio.language || 'Piste sans langue').toUpperCase() : 'indisponible'} · Sous-titres : ${subtitle?.lang === 'off' ? 'désactivés' : (subtitle?.label || 'indisponibles')}`;
}

function openInAppSeriesPreference() {
  if (!inAppSeriesId) return;
  updateInAppSeriesPreferenceUI();
  inAppSeriesPreferenceStatus(inAppSeriesPreference ? 'Réglages mémorisés pour cette série.' : 'Cette série utilise les réglages généraux.');
  const dialog = document.getElementById('inAppSeriesPreferenceDialog');
  if (dialog && !dialog.open) dialog.showModal();
}

function closeInAppSeriesPreference() {
  document.getElementById('inAppSeriesPreferenceDialog')?.close();
  resetInAppIdleTimer();
}

async function saveInAppSeriesPreference() {
  if (!inAppSeriesId) return;
  const seriesId = inAppSeriesId;
  const generation = inAppPlaybackGeneration;
  const track = inAppAudioTracks.find(item => item.index === inAppSelectedAudioIndex);
  const audio = SeriesPlayback.describeAudio(track);
  const subtitle = inAppAvailableSubs[inAppActiveSubIdx];
  const preference = {
    audio: audio && (audio.language !== 'und' || audio.original) ? audio : null,
    subtitles: subtitle?.lang && subtitle.lang !== 'off'
      ? { enabled: true, kind: subtitle.kind, language: subtitle.lang, variant: subtitle.variant || 'standard' }
      : { enabled: false, kind: '', language: '', variant: '' },
  };
  inAppSeriesPreferenceStatus('Enregistrement…');
  try {
    const result = await api('/api/series-playback-preference', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ series_id: seriesId, action: 'save', preference }),
    });
    if (seriesId !== inAppSeriesId || generation !== inAppPlaybackGeneration) return;
    inAppSeriesPreference = result.preference;
    updateInAppSeriesPreferenceUI();
    inAppSeriesPreferenceStatus(audio && !preference.audio
      ? 'Sous-titres mémorisés. Cette piste audio sans langue ne peut pas être reconnue sur un autre épisode.'
      : 'Choix mémorisés pour les prochains épisodes de cette série.');
  } catch (_) {
    inAppSeriesPreferenceStatus('Impossible d’enregistrer les choix pour cette série.');
  }
}

async function resetInAppSeriesPreference() {
  if (!inAppSeriesId) return;
  const seriesId = inAppSeriesId;
  const generation = inAppPlaybackGeneration;
  inAppSeriesPreferenceStatus('Réinitialisation…');
  try {
    await api('/api/series-playback-preference', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ series_id: seriesId, action: 'clear' }),
    });
    if (seriesId !== inAppSeriesId || generation !== inAppPlaybackGeneration) return;
    inAppSeriesPreference = null;
    inAppSessionAudioChoice = null;
    inAppSessionSubtitleChoice = null;
    if (inAppGlobalAudioTrack && inAppSelectedAudioIndex !== inAppGlobalAudioTrack.index) {
      changeInAppAudioTrack(inAppGlobalAudioTrack.index, false);
    }
    const globalSubs = SeriesPlayback.globalSubtitles(window.kinoPrefLang || 'vf');
    const chosen = SeriesPlayback.chooseSubtitles(inAppAvailableSubs, globalSubs);
    await activateInAppSubtitle(chosen.index, false);
    updateInAppSeriesPreferenceUI();
    inAppSeriesPreferenceStatus('Réglages généraux rétablis pour cette série.');
  } catch (_) {
    inAppSeriesPreferenceStatus('Impossible de rétablir les réglages généraux.');
  }
}

// Freeze guard. A file the KINO window cannot decode freezes the picture while
// data keeps arriving, and WebKit's memory then grows by gigabytes in seconds
// (2026-11-09: 466 MB to 3.7 GB with an AI-upscaled 3600x2160 HEVC film; the
// 8 GB Mac froze). Ordinary 4K plays fine (4K60 HEVC test: 289 MB). A frozen
// picture with data buffered ahead is the signature: switch to 1080p, and stop
// if even that freezes.
const INAPP_STALL_TICKS = 3; // checked every 700 ms: about 2 s frozen
function inAppStallStep(state, sample) {
  const next = { lastTime: sample.time, ticks: 0, downscaled: state.downscaled };
  if (sample.paused || sample.seeking || sample.hidden || sample.readyState < 3 || sample.ahead < 3
      || Math.abs(sample.time - state.lastTime) > 0.05) {
    return { state: next, action: null };
  }
  next.ticks = state.ticks + 1;
  if (next.ticks < INAPP_STALL_TICKS) return { state: next, action: null };
  next.ticks = 0;
  if (state.downscaled) return { state: next, action: 'stop' };
  next.downscaled = true;
  return { state: next, action: 'downscale' };
}

function inAppBufferedAhead(video) {
  const t = video.currentTime;
  for (let i = 0; i < video.buffered.length; i++) {
    if (video.buffered.start(i) <= t + 0.1 && t <= video.buffered.end(i)) return video.buffered.end(i) - t;
  }
  return 0;
}

function startInAppStallGuard(video, generation) {
  clearInterval(inAppStallTimer);
  inAppStallState = { lastTime: -1, ticks: 0, downscaled: inAppNeedsVideoConversion || inAppDownscaleVideo };
  inAppStallTimer = setInterval(() => {
    if (generation !== inAppPlaybackGeneration) { clearInterval(inAppStallTimer); return; }
    const { state, action } = inAppStallStep(inAppStallState, {
      time: video.currentTime, paused: video.paused, seeking: video.seeking, hidden: document.hidden,
      readyState: video.readyState, ahead: inAppBufferedAhead(video),
    });
    inAppStallState = state;
    if (action === 'downscale') downscaleFrozenInAppVideo(video);
    else if (action === 'stop') stopFrozenInAppVideo(video);
  }, 700);
}

function downscaleFrozenInAppVideo(video) {
  const position = Math.floor(inAppAbsoluteTime(video));
  inAppDownscaleVideo = true;
  inAppStreamOffset = position;
  setInAppMediaSource(video, inAppCompatibleUrl(inAppCurrentUrl, position), inAppStreamErrorHandler);
  video.play().catch(() => {});
  showInAppToast('Image bloquée : lecture en 1080p pour protéger le Mac. « Lecteur externe » garde la pleine définition.', 8000);
}

function stopFrozenInAppVideo(video) {
  clearInterval(inAppStallTimer);
  stopInAppRemuxStream();
  video.pause();
  video.removeAttribute('src');
  video.load();
  showInAppToast('Lecture arrêtée : ce fichier bloque le lecteur intégré. Essayez « Lecteur externe ».', 0);
}

function inAppCompatibleUrl(url, seconds, forceAudio = false) {
  const params = new URLSearchParams({ url, ss: String(Math.floor(seconds || 0)) });
  if (inAppNeedsVideoConversion || inAppDownscaleVideo) params.set('video', 'h264');
  else if (inAppVideoCodec === 'hevc') params.set('hvc1', '1');
  if (inAppSelectedAudioIndex !== null) params.set('audio', String(inAppSelectedAudioIndex));
  if (!inAppNeedsAudioConversion && !forceAudio) params.set('acopy', '1');
  return `/api/remux?${params}`;
}

function stopInAppRemuxStream() {
  if (!inAppRemuxStream) return;
  inAppRemuxStream.controller.abort();
  URL.revokeObjectURL(inAppRemuxStream.objectUrl);
  inAppRemuxStream = null;
}

function updateSourceBuffer(buffer, operation, signal) {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      buffer.removeEventListener('updateend', onEnd);
      buffer.removeEventListener('error', onError);
      signal.removeEventListener('abort', onAbort);
    };
    const onEnd = () => { cleanup(); resolve(); };
    const onError = () => { cleanup(); reject(new Error('Media buffer error')); };
    const onAbort = () => { cleanup(); reject(new DOMException('Aborted', 'AbortError')); };
    buffer.addEventListener('updateend', onEnd, { once: true });
    buffer.addEventListener('error', onError, { once: true });
    signal.addEventListener('abort', onAbort, { once: true });
    try { operation(); } catch (error) { cleanup(); reject(error); }
  });
}

function setInAppMediaSource(video, url, onFailure) {
  stopInAppRemuxStream();
  inAppIsRemux = url.startsWith('/api/remux');
  if (!inAppIsRemux || typeof MediaSource === 'undefined' || !MediaSource.isTypeSupported('video/mp4')) {
    video.src = url;
    video.load();
    return;
  }
  const controller = new AbortController();
  const mediaSource = new MediaSource();
  const objectUrl = URL.createObjectURL(mediaSource);
  const stream = { controller, objectUrl };
  inAppRemuxStream = stream;
  mediaSource.addEventListener('sourceopen', async () => {
    try {
      const buffer = mediaSource.addSourceBuffer('video/mp4');
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok || !response.body) {
        const error = new Error('Stream unavailable');
        error.status = response.status;
        throw error;
      }
      const reader = response.body.getReader();
      while (!controller.signal.aborted) {
        while (buffer.buffered.length && buffer.buffered.end(buffer.buffered.length - 1) - video.currentTime > 30) {
          await new Promise(resolve => setTimeout(resolve, 250));
          if (controller.signal.aborted) return;
        }
        const chunk = await reader.read();
        if (chunk.done) break;
        if (buffer.buffered.length && video.currentTime > 60 && buffer.buffered.start(0) < video.currentTime - 30) {
          await updateSourceBuffer(buffer, () => buffer.remove(buffer.buffered.start(0), video.currentTime - 30), controller.signal);
        }
        await updateSourceBuffer(buffer, () => buffer.appendBuffer(chunk.value), controller.signal);
      }
      if (!controller.signal.aborted && mediaSource.readyState === 'open') mediaSource.endOfStream();
    } catch (error) {
      if (!controller.signal.aborted && inAppRemuxStream === stream && onFailure) onFailure(error);
    }
  }, { once: true });
  video.src = objectUrl;
  video.load();
}

function inAppPlaybackPlan(info, selectedAudio, videoElement) {
  const videoTrack = info?.video?.[0];
  const audioTracks = info?.audio || [];
  const audio = selectedAudio || audioTracks[0];
  const container = String(info?.container || '').toLowerCase().split(',');
  const videoCodecs = { h264: 'avc1.42E01E', hevc: 'hvc1.1.6.L93.B0', av1: 'av01.0.04M.08' };
  const codec = videoCodecs[videoTrack?.codec];
  const highBitDepth = /(?:10|12|14|16)(?:le|be)?$/i.test(videoTrack?.pixel_format || '');
  const videoSupported = Boolean(codec && !highBitDepth && videoElement.canPlayType(`video/mp4; codecs="${codec}"`));
  const audioSupported = Boolean(audio && ['aac', 'mp3'].includes(audio.codec));
  const defaultAudio = audioTracks.find(track => track.default) || audioTracks[0];
  const needsTrackSelection = Boolean(audio && defaultAudio && audio.index !== defaultAudio.index);
  const needsVideoConversion = !videoSupported;
  const needsAudioConversion = Boolean(audio && !audioSupported);
  const needsCompatibleStream = !container.some(type => ['mov', 'mp4', 'm4v'].includes(type)) ||
    needsVideoConversion || needsAudioConversion || needsTrackSelection;
  return { needsVideoConversion, needsAudioConversion, needsCompatibleStream,
    audioIndex: audio ? audio.index : null, hasVideo: Boolean(videoTrack), hasAudio: Boolean(audio) };
}

async function recoverInAppSource(generation) {
  const recovery = inAppRecovery;
  if (!recovery || recovery.pending || generation !== inAppPlaybackGeneration || recovery.attempts >= 5) return false;
  recovery.pending = true;
  recovery.attempts++;
  const video = document.getElementById('inAppVideo');
  const position = recovery.resetPosition ? 0 : (recovery.resumePosition ?? (video ? inAppAbsoluteTime(video) : 0));
  showInAppToast('Source indisponible. Recherche d’une autre source…', 0);
  try {
    if (recovery.sourceKey && !recovery.feedbackRecorded) {
      await api('/api/playback-feedback', { method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({context: recovery.params, source_key: recovery.sourceKey, reason: 'stalled'}) });
    }
    if (generation !== inAppPlaybackGeneration || recovery !== inAppRecovery) return true;
    const result = await api('/api/one-click-play', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      // A recovery never waits for a preparation: a ready source, now.
      body: JSON.stringify({ ...recovery.params, prepared: undefined, prefer_ready: true, exclude_source_keys: [...recovery.excluded] }),
    });
    if (generation !== inAppPlaybackGeneration || recovery !== inAppRecovery) return true;
    if (!result.stream_url || !result.source_key || recovery.excluded.has(result.source_key)) throw new Error('No alternate source');
    for (const key of result.attempted_source_keys || [result.source_key]) if (key) recovery.excluded.add(key);
    recovery.pending = false;
    recovery.sourceKey = result.source_key;
    recovery.feedbackRecorded = false;
    recovery.resetPosition = false;
    const playlist = result.playlist?.length ? result.playlist : [{ title: recovery.params.title, url: result.stream_url }];
    if (result.debrid) renderDebridState(result.debrid, recovery.params.season, recovery.params.episode, result.chosen_torrent || '', playlist.length);
    openInAppPlayer(result.stream_url, recovery.params.title, playlist, inAppCurrentMedia,
      position, result.file?.filename || result.chosen_torrent || '', recovery);
    return true;
  } catch (error) {
    if (generation === inAppPlaybackGeneration && recovery === inAppRecovery) {
      recovery.pending = false;
      showInAppToast(error.code === 'provider_auth'
        ? 'Votre fournisseur refuse l’accès. Vérifiez la connexion dans les paramètres.'
        : 'Aucune autre source lisible. Ouvrez « Problème de lecture » pour réessayer les sources écartées.', 6000);
    }
    return true;
  }
}

function renderInAppAudioTracks(tracks, selectedIndex) {
  const select = document.getElementById('inAppAudioTrack');
  if (!select) return;
  select.replaceChildren();
  select.style.display = tracks.length > 1 ? 'inline-block' : 'none';
  tracks.forEach((track, index) => {
    const option = document.createElement('option');
    option.value = String(track.index);
    const language = track.language && track.language !== 'und' ? track.language.toUpperCase() : `Piste ${index + 1}`;
    option.textContent = `${language}${track.title ? ` · ${track.title}` : ''}`;
    option.selected = track.index === selectedIndex;
    select.appendChild(option);
  });
}

function changeInAppAudioTrack(value, rememberForSession = true) {
  const video = document.getElementById('inAppVideo');
  const track = inAppAudioTracks.find(item => String(item.index) === String(value));
  if (!video || !track || !inAppCurrentUrl) return;
  const wasPlaying = !video.paused;
  const position = Math.floor(inAppAbsoluteTime(video));
  inAppSelectedAudioIndex = track.index;
  if (rememberForSession && inAppSeriesId) {
    inAppSessionAudioChoice = SeriesPlayback.describeAudio(track);
  }
  inAppNeedsAudioConversion = !['aac', 'mp3'].includes(track.codec);
  inAppStreamOffset = position;
  setInAppMediaSource(video, inAppCompatibleUrl(inAppCurrentUrl, position), inAppStreamErrorHandler);
  if (wasPlaying) video.play().catch(() => showInAppToast('Cliquez sur lecture pour reprendre.', 3500));
  updateInAppSeriesPreferenceUI();
}

function inAppSeekTo(seconds) {
  const video = document.getElementById('inAppVideo');
  if (!video) return;
  const total = inAppDuration(video);
  const target = Math.max(0, total > 0 ? Math.min(total, seconds) : seconds);
  if (inAppIsRemux && inAppCurrentUrl) {
    inAppStreamOffset = target;
    setInAppMediaSource(video, inAppCompatibleUrl(inAppCurrentUrl, target), inAppStreamErrorHandler);
    video.play().catch(() => {});
  } else {
    video.currentTime = target;
  }
}

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
    if (video && !video.paused && !inAppIsDraggingVol && !isInAppMoreMenuOpen()) {
      overlay.classList.add('idle');
    }
  }, 2500);
}

// « Options » gathers the player's secondary settings and actions, so the top
// bar keeps only the title, the languages and this button.
function isInAppMoreMenuOpen() {
  const menu = document.getElementById('inAppMoreMenu');
  return Boolean(menu && !menu.hidden);
}

function inAppMenuItems() {
  const menu = document.getElementById('inAppMoreMenu');
  return menu ? [...menu.querySelectorAll('button')].filter(item => item.style.display !== 'none' && !item.disabled) : [];
}

function openInAppMoreMenu(focusFirst) {
  const menu = document.getElementById('inAppMoreMenu');
  const button = document.getElementById('inAppMoreBtn');
  if (!menu) return;
  menu.hidden = false;
  if (button) button.setAttribute('aria-expanded', 'true');
  resetInAppIdleTimer();
  if (focusFirst) inAppMenuItems()[0]?.focus();
}

function closeInAppMoreMenu(returnFocus) {
  const menu = document.getElementById('inAppMoreMenu');
  const button = document.getElementById('inAppMoreBtn');
  if (!menu || menu.hidden) return false;
  menu.hidden = true;
  if (button) {
    button.setAttribute('aria-expanded', 'false');
    if (returnFocus) button.focus();
  }
  return true;
}

// « Sur la TV »: the TV goes on from this moment, the Mac stops.
async function sendInAppPlaybackToTv() {
  closeInAppMoreMenu(false);
  const video = document.getElementById('inAppVideo');
  const position = Math.floor(inAppAbsoluteTime(video));
  const base = inAppCurrentMedia || {};
  const code = /S(\d{1,3})E(\d{1,4})/i.exec(`${inAppCurrentFilename || ''} ${inAppCurrentTitle || ''}`);
  const season = base.season || (code ? Number(code[1]) : 0);
  const episode = base.episode || (code ? Number(code[2]) : 0);
  const media = base.id ? tvMediaPayload(base, season, episode) : null;
  if (video) video.pause();
  showInAppToast('Envoi à la TV…', 6000);
  try {
    await castToTv({ stream_url: inAppCurrentUrl, title: tvTitle(media, inAppCurrentTitle), filename: inAppCurrentFilename,
      media, resume_sec: position });
    hideInAppToast();
    closeInAppPlayer();
  } catch (error) {
    showInAppToast(error.message || 'La TV ne répond pas.', 5000);
  }
}

function toggleInAppMoreMenu(event) {
  if (isInAppMoreMenuOpen()) closeInAppMoreMenu(false);
  // A keyboard activation (no pointer) moves focus into the menu.
  else openInAppMoreMenu(Boolean(event && event.detail === 0));
}

// In the menu, arrows move between items and Space/Enter press them instead of
// driving playback. Returns true when the key belongs to the menu.
function handleInAppMenuKey(event) {
  const menu = document.getElementById('inAppMoreMenu');
  if (!menu || menu.hidden || !menu.contains(document.activeElement)) return false;
  if (event.key === ' ' || event.key === 'Enter') return true;
  const items = inAppMenuItems();
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

function initInAppMoreMenu() {
  const menu = document.getElementById('inAppMoreMenu');
  if (!menu) return;
  // Settings that cycle (voix, clarté, vitesse, taille) keep the menu open;
  // an action that opens something else closes it.
  menu.addEventListener('click', event => {
    const item = event.target.closest?.('button');
    if (item && !item.dataset.name) closeInAppMoreMenu(false);
  });
  document.addEventListener('pointerdown', event => {
    if (isInAppMoreMenuOpen() && !event.target.closest?.('.kp-more')) closeInAppMoreMenu(false);
  });
}

function showInAppToast(msg, duration = 3000) {
  // The native player shows its own messages (Watch Party, remote…).
  if (window.NativePlayer?.isOpen?.() && typeof window.NativePlayer.notify === 'function') {
    window.NativePlayer.notify(msg, duration > 0 ? duration : 3000);
    return;
  }
  const toast = document.getElementById('inAppStatusToast');
  if (!toast) return;
  clearTimeout(inAppToastTimer);
  toast.innerHTML = msg;
  toast.style.display = 'block';
  if (duration > 0) {
    inAppToastTimer = setTimeout(() => {
      toast.style.display = 'none';
    }, duration);
  }
}

function hideInAppToast() {
  clearTimeout(inAppToastTimer);
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
  const curSec = Math.floor(inAppAbsoluteTime(video));
  inAppNeedsAudioConversion = true;
  const remuxUrl = inAppCompatibleUrl(inAppCurrentUrl, curSec, true);
  inAppStreamOffset = curSec;
  showInAppToast('Conversion vers un flux compatible AAC… La lecture va reprendre à la même position.', 4000);
  setInAppMediaSource(video, remuxUrl, inAppStreamErrorHandler);
  video.muted = false;
  video._userMuted = false;
  if (!video.volume || video.volume < 0.1) video.volume = 1.0;
  video.play().catch(() => {});
  updateInAppVolUI();
  const btn = document.getElementById('inAppAudioFixBtn');
  if (btn) btn.textContent = 'Son AAC demandé';
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
    btn.textContent = preset.label.replace(/^Clarté : /, '');
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
let inAppAvailableSubs = [{ label: 'CC : Off', lang: 'off', kind: '', variant: '', url: '' }];
let inAppActiveSubIdx = 0;
let inAppActiveCues = [];
let inAppSubDelaySec = 0;
const inAppSubCuesCache = {};

async function loadInAppSubtitlesForCurrentMedia() {
  const generation = inAppPlaybackGeneration;
  inAppAvailableSubs = [{ label: 'CC : Off', lang: 'off', kind: '', variant: '', url: '' }];
  inAppActiveSubIdx = 0;
  inAppActiveCues = [];
  inAppSubDelaySec = 0;
  const subOverlay = document.getElementById('inAppSubOverlay');
  if (subOverlay) { subOverlay.style.display = 'none'; subOverlay.innerHTML = ''; }
  const btn = document.getElementById('inAppSubsBtn');
  if (btn) { btn.textContent = 'CC : Off'; btn.style.borderColor = ''; }

  if (!inAppCurrentMedia || !inAppCurrentMedia.id) return;
  if (inAppCurrentMedia.offline_id) {
    try {
      const result = await api(`/api/offline/subtitles/${encodeURIComponent(inAppCurrentMedia.offline_id)}`);
      if (generation !== inAppPlaybackGeneration) return;
      const sub = result.subtitle;
      if (Array.isArray(sub?.cues) && sub.cues.length) {
        inAppAvailableSubs.push({label: sub.label, lang: sub.lang, kind: sub.kind,
          variant: sub.variant, cues: sub.cues, url: ''});
      }
    } catch (error) {}
    const selected = SeriesPlayback.resolveSubtitles(inAppAvailableSubs,
      inAppSessionSubtitleChoice, inAppSeriesPreference?.subtitles,
      SeriesPlayback.automaticSubtitles(window.kinoPrefLang || 'vf', inAppCurrentFilename || inAppCurrentTitle, window.kinoAutoFrSubs !== false));
    await activateInAppSubtitle(selected.index, false);
    updateInAppSeriesPreferenceUI();
    return;
  }
  const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : 1);
  const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : 1);
  const mtype = inAppCurrentMedia.type || 'movie';
  try {
    // The played file name lets KINO prefer subtitles timed for this release.
    const release = encodeURIComponent(String(inAppCurrentFilename || inAppCurrentTitle || '').slice(0, 300));
    const res = await api(`/api/subtitles?imdb_id=${encodeURIComponent(inAppCurrentMedia.id)}&type=${encodeURIComponent(mtype)}&season=${s || 1}&episode=${e || 1}&release=${release}&fps=${inAppVideoFps || ''}`);
    if (generation !== inAppPlaybackGeneration) return;
    const subs = (res && res.subtitles) || [];
    const frList = subs.filter(x => x.lang === 'fr').slice(0, 2);
    const enList = subs.filter(x => x.lang === 'en').slice(0, 2);
    frList.forEach((item, idx) => {
      const suffix = (item.variant === 'sdh' ? ' · SDH' : item.variant === 'forced' ? ' · Forcés' : '') + (item.partial ? ' · partiel' : '');
      inAppAvailableSubs.push({
        label: (frList.length > 1 ? `CC : Français #${idx + 1}` : 'CC : Français') + suffix,
        lang: 'fr',
        kind: 'opensubtitles',
        variant: item.variant || 'standard',
        url: item.url
      });
    });
    enList.forEach((item, idx) => {
      const suffix = (item.variant === 'sdh' ? ' · SDH' : item.variant === 'forced' ? ' · Forced' : '') + (item.partial ? ' · partiel' : '');
      inAppAvailableSubs.push({
        label: (enList.length > 1 ? `CC : English #${idx + 1}` : 'CC : English') + suffix,
        lang: 'en',
        kind: 'opensubtitles',
        variant: item.variant || 'standard',
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
  if (generation !== inAppPlaybackGeneration) return;
  const selected = SeriesPlayback.resolveSubtitles(inAppAvailableSubs,
    inAppSessionSubtitleChoice, inAppSeriesPreference?.subtitles,
    SeriesPlayback.automaticSubtitles(window.kinoPrefLang || 'vf', inAppCurrentFilename || inAppCurrentTitle, window.kinoAutoFrSubs !== false));
  await activateInAppSubtitle(selected.index, false);
  const automatic = !SeriesPlayback.globalSubtitles(window.kinoPrefLang || 'vf').enabled
    && SeriesPlayback.automaticSubtitles(window.kinoPrefLang || 'vf', inAppCurrentFilename || inAppCurrentTitle, window.kinoAutoFrSubs !== false).enabled;
  if (automatic && selected.source === 'global' && generation === inAppPlaybackGeneration) {
    showInAppToast(selected.missing
      ? 'Version originale : aucun sous-titre français trouvé. Le bouton CC propose les autres langues.'
      : 'Version originale : sous-titres français activés. Le bouton CC permet de les retirer.', 4500);
  } else if (selected.missing && generation === inAppPlaybackGeneration) {
    showInAppToast(selected.index === 0
      ? 'Sous-titres préférés absents pour cet épisode : ils restent désactivés. Choisissez une autre langue avec CC.'
      : 'Sous-titres préférés absents : une autre langue disponible a été choisie. Vérifiez le bouton CC.', 6000);
  }
  updateInAppSeriesPreferenceUI();
}

async function cycleInAppSubtitles() {
  resetInAppIdleTimer();
  if (inAppAvailableSubs.length <= 1) {
    showInAppToast(`<strong>Sous-titres</strong><br><span style="font-size:0.78rem; color:var(--muted);">Aucun sous-titre OpenSubtitles trouvé pour ce titre</span>`, 1800);
    return;
  }
  await activateInAppSubtitle((inAppActiveSubIdx + 1) % inAppAvailableSubs.length, true);
}

async function activateInAppSubtitle(index, rememberForSession = true) {
  const generation = inAppPlaybackGeneration;
  inAppActiveSubIdx = index;
  const sub = inAppAvailableSubs[index];
  if (!sub) return;
  const btn = document.getElementById('inAppSubsBtn');
  const subOverlay = document.getElementById('inAppSubOverlay');
  inAppActiveCues = [];
  if (rememberForSession && inAppSeriesId) {
    inAppSessionSubtitleChoice = sub.lang === 'off'
      ? { enabled: false, kind: '', language: '', variant: '' }
      : { enabled: true, kind: sub.kind, language: sub.lang, variant: sub.variant || 'standard' };
  }
  if (btn) {
    btn.textContent = sub.label;
    btn.style.borderColor = sub.lang !== 'off' ? '#fafafa' : '';
  }
  if (Array.isArray(sub.cues) && sub.cues.length) {
    inAppActiveCues = sub.cues;
    const video = document.getElementById('inAppVideo');
    if (video) updateInAppSubtitleOverlay(inAppAbsoluteTime(video));
    updateInAppSeriesPreferenceUI();
    return;
  }
  if (sub.lang === 'off' || !sub.url) {
    if (subOverlay) { subOverlay.style.display = 'none'; subOverlay.innerHTML = ''; }
    if (rememberForSession) showInAppToast('Sous-titres désactivés', 1400);
    updateInAppSeriesPreferenceUI();
    return;
  }
  if (rememberForSession) showInAppToast(`${escapeHtml(sub.label)} · Chargement des sous-titres…`, 1800);
  if (inAppSubCuesCache[sub.url]) {
    inAppActiveCues = inAppSubCuesCache[sub.url];
    const v = document.getElementById('inAppVideo');
    if (v) updateInAppSubtitleOverlay(inAppAbsoluteTime(v));
    updateInAppSeriesPreferenceUI();
    return;
  }
  try {
    const data = await api(`/api/subtitle-cues?url=${encodeURIComponent(sub.url)}`);
    if (generation !== inAppPlaybackGeneration || inAppActiveSubIdx !== index) return;
    if (data && Array.isArray(data.cues) && data.cues.length) {
      inAppSubCuesCache[sub.url] = data.cues;
      inAppActiveCues = data.cues;
      const v = document.getElementById('inAppVideo');
      if (v) updateInAppSubtitleOverlay(inAppAbsoluteTime(v));
    } else {
      throw new Error('Sous-titres vides');
    }
  } catch (err) {
    if (generation !== inAppPlaybackGeneration || inAppActiveSubIdx !== index) return;
    inAppActiveSubIdx = 0;
    if (btn) { btn.textContent = 'CC : Off'; btn.style.borderColor = ''; }
    if (subOverlay) { subOverlay.style.display = 'none'; subOverlay.innerHTML = ''; }
    showInAppToast('Sous-titres indisponibles pour ce fichier. Essayez une autre piste avec CC.', 5000);
  }
  updateInAppSeriesPreferenceUI();
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
  if (v) updateInAppSubtitleOverlay(inAppAbsoluteTime(v));
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
    btn.textContent = isBoost ? 'Boost' : 'Normal';
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
      showInAppToast(`<strong>Capture enregistrée</strong><br><span style="font-size:0.78rem; color:var(--muted);">${escapeHtml(res.filename)}</span>`, 2200);
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
    inAppSeekTo(inAppUndoTargetSec);
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

  const curT = inAppAbsoluteTime(video);
  showUndoSkip(curT);

  if (inAppIntroData && inAppIntroData.op && curT < inAppIntroData.op.end) {
    const targetT = inAppIntroData.op.end + 0.5;
    inAppSeekTo(targetT);
    const prov = inAppIntroData.provider ? `<br><span style="font-size:0.75rem; color:var(--muted);">${inAppIntroData.provider}</span>` : '';
    showInAppToast(`<strong>Intro passée (→ ${formatTime(inAppIntroData.op.end)})</strong>${prov}`, 2000);
  } else if (inAppIntroData && inAppIntroData.recap && curT < inAppIntroData.recap.end) {
    const targetT = inAppIntroData.recap.end + 0.5;
    inAppSeekTo(targetT);
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

async function openInAppPlayer(streamUrl, title, playlist = null, media = null, resumeSec = 0, releaseName = '', recovery = null) {
  const overlay = document.getElementById('inAppPlayerOverlay');
  const video = document.getElementById('inAppVideo');
  const titleEl = document.getElementById('inAppTitle');

  clearTimeout(inAppFallbackTimer);
  hideInAppToast();
  const generation = ++inAppPlaybackGeneration;
  inAppRecovery = recovery;
  if (recovery) recovery.resumePosition = resumeSec;
  stopInAppRemuxStream();
  inAppIsRemux = false;
  if (video._onPlayingHandler) video.removeEventListener('playing', video._onPlayingHandler);
  if (video._onErrorHandler) video.removeEventListener('error', video._onErrorHandler);
  video.pause();
  video.removeAttribute('src');
  video.load();
  const timeText = document.getElementById('inAppTimeText');
  if (timeText) timeText.textContent = '00:00 / 00:00';
  const previousAudioRepair = document.getElementById('inAppAudioFixBtn');
  if (previousAudioRepair) previousAudioRepair.style.display = 'none';

  inAppCurrentUrl = streamUrl;
  inAppCurrentTitle = title;
  inAppCurrentFilename = releaseName || title;
  inAppStreamOffset = 0;
  inAppTotalDuration = 0;
  inAppCurrentMedia = media || currentMedia;
  const seriesScope = inAppSeriesScope(inAppCurrentMedia, title, recovery);
  inAppSeriesId = seriesScope.id;
  if (seriesScope.scope !== inAppSessionScope) {
    inAppSessionScope = seriesScope.scope;
    inAppSessionAudioChoice = null;
    inAppSessionSubtitleChoice = null;
  }
  inAppSeriesPreference = null;
  inAppGlobalAudioTrack = null;
  updateInAppSeriesPreferenceUI();
  const seriesPreferencePromise = inAppSeriesId
    ? api(`/api/series-playback-preference?series_id=${encodeURIComponent(inAppSeriesId)}`)
      .then(result => result.preference).catch(() => null)
    : Promise.resolve(null);
  const runtimeMinutes = parseInt(String(inAppCurrentMedia?.runtime || inAppCurrentMedia?.runtime_minutes || ''), 10);
  if (runtimeMinutes > 0 && runtimeMinutes < 1000) inAppTotalDuration = runtimeMinutes * 60;
  inAppSkipIntroDismissed = false;
  inAppNextEpCancelled = false;
  traktScrobbledStarted = false;
  traktScrobbledStop = false;
  startRemotePolling();
  dismissUndoSkip();
  if (!inAppCurrentMedia?.offline_id) loadInAppIntroData(streamUrl, inAppCurrentMedia);
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
  const saveOfflineButton = document.getElementById('inAppSaveOfflineBtn');
  if (saveOfflineButton) saveOfflineButton.style.display = inAppCurrentMedia?.offline_id ? 'none' : 'inline-flex';
  refreshPlayerSpoilerTitles();
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

  // Analyze actual tracks first; keep the filename heuristic as a fallback.
  const releaseInfo = `${releaseName || ''} ${title || ''} ${streamUrl || ''}`;
  let needsVideoConversion = /\b(hevc|h\.?265|10bit|dv|dovi|av1|xvid|divx)\b/i.test(releaseInfo);
  const hasTrickyAudio = /\b(dts|dts-hd|truehd|atmos|eac3|ac3|ddp|dd\+|flac|opus|vorbis|pcm|5\.1|7\.1)\b/i.test(releaseInfo);
  let needsCompatibleStream = hasTrickyAudio || /\.mkv(?:\b|$)/i.test(releaseInfo) || needsVideoConversion;
  inAppNeedsAudioConversion = true;
  inAppSelectedAudioIndex = null;
  inAppVideoFps = null;
  inAppVideoCodec = null;
  inAppDownscaleVideo = false;
  clearInterval(inAppStallTimer);
  inAppAudioTracks = [];
  renderInAppAudioTracks([], null);
  showInAppToast('Analyse du son et de la vidéo…', 0);
  const probeController = new AbortController();
  const probeTimeout = setTimeout(() => probeController.abort(), 12000);
  let audioFallbackNote = '';
  try {
    const result = await api('/api/probe-media', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ url: streamUrl }), signal: probeController.signal,
    });
    if (generation !== inAppPlaybackGeneration) return;
    inAppSeriesPreference = await seriesPreferencePromise;
    if (generation !== inAppPlaybackGeneration) return;
    inAppGlobalAudioTrack = result.selected_audio || null;
    const selected = SeriesPlayback.resolveAudio(result.media?.audio,
      inAppSessionAudioChoice, inAppSeriesPreference?.audio, inAppGlobalAudioTrack);
    const plan = inAppPlaybackPlan(result.media, selected.track, video);
    if (selected.missing) audioFallbackNote = selected.source === 'series'
      ? 'La piste choisie pour cette lecture est absente : utilisation du réglage de la série.'
      : 'La piste audio préférée est absente : utilisation du réglage général. Choisissez une autre piste si nécessaire.';
    if (Number(result.media?.duration) > 0) inAppTotalDuration = Number(result.media.duration);
    inAppVideoFps = Number(result.media?.video?.[0]?.fps) || null;
    inAppVideoCodec = result.media?.video?.[0]?.codec || null;
    if (!plan.hasVideo || !plan.hasAudio) {
      if (!await recoverInAppSource(generation)) showInAppToast('Aucune piste vidéo ou audio utilisable dans ce fichier.', 5000);
      return;
    }
    needsVideoConversion = plan.needsVideoConversion;
    needsCompatibleStream = plan.needsCompatibleStream;
    inAppNeedsAudioConversion = plan.needsAudioConversion;
    inAppSelectedAudioIndex = plan.audioIndex;
    inAppAudioTracks = result.media.audio || [];
    renderInAppAudioTracks(inAppAudioTracks, plan.audioIndex);
    updateInAppSeriesPreferenceUI();
  } catch (error) {
    if (generation !== inAppPlaybackGeneration) return;
    // A source can be playable even if its metadata could not be inspected.
  } finally {
    clearTimeout(probeTimeout);
  }
  if (generation !== inAppPlaybackGeneration) return;
  if (!inAppSeriesPreference) inAppSeriesPreference = await seriesPreferencePromise;
  updateInAppSeriesPreferenceUI();
  inAppNeedsVideoConversion = needsVideoConversion;
  const compatibleUrl = seconds => inAppCompatibleUrl(streamUrl, seconds);
  const mpvBtn = document.getElementById('inAppMpvSuggestBtn');
  if (mpvBtn) {
    mpvBtn.style.display = 'none';
  }
  const audioFixBtn = document.getElementById('inAppAudioFixBtn');
  if (audioFixBtn) {
    audioFixBtn.style.display = needsCompatibleStream ? 'none' : 'inline-flex';
    audioFixBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px; margin-right:4px;"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>Réparer le son';
  }
  if (needsCompatibleStream) {
    setTimeout(() => {
      showInAppToast(inAppNeedsAudioConversion ? 'Préparation du son pour le lecteur…' : 'Préparation du flux pour le lecteur…', 1800);
    }, 250);
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
    if (generation !== inAppPlaybackGeneration) return;
    playbackStarted = true;
    if (recovery) recovery.resumePosition = null;
    clearTimeout(inAppFallbackTimer);
    hideInAppToast();
    if (inAppStallGeneration !== generation) {
      inAppStallGeneration = generation;
      startInAppStallGuard(video, generation);
    }
    if (audioFallbackNote) showInAppToast(audioFallbackNote, 6000);
    if (!inAppIsRemux && resumeSec > 15 && video.duration && resumeSec < video.duration * 0.95) {
      video.currentTime = resumeSec;
    }
    if (inAppCurrentMedia && inAppCurrentMedia.id) {
      const s = inAppCurrentMedia.season || (document.getElementById('seasonSelect') ? parseInt(document.getElementById('seasonSelect').value) : null);
      const e = inAppCurrentMedia.episode || (document.getElementById('episodeSelect') ? parseInt(document.getElementById('episodeSelect').value) : null);
      api('/api/history-record', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ media: {
          id: inAppCurrentMedia.id, name: inAppCurrentMedia.name || title,
          type: inAppCurrentMedia.type || 'movie', year: inAppCurrentMedia.year || '',
          poster: inAppCurrentMedia.poster || '', season: s, episode: e, filename: title,
        } }),
      }).then(refreshUserLists).catch(() => {});
    }
  };

  const onFallbackRequired = (reason) => {
    if (fallbackTriggered || generation !== inAppPlaybackGeneration) return;
    clearTimeout(inAppFallbackTimer);
    if (playbackStarted) {
      fallbackTriggered = true;
      recoverInAppSource(generation).then(recovered => {
        if (!recovered) showInAppToast('Lecture interrompue. Choisissez une autre source.', 5000);
      });
      return;
    }
    if (!inAppIsRemux) {
      showInAppToast(`Format conteneur MKV/Audio non natif.<br><strong>Remuxing vidéo/audio instantané...</strong>`, 2200);
      video.removeEventListener('playing', video._onPlayingHandler || (() => {}));
      video.removeEventListener('error', video._onErrorHandler || (() => {}));
      const remuxPlaying = () => {
        onPlaying();
      };
      const remuxError = error => {
        if (fallbackTriggered || generation !== inAppPlaybackGeneration) return;
        fallbackTriggered = true;
        recoverInAppSource(generation).then(recovered => {
          if (!recovered) showInAppToast(error?.status === 502
            ? 'Lien de lecture expiré ou inaccessible. Choisissez une autre source.'
            : 'Lien inaccessible ou format illisible. Choisissez une autre source.', 6000);
        });
      };
      video._onPlayingHandler = remuxPlaying;
      video._onErrorHandler = remuxError;
      video.addEventListener('playing', remuxPlaying, { once: true });
      video.addEventListener('error', remuxError, { once: true });
      const curSec = Math.floor(video.currentTime || resumeSec || 0);
      const knownDuration = inAppDuration(video);
      if (knownDuration > 0) inAppTotalDuration = knownDuration;
      inAppStreamOffset = curSec;
      inAppStreamErrorHandler = remuxError;
      setInAppMediaSource(video, compatibleUrl(curSec), remuxError);
      video.play().catch(() => {});
      inAppFallbackTimer = setTimeout(() => {
        if (!playbackStarted && (video.paused || video.readyState === 0)) {
          remuxError();
        }
      }, 20000);
      return;
    }
    fallbackTriggered = true;
    recoverInAppSource(generation).then(recovered => {
      if (!recovered) showInAppToast('Ce flux ne peut pas être lu. Choisissez une autre source.', 5000);
    });
  };

  video.removeEventListener('playing', video._onPlayingHandler || (() => {}));
  video.removeEventListener('error', video._onErrorHandler || (() => {}));

  video._onPlayingHandler = onPlaying;
  video._onErrorHandler = () => onFallbackRequired('error');
  inAppStreamErrorHandler = video._onErrorHandler;

  video.addEventListener('playing', video._onPlayingHandler, { once: true });
  video.addEventListener('error', video._onErrorHandler, { once: true });

  inAppStreamOffset = needsCompatibleStream ? Math.floor(resumeSec || 0) : 0;
  setInAppMediaSource(video, needsCompatibleStream ? compatibleUrl(resumeSec) : streamUrl, inAppStreamErrorHandler);

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
  }, 15000);
}

function reportInAppPlaybackClosed() {
  fetch('/api/player/state', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({playing: false, paused: true, current_time: 0, duration: 0, title: 'Aucune lecture en cours', sub_title: ''})
  }).catch(() => {});
}

function closeInAppPlayer() {
  const finishedId = inAppCurrentMedia?.id || '';
  reportInAppPlaybackClosed();
  closePlaybackHelp();
  closeInAppSeriesPreference();
  inAppPlaybackGeneration++;
  inAppRecovery = null;
  stopInAppRemuxStream();
  clearInterval(inAppStallTimer);
  inAppStallTimer = null;
  inAppIsRemux = false;
  inAppStreamErrorHandler = null;
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
      const duration = inAppDuration(video);
      const pct = duration > 0 ? Math.round((inAppAbsoluteTime(video) / duration) * 100) : 0;
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
  closeInAppMoreMenu(false);
  hideInAppToast();
  document.body.style.overflow = '';
  clearTimeout(inAppIdleTimeout);
  inAppStreamOffset = 0;
  inAppTotalDuration = 0;
  inAppSeriesId = '';
  inAppSeriesPreference = null;
  inAppSessionScope = '';
  inAppSessionAudioChoice = null;
  inAppSessionSubtitleChoice = null;
  refreshUserLists();
  if (typeof askRatingAfterPlayback === 'function') askRatingAfterPlayback(finishedId);
  if (kinoUpdatePendingAction) {
    setTimeout(() => {
      if (!document.getElementById('inAppPlayerOverlay')?.classList.contains('active')) {
        const action = kinoUpdatePendingAction;
        kinoUpdatePendingAction = '';
        if (action === 'install') installKinoUpdate();
        else rollbackKinoUpdate();
      }
    }, 3500);
  }
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
  if (!video || !inAppDuration(video)) return;
  inAppSeekTo(inAppAbsoluteTime(video) + delta);
  resetInAppIdleTimer();
}

function seekInApp(event) {
  const video = document.getElementById('inAppVideo');
  const seekbar = document.getElementById('inAppSeekbar');
  const duration = inAppDuration(video);
  if (!video || !duration || !seekbar) return;
  const rect = seekbar.getBoundingClientRect();
  const clickX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  const pct = clickX / rect.width;
  inAppSeekTo(pct * duration);
  resetInAppIdleTimer();
}

function inAppPrevTrack() {
  if (inAppPlaylist.length <= 1) return;
  if (inAppPlaylistIndex > 0) {
    inAppSessionScope = '';
    inAppPlaylistIndex--;
    const item = inAppPlaylist[inAppPlaylistIndex];
    const parsed = Spoilers.episodeNumber(item.title);
    const media = {...inAppCurrentMedia, season: parsed?.season || inAppCurrentMedia?.season,
      episode: parsed?.episode || Math.max(1, Number(inAppCurrentMedia?.episode || 2) - 1)};
    openInAppPlayer(item.url, item.title, inAppPlaylist, media);
  }
}

function inAppNextTrack() {
  if (inAppPlaylist.length <= 1) return;
  if (inAppPlaylistIndex < inAppPlaylist.length - 1) {
    inAppSessionScope = '';
    inAppPlaylistIndex++;
    const item = inAppPlaylist[inAppPlaylistIndex];
    const parsed = Spoilers.episodeNumber(item.title);
    const media = {...inAppCurrentMedia, season: parsed?.season || inAppCurrentMedia?.season,
      episode: parsed?.episode || Number(inAppCurrentMedia?.episode || 0) + 1};
    openInAppPlayer(item.url, item.title, inAppPlaylist, media);
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
  inAppVideoEl.addEventListener('loadedmetadata', () => {
    inAppTotalDuration = inAppMetadataDuration(inAppVideoEl.duration);
  });
  inAppVideoEl.addEventListener('playing', resetInAppIdleTimer);
  inAppVideoEl.addEventListener('play', () => {
    resetInAppIdleTimer();
    updatePlayPauseBtn(true);
    updateDiscordRpc(false);
    if (!traktScrobbledStarted && inAppCurrentMedia) {
      traktScrobbledStarted = true;
      const duration = inAppDuration(inAppVideoEl);
      const pct = duration > 0 ? Math.round((inAppAbsoluteTime(inAppVideoEl) / duration) * 100) : 0;
      scrobbleTrakt('start', inAppCurrentMedia, pct);
    }
    broadcastWpEvent('play', inAppAbsoluteTime(inAppVideoEl));
  });
  inAppVideoEl.addEventListener('pause', () => {
    updatePlayPauseBtn(false);
    updateDiscordRpc(true);
    if (inAppOverlayEl) inAppOverlayEl.classList.remove('idle');
    if (inAppCurrentMedia) {
      const duration = inAppDuration(inAppVideoEl);
      const pct = duration > 0 ? Math.round((inAppAbsoluteTime(inAppVideoEl) / duration) * 100) : 0;
      scrobbleTrakt('pause', inAppCurrentMedia, pct);
    }
    broadcastWpEvent('pause', inAppAbsoluteTime(inAppVideoEl));
  });
  inAppVideoEl.addEventListener('seeked', () => {
    broadcastWpEvent('seek', inAppAbsoluteTime(inAppVideoEl));
    updateDiscordRpc(inAppVideoEl.paused);
  });
  inAppVideoEl.addEventListener('timeupdate', () => {
    updateDiscordRpc(inAppVideoEl.paused);
    if (inAppActiveCues.length > 0) {
      updateInAppSubtitleOverlay(inAppAbsoluteTime(inAppVideoEl));
    }
    if (inAppDuration(inAppVideoEl)) {
      const curT = inAppAbsoluteTime(inAppVideoEl);
      const durT = inAppDuration(inAppVideoEl);
      const pct = (curT / durT) * 100;
      if (inAppSeekFillEl) inAppSeekFillEl.style.width = pct + '%';
      if (inAppTimeTextEl) inAppTimeTextEl.textContent = `${formatTime(curT)} / ${formatTime(durT)}`;

      // Scrobble Trakt.tv automatique dès 80% de visionnage
      if (pct >= 80 && !traktScrobbledStop && inAppCurrentMedia) {
        traktScrobbledStop = true;
        scrobbleTrakt('stop', inAppCurrentMedia, Math.round(pct)).then(sent => {
          if (sent) showInAppToast('✓ Scrobblé sur Trakt.tv (80%)', 2200);
        });
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
          if (nextTitleEl && nextItem) refreshPlayerSpoilerTitles();
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

