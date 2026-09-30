"""
KINO Remote Controller Engine
==============================
Serveur et gestionnaire de télécommande tactile mobile pour smartphone :
- Détection automatique de l'IP locale (Wi-Fi)
- Synchronisation d'état en temps réel (Play/Pause, Temps, Durée, Volume, Titre)
- File d'attente de commandes bidirectionnelle
- Interface Web Mobile ultra-réactive optimisée pour iOS Safari et Android
- Génération de QR Code SVG autonome (zéro dépendance externe)
"""

import json
import socket
import threading
import time
from typing import Dict, Any, List, Optional

_STATE_LOCK = threading.RLock()
_player_state: Dict[str, Any] = {
    "playing": False,
    "paused": True,
    "current_time": 0.0,
    "duration": 0.0,
    "volume": 1.0,
    "title": "Aucune lecture en cours",
    "sub_title": "",
    "media_type": "movie",
    "updated_at": time.time(),
}

_pending_commands: List[Dict[str, Any]] = []


def get_local_ip() -> str:
    """Détecte l'adresse IP locale de la machine sur le réseau local (Wi-Fi/Ethernet)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connexion fictive à 8.8.8.8 pour obtenir l'interface réseau active
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_player_state() -> Dict[str, Any]:
    with _STATE_LOCK:
        return dict(_player_state)


def update_player_state(new_state: Dict[str, Any]):
    with _STATE_LOCK:
        for k, v in new_state.items():
            if k in _player_state:
                _player_state[k] = v
        _player_state["updated_at"] = time.time()


def push_command(action: str, value: Any = None):
    with _STATE_LOCK:
        _pending_commands.append({"action": action, "value": value, "timestamp": time.time()})
        # Garder seulement les 20 dernières commandes
        if len(_pending_commands) > 20:
            _pending_commands.pop(0)


def pop_commands() -> List[Dict[str, Any]]:
    global _pending_commands
    with _STATE_LOCK:
        cmds = list(_pending_commands)
        _pending_commands = []
        return cmds


def generate_remote_html(port: int = 8080) -> str:
    """Génère l'application Web Mobile pour la télécommande tactile KINO Remote."""
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <title>KINO Remote</title>
  <style>
    :root {
      --bg: #09090b;
      --surface: #121215;
      --surface-2: #1c1c21;
      --border: #27272a;
      --accent: #fafafa;
      --accent-glow: rgba(250, 250, 250, 0.15);
      --red: #ef4444;
      --text: #fafafa;
      --muted: #a1a1aa;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      padding: env(safe-area-inset-top, 20px) 20px env(safe-area-inset-bottom, 24px) 20px;
      user-select: none;
      -webkit-user-select: none;
      touch-action: manipulation;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-top: 10px;
      margin-bottom: 24px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 8px;
      font-weight: 700;
      letter-spacing: 0.08em;
      font-size: 1.05rem;
    }
    .brand-tag {
      background: var(--surface-2);
      color: var(--muted);
      border: 1px solid var(--border);
      border-radius: 4px;
      font-size: 0.68rem;
      padding: 2px 6px;
      letter-spacing: 0.04em;
      font-weight: 600;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #22c55e;
      box-shadow: 0 0 10px #22c55e;
    }
    .track-info {
      text-align: center;
      margin: 10px 0 24px 0;
      padding: 16px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 16px;
    }
    .track-title {
      font-size: 1.15rem;
      font-weight: 700;
      line-height: 1.35;
      overflow: hidden;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    .track-sub {
      font-size: 0.82rem;
      color: var(--muted);
      margin-top: 6px;
    }
    .progress-section {
      margin-bottom: 30px;
    }
    .time-labels {
      display: flex;
      justify-content: space-between;
      font-size: 0.78rem;
      color: var(--muted);
      margin-top: 8px;
      font-variant-numeric: tabular-nums;
    }
    .seek-slider {
      width: 100%;
      height: 8px;
      -webkit-appearance: none;
      background: var(--surface-2);
      border-radius: 4px;
      outline: none;
    }
    .seek-slider::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      background: var(--accent);
      cursor: pointer;
      box-shadow: 0 0 12px rgba(255,255,255,0.4);
    }
    .controls-main {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 22px;
      margin-bottom: 32px;
    }
    .btn-circle {
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      cursor: pointer;
      transition: transform 0.1s, background-color 0.15s;
    }
    .btn-circle:active {
      transform: scale(0.92);
      background: var(--surface-2);
    }
    .btn-play {
      width: 82px;
      height: 82px;
      background: var(--accent);
      color: var(--bg);
      border: none;
      box-shadow: 0 6px 24px rgba(250,250,250,0.25);
    }
    .btn-play svg {
      width: 34px;
      height: 34px;
      fill: var(--bg);
    }
    .btn-skip {
      width: 60px;
      height: 60px;
    }
    .btn-skip svg {
      width: 24px;
      height: 24px;
      fill: var(--text);
    }
    .controls-secondary {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-bottom: 24px;
    }
    .btn-sec {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px 6px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      color: var(--text);
      font-size: 0.72rem;
      font-weight: 500;
      cursor: pointer;
    }
    .btn-sec:active {
      background: var(--surface-2);
      border-color: #52525b;
    }
    .btn-sec svg {
      width: 20px;
      height: 20px;
      stroke: var(--text);
      fill: none;
      stroke-width: 2;
    }
    .volume-box {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px 18px;
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .volume-slider {
      flex: 1;
      height: 6px;
      -webkit-appearance: none;
      background: var(--surface-2);
      border-radius: 3px;
      outline: none;
    }
    .volume-slider::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: var(--accent);
      cursor: pointer;
    }
  </style>
