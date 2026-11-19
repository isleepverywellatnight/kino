// @ts-check
// Mes statistiques (Bibliothèque → Plus). Loaded after the app parts (app-*.js), whose helpers
// it uses: api, escapeHtml, titleAttrs, displayTitle, GENRE_FR, CLOUD_MONTHS.

import { act, html, render, tHtml } from './html.js';
import { activeTab, api } from './app-core.js';
import { CLOUD_MONTHS } from './app-cloud-settings.js';
import { GENRE_FR, displayTitle, titleAttrs } from './app-fiche.js';

'use strict';

// --- Mes statistiques: what the history says, computed on this Mac (watch_stats.py) ---
// « 50 min », « 2 h 50 », « 34 h ».
function statsDuration(seconds) {
  const minutes = Math.round(Number(seconds || 0) / 60);
  if (minutes < 60) return { value: String(minutes), unit: 'min' };
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return { value: hours >= 10 || !rest ? `${KINO_I18N.t('anime.x_h', hours)}` : `${KINO_I18N.t('anime.x_h_x', hours, String(rest).padStart(2, '0'))}`, unit: '' };
}

function statsPlural(count, word, plural = `${word}s`) {
  return `${count} ${count > 1 ? plural : word}`;
}

function statsTile(value, unit, label, detail) {
  return html`<div class="stats-tile">
      <span class="stats-value">${value}${unit ? html` <small>${unit}</small>` : ''}</span>
      <span class="stats-label">${label}</span>
      ${detail ? html`<span class="stats-detail">${detail}</span>` : ''}
    </div>`;
}

// One row per item: name, bar against the largest, figure.
function statsMeterList(rows, label) {
  const max = Math.max(1, ...rows.map(row => row.count));
  return html`<ul class="stats-meters" aria-label="${label}">${rows.map(row => {
    const text = `${row.name} : ${statsPlural(row.count, 'titre')}`;
    return html`<li class="stats-meter" title="${text}">
        <span class="stats-meter-name">${row.name}</span>
        <span class="stats-meter-track" aria-hidden="true"><span style="width:${Math.max(2, Math.round(row.count / max * 100))}%"></span></span>
        <span class="stats-meter-count">${row.count}</span>
      </li>`;
  })}</ul>`;
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
    return html`<div class="stats-col${month === peak ? ' is-peak' : ''}" tabindex="0" title="${text}" aria-label="${text}">
        <span class="stats-col-value">${month.count}</span>
        <span class="stats-col-bar" style="height:${month.count ? Math.max(4, Math.round(month.count / max * 100)) : 0}%"></span>
        <span class="stats-col-label" aria-hidden="true">${label.short}</span>
      </div>`;
  });
  const table = html`<table class="sr-only"><caption>${KINO_I18N.t('stats.titres_ouverts_par_mois')}</caption><tr><th>${KINO_I18N.t('stats.mois')}</th><th>${KINO_I18N.t('fiche.titres')}</th></tr>${
    months.map(month => html`<tr><td>${name(month.month).long}</td><td>${month.count}</td></tr>`)}</table>`;
  return html`<div class="stats-columns">${columns}</div>${table}`;
}

