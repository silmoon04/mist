export const FPS = 15;
export const FRAME_MS = 1000 / FPS;
export const TRANSITION_MS = 400;
export const EYE_FRAMES = ['rest', 'anticipation', 'half', 'near-closed', 'closed', 'opening-low', 'opening', 'rest-after-blink', 'look-left', 'look-right', 'settle', 'rest-end'];
export const MOUTH_FRAMES = ['rest', 'MBP', 'release', 'small-AA', 'AA', 'EH', 'EE', 'OH', 'OO', 'FV', 'LNT', 'S'];

export function canonicalViseme(value) {
  const raw = String(value ?? 'rest').trim();
  const aliases = { SIL: 'rest', SILENCE: 'rest', PAUSE: 'rest', NEUTRAL: 'rest', TH: 'LNT', DENTAL: 'LNT', A: 'AA', AI: 'AA', E: 'EH', I: 'EE', O: 'OH', U: 'OO', B: 'MBP', M: 'MBP', P: 'MBP', F: 'FV', V: 'FV', L: 'LNT', N: 'LNT', T: 'LNT', D: 'LNT', Z: 'S' };
  if (MOUTH_FRAMES.includes(raw)) return raw;
  if (raw.toLowerCase() === 'small-aa') return 'small-AA';
  return aliases[raw.toUpperCase()] || (MOUTH_FRAMES.includes(raw.toUpperCase()) ? raw.toUpperCase() : 'rest');
}

export function normalizeCues(raw = [], durationMs = 0) {
  const points = raw.map((cue, index) => ({
    startMs: Number.isFinite(cue.startMs) ? cue.startMs : Number(cue.time ?? cue.start ?? 0) * 1000,
    explicitEnd: Number.isFinite(cue.endMs) ? cue.endMs : undefined,
    viseme: canonicalViseme(cue.viseme), index
  })).filter(cue => Number.isFinite(cue.startMs) && cue.startMs >= 0)
    .sort((a, b) => a.startMs - b.startMs || a.index - b.index);
  const cues = [];
  for (let i = 0; i < points.length; i++) {
    const point = points[i];
    const endMs = Math.min(point.explicitEnd ?? points[i + 1]?.startMs ?? durationMs, durationMs || Infinity);
    if (endMs <= point.startMs) continue;
    const last = cues.at(-1);
    if (last?.viseme === point.viseme && Math.abs(last.endMs - point.startMs) < 0.01) last.endMs = endMs;
    else cues.push({ startMs: point.startMs, endMs, viseme: point.viseme });
  }
  return cues;
}

export function rawVisemeAt(cues, positionMs) {
  if (!Number.isFinite(positionMs) || positionMs < 0) return 'rest';
  return cues.find(cue => positionMs >= cue.startMs && positionMs < cue.endMs)?.viseme || 'rest';
}

export function mouthAt(cues, positionMs, rest = 'rest') {
  if (!Number.isFinite(positionMs) || positionMs < 0) return rest;
  const index = cues.findIndex(cue => positionMs >= cue.startMs && positionMs < cue.endMs);
  if (index < 0) return rest;
  const cue = cues[index];
  if (cue.viseme === 'rest') return rest;
  const recentClosure = cues.slice(0, index + 1).findLast(item => item.viseme === 'MBP' && positionMs >= item.startMs && positionMs < item.startMs + FRAME_MS);
  if (recentClosure) return 'MBP';
  const previous = cues[index - 1];
  if (previous?.viseme === 'MBP' && positionMs < Math.max(previous.endMs, previous.startMs + FRAME_MS) + FRAME_MS) return 'release';
  return cue.viseme;
}

function presetOf(state, id) {
  const preset = state.presets.find(item => item.id === id);
  if (!preset) throw new Error(`Unknown face preset: ${id}`);
  return preset;
}

