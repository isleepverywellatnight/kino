// Mes statistiques (Bibliothèque → Plus). Loaded after the app parts (app-*.js), whose helpers
// it uses: api, escapeHtml, titleAttrs, displayTitle, GENRE_FR, CLOUD_MONTHS.
'use strict';

// --- Mes statistiques: what the history says, computed on this Mac (watch_stats.py) ---
// « 50 min », « 2 h 50 », « 34 h ».
function statsDuration(seconds) {
  const minutes = Math.round(Number(seconds || 0) / 60);
  if (minutes < 60) return { value: String(minutes), unit: 'min' };
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return { value: hours >= 10 || !rest ? `${hours} h` : `${hours} h ${String(rest).padStart(2, '0')}`, unit: '' };
}

function statsPlural(count, word, plural = `${word}s`) {
  return `${count} ${count > 1 ? plural : word}`;
}

function statsTile(value, unit, label, detail) {
  return `<div class="stats-tile">
      <span class="stats-value">${escapeHtml(value)}${unit ? ` <small>${escapeHtml(unit)}</small>` : ''}</span>
      <span class="stats-label">${escapeHtml(label)}</span>
      ${detail ? `<span class="stats-detail">${escapeHtml(detail)}</span>` : ''}
    </div>`;
}

// One row per item: name, bar against the largest, figure.
function statsMeterList(rows, label) {
  const max = Math.max(1, ...rows.map(row => row.count));
  return `<ul class="stats-meters" aria-label="${escapeHtml(label)}">${rows.map(row => {
    const text = `${row.name} : ${statsPlural(row.count, 'titre')}`;
    return `<li class="stats-meter" title="${escapeHtml(text)}">
        <span class="stats-meter-name">${escapeHtml(row.name)}</span>
        <span class="stats-meter-track" aria-hidden="true"><span style="width:${Math.max(2, Math.round(row.count / max * 100))}%"></span></span>
        <span class="stats-meter-count">${row.count}</span>
      </li>`;
  }).join('')}</ul>`;
}

// Twelve columns, the busiest month labelled; every column says its figure on hover or focus.
function statsMonthsChart(months) {
  const max = Math.max(1, ...months.map(month => month.count));
  const peak = months.reduce((best, month) => (month.count > (best?.count || 0) ? month : best), null);
  const name = key => {
    const [year, month] = key.split('-').map(Number);
    return { short: KINO_I18N.months('narrow')[month - 1], long: `${CLOUD_MONTHS[month - 1].replace('.', '')} ${year}` };
  };
  const columns = months.map(month => {
    const label = name(month.month);
    const text = `${label.long} : ${statsPlural(month.count, 'titre')}`;
    return `<div class="stats-col${month === peak ? ' is-peak' : ''}" tabindex="0" title="${escapeHtml(text)}" aria-label="${escapeHtml(text)}">
        <span class="stats-col-value">${month.count}</span>
        <span class="stats-col-bar" style="height:${month.count ? Math.max(4, Math.round(month.count / max * 100)) : 0}%"></span>
        <span class="stats-col-label" aria-hidden="true">${escapeHtml(label.short)}</span>
      </div>`;
  }).join('');
  const table = `<table class="sr-only"><caption>Titres ouverts par mois</caption><tr><th>Mois</th><th>Titres</th></tr>${
    months.map(month => `<tr><td>${escapeHtml(name(month.month).long)}</td><td>${month.count}</td></tr>`).join('')}</table>`;
  return `<div class="stats-columns">${columns}</div>${table}`;
}

function statsHtml(stats) {
  const films = stats.films || {};
  const series = stats.series || {};
  const seen = Number(stats.titles_seen || 0);
  if (!seen && !films.started && !series.total) {
    return '<div class="library-empty"><span class="library-empty-icon" aria-hidden="true">▷</span><h3>Rien à compter pour l’instant</h3><p>Vos statistiques apparaîtront après vos premiers films et épisodes.</p><button class="btn" onclick="switchTab(\'movies\')">Découvrir les films</button></div>';
  }
  const time = statsDuration(stats.watch_seconds);
  const filmDetail = [films.imported ? `dont ${films.imported} importé${films.imported > 1 ? 's' : ''}` : '', films.started ? `${films.started} en cours` : ''].filter(Boolean).join(' · ');
  const seriesDetail = series.total ? `${series.started} en cours · ${statsPlural(series.finished, 'terminée')}` : '';
  const tiles = [
    statsTile(String(films.seen || 0), '', films.seen > 1 ? 'Films vus' : 'Film vu', filmDetail),
    statsTile(String(stats.episodes?.seen || 0), '', (stats.episodes?.seen || 0) > 1 ? 'Épisodes vus' : 'Épisode vu', series.total ? `dans ${statsPlural(series.total, 'série')}` : ''),
    statsTile(String(series.total || 0), '', (series.total || 0) > 1 ? 'Séries' : 'Série', seriesDetail),
    statsTile(time.value, time.unit, 'De lecture dans KINO', 'positions enregistrées'),
  ].join('');
  const genres = (stats.genres || []).map(genre => ({ name: GENRE_FR[genre.name] || genre.name, count: genre.count }));
  const decades = (stats.decades || []).map(decade => ({ name: `Années ${decade.decade}`, count: decade.count }));
  const oldest = stats.oldest ? `<p class="stats-fact">Le plus ancien film vu : <strong${titleAttrs({ id: stats.oldest.id, name: stats.oldest.name, year: String(stats.oldest.year) }, 'movie')}>${escapeHtml(displayTitle(stats.oldest))}</strong> (${escapeHtml(stats.oldest.year)}).</p>` : '';
  return `
    <div class="stats-tiles">${tiles}</div>
    <div class="stats-grid">
      <section class="stats-card" aria-labelledby="statsGenresTitle">
        <h3 id="statsGenresTitle">Genres préférés</h3>
        <p class="stats-sub">${seen ? `Sur ${statsPlural(stats.genres_known || 0, 'titre')} vu${(stats.genres_known || 0) > 1 ? 's' : ''} dont le genre est connu, sur ${seen}.` : ''}</p>
        ${genres.length ? statsMeterList(genres, 'Genres préférés') : '<p class="stats-empty">Pas encore de genre connu.</p>'}
      </section>
      <section class="stats-card" aria-labelledby="statsMonthsTitle">
        <h3 id="statsMonthsTitle">Titres ouverts par mois</h3>
        <p class="stats-sub">Les 12 derniers mois, d’après la dernière lecture de chaque titre.</p>
        ${statsMonthsChart(stats.months || [])}
      </section>
      <section class="stats-card" aria-labelledby="statsDecadesTitle">
        <h3 id="statsDecadesTitle">Époques des titres vus</h3>
        <p class="stats-sub">Par décennie de sortie.</p>
        ${decades.length ? statsMeterList(decades, 'Époques des titres vus') : '<p class="stats-empty">Pas encore d’année connue.</p>'}
        ${oldest}
      </section>
    </div>
    <p class="stats-note">Calculées sur ce Mac à partir de votre historique, sans rien envoyer. Les films importés comme vus comptent parmi les films vus, pas dans le temps de lecture ni dans les mois.</p>`;
}

async function loadStats() {
  const body = document.getElementById('viewingStatsBody');
  if (!body) return;
  body.innerHTML = '<p class="stats-empty">Calcul de vos statistiques…</p>';
  try {
    const stats = await api('/api/stats');
    if (activeTab === 'stats') body.innerHTML = statsHtml(stats);
  } catch (error) {
    body.innerHTML = `<p class="stats-empty">Statistiques indisponibles : ${escapeHtml(error.message)}</p>`;
  }
}

