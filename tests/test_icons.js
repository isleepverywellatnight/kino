// one icon set. Every icon outside the players comes
// from web/icons.svg, draws with the same fine line, and each sidebar tab shows a
// filled twin when it is active, without the old grey box.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const html = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/index.html'), 'utf8');
const icons = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/icons.svg'), 'utf8');
const css = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/css/design.css'), 'utf8');
const symbols = new Set([...icons.matchAll(/<symbol id="([^"]+)"/g)].map(m => m[1]));

test('symbols leave the line width to the size classes', () => {
  assert.doesNotMatch(icons, /stroke-width=/, 'design.css sets the stroke width');
  assert.match(css, /\.ui-icon \{ stroke-width: 1\.6; \}/);
  assert.match(css, /\.ui-icon-xs \{[^}]*stroke-width: 2\.1;/);
});

test('each sidebar tab has an outline icon and its filled twin', () => {
  const nav = html.slice(html.indexOf('<nav class="nav-tabs"'), html.indexOf('</nav>'));
  const tabs = [...nav.matchAll(/<button class="nav-tab[^"]*" id="([^"]+)"[\s\S]*?<\/button>/g)];
  assert.equal(tabs.length, 8, "Films, Séries, Anime, Manga, Ce soir, Explorer, Bibliothèque, Paramètres");
  for (const [button, id] of tabs) {
    const off = button.match(/class="ui-icon icon-off"[^>]*><use href="\/icons\.svg#([a-z-]+)">/);
    const on = button.match(/class="ui-icon icon-on"[^>]*><use href="\/icons\.svg#([a-z-]+)">/);
    assert.ok(off && on, id);
    assert.equal(on[1], `${off[1]}-fill`, id);
    assert.ok(symbols.has(off[1]) && symbols.has(on[1]), id);
  }
  assert.doesNotMatch(nav, /viewBox=/, 'no icon drawn in place in the sidebar');
});

test('the active tab is white and filled, with no box', () => {
  assert.match(css, /\.nav-tab\.active \.nav-icon \.icon-off \{ display: none; \}/);
  assert.match(css, /\.nav-tab\.active \.nav-icon \.icon-on \{ display: block; \}/);
  assert.match(css, /\.nav-tab\.active, \.nav-tab\.active:hover \{[^}]*background: transparent;[^}]*border-color: transparent;/);
});

test('top bar, tools menu, search and settings draw from the sprite only', () => {
  const chrome = html.slice(0, html.indexOf('<div class="container">'));
  const settings = html.slice(html.indexOf('data-settings-target="settings-player"'), html.indexOf('id="settings-shortcuts"'));
  for (const [name, part] of [['chrome', chrome], ['settings', settings]]) {
    const inline = [...part.replace(/<div[^>]*class="[^"]*win-(?:titlebar|controls)[\s\S]*?<\/div>/g, '').matchAll(/<svg\b(?![^>]*class="(?:ui-icon|mac-icon|logo-mark|win-icon))[^>]*>/g)];
    assert.deepEqual(inline.map(m => m[0]), [], name);
  }
  for (const [target, id] of [['settings-player', 'play'], ['settings-account', 'cloud'], ['settings-connections', 'sync'], ['settings-app', 'window']]) {
    assert.match(html, new RegExp(`data-settings-target="${target}"(?: aria-current="location")?><svg class="ui-icon" aria-hidden="true"><use href="/icons\\.svg#${id}">`), target);
  }
});

test('one KINO logo: the page header, the phone remote and the tab icon share the frame and its K', () => {
  const remote = fs.readFileSync(path.join(path.join(__dirname, '..'), 'web/remote.html'), 'utf8');
  const mark = html.match(/<svg class="logo-mark" viewBox="130 130 764 764"[^>]*>([\s\S]*?)<\/svg>/);
  assert.ok(mark, 'header mark drawn on the 1024 grid (tools/render_logo.swift)');
  assert.match(mark[1], /<rect x="176" y="176" width="672" height="672" rx="150" fill="none" stroke="#fafafa" stroke-width="76"\/>/);
  assert.ok(remote.includes(mark[1]), 'the remote page uses the same mark');
  assert.match(html, /<link rel="icon" type="image\/svg\+xml" href="data:image\/svg\+xml,[^"]*viewBox=%22100 100 824 824%22/);
  for (const [name, text] of [['index.html', html], ['web/remote.html', remote]]) {
    assert.doesNotMatch(text, /23\.5,7\.5|M10 8v16M10 16l11-8v16/, `${name}: the old « previous » mark is gone`);
  }
  for (const file of ['assets/AppIcon.icns', 'assets/kino.png', 'assets/kino.ico', 'tools/render_logo.swift']) assert.ok(fs.existsSync(path.join(path.join(__dirname, '..'), file)), file);
});
