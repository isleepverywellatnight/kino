// --- Episode row helpers (kept with renderDetailEpisodes) ---
let detailEpisodeSeason = 0;
const EPISODE_OVERVIEWS_FR = new Map();
const EPISODE_MONTHS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

function episodeAirDate(video) {
  const day = String(video?.released || video?.firstAired || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : '';
}

function episodeIsUpcoming(video, today) {
  const day = episodeAirDate(video);
  return Boolean(day && day > today);
}

// « 12 déc. 2024 ».
function episodeAirLabel(day) {
  const [year, month, date] = String(day || '').split('-').map(Number);
  if (!year || !month || !date) return '';
  return `${date === 1 ? '1er' : date} ${EPISODE_MONTHS[month - 1]} ${year}`;
}

// Where the row opens: {next} is the episode to watch (in progress, or after
// the last one watched), {focus} the episode to show first. When the next
// episode has not aired yet there is nothing « À suivre »: the row shows it.
function episodeToWatch(seasonNum, eps, histItem, today, seriesId) {
  const aired = eps.filter(v => !episodeIsUpcoming(v, today)).map(v => v.episode || v.number || 1);
  const numbers = eps.map(v => v.episode || v.number || 1);
  if (histItem && typeof getSeriesTargetEpisode === 'function') {
    const target = getSeriesTargetEpisode(histItem);
    const episode = Number(target?.episode);
    if (target && !target.finished && Number(target.season) === seasonNum && numbers.includes(episode)) {
      return aired.includes(episode) ? { next: episode, focus: episode } : { next: null, focus: episode };
    }
  }
  const first = aired.find(ep => !Spoilers.watched(userHistory, seriesId, seasonNum, ep)) || null;
  return { next: first, focus: first };
}

// Cinemeta names unannounced episodes « TBA ».
function episodeName(video, epNum) {
  const name = String(video.name || video.title || '').trim();
  return !name || /^(tba|tbd|to be announced|episode \d+|épisode \d+)$/i.test(name) ? Spoilers.neutralTitle(epNum) : name;
}

function episodeTileHtml(v, ctx) {
  const { seasonNum, seriesId, epPosMap, today, fr } = ctx;
  const epNum = v.episode || v.number || 1;
  const code = `S${String(seasonNum).padStart(2, '0')}E${String(epNum).padStart(2, '0')}`;
  const hidden = spoilerConcealed(seriesId, seasonNum, epNum);
  const upcoming = episodeIsUpcoming(v, today);
  const title = hidden ? Spoilers.neutralTitle(epNum) : episodeName(v, epNum);
  const overview = hidden ? '' : (fr[String(epNum)] || v.overview || v.description || '');
  const thumb = hidden || upcoming ? '' : (v.thumbnail || '');
  const isWatched = Spoilers.watched(userHistory, seriesId, seasonNum, epNum);
  const position = epPosMap[code] || epPosMap[`s${seasonNum}e${epNum}`];
  const pct = position ? Number(position.pct || (position.duration ? (position.position / position.duration) * 100 : 0)) : 0;
  const inProgress = !isWatched && pct > 1 && pct < 92;
  const isNext = ctx.target.next === epNum && !upcoming;
  const classes = ['ep-tile', isWatched ? 'is-watched' : '', isNext ? 'is-next' : '', upcoming ? 'is-upcoming' : ''].filter(Boolean).join(' ');
  const badge = upcoming ? 'Inédit' : isNext ? (inProgress ? 'En cours' : 'À suivre') : isWatched ? '✓ Vu' : '';
  const air = episodeAirLabel(episodeAirDate(v));
  const meta = [upcoming ? (air ? `Diffusion le ${air}` : 'À venir') : air, inProgress ? `${Math.round(pct)} % vu` : ''].filter(Boolean).join(' · ');
  const art = hidden
    ? `<span class="spoiler-neutral-art" aria-label="Image masquée pour l'épisode ${epNum}">ÉPISODE ${epNum}</span>`
    : (thumb ? `<img class="ep-tile-img" src="${escapeHtml(thumb)}" alt="" loading="lazy" onerror="this.remove()">` : '') + `<span class="ep-tile-fallback" aria-hidden="true">${epNum}</span>`;
  const inside = `${art}${badge ? `<span class="ep-tile-badge">${escapeHtml(badge)}</span>` : ''}${upcoming ? '' : '<span class="ep-tile-play" aria-hidden="true">▶</span>'}${pct > 0 && !isWatched ? `<span class="ep-tile-progress"><span style="width:${Math.min(100, Math.max(0, pct))}%"></span></span>` : ''}`;
  const action = inProgress ? 'Reprendre' : 'Lancer';
  const head = upcoming
    ? `<div class="ep-tile-art">${inside}</div>`
    : `<button type="button" class="ep-tile-art" onclick="oneClickSeriesEpisode(this, ${seasonNum}, ${epNum})" aria-label="${action} ${code} · ${escapeHtml(title)}">${inside}</button>`;
  return `
    <article class="${classes}" role="listitem" data-ep="${epNum}"${hidden ? ' data-hidden="1"' : ''}${ctx.target.focus === epNum ? ' data-focus="1"' : ''}>
      ${head}
      <div class="ep-tile-body">
        <div class="ep-tile-title"><span class="ep-tile-num">${epNum}.</span> ${escapeHtml(title)}</div>
        ${meta ? `<div class="ep-tile-meta">${escapeHtml(meta)}</div>` : ''}
        ${overview ? `<p class="ep-tile-desc">${escapeHtml(overview)}</p>` : ''}
        <div class="ep-tile-actions">
          ${upcoming ? '' : `<button type="button" class="btn btn-secondary ep-mini" onclick="pickSeriesEpisodeSources(${seasonNum}, ${epNum})">Sources</button>`}
          ${hidden ? `<button type="button" class="btn btn-secondary ep-mini spoiler-reveal-btn" onclick="revealSpoilerEpisode(${seasonNum}, ${epNum})" aria-label="Afficher le titre, le résumé et l'image de l'épisode ${epNum}">Afficher pour cet épisode</button>` : ''}
          ${upcoming ? '' : `<button type="button" class="btn btn-secondary ep-mini ep-watch${isWatched ? ' is-on' : ''}" aria-pressed="${isWatched}" title="${isWatched ? 'Démarquer comme vu' : 'Marquer cet épisode comme vu'}" onclick="toggleWatchedSeriesEp(${seasonNum}, ${epNum})">${isWatched ? '✓ Vu' : '✓ Marquer vu'}</button>`}
        </div>
      </div>
    </article>`;
}

// Bring the episode to watch to the start of the row (no animation).
function revealEpisodeToWatch(container) {
  const tile = container.querySelector?.('.ep-tile[data-focus]');
  if (!tile || typeof container.getBoundingClientRect !== 'function') return;
  const offset = tile.getBoundingClientRect().left - container.getBoundingClientRect().left;
  // 'instant': 'auto' would follow the row's smooth scrolling.
  container.scrollTo?.({ left: Math.max(0, container.scrollLeft + offset - 8), behavior: 'instant' });
}

function updateEpisodeArrows() {
  const row = document.getElementById('detailEpisodesList');
  const strip = document.getElementById('detailEpisodesStrip');
  if (!row || !strip || typeof strip.querySelector !== 'function') return;
  const max = row.scrollWidth - row.clientWidth;
  const prev = strip.querySelector('.ep-scroll-prev');
  const next = strip.querySelector('.ep-scroll-next');
  if (prev) prev.hidden = !(max > 2 && row.scrollLeft > 2);
  if (next) next.hidden = !(max > 2 && row.scrollLeft < max - 2);
}

function scrollEpisodes(direction) {
  const row = document.getElementById('detailEpisodesList');
  if (row) row.scrollBy({ left: direction * Math.max(280, row.clientWidth * 0.8), behavior: 'smooth' });
}

// French summaries arrive once per season; shown episodes are updated in place.
function requestEpisodeOverviews(seriesId, seasonNum) {
  const key = `${seriesId}:${seasonNum}`;
  if (!/^tt\d+$/.test(String(seriesId || '')) || EPISODE_OVERVIEWS_FR.has(key)) return;
  EPISODE_OVERVIEWS_FR.set(key, {});
  api(`/api/episodes-fr?imdb_id=${encodeURIComponent(seriesId)}&season=${encodeURIComponent(seasonNum)}`).then(data => {
    const overviews = (data && data.overviews) || {};
    EPISODE_OVERVIEWS_FR.set(key, overviews);
    if (spoilerSeriesId() !== seriesId || detailEpisodeSeason !== seasonNum) return;
    document.querySelectorAll('#detailEpisodesList .ep-tile:not([data-hidden]) .ep-tile-desc').forEach(desc => {
      const text = overviews[desc.closest('.ep-tile')?.dataset.ep || ''];
      if (text) desc.textContent = text;
    });
  }).catch(() => {});
}

function pickSeriesEpisodeSources(s, e) {
  document.getElementById('seasonSelect').value = String(s);
  onSeasonChange(false);
  document.getElementById('episodeSelect').value = String(e);
  reloadSeriesEpisode(true);
}

function onSeasonChange(triggerReload = true) {
  const s = parseInt(document.getElementById('seasonSelect').value || '1', 10);
  const eSel = document.getElementById('episodeSelect');
  const eps = seriesMetaVideos.filter(v => v.season === s).sort((a,b) => (a.episode || a.number) - (b.episode || b.number));
  if (eps.length) {
    eSel.innerHTML = eps.map(v => {
      const epNum = v.episode || v.number;
      const epTitle = spoilerEpisodeLabel(spoilerSeriesId(), s, epNum, v.name || v.title);
      return `<option value="${epNum}">E${String(epNum).padStart(2,'0')} — ${escapeHtml(epTitle)}</option>`;
    }).join('');
  } else {
    eSel.innerHTML = Array.from({length: 24}, (_, i) => `<option value="${i+1}">Épisode ${i+1}</option>`).join('');
  }
  if (triggerReload) reloadSeriesEpisode(false);
}

function reloadSeriesEpisode(scrollToTorrents = false) {
  if (!currentMedia) return;
  const s = document.getElementById('seasonSelect').value || 1;
  const e = document.getElementById('episodeSelect').value || 1;
  loadTorrents({
    imdb_id: currentMedia.id,
    type: 'series',
    season: s,
    episode: e,
    title: `${currentMedia.name} — S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`
  }, scrollToTorrents);
}

function extractTorrentSizeGb(t) {
  if (t.size_gb !== undefined && t.size_gb !== null && !isNaN(t.size_gb) && Number(t.size_gb) > 0) {
    return Number(t.size_gb);
  }
  const text = (t.meta || '') + ' ' + (t.title || '');
  const mGb = text.match(/(\d+(?:\.\d+)?)\s*(?:GB|GiB)/i);
  if (mGb) return parseFloat(mGb[1]);
  const mMb = text.match(/(\d+(?:\.\d+)?)\s*(?:MB|MiB)/i);
  if (mMb) return parseFloat(mMb[1]) / 1024.0;
  return 0;
}

function extractTorrentSeeders(t) {
  if (t.seeders !== undefined && t.seeders !== null && !isNaN(t.seeders) && Number(t.seeders) > 0) {
    return Number(t.seeders);
  }
  const text = (t.meta || '') + ' ' + (t.title || '');
  const mSeed = text.match(/(\d+)\s*(?:seeders?|seeds?|pairs?)/i) || text.match(/(?:👤|peers?|seeds?|seeders?)\s*(\d+)/i);
  if (mSeed) return parseInt(mSeed[1], 10);
  return 0;
}

async function loadTorrents(params, scroll = true) {
  const requestId = ++torrentRequestId;
  lastReadyDebrid = null;
  sourcesMoreOpen = false;
  const panel = document.getElementById('torrentsPanel');
  const list = document.getElementById('torrentsList');
  panel.style.display = 'block';
  document.getElementById('torrentsHeading').textContent = params.title;
  document.getElementById('torrentsSub').textContent = 'Recherche des sources...';
  list.innerHTML = '<p style="color:var(--dim); font-size:0.84rem;">Chargement...</p>';
  if (scroll) panel.scrollIntoView({behavior: 'smooth'});

  if (!params.runtime && currentMedia && currentMedia.runtime) {
    params.runtime = currentMedia.runtime;
  }
  if (!params.year && currentMedia && (currentMedia.year || currentMedia.releaseInfo)) {
    params.year = currentMedia.year || currentMedia.releaseInfo;
  }
  if (!params.q && currentMedia && currentMedia.name) {
    params.q = currentMedia.name;
  }

  const qs = new URLSearchParams(params).toString();
  try {
    const data = await api('/api/torrents?' + qs);
    if (requestId !== torrentRequestId) return;
    allTorrents = data.torrents || [];
    expandedSourceGroups.clear();
    renderTorrents();
  } catch (e) {
    if (requestId !== torrentRequestId) return;
    list.innerHTML = `<p style="color:var(--muted); font-size:0.84rem;">Erreur : ${escapeHtml(e.message)}</p>`;
  }
}

function setFilter(flt, el) {
  activeFilter = flt;
  const filterChips = [...document.querySelectorAll('.filters .chip')];
  filterChips.forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  syncChipPressed(filterChips, el);
  renderTorrents();
}

function getFilteredTorrents() {
  const custom = document.getElementById('customFilter')?.value.trim().toLowerCase() || '';
  const words = custom ? custom.split(/\s+/).filter(Boolean) : [];
  const isMovie = !currentMedia || currentMedia.type !== 'series';
  const rm = currentMedia && currentMedia.runtime ? parseInt(currentMedia.runtime, 10) : 0;

  let filtered = allTorrents.filter(t => {
    const quals = t.qualities || [];
    const hay = (t.title + ' ' + t.meta + ' ' + t.source + ' ' + quals.join(' ') + ' ' + (t.langs || []).join(' ')).toLowerCase();

    // Filtre des torrents frauduleux / CAM réencodés trop légers pour leurs caractéristiques
    const size = extractTorrentSizeGb(t);
    const is4k = quals.includes('4K') || /\b(2160p|4k|uhd)\b/i.test(t.title);
    const is1080 = quals.includes('1080p') || /\b(1080p|fhd)\b/i.test(t.title);

    if (/\b(CAM|HDCAM|CAMRIP|TS|HDTS|TELESYNC|TELECINE|SCR|SCREENER|DVDSCREENER|WP|WORKPRINT)\b/i.test(t.title)) {
      return false;
    }

    if (isMovie && size > 0) {
      let min4k = 4.8;
      if (rm > 70) min4k = Math.max(4.8, (rm * 60 * 6.0) / (8 * 1024));
      if (is4k && size < min4k) return false;

      let min1080 = 0.75;
      if (rm > 70) min1080 = Math.max(0.75, (rm * 60 * 1.5) / (8 * 1024));
      if (is1080 && size < min1080) return false;

      if (size < 0.15) return false;
    } else if (!isMovie && size > 0) {
      if (is4k && size < 1.3) return false;
      if (is1080 && size < 0.22) return false;
      if (size < 0.08) return false;
    }

    if (activeFilter === 'fr') {
      // Server-parsed languages already exclude subtitle-only cues such as "Multi Subs".
      const frenchLangs = ['MULTI', 'VFF', 'VF', 'VFQ', 'FR', 'VOSTFR'];
      if (!(t.langs || []).some(l => frenchLangs.includes(String(l).toUpperCase()))) return false;
    } else if (activeFilter === 'sdr') {
      if (quals.includes('HDR') || quals.includes('DV') || /\b(hdr|hdr10|dv|dovi|dolby[\s\.\-]*vision)\b/i.test(t.title || '')) return false;
    } else if (activeFilter === '4k') {
      if (!quals.includes('4K') && !/(2160p|4k|uhd)/i.test(hay)) return false;
    } else if (activeFilter === '1080p') {
      if (!quals.includes('1080p') && !/(1080p|fhd)/i.test(hay)) return false;
    } else if (activeFilter && !hay.includes(activeFilter)) {
      return false;
    }
    if (words.length && !words.every(w => hay.includes(w))) return false;
    return true;
  });

  const sortMode = document.getElementById('torrentSortSelect')?.value || 'default';
  if (sortMode === 'seeds') {
    filtered.sort((a, b) => extractTorrentSeeders(b) - extractTorrentSeeders(a));
  } else if (sortMode === 'size_desc') {
    filtered.sort((a, b) => extractTorrentSizeGb(b) - extractTorrentSizeGb(a));
  } else if (sortMode === 'size_asc') {
    filtered.sort((a, b) => {
      const sa = extractTorrentSizeGb(a);
      const sb = extractTorrentSizeGb(b);
      if (!sa && !sb) return 0;
      if (!sa) return 1;
      if (!sb) return -1;
      return sa - sb;
    });
  }

  return filtered;
}

function scoreTorrent(t) {
  const score = t && t.score != null ? Number(t.score) : NaN;
  return Number.isFinite(score) ? score : Number.NEGATIVE_INFINITY;
}

// --- Sources: the best one first, then one group per quality; the same file
// found on several sites shows once. Rows keep their index in the filtered
// list: « Lancer » tries that source, then the following ones. ---
const SOURCE_GROUP_SIZE = 4;
const SOURCE_GROUP_MAX = 100;
const SOURCE_GROUPS = [['4k', '4K'], ['1080p', '1080p'], ['720p', '720p'], ['other', 'Autres qualités']];
const expandedSourceGroups = new Set();
// The other sources are folded under the recommended one.
let sourcesMoreOpen = false;

function sourceResolution(t) {
  const resolution = String(t?.parsed_details?.resolution || '').toLowerCase();
  const qualities = (t?.qualities || []).map(q => String(q).replace(/\+$/, '').toLowerCase());
  const has = value => resolution === value || qualities.includes(value);
  if (has('4k') || has('2160p')) return '4k';
  if (has('1080p')) return '1080p';
  if (has('720p')) return '720p';
  return 'other';
}

function sourceKey(t) {
  return `${String(t?.title || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()}|${Number(t?.size_gb || 0).toFixed(1)}`;
}

// « WEB-DL · HEVC · HDR10 · DDP », from what the source announces.
function sourceDetails(t) {
  const parsed = t?.parsed_details || {};
  const hdr = parsed.hdr_type && parsed.hdr_type !== 'SDR' ? parsed.hdr_type : '';
  return [parsed.source, parsed.codec, hdr, ...(Array.isArray(parsed.audio_formats) ? parsed.audio_formats : [])]
    .filter(Boolean).map(String).join(' · ');
}

// The filtered list as rows: the best source first, each file once.
function sourceRows(filtered, bestIdx) {
  const rows = [];
  const byKey = new Map();
  const add = (t, idx) => {
    const key = sourceKey(t);
    const row = byKey.get(key);
    if (row) {
      row.copies++;
      return;
    }
    const entry = { t, idx, copies: 1 };
    byKey.set(key, entry);
    rows.push(entry);
  };
  if (bestIdx >= 0) add(filtered[bestIdx], bestIdx);
  filtered.forEach((t, idx) => { if (idx !== bestIdx) add(t, idx); });
  return rows;
}

function toggleSourceGroup(key) {
  if (expandedSourceGroups.has(key)) expandedSourceGroups.delete(key);
  else expandedSourceGroups.add(key);
  renderTorrents();
}

// --- Pourquoi cette source : the score's own reasons, in words (docs/playback.md) ---
const SOURCE_PART_LABELS = {
  instant_points: 'disponibilité', quality_points: 'qualité', language_points: 'langue', picture_points: 'image',
  sound_points: 'son', seeder_points: 'partage', fake_penalty: 'fiabilité', size_points: 'taille',
  anime_group_points: 'équipe', year_points: 'année', priority_points: 'qualité choisie d’abord',
};

function sourceWhy(t) {
  return Array.isArray(t?.why) ? t.why.filter(reason => reason && typeof reason.text === 'string' && reason.text) : [];
}

// The reasons of one tone, the weightiest first (the worst first for « bad »).
function sourceReasons(t, tone) {
  const points = reason => Number(reason.points) || 0;
  return sourceWhy(t).filter(reason => reason.tone === tone)
    .sort((a, b) => (tone === 'bad' ? points(a) - points(b) : points(b) - points(a)))
    .map(reason => reason.text);
}

// Where a source loses or gains points against the recommended one, biggest gap first.
function sourceComparison(t, best) {
  const mine = t?.score_parts || {};
  const theirs = best?.score_parts || {};
  const gaps = Object.keys(SOURCE_PART_LABELS)
    .map(part => [part, (Number(theirs[part]) || 0) - (Number(mine[part]) || 0)])
    .filter(([, gap]) => gap !== 0);
  const labels = list => list.map(([part]) => SOURCE_PART_LABELS[part]);
  return {
    behind: labels(gaps.filter(([, gap]) => gap > 0).sort((a, b) => b[1] - a[1])).slice(0, 3),
    ahead: labels(gaps.filter(([, gap]) => gap < 0).sort((a, b) => a[1] - b[1])).slice(0, 3),
  };
}

function sourceWhyHtml(t, best) {
  if (!sourceWhy(t).length) return '';
  const line = (label, items) => (items.length ? `<p><strong>${label} :</strong> ${items.map(escapeHtml).join(' · ')}</p>` : '');
  let compared = '';
  if (best && best !== t && best.score_parts && t.score_parts) {
    const { behind, ahead } = sourceComparison(t, best);
    if (behind.length) compared = `<p><strong>Moins bien que la recommandée :</strong> ${behind.join(', ')}${ahead.length ? ` · mieux : ${ahead.join(', ')}` : ''}</p>`;
  }
  return `<details class="source-why"><summary>Pourquoi ce rang ?</summary>${line('Points forts', sourceReasons(t, 'good'))}`
    + `${line('À noter', [...sourceReasons(t, 'bad'), ...sourceReasons(t, 'neutral')])}${compared}</details>`;
}

function sourceRowHtml(row, isBest, sourceHidden, sourceEpisode, best = null) {
  const { t, idx, copies } = row;
  const recBadge = isBest ? '<span class="badge badge-recommended">Recommandé</span>' : '';
  const isCached = typeof t.is_instant === 'boolean' ? t.is_instant : Boolean((t.source && t.source.includes('+')) || (t.qualities && t.qualities.some(q => q.endsWith('+'))));
  const cachedBadge = isCached ? '<span class="badge badge-cached" title="Disponibilité immédiate signalée par la source">Disponible</span>' : '';
  const recommendation = t.recommendation || {};
  const reasons = isBest && Array.isArray(recommendation.reasons) ? recommendation.reasons : [];
  const warnings = Array.isArray(recommendation.warnings) ? recommendation.warnings : [];
  // The recommended source says what put it first; older answers keep their announced signals.
  const strengths = isBest ? sourceReasons(t, 'good').slice(0, 4) : [];
  const explanation = sourceHidden ? ''
    : strengths.length ? `<div class="torrent-explanation">Pourquoi celle-ci : ${strengths.map(escapeHtml).join(' · ')}</div>`
      : reasons.length ? `<div class="torrent-explanation">Pourquoi : ${reasons.map(escapeHtml).join(' · ')}</div>` : '';
  const warning = !sourceHidden && warnings.length ? `<div class="torrent-warning">À vérifier : ${warnings.map(escapeHtml).join(' · ')}</div>` : '';
  const why = sourceHidden ? '' : sourceWhyHtml(t, best?.t);

  const qualBadges = (t.qualities || []).map(q => {
    const qClean = String(q).replace(/\+$/, '');
    let cls = 'badge';
    let tip = '';
    if (qClean === '4K' || qClean === '2160p') {
      cls = 'badge badge-res-4k';
      tip = ' title="Ultra Haute Définition 4K"';
    } else if (qClean === '1080p') {
      cls = 'badge badge-res-1080p';
      tip = ' title="Haute Définition 1080p"';
    } else if (qClean === 'DV' || qClean === 'Dolby Vision') {
      cls = 'badge badge-codec-dv';
      tip = ' title="Dolby Vision (Tone-Mapping anti-noirs bouchés actif)"';
    } else if (qClean === 'HDR' || qClean === 'HDR10' || qClean === 'HDR10+') {
      cls = 'badge badge-codec-hdr';
      tip = ' title="High Dynamic Range (HDR10)"';
    } else if (qClean === 'REMUX') {
      cls = 'badge badge-codec-remux';
      tip = ' title="Qualité Blu-Ray intégrale sans recompression"';
    } else if (['Atmos', 'DTS', 'DTS-HD', 'TrueHD', '5.1', '7.1'].includes(qClean)) {
      cls = 'badge badge-codec-audio';
      tip = ` title="Audio spatial / multicanal ${escapeHtml(qClean)}"`;
    }
    return `<span class="${cls}"${tip}>${escapeHtml(qClean)}</span>`;
  }).join('');

  const langBadges = (t.langs || []).map(l => {
    const isFr = ['MULTI', 'TRUEFRENCH', 'VFF', 'VF', 'VOSTFR'].includes(String(l).toUpperCase());
    return `<span class="badge ${isFr ? 'badge-lang-fr' : 'badge-hi'}">${escapeHtml(l)}</span>`;
  }).join('');

  const seeders = Number(t.seeders || 0);
  const facts = [t.source, t.size_str, sourceDetails(t), seeders > 0 ? `${seeders} pair${seeders > 1 ? 's' : ''}` : '',
    copies > 1 ? `trouvé ${copies} fois` : ''].filter(Boolean);
  // A concealed episode never shows the source's own description (it may name the episode).
  const meta = sourceHidden || facts.length > 2 || !t.meta ? facts.map(escapeHtml).join(' • ') : escapeHtml(t.meta);

  return `
      <div class="torrent-item ${isBest ? 'is-recommended' : ''}">
        <div class="torrent-main">
          <div class="torrent-title">
            ${recBadge}${cachedBadge}${qualBadges}${langBadges}<span class="torrent-name" title="${sourceHidden ? '' : escapeHtml(t.title)}">${sourceHidden ? Spoilers.neutralTitle(sourceEpisode) : escapeHtml(t.title)}</span>
          </div>
          <div class="torrent-meta">${meta}</div>
          ${explanation}${warning}${why}
        </div>
        <div class="torrent-actions">
          ${t.magnet ? `<button class="btn btn-secondary btn-icon" onclick="copyTorrentMagnet(event, this, ${idx})" title="Copier le lien Magnet" aria-label="Copier le lien Magnet"><svg class="ui-icon ui-icon-sm" aria-hidden="true"><use href="/icons.svg#link"></use></svg></button>` : ''}
          ${isBest
            ? `<button class="btn btn-play-hero" style="padding:7px 16px !important; font-size:0.82rem !important;" onclick="debridFromIndex(${idx})"><svg class="ui-icon ui-icon-xs" aria-hidden="true"><use href="/icons.svg#play-solid"></use></svg> Lancer</button>`
            : `<button class="btn btn-secondary" style="padding:6px 12px; font-size:0.78rem;" onclick="debridFromIndex(${idx})">Sélectionner</button>`
          }
        </div>
      </div>`;
}

function renderTorrents() {
  const list = document.getElementById('torrentsList');
  const filtered = getFilteredTorrents();
  const sourceSeason = Number(document.getElementById('seasonSelect')?.value || 0);
  const sourceEpisode = Number(document.getElementById('episodeSelect')?.value || 0);
  const sourceHidden = currentMedia?.type === 'series' && spoilerConcealed(spoilerSeriesId(), sourceSeason, sourceEpisode);
  const revealSource = sourceHidden ? `<button class="btn btn-secondary spoiler-reveal-btn" style="margin-bottom:10px" onclick="revealSpoilerEpisode(${sourceSeason}, ${sourceEpisode})">Afficher pour cet épisode</button>` : '';

  let bestIdx = -1;
  let maxScore = -1;
  filtered.forEach((t, i) => {
    const sc = scoreTorrent(t);
    if (sc > maxScore) {
      maxScore = sc;
      bestIdx = i;
    }
  });
  if (maxScore < 120) bestIdx = -1;
  const rows = sourceRows(filtered, bestIdx);

  const subEl = document.getElementById('torrentsSub');
  if (subEl) {
    const versions = `${rows.length} version${rows.length > 1 ? 's' : ''}`;
    const sources = `${filtered.length} source${filtered.length > 1 ? 's' : ''}${filtered.length !== allTorrents.length ? ` (sur ${allTorrents.length})` : ''}`;
    subEl.textContent = rows.length === filtered.length ? sources : `${versions} · ${sources}`;
  }

  if (!filtered.length) {
    list.innerHTML = revealSource + '<p style="color:var(--dim); font-size:0.84rem;">Aucun résultat pour ce filtre.</p>';
    return;
  }

  const best = bestIdx >= 0 ? rows[0] : null;
  const rest = best ? rows.slice(1) : rows;
  const groups = SOURCE_GROUPS.map(([key, label]) => ({ key, label, rows: rest.filter(row => sourceResolution(row.t) === key) }))
    .filter(group => group.rows.length);
  const groupsHtml = groups.map(group => {
      const open = expandedSourceGroups.has(group.key);
      const shown = group.rows.slice(0, open ? SOURCE_GROUP_MAX : SOURCE_GROUP_SIZE);
      const hidden = group.rows.length - shown.length;
      const where = group.key === 'other' ? '' : ` en ${group.label}`;
      const toggle = !open && hidden > 0
        ? `<button type="button" class="btn btn-secondary source-more" onclick="toggleSourceGroup('${group.key}')" aria-expanded="false">Voir ${hidden} autre${hidden > 1 ? 's' : ''}${where}</button>`
        : (open && group.rows.length > SOURCE_GROUP_SIZE ? `<button type="button" class="btn btn-secondary source-more" onclick="toggleSourceGroup('${group.key}')" aria-expanded="true">Réduire</button>` : '');
      return `<section class="source-group" aria-label="${group.label}">
        <h4 class="source-group-title">${group.label} <span class="list-count">${group.rows.length}</span></h4>
        ${shown.map(row => sourceRowHtml(row, false, sourceHidden, sourceEpisode, best)).join('')}
        ${toggle}
      </section>`;
    }).join('');
  // With a recommended source, the others wait behind « Choisir une autre source ».
  const others = rest.length;
  const folded = best && others
    ? `<details class="sources-more"${sourcesMoreOpen ? ' open' : ''} ontoggle="sourcesMoreOpen = this.open"><summary>Choisir une autre source <span class="list-count">${others}</span></summary>${groupsHtml}</details>`
    : groupsHtml;
  list.innerHTML = revealSource
    + (best ? `<div class="source-best">${sourceRowHtml(best, true, sourceHidden, sourceEpisode)}</div>` : '')
    + folded;
}

async function copyTorrentMagnet(ev, btnEl, idx) {
  if (ev) ev.stopPropagation();
  const filtered = getFilteredTorrents();
  const item = filtered[idx];
  if (!item || !item.magnet) return;
  try {
    await navigator.clipboard.writeText(item.magnet);
    if (btnEl) {
      const orig = btnEl.textContent;
      btnEl.textContent = '✓ Copié';
      setTimeout(() => { btnEl.textContent = orig; }, 1400);
    }
  } catch (e) {}
}

async function debridFromIndex(startIdx) {
  const filtered = getFilteredTorrents();
  const candidates = filtered.slice(startIdx);
  await debridCandidates(candidates);
}

async function debridMagnet(magnet) {
  await debridCandidates([{title: 'Magnet direct', magnet}]);
}

async function debridCandidates(candidates) {
  const box = document.getElementById('debridResultPanel');
  box.style.display = 'block';
  box.scrollIntoView({behavior: 'smooth'});

  const season = currentMedia && currentMedia.type === 'series' ? document.getElementById('seasonSelect').value : null;
  const episode = currentMedia && currentMedia.type === 'series' ? document.getElementById('episodeSelect').value : null;

  for (let i = 0; i < candidates.length; i++) {
    const item = candidates[i];
    box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Débridage en cours (${i + 1}/${candidates.length}) : <strong style="color:var(--text);">${escapeHtml(item.title || 'Magnet')}</strong>...</p>`;
    try {
      const res = await api('/api/debrid', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({magnet: item.magnet, resolve_url: item.resolve_url || '', season, episode})
      });
      renderDebridState(res, season, episode);
      return;
    } catch (e) {
      if (i + 1 < candidates.length && (e.message.includes('451') || e.message.includes('infringing_file') || e.message.includes('refusé'))) {
        box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Source #${i + 1} indisponible (451), passage à la suivante...</p>`;
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${escapeHtml(e.message)}</p>`;
      return;
    }
  }
}

function renderDebridState(res, season, episode, chosenTorrentTitle = '', playlistCount = 1) {
  const box = document.getElementById('debridResultPanel');
  if (!res.ready) {
    box.innerHTML = `
      <h3>Mise en cache Real-Debrid (${res.progress}%)</h3>
      <p style="color:var(--muted); font-size:0.82rem; margin-top:4px;">Statut : ${res.status} • Vitesse : ${res.speed} • Pairs : ${res.seeders || 0}</p>
      <div class="progress-bar"><div class="progress-fill" style="width:${res.progress}%"></div></div>
    `;
    setTimeout(async () => {
      try {
        const next = await api('/api/debrid-status', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({torrent_id: res.torrent_id, season, episode})
        });
        renderDebridState(next, season, episode, chosenTorrentTitle, playlistCount);
      } catch (e) {
        box.innerHTML = `<p style="color:var(--muted); font-size:0.85rem;">Erreur : ${escapeHtml(e.message)}</p>`;
      }
    }, 2500);
    return;
  }

  window.lastDebridFiles = res.files || [];
  lastReadyDebrid = [res, season, episode, chosenTorrentTitle, playlistCount];
  const selectedHidden = currentMedia?.type === 'series' && spoilerConcealed(spoilerSeriesId(), Number(season), Number(episode));

  const subBanner = chosenTorrentTitle
    ? `<p style="color:var(--muted); font-size:0.8rem; margin-bottom:6px;">Lecture : ${selectedHidden ? Spoilers.neutralTitle(episode) : escapeHtml(chosenTorrentTitle)}</p>`
    : '';
  const playlistNote = (playlistCount > 1 || (currentMedia && currentMedia.type === 'series'))
    ? `<p style="color:var(--dim); font-size:0.77rem; margin-bottom:10px;">Playlist active${playlistCount > 1 ? ` (${playlistCount} épisodes)` : ''} — Touche <code style="color:var(--text); background:var(--surface-2); padding:1px 5px; border-radius:3px; border:1px solid var(--border);">&gt;</code> pour l'épisode suivant</p>`
    : '';

  box.innerHTML = `
    <h3 style="margin-bottom:6px;">Fichier prêt</h3>
    ${subBanner}
    ${playlistNote}
    <div style="display:flex; flex-direction:column; gap:8px;">
      ${res.files.map((f, index) => {
        const parsed = Spoilers.episodeNumber(f.filename);
        const fileSeason = Number(parsed?.season || (f.is_target_ep ? season : 0));
        const fileEpisode = Number(parsed?.episode || (f.is_target_ep ? episode : 0));
        const hidden = currentMedia?.type === 'series' && spoilerConcealed(spoilerSeriesId(), fileSeason, fileEpisode);
        return `
        <div class="torrent-item" style="${f.is_target_ep ? 'border-color: var(--border-hover);' : ''}">
          <div style="flex:1; min-width:240px;">
            <div class="torrent-title">
              ${f.is_target_ep ? '<span class="badge badge-hi">Épisode</span>' : ''}
              ${hidden ? Spoilers.neutralTitle(fileEpisode) : escapeHtml(f.filename)}
            </div>
            <div class="torrent-meta">${escapeHtml(f.filesize || '')}</div>
          </div>
          <div style="display:flex; gap:6px; flex-wrap:wrap;">
            ${hidden && fileEpisode ? `<button class="btn btn-secondary spoiler-reveal-btn" onclick="revealSpoilerEpisode(${fileSeason}, ${fileEpisode})">Afficher pour cet épisode</button>` : ''}
            <button class="btn" onclick="openDebridFile(this, ${index})">Lancer</button>
            <button class="btn btn-secondary" onclick="sendDebridFileToTv(this, ${index})" title="Lire ce fichier sur la TV"><svg class="ui-icon ui-icon-xs" aria-hidden="true"><use href="/icons.svg#cast"></use></svg> Sur la TV</button>
            <button class="btn btn-secondary" onclick="downloadDebridFile(${index})">Télécharger</button>
            <button class="btn btn-secondary" onclick="openDebridLink(${index})">Lien direct</button>
          </div>
        </div>
      `; }).join('')}
    </div>
    <p id="debridTvStatus" class="debrid-tv-status" role="status" aria-live="polite"></p>
  `;
}

// Where this title (or episode) was left, to start there.
function savedPosition(media, season, episode) {
  const entry = media ? (userHistory || []).find(h => h.id === media.id) : null;
  if (!entry) return 0;
  if (season && episode && entry.ep_positions) return entry.ep_positions[`s${season}e${episode}`]?.position || 0;
  return entry.position || 0;
}

// « Sur la TV » for one file of the chosen source.
async function sendDebridFileToTv(button, index) {
  const file = window.lastDebridFiles?.[index];
  if (!file?.download) return;
  const [, chosenSeason, chosenEpisode] = lastReadyDebrid || [];
  const isSeries = currentMedia?.type === 'series' || currentMedia?.type === 'anime';
  const parsed = isSeries ? Spoilers.episodeNumber(file.filename) : null;
  const season = isSeries ? Number(parsed?.season || (file.is_target_ep ? chosenSeason : 0) || 0) : 0;
  const episode = isSeries ? Number(parsed?.episode || (file.is_target_ep ? chosenEpisode : 0) || 0) : 0;
  const media = currentMedia ? tvMediaPayload(currentMedia, season, episode) : null;
  const status = document.getElementById('debridTvStatus');
  const label = button.innerHTML;
  button.disabled = true;
  button.textContent = 'Envoi…';
  try {
    await castToTv({ stream_url: file.download, title: tvTitle(media, file.filename), filename: file.filename, media,
      resume_sec: savedPosition(currentMedia, season, episode) });
    if (status) status.textContent = '';
  } catch (error) {
    if (status) status.textContent = error.message || 'La TV ne répond pas.';
  } finally {
    button.disabled = false;
    button.innerHTML = label;
  }
}

function openDebridFile(button, index) {
  const file = window.lastDebridFiles?.[index];
  if (file) openMpv(button, file.download, file.filename);
}

function downloadDebridFile(index) {
  const file = window.lastDebridFiles?.[index];
  if (file) startPcDownload(file.download, file.filename, downloadMediaContext());
}

function openDebridLink(index) {
  const file = window.lastDebridFiles?.[index];
  if (file?.download) window.open(file.download, '_blank', 'noopener');
}

function downloadMediaContext(media = currentMedia) {
  if (!media) return {};
  const context = {
    id: media.id || '', name: media.name || '', type: media.type || 'movie',
    year: media.year || '', poster: media.poster || '', runtime: media.runtime || ''
  };
  if (context.type === 'series' || context.type === 'anime') {
    context.season = media.season || Number(document.getElementById('seasonSelect')?.value) || null;
    context.episode = media.episode || Number(document.getElementById('episodeSelect')?.value) || null;
  }
  return context;
}

async function startPcDownload(url, filename, media = {}, subtitles = null) {
  try {
    const result = await api('/api/download', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, filename, media, subtitles})
    });
    await pollDownloads();
    if (document.getElementById('inAppPlayerOverlay')?.classList.contains('active')) {
      showInAppToast(result.duplicate ? 'Ce fichier est déjà dans la bibliothèque hors ligne.' : 'Téléchargement ajouté à la bibliothèque hors ligne.', 3500);
    }
    return result;
  } catch (error) {
    if (document.getElementById('inAppPlayerOverlay')?.classList.contains('active')) showInAppToast(escapeHtml(error.message), 5000);
    else window.alert(error.message);
    return null;
  }
}

