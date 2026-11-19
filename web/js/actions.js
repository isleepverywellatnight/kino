// What the page does on a click, a key or a change, declared in its markup
// without inline code: `data-on-click="switchTab"` and the call's arguments in
// `data-click-args='["movies", {"$": "this"}]'`, read as JSON. The arguments
// {"$": "this"}, {"$": "event"}, {"$": "value"}, {"$": "checked"} and
// {"$": "files"} stand for the element, the event, and the element's value,
// state and files. `data-click-stop` stops the event there, as
// event.stopPropagation() did.
//
// A declared name runs only what a file registered under it
// (registerActions({ switchTab, … })), never whatever the page happens to hold
// under that name.
//
// One listener per kind of event answers for the whole page, pages drawn later
// included. Events that bubble are followed from the target up to the page,
// in that order, as inline handlers ran; the others (focus, load, error,
// toggle, close, scroll) are caught on their way down.

/** @type {Map<string, Function>} */
const pageActions = new Map();

/** Makes these functions available to the markup, each under its key. */
export function registerActions(handlers) {
  for (const [name, fn] of Object.entries(handlers)) {
    if (typeof fn !== 'function') throw new TypeError(`KINO: action « ${name} » is not a function`);
    if (pageActions.has(name)) throw new Error(`KINO: action « ${name} » registered twice`);
    pageActions.set(name, fn);
  }
}

/** Runs the action registered under this name; false when there is none. */
export function runAction(name, ...args) {
  const fn = pageActions.get(name);
  if (!fn) {
    console.warn(`KINO: no action « ${name} »`);
    return false;
  }
  fn(...args);
  return true;
}

(function () {
  'use strict';
  const BUBBLING = ['click', 'dblclick', 'mousedown', 'change', 'input', 'keydown', 'submit'];
  const DIRECT = ['focus', 'load', 'error', 'toggle', 'close', 'scroll'];

  function argument(arg, element, event) {
    if (!arg || typeof arg !== 'object' || Array.isArray(arg) || !('$' in arg)) return arg;
    switch (arg.$) {
      case 'this': return element;
      case 'event': return event;
      case 'value': return element.value;
      case 'checked': return element.checked;
      case 'files': return element.files;
      default: return undefined;
    }
  }

  function run(element, type, event) {
    const name = element.getAttribute(`data-on-${type}`);
    if (!name) return;
    if (!pageActions.has(name)) {
      console.warn(`KINO: no action « ${name} »`);
      return;
    }
    let args = [];
    const raw = element.getAttribute(`data-${type}-args`);
    if (raw) {
      try {
        args = JSON.parse(raw);
      } catch (error) {
        console.warn(`KINO: unreadable arguments for « ${name} »`, raw);
        return;
      }
    }
    if (element.hasAttribute(`data-${type}-stop`)) event.stopPropagation();
    runAction(name, ...args.map(arg => argument(arg, element, event)));
  }

  for (const type of BUBBLING) {
    document.addEventListener(type, event => {
      for (let element = event.target; element && element.nodeType === 1; element = element.parentElement) {
        if (element.hasAttribute(`data-on-${type}`)) run(element, type, event);
        if (event.cancelBubble) break;
      }
    });
  }
  for (const type of DIRECT) {
    document.addEventListener(type, event => {
      const element = event.target;
      if (element && element.nodeType === 1 && element.hasAttribute(`data-on-${type}`)) run(element, type, event);
    }, true);
  }

  // --- What used to be a few words of code in the markup ---
  // (those that belong to one part of KINO are registered by that part).
  registerActions({
    // A click on the dark backdrop of a dialog (not inside it) closes it.
    kinoOnBackdrop(event, element, name) {
      if (event.target === element) runAction(name);
    },
    // Enter in a field.
    kinoOnEnter(event, name) {
      if (event.key === 'Enter') runAction(name);
    },
    // A form sent by KINO itself, not by the page reloading.
    kinoSubmit(event, name) {
      event.preventDefault();
      runAction(name);
    },
    // A button that opens a hidden file field.
    kinoPickFile(id) {
      const input = document.getElementById(id);
      if (input) input.click();
    },
    // An element that has nothing to show (an image that does not load) goes.
    kinoRemove(element) {
      element.remove();
    },
    // A press on a suggestion keeps the focus in the search field.
    kinoKeepFocus(event) {
      event.preventDefault();
    },
    // An image that does not load stays as a faint shape.
    kinoDimImage(element) {
      element.style.opacity = 0.08;
    },
    // The title's logo has loaded: the hero shows it instead of the text.
    kinoHeroLogo(element) {
      element.closest('.detail-hero')?.classList.add('has-logo');
    },
    // An image that does not load keeps its place, unseen.
    kinoInvisible(element) {
      element.style.visibility = 'hidden';
    },
    // A poster has arrived: its placeholder fades out.
    kinoLoaded(element) {
      element.classList.add('is-loaded');
    },
    // An image that does not load is hidden.
    kinoHideImage(element) {
      element.hidden = true;
    },
  });
})();
