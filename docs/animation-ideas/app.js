(function () {
  'use strict';
  const $ = id => document.getElementById(id), review = window.MistAnimationReview;
  const state = { concepts: [], visible: [], active: null, opener: null }, choices = ['develop', 'revise', 'skip'];
  const make = (tag, cls, text) => { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined) el.textContent = text; return el; };
  const asset = (value, extension) => { const u = new URL(value, location.href); if (!/^https?:$/.test(u.protocol) || u.origin !== location.origin || !u.pathname.endsWith(extension) || u.username || u.password) throw new Error('Invalid asset path.'); return u.href; };
  function warning() { $('storage-warning').hidden = review.storageAvailable(); }
  function normalise(data) {
    if (!data || data.version !== 1 || !Array.isArray(data.concepts) || data.concepts.length !== 12) throw new Error('Incomplete catalog.');
    const ids = new Set();
    const id = value => { if (typeof value !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,199}$/.test(value) || ['__proto__','constructor','prototype'].includes(value) || ids.has(value)) throw new Error('Invalid ID.'); ids.add(value); return value; };
    const concepts = data.concepts.map(c => { id(c.id); if (!Array.isArray(c.poses) || c.poses.length !== 3 || ![1,2,3].includes(c.wave)) throw new Error('Invalid concept.'); return { ...c, poses: c.poses.map(p => ({ ...p, id: id(p.id), image: asset(p.image, '.png'), prompt: asset(p.prompt, '.txt') })) }; });
    review.configure([...ids]); return concepts;
  }
  function matches(c) {
    const q = $('search').value.trim().toLowerCase(), wave = $('wave').value, choice = $('choice-filter').value;
    return (!q || [c.title, c.summary, ...c.poses.map(p => p.label)].join(' ').toLowerCase().includes(q)) && (!wave || c.wave === Number(wave)) && (!choice || review.get(c.id).choice === choice);
  }
  function poseFigure(c, p) {
    const fig = make('figure', 'pose'), button = make('button', 'pose-open'), image = make('img');
    button.type = 'button'; button.id = 'open-' + p.id; button.setAttribute('aria-label', c.title + ': ' + p.label + '. Inspect pose');
    image.src = p.image; image.alt = c.title + ', ' + p.label + ' key pose'; image.loading = 'lazy'; image.decoding = 'async'; image.width = p.width; image.height = p.height;
    image.addEventListener('error', () => button.append(make('span', 'image-failed', 'Image unavailable')));
    button.append(image); button.addEventListener('click', () => open(c, p, button)); fig.append(button);
    const caption = make('figcaption'), note = make('button', 'pose-note-button', 'Note'); note.type = 'button'; note.id = 'note-' + p.id; note.classList.toggle('has-note', Boolean(review.get(p.id).note.trim())); note.setAttribute('aria-label', 'Note on ' + c.title + ': ' + p.label); note.addEventListener('click', () => { open(c, p, note); $('pose-note').focus(); });
    caption.append(make('span', '', p.label), note); fig.append(caption); return fig;
  }
  function conceptRow(c) {
    const row = make('article', 'idea'); row.id = c.id; const heading = make('div', 'idea-head'); heading.append(make('h2', '', c.title), make('span', 'quiet', ['','First','Then','Later'][c.wave])); row.append(heading, make('p', 'summary', c.summary));
    const poses = make('div', 'poses'); for (const p of c.poses) poses.append(poseFigure(c, p)); row.append(poses);
    const feedback = make('div', 'feedback'), group = make('div', 'choices'); group.setAttribute('role','group'); group.setAttribute('aria-label', 'Choice for ' + c.title);
    for (const choice of choices) { const b = make('button', '', choice[0].toUpperCase() + choice.slice(1)); b.type = 'button'; b.id = 'choice-' + c.id + '-' + choice; b.setAttribute('aria-pressed', String(review.get(c.id).choice === choice)); b.addEventListener('click', () => { review.update(c.id, { choice: review.get(c.id).choice === choice ? '' : choice }); render(); const target = document.getElementById(b.id); (target || $('choice-filter')).focus(); }); group.append(b); }
    const label = make('label','idea-note-label'), note = make('textarea','idea-note'); label.append(make('span','sr-only','Feedback on ' + c.title)); note.rows = 1; note.maxLength = 20000; note.placeholder = 'What should we keep or change?'; note.value = review.get(c.id).note; note.addEventListener('input', () => { review.update(c.id, { note: note.value }); warning(); }); label.append(note); feedback.append(group,label); row.append(feedback);
    const detail = make('details','motion'); detail.append(make('summary','','Proposed motion')); detail.append(make('p','',c.cycle), make('p','timing','Proposed timing: ' + c.timing)); row.append(detail); return row;
  }
  function render() {
    state.visible = state.concepts.filter(matches); $('board').replaceChildren(...state.visible.map(conceptRow)); $('empty').hidden = Boolean(state.visible.length); $('count').textContent = state.visible.length + (state.visible.length === 1 ? ' idea / ' : ' ideas / ') + state.visible.length * 3 + ' key poses'; warning();
  }
  function fillInspector(c,p) {
    state.active = { c,p }; $('inspect-title').textContent = c.title + ' / ' + p.label; $('inspect-summary').textContent = c.summary; $('inspect-image').src = p.image; $('inspect-image').alt = c.title + ', ' + p.label; $('pose-note').value = review.get(p.id).note;
    $('position').textContent = c.poses.indexOf(p) + 1 + ' / 3'; $('image-download').href = p.image; $('image-download').download = p.filename; $('prompt-link').href = p.prompt;
    $('inspect-limits').textContent = 'Static key pose; motion and speech timing still need testing.' + (p.limitations ? ' ' + p.limitations : ''); $('note-status').textContent = review.storageAvailable() ? 'Saved in this browser.' : 'Held in memory. Export before closing.';
  }
  function open(c,p,opener) { state.opener = opener; fillInspector(c,p); $('inspect').showModal(); $('inspect-close').focus(); }
  function move(delta) { if (!state.active) return; const c = state.active.c, index = c.poses.indexOf(state.active.p); fillInspector(c,c.poses[(index + delta + 3) % 3]); }
  $('previous').addEventListener('click', () => move(-1)); $('next').addEventListener('click', () => move(1)); $('inspect-close').addEventListener('click', () => $('inspect').close());
  $('inspect').addEventListener('keydown', e => { if (e.target === $('pose-note')) return; if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); move(e.key === 'ArrowLeft' ? -1 : 1); } });
  $('inspect').addEventListener('close', () => { const opener = state.opener; state.active = null; render(); const restored = opener && opener.id ? document.getElementById(opener.id) : null; if (restored) restored.focus(); else if (opener && opener.isConnected) opener.focus(); else $('search').focus(); });
  $('pose-note').addEventListener('input', () => { if (!state.active) return; review.update(state.active.p.id,{note:$('pose-note').value}); warning(); $('note-status').textContent = review.storageAvailable() ? 'Saved in this browser.' : 'Held in memory. Export before closing.'; });
  for (const id of ['search','wave','choice-filter']) $(id).addEventListener(id === 'search' ? 'input' : 'change',render);
  $('clear-filters').addEventListener('click', () => { $('search').value=''; $('wave').value=''; $('choice-filter').value=''; render(); $('search').focus(); });
  $('export-open').addEventListener('click', () => { const data=review.exportData(state.concepts); $('export-summary').textContent = Object.keys(data.records).length + ' ideas or poses with feedback.'; $('export-dialog').showModal(); });
  $('export-close').addEventListener('click', () => $('export-dialog').close()); $('export-dialog').addEventListener('close', () => $('export-open').focus());
  $('export-json').addEventListener('click', () => { const data=review.exportData(state.concepts), url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'})), a=make('a'); a.href=url; a.download='mist-animation-feedback-20260930.json'; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url),10000); });
  $('import-json').addEventListener('change', async e => { const file=e.target.files[0]; if (!file) return; try { if (file.size > 5000000) throw new Error('This export is too large.'); const count=review.importData(JSON.parse(await file.text())); $('import-status').textContent='Imported '+count+' records.'; render(); } catch(err) { $('import-status').textContent=err.message || 'The export could not be read.'; } e.target.value=''; });
  window.addEventListener('storage', e => { if (e.key === review.key) { review.sync(); if (document.activeElement.tagName !== 'TEXTAREA') render(); else warning(); } });
  fetch('catalog.json').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(data => { state.concepts=normalise(data); $('total').textContent='12 ideas · 36 key poses'; render(); }).catch(() => { $('count').textContent=''; $('load-error').hidden=false; });
})();
