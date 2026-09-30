(function (root) {
  'use strict';
  const KEY = 'mist.animation-concepts.20260930.v1';
  const SCHEMA = 'mist-animation-concepts-review';
  const choices = new Set(['', 'develop', 'revise', 'skip']);
  const blocked = new Set(['__proto__', 'constructor', 'prototype']);
  const own = (v, k) => Object.prototype.hasOwnProperty.call(v, k);
  const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  const validId = id => typeof id === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,199}$/.test(id) && !blocked.has(id);
  const blank = () => ({ choice: '', note: '', updatedAt: '' });
  let records = Object.create(null), pending = Object.create(null), storageOK = true, knownIds = null;
  function cleanRecord(v) {
    if (!object(v)) return null;
    const date = own(v, 'updatedAt') && typeof v.updatedAt === 'string' && v.updatedAt.length < 65 && Number.isFinite(Date.parse(v.updatedAt)) ? new Date(v.updatedAt).toISOString() : '';
    return { choice: own(v, 'choice') && choices.has(v.choice) ? v.choice : '', note: own(v, 'note') && typeof v.note === 'string' ? v.note.slice(0, 20000) : '', updatedAt: date };
  }
  function clean(v) {
    const out = Object.create(null);
    if (!object(v)) return out;
    for (const [id, item] of Object.entries(v)) if (validId(id) && (!knownIds || knownIds.has(id))) { const r = cleanRecord(item); if (r) out[id] = r; }
    return out;
  }
  function sync() {
    let stored;
    try { stored = root.localStorage.getItem(KEY); } catch (_) { storageOK = false; return false; }
    let fresh;
    try { const parsed = JSON.parse(stored || '{}'); if (!object(parsed)) throw new Error(); fresh = clean(parsed); }
    catch (_) { storageOK = false; return true; }
    for (const [id, patch] of Object.entries(pending)) fresh[id] = { ...(own(fresh, id) ? fresh[id] : blank()), ...patch };
    records = fresh;
    return true;
  }
  function persist() {
    if (!sync()) return;
    try { root.localStorage.setItem(KEY, JSON.stringify(records)); pending = Object.create(null); storageOK = true; }
    catch (_) { storageOK = false; }
  }
  function current(id) { return { ...(validId(id) && own(records, id) ? records[id] : blank()) }; }
  function configure(ids) {
    if (!Array.isArray(ids) || ids.some(id => !validId(id))) throw new Error('Unsupported review IDs.');
    knownIds = new Set(ids); records = clean(records); pending = cleanPending(pending); sync();
  }
  function cleanPending(v) { const out = Object.create(null); for (const [id, patch] of Object.entries(v)) if (knownIds.has(id)) out[id] = patch; return out; }
  function patch(id, fields) { records[id] = { ...current(id), ...fields }; pending[id] = { ...(own(pending, id) ? pending[id] : {}), ...fields }; }
  function get(id) { sync(); return current(id); }
  function update(id, value) {
    if (!validId(id) || (knownIds && !knownIds.has(id)) || !object(value)) throw new Error('Unsupported review item.');
    if (own(value, 'choice') && !choices.has(value.choice)) throw new Error('Unsupported choice.');
    if (own(value, 'note') && typeof value.note !== 'string') throw new Error('Notes must be text.');
    const fields = { updatedAt: new Date().toISOString() };
    if (own(value, 'choice')) fields.choice = value.choice;
    if (own(value, 'note')) fields.note = value.note.slice(0, 20000);
    sync(); patch(id, fields); persist(); return current(id);
  }
  function exportData(concepts) {
    sync(); const selected = {};
    for (const [id, r] of Object.entries(records)) if (r.choice || r.note.trim()) selected[id] = { ...r };
    return { schema: SCHEMA, version: 1, exportedAt: new Date().toISOString(), records: selected,
      ideas: (Array.isArray(concepts) ? concepts : []).filter(c => own(selected, c.id) || c.poses.some(p => own(selected, p.id))).map(c => ({ id: c.id, title: c.title, wave: c.wave, poses: c.poses.map(p => ({ id: p.id, label: p.label, image: p.image })) })) };
  }
  function importData(data) {
    if (!object(data) || !own(data, 'schema') || data.schema !== SCHEMA || !own(data, 'version') || data.version !== 1 || !own(data, 'records') || !object(data.records)) throw new Error('Use an animation ideas JSON export.');
    const imported = Object.create(null);
    for (const [id, v] of Object.entries(data.records)) {
      if (!validId(id) || (knownIds && !knownIds.has(id)) || !object(v)) throw new Error('The export contains an unknown review item.');
      if ((own(v, 'choice') && !choices.has(v.choice)) || (own(v, 'note') && typeof v.note !== 'string')) throw new Error('A choice or note has an unsupported format.');
      const r = cleanRecord(v), fields = {};
      if (own(v, 'choice')) fields.choice = r.choice;
      if (own(v, 'note')) fields.note = r.note;
      if (own(v, 'updatedAt')) fields.updatedAt = r.updatedAt;
      imported[id] = fields;
    }
    sync(); for (const [id, fields] of Object.entries(imported)) patch(id, fields); persist(); return Object.keys(imported).length;
  }
  sync();
  root.MistAnimationReview = { key: KEY, configure, get, update, exportData, importData, sync, storageAvailable: () => storageOK };
})(typeof window === 'undefined' ? globalThis : window);
