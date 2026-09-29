(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const player = $('player');
  const filter = $('filter');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const review = window.MistReview;
  const STORAGE_KEY = 'mist.acting-studies.v1.review';
  let selections = new Map();
  const articleViews = new Map();
  const groupViews = new Map();
  let preserveUnreadStorage = false;
  let manifest;
  let activeExample = null;
  let activeGroup = null;
  let poseIndex = 0;
  let timer = null;
  let playing = false;
  let returnFocus = null;

  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const imageUrl = file => `${file}${file.includes('?') ? '&' : '?'}v=${encodeURIComponent(manifest.version)}`;
  const duration = pose => Number.isFinite(Number(pose.durationMs)) && Number(pose.durationMs) > 0 ? Number(pose.durationMs) : 600;

  const currentPage = () => location.href.split('#')[0].split('?')[0];
  function message(text, isError = false) {
    $('review-message').textContent = text;
    $('review-message').hidden = !text;
    $('review-message').classList.toggle('review-error', isError);
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
    let selected = 0;
    let notes = 0;
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
        const show = (filter.value === 'all' || filter.value === group.id) && (!$('selected-only').checked || review.hasSelection(selections.get(key)));
        articleViews.get(key).article.hidden = !show;
        if (show) groupVisible++;
      }
      groupViews.get(group.id).hidden = groupVisible === 0;
      visible += groupVisible;
    }
    $('empty-selection').hidden = visible > 0;
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
    const link = element('a');
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function exportData() {
    saveReview();
    return review.exportReview(selections, manifest, currentPage());
  }
  async function copyLink() {
    saveReview();
    let url;
    try { url = review.shareLink(selections, currentPage()); }
    catch (error) {
      message(error.message, true);
      $('share-fallback').hidden = true;
      return;
    }
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

  function renderGroups() {
    $('groups').replaceChildren(...manifest.groups.map((group, groupIndex) => {
      const section = element('section', 'study-group');
      groupViews.set(group.id, section);
      const heading = element('h2', '', group.label);
      heading.id = `group-${group.id}`;
      section.setAttribute('aria-labelledby', heading.id);
      const label = element('div', 'group-label');
      label.append(heading);
      if (group.inspiration && group.inspiration !== group.label) label.append(element('p', 'inspiration', group.inspiration));
      const links = element('div', 'group-links');
      if (group.sourceUrl) {
        const source = element('a', '', 'Inspiration');
        source.href = group.sourceUrl;
        source.target = '_blank';
        source.rel = 'noopener';
        links.append(source);
      }
      if (group.sheet) {
        const sheet = element('a', '', 'Download source sheet');
        sheet.href = group.sheet;
        sheet.download = `MIST-${group.id}-source.png`;
        links.append(sheet);
      }
      const groupHead = element('div', 'group-head');
      groupHead.append(label, links);
      const examples = element('div', 'examples');
      for (const example of group.examples) {
        const key = review.keyFor(group, example);
        const controlId = `${group.id}-${example.id}`;
        const article = element('article', 'example');
        const title = element('h3', '', example.label);
        title.id = `title-${controlId}`;
        article.setAttribute('aria-labelledby', title.id);
        const when = element('p', 'when', example.when || '');
        const poses = element('div', 'poses');
        const poseChecks = [];
        for (const [index, pose] of example.poses.entries()) {
          const figure = element('figure');
          const stage = element('div', 'pose-image');
          const img = element('img');
          img.alt = `${example.label}: ${pose.label}`;
          img.loading = groupIndex === 0 ? 'eager' : 'lazy';
          img.decoding = 'async';
          img.addEventListener('error', () => {
            img.hidden = true;
            stage.append(element('span', 'missing', 'Image unavailable. Refresh to retry.'));
          }, { once: true });
          img.src = imageUrl(pose.file);
          stage.append(img);
          const poseLabel = pose.label || ['Setup', 'Change', 'Settle'][index];
          const caption = element('figcaption');
          const favourite = element('label', 'pose-choice');
          const checkbox = element('input');
          checkbox.type = 'checkbox';
          checkbox.setAttribute('aria-label', `Keep ${poseLabel}: ${example.label}, ${group.label}`);
          checkbox.addEventListener('change', () => {
            const current = selections.get(key) || review.emptyEntry();
            const chosen = new Set(current.poses);
            if (checkbox.checked) chosen.add(index); else chosen.delete(index);
            updateEntry(key, { poses: [...chosen].sort((a, b) => a - b) }, true);
          });
          poseChecks.push(checkbox);
          favourite.append(checkbox, element('span', '', poseLabel));
          caption.append(favourite);
          figure.append(stage, caption);
          poses.append(figure);
        }
        const play = element('button', '', 'Play timing');
        play.type = 'button';
        play.setAttribute('aria-label', `Play timing: ${example.label}, ${group.label}`);
        play.addEventListener('click', () => openPlayer(group, example, play));
        const reviewFields = element('div', 'review-fields');
        const choice = element('label', 'sequence-choice');
        const choose = element('input');
        choose.type = 'checkbox';
        choose.setAttribute('aria-label', `Animate this: ${example.label}, ${group.label}`);
        choose.addEventListener('change', () => updateEntry(key, { selected: choose.checked }, true));
        choice.append(choose, element('span', '', 'Animate this'));
        const actions = element('div', 'example-actions');
        actions.append(play, choice);
        const noteLabel = element('label', 'note-label', 'What should we keep or change?');
        noteLabel.htmlFor = `note-${controlId}`;
        const note = element('textarea');
        note.id = noteLabel.htmlFor;
        note.rows = 2;
        note.maxLength = review.MAX_NOTE;
        note.setAttribute('aria-label', `What should we keep or change? ${example.label}, ${group.label}`);
        note.placeholder = 'Eyes, mouth, timing, where you would use it…';
        note.addEventListener('input', () => updateEntry(key, { note: note.value }));
        reviewFields.append(actions, noteLabel, note);
        articleViews.set(key, { article, choose, note, poseChecks });
        article.append(title, when, poses, reviewFields);
        examples.append(article);
      }
      section.append(groupHead, examples);
      return section;
    }));
  }

  function stop() {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    playing = false;
    $('play').disabled = false;
    $('stop').disabled = true;
  }

  function showPose() {
    const pose = activeExample.poses[poseIndex];
    $('player-image').src = imageUrl(pose.file);
    $('player-image').alt = `${activeExample.label}: ${pose.label}`;
    $('pose-label').textContent = pose.label;
    $('pose-position').textContent = `${poseIndex + 1} / ${activeExample.poses.length} · ${duration(pose)} ms`;
    const download = $('download-pose');
    download.href = imageUrl(pose.file);
    download.download = `MIST-${activeGroup.id}-${activeExample.id}-${poseIndex + 1}.png`;
  }

  function queueNext() {
    if (!playing || !player.open) return;
    const lastPose = poseIndex === activeExample.poses.length - 1;
    timer = window.setTimeout(() => {
      timer = null;
      if (!playing || !player.open) return;
      poseIndex = (poseIndex + 1) % activeExample.poses.length;
      showPose();
      queueNext();
    }, duration(activeExample.poses[poseIndex]) + (lastPose ? 800 : 0));
  }

  function play() {
    stop();
    if (!activeExample || reducedMotion.matches || !player.open) return;
    playing = true;
    $('play').disabled = true;
    $('stop').disabled = false;
    queueNext();
  }

  function applyMotionPreference() {
    const reduce = reducedMotion.matches;
    $('motion-note').hidden = !reduce;
    $('play').hidden = reduce;
    $('stop').hidden = reduce;
    if (reduce) stop();
  }

  function openPlayer(group, example, trigger) {
    stop();
    activeExample = example;
    activeGroup = group;
    returnFocus = trigger;
    poseIndex = 0;
    $('player-title').textContent = `${group.label} / ${example.label}`;
    $('player-when').textContent = example.when || '';
    $('player-notes').textContent = Array.isArray(example.notes) ? example.notes.join(' ') : (example.notes || '');
    $('player-notes').hidden = !$('player-notes').textContent;
    showPose();
    applyMotionPreference();
    player.showModal();
    if (!reducedMotion.matches) play();
  }

  filter.addEventListener('change', updateVisibility);
  $('selected-only').addEventListener('change', updateVisibility);
  $('export-json').addEventListener('click', () => {
    downloadText('MIST-animation-review.json', JSON.stringify(exportData(), null, 2), 'application/json');
    message('Review exported. Send this JSON file back to continue with your choices.');
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
  $('play').addEventListener('click', play);
  $('stop').addEventListener('click', stop);
  $('next').addEventListener('click', () => {
    stop();
    poseIndex = (poseIndex + 1) % activeExample.poses.length;
    showPose();
  });
  $('close').addEventListener('click', () => { stop(); player.close(); });
  player.addEventListener('cancel', stop);
  player.addEventListener('close', () => { stop(); returnFocus?.focus(); });
  player.addEventListener('click', event => {
    if (event.target !== player) return;
    const box = player.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) { stop(); player.close(); }
  });
  reducedMotion.addEventListener('change', applyMotionPreference);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  window.addEventListener('pagehide', stop);

  async function load() {
    try {
      const response = await fetch('manifest.json', { cache: 'no-store' });
      if (!response.ok) throw new Error(`Manifest returned ${response.status}`);
      manifest = await response.json();
      if (!Array.isArray(manifest.groups) || !manifest.groups.length) throw new Error('No groups listed');
      for (const group of manifest.groups) {
        if (!Array.isArray(group.examples) || !group.examples.length || group.examples.some(example => !Array.isArray(example.poses) || example.poses.length !== 3 || example.poses.some(pose => !pose.file))) throw new Error(`Incomplete poses in ${group.id}`);
        filter.add(new Option(group.label, group.id));
      }
      const count = manifest.groups.reduce((sum, group) => sum + group.examples.length, 0);
      $('study-count').textContent = `${count} examples · 3 poses each · timing sketches`;
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
      filter.disabled = false;
      for (const id of ['selected-only', 'export-json', 'export-markdown', 'copy-review', 'import-review']) $(id).disabled = false;
      if (location.hash.startsWith('#review=')) {
        try {
          receiveReview(review.parseShare(location.hash, manifest), 'Shared review');
          history.replaceState(null, '', currentPage());
        } catch (error) { message(`The shared review could not be loaded. ${error.message}`, true); }
      }
      $('status').hidden = true;
    } catch (error) {
      console.error('Acting studies:', error);
      $('status').hidden = true;
      $('error').hidden = false;
      $('error').textContent = 'The pose files could not load. Refresh the page or try again in a moment.';
    }
  }
  load();
})();
