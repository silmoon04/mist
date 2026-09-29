(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MistReview = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 'mist-acting-review';
  const STUDY = 'acting_studies_v1';
  const VERSION = 1;
  const MAX_NOTE = 20000;
  const MAX_FILE = 3000000;
  const MAX_LINK = 12000;
  const keyFor = (group, example) => `${group.id}/${example.id}`;
  const emptyEntry = () => ({ selected: false, note: '', poses: [] });
  const hasSelection = entry => Boolean(entry && (entry.selected || entry.poses.length));
  const hasContent = entry => Boolean(entry && (hasSelection(entry) || entry.note.trim()));
  function catalog(manifest) {
    return new Map(manifest.groups.flatMap(group => group.examples.map(example => [keyFor(group, example), { group, example }])));
  }
  function envelope(entries) {
    return { schema: SCHEMA, version: VERSION, studyId: STUDY, entries };
  }
  function validate(value, manifest) {
    function checkKeys(node, depth = 0) {
      if (!node || typeof node !== 'object') return;
      if (depth > 20) throw new Error('This review has an unsupported structure.');
      for (const key of Object.keys(node)) {
        if (['__proto__', 'constructor', 'prototype'].includes(key)) throw new Error('This review contains an unsupported property.');
        checkKeys(node[key], depth + 1);
      }
    }
    checkKeys(value);
    if (!value || typeof value !== 'object' || value.schema !== SCHEMA || value.version !== VERSION || value.studyId !== STUDY || !Array.isArray(value.entries)) throw new Error('Choose a MIST acting-study review JSON file.');
    const known = catalog(manifest);
    if (value.entries.length > known.size) throw new Error('This review contains too many examples.');
    const seen = new Set();
    return envelope(value.entries.map(entry => {
      if (!entry || typeof entry !== 'object' || typeof entry.id !== 'string' || !known.has(entry.id) || seen.has(entry.id)) throw new Error('This review contains an unknown or repeated example.');
      seen.add(entry.id);
      if (typeof entry.selected !== 'boolean' || typeof entry.note !== 'string' || entry.note.length > MAX_NOTE || !Array.isArray(entry.poses) || entry.poses.length > known.get(entry.id).example.poses.length || new Set(entry.poses).size !== entry.poses.length || entry.poses.some(index => !Number.isInteger(index) || index < 0 || index >= known.get(entry.id).example.poses.length)) throw new Error('A selection or note has an unsupported format.');
      return { id: entry.id, selected: entry.selected, note: entry.note, poses: [...entry.poses].sort((a, b) => a - b) };
    }));
  }
  function parse(text, manifest) {
    if (typeof text !== 'string' || text.length > MAX_FILE) throw new Error('This review is too large to import.');
    let value;
    try { value = JSON.parse(text); } catch { throw new Error('This file is not valid JSON.'); }
    return validate(value, manifest);
  }
  function toState(review) {
    return new Map(review.entries.map(entry => [entry.id, { selected: entry.selected, note: entry.note, poses: [...entry.poses] }]));
  }
  function fromState(state) {
    return envelope([...state].filter(([, entry]) => hasContent(entry)).map(([id, entry]) => ({ id, selected: entry.selected, note: entry.note, poses: [...entry.poses] })));
  }
  function shareLink(state, pageUrl) {
    const url = `${pageUrl.split('#')[0]}#review=${encodeURIComponent(JSON.stringify(fromState(state)))}`;
    if (url.length > MAX_LINK) throw new Error('These notes are too long for a reliable link. Use Export review to share them.');
    return url;
  }
  function parseShare(hash, manifest) {
    if (typeof hash !== 'string' || !hash.startsWith('#review=')) throw new Error('This is not a MIST review link.');
    if (hash.length > MAX_LINK) throw new Error('This review link is too long. Import its JSON file instead.');
    let decoded;
    try { decoded = decodeURIComponent(hash.slice(8)); }
    catch { throw new Error('This review link is incomplete or damaged.'); }
    return parse(decoded, manifest);
  }
  function merge(state, incoming) {
    const merged = new Map([...state].map(([id, entry]) => [id, { ...entry, poses: [...entry.poses] }]));
    for (const entry of incoming.entries) {
      const old = merged.get(entry.id) || emptyEntry();
      let note = old.note;
      if (!note.trim()) note = entry.note;
      else if (entry.note.trim() && note !== entry.note && !note.split('\n\nImported note:\n').includes(entry.note)) note += `\n\nImported note:\n${entry.note}`;
      if (note.length > MAX_NOTE) throw new Error('Combining these notes would exceed the note limit. Your current review has not changed.');
      merged.set(entry.id, { selected: old.selected || entry.selected, note, poses: [...new Set([...old.poses, ...entry.poses])].sort((a, b) => a - b) });
    }
    return merged;
  }
  function exportReview(state, manifest, pageUrl) {
    const known = catalog(manifest);
    const review = fromState(state);
    return { ...review, exportedAt: new Date().toISOString(), pageUrl, entries: review.entries.map(entry => {
      const { group, example } = known.get(entry.id);
      return { ...entry, groupLabel: group.label, label: example.label, inspiration: group.inspiration || '', referenceUrl: group.sourceUrl || '', when: example.when || '', selectedPoses: entry.poses.map(index => ({ index, label: example.poses[index].label, file: example.poses[index].file, url: new URL(example.poses[index].file, pageUrl).href })) };
    }) };
  }
  const plain = text => String(text).replace(/[\\`*_{}\[\]<>#|]/g, '\\$&');
  function markdown(review) {
    const lines = ['# MIST animation review', '', `Review page: ${review.pageUrl}`, '', `${review.entries.filter(hasSelection).length} examples selected; ${review.entries.filter(entry => entry.note.trim()).length} with notes.`, ''];
    for (const entry of review.entries) {
      lines.push(`## ${plain(entry.label)} (${entry.id})`, '', `Inspiration: ${plain(entry.groupLabel)}`, `Animate sequence: ${entry.selected ? 'Yes' : 'Not selected'}`, `Favourite poses: ${entry.selectedPoses.length ? entry.selectedPoses.map(pose => plain(pose.label)).join(', ') : 'None'}`, '', `When: ${plain(entry.when)}`);
      if (entry.referenceUrl) lines.push(`Reference: ${entry.referenceUrl}`);
      if (entry.note.trim()) lines.push('', 'Notes:', ...entry.note.split('\n').map(line => `> ${plain(line)}`));
      if (entry.selectedPoses.length) lines.push('', ...entry.selectedPoses.map(pose => `- ${plain(pose.label)}: ${pose.url}`));
      lines.push('');
    }
    return lines.join('\n');
  }
  return { MAX_NOTE, MAX_FILE, MAX_LINK, keyFor, emptyEntry, hasSelection, hasContent, catalog, envelope, validate, parse, toState, fromState, shareLink, parseShare, merge, exportReview, markdown };
});
