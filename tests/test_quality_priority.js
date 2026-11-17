// Setting « Priorité » in Paramètres → Lecture: two choices, loaded from KINO
// and saved with the other settings; the source comparison names it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { loadPage, settle } = require('../tools/page_harness.js');

const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const source = require('../tools/interface_source.js').appSource();

test('Lecture offers the two priorities, « Lancer vite » first, and says what each does', () => {
  const lecture = html.slice(html.indexOf('id="settings-player"'), html.indexOf('id="settings-account"'));
  const field = lecture.slice(lecture.indexOf('for="cfgSourcePriority"'));
  assert.match(lecture, /<label class="settings-label" for="cfgSourcePriority">Priorité<\/label>/);
  const options = [...field.slice(0, field.indexOf('</select>')).matchAll(/<option value="([^"]+)">([^<]+)<\/option>/g)].map(m => [m[1], m[2]]);
  // short choices; the sentence beside them says what each does.
  assert.deepEqual(options, [['speed', 'Lancer vite'], ['quality', 'Qualité choisie d’abord']]);
  const row = lecture.slice(lecture.lastIndexOf('<div class="set-row">', lecture.indexOf('for="cfgSourcePriority"')), lecture.indexOf('</select>', lecture.indexOf('id="cfgSourcePriority"')));
  assert.match(row, /« Lancer vite » prend une source prête tout de suite\. « Qualité choisie d’abord » peut attendre que Debrid la prépare, avec un bouton pour lancer une source prête à la place\./);
  assert.ok(lecture.indexOf('cfgPrefQuality') < lecture.indexOf('cfgSourcePriority'), 'after the quality');
  assert.ok(lecture.indexOf('<details class="set-advanced">') < lecture.indexOf('cfgSourcePriority'), 'among the advanced options');
});

test('the setting is loaded from KINO and saved with the others', async () => {
  let saved = null;
  const page = await loadPage((url, options = {}) => {
    if (url === '/api/config' && options.method === 'POST') { saved = JSON.parse(options.body); return { ok: true }; }
    if (url === '/api/config') return { source_priority: 'quality', pref_quality: '4k', has_token: true };
    return {};
  });
  await settle();
  assert.equal(page.element('cfgSourcePriority').value, 'quality');
  page.element('cfgSourcePriority').value = 'speed';
  await page.context.saveConfig();
  await settle();
  assert.equal(saved.source_priority, 'speed');
  page.element('cfgSourcePriority').value = 'nonsense';
  await page.context.saveConfig();
  await settle();
  assert.equal(saved.source_priority, 'speed', 'only the two values leave the page');
});

test('an unknown value from KINO shows « Lancer vite »', async () => {
  const page = await loadPage(url => (url === '/api/config' ? { source_priority: 'fastest' } : {}));
  await settle();
  assert.equal(page.element('cfgSourcePriority').value, 'speed');
});

test('the comparison under a source names the new part', () => {
  assert.match(source, /priority_points: 'qualité choisie d’abord'/);
});
