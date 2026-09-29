# MIST acting studies

These are proposed experiments drawn from the reference research. They are not claims about the games' exact frame counts or timing, and they are not a new approved MIST style.

## References to start with

| Reference | What to study | What to try in MIST |
| --- | --- | --- |
| [SmallBu / Later Alligator](https://community.wacom.com/en-gb/later-alligator-a-qa-with-the-small-buteras/) | Reactions authored around the character's conversation nodes; strong directional eyes and distinct attitudes. | Give recognition, uncertainty and a correction different poses, even when all three use the same basic eye family. |
| [Ian McConville / Slime Rancher](https://store.steampowered.com/news/posts/?appids=433340&enddate=1576525914&feed=steam_community_announcements) | A simple surface with separable eye and mouth treatment and visibly different emotions. | Retain a separate mouth layer, but give sad, doubtful and irritated speech their own resting and open shapes. |
| [Marlowe Dobbe / Dicey Dungeons](https://marlowedobbe.com/dicey-dungeons) | The contrast between Lady Luck's shaped eyelids and the warrior's open excitement. | Make eyelids and gaze establish the attitude before the mouth starts speaking. A small mouth can remain expressive. |
| [Tina Nawrocki / Cuphead](https://tinanawrocki.blogspot.com/2017/10/cuphead-hopus-pocus-magician-rabbit.html) | Authored mouth and eye extremes that preserve the character's identity. | Draw useful transition poses and blink closures for each eye family. Avoid forcing every change through a resized neutral drawing. |
| [Yugo Limbo and Day Lane / Smile For Me](https://limbolane.com/games/smile-for-me/press) | Responses to conversational input, rather than a decorative idle animation. | Separate an acknowledgement from a disagreement or a request to repeat something. Keep each response legible without sound. |
| [Rick Lico / Moss](https://blog.playstation.com/2018/04/09/the-magic-of-animation-in-moss-how-polyarc-brought-quill-to-life/) | Acknowledging the player through attention and gestures. | When the user addresses MIST, visibly direct attention toward them; avoid continuing an unrelated thinking loop. |
| [Pip Williamson / Thank Goodness You're Here!](https://www.williamsonpip.co.uk/project/thank-goodness-youre-here) | Drawn roughs and replacement sprites where engine transforms are insufficient. | Test a rough acting sequence first. Keep a replacement drawing wherever interpolation weakens the expression. |

## What needs to change in our approach

The previous MIST work has two reported problems: inconsistent drawings in the middle of animations, and mouth motion that makes less cheerful expressions look happy. More intermediate frames will not fix either problem on their own. The expression design and the sequence need to be checked separately.

Keep MIST's familiar cyan marks and dark screen while trying the acting choices above. A game can be a reference for attention, attitude or pose contrast without importing its character anatomy or drawing style.

For the next study, make a few excellent held expressions first: attentive warmth, neutral interest, doubt, mild frustration, sadness and recognition. Check all of them with the same short spoken line. Then add blinking, speech and expression changes while preserving that attitude. This is a proposed test order, not a requirement to replace the existing 40 faces.

## Playback tests after choosing a direction

| Sequence | The face should communicate | Common failure to watch for |
| --- | --- | --- |
| User starts talking while MIST is speaking | Attention shifts toward the user; listening remains distinct from speech. | The mouth keeps moving after interrupted audio stops. |
| "Let me check that" followed by a file read | Acknowledgement, then a distinct working pose, then a result reaction. | A loop repeats the acknowledgement or smiles throughout an uncertain task. |
| "I'm not sure" followed by clarification | Doubt without looking hostile or delighted. | An ordinary happy speech mouth overwhelms the doubtful eyes. |
| A successful task | Recognition and modest satisfaction. | A large celebration happens after every small tool call. |
| A tool fails and MIST explains the issue | Concern, composure and a useful explanation. | The face stays cheerful or turns exaggeratedly angry. |
| A quiet pause | A stable, attentive pose with occasional variation. | Every feature keeps moving, making the character look restless. |

Review each sequence at actual phone size, with audio, silently, and frame by frame. Check the eye centers, eyelid direction, mouth baseline, silhouette and loop boundary. A blink must close in its own eye's coordinate system; a tilted eye should not appear to rotate as it closes.

## Guidance for the model and renderer

This is a proposed integration contract for later implementation:

- The model chooses an intention such as acknowledgement, uncertainty, reassurance or satisfaction. It does not choose individual animation frames.
- The renderer combines that intention with speaking, listening or working state. Speech timing follows audible playback, including pauses and interruption.
- Eyelids, gaze, emotion and speech can share components, but a sad or irritated face keeps its own mouth character while speaking.
- Tool categories can select small activity animations. They should not overwrite the conversational attitude or switch the whole face on every tool event.
- Expression changes should settle into a readable pose. Holds and a few meaningful changes are worth testing alongside longer sequences; smoothness alone is not the quality target.

Use the board's Save and note controls to select the references and explain what appeals to you. The next drawing brief can then name specific poses, reactions and transitions instead of asking for an unspecified "better" animation.
