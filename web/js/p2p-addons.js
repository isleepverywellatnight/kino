// Sources without a debrid service: add-ons the user adds by their address
// (more sources, and their catalogues in Explorer; they speak the Stremio add-on
// protocol), and the torrents KINO reads itself, peer to peer, once the user has
// agreed to what it means.

import { registerActions } from './actions.js';
import { CHECKED, act, actOn, attrJson, html, render } from './html.js';
import { api } from './app-core.js';
import { LIST_SHELF_SIZE } from './app-letterboxd.js';
import { posterCardHtml } from './app-tabs.js';
import { offlineSize } from './app-start.js';

let stremioAddons = [];
let stremioCatalogs = [];
let p2pState = { available: false, accepted: false, upload: 'minimum', download: 'none', encrypted: false, port: 0, cache_bytes: 0 };
let p2pConsentAnswer = null;

// ---------- The consent, asked once ----------

// Resolves true once the user agreed (and KINO saved it), false otherwise.
export function askP2PConsent() {
  const dialog = /** @type {HTMLDialogElement} */ (document.getElementById('p2pConsent'));
  if (!dialog) return Promise.resolve(false);
  const upload = /** @type {HTMLSelectElement} */ (document.getElementById('p2pConsentUpload'));
  if (upload) upload.value = p2pState.upload || 'minimum';
  if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
  return new Promise(resolve => { p2pConsentAnswer = resolve; });
}

async function answerP2PConsent(accepted) {
  const dialog = /** @type {HTMLDialogElement} */ (document.getElementById('p2pConsent'));
  const resolve = p2pConsentAnswer;
  p2pConsentAnswer = null;
  if (dialog?.open) dialog.close();
  if (!accepted) {
    renderP2PSettings();
    if (resolve) resolve(false);
    return;
  }
  try {
    const upload = /** @type {HTMLSelectElement} */ (document.getElementById('p2pConsentUpload'))?.value || 'minimum';
    p2pState = await api('/api/p2p/consent', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accept: true, upload }) });
  } catch (error) {
    alert(`La lecture P2P n'a pas pu être activée : ${error.message}`);
    if (resolve) resolve(false);
    return;
  }
  renderP2PSettings();
  if (resolve) resolve(true);
}

// ---------- Paramètres → Compte ----------

export async function loadSourcesSettings() {
  try {
    const [p2p, addons] = await Promise.all([api('/api/p2p'), api('/api/stremio-addons')]);
    p2pState = p2p || p2pState;
    stremioAddons = addons?.addons || [];
    stremioCatalogs = addons?.catalogs || [];
  } catch (error) {
    // The rows keep what they showed.
  }
  renderP2PSettings();
  renderStremioAddons();
}

function renderP2PSettings() {
  const toggle = /** @type {HTMLInputElement} */ (document.getElementById('cfgP2P'));
  const status = document.getElementById('cfgP2PStatus');
  if (toggle) {
    toggle.checked = Boolean(p2pState.accepted);
    toggle.disabled = !p2pState.available;
  }
  // The choices can be made before agreeing: they apply once P2P is on.
  const fields = { cfgP2PUpload: p2pState.upload || 'minimum', cfgP2PDownload: p2pState.download || 'none',
    cfgP2PPort: p2pState.port ? String(p2pState.port) : '' };
  for (const [id, value] of Object.entries(fields)) {
    const field = /** @type {HTMLInputElement | HTMLSelectElement | null} */ (document.getElementById(id));
    if (field) {
      field.value = value;
      field.disabled = !p2pState.available;
    }
  }
  const encrypted = /** @type {HTMLInputElement} */ (document.getElementById('cfgP2PEncrypted'));
  if (encrypted) {
    encrypted.checked = Boolean(p2pState.encrypted);
    encrypted.disabled = !p2pState.available;
  }
  const cache = document.getElementById('cfgP2PCache');
  const clear = /** @type {HTMLButtonElement} */ (document.getElementById('cfgP2PClear'));
  const bytes = Number(p2pState.cache_bytes) || 0;
  if (cache) {
    cache.textContent = bytes > 0 ? `${offlineSize(bytes)} sur cet ordinateur. KINO garde les deux derniers torrents lus et efface le reste au démarrage.`
      : 'Aucun fichier pour l’instant.';
  }
  if (clear) clear.disabled = bytes <= 0;
  if (status) {
    status.textContent = !p2pState.available ? 'Indisponible dans cette version de KINO.'
      : p2pState.accepted ? 'Activée : sans service de débridage, KINO lit les torrents lui-même.'
        : 'Désactivée : sans service de débridage, seuls les liens directs des addons sont lus.';
  }
}

async function setP2PEnabled(input) {
  if (input.checked) {
    input.checked = false;
    if (await askP2PConsent()) input.checked = true;
    return;
  }
  try {
    p2pState = await api('/api/p2p/consent', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accept: false }) });
  } catch (error) {
    input.checked = true;
  }
  renderP2PSettings();
}