export function createFaceState(presets, id = presets[0]?.id, nowMs = 0) {
  const state = { presets, targetId: id, epochMs: nowMs, eyeSeed: presets.findIndex(item => item.id === id) + 1, transition: null, requestCount: 0 };
  presetOf(state, id);
  return state;
}

function nextMouthBoundary(speech, nowMs, sameFamily) {
  if (sameFamily || !speech?.playing) return { atMs: nowMs, forced: false };
  const position = speech.positionMs;
  if (['rest', 'MBP'].includes(rawVisemeAt(speech.cues || [], position))) return { atMs: nowMs, forced: false };
  const boundaries = [
    ...(speech.cues || []).filter(cue => cue.startMs >= position && ['rest', 'MBP'].includes(cue.viseme)).map(cue => cue.startMs),
    ...(speech.cues || []).filter((cue, index, cues) => cue.endMs >= position && (!cues[index + 1] || cues[index + 1].startMs > cue.endMs)).map(cue => cue.endMs)
  ].filter(at => at - position <= TRANSITION_MS).sort((a, b) => a - b);
  return boundaries.length ? { atMs: nowMs + boundaries[0] - position, forced: false } : { atMs: nowMs + TRANSITION_MS, forced: true };
}

export function requestPreset(state, id, nowMs, speech = null, options = {}) {
  presetOf(state, id);
  if (state.targetId === id) return state;
  const before = sampleFace(state, nowMs, { speech, mode: options.mode || 'idle', quiet: options.quiet });
  const interruptedClosureUntilMs = before.mouthFrame === 'MBP' && state.transition?.forcedMouth
    ? state.transition.mouthAtMs + FRAME_MS : state.transition?.preservedClosureUntilMs;
  const target = presetOf(state, id);
  const boundary = before.mouthFrame === 'MBP'
    ? { atMs: nowMs, forced: false }
    : nextMouthBoundary(speech, nowMs, before.mouthFamily === target.mouth);
  state.transition = { startMs: nowMs, source: before, targetId: id, mouthAtMs: boundary.atMs, forcedMouth: boundary.forced, preservedClosureUntilMs: interruptedClosureUntilMs > nowMs ? interruptedClosureUntilMs : null, bridgeFamily: options.bridgeFamily || 'attentive' };
  state.targetId = id;
  state.requestCount++;
  return state;
}

function idleEye(state, nowMs, quiet) {
  if (quiet) return 'rest';
  const seed = state.eyeSeed;
  const elapsed = Math.max(0, nowMs - state.epochMs);
  let start = 3200 + (seed * 349 % 2700);
  let cycle = 0;
  while (elapsed >= start + FRAME_MS * 5) { cycle++; start += FRAME_MS * 4 + 3200 + ((seed * 349 + cycle * 977) % 2700); }
  const blink = elapsed - start;
  if (blink >= 0 && blink < FRAME_MS * 4) return ['half', 'closed', 'opening-low', 'opening'][Math.floor(blink / FRAME_MS)];
  if (blink >= FRAME_MS * 4 && blink < FRAME_MS * 5) return 'rest-after-blink';
  const gazeStart = start - 1550;
  if (cycle % 4 === 2 && elapsed >= gazeStart && elapsed < gazeStart + 700) return (seed + cycle) % 2 ? 'look-left' : 'look-right';
  if (cycle % 4 === 2 && elapsed >= gazeStart + 700 && elapsed < gazeStart + 900) return 'settle';
  return 'rest';
}

