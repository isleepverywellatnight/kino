// Runs KINO's real interface scripts against a stand-in page built from
// web/index.html, without a browser or any dependency: every element with an
// id exists, keeps its inline « display » and its « hidden » attribute, and the
// server answers through a function. Good enough to drive switchTab(),
// runSearch() and selectMedia(), then read which sections are shown.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
// The scripts of web/index.html, in its order (the app parts among them).
const { interfaceScripts, scriptText } = require('./interface_source.js');

function classList(initial) {
  const set = new Set(String(initial || '').split(/\s+/).filter(Boolean));
  return {
    add: (...names) => names.forEach(name => set.add(name)),
    remove: (...names) => names.forEach(name => set.delete(name)),
    toggle: (name, force) => {
      const on = force === undefined ? !set.has(name) : Boolean(force);
      if (on) set.add(name); else set.delete(name);
      return on;
    },
    contains: name => set.has(name),
    get length() { return set.size; },
    toString: () => [...set].join(' '),
  };
}

class FakeElement {
  constructor(id = '', tag = 'div', { display = '', hidden = false, className = '' } = {}) {
    this.id = id;
    this.tagName = tag.toUpperCase();
    this.style = { display, setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; } };
    this.hidden = hidden;
    this.classList = classList(className);
    this.dataset = {};
    this.attributes = {};
    this.innerHTML = '';
    this.textContent = '';
    this.innerText = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.open = false;
    this.options = [];
    this.children = [];
    this.childNodes = [];
    this.scrollLeft = 0;
    this.scrollTop = 0;
    this.scrollWidth = 0;
    this.clientWidth = 0;
    this.offsetWidth = 0;
    this.offsetHeight = 0;
  }
  get className() { return this.classList.toString(); }
  set className(value) { this.classList = classList(value); }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'hidden') this.hidden = true; }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  hasAttribute(name) { return name === 'hidden' ? this.hidden : Object.prototype.hasOwnProperty.call(this.attributes, name); }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'hidden') this.hidden = false; }
  toggleAttribute(name, force) { const on = force === undefined ? !this.hasAttribute(name) : force; if (on) this.setAttribute(name, ''); else this.removeAttribute(name); return on; }
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() { return true; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  matches() { return false; }
  contains() { return false; }
  appendChild(child) { this.children.push(child); return child; }
  add(option) { this.options.push(option); }
  append() {}
  prepend() {}
  insertBefore(child) { return child; }
  removeChild(child) { return child; }
  replaceChildren() {}
  insertAdjacentHTML() {}
  insertAdjacentElement(where, element) { return element; }
  remove() {}
  cloneNode() { return new FakeElement('', this.tagName); }
  getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 }; }
  getClientRects() { return []; }
  scrollIntoView() {}
  scrollTo() {}
  scrollBy() {}
  focus() {}
  blur() {}
  click() {}
  select() {}
  play() { return Promise.resolve(); }
  pause() {}
  load() {}
  requestFullscreen() { return Promise.resolve(); }
}

// Every element of index.html that carries an id, with its initial state.
function pageElements(html) {
  const elements = new Map();
  for (const match of html.matchAll(/<([a-zA-Z][\w-]*)\b([^>]*?)\sid="([^"]+)"([^>]*)>/g)) {
    const attrs = `${match[2]} ${match[4]}`;
    const style = /\bstyle="([^"]*)"/.exec(attrs)?.[1] || '';
    const display = /(?:^|;)\s*display\s*:\s*([^;]+)/.exec(style)?.[1]?.trim() || '';
    const hidden = /(?:^|\s)hidden(?:\s|=|$)/.test(attrs.replace(/"[^"]*"/g, '""'));
    const className = /\bclass="([^"]*)"/.exec(attrs)?.[1] || '';
    elements.set(match[3], new FakeElement(match[3], match[1], { display, hidden, className }));
  }
  return elements;
}

function quietTimers() {
  return {
    setTimeout: (fn, ms, ...args) => { const timer = setTimeout(fn, Math.min(Number(ms) || 0, 2), ...args); timer.unref?.(); return timer; },
    clearTimeout,
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: fn => { const timer = setTimeout(() => fn(Date.now()), 0); timer.unref?.(); return timer; },
    cancelAnimationFrame: () => {},
  };
}

