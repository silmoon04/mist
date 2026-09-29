(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MistActing = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const LAYERS = Object.freeze(['leftEye', 'rightEye', 'mouth']);
  const EYES = Object.freeze(['leftEye', 'rightEye']);
  const FRAME_COUNT = 12;
  const BLINK_FRAME_COUNT = 7;
  const DEFAULT_FPS = 15;
  const RIG = Object.freeze({ width: 512, height: 320 });
  const INTENTS = Object.freeze({
    listening: 'aardman/listening', disbelief: 'aardman/disbelief', realisation: 'aardman/realisation',
    reassurance: 'bluey/reassurance', delight: 'bluey/delight', uncertainty: 'bluey/uncertainty',
    recall: 'pixar/recall', checking: 'pixar/checking', discovery: 'pixar/discovery',
    double_take: 'mickey/double_take', mock_outrage: 'mickey/mock_outrage', triumph: 'mickey/triumph',
    notice: 'simons_cat/notice', waiting: 'simons_cat/waiting', clarify: 'simons_cat/clarify',
    company: 'peanuts/company', wry: 'peanuts/wry', pride: 'peanuts/pride'
  });

  function exampleName(example) { return example && (example.key || example.id) || 'unnamed example'; }
  function checkExample(example) {
    if (!example || example.width !== RIG.width || example.height !== RIG.height) {
      throw new Error('Animation needs the shared 512×320 rig.');
    }
    if (!Array.isArray(example.frames) || example.frames.length !== FRAME_COUNT) {
      throw new Error(exampleName(example) + ' needs exactly 12 acting frames.');
    }
    return example;
  }
  function checkBlink(example) {
    checkExample(example);
    if (!Array.isArray(example.blinkFrames) || example.blinkFrames.length !== BLINK_FRAME_COUNT) {
      throw new Error(exampleName(example) + ' needs exactly 7 separate blink frames.');
    }
    return example;
  }
  function checkLayer(example, frame, name) {
    const layer = frame && frame.layers && frame.layers[name];
    if (!layer || typeof layer.file !== 'string' || !layer.file.trim()) {
      throw new Error(exampleName(example) + ' is missing ' + name + ' artwork.');
    }
    if (![layer.x, layer.y, layer.width, layer.height].every(Number.isFinite) ||
        layer.x < 0 || layer.y < 0 || layer.width <= 0 || layer.height <= 0 ||
        layer.x + layer.width > RIG.width || layer.y + layer.height > RIG.height) {
      throw new Error(exampleName(example) + ' has invalid ' + name + ' bounds.');
    }
    return layer;
  }
  function sameLayer(a, b) {
    return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height &&
      (a.file === b.file || (a.sha256 && a.sha256 === b.sha256));
  }
  function baseDuration(frame) {
    const ticks = frame && frame.exposureTicks;
    const ms = frame && frame.durationMs;
    if (ticks !== undefined && (!Number.isInteger(ticks) || ticks <= 0)) {
      throw new Error('Frame exposureTicks must be a positive integer.');
    }
    if (ms !== undefined && (!Number.isFinite(ms) || ms <= 0)) {
      throw new Error('Frame durationMs must be greater than zero.');
    }
    if (ticks === undefined && ms === undefined) throw new Error('Every frame needs an authored durationMs or exposureTicks.');
    if (ticks !== undefined && ms !== undefined && Math.abs(ticks * 1000 / DEFAULT_FPS - ms) > 1) {
      throw new Error('Frame durationMs and exposureTicks disagree.');
    }
    return ms === undefined ? ticks * 1000 / DEFAULT_FPS : ms;
  }
  function speed(fps) {
    if (!Number.isFinite(fps) || fps <= 0) throw new Error('FPS must be greater than zero.');
    return DEFAULT_FPS / fps;
  }
  function restDuration(example) {
    const restMs = example.restMs === undefined ? 1000 : example.restMs;
    if (!Number.isFinite(restMs) || restMs < 0) throw new Error('Rest duration cannot be negative.');
    return restMs;
  }
  function timeline(example, fps = DEFAULT_FPS) {
    checkExample(example);
    const scale = speed(fps);
    const durations = example.frames.map(frame => baseDuration(frame) * scale);
    const activeMs = durations.reduce((sum, ms) => sum + ms, 0);
    const restMs = restDuration(example);
    return { durations, activeMs, restMs, totalMs: activeMs + restMs };
  }
  function indexAt(ms, durations) {
    let boundary = 0;
    for (let index = 0; index < durations.length; index++) {
      boundary += durations[index];
      if (ms < boundary - 1e-7) return index;
    }
    return durations.length - 1;
  }
  function frameAt(elapsedMs, example, fps = DEFAULT_FPS) {
    if (!Number.isFinite(elapsedMs)) throw new Error('Elapsed time must be finite.');
    const clock = timeline(example, fps);
    const local = Math.max(0, elapsedMs) % clock.totalMs;
    return local >= clock.activeMs - 1e-7 ? 0 : indexAt(local, clock.durations);
  }
  function blinkAt(elapsedMs, example, fps = DEFAULT_FPS) {
    if (!Number.isFinite(elapsedMs)) throw new Error('Elapsed time must be finite.');
    checkBlink(example);
    const scale = speed(fps);
    return indexAt(Math.max(0, elapsedMs), example.blinkFrames.map(frame => baseDuration(frame) * scale));
  }
  function frameStartMs(example, index, fps = DEFAULT_FPS) {
    return timeline(example, fps).durations.slice(0, indexWithin(index)).reduce((sum, ms) => sum + ms, 0);
  }
  function cycleDurationMs(example, fps = DEFAULT_FPS) { return timeline(example, fps).totalMs; }
  function blinkDurationMs(example, fps = DEFAULT_FPS) {
    checkBlink(example);
    const scale = speed(fps);
    return example.blinkFrames.reduce((sum, frame) => sum + baseDuration(frame) * scale, 0);
  }
  function validateExample(example) {
    checkBlink(example);
    timeline(example);
    example.frames.forEach(frame => LAYERS.forEach(name => checkLayer(example, frame, name)));
    example.blinkFrames.forEach(frame => {
      baseDuration(frame);
      EYES.forEach(name => checkLayer(example, frame, name));
      if (frame.layers.mouth) checkLayer(example, frame, 'mouth');
    });
    for (const name of LAYERS) {
      if (!sameLayer(example.frames[0].layers[name], example.frames[FRAME_COUNT - 1].layers[name])) {
        throw new Error(exampleName(example) + ' acting loop must return to its resting ' + name + ' before the rest hold.');
      }
    }
    for (const name of EYES) {
      const rest = example.frames[0].layers[name];
      for (const index of [0, BLINK_FRAME_COUNT - 1]) {
        if (!sameLayer(rest, example.blinkFrames[index].layers[name])) {
          throw new Error(exampleName(example) + ' blink endpoints must match its resting ' + name + '.');
        }
      }
    }
    return example;
  }
  function indexWithin(value, fallback = 0, count = FRAME_COUNT) {
    if (value === undefined || value === null) return fallback;
    if (!Number.isFinite(value)) throw new Error('Frame index must be a finite number.');
    return Math.max(0, Math.min(count - 1, Math.floor(value)));
  }
  function stepAtProgress(progress) {
    if (!Number.isFinite(progress)) throw new Error('Transition progress must be finite.');
    return Math.min(FRAME_COUNT - 1, Math.floor(Math.max(0, Math.min(1, progress)) * FRAME_COUNT));
  }
  const EYE_STEPS = Object.freeze([
    ['source', 0, 'source'], ['source', 1, 'closing'], ['source', 2, 'closing'],
    ['source', 3, 'closed-source'], ['source', 3, 'closed-source'],
    ['target', 3, 'closed-handoff'], ['target', 3, 'closed-target'],
    ['target', 4, 'opening'], ['target', 5, 'opening'],
    ['target', 6, 'settling'], ['target', 6, 'target'], ['target', 6, 'target']
  ].map(Object.freeze));
  function transitionPlan(fromExample, toExample) {
    checkBlink(fromExample); checkBlink(toExample);
    for (const name of EYES) {
      if (!sameLayer(checkLayer(fromExample, fromExample.blinkFrames[3], name),
        checkLayer(toExample, toExample.blinkFrames[3], name))) {
        throw new Error('Transition needs identical shared closed ' + name + ' artwork and placement.');
      }
    }
    if (!sameLayer(checkLayer(fromExample, fromExample.frames[0], 'mouth'),
      checkLayer(toExample, toExample.frames[0], 'mouth'))) {
      throw new Error('Transition needs identical shared resting mouth artwork and placement.');
    }
    return EYE_STEPS.map(([side, frame, phase], index) => ({
      index, phase, eyes: { side, frame, clip: 'blink' },
      sourceFrame: side === 'source' ? frame : null,
      targetFrame: side === 'target' ? frame : null,
      mouth: { sourceFrame: 0, targetFrame: 0, targetWeight: side === 'source' ? 0 : 1, mode: 'shared-rest' }
    }));
  }
  function selectedLayers(options) {
    if (options.layers === undefined) return LAYERS;
    if (!Array.isArray(options.layers) || options.layers.some(name => !LAYERS.includes(name))) {
      throw new Error('Layers must contain only leftEye, rightEye and mouth.');
    }
    return [...new Set(options.layers)];
  }
  function assetImage(assets, file) {
    if (!assets || assets.disposed) throw new Error('Animation assets are not available.');
    const image = assets.images instanceof Map ? assets.images.get(file) : assets.images && assets.images[file];
    if (!image) throw new Error('Missing decoded artwork: ' + file);
    return image;
  }
  function collectDraw(example, frameIndex, name, assets, alpha = 1, clip = 'acting') {
    const frames = clip === 'blink' ? example.blinkFrames : example.frames;
    const layer = checkLayer(example, frames[indexWithin(frameIndex, 0, frames.length)], name);
    return { layer, image: assetImage(assets, layer.file), alpha };
  }
  function paint(ctx, draws) {
    if (!ctx || !ctx.canvas) throw new Error('A canvas 2D context is required.');
    const width = ctx.canvas.width, height = ctx.canvas.height;
    const scale = Math.min(width / RIG.width, height / RIG.height);
    const dx = (width - RIG.width * scale) / 2, dy = (height - RIG.height * scale) / 2;
    ctx.save();
    try {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      for (const { layer, image, alpha, composite } of draws) {
        if (alpha <= 0) continue;
        ctx.globalAlpha = alpha;
        ctx.globalCompositeOperation = composite || 'source-over';
        ctx.drawImage(image, dx + layer.x * scale, dy + layer.y * scale, layer.width * scale, layer.height * scale);
      }
    } finally { ctx.restore(); }
  }
  function drawFrame(ctx, example, index, assets, options = {}) {
    checkExample(example);
    const frameIndex = indexWithin(index);
    const draws = selectedLayers(options).map(name => collectDraw(example,
      indexWithin(options[name + 'Frame'], frameIndex), name, assets));
    paint(ctx, draws);
    return frameIndex;
  }
  // Trigger at rest or at a resting boundary; an acting glance has its own eye placement.
  function drawBlink(ctx, example, index, assets, options = {}) {
    checkBlink(example);
    const frameIndex = indexWithin(index, 0, BLINK_FRAME_COUNT);
    const draws = selectedLayers(options).map(name => name === 'mouth'
      ? collectDraw(example, indexWithin(options.mouthFrame), name, assets)
      : collectDraw(example, indexWithin(options[name + 'Frame'], frameIndex, BLINK_FRAME_COUNT), name, assets, 1, 'blink'));
    paint(ctx, draws);
    return frameIndex;
  }
  function drawTransition(ctx, fromExample, toExample, progress, assets, options = {}) {
    const step = transitionPlan(fromExample, toExample)[stepAtProgress(progress)];
    const draws = [];
    for (const name of selectedLayers(options)) {
      if (name !== 'mouth') {
        const example = step.eyes.side === 'source' ? fromExample : toExample;
        draws.push(collectDraw(example, step.eyes.frame, name, assets, 1, 'blink'));
      } else {
        draws.push(collectDraw(step.mouth.targetWeight ? toExample : fromExample, 0, name, assets));
      }
    }
    paint(ctx, draws);
    return step;
  }
  function resolveExpression(intent, manifest) {
    if (typeof intent !== 'string') throw new Error('Expression intent must be a string.');
    const token = intent.trim().toLowerCase().replace(/[ -]+/g, '_');
    const key = Object.prototype.hasOwnProperty.call(INTENTS, token) ? INTENTS[token]
      : Object.values(INTENTS).find(value => value === token);
    if (!key) throw new Error('Unknown expression intent: ' + intent);
    if (manifest) {
      const present = Array.isArray(manifest.groups) && manifest.groups.some(group => Array.isArray(group.examples) &&
        group.examples.some(example => (example.key || group.id + '/' + example.id) === key));
      if (!present) throw new Error('Expression is not present in this manifest: ' + key);
    }
    return key;
  }
  function resolveBase(baseUrl) {
    const documentBase = typeof document !== 'undefined' ? document.baseURI : undefined;
    const value = String(baseUrl || '.');
    return new URL(value.endsWith('/') ? value : value + '/', documentBase).href;
  }
  function browserImage(url, timeoutMs) {
    return new Promise((resolve, reject) => {
      if (typeof Image === 'undefined') { reject(new Error('Image decoding requires a browser.')); return; }
      const image = new Image();
      image.decoding = 'async';
      let finished = false;
      const timeout = setTimeout(() => finish(new Error('Image load timed out.')), timeoutMs);
      function finish(error) {
        if (finished) return;
        finished = true;
        clearTimeout(timeout);
        image.onload = null; image.onerror = null;
        if (error) { image.src = ''; reject(error); } else resolve(image);
      }
      image.onerror = () => finish(new Error('Image could not be loaded.'));
      image.onload = async () => {
        try {
          if (typeof image.decode === 'function') await image.decode();
          if (!image.naturalWidth || !image.naturalHeight) throw new Error('Decoded image is empty.');
          finish();
        } catch (error) { finish(error); }
      };
      image.src = url;
    });
  }
  function release(image) { if (image && typeof image.close === 'function') image.close(); }
  function releaseAll(images) { new Set(images.values()).forEach(release); images.clear(); }
  async function preload(manifest, baseUrl = '.', options = {}) {
    if (!manifest || !Array.isArray(manifest.groups) || !manifest.groups.length) throw new Error('Animation manifest has no groups.');
    const files = new Map();
    let first = null;
    for (const group of manifest.groups) {
      if (!Array.isArray(group.examples) || !group.examples.length) throw new Error('Animation group has no examples.');
      for (const example of group.examples) {
        validateExample(example);
        if (first) transitionPlan(first, example); else first = example;
        for (const frame of [...example.frames, ...example.blinkFrames]) {
          for (const name of LAYERS) {
            const layer = frame.layers[name];
            if (!layer) continue;
            const old = files.get(layer.file);
            if (old && (old.width !== layer.width || old.height !== layer.height)) {
              throw new Error('Conflicting dimensions for artwork: ' + layer.file);
            }
            files.set(layer.file, layer);
          }
        }
      }
    }
    const resolvedBase = resolveBase(baseUrl);
    const loader = options.loadImage || browserImage;
    const concurrency = options.concurrency === undefined ? 8 : options.concurrency;
    const timeoutMs = options.timeoutMs === undefined ? 30000 : options.timeoutMs;
    if (!Number.isInteger(concurrency) || concurrency <= 0 || concurrency > 16) throw new Error('Image concurrency must be an integer from 1 to 16.');
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('Image timeout must be greater than zero.');
    const resources = new Map();
    for (const [file, layer] of files) {
      const identity = typeof layer.sha256 === 'string' && /^[a-f0-9]{64}$/i.test(layer.sha256)
        ? 'sha256:' + layer.sha256.toLowerCase() : 'file:' + file;
      const old = resources.get(identity);
      if (old) {
        if (old.layer.width !== layer.width || old.layer.height !== layer.height) {
          throw new Error('Conflicting decoded dimensions for shared artwork: ' + file);
        }
        old.files.push(file);
      } else resources.set(identity, { file, layer, files: [file] });
    }
    const images = new Map(), entries = [...resources.values()], errors = [];
    let next = 0;
    async function worker() {
      while (next < entries.length) {
        const { file, layer, files: aliases } = entries[next++];
        let image;
        try {
          const url = new URL(file, resolvedBase);
          url.searchParams.set('asset', layer.sha256 || String(manifest.version || 3));
          image = await loader(url.href, timeoutMs);
          const width = image && (image.naturalWidth === undefined ? image.width : image.naturalWidth);
          const height = image && (image.naturalHeight === undefined ? image.height : image.naturalHeight);
          if (width !== layer.width || height !== layer.height) throw new Error('Decoded dimensions do not match manifest.');
          aliases.forEach(alias => images.set(alias, image));
        } catch (error) {
          release(image);
          errors.push({ file, message: error.message || String(error) });
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(concurrency, entries.length) }, worker));
    if (errors.length) {
      releaseAll(images);
      const error = new Error('Could not load ' + errors.length + ' animation layer(s): ' + errors.slice(0, 3).map(item => item.file).join(', '));
      error.assets = errors;
      throw error;
    }
    return {
      images, manifest, baseUrl: resolvedBase, disposed: false, decodedCount: entries.length,
      dispose() {
        if (this.disposed) return;
        this.disposed = true;
        releaseAll(this.images);
      }
    };
  }
  return Object.freeze({ LAYERS, FRAME_COUNT, BLINK_FRAME_COUNT, DEFAULT_FPS, RIG, INTENTS,
    preload, drawFrame, drawBlink, drawTransition, transitionPlan, frameAt, blinkAt,
    timeline, frameStartMs, cycleDurationMs, blinkDurationMs,
    stepAtProgress, validateExample, resolveExpression });
});
