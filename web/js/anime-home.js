// The Anime tab: « À la une » in rows (what is on, from AniList),
// the week's planning in lines, and the catalogue. Every work opens KINO's own
// fiche: the server pairs it with the fiche KINO can play (anime_home.py).

let currentAnimeSubTab = 'home';
let animeHomeData = null;
let animeHomeRequest = null;
let animeLibraryIds = new Set();
let animeWeekData = null;
let animeWeekRequest = null;
let animeWeekDay = -1;
let animeWeekFilter = 'popular';
let animeWeekTimer = null;
const animeWorks = new Map();

const ANIME_PAGES = { home: 'animeSubTabHome', schedule: 'animeSubTabSchedule', catalog: 'animeSubTabCatalog' };
const ANIME_FORMATS = { TV: 'Série', TV_SHORT: 'Série courte', ONA: 'Série web', MOVIE: 'Film', OVA: 'OVA', SPECIAL: 'Spécial' };
const ANIME_MONTHS = KINO_I18N.months('short');
const ANIME_WEEKDAYS = ['dim.', 'lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.'];
// AniList counts the members who follow a work; below this the planning is
// mostly shorts and children's programmes, kept for « Tout ».
const ANIME_POPULAR_FROM = 10000;
const ANIME_WEEK_FILTERS = { popular: 'Populaires', mine: 'Mes animes', all: 'Tout' };
// AniList's own genres; the others are the catalogue's (frenchGenre).
const ANIME_GENRES = { 'Slice of Life': 'Tranche de vie', 'Sports': 'Sport', 'Supernatural': 'Surnaturel', 'Psychological': 'Psychologique',
  'Mahou Shoujo': 'Magical girl' };

// A title of the history or of Ma Liste that is an anime: the server names the
// ones it knows (anime_home.library_ids).
function isAnimeTitle(entry) {
  return Boolean(entry && (entry.is_anime || entry.type === 'anime' || animeLibraryIds.has(entry.id)));
}

// What the user follows, by fiche: Ma Liste and the history.
function animeMineStates() {
  const states = new Map();
  for (const item of userWatchlist || []) if (item?.id) states.set(item.id, 'Ma Liste');
  for (const entry of userHistory || []) {
    if (!entry?.id) continue;
    const done = Boolean(entry.completed || Number(entry.progress_pct || 0) >= 85);
    const finished = entry.type === 'series' ? Boolean(entry.series_finished && done) : done;
    states.set(entry.id, finished ? '✓ Vu' : 'En cours');
  }
  return states;
}

// ---------- The three pages ----------

// What each page shows. Called again when the catalogue lands, since it brings
// its own banner and « Voir plus ».
function applyAnimeSubTab() {
  const page = currentAnimeSubTab;
  const set = (id, display) => { const el = document.getElementById(id); if (el) el.style.display = display; };
  set('animeHomePanel', page === 'home' ? 'block' : 'none');
  set('animeSchedulePanel', page === 'schedule' ? 'block' : 'none');
  set('catalogHeader', page === 'catalog' ? 'flex' : 'none');
  set('genreFilters', page === 'catalog' ? 'flex' : 'none');
  set('catalogSortWrap', page === 'catalog' ? 'flex' : 'none');
  set('postersGrid', page === 'catalog' ? 'grid' : 'none');
  set('loadMoreWrap', page === 'catalog' && catalogHasMore ? 'flex' : 'none');
  if (page !== 'home') {
    set('heroSpotlight', 'none');
    set('homeResumeSection', 'none');
  } else if (heroSpotlightItems.length) {
    set('heroSpotlight', 'flex');
  }
}

async function switchAnimeSubTab(page = currentAnimeSubTab) {
  currentAnimeSubTab = Object.prototype.hasOwnProperty.call(ANIME_PAGES, page) ? page : 'home';
  for (const [key, id] of Object.entries(ANIME_PAGES)) {
    const tab = document.getElementById(id);
    if (!tab) continue;
    tab.classList.toggle('active', key === currentAnimeSubTab);
    if (key === currentAnimeSubTab) tab.setAttribute('aria-current', 'page');
    else tab.removeAttribute('aria-current');
  }
  applyAnimeSubTab();
  clearInterval(animeWeekTimer);
  animeWeekTimer = null;
  if (currentAnimeSubTab === 'home') {
    renderAnimeHome();
    await loadAnimeHome();
  } else if (currentAnimeSubTab === 'schedule') {
    renderAnimeWeek();
    // The « dans 3 h » of each line follows the clock.
    animeWeekTimer = setInterval(() => { if (!document.hidden) renderAnimeWeek(); }, 60000);
    await loadAnimeWeek();
  }
}

