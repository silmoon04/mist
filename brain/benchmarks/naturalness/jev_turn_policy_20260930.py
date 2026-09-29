"""Experimental, event-driven JEV turn advisor. No production wiring or key loading.

The synchronous, pooled JevClient from jev_comparison_20260929 is injected by the
caller. A single worker keeps its network request off the ASR event loop. Late
responses are retained in ``outcomes`` for benchmark accounting, but never act.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import inspect
from threading import Lock
from time import monotonic
from typing import Any, Literal

try:
    from .jev_comparison_20260929 import MODEL
except ImportError:
    from jev_comparison_20260929 import MODEL


Decision = Literal["wait", "take_turn", "keep_speaking", "yield", "uncertain"]
Event = Literal["candidate_endpoint", "stable_overlap"]
CHOICES = ("wait", "take_turn", "keep_speaking", "yield", "uncertain")
QUESTION = {
    "turn_intent": {
        "type": "choice",
        "instructions": (
            "Choose the next turn action from the latest user transcript, relevant "
            "short history, and trusted local speaking and turn-ready state. "
            "wait means the user appears to be continuing an unfinished clause; "
            "take_turn means process a completed substantive turn; keep_speaking "
            "means a listener backchannel or encouragement while the assistant speaks; "
            "yield means the user is asking the assistant to stop or interrupt now; "
            "uncertain means context does not support a confident action. A quoted, "
            "negated, or reported use of 'stop' is not by itself a stop request. "
            "An incoming user backchannel is not permission to emit an outgoing cue."
        ),
        "criteria": {
            "wait": "Wait briefly for more user speech.",
            "take_turn": "The user has completed a substantive turn.",
            "keep_speaking": "The assistant should continue its current speech.",
            "yield": "The assistant should stop its current speech for the user.",
            "uncertain": "Insufficient evidence for a semantic turn action.",
        },
    }
}


@dataclass(frozen=True)
class TurnSnapshot:
    endpoint_id: str
    epoch: int
    revision: int
    transcript: str
    event: Event
    short_history: tuple[tuple[str, str], ...] = ()
    assistant_is_speaking: bool = False
    local_turn_ready: bool = False
    user_floor_held: bool = False
    speech_active: bool = False
    overlap_stable: bool = False
    explicit_local_stop: bool = False

    def __post_init__(self) -> None:
        if not self.endpoint_id or self.epoch < 0 or self.revision < 0:
            raise ValueError("endpoint ID, nonnegative epoch, and revision required")
        if self.event not in ("candidate_endpoint", "stable_overlap"):
            raise ValueError("unsupported turn event")
        if len(self.short_history) > 6 or any(role not in ("user", "assistant")
                                              for role, _ in self.short_history):
            raise ValueError("short_history must contain at most six user/assistant turns")

    def provider_state(self) -> dict[str, Any]:
        return {
            "latest_user_transcript": self.transcript,
            "short_history": [{"role": role, "text": text}
                              for role, text in self.short_history],
            "assistant_is_speaking": self.assistant_is_speaking,
            "local_turn_ready": self.local_turn_ready,
            "user_floor_held": self.user_floor_held,
            "speech_active": self.speech_active,
            "event": self.event,
        }

    def identity(self) -> tuple[Any, ...]:
        return (self.endpoint_id, self.epoch, self.revision, self.transcript,
                self.assistant_is_speaking, self.local_turn_ready,
                self.user_floor_held, self.speech_active)


@dataclass(frozen=True)
class TurnAdvice:
    decision: Decision
    latency_ms: float
    raw: dict[str, Any] | None
    usage: dict[str, Any] | None
    source: str
    endpoint_id: str
    epoch: int
    revision: int
    applicable: bool
    hold_until: float | None = None


def _local_fallback(snapshot: TurnSnapshot) -> Decision:
    if snapshot.explicit_local_stop:
        return "yield"
    if snapshot.speech_active or snapshot.user_floor_held:
        return "wait"
    if snapshot.local_turn_ready:
        return "take_turn"
    if snapshot.assistant_is_speaking:
        return "keep_speaking"
    return "wait"


class JEVTurnPolicy:
    """One in-flight network request; caller must close the injected client."""

    def __init__(self, client: Any, *, deadline_ms: int = 500,
                 grace_ms: int = 1500, total_deadline_ms: int = 2000,
                 max_outcomes: int = 128):
        if (deadline_ms <= 0 or grace_ms <= 0 or max_outcomes <= 0
                or total_deadline_ms < deadline_ms):
            raise ValueError("policy limits must be positive")
        self.client = client
        self.deadline_ms = deadline_ms
        self.grace_ms = grace_ms
        self.total_deadline_ms = total_deadline_ms
        self._async_client = inspect.iscoroutinefunction(client.decide)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-turn")
        self._remote: Future | asyncio.Task | None = None
        self._remote_started: float | None = None
        self._current: TurnSnapshot | None = None
        self._tasks: OrderedDict[tuple[int, str], asyncio.Task[TurnAdvice]] = OrderedDict()
        self._outcomes: OrderedDict[tuple[int, str], dict[str, Any]] = OrderedDict()
        self._outcomes_lock = Lock()
        self._max_outcomes = max_outcomes

    def update_current(self, snapshot: TurnSnapshot) -> None:
        """Call on changed transcript, epoch, speech state, or local turn state."""
        self._current = snapshot

    def _applicable(self, snapshot: TurnSnapshot) -> bool:
        return self._current is not None and self._current.identity() == snapshot.identity()

    def _record(self, key: tuple[int, str], result: dict[str, Any]) -> None:
        with self._outcomes_lock:
            self._outcomes[key] = result
            self._outcomes.move_to_end(key)
            while len(self._outcomes) > self._max_outcomes:
                self._outcomes.popitem(last=False)

    @property
    def outcomes(self) -> dict[tuple[int, str], dict[str, Any]]:
        """Thread-safe snapshot, including completed responses after a deadline."""
        with self._outcomes_lock:
            return dict(self._outcomes)

    def submit(self, snapshot: TurnSnapshot) -> asyncio.Task[TurnAdvice]:
        """Schedule an endpoint event without blocking the ASR receive loop."""
        key = (snapshot.epoch, snapshot.endpoint_id)
        existing = self._tasks.get(key)
        if existing is not None:
            if self._current is not None and (snapshot.epoch, snapshot.revision) > (
                    self._current.epoch, self._current.revision):
                self.update_current(snapshot)
            return existing
        self.update_current(snapshot)
        task = asyncio.create_task(self.classify(snapshot))
        self._tasks[key] = task
        self._tasks.move_to_end(key)
        while len(self._tasks) > self._max_outcomes:
            self._tasks.popitem(last=False)
        return task

    async def classify(self, snapshot: TurnSnapshot) -> TurnAdvice:
        """Return decision, wall latency, raw result and usage for one event.

        Direct callers should call ``update_current`` first. ``submit`` does that
        automatically. A late response cannot be applied after state changes.
        """
        key = (snapshot.epoch, snapshot.endpoint_id)
        start = monotonic()
        fallback = _local_fallback(snapshot)

        def advice(decision: Decision, source: str, raw: dict[str, Any] | None = None,
                   usage: dict[str, Any] | None = None) -> TurnAdvice:
            applicable = self._applicable(snapshot)
            if not applicable:
                decision, source = "uncertain", "stale"
            hold_until = None
            if applicable and source == "jev" and decision == "wait":
                deadline = start + self.grace_ms / 1000
                if monotonic() < deadline:
                    hold_until = deadline
                else:
                    decision, source = fallback, "grace_expired"
            return TurnAdvice(decision, round((monotonic() - start) * 1000, 3),
                              raw, usage, source, snapshot.endpoint_id,
                              snapshot.epoch, snapshot.revision, applicable, hold_until)

        if snapshot.explicit_local_stop:
            return advice("yield", "local_explicit_stop")
        if not snapshot.transcript.strip():
            return advice(fallback, "empty_transcript")
        if snapshot.event == "stable_overlap" and not snapshot.overlap_stable:
            return advice(fallback, "unstable_overlap")
        if self._remote is not None and not self._remote.done():
            return advice(fallback, "busy_fallback")

        if self._async_client:
            async def bounded_request() -> dict[str, Any]:
                try:
                    return await asyncio.wait_for(
                        self.client.decide(snapshot.provider_state(), QUESTION),
                        self.total_deadline_ms / 1000)
                except asyncio.TimeoutError:
                    return {"status": "error", "request_model": MODEL,
                            "response": None, "error_type": "total_deadline",
                            "usage_known": False, "wall_latency_ms": self.total_deadline_ms,
                            "transport": "httpx_async_pooled_client"}

            future = asyncio.create_task(bounded_request())
        else:
            future = self._pool.submit(self.client.decide, snapshot.provider_state(), QUESTION)
        self._remote = future
        self._remote_started = monotonic()

        def retain_late(done: Future | asyncio.Task) -> None:
            try:
                result = done.result()
            except BaseException as exc:
                result = {"status": "error", "error_type": type(exc).__name__}
            self._record(key, result)

        future.add_done_callback(retain_late)
        try:
            wrapped = future if isinstance(future, asyncio.Task) else asyncio.wrap_future(future)
            raw = await asyncio.wait_for(asyncio.shield(wrapped), self.deadline_ms / 1000)
        except asyncio.TimeoutError:
            return advice(fallback, "deadline_fallback")
        except Exception:
            return advice(fallback, "client_error_fallback")

        usage = raw.get("response", {}).get("usage") if raw.get("response") else None
        if raw.get("status") != "ok":
            return advice(fallback, "provider_error_fallback", raw, usage)
        choice = raw.get("response", {}).get("answers", {}).get("turn_intent", {}).get("choice")
        if choice not in CHOICES:
            return advice(fallback, "invalid_choice_fallback", raw, usage)
        if choice == "uncertain":
            return advice(fallback, "uncertain_fallback", raw, usage)
        if (choice == "take_turn" and (not snapshot.local_turn_ready or snapshot.speech_active
                                       or snapshot.user_floor_held)) or (
                choice in ("keep_speaking", "yield") and not snapshot.assistant_is_speaking):
            return advice(fallback, "local_state_fallback", raw, usage)
        return advice(choice, "jev", raw, usage)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def drain(self, *, timeout_s: float | None = None) -> bool:
        """Wait briefly for a late request; false means it may still be running.

        Call between trials and do not start another trial when this returns
        false. A synchronous network call cannot be forcibly killed by asyncio.
        """
        future = self._remote
        if future is None:
            return True
        if timeout_s is not None:
            limit = timeout_s
        elif self._async_client:
            elapsed = monotonic() - (self._remote_started or monotonic())
            limit = max(0.25, self.total_deadline_ms / 1000 - elapsed + 0.25)
        else:
            limit = min(float(getattr(self.client, "timeout_s", 5.0)), 5.0)
        if limit <= 0:
            raise ValueError("drain timeout must be positive")
        try:
            wrapped = future if isinstance(future, asyncio.Task) else asyncio.wrap_future(future)
            await asyncio.wait_for(asyncio.shield(wrapped), limit)
        except asyncio.TimeoutError:
            return False
        except BaseException:
            pass
        return True

    async def aclose(self, *, timeout_s: float | None = None) -> bool:
        """Drain and close the worker; false signals an unresolved request."""
        drained = await self.drain(timeout_s=timeout_s)
        if not drained and isinstance(self._remote, asyncio.Task):
            self._remote.cancel()
            try:
                await asyncio.wait_for(self._remote, .25)
            except BaseException:
                pass
            drained = self._remote.done()
        self.close()
        return drained