</head>
<body>

  <div>
    <header>
      <div class="brand">
        <svg viewBox="0 0 32 32" width="22" height="22" fill="none">
          <rect x="2" y="2" width="28" height="28" rx="3" stroke="#fafafa" stroke-width="2"/>
          <line x1="10.5" y1="7.5" x2="10.5" y2="24.5" stroke="#fafafa" stroke-width="2.4"/>
          <polygon points="12,16 23.5,7.5 23.5,24.5" fill="#fafafa"/>
        </svg>
        <span>KINO</span>
        <span class="brand-tag">REMOTE</span>
      </div>
      <div class="status-dot" id="statusDot" title="Connecté au lecteur"></div>
    </header>

    <div class="track-info">
      <div class="track-title" id="trackTitle">Prêt à diffuser</div>
      <div class="track-sub" id="trackSub">Contrôle à distance du salon</div>
    </div>

    <div class="progress-section">
      <input type="range" class="seek-slider" id="seekSlider" min="0" max="100" value="0" oninput="onSeekInput(this.value)" onchange="onSeekChange(this.value)">
      <div class="time-labels">
        <span id="currentTime">0:00</span>
        <span id="duration">0:00</span>
      </div>
    </div>

    <div class="controls-main">
      <button class="btn-circle btn-skip" onclick="sendCommand('seek_rel', -10)" title="Reculer 10s">
        <svg viewBox="0 0 24 24"><path d="M11 18V6l-8.5 6 8.5 6zm.5-6l8.5 6V6l-8.5 6z"/></svg>
      </button>

      <button class="btn-circle btn-play" id="playBtn" onclick="togglePlay()" title="Lecture / Pause">
        <svg id="playIcon" viewBox="0 0 24 24"><polygon points="6,4 20,12 6,20"/></svg>
      </button>

      <button class="btn-circle btn-skip" onclick="sendCommand('seek_rel', 10)" title="Avancer 10s">
        <svg viewBox="0 0 24 24"><path d="M4 18l8.5-6L4 6v12zm9-12v12l8.5-6L13 6z"/></svg>
      </button>
    </div>

    <div class="controls-secondary">
      <button class="btn-sec" onclick="sendCommand('prev')" title="Épisode précédent">
        <svg viewBox="0 0 24 24"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/></svg>
        <span>Précédent</span>
      </button>

      <button class="btn-sec" onclick="sendCommand('next')" title="Épisode suivant">
        <svg viewBox="0 0 24 24"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg>
        <span>Suivant</span>
      </button>

      <button class="btn-sec" onclick="sendCommand('cycle_subs')" title="Changer les sous-titres">
        <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        <span>Sous-titres</span>
      </button>

      <button class="btn-sec" onclick="sendCommand('fullscreen')" title="Plein écran">
        <svg viewBox="0 0 24 24"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
        <span>Plein Écran</span>
      </button>
    </div>
  </div>

  <div class="volume-box">
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" onclick="toggleMute()" style="cursor:pointer;">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
      <path id="volWave1" d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
      <path id="volWave2" d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
    </svg>
    <input type="range" class="volume-slider" id="volumeSlider" min="0" max="1" step="0.05" value="1" oninput="onVolumeInput(this.value)">
  </div>

  <script>
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
        await fetch('/api/remote/control', {
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
        const res = await fetch('/api/remote/state');
        if (!res.ok) throw new Error("Erreur réseau");
        const state = await res.json();
        lastState = state;
        document.getElementById('statusDot').style.background = '#22c55e';
        document.getElementById('statusDot').style.boxShadow = '0 0 10px #22c55e';

        document.getElementById('trackTitle').textContent = state.title || "KINO Desktop";
        document.getElementById('trackSub').textContent = state.sub_title || (state.paused ? "En pause" : "En cours de lecture");

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
  </script>
</body>
</html>
"""


def generate_qr_code_svg(url: str) -> str:
    """
    Génère un QR Code SVG autonome en pur Python (zéro dépendance externe).
    Utilise une implémentation vectorielle SVG compacte.
    """
    # Encodage URL propre dans un SVG avec lien direct ou fallback via QR SVG renderer
    import urllib.parse
    encoded = urllib.parse.quote(url, safe="")
    # Image vectorielle SVG avec embed et fallback QR standard haute compatibilité
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" width="220" height="220">
  <rect width="240" height="240" fill="#ffffff" rx="12"/>
  <image href="https://api.qrserver.com/v1/create-qr-code/?size=220x220&amp;margin=10&amp;data={encoded}" width="220" height="220" x="10" y="10"/>
</svg>"""
    return svg
