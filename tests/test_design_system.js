// design.css is the reference, loaded last;
// its text never goes below 12 px; the banner takes its colours from its image.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
// Templates declare actions with the page's helpers (act, THIS…): each sandbox gets them.
const { withCalls } = require('../tools/interface_source.js');
const vm = (({ withHelpers }, real) => ({ ...real, runInNewContext: (code, context, ...rest) => real.runInNewContext(code, withHelpers(context), ...rest) }))(require('../tools/interface_source.js'), require('node:vm'));

const html = require('../tools/interface_source.js').indexHtml();
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const source = require('../tools/interface_source.js').appSource();

test('design.css comes after the two older sheets', () => {
  const sheets = [...html.matchAll(/<link rel="stylesheet" href="\/css\/([a-z]+\.css)">/g)].map(m => m[1]);
  assert.deepEqual(sheets, ['style.css', 'redesign.css', 'design.css']);
});

test('one scale of greys, sizes, corners and motion, read by the older names too', () => {
  for (const token of ['--k-bg', '--k-surface-1', '--k-text', '--k-text-2', '--k-text-3', '--k-fs-xs', '--k-fs-md',
    '--k-space-4', '--k-radius', '--k-ease']) assert.match(css, new RegExp(`${token}:`), token);
  assert.match(css, /--muted: var\(--k-text-2\);/);
  assert.match(css, /--k-fs-xs: \.75rem;/, 'the smallest text is 12 px');
  const sizes = [...css.matchAll(/font-size:\s*([0-9.]+)rem/g)].map(m => Number(m[1]));
  assert.ok(sizes.every(size => size >= 0.75), `no literal size under 12 px: ${sizes}`);
});

test('the banner hands its image to the glow, and speaks in sentence case', () => {
  const box = { style: { setProperty(name, value) { this[name] = value; } } };
  const start = source.indexOf('function setHeroImage(box, css) {');
  const images = [];
  const context = { Image: function Image() { images.push(this); } };
  vm.runInNewContext(`${source.slice(start, source.indexOf('\n}\n', start) + 3)} this.set = setHeroImage;`, context);
  context.set(box, "url('https://img/b.jpg')");
  assert.equal(box.style.backgroundImage, "url('https://img/b.jpg')");
  assert.equal(box.style['--hero-image'], "url('https://img/b.jpg')");
  // The glow waits for the image: a late image was only redrawn inside the banner, cutting the glow below it.
  assert.equal(box.style['--hero-glow'], undefined, 'no glow before the image has loaded');
  assert.equal(images[0].src, 'https://img/b.jpg');
  context.set(box, "url('https://img/c.jpg')");
  images[0].onload();
  assert.equal(box.style['--hero-glow'], undefined, 'a previous slide that loads late does not take the glow');
  images[1].onload();
  assert.equal(box.style['--hero-glow'], "url('https://img/c.jpg')");
  assert.match(css, /\.hero-spotlight::after \{[\s\S]*?background: var\(--hero-glow, none\)[\s\S]*?filter: blur\(60px\)/);
  assert.match(css, /\.hero-spotlight::before \{[\s\S]*?var\(--hero-image, none\) center 22% \/ cover/, 'the sharp image sits above the glow');
  assert.match(source, /`À la une · \$\{mtype === 'series' \? 'Série' : 'Film'\}`/);
  assert.doesNotMatch(source, /À LA UNE|PANTHÉON/);
});

test('posters have no frame and no repeated button; their actions show on hover or focus', () => {
  assert.doesNotMatch(source, /class="btn-oneclick"/);
  assert.match(css, /\.poster-card, \.poster-card:hover \{[\s\S]*?border: 0;[\s\S]*?background: transparent;/);
  assert.match(css, /\.poster-card:hover \.poster-preview, \.poster-card:focus-within \.poster-preview/);
  assert.match(css, /\.poster-card:hover \.wl-btn, \.poster-card:focus-within \.wl-btn, \.wl-btn\.in-list/);
});
