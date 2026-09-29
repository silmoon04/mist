# Acting animation previews

`all-animations.gif` plays all 18 examples together in six columns and three rows. Each column is one inspiration group; each row is that group's corresponding example. The group folders contain the 18 individual previews.

Each GIF contains all 12 generated drawings and loops indefinitely. Timing is 15 fps on average: GIF's 10 ms precision is represented with 70, 60, 70 ms frame durations. Frame zero includes a 1000 ms rest, giving a total loop length of 1800 ms.

Previews mechanically resize and composite the validated frame PNGs on a dark stage. The original transparent PNGs and separate eye/mouth layers remain unchanged. GIF is a review format with a 256-colour palette, not the production asset format. These examples are not connected to live speech.

`receipt.json` records source and output SHA-256 hashes, canvas sizes and verified encoded frame counts and timings. Run `python build_motion_previews.py` from the study directory to rebuild after the manifest is complete and asset checks pass.
