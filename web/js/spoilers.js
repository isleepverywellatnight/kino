const Spoilers = (() => {
  const empty = () => ({ enabled: false, overrides: {}, revealed: {} });
  const code = (season, episode) => `S${String(Number(season)).padStart(2, '0')}E${String(Number(episode)).padStart(2, '0')}`;
  const canonicalCode = value => {
    const match = String(value || '').match(/^s(\d{1,2})e(\d{1,3})$/i);
    return match ? code(match[1], match[2]) : '';
  };

  function watched(history, seriesId, season, episode) {
    if (!seriesId || !Number.isInteger(Number(season)) || !Number.isInteger(Number(episode))) return false;
    const item = (history || []).find(h => String(h.id).toLowerCase() === String(seriesId).toLowerCase());
    if (!item) return false;
    const key = code(season, episode);
    if (Array.isArray(item.watched_episodes) && item.watched_episodes.some(value => canonicalCode(value) === key)) return true;
    const positionKey = Object.keys(item.ep_positions || {}).find(value => canonicalCode(value) === key);
    const position = positionKey ? item.ep_positions[positionKey] : null;
    return Boolean(position?.completed || Number(position?.pct || 0) >= 85);
  }

  function concealed(state, seriesId, season, episode, isWatched) {
    const pref = state || empty();
    const id = String(seriesId || '').toLowerCase();
    const enabled = Object.prototype.hasOwnProperty.call(pref.overrides || {}, id)
      ? pref.overrides[id] : pref.enabled === true;
    if (!enabled || isWatched === true) return false;
    return !(pref.revealed?.[id] || []).includes(code(season, episode));
  }

  function episodeNumber(title) {
    const text = String(title || '');
    const seasonMatch = text.match(/\bS(\d{1,2})[ ._-]*E(\d{1,3})\b/i);
    if (seasonMatch) return { season: Number(seasonMatch[1]), episode: Number(seasonMatch[2]) };
    const episodeMatch = text.match(/\b(?:episode|épisode|ep)[ ._-]*(\d{1,3})\b/i);
    return episodeMatch ? { season: null, episode: Number(episodeMatch[1]) } : null;
  }

  const neutralTitle = episode => Number(episode) > 0 ? `Épisode ${Number(episode)}` : 'Épisode suivant';
  const normalizeTitle = title => String(title || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

  return { empty, code, watched, concealed, episodeNumber, neutralTitle, normalizeTitle };
})();

if (typeof module !== 'undefined') module.exports = Spoilers;
