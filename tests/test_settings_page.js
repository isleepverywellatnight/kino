// Simpler settings: four groups with Lecture first, plain names,
// one sentence per setting, saved as soon as they change, a notice when no Debrid
// key is saved. And the home rows stay on the
// home tabs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { loadPage, settle } = require('../tools/page_harness.js');

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/redesign.css'), 'utf8');
const settings = html.slice(html.indexOf('<div id="settingsPanel"'), html.indexOf('id="settings-shortcuts"'));
const card = id => settings.slice(settings.indexOf(`id="${id}"`), settings.indexOf('</section>', settings.indexOf(`id="${id}"`)));

test('four groups, Lecture first; the blocks keep the ids the Diagnostic opens', () => {
  const index = [...settings.matchAll(/data-settings-target="([^"]+)"/g)].map(m => m[1]);
  assert.deepEqual(index, ['settings-player', 'settings-account', 'settings-connections', 'settings-app']);
  assert.match(settings, /class="settings-index-item active" data-settings-target="settings-player" aria-current="location"/);
  const at = id => html.indexOf(`id="${id}"`);
  assert.ok(at('settings-player') < at('settings-account') && at('settings-account') < at('settings-connections') && at('settings-connections') < at('settings-app'));
  for (const id of ['settings-sync', 'settings-discord']) assert.ok(at('settings-connections') < at(id) && at(id) < at('settings-app'), id);
  for (const id of ['settings-storage', 'settings-updates', 'settings-diagnostic', 'settings-shortcuts']) assert.ok(at('settings-app') < at(id), id);
  // no numbered kickers, nothing to save by hand.
  assert.doesNotMatch(html, /settings-card-kicker|settings-save-bar|settingsSaveBtn|onclick="saveConfig\(\)"/);
  assert.match(html, /<p class="settings-header-desc">Chaque changement s’applique tout de suite\.<\/p>/);
  assert.match(html, /<span id="settingsSaveStatus" class="settings-save-status" role="status" aria-live="polite"><\/span>/);
});

test('plain names, and one sentence for each setting', () => {
  const player = card('settings-player');
  const account = card('settings-account');
  for (const [id, label] of [['cfgPrefLang', 'Langue'], ['cfgPrefQuality', 'Qualité'], ['cfgPlayerMode', 'Lecteur'],
    ['cfgHdrMode', 'Image'], ['cfgAudioMode', 'Son'], ['cfgSpoilerProtection', 'Épisodes non vus']]) {
    assert.match(player, new RegExp(`<label class="settings-label" for="${id}">${label}</label>`), id);
  }
  assert.match(account, /<label class="settings-label" for="cfgProvider">Service<\/label>/);
  assert.match(account, /<label class="settings-label" for="cfgRdRetention">Nettoyage du Cloud<\/label>/);
  assert.doesNotMatch(settings, /1-Clic|Plage Dynamique|Tone-Mapping|Passthrough|Fournisseur Actif|Nettoyage Automatique|Purger|C:\\\\Users|MULTI|VOSTFR|débrider|OpenSubtitles|ID Client/);
  assert.match(player, /Volume égalisé, pour le lecteur externe\. Dans KINO, il se change pendant la lecture : Options → Voix\./);
  assert.match(player, /Sur un écran standard, le lecteur convertit le HDR : le SDR ne sert qu’à départager deux sources égales\./);
  const rows = player.split('class="set-row').slice(1);
  assert.ok(rows.length >= 9, 'every playback setting is a row');
  for (const row of rows) assert.equal((row.match(/<p class="settings-desc">/g) || []).length, 1, row.slice(0, 120));
  const advanced = player.slice(player.indexOf('<details class="set-advanced">'));
  for (const id of ['cfgSourcePriority', 'cfgHdrMode', 'cfgAudioMode']) assert.match(advanced, new RegExp(`id="${id}"`), `${id} is folded`);
  assert.match(advanced, /^<details class="set-advanced">\s*<summary>Options avancées<\/summary>/);
});

