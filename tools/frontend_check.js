// Static checks for KINO's web interface, without any dependency:
//  - functions called in the scripts but defined nowhere;
//  - inline handlers (onclick="…") calling a function that is not global;
//  - getElementById ids that exist neither in index.html nor in a template.
// A rough tokenizer separates code from strings, regexes and comments, but
// reads template expressions (${…}) and the handlers written inside strings.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const KEYWORDS = new Set(('if for while switch catch return typeof function class new await async delete void in of '
  + 'instanceof do else try finally throw yield super this import export case default break continue let const var with').split(' '));
// Browser and language globals, and libraries loaded from <script> tags.
const BUILTINS = new Set(('setTimeout clearTimeout setInterval clearInterval requestAnimationFrame cancelAnimationFrame queueMicrotask '
  + 'fetch alert confirm prompt parseInt parseFloat isNaN isFinite encodeURIComponent decodeURIComponent encodeURI decodeURI escape '
  + 'Option Number String Boolean Array Object Symbol Map Set WeakMap WeakSet Date RegExp Error TypeError RangeError Promise Proxy Reflect JSON Math BigInt '
  + 'URL URLSearchParams AbortController DOMParser Blob File FileReader FormData Image Audio Event CustomEvent KeyboardEvent MouseEvent PointerEvent '
  + 'MutationObserver ResizeObserver IntersectionObserver Intl TextEncoder TextDecoder Uint8Array Float32Array ArrayBuffer DataView '
  + 'getComputedStyle structuredClone atob btoa matchMedia require Notification WebSocket Worker AudioContext webkitAudioContext '
  + 'OscillatorNode GainNode MediaSource SourceBuffer VTTCue TextTrack '
  + 'DOMException Peer QRCode Hls').split(' '));

function isIdentStart(ch) { return /[A-Za-z_$]/.test(ch); }
function isIdent(ch) { return /[A-Za-z0-9_$]/.test(ch); }

