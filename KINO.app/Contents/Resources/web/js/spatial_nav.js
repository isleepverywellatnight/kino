/**
 * KINO Spatial Navigation & Gamepad Engine (Big Picture / TV Mode)
 * ===============================================================
 * Architecture modulaire pour navigation 100% manette & clavier de salon :
 * - Prise en charge HTML5 Gamepad API (Xbox, PlayStation DualShock/DualSense, 8BitDo, etc.)
 * - Navigation spatiale géométrique 2D ultra-précise (Flèches / Stick / D-Pad)
 * - Défilement automatique fluide et mise en avant visuelle (Glow / Scale)
 * - Raccourcis manette complets en mode catalogue et pendant la lecture vidéo
 * - Système de notifications toast universel KINO
 */

(function () {
  'use strict';

  const PRIMARY_TABS = ['movies', 'series', 'anime', 'community', 'library'];

  const SpatialNav = {
    active: false,
    focusedElem: null,
    gamepadConnected: false,
    gamepadIndex: null,
    gamepadId: '',
    rafId: null,

    // Gestion du debounce pour les boutons & axes
    lastAxisMoveTime: 0,
    axisThreshold: 0.45,
    repeatInitialDelay: 320,
    repeatTickRate: 110,
    activeDirection: null,
    directionStartTime: 0,
    lastDirectionTick: 0,

    prevButtonStates: {},

    init() {
      this.setupKeyboard();
      this.setupGamepadListeners();
      this.setupMouseReset();
      this.injectGlobalToastContainer();

      // Démarrage de la boucle de scan manette
      this.startGamepadLoop();

      console.log('[KINO SpatialNav] Moteur de navigation TV & Gamepad initialisé.');
    },

    // ------------------------------------------------------------------------
    // GESTION DU FOCUS & GÉOMÉTRIE SPATIALE
    // ------------------------------------------------------------------------

    getCurrentContainer() {
      // Détecter si une modale ou un panneau prioritaire est affiché
      const inAppOverlay = document.getElementById('inAppPlayerOverlay');
      if (inAppOverlay && inAppOverlay.classList.contains('active')) {
        return inAppOverlay;
      }

      const detailPanel = document.getElementById('detailPanel');
      if (detailPanel && (detailPanel.style.display === 'block' || detailPanel.classList.contains('active'))) {
        return detailPanel;
      }

      const shortcutsModal = document.getElementById('shortcutsModal');
      if (shortcutsModal && shortcutsModal.style.display === 'flex') {
        return shortcutsModal;
      }

      const settingsPanel = document.getElementById('settingsPanel');
      if (settingsPanel && settingsPanel.style.display === 'block') {
        return settingsPanel;
      }

      const remoteModal = document.getElementById('remoteModal');
      if (remoteModal && remoteModal.style.display === 'flex') {
        return remoteModal;
      }

      const wpModal = document.getElementById('watchPartyModal');
      if (wpModal && (wpModal.style.display === 'flex' || wpModal.classList.contains('active'))) {
        return wpModal;
      }

      const addonsModal = document.getElementById('addonsModal');
      if (addonsModal && (addonsModal.style.display === 'flex' || addonsModal.classList.contains('active'))) {
        return addonsModal;
      }

      const letterboxdModal = document.getElementById('letterboxdModal');
      if (letterboxdModal && (letterboxdModal.classList.contains('active') || letterboxdModal.style.display === 'flex')) {
        return letterboxdModal;
      }

      return document.querySelector('.container') || document.body;
    },

    getCandidates() {
      const container = this.getCurrentContainer();
      if (!container) return [];

      const selector = [
        '.poster-card',
        '.card',
        '.watchlist-card',
        '.rd-card',
        '.nav-tab',
        '.lib-subtab',
        '.header-tool-btn:not([style*="display: none"]):not([style*="display:none"])',
        '.header-user-pill',
        '.btn:not([disabled])',
        'button:not([disabled]):not([style*="display: none"]):not([style*="display:none"])',
        '.search-type-select',
        '#searchInput',
        '.genre-pill',
        '.filter-btn',
        '.source-pill',
        '.torrent-item',
        '.torrent-row',
        '.stream-btn',
        '.ep-item',
        '.season-btn',
        '.player-btn',
        '.custom-list-card',
        '.shortcut-tab-btn'
      ].join(', ');

      const raw = Array.from(container.querySelectorAll(selector));

      // Filtrer les éléments réellement visibles dans le viewport
      return raw.filter((el) => {
        if (!el || el.disabled) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        return true;
      });
    },

    setFocus(elem, smoothScroll = true) {
      if (!elem) return;

      if (this.focusedElem && this.focusedElem !== elem) {
        this.focusedElem.classList.remove('kino-focused');
      }

      this.focusedElem = elem;
      elem.setAttribute('tabindex', '0');
      elem.classList.add('kino-focused');

      try {
        elem.focus({ preventScroll: true });
      } catch (_) {}

      if (smoothScroll) {
        elem.scrollIntoView({
          behavior: 'smooth',
          block: 'nearest',
          inline: 'nearest'
        });
      }
    },

    clearFocus() {
      if (this.focusedElem) {
        this.focusedElem.classList.remove('kino-focused');
        this.focusedElem = null;
      }
    },

    moveFocus(direction) {
      const isPlayerActive = this.isPlayerOpen();
      if (isPlayerActive) {
        this.handlePlayerNavigation(direction);
        return;
      }

      const candidates = this.getCandidates();
      if (!candidates.length) return;

      // Si aucun élément n'est focusé ou s'il est orphelin du DOM
      if (!this.focusedElem || !document.contains(this.focusedElem) || !this.focusedElem.offsetParent) {
        // Sélectionner par défaut le premier élément logique (carte visible ou onglet actif)
        const firstPoster = candidates.find((c) => c.classList.contains('poster-card') || c.classList.contains('card'));
        const activeTab = candidates.find((c) => c.classList.contains('nav-tab') && c.classList.contains('active'));
        this.setFocus(firstPoster || activeTab || candidates[0]);
        return;
      }

      const curRect = this.focusedElem.getBoundingClientRect();
      const curCenterX = curRect.left + curRect.width / 2;
      const curCenterY = curRect.top + curRect.height / 2;

      let bestCand = null;
      let minScore = Infinity;

      for (let i = 0; i < candidates.length; i++) {
        const cand = candidates[i];
        if (cand === this.focusedElem) continue;

        const r = cand.getBoundingClientRect();
        const candCenterX = r.left + r.width / 2;
        const candCenterY = r.top + r.height / 2;

        let primaryDist = 0;
        let secondaryDist = 0;
        let isValidDirection = false;

        switch (direction) {
          case 'right':
            if (candCenterX > curCenterX + 8 && r.right > curRect.right) {
              isValidDirection = true;
              primaryDist = Math.max(0, r.left - curRect.right);
              secondaryDist = Math.abs(candCenterY - curCenterY);
            }
            break;
          case 'left':
            if (candCenterX < curCenterX - 8 && r.left < curRect.left) {
              isValidDirection = true;
              primaryDist = Math.max(0, curRect.left - r.right);
              secondaryDist = Math.abs(candCenterY - curCenterY);
            }
            break;
          case 'down':
            if (candCenterY > curCenterY + 8 && r.bottom > curRect.bottom) {
              isValidDirection = true;
              primaryDist = Math.max(0, r.top - curRect.bottom);
              secondaryDist = Math.abs(candCenterX - curCenterX);
            }
            break;
          case 'up':
            if (candCenterY < curCenterY - 8 && r.top < curRect.top) {
              isValidDirection = true;
              primaryDist = Math.max(0, curRect.top - r.bottom);
              secondaryDist = Math.abs(candCenterX - curCenterX);
            }
            break;
        }

        if (isValidDirection) {
          // Pénaliser les éléments désaxés pour privilégier la ligne/colonne naturelle
          const score = primaryDist * 1.0 + secondaryDist * 2.2;
          if (score < minScore) {
            minScore = score;
            bestCand = cand;
          }
        }
      }

      if (bestCand) {
        this.setFocus(bestCand);
      }
    },

    // ------------------------------------------------------------------------
    // ACTIONS & COMMANDES MANETTE / CLAVIER
    // ------------------------------------------------------------------------

    isPlayerOpen() {
      const overlay = document.getElementById('inAppPlayerOverlay');
      return Boolean(overlay && overlay.classList.contains('active'));
    },

    handlePlayerNavigation(dir) {
      if (typeof window.inAppSeekRel !== 'function') return;

      if (dir === 'left') {
        window.inAppSeekRel(-10);
      } else if (dir === 'right') {
        window.inAppSeekRel(10);
      } else if (dir === 'up') {
        const v = document.getElementById('inAppVideo');
        if (v && typeof window.setInAppVolume === 'function') {
          window.setInAppVolume(v.volume + 0.05, true);
        }
      } else if (dir === 'down') {
        const v = document.getElementById('inAppVideo');
        if (v && typeof window.setInAppVolume === 'function') {
          window.setInAppVolume(v.volume - 0.05, true);
        }
      }
    },

    activateCurrent() {
      if (this.isPlayerOpen()) {
        if (typeof window.toggleInAppPlay === 'function') {
          window.toggleInAppPlay();
        }
        return;
      }

      if (this.focusedElem) {
        // Clic sur l'élément focusé
        this.focusedElem.click();
      } else {
        const candidates = this.getCandidates();
        if (candidates.length) {
          this.setFocus(candidates[0]);
          candidates[0].click();
        }
      }
    },

    backCurrent() {
      // 1. Fermer le lecteur in-app
      if (this.isPlayerOpen()) {
        if (typeof window.closeInAppPlayer === 'function') {
          window.closeInAppPlayer();
        }
        return;
      }

      // 2. Modale raccourcis
      const sm = document.getElementById('shortcutsModal');
      if (sm && sm.style.display === 'flex') {
        if (typeof window.toggleShortcutsModal === 'function') window.toggleShortcutsModal();
        return;
      }

      // 3. Fiche détails / Torrents
      const dp = document.getElementById('detailPanel');
      if (dp && dp.style.display === 'block') {
        dp.style.display = 'none';
        const tp = document.getElementById('torrentsPanel');
        if (tp) tp.style.display = 'none';
        return;
      }

      // 4. Modale Réglages
      const sp = document.getElementById('settingsPanel');
      if (sp && sp.style.display === 'block') {
        if (typeof window.closeConfig === 'function') window.closeConfig();
        return;
      }

      // 5. Autres modales
      const modals = ['remoteModal', 'watchPartyModal', 'addonsModal', 'letterboxdModal', 'trailerModal'];
      for (const mId of modals) {
        const m = document.getElementById(mId);
        if (m && (m.style.display === 'flex' || m.classList.contains('active'))) {
          m.style.display = 'none';
          m.classList.remove('active');
          return;
        }
      }

      // 6. Si on est dans le champ de recherche
      const si = document.getElementById('searchInput');
      if (document.activeElement === si) {
        si.blur();
        this.moveFocus('down');
        return;
      }

      // 7. Si on est dans un sous-onglet de bibliothèque, revenir à films
      if (typeof window.switchTab === 'function') {
        window.switchTab('movies');
      }
    },

    toggleWatchlistOnCurrent() {
      if (this.isPlayerOpen()) {
        if (typeof window.skipInAppIntro === 'function') {
          window.skipInAppIntro();
        }
        return;
      }

      if (!this.focusedElem) return;

      // Chercher le bouton de watchlist associé
      const wlBtn = this.focusedElem.querySelector('.wl-btn, .card-wl-btn') ||
                    (this.focusedElem.classList.contains('wl-btn') ? this.focusedElem : null);

      if (wlBtn) {
        wlBtn.click();
        this.showToast('Ma Liste actualisée', '⭐', 1500);
      }
    },

    focusSearchOrTracks() {
      if (this.isPlayerOpen()) {
        if (typeof window.openInAppTrackModal === 'function') {
          window.openInAppTrackModal();
        }
        return;
      }

      const si = document.getElementById('searchInput');
      if (si) {
        this.setFocus(si, true);
        si.focus();
        si.select();
        this.showToast('Recherche activée', '🔍', 1200);
      }
    },

    cyclePrimaryTabs(offset) {
      if (this.isPlayerOpen()) {
        // En lecture, LB/RB avance ou recule de 30s
        if (typeof window.inAppSeekRel === 'function') {
          window.inAppSeekRel(offset * 30);
        }
        return;
      }

      if (typeof window.switchTab !== 'function') return;

      // Détecter l'onglet actif actuel
      const activeTabBtn = document.querySelector('.nav-tab.active');
      let currentTab = 'movies';
      if (activeTabBtn) {
        const id = activeTabBtn.id.replace('tab-', '');
        if (PRIMARY_TABS.includes(id)) currentTab = id;
      }

      let idx = PRIMARY_TABS.indexOf(currentTab);
      if (idx === -1) idx = 0;

      let nextIdx = (idx + offset + PRIMARY_TABS.length) % PRIMARY_TABS.length;
      const targetTab = PRIMARY_TABS[nextIdx];

      window.switchTab(targetTab);

      setTimeout(() => {
        const nextBtn = document.getElementById('tab-' + targetTab);
        if (nextBtn) {
          this.setFocus(nextBtn, true);
        }
      }, 100);
    },

    // ------------------------------------------------------------------------
    // GAMEPAD POLLING & LOGIQUE D'ENTRÉES
    // ------------------------------------------------------------------------

    setupGamepadListeners() {
      window.addEventListener('gamepadconnected', (e) => {
        this.gamepadConnected = true;
        this.gamepadIndex = e.gamepad.index;
        this.gamepadId = (e.gamepad.id || 'Manette').split('(')[0].trim();

        console.log(`[KINO Gamepad] Manette connectée : ${this.gamepadId} (Index ${this.gamepadIndex})`);

        this.updateHeaderGamepadBadge(true);
        this.showToast(`<strong>Manette connectée</strong><br><span style="font-size:0.78rem; opacity:0.8;">${this.gamepadId} • Mode TV actif</span>`, '🎮', 3500);

        // Activer automatiquement le focus sur le premier élément
        if (!this.focusedElem) {
          setTimeout(() => this.moveFocus('down'), 200);
        }
      });

      window.addEventListener('gamepaddisconnected', (e) => {
        if (this.gamepadIndex === e.gamepad.index) {
          this.gamepadConnected = false;
          this.gamepadIndex = null;
          this.updateHeaderGamepadBadge(false);
          this.showToast('Manette déconnectée', '🎮', 2000);
        }
      });
    },

    updateHeaderGamepadBadge(connected) {
      let btn = document.getElementById('gamepadHeaderBtn');
      if (!btn) {
        // Créer le bouton dans le header s'il n'existe pas encore
        const actionGroup = document.querySelector('.header-action-group');
        if (actionGroup) {
          btn = document.createElement('button');
          btn.id = 'gamepadHeaderBtn';
          btn.className = 'header-tool-btn';
          btn.onclick = () => {
            if (typeof window.toggleShortcutsModal === 'function') {
              window.toggleShortcutsModal('gamepad');
            }
          };
          btn.title = 'Manette & Mode TV KINO';
          btn.innerHTML = `
            <svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="6" y1="12" x2="10" y2="12"></line>
              <line x1="8" y1="10" x2="8" y2="14"></line>
              <line x1="15" y1="13" x2="15.01" y2="13"></line>
              <line x1="18" y1="11" x2="18.01" y2="11"></line>
              <rect x="2" y="6" width="20" height="12" rx="2"></rect>
            </svg>
            <span class="btn-text">TV Pad</span>
          `;
          actionGroup.appendChild(btn);
        }
      }

      if (btn) {
        btn.style.display = connected ? 'inline-flex' : 'none';
        if (connected) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      }
    },

    startGamepadLoop() {
      const poll = (time) => {
        this.pollGamepadState(time);
        this.rafId = requestAnimationFrame(poll);
      };
      this.rafId = requestAnimationFrame(poll);
    },

    pollGamepadState(now) {
      const gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
      let gp = null;

      if (this.gamepadIndex !== null && gamepads[this.gamepadIndex]) {
        gp = gamepads[this.gamepadIndex];
      } else {
        // Rechercher la première manette active connectée
        for (let i = 0; i < gamepads.length; i++) {
          if (gamepads[i] && gamepads[i].connected) {
            gp = gamepads[i];
            if (!this.gamepadConnected) {
              this.gamepadConnected = true;
              this.gamepadIndex = i;
              this.gamepadId = (gp.id || 'Manette').split('(')[0].trim();
              this.updateHeaderGamepadBadge(true);
            }
            break;
          }
        }
      }

      if (!gp || !gp.connected) return;

      const buttons = gp.buttons;
      const axes = gp.axes;

      // 1. Bouton A (Index 0) - Valider
      if (buttons[0] && buttons[0].pressed && !this.prevButtonStates[0]) {
        this.activateCurrent();
      }

      // 2. Bouton B (Index 1) - Retour / Fermer
      if (buttons[1] && buttons[1].pressed && !this.prevButtonStates[1]) {
        this.backCurrent();
      }

      // 3. Bouton X (Index 2) - Recherche / Pistes Audio
      if (buttons[2] && buttons[2].pressed && !this.prevButtonStates[2]) {
        this.focusSearchOrTracks();
      }

      // 4. Bouton Y (Index 3) - Watchlist / Skip Intro
      if (buttons[3] && buttons[3].pressed && !this.prevButtonStates[3]) {
        this.toggleWatchlistOnCurrent();
      }

      // 5. LB (Index 4) - Onglet précédent / Reculer 30s
      if (buttons[4] && buttons[4].pressed && !this.prevButtonStates[4]) {
        this.cyclePrimaryTabs(-1);
      }

      // 6. RB (Index 5) - Onglet suivant / Avancer 30s
      if (buttons[5] && buttons[5].pressed && !this.prevButtonStates[5]) {
        this.cyclePrimaryTabs(1);
      }

      // 7. Start / Menu (Index 9) - Aide & Raccourcis
      if (buttons[9] && buttons[9].pressed && !this.prevButtonStates[9]) {
        if (typeof window.toggleShortcutsModal === 'function') {
          window.toggleShortcutsModal('gamepad');
        }
      }

      // 8. Select / Back (Index 8) - Ouvrir les réglages
      if (buttons[8] && buttons[8].pressed && !this.prevButtonStates[8]) {
        if (typeof window.openConfig === 'function') {
          window.openConfig();
        }
      }

      // Enregistrer les états précédents des boutons
      for (let b = 0; b < buttons.length; b++) {
        this.prevButtonStates[b] = buttons[b].pressed;
      }

      // 9. Directionnelle (D-Pad + Stick Gauche)
      let currentDir = null;

      // D-Pad standard (12=Haut, 13=Bas, 14=Gauche, 15=Droite)
      if (buttons[12] && buttons[12].pressed) currentDir = 'up';
      else if (buttons[13] && buttons[13].pressed) currentDir = 'down';
      else if (buttons[14] && buttons[14].pressed) currentDir = 'left';
      else if (buttons[15] && buttons[15].pressed) currentDir = 'right';

      // Stick Gauche (Axes 0 et 1)
      if (!currentDir && axes.length >= 2) {
        const ax = axes[0];
        const ay = axes[1];
        if (Math.abs(ax) > Math.abs(ay)) {
          if (ax > this.axisThreshold) currentDir = 'right';
          else if (ax < -this.axisThreshold) currentDir = 'left';
        } else {
          if (ay > this.axisThreshold) currentDir = 'down';
          else if (ay < -this.axisThreshold) currentDir = 'up';
        }
      }

      // Traitement du déplacement avec répétition naturelle
      if (currentDir) {
        if (this.activeDirection !== currentDir) {
          // Premier appui immédiat
          this.activeDirection = currentDir;
          this.directionStartTime = now;
          this.lastDirectionTick = now;
          this.moveFocus(currentDir);
        } else {
          // Maintien prolongé : répétition
          const elapsed = now - this.directionStartTime;
          if (elapsed > this.repeatInitialDelay) {
            const timeSinceTick = now - this.lastDirectionTick;
            if (timeSinceTick > this.repeatTickRate) {
              this.lastDirectionTick = now;
              this.moveFocus(currentDir);
            }
          }
        }
      } else {
        this.activeDirection = null;
      }
    },

    // ------------------------------------------------------------------------
    // CLAVIER DE SALON & RESET SOURIS
    // ------------------------------------------------------------------------

    setupKeyboard() {
      window.addEventListener(
        'keydown',
        (e) => {
          const isInput = ['INPUT', 'SELECT', 'TEXTAREA'].includes(
            document.activeElement ? document.activeElement.tagName : ''
          );

          // Si l'utilisateur est en train de taper dans un champ texte standard
          if (isInput) {
            if (e.key === 'Escape') {
              document.activeElement.blur();
              this.moveFocus('down');
            }
            return;
          }

          // Si le lecteur est actif, ses propres raccourcis s'appliquent en priorité
          if (this.isPlayerOpen()) {
            return;
          }

          // Flèches directionnelles -> Navigation spatiale
          if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
            e.preventDefault();
            const dirMap = {
              ArrowUp: 'up',
              ArrowDown: 'down',
              ArrowLeft: 'left',
              ArrowRight: 'right'
            };
            this.moveFocus(dirMap[e.key]);
            return;
          }

          // Entrée sur l'élément focusé
          if (e.key === 'Enter') {
            if (this.focusedElem && this.focusedElem !== document.activeElement) {
              e.preventDefault();
              this.activateCurrent();
            }
            return;
          }

          // Touches de cycle d'onglets (Tab / Shift+Tab quand aucun champ texte n'est actif)
          if (e.key === 'PageDown' || e.key === 'PageUp') {
            e.preventDefault();
            this.cyclePrimaryTabs(e.key === 'PageDown' ? 1 : -1);
            return;
          }
        },
        { passive: false }
      );
    },

    setupMouseReset() {
      // Si la souris bouge significativement, désactiver subtilement le glow manette pour ne pas parasiter
      let lastX = 0, lastY = 0;
      window.addEventListener('mousemove', (e) => {
        if (Math.abs(e.clientX - lastX) > 12 || Math.abs(e.clientY - lastY) > 12) {
          lastX = e.clientX;
          lastY = e.clientY;
          // Ne pas supprimer le focus complètement, juste réaligner au clic souris
        }
      });
    },

    // ------------------------------------------------------------------------
    // TOAST UNIVERSEL
    // ------------------------------------------------------------------------

    injectGlobalToastContainer() {
      if (document.getElementById('kinoGlobalToast')) return;

      const toast = document.createElement('div');
      toast.id = 'kinoGlobalToast';
      toast.className = 'kino-global-toast';
      toast.innerHTML = `
        <div class="kino-global-toast-icon" id="kinoToastIcon">🎮</div>
        <div class="kino-global-toast-content" id="kinoToastContent"></div>
      `;
      document.body.appendChild(toast);
    },

    showToast(htmlContent, icon = '🎮', duration = 3000) {
      const toast = document.getElementById('kinoGlobalToast');
      const iconEl = document.getElementById('kinoToastIcon');
      const contentEl = document.getElementById('kinoToastContent');

      if (!toast || !iconEl || !contentEl) return;

      iconEl.innerHTML = icon;
      contentEl.innerHTML = htmlContent;

      toast.classList.add('show');

      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => {
        toast.classList.remove('show');
      }, duration);
    }
  };

  // Export global
  window.SpatialNav = SpatialNav;

  // Initialisation dès que le DOM est prêt
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => SpatialNav.init());
  } else {
    SpatialNav.init();
  }
})();
