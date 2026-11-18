// The phone remote (/remote): the player's state, polled, and its commands.
// Loaded by remote.html; its controls are declared actions run by actions.js.
const remoteToken = new URLSearchParams(location.search).get('token') || '';
let isUserSeeking = false;
let lastState = null;

function vibrate() {
  if (navigator.vibrate) {
    navigator.vibrate(15);
  }
}

function formatTime(seconds) {
  if (!seconds || isNaN(seconds)) return "0:00";
  const s = Math.floor(seconds);
  const m = Math.floor(s / 60);
  const remS = s % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const remM = m % 60;
    return `${h}:${remM < 10 ? '0' : ''}${remM}:${remS < 10 ? '0' : ''}${remS}`;
  }
  return `${m}:${remS < 10 ? '0' : ''}${remS}`;
}

async function sendCommand(action, value = null) {
  vibrate();
  try {
    await fetch('/api/remote/control?token=' + encodeURIComponent(remoteToken), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, value })
    });
  } catch (e) {
    console.error("Erreur commande:", e);
  }
}

function togglePlay() {
  const isPaused = lastState ? lastState.paused : false;
  sendCommand(isPaused ? 'play' : 'pause');
}

function onSeekInput(val) {
  isUserSeeking = true;
  if (lastState && lastState.duration > 0) {
    const targetSec = (val / 100) * lastState.duration;
    document.getElementById('currentTime').textContent = formatTime(targetSec);
  }
}

function onSeekChange(val) {
  if (lastState && lastState.duration > 0) {
    const targetSec = (val / 100) * lastState.duration;
    sendCommand('seek', targetSec);
  }
  setTimeout(() => { isUserSeeking = false; }, 400);
}

function onVolumeInput(val) {
  sendCommand('volume', parseFloat(val));
}

function toggleMute() {
  if (lastState) {
    const currentVol = lastState.volume || 1;
    sendCommand('volume', currentVol > 0 ? 0 : 1);
  }
}

async function pollState() {
  try {
    const res = await fetch('/api/remote/state?token=' + encodeURIComponent(remoteToken));
    if (!res.ok) throw new Error("Erreur réseau");
    const state = await res.json();
    lastState = state;
    document.getElementById('statusDot').style.background = '#22c55e';
    document.getElementById('statusDot').style.boxShadow = '0 0 10px #22c55e';

    document.getElementById('trackTitle').textContent = state.title || "KINO Desktop";
    document.getElementById('trackSub').textContent = !state.playing
      ? "Lancez une lecture dans KINO"
      : (state.sub_title || (state.paused ? "En pause" : "En cours de lecture"));

    // Bouton Play / Pause
    const playIcon = document.getElementById('playIcon');
    if (state.paused) {
      playIcon.innerHTML = '<polygon points="6,4 20,12 6,20"/>';
    } else {
      playIcon.innerHTML = '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>';
    }

    // Barre de temps
    if (!isUserSeeking && state.duration > 0) {
      const pct = (state.current_time / state.duration) * 100;
      document.getElementById('seekSlider').value = pct;
      document.getElementById('currentTime').textContent = formatTime(state.current_time);
      document.getElementById('duration').textContent = formatTime(state.duration);
    }

    // Volume
    if (document.activeElement !== document.getElementById('volumeSlider')) {
      document.getElementById('volumeSlider').value = state.volume != null ? state.volume : 1;
    }

  } catch (e) {
    document.getElementById('statusDot').style.background = '#ef4444';
    document.getElementById('statusDot').style.boxShadow = '0 0 8px #ef4444';
  }
}

setInterval(pollState, 1000);
pollState();
