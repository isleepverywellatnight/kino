// « Noter en un geste ». Three gestures, the same everywhere: at the
// end of a playback, in the Home row « Vos derniers vus, à noter » and in the
// fiche. They are Letterboxd values (ratings.py), so an imported note reads the
// same way. A second click on the chosen gesture takes the rating back.
const RATING_GESTURES = [
  { key: 'disliked', value: '1', label: 'Pas aimé', icon: 'thumb-down' },
  { key: 'liked', value: '4', label: 'Aimé', icon: 'thumb-up' },
  { key: 'loved', value: '5', label: 'Adoré', icon: 'heart' },
];
const HOME_RATE_SIZE = 8;
const RATE_PROMPT_SECONDS = 30;

// The gesture a stored value reads as: 1–2.5 « Pas aimé », 3.5–4.5 « Aimé »,
// 5 « Adoré ». A 3 is none of them: the fiche shows it as stars.
function ratingGesture(value) {
  const number = parseFloat(value);
  if (!(number > 0)) return '';
  if (number <= 2.5) return 'disliked';
  if (number >= 5) return 'loved';
  if (number >= 3.5) return 'liked';
  return '';
}

function ratingIsMovie(entry) {
  return (entry?.type || 'movie') === 'movie';
}

// Seen enough to have an opinion: a film watched to the end, a series finished
// or followed for three episodes.
function ratingSeenEnough(entry) {
  if (!entry?.id) return false;
  if (entry.imported_watched) return true;
  if (ratingIsMovie(entry)) return Boolean(entry.completed || Number(entry.progress_pct || 0) >= 85);
  return Boolean(entry.series_finished || (Array.isArray(entry.watched_episodes) && entry.watched_episodes.length >= 3));
}

// The titles of one kind ('movie' or 'series') still to rate, last seen first.
function ratingCandidates(history, kind, limit = HOME_RATE_SIZE) {
  return (history || [])
    .filter(entry => ratingSeenEnough(entry) && !entry.user_rating && !entry.rating_skipped && entry.name
      && (kind === 'movie' ? ratingIsMovie(entry) : !ratingIsMovie(entry))
      && (entry.poster || /^tt\d+$/.test(String(entry.id))))
    .sort((a, b) => Number(b.last_watched || 0) - Number(a.last_watched || 0))
    .slice(0, limit);
}

// Which end of playback asks about this title: 'film', 'S2', or '' (not now).
// Once per film, once per season, never for a title already rated.
function ratingAskFor(entry) {
  if (!entry?.id || entry.user_rating) return '';
  if (ratingIsMovie(entry)) {
    const finished = entry.completed || Number(entry.progress_pct || 0) >= 88;
    return finished && entry.rating_asked !== 'film' ? 'film' : '';
  }
  const season = Number(entry.season || 0);
  const seasonOver = entry.series_finished || Number(entry.next_season || 0) > season;
  if (!entry.completed || !season || !seasonOver) return '';
  const asked = `S${season}`;
  return entry.rating_asked === asked ? '' : asked;
}

function ratingPoster(entry) {
  if (entry?.poster) return entry.poster;
  return /^tt\d+$/.test(String(entry?.id || '')) ? `https://images.metahub.space/poster/small/${entry.id}/img` : '';
}

function ratingGesturesHtml(id, current, { labels = true } = {}) {
  const chosen = ratingGesture(current);
  return `<div class="rate-gestures${labels ? '' : ' is-compact'}" role="group" aria-label="Votre avis">${RATING_GESTURES.map(g => {
    const pressed = chosen === g.key;
    return `<button type="button" class="rate-gesture is-${g.key}" data-rate-id="${escapeHtml(id)}" data-rate-value="${g.value}"`
      + ` aria-pressed="${pressed}" title="${pressed ? `${g.label} (cliquer pour retirer)` : g.label}"${labels ? '' : ` aria-label="${g.label}"`}`
      + ` onclick="rateFromButton(event, this)">`
      + `<svg class="ui-icon ui-icon-sm" aria-hidden="true"><use href="/icons.svg#${g.icon}${pressed ? '-fill' : ''}"></use></svg>`
      + `${labels ? `<span>${g.label}</span>` : ''}</button>`;
  }).join('')}</div>`;
}

