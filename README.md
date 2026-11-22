<p align="center">
  <img src="docs/readme/hero.gif" width="880" alt="MIST's animated face says hello, then turns happy, curious, surprised, smug, in love, suspicious, mischievous and sleepy, then shows thinking, reading and writing.">
</p>

<h1 align="center">MIST</h1>

<p align="center">A voice companion with a face.</p>

<p align="center">
  <a href="https://silmoon04.github.io/mist/">Live page</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/silmoon04/mist-robot">Robot body</a>
</p>

## Faces

<p align="center">
  <img src="docs/readme/faces.gif" width="880" alt="Twelve of MIST's 40 faces, blinking and talking: happy, curious, surprised, love, smug, mischief, proud, suspicious, angry, sad, sleepy and embarrassed.">
</p>

## How it works

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/readme/how-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/readme/how-light.png">
    <img src="docs/readme/how-light.png" width="880" alt="Sketch of one turn. You talk; Deepgram Flux hears; Qwen 3.8 on Cerebras thinks; ElevenLabs Flash speaks; the face shows it. You can interrupt at any time. Meanwhile, three helpers run beside the thinking step: a Qwen that picks the face, a Qwen that keeps notes, and Luna, through Codex, that researches.">
  </picture>
</p>

## Tools

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/readme/tools-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/readme/tools-light.png">
    <img src="docs/readme/tools-light.png" width="880" alt="Sketch of MIST's ten tools. Face: set_expression. Memory: remember, recall. Senses: get_sensor_snapshot. Body, previews only with no motors yet: preview_motion, pan_phone, stop_robot. Background work: start_background_task, background_task_status, cancel_background_task.">
  </picture>
</p>

The face shows what MIST is busy with.

<p align="center">
  <img src="docs/readme/activities.gif" width="880" alt="Eight small animations beside MIST's face: searching, reading files, writing code, writing, planning, calculating, connecting and checking results.">
</p>

## Run it

```sh
python -m pip install -r requirements.txt    # Python 3.12
python brain/duplex/live_studio.py --port 9053
```

Open `http://127.0.0.1:9053`. The faces need no keys. To talk, set the keys named in [`.env.example`](.env.example).

Hosting, saved data and tests are in the [guide](brain/deploy/GUIDE.md).
