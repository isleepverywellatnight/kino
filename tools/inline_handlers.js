// Moves the inline handlers of a page (onclick="…") to declared actions read by
// web/js/actions.js: data-on-click="name" and data-click-args='[…]'.
// Usage: node tools/inline_handlers.js web/index.html [--check]
// Each simple call is converted and checked: the former code, run with stand-ins,
// and the new attributes, read as actions.js reads them, make the same call.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const LITERAL = String.raw`'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null|\{[^{}]*\}`;
const TOKEN = String.raw`this\.value|this\.checked|this\.files|this|event`;
const ARG = `(?:${LITERAL}|${TOKEN})`;
const CALL = new RegExp(String.raw`^\s*(event\.stopPropagation\(\);\s*)?([A-Za-z_$][\w$.]*)\(\s*((?:${ARG})(?:\s*,\s*(?:${ARG}))*)?\s*\)\s*;?\s*$`);
const SPLIT = new RegExp(String.raw`\s*(${ARG})\s*(?:,|$)`, 'g');
const SPECIAL = { 'this': { $: 'this' }, 'event': { $: 'event' }, 'this.value': { $: 'value' }, 'this.checked': { $: 'checked' }, 'this.files': { $: 'files' } };

// The few words of code that are not one call, and the action each became.
const PATTERNS = [
  [/^\s*([A-Za-z_$][\w$]*)\(\);\s*closeHeaderMenu\(\);\s*$/, m => ['kinoMenuAction', [m[1]]]],
  [/^\s*if\s*\(\s*event\.target\s*===\s*this\s*\)\s*([A-Za-z_$][\w$]*)\(\)\s*;?\s*$/, m => ['kinoOnBackdrop', [SPECIAL.event, SPECIAL.this, m[1]]]],
  [/^\s*if\s*\(\s*event\.key\s*===\s*'Enter'\s*\)\s*([A-Za-z_$][\w$]*)\(\)\s*;?\s*$/, m => ['kinoOnEnter', [SPECIAL.event, m[1]]]],
  [/^\s*event\.preventDefault\(\);\s*([A-Za-z_$][\w$]*)\(\);?\s*$/, m => ['kinoSubmit', [SPECIAL.event, m[1]]]],
  [/^\s*document\.getElementById\('([\w-]+)'\)\.click\(\)\s*;?\s*$/, m => ['kinoPickFile', [m[1]]]],
  [/^this\.hidden = true$/, () => ['kinoHideImage', [SPECIAL.this]]],
  [/^if \(!event\.target\.closest\('\.no-drag'\)\) windowAction\('maximize'\)$/, () => ['kinoBarDoubleClick', [SPECIAL.event]]],
  [/^if\(document\.body\.classList\.contains\('is-fullscreen'\)\|\|Boolean\(document\.fullscreenElement\|\|document\.webkitFullscreenElement\)\)\{toggleInAppFullscreen\(\);\}else\{windowAction\('maximize'\);\}$/, () => ['kinoPlayerDoubleClick', []]],
  [/^surpriseMeMedia\(activeTab === 'classics' \? 'classics' : 'catalog'\)$/, () => ['kinoSurprise', []]],
  [/^setGlobalSpoilerProtection\(this\.value === 'on'\)$/, () => ['kinoSpoilerSetting', [SPECIAL.value]]],
  [/^if \(p2pConsentAnswer\) answerP2PConsent\(false\)$/, () => ['kinoP2PConsentClosed', []]],
];

const decode = text => text.replaceAll('&quot;', '"').replaceAll('&#39;', "'").replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&amp;', '&');
// JSON safe inside a single-quoted attribute.
const attrJson = value => JSON.stringify(value).replace(/[&'<>]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);

function literal(text) {
  return vm.runInNewContext(`(${text})`, Object.create(null));
}

/// [name, args, stop] for one handler's code, or null.
function convert(code) {
  const call = CALL.exec(code);
  if (call) {
    const args = [];
    for (const [, arg] of (call[3] || '').matchAll(SPLIT)) {
      if (!arg) continue;
      args.push(SPECIAL[arg] || literal(arg));
    }
    return [call[2], args, Boolean(call[1])];
  }
  for (const [re, make] of PATTERNS) {
    const m = re.exec(code);
    if (m) return [...make(m), false];
  }
  return null;
}

// The call the former code makes, with stand-ins for the element and the event.
function formerCall(code, name) {
  const element = { value: 'VALUE', checked: 'CHECKED', files: 'FILES' };
  const event = { stopPropagation() { this.stopped = true; } };
  const calls = [];
  const head = name.split('.')[0];
  const recorder = (...args) => calls.push(args);
  const context = { event, [head]: name.includes('.') ? new Proxy({}, { get: () => recorder }) : recorder };
  vm.runInNewContext(`(function () { ${code} }).call(__element)`, Object.assign(context, { __element: element }));
  const resolve = a => a === element ? { $: 'this' } : a === event ? { $: 'event' } : a === 'VALUE' ? { $: 'value' }
    : a === 'CHECKED' ? { $: 'checked' } : a === 'FILES' ? { $: 'files' } : a;
  return { args: (calls[0] || []).map(resolve), count: calls.length, stopped: Boolean(event.stopped) };
}

function rewrite(file) {
  const html = fs.readFileSync(file, 'utf8');
  const left = [];
  let converted = 0;
  const out = html.replace(/<[a-zA-Z][^<>]*\son[a-z]+=(?:"[^"]*"|'[^']*')[^<>]*>/g, tag => {
    return tag.replace(/\son([a-z]+)=("([^"]*)"|'([^']*)')/g, (whole, type, _q, dq, sq) => {
      const code = decode(dq ?? sq);
      const found = convert(code);
      if (!found) {
        left.push(`on${type}="${code}"`);
        return whole;
      }
      const [name, args, stop] = found;
      if (CALL.test(code)) {
        const former = formerCall(code, name);
        const same = former.count === 1 && JSON.stringify(former.args) === JSON.stringify(args) && former.stopped === stop;
        if (!same) throw new Error(`not the same call: ${code} → ${name} ${JSON.stringify(args)}`);
      }
      converted++;
      return ` data-on-${type}="${name}"${args.length ? ` data-${type}-args='${attrJson(args)}'` : ''}${stop ? ` data-${type}-stop` : ''}`;
    });
  });
  return { out, converted, left };
}

if (require.main === module) {
  const file = path.resolve(process.argv[2] || path.join(ROOT, 'web', 'index.html'));
  const { out, converted, left } = rewrite(file);
  console.log(`${converted} handlers converted, ${left.length} left`);
  for (const l of left) console.log('  left:', l);
  if (!process.argv.includes('--check')) fs.writeFileSync(file, out);
}

module.exports = { convert, formerCall, rewrite };
