// Stable properties are used across episodes; file-local stream indexes are never stored.
const SeriesPlayback = (() => {
  const aliases = {
    fre: 'fr', fra: 'fr', french: 'fr', francais: 'fr',
    eng: 'en', english: 'en',
    jpn: 'ja', japanese: 'ja',
    deu: 'de', ger: 'de', german: 'de',
    spa: 'es', spanish: 'es',
    ita: 'it', italian: 'it',
  };

  function language(value, title = '') {
    const raw = String(value || '').trim().toLowerCase();
    if (aliases[raw]) return aliases[raw];
    if (/^[a-z]{2}$/.test(raw)) return raw;
    const words = String(title || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    if (/\b(francais|french|vf)\b/.test(words)) return 'fr';
    if (/\b(english|anglais|vo)\b/.test(words)) return 'en';
    if (/\b(japanese|japonais)\b/.test(words)) return 'ja';
    return 'und';
  }

  function audioRole(track) {
    const title = String(track?.title || '').toLowerCase();
    if (/commentary|commentaire|director.?s?\s*comment/.test(title)) return 'commentary';
    if (/description|audio.?descript|malvoyant/.test(title)) return 'description';
    return 'main';
  }

  function describeAudio(track) {
    if (!track) return null;
    return {
      language: language(track.language, track.title),
      role: audioRole(track),
      original: Boolean(track.original),
    };
  }

  function chooseAudio(tracks, preference, globalTrack) {
    const available = Array.isArray(tracks) ? tracks : [];
    const fallback = available.find(track => track.index === globalTrack?.index)
      || available.find(track => track.default) || available[0] || null;
    if (!preference) return { track: fallback, missing: false };
    const matches = available.filter(track => {
      const candidate = describeAudio(track);
      return candidate.language === preference.language && candidate.role === preference.role;
    });
    if (preference.language === 'und' && matches.length > 1) return { track: fallback, missing: true };
    const preferred = matches.find(track => Boolean(track.original) === Boolean(preference.original))
      || matches.find(track => track.default) || matches[0];
    return preferred ? { track: preferred, missing: false } : { track: fallback, missing: true };
  }

  function resolveAudio(tracks, currentChoice, seriesChoice, globalTrack) {
    let missing = false;
    for (const [source, preference] of [['current', currentChoice], ['series', seriesChoice]]) {
      if (!preference) continue;
      const choice = chooseAudio(tracks, preference, globalTrack);
      if (!choice.missing) return { track: choice.track, missing, source };
      missing = true;
    }
    return { track: chooseAudio(tracks, null, globalTrack).track, missing, source: 'global' };
  }

  function globalSubtitles(prefLang) {
    return prefLang === 'vostfr' || prefLang === 'vost'
      ? { enabled: true, kind: 'opensubtitles', language: 'fr', variant: 'standard' }
      : { enabled: false, kind: '', language: '', variant: '' };
  }

  // Release names of French dubs always say so (VFF, TRUEFRENCH, FRENCH, MULTI…);
  // « SUBFRENCH » or « VOSTFR » are subtitles, not French audio.
  const FRENCH_AUDIO = /(?:^|[^a-z0-9])(?:vff|vfq|vfi|vf2|vf|truefrench|french|multi)(?:[^a-z0-9]|$)/i;
  const FRENCH_LANG = /^(?:fr|fre|fra|french|fr-[a-z]{2})$/i;

  function releaseHasFrenchAudio(release) {
    return FRENCH_AUDIO.test(String(release || ''));
  }

  function frenchTrack(track) {
    return FRENCH_LANG.test(String(track?.lang || '').trim()) || /fran[cç]ais|\bfrench\b/i.test(String(track?.title || ''));
  }

  function forcedTrack(track) {
    return /forc/i.test(String(track?.title || ''));
  }

  // Native player, once a file is loaded: French audio needs nothing; otherwise
  // the file's full French subtitles, or French ones from OpenSubtitles.
  function frenchSubtitlePlan(tracks, release) {
    const list = Array.isArray(tracks) ? tracks : [];
    const audio = list.filter(track => track.type === 'audio');
    const subs = list.filter(track => track.type === 'sub');
    const current = audio.find(track => track.selected) || audio[0];
    if (current && frenchTrack(current)) return { action: 'none', reason: 'french-audio' };
    if (!String(current?.lang || '').trim() && releaseHasFrenchAudio(release)) return { action: 'none', reason: 'french-release' };
    if (subs.some(track => track.selected && frenchTrack(track) && !forcedTrack(track))) return { action: 'none', reason: 'french-subtitles-on' };
    const french = subs.filter(track => frenchTrack(track) && !forcedTrack(track));
    if (french.length) {
      const best = french.find(track => !/\bsdh\b|malentendant/i.test(String(track.title || ''))) || french[0];
      return { action: 'select', id: String(best.id), reason: 'embedded' };
    }
    return { action: 'online', reason: 'missing' };
  }

  // Built-in player: it cannot read the audio tracks, the release name tells.
  // A preference for French subtitles stays; a French audio release needs none.
  function automaticSubtitles(prefLang, release, enabled = true) {
    const base = globalSubtitles(prefLang);
    if (base.enabled || !enabled || !String(release || '').trim() || releaseHasFrenchAudio(release)) return base;
    return { enabled: true, kind: 'opensubtitles', language: 'fr', variant: 'standard', automatic: true };
  }

  function chooseSubtitles(available, preference) {
    if (!preference?.enabled) return { index: 0, missing: false };
    const variant = preference.variant || 'standard';
    const index = available.findIndex((sub, i) => i > 0 && sub.kind === preference.kind
      && sub.lang === preference.language && (sub.variant || 'standard') === variant);
    return index >= 0 ? { index, missing: false } : { index: 0, missing: true };
  }

  function resolveSubtitles(available, currentChoice, seriesChoice, globalChoice) {
    let missing = false;
    for (const [source, preference] of [['current', currentChoice], ['series', seriesChoice], ['global', globalChoice]]) {
      if (!preference) continue;
      const choice = chooseSubtitles(available, preference);
      if (!choice.missing) return { index: choice.index, missing, source };
      missing = true;
    }
    return { index: 0, missing, source: 'global' };
  }

  return { language, audioRole, describeAudio, chooseAudio, resolveAudio,
    globalSubtitles, chooseSubtitles, resolveSubtitles,
    releaseHasFrenchAudio, frenchSubtitlePlan, automaticSubtitles };
})();

if (typeof module !== 'undefined') module.exports = SeriesPlayback;