test('the saved values are unchanged', () => {
  const values = id => settings.match(new RegExp(`<select id="${id}"[^>]*>([\\s\\S]*?)</select>`))[1]
    .match(/value="([^"]+)"/g).map(v => v.slice(7, -1));
  assert.deepEqual(values('cfgPrefLang'), ['vf', 'vostfr']);
  assert.deepEqual(values('cfgPrefQuality'), ['4k', '1080p']);
  assert.deepEqual(values('cfgPlayerMode'), ['integrated', 'external']);
  assert.deepEqual(values('cfgHdrMode'), ['sdr_pref', 'hdr_native']);
  assert.deepEqual(values('cfgAudioMode'), ['voice_boost', 'standard']);
  assert.deepEqual(values('cfgSpoilerProtection'), ['off', 'on']);
  assert.deepEqual(values('cfgProvider'), ['realdebrid', 'alldebrid', 'torbox', 'debridlink', 'premiumize', 'megadebrid']);
  assert.deepEqual(values('cfgRdRetention'), ['0', '1', '3', '7', '14', '30']);
});

test('without a Debrid key, the page says so and leads to the key field', () => {
  assert.match(settings, /<div id="settingsSetupNotice" class="settings-setup" role="status" hidden>[\s\S]*Ajoutez votre clé Debrid[\s\S]*onclick="focusDebridKey\(\)">Ajouter ma clé<\/button>/);
  assert.match(source, /const setupNotice = document\.getElementById\('settingsSetupNotice'\);\n\s+if \(setupNotice\) setupNotice\.hidden = Boolean\(cfg\.has_token\);/);
  const code = source.slice(source.indexOf('// From the setup notice: straight to the key field.'), source.indexOf('function closeConfig() {'));
  const calls = [];
  const context = { document: { getElementById: id => (id === 'settings-account'
    ? { scrollIntoView: options => calls.push(['scroll', options.block]) }
    : { focus: options => calls.push(['focus', id, options.preventScroll]) }) } };
  vm.runInNewContext(`${code}\nfocusDebridKey();`, context);
  assert.deepEqual(calls, [['scroll', 'start'], ['focus', 'cfgToken', true]]);
  assert.match(css, /\.settings-setup\[hidden\] \{ display: none; \}/);
});

test('the native player switch keeps its place beside its explanation', () => {
  assert.match(card('settings-player'), /<div id="cfgNativePlayerField" style="display:none;" class="set-row">[\s\S]*<label class="settings-label" for="cfgNativePlayer">Lecteur natif \(recommandé\)<\/label>[\s\S]*<input type="checkbox" id="cfgNativePlayer" role="switch">/);
  assert.match(source, /nativeField\.style\.display = cfg\.native_player_available === true \? 'flex' : 'none';/);
  assert.match(source, /native_player: nativeToggle && document\.getElementById\('cfgNativePlayerField'\)\?\.style\.display !== 'none'/);
});

