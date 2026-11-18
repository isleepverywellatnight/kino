// Diagnostic in Paramètres: one line per part of KINO, the state in words and
// by the mark's shape, a way to the right setting, and a report to copy.
// The real interface scripts run on a stand-in page (tools/page_harness.js).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { loadPage, settle } = require('../tools/page_harness.js');

const html = require('../tools/interface_source.js').indexHtml();
const RESULT = {
  items: [
    { id: 'internet', label: 'Internet', state: 'ok', summary: 'Internet répond.', advice: '' },
    { id: 'debrid', label: 'Compte Real-Debrid', state: 'problem', summary: 'Votre abonnement Real-Debrid est terminé.', advice: 'Renouvelez-le.', section: 'settings-account' },
    { id: 'notifications', label: 'Notifications', state: 'warning', summary: 'macOS bloque les notifications de KINO.', advice: 'Autorisez-les.' },
    { id: 'trakt', label: 'Trakt <b>', state: 'off', summary: 'Non connecté.', advice: 'Facultatif.', section: "settings-sync');alert(1)//" },
  ],
  report: 'Diagnostic KINO · 11/11/2026 à 02:40\n[OK] Internet : Internet répond.',
};

async function open(answer = RESULT) {
  const page = await loadPage(url => (url.startsWith('/api/diagnostic') ? answer : {}));
  page.context.openConfig();
  await settle();
  return page;
}

test('Paramètres has a Diagnostic card and the Outils menu leads to it', () => {
  // The Diagnostic is a block of « Application », reached by its id.
  assert.match(html, /<div class="set-block" id="settings-diagnostic" aria-labelledby="settings-diagnostic-title">\s*<h3 class="set-block-title" id="settings-diagnostic-title">Diagnostic<\/h3>/);
  assert.match(html, /Rien n’est modifié\./);
  assert.match(html, /onclick="kinoMenuAction\('openDiagnostic'\)"[\s\S]{0,300}<span class="hdi-title">Diagnostic<\/span>/);
  assert.match(fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/icons.svg'), 'utf8'), /<symbol id="check" /);
});

test('each line says its state in words, with what to do and where', async () => {
  const page = await open();
  await page.context.runDiagnostic();
  const list = page.element('diagnosticList');
  assert.equal(list.hidden, false);
  assert.equal(page.element('diagnosticSummary').textContent, '1 problème et 1 point à vérifier.');
  const lines = list.innerHTML.split('</li>');
  assert.match(lines[0], /class="diagnostic-item is-ok"><span class="diagnostic-mark" aria-hidden="true">✓<\/span><div><strong>Internet<span class="diagnostic-state">OK<\/span>/);
  assert.doesNotMatch(lines[0], /Ouvrir/);
  assert.match(lines[1], /is-problem[\s\S]*>×<[\s\S]*Problème[\s\S]*Votre abonnement Real-Debrid est terminé\.[\s\S]*Renouvelez-le\.[\s\S]*onclick="openSettingsSection\('settings-account'\)">Ouvrir/);
  assert.match(lines[2], /is-warning[\s\S]*>!<[\s\S]*À vérifier/);
  assert.doesNotMatch(lines[2], /Ouvrir/, 'no section, no button');
  assert.match(lines[3], /is-off[\s\S]*>–<[\s\S]*Trakt &lt;b&gt;[\s\S]*Désactivé/);
  assert.doesNotMatch(lines[3], /alert|Ouvrir/, 'a strange section name gives no button');
  assert.equal(page.element('diagnosticCopyBtn').disabled, false);
  assert.equal(page.element('diagnosticRunBtn').textContent, 'Relancer le diagnostic');
});

test('all well says so', async () => {
  const page = await open({ items: [RESULT.items[0]], report: 'r' });
  await page.context.runDiagnostic();
  assert.equal(page.element('diagnosticSummary').textContent, 'Tout fonctionne.');
});

test('the report is copied as the server wrote it, or shown selected for ⌘C', async () => {
  const page = await open();
  await page.context.runDiagnostic();
  let copied = '';
  page.context.navigator.clipboard.writeText = async text => { copied = text; };
  await page.context.copyDiagnosticReport();
  assert.equal(copied, RESULT.report);
  assert.equal(page.element('diagnosticCopyStatus').textContent, 'Rapport copié.');
  assert.equal(page.element('diagnosticReport').hidden, true);
  let selected = false;
  page.element('diagnosticReport').select = () => { selected = true; };
  page.context.navigator.clipboard.writeText = async () => { throw new Error('denied'); };
  await page.context.copyDiagnosticReport();
  assert.equal(page.element('diagnosticReport').hidden, false);
  assert.equal(page.element('diagnosticReport').value, RESULT.report);
  assert.equal(selected, true);
  assert.equal(page.element('diagnosticCopyStatus').textContent, 'Le rapport est sélectionné : appuyez sur ⌘C pour le copier.');
});

test('a failed diagnostic says why, and the button comes back', async () => {
  const page = await open({ error: 'Délai dépassé' });
  await page.context.runDiagnostic();
  assert.equal(page.element('diagnosticSummary').textContent, 'Diagnostic impossible : Délai dépassé');
  assert.equal(page.element('diagnosticRunBtn').disabled, false);
  assert.equal(page.element('diagnosticCopyBtn').disabled, true);
});

test('from the Outils menu: Paramètres opens and the diagnostic runs', async () => {
  const page = await loadPage(url => (url.startsWith('/api/diagnostic') ? RESULT : {}));
  page.context.openDiagnostic();
  await settle();
  assert.equal(vm.runInContext('activeTab', page.context), 'settings');
  assert.equal(page.requests.filter(url => url === '/api/diagnostic').length, 1);
  assert.equal(page.element('diagnosticSummary').textContent, '1 problème et 1 point à vérifier.');
});
