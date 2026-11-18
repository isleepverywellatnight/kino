'use strict';
// Every window of the page is announced as one (role="dialog", aria-modal),
// names itself with a title that exists, and has a named close button.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');

test('each backdrop holds a labelled dialog with a named close button', () => {
  const backdrops = [...html.matchAll(/<div id="(\w+)" class="(?:modal-bg|shortcuts-modal)"[^>]*>\s*<div ([^>]*)>/g)];
  assert.ok(backdrops.length >= 8, `found ${backdrops.length} windows`);
  for (const [, id, attrs] of backdrops) {
    assert.match(attrs, /role="dialog"/, `${id}: role="dialog"`);
    assert.match(attrs, /aria-modal="true"/, `${id}: aria-modal`);
    const label = /aria-labelledby="(\w+)"/.exec(attrs);
    assert.ok(label, `${id}: aria-labelledby`);
    assert.ok(html.includes(`id="${label[1]}"`), `${id}: its title #${label[1]} exists`);
  }
  for (const button of html.matchAll(/<button[^>]*class="modal-close"[^>]*>/g)) {
    assert.match(button[0], /aria-label="[^"]+"/, `close button named: ${button[0]}`);
  }
});

test('the player message is announced politely', () => {
  assert.match(html, /id="inAppStatusToast" class="inapp-toast" role="status" aria-live="polite"/);
});
