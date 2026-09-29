// Expressions select eyes and a resting mouth. Only audible audio drives speech.
export const EXPRESSION_LIMITS = Object.freeze({minDurationMs:400,maxDurationMs:6000,cooldownMs:450,maxQueuedAgeMs:1200,maxTransitionMs:1600,maxSequenceLength:8,maxSequenceAgeMs:10000});
export const THINKING_FACE_TIMING = Object.freeze({delayMs:650,holdMs:3600});
const STATES = new Set(['available','connecting','listening','thinking','checking','speaking']);
const SOURCES = new Set(['model','director','automatic','manual','system']);
const isThinking = state => state === 'thinking' || state === 'checking';

export function normalizeExpression(map, command) {
  if (!command || typeof command !== 'object' || Array.isArray(command)) throw new TypeError('Expected an expression object');
  if (Object.keys(command).some(key => !['expression','variant','duration_ms','persistent'].includes(key))) throw new TypeError('Unknown expression field');
  if (command.persistent !== undefined && typeof command.persistent !== 'boolean') throw new TypeError('persistent must be a boolean');
  const preset = typeof command.expression === 'string' && Object.hasOwn(map.expressions, command.expression) && map.expressions[command.expression];
  if (!preset) throw new TypeError('Unknown expression');
  const variants = [preset.primary, ...(preset.alts || [])];
  const variant = command.variant === undefined ? 0 : command.variant;
  if (!Number.isInteger(variant) || variant < 0 || variant >= variants.length) throw new TypeError('Invalid expression variant');
  const duration = command.duration_ms === undefined ? (preset.duration_ms ?? 2800) : command.duration_ms;
  if (!Number.isInteger(duration)) throw new TypeError('duration_ms must be an integer');
  return {expression:command.expression,variant,faceId:variants[variant],timed:command.persistent === undefined ? Object.hasOwn(command,'duration_ms') : !command.persistent,
    duration_ms:Math.max(EXPRESSION_LIMITS.minDurationMs,Math.min(EXPRESSION_LIMITS.maxDurationMs,duration))};
}

export class ExpressionPolicy {
  constructor(map, {now = () => performance.now(), transitionMs = 0} = {}) {
    if (!map?.expressions?.neutral?.primary) throw new TypeError('Missing neutral expression');
    if (!Number.isFinite(transitionMs) || transitionMs < 0 || transitionMs > EXPRESSION_LIMITS.maxTransitionMs) throw new TypeError('Invalid expression transition duration');
    this.map = map;
    this.now = now;
    this.transitionMs = transitionMs;
    this.state = 'available';
    this.active = null;
    this.base = null;
    this.pending = null;
    this.sequenceQueue = [];
    this.lastAppliedAt = -Infinity;
    this.generation = 0;
    this.thinkingSince = null;
    this.thinkingFace = null;
    this.thinkingIndex = -1;
    this.thinkingFaces = [{expression:'curious',variant:0},{expression:'neutral',variant:1},{expression:'neutral',variant:0}]
      .map(value => ({...value,faceId:[map.expressions[value.expression]?.primary,...(map.expressions[value.expression]?.alts || [])][value.variant]}))
      .filter(value => value.faceId);
    this.counters = {applied:0,duplicate:0,queued:0,rejected:0,interrupted:0};
  }

  request(command, {source = 'model', generation = this.generation, sequenceKey = null} = {}) {
    const at = this.now();
    this.advance(at);
    if (!SOURCES.has(source) || generation !== this.generation) {
      this.counters.rejected++;
      return {status:'ignored',reason:generation !== this.generation ? 'stale_generation' : 'unknown_source'};
    }
    let value;
    try { value = normalizeExpression(this.map, command); }
    catch (error) { this.counters.rejected++; return {status:'refused',reason:error.message}; }
    const same = item => item && item.expression === value.expression && item.variant === value.variant && item.timed === value.timed;
    const sequenced = source === 'model' && sequenceKey !== null;
    if (source === 'manual' || source === 'system' || (['model','director'].includes(source) && !sequenced) ||
        (sequenced && this.active?.sequenceKey !== sequenceKey)) {
      this.pending = null;
      this.sequenceQueue = [];
    }
    // Repeated calls cannot keep a face pinned or restart its transition.
    if (same(this.active) || same(this.pending) || (sequenced && same(this.sequenceQueue.at(-1)))) {
      if (same(this.active) && (source !== 'automatic' || this.active.source === 'automatic')) {
        this.pending = null;
        if (source === 'manual' || source === 'system') this.sequenceQueue = [];
        if (source !== 'automatic') this.active.source = source;
        if (sequenced) this.active.sequenceKey = sequenceKey;
      }
      this.counters.duplicate++;
      return {status:'unchanged',reason:'duplicate',faceId:value.faceId};
    }
    if (source === 'automatic' && ((this.active && this.active.source !== 'automatic') || (this.pending && this.pending.source !== 'automatic'))) {
      this.counters.rejected++;
      return {status:'ignored',reason:'explicit_expression_active'};
    }
    const item = {...value,source,generation,sequenceKey,requestedAt:at};
    if (sequenced && this.active?.sequenceKey === sequenceKey) {
      if (this.sequenceQueue.length >= EXPRESSION_LIMITS.maxSequenceLength) {
        this.counters.rejected++;
        return {status:'ignored',reason:'sequence_full',queueDepth:this.sequenceQueue.length};
      }
      this.sequenceQueue.push(item);
      this.counters.queued++;
      return {status:'queued',faceId:value.faceId,queueDepth:this.sequenceQueue.length};
    }
    if (!sequenced && !['manual','system'].includes(source) && at-this.lastAppliedAt < EXPRESSION_LIMITS.cooldownMs) {
      this.pending = item;
      this.counters.queued++;
      return {status:'queued',faceId:value.faceId,queueDepth:1};
    }
    this.pending = null;
    this.apply(item, at);
    return {status:'applied',faceId:value.faceId};
  }

