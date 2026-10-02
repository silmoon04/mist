import { FPS, FRAME_MS, EYE_FRAMES, MOUTH_FRAMES, normalizeCues, audibleContextTime, audiblePositionMs } from './state.mjs';

const finiteArray = (value, length) => Array.isArray(value) && value.length === length && value.every(Number.isFinite);
function validateOverride(record, name) {
  for (const key of ['width', 'height', 'scale']) {
    if (Object.hasOwn(record, key) && (!Number.isFinite(record[key]) || record[key] <= 0)) throw new Error(`Invalid ${key} override: ${name}.`);
  }
  if (Object.hasOwn(record, 'file') && (typeof record.file !== 'string' || !record.file || !record.width || !record.height)) throw new Error(`Missing file dimensions: ${name}.`);
  if (!Object.hasOwn(record, 'file') && (Object.hasOwn(record, 'width') || Object.hasOwn(record, 'height'))) throw new Error(`Dimension overrides require a file: ${name}.`);
}

export function validateManifest(manifest) {
  if (manifest?.version !== 1 || manifest.fps !== FPS || manifest.presets?.length !== 13) throw new Error('asset-manifest.json must contain version 1, 15 fps and all 13 presets.');
  const ids = new Set();
  for (const preset of manifest.presets) {
    if (!preset.id || ids.has(preset.id) || !manifest.eyes?.[preset.eyes] || !manifest.mouths?.[preset.mouth] || !MOUTH_FRAMES.includes(preset.rest)) throw new Error(`Incomplete or repeated face preset: ${preset.id || '(missing ID)'}.`);
    ids.add(preset.id);
  }
  for (const [kind, families, expected] of [['eyes', manifest.eyes, EYE_FRAMES], ['mouths', manifest.mouths, MOUTH_FRAMES]]) {
    for (const [name, asset] of Object.entries(families)) {
      if (!asset.file || !Number.isFinite(asset.width) || !Number.isFinite(asset.height) || !(asset.scale > 0) || asset.frames?.length !== 12) throw new Error(`Incomplete ${kind} atlas: ${name}. Check its file, dimensions, uniform scale and 12 frame records.`);
      for (const id of expected) {
        const frame = asset.frames.find(item => item.id === id);
        if (!frame || !finiteArray(frame.source, 4)) throw new Error(`Missing ${kind} cel ${name}/${id}.`);
        validateOverride(frame, `${name}/${id}`);
        for (const component of kind === 'eyes' ? [frame.left, frame.right] : [frame]) {
          if (!finiteArray(component?.rect, 4) || !finiteArray(component?.anchor, 2)) throw new Error(`Missing crop or anchor for ${name}/${id}.`);
          validateOverride(component, `${name}/${id}`);
          const [x, y, width, height] = component.rect;
          if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > (component.width ?? frame.width ?? asset.width) || y + height > (component.height ?? frame.height ?? asset.height)) throw new Error(`Crop outside atlas bounds: ${name}/${id}.`);
        }
      }
    }
  }
  return manifest;
}

export async function fetchJson(path) {
  const response = await fetch(path, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`${path} could not load (HTTP ${response.status}). Check the published pack files, then retry.`);
  return response.json();
}

export class AtlasRenderer {
  constructor(manifest) { this.manifest = validateManifest(manifest); this.images = new Map(); this.loaded = false; }

  async load(onProgress = () => {}) {
    const assets = [...Object.values(this.manifest.eyes), ...Object.values(this.manifest.mouths)];
    const components = assets.flatMap(asset => asset.frames.flatMap(frame => [frame, frame.left, frame.right].filter(Boolean)));
    const files = [...new Set([...assets.map(asset => asset.file), ...components.map(component => component.file).filter(Boolean), ...(this.manifest.extraAssets || [])])];
    let loaded = 0;
    const results = await Promise.allSettled(files.map(file => new Promise((resolve, reject) => {
      const image = new Image(); image.decoding = 'async';
      image.onload = () => {
        const records = [...assets.filter(asset => asset.file === file), ...components.filter(component => component.file === file && component.width && component.height)];
        if (records.some(asset => image.naturalWidth !== asset.width || image.naturalHeight !== asset.height)) { reject(new Error(`${file} dimensions differ from its manifest. Rebuild the registration metadata.`)); return; }
        this.images.set(file, image); onProgress(++loaded, files.length); resolve(image);
      };
      image.onerror = () => reject(new Error(`${file} is unavailable. Restore this native PNG and retry.`));
      image.src = file;
    })));
    const errors = results.filter(result => result.status === 'rejected').map(result => result.reason.message);
    if (errors.length) throw new Error(errors.join(' '));
    this.loaded = true;
  }

