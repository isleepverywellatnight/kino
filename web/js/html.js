// @ts-check
// How the page turns data into HTML. Text from services, add-ons, torrents or
// users is never trusted: html`…` escapes every value it is given, unless the
// value is already a fragment (made by html`…`, raw() or act()). render() is the
// one place that writes HTML into the page, and it only takes fragments.

/** HTML known to be safe: made by html`…`, raw(), act() or actOn(). */
class SafeHtml {
  /** @param {string} text */
  constructor(text) { this.text = text; }
  toString() { return this.text; }
}

/** Text made safe for HTML; a fragment, so html`…` does not escape it twice. @param {unknown} s */
function escapeHtml(s) {
  return new SafeHtml(escapeText(s));
}

/** @param {unknown} s @returns {string} */
function escapeText(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Text the page wrote itself (markup, an icon): taken as it is. @param {unknown} text */
function raw(text) {
  return text instanceof SafeHtml ? text : new SafeHtml(text === null || text === undefined ? '' : String(text));
}

/** @param {unknown} value @returns {string} */
function htmlPart(value) {
  if (value === null || value === undefined) return '';
  if (value instanceof SafeHtml) return value.text;
  if (Array.isArray(value)) return value.map(htmlPart).join('');
  return escapeText(value);
}

/**
 * A fragment: the template's own markup as written, each value escaped.
 * @param {TemplateStringsArray} strings @param {...unknown} values
 */
function html(strings, ...values) {
  let out = strings[0];
  values.forEach((value, i) => { out += htmlPart(value) + strings[i + 1]; });
  return new SafeHtml(out);
}

/**
 * Writes into an element: the only way the page sets HTML. A fragment (or a
 * list of them) goes in as markup; anything else goes in as text.
 * @param {Element | null | undefined} element @param {unknown} content
 */
function render(element, content) {
  if (!element) return;
  element.innerHTML = htmlPart(content);
}

/** Replaces an element with a fragment (outerHTML). @param {Element | null | undefined} element @param {unknown} fragment */
function replaceWith(element, fragment) {
  if (!element) return;
  element.outerHTML = htmlPart(fragment);
}

/** Inserts a fragment next to or inside an element (insertAdjacentHTML). @param {Element | null | undefined} element @param {InsertPosition} position @param {unknown} fragment */
function renderAdjacent(element, position, fragment) {
  if (!element) return;
  element.insertAdjacentHTML(position, htmlPart(fragment));
}

/** A link or image address that cannot run code: web, page-relative or an inline image. @param {unknown} url */
function safeUrl(url) {
  const text = String(url ?? '').trim();
  if (/^(https?:)?\/\//i.test(text) || text.startsWith('/') || /^data:image\//i.test(text) || text.startsWith('blob:')) return text;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(text)) return text;
  return '';
}

/** JSON for a value inside a double-quoted HTML attribute. @param {unknown} value */
function attrJson(value) {
  return raw(escapeText(JSON.stringify(value === undefined ? null : value)));
}

// A declared action for a template, run by web/js/actions.js:
// `<button ${act('debridFromIndex', idx)}>` gives data-on-click="debridFromIndex"
// and data-click-args="[3]". The arguments are values, not code; THIS and EVENT
// stand for the element and the event, VALUE and CHECKED for its value and state.
const THIS = { $: 'this' };
const EVENT = { $: 'event' };
const VALUE = { $: 'value' };
const CHECKED = { $: 'checked' };

/** @param {string} type @param {string} name @param {...unknown} args */
function actOn(type, name, ...args) {
  return raw(`data-on-${type}="${escapeText(name)}"${args.length ? ` data-${type}-args="${attrJson(args)}"` : ''}`);
}

/** @param {string} name @param {...unknown} args */
function act(name, ...args) {
  return actOn('click', name, ...args);
}

