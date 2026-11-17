// --- Cloud: files gathered by work (cloud_titles.py), with the catalogue poster ---
let cloudGroups = [];
let cloudFilter = 'all';
let cloudQuery = '';
const cloudOpen = new Set();
const cloudAsked = new Set();
const CLOUD_MATCHES = new Map();
const CLOUD_VIDEO = /\.(mkv|mp4|avi|m4v|mov|ts|m2ts|wmv|webm|mpe?g)$/i;
const CLOUD_MONTHS = KINO_I18N.months('short');

function cloudDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
  if (!match || Number(match[2]) < 1 || Number(match[2]) > 12) return '';
  const year = Number(match[1]) === new Date().getFullYear() ? '' : ` ${match[1]}`;
  return `${Number(match[3])} ${CLOUD_MONTHS[Number(match[2]) - 1]}${year}`;
}

function cloudPlural(count, word) {
  return `${count} ${word}${count > 1 ? 's' : ''}`;
}

function cloudEpisodeCode(file) {
  const pad = n => String(n).padStart(2, '0');
  if (file.season != null && file.episode != null) return `S${pad(file.season)}E${pad(file.episode)}`;
  if (file.episode != null) return `Épisode ${file.episode}`;
  if (file.season != null) return `Saison ${file.season}`;
  return '';
}

function cloudQualityLabel(file) {
  return [file.quality, file.hdr ? 'HDR' : ''].filter(Boolean).join(' · ');
}

function cloudSeasonsText(seasons) {
  if (!seasons || !seasons.length) return '';
  if (seasons.length === 1) return `saison ${seasons[0]}`;
  return `saisons ${seasons.slice(0, -1).join(', ')} et ${seasons.at(-1)}`;
}

function cloudGroup(key) {
  return cloudGroups.find(group => group.key === key) || null;
}

function cloudMedia(group) {
  const media = group ? CLOUD_MATCHES.get(group.key) : null;
  return media && media.id ? media : null;
}

function cloudName(group) {
  if (group.kind === 'other') return 'Autres fichiers';
  const media = cloudMedia(group);
  return media ? displayTitle(media) : group.title;
}

function cloudFileWatched(group, file) {
  const media = cloudMedia(group);
  if (!media) return false;
  if (group.kind === 'series') {
    return file.season != null && file.episode != null && Spoilers.watched(userHistory, media.id, file.season, file.episode);
  }
  return group.kind === 'movie' && listItemState(media) === 'watched';
}

// A series starts at its first episode not seen yet.
function cloudStartIndex(group) {
  if (group.kind !== 'series') return 0;
  const index = group.files.findIndex(file => !cloudFileWatched(group, file));
  return index < 0 ? 0 : index;
}

function cloudGroupMeta(group) {
  const parts = [];
  if (group.kind === 'series') {
    const episodes = group.files.filter(file => file.episode != null).length;
    const seen = group.files.filter(file => cloudFileWatched(group, file)).length;
    parts.push('Série', episodes === group.count ? cloudPlural(episodes, 'épisode') : cloudPlural(group.count, 'fichier'));
    if (seen) parts.push(`${seen} vu${seen > 1 ? 's' : ''}`);
    const seasons = cloudSeasonsText(group.seasons);
    if (seasons) parts.push(seasons);
  } else if (group.kind === 'movie') {
    parts.push('Film');
    if (group.count > 1) parts.push(`${group.count} versions`);
    else if (cloudQualityLabel(group.files[0])) parts.push(cloudQualityLabel(group.files[0]));
    if (cloudFileWatched(group, group.files[0])) parts.push('vu');
  } else {
    parts.push(cloudPlural(group.count, 'fichier'));
  }
  if (Number(group.bytes) > 0) parts.push(offlineSize(group.bytes));
  const added = cloudDate(group.latest);
  if (added) parts.push(`ajouté le ${added}`);
  return parts.join(' · ');
}

function cloudToggleLabel(group) {
  if (group.kind === 'series' && group.files.every(file => file.episode != null)) return cloudPlural(group.count, 'épisode');
  return group.count > 1 ? `${group.count} fichiers` : 'Fichier';
}

