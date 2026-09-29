# MIST acting animations

Open https://silmoon04.github.io/mist/acting-animations/

The gallery contains 18 animations, each with 12 ImageGen drawings. Playback defaults to 15 fps, followed by a short rest. Grid shows all faces together; Review shows the selection and notes fields. Inspect opens frame stepping, layer visibility and separate mouth timing. If reduced motion is enabled, press Play all to start.

## Assets

- source/: 18 original transparent ImageGen sprite sheets.
- frames/: 216 registered full-face frames.
- layers/: 648 separate left-eye, right-eye and mouth PNGs.
- contact/: full-size frame sheets and filmstrips.
- previews/: looping GIFs for each example and the entire grid.
- prompts/: generation and correction prompts.
- manifest.json: source hashes, crop coordinates, layer placement, timing and intended use.
- transition_plans.json: 12-step recipes for every directed change between the 18 faces.
- runtime.js: layer rendering, playback timing and reusable face transitions.

The compiler applies integer translations to keep the generated drawings registered. It does not stretch or rotate eyes. Layer extraction preserves visible source pixels; disconnected shapes with overlapping bounding rectangles use component masks. Original generated sheets remain unchanged.

Transitions reuse the source face's closing frames and the destination face's opening frames. Mouths blend independently while the eyes are closed. These are 12 composite steps built from the generated layers, not hundreds of separately generated strips.

The three review pose choices still mean Setup, Change and Settle. They map to frames 0, 7 and 11. Existing selections and notes remain compatible with the original pose review.

## Review and reuse

Selections and notes save in the current browser. Use Export review or Copy review link to send them back or move to another device. There is no review database or automatic sync.

Serve an extracted pack with a static HTTP server, then open index.html. The viewer fetches its local JSON and PNG assets.

Use the full group/example key when choosing an animation, for example aardman/listening or pixar/checking. The manifest's when and notes fields describe suitable contexts and cautions. A thinking or checking expression should follow real task state; a success expression should follow a confirmed result.

These are review animations. The separated mouths are expressive acting drawings, not a phoneme-complete speech library. Live voice assets have not been replaced, and this preview does not claim audio alignment.