// The small mark on a poster: the gesture, or the stars of an imported 3.
function ratingBadgeHtml(value) {
  const gesture = RATING_GESTURES.find(g => g.key === ratingGesture(value));
  if (gesture) {
    return `<span class="poster-user-rating is-${gesture.key}" title="Votre avis : ${gesture.label}"><svg class="ui-icon ui-icon-xs" aria-hidden="true"><use href="/icons.svg#${gesture.icon}-fill"></use></svg></span>`;
  }
  return value ? `<span class="poster-user-rating" title="Votre note">★ ${escapeHtml(String(value))}/5</span>` : '';
}

// « Votre avis » in the fiche, for a title of the history.
function ficheRatingHtml(mediaId, entry, value) {
  if (!entry) return value ? `<span class="detail-chip detail-chip-strong">★ ${escapeHtml(String(value))}/5 · Ma note</span>` : '';
  const stars = value && !ratingGesture(value) ? `<span class="detail-chip">★ ${escapeHtml(String(value))}/5</span>` : '';
  return `<span class="fiche-rating" id="ficheRating" data-rating-id="${escapeHtml(mediaId)}"><span class="fiche-rating-label">Votre avis</span>${stars}${ratingGesturesHtml(mediaId, value)}<span class="rate-status" role="status"></span></span>`;
}

// --- Saving -------------------------------------------------------------------
let ratingSuggestTimer = null;

function ratingEntry(id) {
  return (typeof userHistory !== 'undefined' ? userHistory : []).find(entry => entry.id === id);
}

async function saveRating(id, body) {
  const res = await api('/api/rating', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, ...body }) });
  const entry = ratingEntry(id);
  if (entry && res?.fields) Object.assign(entry, res.fields);
  return res?.fields || {};
}

// Rates one title; the same gesture twice takes the rating back. Returns the new value.
async function rateTitle(id, value) {
  const current = ratingEntry(id)?.user_rating || '';
  const next = current && ratingGesture(current) === ratingGesture(value) ? '' : value;
  await saveRating(id, { rating: next });
  // « Pour vous » and « Si vous avez aimé » are asked again, once the clicks stop.
  if (typeof FOR_YOU_CACHE !== 'undefined') FOR_YOU_CACHE.clear();
  if (typeof RELATED_CLIENT_CACHE !== 'undefined') RELATED_CLIENT_CACHE.clear();
  clearTimeout(ratingSuggestTimer);
  ratingSuggestTimer = setTimeout(() => { if (typeof renderHomeForYou === 'function') renderHomeForYou(); }, 1500);
  return next;
}

function setRatingStatus(container, text) {
  const status = container?.querySelector('.rate-status');
  if (status) status.textContent = text;
}

async function rateFromButton(event, button) {
  if (event) event.stopPropagation();
  const id = button.dataset.rateId;
  const value = button.dataset.rateValue;
  const prompt = button.closest('.rate-prompt');
  const item = button.closest('.rate-item');
  const fiche = button.closest('.fiche-rating');
  const group = button.closest('.rate-gestures');
  group?.querySelectorAll('button').forEach(b => { b.disabled = true; });
  try {
    const next = await rateTitle(id, value);
    if (prompt) thankRatePrompt(id, next);
    if (item) {
      item.classList.add('is-rated');
      setTimeout(renderHomeRate, 260);
    }
    if (fiche) {
      group.outerHTML = ratingGesturesHtml(id, next);
      setRatingStatus(fiche, next ? 'Noté. Vos suggestions en tiennent compte.' : 'Note retirée.');
    }
  } catch (error) {
    group?.querySelectorAll('button').forEach(b => { b.disabled = false; });
    const text = 'Note non enregistrée. Réessayez.';
    if (prompt) setRatingStatus(prompt, text);
    if (item) setRatingStatus(item, text);
    if (fiche) setRatingStatus(fiche, text);
  }
}

// « Passer »: out of the Home row, still possible from the fiche.
async function skipRating(event, button) {
  if (event) event.stopPropagation();
  const item = button.closest('.rate-item');
  try {
    await saveRating(button.dataset.rateId, { skip: true });
    item?.classList.add('is-rated');
    setTimeout(renderHomeRate, 260);
  } catch (error) {
    setRatingStatus(item, 'Non enregistré. Réessayez.');
  }
}

