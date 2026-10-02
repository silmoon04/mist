# Geraldine MIST animation pack

Thirteen presets share six eye families and three speech-mouth families. Each family has twelve native drawings. Corrected concern and laughing details replace the weak shapes in playback. All PNGs retain their ImageGen source bytes.

## Review

Open `index.html` through an HTTP server. Choose a face, then use:

- **Idle** for ordinary blinking and small glances.
- **Reaction** to inspect its twelve eye drawings in sequence.
- **Speak** for one of three recorded MIST samples. **Loop all** makes the whole grid speak to the same audio and repeats the sample.
- **Transition tour** to visit the thirteen presets. Eyes pass through drawn closed lids; mouth families switch at a closure or quiet boundary when possible.
- **Pause** to freeze the current playback position. **Stop** cancels speech and returns to the preset's resting face.
- **Frame** to inspect any of twelve eye/mouth cel pairs. These are component drawings, rather than twelve phonemes played in a fixed speaking order.
- **Keep**, **Revise**, and the note field to record feedback. **Export notes** offers a JSON download and a copyable version. Notes stay in this browser until you export them.

## Model integration

Use `face-tool.json` as the tool schema. The model chooses one `face_id` for a conversational beat. Keep the expression while the intent remains the same; a new word alone is not a reason to change it.

| Face ID | Use |
| --- | --- |
| attentive | Available and engaged |
| considering | Weighing a possibility |
| skeptical | Gently challenging a claim |
| amused | Enjoying a joke |
| concerned | Acknowledging a difficulty |
| quiet-listening | Giving the user room to speak |
| friendly-curiosity | Asking an interested follow-up |
| wry-teasing | Light teasing when welcome |
| reassuring | Offering comfort |
| quietly-pleased | Small satisfaction or approval |
| laughing | A brief strong reaction to humour |
| doubtful | Uncertain about a conclusion |
| eager-delight | Enthusiastic good news |

The JS modules expose `createFaceState`, `requestPreset`, `sampleFace`, `AtlasRenderer`, `SpeechPlayer`, and `SharedClock`. Load the manifest and native sheets, then call `requestPreset` with the tool's face ID. Pass the latest audible speech position and normalized cues to `sampleFace`. Render the returned pose at 15 fps. A cancellation calls `SpeechPlayer.stop()` and `stopFace()`.

Asset paths are relative to this folder. When mounting the modules in another app, resolve each manifest asset path against the pack URL. The model chooses the expression; audio cues choose the mouth drawing. This review player is not connected to the live brain yet.

## Speech cues

Two samples use retained provider caption onsets to bound words, then estimated pronunciation timing inside those words. The third uses audio energy. These are not measured phoneme timestamps or a human listening score. The player follows audible Web Audio time and closes speaking mouths during silent gaps.

The optional Python module is in `speech-module/duplex`. Supply the dictionary explicitly when using it outside the project:

```python
import json
from duplex.phoneme_cues import phoneme_mouth_cues

dictionary = json.load(open("speech-module/cmudict-subset.json", encoding="utf-8"))["entries"]
cues, source = phoneme_mouth_cues(
    pcm_chunk, provider_alignment, audio_offset=audio_offset,
    pronunciations=dictionary,
)
```

The 319-word English subset falls back for unknown or incomplete words. Its default selects the first pronunciation; use an explicit override for ambiguous words or accents. Keep the existing provider clock conversion and packet slicing when integrating it. The CMU license is bundled.
