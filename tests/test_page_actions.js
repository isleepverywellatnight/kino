// web/js/actions.js runs what the markup declares (data-on-click and its
// arguments) as the inline handlers did: from the target up, stopped where
// asked, with the element, the event and its value standing in.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const ROOT = path.join(__dirname, '..');
const actions = require('../tools/interface_source.js').scriptText('actions.js');
const { convert, formerCall } = require('../tools/inline_handlers.js');
const { registeredActions } = require('../tools/frontend_check.js');

function element(attributes = {}, parent = null) {
  return {
    nodeType: 1, parentElement: parent, value: 'chosen', checked: true, files: ['a.torrent'],
    getAttribute: name => (name in attributes ? attributes[name] : null),
    hasAttribute: name => name in attributes,
    closest: () => null,
  };
}

function page() {
  const listeners = {};
  const calls = [];
  const warnings = [];
  const context = {
    console: { warn: (...args) => warnings.push(args.join(' ')) },
    document: {
      addEventListener: (type, fn, capture) => (listeners[type] ||= []).push({ fn, capture: Boolean(capture) }),
      getElementById: id => ({ click: () => calls.push(['picked', id]) }),
      body: { classList: { contains: () => false } },
    },
  };
  context.window = context;
  vm.runInNewContext(actions, context);
  const fire = (type, target, extra = {}) => {
    const event = { type, target, cancelBubble: false, stopPropagation() { this.cancelBubble = true; }, ...extra };
    for (const l of listeners[type] || []) l.fn(event);
    return event;
  };
  return { context, calls, warnings, fire, listeners };
}

test('a click runs the actions from the target up, with their arguments', () => {
  const p = page();
  p.context.registerActions({
    switchTab: (...args) => p.calls.push(['switchTab', ...args]),
    openRow: (...args) => p.calls.push(['openRow', ...args]),
  });
  const row = element({ 'data-on-click': 'openRow', 'data-click-args': '[3]' });
  const button = element({ 'data-on-click': 'switchTab', 'data-click-args': '["movies", {"$": "this"}, {"$": "event"}, {"$": "value"}]' }, row);
  const event = p.fire('click', button);
  assert.deepEqual(p.calls.map(c => c[0]), ['switchTab', 'openRow'], 'target first, then its row');
  assert.equal(p.calls[0][2], button);
  assert.equal(p.calls[0][3], event);
  assert.equal(p.calls[0][4], 'chosen');
  assert.deepEqual(p.calls[1], ['openRow', 3]);
});

test('a stop keeps the event from the rows around', () => {
  const p = page();
  p.context.registerActions({ copyLink: () => p.calls.push('copy'), openRow: () => p.calls.push('row') });
  const row = element({ 'data-on-click': 'openRow' });
  const button = element({ 'data-on-click': 'copyLink', 'data-click-stop': '' }, row);
  p.fire('click', button);
  assert.deepEqual(p.calls, ['copy']);
});

test('loads, errors and closes are caught on their way down', () => {
  const p = page();
  assert.ok(p.listeners.error.every(l => l.capture) && p.listeners.close.every(l => l.capture));
  assert.ok(p.listeners.click.every(l => !l.capture));
  const image = element({ 'data-on-error': 'kinoHideImage', 'data-error-args': '[{"$": "this"}]' });
  p.fire('error', image);
  assert.equal(image.hidden, true);
});

test('only registered actions run, and broken arguments run nothing', () => {
  const p = page();
  // Functions the page holds are not actions until a file registers them.
  p.context.missing = () => p.calls.push('missing');
  for (const name of ['alert("x")', 'missing', 'constructor.constructor', 'toString']) {
    p.fire('click', element({ 'data-on-click': name }));
  }
  p.context.registerActions({ ok: () => p.calls.push('ran') });
  p.fire('click', element({ 'data-on-click': 'ok', 'data-click-args': '[broken' }));
  assert.deepEqual(p.calls, []);
  assert.equal(p.warnings.filter(w => w.startsWith('KINO: no action')).length, 4, p.warnings.join('\n'));
  assert.equal(p.warnings.filter(w => w.startsWith('KINO: unreadable arguments')).length, 1, p.warnings.join('\n'));
});

test('an action is registered once, and only as a function', () => {
  const p = page();
  p.context.registerActions({ once: () => {} });
  assert.throws(() => p.context.registerActions({ once: () => {} }), /registered twice/);
  assert.throws(() => p.context.registerActions({ notAFunction: 5 }), /not a function/);
});

test('the menu, the backdrop, Enter, a form and a file field', () => {
  const p = page();
  for (const name of ['openConfig', 'closeTrailerModal', 'sendWpMessage', 'searchManga']) {
    p.context.registerActions({ [name]: () => p.calls.push(name) });
  }
  // The header menu's wrapper is the menu's own (app-cloud-settings.js).
  const menu = require('../tools/interface_source.js').scriptText('app-cloud-settings.js');
  const start = menu.indexOf('function kinoMenuAction(name) {');
  p.context.closeHeaderMenu = () => p.calls.push('closeHeaderMenu');
  vm.runInContext(`${menu.slice(start, menu.indexOf('\n}\n', start) + 3)} registerActions({ kinoMenuAction });`, p.context);
  p.fire('click', element({ 'data-on-click': 'kinoMenuAction', 'data-click-args': '["openConfig"]' }));
  const backdrop = element({ 'data-on-click': 'kinoOnBackdrop', 'data-click-args': '[{"$": "event"}, {"$": "this"}, "closeTrailerModal"]' });
  p.fire('click', element({}, backdrop));
  p.fire('click', backdrop);
  const field = element({ 'data-on-keydown': 'kinoOnEnter', 'data-keydown-args': '[{"$": "event"}, "sendWpMessage"]' });
  p.fire('keydown', field, { key: 'a' });
  p.fire('keydown', field, { key: 'Enter' });
  let prevented = false;
  p.fire('submit', element({ 'data-on-submit': 'kinoSubmit', 'data-submit-args': '[{"$": "event"}, "searchManga"]' }),
    { preventDefault: () => { prevented = true; } });
  p.fire('click', element({ 'data-on-click': 'kinoPickFile', 'data-click-args': '["addonFileInput"]' }));
  assert.deepEqual(p.calls, ['openConfig', 'closeHeaderMenu', 'closeTrailerModal', 'sendWpMessage', 'searchManga', ['picked', 'addonFileInput']],
    'the backdrop closes only for a click on itself, Enter only');
  assert.ok(prevented, 'the page does not reload');
});

