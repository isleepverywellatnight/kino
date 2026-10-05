/**
 * KINO Samsung Tizen Smart TV Bridge
 * ==================================
 * Intégration native pour téléviseurs Samsung sous Tizen OS :
 * - Enregistrement et écoute des touches physiques de la télécommande Samsung (Smart Control / Télécommande classique)
 * - Mappage des touches multimédia (Play/Pause 10252, Retour 10009, Avance/Recul, Touches de couleur)
 * - Empêchement de la mise en veille de la TV pendant le visionnage (Samsung AV / AppCommon API)
 * - Support du mode hybride Serveur Local (IP) pour le déploiement en Widget .wgt
 */

(function () {
  'use strict';

  const TizenBridge = {
    isTizen: false,
    serverHost: '',

    init() {
      this.detectTizen();
      this.setupServerHost();
      this.registerRemoteKeys();
      this.setupKeyListeners();
      this.setupScreenSaver();

      if (this.isTizen) {
        console.log('[KINO Tizen] Téléviseur Samsung Tizen détecté. Pont multimédia actif.');
        document.body.classList.add('is-tizen-tv');
        if (window.SpatialNav) {
          window.SpatialNav.showToast('<strong>Samsung Smart TV</strong><br><span style="font-size:0.78rem; opacity:0.85;">Télécommande Samsung connectée</span>', '📺', 4000);
        }
      }
    },

    detectTizen() {
      const ua = navigator.userAgent || '';
      this.isTizen = Boolean(
        window.tizen ||
        window.webapis ||
        ua.includes('Tizen') ||
        ua.includes('SmartTV') ||
        ua.includes('Samsung') ||
        window.location.protocol === 'file:' ||
        window.location.protocol.includes('widget')
      );
    },

    // ------------------------------------------------------------------------
    // GESTION DU SERVEUR KINO (Mode Standalone Widget TV)
    // ------------------------------------------------------------------------

    setupServerHost() {
      // Si la page est déjà servie par HTTP (ex: http://192.168.1.89:8080/), le host est relatif
      if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
        if (!window.location.href.includes('file:')) {
          this.serverHost = '';
          window.KINO_SERVER_HOST = '';
          return;
        }
      }

      // Si l'application tourne en local dans le package .wgt (file:/// ou tizen-widget://)
      const savedHost = localStorage.getItem('kino_server_host') || 'http://192.168.1.89:8080';
      this.serverHost = savedHost;
      window.KINO_SERVER_HOST = savedHost;

      // Si pas encore configuré ou premier lancement, proposer le panneau de connexion TV
      if (!localStorage.getItem('kino_server_host_confirmed')) {
        this.injectServerPromptModal(savedHost);
      }
    },

    injectServerPromptModal(defaultHost) {
      if (document.getElementById('tizenServerPrompt')) return;

      const modal = document.createElement('div');
      modal.id = 'tizenServerPrompt';
      modal.className = 'modal-bg';
      modal.style.display = 'flex';
      modal.style.zIndex = '9999999';
      modal.innerHTML = `
        <div class="modal" style="max-width:520px; padding:28px; text-align:center; border:1px solid rgba(255,255,255,0.2);">
          <div style="font-size:2.4rem; margin-bottom:12px;">📺</div>
          <h2 style="font-size:1.25rem; font-weight:700; margin:0 0 8px 0; color:#ffffff;">KINO sur Samsung Smart TV</h2>
          <p style="font-size:0.86rem; color:var(--muted); line-height:1.45; margin-bottom:20px;">
            Pour accéder à vos films, séries et au débridage Real-Debrid, connectez l'application à votre PC KINO sur le réseau Wi-Fi local :
          </p>
          <div style="margin-bottom:18px;">
            <input type="text" id="tizenServerHostInput" value="${defaultHost}"
                   style="width:100%; padding:12px 14px; background:var(--bg); border:1px solid var(--border-hover); border-radius:8px; color:#fff; font-size:1rem; text-align:center; font-family:monospace;"
                   placeholder="http://192.168.1.XX:8080">
          </div>
          <div style="display:flex; gap:10px; justify-content:center;">
            <button id="btnConnectTizen" class="btn btn-primary" style="padding:10px 24px; font-size:0.92rem; font-weight:600;" onclick="TizenBridge.saveServerHost()">
              Connexion Immédiate
            </button>
          </div>
          <div style="font-size:0.75rem; color:var(--dim); margin-top:14px;">
            Vérifiez que KINO est bien lancé sur votre ordinateur.
          </div>
        </div>
      `;
      document.body.appendChild(modal);

      setTimeout(() => {
        const btn = document.getElementById('btnConnectTizen');
        if (btn && window.SpatialNav) {
          window.SpatialNav.setFocus(btn);
        }
      }, 300);
    },

    saveServerHost() {
      const inp = document.getElementById('tizenServerHostInput');
      let host = (inp ? inp.value : '').trim();
      if (!host) host = 'http://192.168.1.89:8080';
      if (!host.startsWith('http://') && !host.startsWith('https://')) {
        host = 'http://' + host;
      }
      host = host.replace(/\/+$/, '');

      this.serverHost = host;
      window.KINO_SERVER_HOST = host;
      localStorage.setItem('kino_server_host', host);
      localStorage.setItem('kino_server_host_confirmed', 'true');

      const modal = document.getElementById('tizenServerPrompt');
      if (modal) modal.remove();

      if (window.SpatialNav) {
        window.SpatialNav.showToast('Serveur KINO connecté : ' + host, '📡', 3000);
      }

      // Recharger la configuration depuis le serveur
      if (typeof window.checkConfig === 'function') {
        window.checkConfig();
      }
      if (typeof window.loadCategory === 'function') {
        window.loadCategory('trending');
      }
    },

    // ------------------------------------------------------------------------
    // ENREGISTREMENT DES TOUCHES TÉLÉCOMMANDE SAMSUNG TIZEN
    // ------------------------------------------------------------------------

    registerRemoteKeys() {
      if (!window.tizen || !window.tizen.tvinputdevice) return;

      const keysToRegister = [
        'MediaPlay',
        'MediaPause',
        'MediaPlayPause',
        'MediaFastForward',
        'MediaRewind',
        'MediaStop',
        'ColorF0Red',
        'ColorF1Green',
        'ColorF2Yellow',
        'ColorF3Blue',
        '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'
      ];

      for (const k of keysToRegister) {
        try {
          window.tizen.tvinputdevice.registerKey(k);
        } catch (e) {
          // Ignorer les touches non supportées par le modèle de TV spécifique
        }
      }
    },

    setupKeyListeners() {
      window.addEventListener('keydown', (e) => {
        const keyCode = e.keyCode;

        // 1. Touche RETOUR / BACK de la télécommande Samsung (KeyCode officiel Tizen: 10009)
        if (keyCode === 10009) {
          e.preventDefault();
          if (window.SpatialNav) {
            window.SpatialNav.backCurrent();
          } else if (typeof window.closeInAppPlayer === 'function') {
            window.closeInAppPlayer();
          }
          return;
        }

        // 2. Touche PLAY/PAUSE combinée Samsung Smart Control (KeyCode: 10252)
        if (keyCode === 10252) {
          e.preventDefault();
          if (typeof window.toggleInAppPlay === 'function') {
            window.toggleInAppPlay();
          }
          return;
        }

        // 3. Touche PLAY dédiée (KeyCode: 415)
        if (keyCode === 415) {
          e.preventDefault();
          const v = document.getElementById('inAppVideo');
          if (v && v.paused && typeof window.toggleInAppPlay === 'function') {
            window.toggleInAppPlay();
          }
          return;
        }

        // 4. Touche PAUSE dédiée (KeyCode: 19)
        if (keyCode === 19) {
          e.preventDefault();
          const v = document.getElementById('inAppVideo');
          if (v && !v.paused && typeof window.toggleInAppPlay === 'function') {
            window.toggleInAppPlay();
          }
          return;
        }

        // 5. Touche STOP dédiée (KeyCode: 413)
        if (keyCode === 413) {
          e.preventDefault();
          if (typeof window.closeInAppPlayer === 'function') {
            window.closeInAppPlayer();
          }
          return;
        }

        // 6. Touche AVANCE RAPIDE >> (KeyCode: 417)
        if (keyCode === 417) {
          e.preventDefault();
          if (typeof window.inAppSeekRel === 'function') {
            window.inAppSeekRel(30);
          }
          return;
        }

        // 7. Touche RETOUR RAPIDE << (KeyCode: 412)
        if (keyCode === 412) {
          e.preventDefault();
          if (typeof window.inAppSeekRel === 'function') {
            window.inAppSeekRel(-30);
          }
          return;
        }

        // 8. Touches de COULEUR de la télécommande Samsung
        // Rouge (403) : Ma Liste / Watchlist
        if (keyCode === 403) {
          e.preventDefault();
          if (window.SpatialNav) {
            window.SpatialNav.toggleWatchlistOnCurrent();
          }
          return;
        }

        // Vert (404) : Focus Recherche
        if (keyCode === 404) {
          e.preventDefault();
          if (window.SpatialNav) {
            window.SpatialNav.focusSearchOrTracks();
          }
          return;
        }

        // Jaune (405) : Paramètres KINO
        if (keyCode === 405) {
          e.preventDefault();
          if (typeof window.openConfig === 'function') {
            window.openConfig();
          }
          return;
        }

        // Bleu (406) : Aide et Raccourcis
        if (keyCode === 406) {
          e.preventDefault();
          if (typeof window.toggleShortcutsModal === 'function') {
            window.toggleShortcutsModal('gamepad');
          }
          return;
        }
      });
    },

    // ------------------------------------------------------------------------
    // GESTION DE LA VEILLE DE LA TV (Screen Saver API Samsung)
    // ------------------------------------------------------------------------

    setupScreenSaver() {
      const updateScreenSaverState = (disableSaver) => {
        if (window.webapis && window.webapis.appcommon) {
          try {
            const state = disableSaver
              ? window.webapis.appcommon.AppCommonScreenSaverState.SCREEN_SAVER_OFF
              : window.webapis.appcommon.AppCommonScreenSaverState.SCREEN_SAVER_ON;
            window.webapis.appcommon.setScreenSaver(state);
          } catch (_) {}
        }
      };

      // Écouter l'état du lecteur vidéo in-app
      const video = document.getElementById('inAppVideo');
      if (video) {
        video.addEventListener('play', () => updateScreenSaverState(true));
        video.addEventListener('playing', () => updateScreenSaverState(true));
        video.addEventListener('pause', () => updateScreenSaverState(false));
        video.addEventListener('ended', () => updateScreenSaverState(false));
      }
    }
  };

  // Export global
  window.TizenBridge = TizenBridge;

  // Initialisation automatique
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => TizenBridge.init());
  } else {
    TizenBridge.init();
  }
})();