async function setP2PSetting(name, value) {
  const port = Number(value);
  if (name === 'port' && value !== '' && !(Number.isInteger(port) && port >= 1024 && port <= 65535)) {
    alert('Choisissez un port entre 1024 et 65535, ou laissez vide.');
    renderP2PSettings();
    return;
  }
  try {
    p2pState = await api('/api/p2p/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [name]: value }) });
  } catch (error) {
    alert(`Réglage non enregistré : ${error.message}`);
  }
  renderP2PSettings();
}

async function clearP2PCache() {
  if (!confirm('Vider les fichiers temporaires ? Une lecture P2P en cours s’arrête.')) return;
  try {
    p2pState = await api('/api/p2p/clear', { method: 'POST' });
  } catch (error) {
    alert(error.message);
  }
  renderP2PSettings();
}

function stremioAddonHtml(addon) {
  const offers = [addon.streams ? 'Sources' : '', addon.catalogs?.length ? `${addon.catalogs.length} catalogue${addon.catalogs.length > 1 ? 's' : ''}` : '']
    .filter(Boolean).join(' · ');
  const id = attrJson(addon.id);
  return html`<li class="addon-row${addon.enabled ? '' : ' is-off'}">
      <div class="addon-text">
        <strong>${addon.name}</strong>
        <span>${[offers, addon.version ? `v${addon.version}` : ''].filter(Boolean).join(' · ')}</span>
      </div>
      <label class="k-switch" title="${addon.enabled ? 'Désactiver cet addon' : 'Activer cet addon'}"><input type="checkbox" role="switch" aria-label="${addon.name} actif"${addon.enabled ? ' checked' : ''} ${actOn('change', 'toggleStremioAddon', addon.id, CHECKED)}><span aria-hidden="true"></span></label>
      <button type="button" class="btn btn-secondary btn-small" ${act('removeStremioAddon', addon.id)} aria-label="Retirer ${addon.name}">${KINO_I18N.t('page.retirer')}</button>
    </li>`;
}

function renderStremioAddons() {
  const list = document.getElementById('stremioAddonList');
  if (!list) return;
  render(list, stremioAddons.length ? stremioAddons.map(stremioAddonHtml)
    : html`<li class="addon-empty">${KINO_I18N.t('p2p.aucun_addon_pour_l_instant_collez_l')}</li>`);
}

async function changeStremioAddons(payload) {
  const data = await api('/api/stremio-addons', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload) });
  stremioAddons = data?.addons || [];
  stremioCatalogs = data?.catalogs || [];
  renderStremioAddons();
}

async function addStremioAddon(event) {
  if (event) event.preventDefault();
  const input = /** @type {HTMLInputElement} */ (document.getElementById('stremioAddonUrl'));
  const button = /** @type {HTMLButtonElement} */ (document.getElementById('stremioAddonAdd'));
  const url = input?.value.trim();
  if (!url) return;
  if (button) button.disabled = true;
  try {
    await changeStremioAddons({ action: 'add', url });
    input.value = '';
  } catch (error) {
    alert(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function toggleStremioAddon(id, enabled) {
  try { await changeStremioAddons({ action: 'enable', id, enabled }); } catch (error) { alert(error.message); }
}

async function removeStremioAddon(id) {
  const addon = stremioAddons.find(a => a.id === id);
  if (!confirm(`Retirer l’addon « ${addon?.name || id} » ?`)) return;
  try { await changeStremioAddons({ action: 'remove', id }); } catch (error) { alert(error.message); }
}

// ---------- Explorer: the add-ons' catalogues ----------

export async function renderAddonCatalogs() {
  const box = document.getElementById('addonCatalogs');
  if (!box) return;
  try {
    const data = await api('/api/stremio-addons');
    stremioCatalogs = data?.catalogs || [];
  } catch (error) {
    stremioCatalogs = [];
  }
  box.hidden = !stremioCatalogs.length;
  if (!stremioCatalogs.length) {
    render(box, '');
    return;
  }
  render(box, stremioCatalogs.map((catalog, index) => html`
    <section class="list-shelf" aria-label="${catalog.name}">
      <div class="list-shelf-head">
        <div class="list-shelf-info">
          <h3 class="list-shelf-title">${catalog.name}</h3>
          <div class="list-shelf-meta">${catalog.addon_name} · ${catalog.type === 'series' ? 'Séries' : 'Films'}</div>
        </div>
      </div>
      <div class="list-shelf-row" data-addon-catalog="${index}"><p class="list-shelf-empty">${KINO_I18N.t('anime.chargement')}</p></div>
    </section>`));
  await Promise.all(stremioCatalogs.map(async (catalog, index) => {
    const row = box.querySelector(`[data-addon-catalog="${index}"]`);
    try {
      const data = await api(`/api/stremio-addons/catalog?addon=${encodeURIComponent(catalog.addon)}&type=${encodeURIComponent(catalog.type)}&id=${encodeURIComponent(catalog.id)}`);
      const items = data?.items || [];
      if (row) render(row, items.length ? items.slice(0, LIST_SHELF_SIZE).map(m => posterCardHtml(m, catalog.type))
        : html`<p class="list-shelf-empty">${KINO_I18N.t('p2p.ce_catalogue_est_vide')}</p>`);
    } catch (error) {
      if (row) render(row, html`<p class="list-shelf-empty">${KINO_I18N.t('p2p.ce_catalogue_ne_repond_pas_pour_le')}</p>`);
    }
  }));
}

// The peer-to-peer consent dialog closed without an answer: a « no ».
function kinoP2PConsentClosed() {
  if (p2pConsentAnswer) answerP2PConsent(false);
}

// What the markup may run from this file (web/js/actions.js).
registerActions({
  addStremioAddon, answerP2PConsent, clearP2PCache, kinoP2PConsentClosed, removeStremioAddon,
  setP2PEnabled, setP2PSetting, toggleStremioAddon,
});
