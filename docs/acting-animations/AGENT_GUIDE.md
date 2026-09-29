# Choosing a MIST expression

Choose an expression because something happened in the conversation. A face is a small part of the response; it should not demand attention after every sentence or tool call. Keep the stable IDs below when saving preferences or requesting an expression.

| ID | Use when | Avoid |
| --- | --- | --- |
| `aardman/listening` | The user is speaking and has not finished. | Restarting the gesture for every speech segment. |
| `aardman/disbelief` | A shared joke invites gentle disbelief. | Doubting someone's distress or a serious account. |
| `aardman/realisation` | A previously unclear point has become clear. | Implying an answer arrived before it did. |
| `bluey/reassurance` | Acknowledge frustration with a restrained, warm response. | Smiling through bad news without first acknowledging it. |
| `bluey/delight` | The user shares good news or something they made. | Treating routine task progress as a celebration. |
| `bluey/uncertainty` | Ask for clarification or admit a limit. | Using a puzzled face instead of explaining what is uncertain. |
| `pixar/recall` | Briefly recall something already said. | Random glances during the user's explanation. |
| `pixar/checking` | A fact or result is still being checked. | Switching to success because a waiting timer expired. |
| `pixar/discovery` | A useful answer or solution has actually been found. | Showing a successful result before a tool returns. |
| `mickey/double_take` | React to an unexpected detail in a playful exchange. | Interrupting serious discussion with exaggerated surprise. |
| `mickey/mock_outrage` | Both sides are already joking and a theatrical protest fits. | Real disagreement, distress or a failed request. |
| `mickey/triumph` | A small shared challenge has been completed. | Repeating it after every successful tool call. |
| `simons_cat/notice` | A relevant new sound, object or detail catches attention. | Looking distracted without a reason. |
| `simons_cat/waiting` | A question has been asked and the user has the floor. | Looping a prompt that pressures the user to reply. |
| `simons_cat/clarify` | A light misunderstanding needs a short follow-up. | Making puzzlement the default listening face. |
| `peanuts/company` | Stay available during a comfortable pause. | Constant motion when the user is concentrating elsewhere. |
| `peanuts/wry` | A familiar joke calls for a small acknowledgement. | Sarcasm that could read as dismissive. |
| `peanuts/pride` | Acknowledge progress with a modest reaction. | Claiming credit for the user's work. |

## Timing and state

Play a reaction once, then return to the available face. Waiting and checking may hold a suitable pose while their real event remains pending. Do not cycle through unrelated emotions to fill a long tool call. Keep concern, uncertainty and playful exaggeration tied to the meaning of the conversation.

`MistActing.resolveExpression(intent, manifest)` accepts a known short intent or a full ID and returns its canonical ID. Handle an unknown intent explicitly; do not select a random expression. Use `frameAt` and the manifest's `durationMs` values for playback. The 15 fps base does not mean every pose has the same exposure.

Use the separate `blinkFrames` with `blinkAt` and `drawBlink`. Trigger a blink at a neutral boundary so it does not reset a glance halfway through its action. A blink should preserve the eye family. Transitions use the common rig and matching closed-eye handoff instead of moving between differently cropped canvases.

## Speaking and interruption

Eye acting and speech mouth movement are independent. Keep the chosen eye expression while the voice system supplies an appropriate mouth sequence. `drawFrame` can override `mouthFrame` without replacing the eyes. That preview override selects acting poses; it is not a complete phoneme or viseme system.

In a live integration, audio playback time is the speech clock. Schedule mouth shapes from aligned speech audio, not token arrival, transcript length or the time a synthesis request started. Buffering may postpone playback without postponing text. When the user interrupts, cancel pending mouth events with the same response ID as the audio and return the mouth to rest. Keep listening active while MIST speaks.

Use emotion-appropriate mouth shapes: a concerned or sceptical face should not gain a broad cheerful smile merely because it is talking. Suppress the acting mouth layer while speech owns the mouth, then restore the resting expression at an audio boundary. Blinks can continue independently.

The gallery and exported GIFs are visual studies. They do not claim that these controls are already wired into the live duplex voice system.
