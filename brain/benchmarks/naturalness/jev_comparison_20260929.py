"""Bounded text-only JEV microdecision benchmark. No production wiring or audio."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx


HERE = Path(__file__).resolve().parent
BRAIN = HERE.parents[1]
RESULT = BRAIN / "results" / "jev-hardbench-20260929" / "jev-live"
API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
INPUT_USD_PER_MILLION = 0.042
MAX_ATTEMPTS = 64


def _valid_probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def validate_answers(questions: dict, response: dict) -> dict:
    """Return a normalized copy, rejecting missing or malformed typed answers."""
    if not isinstance(response, dict) or response.get("model") != MODEL:
        raise ValueError("response model differs from pinned model")
    answers = response.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("response question IDs differ from request")
    normalized = {}
    for qid, question in questions.items():
        answer = answers[qid]
        qtype = question.get("type")
        if not isinstance(answer, dict) or answer.get("type") != qtype:
            raise ValueError(f"invalid answer type for {qid}")
        if qtype == "noul":
            if not _valid_probability(answer.get("noul")):
                raise ValueError(f"invalid noul for {qid}")
            normalized[qid] = {"type": "noul", "noul": float(answer["noul"])}
        elif qtype == "choice":
            options = question.get("criteria")
            probs = answer.get("probabilities")
            choice = answer.get("choice")
            if not isinstance(options, dict) or not isinstance(probs, dict) or set(probs) != set(options) \
                    or choice not in options or not all(_valid_probability(p) for p in probs.values()) \
                    or abs(sum(probs.values()) - 1) > 0.02 or not _valid_probability(answer.get("confidence")):
                raise ValueError(f"invalid choice distribution for {qid}")
            normalized[qid] = {"type": "choice", "choice": choice,
                               "probabilities": {k: float(v) for k, v in probs.items()},
                               "confidence": float(answer["confidence"])}
        else:
            raise ValueError(f"unsupported question type for {qid}")
    usage = response.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise ValueError("invalid usage")
    usage = usage or {}
    for field in ("input_tokens", "output_tokens"):
        if field in usage and (not isinstance(usage[field], int) or isinstance(usage[field], bool) or usage[field] < 0):
            raise ValueError(f"invalid {field}")
    return {"model": response["model"], "answers": normalized,
            "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens")}}


class JevClient:
    """One request per decision; never retries, and does not log credentials."""

    def __init__(self, api_key: str, *, timeout_s: float = 12, opener=None,
                 transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ValueError("JEV API key is missing")
        self._api_key = api_key
        self.timeout_s = timeout_s
        self._opener = opener
        self.transport_name = "urllib_injected" if opener is not None else "httpx_pooled_client"
        self._http = None if opener is not None else httpx.Client(
            timeout=timeout_s, transport=transport, follow_redirects=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4))

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    def decide(self, state: str | dict | list, questions: dict) -> dict:
        if not isinstance(state, (str, dict, list)) or not isinstance(questions, dict) or not questions:
            raise ValueError("state and nonempty questions required")
        body = json.dumps({"state": state, "model": MODEL, "questions": questions},
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": "Bearer " + self._api_key,
                   "Content-Type": "application/json"}
        started = time.perf_counter()
        row: dict[str, Any] = {"status": "error", "request_model": MODEL, "http_status": None,
                               "wall_latency_ms": None, "error_type": None,
                               "response": None, "usage_known": False,
                               "estimated_input_cost_usd": None, "transport": self.transport_name}
        try:
            if self._http is not None:
                response = self._http.post(API_URL, content=body, headers=headers)
                row["http_status"] = response.status_code
                if response.status_code >= 400:
                    row["error_type"] = "http_error"
                    return row
                parsed = response.json()
            else:
                request = Request(API_URL, data=body, method="POST", headers=headers)
                with self._opener(request, timeout=self.timeout_s) as response:
                    row["http_status"] = response.status
                    parsed = json.loads(response.read())
            row["response"] = validate_answers(questions, parsed)
            row["status"] = "ok"
            tokens = row["response"]["usage"]["input_tokens"]
            row["usage_known"] = tokens is not None
            if tokens is not None:
                row["estimated_input_cost_usd"] = tokens * INPUT_USD_PER_MILLION / 1_000_000
        except HTTPError as exc:
            row["http_status"] = exc.code
            row["error_type"] = "http_error"
        except httpx.TimeoutException:
            row["error_type"] = "timeout"
        except httpx.RequestError:
            row["error_type"] = "network_error"
        except (TimeoutError, URLError, OSError) as exc:
            row["error_type"] = "timeout" if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() else "network_error"
        except (ValueError, UnicodeError) as exc:
            row["error_type"] = "response_validation_error"
        finally:
            row["wall_latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return row


def load_key(env_path: Path) -> str:
    """Read only the exact JEV_API_KEY assignment; do not copy or print .env."""
    if os.environ.get("JEV_API_KEY"):
        return os.environ["JEV_API_KEY"].strip()
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip().removeprefix("export ").strip() == "JEV_API_KEY":
            return value.strip().strip('"\'')
    raise RuntimeError("JEV_API_KEY unavailable")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def score_choice(answer: dict, allowed: list[str]) -> dict:
    """Chosen-option Brier component: (P(chosen) - correctness)^2."""
    if answer["type"] != "choice" or not allowed:
        raise ValueError("choice answer and nonempty allowed labels required")
    correct = answer["choice"] in allowed
    p = answer["probabilities"][answer["choice"]]
    return {"correct": correct, "chosen_probability": p,
            "chosen_probability_brier": round((p - int(correct)) ** 2, 8)}


def _case_parts(case: dict, shared_questions: dict | None = None) -> tuple[Any, dict, dict]:
    state = case.get("state")
    questions = case.get("questions", shared_questions)
    if "question_ids" in case:
        selected = case["question_ids"]
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) \
                or not isinstance(questions, dict) or not set(selected) <= set(questions):
            raise ValueError(f"invalid question selector for {case.get('id')}")
        questions = {qid: questions[qid] for qid in selected}
    expected = case.get("expected", {})
    if state is None or not isinstance(questions, dict) or not questions:
        raise ValueError(f"case {case.get('id')} lacks state/questions")
    return state, questions, expected


def run_cases(cases_path: Path, output_dir: Path, client: JevClient, *,
              gold_path: Path | None = None, repetitions: int = 2) -> dict:
    if repetitions != 2:
        raise ValueError("this frozen protocol requires two repetitions")
    raw = cases_path.read_bytes()
    source = json.loads(raw)
    cases = source["cases"] if isinstance(source, dict) else source
    shared_questions = source.get("questions") if isinstance(source, dict) else None
    if isinstance(source, dict) and source.get("model", MODEL) != MODEL:
        raise ValueError("input model differs from pinned model")
    if not isinstance(cases, list) or not cases or len(cases) * repetitions > MAX_ATTEMPTS:
        raise ValueError("case count exceeds bounded protocol")
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case IDs")
    for case in cases:
        _case_parts(case, shared_questions)
    if any(path.exists() for path in (output_dir / "plan.frozen.json", output_dir / "attempts.json",
                                      output_dir / "report.json")):
        raise FileExistsError("scored output already exists; choose a fresh output directory")
    gold = {}
    gold_digest = None
    if gold_path is not None:
        gold_raw = gold_path.read_bytes()
        gold_digest = hashlib.sha256(gold_raw).hexdigest()
        gold_cases = json.loads(gold_raw)["cases"]
        gold = {case["id"]: case for case in gold_cases}
        if len(gold) != len(cases) or set(gold) != set(ids):
            raise ValueError("gold and provider input case IDs differ")
        for case in cases:
            reference = gold[case["id"]]
            if case["state"].get("latest_user_transcript") != reference["transcript"]:
                raise ValueError(f"transcript differs from frozen gold for {case['id']}")
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    snapshot = {"case_sha256": digest, "gold_sha256": gold_digest,
                "source_file": cases_path.name, "gold_file": gold_path.name if gold_path else None,
                "cases": cases, "questions": shared_questions, "model": MODEL, "repetitions": repetitions,
                "request_cap": MAX_ATTEMPTS, "created_utc": datetime.now(timezone.utc).isoformat()}
    _atomic_json(output_dir / "plan.frozen.json", snapshot)
    schedule = [(case, rep) for rep in range(1, repetitions + 1) for case in cases]
    random.Random(20260929).shuffle(schedule)
    rows = []
    for order, (case, rep) in enumerate(schedule, 1):
        state, questions, expected = _case_parts(case, shared_questions)
        reference = gold.get(case["id"])
        if reference is not None:
            expected = {"turn_intent": reference["allowed"]}
            if "allowed_expression" in reference:
                expected["expression_intent"] = reference["allowed_expression"]
            if "route" in reference:
                expected["route"] = reference["route"]
        result = client.decide(state, questions)
        row = {"order": order, "case_id": case["id"], "repetition": rep,
               "family": reference.get("family") if reference else case.get("family"),
               "state_flags": {k: state.get(k) for k in ("assistant_is_speaking", "user_floor_held",
                                "quiet_requested", "speaking_practice") if isinstance(state, dict) and k in state},
               **result, "scores": {}}
        if result["status"] == "ok" and isinstance(expected, dict):
            for qid, labels in expected.items():
                if qid in result["response"]["answers"]:
                    if isinstance(labels, str):
                        labels = [labels]
                    if isinstance(labels, list) and result["response"]["answers"][qid]["type"] == "choice":
                        row["scores"][qid] = {"allowed": labels, **score_choice(result["response"]["answers"][qid], labels)}
        rows.append(row)
        _atomic_json(output_dir / "attempts.json", rows)
    scored = [score for row in rows for score in row["scores"].values()]
    times = sorted(row["wall_latency_ms"] for row in rows if row["status"] == "ok")
    by_question = {}
    by_family = {}
    for row in rows:
        family = by_family.setdefault(row["family"] or "unspecified", {"attempts": 0, "errors": 0,
                                                                         "intent_scored": 0, "intent_correct": 0})
        family["attempts"] += 1
        family["errors"] += row["status"] != "ok"
        for qid, score in row["scores"].items():
            item = by_question.setdefault(qid, {"scored": 0, "correct": 0, "brier_sum": 0.0})
            item["scored"] += 1
            item["correct"] += score["correct"]
            item["brier_sum"] += score["chosen_probability_brier"]
            if qid == "turn_intent":
                family["intent_scored"] += 1
                family["intent_correct"] += score["correct"]
    for item in by_question.values():
        item["mean_chosen_probability_brier"] = item.pop("brier_sum") / item["scored"]
    report = {"case_sha256": digest, "gold_sha256": gold_digest, "model": MODEL,
              "transport": getattr(client, "transport_name", "injected_test_client"),
              "attempts": len(rows),
              "successes": sum(row["status"] == "ok" for row in rows),
              "errors": sum(row["status"] != "ok" for row in rows),
              "scored_decisions": len(scored),
              "correct_decisions": sum(score["correct"] for score in scored),
              "mean_chosen_probability_brier": (sum(s["chosen_probability_brier"] for s in scored) / len(scored) if scored else None),
              "latency_ms_median_success": (statistics.median(times) if times else None),
              "latency_ms_p95_success": (times[math.ceil(0.95 * len(times)) - 1] if times else None),
              "first_request_wall_latency_ms": rows[0]["wall_latency_ms"],
              "subsequent_latency_ms_median_success": (statistics.median(row["wall_latency_ms"] for row in rows[1:]
                  if row["status"] == "ok") if any(row["status"] == "ok" for row in rows[1:]) else None),
              "by_question": by_question, "by_family": by_family,
              "known_input_tokens": sum(row["response"]["usage"]["input_tokens"] or 0 for row in rows if row["response"]),
              "usage_unknown_attempts": sum(not row["usage_known"] for row in rows),
              "estimated_input_cost_usd_known_only": sum(row["estimated_input_cost_usd"] or 0 for row in rows),
              "limitations": ["UK host wall latency is text API time, not speech latency.",
                              "This small labelled sample cannot establish probability calibration.",
                              "No hardware, acoustic, or naturalness outcome is measured."]}
    _atomic_json(output_dir / "report.json", report)
    return report


def summarize_saved_run(output_dir: Path, cases_path: Path, gold_path: Path) -> dict:
    """Analyze saved attempts only. This function performs no provider requests."""
    rows = json.loads((output_dir / "attempts.json").read_text(encoding="utf-8"))
    plan = json.loads((output_dir / "plan.frozen.json").read_text(encoding="utf-8"))
    report_path = output_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    input_raw = cases_path.read_bytes()
    gold_raw = gold_path.read_bytes()
    input_hash = hashlib.sha256(input_raw).hexdigest()
    gold_hash = hashlib.sha256(gold_raw).hexdigest()
    if (input_hash != plan["case_sha256"] or gold_hash != plan["gold_sha256"]
            or len(rows) != 60 or {row["order"] for row in rows} != set(range(1, 61))):
        raise ValueError("saved run and frozen source differ")
    provider_cases = {case["id"]: case for case in json.loads(input_raw)["cases"]}
    gold_cases = {case["id"]: case for case in json.loads(gold_raw)["cases"]}
    wrong = []
    by_case_question = {}
    for row in rows:
        for qid, score in row["scores"].items():
            by_case_question.setdefault((row["case_id"], qid), []).append(score["correct"])
            if score["correct"]:
                continue
            answer = row["response"]["answers"][qid]
            state = provider_cases[row["case_id"]]["state"]
            wrong.append({"case_id": row["case_id"], "repetition": row["repetition"],
                          "question": qid, "family": gold_cases[row["case_id"]]["family"],
                          "transcript": state["latest_user_transcript"],
                          "state_flags": row["state_flags"], "allowed": score["allowed"],
                          "chosen": answer["choice"], "chosen_probability": score["chosen_probability"],
                          "confidence": answer["confidence"]})
    times = [row["wall_latency_ms"] for row in rows]
    by_question_count = {}
    for row in rows:
        count = len(row["response"]["answers"]) if row["response"] else len(provider_cases[row["case_id"]]["question_ids"])
        item = by_question_count.setdefault(str(count), {"requests": 0, "latencies_ms": [],
                                                         "input_tokens_known": 0})
        item["requests"] += 1
        item["latencies_ms"].append(row["wall_latency_ms"])
        if row["response"]:
            item["input_tokens_known"] += row["response"]["usage"]["input_tokens"] or 0
    for item in by_question_count.values():
        item["median_wall_latency_ms"] = statistics.median(item.pop("latencies_ms"))
    analysis = {"wrong_verdicts": wrong,
                "wrong_case_ids": sorted({item["case_id"] for item in wrong}),
                "repeat_consistency": {"unit": "case-question pair across two repetitions",
                                       "both_correct": sum(all(scores) for scores in by_case_question.values()),
                                       "both_wrong": sum(not any(scores) for scores in by_case_question.values()),
                                       "mixed": sum(any(scores) and not all(scores)
                                                    for scores in by_case_question.values())},
                "deadlines_exceeded_ms": {str(deadline): sum(value > deadline for value in times)
                                          for deadline in (300, 450, 700)},
                "by_question_count": by_question_count,
                "high_probability_wrong_ge_0_8": sum(item["chosen_probability"] >= 0.8 for item in wrong),
                "high_confidence_wrong_ge_0_8": sum(item["confidence"] >= 0.8 for item in wrong)}
    _atomic_json(output_dir / "analysis.json", analysis)
    report["latency_ms_median_success"] = statistics.median(times)
    report["subsequent_latency_ms_median_success"] = statistics.median(times[1:])
    report["analysis_file"] = "analysis.json"
    _atomic_json(report_path, report)
    provenance_paths = {"client_source": Path(__file__), "provider_input": cases_path,
                        "gold_labels": gold_path,
                        "lab_protocol": BRAIN / "results/jev-hardbench-20260929/LAB_METHODS.md",
                        "jev_contract": BRAIN / "results/jev-hardbench-20260929/JEV.md",
                        "plan_snapshot": output_dir / "plan.frozen.json",
                        "attempts_run_order_and_outputs": output_dir / "attempts.json"}
    provenance = {"sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                             for name, path in provenance_paths.items()},
                  "note": "Client source hash is post-run; a median display correction was made after requests. "
                          "Provider input and gold hashes match the pre-call plan snapshot. "
                          "After the run, a fixture regeneration overwrote the canonical paths. "
                          "The approved input was restored byte-for-byte from the pre-call plan, and the approved "
                          "gold bytes were recovered by removing the post-run transcript_finalized field; both "
                          "recovered hashes match the pre-call plan. The unrun proposal is preserved in "
                          "semantic/UNRUN-proposal-v2. No credentials are stored in these artifacts."}
    _atomic_json(output_dir / "provenance.json", provenance)
    lines = ["# JEV live microdecision results", "",
             "Pinned `jev-1.13.0`; 30 text cases, two repetitions, 60 pooled HTTP requests and 78 selected Choice verdicts.",
             f"All {report['successes']} requests succeeded; {report['correct_decisions']}/{report['scored_decisions']} raw labels matched an allowed set.",
             f"Intent {report['by_question']['turn_intent']['correct']}/60; route {report['by_question']['route']['correct']}/8; expression {report['by_question']['expression_intent']['correct']}/10.",
             "", "The eight misses are the same four cases in both repetitions:", "",
             "| Case | Text | Allowed | Chosen and probability, rep 1 / rep 2 |", "|---|---|---|---|"]
    for case_id in analysis["wrong_case_ids"]:
        misses = sorted((item for item in wrong if item["case_id"] == case_id), key=lambda item: item["repetition"])
        first = misses[0]
        chosen = " / ".join(f"{item['chosen']} {item['chosen_probability']:.2f}" for item in misses)
        lines.append(f"| {case_id} | {first['transcript']} | {', '.join(first['allowed'])} | {chosen} |")
    lines += ["", f"No wrong chosen option had probability ≥0.8 ({analysis['high_probability_wrong_ge_0_8']}/8). "
              "These scores are descriptive; 30 cases cannot establish calibration.",
              f"UK-host request wall time: median {report['latency_ms_median_success']:.1f} ms; "
              f"p95 {report['latency_ms_p95_success']:.1f} ms; first request {report['first_request_wall_latency_ms']:.1f} ms; "
              f"later-request median {report['subsequent_latency_ms_median_success']:.1f} ms. "
              "The first/later difference is observational, not a cold-versus-warm causal experiment.",
              "", "| Question count | Requests | Median wall ms | Known input tokens |", "|---:|---:|---:|---:|"]
    for count, item in sorted(by_question_count.items()):
        lines.append(f"| {count} | {item['requests']} | {item['median_wall_latency_ms']:.1f} | {item['input_tokens_known']} |")
    lines += ["", "Deadline exceedances (descriptive): " + ", ".join(
        f">{k} ms: {v}/60" for k, v in analysis["deadlines_exceeded_ms"].items()) + ".",
        f"Usage was returned for every request: {report['known_input_tokens']} input tokens, "
        f"estimated listed input charge ${report['estimated_input_cost_usd_known_only']:.6f}. "
        "Output tokens are returned but listed as free; this estimate excludes account-specific adjustments.",
        "", "These are raw semantic labels before MIST's local stop, quiet, practice, stale-response, and timing gates. "
        "No microphone, playback, hardware, or speech naturalness outcome was measured.",
        "", "The four repeated misses need semantic review. S17's 'Actually' can reasonably indicate a correction, "
        "and S19's 'Maybe later' may end the current offer; these allowed-label sets merit human adjudication. "
        "S28/S29 choose `keep_speaking` while the provider state says MIST is silent; "
        "local quiet/practice controls would still veto an outgoing cue. Do not tune against this visible set and call it held-out evidence.",
        "", "The canonical fixtures were overwritten after the run by an unrun proposal, then restored to the "
        "exact approved hashes recorded before the first call. The proposal is preserved under `semantic/UNRUN-proposal-v2`; "
        "this run uses the original frozen labels and inputs.",
        "", "Hashes and full request order are recorded in `provenance.json` and `plan.frozen.json`. "
        "The standalone schema smoke is separate from these 60 attempts."]
    (output_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return analysis


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--output", type=Path, default=RESULT)
    parser.add_argument("--env", type=Path, default=Path.home() / "Downloads/03_Programming_Projects/misc/.env")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    if args.summarize_only:
        if args.cases is None or args.gold is None:
            parser.error("--cases and --gold required for saved-run analysis")
        print(json.dumps(summarize_saved_run(args.output, args.cases, args.gold)))
        return
    client = JevClient(load_key(args.env))
    try:
        if args.smoke:
            question = {"turn_action": {"type": "choice", "instructions": "Classify the fictional user's brief acknowledgement.",
                                        "criteria": {"listener_cue": "Brief acknowledgment only", "take_turn": "New substantive request"}}}
            result = client.decide({"user_transcript": {"text": "Mm-hm", "status": "final"}}, question)
            _atomic_json(args.output / "schema-smoke.json", result)
            print(json.dumps({"status": result["status"], "http_status": result["http_status"],
                              "error_type": result["error_type"]}))
        else:
            if args.cases is None:
                parser.error("--cases is required for the benchmark")
            print(json.dumps(run_cases(args.cases, args.output, client, gold_path=args.gold)))
    finally:
        client.close()


if __name__ == "__main__":
    main()
