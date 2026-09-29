"""Run supported live text cases through the existing Cerebras client.

This direct model adapter is not the MIST application pipeline. It cannot
validate acoustic recognition, audio turn control, TTS, app actions or memory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import time

HERE = Path(__file__).resolve().parent
NATURALNESS = HERE.parent
BRAIN = HERE.parents[2]
DEFAULT_CASES = HERE / "cases.json"
DEFAULT_PROMPT = BRAIN / "duplex" / "persona.txt"
FROZEN_CASES_SHA256 = "8725038c44c933930f9e32ba671bcaa9821d6f982ceeb137b4c64a48bb11c9e1"
sys.path.insert(0, str(NATURALNESS))
from cerebras_client import CerebrasClient  # noqa: E402

TEXT_MODES = {"text_multiturn", "text_multispeaker", "text_multiturn_with_fixture", "multiturn_asr_fixture"}
EMPTY_MEMORY = {"none", "empty", "isolated_empty"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_suite(path=DEFAULT_CASES, require_frozen=True):
    raw = Path(path).read_bytes()
    digest = sha(raw)
    if require_frozen and digest != FROZEN_CASES_SHA256:
        raise ValueError(f"Frozen cases hash mismatch: expected {FROZEN_CASES_SHA256}, got {digest}")
    suite = json.loads(raw)
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != suite.get("case_count"):
        raise ValueError("Case count does not match cases.json")
    ids, family_splits = set(), {}
    required = ("fixture", "timeline", "user_goal", "expected_behavior", "objective_assertions",
                "acceptable_alternatives", "critical_failures", "failure_seeds", "latency_metrics",
                "human_review_required")
    for case in cases:
        cid, family, split = case.get("id"), case.get("scenario_family"), case.get("split")
        if not isinstance(cid, str) or not cid or cid in ids:
            raise ValueError(f"Invalid or duplicate case ID: {cid}")
        ids.add(cid)
        if split not in {"development", "heldout"} or not isinstance(family, str) or not family:
            raise ValueError(f"Invalid split/family in {cid}")
        family_splits.setdefault(family, set()).add(split)
        if any(k not in case for k in required):
            raise ValueError(f"Missing contract field in {cid}")
        last = -1
        for event in case["timeline"]:
            offset = event.get("t_ms")
            if type(offset) is not int or offset < last:
                raise ValueError(f"Invalid/non-monotonic timeline in {cid}")
            last = offset
    if any(len(v) != 1 for v in family_splits.values()):
        raise ValueError("Scenario family crosses development/heldout boundary")
    return suite, raw, digest


def unsupported_reason(case):
    """Return why the text-only model path cannot test this condition honestly."""
    fixture, mode = case.get("fixture", {}), case.get("mode")
    memory = fixture.get("memory")
    if isinstance(memory, (dict, list)) and memory:
        return "Requires a real approved-memory fixture; prompt-injected memory would fake recall."
    if isinstance(memory, str) and memory not in EMPTY_MEMORY:
        if "robot preview only" in memory:
            return "Requires robot action capability/receipt fixture; this adapter has no app tools."
        if not (case.get("category") == "recognition_repair" and
                ("not otherwise present" in memory or "no source-code path stored" in memory)):
            return "Requires declared memory condition; this adapter has no memory-store integration."
    if fixture.get("background_tasks"):
        return "Requires background task receipts/revisions and async delivery control."
    if "sensor" in fixture or "image" in fixture:
        return "Requires the declared sensor or image fixture; none is supplied to this adapter."
    if "voice_output" in fixture or "audio" in fixture:
        return "Requires recorded audio/playback evidence."
    if mode not in TEXT_MODES:
        return f"Requires {mode} evidence; adapter sends sequential text turns only."
    return None


def user_events(case):
    result = []
    for event in case.get("timeline", []):
        actor, text = event.get("actor", ""), event.get("text")
        if (actor == "user" or actor.startswith("user_")) and isinstance(text, str) and text.strip():
            if actor.startswith("user_"):
                text = f"Speaker {actor.removeprefix('user_')}: {text}"
            result.append({"actor": actor, "kind": event.get("kind"), "user_text": text,
                           "planned_offset_ms": event.get("t_ms")})
    return result


def select_cases(suite, split, requested_ids=None):
    cases = [c for c in suite["cases"] if split == "all" or c["split"] == split]
    if requested_ids:
        wanted = set(requested_ids)
        found = {c["id"] for c in cases if c["id"] in wanted}
        if found != wanted:
            raise ValueError(f"Unknown or out-of-split case IDs: {sorted(wanted - found)}")
        cases = [c for c in cases if c["id"] in wanted]
    if not cases:
        raise ValueError("No selected cases")
    return cases


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def arm_config(arm, args, prompt_hash):
    return {"model": getattr(args, f"{arm}_model"), "reasoning": getattr(args, f"{arm}_reasoning"),
            "provider": "cerebras", "system_prompt_sha256": prompt_hash,
            "max_output_tokens": args.max_output_tokens, "timeout_s": args.timeout,
            "tools": "none", "session_policy": "new session per case; actual generated history within case"}


def run_case(case, repeat, arm, config, prompt, client_factory=CerebrasClient):
    base = {"case_id": case["id"], "split": case["split"], "scenario_family": case["scenario_family"],
            "repeat": repeat, "arm": arm, "mode": case["mode"], "evidence_mode": "live_model_text_only",
            "acoustic_recognition": "unmeasured", "turn_timing": "unmeasured",
            "app_tools_and_async_tasks": "unmeasured", "semantic_judgment": "unrated"}
    reason = unsupported_reason(case)
    if reason:
        return {**base, "status": "unsupported", "unsupported_reason": reason, "turns": []}
    events = user_events(case)
    if not events:
        return {**base, "status": "unsupported", "unsupported_reason": "No sequential user text in timeline.", "turns": []}
    client = client_factory(model=config["model"], thinking=config["reasoning"],
                            system_prompt=prompt, max_output_tokens=config["max_output_tokens"])
    result = {**base, "status": "completed", "turns": [], "expected_user_events": len(events)}
    try:
        client.new_session()
        origin = time.monotonic()
        for index, event in enumerate(events, 1):
            start = time.monotonic()
            reply = client.ask(event["user_text"], timeout=config["timeout_s"])
            end = time.monotonic()
            result["turns"].append({"turn_index": index, **event,
                "adapter_submit_offset_ms": round((start - origin) * 1000),
                "response_complete_offset_ms": round((end - origin) * 1000),
                "text": reply.text, "latency_ms": round((end - start) * 1000),
                "first_text_ms": None if reply.ttft_s is None else round(reply.ttft_s * 1000),
                "tools": [{"name": t.name, "args": t.args, "is_error": t.is_error} for t in reply.tool_calls],
                "errors": list(reply.errors)})
            if reply.errors:
                result.update(status="failed", stopped_after_turn=index, errors=list(reply.errors))
                break
        result["completed_user_events"] = len(result["turns"])
        result["case_complete"] = result["status"] == "completed" and len(result["turns"]) == len(events)
        if case["mode"] == "multiturn_asr_fixture":
            result["coverage_note"] = "Text transcript repair only; speech recognition was not tested."
        if case["mode"] == "text_multispeaker":
            result["coverage_note"] = "Speaker names are text prefixes; overlap and acoustic attribution were not tested."
    finally:
        client.close()
    return result


def build_blind_packet(run_data, cases, seed):
    lookup, grouped = {c["id"]: c for c in cases}, {}
    for sample in run_data["samples"]:
        grouped.setdefault((sample["case_id"], sample["repeat"]), {})[sample["arm"]] = sample
    rng, pairs, key = random.Random(seed), [], {"suite_sha256": run_data["suite_sha256"], "seed": seed, "pairs": []}
    for case_id, repeat in sorted(grouped):
        arms = grouped[(case_id, repeat)]
        if set(arms) != {"a", "b"} or any(
                s.get("status") != "completed" or s.get("case_complete") is False for s in arms.values()):
            continue
        case, order = lookup[case_id], ["a", "b"]
        rng.shuffle(order)
        pair_id = "pair-" + hashlib.sha256(
            f"{run_data['suite_sha256']}|{seed}|{case_id}|{repeat}".encode()).hexdigest()[:12]
        options, answer_options = {}, {}
        for label, arm in zip(("A", "B"), order):
            sample = arms[arm]
            options[label] = {"status": sample["status"], "turns": sample.get("turns", []),
                "errors": sample.get("errors"),
                "evidence_notice": "Text only. No audio, timing, memory-store or app-action evidence is included."}
            answer_options[label] = {"arm": arm, "config": run_data["arms"][arm], "status": sample["status"]}
        pairs.append({"blind_pair_id": pair_id, "case_id": case_id, "repeat": repeat,
            "user_goal": case["user_goal"], "review_criteria": case["expected_behavior"],
            "acceptable_alternatives": case["acceptable_alternatives"],
            "critical_failures_to_check": case["critical_failures"], "options": options,
            "ratings": {"A": None, "B": None, "preference": None, "reviewer_reason": None}})
        key["pairs"].append({"blind_pair_id": pair_id, "case_id": case_id, "repeat": repeat,
                              "options": answer_options})
    packet = {"schema_version": 1, "suite_id": run_data["suite_id"],
              "suite_version": run_data["suite_version"], "suite_sha256": run_data["suite_sha256"],
              "evidence_mode": "live_model_text_only", "review_status": "unrated_pending_independent_review",
              "pairs": pairs}
    return packet, key


def run(args, client_factory=CerebrasClient):
    suite, case_bytes, digest = load_suite(args.cases)
    cases = select_cases(suite, args.split, args.ids)
    if args.repeats < 1 or args.max_output_tokens < 1 or args.timeout <= 0:
        raise ValueError("Repeats, token limit and timeout must be positive")
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("Refusing to overwrite output " + str(output))
    prompt_paths = {"a": args.a_prompt, "b": args.b_prompt}
    prompts = {arm: path.read_text(encoding="utf-8") for arm, path in prompt_paths.items()}
    if any(not value.strip() for value in prompts.values()):
        raise ValueError("System prompts must not be empty")
    hashes = {arm: sha(value.encode()) for arm, value in prompts.items()}
    arms = {arm: arm_config(arm, args, hashes[arm]) for arm in ("a", "b")}
    plan = [(case, repeat) for case in cases for repeat in range(1, args.repeats + 1)]
    rng = random.Random(args.seed)
    rng.shuffle(plan)
    output.mkdir(parents=True)
    data = {"manifest": {"schema_version": 1, "run_id": output.name,
        "created_utc": datetime.now(timezone.utc).isoformat(), "suite_id": suite["suite_id"],
        "suite_version": suite["version"], "suite_sha256": digest, "adapter": "cerebras-direct-text",
        "evidence_mode": "live_model_text_only", "scope": "Direct model output; not MIST app performance.",
        "semantic_judgments": "unrated_pending_independent_review", "audio_quality": "unmeasured",
        "turn_timing": "unmeasured", "selected_case_ids": [c["id"] for c in cases],
        "repeats": args.repeats, "seed": args.seed, "max_output_tokens": args.max_output_tokens,
        "timeout_s": args.timeout, "case_file_sha256": sha(case_bytes), "arms": arms,
        "prompt_paths": {k: str(v) for k, v in prompt_paths.items()},
        "source_hashes": {"cases.json": sha(case_bytes), "runner.py": sha(Path(__file__).read_bytes()),
            "cerebras_client.py": sha((NATURALNESS / "cerebras_client.py").read_bytes())}}, "samples": []}

    def save():
        write_json(output / "run.json", data)

    save()
    for case, repeat in plan:
        order = ["a", "b"]
        rng.shuffle(order)
        for arm in order:
            data["samples"].append(run_case(case, repeat, arm, arms[arm], prompts[arm], client_factory))
            save()
    packet, answer_key = build_blind_packet({"suite_id": suite["suite_id"], "suite_version": suite["version"],
        "suite_sha256": digest, "arms": arms, "samples": data["samples"]}, cases, args.seed)
    write_json(output / "blind_packet.json", packet)
    write_json(output / "answer_key.json", answer_key)
    statuses = {key: sum(s["status"] == key for s in data["samples"])
                for key in sorted({s["status"] for s in data["samples"]})}
    data.update(status="finished", summary={"sample_status_counts": statuses,
        "semantic_judgments": "unrated", "blind_packet": "blind_packet.json",
        "answer_key": "answer_key.json; keep private from reviewers"})
    save()
    return int(any(s["status"] == "failed" for s in data["samples"]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    p.add_argument("--split", choices=("development", "heldout", "all"), default="development")
    p.add_argument("--ids", help="Comma-separated IDs, constrained by --split")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seed", type=int, default=20260929)
    p.add_argument("--a-prompt", type=Path, default=DEFAULT_PROMPT)
    p.add_argument("--b-prompt", type=Path, default=DEFAULT_PROMPT)
    p.add_argument("--a-model", choices=("qwen-3.8-27b", "gpt-oss-120b"), default="qwen-3.8-27b")
    p.add_argument("--b-model", choices=("qwen-3.8-27b", "gpt-oss-120b"), default="qwen-3.8-27b")
    p.add_argument("--a-reasoning", choices=("none", "low", "medium", "high"), default="low")
    p.add_argument("--b-reasoning", choices=("none", "low", "medium", "high"), default="low")
    p.add_argument("--max-output-tokens", type=int, default=512)
    p.add_argument("--timeout", type=float, default=45)
    args = p.parse_args(argv)
    args.ids = [value.strip() for value in args.ids.split(",") if value.strip()] if args.ids else None
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
