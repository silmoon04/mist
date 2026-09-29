# Motion previews

`all-animations.gif` shows all 18 acting clips. Each group occupies a column, with three examples down the column. Each clip plays once at its authored speed, then waits at rest for the other clips before the grid repeats.

Each individual acting GIF contains the 12 composed poses. The `_blink.gif` files show separate blink clips with a neutral pause. The browser uses a 15 fps timebase with authored holds; GIF timing rounds cumulative boundaries to the format's 10 ms precision. Identical held poses may be merged by the encoder without shortening their exposure.

The transparent PNGs remain the source assets. GIF previews have 256 colours and a dark background. They do not demonstrate live speech synchronisation. `receipt.json` records input and output hashes and timings.