test('each setting saves as soon as it changes; the key waits for « Vérifier »', () => {
  const ids = JSON.parse(source.match(/const SETTINGS_AUTOSAVE_IDS = (\[[\s\S]*?\]);/)[1].replace(/'/g, '"'));
  for (const id of ids) assert.match(html, new RegExp(`id="${id}"`), id);
  for (const id of ['cfgPrefLang', 'cfgPrefQuality', 'cfgPlayerMode', 'cfgNativePlayer', 'cfgAutoFrSubs', 'cfgEpisodeNotifications',
    'cfgSourcePriority', 'cfgHdrMode', 'cfgAudioMode', 'cfgProvider', 'cfgRdRetention', 'cfgDiscordRpc', 'cfgDiscordPreset', 'cfgDiscordClientId', 'cfgDir']) {
    assert.ok(ids.includes(id), id);
  }
  assert.ok(!ids.includes('cfgToken'), 'the key is only saved by « Vérifier »');
  assert.match(source, /if \(el\) el\.addEventListener\('change', scheduleSettingsSave\);/);
  assert.match(source, /initSettingsSectionNavigation\(\);\ninitSettingsAutosave\(\);/);
  assert.match(html, /<button type="button" class="btn" id="cfgTokenVerifyBtn" onclick="verifyDebridKey\(this\)">Vérifier<\/button>/);
});

test('a change is saved without the key; « Vérifier » sends the key with its service', async () => {
  const posts = [];
  const page = await loadPage((url, options = {}) => {
    if (url === '/api/config' && options.method === 'POST') { posts.push(JSON.parse(options.body)); return { ok: true }; }
    if (url === '/api/config') return { debrid_provider: 'realdebrid', has_token: true, configured_providers: { realdebrid: true }, pref_quality: '4k' };
    return {};
  });
  await settle();
  const status = page.element('settingsSaveStatus');
  page.element('cfgPrefQuality').value = '1080p';
  page.element('cfgToken').value = 'typed-but-not-checked';
  await page.context.saveConfig();
  assert.equal(status.textContent, 'Enregistré');
  assert.equal(status.dataset.state, 'saved');
  await settle();
  assert.equal(status.textContent, '', '« Enregistré » goes away by itself');
  assert.equal(posts.at(-1).pref_quality, '1080p');
  assert.equal(posts.at(-1).debrid_provider, 'realdebrid');
  assert.ok(!('rd_token' in posts.at(-1)), 'an ordinary change never sends the key');

  // A service without a saved key does not become the active one by itself.
  page.element('cfgProvider').value = 'alldebrid';
  await page.context.saveConfig();
  await settle();
  assert.ok(!('debrid_provider' in posts.at(-1)));
  assert.equal(page.element('cfgProvider').value, 'alldebrid', 'the service being set up stays on screen');
  assert.equal(status.textContent, 'Collez votre clé AllDebrid, puis Vérifier.');

  page.element('cfgToken').value = '';
  const count = posts.length;
  await page.context.verifyDebridKey(page.element('cfgTokenVerifyBtn'));
  assert.equal(posts.length, count, 'no key, nothing sent');
  assert.equal(status.textContent, 'Collez d’abord votre clé.');

  page.element('cfgToken').value = 'new-key';
  await page.context.verifyDebridKey(page.element('cfgTokenVerifyBtn'));
  await settle();
  assert.equal(posts.at(-1).debrid_provider, 'alldebrid');
  assert.equal(posts.at(-1).rd_token, 'new-key');
  assert.equal(page.element('cfgToken').value, '', 'the field empties once the key is saved');
  assert.equal(page.element('cfgTokenVerifyBtn').textContent, 'Vérifier');
});

test('a refused save says so beside the title', async () => {
  const page = await loadPage((url, options = {}) => {
    if (url === '/api/config' && options.method === 'POST') return { error: 'Dossier introuvable' };
    if (url === '/api/config') return { debrid_provider: 'realdebrid', has_token: true, configured_providers: { realdebrid: true } };
    return {};
  });
  await settle();
  await assert.rejects(page.context.saveConfig());
  assert.equal(page.element('settingsSaveStatus').textContent, 'Pas enregistré : Dossier introuvable');
  assert.equal(page.element('settingsSaveStatus').dataset.state, 'error');
});

test('the home rows stay on the Films and Séries tabs', () => {
  const code = source.slice(source.indexOf('function hideHomeRows() {'), source.indexOf('function renderHomeResume() {'));
  const elements = Object.fromEntries(['homeResumeSection', 'homeNewSection', 'homeListSection', 'homeBecauseSection']
    .map(id => [id, { style: { display: 'block' }, hidden: false }]));
  vm.runInNewContext(`${code}\nhideHomeRows();`, { document: { getElementById: id => elements[id] } });
  assert.deepEqual(JSON.parse(JSON.stringify([elements.homeResumeSection.style.display, elements.homeNewSection.style.display,
    elements.homeListSection.hidden, elements.homeBecauseSection.hidden])), ['none', 'none', true, true]);
  assert.match(source, /hideDetailMore\(\);\n\s+\/\/ The home rows belong to the Films and Séries tabs only\.\n\s+if \(tab === 'movies' \|\| tab === 'series'\) renderHomeResume\(\);\n\s+else hideHomeRows\(\);/);
  const search = source.slice(source.indexOf('async function runSearch() {'), source.indexOf('async function oneClickCard('));
  assert.match(search, /hideHomeRows\(\);/);
});
