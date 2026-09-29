"""Repeatable MIST evaluations. Live text calls are explicit; audio is never inferred."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
BRAIN = HERE.parents[1]
ROOT = BRAIN.parent
sys.path.insert(0, str(HERE))


def benchmark_metrics():
    # The model harness also has a module named metrics.
    module_name = 'mist_naturalness_metrics'
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, HERE / 'metrics.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return sys.modules[module_name]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_suite(path):
    raw = Path(path).read_bytes()
    suite = json.loads(raw)
    cases = suite.get("cases", [])
    if not cases:
        raise ValueError("Suite has no cases")
    seen = set()
    for case in cases:
        cid = case.get("id")
        if not isinstance(cid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*", cid) or cid in seen:
            raise ValueError(f"Invalid or duplicate case ID: {cid}")
        seen.add(cid)
        if case.get("split") not in ("dev", "validation", "holdout"):
            raise ValueError(f"Invalid split: {cid}")
        if not case.get("category") or not case.get("mode"):
            raise ValueError(f"Missing category/mode: {cid}")
        if case["mode"] == "text" and not case.get("turns"):
            raise ValueError(f"Text case has no turns: {cid}")
        for turn in case.get("turns", []):
            if not isinstance(turn.get("user"), str) or not turn["user"].strip():
                raise ValueError(f"Turn has no user text: {cid}")
    return suite, sha(raw)


def select(suite, split="dev", ids=None, limit=None):
    cases = [c for c in suite["cases"] if split == "all" or c["split"] == split]
    if ids:
        wanted = set(ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
        if {c["id"] for c in cases} != wanted:
            raise ValueError("Requested IDs are missing or excluded by the split")
    if limit is not None:
        if limit < 1:
            raise ValueError("Limit must be positive")
        cases = cases[:limit]
    if not cases:
        raise ValueError("No selected cases")
    return cases


def unsupported_reason(case):
    if case["mode"] != "text":
        return "Requires " + case["mode"] + " evidence; the text adapter cannot establish this behavior."
    allowed = {"sensor": {"none", "fresh_battery", "stale_battery", "fresh_orientation"},
               "memory": {"isolated_empty"}, "robot": {"preview_only"}}
    for name, value in case.get("fixtures", {}).items():
        if name not in allowed or not isinstance(value, str) or value not in allowed[name]:
            return "The text adapter has no implemented fixture for " + name + "."
    for turn in case.get("turns", []):
        if turn.get("sensor_profile", "none") not in allowed["sensor"] or turn.get("fixture"):
            return "The text adapter cannot supply this turn's declared fixture."
    if case.get("session_boundaries"):
        return "Requires a session-reset and memory adapter."
    return None


def unsupported_sample(case, repeat, reason):
    return {"case_id": case["id"], "repeat": repeat, "category": case["category"], "split": case["split"],
            "status": "unsupported", "turns": [], "events": [], "checks": [], "outcomes": {},
            "errors": [reason], "scenario_hash": sha(json.dumps(case, sort_keys=True).encode())}


def objective_checks(expect, text, tools, before, after):
    """Only inspect explicit contracts. Social quality remains a human judgment."""
    checks = []
    names = [t["name"] for t in tools]
    def add(name, ok, critical=False):
        checks.append({"name": name, "passed": bool(ok), "critical": critical})
    def comparable(value):
        # Hyphen and nonbreaking hyphen are equivalent in spoken-word checks.
        return ' '.join(value.casefold().replace('\u2010', '-').replace('\u2011', '-').split())
    for name in expect.get("required_tools", []):
        add("required_tool:" + name, name in names)
    for name in expect.get("forbidden_tools", []):
        add("forbidden_tool:" + name, name not in names, name in ("preview_motion", "pan_phone", "remember"))
    if "first_tool" in expect:
        add("first_tool", bool(names) and names[0] == expect["first_tool"], expect["first_tool"] == "stop_robot")
    for field in expect.get("state_unchanged", []):
        add("state_unchanged:" + field, before.get(field) == after.get(field), True)
    for literal in expect.get("contains", []):
        add("contains:" + literal, comparable(literal) in comparable(text))
    if "contains_any" in expect:
        add("contains_any", any(comparable(literal) in comparable(text) for literal in expect["contains_any"]))
    for name, required_args in expect.get("tool_args", {}).items():
        matching = [t for t in tools if t["name"] == name]
        add("tool_args:" + name, any(isinstance(t.get("args"), dict) and all(t["args"].get(k) == v for k, v in required_args.items()) for t in matching))
    if "max_tools" in expect:
        add("max_tools", len(tools) <= expect["max_tools"])
    if "max_words" in expect:
        add("requested_brevity", len(re.findall(r"\b\w+(?:['’]\w+)?\b", text)) <= expect["max_words"])
    if "max_questions" in expect:
        add("max_questions", text.count("?") <= expect["max_questions"])
    for field in ("expression", "variant", "duration_ms"):
        if field in expect:
            face = [t for t in tools if t["name"] == "set_expression"]
            add("requested_" + field, len(face) == 1 and isinstance(face[0].get("args"), dict) and face[0]["args"].get(field) == expect[field])
    if "allowed_expressions" in expect:
        add("expression_fit_set", all(isinstance(t.get("args"), dict) and t["args"].get("expression") in expect["allowed_expressions"] for t in tools if t["name"] == "set_expression"))
    add("no_rejected_model_tool", not any(t.get("is_error") for t in tools))
    add("no_spoken_control_markup", "[[face:" not in text and "<expression" not in text)
    supported = {"required_tools", "forbidden_tools", "first_tool", "state_unchanged", "contains", "contains_any", "max_tools", "max_words", "max_questions", "expression", "variant", "duration_ms", "allowed_expressions", "tool_args"}
    checks.extend({"name": "unmeasured:" + key, "passed": None, "critical": False} for key in expect if key not in supported)
    return checks


def luna_case(case, repeat, output, config, persona):
    reason = unsupported_reason(case)
    if reason:
        return unsupported_sample(case, repeat, reason)
    sys.path.insert(0, str(BRAIN))
    sys.path.insert(0, str(BRAIN / "harness"))
    from codex_client import CodexClient
    from eval_luna_conversations import RuntimeBridge, choose_binary, state_snapshot
    from duplex.runtime import RobotRuntime, specs
    from duplex.background import specs as background_specs
    from duplex.phrasing import next_phrase
    provider = config.get('provider', 'codex')
    if provider == 'codex':
        choose_binary()
        client_class = CodexClient
    elif provider == 'cerebras':
        from cerebras_client import CerebrasClient
        client_class = CerebrasClient
    else:
        raise ValueError('Unknown text provider')
    row = {"case_id": case["id"], "repeat": repeat, "category": case["category"],
           "split": case["split"], "status": "completed", "turns": [], "events": [],
           "checks": [], "outcomes": {}, "errors": [], "evidence_mode": "text_only",
           "limitations": ["No microphone, TTS, playback, or native voice delegation measured.",
                           "Real bounded application tools; background analyst is a labelled stub."]}
    client = None
    start = time.perf_counter()
    def event(name, tid, **extra):
        row["events"].append({"name": name, "t_ms": (time.perf_counter()-start)*1000,
                              "clock_id": "runner_perf_counter", "turn_id": tid, "case_id": case["id"], **extra})
    with tempfile.TemporaryDirectory(prefix="mist-benchmark-") as temp:
        runtime = RobotRuntime(Path(temp))
        bridge = RuntimeBridge(runtime)
        try:
            client = client_class(model=config["model"], thinking=config["reasoning"],
                                 service_tier=config["service_tier"], tools=[], with_memory=False,
                                 system_prompt=persona, run_dir=output, max_output_tokens=config["max_output_tokens"])
            client._specs = specs() + background_specs()
            client._selected = {s["name"] for s in client._specs}
            client._bridge = bridge
            client.new_session()
            row["backend"] = client.backend_info
            row["startup_ms"] = (time.perf_counter()-start)*1000
            for index, step in enumerate(case["turns"]):
                tid = str(index+1)
                before = state_snapshot(runtime)
                bridge.prepare(step.get("sensor_profile", "none"), step["user"])
                acc = {"text": "", "first": False, "phrase": False, "tool": False}
                callback_errors = []
                def capture(e):
                    try:
                        kind = e.get("type")
                        if kind == "message_update":
                            delta = e.get("assistantMessageEvent", {}).get("delta", "")
                            acc["text"] += delta
                            if delta.strip() and not acc["first"]:
                                event("first_text", tid); acc["first"] = True
                            if not acc["phrase"] and next_phrase(acc["text"]):
                                event("first_phrase", tid); acc["phrase"] = True
                        elif kind in ("tool_execution_start", "tool_execution_end"):
                            if kind == "tool_execution_start" and not acc["tool"]:
                                event("first_tool", tid); acc["tool"] = True
                            event("tool_started" if kind.endswith("start") else "tool_finished", tid,
                                  tool_call_id=e.get("toolCallId"), tool_name=e.get("toolName"),
                                  is_error=e.get("isError", False))
                    except Exception as exc:
                        callback_errors.append(type(exc).__name__ + ": " + str(exc))
                event("turn_committed", tid, boundary="text_prompt_submitted")
                result = client.ask(step["user"], timeout=config["timeout_s"], on_event=capture)
                if result.text.strip() and not acc["phrase"]:
                    event("first_phrase", tid, boundary="final_flush")
                event("turn_completed", tid)
                tools = [asdict(t) for t in result.tool_calls]
                after = state_snapshot(runtime)
                checks = objective_checks(step.get("expect", {}), result.text, tools, before, after)
                if callback_errors:
                    checks.append({"name": "event_capture", "passed": False, "critical": True})
                errors = list(result.errors) + callback_errors
                row["turns"].append({"turn_id": tid, "user": step["user"], "text": result.text,
                                     "tools": tools, "receipts": list(bridge.receipts), "checks": checks,
                                     "state_before": before, "state_after": after,
                                     "raw_timings": result.timings, "errors": errors,
                                     "input_tokens": result.input_tokens, "output_tokens": result.output_tokens})
                row["checks"].extend({**c, "name": tid+":"+c["name"]} for c in checks)
                save(output / "sample.partial.json", row)
                if errors:
                    row["errors"].extend(errors)
                    row["status"] = "timeout" if any("timeout" in x.lower() or "timed out" in x.lower() for x in errors) else "failed"
                    break
        except Exception as exc:
            row["status"] = "failed"
            row["errors"].append(type(exc).__name__ + ": " + str(exc)[:700])
        finally:
            if client:
                client.close()
    row["expected_turns"] = len(case["turns"])
    row["completed_turns"] = len(row["turns"])
    row["human_review"] = "pending"
    row["scenario_hash"] = sha(json.dumps(case, sort_keys=True).encode())
    return row


def write_report(run, output):
    summary = benchmark_metrics().summarize_run(run)
    save(output / "run.json", run)
    save(output / "summary.json", summary)
    entries = []
    for sample in run["samples"]:
        failed = [c["name"] for c in sample.get("checks", []) if c.get("passed") is False]
        turns = "".join("<p><b>You</b> "+html.escape(t["user"])+"</p><p><b>MIST</b> "+html.escape(t["text"])+"</p>" for t in sample.get("turns", []))
        entries.append("<details><summary>"+html.escape(f"{sample['case_id']} · {sample['repeat']} · {sample['status']}")+"</summary><p>"+html.escape(", ".join(failed) or "No recorded contract failures. Human quality rating is separate.")+"</p>"+turns+"<pre>"+html.escape(json.dumps(sample.get("errors", []), indent=2))+"</pre></details>")
    document = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MIST benchmark</title><style>body{max-width:960px;margin:40px auto;padding:0 24px;font:16px/1.6 system-ui;color:#202728;background:#f7f6f2}h1{font-size:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}details{padding:16px 0;border-bottom:1px solid #d6d9d4}summary{cursor:pointer}a{color:#12645e}</style><h1>MIST benchmark</h1>"""
    document += "<p>"+html.escape(run["manifest"]["run_id"])+" · "+html.escape(run["manifest"]["adapter"])+"</p><p>Text timing is not speaker latency. Missing audio or human ratings remain unmeasured.</p><p><a href='run.json'>Raw run</a> · <a href='summary.json'>Metrics</a></p><details><summary>Metrics and coverage</summary><pre>"+html.escape(json.dumps(summary, indent=2))+"</pre></details>"+"".join(entries)
    (output / "report.html").write_text(document, encoding="utf-8")
    return summary


