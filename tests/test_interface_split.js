// The interface's main code is split by theme (docs/interface-files.md): the
// parts load in order, each is complete, and nothing run at load time needs a
// part loaded after it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));
const { appParts, interfaceScripts } = require('../tools/interface_source.js');

const parts = appParts();
const read = name => fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/js', name), 'utf8');
const doc = fs.readFileSync(path.join(path.join(__dirname, '..'), 'docs/dev/interface-files.md'), 'utf8');

test('eleven parts, loaded between the helpers and the players', () => {
  assert.deepEqual(parts, ['app-core.js', 'app-home.js', 'app-lists.js', 'app-letterboxd.js', 'app-tabs.js', 'app-cloud-settings.js',
    'app-social.js', 'app-fiche.js', 'app-sources.js', 'app-player.js', 'app-start.js']);
  const scripts = interfaceScripts();
  const at = name => scripts.indexOf(name);
  assert.ok(at('anime-journey.js') < at('app-core.js') && at('app-start.js') < at('stats.js') && at('app-start.js') < at('native-player.js'));
  assert.equal(fs.existsSync(path.join(path.join(__dirname, '..'), 'web/js/app.js')), false, 'no second copy');
});

test('each part is complete on its own', () => {
  for (const name of parts) {
    assert.doesNotThrow(() => new vm.Script(read(name), { filename: name }), name);
    assert.ok(read(name).endsWith('\n'), `${name} ends a line`);
  }
});

test('nothing run at load time calls a part loaded later', () => {
  const declaredIn = new Map();
  parts.forEach((name, index) => {
    for (const match of read(name).matchAll(/^(?:async )?function\*? ([\w$]+)|^(?:let|const|var|class) ([\w$]+)/gm)) declaredIn.set(match[1] || match[2], index);
  });
  const late = [];
  parts.forEach((name, index) => {
    for (const line of read(name).split('\n')) {
      const initializer = /^(?:let|const|var) [\w$]+\s*=\s*(.*)$/.exec(line);
      const runs = /^[A-Za-z_$(]/.test(line) && !/^(?:async function|function|let |const |var |class )/.test(line);
      const code = initializer ? initializer[1] : runs ? line : '';
      for (const match of code.matchAll(/\b([A-Za-z_$][\w$]*)\b/g)) {
        if (declaredIn.has(match[1]) && declaredIn.get(match[1]) > index) late.push(`${name}: ${match[1]} (${parts[declaredIn.get(match[1])]})`);
      }
    }
  });
  assert.deepEqual(late, []);
});

test('the start-up stays at the end, and the document lists every part', () => {
  assert.match(read('app-start.js'), /\nloadSpoilerState\(\)\.finally\(openFirstTab\);\n/);
  for (const name of parts) assert.match(doc, new RegExp(`\\| \`${name.replace('.', '\\.')}\` \\|`), name);
  assert.equal((doc.match(/^\| `app-[\w-]+\.js` \|/gm) || []).length, parts.length);
});
