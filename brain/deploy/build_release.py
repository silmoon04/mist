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
import shutil


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
        "remote_access",
    )],
    "brain/duplex/face_map.json", "brain/duplex/persona.txt",
    *[f"brain/duplex/static/{name}" for name in (
        "index.html", "style.css", "app.js", "playback.js", "delivery.js",
        "expression_policy.js", "activity_state.js", "listener_cue.js",
        "user_transcript_state.mjs", "capture-worklet.js", "debug.html",
        "debug.js", "expressions.html", "expressions.js", "inspect.css",
        "trials.html", "trials.css", "trials.js", "sample.wav",
    )],
    "brain/ui/static/face_runtime.js", "brain/ui/static/drawn_face_renderer.js",
    "brain/face_assets/app/app_data.js",
    "brain/benchmarks/naturalness/cascade_voice.py",
    "brain/benchmarks/naturalness/cerebras_client.py",
    "brain/harness/pi_client.py", "brain/harness/codex_client.py",
    "brain/harness/response_format.py", "brain/harness/eval_luna_conversations.py",
    "brain/harness/test_laptop_host.py", "brain/harness/test_live_studio.py",
    "brain/harness/test_live_studio_remote.py",
    "brain/deploy/fixtures/manifest.json",
    "brain/deploy/fixtures/audio/audio_phrase_seam-1.wav",
    f"brain/{DRAWN}/atlas.json", f"brain/{HANDDRAWN}/manifest.json",
    f"brain/{HANDDRAWN}/runtime.js",
    "design/hexapod_phone_quad_r5_20260912/cad/output/assembly_manifest.json",
]
DEPLOY_FILES = (
    "laptop_host.py",
    "start_laptop_host.ps1",
    "LAPTOP_HOST.md",
    "test_remote_e2e.py",
    "test_remote_e2e_helpers.py",
)
PAGE_FILES = ("index.html", "style.css", "app.js", ".nojekyll")


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
    if path.suffix.lower() in {".py", ".js", ".mjs", ".html", ".css", ".json", ".txt"}:
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


def build(output: Path, dry_run: bool) -> dict:
    paths = file_list()
    secrets = private_values()
    files = []
    for relative in paths:
        source = PROJECT / relative
        check_source(source, secrets)
        files.append({"path": relative.replace("\\", "/"), "bytes": source.stat().st_size,
                      "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    report = {"files": files, "file_count": len(files),
              "total_bytes": sum(item["bytes"] for item in files),
              "mutable_files": ["docs/endpoint.json"]}
    if not dry_run:
        output = output.resolve()
        if output == PROJECT or not output.is_relative_to(PROJECT / "output"):
            raise ValueError("Release output must be beneath project/output")
        for item in files:
            target = output / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT / item["path"], target)
        (output / "brain" / "deploy").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__), output / "brain" / "deploy" / "build_release.py")
        shutil.copyfile(BRAIN / "deploy" / "requirements.txt", output / "requirements.txt")
        shutil.copyfile(BRAIN / "deploy" / ".env.example", output / ".env.example")
        shutil.copyfile(BRAIN / "deploy" / "README.md", output / "README.md")
        for name in DEPLOY_FILES:
            source = BRAIN / "deploy" / name
            if source.is_file():
                check_source(source, secrets)
                shutil.copyfile(source, output / "brain" / "deploy" / name)
        docs = output / "docs"
        docs.mkdir(exist_ok=True)
        for name in PAGE_FILES:
            source = BRAIN / "deploy" / "pages" / name
            if source.is_file():
                check_source(source, secrets)
                shutil.copyfile(source, docs / name)
        # The deployed tunnel origin is written by the private laptop host.
        # Refreshing app sources must not revert that public status file.
        endpoint = docs / "endpoint.json"
        if not endpoint.exists():
            source = BRAIN / "deploy" / "pages" / "endpoint.json"
            check_source(source, secrets)
            shutil.copyfile(source, endpoint)
        (output / ".gitignore").write_text(
            "/.env\n__pycache__/\n*.pyc\n/brain/results/\n/output/\n.venv/\n", encoding="utf-8")
        known = {item["path"] for item in files}
        extras = [
            "brain/deploy/build_release.py", "requirements.txt", ".env.example",
            "README.md", ".gitignore", "docs/endpoint.json",
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