function cloudGroupHtml(group) {
  const media = cloudMedia(group);
  const key = attrJson(group.key);
  const open = cloudOpen.has(group.key);
  const name = cloudName(group);
  const year = group.kind === 'other' ? '' : (media?.year || group.year || '');
  const initial = `<span aria-hidden="true">${escapeHtml((name || '?').trim().charAt(0).toUpperCase())}</span>`;
  const image = media?.poster ? `<img src="${escapeHtml(media.poster)}" alt="" loading="lazy" onerror="this.remove()">` : '';
  const poster = media
    ? `<button type="button" class="cloud-poster" onclick="openCloudFiche(${key})" aria-label="Ouvrir la fiche de ${escapeHtml(name)}">${initial}${image}</button>`
    : `<div class="cloud-poster" aria-hidden="true">${initial}</div>`;
  const title = media ? `<span${titleAttrs(media, group.kind)}>${escapeHtml(name)}</span>` : `<span>${escapeHtml(name)}</span>`;
  const start = cloudStartIndex(group);
  const code = group.kind === 'series' ? cloudEpisodeCode(group.files[start]) : '';
  const play = group.kind === 'other' ? ''
    : `<button type="button" class="btn" onclick="playCloudFile(${key}, ${start}, this)">${code ? `Lancer ${escapeHtml(code)}` : 'Lancer'}</button>`;
  const files = !open ? '' : `<div class="cloud-files">${group.files.map((file, index) => cloudFileHtml(group, file, index)).join('')}${
    group.count > 1 ? `<div class="cloud-files-foot"><button type="button" class="btn btn-secondary" onclick="deleteCloudGroup(${key}, this)">Retirer les ${group.count} fichiers du Cloud</button></div>` : ''}</div>`;
  return `<article class="cloud-group${open ? ' is-open' : ''}" aria-label="${escapeHtml(name)}">
    ${poster}
    <div class="cloud-group-main">
      <h4 class="cloud-group-title">${title}${year ? ` <span class="cloud-group-year">${escapeHtml(year)}</span>` : ''}</h4>
      <div class="cloud-group-meta">${escapeHtml(cloudGroupMeta(group))}</div>
    </div>
    <div class="cloud-group-actions">${play}<button type="button" class="btn btn-secondary cloud-toggle" onclick="toggleCloudGroup(${key})" aria-expanded="${open}">${cloudToggleLabel(group)}<span class="cloud-chevron" aria-hidden="true"></span></button></div>
    ${files}
  </article>`;
}

function cloudFileHtml(group, file, index) {
  const key = attrJson(group.key);
  const label = group.kind === 'series' ? (cloudEpisodeCode(file) || 'Fichier')
    : group.kind === 'movie' ? (cloudQualityLabel(file) || `Version ${index + 1}`) : file.filename;
  const added = cloudDate(file.generated);
  const size = Number(file.bytes) > 0 ? offlineSize(file.bytes) : (file.filesize || '');
  const details = [group.kind === 'other' ? '' : file.filename, size, added ? `ajouté le ${added}` : ''].filter(Boolean).join(' · ');
  // Only a web address is offered; « other » files play only when they are videos.
  const web = /^https?:\/\//i.test(String(file.download || ''));
  const playable = web && (group.kind !== 'other' || CLOUD_VIDEO.test(file.filename));
  const play = playable ? `<button type="button" class="btn" onclick="playCloudFile(${key}, ${index}, this)">Lancer</button>` : '';
  const save = web ? `<button type="button" class="btn btn-secondary" onclick="startPcDownload(${attrJson(file.download)}, ${attrJson(file.filename)})">Télécharger</button>
        <a class="btn btn-secondary" href="${escapeHtml(file.download)}" target="_blank" rel="noopener">Lien direct</a>` : '';
  return `<div class="cloud-file">
      <div class="cloud-file-main">
        <div class="cloud-file-label">${escapeHtml(label)}${cloudFileWatched(group, file) ? ' <span class="cloud-seen">Vu</span>' : ''}</div>
        <div class="cloud-file-name" title="${escapeHtml(file.filename)}">${escapeHtml(details)}</div>
      </div>
      <div class="cloud-file-actions">
        ${play}
        ${save}
        <button type="button" class="btn btn-secondary" onclick="deleteCloudFile(${key}, ${index}, this)" aria-label="Retirer ${escapeHtml(file.filename)} du Cloud">Retirer</button>
      </div>
    </div>`;
}

function cloudVisibleGroups() {
  const query = foldSearchText(cloudQuery.trim());
  return cloudGroups.filter(group => (cloudFilter === 'all' || group.kind === cloudFilter) && (!query
    || foldSearchText([cloudName(group), cloudMedia(group)?.name || '', group.title, ...group.files.map(file => file.filename)].join(' ')).includes(query)));
}

function renderCloud() {
  const tools = document.getElementById('rdCloudTools');
  const list = document.getElementById('rdCloudList');
  if (!cloudGroups.length) {
    if (tools) { tools.hidden = true; tools.innerHTML = ''; }
    list.innerHTML = `<p class="cloud-empty">Aucun fichier récent sur votre compte ${escapeHtml(window.activeDebridName || 'Debrid')}.</p>`;
    return;
  }
  const counts = { all: cloudGroups.length, movie: 0, series: 0, other: 0 };
  cloudGroups.forEach(group => { counts[group.kind] = (counts[group.kind] || 0) + 1; });
  if (!counts[cloudFilter]) cloudFilter = 'all';
  if (tools) {
    const chips = [['all', 'Tout'], ['movie', 'Films'], ['series', 'Séries'], ['other', 'Autres']]
      .filter(([kind]) => kind === 'all' || counts[kind])
      .map(([kind, label]) => `<button type="button" class="chip${cloudFilter === kind ? ' active' : ''}" aria-pressed="${cloudFilter === kind}" onclick="setCloudFilter('${kind}')">${label} <span class="list-count">${counts[kind]}</span></button>`)
      .join('');
    const files = cloudGroups.reduce((total, group) => total + group.count, 0);
    const bytes = cloudGroups.reduce((total, group) => total + (Number(group.bytes) || 0), 0);
    const summary = [cloudPlural(cloudGroups.length, 'titre'), cloudPlural(files, 'fichier'), bytes > 0 ? offlineSize(bytes) : ''].filter(Boolean).join(' · ');
    tools.hidden = false;
    tools.innerHTML = `<div class="cloud-chips" role="group" aria-label="Type de titre">${chips}</div>
      <input type="search" id="rdCloudSearch" class="cloud-search" placeholder="Rechercher un titre" aria-label="Rechercher dans le Cloud" value="${escapeHtml(cloudQuery)}" oninput="onCloudSearch(this.value)">
      <span class="cloud-summary">${escapeHtml(summary)}</span>`;
  }
  renderCloudList();
}

