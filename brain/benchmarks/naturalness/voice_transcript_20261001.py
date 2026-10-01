"""Check speech intelligibility and spoken delivery-tag leakage for the v4 pilot.

``--plan`` is offline and needs no pilot output or credentials. ``--run`` sends
only the selected synthetic WAV clips to Deepgram Nova-3; it never retries.
This measures transcription agreement, not subjective voice quality.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain"))
from duplex.server import load_env as server_load_env  # noqa: E402
from duplex.tts import DELIVERY_TAGS  # noqa: E402

PILOT_DIR = ROOT / "brain/results/v4-expressive-20261001/pilot"
OUTPUT_DIR = ROOT / "brain/results/v4-expressive-20261001/transcript-check"
MIST_VOICE_ID = "24AMj4dc02cYAwoUnqzN"
MIST_NAMES = {"mist refined", "mist"}
IMPORTANT_COMMANDS = ("stop", "motors", "power")
MAX_CONCURRENCY = 2
REQUEST_TIMEOUT_S = 30
DEEPGRAM_URL = (
    "https://api.deepgram.com/v1/listen?"
    + urllib.parse.urlencode({
        "model": "nova-3",
        "smart_format": "false",
        "punctuate": "false",
        "mip_opt_out": "true",
    })
)


def words(text: str) -> list[str]:
    """Tokenize casefolded words consistently for WER and command checks."""
    return re.findall(r"[\w]+(?:['’][\w]+)?", text.casefold(), flags=re.UNICODE)


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    """Levenshtein distance using O(min(n, m)) memory."""
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def word_error(reference: str, hypothesis: str) -> dict:
    ref_words, hyp_words = words(reference), words(hypothesis)
    edits = edit_distance(ref_words, hyp_words)
    return {"word_edits": edits, "reference_word_count": len(ref_words),
            "hypothesis_word_count": len(hyp_words),
            "word_error_rate": round(edits / max(1, len(ref_words)), 4)}


def known_tag_phrases() -> tuple[tuple[str, ...], ...]:
    """Return the actual non-neutral spoken tag vocabulary used by the TTS map."""
    phrases = set()
    for tag in DELIVERY_TAGS.values():
        if isinstance(tag, str) and tag.startswith("[") and tag.endswith("]"):
            tokenized = tuple(words(tag[1:-1]))
            if tokenized:
                phrases.add(tokenized)
    return tuple(sorted(phrases))


KNOWN_TAG_PHRASES = known_tag_phrases()


def find_spoken_tags(reference: str, transcript: str) -> list[str]:
    """Find mapped tag phrases that occur more often in ASR than source text.

    Comparing occurrence counts avoids flagging a tag word that is legitimately
    part of the spoken script while still catching an extra spoken tag.
    """
    ref_words, hyp_words = words(reference), words(transcript)
    leaked: list[str] = []
    for phrase in KNOWN_TAG_PHRASES:
        ref_count = _phrase_count(ref_words, phrase)
        heard_count = _phrase_count(hyp_words, phrase)
        if heard_count > ref_count:
            leaked.append(" ".join(phrase))
    return leaked


def _phrase_count(tokens: list[str], phrase: tuple[str, ...]) -> int:
    size = len(phrase)
    if not size:
        return 0
    return sum(tuple(tokens[index:index + size]) == phrase
               for index in range(len(tokens) - size + 1))


def reference_text(sample: dict) -> str:
    value = sample.get("text")
    if isinstance(value, str):
        return value
    parts = sample.get("parts")
    if isinstance(parts, list):
        return "".join(str(part) for part in parts)
    if isinstance(parts, str):
        return parts
    return ""


def is_mist(sample: dict) -> bool:
    voice_id = sample.get("voice_id")
    voice_name = str(sample.get("voice_name", "")).casefold().strip()
    return voice_id == MIST_VOICE_ID or voice_name in MIST_NAMES


def is_flash(sample: dict) -> bool:
    return "flash" in str(sample.get("model", "")).casefold()


def select_samples(samples: list[dict]) -> list[dict]:
    """Select MIST's seven v4 cases, other voices' calm-urgency, and two controls."""
    mist_v4 = [s for s in samples if is_mist(s) and not is_flash(s)]
    mist_v4.sort(key=lambda s: str(s.get("clip_id", "")))
    selected: list[dict] = mist_v4

    others = [s for s in samples if not is_mist(s)
              and str(s.get("case_id", "")).casefold() == "calm-urgency"]
    seen_voices: set[str] = set()
    for sample in sorted(others, key=lambda s: str(s.get("clip_id", ""))):
        identity = str(sample.get("voice_id") or sample.get("voice_name") or "").casefold()
        if identity and identity not in seen_voices:
            selected.append(sample)
            seen_voices.add(identity)

    flash_controls = [s for s in samples if is_mist(s) and is_flash(s)
                      and str(s.get("case_id", "")).casefold() in {"neutral", "empathy"}]
    wanted = {"neutral", "empathy"}
    chosen: dict[str, dict] = {}
    for sample in sorted(flash_controls, key=lambda s: str(s.get("clip_id", ""))):
        case = str(sample.get("case_id", "")).casefold()
        if case in wanted and case not in chosen:
            chosen[case] = sample
    selected.extend(chosen[case] for case in ("neutral", "empathy") if case in chosen)
    return selected


def plan() -> dict:
    return {
        "offline": True,
        "input": str(PILOT_DIR / "results.json"),
        "selected_target": "MIST seven v4 cases; each other voice calm-urgency; MIST neutral and empathy Flash controls",
        "expected_selected_samples": 12,
        "deepgram_model": "nova-3",
        "endpoint": "Deepgram pre-recorded REST /v1/listen",
        "request_timeout_seconds": REQUEST_TIMEOUT_S,
        "max_concurrency": MAX_CONCURRENCY,
        "retries": 0,
        "zero_data_retention": "mip_opt_out=true",
        "known_spoken_tag_phrases": [" ".join(p) for p in KNOWN_TAG_PHRASES],
        "output": str(OUTPUT_DIR / "transcript_check.private.json"),
        "limitation": "ASR agreement is an intelligibility proxy; it does not assess motion, naturalness, or subjective voice quality.",
    }


def deepgram_key() -> str | None:
    # Keep the existing project loader behavior, while allowing shell env to override.
    env = server_load_env()
    env.update(os.environ)
    key = env.get("DEEPGRAM_API_KEY")
    return key.strip() if isinstance(key, str) and key.strip() else None


def transcribe(wav_path: Path, key: str) -> dict:
    request = urllib.request.Request(
        DEEPGRAM_URL,
        data=wav_path.read_bytes(),
        headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            payload = json.load(response)
        alternative = payload["results"]["channels"][0]["alternatives"][0]
        transcript = alternative.get("transcript")
        if not isinstance(transcript, str):
            return {"status": "transcript_unavailable", "failure_kind": "unreadable_response"}
        return {"status": "ok" if transcript.strip() else "transcript_unavailable",
                "failure_kind": None if transcript.strip() else "empty_transcript",
                "transcript": transcript,
                "confidence": alternative.get("confidence"),
                "api_latency_s": round(time.monotonic() - started, 3),
                "request_id": payload.get("metadata", {}).get("request_id")}
    except urllib.error.HTTPError as error:
        return {"status": "provider_failure", "failure_kind": "http_error",
                "http_status": error.code}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"status": "provider_failure", "failure_kind": "transport_or_timeout"}
    except (ValueError, KeyError, IndexError, TypeError):
        return {"status": "transcript_unavailable", "failure_kind": "unreadable_response"}


def wav_for_sample(sample: dict, pilot_dir: Path) -> Path | None:
    name = sample.get("wav_file")
    if not isinstance(name, str) or not name.strip():
        return None
    relative = Path(name)
    candidate = relative if relative.is_absolute() else pilot_dir / relative
    if not candidate.is_file() and not relative.is_absolute():
        candidate = pilot_dir / "audio" / relative.name
    try:
        candidate.resolve().relative_to(pilot_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def analyze_sample(sample: dict, pilot_dir: Path, key: str) -> dict:
    result = {name: sample.get(name) for name in
              ("clip_id", "case_id", "voice_name", "model", "delivery", "status")}
    reference = reference_text(sample)
    result["reference_text"] = reference
    wav_path = wav_for_sample(sample, pilot_dir)
    if not wav_path:
        result.update(status="synthesis_failure",
                      synthesis_status=sample.get("status", "missing"),
                      failure_kind="wav_unavailable")
        return result
    if sample.get("status") != "ok":
        result["synthesis_warning"] = str(sample.get("status", "unknown"))
    provider = transcribe(wav_path, key)
    result["transcription"] = provider
    if provider["status"] != "ok":
        result.update(status=provider["status"], failure_kind=provider.get("failure_kind"))
        return result
    transcript = provider["transcript"]
    result["status"] = "ok"
    result["metrics"] = word_error(reference, transcript)
    result["missing_important_commands"] = [command for command in IMPORTANT_COMMANDS
                                             if command in words(reference)
                                             and command not in words(transcript)]
    result["spoken_control_tags"] = find_spoken_tags(reference, transcript)
    return result


def _sample_key(sample: dict) -> tuple[str, str, str]:
    return (str(sample.get("clip_id", "")), str(sample.get("case_id", "")),
            str(sample.get("voice_name", "")))


def run(input_path: Path, output_dir: Path, key: str) -> dict:
    # The pilot runner currently writes results.private.json; accept the requested
    # public-shape results.json when provided, with a private-name fallback.
    if not input_path.is_file() and input_path.name == "results.json":
        private_name = input_path.with_name("results.private.json")
        if private_name.is_file():
            input_path = private_name
    pilot_dir = input_path.parent
    report = json.loads(input_path.read_text(encoding="utf-8"))
    samples = report.get("samples") if isinstance(report, dict) else None
    if not isinstance(samples, list):
        raise ValueError("Pilot report must contain a samples array")
    selected = select_samples(samples)
    if not selected:
        raise ValueError("No clips matched the frozen transcript-check subset")

    output_dir.mkdir(parents=True, exist_ok=False)
    rows: list[dict | None] = [None] * len(selected)
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        futures = {pool.submit(analyze_sample, sample, pilot_dir, key): index
                   for index, sample in enumerate(selected)}
        for future in as_completed(futures):
            rows[futures[future]] = future.result()

    completed = [row for row in rows if row is not None]
    counts = Counter(row.get("status", "unknown") for row in completed)
    summary = {"selected": len(completed), "statuses": dict(sorted(counts.items())),
               "mean_word_error_rate": (round(sum(r["metrics"]["word_error_rate"] for r in completed
                                                    if r.get("metrics")) /
                                                  sum("metrics" in r for r in completed), 4)
                                        if any("metrics" in r for r in completed) else None),
               "samples_with_missing_important_commands": sum(bool(r.get("missing_important_commands"))
                                                              for r in completed),
               "samples_with_spoken_control_tags": sum(bool(r.get("spoken_control_tags"))
                                                        for r in completed)}
    result = {
        "schema_version": 1,
        "method": "Nova-3 transcription of selected synthetic pilot WAVs; word error rate against submitted TTS text.",
        "input_report": str(input_path),
        "model": "nova-3",
        "max_concurrency": MAX_CONCURRENCY,
        "retries": 0,
        "privacy": "mip_opt_out=true on every Deepgram request",
        "summary": summary,
        "samples": completed,
        "limitation": "ASR agreement is an intelligibility proxy; it does not assess motion, naturalness, or subjective voice quality.",
    }
    (output_dir / "transcript_check.private.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def selfcheck() -> None:
    assert edit_distance([], []) == 0
    assert edit_distance(["stop", "the", "motor"], ["stop", "motor"]) == 1
    assert word_error("STOP the motor", "stop motor")["word_error_rate"] == 0.3333
    assert find_spoken_tags("Speak warmly now.", "Speak warmly now.") == []
    assert find_spoken_tags("Speak now.", "[warmly] Speak now.") == ["warmly"]
    assert find_spoken_tags("Be calmly careful.", "Be calmly careful.") == []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="show the frozen offline plan")
    action.add_argument("--run", action="store_true", help="transcribe selected synthetic WAVs")
    action.add_argument("--selfcheck", action="store_true", help="run focused offline metric checks")
    parser.add_argument("--input", type=Path, default=PILOT_DIR / "results.json")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), indent=2))
        return 0
    if args.selfcheck:
        selfcheck()
        print("selfcheck passed")
        return 0
    key = deepgram_key()
    if not key:
        raise SystemExit("DEEPGRAM_API_KEY is unavailable in the project environment")
    if args.output_dir.exists():
        raise SystemExit(f"Refusing to replace existing output directory: {args.output_dir}")
    result = run(args.input, args.output_dir, key)
    compact = {"selected": result["summary"]["selected"],
               "statuses": result["summary"]["statuses"],
               "mean_word_error_rate": result["summary"]["mean_word_error_rate"],
               "samples_with_missing_important_commands": result["summary"]["samples_with_missing_important_commands"],
               "samples_with_spoken_control_tags": result["summary"]["samples_with_spoken_control_tags"],
               "result": str(args.output_dir / "transcript_check.private.json")}
    print(json.dumps(compact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
