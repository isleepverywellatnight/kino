// Player troubleshooting is local; reports contain only explicitly selected fields.
function playbackHelpStatus(message) {
  document.getElementById('playbackHelpStatus').textContent = message;
}

function openPlaybackHelp() {
  const dialog = document.getElementById('playbackHelp');
  if (!dialog.open) dialog.showModal();
  document.getElementById('playbackHelpRetry').disabled = !inAppRecovery;
  showPlaybackHelp('no_audio');
}

function closePlaybackHelp() {
  document.getElementById('playbackHelp')?.close();
  resetInAppIdleTimer();
}

function showPlaybackHelp(kind) {
  const host = document.getElementById('playbackHelpActions');
  host.replaceChildren();
  playbackHelpStatus('');
  document.querySelectorAll('[data-help-kind]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.helpKind === kind));
  });
  const explain = text => { const p = document.createElement('p'); p.textContent = text; host.append(p); };
  const action = (label, callback, disabled = false) => {
    const button = document.createElement('button');
    button.className = 'inapp-btn'; button.textContent = label;
    button.disabled = disabled; button.onclick = callback; host.append(button);
  };
  const alternate = reason => action('Essayer une autre source', () => replacePlaybackSource(reason), !inAppRecovery);
  if (kind === 'no_audio') {
    explain(inAppAudioTracks.length ? 'Vérifiez le volume, puis la piste audio. La conversion AAC peut rendre la piste compatible avec le lecteur.' : 'Aucune piste audio détectée. Si le fichier est muet, la conversion ne peut pas créer de son : essayez une autre source.');
    action('Réactiver le son', () => {
      setInAppVolume(0.7, true);
      playbackHelpStatus('Son réactivé à 70 %. Vérifiez aussi le volume et la sortie audio de macOS.');
    });
    if (inAppAudioTracks.length) {
      const label = document.createElement('label'); label.textContent = 'Piste audio ';
      const select = document.createElement('select'); select.className = 'inapp-btn';
      inAppAudioTracks.forEach((track, index) => {
        const option = document.createElement('option'); option.value = track.index;
        option.textContent = `${track.language || 'Piste ' + (index + 1)} · ${track.codec || 'codec inconnu'}`;
        option.selected = track.index === inAppSelectedAudioIndex; select.append(option);
      });
      select.onchange = () => { changeInAppAudioTrack(select.value); playbackHelpStatus('Changement de piste demandé à la même position.'); };
      label.append(select); host.append(label);
    }
    action('Convertir en AAC', () => { fixInAppAudio(); playbackHelpStatus('Conversion demandée. Vérifiez si le son est maintenant audible.'); }, !inAppAudioTracks.length);
    alternate('no_audio');
  } else if (kind === 'wrong_content') {
    explain('Cette source sera écartée pour ce film ou cet épisode, même après redémarrage. La source suivante commencera au début. Vous pourrez annuler cette exclusion.');
    alternate('wrong_content');
  } else if (kind === 'stalled') {
    explain('Une autre source reprendra à votre position actuelle. La source bloquée sera écartée pendant une heure.');
    alternate('stalled');
  } else {
    explain(inAppActiveCues.length ? 'Une valeur positive affiche les sous-titres plus tard ; une valeur négative les avance.' : 'Activez d’abord une piste avec le bouton CC du lecteur.');
    const change = delta => { adjustInAppSubDelay(delta); playbackHelpStatus(`Décalage : ${inAppSubDelaySec >= 0 ? '+' : ''}${inAppSubDelaySec} s`); };
    action('Plus tôt (−0,5 s)', () => change(-0.5), !inAppActiveCues.length);
    action('Plus tard (+0,5 s)', () => change(0.5), !inAppActiveCues.length);
    action('Remettre à zéro', () => change(-inAppSubDelaySec), !inAppActiveCues.length);
  }
  if (!inAppRecovery && kind !== 'subtitles') explain('Pour changer de source, revenez à la fiche et utilisez « Lecture en un clic ».');
}

let playbackHelpBusy = false;
async function replacePlaybackSource(reason) {
  const recovery = inAppRecovery;
  const generation = inAppPlaybackGeneration;
  if (!recovery || recovery.pending || playbackHelpBusy) return;
  if (recovery.attempts >= 5) { playbackHelpStatus('Cinq essais effectués. Réessayez les sources écartées ou revenez à la fiche.'); return; }
  playbackHelpBusy = true;
  recovery.pending = true;
  playbackHelpStatus('Enregistrement du problème…');
  try {
    await api('/api/playback-feedback', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({context: recovery.params, source_key: recovery.sourceKey, reason})});
    recovery.pending = false;
    if (recovery !== inAppRecovery || generation !== inAppPlaybackGeneration) return;
    recovery.feedbackRecorded = true;
    recovery.resetPosition = reason === 'wrong_content';
    if (recovery.resetPosition) document.getElementById('inAppVideo').pause();
    closePlaybackHelp();
    await recoverInAppSource(generation);
  } catch (_) {
    recovery.pending = false;
    playbackHelpStatus('Impossible d’enregistrer ce problème. La source actuelle est conservée.');
  } finally { playbackHelpBusy = false; }
}

async function retryPlaybackSources() {
  const recovery = inAppRecovery;
  const generation = inAppPlaybackGeneration;
  if (!recovery || recovery.pending || playbackHelpBusy) return;
  playbackHelpBusy = true;
  recovery.pending = true;
  try {
    await api('/api/playback-feedback', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({context: recovery.params, action: 'clear'})});
    recovery.pending = false;
    if (recovery !== inAppRecovery || generation !== inAppPlaybackGeneration) return;
    recovery.excluded.clear(); recovery.attempts = 0; recovery.feedbackRecorded = true;
    closePlaybackHelp();
    await recoverInAppSource(generation);
  } catch (_) { recovery.pending = false; playbackHelpStatus('Impossible de réessayer pour le moment.'); }
  finally { playbackHelpBusy = false; }
}

function playbackDiagnostic(video) {
  const number = value => Number.isFinite(value) ? Math.round(value * 100) / 100 : null;
  // Codec/language strings are deliberately omitted: even media metadata can contain URLs or secrets.
  return { schema: 1, player: 'KINO internal',
    position: number(inAppAbsoluteTime(video)), duration: number(inAppDuration(video)),
    paused: Boolean(video.paused), muted: Boolean(video.muted), volume: number(video.volume),
    readyState: number(video.readyState), networkState: number(video.networkState),
    mediaError: number(video.error?.code), compatibleStream: Boolean(inAppIsRemux),
    audioTrackCount: inAppAudioTracks.length, selectedAudioIndex: number(inAppSelectedAudioIndex),
    subtitleCueCount: inAppActiveCues.length, subtitleDelay: number(inAppSubDelaySec),
    sourceAttempts: inAppRecovery?.attempts || 0 };
}

async function exportPlaybackDiagnostic() {
  const payload = playbackDiagnostic(document.getElementById('inAppVideo'));
  try {
    if (window.pywebview?.api?.export_playback_diagnostic) {
      const saved = await window.pywebview.api.export_playback_diagnostic(payload);
      playbackHelpStatus(saved ? 'Diagnostic enregistré.' : 'Export annulé.');
    } else {
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}));
      const link = document.createElement('a'); link.href = url; link.download = 'KINO-diagnostic.json';
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      playbackHelpStatus('Diagnostic préparé pour le téléchargement.');
    }
  } catch (_) { playbackHelpStatus('Impossible d’exporter le diagnostic.'); }
}