function renderCloudList() {
  const list = document.getElementById('rdCloudList');
  if (!list || !cloudGroups.length) return;
  const visible = cloudVisibleGroups();
  list.innerHTML = visible.length ? visible.map(cloudGroupHtml).join('') : '<p class="cloud-empty">Aucun titre ne correspond.</p>';
}

function setCloudFilter(kind) {
  cloudFilter = kind;
  renderCloud();
}

function onCloudSearch(value) {
  cloudQuery = String(value || '');
  renderCloudList();
}

function toggleCloudGroup(key) {
  if (cloudOpen.has(key)) cloudOpen.delete(key);
  else cloudOpen.add(key);
  renderCloudList();
}

// Posters and catalogue titles arrive after the list; a miss is asked again on the next visit.
async function requestCloudMatches() {
  const titles = cloudGroups.filter(group => group.kind !== 'other' && !CLOUD_MATCHES.has(group.key) && !cloudAsked.has(group.key))
    .slice(0, 40).map(group => ({ key: group.key, title: group.title, year: group.year, kind: group.kind }));
  if (!titles.length) return;
  titles.forEach(title => cloudAsked.add(title.key));
  let result;
  try {
    result = await api('/api/cloud-titles', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ titles }),
    });
  } catch (error) {
    return;
  }
  const found = Object.entries(result.matches || {}).filter(([, media]) => media && media.id);
  if (found.length) {
    found.forEach(([key, media]) => CLOUD_MATCHES.set(key, media));
    if (activeTab === 'rdcloud') renderCloudList();
  }
  requestCloudMatches();
}

function openCloudFiche(key) {
  const group = cloudGroup(key);
  const media = cloudMedia(group);
  if (media) selectMedia({ ...media, type: group.kind });
}

