// The bridge's types (web/types/bridge.d.ts) name what the desktop app answers:
// the names it lets the page call (BRIDGE, crates/kino-desktop/src/main.rs), the
// ones it decodes (BridgeCall::parse, bridge.rs), and the ones the page calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const ROOT = path.join(__dirname, '..');
const read = name => fs.readFileSync(path.join(ROOT, name), 'utf8');

function typedNames(dts) {
  const start = dts.indexOf('interface KinoBridge {');
  assert.ok(start >= 0, 'interface KinoBridge');
  const body = dts.slice(start, dts.indexOf('\n}\n', start));
  return [...body.matchAll(/^ {2}([a-z_]+)\(/gm)].map(m => m[1]).sort();
}

function allowedNames(mainRs) {
  const list = /const BRIDGE: \[&str; (\d+)\] = \[([\s\S]*?)\];/.exec(mainRs);
  assert.ok(list, 'BRIDGE in main.rs');
  const names = [...list[2].matchAll(/"([a-z_]+)"/g)].map(m => m[1]);
  assert.equal(names.length, Number(list[1]), 'BRIDGE says how many it holds');
  return names.sort();
}

function decodedNames(bridgeRs) {
  const start = bridgeRs.indexOf('pub fn parse(name: &str, args: &[Value])');
  assert.ok(start >= 0, 'BridgeCall::parse');
  const body = bridgeRs.slice(start, bridgeRs.indexOf('\n    }\n', start));
  return [...body.matchAll(/^ {12}"([a-z_]+)" => /gm)].map(m => m[1]).sort();
}

test('the types, the app\'s list and its decoder name the same calls', () => {
  const typed = typedNames(read('web/types/bridge.d.ts'));
  const allowed = allowedNames(read('crates/kino-desktop/src/main.rs'));
  const decoded = decodedNames(read('crates/kino-desktop/src/bridge.rs'));
  assert.ok(allowed.length >= 20, allowed.join(', '));
  assert.deepEqual(typed, allowed, 'bridge.d.ts and BRIDGE');
  assert.deepEqual(decoded, allowed, 'BridgeCall::parse and BRIDGE');
});

test('the page calls only what the bridge offers', () => {
  const allowed = new Set(allowedNames(read('crates/kino-desktop/src/main.rs')));
  const dir = path.join(ROOT, 'web/js');
  const called = new Set();
  for (const name of fs.readdirSync(dir).filter(file => file.endsWith('.js'))) {
    const text = fs.readFileSync(path.join(dir, name), 'utf8');
    for (const m of text.matchAll(/pywebview\??\.api\??\.([A-Za-z_$][\w$]*)/g)) called.add(m[1]);
  }
  assert.ok(called.size >= 8, [...called].join(', '));
  assert.deepEqual([...called].filter(name => !allowed.has(name)), []);
});

test('the page\'s window has the typed bridge', () => {
  assert.match(read('web/types/globals.d.ts'), /pywebview\?: \{ api: KinoBridge; platform: string \};/);
  assert.match(read('jsconfig.json'), /"web\/types\/\*\.d\.ts"/, 'the types are read by the check');
});
