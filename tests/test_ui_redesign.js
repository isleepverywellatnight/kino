const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const source = require('../tools/interface_source.js').appSource();
const markup = require('../tools/interface_source.js').indexHtml();
// What came from redesign.css, now folded into design.css.
const designCss = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const redesign = designCss.slice(designCss.indexOf('From redesign.css'), designCss.indexOf('\nbody {\n  background: radial-gradient'));
const icons = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/icons.svg'), 'utf8');
const resumeFunction = 'const FRENCH_MONTHS' + source
  .split('const FRENCH_MONTHS', 2)[1]
  .split('async function removeHistoryItem', 1)[0]
  + 'function getSeriesTargetEpisode(h)' + source
  .split('function getSeriesTargetEpisode(h)', 2)[1]
  .split('const FRENCH_MONTHS', 1)[0];
const settingsNavigationFunction = 'function initSettingsSectionNavigation()' + source
  .split('function initSettingsSectionNavigation()', 2)[1]
  .split('function initHeaderMenuKeyboard()', 1)[0];
const genreFunction = 'function renderGenreChipsForTab(tab)' + source
  .split('function renderGenreChipsForTab(tab)', 2)[1]
  .split('function updateCatalogHeading()', 1)[0];
const torrentFilterFunction = 'function getFilteredTorrents()' + source
  .split('function getFilteredTorrents()', 2)[1]
  .split('function scoreTorrent(t)', 1)[0];
const torrentRecommendationFunctions = 'function scoreTorrent(t)' + source
  .split('function scoreTorrent(t)', 2)[1]
  .split('async function copyTorrentMagnet(', 1)[0];
const playbackPlanFunction = 'function inAppPlaybackPlan(info, selectedAudio, videoElement)' + source
  .split('function inAppPlaybackPlan(info, selectedAudio, videoElement)', 2)[1]
  .split('async function recoverInAppSource(', 1)[0];
const playbackRecoveryFunction = 'async function recoverInAppSource(generation)' + source
  .split('async function recoverInAppSource(generation)', 2)[1]
  .split('function renderInAppAudioTracks(', 1)[0];

function renderFor(activeTab, history) {
  const elements = Object.fromEntries(
    ['homeResumeSection', 'homeResumeGrid', 'resumeSectionTitle', 'homeResumeAll', 'homeResumeClear']
      .map(id => [id, { style: {}, innerHTML: '', textContent: '', classList: { toggle(name, on) { this[name] = on; } } }])
  );
  const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  const kindCode = 'function listItemKind(m)' + source.split('function listItemKind(m)', 2)[1].split('\n}\n', 1)[0] + '\n}\n';
  vm.runInNewContext(`${kindCode}; ${resumeFunction}; renderHomeResume();`, {
    activeTab,
    networkOnline: true,
    document: { getElementById: id => elements[id] },
    META_CLIENT_CACHE: new Map(),
    api: async () => ({ meta: {} }),
    attrJson: value => escapeHtml(JSON.stringify(value)),
    Date,
    Promise,
    getInProgressHistory: () => history,
    formatRemaining: () => '25 min restantes',
    titleAttrs: () => '',
    displayTitle: item => item?.name || '',
    escapeHtml,
    encodeURIComponent,
  });
  return elements;
}

const history = [1, 2, 3, 4, 5].map(n => ({
  id: `tt${n}`, name: `Film ${n}`, type: 'movie', year: '2026', progress_pct: 36,
  position: 60, duration: 180, poster: `https://example.test/${n}.jpg`,
}));

test('home resume is a compact preview, without a destructive clear action', () => {
  const elements = renderFor('movies', history);
  assert.equal((elements.homeResumeGrid.innerHTML.match(/<article class="resume-card">/g) || []).length, 4);
  assert.equal(elements.homeResumeGrid.classList['resume-row'], true);
  assert.equal(elements.homeResumeClear.style.display, 'none');
  assert.equal(elements.homeResumeAll.style.display, 'inline-flex');
  assert.match(elements.homeResumeGrid.innerHTML, /class="resume-main"/);
});

