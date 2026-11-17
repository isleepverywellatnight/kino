#!/usr/bin/env node
// KINO's interface translations.
//
// The interface is written in French. web/js/i18n.js translates it while it is
// displayed, with one dictionary per language: web/js/i18n/<lang>.js, which maps
// each French text to its translation. "{}" stands for a variable part:
// "{} épisodes" -> "{} episodes" (use {1}, {2}… to reorder them).
//
// The server (crates/, Rust) also writes French that the interface shows: its
// error messages and notices. Its French string literals are listed too.
//
//   node tools/i18n.js extract      list the French texts of web/ and crates/ in web/js/i18n/strings.json
//   node tools/i18n.js status       count, per language, the texts still untranslated
//   node tools/i18n.js missing de   print the texts German still lacks (JSON)
//
// A missing text stays in French: translations can be completed little by little.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const PROJECT = path.join(__dirname, '..');
const WEB = path.join(PROJECT, 'web');
const CRATES = path.join(PROJECT, 'crates');
const I18N = path.join(WEB, 'js', 'i18n');
const CATALOG = path.join(I18N, 'strings.json');
const PREFIX = 'KINO_I18N.load(';
const SUFFIX = ');\n';
const ATTRIBUTES = ['placeholder', 'title', 'aria-label', 'alt', 'data-tooltip', 'data-label'];
const W = '[\\p{L}\\p{N}_]';
// Python's \b: a change between a word character (any letter or digit, any
// script) and anything else. JavaScript's \b only knows ASCII letters.
const B = `(?:(?<=${W})(?!${W})|(?<!${W})(?=${W}))`;
const re = (source, flags = '') => new RegExp(source.replaceAll('\\b', B), `u${flags}`);
// Words that only appear in code: a literal made of them alone is not shown text.
const CODE_HINTS = re(String.raw`=>|\bfunction\b|\breturn\b|;\s*$|^\s*[.#[]|var\(--|rgba?\(|\bpx\b|https?:\/\/|^\S*\.(js|css|svg|png|json)\b\S*$|^[a-z]+[A-Z][\p{L}\p{N}_]*$|^[\p{L}\p{N}_-]+=|^\{\}$|^[a-z0-9_-]+$`);
// JavaScript caught between two literals: calls, operators, statements.
const CODE_SYNTAX = re(String.raw`[A-Za-z_$]\((?!s\))|\)\s*[;{]|\)\.[A-Za-z_$]|[;{}]\s*$|^\s*[;)\]}]|==|&&|\|\||\?\s*'|\b(const|let|var|if|else)\s+[\p{L}\p{N}_(]|\.(style|push|join|map|get|set|forEach|classList)\b`);
const LETTER = /[A-Za-zÀ-ÖØ-öø-ÿ]/u;
const FRENCH_HINT = /[À-ÖØ-öø-ÿ«»’]|^[A-ZÀ-Ö]|\s/u;
// The server also writes English (logs, internal errors): only French reaches the catalog.
const FRENCH = re(String.raw`[À-ÿŒœ«»’]|\b(?:l'|d'|le|la|les|de|des|du|un|une|ce|cette|est|pas|aucun|aucune|pour|sur|votre|vos|introuvable|invalide|inconnue?|manquante?|indisponible)\b`, 'i');

// As Python's str.split() and " ".join(): any Unicode space.
const normalize = text => text.split(/[\s\u001c-\u001f\u0085]+/u).filter(Boolean).join(' ');
const isSpace = ch => Boolean(ch) && /[\s\u001c-\u001f\u0085]/u.test(ch);

function isShownText(text) {
  text = normalize(text);
  if (!text || !LETTER.test(text) || [...text].length > 400) return false;
  const bare = text.replaceAll('{}', ''); // variables are not code
  if (CODE_HINTS.test(text) || CODE_SYNTAX.test(bare) || !FRENCH_HINT.test(text)) return false;
  // Key names, tags, error names: ArrowDown, INPUT, NotAllowedError…
  if (/^(?:[A-Z0-9_+-]{1,3}|[A-Z][A-Z_]{3,}|[A-Z][a-z]+(?:[A-Z][a-z]+)+(Error)?|[A-Z]{4,}[0-9]+)$/u.test(text)) return false;
  return !new RegExp(`^[${W.slice(1, -1)}.-]+(\\s+[${W.slice(1, -1)}.-]+)*$`, 'u').test(text) || /[A-ZÀ-Öà-ÿ]/u.test(text);
}