// ---------- « À la une » ----------

function animeDayStart(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function animeClock(date) {
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

// « Ép. 8 · sam. »: the next episode and when, short enough for a poster.
function animeNextLabel(next, nowMs = Date.now()) {
  if (!next || !next.airing_at) return '';
  const at = new Date(next.airing_at * 1000);
  const episode = `Ép. ${Number(next.episode) || 1}`;
  const days = Math.round((animeDayStart(at) - animeDayStart(new Date(nowMs))) / 86400000);
  if (at.getTime() <= nowMs) return `${episode} · diffusé`;
  if (days === 0) return `${episode} · ${animeClock(at)}`;
  if (days === 1) return `${episode} · demain`;
  if (days < 7) return `${episode} · ${ANIME_WEEKDAYS[at.getDay()]}`;
  return `${episode} · ${at.getDate()} ${ANIME_MONTHS[at.getMonth()]}`;
}

// « Série · 12 ép. · 87 % », or the month of release for a work not out yet.
function animeWorkFacts(item) {
  const facts = [ANIME_FORMATS[item.format] || 'Anime'];
  if (Number(item.episodes) > 1) facts.push(`${Number(item.episodes)} ép.`);
  if (item.status === 'NOT_YET_RELEASED') {
    if (item.year) facts.push(item.month ? `${ANIME_MONTHS[item.month - 1]} ${item.year}` : String(item.year));
  } else if (Number(item.score) > 0) {
    facts.push(`${Number(item.score)} %`);
  }
  return facts.join(' · ');
}

function animeCardHtml(item, nowMs = Date.now(), mine = new Map()) {
  const id = Number(item.id);
  animeWorks.set(id, item);
  const title = escapeHtml(item.title || 'Sans titre');
  const next = animeNextLabel(item.next, nowMs);
  const state = item.kino ? mine.get(item.kino.id) : '';
  return `
      <div class="poster-card anime-card" data-anime="${id}" onclick="openAnimeWork(${id})">
        ${state ? `<span class="poster-watched is-static">${escapeHtml(state)}</span>` : ''}
        <div class="poster-art">
          <img src="${escapeHtml(item.poster || '')}" alt="${title}" loading="lazy" onload="this.classList.add('is-loaded')" onerror="this.classList.add('is-loaded'); handlePosterCardError(this, '')">
          ${next ? `<span class="anime-card-next">${escapeHtml(next)}</span>` : ''}
        </div>
        <div class="poster-info">
          <div class="poster-title"${item.romaji ? ` title="${escapeHtml(item.romaji)}"` : ''}>${title}</div>
          <div class="poster-year">${escapeHtml(animeWorkFacts(item))}</div>
        </div>
      </div>`;
}

function animeHomeSkeleton() {
  const cards = Array.from({ length: 8 }, () => `
      <div class="poster-card is-skeleton" aria-hidden="true">
        <div class="poster-art"></div>
        <div class="poster-info"><span class="skeleton-line" style="width:82%"></span><span class="skeleton-line" style="width:44%"></span></div>
      </div>`).join('');
  return `<div class="poster-skeleton-status" role="status">Chargement…</div>`
    + ['Tendances', 'Cette saison'].map(title => `
    <section class="home-shelf anime-shelf">
      <div class="section-header"><span class="section-title">${title}</span></div>
      <div class="list-shelf-row">${cards}</div>
    </section>`).join('');
}

function renderAnimeHome() {
  const panel = document.getElementById('animeHomePanel');
  if (!panel) return;
  // « À continuer » on this page holds the anime titles only (renderHomeResume).
  if (activeTab === 'anime' && currentAnimeSubTab === 'home') renderHomeResume();
  const data = animeHomeData;
  if (!data) {
    panel.innerHTML = animeHomeSkeleton();
    return;
  }
  if (!(data.rows || []).length) {
    panel.innerHTML = `<div class="anime-home-none"><p>${escapeHtml(data.message || 'Les rangées anime sont indisponibles pour le moment.')}</p>`
      + `<div class="lib-tools"><button type="button" class="btn btn-secondary" onclick="loadAnimeHome(true)">Réessayer</button>`
      + `<button type="button" class="btn btn-secondary" onclick="switchAnimeSubTab('catalog')">Ouvrir le catalogue</button></div></div>`;
    return;
  }
  const now = Date.now();
  const mine = animeMineStates();
  panel.innerHTML = (data.stale ? '<p class="anime-home-stale">AniList ne répond pas : voici les rangées de la dernière visite.</p>' : '')
    + data.rows.map(row => `
    <section class="home-shelf anime-shelf" aria-label="${escapeHtml(row.title)}">
      <div class="section-header">
        <span class="section-title">${escapeHtml(row.title)}</span>
        ${row.note ? `<span class="section-note">${escapeHtml(row.note)}</span>` : ''}
      </div>
      <div class="list-shelf-row">${(row.items || []).map(item => animeCardHtml(item, now, mine)).join('')}</div>
    </section>`).join('');
}

function loadAnimeHome(force = false) {
  if (animeHomeRequest) return animeHomeRequest;
  animeHomeRequest = (async () => {
    try {
      const data = await api(`/api/anime/home${force ? '?force=1' : ''}`);
      if (data && Array.isArray(data.rows) && (data.rows.length || !animeHomeData?.rows?.length)) animeHomeData = data;
      if (Array.isArray(data?.library)) animeLibraryIds = new Set(data.library);
    } catch (error) {
      if (!animeHomeData) {
        animeHomeData = { ok: false, rows: [], message: error?.code === 'offline'
          ? 'Pas de connexion à internet : les rangées anime reviendront avec elle.'
          : 'Les rangées anime sont indisponibles pour le moment.' };
      }
    } finally {
      animeHomeRequest = null;
    }
    renderAnimeHome();
  })();
  return animeHomeRequest;
}

// ---------- Opening a work ----------

// A search by title, for the rare work without a fiche KINO can check.
async function searchAnimeTitle(title) {
  const input = document.getElementById('searchInput');
  const mode = document.getElementById('searchType');
  if (!input || !mode || !title) return;
  input.value = title;
  mode.value = 'anime';
  await runSearch();
}

// The fiche carries the series' own name (the one sources are searched with),
// not the name of one of its seasons: it is read before the fiche opens.
async function openAnimeWork(id) {
  const work = animeWorks.get(Number(id));
  if (!work || work.opening) return;
  work.opening = true;
  const cards = [...document.querySelectorAll(`[data-anime="${Number(id)}"]`)];
  cards.forEach(card => card.classList.add('is-opening'));
  try {
    let kino = work.kino;
    if (!kino) {
      try {
        const found = await api('/api/anime/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: work.id }) });
        if (found?.ok && found.media?.id) {
          kino = { id: found.media.id, type: found.media.type === 'movie' ? 'movie' : 'series', season: Number(found.media.open_season) || null };
          work.kino = kino;
        }
      } catch (error) {}
    }
    if (!kino) {
      await searchAnimeTitle(work.title);
      return;
    }
    let meta = META_CLIENT_CACHE.get(kino.id);
    if (!meta) {
      try {
        meta = (await api(`/api/meta?type=${kino.type}&imdb_id=${encodeURIComponent(kino.id)}`)).meta || {};
        META_CLIENT_CACHE.set(kino.id, meta);
      } catch (error) {
        meta = null;
      }
    }
    await selectMedia({
      id: kino.id,
      name: meta?.name || work.title,
      type: kino.type,
      is_anime: true,
      year: String(meta?.releaseInfo || meta?.year || work.year || ''),
      poster: meta?.poster || work.poster || '',
      open_season: Number(kino.season) || 0,
    });
  } finally {
    work.opening = false;
    cards.forEach(card => card.classList.remove('is-opening'));
  }
}

// ---------- The week's planning ----------

// Seven days from the first one of the answer, each with its episodes.
function animeWeekDays(data, nowMs = Date.now()) {
  if (!data || !data.from) return [];
  const first = new Date(data.from * 1000);
  const today = animeDayStart(new Date(nowMs));
  return Array.from({ length: 7 }, (_, index) => {
    const start = new Date(first.getFullYear(), first.getMonth(), first.getDate() + index);
    const end = new Date(first.getFullYear(), first.getMonth(), first.getDate() + index + 1);
    const offset = Math.round((start.getTime() - today) / 86400000);
    const name = ANIME_WEEKDAYS[start.getDay()];
    const label = offset === 0 ? 'Aujourd’hui' : offset === 1 ? 'Demain' : offset === -1 ? 'Hier'
      : `${name.charAt(0).toUpperCase()}${name.slice(1)} ${start.getDate()}`;
    return {
      label,
      offset,
      episodes: (data.episodes || []).filter(episode => episode.airing_at * 1000 >= start.getTime() && episode.airing_at * 1000 < end.getTime()),
    };
  });
}

function animeWeekKeep(episode, filter, mine) {
  const followed = Boolean(episode.kino && mine.has(episode.kino.id));
  if (filter === 'mine') return followed;
  if (filter === 'popular') return followed || Number(episode.popularity || 0) >= ANIME_POPULAR_FROM;
  return true;
}

// « dans 3 h 20 », « dans 12 min », « diffusé il y a 2 h ».
function animeAiringLabel(airingAt, nowMs = Date.now()) {
  const minutes = Math.round((airingAt * 1000 - nowMs) / 60000);
  const span = value => {
    const hours = Math.floor(value / 60);
    if (hours >= 48) return `${Math.floor(hours / 24)} j`;
    if (hours >= 1) return value % 60 && hours < 10 ? `${hours} h ${String(value % 60).padStart(2, '0')}` : `${hours} h`;
    return `${Math.max(1, value)} min`;
  };
  return minutes > 0 ? `Dans ${span(minutes)}` : `Diffusé il y a ${span(-minutes)}`;
}

function animeWeekRowHtml(episode, nowMs, mine) {
  const id = Number(episode.id);
  animeWorks.set(id, episode);
  const number = Number(episode.episode) || 1;
  const state = episode.kino ? mine.get(episode.kino.id) : '';
  const target = scheduleSpoilerTarget(episode);
  const hidden = Boolean(target && spoilerConcealed(target.id, target.season, number));
  const facts = [ANIME_FORMATS[episode.format] || 'Anime', ...(episode.genres || []).slice(0, 2).map(genre => ANIME_GENRES[genre] || frenchGenre(genre)),
    Number(episode.score) > 0 ? `${Number(episode.score)} %` : ''].filter(Boolean).join(' · ');
  const aired = episode.airing_at * 1000 <= nowMs;
  const poster = hidden || !episode.poster ? '' : `<img src="${escapeHtml(episode.poster)}" alt="" loading="lazy" onerror="this.remove()">`;
  return `<article class="offline-item myseries-item week-row${aired ? ' is-aired' : ''}" data-anime="${id}">
      <time class="week-time" datetime="${new Date(episode.airing_at * 1000).toISOString()}">${animeClock(new Date(episode.airing_at * 1000))}</time>
      <div class="offline-poster"${hidden ? ` role="img" aria-label="Image masquée pour l’épisode ${number}"` : ''}>${poster}<span aria-hidden="true">${hidden ? number : '▷'}</span></div>
      <div class="offline-info">
        <strong${episode.romaji ? ` title="${escapeHtml(episode.romaji)}"` : ''}>${escapeHtml(episode.title || 'Sans titre')}</strong>
        <span class="myseries-fact">Épisode ${number}${state ? ` · ${escapeHtml(state)}` : ''}</span>
        <span class="myseries-fact">${escapeHtml(facts)}</span>
      </div>
      <div class="offline-actions">
        <span class="week-when">${escapeHtml(animeAiringLabel(episode.airing_at, nowMs))}</span>
        ${hidden ? `<button type="button" class="btn btn-secondary spoiler-reveal-btn" onclick="revealScheduleEpisode(${id}, ${number})" aria-label="Afficher l’image de l’épisode ${number}">Afficher</button>` : ''}
        <button type="button" class="btn btn-secondary" onclick="openAnimeWork(${id})">Fiche</button>
      </div>
    </article>`;
}

function renderAnimeWeek() {
  const list = document.getElementById('animeScheduleGrid');
  const daysBar = document.getElementById('animeScheduleDays');
  const summary = document.getElementById('animeScheduleSummary');
  const filter = document.getElementById('animeScheduleFilter');
  if (!list || !daysBar) return;
  if (filter) filter.value = animeWeekFilter;
  const data = animeWeekData;
  if (!data) {
    daysBar.innerHTML = '';
    if (summary) summary.textContent = 'Chargement du planning…';
    list.innerHTML = '';
    return;
  }
  if (!data.ok) {
    daysBar.innerHTML = '';
    if (summary) summary.textContent = data.message || 'Planning indisponible pour le moment.';
    list.innerHTML = '';
    return;
  }
  const now = Date.now();
  const mine = animeMineStates();
  const days = animeWeekDays(data, now).map(day => ({ ...day, shown: day.episodes.filter(episode => animeWeekKeep(episode, animeWeekFilter, mine)) }));
  if (!days[animeWeekDay]) {
    const today = days.findIndex(day => day.offset === 0);
    animeWeekDay = today >= 0 ? today : 0;
  }
  const total = days.reduce((sum, day) => sum + day.shown.length, 0);
  const followed = days.reduce((sum, day) => sum + day.episodes.filter(episode => episode.kino && mine.has(episode.kino.id)).length, 0);
  if (summary) {
    summary.textContent = [`${total} épisode${total > 1 ? 's' : ''} sur sept jours`,
      followed ? `${followed} de vos animes` : '', data.stale ? 'planning de la dernière visite' : ''].filter(Boolean).join(' · ');
  }
  daysBar.innerHTML = days.map((day, index) => `<button type="button" class="chip${index === animeWeekDay ? ' active' : ''}" aria-pressed="${index === animeWeekDay}" onclick="selectAnimeWeekDay(${index})">${escapeHtml(day.label)} <span class="lib-count">${day.shown.length || ''}</span></button>`).join('');
  const day = days[animeWeekDay];
  list.innerHTML = day.shown.length
    ? day.shown.map(episode => animeWeekRowHtml(episode, now, mine)).join('')
    : `<p class="myseries-fact calendar-none">${animeWeekFilter === 'mine'
      ? 'Aucun de vos animes ce jour-là. Ajoutez une série à Ma Liste pour la retrouver ici.'
      : 'Aucune diffusion ce jour-là.'}</p>`;
}

function selectAnimeWeekDay(index) {
  animeWeekDay = Number(index) || 0;
  renderAnimeWeek();
}

function setAnimeWeekFilter(value) {
  animeWeekFilter = Object.prototype.hasOwnProperty.call(ANIME_WEEK_FILTERS, value) ? value : 'popular';
  renderAnimeWeek();
}

function revealScheduleEpisode(id, number) {
  const target = scheduleSpoilerTarget(animeWorks.get(Number(id)));
  if (target) revealSpoilerEpisode(target.season, number, target.id);
}

function loadAnimeWeek(force = false) {
  if (animeWeekRequest) return animeWeekRequest;
  animeWeekRequest = (async () => {
    try {
      const data = await api(`/api/anime/week${force ? '?force=1' : ''}`);
      if (data && (data.ok || !animeWeekData?.ok)) {
        if (data.from !== animeWeekData?.from) animeWeekDay = -1;
        animeWeekData = data;
      }
    } catch (error) {
      if (!animeWeekData?.ok) {
        animeWeekData = { ok: false, episodes: [], message: error?.code === 'offline'
          ? 'Pas de connexion à internet : le planning reviendra avec elle.' : 'Planning indisponible pour le moment.' };
      }
    } finally {
      animeWeekRequest = null;
    }
    renderAnimeWeek();
  })();
  return animeWeekRequest;
}