test('index.html has no inline code left, and each action is registered', () => {
  const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
  assert.doesNotMatch(html, /\son[a-z]+=["']/i);
  // i18n.js translates the page as it is drawn; KINO itself is one module.
  assert.match(html, /<script src="\/js\/i18n\.js"><\/script>/);
  assert.equal((html.match(/<script type="module"/g) || []).length, 1);
  assert.match(html, /<script type="module" src="\/js\/main\.js"><\/script>/);
  assert.equal(require('../tools/interface_source.js').interfaceScripts()[1], 'actions.js', 'the actions first');
  const scripts = fs.readdirSync(path.join(ROOT, 'web/js')).filter(f => f.endsWith('.js'))
    .map(f => fs.readFileSync(path.join(ROOT, 'web/js', f), 'utf8')).join('\n');
  const names = new Set([...html.matchAll(/data-on-[a-z]+="([^"]+)"/g)].map(m => m[1]));
  assert.ok(names.size > 100);
  const registered = registeredActions([scripts]);
  for (const name of names) assert.ok(registered.has(name), name);
  for (const [, args] of html.matchAll(/data-[a-z]+-args='([^']*)'/g)) JSON.parse(args);
});

test('the conversion keeps the call: its function, its arguments, its stop', () => {
  for (const code of ["switchTab('movies', this)", 'setFilter(\'4k\', this)', 'tvControl("pause")', 'event.stopPropagation(); copyLink(3)',
    'setP2PSetting(\'cap\', this.value)', 'toggleSpoiler(this.checked)', 'handleTorrentFileSelect(this.files)', 'submitLetterboxdImport({ restoreRemoved: true })',
    "selectGenre('Films d\\'Animation', this)", 'Spoilers.reveal(2, 5)']) {
    const [name, args, stop] = convert(code);
    const former = formerCall(code, name);
    assert.equal(former.count, 1, code);
    assert.equal(JSON.stringify(args), JSON.stringify(former.args), code);
    assert.equal(stop, former.stopped, code);
  }
  assert.equal(convert('alert(1); evil()'), null, 'two calls: not converted');
  assert.deepEqual(convert("openConfig(); closeHeaderMenu();"), ['kinoMenuAction', ['openConfig'], false]);
});

test('each action a template declares is registered', () => {
  const dir = path.join(ROOT, 'web/js');
  const scripts = fs.readdirSync(dir).filter(f => f.endsWith('.js')).map(f => fs.readFileSync(path.join(dir, f), 'utf8')).join('\n');
  const names = new Set([
    ...[...scripts.matchAll(/\bact\('([\w$.]+)'/g)].map(m => m[1]),
    ...[...scripts.matchAll(/\bactOn\('[a-z]+', '([\w$.]+)'/g)].map(m => m[1]),
  ]);
  assert.ok(names.size >= 10, [...names].join(', '));
  const registered = registeredActions([scripts]);
  for (const name of names) assert.ok(registered.has(name), name);
});

test('a template action carries its arguments as values', () => {
  const { templateHelpers, withCalls } = require('../tools/interface_source.js');
  const { act, actOn, THIS, EVENT } = templateHelpers();
  const html = `<b ${act('toggleSourceGroup', `4k" onclick="x`)}></b><i ${actOn('error', 'kinoRemove', THIS)}></i><u ${act('copyLink', EVENT, 3)}></u>`;
  assert.doesNotMatch(withCalls(html), /onclick="x/, 'an argument stays inside its attribute');
  assert.equal(withCalls(html), `<b onclick="toggleSourceGroup('4k&quot; onclick=&quot;x')"></b><i onerror="kinoRemove(this)"></i><u onclick="copyLink(event, 3)"></u>`);
});

test('the phone remote has no inline code either, and loads its code as files', () => {
  const remote = fs.readFileSync(path.join(ROOT, 'web/remote.html'), 'utf8');
  assert.doesNotMatch(remote, /\son[a-z]+=["']/i);
  assert.doesNotMatch(remote, /<script(?![^>]*\bsrc=)[^>]*>/, 'no inline script');
  assert.deepEqual([...remote.matchAll(/<script\b[^>]*>/g)].map(m => m[0]), ['<script type="module" src="/js/remote.js">']);
  const imports = [...fs.readFileSync(path.join(ROOT, 'web/js/remote.js'), 'utf8').matchAll(/^import .* from '(\.\/[\w-]+\.js)';$/gm)].map(m => m[1]);
  assert.deepEqual(imports, ['./actions.js', './html.js']);
});
