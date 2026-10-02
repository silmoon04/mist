import { createFaceState, requestPreset, sampleFace, stopFace, EYE_FRAMES, MOUTH_FRAMES } from './state.mjs';
import { AtlasRenderer, SharedClock, SpeechPlayer, fetchJson, createFrameLoop } from './player.mjs';

const $ = id => document.getElementById(id);
const clock = new SharedClock();
const audio = new SpeechPlayer();
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const storageKey = 'mist-geraldine-animation-review-20261002';
let storageAvailable = true;
let records = {};
try { records = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch (_) { storageAvailable = false; }
if (!records || Array.isArray(records) || typeof records !== 'object') records = {};
const view = { manifest: null, renderer: null, clips: [], machines: new Map(), cards: new Map(), selectedId: null, inspector: null, ready: false, loading: false, scrubFrame: null, tourAt: null, tourIndex: 0, stopLoop: null, pendingSpeech: false, stopped: false };

function node(tag, className, text) { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; }
function record(id) { const value = records[id]; return { verdict: ['keep', 'revise'].includes(value?.verdict) ? value.verdict : '', note: typeof value?.note === 'string' ? value.note : '' }; }
function save(id, patch) {
  records[id] = { ...record(id), ...patch, updatedAt: new Date().toISOString() };
  try { localStorage.setItem(storageKey, JSON.stringify(records)); } catch (_) { storageAvailable = false; }
  refreshReview();
}
function reviewButtons(id) {
  const wrap = node('div', 'verdict');
  for (const verdict of ['keep', 'revise']) {
    const button = node('button', '', verdict === 'keep' ? 'Keep' : 'Revise'); button.type = 'button'; button.dataset.verdict = verdict; button.dataset.faceId = id;
    button.setAttribute('aria-label', `${verdict === 'keep' ? 'Keep' : 'Revise'} ${view.manifest.presets.find(preset => preset.id === id).name}`);
    wrap.append(button);
  }
  return wrap;
}
function refreshReview() {
  for (const button of document.querySelectorAll('[data-verdict]')) {
    const id = button.dataset.faceId || view.selectedId;
    button.setAttribute('aria-pressed', String(record(id).verdict === button.dataset.verdict));
  }
  for (const [id, card] of view.cards) card.note.textContent = record(id).note.trim() ? 'Note' : '';
  $('note-status').textContent = storageAvailable ? 'Saved in this browser.' : 'Storage unavailable. Export notes before closing.';
}
function setControls(ready) {
  for (const id of ['mode', 'play', 'stop', 'loop-all', 'tour']) $(id).disabled = !ready;
  $('play').disabled = !ready || view.pendingSpeech;
  $('phrase').disabled = !ready || !view.clips.length;
  $('speak').disabled = !ready || !view.clips.length || view.pendingSpeech;
}
function showError(error) { $('error-message').textContent = error.message || String(error); $('error').hidden = false; }
function speechFor(id) { return $('loop-all').checked || id === view.selectedId ? audio.snapshot() : null; }
function faceOptions(id) {
  return { mode: $('mode').value, speech: speechFor(id), quiet: view.stopped || (reducedMotion.matches && !clock.explicit), scrubFrame: view.scrubFrame };
}
function currentSpeech() { return audio.snapshot(); }
function select(id, { tour = false } = {}) {
  if (!view.ready || !view.machines.has(id)) return;
  if (!tour) view.tourAt = null;
  requestPreset(view.inspector, id, clock.time(), currentSpeech(), { mode: $('mode').value, quiet: view.stopped, bridgeFamily: view.manifest.closedBridge?.family || 'attentive' });
  view.selectedId = id;
  const preset = view.manifest.presets.find(item => item.id === id);
  $('selected-title').textContent = preset.name;
  $('selected-canvas').setAttribute('aria-label', `${preset.name}, animated MIST face`);
  $('note').value = record(id).note;
  for (const [key, card] of view.cards) card.button.setAttribute('aria-pressed', String(key === id));
  refreshReview(); render();
}
function createCards() {
  view.cards.clear(); $('faces').replaceChildren();
  for (const preset of view.manifest.presets) {
    const article = node('article', 'face-card'); article.id = preset.id;
    const button = node('button', 'face-button'); button.type = 'button'; button.dataset.faceId = preset.id; button.dataset.action = 'inspect';
    button.setAttribute('aria-label', `Inspect ${preset.name}`); button.setAttribute('aria-pressed', 'false');
    const canvas = node('canvas'); canvas.width = 400; canvas.height = 256; canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', `${preset.name}, animated MIST face`); button.append(canvas);
    const meta = node('div', 'card-meta'); const name = node('span', 'card-name', preset.name); const note = node('span', 'note-mark'); name.append(note);
    meta.append(name, reviewButtons(preset.id)); article.append(button, meta); $('faces').append(article);
    view.cards.set(preset.id, { button, canvas, note });
  }
}
function render() {
  if (!view.ready) return;
  $('motion-note').hidden = !reducedMotion.matches || clock.explicit;
  const nowMs = clock.time();
  for (const [id, machine] of view.machines) view.renderer.draw(view.cards.get(id).canvas, sampleFace(machine, nowMs, faceOptions(id)));
  const pose = sampleFace(view.inspector, nowMs, { ...faceOptions(view.selectedId), speech: currentSpeech() });
  view.renderer.draw($('selected-canvas'), pose);
  $('frame-detail').textContent = `${pose.eyeFrame} / ${pose.mouthFrame}`;
  $('play').textContent = clock.running ? 'Pause' : 'Play';
  $('tour').textContent = view.tourAt === null ? 'Transition tour' : 'End tour';
  $('release-scrub').hidden = view.scrubFrame === null;
  $('frame-value').textContent = `${(view.scrubFrame ?? Math.max(0, EYE_FRAMES.indexOf(pose.eyeFrame))) + 1} / 12`;
  if (view.scrubFrame === null) $('frame').value = String(Math.max(0, EYE_FRAMES.indexOf(pose.eyeFrame)));
  let status = view.scrubFrame !== null ? `Paused on cel ${view.scrubFrame + 1} of 12.` : view.tourAt !== null ? `Transition tour: ${view.tourIndex + 1} / 13.` : audio.active ? `${audio.playing ? 'Speaking' : 'Speech paused'}: ${audio.clip?.title || audio.clip?.id}.` : clock.running ? `${$('mode').selectedOptions[0].textContent} playing at 15 fps.` : 'Paused.';
  if ($('status').textContent !== status) $('status').textContent = status;
}
function pause() { audio.pause(); clock.pause(); render(); }
function stop() {
  audio.stop(); clock.pause(); clock.reset(); view.scrubFrame = null; view.tourAt = null; view.stopped = true;
  for (const machine of view.machines.values()) stopFace(machine, 0);
  if (view.inspector) stopFace(view.inspector, 0);
  render();
}
async function speak({ repeat = false } = {}) {
  if (!view.ready || view.pendingSpeech || !view.clips.length) return;
  const clip = view.clips.find(item => item.id === $('phrase').value) || view.clips[0];
  if (!repeat && audio.active && audio.clip?.id === clip.id && audio.playing) { clock.play(); render(); return; }
  view.pendingSpeech = true; setControls(true); $('error').hidden = true;
  $('status').textContent = 'Loading speech audio…';
  try {
    const resume = !repeat && audio.active && !audio.playing && audio.clip?.id === clip.id;
    const started = resume ? await audio.resume() : await audio.play(clip);
    if (started) { $('mode').value = 'speech'; view.scrubFrame = null; view.stopped = false; clock.play(); }
  } catch (error) { showError(error); audio.stop(); }
  finally { view.pendingSpeech = false; setControls(view.ready); render(); }
}
async function play() {
  if (!view.ready) return;
  if (clock.running) { pause(); return; }
  view.scrubFrame = null; view.stopped = false;
  if ($('mode').value === 'speech') await speak();
  else { clock.play(); render(); }
}
function tick() {
  if (!view.ready) return;
  const nowMs = clock.time();
  if (view.tourAt !== null && clock.running && nowMs - view.tourAt >= 1200) {
    view.tourAt += 1200; view.tourIndex++;
    if (view.tourIndex >= view.manifest.presets.length) { view.tourAt = null; view.tourIndex = 0; }
    else select(view.manifest.presets[view.tourIndex].id, { tour: true });
  }
  if (audio.finished()) {
    audio.stop();
    if ($('loop-all').checked) speak({ repeat: true });
    else {
      clock.pause(); view.stopped = true;
      for (const machine of view.machines.values()) stopFace(machine, nowMs);
      stopFace(view.inspector, nowMs);
    }
  }
  render();
}
async function load() {
  if (view.loading) return;
  view.loading = true; view.ready = false; setControls(false); $('error').hidden = true; $('status').textContent = 'Loading pack…';
  audio.stop(); view.stopLoop?.(); $('inspector').hidden = true; $('faces').replaceChildren();
  try {
    const [manifestResult, speechResult] = await Promise.allSettled([fetchJson('asset-manifest.json'), fetchJson('speech-clips.json')]);
    if (manifestResult.status === 'rejected') throw manifestResult.reason;
    view.manifest = manifestResult.value; view.renderer = new AtlasRenderer(view.manifest);
    await view.renderer.load((loaded, total) => { $('status').textContent = `Loading native sheets: ${loaded} / ${total}.`; });
    view.clips = speechResult.status === 'fulfilled' && Array.isArray(speechResult.value.clips) ? speechResult.value.clips : [];
    if (speechResult.status === 'rejected') showError(speechResult.reason);
    else if (!view.clips.length) showError(new Error('speech-clips.json has no recorded clips. Restore the three speech samples, then retry.'));
    $('phrase').replaceChildren(...view.clips.map(clip => { const option = node('option', '', clip.title || clip.id); option.value = clip.id; return option; }));
    if (!view.clips.length) { const option = node('option', '', 'Speech unavailable'); $('phrase').append(option); }
    $('speech-limit').textContent = speechResult.status === 'fulfilled' ? (speechResult.value.limitations || view.clips[0]?.limitations || '') : '';
    if (view.manifest.download) $('download-pack').href = view.manifest.download;
    const links = $('source-links'); links.replaceChildren();
    for (const [label, href] of [['Asset manifest', 'asset-manifest.json'], ['Speech timings', 'speech-clips.json'], ['Model tool', 'face-tool.json'], ['Integration notes', 'README.md']]) { const anchor = node('a', '', label); anchor.href = href; anchor.target = '_blank'; anchor.rel = 'noopener'; links.append(anchor); }
    view.machines = new Map(view.manifest.presets.map(preset => [preset.id, createFaceState(view.manifest.presets, preset.id)]));
    view.selectedId = view.manifest.presets[0].id; view.inspector = createFaceState(view.manifest.presets, view.selectedId);
    clock.pause(); clock.reset(); clock.explicit = false; view.scrubFrame = null; view.stopped = false; view.tourAt = null; view.tourIndex = 0;
    if (!reducedMotion.matches) { clock.started = performance.now(); clock.running = true; }
    view.ready = true; createCards(); $('inspector').hidden = false; setControls(true);
    select(view.selectedId); $('motion-note').hidden = !reducedMotion.matches;
    view.stopLoop = createFrameLoop(tick);
  } catch (error) { showError(error); $('status').textContent = 'Pack unavailable.'; setControls(false); }
  finally { view.loading = false; }
}

$('play').addEventListener('click', play);
$('stop').addEventListener('click', stop);
$('speak').addEventListener('click', () => speak());
$('mode').addEventListener('change', () => {
  audio.stop(); view.tourAt = null; view.scrubFrame = null; view.stopped = false; clock.reset();
  for (const machine of view.machines.values()) stopFace(machine, 0);
  stopFace(view.inspector, 0);
  if ($('mode').value === 'speech') clock.pause();
  render();
});
$('loop-all').addEventListener('change', render);
$('tour').addEventListener('click', () => {
  if (view.tourAt !== null) { view.tourAt = null; render(); return; }
  view.scrubFrame = null; view.stopped = false; view.tourIndex = 0; view.tourAt = clock.time(); select(view.manifest.presets[0].id, { tour: true }); clock.play(); render();
});
$('frame').addEventListener('input', () => { pause(); view.tourAt = null; view.scrubFrame = Number($('frame').value); render(); });
$('release-scrub').addEventListener('click', () => { view.scrubFrame = null; render(); });
$('note').addEventListener('input', () => { if (view.selectedId) save(view.selectedId, { note: $('note').value }); });
$('selected-verdict').addEventListener('click', event => { const button = event.target.closest('[data-verdict]'); if (button && view.selectedId) save(view.selectedId, { verdict: record(view.selectedId).verdict === button.dataset.verdict ? '' : button.dataset.verdict }); });
$('faces').addEventListener('click', event => {
  const button = event.target.closest('button[data-face-id]'); if (!button) return;
  if (button.dataset.verdict) save(button.dataset.faceId, { verdict: record(button.dataset.faceId).verdict === button.dataset.verdict ? '' : button.dataset.verdict });
  else select(button.dataset.faceId);
});
$('export-notes').addEventListener('click', () => {
  const data = { version: 1, pack: 'mist-geraldine-animation-20261002', exportedAt: new Date().toISOString(), faces: (view.manifest?.presets || []).map(preset => ({ id: preset.id, name: preset.name, ...record(preset.id) })) };
  $('notes-json').value = JSON.stringify(data, null, 2);
  $('export-status').textContent = '';
  $('notes-export').showModal();
});
$('download-notes').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([$('notes-json').value], { type: 'application/json' }));
  const anchor = node('a'); anchor.href = url; anchor.download = 'mist-geraldine-animation-notes.json'; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('copy-notes').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('notes-json').value); $('export-status').textContent = 'Copied.'; }
  catch (_) { $('notes-json').focus(); $('notes-json').select(); $('export-status').textContent = 'Select and copy the JSON.'; }
});
$('retry').addEventListener('click', load);
window.addEventListener('resize', render);
window.addEventListener('storage', event => {
  if (event.key !== storageKey) return;
  try {
    const data = JSON.parse(event.newValue || '{}'); records = data && typeof data === 'object' && !Array.isArray(data) ? data : {};
    if (view.selectedId && document.activeElement !== $('note')) $('note').value = record(view.selectedId).note;
    refreshReview();
  } catch (_) {}
});
reducedMotion.addEventListener('change', () => { $('motion-note').hidden = !reducedMotion.matches; if (reducedMotion.matches && !clock.explicit) pause(); render(); });
document.addEventListener('visibilitychange', () => { if (document.hidden && view.ready && !audio.playing) pause(); });
load();
