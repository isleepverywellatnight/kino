// What the page does on a click, a key or a change, declared in its markup
// without inline code: `data-on-click="switchTab"` and the call's arguments in
// `data-click-args='["movies", {"$": "this"}]'`, read as JSON. The arguments
// {"$": "this"}, {"$": "event"}, {"$": "value"}, {"$": "checked"} and
// {"$": "files"} stand for the element, the event, and the element's value,
// state and files. `data-click-stop` stops the event there, as
// event.stopPropagation() did.
//
// One listener per kind of event answers for the whole page, pages drawn later
// included. Events that bubble are followed from the target up to the page,
// in that order, as inline handlers ran; the others (focus, load, error,
// toggle, close) are caught on their way down.
(function () {
  'use strict';
  const BUBBLING = ['click', 'dblclick', 'mousedown', 'change', 'input', 'keydown', 'submit'];
  const DIRECT = ['focus', 'load', 'error', 'toggle', 'close'];

  // `window.name` or `window.Some.name`: a function of the page, nothing else.
  function target(name) {
    if (!/^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$/.test(name)) return null;
    let owner = window;
    const parts = name.split('.');
    for (const part of parts.slice(0, -1)) {
      owner = owner && owner[part];
    }
    const fn = owner && owner[parts[parts.length - 1]];
    return typeof fn === 'function' ? { owner, fn } : null;
  }

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
    const found = name && target(name);
    if (!found) {
      if (name) console.warn(`KINO: no action « ${name} »`);
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
    found.fn.apply(found.owner === window ? undefined : found.owner, args.map(arg => argument(arg, element, event)));
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

  // A choice of the header menu, then the menu closes.
  window.kinoMenuAction = function (name) {
    const found = target(name);
    if (found) found.fn.call(found.owner === window ? undefined : found.owner);
    if (typeof window.closeHeaderMenu === 'function') window.closeHeaderMenu();
  };

  // A click on the dark backdrop of a dialog (not inside it) closes it.
  window.kinoOnBackdrop = function (event, element, name) {
    if (event.target !== element) return;
    const found = target(name);
    if (found) found.fn.call(found.owner === window ? undefined : found.owner);
  };

  // Enter in a field.
  window.kinoOnEnter = function (event, name) {
    if (event.key !== 'Enter') return;
    const found = target(name);
    if (found) found.fn.call(found.owner === window ? undefined : found.owner);
  };

  // A form sent by KINO itself, not by the page reloading.
  window.kinoSubmit = function (event, name) {
    event.preventDefault();
    const found = target(name);
    if (found) found.fn.call(found.owner === window ? undefined : found.owner);
  };

  // A button that opens a hidden file field.
  window.kinoPickFile = function (id) {
    const input = document.getElementById(id);
    if (input) input.click();
  };

  // An element that has nothing to show (an image that does not load) goes.
  window.kinoRemove = function (element) {
    element.remove();
  };

  // An image that does not load is hidden.
  window.kinoHideImage = function (element) {
    element.hidden = true;
  };

  // A double click on the bar maximizes, except on its controls.
  window.kinoBarDoubleClick = function (event) {
    if (!event.target.closest('.no-drag')) window.windowAction('maximize');
  };

  // A double click on the player: out of full screen if in it, else maximize.
  window.kinoPlayerDoubleClick = function () {
    const full = document.body.classList.contains('is-fullscreen')
      || Boolean(document.fullscreenElement || document.webkitFullscreenElement);
    if (full) window.toggleInAppFullscreen();
    else window.windowAction('maximize');
  };

  // « Surprise »: from the classics when on that tab, else from the catalogue.
  window.kinoSurprise = function () {
    // eslint-disable-next-line no-undef
    window.surpriseMeMedia(activeTab === 'classics' ? 'classics' : 'catalog');
  };

  window.kinoSpoilerSetting = function (value) {
    window.setGlobalSpoilerProtection(value === 'on');
  };

  // The peer-to-peer consent dialog closed without an answer: a « no ».
  window.kinoP2PConsentClosed = function () {
    // eslint-disable-next-line no-undef
    if (p2pConsentAnswer) window.answerP2PConsent(false);
  };
})();