async function saveCurrentPlaybackOffline() {
  if (!/^https?:\/\//i.test(inAppCurrentUrl) || inAppCurrentMedia?.offline_id) {
    showInAppToast('Cette lecture est déjà locale.', 3000);
    return;
  }
  const filename = inAppCurrentFilename || inAppCurrentTitle;
  if (!/\.(mkv|mp4|avi|m4v|mov|ts|wmv|webm)$/i.test(filename)) {
    showInAppToast('Nom du fichier inconnu. Téléchargez-le depuis la liste des sources.', 5000);
    return;
  }
  let subtitles = null;
  const selected = inAppAvailableSubs[inAppActiveSubIdx];
  if (selected && selected.lang !== 'off') {
    let cues = inAppActiveCues.length ? inAppActiveCues : inAppSubCuesCache[selected.url];
    if (!cues?.length && selected.url) {
      try {
        const data = await api(`/api/subtitle-cues?url=${encodeURIComponent(selected.url)}`);
        cues = data.cues;
      } catch (error) {}
    }
    if (!cues?.length) {
      showInAppToast('Les sous-titres choisis ne sont pas encore disponibles. Réessayez dans un instant.', 5000);
      return;
    }
    subtitles = {label: selected.label, lang: selected.lang, kind: selected.kind,
      variant: selected.variant, cues};
  }
  const button = document.getElementById('inAppSaveOfflineBtn');
  if (button) button.disabled = true;
  try {
    await startPcDownload(inAppCurrentUrl, filename, downloadMediaContext(inAppCurrentMedia), subtitles);
  } finally {
    if (button) button.disabled = false;
  }
}

