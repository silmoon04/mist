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
