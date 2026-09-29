# MIST voice app

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

Build a fresh release from the private workspace with `python
brain/deploy/build_release.py`. The script copies an explicit source list and
only PNG files referenced by the face manifests. Review `release-manifest.json`
before publishing. It lists paths, sizes, and hashes without secret values.
