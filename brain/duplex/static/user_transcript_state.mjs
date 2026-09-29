export class UserTranscriptState {
  constructor() {
    this.reset();
  }

  reset() {
    this.text = '';
    this.utteranceId = null;
    this.revision = null;
    this.final = false;
    this.source = null;
    this.pendingUtteranceId = null;
    this.pendingRevision = null;
    this.fallbackSequence = 0;
    this.finalized = new Set();
    return this.snapshot();
  }

  snapshot() {
    return {
      text: this.text,
      utteranceId: this.utteranceId,
      revision: this.revision,
      final: this.final,
      source: this.source,
      visible: Boolean(this.text.trim()),
    };
  }

  replace({ text = '', final = false, utterance_id: utteranceId, revision } = {}) {
    if (utteranceId === undefined || utteranceId === null) return this.snapshot();
    const id = `server:${utteranceId}`;

    if (id === this.pendingUtteranceId) {
      if (this.isOlderRevision(revision, this.pendingRevision)) return this.snapshot();
      if (!String(text).trim() && !final && this.final && this.text.trim()) {
        if (Number.isInteger(revision)) this.pendingRevision = revision;
        return this.snapshot();
      }
      this.utteranceId = id;
      this.revision = this.pendingRevision;
      this.text = '';
      this.final = false;
      this.source = 'server';
      this.pendingUtteranceId = null;
      this.pendingRevision = null;
    }

    if (id !== this.utteranceId) {
      if (this.finalized.has(id)) return this.snapshot();
      if (!String(text).trim() && !final && this.final && this.text.trim()) {
        this.pendingUtteranceId = id;
        this.pendingRevision = Number.isInteger(revision) ? revision : null;
        return this.snapshot();
      }
      this.utteranceId = id;
      this.revision = Number.isInteger(revision) ? revision : null;
      this.text = '';
      this.final = false;
      this.source = 'server';
    }
    if (this.isOlderRevision(revision, this.revision)) return this.snapshot();
    if (this.final && !final) return this.snapshot();
    this.text = String(text);
    this.final = Boolean(final);
    if (Number.isInteger(revision)) this.revision = revision;
    if (this.final) this.rememberFinal(id);
    return this.snapshot();
  }

  isOlderRevision(incoming, current) {
    return Number.isInteger(incoming) && Number.isInteger(current) && incoming < current;
  }

  appendFallback(delta) {
    if (this.source === 'server') return this.snapshot();
    if (this.source !== 'fallback' || this.final) {
      this.utteranceId = `fallback:${++this.fallbackSequence}`;
      this.revision = null;
      this.text = '';
      this.final = false;
      this.source = 'fallback';
      this.pendingUtteranceId = null;
      this.pendingRevision = null;
    }
    this.text += String(delta);
    return this.snapshot();
  }

  finalizeFallback(text) {
    if (this.source === 'server') return this.snapshot();
    if (this.source !== 'fallback' || this.final) {
      this.utteranceId = `fallback:${++this.fallbackSequence}`;
      this.revision = null;
      this.text = '';
      this.source = 'fallback';
      this.pendingUtteranceId = null;
      this.pendingRevision = null;
    }
    this.text = String(text);
    this.final = true;
    this.rememberFinal(this.utteranceId);
    return this.snapshot();
  }

  rememberFinal(id) {
    this.finalized.add(id);
    if (this.finalized.size > 64) this.finalized.delete(this.finalized.values().next().value);
  }
}
