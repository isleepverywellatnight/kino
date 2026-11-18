// Notifications of new episodes, page side: the setting appears only where
// macOS notifications exist; the test button explains macOS's answer; a click
// on a notification opens the series.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const code = source.slice(source.indexOf('// --- Notifications of new episodes (macOS) ---'), source.indexOf('// From the setup notice: straight to the key field.'));

function load(answer) {
  const status = { textContent: '' };
  const calls = [];
  const context = {
    activeTab: 'movies',
    document: { getElementById: id => (id === 'cfgNotificationStatus' ? status : null) },
    api: async (url, options) => { calls.push([url, options?.method]); if (answer instanceof Error) throw answer; return answer; },
    switchTab: async tab => { calls.push(['tab', tab]); context.activeTab = tab; },
    selectMedia: async media => calls.push(['fiche', JSON.parse(JSON.stringify(media))]),
  };
  vm.runInNewContext(`${code}\nthis.test = testEpisodeNotification; this.open = openFromNotification;`, context);
  return { context, status, calls };
}

test('the test button explains what macOS answered', async () => {
  for (const [answer, expected] of [
    [{ ok: true, status: 'authorized' }, /Notification envoyée/],
    [{ ok: false, status: 'denied' }, /Réglages Système → Notifications → KINO/],
    [{ ok: false, status: 'unavailable' }, /que dans l’application KINO pour Mac/],
    [new Error('hors ligne'), /^Essai impossible : hors ligne$/],
  ]) {
    const page = load(answer);
    const button = { disabled: false };
    await page.context.test(button);
    assert.match(page.status.textContent, expected);
    assert.equal(button.disabled, false);
  }
  assert.deepEqual(load({ status: 'authorized' }).calls, []);
});

test('a click on a notification opens the series', async () => {
  const page = load({});
  await page.context.open({ id: 'tt14452776', name: 'The Bear', season: '3' });
  assert.deepEqual(page.calls, [['tab', 'series'], ['fiche', { id: 'tt14452776', name: 'The Bear', type: 'series' }]]);
  const bad = load({});
  await bad.context.open({ id: 'javascript:alert(1)' });
  assert.deepEqual(bad.calls, []);
});

test('the setting: shown only where notifications exist, saved', () => {
  assert.match(html, /<div class="set-row" id="cfgEpisodeNotificationsField" style="display:none;">[\s\S]*?Notifications des nouveaux épisodes[\s\S]*?onclick="testEpisodeNotification\(this\)">Envoyer un essai<\/button>[\s\S]*?<input type="checkbox" id="cfgEpisodeNotifications" role="switch" checked>/);
  assert.match(source, /notifyField\.style\.display = cfg\.notifications_available === true \? 'flex' : 'none';/);
  assert.match(source, /episode_notifications: checked\('cfgEpisodeNotifications', true\),/);
});