function statsHtml(stats) {
  const films = stats.films || {};
  const series = stats.series || {};
  const seen = Number(stats.titles_seen || 0);
  if (!seen && !films.started && !series.total) {
    return html`<div class="library-empty"><span class="library-empty-icon" aria-hidden="true">▷</span><h3>${KINO_I18N.t('stats.rien_a_compter_pour_l_instant')}</h3><p>${KINO_I18N.t('stats.vos_statistiques_apparaitront_apres_vos')}</p><button class="btn" ${act('switchTab', 'movies')}>${KINO_I18N.t('home.decouvrir_les_films')}</button></div>`;
  }
  const time = statsDuration(stats.watch_seconds);
  const filmDetail = [films.imported ? `${KINO_I18N.t('stats.dont_x_importe_x', films.imported, films.imported > 1 ? 's' : '')}` : '', films.started ? `${KINO_I18N.t('stats.x_en_cours', films.started)}` : ''].filter(Boolean).join(' · ');
  const seriesDetail = series.total ? `${KINO_I18N.t('stats.x_en_cours_x', series.started, statsPlural(series.finished, 'terminée'))}` : '';
  const tiles = [
    statsTile(String(films.seen || 0), '', films.seen > 1 ? 'Films vus' : 'Film vu', filmDetail),
    statsTile(String(stats.episodes?.seen || 0), '', (stats.episodes?.seen || 0) > 1 ? 'Épisodes vus' : 'Épisode vu', series.total ? `${KINO_I18N.t('stats.dans_x', statsPlural(series.total, 'série'))}` : ''),
    statsTile(String(series.total || 0), '', (series.total || 0) > 1 ? 'Séries' : 'Série', seriesDetail),
    statsTile(time.value, time.unit, 'De lecture dans KINO', 'positions enregistrées'),
  ];
  const genres = (stats.genres || []).map(genre => ({ name: GENRE_FR[genre.name] || genre.name, count: genre.count }));
  const decades = (stats.decades || []).map(decade => ({ name: `${KINO_I18N.t('stats.annees_x', decade.decade)}`, count: decade.count }));
  const oldest = stats.oldest ? html`<p class="stats-fact">Le plus ancien film vu : <strong${titleAttrs({ id: stats.oldest.id, name: stats.oldest.name, year: String(stats.oldest.year) }, 'movie')}>${displayTitle(stats.oldest)}</strong> (${stats.oldest.year}).</p>` : '';
  return html`
    <div class="stats-tiles">${tiles}</div>
    <div class="stats-grid">
      <section class="stats-card" aria-labelledby="statsGenresTitle">
        <h3 id="statsGenresTitle">${KINO_I18N.t('stats.genres_preferes')}</h3>
        <p class="stats-sub">${seen ? `${KINO_I18N.t('stats.sur_x_vu_x_dont_le_genre_est_connu_sur_x', statsPlural(stats.genres_known || 0, 'titre'), (stats.genres_known || 0) > 1 ? 's' : '', seen)}` : ''}</p>
        ${genres.length ? statsMeterList(genres, 'Genres préférés') : html`<p class="stats-empty">${KINO_I18N.t('stats.pas_encore_de_genre_connu')}</p>`}
      </section>
      <section class="stats-card" aria-labelledby="statsMonthsTitle">
        <h3 id="statsMonthsTitle">${KINO_I18N.t('stats.titres_ouverts_par_mois')}</h3>
        <p class="stats-sub">${KINO_I18N.t('stats.les_12_derniers_mois_d_apres_la_derniere')}</p>
        ${statsMonthsChart(stats.months || [])}
      </section>
      <section class="stats-card" aria-labelledby="statsDecadesTitle">
        <h3 id="statsDecadesTitle">${KINO_I18N.t('stats.epoques_des_titres_vus')}</h3>
        <p class="stats-sub">${KINO_I18N.t('stats.par_decennie_de_sortie')}</p>
        ${decades.length ? statsMeterList(decades, 'Époques des titres vus') : html`<p class="stats-empty">${KINO_I18N.t('stats.pas_encore_d_annee_connue')}</p>`}
        ${oldest}
      </section>
    </div>
    <p class="stats-note">${KINO_I18N.t('stats.calculees_sur_ce_mac_a_partir_de_votre')}</p>`;
}

export async function loadStats() {
  const body = document.getElementById('viewingStatsBody');
  if (!body) return;
  render(body, html`<p class="stats-empty">${KINO_I18N.t('stats.calcul_de_vos_statistiques')}</p>`);
  try {
    const stats = await api('/api/stats');
    if (activeTab === 'stats') render(body, statsHtml(stats));
  } catch (error) {
    render(body, html`<p class="stats-empty">${tHtml('stats.statistiques_indisponibles_x', error.message)}</p>`);
  }
}

