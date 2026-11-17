// « Sauvegardes » in Paramètres → Application: the copies, when, what
// they hold, and « Restaurer » asked first, then the page reloaded.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const start = source.indexOf('// --- Sauvegardes (data_backup.py');
const code = source.slice(start, source.indexOf('// --- fin des sauvegardes', start));

function load({ answer = () => ({ items: [] }), confirm = () => true } = {}) {
  const elements = {};
  const el = id => elements[id] || (elements[id] = { id, textContent: '', innerHTML: '' });
  const calls = [], statuses = [], reloads = [], asked = [];
  const context = {
    document: { getElementById: el },
    api: async (url, options) => { calls.push([url, options && JSON.parse(options.body || 'null')]); return answer(url); },
    escapeHtml: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
    showSettingsStatus: (text, state) => statuses.push([text, state]),
    window: { confirm: text => { asked.push(text); return confirm(); }, location: { reload: () => reloads.push(1) } },
    setTimeout: fn => fn(),
    Date, Math, Number, String,
  };
  vm.runInNewContext(`${code}\nthis.api_ = { backupWhen, backupCounts, renderBackups, loadBackups, backupNow, restoreBackup };`, context);
  return { ...context.api_, el, calls, statuses, reloads, asked };
}

const noon = new Date(2026, 10, 12, 12, 0).getTime();
const at = (days, hour = 9) => new Date(2026, 10, 12 - days, hour, 5).getTime() / 1000;

test('when a copy was made, in French', () => {
  const page = load();
  assert.equal(page.backupWhen(at(0), noon), 'Aujourd’hui, 09:05');
  assert.equal(page.backupWhen(at(1), noon), 'Hier, 09:05');
  assert.equal(page.backupWhen(at(2), noon), 'mardi 10 novembre, 09:05');
  assert.equal(page.backupCounts({ watchlist: 48, seen: 312, in_progress: 1, rated: 0 }),
    '48 dans Ma Liste · 312 vus · 1 en cours · 0 noté');
});

test('the list of copies, each with « Restaurer »', () => {
  const page = load();
  page.renderBackups([
    { id: 'avant-restauration-2026-11-12-101500', kind: 'before_restore', created_at: Date.now() / 1000, counts: { watchlist: 2 }, restorable: true },
    { id: '2026-11-05', kind: 'daily', created_at: at(7), counts: { seen: 3 }, restorable: false },
  ]);
  const list = page.el('backupsList').innerHTML;
  assert.match(list, /Avant restauration · Aujourd’hui, /);
  assert.match(list, /data-backup="avant-restauration-2026-11-12-101500"/);
  assert.equal((list.match(/>Restaurer<\/button>/g) || []).length, 2);
  assert.match(list, /data-backup="2026-11-05"[^>]*disabled/, 'a copy of a too different version cannot be restored');
  assert.match(page.el('backupsStatus').textContent, /^Dernière copie : avant restauration|^Dernière copie : aujourd’hui/);
  page.renderBackups([]);
  assert.equal(page.el('backupsStatus').textContent, 'Première copie quelques minutes après le lancement de KINO.');
});

test('« Sauvegarder maintenant » and « Restaurer » ask KINO', async () => {
  const page = load({ answer: url => (url === '/api/backups/now' ? { items: [{ id: '2026-11-12', created_at: at(0), counts: {}, restorable: true }] } : { ok: true }) });
  await page.backupNow(null);
  assert.deepEqual(page.calls[0], ['/api/backups/now', {}]);
  assert.deepEqual(page.statuses.at(-1), ['Sauvegardé', 'saved']);
  const btn = { dataset: { backup: '2026-11-12', label: 'Aujourd’hui, 09:05' }, disabled: false };
  await page.restoreBackup(btn);
  assert.match(page.asked[0], /^Restaurer la copie « Aujourd’hui, 09:05 » \?/);
  assert.match(page.asked[0], /Vos clés restent celles d’aujourd’hui/);
  assert.deepEqual(page.calls.at(-1), ['/api/backups/restore', { id: '2026-11-12' }]);
  assert.equal(page.reloads.length, 1, 'the page reloads with the data put back');
});

test('nothing is restored without a yes', async () => {
  const page = load({ confirm: () => false });
  await page.restoreBackup({ dataset: { backup: '2026-11-12', label: 'Hier' } });
  assert.equal(page.calls.length, 0);
});

test('the block sits in Paramètres → Application', () => {
  const app = html.slice(html.indexOf('id="settings-app"'));
  assert.ok(app.indexOf('id="settings-backups"') > 0 && app.indexOf('id="settings-backups"') < app.indexOf('id="settings-diagnostic"'));
  assert.match(html, /id="backupNowBtn" onclick="backupNow\(this\)">Sauvegarder maintenant</);
  assert.match(source, /loadSuggestionStatus\(\);\n\s+loadBackups\(\);/);
});