def run_cases(args):
    suite, digest = checked_suite(args.suite)
    cases = select(suite, args.split, args.ids, args.limit)
    if args.repeats < 1:
        raise ValueError("Repeats must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    persona_bytes = args.persona.read_bytes()
    persona = persona_bytes.decode("utf-8").replace("Your voice is being converted to the owner's MIST voice.", "Your replies are spoken using the owner's MIST voice through streaming text to speech.")
    config = {"model": args.model, "reasoning": args.reasoning, "service_tier": args.service_tier,
              "max_output_tokens": args.max_output_tokens, "timeout_s": args.timeout, "concurrency": 1,
              "seed": args.seed, "persona_sha256": sha(persona.encode()), "condition": "new_session_per_case_warm_within_case"}
    source_files = [BRAIN/"duplex"/f for f in ("runtime.py", "persona.txt", "background.py", "expression_policy.py", "expression_requests.py", "memory_requests.py", "motion_requests.py", "phrasing.py", "face_map.json")]
    source_files += [BRAIN/"harness"/f for f in ("codex_client.py", "eval_luna_conversations.py", "response_format.py")]
    source_files += list(HERE.glob("*.py"))
    run = {"manifest": {"schema_version": 1, "run_id": output.name, "suite_sha256": digest,
            "adapter": "luna-text", "created_utc": datetime.now(timezone.utc).isoformat(), "config": config,
            "selected_case_ids": [c["id"] for c in cases], "repeats": args.repeats,
            "source_hashes": {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in source_files},
            "rubric_sha256": sha((HERE/"RUBRIC.md").read_bytes()) if (HERE/"RUBRIC.md").exists() else None,
            "evidence_mode": "text_only", "human_quality": "unrated", "audio_quality": "unmeasured"}, "samples": []}
    (output/"cases.frozen.json").write_bytes(args.suite.read_bytes())
    (output/"persona.frozen.txt").write_text(persona, encoding="utf-8")
    save(output/"run.json", run)
    jobs = [(case, repeat) for repeat in range(1, args.repeats+1) for case in cases]
    random.Random(args.seed).shuffle(jobs)
    for case, repeat in jobs:
        sub = output / (case["id"] + "-r" + str(repeat))
        sub.mkdir()
        reason = unsupported_reason(case)
        if reason:
            row = unsupported_sample(case, repeat, reason)
        else:
            row = luna_case(case, repeat, sub, config, persona)
        run["samples"].append(row)
        save(sub/"sample.json", row)
        save(output/"run.json", run)
        print(json.dumps({"case": case["id"], "repeat": repeat, "status": row["status"], "failed_checks": sum(c.get("passed") is False for c in row["checks"])}), flush=True)
    write_report(run, output)
    return 0 if all(s["status"] in ("completed", "unsupported") and all(c.get("passed") is not False for c in s["checks"]) for s in run["samples"]) else 1


CONTRACTS = [
    ("python", "brain/harness/test_duplex.py"), ("python", "brain/harness/test_streaming_tts.py"),
    ("python", "brain/harness/test_background.py"), ("python", "brain/harness/test_native_request_guards.py"),
    ("python", "brain/harness/test_lipsync.py"), ("node", "brain/harness/test_duplex_playback.mjs"),
    ("node", "brain/harness/test_face_audio_clock.mjs"), ("node", "brain/harness/test_expression_policy.mjs"),
    ("node", "brain/harness/test_drawn_timing.cjs")]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--suite", type=Path, default=HERE/"cases.json")
        if name != "validate":
            p.add_argument("--split", choices=["dev", "validation", "holdout", "all"], default="dev")
            p.add_argument("--ids"); p.add_argument("--limit", type=int)
        if name == "run":
            p.add_argument("--output", type=Path, required=True)
            p.add_argument("--model", default="gpt-6-luna")
            p.add_argument("--reasoning", default="low", choices=["low", "medium", "high"])
            p.add_argument("--service-tier", default="default", choices=["default", "priority"])
            p.add_argument("--persona", type=Path, default=BRAIN/"duplex/persona.txt")
            p.add_argument("--repeats", type=int, default=3); p.add_argument("--seed", type=int, default=20260923)
            p.add_argument("--timeout", type=float, default=45)
            p.add_argument("--max-output-tokens", type=int, default=180)
    p = sub.add_parser("score"); p.add_argument("run", type=Path); p.add_argument("--output", type=Path, required=True)
    for name in ("compare", "review"):
        p = sub.add_parser(name)
        p.add_argument("--baseline", type=Path, required=True); p.add_argument("--candidate", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("contracts"); p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command in ("validate", "plan"):
        suite, digest = checked_suite(args.suite)
        cases = suite["cases"] if args.command == "validate" else select(suite, args.split, args.ids, args.limit)
        print(json.dumps({"suite_sha256": digest, "cases": len(cases), "turns": sum(len(c.get("turns", [])) for c in cases),
                          "modes": dict(Counter(c["mode"] for c in cases)), "splits": dict(Counter(c["split"] for c in cases)),
                          "categories": dict(Counter(c["category"] for c in cases)),
                          "text_adapter_supported": sum(unsupported_reason(c) is None for c in cases),
                          "selected": [c["id"] for c in cases]}, indent=2))
        return 0
    if args.command == "run":
        return run_cases(args)
    if args.command == "score":
        args.output.mkdir(parents=True, exist_ok=False)
        write_report(load(args.run), args.output)
    elif args.command == "compare":
        compare_runs = benchmark_metrics().compare_runs
        result = compare_runs(load(args.baseline), load(args.candidate))
        save(args.output, result)
        print(json.dumps(result, indent=2))
    elif args.command == "review":
        from review import build_review
        print(json.dumps(build_review(load(args.baseline), load(args.candidate), args.output), indent=2))
    elif args.command == "contracts":
        args.output.mkdir(parents=True, exist_ok=False)
        rows = []
        for executable, script in CONTRACTS:
            command = [sys.executable if executable == "python" else executable, script]
            start = time.perf_counter()
            try:
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
                code, log = result.returncode, result.stdout + result.stderr
            except subprocess.TimeoutExpired:
                code, log = 124, "Contract command exceeded 90 seconds."
            log_path = args.output/(Path(script).stem+".log")
            log_path.write_text(log, encoding="utf-8")
            rows.append({"script": script, "passed": code == 0, "exit_code": code, "elapsed_s": time.perf_counter()-start})
            print(json.dumps(rows[-1]), flush=True)
        save(args.output/"contracts.json", {"evidence": "offline contracts, not conversation quality", "results": rows})
        return 0 if all(x["passed"] for x in rows) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