  draw(canvas, pose) {
    if (!this.loaded) return;
    const dpr = Math.min(3, globalThis.devicePixelRatio || 1);
    const logicalWidth = 100; const logicalHeight = 64;
    const width = Math.round((canvas.clientWidth || 300) * dpr); const height = Math.round(width * logicalHeight / logicalWidth);
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    const context = canvas.getContext('2d');
    context.setTransform(width / logicalWidth, 0, 0, width / logicalWidth, 0, 0);
    context.clearRect(0, 0, logicalWidth, logicalHeight);
    context.fillStyle = this.manifest.layout?.background || '#090d0e';
    context.fillRect(0, 0, logicalWidth, logicalHeight);
    context.imageSmoothingEnabled = true; context.imageSmoothingQuality = 'high';
    const eyes = this.manifest.eyes[pose.eyeFamily];
    const mouth = this.manifest.mouths[pose.mouthFamily];
    const eyeCel = eyes.frames.find(frame => frame.id === pose.eyeFrame);
    const mouthCel = mouth.frames.find(frame => frame.id === pose.mouthFrame);
    const positions = eyes.positions || eyes.position || this.manifest.layout?.eyes || { left: [31, 25], right: [69, 25] };
    this.component(context, eyes, eyeCel.left, positions.left || [31, 25], eyeCel);
    this.component(context, eyes, eyeCel.right, positions.right || [69, 25], eyeCel);
    this.component(context, mouth, mouthCel, mouth.position || this.manifest.layout?.mouth?.at || [50, 46], mouthCel);
  }

  component(context, asset, component, position, frame) {
    const [x, y, width, height] = component.rect;
    const [anchorX, anchorY] = component.anchor;
    const scale = component.scale || frame.scale || asset.scale;
    context.drawImage(this.images.get(component.file || frame.file || asset.file), x, y, width, height,
      position[0] - (anchorX - x) * scale, position[1] - (anchorY - y) * scale, width * scale, height * scale);
  }
}

export class SharedClock {
  constructor(now = () => performance.now()) { this.now = now; this.started = now(); this.offset = 0; this.running = false; this.explicit = false; }
  time() { return this.offset + (this.running ? this.now() - this.started : 0); }
  play() { if (!this.running) { this.started = this.now(); this.running = true; } this.explicit = true; }
  pause() { if (this.running) { this.offset = this.time(); this.running = false; } }
  reset() { this.offset = 0; this.started = this.now(); }
  frame() { return Math.floor(this.time() / FRAME_MS); }
}

export class SpeechPlayer {
  constructor({ contextFactory, now = () => performance.now() } = {}) {
    this.contextFactory = contextFactory || (() => new (globalThis.AudioContext || globalThis.webkitAudioContext)());
    this.now = now; this.context = null; this.buffers = new Map(); this.clip = null; this.source = null;
    this.playing = false; this.active = false; this.hasStarted = false; this.offsetMs = 0; this.startedAt = 0; this.serial = 0;
  }

  async prepare(rawClip) {
    if (!rawClip?.audio) throw new Error('This speech sample has no audio file. Check speech-clips.json.');
    if (!this.context) this.context = this.contextFactory();
    await this.context.resume();
    let buffer = this.buffers.get(rawClip.audio);
    if (!buffer) {
      const response = await fetch(rawClip.audio);
      if (!response.ok) throw new Error(`${rawClip.audio} could not load (HTTP ${response.status}). Restore the WAV and retry.`);
      buffer = await this.context.decodeAudioData(await response.arrayBuffer());
      this.buffers.set(rawClip.audio, buffer);
    }
    const durationMs = buffer.duration * 1000;
    return { ...rawClip, durationMs, cues: normalizeCues(rawClip.cues, durationMs), buffer };
  }

  async play(rawClip, offsetMs = 0) {
    const serial = ++this.serial;
    this.disconnect(); this.playing = false; this.active = false;
    const clip = await this.prepare(rawClip);
    if (serial !== this.serial) return false;
    this.clip = clip; this.offsetMs = Math.max(0, Math.min(offsetMs, clip.durationMs));
    this.startedAt = this.context.currentTime + 0.04;
    this.source = this.context.createBufferSource(); this.source.buffer = clip.buffer; this.source.connect(this.context.destination);
    this.source.start(this.startedAt, this.offsetMs / 1000);
    this.playing = true; this.active = true; this.hasStarted = this.offsetMs > 0;
    return true;
  }

  disconnect() { if (this.source) { try { this.source.stop(); } catch (_) {} this.source.disconnect(); this.source = null; } }
  position() {
    if (!this.playing) return this.offsetMs;
    const now = this.now();
    if (audibleContextTime(this.context, now) >= this.startedAt) this.hasStarted = true;
    return audiblePositionMs(this.context, now, this.startedAt, this.offsetMs, this.clip.durationMs);
  }
  pause() { if (!this.playing) return; this.offsetMs = this.position(); this.serial++; this.disconnect(); this.playing = false; }
  async resume() { if (!this.clip) return false; return this.play(this.clip, this.offsetMs); }
  stop() { this.serial++; this.disconnect(); this.playing = false; this.active = false; this.hasStarted = false; this.offsetMs = 0; }
  snapshot() {
    if (!this.clip) return null;
    const positionMs = this.position();
    return { playing: this.active && this.hasStarted, active: this.active, positionMs, durationMs: this.clip.durationMs, cues: this.clip.cues, id: this.clip.id };
  }
  finished() { return this.playing && this.position() >= this.clip.durationMs - 1; }
}

export function createFrameLoop(callback, now = () => performance.now(), request = callback => requestAnimationFrame(callback)) {
  let lastFrame = -1; let running = true;
  function tick() {
    if (!running) return;
    const time = now(); const frame = Math.floor(time / FRAME_MS);
    if (frame !== lastFrame) { lastFrame = frame; callback(time, frame); }
    request(tick);
  }
  request(tick);
  return () => { running = false; };
}
