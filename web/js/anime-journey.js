const AnimeJourneyView = (() => {
  const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  const formatLabel = format => ({TV: 'Série', TV_SHORT: 'Série courte', ONA: 'Série web',
    MOVIE: 'Film', OVA: 'OVA', SPECIAL: 'Spécial'}[format] || 'Œuvre');
  const relationLabel = relation => ({SELF: 'Œuvre actuelle', SEQUEL: 'Suite', PREQUEL: 'Préquelle',
    SIDE_STORY: 'Récit parallèle', PARENT: 'Œuvre principale', SPIN_OFF: 'Dérivé',
    ALTERNATIVE: 'Version alternative', SUMMARY: 'Résumé'}[relation] || 'Œuvre liée');

  function progress(work, history, playback) {
    if (!playback?.id) return {done: false, label: 'Lecture à vérifier'};
    const item = (history || []).find(entry => entry.id === playback.id);
    if (!item) return {done: false, label: 'À voir'};
    if (playback.type === 'movie') {
      if (item.completed || Number(item.progress_pct || 0) >= 85) return {done: true, label: 'Film vu'};
      if (Number(item.progress_pct || 0) > 0) return {done: false, label: 'Film en cours'};
      return {done: false, label: 'À voir'};
    }
    const watched = new Set((item.watched_episodes || []).map(value => String(value).toUpperCase())).size;
    const total = Number(work.episodes || 0);
    if (total > 0) return {done: watched >= total, label: `${watched}/${total} épisodes vus`};
    return {done: false, label: watched ? `${watched} épisodes vus` : 'À voir'};
  }

  function render(journey, history, protectionEnabled, revealed, matches, currentWorkId) {
    if (!journey?.ok) return `<p class="journey-empty">${escape(journey?.message || 'Parcours indisponible.')}</p>`;
    const works = journey.works || [];
    const observations = works.map(work => {
      const match = matches?.get(work.id);
      const playback = work.playback || (match?.ok ? {id: match.media.id, type: match.media.type} : null);
      return {work, match, playback, progress: progress(work, history, playback)};
    });
    const nextId = observations.find(entry => !entry.progress.done)?.work.id;
    const customOrder = journey.order_label === 'Ordre personnalisé';
    const header = `<div class="journey-header"><div><h3>Parcours de la saga</h3><p>${escape(journey.order_label)} · Relations : AniList</p></div>`
      + (journey.order_label === 'Ordre personnalisé' ? '<button class="btn btn-secondary" onclick="resetJourneyOrder()">Rétablir l’ordre de sortie</button>' : '')
      + '</div>';
    const note = !journey.relations_known
      ? '<p class="journey-note">AniList ne renseigne pas de relation pour cette œuvre. Aucun autre élément de saga n’est déduit de son titre.</p>'
      : `<p class="journey-note">${customOrder ? 'Ordre personnalisé' : 'Ordre de sortie'} des œuvres reliées ; il ne prouve pas l’ordre narratif.${journey.partial ? ' Relations partielles : certaines œuvres peuvent manquer.' : ''}</p>`;
    const cards = observations.map(({work, match, playback, progress: state}, index) => {
      const hidden = protectionEnabled && !state.done && work.id !== currentWorkId && !revealed?.has(work.id);
      const title = hidden ? `${formatLabel(work.format)} · ${work.year || 'date inconnue'}` : work.title;
      const message = match && !match.ok ? `<span class="journey-unavailable">${escape(match.message || 'Lecture non identifiée')}</span>` : '';
      const notPlayable = match && !match.ok && match.reason !== 'unavailable';
      return `<li class="journey-work" data-journey-id="${Number(work.id)}">
        <div class="journey-order" aria-label="Position ${index + 1}">${String(index + 1).padStart(2, '0')}</div>
        <div class="journey-copy">
          <div class="journey-kickers"><span>${escape(formatLabel(work.format))}</span><span>${escape(relationLabel(work.relation))}</span><span>${escape(work.year || 'Date inconnue')}</span></div>
          <strong>${escape(title)}</strong>
          <span class="journey-progress">${escape(state.label)}</span>
          ${work.id === nextId ? `<span class="journey-next">${customOrder ? 'Prochain selon votre ordre' : "Prochain selon l'ordre de sortie"}</span>` : ''}
          ${message}
        </div>
        <div class="journey-actions">
          ${hidden ? `<button class="btn btn-secondary" onclick="revealJourneyTitle(${Number(work.id)})">Afficher le titre</button>` : ''}
          ${notPlayable ? `<a class="btn btn-secondary" href="https://anilist.co/anime/${Number(work.id)}" target="_blank" rel="noopener noreferrer">Voir sur AniList</a>`
            : `<button class="btn btn-secondary" onclick="openJourneyWork(${Number(work.id)})">${match?.reason === 'unavailable' ? 'Réessayer' : 'Ouvrir'}</button><button class="btn" onclick="playJourneyWork(${Number(work.id)}, this)">Lancer</button>`}
          <div class="journey-move"><button aria-label="Monter l’œuvre ${index + 1}" title="Monter" onclick="moveJourneyWork(${Number(work.id)}, -1)" ${index === 0 ? 'disabled' : ''}>↑</button><button aria-label="Descendre l’œuvre ${index + 1}" title="Descendre" onclick="moveJourneyWork(${Number(work.id)}, 1)" ${index === works.length - 1 ? 'disabled' : ''}>↓</button></div>
        </div>
      </li>`;
    }).join('');
    return header + note + `<ol class="journey-list">${cards}</ol><p class="journey-status" id="journeyStatus" role="status"></p>`;
  }

  return {render, progress};
})();

if (typeof module !== 'undefined') module.exports = AnimeJourneyView;
