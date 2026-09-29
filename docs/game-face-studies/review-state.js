(function (root) {
  'use strict';

  const KEY = 'mist.generated-game-faces.20260929.v1';
  const SCHEMA = 'mist-generated-game-faces-review';
  const VERSION = 1;
  const MAX_NOTE = 20000;
  const ID_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_\-/.:]{0,199}$/;
  const BLOCKED_IDS = new Set(['__proto__', 'constructor', 'prototype']);
  const FACE_FIELDS = ['id', 'studyId', 'displayTitle', 'poseLabel', 'category', 'approach', 'sheet', 'prompt', 'sourceStudyUrl', 'referenceUrl'];
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const validId = id => typeof id === 'string' && ID_PATTERN.test(id) && !BLOCKED_IDS.has(id);
  const emptyRecord = () => ({ saved: false, note: '', updatedAt: '' });
  const hasContent = record => record.saved || Boolean(record.note.trim());
  let records = Object.create(null);
  let pending = Object.create(null);
  let storageOK = true;

  function cleanRecord(value) {
    if (!isObject(value)) return null;
    const timestamp = own(value, 'updatedAt') && typeof value.updatedAt === 'string' ? value.updatedAt : '';
    const validTimestamp = timestamp.length <= 64 && /^\d{4}-\d{2}-\d{2}T/.test(timestamp) && Number.isFinite(Date.parse(timestamp));
    return {
      saved: own(value, 'saved') && value.saved === true,
      note: own(value, 'note') && typeof value.note === 'string' ? value.note.slice(0, MAX_NOTE) : '',
      updatedAt: validTimestamp ? new Date(timestamp).toISOString() : ''
    };
  }

  function clean(value) {
    const output = Object.create(null);
    if (!isObject(value)) return output;
    for (const [id, valueRecord] of Object.entries(value)) {
      if (!validId(id)) continue;
      const record = cleanRecord(valueRecord);
      if (record) output[id] = record;
    }
    return output;
  }

  function syncStorage() {
    let stored;
    try {
      stored = root.localStorage.getItem(KEY);
    } catch (_) {
      storageOK = false;
      return false;
    }
    let fresh;
    try {
      const parsed = JSON.parse(stored || '{}');
      if (!isObject(parsed)) throw new Error('Unsupported stored review.');
      fresh = clean(parsed);
    } catch (_) {
      storageOK = false;
      // A readable but corrupt value can be repaired from the cached review.
      return true;
    }
    for (const [id, patch] of Object.entries(pending)) {
      fresh[id] = { ...(own(fresh, id) ? fresh[id] : emptyRecord()), ...patch };
    }
    records = fresh;
    return true;
  }

  syncStorage();

  function persist() {
    // Do not replace unseen changes when storage cannot be read.
    if (!syncStorage()) return;
    try {
      root.localStorage.setItem(KEY, JSON.stringify(records));
      pending = Object.create(null);
      storageOK = true;
    } catch (_) {
      storageOK = false;
    }
  }

  function currentRecord(id) {
    return { ...(validId(id) && own(records, id) ? records[id] : emptyRecord()) };
  }

  function get(id) {
    syncStorage();
    return currentRecord(id);
  }

  function applyPatch(id, patch) {
    records[id] = { ...currentRecord(id), ...patch };
    // Keep only changed fields so a save action cannot replace another tab's note.
    pending[id] = { ...(own(pending, id) ? pending[id] : {}), ...patch };
  }

  function update(id, patch) {
    if (!validId(id)) throw new Error('This face has an unsupported review ID.');
    const changes = isObject(patch) ? patch : {};
    const cleaned = cleanRecord(changes);
    const fields = { updatedAt: new Date().toISOString() };
    if (own(changes, 'saved')) fields.saved = cleaned.saved;
    if (own(changes, 'note')) fields.note = cleaned.note;
    syncStorage();
    applyPatch(id, fields);
    persist();
    return currentRecord(id);
  }

  function selectedFaces(entries) {
    if (!Array.isArray(entries)) return [];
    const seen = new Set();
    const selected = [];
    for (const entry of entries) {
      if (!isObject(entry) || !own(entry, 'id') || !validId(entry.id) || seen.has(entry.id) || !hasContent(currentRecord(entry.id))) continue;
      seen.add(entry.id);
      const face = {};
      for (const field of FACE_FIELDS) face[field] = own(entry, field) && typeof entry[field] === 'string' ? entry[field] : '';
      selected.push(face);
    }
    return selected;
  }

  function exportData(entries) {
    syncStorage();
    const selectedRecords = {};
    for (const [id, record] of Object.entries(records)) {
      if (hasContent(record)) selectedRecords[id] = { ...record };
    }
    return {
      schema: SCHEMA,
      version: VERSION,
      exportedAt: new Date().toISOString(),
      records: selectedRecords,
      faces: selectedFaces(entries)
    };
  }

  function importData(data) {
    if (!isObject(data) || !own(data, 'schema') || data.schema !== SCHEMA || !own(data, 'version') || data.version !== VERSION || !own(data, 'records') || !isObject(data.records)) {
      throw new Error('Use a JSON review export from this generated MIST face board.');
    }
    const imported = Object.create(null);
    for (const [id, value] of Object.entries(data.records)) {
      if (!validId(id) || !isObject(value)) continue;
      const hasSaved = own(value, 'saved');
      const hasNote = own(value, 'note');
      if (!hasSaved && !hasNote) continue;
      if ((hasSaved && typeof value.saved !== 'boolean') || (hasNote && typeof value.note !== 'string')) {
        throw new Error('A saved choice or note in this review has an unsupported format.');
      }
      const cleaned = cleanRecord(value);
      const fields = {};
      if (hasSaved) fields.saved = cleaned.saved;
      if (hasNote) fields.note = cleaned.note;
      if (own(value, 'updatedAt')) fields.updatedAt = cleaned.updatedAt;
      imported[id] = fields;
    }
    syncStorage();
    for (const [id, fields] of Object.entries(imported)) applyPatch(id, fields);
    persist();
    return Object.keys(imported).length;
  }

  const plain = value => String(value).replace(/[\\`*_{}\[\]<>#|]/g, '\\$&');
  const line = value => plain(value.replace(/[\r\n]+/g, ' '));

  function markdown(entries) {
    syncStorage();
    const selected = selectedFaces(entries);
    const lines = [
      '# MIST generated face review',
      '',
      "Static MIST expression studies generated with ImageGen. These are our adaptations of the referenced studies. They are not ready animations or the original artists' work.",
      '',
      `${selected.length} faces saved or noted.`,
      ''
    ];
    for (const face of selected) {
      const record = currentRecord(face.id);
      lines.push(`## ${line(face.displayTitle || face.studyId || face.id)} / ${line(face.poseLabel || face.id)}`, '', `Face ID: ${line(face.id)}`, `Saved: ${record.saved ? 'Yes' : 'No'}`);
      if (face.category) lines.push(`Category: ${line(face.category)}`);
      if (face.approach) lines.push(`Our adaptation: ${line(face.approach)}`);
      if (face.sourceStudyUrl) lines.push(`Source study: ${line(face.sourceStudyUrl)}`);
      if (face.referenceUrl) lines.push(`Original inspiration: ${line(face.referenceUrl)}`);
      if (face.sheet) lines.push(`Original sheet: ${line(face.sheet)}`);
      if (face.prompt) lines.push(`ImageGen prompt: ${line(face.prompt)}`);
      if (record.note.trim()) lines.push('', 'Notes:', ...record.note.replace(/\r\n?/g, '\n').split('\n').map(noteLine => `> ${plain(noteLine)}`));
      lines.push('');
    }
    return lines.join('\n');
  }

  root.MistFaceReview = { key: KEY, get, update, exportData, importData, markdown, sync: syncStorage, storageAvailable: () => storageOK };
})(typeof window === 'undefined' ? globalThis : window);
