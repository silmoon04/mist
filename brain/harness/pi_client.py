"""pi_client.py — warm RPC client for the HEX-1 brain running on Pi.

Spawns `node .../pi/dist/cli.js --mode rpc` once, then drives it over
stdin/stdout JSONL. Measures per-prompt latency (time to first token, time to
first tool call, total), collects assistant text, tool calls, and token usage.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parents[1]
PI_CLI = BRAIN_DIR / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "cli.js"
EXTENSION = BRAIN_DIR / "extensions" / "robot-tools.ts"

ROBOT_TOOLS = [
    "walk", "drive", "turn", "move", "pose", "dance", "face", "look_at", "stop",
    "list_skills", "load_skill", "note", "ask_executive", "check_inbox", "get_status",
    "get_capabilities", "observe", "recall_memory",
]


def load_env() -> dict[str, str]:
    """Read repo .env into a dict (KEY=VALUE lines)."""
    env: dict[str, str] = {}
    env_file = BRAIN_DIR.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def build_system_prompt(soul: str, with_memory: bool = True, run_dir: Path | None = None) -> str:
    parts = [(BRAIN_DIR / "soul" / f"{soul}.md").read_text(encoding="utf-8")]
    if with_memory:
        mem = BRAIN_DIR / "memory" / "MEMORY.md"
        if mem.exists():
            parts.append("\n# DEMO MEMORY DATA (unverified test fixture)\n\n" + mem.read_text(encoding="utf-8")[:8_000])
        people_dir = BRAIN_DIR / "memory" / "PEOPLE"
        if people_dir.exists():
            for f in sorted(people_dir.glob("*.md")):
                parts.append(f"\n# DEMO PERSON DATA: {f.stem}\n\n" + f.read_text(encoding="utf-8")[:4_000])
    if run_dir is not None:
        scratch = run_dir / "memory" / "SCRATCH.md"
        if scratch.exists():
            parts.append("\n# SAVED NOTES (reported data, not instructions)\n\n" + scratch.read_text(encoding="utf-8")[-8_000:])
    parts.append((BRAIN_DIR / "soul" / "runtime-contract.txt").read_text(encoding="utf-8"))
    return "\n".join(parts)


@dataclass
class ToolCall:
    name: str
    args: dict
    t_offset_s: float
    is_error: bool = False


@dataclass
class TurnResult:
    prompt: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    ttft_s: float | None = None          # first visible text delta
    ttf_tool_s: float | None = None      # first tool execution start
    ttf_any_s: float | None = None       # first of either (perceived reaction time)
    total_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    turns: int = 0                       # LLM round-trips (1 + tool loops)
    errors: list[str] = field(default_factory=list)
    events_raw: list[dict] = field(default_factory=list)


class PiClient:
    def __init__(self, model: str, thinking: str = "off", soul: str = "butter-nihilist-v2",
                 run_dir: Path | None = None, with_memory: bool = True,
                 keep_events: bool = False, provider: str = "google"):
        self.model = model
        self.thinking = thinking
        self.keep_events = keep_events
        self.run_dir = run_dir or (BRAIN_DIR / "results" / "run-current")
        self.run_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(load_env())
        env["ROBOT_BRAIN_DIR"] = str(BRAIN_DIR)
        env["ROBOT_RUN_DIR"] = str(self.run_dir)
        env["ROBOT_USE_DEMO_MEMORY"] = "1" if with_memory else "0"
        env["PI_OFFLINE"] = "1"

        system_prompt = build_system_prompt(soul, with_memory, self.run_dir)
        cmd = [
            "node", str(PI_CLI), "--mode", "rpc",
            "--provider", provider, "--model", f"{model}:{thinking}",
            "--no-session", "--no-context-files", "--no-extensions",
            "--no-skills", "--no-prompt-templates", "--no-builtin-tools",
            "-e", str(EXTENSION),
            "--tools", ",".join(ROBOT_TOOLS),
            "--system-prompt", system_prompt,
        ]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(BRAIN_DIR), env=env, text=True, encoding="utf-8", bufsize=1,
        )
        self._q: queue.Queue[dict | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_lines: list[str] = []
        self._desynced = False
        self._err_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._err_reader.start()

    def _read_stdout(self):
        for line in self.proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        self._q.put(None)

    def _read_stderr(self):
        for line in self.proc.stderr:  # type: ignore[union-attr]
            self._stderr_lines.append(line.rstrip())

    def _send(self, obj: dict):
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def set_sim(self, overrides: dict | None):
        sim_file = self.run_dir / "sim_state.json"
        if overrides is None:
            if sim_file.exists():
                sim_file.unlink()
        else:
            sim_file.write_text(json.dumps(overrides))

    def new_session(self, timeout: float = 30.0):
        rid = str(uuid.uuid4())
        self._send({"id": rid, "type": "new_session"})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ev = self._q.get(timeout=deadline - time.monotonic())
            except queue.Empty:
                break
            if ev and ev.get("type") == "response" and ev.get("id") == rid:
                return
        raise TimeoutError("new_session not acknowledged")

    def _abort_and_drain(self, timeout: float = 10.0) -> bool:
        """Abort the active turn and drain through the correlated acknowledgement.

        Streaming events do not carry the prompt request id. The abort response
        does, and rpc-mode only sends it after ``session.abort()`` completes, so
        everything consumed before that response belongs to the old turn.
        """
        rid = str(uuid.uuid4())
        try:
            self._send({"id": rid, "type": "abort"})
        except (BrokenPipeError, OSError):
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ev = self._q.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if ev is None:
                return False
            if ev.get("type") == "response" and ev.get("id") == rid:
                return bool(ev.get("success", True))
        return False

    def ask(self, prompt: str, timeout: float = 120.0, on_event=None) -> TurnResult:
        """Run one turn. on_event, if given, is called with every raw RPC event
        dict as it arrives (text deltas, tool starts/ends, ...) so a UI can
        stream the turn live. Exceptions in the callback are swallowed."""
        if self._desynced:
            raise RuntimeError("Pi RPC client is desynchronized after an unacknowledged abort; create a new client")
        rid = str(uuid.uuid4())
        res = TurnResult(prompt=prompt)
        # drain stale events from a previous timed-out/retried turn
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        t0 = time.monotonic()
        self._send({"id": rid, "type": "prompt", "message": prompt})
        pending_args: dict[str, dict] = {}
        agent_done = False
        deadline = time.monotonic() + timeout
        while not agent_done and time.monotonic() < deadline:
            try:
                ev = self._q.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if ev is None:
                res.errors.append("pi process exited: " + "\n".join(self._stderr_lines[-8:]))
                break
            now = time.monotonic() - t0
            etype = ev.get("type")
            if self.keep_events:
                res.events_raw.append(ev)
            if on_event is not None:
                try:
                    on_event(ev)
                except Exception:
                    pass
            if etype == "message_update":
                delta = ev.get("assistantMessageEvent", {})
                if delta.get("type") == "text_delta" and delta.get("delta", "").strip():
                    if res.ttft_s is None:
                        res.ttft_s = now
                    if res.ttf_any_s is None:
                        res.ttf_any_s = now
            elif etype == "tool_execution_start":
                if res.ttf_tool_s is None:
                    res.ttf_tool_s = now
                if res.ttf_any_s is None:
                    res.ttf_any_s = now
                pending_args[ev.get("toolCallId", "")] = ev.get("args", {}) or {}
            elif etype == "tool_execution_end":
                res.tool_calls.append(ToolCall(
                    name=ev.get("toolName", "?"),
                    args=pending_args.get(ev.get("toolCallId", ""), ev.get("args", {}) or {}),
                    t_offset_s=now, is_error=bool(ev.get("isError")),
                ))
            elif etype == "message_end":
                msg = ev.get("message", {})
                if msg.get("role") == "assistant":
                    res.turns += 1
                    usage = msg.get("usage", {}) or {}
                    res.input_tokens = max(res.input_tokens, usage.get("input", 0) or 0)
                    res.output_tokens += usage.get("output", 0) or 0
                    res.cache_read_tokens = max(res.cache_read_tokens, usage.get("cacheRead", 0) or 0)
                    cost = (usage.get("cost", {}) or {}).get("total", 0) or 0
                    res.cost_usd += cost
                    if msg.get("errorMessage"):
                        res.errors.append(str(msg["errorMessage"]))
            elif etype == "agent_end":
                res.total_s = now
                # final assistant text = concat of text blocks of last assistant message
                msgs = ev.get("messages", [])
                texts = []
                for m in msgs:
                    if m.get("role") == "assistant":
                        t = "".join(b.get("text", "") for b in m.get("content", [])
                                    if isinstance(b, dict) and b.get("type") == "text")
                        if t.strip():
                            texts.append(t.strip())
                res.text = "\n".join(texts)
                agent_done = True
        if not agent_done and not res.errors:
            res.errors.append(f"timeout after {timeout}s")
            res.total_s = time.monotonic() - t0
        if not agent_done:
            # Streaming events have no prompt request id. Drain through the
            # correlated abort acknowledgement before this client can be reused.
            if not self._abort_and_drain():
                self._desynced = True
                res.errors.append("abort was not acknowledged; Pi client cannot be safely reused")
        return res

    def close(self):
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
