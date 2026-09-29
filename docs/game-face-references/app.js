(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const review = window.MistGameReview;
  const ui = { board: $('board'), filters: $('filters'), search: $('search'), category: $('category'), media: $('media-filter'), indie: $('indie-filter'), featured: $('featured-filter'), saved: $('saved-filter'), dialog: $('detail-dialog') };
  const state = { entries: [], visible: [], activeId: null, activeMedia: 0, motion: !matchMedia('(prefers-reduced-motion: reduce)').matches, gifs: new Set(), opener: null };
  const wallObserver = new ResizeObserver(records => { for (const record of records) { const height = record.target.getBoundingClientRect().height; record.target.style.gridRowEnd = 'span ' + Math.ceil((height + 12) / 13); } });
  function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
  function safeUrl(value) { if (typeof value !== 'string' || !value.trim()) return null; try { const url = new URL(value, location.href); return /^https?:$/.test(url.protocol) ? url.href : null; } catch (_) { return null; } }
  function link(label, value, className) { const node = element('a', className, label); const url = safeUrl(value); if (url) { node.href = url; node.target = '_blank'; node.rel = 'noopener noreferrer'; } else { node.removeAttribute('href'); } return node; }
  function mediaList(entry) { return (entry.media || []).filter(media => media && media.verified === true && safeUrl(media.url)); }
  function visualMedia(entry) { const list = mediaList(entry); const preferred = Number.isInteger(entry.previewIndex) ? entry.media?.[entry.previewIndex] : null; if (preferred && list.includes(preferred)) return preferred; return list.find(media => media.kind === 'gif') || list.find(media => media.kind === 'image') || list.find(media => ['youtube', 'video'].includes(media.kind)) || list[0]; }
  function hasMotion(entry) { return mediaList(entry).some(media => ['gif', 'youtube', 'video'].includes(media.kind)); }
  function credits(entry) { return [entry.studio, ...(entry.people || []).map(person => person.name)].filter(Boolean).join(' / '); }
  function sourceFor(entry, media) { return safeUrl(media?.sourceUrl) || safeUrl(entry.sources?.[0]?.url) || safeUrl(media?.url); }
  function mediaLabel(media) { if (!media) return 'Source'; return { gif: 'GIF', image: 'Still', youtube: 'Video', video: 'Video', page: 'Source' }[media.kind] || 'Source'; }
  function placeholder(entry, media, detail) {
    const node = element('div', 'media-placeholder');
    const inner = element('div', 'placeholder-inner');
    if (['youtube', 'video'].includes(media?.kind)) inner.append(element('span', 'play-symbol', '▶'));
    inner.append(element('strong', '', detail ? 'Open this motion reference' : entry.title));
    inner.append(element('span', '', detail ? (media?.alt || 'The source clip loads when you select it.') : (media?.kind === 'page' ? 'Open the artist’s source' : 'Motion reference')));
    node.append(inner); return node;
  }
  function freeze(record) {
    if (!record.image.complete || !record.image.naturalWidth) return;
    try {
      record.canvas.width = record.image.naturalWidth; record.canvas.height = record.image.naturalHeight;
      record.canvas.getContext('2d').drawImage(record.image, 0, 0);
      record.canvas.hidden = false; record.image.style.visibility = 'hidden';
    } catch (_) { record.canvas.hidden = true; record.image.style.visibility = ''; }
  }
  function setMotion(enabled) {
    state.motion = enabled;
    $('motion-toggle').textContent = enabled ? 'Pause GIFs' : 'Play GIFs';
    $('motion-toggle').setAttribute('aria-pressed', String(enabled));
    $('detail-motion').textContent = enabled ? 'Pause GIFs' : 'Play GIFs'; $('detail-motion').setAttribute('aria-pressed', String(enabled));
    for (const record of state.gifs) { if (!record.image.isConnected) { state.gifs.delete(record); continue; } if (enabled) { record.canvas.hidden = true; record.image.style.visibility = ''; } else freeze(record); }
  }
  function imageMedia(entry, media, detail) {
    const wrap = element('div', 'image-wrap');
    const img = element('img', media.portrait ? 'portrait' : '');
    img.alt = media.alt || entry.title; img.loading = detail ? 'eager' : 'lazy'; img.decoding = 'async'; img.referrerPolicy = 'strict-origin-when-cross-origin';
    if (media.focus) img.style.objectPosition = String(media.focus);
    if (media.crop === 'contain') img.style.objectFit = 'contain';
    wrap.append(img);
    if (media.kind === 'gif') {
      const canvas = element('canvas', 'frozen-canvas'); canvas.hidden = true; canvas.setAttribute('aria-hidden', 'true');
      wrap.append(canvas); const record = { image: img, canvas }; state.gifs.add(record);
      img.addEventListener('load', () => { if (!state.motion) freeze(record); });
    }
    img.addEventListener('error', () => {
      img.hidden = true; const error = element('div', 'media-error'); error.append(element('span', '', 'Preview unavailable'), detail ? link('Open source', sourceFor(entry, media)) : element('span', '', 'Use the source link below.')); wrap.append(error);
    }, { once: true });
    img.src = safeUrl(media.url); return wrap;
  }
  function youtubeId(value) {
    try { const url = new URL(value); const host = url.hostname.replace(/^www\./, ''); let id = null; if (host === 'youtu.be') id = url.pathname.slice(1).split('/')[0]; else if (host === 'youtube.com' || host === 'youtube-nocookie.com') id = url.searchParams.get('v') || url.pathname.match(/^\/(?:embed|shorts)\/([\w-]+)/)?.[1]; return /^[\w-]{11}$/.test(id || '') ? id : null; } catch (_) { return null; }
  }
  function vimeoId(value) { try { const url = new URL(value); if (!['vimeo.com', 'www.vimeo.com', 'player.vimeo.com'].includes(url.hostname)) return null; return url.pathname.match(/(?:^\/|\/video\/)(\d{6,12})(?:\/|$)/)?.[1] || null; } catch (_) { return null; } }
  function clearMedia(container) {
    for (const video of container.querySelectorAll('video')) { video.pause(); video.removeAttribute('src'); video.load(); }
    container.replaceChildren();
  }
  function loadClip(entry, media, container) {
    if (media.kind === 'youtube' && youtubeId(media.url)) {
      const iframe = element('iframe');
      iframe.title = media.alt || entry.title + ' motion reference'; iframe.allow = 'autoplay; encrypted-media; picture-in-picture'; iframe.allowFullscreen = true; iframe.referrerPolicy = 'strict-origin-when-cross-origin';
      const url = new URL('https://www.youtube-nocookie.com/embed/' + youtubeId(media.url)); url.searchParams.set('autoplay', '1'); url.searchParams.set('rel', '0');
      const original = new URL(media.url); const timestamp = media.startSeconds ?? original.searchParams.get('start'); if (Number.isFinite(Number(timestamp)) && Number(timestamp) > 0) url.searchParams.set('start', String(Math.floor(Number(timestamp))));
      iframe.src = url.href; clearMedia(container); container.append(iframe); return;
    }
    if (['video', 'page'].includes(media.kind) && vimeoId(media.url)) {
      const iframe = element('iframe'); iframe.title = media.alt || entry.title + ' motion reference'; iframe.allow = 'autoplay; fullscreen; picture-in-picture'; iframe.allowFullscreen = true; iframe.referrerPolicy = 'strict-origin-when-cross-origin'; const original = new URL(media.url); const url = new URL('https://player.vimeo.com/video/' + vimeoId(media.url)); url.searchParams.set('autoplay', '1'); const privateHash = original.searchParams.get('h') || original.pathname.match(/\/\d{6,12}\/([\w-]+)\/?$/)?.[1]; if (privateHash && /^[\w-]{1,100}$/.test(privateHash)) url.searchParams.set('h', privateHash); iframe.src = url.href; clearMedia(container); container.append(iframe); return;
    }
    if (media.kind === 'video' && /\.(mp4|webm|ogg)(?:$|[?#])/i.test(media.url)) {
      const video = element('video'); video.controls = true; video.playsInline = true; video.preload = 'metadata'; video.src = safeUrl(media.url); video.setAttribute('aria-label', media.alt || entry.title); clearMedia(container); container.append(video); video.play().catch(() => {}); return;
    }
    window.open(sourceFor(entry, media), '_blank', 'noopener,noreferrer');
  }
  function saveButton(entry) {
    const node = element('button', 'save-button', review.get(entry.id).saved ? '♥' : '♡'); node.type = 'button'; node.setAttribute('aria-label', 'Save ' + entry.title); node.setAttribute('aria-pressed', String(review.get(entry.id).saved));
    node.addEventListener('click', () => { review.update(entry.id, { saved: !review.get(entry.id).saved }); refreshSaveButtons(); if (ui.saved.checked) render(); }); return node;
  }
  function card(entry) {
    const article = element('article', 'reference'); article.dataset.id = entry.id;
    const media = visualMedia(entry); const visual = element('div', 'reference-visual'); const open = element('button', 'media-open'); open.type = 'button'; open.setAttribute('aria-label', 'Inspect ' + entry.title);
    open.append(['gif', 'image'].includes(media?.kind) ? imageMedia(entry, media, false) : placeholder(entry, media, false));
    if (media) open.append(element('span', 'media-kind', mediaLabel(media))); open.addEventListener('click', () => openDetail(entry.id, open)); visual.append(open, saveButton(entry));
    const copy = element('div', 'reference-copy'); const title = element('h2', 'reference-title'); const titleButton = element('button', '', entry.displayTitle || entry.title); titleButton.type = 'button'; titleButton.addEventListener('click', () => openDetail(entry.id, titleButton)); title.append(titleButton);
    copy.append(title, element('p', 'reference-artist', credits(entry)), element('p', 'reference-note', entry.summary || entry.observations?.[0]?.text || ''));
    const sources = element('div', 'reference-source'); sources.append(link('Source', sourceFor(entry, media)));
    copy.append(sources); article.append(visual, copy); return article;
  }
  function filtered() {
    const query = ui.search.value.trim().toLowerCase();
    return state.entries.filter(entry => {
      const haystack = [entry.title, entry.studio, entry.summary, entry.category, ...(entry.tags || []), ...(entry.people || []).flatMap(person => [person.name, person.role]), ...(entry.observations || []).map(observation => observation.text)].join(' ').toLowerCase();
      return (!query || query.split(/\s+/).every(word => haystack.includes(word))) && (!ui.category.value || entry.category === ui.category.value) && (!ui.media.value || (ui.media.value === 'motion' ? hasMotion(entry) : mediaList(entry).some(media => media.kind === 'image'))) && (!ui.indie.checked || entry.indie === true || (entry.tags || []).some(tag => /\bindie\b/i.test(tag))) && (!ui.featured.checked || entry.featured === true) && (!ui.saved.checked || review.get(entry.id).saved);
    });
  }
  function render() {
    state.visible = filtered();
    wallObserver.disconnect();
    ui.board.replaceChildren(...state.visible.map(card));
    for (const reference of ui.board.children) wallObserver.observe(reference);
    for (const record of state.gifs) if (!record.image.isConnected) state.gifs.delete(record);
    $('result-count').textContent = state.visible.length + ' reference' + (state.visible.length === 1 ? '' : 's') + (state.visible.length !== state.entries.length ? ' of ' + state.entries.length : '');
    $('empty-state').hidden = state.visible.length > 0; $('empty-message').textContent = ui.saved.checked ? 'No saved references match these filters.' : 'No references match these filters.';
    $('reset-filters').hidden = ![ui.search.value, ui.category.value, ui.media.value, ui.indie.checked, ui.featured.checked, ui.saved.checked].some(Boolean);
    refreshSaveButtons();
  }
  function refreshSaveButtons() {
    $('saved-count').textContent = state.entries.filter(entry => review.get(entry.id).saved).length;
    for (const article of document.querySelectorAll('.reference')) { const saved = review.get(article.dataset.id).saved; const button = article.querySelector('.save-button'); button.textContent = saved ? '♥' : '♡'; button.setAttribute('aria-pressed', String(saved)); }
    if (state.activeId) { const saved = review.get(state.activeId).saved; $('detail-save').textContent = saved ? 'Saved' : 'Save'; $('detail-save').setAttribute('aria-pressed', String(saved)); }
  }
  function resetFilters() { ui.filters.reset(); render(); }
  function setDetailMedia(entry, index) {
    const media = mediaList(entry)[index]; state.activeMedia = index;
    const container = $('detail-media'); clearMedia(container);
    if (media && ['gif', 'image'].includes(media.kind)) container.append(imageMedia(entry, media, true));
    else { const button = element('button', 'media-open'); button.type = 'button'; button.append(placeholder(entry, media, true)); button.addEventListener('click', () => { if (media) loadClip(entry, media, container); else { const source = sourceFor(entry); if (source) window.open(source, '_blank', 'noopener,noreferrer'); } }); container.append(button); }
    const picker = $('media-picker'); picker.replaceChildren();
    mediaList(entry).forEach((item, i) => { const button = element('button', '', mediaLabel(item) + (mediaList(entry).filter(other => other.kind === item.kind).length > 1 ? ' ' + (i + 1) : '')); button.type = 'button'; button.setAttribute('aria-pressed', String(i === index)); button.addEventListener('click', () => setDetailMedia(entry, i)); picker.append(button); });
    const credit = $('media-credit'); credit.replaceChildren();
    if (media) { credit.append(element('span', '', media.credit || entry.studio || ''), document.createTextNode(' · '), link('Original source', sourceFor(entry, media))); if (['image', 'gif'].includes(media.kind)) credit.append(document.createTextNode(' · '), link('Full-size image', media.url)); if (media.motionVerified !== true && ['gif', 'youtube', 'video'].includes(media.kind)) credit.append(element('span', '', ' · Motion linked; observations may be based on stills.')); }
    else credit.append(link('Open source', sourceFor(entry)));
  }
  function openDetail(id, opener) {
    const entry = state.entries.find(item => item.id === id); if (!entry) return;
    if (opener) state.opener = opener; state.activeId = id;
    $('detail-title').textContent = entry.title; $('detail-studio').textContent = entry.studio || ''; $('detail-summary').textContent = entry.summary || '';
    const people = $('detail-people'); people.className = 'people'; people.replaceChildren();
    for (const person of entry.people || []) { const line = element('p'); line.append(link(person.name, person.sourceUrl), document.createTextNode(' — ' + (person.role || ''))); people.append(line); }
    const tags = $('detail-tags'); tags.replaceChildren(); for (const tag of entry.tags || []) { const button = element('button', '', tag); button.type = 'button'; button.addEventListener('click', () => { closeDetail(); ui.search.value = tag; render(); }); tags.append(button); }
    const observations = $('detail-observations'); observations.replaceChildren(); for (const observation of entry.observations || []) { const row = element('div', 'observation'); row.append(element('p', '', observation.text)); row.append(link(observation.basis === 'creator statement' ? 'Creator statement / source' : 'Our visual observation / source', observation.sourceUrl, 'basis')); observations.append(row); }
    const ideas = $('detail-ideas'); ideas.replaceChildren(...(entry.mistIdeas || []).map(idea => element('li', '', idea)));
    $('why-note').value = review.get(id).note; updateNoteStatus();
    const sources = $('detail-sources'); sources.replaceChildren(); for (const source of entry.sources || []) { const row = element('div', 'source'); row.append(link(source.title || 'Source', source.url), element('p', '', source.supports || source.kind || '')); sources.append(row); }
    $('detail-research-notes').textContent = entry.researchNotes || ''; $('source-details').open = false;
    const pos = state.visible.findIndex(item => item.id === id); $('detail-position').textContent = (pos + 1) + ' / ' + state.visible.length; $('previous-reference').disabled = pos <= 0; $('next-reference').disabled = pos < 0 || pos >= state.visible.length - 1;
    setDetailMedia(entry, Math.max(0, mediaList(entry).indexOf(visualMedia(entry)))); refreshSaveButtons(); if (!ui.dialog.open) ui.dialog.showModal(); ui.dialog.scrollTop = 0;
    history.replaceState(null, '', '#' + encodeURIComponent(id));
  }
  function closeDetail() { ui.dialog.close(); }
  function updateNoteStatus() { $('note-status').textContent = review.storageAvailable() ? 'Saved in this browser.' : 'Browser storage is unavailable. Export your notes before leaving.'; }
  function pageDetail(direction) { const pos = state.visible.findIndex(entry => entry.id === state.activeId); const next = state.visible[pos + direction]; if (next) openDetail(next.id); }
  function download(text, filename, type) { const url = URL.createObjectURL(new Blob([text], { type })); const anchor = element('a'); anchor.href = url; anchor.download = filename; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  ui.filters.addEventListener('submit', event => event.preventDefault()); ui.filters.addEventListener('input', render); ui.filters.addEventListener('change', render);
  $('reset-filters').addEventListener('click', resetFilters); $('empty-reset').addEventListener('click', resetFilters);
  $('motion-toggle').addEventListener('click', () => setMotion(!state.motion));
  $('detail-motion').addEventListener('click', () => setMotion(!state.motion));
  $('close-detail').addEventListener('click', closeDetail);
  ui.dialog.addEventListener('close', () => { clearMedia($('detail-media')); state.activeId = null; history.replaceState(null, '', location.pathname + location.search); if (ui.saved.checked) render(); if (state.opener?.isConnected) state.opener.focus(); });
  ui.dialog.addEventListener('click', event => { if (event.target === ui.dialog) { const bounds = ui.dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeDetail(); } });
  $('previous-reference').addEventListener('click', () => pageDetail(-1)); $('next-reference').addEventListener('click', () => pageDetail(1));
  ui.dialog.addEventListener('keydown', event => { if (['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return; if (event.key === 'ArrowLeft') { event.preventDefault(); pageDetail(-1); } else if (event.key === 'ArrowRight') { event.preventDefault(); pageDetail(1); } });
  $('detail-save').addEventListener('click', () => { if (!state.activeId) return; review.update(state.activeId, { saved: !review.get(state.activeId).saved }); refreshSaveButtons(); updateNoteStatus(); });
  $('why-note').addEventListener('input', () => { if (!state.activeId) return; review.update(state.activeId, { note: $('why-note').value }); updateNoteStatus(); });
  $('export-toggle').addEventListener('click', () => { const count = state.entries.filter(entry => { const record = review.get(entry.id); return record.saved || record.note; }).length; $('export-summary').textContent = count + ' reference' + (count === 1 ? '' : 's') + ' with a save or note. Downloads stay on your device.'; $('import-status').textContent = ''; $('export-dialog').showModal(); });
  $('close-export').addEventListener('click', () => $('export-dialog').close());
  $('export-json').addEventListener('click', () => download(JSON.stringify(review.exportData(state.entries), null, 2), 'MIST_game_face_notes.json', 'application/json'));
  $('export-markdown').addEventListener('click', () => download(review.markdown(state.entries), 'MIST_game_face_shortlist.md', 'text/markdown'));
  $('import-notes').addEventListener('change', async event => { const file = event.target.files[0]; if (!file) return; try { if (file.size > 3000000) throw new Error('This file is too large. Choose a notes export under 3 MB.'); const count = review.importData(JSON.parse(await file.text())); $('import-status').textContent = 'Imported ' + count + ' notes.' + (review.storageAvailable() ? '' : ' Browser storage is unavailable. Export before leaving.'); render(); } catch (error) { $('import-status').textContent = error.message; } event.target.value = ''; });
  async function start() {
    setMotion(state.motion);
    try {
      const response = await fetch('catalog.json'); if (!response.ok) throw new Error('Catalog ' + response.status); const data = await response.json(); const entries = Array.isArray(data) ? data : data.entries;
      if (!Array.isArray(entries)) throw new Error('Catalog must contain a reference array.');
      const seen = new Set(); state.entries = entries.filter(entry => { if (!entry || typeof entry.id !== 'string' || !entry.title || seen.has(entry.id)) return false; seen.add(entry.id); return true; }).sort((a, b) => Number(b.featured === true) - Number(a.featured === true));
      const categories = [...new Set(state.entries.map(entry => entry.category).filter(Boolean))].sort(); categories.forEach(category => { const option = element('option', '', { handdrawn: 'Hand drawn', '3d': '3D', cozy: 'Cozy', dialogue: 'Dialogue', graphic: 'Graphic', action: 'Action', process: 'Artist process' }[category] || category); option.value = category; ui.category.append(option); });
      $('featured-filter').closest('label').hidden = !state.entries.some(entry => entry.featured === true); $('indie-filter').closest('label').hidden = !state.entries.some(entry => entry.indie === true || (entry.tags || []).some(tag => /\bindie\b/i.test(tag)));
      $('total-count').textContent = state.entries.length; render();
      let hashId = ''; try { hashId = decodeURIComponent(location.hash.slice(1)); } catch (_) {} if (hashId) openDetail(hashId);
    } catch (error) { $('result-count').textContent = 'References could not load.'; $('empty-state').hidden = false; $('empty-message').textContent = 'Reload this page, or open the Research download above.'; $('empty-reset').hidden = true; console.error(error); }
  }
  start();
})();