// HTML character references, as Python's html.unescape for the ones KINO's pages use.
const ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', middot: '·', rarr: '→', larr: '←', times: '×',
  hellip: '…', laquo: '«', raquo: '»', rsquo: '’', lsquo: '‘', ldquo: '“', rdquo: '”', mdash: '—', ndash: '–',
  copy: '©', reg: '®', deg: '°', eacute: 'é', egrave: 'è', agrave: 'à', ccedil: 'ç', ecirc: 'ê', bull: '•', check: '✓',
  uarr: '↑', darr: '↓', harr: '↔', crarr: '↵', star: '☆', starf: '★', infin: '∞', plusmn: '±', thinsp: ' ', zwj: '‍',
};
function unescape(text) {
  return text.replace(/&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);?/g, (all, code) => {
    if (code[0] === '#') {
      const value = code[1] === 'x' || code[1] === 'X' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return Number.isFinite(value) ? String.fromCodePoint(value) : all;
    }
    return Object.hasOwn(ENTITIES, code) ? ENTITIES[code] : all;
  });
}

// The pieces of a JS literal that can end up as text: HTML markup is split away,
// ${…} becomes {}, and a literal that begins or ends with a space or a colon is
// completed by {} (it is concatenated with something).
function textSegments(literal) {
  const out = [];
  // Attribute values written in HTML templates (title="…") are text too.
  const attribute = new RegExp(`\\b(?:${ATTRIBUTES.join('|')})=("([^"]*)"|'([^']*)')`, 'g');
  for (const match of literal.matchAll(attribute)) {
    const value = normalize(unescape(match[2] !== undefined ? match[2] : match[3]));
    if (value && value !== '{}') out.push(value);
  }
  for (let piece of literal.split(/<[^<>]*>/)) {
    if (piece.includes('<') || piece.includes('>') || piece.includes('="') || piece.includes("='")) continue;
    piece = unescape(piece).replaceAll('\\n', ' ').replaceAll("\\'", "'").replaceAll('\\"', '"');
    let core = normalize(piece);
    if (!core) continue;
    if (isSpace(piece[0]) && !core.startsWith('{}')) core = `{} ${core}`;
    if ((isSpace(piece[piece.length - 1]) || core.endsWith(':')) && !core.endsWith('{}')) core = `${core} {}`;
    out.push(core);
  }
  return out;
}

// String and template literals of a JavaScript file (templates with ${…} as {}).
function jsLiterals(source) {
  const n = source.length;
  const out = [];
  let i = 0;
  let prev = ''; // last significant character, to tell a regex from a division
  function template(start) {
    let j = start;
    const buf = [];
    while (j < n) {
      const c = source[j];
      if (c === '\\') { buf.push(source.slice(j, j + 2)); j += 2; continue; }
      if (c === '`') return [buf.join(''), j + 1];
      if (c === '$' && source[j + 1] === '{') {
        let depth = 1;
        j += 2;
        while (j < n && depth) {
          if (source[j] === '`') {
            const [inner, next] = template(j + 1);
            out.push(inner);
            j = next;
            continue;
          }
          if (source[j] === "'" || source[j] === '"') {
            const q = source[j];
            j += 1;
            while (j < n && source[j] !== q) j += source[j] === '\\' ? 2 : 1;
          }
          if (source[j] === '{') depth += 1;
          else if (source[j] === '}') depth -= 1;
          j += 1;
        }
        buf.push('{}');
        continue;
      }
      buf.push(c);
      j += 1;
    }
    return [buf.join(''), j];
  }
  while (i < n) {
    const c = source[i];
    if (source.startsWith('//', i)) { i = source.indexOf('\n', i); if (i < 0) i = n; continue; }
    if (source.startsWith('/*', i)) { i = source.indexOf('*/', i + 2); i = i < 0 ? n : i + 2; continue; }
    if (c === "'" || c === '"') {
      let j = i + 1;
      while (j < n && source[j] !== c && source[j] !== '\n') j += source[j] === '\\' ? 2 : 1;
      out.push(source.slice(i + 1, j)); i = j + 1; prev = 'a'; continue;
    }
    if (c === '`') { const [text, next] = template(i + 1); out.push(text); i = next; prev = 'a'; continue; }
    if (c === '/' && ('(,=:[!&|?{};+-*%<>~^'.includes(prev) || prev === '')) {
      let j = i + 1;
      let inClass = false;
      while (j < n && source[j] !== '\n') {
        if (source[j] === '\\') { j += 2; continue; }
        if (source[j] === '[') inClass = true;
        else if (source[j] === ']') inClass = false;
        else if (source[j] === '/' && !inClass) break;
        j += 1;
      }
      i = j + 1; prev = 'a'; continue;
    }
    if (!isSpace(c)) {
      const word = /^[A-Za-z_$][\p{L}\p{N}_$]*/u.exec(source.slice(i, i + 200));
      if (word) {
        prev = ['return', 'typeof', 'case', 'in', 'of', 'else', 'do'].includes(word[0]) ? '(' : 'a';
        i += word[0].length;
        continue;
      }
      prev = c;
    }
    i += 1;
  }
  return out;
}

