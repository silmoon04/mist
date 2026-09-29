# Animation preview

Open the live MIST page and press **Animations** in the header. A compact tray keeps the face visible while you choose a face, an activity motion, or both. The face menu includes all 40 exact original face IDs; the activity menu is populated from the current hand-drawn v6 manifest. **Return to live** releases both selections. The two menus also have individual “Follow live” options.

Preview selection is local to this browser. On the hosted site, open the page after pairing; the preview works before starting voice or granting microphone access. It does not send an expression command to the server. Live expression and activity events continue while preview is active, then become visible when preview is released. If actual speech plays, the existing audio clock continues to drive mouth movement. Preview does not generate speech, transcripts, or activity events. If the activity assets fail to load, that menu stays disabled and the face menu remains available.

Checks:

```text
node --test brain/duplex/static/animation_picker.test.mjs
node brain/art_direction/artist_studio_20260916/reuse/handdrawn_v6/test_runtime.cjs
```

The UI detector reported one existing 2 px transcript accent border in `style.css`; it did not flag the new preview controls. The detector could not resolve the app's server-root stylesheet URL from the local HTML file. Visual browser inspection remains useful after the hosting release is built.
