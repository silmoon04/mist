(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const review = window.MistReview;
  const runtime = window.MistActing;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const STORAGE_KEY = 'mist.acting-studies.v1.review';
  const LAYERS = ['leftEye', 'rightEye', 'mouth'];
  const articleViews = new Map();
  const groupViews = new Map();
  let manifest, assets;
  let entries = [];
  let selections = new Map();
  let preserveUnreadStorage = false;
  let galleryPlaying = !reducedMotion.matches;
  let galleryElapsed = 0;
  let fps = 15;
  let raf = null;
  let lastTick = null;
  let inspector = null;
  let inspectorPlaying = false;
  let inspectorElapsed = 0;
  let inspectorFrame = 0;
  let inspectorLastDraw = '';
  let returnFocus = null;
  const transition = { from: 0, to: 1, step: 0, elapsed: 0, playing: false, lastDraw: '' };
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const pageUrl = () => location.href.split('#')[0].split('?')[0];
  const imageUrl = file => `${file}${file.includes('?') ? '&' : '?'}v=${encodeURIComponent(manifest.version)}`;
  const observer = new IntersectionObserver(changes => {
    for (const change of changes) {
      const view = articleViews.get(change.target.dataset.key);
      if (!view) continue;
      view.visible = change.isIntersecting;
      if (view.visible && assets) paintCard(view, true);
    }
    schedule();
  }, { rootMargin: '80px' });

  function message(text, error = false) {
    $('review-message').textContent = text;
    $('review-message').hidden = !text;
    $('review-message').classList.toggle('review-error', error);
  }
  function saveReview() {
    if (preserveUnreadStorage) {
      $('save-status').textContent = 'An older saved review could not be read. Export these changes to keep them.';
      return;
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(review.fromState(selections)));
      $('save-status').textContent = 'Saved in this browser.';
    } catch {
      $('save-status').textContent = 'Browser saving is unavailable. Export your review before leaving.';
    }
  }
  function updateSummary() {
    let selected = 0, notes = 0;
    for (const entry of selections.values()) {
      if (review.hasSelection(entry)) selected++;
      if (entry.note.trim()) notes++;
    }
    $('selected-count').textContent = `${selected} selected${notes ? ` · ${notes} with notes` : ''}`;
  }
  function updateVisibility() {
    let visible = 0;
    for (const group of manifest.groups) {
      let groupVisible = 0;
      for (const example of group.examples) {
        const key = review.keyFor(group, example);
        const view = articleViews.get(key);
        const show = ($('filter').value === 'all' || $('filter').value === group.id)
          && (!$('selected-only').checked || review.hasSelection(selections.get(key)));
        view.article.hidden = !show;
        if (show) groupVisible++;
      }
      groupViews.get(group.id).hidden = groupVisible === 0;
      visible += groupVisible;
    }
    $('empty-selection').hidden = visible > 0;
    schedule();
  }
  function updateEntry(key, update, affectsFilter = false) {
    const entry = { ...(selections.get(key) || review.emptyEntry()), ...update };
    selections.set(key, entry);
    articleViews.get(key)?.article.classList.toggle('is-selected', review.hasSelection(entry));
    saveReview();
    updateSummary();
    if (affectsFilter) updateVisibility();
  }
  function syncControls() {
    for (const [key, view] of articleViews) {
      const entry = selections.get(key) || review.emptyEntry();
      view.choose.checked = entry.selected;
      view.note.value = entry.note;
      view.poseChecks.forEach((checkbox, index) => { checkbox.checked = entry.poses.includes(index); });
      view.article.classList.toggle('is-selected', review.hasSelection(entry));
    }
    updateSummary();
    updateVisibility();
  }
  function receiveReview(incoming, origin) {
    selections = review.merge(selections, incoming);
    syncControls();
    saveReview();
    message(`${origin} merged. Existing choices and notes were kept.`);
  }
  function downloadText(name, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const link = make('a');
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function exportData() {
    saveReview();
    return review.exportReview(selections, manifest, pageUrl());
  }
  async function copyLink() {
    saveReview();
    let url;
    try { url = review.shareLink(selections, pageUrl()); }
    catch (error) { message(error.message, true); $('share-fallback').hidden = true; return; }
    try {
      await navigator.clipboard.writeText(url);
      $('share-fallback').hidden = true;
      message('Review link copied. It includes your choices and notes.');
    } catch {
      $('share-fallback').hidden = false;
      $('share-url').value = url;
      $('share-url').focus();
      $('share-url').select();
      message('Copy the selected link. It includes your choices and notes.');
    }
  }

  function sizeCanvas(canvas, example) {
    canvas.width = example.width;
    canvas.height = example.height;
  }
  function renderGroups() {
    $('groups').replaceChildren(...manifest.groups.map(group => {
      const section = make('section', 'study-group');
      groupViews.set(group.id, section);
      const heading = make('h2', '', group.label);
      heading.id = `group-${group.id}`;
      section.setAttribute('aria-labelledby', heading.id);
      const head = make('div', 'group-head');
      head.append(heading);
      if (group.sourceUrl) {
        const source = make('a', '', 'Inspiration');
        source.href = group.sourceUrl;
        source.target = '_blank';
        source.rel = 'noopener';
        head.append(source);
      }
      const examples = make('div', 'examples');
      for (const example of group.examples) {
        const key = review.keyFor(group, example);
        const id = `${group.id}-${example.id}`;
        const article = make('article', 'example');
        article.dataset.key = key;
        const title = make('h3', '', example.label);
        title.id = `title-${id}`;
        article.setAttribute('aria-labelledby', title.id);
        const when = make('p', 'when', example.when || '');
        const stage = make('div', 'stage');
        const canvas = make('canvas');
        canvas.setAttribute('aria-label', `${example.label}, animated preview`);
        sizeCanvas(canvas, example);
        const readout = make('span', 'frame-readout', '1 / 12');
        readout.setAttribute('aria-hidden', 'true');
        stage.append(canvas, readout);
        const inspect = make('button', '', 'Inspect');
        inspect.type = 'button';
        inspect.disabled = true;
        inspect.setAttribute('aria-label', `Inspect: ${example.label}, ${group.label}`);
        inspect.addEventListener('click', () => openInspector(group, example, inspect));
        const choice = make('label', 'sequence-choice');
        const choose = make('input');
        choose.type = 'checkbox';
        choose.setAttribute('aria-label', `Animate this: ${example.label}, ${group.label}`);
        choose.addEventListener('change', () => updateEntry(key, { selected: choose.checked }, true));
        choice.append(choose, make('span', '', 'Animate this'));
        const actions = make('div', 'example-actions');
        actions.append(choice, inspect);
        const poseChoices = make('div', 'pose-choices');
        const poseChecks = [];
        for (const [index, pose] of example.poses.entries()) {
          const label = make('label', 'pose-choice');
          const checkbox = make('input');
          checkbox.type = 'checkbox';
          checkbox.setAttribute('aria-label', `Keep ${pose.label}: ${example.label}, ${group.label}`);
          checkbox.addEventListener('change', () => {
            const current = selections.get(key) || review.emptyEntry();
            const selected = new Set(current.poses);
            if (checkbox.checked) selected.add(index); else selected.delete(index);
            updateEntry(key, { poses: [...selected].sort((a, b) => a - b) }, true);
          });
          poseChecks.push(checkbox);
          label.append(checkbox, make('span', '', pose.label));
          poseChoices.append(label);
        }
        const noteLabel = make('label', 'note-label', 'What should we keep or change?');
        noteLabel.htmlFor = `note-${id}`;
        const note = make('textarea');
        note.id = noteLabel.htmlFor;
        note.rows = 2;
        note.maxLength = review.MAX_NOTE;
        note.placeholder = 'Eyes, mouth, timing, where you would use it…';
        note.setAttribute('aria-label', `What should we keep or change? ${example.label}, ${group.label}`);
        note.addEventListener('input', () => updateEntry(key, { note: note.value }));
        const view = { article, choose, note, poseChecks, example, canvas, ctx: canvas.getContext('2d'), readout, inspect, visible: false, lastFrame: -1, timeOffset: 0 };
        articleViews.set(key, view);
        const groupLabel = make('p', 'card-group', group.label);
        article.append(groupLabel, title, when, stage, actions, poseChoices, noteLabel, note);
        examples.append(article);
        observer.observe(article);
      }
      section.append(head, examples);
      return section;
    }));
  }

  function paintCard(view, force = false) {
    if (!assets || !view.visible || view.article.hidden || $('inspector').open) return;
    const index = runtime.frameAt(galleryElapsed + view.timeOffset, view.example, fps);
    if (!force && index === view.lastFrame) return;
    runtime.drawFrame(view.ctx, view.example, index, assets);
    view.readout.textContent = `${index + 1} / 12`;
    view.lastFrame = index;
  }
  function updatePlaybackControls() {
    $('play-all').disabled = !assets || galleryPlaying;
    $('pause-all').disabled = !assets || !galleryPlaying;
    $('playback-status').textContent = galleryPlaying ? 'Playing' : reducedMotion.matches ? 'Paused · reduced motion' : 'Paused';
    $('inspector-play').disabled = inspectorPlaying;
    $('inspector-pause').disabled = !inspectorPlaying;
    $('transition-pause').disabled = !assets || !transition.playing;
  }
  function schedule() {
    if (!assets || document.hidden || raf !== null) return;
    const anyVisible = [...articleViews.values()].some(view => view.visible && !view.article.hidden);
    if (!(galleryPlaying && anyVisible && !$('inspector').open) && !inspectorPlaying && !(transition.playing && $('transitions').open)) return;
    raf = requestAnimationFrame(tick);
  }
  function tick(now) {
    raf = null;
    if (document.hidden) { lastTick = null; return; }
    const delta = lastTick === null ? 0 : Math.min(now - lastTick, 100);
    lastTick = now;
    if (galleryPlaying && !$('inspector').open) {
      galleryElapsed += delta;
      for (const view of articleViews.values()) paintCard(view);
    }
    if (inspectorPlaying && inspector && $('inspector').open) {
      inspectorElapsed += delta;
      inspectorFrame = runtime.frameAt(inspectorElapsed, inspector.example, fps);
      paintInspector();
    }
    if (transition.playing && $('transitions').open) {
      transition.elapsed += delta;
      transition.step = Math.min(11, Math.floor(transition.elapsed * fps / 1000));
      paintTransition();
      if (transition.elapsed >= 12000 / fps) {
        if ($('transition-auto').checked) {
          if (transition.elapsed >= 12000 / fps + 1000) {
            transition.from = transition.to;
            transition.to = (transition.to + 1) % entries.length;
            $('transition-from').value = String(transition.from);
            $('transition-to').value = String(transition.to);
            transition.elapsed = 0;
            transition.step = 0;
            paintTransition(true);
          }
        } else { transition.playing = false; updatePlaybackControls(); }
      }
    }
    schedule();
    if (raf === null) lastTick = null;
  }
  function playGallery(play) {
    galleryPlaying = play;
    lastTick = null;
    updatePlaybackControls();
    schedule();
  }

  function inspectorOptions() {
    return {
      layers: LAYERS.filter(layer => $(`layer-${layer}`).checked),
      ...($('mouth-independent').checked ? { mouthFrame: Number($('mouth-frame').value) } : {})
    };
  }
  function paintInspector(force = false) {
    if (!inspector || !assets) return;
    const options = inspectorOptions();
    const signature = `${inspectorFrame}:${JSON.stringify(options)}`;
    if (!force && signature === inspectorLastDraw) return;
    runtime.drawFrame($('inspector-canvas').getContext('2d'), inspector.example, inspectorFrame, assets, options);
    $('inspector-frame').value = inspectorFrame;
    $('inspector-position').textContent = `${inspectorFrame + 1} / 12`;
    const mouthFrame = options.mouthFrame ?? inspectorFrame;
    if (!$('mouth-independent').checked) $('mouth-frame').value = mouthFrame;
    $('mouth-position').textContent = `${mouthFrame + 1} / 12`;
    $('filmstrip').querySelectorAll('button').forEach((button, index) => button.setAttribute('aria-pressed', String(index === inspectorFrame)));
    const download = $('download-frame');
    download.href = imageUrl(inspector.example.frames[inspectorFrame].file);
    download.download = `MIST-${inspector.group.id}-${inspector.example.id}-${String(inspectorFrame + 1).padStart(2, '0')}.png`;
    inspectorLastDraw = signature;
  }
  function setInspectorFrame(frame) {
    inspectorPlaying = false;
    inspectorFrame = frame;
    inspectorElapsed = frame * 1000 / fps;
    paintInspector(true);
    updatePlaybackControls();
  }
  function openInspector(group, example, trigger) {
    inspector = { group, example };
    inspectorFrame = 0;
    inspectorElapsed = 0;
    inspectorLastDraw = '';
    returnFocus = trigger;
    $('inspector-title').textContent = `${group.label} / ${example.label}`;
    $('inspector-when').textContent = example.when || '';
    $('inspector-notes').textContent = Array.isArray(example.notes) ? example.notes.join(' ') : example.notes || '';
    $('inspector-notes').hidden = !$('inspector-notes').textContent;
    sizeCanvas($('inspector-canvas'), example);
    for (const layer of LAYERS) $(`layer-${layer}`).checked = true;
    $('mouth-independent').checked = false;
    $('mouth-frame').disabled = true;
    $('mouth-frame').value = 0;
    $('filmstrip').replaceChildren(...example.frames.map((frame, index) => {
      const button = make('button');
      button.type = 'button';
      button.setAttribute('aria-label', `Show frame ${index + 1}`);
      button.setAttribute('aria-pressed', 'false');
      const image = make('img');
      image.alt = '';
      image.src = imageUrl(frame.file);
      image.decoding = 'async';
      button.append(image, make('span', '', `${index + 1}`));
      button.addEventListener('click', () => setInspectorFrame(index));
      return button;
    }));
    $('inspector').showModal();
    inspectorPlaying = galleryPlaying;
    paintInspector(true);
    lastTick = null;
    updatePlaybackControls();
    schedule();
  }
  function closeInspector() {
    inspectorPlaying = false;
    inspector = null;
    lastTick = null;
    updatePlaybackControls();
    returnFocus?.focus();
    for (const view of articleViews.values()) paintCard(view, true);
    schedule();
  }

  function paintTransition(force = false) {
    if (!assets || !entries.length || !$('transitions').open) return;
    const signature = `${transition.from}:${transition.to}:${transition.step}`;
    if (!force && signature === transition.lastDraw) return;
    const from = entries[transition.from].example;
    const to = entries[transition.to].example;
    const canvas = $('transition-canvas');
    if (canvas.width !== from.width || canvas.height !== from.height) sizeCanvas(canvas, from);
    runtime.drawTransition(canvas.getContext('2d'), from, to, transition.step / 11, assets);
    $('transition-frame').value = transition.step;
    $('transition-position').textContent = `${transition.step + 1} / 12`;
    transition.lastDraw = signature;
  }
  function startTransition() {
    transition.elapsed = 0;
    transition.step = 0;
    transition.playing = true;
    lastTick = null;
    paintTransition(true);
    updatePlaybackControls();
    schedule();
  }
  function changeTransition() {
    transition.from = Number($('transition-from').value);
    transition.to = Number($('transition-to').value);
    transition.step = 0;
    transition.elapsed = 0;
    transition.playing = false;
    $('transition-auto').checked = false;
    paintTransition(true);
    updatePlaybackControls();
  }

  $('filter').addEventListener('change', updateVisibility);
  $('selected-only').addEventListener('change', updateVisibility);
  function setView(compact) {
    document.body.classList.toggle('compact', compact);
    $('view-grid').setAttribute('aria-pressed', String(compact));
    $('view-review').setAttribute('aria-pressed', String(!compact));
    for (const view of articleViews.values()) paintCard(view, true);
    schedule();
  }
  $('view-grid').addEventListener('click', () => setView(true));
  $('view-review').addEventListener('click', () => setView(false));
  $('play-all').addEventListener('click', () => playGallery(true));
  $('pause-all').addEventListener('click', () => playGallery(false));
  $('fps').addEventListener('change', () => {
    const oldFps = fps;
    fps = Number($('fps').value);
    const oldDuration = 12000 / oldFps;
    const newDuration = 12000 / fps;
    const remapCycle = (elapsed, example) => {
      const rest = Number.isFinite(example.restMs) && example.restMs >= 0 ? example.restMs : 1000;
      const phase = elapsed % (oldDuration + rest);
      return phase < oldDuration ? phase * oldFps / fps : newDuration + phase - oldDuration;
    };
    for (const view of articleViews.values()) view.timeOffset = remapCycle(galleryElapsed + view.timeOffset, view.example) - galleryElapsed;
    if (inspector) inspectorElapsed = remapCycle(inspectorElapsed, inspector.example);
    transition.elapsed = transition.elapsed < oldDuration ? transition.elapsed * oldFps / fps : newDuration + transition.elapsed - oldDuration;
    for (const view of articleViews.values()) paintCard(view, true);
    paintInspector(true);
    schedule();
  });
  $('export-json').addEventListener('click', () => {
    downloadText('MIST-animation-review.json', JSON.stringify(exportData(), null, 2), 'application/json');
    message('Review exported. Send this JSON file back with your choices.');
  });
  $('export-markdown').addEventListener('click', () => {
    downloadText('MIST-animation-notes.md', review.markdown(exportData()), 'text/markdown;charset=utf-8');
    message('Notes downloaded. Export review keeps an importable copy too.');
  });
  $('copy-review').addEventListener('click', copyLink);
  $('import-review').addEventListener('click', () => $('import-file').click());
  $('import-file').addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (file.size > review.MAX_FILE) throw new Error('This review is too large to import.');
      receiveReview(review.parse(await file.text(), manifest), 'Imported review');
    } catch (error) { message(error.message, true); }
    finally { event.target.value = ''; }
  });
  $('close-inspector').addEventListener('click', () => $('inspector').close());
  $('inspector').addEventListener('close', closeInspector);
  $('inspector-play').addEventListener('click', () => { inspectorPlaying = true; lastTick = null; updatePlaybackControls(); schedule(); });
  $('inspector-pause').addEventListener('click', () => { inspectorPlaying = false; updatePlaybackControls(); });
  $('inspector-next').addEventListener('click', () => setInspectorFrame((inspectorFrame + 1) % 12));
  $('inspector-frame').addEventListener('input', event => setInspectorFrame(Number(event.target.value)));
  for (const layer of LAYERS) $(`layer-${layer}`).addEventListener('change', () => paintInspector(true));
  $('mouth-independent').addEventListener('change', () => {
    $('mouth-frame').disabled = !$('mouth-independent').checked;
    if ($('mouth-independent').checked) $('mouth-frame').value = inspectorFrame;
    paintInspector(true);
  });
  $('mouth-frame').addEventListener('input', () => paintInspector(true));
  $('transition-from').addEventListener('change', changeTransition);
  $('transition-to').addEventListener('change', changeTransition);
  $('transition-play').addEventListener('click', startTransition);
  $('transition-pause').addEventListener('click', () => { transition.playing = false; updatePlaybackControls(); });
  $('transition-auto').addEventListener('change', () => { if ($('transition-auto').checked) startTransition(); });
  $('transition-frame').addEventListener('input', event => {
    transition.playing = false;
    $('transition-auto').checked = false;
    transition.step = Number(event.target.value);
    transition.elapsed = transition.step * 1000 / fps;
    paintTransition(true);
    updatePlaybackControls();
  });
  $('transitions').addEventListener('toggle', () => {
    if ($('transitions').open) { paintTransition(true); lastTick = null; schedule(); }
    else { transition.playing = false; updatePlaybackControls(); }
  });
  reducedMotion.addEventListener('change', event => {
    if (event.matches) {
      galleryPlaying = false;
      inspectorPlaying = false;
      transition.playing = false;
      updatePlaybackControls();
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (raf !== null) cancelAnimationFrame(raf);
    raf = null;
    lastTick = null;
    if (!document.hidden) schedule();
  });
  window.addEventListener('pagehide', () => {
    if (raf !== null) cancelAnimationFrame(raf);
    raf = null;
    lastTick = null;
  });
  window.addEventListener('pageshow', schedule);

  async function load() {
    try {
      if (!runtime || !review) throw new Error('Animation renderer or review tools are missing.');
      const response = await fetch('manifest.json', { cache: 'no-store' });
      if (!response.ok) throw new Error(`Manifest returned ${response.status}`);
      manifest = await response.json();
      if (!Array.isArray(manifest.groups) || !manifest.groups.length) throw new Error('No animation groups listed.');
      for (const group of manifest.groups) {
        if (!Array.isArray(group.examples) || !group.examples.length) throw new Error(`No examples in ${group.id}`);
        for (const example of group.examples) {
          if (!Number.isFinite(example.width) || !Number.isFinite(example.height) || !Array.isArray(example.frames) || example.frames.length !== 12 || !Array.isArray(example.poses) || example.poses.length !== 3) throw new Error(`Incomplete animation: ${group.id}/${example.id}`);
          entries.push({ group, example });
        }
        $('filter').add(new Option(group.label, group.id));
      }
      $('study-count').textContent = `${entries.length} animations · 12 frames each`;
      $('transition-auto').nextSibling.textContent = `Auto cycle all ${entries.length}`;
      let stored;
      try { stored = localStorage.getItem(STORAGE_KEY); }
      catch { $('save-status').textContent = 'Browser saving is unavailable. Export your review before leaving.'; }
      if (stored) {
        try {
          selections = review.toState(review.parse(stored, manifest));
          $('save-status').textContent = 'Saved review restored from this browser.';
        } catch {
          preserveUnreadStorage = true;
          $('save-status').textContent = 'An older saved review could not be read. Export these changes to keep them.';
        }
      }
      renderGroups();
      syncControls();
      entries.forEach(({ group, example }, index) => {
        for (const id of ['transition-from', 'transition-to']) $(id).add(new Option(`${group.label} / ${example.label}`, String(index)));
      });
      transition.to = entries.length > 1 ? 1 : 0;
      $('transition-to').value = String(transition.to);
      if (location.hash.startsWith('#review=')) {
        try {
          receiveReview(review.parseShare(location.hash, manifest), 'Shared review');
          history.replaceState(null, '', pageUrl());
        } catch (error) { message(`The shared review could not be loaded. ${error.message}`, true); }
      }
      for (const id of ['filter', 'selected-only', 'export-json', 'export-markdown', 'copy-review', 'import-review']) $(id).disabled = false;
      assets = await runtime.preload(manifest);
      for (const view of articleViews.values()) { view.inspect.disabled = false; paintCard(view, true); }
      for (const id of ['fps', 'transition-from', 'transition-to', 'transition-play', 'transition-auto', 'transition-frame']) $(id).disabled = false;
      $('status').hidden = true;
      updatePlaybackControls();
      paintTransition(true);
      schedule();
    } catch (error) {
      console.error('Acting animations:', error);
      $('status').hidden = true;
      $('error').hidden = false;
      $('error').textContent = 'Animation files could not load. Refresh to retry, or download the pack to inspect the source frames.';
    }
  }
  load();
})();
