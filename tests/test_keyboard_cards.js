// Clickable cards can be reached with Tab and opened with Enter or Space.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = require('../tools/interface_source.js').appSource();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/redesign.css'), 'utf8');
const module_ = "const KEYBOARD_CARD_SELECTOR" + source.split('const KEYBOARD_CARD_SELECTOR', 2)[1].split('function initKeyboardCards()', 1)[0];

function fakeCard(className, { onclick = true, title = 'Le Parrain' } = {}) {
  const attributes = new Map(onclick ? [['onclick', 'selectMedia({})']] : []);
  const card = {
    className, clicks: 0,
    matches: selector => selector.split(',').some(part => part.trim() === '.' + className),
    hasAttribute: name => attributes.has(name),
    getAttribute: name => attributes.get(name) ?? null,
    setAttribute: (name, value) => attributes.set(name, String(value)),
    querySelector: () => (title ? { textContent: `  ${title} ` } : null),
    click() { this.clicks += 1; },
  };
  return card;
}

function load() {
  const context = {};
  vm.runInNewContext(`${module_}; this.enhance = enhanceKeyboardCards; this.activate = activateKeyboardCard;`, context);
  return context;
}

test('cards become focusable buttons named after their title', () => {
  const { enhance } = load();
  const poster = fakeCard('poster-card');
  const plain = fakeCard('card', { onclick: false });
  enhance({ querySelectorAll: () => [poster, plain], matches: () => false });
  assert.equal(poster.getAttribute('tabindex'), '0');
  assert.equal(poster.getAttribute('role'), 'button');
  assert.equal(poster.getAttribute('aria-label'), 'Le Parrain');
  assert.equal(plain.getAttribute('tabindex'), null, 'a card without action stays out of the tab order');
});

test('Enter and Space open the focused card only', () => {
  const { enhance, activate } = load();
  const card = fakeCard('poster-card');
  enhance({ querySelectorAll: () => [card], matches: () => false });
  let prevented = 0;
  const press = (key, target) => activate({ key, target, preventDefault: () => { prevented += 1; } });
  press('Enter', card);
  press(' ', card);
  press('a', card);
  press('Enter', { matches: () => false });
  assert.equal(card.clicks, 2);
  assert.equal(prevented, 2);
});

test('focused cards show a monochrome outline', () => {
  assert.match(css, /\.poster-card:focus-visible[^{]*\{ outline: 2px solid #fafafa;/);
});

test('arrow keys move through a grid of cards', () => {
  const { enhance, activate } = load();
  const container = { children: [] };
  let focused = null;
  for (let index = 0; index < 6; index += 1) {
    const card = fakeCard('poster-card', { title: `Film ${index}` });
    const column = index % 3;
    const row = Math.floor(index / 3);
    card.parentElement = container;
    card.getBoundingClientRect = () => ({ left: column * 150, top: row * 260, width: 140, height: 250 });
    card.focus = () => { focused = card; };
    card.scrollIntoView = () => {};
    container.children.push(card);
  }
  enhance({ querySelectorAll: () => container.children, matches: () => false });
  const cards = container.children;
  const press = (key, target) => {
    let prevented = false;
    activate({ key, target, preventDefault: () => { prevented = true; } });
    return prevented;
  };
  press('ArrowRight', cards[0]); assert.equal(focused, cards[1]);
  press('ArrowDown', cards[1]); assert.equal(focused, cards[4]);
  press('ArrowUp', cards[5]); assert.equal(focused, cards[2]);
  press('ArrowLeft', cards[3]); assert.equal(focused, cards[2]);
  focused = null;
  assert.equal(press('ArrowUp', cards[0]), false, 'no row above: the page keeps the key');
  assert.equal(press('ArrowRight', cards[5]), false, 'last card: nothing to focus');
  assert.equal(focused, null);
});

function fakeControl(className, { tagName = 'SPAN', active = false, onclick = true } = {}) {
  const attributes = new Map(onclick ? [['onclick', 'selectGenre("Action", this)']] : []);
  const classes = new Set(className.split(' '));
  if (active) classes.add('active');
  return {
    tagName, clicks: 0,
    classList: { contains: name => classes.has(name) },
    matches: selector => selector.split(',').some(part => {
      part = part.trim();
      const [base, attr] = part.split('[');
      if (base.startsWith('#')) return false;
      if (!base.startsWith('.') || !classes.has(base.slice(1))) return false;
      return attr ? attributes.has(attr.replace(']', '')) : true;
    }),
    hasAttribute: name => attributes.has(name),
    getAttribute: name => attributes.get(name) ?? null,
    setAttribute: (name, value) => attributes.set(name, String(value)),
    click() { this.clicks += 1; },
  };
}

function loadControls() {
  const context = {};
  vm.runInNewContext(`${module_}; this.enhance = enhanceKeyboardControls; this.activate = activateKeyboardCard; this.sync = syncChipPressed;`, context);
  return context;
}

test('genre and source filter chips become toggle buttons reachable with Tab', () => {
  const { enhance, sync } = loadControls();
  const all = fakeControl('chip', { active: true });
  const action = fakeControl('chip');
  const inert = fakeControl('chip', { onclick: false });
  enhance({ querySelectorAll: selector => [all, action, inert].filter(el => el.matches(selector)), matches: () => false });
  for (const chip of [all, action]) {
    assert.equal(chip.getAttribute('tabindex'), '0');
    assert.equal(chip.getAttribute('role'), 'button');
  }
  assert.equal(all.getAttribute('aria-pressed'), 'true');
  assert.equal(action.getAttribute('aria-pressed'), 'false');
  assert.equal(inert.getAttribute('tabindex'), null, 'a chip without action stays out of the tab order');
  sync([all, action], action);
  assert.equal(all.getAttribute('aria-pressed'), 'false');
  assert.equal(action.getAttribute('aria-pressed'), 'true');
});

test('Enter and Space activate a focused chip or cast name, other keys do nothing', () => {
  const { enhance, activate } = loadControls();
  const chip = fakeControl('chip');
  const person = fakeControl('person-link', { onclick: false });
  enhance({ querySelectorAll: selector => [chip, person].filter(el => el.matches(selector)), matches: () => false });
  let prevented = 0;
  const press = (key, target) => activate({ key, target, preventDefault: () => { prevented += 1; } });
  press('Enter', chip);
  press(' ', person);
  press('ArrowRight', chip);
  press('a', person);
  assert.equal(chip.clicks, 1);
  assert.equal(person.clicks, 1);
  assert.equal(prevented, 2, 'arrows stay with the page on filter chips');
});

test('native buttons are left alone', () => {
  const { enhance } = loadControls();
  const button = fakeControl('chip', { tagName: 'BUTTON' });
  enhance({ querySelectorAll: selector => [button].filter(el => el.matches(selector)), matches: () => false });
  assert.equal(button.getAttribute('role'), null);
  assert.equal(button.getAttribute('tabindex'), null);
});

test('cast names, the logo and the account pill are marked as keyboard controls', () => {
  // Since the immersive fiche (step 50), cast and crew are native buttons.
  assert.match(source, /<button type="button" class="detail-person" data-person="\$\{escapeHtml\(person\.name\)\}" onclick='openPerson\(/);
  const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
  assert.match(html, /class="logo no-drag" onclick="switchTab\('movies'\)" aria-label="Accueil KINO"/);
  assert.match(html, /id="userBadge"/);
  assert.match(css, /\.person-link:focus-visible, #userBadge:focus-visible/);
});