// --- Home row ------------------------------------------------------------------
function renderHomeRate() {
  const sec = document.getElementById('homeRateSection');
  const row = document.getElementById('homeRateRow');
  if (!sec || !row) return;
  if (!onHomeTab() || (typeof networkOnline !== 'undefined' && !networkOnline)) {
    sec.hidden = true;
    row.innerHTML = '';
    return;
  }
  const kind = homeKind();
  const all = ratingCandidates(userHistory, kind, Infinity);
  sec.hidden = !all.length;
  if (!all.length) { row.innerHTML = ''; return; }
  const count = document.getElementById('homeRateCount');
  if (count) count.textContent = all.length > 1 ? `${all.length} titres · un clic par titre suffit` : 'Un clic suffit';
  row.innerHTML = all.slice(0, HOME_RATE_SIZE).map(entry => {
    const media = { id: entry.id, name: entry.name, type: kind, year: entry.year || '', poster: ratingPoster(entry) };
    return `<div class="foryou-item rate-item" data-rate-item="${escapeHtml(entry.id)}">${posterCardHtml(media, kind)}`
      + ratingGesturesHtml(entry.id, '', { labels: false })
      + `<button type="button" class="foryou-no" data-rate-id="${escapeHtml(entry.id)}" onclick="skipRating(event, this)">Passer</button>`
      + `<span class="rate-status" role="status"></span></div>`;
  }).join('');
}

// --- After a playback -------------------------------------------------------------
let ratePromptTimer = null;

function playbackOpen() {
  return Boolean(document.getElementById('inAppPlayerOverlay')?.classList.contains('active')
    || document.documentElement.classList.contains('native-playing'));
}

// Called when a player closes: asks about the film, or the season, just finished.
async function askRatingAfterPlayback(id) {
  if (!id) return;
  await new Promise(resolve => setTimeout(resolve, 700));  // the last position reaches the history
  try { await refreshUserLists(); } catch (error) {}
  const entry = ratingEntry(id);
  const asked = ratingAskFor(entry);
  if (!asked || playbackOpen()) return;
  showRatePrompt(entry, asked);
  saveRating(id, { asked }).catch(() => {});
}

function showRatePrompt(entry, asked) {
  const prompt = document.getElementById('ratePrompt');
  if (!prompt) return;
  const name = typeof displayTitle === 'function' ? displayTitle(entry) : entry.name;
  const poster = document.getElementById('ratePromptPoster');
  const source = ratingPoster(entry);
  if (poster) {
    poster.hidden = !source;
    if (source) poster.src = source;
  }
  document.getElementById('ratePromptKicker').textContent = asked === 'film'
    ? 'Film terminé' : (entry.series_finished ? 'Série terminée' : `Saison ${asked.slice(1)} terminée`);
  document.getElementById('ratePromptTitle').textContent = `Vous avez aimé ${name} ?`;
  document.getElementById('ratePromptGestures').innerHTML = ratingGesturesHtml(entry.id, '');
  setRatingStatus(prompt, '');
  prompt.classList.remove('is-done');
  prompt.hidden = false;
  clearTimeout(ratePromptTimer);
  ratePromptTimer = setTimeout(closeRatePrompt, RATE_PROMPT_SECONDS * 1000);
}

function thankRatePrompt(id, value) {
  const prompt = document.getElementById('ratePrompt');
  if (!prompt) return;
  const gestures = document.getElementById('ratePromptGestures');
  if (gestures) {
    gestures.innerHTML = ratingGesturesHtml(id, value);
    gestures.querySelectorAll('button').forEach(button => { button.disabled = true; });
  }
  prompt.classList.add('is-done');
  setRatingStatus(prompt, value ? 'Merci ! Vos suggestions en tiennent compte.' : 'Note retirée.');
  clearTimeout(ratePromptTimer);
  ratePromptTimer = setTimeout(closeRatePrompt, 1800);
}

// « Plus tard »: the title stays in « Vos derniers vus, à noter ».
function closeRatePrompt() {
  clearTimeout(ratePromptTimer);
  const prompt = document.getElementById('ratePrompt');
  if (prompt) prompt.hidden = true;
  if (typeof renderHomeRate === 'function') renderHomeRate();
}