  apply(item, at) {
    this.active = {...item,appliedAt:at,holdStartsAt:at+this.transitionMs,
      expiresAt:item.timed ? at+this.transitionMs+item.duration_ms : null};
    if (!item.timed) this.base = this.active;
    this.lastAppliedAt = at;
    this.counters.applied++;
  }

  advance(at) {
    if (this.active && this.active.expiresAt !== null && this.active.expiresAt <= at) this.active = this.base;
    this.sequenceQueue = this.sequenceQueue.filter(item => item.generation === this.generation && at-item.requestedAt <= EXPRESSION_LIMITS.maxSequenceAgeMs);
    if (this.sequenceQueue.length && (!this.active || at >= this.active.holdStartsAt +
        (this.active.timed ? this.active.duration_ms : EXPRESSION_LIMITS.minDurationMs))) {
      const item = this.sequenceQueue.shift();
      this.apply(item,at);
    }
    if (this.pending && (this.pending.generation !== this.generation || at-this.pending.requestedAt > EXPRESSION_LIMITS.maxQueuedAgeMs)) this.pending = null;
    if (this.pending && at-this.lastAppliedAt >= EXPRESSION_LIMITS.cooldownMs) {
      const item = this.pending;
      this.pending = null;
      this.apply(item, at);
    }
  }

  setState(state) {
    if (!STATES.has(state)) return {status:'refused',reason:'unknown_state'};
    if (isThinking(state) && !isThinking(this.state)) this.thinkingSince = this.now();
    if (!isThinking(state)) {this.thinkingSince = null;this.thinkingFace = null;}
    this.state = state;
    return this.snapshot();
  }

  interrupt() {
    this.generation++;
    if (this.active?.timed) this.active = this.base;
    this.pending = null;
    this.sequenceQueue = [];
    this.lastAppliedAt = -Infinity;
    this.state = 'listening';
    this.thinkingSince = null;
    this.thinkingFace = null;
    this.counters.interrupted++;
    return this.snapshot();
  }

  reset() {
    this.interrupt();
    this.active = null;
    this.base = null;
    this.state = 'available';
    return this.snapshot();
  }

  snapshot() {
    const at = this.now();
    this.advance(at);
    // Keep short waits quiet, then vary the resting face without taking an expression hold.
    const thinking = isThinking(this.state);
    const showThinkingFace = thinking && (!this.active || this.active.source === 'automatic') &&
      at-this.thinkingSince >= THINKING_FACE_TIMING.delayMs;
    if (showThinkingFace && (!this.thinkingFace || at >= this.thinkingFace.nextAt)) {
      this.thinkingIndex = (this.thinkingIndex+1)%this.thinkingFaces.length;
      this.thinkingFace = {...this.thinkingFaces[this.thinkingIndex],nextAt:at+this.transitionMs+THINKING_FACE_TIMING.holdMs};
    }
    // Quiet listening rests on the ordinary neutral face; live microphone
    // energy is shown beside it, never by changing the mouth or preset.
    const defaultExpression = 'neutral';
    const selected = showThinkingFace ? this.thinkingFace : this.active;
    const expression = selected?.expression || defaultExpression;
    const requestedFaceId = selected?.faceId || this.map.expressions[expression].primary;
    let faceId = requestedFaceId;
    if (this.state === 'speaking' && this.map.faces && !this.map.faces[faceId]?.parts?.mouth) {
      const preset = this.map.expressions[expression];
      faceId = [preset.primary,...(preset.alts || [])].find(id => this.map.faces[id]?.parts?.mouth) || faceId;
    }
    return {expression,faceId,requestedFaceId,speechFallback:faceId !== requestedFaceId,variant:selected?.variant || 0,state:this.state,
      source:selected?.source || 'state',expiresAt:selected?.expiresAt || null,
      appliedAt:selected?.appliedAt ?? null,holdStartsAt:selected?.holdStartsAt ?? null,transitionMs:this.transitionMs,
      gaze:thinking ? [-0.35,-0.2] : null,mouthMode:this.state === 'speaking' ? 'audio' : 'rest',
      generation:this.generation,queued:this.sequenceQueue[0]?.expression || this.pending?.expression || null,
      queueDepth:this.sequenceQueue.length + (this.pending ? 1 : 0),sequenceKey:this.active?.sequenceKey ?? null,
      timed:this.active?.timed ?? false,baseFaceId:this.base?.faceId ?? null};
  }
}
