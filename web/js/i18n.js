// KINO in the user's language.
//
// The interface is written in French. This script, loaded first, picks the
// language (Paramètres → Langue, else the system's), loads its dictionary
// (web/js/i18n/<lang>.js) and translates the page while it is displayed: text,
// placeholders, tooltips and alert()/confirm() messages, including everything
// the other scripts add later. A text missing from the dictionary stays in
// French. Dictionaries map French to the translation; "{}" is a variable part.
// See tools/i18n.py.
(function () {
  'use strict';

  const LANGUAGES = {
    fr: 'Français', en: 'English', de: 'Deutsch', es: 'Español', it: 'Italiano',
    pt: 'Português', ru: 'Русский', zh: '中文', ja: '日本語',
  };
  const LOCALES = { fr: 'fr-FR', en: 'en-US', de: 'de-DE', es: 'es-ES', it: 'it-IT', pt: 'pt-BR', ru: 'ru-RU', zh: 'zh-CN', ja: 'ja-JP' };
  // KINO's own French month names, kept as they were before translations.
  const MONTHS_FR = {
    long: ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'],
    short: ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'],
    narrow: 'JFMAMJJASOND'.split(''),
  };
  const ATTRIBUTES = ['placeholder', 'title', 'aria-label', 'alt', 'data-tooltip', 'data-label'];
  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE', 'svg']);

  function storedChoice() {
    try { return localStorage.getItem('kino.language') || ''; } catch (_) { return ''; }
  }

  function pickLanguage() {
    const chosen = window.KINO_UI_LANGUAGE || storedChoice();
    if (LANGUAGES[chosen]) return chosen;
    for (const tag of navigator.languages || [navigator.language || '']) {
      const base = String(tag).slice(0, 2).toLowerCase();
      if (LANGUAGES[base]) return base;
    }
    return 'en';
  }

  const lang = pickLanguage();
  const exact = new Map();
  const patternsBySource = new Map();
  let patterns = [];
  let started = false;
  const written = new WeakMap(); // node -> text this script set, so it is not translated twice

  function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function compile(source, target) {
    const parts = source.split('{}');
    const anchor = parts.reduce((a, b) => (b.trim().length > a.length ? b.trim() : a), '');
    // A placeholder glued to a word (« idée{} » for a plural ending) may be empty.
    let pattern = '^' + escapeRegExp(parts[0]);
    for (let i = 1; i < parts.length; i++) {
      pattern += (/[A-Za-zÀ-ÿ]$/.test(parts[i - 1]) ? '(.*?)' : '(.+?)') + escapeRegExp(parts[i]);
    }
    const re = new RegExp(pattern + '$');
    return { re, anchor, target };
  }

  function fill(target, values) {
    let next = 0;
    return target.replace(/\{(\d*)\}/g, (_, index) => {
      const value = index ? values[Number(index) - 1] : values[next++];
      return value === undefined ? '' : value;
    });
  }

  function translateCore(text, depth) {
    const hit = exact.get(text);
    if (hit !== undefined) return hit;
    if (depth > 2) return null;
    for (const p of patterns) {
      if (p.anchor && !text.includes(p.anchor)) continue;
      const m = p.re.exec(text);
      if (m) return fill(p.target, m.slice(1).map((v) => translateCore(v, depth + 1) ?? v));
    }
    return null;
  }

  // The translation of a text, or null; surrounding spaces are kept.
  function translate(text) {
    if (typeof text !== 'string' || !text || text.length > 600) return null;
    const core = text.trim().replace(/\s+/g, ' ');
    if (!core || !/[A-Za-zÀ-ÿ]/.test(core)) return null;
    const out = translateCore(core, 0);
    if (out === null || out === core) return null;
    const lead = text.match(/^\s*/)[0];
    const tail = text.match(/\s*$/)[0];
    return lead + out + tail;
  }

  function translateText(node) {
    const value = node.nodeValue;
    if (written.get(node) === value) return;
    const out = translate(value);
    if (out !== null) {
      written.set(node, out);
      node.nodeValue = out;
    }
  }

  function translateAttributes(el) {
    for (const name of ATTRIBUTES) {
      const value = el.getAttribute && el.getAttribute(name);
      if (!value) continue;
      const out = translate(value);
      if (out !== null) el.setAttribute(name, out);
    }
  }

  function skipped(el) {
    return !el || SKIP.has(el.nodeName) || el.isContentEditable || (el.closest && el.closest('[translate="no"], .notranslate'));
  }

  function walk(root) {
    if (root.nodeType === Node.TEXT_NODE) {
      if (!skipped(root.parentElement)) translateText(root);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE || skipped(root)) return;
    translateAttributes(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (node.nodeType === Node.ELEMENT_NODE) return skipped(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.nodeType === Node.TEXT_NODE) translateText(node);
      else translateAttributes(node);
    }
  }

  function start() {
    walk(document.documentElement);
    new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === 'childList') r.addedNodes.forEach(walk);
        else if (r.type === 'characterData') { if (!skipped(r.target.parentElement)) translateText(r.target); }
        else if (r.type === 'attributes' && !skipped(r.target)) {
          const value = r.target.getAttribute(r.attributeName);
          const out = translate(value);
          if (out !== null) r.target.setAttribute(r.attributeName, out);
        }
      }
    }).observe(document.documentElement, {
      childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTES,
    });
    for (const name of ['alert', 'confirm', 'prompt']) {
      const original = window[name];
      if (typeof original === 'function') {
        window[name] = function (message, ...rest) { return original.call(window, translate(message) ?? message, ...rest); };
      }
    }
  }

  window.KINO_LANG = lang;
  window.KINO_LOCALE = LOCALES[lang];
  window.KINO_LANGUAGES = LANGUAGES;
  window.KINO_I18N = {
    lang,
    // Called by web/js/i18n/<lang>.js with its dictionary.
    // English is loaded first for every other language: what a dictionary lacks shows in English.
    load(table) {
      for (const [source, target] of Object.entries(table)) {
        if (!target) continue;
        if (source.includes('{}')) patternsBySource.set(source, compile(source, target));
        else exact.set(source, target);
      }
      // Longest patterns first: « {} épisodes vus » before « {} épisodes ».
      patterns = [...patternsBySource.values()].sort((a, b) => b.re.source.length - a.re.source.length);
      if (!started) { started = true; start(); }
    },
    // For scripts that build text outside the page (notifications, canvas…).
    t(text) { return translate(text) ?? text; },
    // Month names in the interface language: 'long' (janvier), 'short' (janv.) or 'narrow' (J).
    months(style) {
      if (lang === 'fr' && MONTHS_FR[style]) return MONTHS_FR[style].slice();
      const format = new Intl.DateTimeFormat(LOCALES[lang], { month: style });
      return Array.from({ length: 12 }, (_, m) => format.format(new Date(2020, m, 15)));
    },
    // Paramètres → Langue: remembered here and in KINO's settings, then the page reloads.
    async choose(next) {
      if (!LANGUAGES[next]) return;
      try { localStorage.setItem('kino.language', next); } catch (_) {}
      try {
        await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ui_language: next }) });
      } catch (_) {}
      location.reload();
    },
  };

  document.documentElement.lang = lang;
  if (lang !== 'fr') {
    // Synchronous on purpose: the dictionaries are ready before the page is drawn.
    const files = lang === 'en' ? ['en'] : ['en', lang];
    for (const file of files) document.write('<script src="/js/i18n/' + file + '.js"><\/script>');
  }
})();
