// The whole interface passes tools/frontend_check.js: no call to a function that
// exists nowhere, no inline handler without its global function, no
// getElementById on an id that no page or template creates. Then the bugs that
// check found stay fixed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const checker = require('../tools/frontend_check.js');

const source = require('../tools/interface_source.js').appSource();
const html = require('../tools/interface_source.js').indexHtml();
const fn = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));

test('the interface calls only functions and ids that exist', () => {
  const result = checker.check(path.join(__dirname, '..'));
  assert.deepEqual(result.undefinedCalls, [], 'functions called but defined nowhere');
  assert.deepEqual(result.missingHandlers, [], 'inline handlers without a global function');
  assert.deepEqual(result.missingIds, [], 'ids looked up but never created');
});

test('the check finds each kind of mistake', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kino-frontend-check-'));
  try {
    fs.mkdirSync(path.join(root, 'web/js'), { recursive: true });
    fs.writeFileSync(path.join(root, 'web/index.html'), `
      <div id="known"></div>
      <button onclick="present()">ok</button>
      <button onclick="vanished('x')">broken</button>
      <script>window.fromPage = () => present();</script>`);
    // Top-level declarations start at the first column, as in the real files.
    fs.writeFileSync(path.join(root, 'web/js/a.js'), [
      'function present() {',
      '  const local = n => n + 1;',
      "  document.getElementById('known');",
      "  document.getElementById('missing');",
      '  el.innerHTML = `<div id="made"></div><a onclick="alsoGone()">x</a>`;',
      "  document.getElementById('made');",
      '  // gone() in a comment is ignored',
      "  const text = 'gone() in a string is ignored';",
      '  return local(1) + gone() + /gone\\(/.test(text);',
      '}',
    ].join('\n'));
    const result = checker.check(root);
    assert.deepEqual(result.undefinedCalls.map(item => item.name), ['gone']);
    assert.deepEqual(result.missingHandlers.map(item => item.name).sort(), ['alsoGone', 'vanished']);
    assert.deepEqual(result.missingIds.map(item => item.id), ['missing']);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('the tokenizer reads template expressions and skips regexes', () => {
  const { code, strings } = checker.tokenize('const a = `x${f(/[)]/.test(s) ? `y${g()}` : "z"}`;');
  assert.match(code, /\bf\s*\(/);
  assert.match(code, /\bg\s*\(/);
  assert.ok(strings.includes('z'));
  assert.doesNotMatch(code, /test\(s\)\s*\?\s*`/);
});

test('Explorer « Importer » fills the list address and starts the import', () => {
  const elements = {};
  const calls = [];
  const context = {
    document: { getElementById: id => (elements[id] ||= { value: '' }) },
    alert: message => calls.push(['alert', message]),
    openLetterboxdModal: () => calls.push(['modal']),
    switchLbxModalTab: tab => calls.push(['tab', tab]),
    submitLetterboxdCustomListImport: () => calls.push(['import', elements.lbxCustomListUrlInput.value]),
  };
  vm.runInNewContext(`${fn('async function importCustomLetterboxdFromInput()', 'function searchByPerson(')}; this.run = importCustomLetterboxdFromInput;`, context);
  elements.communityLetterboxdInput = { value: '  https://letterboxd.com/u/list/top/ ' };
  context.run();
  assert.deepEqual(calls, [['modal'], ['tab', 'list'], ['import', 'https://letterboxd.com/u/list/top/']]);
  assert.equal(elements.communityLetterboxdInput.value, '', 'the Explorer field is cleared');
  calls.length = 0;
  elements.communityLetterboxdInput.value = '';
  context.run();
  assert.deepEqual(calls.map(call => call[0]), ['alert']);
});

// Explorer's selections and imported lists: test_explore_page.js.

test('a Google Drive sync reloads the lists it brought back', async () => {
  const calls = [];
  const status = { innerHTML: '' };
  const context = {
    document: { getElementById: () => status },
    api: async () => ({ status: 'synced', updated_at: 1, history_count: 3, watchlist_count: 2 }),
    refreshUserLists: async () => calls.push('lists'),
    escapeHtml: value => String(value),
    Date,
  };
  vm.runInNewContext(`${fn('async function triggerGdriveSync(btn)', 'function toggleDiscordRpcFields()')}; this.sync = triggerGdriveSync;`, context);
  const button = { disabled: false, textContent: '' };
  await context.sync(button);
  assert.deepEqual(calls, ['lists']);
  assert.match(status.innerHTML, /3 films, 2 favoris/);
  assert.equal(button.disabled, false);
});

test('the Cloud tab and the picture-in-picture button are found by their ids', () => {
  assert.match(html, /id="subtab-rdcloud"/);
  assert.match(source, /getElementById\('subtab-rdcloud'\);\n\s*if \(cloudTab\) \{\n\s*cloudTab\.textContent = `Cloud \$\{pmeta\.short\}`;/);
  assert.match(withCalls(html), /id="inAppPipBtn" onclick="toggleInAppPiP\(\)"/);
  assert.doesNotMatch(source, /animePanel|'tab-rdcloud'|lbxListInput|importLetterboxdList\(|loadUserData/);
});
