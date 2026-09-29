# Animation preview

Open the MIST page and press **Animations**. The browse tray keeps the main face visible while you switch between **Faces** and **Activities**. Face groups can be filtered, every card has a live thumbnail, and the selected card is marked for assistive technology and by sight. The face grid contains all 40 original face IDs. The activity grid contains all nine motions in the hand-drawn v6 manifest.

Select any face or motion to preview it on the main MIST face. **Return to live** releases both selections; live expression and activity updates remain available underneath the preview. Opening the tray does not require pairing, voice, or microphone permission. It sends no commands to the server and creates no transcripts, speech, or activity events.

Thumbnails use the installed MIST renderers and shared decoded activity assets. Their runtimes are created only while a card is visible and the tray is open. A single 15 fps scheduler advances visible thumbnails; it stops when the tray or page closes, or when reduced motion is preferred. Closing or scrolling away destroys the card renderer. If the hand-drawn assets fail to load, the grid reports activity previews as unavailable and the main live face continues normally.

Checks:

```text
node --test brain/duplex/static/animation_picker.test.mjs
node brain/art_direction/artist_studio_20260916/reuse/handdrawn_v6/test_runtime.cjs
```