export function sampleFace(state, nowMs, options = {}) {
  const target = presetOf(state, state.targetId);
  const speech = options.speech;
  const speaking = Boolean(speech?.playing);
  const speechEngaged = Boolean(speech?.active || speech?.playing);
  let eyeFamily = target.eyes;
  let eyeFrame = options.mode === 'reaction' && !options.quiet
    ? EYE_FRAMES[Math.min(11, Math.floor(((Math.max(0, nowMs - state.epochMs)) % 1800) / FRAME_MS))]
    : idleEye(state, nowMs, options.quiet);
  let mouthFamily = target.mouth;
  let mouthFrame = speechEngaged ? speaking ? mouthAt(speech.cues || [], speech.positionMs, 'rest') : 'rest' : target.rest || 'rest';
  let phase = 'hold';
  const transition = state.transition;
  if (transition && nowMs - transition.startMs < TRANSITION_MS) {
    const frame = Math.max(0, Math.floor((nowMs - transition.startMs + 0.001) / FRAME_MS));
    phase = 'transition';
    const outgoing = transition.source;
    if (outgoing.eyeFamily === target.eyes) { eyeFamily = target.eyes; }
    else if (frame === 0) { eyeFamily = outgoing.eyeFamily; eyeFrame = outgoing.phase === 'transition' ? outgoing.eyeFrame : ['closed', 'near-closed', 'half'].includes(outgoing.eyeFrame) ? outgoing.eyeFrame : 'half'; }
    else if (frame === 1) { eyeFamily = outgoing.eyeFamily; eyeFrame = 'closed'; }
    else if (frame === 2) { eyeFamily = transition.bridgeFamily; eyeFrame = 'closed'; }
    else if (frame === 3) { eyeFamily = target.eyes; eyeFrame = 'closed'; }
    else { eyeFamily = target.eyes; eyeFrame = frame === 4 ? 'opening-low' : 'opening'; }
  }
  if (transition && speaking && nowMs < transition.mouthAtMs) {
    mouthFamily = transition.source.mouthFamily;
    mouthFrame = mouthAt(speech.cues || [], speech.positionMs, 'rest');
  }
  const articulating = speaking && rawVisemeAt(speech.cues || [], speech.positionMs) !== 'rest';
  if (transition?.forcedMouth && articulating) {
    const delta = nowMs - transition.mouthAtMs;
    if (delta >= -FRAME_MS && delta < FRAME_MS) mouthFrame = 'MBP';
    else if (delta >= FRAME_MS && delta < FRAME_MS * 2) mouthFrame = 'release';
  }
  if (transition?.preservedClosureUntilMs && articulating) {
    if (nowMs < transition.preservedClosureUntilMs) mouthFrame = 'MBP';
    else if (nowMs < transition.preservedClosureUntilMs + FRAME_MS) mouthFrame = 'release';
  }
  if (Number.isInteger(options.scrubFrame)) {
    eyeFamily = target.eyes; mouthFamily = target.mouth;
    eyeFrame = EYE_FRAMES[Math.max(0, Math.min(11, options.scrubFrame))];
    mouthFrame = MOUTH_FRAMES[Math.max(0, Math.min(11, options.scrubFrame))];
    phase = 'scrub';
  }
  return { presetId: target.id, eyeFamily, eyeFrame, mouthFamily, mouthFrame, phase, speaking };
}

export function stopFace(state, nowMs) {
  state.transition = null;
  state.epochMs = nowMs;
  return state;
}

export function audibleContextTime(context, performanceMs) {
  let stamp;
  try { stamp = context.getOutputTimestamp?.(); } catch (_) { /* The latency fallback also supports older browsers. */ }
  const age = performanceMs - Number(stamp?.performanceTime);
  if (stamp && Number.isFinite(stamp.contextTime) && Number.isFinite(age) && stamp.performanceTime > 0 && age >= -20 && age <= 250) {
    return Math.max(0, Math.min(context.currentTime, stamp.contextTime + Math.max(0, age) / 1000));
  }
  const latency = context.outputLatency > 0 ? context.outputLatency : context.baseLatency > 0 ? context.baseLatency : 0;
  return Math.max(0, context.currentTime - latency);
}

export function audiblePositionMs(context, performanceMs, startedAt, offsetMs, durationMs) {
  return Math.max(0, Math.min(durationMs, Math.max(0, audibleContextTime(context, performanceMs) - startedAt) * 1000 + offsetMs));
}