test('the library keeps all unfinished items and history controls', () => {
  const elements = renderFor('history', history);
  assert.equal((elements.homeResumeGrid.innerHTML.match(/<article class="resume-card">/g) || []).length, 5);
  assert.equal(elements.homeResumeGrid.classList['resume-row'], false);
  assert.equal(elements.homeResumeClear.style.display, 'inline-flex');
  assert.equal(elements.homeResumeAll.style.display, 'none');
  assert.equal(elements.resumeSectionTitle.textContent, '5 lectures en cours', 'a summary: the tab is the title');
});

test('an empty home has no resume block', () => {
  const elements = renderFor('movies', []);
  assert.equal(elements.homeResumeSection.style.display, 'none');
});

test('what to watch now leads the page, then discovery; every library page has its tab', () => {
  assert.ok(markup.indexOf('id="homeResumeSection"') < markup.indexOf('id="heroSpotlight"'));
  assert.ok(markup.indexOf('id="heroSpotlight"') < markup.indexOf('id="catalogHeader"'));
  assert.doesNotMatch(markup, /id="libraryMore"/, 'no page hides behind « Plus »');
  for (const tab of ['watchlist', 'history', 'myseries', 'calendar', 'watched', 'offline', 'customlists', 'rdcloud', 'stats']) {
    assert.match(markup, new RegExp(`<button class="lib-subtab[^"]*" id="subtab-${tab}" onclick="switchTab\\('${tab}'\\)">`), tab);
  }
  assert.match(markup, /id="tab-settings"/);
});

