# MIST voice app

Open the public entry page at **https://silmoon04.github.io/mist/**. Live voice
requires the paired laptop to be online, awake, and running the private host;
the page shows when that host is available. Keep its pairing code private and
enter it only on the live MIST page.

This repository contains the MIST browser voice service and its face assets. It
does not include CAD models, study results, saved conversations, credentials, or
generated listener cue caches. The single robot assembly manifest is used for a
preview-only geometry identifier; this app does not actuate hardware.

Use Python 3.12. Install dependencies with `python -m pip install -r
requirements.txt`. Set the private environment variables listed in
`.env.example` on the service host. Start locally with `python
brain/duplex/live_studio.py --port 9053`. For remote access, terminate HTTPS at
a reverse proxy and pass `--remote-origin https://your-domain.example` plus a
private `--run-dir` outside the repository. The app writes runtime
state and conversation traces beneath `brain/results/` by default, which is
ignored by Git. Supply a persistent private run directory when deploying.

The Cerebras architectures need Cerebras, Deepgram, and ElevenLabs keys. The
Codex choices additionally require a signed-in Codex CLI on the service host;
they are optional and may be unavailable on ordinary cloud hosts. The service
can launch without provider keys, but those architectures will be unavailable.

The default **Qwen · memory + Flux** uses Flux for recognition, Qwen for replies,
a separate Qwen expression reader, and the MIST voice. A second background Qwen
client makes a short, sourced digest every four user turns. Replies also receive
recent verbatim turns and local retrieval, so a slow or failed digest does not
block a reply. Difficult tool work can run on a separate Codex Luna worker when
the host has a signed-in Codex CLI. Earlier architectures remain available for
comparison.

The face view's **Animations** button opens a local preview of all 40 faces and
nine activity motions. Choose either or both, then use **Return to live** to
resume automatic choices. This works without starting voice after pairing on
the hosted page. During a conversation, playback still drives the mouth.
Preview choices send no tool command and do not change the conversation state.

New sessions are saved on the service host, including sessions started from a
remote phone. The private run directory contains `sessions.sqlite3` for turns,
events, notes, preferences, and versioned summaries, plus `sessions/<id>/mic.wav`
and `assistant_generated.wav` for audio. The microphone file contains the PCM
received by the host; the generated voice file includes speech that may have
been interrupted before playback. Chunk metadata preserves timing and sequence
information. Neither file proves what a person heard. Recordings remain local
to the host, while recognition and voice generation still use their configured
providers. No automatic recording deletion is configured.

In the trial page, **Saved on this host** shows the selected session's notes and
audio without an export step. **Speech feedback** keeps verbatim speech central
to the digest rather than smoothing out wording. The original final transcript
and recording are retained in both modes; ASR errors may still need checking
against the audio. Explicitly saved preferences carry into new conversations;
other past-session material can be searched with the recall tool.

The checked-in smoke fixture is one synthetic Deepgram Aura2 Thalia audio case,
with its source and WAV hashes in `brain/deploy/fixtures/manifest.json`. It is
not a human recording. The remote protocol smoke runner is opt-in: see
`brain/deploy/LAPTOP_HOST.md`; its received audio and transcripts belong in a
private output directory. It checks the remote app and audio protocol with
synthetic input; it does not verify a real microphone, speaker, or robot.

Run the packaged offline checks with `python -B -m
brain.harness.test_live_studio_remote`, `python -B
brain/harness/test_live_studio.py`, `python -B
brain/harness/test_laptop_host.py`, and `python -B
brain/deploy/test_remote_e2e_helpers.py`.

Build a fresh release from the private workspace with `python
brain/deploy/build_release.py`. The script copies an explicit source list and
only PNG files referenced by the face manifests. Review `release-manifest.json`
before publishing. It lists paths, sizes, and hashes without secret values. Static
text is staged with LF line endings and `.gitattributes` keeps those bytes stable
in fresh Git clones. The supervisor updates `docs/endpoint.json` as tunnel
status changes; that dynamic file is intentionally excluded from static hashes.