// answer(path, options) → JSON body; « { error } » becomes a failed request.
async function loadPage(answer = () => ({})) {
  const html = fs.readFileSync(path.join(ROOT, 'web', 'index.html'), 'utf8');
  const elements = pageElements(html);
  const element = id => {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  };
  const storage = () => {
    const data = new Map();
    return { getItem: key => (data.has(key) ? data.get(key) : null), setItem: (key, value) => data.set(key, String(value)),
      removeItem: key => data.delete(key), clear: () => data.clear() };
  };
  const requests = [];
  const body = new FakeElement('', 'body');
  const document = {
    body, head: new FakeElement('', 'head'), documentElement: Object.assign(new FakeElement('', 'html'), { scrollHeight: 0, clientWidth: 1280 }),
    getElementById: element,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: tag => new FakeElement('', tag),
    createTextNode: text => ({ textContent: text }),
    addEventListener() {}, removeEventListener() {},
    activeElement: null, fullscreenElement: null, visibilityState: 'visible', hidden: false,
    exitFullscreen: () => Promise.resolve(),
    // web/js/i18n.js writes its dictionaries' script tags: the page stays in French
    // here, with the French texts of the keys (fr.js), run as the page would.
    write(markup) {
      const file = /src="\/js\/i18n\/(fr)\.js"/.exec(markup)?.[1];
      if (file) vm.runInContext(fs.readFileSync(path.join(ROOT, 'web', 'js', 'i18n', `${file}.js`), 'utf8'), context, { filename: `i18n/${file}.js` });
    },
  };
  const observer = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
  const context = {
    console: { log() {}, info() {}, warn() {}, error() {}, debug() {} },
    document, navigator: { platform: 'MacIntel', userAgent: 'Mac', language: 'fr-FR', languages: ['fr-FR'], clipboard: { writeText: async () => {} }, onLine: true },
    location: { href: 'http://127.0.0.1:8080/', origin: 'http://127.0.0.1:8080', protocol: 'http:', host: '127.0.0.1:8080', hostname: '127.0.0.1', pathname: '/', search: '', hash: '', reload() {} },
    history: { pushState() {}, replaceState() {}, back() {} },
    localStorage: storage(), sessionStorage: storage(),
    KINO_UI_LANGUAGE: 'fr',
    // The language menu of Paramètres (app-start.js) is filled with new Option().
    Option: function Option(text, value) { return Object.assign(new FakeElement('', 'option'), { text, textContent: text, value }); },
    innerWidth: 1280, innerHeight: 800, scrollY: 0, devicePixelRatio: 2,
    scrollTo() {}, scrollBy() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
    getComputedStyle: el => ({ display: el?.style?.display || 'block', getPropertyValue: () => '' }),
    alert() {}, confirm: () => false, prompt: () => null, open: () => null,
    MutationObserver: observer, ResizeObserver: observer, IntersectionObserver: observer,
    Image: class { constructor() { this.src = ''; } }, Audio: class { play() { return Promise.resolve(); } pause() {} },
    URL, URLSearchParams, AbortController, TextEncoder, TextDecoder, Intl, structuredClone,
    Event: class { constructor(type) { this.type = type; } }, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
    KeyboardEvent: class {}, MouseEvent: class {},
    fetch: async (url, options = {}) => {
      requests.push(String(url));
      let body = answer(String(url), options);
      body = await body;
      const failed = body && typeof body === 'object' && body.error;
      return { ok: !failed, status: failed ? 500 : 200, headers: { get: () => 'application/json' }, json: async () => body ?? {}, text: async () => JSON.stringify(body ?? {}) };
    },
    ...quietTimers(),
  };
  context.window = context;
  context.self = context;
  context.globalThis = context;
  vm.createContext(context);
  for (const name of interfaceScripts(ROOT)) {
    vm.runInContext(scriptText(name), context, { filename: name });
  }
  const inline = /<script>([\s\S]*?)<\/script>/.exec(html)?.[1];
  if (inline) vm.runInContext(inline, context, { filename: 'index.html#script' });
  await settle();
  return { context, element, elements, requests };
}

async function settle(rounds = 6) {
  for (let i = 0; i < rounds; i++) await new Promise(resolve => setTimeout(resolve, 5));
}

function shown(element) {
  return Boolean(element) && element.style.display !== 'none' && !element.hidden;
}

module.exports = { loadPage, settle, shown, FakeElement, pageElements };