// Played under its own title (history, resume, Trakt), episodes chained in order.
function playCloudFile(key, index, btn) {
  const group = cloudGroup(key);
  const file = group?.files[index];
  if (!file || !/^https?:\/\//i.test(String(file.download || ''))) return;
  const media = cloudMedia(group);
  const episode = group.kind === 'series' && file.season != null && file.episode != null;
  const entryTitle = item => {
    const code = cloudEpisodeCode(item);
    return /^S\d/.test(code) ? `${code} · ${cloudName(group)}` : item.filename;
  };
  const files = group.kind === 'series'
    ? group.files.slice(index).map(item => ({ filename: entryTitle(item), download: item.download }))
    : null;
  return openMpv(btn, file.download, episode ? entryTitle(file) : file.filename, {
    media: media ? { ...media, type: group.kind } : null,
    season: episode ? file.season : null,
    episode: episode ? file.episode : null,
    files,
  });
}

async function deleteCloudFile(key, index, btn) {
  const file = cloudGroup(key)?.files[index];
  if (!file) return;
  const pName = window.activeDebridName || 'votre débrideur';
  if (!window.confirm(`Retirer « ${file.filename} » du Cloud ${pName} ?`)) return;
  await deleteRdCloudItem(btn, file.ids || [file.id]);
}

async function deleteCloudGroup(key, btn) {
  const group = cloudGroup(key);
  if (!group) return;
  const pName = window.activeDebridName || 'votre débrideur';
  if (!window.confirm(`Retirer les ${group.count} fichiers de « ${cloudName(group)} » du Cloud ${pName} ?`)) return;
  await deleteRdCloudItem(btn, group.files.flatMap(file => file.ids || [file.id]));
}

async function deleteRdCloudItem(btn, ids) {
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Suppression…'; }
  try {
    await api('/api/rd-delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ids})
    });
    await loadRdCloud();
  } catch (e) {
    alert('Erreur suppression : ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

async function onChangeRdRetention(daysStr) {
  const rd_retention_days = parseInt(daysStr || '0', 10);
  if (document.getElementById('cfgRdRetention')) {
    document.getElementById('cfgRdRetention').value = String(rd_retention_days);
  }
  await api('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rd_retention_days})
  });
  await loadRdCloud();
}

async function purgeRdCloud(mode, btn) {
  const orig = btn ? btn.textContent : '';
  const pName = window.activeDebridName || 'votre débrideur';
  if (mode === 'all' && !window.confirm(`Vider tout le Cloud ${pName} ? Tous les fichiers et torrents du compte seront supprimés, sans retour possible.`)) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Suppression...'; }
  try {
    const res = await api('/api/rd-cleanup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({max_age_days: mode === 'all' ? -1 : null})
    });
    const noteEl = document.getElementById('rdCloudStatusNote');
    if (noteEl && res.cleaned) {
      noteEl.textContent = `${res.cleaned.deleted_downloads} fichier(s) et ${res.cleaned.deleted_torrents} torrent(s) supprimés du Cloud ${pName}.`;
    }
    await loadRdCloud();
  } catch (e) {
    alert('Erreur nettoyage Cloud : ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

async function openTrailerModal(trailerId, title, year = '', lang = 'vf') {
  currentTrailerCtx = {trailerId: trailerId || '', title: title || '', year: year || '', lang: lang || 'vf'};
  const modal = document.getElementById('trailerModal');
  const video = document.getElementById('trailerVideo');
  const iframe = document.getElementById('trailerIframe');
  const loading = document.getElementById('trailerLoading');
  const titleEl = document.getElementById('trailerModalTitle');
  const extLink = document.getElementById('trailerExternalLink');
  const btnVf = document.getElementById('trailerBtnVf');
  const btnVo = document.getElementById('trailerBtnVo');
  if (!modal) return;

  if (btnVf) btnVf.classList.toggle('active', currentTrailerCtx.lang === 'vf');
  if (btnVo) btnVo.classList.toggle('active', currentTrailerCtx.lang === 'vo');

  titleEl.textContent = `Bande-annonce (${currentTrailerCtx.lang.toUpperCase()}) — ${title || 'KINO'}`;
  if (video) { video.pause(); video.removeAttribute('src'); video.style.display = 'none'; }
  if (iframe) { iframe.src = ''; iframe.style.display = 'none'; }
  if (loading) loading.style.display = 'flex';
  modal.style.display = 'flex';

  try {
    const qs = new URLSearchParams({
      title: currentTrailerCtx.title,
      year: currentTrailerCtx.year,
      yt_id: currentTrailerCtx.trailerId,
      lang: currentTrailerCtx.lang
    });
    const data = await api(`/api/trailer?${qs.toString()}`);
    if (extLink && data.watch_url) extLink.href = data.watch_url;
    if (loading) loading.style.display = 'none';
    if (data.stream_url && video) {
      video.src = data.stream_url;
      video.style.display = 'block';
      video.play().catch(() => {});
    } else if (data.embed_url && iframe) {
      iframe.src = data.embed_url;
      iframe.style.display = 'block';
    }
  } catch (e) {
    if (loading) loading.style.display = 'none';
    if (trailerId && iframe) {
      extLink.href = `https://www.youtube.com/watch?v=${encodeURIComponent(trailerId)}`;
      iframe.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(trailerId)}?autoplay=1&rel=0`;
      iframe.style.display = 'block';
    }
  }
}

function switchTrailerLang(lang) {
  if (!currentTrailerCtx) return;
  openTrailerModal(currentTrailerCtx.trailerId, currentTrailerCtx.title, currentTrailerCtx.year, lang);
}

function closeTrailerModal() {
  const modal = document.getElementById('trailerModal');
  const video = document.getElementById('trailerVideo');
  const iframe = document.getElementById('trailerIframe');
  if (video) { video.pause(); video.removeAttribute('src'); video.style.display = 'none'; }
  if (iframe) { iframe.src = ''; iframe.style.display = 'none'; }
  if (modal) modal.style.display = 'none';
}

function toggleHeaderMenu(e) {
  if (e) e.stopPropagation();
  const wrap = document.querySelector('.header-menu-wrap');
  const menu = document.getElementById('headerDropdownMenu');
  const button = document.getElementById('headerSettingsBtn');
  if (!menu) return;
  const isShown = (menu.style.display === 'flex');
  if (isShown) {
    menu.style.display = 'none';
    if (button) button.setAttribute('aria-expanded', 'false');
    if (wrap) wrap.classList.remove('active');
  } else {
    menu.style.display = 'flex';
    if (button) button.setAttribute('aria-expanded', 'true');
    if (wrap) wrap.classList.add('active');
  }
}

function closeHeaderMenu() {
  const wrap = document.querySelector('.header-menu-wrap');
  const menu = document.getElementById('headerDropdownMenu');
  const button = document.getElementById('headerSettingsBtn');
  if (menu) menu.style.display = 'none';
  if (button) button.setAttribute('aria-expanded', 'false');
  if (wrap) wrap.classList.remove('active');
}

window.addEventListener('click', (e) => {
  const wrap = document.querySelector('.header-menu-wrap');
  if (wrap && !wrap.contains(e.target)) {
    closeHeaderMenu();
  }
});

function openConfig() { 
  if (activeTab && activeTab !== 'settings') {
    previousTab = activeTab;
  }
  switchTab('settings');
}

// --- Diagnostic: what works, and what to do about the rest ---
const DIAGNOSTIC_MARKS = { ok: '✓', warning: '!', problem: '×', off: '–' };
const DIAGNOSTIC_STATES = { ok: 'OK', warning: 'À vérifier', problem: 'Problème', off: 'Désactivé' };
let diagnosticReport = '';

function diagnosticSummaryText(items) {
  const problems = items.filter(item => item.state === 'problem').length;
  const warnings = items.filter(item => item.state === 'warning').length;
  if (!problems && !warnings) return 'Tout fonctionne.';
  const parts = [];
  if (problems) parts.push(`${problems} problème${problems > 1 ? 's' : ''}`);
  if (warnings) parts.push(`${warnings} point${warnings > 1 ? 's' : ''} à vérifier`);
  return `${parts.join(' et ')}.`;
}

function diagnosticItemHtml(item) {
  const state = DIAGNOSTIC_STATES[item.state] ? item.state : 'warning';
  const advice = item.advice ? `<span class="diagnostic-advice">${escapeHtml(item.advice)}</span>` : '';
  const section = /^settings-[a-z]+$/.test(item.section || '') ? item.section : '';
  const open = section && state !== 'ok' ? `<button type="button" class="btn btn-secondary" onclick="openSettingsSection('${section}')">Ouvrir</button>` : '';
  return `<li class="diagnostic-item is-${state}"><span class="diagnostic-mark" aria-hidden="true">${DIAGNOSTIC_MARKS[state]}</span>`
    + `<div><strong>${escapeHtml(item.label)}<span class="diagnostic-state">${DIAGNOSTIC_STATES[state]}</span></strong>`
    + `<span>${escapeHtml(item.summary)}</span>${advice}</div>${open}</li>`;
}

function openSettingsSection(id) {
  const link = document.querySelector(`.settings-index-item[data-settings-target="${id}"]`);
  if (link) link.click();
  else document.getElementById(id)?.scrollIntoView({ block: 'start', behavior: 'auto' });
}

async function runDiagnostic() {
  const button = document.getElementById('diagnosticRunBtn');
  const summary = document.getElementById('diagnosticSummary');
  const list = document.getElementById('diagnosticList');
  const copy = document.getElementById('diagnosticCopyBtn');
  const report = document.getElementById('diagnosticReport');
  if (button) { button.disabled = true; button.textContent = 'Vérification…'; }
  if (summary) summary.textContent = 'Vérification en cours…';
  document.getElementById('diagnosticCopyStatus').textContent = '';
  try {
    const result = await api('/api/diagnostic');
    const items = Array.isArray(result.items) ? result.items : [];
    diagnosticReport = String(result.report || '');
    list.innerHTML = items.map(diagnosticItemHtml).join('');
    list.hidden = !items.length;
    summary.textContent = diagnosticSummaryText(items);
    if (report) { report.value = diagnosticReport; report.hidden = true; }
    if (copy) copy.disabled = !diagnosticReport;
  } catch (error) {
    // An older report would describe another moment: nothing to copy.
    diagnosticReport = '';
    if (copy) copy.disabled = true;
    if (list) list.hidden = true;
    if (summary) summary.textContent = `Diagnostic impossible : ${error.message}`;
  } finally {
    if (button) { button.disabled = false; button.textContent = 'Relancer le diagnostic'; }
  }
}

// The report holds no key, link, e-mail or account name: it can be sent as is.
async function copyDiagnosticReport() {
  if (!diagnosticReport) return;
  const status = document.getElementById('diagnosticCopyStatus');
  try {
    await navigator.clipboard.writeText(diagnosticReport);
    status.textContent = 'Rapport copié.';
  } catch (error) {
    // No clipboard here: the report is shown selected, ready for ⌘C.
    const box = document.getElementById('diagnosticReport');
    box.value = diagnosticReport;
    box.hidden = false;
    box.focus();
    box.select();
    status.textContent = 'Le rapport est sélectionné : appuyez sur ⌘C pour le copier.';
  }
}

// From the « Outils » menu: Paramètres, at the diagnostic, already running.
function openDiagnostic() {
  openConfig();
  requestAnimationFrame(() => openSettingsSection('settings-diagnostic'));
  runDiagnostic();
}

// --- Notifications of new episodes (macOS) ---
const NOTIFICATION_STATUS = {
  authorized: 'Notification envoyée : elle doit apparaître en haut à droite de l’écran.',
  denied: 'macOS bloque les notifications de KINO : autorisez-les dans Réglages Système → Notifications → KINO.',
  not_determined: 'macOS n’a pas encore reçu de réponse : acceptez la demande d’autorisation de KINO.',
  unavailable: 'Les notifications ne sont disponibles que dans l’application KINO pour Mac.',
};

async function testEpisodeNotification(button) {
  const status = document.getElementById('cfgNotificationStatus');
  if (button) button.disabled = true;
  if (status) status.textContent = 'Envoi…';
  try {
    const result = await api('/api/notifications/test', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
    if (status) status.textContent = NOTIFICATION_STATUS[result.status] || 'Réponse inattendue de macOS.';
  } catch (error) {
    if (status) status.textContent = 'Essai impossible : ' + error.message;
  } finally {
    if (button) button.disabled = false;
  }
}

// A notification was clicked (desktop.py): the series opens.
async function openFromNotification(info) {
  if (!info || !/^tt\d{1,12}$/.test(String(info.id || ''))) return;
  if (activeTab !== 'series') await switchTab('series');
  await selectMedia({ id: info.id, name: info.name || '', type: 'series' });
}

// From the setup notice: straight to the key field.
function focusDebridKey() {
  const card = document.getElementById('settings-account');
  if (card) card.scrollIntoView({ block: 'start', behavior: 'auto' });
  document.getElementById('cfgToken')?.focus({ preventScroll: true });
}

function closeConfig() {
  switchTab(previousTab || 'movies');
}

function togglePasswordVisibility(inputId, btn) {
  const inp = document.getElementById(inputId);
  if (!inp) return;
  if (inp.type === 'password') {
    inp.type = 'text';
    if (btn) btn.style.color = 'var(--text)';
  } else {
    inp.type = 'password';
    if (btn) btn.style.color = 'var(--muted)';
  }
}

async function triggerGdriveSync(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'En cours...'; }
  try {
    const res = await api('/api/sync/gdrive');
    const el = document.getElementById('gdriveSyncStatus');
    if (res && res.status === 'synced') {
      const d = new Date((res.updated_at || Date.now()/1000) * 1000);
      const timeStr = d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
      if (el) el.innerHTML = `<span style="color:#4ade80;">✓ Synchronisé (${res.history_count || 0} films, ${res.watchlist_count || 0} favoris) à ${timeStr}</span>`;
      await refreshUserLists();
    } else {
      if (el) el.innerHTML = `<span style="color:var(--muted);">${escapeHtml((res && res.message) ? res.message : 'Google Drive non détecté')}</span>`;
    }
  } catch(e) {
    const el = document.getElementById('gdriveSyncStatus');
    if (el) el.innerHTML = `<span style="color:#ef4444;">Erreur : ${escapeHtml(e.message)}</span>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Synchroniser'; }
  }
}

// Settings → Application → Suggestions (recommender.py): the pool and the « Pas pour moi ».
async function loadSuggestionStatus() {
  const el = document.getElementById('suggestionsStatus');
  if (!el) return;
  try {
    const res = await api('/api/for-you/status');
    const pool = Number(res.pool || 0);
    const when = res.updated_at ? new Date(res.updated_at * 1000).toLocaleDateString((window.KINO_LOCALE || 'fr-FR'), { day: 'numeric', month: 'long' }) : '';
    const removed = Number(res.not_for_me || 0);
    el.textContent = pool >= 500
      ? `Réserve de ${pool.toLocaleString((window.KINO_LOCALE || 'fr-FR'))} titres publics, mise à jour le ${when}.` + (removed ? ` ${removed} titre${removed > 1 ? 's' : ''} écarté${removed > 1 ? 's' : ''}.` : '')
      : (res.building ? 'Préparation de la réserve de titres…' : 'La réserve de titres se prépare au prochain lancement, avec internet.');
  } catch (e) {
    el.textContent = '';
  }
}

async function resetNotForMe(btn) {
  if (btn) btn.disabled = true;
  try {
    await api('/api/for-you/reset', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
    if (typeof FOR_YOU_CACHE !== 'undefined') FOR_YOU_CACHE.clear();
    RELATED_CLIENT_CACHE.clear();
    showSettingsStatus('Enregistré', 'saved');
    loadSuggestionStatus();
  } catch (e) {
    showSettingsStatus(`Pas enregistré : ${e.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// --- Sauvegardes (data_backup.py): a copy a day, « Restaurer ».
function backupWhen(seconds, now = Date.now()) {
  const date = new Date(seconds * 1000);
  const hour = date.toLocaleTimeString((window.KINO_LOCALE || 'fr-FR'), { hour: '2-digit', minute: '2-digit' });
  const day = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(new Date(now)) - day(date)) / 86400000);
  if (days === 0) return `Aujourd’hui, ${hour}`;
  if (days === 1) return `Hier, ${hour}`;
  return `${date.toLocaleDateString((window.KINO_LOCALE || 'fr-FR'), { weekday: 'long', day: 'numeric', month: 'long' })}, ${hour}`;
}

function backupCounts(counts = {}) {
  const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;
  return [
    `${Number(counts.watchlist || 0)} dans Ma Liste`,
    plural(Number(counts.seen || 0), 'vu', 'vus'),
    plural(Number(counts.in_progress || 0), 'en cours', 'en cours'),
    plural(Number(counts.rated || 0), 'noté', 'notés'),
  ].join(' · ');
}

function renderBackups(items = []) {
  const list = document.getElementById('backupsList');
  const status = document.getElementById('backupsStatus');
  if (status) status.textContent = items.length
    ? `Dernière copie : ${backupWhen(items[0].created_at).replace(/^./, c => c.toLowerCase())}.`
    : 'Première copie quelques minutes après le lancement de KINO.';
  if (!list) return;
  list.innerHTML = items.map(item => {
    const when = backupWhen(item.created_at);
    const label = item.kind === 'before_restore' ? `Avant restauration · ${when}` : when;
    return `<div class="set-row backup-row">
      <div class="set-text"><span class="settings-label">${escapeHtml(label)}</span><p class="settings-desc">${escapeHtml(backupCounts(item.counts))}</p></div>
      <button type="button" class="btn btn-secondary btn-small" data-backup="${escapeHtml(item.id)}" data-label="${escapeHtml(label)}"
        onclick="restoreBackup(this)"${item.restorable ? '' : ' disabled title="Sauvegarde d’une version trop différente de KINO"'}>Restaurer</button>
    </div>`;
  }).join('');
}

async function loadBackups() {
  try {
    const res = await api('/api/backups');
    renderBackups(res.items || []);
  } catch (e) {
    const status = document.getElementById('backupsStatus');
    if (status) status.textContent = '';
  }
}

async function backupNow(btn) {
  if (btn) btn.disabled = true;
  try {
    const res = await api('/api/backups/now', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
    renderBackups(res.items || []);
    showSettingsStatus('Sauvegardé', 'saved');
  } catch (e) {
    showSettingsStatus(`Pas sauvegardé : ${e.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function restoreBackup(btn) {
  const id = btn?.dataset?.backup;
  if (!id) return;
  if (!window.confirm(`Restaurer la copie « ${btn.dataset.label || id} » ?\n\nMa Liste, l’historique, vos notes et vos réglages reprendront leur état de ce moment. Vos clés restent celles d’aujourd’hui, et l’état actuel est d’abord gardé dans « Avant restauration ».`)) return;
  btn.disabled = true;
  try {
    await api('/api/backups/restore', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id }) });
    const status = document.getElementById('backupsStatus');
    if (status) status.textContent = 'Restauré. KINO se recharge…';
    setTimeout(() => window.location.reload(), 900);
  } catch (e) {
    showSettingsStatus(`Pas restauré : ${e.message}`, 'error');
    btn.disabled = false;
  }
}
// --- fin des sauvegardes

// Settings → Connexions → TMDB: the user's key for titles close to a film (tmdb.py).
function showTmdbStatus(hasKey, message) {
  const status = document.getElementById('cfgTmdbStatus');
  if (status) status.textContent = message || (hasKey
    ? 'Clé enregistrée : les titres proches viennent de TMDB.'
    : 'Pas de clé : KINO propose des titres du même genre.');
}

async function verifyTmdbKey(btn) {
  const input = document.getElementById('cfgTmdbKey');
  const key = (input?.value || '').trim();
  if (!key) {
    showTmdbStatus(false, 'Collez d’abord votre clé d’API TMDB.');
    input?.focus();
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Vérification…'; }
  try {
    await api('/api/tmdb/key', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ key }) });
    if (input) input.value = '';
    RELATED_CLIENT_CACHE.clear();
    showTmdbStatus(true);
    showSettingsStatus('Clé enregistrée', 'saved');
  } catch (e) {
    showTmdbStatus(false, e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Vérifier'; }
  }
}

// Settings → Connexions → Téléviseur: the TVs of the local network (tv_cast.py).
async function loadTvDevices(refresh = false, btn = null) {
  const select = document.getElementById('cfgTvDevice');
  const status = document.getElementById('cfgTvStatus');
  if (!select) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Recherche…'; }
  try {
    const res = await api(`/api/tv/devices${refresh ? '?refresh=1' : ''}`);
    const devices = res.devices || [];
    const chosen = (res.chosen && res.chosen.location) || '';
    select.innerHTML = devices.length
      ? devices.map(d => `<option value="${escapeHtml(d.location)}">${escapeHtml(d.name)}</option>`).join('')
      : '<option value="">Aucune TV trouvée</option>';
    if (chosen && devices.some(d => d.location === chosen)) select.value = chosen;
    if (status) status.textContent = devices.length
      ? 'Le Mac envoie la vidéo à cette TV : il doit rester allumé pendant le film.'
      : 'Aucune TV trouvée. Allumez-la, sur la même box que ce Mac, puis Rechercher.';
  } catch (e) {
    if (status) status.textContent = e.message;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Rechercher'; }
  }
}

async function chooseTvDevice(location) {
  if (!location) return;
  try {
    await api('/api/tv/device', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ location }) });
    showSettingsStatus('Enregistré', 'saved');
  } catch (e) {
    showSettingsStatus(`Pas enregistré : ${e.message}`, 'error');
  }
}

function toggleDiscordRpcFields() {
  const chk = document.getElementById('cfgDiscordRpc');
  const opt = document.getElementById('discordRpcOptions');
  if (opt) opt.hidden = !(chk && chk.checked);
}

function onDiscordPresetChange() {
  const p = document.getElementById('cfgDiscordPreset');
  const box = document.getElementById('cfgDiscordCustomBox');
  if (box && p) {
    box.style.display = (p.value === 'custom') ? 'flex' : 'none';
  }
}

// Settings apply as soon as they change. Every control below saves
// on « change »; the Debrid key waits for « Vérifier », and a service without a
// saved key only becomes the active one with its key.
const SETTINGS_AUTOSAVE_IDS = ['cfgPrefLang', 'cfgPrefQuality', 'cfgPlayerMode', 'cfgNativePlayer', 'cfgAutoFrSubs',
  'cfgEpisodeNotifications', 'cfgSourcePriority', 'cfgHdrMode', 'cfgAudioMode', 'cfgProvider', 'cfgRdRetention',
  'cfgDiscordRpc', 'cfgDiscordPreset', 'cfgDiscordClientId', 'cfgDir'];
let settingsSaveTimer = null;
let settingsSaveChain = Promise.resolve();
let settingsStatusTimer = null;

function showSettingsStatus(text, state) {
  const el = document.getElementById('settingsSaveStatus');
  if (!el) return;
  clearTimeout(settingsStatusTimer);
  el.textContent = text;
  el.dataset.state = state || '';
  if (state === 'saved') {
    settingsStatusTimer = setTimeout(() => { el.textContent = ''; el.dataset.state = ''; }, 2200);
  }
}

function initSettingsAutosave() {
  for (const id of SETTINGS_AUTOSAVE_IDS) {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', scheduleSettingsSave);
  }
  const token = document.getElementById('cfgToken');
  if (token) token.addEventListener('keydown', event => { if (event.key === 'Enter') verifyDebridKey(document.getElementById('cfgTokenVerifyBtn')); });
}

function scheduleSettingsSave() {
  clearTimeout(settingsSaveTimer);
  showSettingsStatus('Enregistrement…', 'saving');
  settingsSaveTimer = setTimeout(() => { settingsSaveTimer = null; saveConfig().catch(() => {}); }, 250);
}

// From the key field: save the key (and its service), then show what the service says.
async function verifyDebridKey(btn) {
  const token = document.getElementById('cfgToken')?.value.trim() || '';
  const provider = document.getElementById('cfgProvider')?.value || 'realdebrid';
  if (!token && !(configuredProviders && configuredProviders[provider])) {
    showSettingsStatus('Collez d’abord votre clé.', 'error');
    document.getElementById('cfgToken')?.focus();
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Vérification…'; }
  try {
    await saveConfig({ withKey: true });
  } catch (e) {
    // The status line already says why.
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Vérifier'; }
  }
}

function settingsPayload(withKey) {
  const value = (id, fallback) => (document.getElementById(id) ? document.getElementById(id).value : fallback);
  const checked = (id, fallback) => (document.getElementById(id) ? document.getElementById(id).checked : fallback);
  const isMacPlatform = /Mac/i.test(navigator.platform || navigator.userAgent);
  const debrid_provider = value('cfgProvider', 'realdebrid');
  const nativeToggle = document.getElementById('cfgNativePlayer');
  const payload = {
    download_dir: value('cfgDir', '').trim(),
    player_mode: value('cfgPlayerMode', window.kinoPlayerMode || (isMacPlatform ? 'integrated' : 'kino')),
    pref_lang: value('cfgPrefLang', 'vf'),
    pref_quality: value('cfgPrefQuality', '4k'),
    source_priority: value('cfgSourcePriority', '') === 'quality' ? 'quality' : 'speed',
    hdr_mode: value('cfgHdrMode', 'sdr_pref'),
    audio_mode: value('cfgAudioMode', 'voice_boost'),
    rd_retention_days: parseInt(value('cfgRdRetention', '0') || '0', 10),
    discord_rpc: checked('cfgDiscordRpc', true),
    auto_fr_subs: checked('cfgAutoFrSubs', true),
    episode_notifications: checked('cfgEpisodeNotifications', true),
    native_player: nativeToggle && document.getElementById('cfgNativePlayerField')?.style.display !== 'none'
      ? nativeToggle.checked : undefined,
  };
  let discord_client_id = '1556492442415398973';
  const presetEl = document.getElementById('cfgDiscordPreset');
  if (presetEl) {
    discord_client_id = presetEl.value === 'custom'
      ? (value('cfgDiscordClientId', '').trim() || '1556492442415398973')
      : presetEl.value;
  }
  payload.discord_client_id = discord_client_id;
  const keyReady = Boolean(configuredProviders && configuredProviders[debrid_provider]) || debrid_provider === window.activeDebridProvider;
  if (withKey) {
    payload.debrid_provider = debrid_provider;
    payload.rd_token = value('cfgToken', '').trim();
  } else if (keyReady) {
    payload.debrid_provider = debrid_provider;
  }
  return payload;
}

// Saves the whole form (each field is checked again by KINO). Changes made while a
// save is running wait for it, so the last choice always wins.
function saveConfig(options = {}) {
  const run = settingsSaveChain.then(() => saveConfigNow(Boolean(options.withKey)));
  settingsSaveChain = run.catch(() => {});
  return run;
}

async function saveConfigNow(withKey) {
  const payload = settingsPayload(withKey);
  const provider = document.getElementById('cfgProvider')?.value || 'realdebrid';
  const providerWaits = !withKey && !('debrid_provider' in payload);
  showSettingsStatus('Enregistrement…', 'saving');
  try {
    await api('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    window.kinoPlayerMode = payload.player_mode;
    window.kinoHdrMode = payload.hdr_mode;
    window.kinoAudioMode = payload.audio_mode;
    if (withKey && document.getElementById('cfgToken')) document.getElementById('cfgToken').value = '';
    if (!settingsSaveTimer) await checkConfig();
    if (providerWaits && document.getElementById('cfgProvider')) {
      // checkConfig put back the active service: keep the one being set up on screen.
      document.getElementById('cfgProvider').value = provider;
      onConfigProviderChange();
      const meta = PROVIDER_META[provider] || PROVIDER_META.realdebrid;
      showSettingsStatus(`Collez votre clé ${meta.name}, puis Vérifier.`, 'info');
    } else {
      showSettingsStatus(withKey ? 'Clé enregistrée' : 'Enregistré', 'saved');
    }
    if (activeTab === 'rdcloud') loadRdCloud();
  } catch (e) {
    showSettingsStatus(`Pas enregistré : ${e.message}`, 'error');
    throw e;
  }
}

async function openFolder() {
  await api('/api/open-folder', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
}