// From a fiche, the title and episode come from it; the Cloud passes its own
// (options: {media, season, episode, files: [{filename, download}]}).
async function openMpv(btn, url, filename, options = null) {
  if (_isGlobalPlayLaunching) return;
  _isGlobalPlayLaunching = true;
  setTimeout(() => { _isGlobalPlayLaunching = false; }, 3000);

  if (btn && btn.disabled) return;
  const origText = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = 'Play...';
  }
  const media = options ? (options.media || null) : currentMedia;
  const fromFiche = !options && currentMedia && currentMedia.type === 'series';
  const s = options ? (options.season ?? null) : (fromFiche ? document.getElementById('seasonSelect')?.value : null);
  const e = options ? (options.episode ?? null) : (fromFiche ? document.getElementById('episodeSelect')?.value : null);
  let packFiles = options?.files?.length > 1 ? options.files : null;
  if (!options && window.lastDebridFiles && window.lastDebridFiles.length > 1 && window.lastDebridFiles.some(f => f.download === url)) {
    const sorted = [...window.lastDebridFiles].sort((a, b) => (a.filename || '').localeCompare(b.filename || ''));
    const idx = sorted.findIndex(f => f.download === url);
    if (idx >= 0) packFiles = sorted.slice(idx);
  }

  const isMacPlatform = /Mac/i.test(navigator.platform || navigator.userAgent);
  const isIntegrated = window.kinoPlayerMode === 'integrated' || (window.kinoPlayerMode !== 'external' && isMacPlatform);
  if (isIntegrated) {
    const pl = (packFiles && packFiles.length > 1)
      ? packFiles.map(p => ({ title: p.filename, url: p.download }))
      : [{ title: filename, url: url }];
    const histItem = media ? userHistory.find(h => h.id === media.id) : null;
    let resumeSec = 0;
    if (histItem) {
      if (s && e && histItem.ep_positions) {
        resumeSec = histItem.ep_positions[`s${s}e${e}`]?.position || 0;
      } else {
        resumeSec = histItem.position || 0;
      }
    }
    const openWeb = info => (info && !info.first
      ? openInAppPlayer(info.url, info.title, pl, info.media, info.position || 0)
      : openInAppPlayer(url, filename, pl, media, info?.position || resumeSec));
    const nativeMedia = media ? { ...media, ...(s && e ? { season: Number(s), episode: Number(e) } : {}) } : null;
    const native = await openNativeStream({ url, title: filename, media: nativeMedia, resumeSec, filename, playlist: pl, fallback: openWeb });
    if (!native) openWeb(null);
    if (btn) btn.innerHTML = 'Lancé';
    refreshUserLists();
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origText;
      }, 2500);
    }
    return;
  }


  try {
    await api('/api/mpv', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        url,
        filename,
        pack_files: packFiles,
        media: media ? {
          id: media.id,
          name: media.name,
          type: media.type,
          year: media.year || '',
          poster: media.poster || '',
          season: s,
          episode: e
        } : null
      })
    });
    if (btn) btn.innerHTML = 'Lancé';
    refreshUserLists();
  } catch (err) {
    alert(err.message);
  } finally {
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origText;
      }, 2500);
    }
  }
}

(function initMacNativeWindow() {
  const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);
  const hasWkBridge = Boolean(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.jsBridge);
  if (isMac && (hasWkBridge || window.pywebview)) {
    document.body.classList.add('is-mac-native');
  }
  window.addEventListener('pywebviewready', () => {
    if (isMac) document.body.classList.add('is-mac-native');
  });

  document.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (!document.body.classList.contains('is-mac-native')) return;
    const dragRegion = e.target.closest('.pywebview-drag-region');
    if (!dragRegion) return;
    if (e.target.closest('.no-drag, button, input, select, textarea, a, label')) return;
    try {
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.jsBridge) {
        window.webkit.messageHandlers.jsBridge.postMessage(JSON.stringify({
          funcName: 'start_window_drag',
          params: [],
          id: 'mac_drag'
        }));
        return;
      }
      if (window.pywebview && window.pywebview.api && window.pywebview.api.start_window_drag) {
        window.pywebview.api.start_window_drag();
      }
    } catch (err) {}
  });
})();