test('the redesigned app chrome stays monochrome', () => {
  for (const [, hex] of redesign.matchAll(/#([\da-fA-F]{6})(?![\da-fA-F])/g)) {
    const channels = [0, 2, 4].map(offset => hex.slice(offset, offset + 2));
    assert.ok(channels.every(channel => channel.toLowerCase() === channels[0].toLowerCase()), `tinted color #${hex}`);
  }
  for (const [, red, green, blue] of redesign.matchAll(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/g)) {
    assert.equal(red, green, `tinted rgb(${red}, ${green}, ${blue})`);
    assert.equal(green, blue, `tinted rgb(${red}, ${green}, ${blue})`);
  }
  assert.match(redesign, /\.torrent-item\.is-recommended/);
  // the account dot is grey; only a missing or refused key colours it.
  const design = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
  assert.match(design, /\.user-status-dot \{[^}]*background: var\(--k-text-3\);/);
});

test('settings navigation leads to its four groups', () => {
  const targets = [...markup.matchAll(/class="settings-index-item(?: active)?" data-settings-target="([^"]+)"/g)]
    .map(([, id]) => id);
  assert.deepEqual(targets, ['settings-player', 'settings-account', 'settings-connections', 'settings-app']);
  for (const target of targets) assert.match(markup, new RegExp(`<section class="settings-group" id="${target}"`));
  assert.doesNotMatch(source, /initSettingsCardDisclosure/);
  assert.match(source, /section\.scrollIntoView\(\{ block: 'start', behavior: 'auto' \}\)/);
});

test('settings navigation follows manual scrolling without moving the page again', () => {
  const listeners = {};
  const positions = [90, 420, 760];
  const sections = positions.map((_, index) => ({
    getBoundingClientRect: () => ({ top: positions[index] }),
    scrollIntoView: () => { sections[index].jumped = true; },
  }));
  const links = sections.map((_, index) => {
    const classes = new Set(index === 0 ? ['active'] : []);
    return {
      dataset: { settingsTarget: `section-${index}` },
      classList: { toggle: (name, active) => active ? classes.add(name) : classes.delete(name), contains: name => classes.has(name) },
      setAttribute: () => {}, removeAttribute: () => {},
      addEventListener: (event, callback) => { listeners[`link-${index}-${event}`] = callback; },
    };
  });
  vm.runInNewContext(`let refreshSettingsSectionNavigation = () => {}; ${settingsNavigationFunction}; initSettingsSectionNavigation();`, {
    document: {
      body: { dataset: { activeTab: 'settings' } },
      documentElement: { scrollHeight: 2400 },
      querySelectorAll: () => links,
      getElementById: id => sections[Number(id.split('-')[1])],
    },
    window: { innerWidth: 1200, innerHeight: 800, scrollY: 0, addEventListener: (event, callback) => { listeners[event] = callback; } },
    requestAnimationFrame: callback => callback(),
  });

  positions[1] = 80;
  listeners.scroll();
  assert.equal(links[1].classList.contains('active'), true);
  assert.equal(sections.some(section => section.jumped), false);

  listeners['link-2-click']();
  assert.equal(links[2].classList.contains('active'), true);
  assert.equal(sections[2].jumped, true);
});

test('changing sections resets the scroll without a smooth-scroll glitch', () => {
  const switchTabSource = source.split('async function switchTab(tab)', 2)[1].split('async function loadCatalog(', 1)[0];
  assert.match(switchTabSource, /if \(tabChanged\) window\.scrollTo\(\{ top: 0, behavior: 'auto' \}\)/);
  assert.doesNotMatch(switchTabSource, /window\.scrollTo\(\{top: 0, behavior: 'smooth'\}\)/);
});

test('all referenced vector icons exist in the shared sprite', () => {
  const referenced = new Set([...markup.matchAll(/href="\/icons\.svg#([^"]+)"/g)].map(([, id]) => id));
  assert.ok(referenced.size >= 12);
  for (const id of referenced) assert.match(icons, new RegExp(`<symbol id="${id}"`));
});

test('Discord presence uses the shared KINO identity by default', () => {
  const section = markup.split('id="settings-discord"')[1].split('id="settings-storage"')[0];
  assert.match(section, /<option value="1556492442415398973">KINO/);
  assert.match(section, /Aucune application Discord personnelle/);
  assert.doesNotMatch(section, /IMDb \(Cinéma|<option value="631379801826918400"/);
});

test('anime filters use anime-specific categories while film filters stay unchanged', () => {
  const filters = { innerHTML: '' };
  const context = { document: { getElementById: () => filters } };
  vm.runInNewContext(`${genreFunction}; renderGenreChipsForTab('anime');`, context);
  for (const label of ['Action &amp; combats', 'Fantasy &amp; magie', 'Isekai', 'SF &amp; mecha', 'Tranche de vie', 'Sport', 'Films anime']) {
    assert.ok(filters.innerHTML.includes(label), `missing anime category ${label}`);
  }
  for (const obsolete of ['>Drame<', '>Mystère<', '>Films d\'Animation<']) {
    assert.ok(!filters.innerHTML.includes(obsolete), `old film-style label ${obsolete}`);
  }
  vm.runInNewContext(`${genreFunction}; renderGenreChipsForTab('series');`, context);
  assert.match(filters.innerHTML, />Policier</);
  assert.match(filters.innerHTML, />Western</);
});

test('the recommended source follows validated server ranking, not a second UI score', () => {
  const list = { innerHTML: '' };
  const sub = { textContent: '' };
  const streams = [
    { title: 'A.Monster.Calls.2016.1080p.WEB', source: 'Indexer', score: 1800, seeders: 4, qualities: ['1080p'], langs: ['VOSTFR'], meta: '', is_instant: true,
      recommendation: { reasons: ['Titre cohérent', 'Langue souhaitée : VOSTFR'], warnings: ['Épisode à vérifier dans le fichier'] } },
    { title: 'The.Monster.2016.1080p.WEB', source: 'Indexer+', score: -10000, seeders: 90, qualities: ['1080p'], langs: ['VF'], meta: '' },
  ];
  vm.runInNewContext(`${torrentRecommendationFunctions}; renderTorrents();`, {
    document: { getElementById: id => id === 'torrentsList' ? list : sub },
    getFilteredTorrents: () => streams,
    allTorrents: streams,
    currentMedia: null,
    extractTorrentSeeders: item => item.seeders,
    escapeHtml: value => String(value),
  });
  const recommended = list.innerHTML.split('<div class="torrent-item is-recommended">')[1];
  assert.ok(recommended, 'a source should be marked recommended');
  assert.match(recommended.split('<div class="torrent-item ')[0], /A\.Monster\.Calls/);
  assert.match(recommended, /Pourquoi : Titre cohérent · Langue souhaitée : VOSTFR/);
  assert.match(recommended, /À vérifier : Épisode à vérifier dans le fichier/);
  assert.match(recommended, />Disponible<\/span>/);
});

test('the French filter trusts parsed audio languages, not subtitle words', () => {
  const streams = [
    { title: 'A Monster Calls - Fantasy 2016 Eng Rus Multi Subs 1080p [H264-mp4].mp4', langs: ['VO'], qualities: ['1080p'], meta: '', source: 'Torrentio' },
    { title: 'A Monster Calls 2016 Multi Subs 1080p', langs: [], qualities: ['1080p'], meta: '', source: 'Torrentio' },
    { title: 'A.Monster.Calls.2016.FRENCH.1080p.mp4', langs: ['VF'], qualities: ['1080p'], meta: '', source: 'Torrentio' },
    { title: 'A.Monster.Calls.2016.MULTi.VFF.1080p', langs: ['MULTI', 'VFF'], qualities: ['1080p'], meta: '', source: 'Torrentio' },
    { title: 'A.Monster.Calls.2016.VOSTFR.1080p', langs: ['VOSTFR'], qualities: ['1080p'], meta: '', source: 'Torrentio' },
  ];
  const context = {
    activeFilter: 'fr', allTorrents: streams, currentMedia: null, result: null,
    document: { getElementById: () => null },
    extractTorrentSizeGb: () => 0, extractTorrentSeeders: () => 0,
  };
  vm.runInNewContext(`${torrentFilterFunction}; result = getFilteredTorrents().map(t => t.title);`, context);
  assert.deepEqual(Array.from(context.result), [
    'A.Monster.Calls.2016.FRENCH.1080p.mp4',
    'A.Monster.Calls.2016.MULTi.VFF.1080p',
    'A.Monster.Calls.2016.VOSTFR.1080p',
  ]);
});

test('playback plans copy compatible tracks and convert incompatible audio', () => {
  const video = { canPlayType: () => 'probably' };
  const info = { container: 'mov,mp4', video: [{ codec: 'h264', pixel_format: 'yuv420p' }],
    audio: [{ index: 1, codec: 'aac', default: true }, { index: 2, codec: 'ac3' }] };
  const context = { info, video, result: null };
  vm.runInNewContext(`${playbackPlanFunction}; result = inAppPlaybackPlan(info, info.audio[0], video);`, context);
  assert.equal(context.result.needsCompatibleStream, false);
  vm.runInNewContext(`${playbackPlanFunction}; result = inAppPlaybackPlan(info, info.audio[1], video);`, context);
  assert.equal(context.result.needsCompatibleStream, true);
  assert.equal(context.result.needsAudioConversion, true);
  assert.equal(context.result.needsVideoConversion, false);
  assert.equal(context.result.audioIndex, 2);
});

test('a failed source requests a different candidate and retains the position', async () => {
  const requests = [];
  const starts = [];
  const recovery = { params: { title: 'Example', imdb_id: 'tt1' }, excluded: new Set(['first']), attempts: 0, pending: false };
  const context = {
    inAppRecovery: recovery, inAppPlaybackGeneration: 3, inAppCurrentMedia: { id: 'tt1' },
    document: { getElementById: () => ({ currentTime: 42 }) },
    inAppAbsoluteTime: video => video.currentTime,
    showInAppToast: () => {},
    api: async (_path, options) => {
      requests.push(JSON.parse(options.body));
      return { source_key: 'second', attempted_source_keys: ['first', 'second'], stream_url: 'https://example.invalid/second', file: { filename: 'movie.mkv' } };
    },
    openInAppPlayer: (...args) => starts.push(args),
  };
  await vm.runInNewContext(`${playbackRecoveryFunction}; recoverInAppSource(3);`, context);
  assert.deepEqual(requests[0].exclude_source_keys, ['first']);
  assert.equal(starts[0][0], 'https://example.invalid/second');
  assert.equal(starts[0][4], 42);
  assert.equal(recovery.excluded.has('second'), true);
});

test('playback buttons and the remote are labelled in French', () => {
  const journey = require('../tools/interface_source.js').inFrench(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js/anime-journey.js'), 'utf8'));
  for (const [name, text] of [['index.html', markup], ['app.js', source], ['anime-journey.js', journey]]) {
    assert.doesNotMatch(text, />\s*Play\s*</, `${name} shows an English Play button`);
    assert.doesNotMatch(text, /'Play'|`Play /, `${name} builds an English Play label`);
    assert.doesNotMatch(text, />\s*(KINO )?Remote\s*</, `${name} shows an English Remote label`);
  }
});
