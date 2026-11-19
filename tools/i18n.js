#!/usr/bin/env node
// KINO's interface translations.
//
// The interface is written in French. Each French text has a key:
// web/js/i18n/fr.js maps the key to the French text ("fiche.aucun_synopsis_disponible":
// "Aucun synopsis disponible."), and each language's dictionary,
// web/js/i18n/<lang>.js, maps the same key to its translation. "{}" stands for a
// variable part: "{} épisodes" -> "{} episodes" (use {1}, {2}… to reorder them).
// web/js/i18n.js uses both while the page is displayed.
//
// The server (crates/, Rust) also writes French that the interface shows: its
// error messages and notices. Its French string literals are listed too.
//
//   node tools/i18n.js extract          add the new French texts of web/ and crates/ to fr.js, each with a key
//   node tools/i18n.js extract --prune  also remove from fr.js and every dictionary the texts no longer found
//   node tools/i18n.js status           count, per language, the texts still untranslated
//   node tools/i18n.js missing de       print the texts German still lacks (JSON: key -> French)
//
// A missing text stays in English (in French for English): translations can be
// completed little by little.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const PROJECT = path.join(__dirname, '..');
const WEB = path.join(PROJECT, 'web');
const CRATES = path.join(PROJECT, 'crates');
const I18N = path.join(WEB, 'js', 'i18n');
const SOURCE = path.join(I18N, 'fr.js');
const LANGUAGE_CALL = 'KINO_I18N.load';
const SOURCE_CALL = 'KINO_I18N.source';
// Found in the code but never shown: console messages, names, a log line.
const NOT_SHOWN = new Set([', html', 'KINO_I18N.choose', 'blob: {}', 'Base lente sur un thread async',
  'KINO: action « {} » registered twice', 'KINO: no action « {} »', 'KINO: unreadable arguments for « {} »']);
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

// Each French text shown by web/ and crates/, with the first file it was found in.
function locate() {
  const found = new Map();
  const add = (text, file) => { if (!found.has(text)) found.set(text, path.relative(PROJECT, file)); };
  for (const page of sortedFiles(WEB, n => n.endsWith('.html'))) {
    const { texts, scripts } = parseHtml(fs.readFileSync(page, 'utf8'));
    for (const t of texts) add(normalize(t), page);
    for (const script of scripts) for (const literal of jsLiterals(script)) for (const s of textSegments(literal)) add(s, page);
  }
  for (const script of sortedFiles(path.join(WEB, 'js'), n => n.endsWith('.js'))) {
    for (const literal of jsLiterals(fs.readFileSync(script, 'utf8'))) for (const s of textSegments(literal)) add(s, script);
  }
  for (const module of walk(CRATES).filter(f => f.endsWith('.rs')).sort()) {
    const parts = path.relative(CRATES, module).split(path.sep);
    if (parts.includes('tests') || parts.includes('bin')) continue;
    for (const t of rustTexts(fs.readFileSync(module, 'utf8'))) add(t, module);
  }
  for (const text of [...found.keys()]) if (!isShownText(text) || NOT_SHOWN.has(text)) found.delete(text);
  return found;
}

function extract() {
  return [...locate().keys()].sort(byCodePoint);
}

// --- Keys ---

// The part of KINO a text belongs to, from the file it is found in.
const AREAS = {
  'index.html': 'page', 'remote.html': 'remote', 'app-core.js': 'core', 'app-home.js': 'home', 'app-lists.js': 'lists',
  'app-letterboxd.js': 'letterboxd', 'app-tabs.js': 'tabs', 'app-cloud-settings.js': 'settings', 'app-social.js': 'social',
  'app-fiche.js': 'fiche', 'app-sources.js': 'sources', 'app-player.js': 'player', 'app-start.js': 'start',
  'native-player.js': 'native', 'anime-home.js': 'anime', 'anime-journey.js': 'anime', 'p2p-addons.js': 'p2p',
  'playback-help.js': 'help', 'ratings.js': 'ratings', 'stats.js': 'stats', 'manga.js': 'manga', 'remote.js': 'remote',
  'series-playback.js': 'playback', 'spoilers.js': 'spoilers', 'html.js': 'core', 'actions.js': 'core',
};
function areaOf(file) {
  if (!file) return 'server';
  if (file.startsWith('crates')) return 'server';
  return AREAS[path.basename(file)] || 'misc';
}

