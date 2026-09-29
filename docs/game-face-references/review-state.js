(function (root) {
  'use strict';
  const KEY = 'mist.game-face-references.v1';
  const SCHEMA = 'mist-game-face-review';
  const MAX_NOTE = 20000;
  let records = {};
  let storageOK = true;
  const clean = value => {
    const output = {};
    if (!value || typeof value !== 'object' || Array.isArray(value)) return output;
    for (const [id, record] of Object.entries(value)) {
      if (!/^[a-zA-Z0-9][a-zA-Z0-9_\-/.:]{0,199}$/.test(id) || !record || typeof record !== 'object' || Array.isArray(record)) continue;
      output[id] = { saved: record.saved === true, note: typeof record.note === 'string' ? record.note.slice(0, MAX_NOTE) : '', updatedAt: typeof record.updatedAt === 'string' ? record.updatedAt : '' };
    }
    return output;
  };
  try { records = clean(JSON.parse(root.localStorage.getItem(KEY) || '{}')); } catch (_) { storageOK = false; }
  function persist() { try { root.localStorage.setItem(KEY, JSON.stringify(records)); storageOK = true; } catch (_) { storageOK = false; } }
  function get(id) { return { ...(Object.hasOwn(records, id) ? records[id] : { saved: false, note: '', updatedAt: '' }) }; }
  function update(id, patch) {
    const current = get(id);
    records = { ...records, ...clean({ [id]: { ...current, ...patch, updatedAt: new Date().toISOString() } }) };
    persist();
    return get(id);
  }
  function exportData(entries) {
    return { schema: SCHEMA, version: 1, exportedAt: new Date().toISOString(), records: clean(records), references: entries.filter(entry => { const record = get(entry.id); return record.saved || record.note; }).map(entry => ({ id: entry.id, title: entry.title, studio: entry.studio, people: entry.people, sources: entry.sources })) };
  }
  function importData(data) {
    if (!data || data.schema !== SCHEMA || data.version !== 1 || !data.records || typeof data.records !== 'object' || Array.isArray(data.records)) throw new Error('Use a JSON notes export from this board.');
    const imported = clean(data.records);
    records = { ...records, ...imported };
    persist();
    return Object.keys(imported).length;
  }
  function markdown(entries) {
    const selected = entries.filter(entry => { const record = get(entry.id); return record.saved || record.note; });
    const lines = ['# Game face references', '', 'Selected source references and personal notes.', ''];
    for (const entry of selected) {
      const record = get(entry.id);
      lines.push('## ' + entry.title, '', entry.studio || '', '');
      if (record.note) lines.push(record.note, '');
      for (const person of entry.people || []) lines.push('- ' + person.name + ' — ' + person.role + (person.sourceUrl ? ' (' + person.sourceUrl + ')' : ''));
      if ((entry.people || []).length) lines.push('');
      for (const source of entry.sources || []) lines.push('- [' + source.title.replace(/\]/g, '\\]') + '](' + source.url + ')');
      lines.push('');
    }
    return lines.join('\n');
  }
  root.MistGameReview = { key: KEY, get, update, exportData, importData, markdown, storageAvailable: () => storageOK };
})(typeof window === 'undefined' ? globalThis : window);
