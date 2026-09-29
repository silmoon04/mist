(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MistActing = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const LAYERS = Object.freeze(['leftEye', 'rightEye', 'mouth']);
  const FRAME_COUNT = 12;
  const DEFAULT_FPS = 15;

  function exampleName(example) {
    return example && (example.key || example.id) || 'unnamed example';
  }

  function checkExample(example) {
    if (!example || !Number.isFinite(example.width) || example.width <= 0 ||
        !Number.isFinite(example.height) || example.height <= 0) {
      throw new Error('Animation needs positive width and height.');
    }
    if (!Array.isArray(example.frames) || example.frames.length !== FRAME_COUNT) {
      throw new Error(exampleName(example) + ' needs exactly 12 frames.');
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
        layer.x + layer.width > example.width || layer.y + layer.height > example.height) {
      throw new Error(exampleName(example) + ' has invalid ' + name + ' bounds.');
    }
    return layer;
  }

  function validateExample(example) {
    checkExample(example);
    example.frames.forEach(frame => LAYERS.forEach(name => checkLayer(example, frame, name)));
    return example;
  }

  function indexWithin(value, fallback = 0) {
    if (value === undefined || value === null) return fallback;
    if (!Number.isFinite(value)) throw new Error('Frame index must be a finite number.');
    return Math.max(0, Math.min(FRAME_COUNT - 1, Math.floor(value)));
  }

  function frameAt(elapsedMs, example, fps = DEFAULT_FPS) {
    if (!Number.isFinite(fps) || fps <= 0) throw new Error('FPS must be greater than zero.');
    if (!Number.isFinite(elapsedMs)) throw new Error('Elapsed time must be finite.');
    const restMs = example && example.restMs !== undefined ? example.restMs : 1000;
    if (!Number.isFinite(restMs) || restMs < 0) throw new Error('Rest duration cannot be negative.');
    const activeMs = FRAME_COUNT * 1000 / fps;
    const localMs = Math.max(0, elapsedMs) % (activeMs + restMs);
    if (localMs >= activeMs) return 0;
    // The epsilon keeps exact frame boundaries stable under floating-point division.
    return Math.min(FRAME_COUNT - 1, Math.floor(localMs * fps / 1000 + 1e-9));
  }

  function stepAtProgress(progress) {
    if (!Number.isFinite(progress)) throw new Error('Transition progress must be finite.');
    return Math.min(FRAME_COUNT - 1, Math.floor(Math.max(0, Math.min(1, progress)) * FRAME_COUNT));
  }

  // Hold each fully closed pose for one drawing before opening the new expression.
  // The final settle frame avoids playing the target's unrelated acting beat.
  const EYE_STEPS = Object.freeze([
    ['source', 0, 'source'],
    ['source', 1, 'closing'],
    ['source', 2, 'closing'],
    ['source', 3, 'closed-source'],
    ['source', 3, 'closed-source'],
    ['target', 3, 'closed-handoff'],
    ['target', 4, 'opening'],
    ['target', 4, 'opening'],
    ['target', 5, 'opening'],
    ['target', 11, 'settling'],
    ['target', 0, 'target'],
    ['target', 0, 'target']
  ].map(Object.freeze));
  const MOUTH_MIX = Object.freeze([0, 0, 0, 0, 0.15625, 0.5, 0.84375, 1, 1, 1, 1, 1]);

  function transitionPlan(fromExample, toExample) {
    checkExample(fromExample);
    checkExample(toExample);
    return EYE_STEPS.map(([side, frame, phase], index) => ({
      index,
      phase,
      eyes: { side, frame },
      sourceFrame: side === 'source' ? frame : null,
      targetFrame: side === 'target' ? frame : null,
      mouth: { sourceFrame: 0, targetFrame: 0, targetWeight: MOUTH_MIX[index] }
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
    const img = assets.images instanceof Map ? assets.images.get(file) : assets.images && assets.images[file];
    if (!img) throw new Error('Missing decoded artwork: ' + file);
    return img;
  }

  function collectDraw(example, frameIndex, name, assets, alpha = 1) {
    const layer = checkLayer(example, example.frames[indexWithin(frameIndex)], name);
    return { example, layer, image: assetImage(assets, layer.file), alpha };
  }

  function paint(ctx, draws) {
    if (!ctx || !ctx.canvas) throw new Error('A canvas 2D context is required.');
    const width = ctx.canvas.width;
    const height = ctx.canvas.height;
    ctx.save();
    try {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.globalCompositeOperation = 'source-over';
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      for (const draw of draws) {
        if (draw.alpha <= 0) continue;
        const { example, layer, image, alpha } = draw;
        const scale = Math.min(width / example.width, height / example.height);
        const dx = (width - example.width * scale) / 2;
        const dy = (height - example.height * scale) / 2;
        ctx.globalAlpha = alpha;
        ctx.globalCompositeOperation = draw.composite || 'source-over';
        ctx.drawImage(image, dx + layer.x * scale, dy + layer.y * scale,
          layer.width * scale, layer.height * scale);
      }
    } finally {
      ctx.restore();
    }
  }

  function drawFrame(ctx, example, index, assets, options = {}) {
    checkExample(example);
    const frameIndex = indexWithin(index);
    const draws = selectedLayers(options).map(name => {
      const override = options[name + 'Frame'];
      return collectDraw(example, indexWithin(override, frameIndex), name, assets);
    });
    paint(ctx, draws);
    return frameIndex;
  }

  function drawTransition(ctx, fromExample, toExample, progress, assets, options = {}) {
    const index = stepAtProgress(progress);
    const step = transitionPlan(fromExample, toExample)[index];
    const draws = [];
    for (const name of selectedLayers(options)) {
      if (name !== 'mouth') {
        const example = step.eyes.side === 'source' ? fromExample : toExample;
        draws.push(collectDraw(example, step.eyes.frame, name, assets));
      } else {
        const mix = step.mouth.targetWeight;
        if (mix < 1) draws.push(collectDraw(fromExample, step.mouth.sourceFrame, name, assets, 1 - mix));
        if (mix > 0) {
          const targetMouth = collectDraw(toExample, step.mouth.targetFrame, name, assets, mix);
          // Add weighted premultiplied pixels: source-over would dim overlapping
          // opaque mouth pixels to 75% opacity at the midpoint of the blend.
          if (mix < 1) targetMouth.composite = 'lighter';
          draws.push(targetMouth);
        }
      }
    }
    paint(ctx, draws);
    return step;
  }

  function resolveBase(baseUrl) {
    const documentBase = typeof document !== 'undefined' ? document.baseURI : undefined;
    const value = String(baseUrl || '.');
    return new URL(value.endsWith('/') ? value : value + '/', documentBase).href;
  }

  function browserImage(url, timeoutMs) {
    return new Promise((resolve, reject) => {
      if (typeof Image === 'undefined') {
        reject(new Error('Image decoding requires a browser.'));
        return;
      }
      const img = new Image();
      img.decoding = 'async';
      let finished = false;
      const timeout = setTimeout(() => finish(new Error('Image load timed out.')), timeoutMs);
      function finish(error) {
        if (finished) return;
        finished = true;
        clearTimeout(timeout);
        img.onload = null;
        img.onerror = null;
        if (error) reject(error);
        else resolve(img);
      }
      img.onerror = () => finish(new Error('Image could not be loaded.'));
      img.onload = async () => {
        try {
          if (typeof img.decode === 'function') await img.decode();
          if (!img.naturalWidth || !img.naturalHeight) throw new Error('Decoded image is empty.');
          finish();
        } catch (error) { finish(error); }
      };
      img.src = url;
    });
  }

  async function preload(manifest, baseUrl = '.', options = {}) {
    if (!manifest || !Array.isArray(manifest.groups) || !manifest.groups.length) {
      throw new Error('Animation manifest has no groups.');
    }
    const files = new Map();
    for (const group of manifest.groups) {
      if (!Array.isArray(group.examples) || !group.examples.length) throw new Error('Animation group has no examples.');
      for (const example of group.examples) {
        validateExample(example);
        example.frames.forEach(frame => LAYERS.forEach(name => {
          const layer = frame.layers[name];
          const old = files.get(layer.file);
          if (old && (old.width !== layer.width || old.height !== layer.height)) {
            throw new Error('Conflicting dimensions for artwork: ' + layer.file);
          }
          files.set(layer.file, layer);
        }));
      }
    }
    const resolvedBase = resolveBase(baseUrl);
    const loader = options.loadImage || browserImage;
    const concurrency = Math.max(1, Math.min(16, Math.floor(options.concurrency || 8)));
    const timeoutMs = options.timeoutMs || 30000;
    const images = new Map();
    const entries = [...files];
    const errors = [];
    let next = 0;
    async function worker() {
      while (next < entries.length) {
        const [file, layer] = entries[next++];
        try {
          const img = await loader(new URL(file, resolvedBase).href, timeoutMs);
          const width = img.naturalWidth === undefined ? img.width : img.naturalWidth;
          const height = img.naturalHeight === undefined ? img.height : img.naturalHeight;
          if (width !== layer.width || height !== layer.height) {
            throw new Error('Decoded dimensions do not match manifest.');
          }
          images.set(file, img);
        } catch (error) {
          errors.push({ file, message: error.message || String(error) });
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(concurrency, entries.length) }, worker));
    if (errors.length) {
      images.forEach(img => { if (typeof img.close === 'function') img.close(); });
      const error = new Error('Could not load ' + errors.length + ' animation layer(s): ' + errors.slice(0, 3).map(item => item.file).join(', '));
      error.assets = errors;
      throw error;
    }
    return {
      images, manifest, baseUrl: resolvedBase, disposed: false,
      dispose() {
        if (this.disposed) return;
        this.disposed = true;
        this.images.forEach(img => { if (typeof img.close === 'function') img.close(); });
        this.images.clear();
      }
    };
  }

  return Object.freeze({ LAYERS, FRAME_COUNT, DEFAULT_FPS, preload, drawFrame,
    transitionPlan, drawTransition, frameAt, stepAtProgress, validateExample });
});
