"""Build the small, reviewable MIST hosting source tree.

Only explicit runtime sources and assets named by the two face manifests are
copied. Never copy a whole source directory into a public release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


PROJECT = Path(__file__).resolve().parents[2]
BRAIN = PROJECT / "brain"
DEFAULT_OUTPUT = PROJECT / "output" / "mist-hosting-release"
DRAWN = "art_direction/artist_studio_20260916/reuse/drawn"
HANDDRAWN = "art_direction/artist_studio_20260916/reuse/handdrawn_v6"

# Relative to the release root. The lone design file is read at startup for a
# preview-only geometry hash; no CAD models or build products are included.
RELEASE_FILES = [
    "brain/duplex/__init__.py",
    *[f"brain/duplex/{name}.py" for name in (
        "live_studio", "server", "native", "runtime", "tts", "conversion",
        "background", "affect_director", "affect_controller", "expression_policy",
        "expression_requests", "memory_requests", "motion_requests", "trial_traces",
        "debugging", "sensors", "conversation_policy", "listener_feedback",
        "listener_backchannels", "listener_voice", "lipsync", "phrasing",
        "remote_access", "session_store", "session_memory", "database_traces", "turn_policy", "playback_receipts", "review_data",
    )],
    "brain/duplex/face_map.json", "brain/duplex/persona.txt",
    *[f"brain/duplex/static/{name}" for name in (
        "index.html", "style.css", "app.js", "playback.js", "delivery.js",
        "expression_policy.js", "activity_state.js", "listener_cue.js",
        "user_transcript_state.mjs", "capture-worklet.js", "debug.html",
        "debug.js", "expressions.html", "expressions.js", "inspect.css",
        "trials.html", "trials.css", "trials.js", "trial_review.mjs", "speech_face_cues.mjs", "speech_face_cues.test.mjs", "sample.wav", "trial_memory.mjs", "microphone_signal.mjs",
        "animation_picker.mjs", "animation_picker.test.mjs", "ANIMATION-PREVIEW.md",
        "speech_status.mjs", "speech_status.test.mjs", "favicon.svg", "fonts/Outfit.woff2", "fonts/OFL.txt",
    )],
    "brain/ui/static/face_runtime.js", "brain/ui/static/drawn_face_renderer.js",
    "brain/face_assets/app/app_data.js",
    "brain/benchmarks/naturalness/cascade_voice.py",
    "brain/benchmarks/naturalness/cerebras_client.py",
    "brain/benchmarks/naturalness/streaming_voice_20260930.py",
    "brain/benchmarks/naturalness/streaming_asr_20260930.py",
    # The public Cerebras offline suite imports objective_checks from runner.
    "brain/benchmarks/naturalness/runner.py",
    "brain/harness/pi_client.py", "brain/harness/codex_client.py",
    "brain/harness/codex_tool_bridge.mjs",
    "brain/extensions/robot-tools.ts", "brain/extensions/grounding.ts",
    "brain/art_direction/artist_studio_20260916/reuse/runtime/playback.js",
    "brain/harness/response_format.py", "brain/harness/eval_luna_conversations.py",
    "brain/harness/test_laptop_host.py", "brain/harness/test_live_studio.py",
    "brain/harness/test_live_studio_remote.py",
    "brain/harness/test_start_laptop_host.ps1",
    "brain/harness/test_remote_voice_recovery.mjs",
    "brain/harness/test_host_launcher.mjs",
    "brain/harness/test_session_voice_options.py",
    "brain/harness/test_session_store.py", "brain/harness/test_session_memory.py",
    "brain/harness/test_database_traces.py", "brain/harness/test_memory_requests.py",
    "brain/harness/test_playback_receipts.py",
    "brain/harness/test_affect_director.py", "brain/harness/test_conversion_completion.py", "brain/harness/test_expression_app.mjs",
    "brain/harness/test_session_review.py", "brain/harness/test_expression_requests.py",
    "brain/harness/test_trial_review.mjs",
    "brain/duplex/static/trial_memory.test.mjs", "brain/duplex/static/microphone_signal.test.mjs",
    "brain/benchmarks/naturalness/test_cerebras_client.py",
    *[f"brain/harness/{name}" for name in (
        "test_affect_studio.py", "test_affect_controller.py", "test_background.py",
        "test_codex_client.py", "test_streaming_tts.py", "test_lipsync.py", "test_phrasing.py",
        "test_tts_expressive_protocol_20260929.py", "test_duplex_playback.mjs",
        "test_affect_delivery_20261001.py", "test_voice_delivery_20261001.py",
        "test_speech_stream_stress.mjs", "test_face_runtime_soak.cjs",
        "test_affect_edge_cases.py", "test_duplex_background_continuity.py",
        "test_astra_continuity_review.mjs", "test_waveform_raster_clearance.cjs",
        "test_tts_interrupt_close_20261002.py",
        "test_astra_tts_close_review.py",
        "test_voice_recovery.py",
    )],
    *[f"brain/benchmarks/naturalness/{name}" for name in (
        "quality_gate_20260929.py", "turn_gap_benchmark_20260929.py",
        "voice_quality_20260929.py", "test_streaming_voice_20260930.py",
        "voice_expressive_20261001.py", "affect_delivery_20261001.py", "voice_transcript_20261001.py",
        "test_voice_expressive_benchmark_20261001.py",
        "jev_turn_policy_20260930.py", "jev_comparison_20260929.py",
        "robust_conversation_20260929/cases.json", "robust_conversation_20260929/README.md",
        "robust_conversation_20260929/runner.py", "robust_conversation_20260929/test_runner.py",
    )],
    "brain/deploy/fixtures/manifest.json",
    "brain/deploy/fixtures/audio/audio_phrase_seam-1.wav",
    f"brain/{DRAWN}/atlas.json", f"brain/{HANDDRAWN}/manifest.json",
    f"brain/{HANDDRAWN}/runtime.js", f"brain/{HANDDRAWN}/test_runtime.cjs",
    f"brain/{HANDDRAWN}/test_listening_waveform.cjs",
    "design/hexapod_phone_quad_r5_20260912/cad/output/assembly_manifest.json",
]
DEPLOY_FILES = (
    "laptop_host.py",
    "start_laptop_host.ps1",
    "LAPTOP_HOST.md",
    "test_remote_e2e.py",
    "test_remote_e2e_helpers.py",
)
PAGE_FILES = ("index.html", "style.css", "app.js", ".nojekyll", "favicon.svg", "fonts/Outfit.woff2", "fonts/OFL.txt")
TEXT_SUFFIXES = {".py", ".js", ".ts", ".mjs", ".cjs", ".html", ".css", ".json", ".txt", ".md", ".ps1"}
TEXT_NAMES = {".env.example", ".gitignore", ".gitattributes"}


def referenced_assets(manifest: Path, field: str) -> set[str]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    found: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get(field), str) and value[field].lower().endswith(".png"):
                found.add(value[field])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data["assets"])
    for name in found:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or not (manifest.parent / path).is_file():
            raise ValueError(f"Invalid or missing asset reference in {manifest.name}: {name}")
    return found


def file_list() -> list[str]:
    paths = set(RELEASE_FILES)
    for folder, manifest, field in (
        (DRAWN, "atlas.json", "file"),
        (HANDDRAWN, "manifest.json", "file"),
    ):
        source = BRAIN / folder / manifest
        paths.update(f"brain/{folder}/{name}" for name in referenced_assets(source, field))
    return sorted(paths)


def private_values() -> list[str]:
    source = PROJECT / ".env"
    if not source.is_file():
        return []
    values = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        if re.search(r"KEY|SECRET|PASSWORD|TOKEN", name, re.I):
            value = value.strip().strip("\"'")
            if len(value) >= 8:
                values.append(value)
    return values


def check_source(path: Path, secrets: list[str] | None = None) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or linked release file: {path.relative_to(PROJECT)}")
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError(f"Oversized release file: {path.relative_to(PROJECT)}")
    if path.suffix.lower() in {".py", ".js", ".ts", ".mjs", ".html", ".css", ".json", ".txt"}:
        data = path.read_text(encoding="utf-8")
        # Check for literal credentials and machine-local paths without echoing
        # the matched value to stdout or the generated manifest.
        patterns = (
            r"(?im)^\s*(?:[A-Z_]*(?:API_KEY|SECRET|PASSWORD|TOKEN))\s*=\s*['\"][^'\"\s#]{8,}['\"]",
            r"(?i)(?:sk-[A-Za-z0-9]{20,}|xi-[A-Za-z0-9]{20,})",
            r"(?i)[A-Z]:\\Users\\[^\\\s]+\\",
        )
        if any(re.search(pattern, data) for pattern in patterns):
            raise ValueError(f"Possible credential or local path in: {path.relative_to(PROJECT)}")
        if any(value in data for value in (secrets or [])):
            raise ValueError(f"Private environment value found in: {path.relative_to(PROJECT)}")


def release_bytes(source: Path) -> bytes:
    """Match the LF bytes Git checks out regardless of core.autocrlf."""
    data = source.read_bytes()
    if source.suffix.lower() in TEXT_SUFFIXES or source.name in TEXT_NAMES:
        return data.replace(b"\r\n", b"\n")
    return data


def copy_release(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(release_bytes(source))


def build(output: Path, dry_run: bool) -> dict:
    paths = file_list()
    secrets = private_values()
    files = []
    for relative in paths:
        source = PROJECT / relative
        check_source(source, secrets)
        payload = release_bytes(source)
        files.append({"path": relative.replace("\\", "/"), "bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest()})
    report = {"files": files, "file_count": len(files),
              "total_bytes": sum(item["bytes"] for item in files),
              "mutable_files": ["docs/endpoint.json"],
              "hash_policy": "Static text uses LF bytes; binary files use original bytes. Dynamic endpoint is excluded."}
    if not dry_run:
        output = output.resolve()
        if output == PROJECT or not output.is_relative_to(PROJECT / "output"):
            raise ValueError("Release output must be beneath project/output")
        for item in files:
            target = output / item["path"]
            copy_release(PROJECT / item["path"], target)
        (output / "brain" / "deploy").mkdir(parents=True, exist_ok=True)
        copy_release(Path(__file__), output / "brain" / "deploy" / "build_release.py")
        copy_release(BRAIN / "deploy" / "requirements.txt", output / "requirements.txt")
        copy_release(BRAIN / "deploy" / ".env.example", output / ".env.example")
        copy_release(BRAIN / "deploy" / "README.md", output / "README.md")
        for name in DEPLOY_FILES:
            source = BRAIN / "deploy" / name
            if source.is_file():
                check_source(source, secrets)
                copy_release(source, output / "brain" / "deploy" / name)
        docs = output / "docs"
        docs.mkdir(exist_ok=True)
        for name in PAGE_FILES:
            source = BRAIN / "deploy" / "pages" / name
            if source.is_file():
                check_source(source, secrets)
                copy_release(source, docs / name)
        # The deployed tunnel origin is written by the private laptop host.
        # Refreshing app sources must not revert that public status file.
        endpoint = docs / "endpoint.json"
        if not endpoint.exists():
            source = BRAIN / "deploy" / "pages" / "endpoint.json"
            check_source(source, secrets)
            copy_release(source, endpoint)
        (output / ".gitignore").write_bytes(
            b"/.env\n__pycache__/\n*.pyc\n/brain/results/\n/output/\n.venv/\nnode_modules/\n**/runtime_checks.json\n")
        (output / "package.json").write_text(json.dumps({
            "name": "mist-contract-tests", "private": True, "type": "module",
            "engines": {"node": ">=24"}, "dependencies": {"typebox": "1.3.3"},
        }, indent=2) + "\n", encoding="utf-8")
        check_source(BRAIN / "deploy" / "package-lock.json", secrets)
        copy_release(BRAIN / "deploy" / "package-lock.json", output / "package-lock.json")
        (output / ".gitattributes").write_bytes((
            "*.py text eol=lf\n*.js text eol=lf\n*.ts text eol=lf\n*.mjs text eol=lf\n*.cjs text eol=lf\n"
            "*.html text eol=lf\n*.css text eol=lf\n*.json text eol=lf\n"
            "*.txt text eol=lf\n*.md text eol=lf\n*.ps1 text eol=lf\n"
            ".env.example text eol=lf\n.gitignore text eol=lf\n"
            ".gitattributes text eol=lf\n*.png binary\n*.wav binary\n").encode("utf-8"))
        known = {item["path"] for item in files}
        extras = [
            "brain/deploy/build_release.py", "requirements.txt", ".env.example",
            "README.md", ".gitignore", ".gitattributes", "package.json", "package-lock.json",
            *(f"brain/deploy/{name}" for name in DEPLOY_FILES),
            *(f"docs/{name}" for name in PAGE_FILES),
        ]
        for relative in extras:
            target = output / relative
            if not target.is_file() or relative in known:
                continue
            files.append({"path": relative, "bytes": target.stat().st_size,
                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
            known.add(relative)
        report["files"] = sorted(files, key=lambda item: item["path"])
        report["file_count"] = len(files)
        report["total_bytes"] = sum(item["bytes"] for item in files)
        (output / "release-manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = build(args.output, args.dry_run)
    print(f"{report['file_count']} source/assets files; {report['total_bytes'] / 1048576:.2f} MiB")
    print("Largest files:")
    for item in sorted(report["files"], key=lambda file: file["bytes"], reverse=True)[:8]:
        print(f"  {item['bytes'] / 1048576:.2f} MiB  {item['path']}")
    if not args.dry_run:
        print(f"Staged: {args.output.resolve()}")


if __name__ == "__main__":
    main()
