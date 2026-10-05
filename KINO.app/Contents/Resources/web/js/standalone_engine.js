/**
 * KINO Standalone Pure JS Engine (Mode 100% Autonome / Zéro Serveur PC)
 * ====================================================================
 * Permet à KINO de fonctionner directement dans n'importe quel navigateur
 * ou Smart TV Samsung sans aucun serveur Python local requis :
 * - Catalogue et Métadonnées : Cinemeta API (CORS ouvert *)
 * - Flux et Débridage : Torrentio Real-Debrid API (CORS ouvert *)
 * - Sous-titres : OpenSubtitles v3 Stremio API (CORS ouvert *)
 * - Stockage : LocalStorage persistant (Watchlist, Historique, Reprise)
 * - Import instantané de token via URL (#token=... ou ?token=...)
 */

(function () {
  'use strict';

  const StandaloneEngine = {
    active: false,
    token: '',
    provider: 'realdebrid',

    init() {
      // 1. Détection de clé passée dans l'URL (#token=XXX ou ?token=XXX ou #rd=XXX)
      try {
        const urlParams = new URLSearchParams(window.location.search);
        const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
        const tokenParam = urlParams.get('token') || urlParams.get('rd') || hashParams.get('token') || hashParams.get('rd');
        if (tokenParam && tokenParam.trim().length >= 10) {
          this.token = tokenParam.trim();
          localStorage.setItem('kino_rd_token', this.token);
          // Nettoyer l'URL sans recharger la page pour ne pas exposer le token
          if (window.history && window.history.replaceState) {
            const cleanUrl = window.location.origin + window.location.pathname;
            window.history.replaceState({}, document.title, cleanUrl);
          }
        }
      } catch (_) {}

      this.token = this.token || localStorage.getItem('kino_rd_token') || '';
      this.provider = localStorage.getItem('kino_debrid_provider') || 'realdebrid';

      // 2. Vérification d'hôte : si hébergé en ligne (GitHub Pages, etc.), activer immédiatement
      const isLocalHost = ['localhost', '127.0.0.1'].includes(window.location.hostname);
      if (!isLocalHost && !window.KINO_SERVER_HOST) {
        this.enableStandaloneMode(false);
        return;
      }

      // Test de connectivité au serveur local Python si sur localhost
      this.checkLocalBackend();
    },

    async checkLocalBackend() {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 1200);
        const host = (window.KINO_SERVER_HOST || '').replace(/\/+$/, '');
        const res = await fetch(`${host}/api/config`, { signal: controller.signal });
        clearTimeout(timeout);

        if (res.ok) {
          console.log('[KINO Engine] Serveur local Python détecté et actif.');
          this.active = false;
          return;
        }
      } catch (_) {}

      // Si le serveur local ne répond pas : Activer automatiquement le mode autonome !
      this.enableStandaloneMode(true);
    },

    enableStandaloneMode(showToastNotice = true) {
      this.active = true;
      console.log('[KINO Engine] ⚡ Mode 100% Autonome activé (Zero-Server / Pure Web Client).');

      if (showToastNotice && window.SpatialNav) {
        window.SpatialNav.showToast('<strong>⚡ KINO Mode Autonome Actif</strong><br><span style="font-size:0.78rem; opacity:0.85;">Fonctionne 100% sur la TV sans aucun PC</span>', '🚀', 3500);
      }

      // Si aucun token Real-Debrid n'est enregistré, ouvrir les paramètres au premier lancement
      if (!this.token) {
        setTimeout(() => {
          if (typeof window.openConfig === 'function') {
            window.openConfig();
          }
        }, 1200);
      }
    },

    // ------------------------------------------------------------------------
    // INTERCEPTION ET SIMULATION DES ENDPOINTS /API/ EN PUR JAVASCRIPT
    // ------------------------------------------------------------------------

    async handleApi(path, opts = {}) {
      const urlObj = new URL(path, 'http://localhost');
      const pathname = urlObj.pathname;
      const params = Object.fromEntries(urlObj.searchParams.entries());

      // 1. /api/config
      if (pathname === '/api/config') {
        if (opts.method === 'POST') {
          try {
            const body = typeof opts.body === 'string' ? JSON.parse(opts.body) : (opts.body || {});
            if (body.rd_token && body.rd_token.trim().length > 0) {
              this.token = body.rd_token.trim();
              localStorage.setItem('kino_rd_token', this.token);
            }
            if (body.debrid_provider) {
              this.provider = body.debrid_provider;
              localStorage.setItem('kino_debrid_provider', this.provider);
            }
            if (body.pref_lang) localStorage.setItem('kino_pref_lang', body.pref_lang);
            if (body.pref_quality) localStorage.setItem('kino_pref_quality', body.pref_quality);
          } catch (_) {}
        }

        return {
          debrid_provider: this.provider,
          has_token: Boolean(this.token),
          configured_providers: { [this.provider]: Boolean(this.token) },
          user: this.token ? { username: 'Smart TV Samsung', premium: 86400 * 365 } : null,
          player_mode: 'integrated',
          pref_lang: localStorage.getItem('kino_pref_lang') || 'vf',
          pref_quality: localStorage.getItem('kino_pref_quality') || '4k',
          hdr_mode: 'sdr_pref',
          audio_mode: 'voice_boost',
          download_dir: 'Stockage TV',
          discord_rpc: false
        };
      }

      // 2. /api/catalog
      if (pathname === '/api/catalog') {
        const type = params.type === 'series' ? 'series' : 'movie';
        const skip = parseInt(params.skip || '0', 10);
        const genre = params.genre ? encodeURIComponent(params.genre) : '';
        const extra = genre ? `/genre=${genre}` : '';
        const skipStr = skip > 0 ? `/skip=${skip}` : '';

        const cinemetaUrl = `https://v3-cinemeta.strem.io/catalog/${type}/top${extra}${skipStr}.json`;
        const res = await fetch(cinemetaUrl);
        const data = await res.json();
        return { metas: data.metas || [] };
      }

      // 3. /api/details ou /api/meta
      if (pathname === '/api/details' || pathname === '/api/meta') {
        const id = params.imdb_id || params.id;
        const type = (params.type === 'series' || (id && id.includes(':'))) ? 'series' : 'movie';
        const res = await fetch(`https://v3-cinemeta.strem.io/meta/${type}/${id}.json`);
        const data = await res.json();
        return { meta: data.meta || {} };
      }

      // 4. /api/search-all, /api/search-suggest ou /api/search
      if (pathname === '/api/search-all' || pathname === '/api/search-suggest' || pathname === '/api/search') {
        const q = encodeURIComponent(params.q || '');
        const [mRes, sRes] = await Promise.all([
          fetch(`https://v3-cinemeta.strem.io/catalog/movie/top/search=${q}.json`).then(r => r.json()).catch(() => ({ metas: [] })),
          fetch(`https://v3-cinemeta.strem.io/catalog/series/top/search=${q}.json`).then(r => r.json()).catch(() => ({ metas: [] }))
        ]);
        const combined = [...(mRes.metas || []), ...(sRes.metas || [])];
        return { metas: combined, results: combined };
      }

      // 5. /api/torrents
      if (pathname === '/api/torrents') {
        const imdbId = params.imdb_id || params.id;
        const isSeries = params.type === 'series' || Boolean(params.season);
        const season = params.season || 1;
        const ep = params.episode || 1;

        const target = isSeries ? `series/${imdbId}:${season}:${ep}` : `movie/${imdbId}`;
        const tioConfig = this.token
          ? `providers=torrent9,c411,nyaasi,torrentgalaxy,eztv,yts|language=french|realdebrid=${this.token}`
          : 'providers=torrent9,c411,nyaasi,torrentgalaxy,eztv,yts|language=french';
        const tioUrl = `https://torrentio.strem.fun/${tioConfig}/stream/${target}.json`;

        try {
          const res = await fetch(tioUrl);
          const data = await res.json();
          const streams = data.streams || [];

          const torrents = streams.map((s, idx) => {
            const lines = (s.title || '').split('\n');
            const rawTitle = lines[0] || (s.behaviorHints && s.behaviorHints.filename) || `Source HD #${idx + 1}`;
            const metaLine = lines.slice(1).join(' • ');
            const headerLine = (s.name || '').split('\n');
            const sourceName = headerLine[0] || 'Torrentio';

            const allText = (s.name + ' ' + s.title + ' ' + rawTitle).toUpperCase();
            const qualities = [];
            if (allText.includes('4K') || allText.includes('2160P')) qualities.push('4K');
            if (allText.includes('1080P')) qualities.push('1080p');
            if (allText.includes('720P')) qualities.push('720p');
            if (allText.includes('HDR')) qualities.push('HDR');
            if (allText.includes('DV') || allText.includes('DOLBY VISION')) qualities.push('DV');
            if (allText.includes('REMUX')) qualities.push('REMUX');
            if (allText.includes('MULTI')) qualities.push('MULTI');
            if (allText.includes('TRUEFRENCH') || allText.includes('VFF') || allText.includes('VF')) qualities.push('VF');
            if (allText.includes('VOSTFR')) qualities.push('VOSTFR');

            const langs = [];
            if (allText.includes('MULTI')) langs.push('MULTI');
            if (allText.includes('TRUEFRENCH') || allText.includes('VFF') || allText.includes('FRANCAIS') || allText.includes('VF')) langs.push('VF');
            if (allText.includes('VOSTFR')) langs.push('VOSTFR');
            if (allText.includes('VO') || allText.includes('ENGLISH')) langs.push('VO');

            return {
              title: rawTitle,
              source: sourceName,
              meta: metaLine,
              qualities,
              langs,
              magnet: s.infoHash ? `magnet:?xt=urn:btih:${s.infoHash}&dn=${encodeURIComponent(rawTitle)}` : '',
              resolve_url: s.url || '',
              direct_url: s.url || '',
              seeders: parseInt((s.title && s.title.match(/👤\s*(\d+)/) || [])[1] || '50', 10),
              size_bytes: 0
            };
          });

          return { torrents };
        } catch (e) {
          return { torrents: [] };
        }
      }

      // 6. /api/debrid & /api/debrid-status
      if (pathname === '/api/debrid' || pathname === '/api/debrid-status') {
        const body = typeof opts.body === 'string' ? JSON.parse(opts.body) : (opts.body || {});
        const streamUrl = body.resolve_url || body.stream_url || body.download || '';
        const title = body.filename || body.title || 'Flux 4K KINO TV';

        if (streamUrl) {
          return {
            ready: true,
            status: 'downloaded',
            progress: 100,
            torrent_id: 'tio_direct',
            files: [{
              filename: title,
              download: streamUrl,
              filesize: 'Qualité Optimale',
              is_target_ep: true
            }]
          };
        }

        // Si le flux n'a pas été pré-résolu, inviter à renseigner le token
        if (!this.token) {
          throw new Error('Veuillez entrer votre clé Real-Debrid dans les Réglages pour débrider ce flux.');
        }

        return {
          ready: true,
          status: 'downloaded',
          progress: 100,
          files: [{ filename: title, download: streamUrl, filesize: 'Fichier', is_target_ep: true }]
        };
      }

      // 7. /api/one-click-play & /api/auto-stream & /api/streams
      if (pathname === '/api/one-click-play' || pathname === '/api/auto-stream' || pathname === '/api/streams') {
        let imdbId = params.imdb_id || params.id;
        let season = params.season || 1;
        let ep = params.episode || 1;
        let isSeries = params.type === 'series' || Boolean(params.season);
        let mediaTitle = params.title || 'Film';

        if (opts.method === 'POST') {
          const body = typeof opts.body === 'string' ? JSON.parse(opts.body) : (opts.body || {});
          imdbId = body.imdb_id || imdbId;
          season = body.season || season;
          ep = body.episode || ep;
          isSeries = body.type === 'series' || Boolean(body.season) || isSeries;
          mediaTitle = body.title || mediaTitle;
        }

        const target = isSeries ? `series/${imdbId}:${season}:${ep}` : `movie/${imdbId}`;
        const tioConfig = this.token
          ? `providers=torrent9,c411,nyaasi,torrentgalaxy,eztv,yts|language=french|realdebrid=${this.token}`
          : 'providers=torrent9,c411,nyaasi,torrentgalaxy,eztv,yts|language=french';
        const tioUrl = `https://torrentio.strem.fun/${tioConfig}/stream/${target}.json`;

        const res = await fetch(tioUrl);
        const data = await res.json();
        const streams = (data.streams || []).filter(s => Boolean(s.url));

        if (!streams.length) {
          throw new Error('Aucun flux disponible ou clé Real-Debrid non configurée.');
        }

        const first = streams[0];
        const rawTitle = (first.title || '').split('\n')[0] || mediaTitle;

        if (pathname === '/api/auto-stream') {
          return { stream_url: first.url, title: rawTitle };
        }

        if (pathname === '/api/streams') {
          return {
            streams: streams.map(s => ({
              title: (s.title || '').split('\n')[0] || 'Flux',
              url: s.url,
              is_instant: true,
              quality: s.name || 'HD'
            }))
          };
        }

        return {
          stream_url: first.url,
          chosen_torrent: rawTitle,
          debrid: {
            ready: true,
            status: 'downloaded',
            progress: 100,
            files: [{
              filename: rawTitle,
              download: first.url,
              filesize: 'Fichier Débridé',
              is_target_ep: true
            }]
          },
          mpv: { mode: 'integrated', playlist_count: 1 },
          playlist: [{ title: mediaTitle, url: first.url }],
          resume_sec: 0
        };
      }

      // 8. /api/subtitles
      if (pathname === '/api/subtitles') {
        const imdbId = params.imdb_id || params.id;
        const type = (params.type === 'series' || (imdbId && imdbId.includes(':'))) ? 'series' : 'movie';
        try {
          const subUrl = `https://opensubtitles-v3.strem.io/subtitles/${type}/${imdbId}.json`;
          const sRes = await fetch(subUrl);
          const sData = await sRes.json();
          const list = (sData.subtitles || []).map(s => ({
            id: s.id,
            lang: s.lang === 'fre' || s.lang === 'fra' ? 'Français' : s.lang,
            url: s.url,
            label: `${s.lang === 'fre' ? 'Français' : s.lang} (${s.movieReleaseName || 'ST'})`
          }));
          return { subtitles: list };
        } catch (_) {
          return { subtitles: [] };
        }
      }

      // 9. /api/watchlist & /api/history & /api/history-record & /api/watched-toggle
      if (pathname === '/api/watchlist') {
        let wl = JSON.parse(localStorage.getItem('kino_tv_watchlist') || '[]');
        if (opts.method === 'POST') {
          const body = typeof opts.body === 'string' ? JSON.parse(opts.body) : (opts.body || {});
          const media = body.media;
          if (media && media.id) {
            const idx = wl.findIndex(x => x.id === media.id);
            if (idx >= 0) wl.splice(idx, 1);
            else wl.unshift(media);
            localStorage.setItem('kino_tv_watchlist', JSON.stringify(wl));
          }
        }
        return { watchlist: wl, count: wl.length };
      }

      if (pathname === '/api/history') {
        const hist = JSON.parse(localStorage.getItem('kino_tv_history') || '[]');
        return { history: hist, count: hist.length };
      }

      if (pathname === '/api/history-record' || pathname === '/api/watched-toggle') {
        if (opts.method === 'POST') {
          const body = typeof opts.body === 'string' ? JSON.parse(opts.body) : (opts.body || {});
          let hist = JSON.parse(localStorage.getItem('kino_tv_history') || '[]');
          if (body.id) {
            const idx = hist.findIndex(x => x.id === body.id);
            if (idx >= 0) hist.splice(idx, 1);
            hist.unshift({
              id: body.id,
              name: body.title || body.name || 'Média',
              poster: body.poster || '',
              position: body.position || 0,
              duration: body.duration || 0,
              updated_at: Date.now() / 1000
            });
            if (hist.length > 100) hist.pop();
            localStorage.setItem('kino_tv_history', JSON.stringify(hist));
          }
        }
        return { status: 'ok' };
      }

      // 10. /api/remote/qr
      if (pathname === '/api/remote/qr') {
        return { url: window.location.href };
      }

      // 11. Endpoints neutres
      if (pathname === '/api/addons') return { addons: [] };
      if (pathname === '/api/torrents' || pathname === '/api/rdcloud' || pathname === '/api/rd-history') return { torrents: [] };
      if (pathname === '/api/window') return { status: 'ok' };

      return {};
    }
  };

  window.StandaloneEngine = StandaloneEngine;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => StandaloneEngine.init());
  } else {
    StandaloneEngine.init();
  }
})();
