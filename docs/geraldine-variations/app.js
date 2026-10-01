(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const review = window.MistFaceReview;
  const ui = { board: $('board'), filters: $('filters'), search: $('search'), category: $('category'), saved: $('saved-filter'), dialog: $('detail-dialog'), exportDialog: $('export-dialog') };
  const state = { studies: [], faces: [], faceById: new Map(), matching: [], matches: new Set(), visibleStudies: [], activeId: null, opener: null, sheets: new Map(), loading: false, loaded: false };
  const observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => { for (const entry of entries) if (entry.isIntersecting) { observer.unobserve(entry.target); loadCrop(entry.target); } }, { rootMargin: '320px' }) : null;
  function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
  function safeUrl(value, localOnly = false) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try { const url = new URL(value, location.href); return /^https?:$/.test(url.protocol) && !url.username && !url.password && (!localOnly || url.origin === location.origin) ? url.href : null; } catch (_) { return null; }
  }
  function link(label, value, options = {}) {
    const url = safeUrl(value, options.localOnly); if (!url) return null;
    const node = element('a', options.className, label); node.href = url;
    if (options.download) node.download = options.download === true ? '' : options.download;
    else { node.target = '_blank'; node.rel = 'noopener noreferrer'; }
    return node;
  }
  function referenceStudy(study) { return safeUrl(study.sourceStudyUrl) || safeUrl('../game-face-references/#' + encodeURIComponent(study.id)); }
  function appendLink(container, label, url, options) { const node = link(label, url, options); if (node) container.append(node); }
  function assetUrl(value, type) {
    const url = safeUrl(value, true);
    if (!url || !(type === 'sheet' ? /\.(png|jpe?g|webp)$/i : /\.txt$/i).test(new URL(url).pathname)) throw new Error('A study has an invalid ' + type + ' path.');
    return url;
  }
  function validateCatalog(data) {
    if (!data || data.version !== 1 || !Array.isArray(data.studies) || !data.studies.length) throw new Error('The study catalog is missing or has an unsupported format.');
    const ids = new Set(); const faceIds = new Set();
    const studies = data.studies.map(raw => {
      if (!raw || typeof raw.id !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,199}$/.test(raw.id) || ids.has(raw.id)) throw new Error('The study catalog contains an invalid or repeated study ID.');
      ids.add(raw.id);
      if (!Number.isFinite(raw.width) || raw.width <= 0 || !Number.isFinite(raw.height) || raw.height <= 0 || !Array.isArray(raw.poses) || raw.poses.length !== 5) throw new Error('Each study needs sheet dimensions and exactly five poses.');
      const sheet = assetUrl(raw.sheet, 'sheet'); const prompt = assetUrl(raw.prompt, 'prompt'); const positions = new Set();
      const study = { id: raw.id, displayTitle: typeof raw.displayTitle === 'string' ? raw.displayTitle : raw.id, category: typeof raw.category === 'string' ? raw.category : '', approach: typeof raw.approach === 'string' ? raw.approach : '', width: raw.width, height: raw.height, sheet, prompt, sourceStudyUrl: raw.sourceStudyUrl, referenceUrl: raw.referenceUrl, faces: [] };
      study.faces = raw.poses.map(pose => {
        if (!pose || typeof pose.id !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,199}$/.test(pose.id) || faceIds.has(pose.id) || !Number.isInteger(pose.column) || !Number.isInteger(pose.row) || pose.column < 0 || pose.column > 2 || pose.row < 0 || pose.row > 1 || (pose.column === 2 && pose.row === 1)) throw new Error('The study catalog contains an invalid or repeated face.');
        const position = pose.column + ':' + pose.row; if (positions.has(position)) throw new Error('A study contains two poses in the same sheet cell.'); positions.add(position); faceIds.add(pose.id);
        return { id: pose.id, studyId: study.id, displayTitle: study.displayTitle, poseLabel: typeof pose.label === 'string' ? pose.label : 'Pose', category: study.category, approach: study.approach, sheet, prompt, sourceStudyUrl: referenceStudy(study), referenceUrl: safeUrl(study.referenceUrl), column: pose.column, row: pose.row, ratio: raw.width * 2 / (raw.height * 3), study };
      }).sort((a, b) => a.row - b.row || a.column - b.column);
      return study;
    });
    if (Number.isInteger(data.totalFaces) && data.totalFaces !== faceIds.size) throw new Error('The catalog’s face count does not match its study poses.');
    return studies;
  }
  function loadCrop(crop) {
    const url = crop.dataset.sheet; if (!url) return;
    let receipt = state.sheets.get(url);
    if (!receipt) {
      receipt = new Promise(resolve => { const image = new Image(); image.decoding = 'async'; image.onload = () => resolve(true); image.onerror = () => resolve(false); image.src = url; });
      state.sheets.set(url, receipt);
    }
    receipt.then(loaded => { if (!crop.isConnected) return; crop.setAttribute('aria-busy', 'false'); if (loaded) { crop.style.backgroundImage = 'url(' + JSON.stringify(url) + ')'; crop.classList.add('ready'); } else crop.classList.add('failed'); });
  }
  function cropFor(face, eager = false) {
    const crop = element('span', 'face-crop'); crop.dataset.sheet = face.sheet;
    crop.style.setProperty('--cell-ratio', String(face.ratio)); crop.style.setProperty('--crop-x', (face.column * 50) + '%'); crop.style.setProperty('--crop-y', (face.row * 100) + '%');
    crop.setAttribute('role', 'img'); crop.setAttribute('aria-label', face.displayTitle + ', ' + face.poseLabel + ', static MIST face'); crop.setAttribute('aria-busy', 'true');
    if (eager) queueMicrotask(() => loadCrop(crop));
    return crop;
  }
  function faceTile(face, filtered) {
    const tile = element('div', 'pose' + (filtered && state.matches.has(face.id) ? ' is-match' : '')); tile.dataset.faceId = face.id;
    const open = element('button', 'face-open'); open.type = 'button'; open.dataset.action = 'inspect'; open.dataset.faceId = face.id; open.setAttribute('aria-label', 'Inspect ' + face.displayTitle + ', ' + face.poseLabel); open.append(cropFor(face));
    const meta = element('div', 'pose-meta'); const label = element('span', 'pose-label', face.poseLabel);
    if (filtered && state.matches.has(face.id)) label.append(element('span', 'sr-only', ', matches current filters'));
    const tools = element('div', 'pose-tools');
    const why = element('button', 'why-button', 'Why'); why.type = 'button'; why.dataset.action = 'why'; why.dataset.faceId = face.id; why.setAttribute('aria-label', 'Write a note for ' + face.displayTitle + ', ' + face.poseLabel);
    const save = element('button', 'save-button', 'Save'); save.type = 'button'; save.dataset.action = 'save'; save.dataset.faceId = face.id;
    tools.append(why, save); meta.append(label, tools); tile.append(open, meta); return tile;
  }
  function studyRow(study, filtered) {
    const article = element('article', 'study'); article.id = study.id; article.dataset.studyId = study.id;
    const heading = element('div', 'study-heading'); const title = element('h2', '', study.displayTitle); const meta = element('div', 'study-meta');
    if (study.category) meta.append(element('span', '', study.category)); appendLink(meta, 'Reference', referenceStudy(study)); appendLink(meta, 'Sheet', study.sheet, { localOnly: true }); heading.append(title, meta);
    const row = element('div', 'pose-row'); row.setAttribute('aria-label', study.displayTitle + ', five poses'); row.append(...study.faces.map(face => faceTile(face, filtered)));
    article.append(heading); if (study.approach) article.append(element('p', 'study-approach', study.approach)); article.append(row); return article;
  }
  function hasFilters() { return Boolean(ui.search.value.trim() || ui.category.value || ui.saved.checked); }
  function filteredFaces() {
    const words = ui.search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return state.faces.filter(face => { const text = [face.displayTitle, face.studyId, face.category, face.approach, face.poseLabel].join(' ').toLocaleLowerCase(); return words.every(word => text.includes(word)) && (!ui.category.value || face.category === ui.category.value) && (!ui.saved.checked || review.get(face.id).saved); });
  }
  function render() {
    if (!state.loaded) return;
    const focused = ui.board.contains(document.activeElement) ? { id: document.activeElement.dataset.faceId, action: document.activeElement.dataset.action, index: state.matching.findIndex(face => face.id === document.activeElement.dataset.faceId) } : null;
    state.matching = filteredFaces(); state.matches = new Set(state.matching.map(face => face.id)); const matchingStudies = new Set(state.matching.map(face => face.studyId)); state.visibleStudies = state.studies.filter(study => matchingStudies.has(study.id));
    if (observer) observer.disconnect();
    const filtered = hasFilters(); ui.board.replaceChildren(...state.visibleStudies.map(study => studyRow(study, filtered)));
    for (const crop of ui.board.querySelectorAll('.face-crop')) { if (observer) observer.observe(crop); else loadCrop(crop); }
    $('result-count').textContent = state.matching.length + (filtered ? ' matching face' : ' face') + (state.matching.length === 1 ? '' : 's') + ' in ' + state.visibleStudies.length + ' stud' + (state.visibleStudies.length === 1 ? 'y' : 'ies');
    $('filter-context').hidden = !filtered; $('reset-filters').hidden = !filtered; $('empty-state').hidden = state.matching.length > 0; $('empty-message').textContent = ui.saved.checked ? 'No saved faces match these filters.' : 'No studies or poses match these filters.';
    refreshReviewControls();
    if (focused) {
      const nextFace = state.matching[Math.min(Math.max(focused.index, 0), state.matching.length - 1)]; const controls = [...ui.board.querySelectorAll('button[data-face-id]')];
      const replacement = controls.find(button => button.dataset.faceId === focused.id && button.dataset.action === focused.action) || (nextFace && controls.find(button => button.dataset.faceId === nextFace.id && button.dataset.action === focused.action));
      (replacement || ui.saved).focus();
    }
  }
  function refreshReviewControls() {
    $('saved-count').textContent = state.faces.filter(face => review.get(face.id).saved).length;
    for (const button of ui.board.querySelectorAll('.save-button')) { const face = state.faceById.get(button.dataset.faceId); const record = review.get(face.id); button.textContent = record.saved ? 'Saved' : 'Save'; button.setAttribute('aria-pressed', String(record.saved)); button.setAttribute('aria-label', (record.saved ? 'Unsave ' : 'Save ') + face.displayTitle + ', ' + face.poseLabel); }
    for (const button of ui.board.querySelectorAll('.why-button')) { const note = review.get(button.dataset.faceId).note.trim(); button.classList.toggle('has-note', Boolean(note)); button.title = note ? 'Edit your note' : 'Add a note'; }
    if (state.activeId) { const saved = review.get(state.activeId).saved; $('detail-save').textContent = saved ? 'Saved' : 'Save face'; $('detail-save').setAttribute('aria-pressed', String(saved)); }
    $('storage-warning').hidden = review.storageAvailable();
  }
  function resetFilters() { ui.filters.reset(); render(); }
  function updateNoteStatus() { $('note-status').textContent = review.storageAvailable() ? 'Saved in this browser.' : 'Stored for this visit. Export notes before closing.'; $('storage-warning').hidden = review.storageAvailable(); }
  function navigationFaces() { return state.matching.some(face => face.id === state.activeId) ? state.matching : state.faces; }
  function updatePaging() { const faces = navigationFaces(); const position = faces.findIndex(face => face.id === state.activeId); $('detail-position').textContent = (position + 1) + ' / ' + faces.length; $('previous-face').disabled = position <= 0; $('next-face').disabled = position < 0 || position >= faces.length - 1; }
  function writeHash(id) { try { const url = new URL(location.href); url.hash = id ? encodeURIComponent(id) : ''; history.replaceState(null, '', url); } catch (_) {} }
  function openDetail(id, opener, focusNote = false) {
    const face = state.faceById.get(id); if (!face) return;
    const restorePoseFocus = ui.dialog.open && $('pose-picker').contains(document.activeElement);
    if (opener) state.opener = opener; state.activeId = id;
    $('detail-title').textContent = face.displayTitle; $('detail-category').textContent = face.category; $('detail-category').hidden = !face.category; $('detail-pose').textContent = face.poseLabel; $('detail-approach').textContent = face.approach;
    $('detail-face').replaceChildren(cropFor(face, true)); $('original-sheet').href = face.sheet;
    const picker = $('pose-picker'); picker.replaceChildren(...face.study.faces.map(pose => { const button = element('button', '', pose.poseLabel); button.type = 'button'; button.setAttribute('aria-pressed', String(pose.id === id)); button.addEventListener('click', () => openDetail(pose.id)); return button; }));
    const links = $('detail-links'); links.replaceChildren(); appendLink(links, 'Download sheet', face.sheet, { download: true, localOnly: true }); appendLink(links, 'Prompt', face.prompt, { localOnly: true }); appendLink(links, 'Source study', face.sourceStudyUrl); appendLink(links, 'Original inspiration', face.referenceUrl);
    $('why-note').value = review.get(id).note; updateNoteStatus(); refreshReviewControls(); updatePaging(); writeHash(id);
    if (!ui.dialog.open) ui.dialog.showModal();
    if (focusNote) $('why-note').focus(); else if (restorePoseFocus) picker.querySelector('button[aria-pressed="true"]')?.focus({ preventScroll: true });
  }
  function closeDetail() { if (ui.dialog.open) ui.dialog.close(); }
  function stepFace(delta) { const faces = navigationFaces(); const index = faces.findIndex(face => face.id === state.activeId); const face = faces[index + delta]; if (face) openDetail(face.id); }
  function toggleSave(id) {
    review.update(id, { saved: !review.get(id).saved });
    if (ui.saved.checked) render(); else refreshReviewControls();
    if (ui.dialog.open) updatePaging();
  }
  function notesSummary() { const saved = state.faces.filter(face => review.get(face.id).saved).length; const notes = state.faces.filter(face => review.get(face.id).note.trim()).length; return saved + ' saved face' + (saved === 1 ? '' : 's') + ', ' + notes + ' face' + (notes === 1 ? '' : 's') + ' with notes.'; }
  function download(name, body, type) { const url = URL.createObjectURL(new Blob([body], { type })); const anchor = element('a'); anchor.href = url; anchor.download = name; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  function showExport() { $('export-summary').textContent = notesSummary(); $('import-status').textContent = ''; ui.exportDialog.showModal(); }
  function fromHash() { let id; try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; } if (!id || !state.loaded) return; const face = state.faceById.get(id) || state.studies.find(study => study.id === id)?.faces[0]; if (face) openDetail(face.id); }
  async function loadCatalog() {
    if (state.loading) return; state.loading = true; $('load-error').hidden = true; $('result-count').textContent = 'Loading studies…'; $('retry-load').disabled = true;
    try {
      if (!review) throw new Error('The notes module did not load. Reload the page.');
      const response = await fetch('catalog.json', { cache: 'no-cache' }); if (!response.ok) throw new Error('The study catalog could not load (HTTP ' + response.status + ').');
      state.studies = validateCatalog(await response.json()); state.faces = state.studies.flatMap(study => study.faces); state.faceById = new Map(state.faces.map(face => [face.id, face])); state.loaded = true;
      $('total-count').textContent = state.studies.length + ' studies · ' + state.faces.length + ' faces';
      ui.category.replaceChildren(element('option', '', 'All categories')); ui.category.firstChild.value = ''; for (const category of [...new Set(state.studies.map(study => study.category).filter(Boolean))].sort((a, b) => a.localeCompare(b))) { const option = element('option', '', category); option.value = category; ui.category.append(option); }
      render(); fromHash();
    } catch (error) { $('load-error').hidden = false; $('load-error-message').textContent = error.message || 'The study catalog could not load.'; $('result-count').textContent = 'Studies unavailable'; }
    finally { state.loading = false; $('retry-load').disabled = false; }
  }
  ui.board.addEventListener('click', event => { const button = event.target.closest('button[data-face-id]'); if (!button) return; if (button.dataset.action === 'save') toggleSave(button.dataset.faceId); else openDetail(button.dataset.faceId, button, button.dataset.action === 'why'); });
  ui.filters.addEventListener('submit', event => event.preventDefault()); ui.search.addEventListener('input', render); ui.category.addEventListener('change', render); ui.saved.addEventListener('change', render); $('reset-filters').addEventListener('click', resetFilters); $('empty-reset').addEventListener('click', resetFilters); $('retry-load').addEventListener('click', loadCatalog);
  $('close-detail').addEventListener('click', closeDetail); $('previous-face').addEventListener('click', () => stepFace(-1)); $('next-face').addEventListener('click', () => stepFace(1)); $('detail-save').addEventListener('click', () => { if (state.activeId) toggleSave(state.activeId); });
  $('why-note').addEventListener('input', () => { if (!state.activeId) return; review.update(state.activeId, { note: $('why-note').value }); updateNoteStatus(); refreshReviewControls(); });
  ui.dialog.addEventListener('keydown', event => { if (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.ctrlKey || event.altKey || event.metaKey) return; if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); stepFace(event.key === 'ArrowLeft' ? -1 : 1); } });
  ui.dialog.addEventListener('close', () => { const closedId = state.activeId; state.activeId = null; writeHash(''); const opener = state.opener?.isConnected ? state.opener : [...ui.board.querySelectorAll('.face-open')].find(button => button.dataset.faceId === closedId); if (opener) opener.focus({ preventScroll: true }); else ui.saved.focus({ preventScroll: true }); state.opener = null; });
  for (const dialog of [ui.dialog, ui.exportDialog]) dialog.addEventListener('click', event => { if (event.target !== dialog) return; const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); });
  $('export-toggle').addEventListener('click', showExport); $('close-export').addEventListener('click', () => ui.exportDialog.close()); $('export-json').addEventListener('click', () => download('mist-geraldine-notes-20261001.json', JSON.stringify(review.exportData(state.faces), null, 2), 'application/json')); $('export-markdown').addEventListener('click', () => download('mist-geraldine-shortlist-20261001.md', review.markdown(state.faces), 'text/markdown;charset=utf-8'));
  $('import-notes').addEventListener('change', async event => { const file = event.target.files[0]; if (!file) return; try { if (file.size > 5 * 1024 * 1024) throw new Error('This notes file is over 5 MB. Use a JSON export from this board.'); const count = review.importData(JSON.parse(await file.text())); render(); if (state.activeId) { $('why-note').value = review.get(state.activeId).note; updateNoteStatus(); } $('export-summary').textContent = notesSummary(); $('import-status').textContent = 'Imported ' + count + ' face record' + (count === 1 ? '' : 's') + '.'; } catch (error) { $('import-status').textContent = error.message || 'The notes file could not be imported.'; } finally { event.target.value = ''; } });
  window.addEventListener('hashchange', () => { if (!location.hash) closeDetail(); else fromHash(); });
  window.addEventListener('storage', event => {
    if (!review || (event.key !== review.key && event.key !== null)) return;
    review.sync(); render();
    if (state.activeId) {
      const note = $('why-note'); const updated = review.get(state.activeId).note;
      if (note.value !== updated) { const start = note.selectionStart; const end = note.selectionEnd; note.value = updated; if (document.activeElement === note) note.setSelectionRange(Math.min(start, updated.length), Math.min(end, updated.length)); }
      updateNoteStatus(); updatePaging();
    }
  });
  loadCatalog();
})();