// « Aucun synopsis disponible. » in fiche.js -> fiche.aucun_synopsis_disponible
function keyFor(text, area, taken) {
  const words = text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replaceAll('{}', ' x ').replace(/œ/g, 'oe').replace(/[^a-z0-9]+/g, ' ').trim().split(' ').filter(Boolean);
  let slug = '';
  for (const word of words) {
    if (slug && (slug + '_' + word).length > 40) break;
    slug = slug ? `${slug}_${word}` : word;
  }
  const base = `${area}.${slug || 'symbol'}`;
  let key = base;
  for (let n = 2; taken.has(key); n += 1) key = `${base}_${n}`;
  taken.add(key);
  return key;
}

// --- Files ---

function readTable(file, call) {
  if (!fs.existsSync(file)) return {};
  const raw = fs.readFileSync(file, 'utf8');
  const start = raw.indexOf(`${call}(`);
  if (start < 0) throw new Error(`${path.relative(PROJECT, file)}: no ${call}(…)`);
  return JSON.parse(raw.slice(start + call.length + 1, raw.lastIndexOf(')')));
}

function writeTable(file, call, header, table) {
  const keys = Object.keys(table).sort(byCodePoint);
  const body = keys.map(key => `${JSON.stringify(key)}: ${JSON.stringify(table[key])}`).join(',\n');
  fs.writeFileSync(file, `${header}\n${call}({\n${body}\n});\n`, 'utf8');
}

const readSource = () => readTable(SOURCE, SOURCE_CALL);
const readLanguage = lang => readTable(path.join(I18N, `${lang}.js`), LANGUAGE_CALL);
const languages = () => sortedFiles(I18N, n => /^[a-z]{2}\.js$/.test(n) && n !== 'fr.js').map(f => path.basename(f, '.js'));
const SOURCE_HEADER = '// KINO interface: the key of each French text. See tools/i18n.js.';
const languageHeader = lang => `// KINO interface, by key -> ${lang}. See tools/i18n.js.`;

function writeSource(table) { writeTable(SOURCE, SOURCE_CALL, SOURCE_HEADER, table); }
function writeLanguage(lang, table) { writeTable(path.join(I18N, `${lang}.js`), LANGUAGE_CALL, languageHeader(lang), table); }

function main() {
  const [command = 'status', arg] = process.argv.slice(2);
  if (command === 'extract') {
    const found = locate();
    const source = readSource();
    const known = new Set(Object.values(source));
    const taken = new Set(Object.keys(source));
    let added = 0;
    for (const [text, file] of [...found.entries()].sort((a, b) => byCodePoint(a[0], b[0]))) {
      if (known.has(text)) continue;
      source[keyFor(text, areaOf(file), taken)] = text;
      added += 1;
    }
    const gone = Object.keys(source).filter(key => !found.has(source[key]));
    if (arg === '--prune') {
      for (const key of gone) delete source[key];
      for (const lang of languages()) {
        const table = readLanguage(lang);
        for (const key of gone) delete table[key];
        writeLanguage(lang, table);
      }
    }
    writeSource(source);
    console.log(`${Object.keys(source).length} textes dans ${path.relative(PROJECT, SOURCE)} (${added} ajoutés, `
      + `${gone.length} ${arg === '--prune' ? 'retirés' : 'introuvables dans le code : extract --prune les retire'})`);
  } else if (command === 'status') {
    const keys = Object.keys(readSource());
    for (const lang of languages()) {
      const table = readLanguage(lang);
      console.log(`${lang}: ${keys.filter(key => Object.hasOwn(table, key)).length}/${keys.length} traduits`);
    }
  } else if (command === 'missing') {
    const source = readSource();
    const table = readLanguage(arg);
    const missing = Object.fromEntries(Object.entries(source).filter(([key]) => !Object.hasOwn(table, key)));
    console.log(JSON.stringify(missing, null, 1));
  } else {
    console.error('Usage: node tools/i18n.js extract [--prune] | status | missing <lang>');
    process.exit(1);
  }
}

if (require.main === module) main();
module.exports = { extract, locate, keyFor, areaOf, readSource, readLanguage, writeSource, writeLanguage, languages,
  isShownText, jsLiterals, textSegments, rustTexts, SOURCE, LANGUAGE_CALL, SOURCE_CALL };