// French texts of the Rust server that the interface may display: its string
// literals (comments left out), « {} » for a variable part.
function rustTexts(source) {
  source = source.split('#[cfg(test)]')[0]; // the unit tests' own texts are not shown
  const code = source.split(/\r\n|\r|\n/).filter(line => !line.trimStart().startsWith('//')).join('\n');
  const out = [];
  for (const match of code.matchAll(/(?<![A-Za-z0-9_#])"((?:[^"\\]|\\.)*)"/gs)) {
    const text = match[1].replaceAll('\\"', '"').replaceAll('\\n', '\n');
    out.push(normalize(text.replace(/\{[^{}]*\}/g, '{}')));
  }
  return out.filter(t => FRENCH.test(t) && isShownText(t));
}

// A page's texts (outside script and style), its attribute texts and its scripts.
function parseHtml(page) {
  const texts = [];
  const scripts = [];
  const tag = /<(\/?)([A-Za-z][\w-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>|<!--[\s\S]*?-->|<![^>]*>/g;
  let at = 0;
  let inside = null;
  const data = text => {
    if (inside === 'script') scripts.push(text);
    else if (inside === null) texts.push(unescape(text));
  };
  for (const m of page.matchAll(tag)) {
    if (inside && !(m[1] === '/' && m[2].toLowerCase() === inside)) continue; // raw text until </script>
    if (m.index > at) data(page.slice(at, m.index));
    at = m.index + m[0].length;
    if (!m[2]) continue;
    const name = m[2].toLowerCase();
    if (m[1] === '/') {
      if (name === inside) inside = null;
      continue;
    }
    for (const a of m[3].matchAll(/([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g)) {
      const value = a[2] ?? a[3] ?? a[4];
      if (ATTRIBUTES.includes(a[1].toLowerCase()) && value) texts.push(unescape(value));
    }
    if (name === 'script' || name === 'style') inside = name;
  }
  if (at < page.length) data(page.slice(at));
  return { texts, scripts };
}

function sortedFiles(dir, filter) {
  return fs.readdirSync(dir).filter(filter).sort().map(name => path.join(dir, name));
}

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
}

// As Python's sorted() on strings: by code point.
const byCodePoint = (a, b) => {
  const x = [...a];
  const y = [...b];
  for (let k = 0; k < Math.min(x.length, y.length); k += 1) {
    if (x[k] !== y[k]) return x[k].codePointAt(0) - y[k].codePointAt(0);
  }
  return x.length - y.length;
};

function extract() {
  const found = new Set();
  for (const page of sortedFiles(WEB, n => n.endsWith('.html'))) {
    const { texts, scripts } = parseHtml(fs.readFileSync(page, 'utf8'));
    for (const t of texts) found.add(normalize(t));
    for (const script of scripts) for (const literal of jsLiterals(script)) for (const s of textSegments(literal)) found.add(s);
  }
  for (const script of sortedFiles(path.join(WEB, 'js'), n => n.endsWith('.js'))) {
    for (const literal of jsLiterals(fs.readFileSync(script, 'utf8'))) for (const s of textSegments(literal)) found.add(s);
  }
  for (const module of walk(CRATES).filter(f => f.endsWith('.rs')).sort()) {
    const parts = path.relative(CRATES, module).split(path.sep);
    if (parts.includes('tests') || parts.includes('bin')) continue;
    for (const t of rustTexts(fs.readFileSync(module, 'utf8'))) found.add(t);
  }
  return [...found].filter(isShownText).sort(byCodePoint);
}

function readLanguage(lang) {
  const file = path.join(I18N, `${lang}.js`);
  if (!fs.existsSync(file)) return {};
  const raw = fs.readFileSync(file, 'utf8');
  return JSON.parse(raw.slice(raw.indexOf(PREFIX) + PREFIX.length, raw.lastIndexOf(')')));
}

function main() {
  const [command = 'status', arg] = process.argv.slice(2);
  if (command === 'extract') {
    const strings = extract();
    fs.writeFileSync(CATALOG, `[\n${strings.map(s => JSON.stringify(s)).join(',\n')}\n]\n`, 'utf8');
    console.log(`${strings.length} textes dans ${path.relative(PROJECT, CATALOG)}`);
  } else if (command === 'status') {
    const strings = JSON.parse(fs.readFileSync(CATALOG, 'utf8'));
    for (const file of sortedFiles(I18N, n => n.endsWith('.js'))) {
      const lang = path.basename(file, '.js');
      const table = readLanguage(lang);
      const missing = strings.filter(s => !Object.hasOwn(table, s)).length;
      console.log(`${lang}: ${strings.length - missing}/${strings.length} traduits`);
    }
  } else if (command === 'missing') {
    const strings = JSON.parse(fs.readFileSync(CATALOG, 'utf8'));
    const table = readLanguage(arg);
    const missing = strings.filter(s => !Object.hasOwn(table, s));
    console.log(`[\n${missing.map(s => JSON.stringify(s)).join(',\n')}\n]`);
  } else {
    console.error('Usage: node tools/i18n.js extract | status | missing <lang>');
    process.exit(1);
  }
}

if (require.main === module) main();
module.exports = { extract, isShownText, jsLiterals, textSegments, rustTexts };
