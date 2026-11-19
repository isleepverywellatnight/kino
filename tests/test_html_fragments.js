// web/js/html.js: values are escaped unless they are fragments, and render()
// is the one way HTML reaches the page.
const assert = require('node:assert/strict');
const test = require('node:test');
const { html, raw, render, replaceWith, renderAdjacent, safeUrl, escapeHtml, act, SafeHtml } = require('../tools/interface_source.js').templateHelpers();

const HOSTILE = '<img src=x onerror="alert(1)">\'&';

test('a value is escaped, a fragment is kept, a list is joined', () => {
  const name = HOSTILE;
  const out = html`<p title="${name}">${name}</p>`;
  assert.ok(out instanceof SafeHtml);
  assert.equal(String(out), '<p title="&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&#39;&amp;">&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&#39;&amp;</p>');
  assert.equal(String(html`<ul>${['a<', 'b'].map(item => html`<li>${item}</li>`)}</ul>`), '<ul><li>a&lt;</li><li>b</li></ul>');
  assert.equal(String(html`${raw('<b>ok</b>')}`), '<b>ok</b>');
});

test('nothing is escaped twice', () => {
  assert.equal(String(html`${escapeHtml('<x>')}`), '&lt;x&gt;');
  assert.equal(String(html`<b ${act('open', 'a"b')}>`), '<b data-on-click="open" data-click-args="[&quot;a\\&quot;b&quot;]">');
  assert.equal(String(html`${html`<i>${'<'}</i>`}`), '<i>&lt;</i>');
});

test('empty values: nothing for null and undefined, false and zero as written', () => {
  assert.equal(String(html`[${null}${undefined}]`), '[]');
  assert.equal(String(html`<b aria-pressed="${false}">${0}</b>`), '<b aria-pressed="false">0</b>');
});

test('render writes fragments as markup and anything else as text', () => {
  const box = { innerHTML: '' };
  render(box, html`<em>${HOSTILE}</em>`);
  assert.match(box.innerHTML, /^<em>&lt;img/);
  render(box, '<script>alert(1)</script>');
  assert.equal(box.innerHTML, '&lt;script&gt;alert(1)&lt;/script&gt;', 'a plain string is text');
  render(box, [html`<a>`, 'x<', html`</a>`]);
  assert.equal(box.innerHTML, '<a>x&lt;</a>');
  render(null, html`<b>`);
  const outer = { outerHTML: '' };
  replaceWith(outer, html`<p>${'<'}</p>`);
  assert.equal(outer.outerHTML, '<p>&lt;</p>');
  const calls = [];
  renderAdjacent({ insertAdjacentHTML: (position, text) => calls.push([position, text]) }, 'beforeend', '<b>');
  assert.deepEqual(calls, [['beforeend', '&lt;b&gt;']]);
});

test('safeUrl keeps web, page and image addresses, drops the rest', () => {
  for (const url of ['https://x/y', 'http://x', '//cdn/x', '/api/x', 'data:image/png;base64,AA', 'poster.jpg']) assert.equal(safeUrl(url), url);
  for (const url of ['javascript:alert(1)', ' JavaScript:alert(1)', 'data:text/html,<b>', 'vbscript:x']) assert.equal(safeUrl(url), '', url);
});