// Splits source into code (with template expressions) and string texts.
function tokenize(src) {
  const code = [];      // code characters, strings replaced by spaces
  const strings = [];   // contents of string literals and template text
  let i = 0;
  let lastSignificant = '';
  let lastWord = '';
  const out = ch => { code.push(ch); if (!/\s/.test(ch)) lastSignificant = ch; };
  const regexAllowed = () => !lastSignificant || '(,=:[!&|?{};+-*%<>~^'.includes(lastSignificant)
    || ['return', 'typeof', 'case', 'in', 'of', 'delete', 'void', 'throw'].includes(lastWord);
  function readTemplate() {
    // at a backtick; returns after the closing backtick
    i++;
    let text = '';
    while (i < src.length) {
      const ch = src[i];
      if (ch === '\\') { text += src.slice(i, i + 2); i += 2; continue; }
      if (ch === '`') { i++; break; }
      if (ch === '$' && src[i + 1] === '{') {
        strings.push(text); text = '';
        i += 2;
        out(' ');
        lastSignificant = '{';  // an expression starts: a regex may follow
        readCode('}');
        out(' ');
        continue;
      }
      text += ch; i++;
    }
    strings.push(text);
  }
  function readString(quote) {
    i++;
    let text = '';
    while (i < src.length && src[i] !== quote) {
      if (src[i] === '\\') { text += src.slice(i, i + 2); i += 2; continue; }
      if (src[i] === '\n') break;
      text += src[i]; i++;
    }
    i++;
    strings.push(text);
  }
  function readRegex() {
    i++;
    let inClass = false;
    while (i < src.length) {
      const ch = src[i];
      if (ch === '\\') { i += 2; continue; }
      if (ch === '[') inClass = true;
      else if (ch === ']') inClass = false;
      else if (ch === '/' && !inClass) { i++; break; }
      else if (ch === '\n') break;
      i++;
    }
    while (i < src.length && /[a-z]/i.test(src[i])) i++;
  }
  function readCode(until) {
    let depth = 0;
    while (i < src.length) {
      const ch = src[i];
      if (until && ch === until && depth === 0) { i++; return; }
      if (ch === '{') depth++;
      if (ch === '}') depth--;
      if (ch === '/' && src[i + 1] === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
      if (ch === '/' && src[i + 1] === '*') { const end = src.indexOf('*/', i + 2); i = end < 0 ? src.length : end + 2; continue; }
      if (ch === '"' || ch === "'") { out(' '); readString(ch); out(' '); lastSignificant = 'x'; lastWord = ''; continue; }
      if (ch === '`') { out(' '); readTemplate(); out(' '); lastSignificant = 'x'; lastWord = ''; continue; }
      if (ch === '/' && regexAllowed()) { out(' '); readRegex(); out(' '); lastSignificant = 'x'; lastWord = ''; continue; }
      if (isIdentStart(ch)) {
        let word = '';
        while (i < src.length && isIdent(src[i])) { word += src[i]; out(src[i]); i++; }
        lastWord = word;
        continue;
      }
      out(ch);
      if (!/\s/.test(ch)) lastWord = '';
      i++;
    }
  }
  readCode(null);
  return { code: code.join(''), strings };
}

// Names bound anywhere: declarations, parameters, destructuring, methods.
function definedNames(code) {
  const names = new Set();
  const add = list => list.split(/[^A-Za-z0-9_$]+/).filter(name => name && isIdentStart(name[0]) && !KEYWORDS.has(name)).forEach(name => names.add(name));
  for (const m of code.matchAll(/\b(?:function\*?|class)\s+([A-Za-z_$][\w$]*)/g)) names.add(m[1]);
  for (const m of code.matchAll(/\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g)) names.add(m[1]);
  for (const m of code.matchAll(/\b(?:const|let|var)\s*([{[][^=;]*?[}\]])\s*=/g)) add(m[1].replace(/:\s*[A-Za-z_$][\w$]*\s*(?=[,}])/g, m2 => ', ' + m2.split(':')[1]).replace(/=[^,}\]]*/g, ''));
  for (const m of code.matchAll(/\bfunction\*?\s*[\w$]*\s*\(([^)]*)\)/g)) add(m[1].replace(/=[^,)]*/g, ''));
  for (const m of code.matchAll(/\(([^()]*)\)\s*=>/g)) add(m[1].replace(/=[^,]*/g, ''));
  for (const m of code.matchAll(/([A-Za-z_$][\w$]*)\s*=>/g)) names.add(m[1]);
  for (const m of code.matchAll(/\bcatch\s*\(\s*([A-Za-z_$][\w$]*)/g)) names.add(m[1]);
  for (const m of code.matchAll(/\b(?:window|globalThis|self)\.([A-Za-z_$][\w$]*)\s*=/g)) names.add(m[1]);
  // Object and class methods: name(args) { … }
  for (const m of code.matchAll(/([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{/g)) names.add(m[1]);
  return names;
}

// Names callable from an inline handler: top-level declarations and window.X.
function globalNames(srcFiles) {
  const names = new Set();
  for (const src of srcFiles) {
    for (const m of src.matchAll(/^(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)/gm)) names.add(m[1]);
    for (const m of src.matchAll(/^(?:const|let|var)\s+([A-Za-z_$][\w$]*)/gm)) names.add(m[1]);
    for (const m of src.matchAll(/^class\s+([A-Za-z_$][\w$]*)/gm)) names.add(m[1]);
    for (const m of src.matchAll(/\b(?:window|globalThis)\.([A-Za-z_$][\w$]*)\s*=/g)) names.add(m[1]);
  }
  return names;
}

// Bare calls name( in code (not obj.name(, not a declaration).
function calledNames(code) {
  const calls = new Map();
  for (const m of code.matchAll(/(^|[^\w$.])([A-Za-z_$][\w$]*)\s*\(/g)) {
    const name = m[2];
    if (KEYWORDS.has(name)) continue;
    const before = code.slice(Math.max(0, m.index - 20), m.index + m[1].length);
    if (/\b(?:function\*?|class|async)\s*$/.test(before)) continue;
    calls.set(name, (calls.get(name) || 0) + 1);
  }
  return calls;
}

// Handlers written as attributes: onclick="foo(1)" or onclick='foo(…)'.
function handlerCalls(texts) {
  const calls = new Map();
  for (const text of texts) {
    for (const m of text.matchAll(/\bon[a-z]+\s*=\s*(["'])([\s\S]*?)\1/g)) {
      // Quoted text inside a handler (styles such as 'rgba(…)') is not a call.
      const body = m[2].replace(/(['"])(?:\\.|(?!\1).)*\1/g, ' ').replace(/&quot;[\s\S]*?&quot;|&#39;[\s\S]*?&#39;/g, ' ');
      for (const c of body.matchAll(/(^|[^\w$.])([A-Za-z_$][\w$]*)\s*\(/g)) {
        if (KEYWORDS.has(c[2])) continue;
        calls.set(c[2], (calls.get(c[2]) || 0) + 1);
      }
    }
  }
  return calls;
}

function check(root) {
  const webDir = path.join(root, 'web');
  // The pages: the window's and the phone remote's.
  const pages = ['index.html', 'remote.html'].filter(name => fs.existsSync(path.join(webDir, name)))
    .map(name => ({ name, html: fs.readFileSync(path.join(webDir, name), 'utf8') }));
  const html = pages.map(page => page.html).join('\n');
  const jsFiles = fs.readdirSync(path.join(webDir, 'js')).filter(name => name.endsWith('.js'))
    .map(name => ({ name, src: fs.readFileSync(path.join(webDir, 'js', name), 'utf8') }));
  const inlineScripts = pages.flatMap(page => [...page.html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
    .map((m, index) => ({ name: `${page.name}#script${index + 1}`, src: m[1] })));
  const sources = [...jsFiles, ...inlineScripts];
  const parsed = sources.map(file => ({ ...file, ...tokenize(file.src) }));

  const defined = new Set(BUILTINS);
  for (const file of parsed) definedNames(file.code).forEach(name => defined.add(name));
  const globals = new Set([...BUILTINS, ...globalNames(sources.map(file => file.src))]);

  const undefinedCalls = [];
  for (const file of parsed) {
    for (const [name, count] of calledNames(file.code)) {
      if (!defined.has(name)) undefinedCalls.push({ file: file.name, name, count });
    }
  }
  const handlerTexts = [html.replace(/<script[\s\S]*?<\/script>/g, ''), ...parsed.flatMap(file => file.strings)];
  const missingHandlers = [];
  for (const [name, count] of handlerCalls(handlerTexts)) {
    if (!globals.has(name)) missingHandlers.push({ name, count });
  }
  // Declared actions (web/js/actions.js runs them): data-on-click="name" in a page,
  // act('name', …) and actOn('type', 'name', …) in a template.
  const actions = new Map();
  const count = name => actions.set(name, (actions.get(name) || 0) + 1);
  for (const m of html.matchAll(/data-on-[a-z]+="([\w$.]+)"/g)) count(m[1]);
  for (const file of jsFiles) {
    for (const m of file.src.matchAll(/\bact\('([\w$.]+)'/g)) count(m[1]);
    for (const m of file.src.matchAll(/\bactOn\('[a-z]+', '([\w$.]+)'/g)) count(m[1]);
  }
  for (const [name, n] of actions) {
    if (!globals.has(name.split('.')[0])) missingHandlers.push({ name, count: n });
  }

  const htmlIds = new Set([...html.matchAll(/\bid\s*=\s*["']([^"'$]+)["']/g)].map(m => m[1]));
  const templateIds = new Set();
  for (const file of parsed) {
    for (const text of file.strings) for (const m of text.matchAll(/\bid\s*=\s*["']?([A-Za-z][\w-]*)/g)) templateIds.add(m[1]);
    for (const m of file.src.matchAll(/\.id\s*=\s*['"]([\w-]+)['"]/g)) templateIds.add(m[1]);
    for (const m of file.src.matchAll(/\bid:\s*['"]([\w-]+)['"]/g)) templateIds.add(m[1]);
  }
  const missingIds = [];
  for (const file of sources) {
    for (const m of file.src.matchAll(/getElementById\(\s*['"]([\w-]+)['"]\s*\)/g)) {
      const id = m[1];
      if (!htmlIds.has(id) && !templateIds.has(id) && !missingIds.some(item => item.id === id)) missingIds.push({ file: file.name, id });
    }
  }
  return { undefinedCalls, missingHandlers, missingIds };
}

module.exports = { tokenize, definedNames, calledNames, handlerCalls, globalNames, check };

if (require.main === module) {
  const result = check(path.resolve(__dirname, '..'));
  console.log(JSON.stringify(result, null, 2));
}
