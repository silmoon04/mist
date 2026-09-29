"""Local durable journals of observable conversation events, without credentials.

Rows keep the DebugJournal envelope. elapsed_ms is this server's receipt clock;
timestamps inside an event retain their source and are not presumed aligned.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import deque
from datetime import datetime, timezone
import copy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Iterable
import uuid


MAX_PAGE = 400
TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
REDACTED = "[redacted]"
SECRET_KEYS = frozenset({
    "authorization", "authentication", "auth", "cookie", "setcookie", "token",
    "apikey", "xiapikey", "xapikey", "accesstoken", "refreshtoken", "idtoken",
    "password", "passwd", "clientsecret", "secret", "credentials", "credential",
    "paircode", "pairingcode", "pairingtoken", "sessiontoken", "authtoken",
})
PRIVATE_KEYS = frozenset({
    "reasoning", "privatereasoning", "chainofthought", "cot", "analysis", "thinking",
    "reasoningcontent", "reasoningtext", "reasoningdelta", "encryptedreasoning",
    "encryptedcontent", "internalthoughts", "scratchpad",
})
PAYLOAD_KEYS = frozenset({
    "pcm", "pcmb64", "pcmbase64", "audiobase64", "audiob64", "audiodata",
    "audiobytes", "rawaudio", "waveform", "binary", "binarydata", "blob",
})
TERMINAL = frozenset({"ended", "interrupted", "failed", "aborted", "cancelled", "completed"})


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


class _Cleaner:
    def __init__(self, secrets: Iterable[str] = ()):
        self.secrets = tuple(sorted({value for value in secrets if isinstance(value, str) and value}, key=len, reverse=True))

    def text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, REDACTED)
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer " + REDACTED, value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", REDACTED, value)
        value = re.sub(r"(?i)((?:api[_-]?key|xi[_-]?api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|client[_-]?secret|pair[_-]?code)[\"']?\s*[=:]\s*[\"']?)([^\s&;,\"'}]+)",
                       lambda match: match[1] + REDACTED, value)
        value = re.sub(r"(?i)data:audio/[^;,\s]+(?:;[^,\s]+)*,[A-Za-z0-9+/=_-]+", "[audio payload redacted]", value)
        return value

    def clean(self, value, depth=0, active=None, private_context=False):
        if depth > 100:
            return "[nesting limit]"
        if active is None:
            active = set()
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"binary_payload_redacted": True, "bytes": len(value)}
        if value is None or isinstance(value, (int, bool)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, (dict, list, tuple)):
            if id(value) in active:
                return "[circular reference]"
            active.add(id(value))
            try:
                if isinstance(value, dict):
                    event_type = str(value.get("type", "")).casefold()
                    event_method = str(value.get("method", "")).casefold()
                    private_event = private_context or value.get("channel") in ("analysis", "reasoning", "thinking") or bool(re.search(
                        r"(?:^|[._/ -])(?:reasoning|analysis|thinking|chain_of_thought)(?:$|[._/ -])", event_type + " " + event_method))
                    audio_event = "audio" in event_type or event_type in ("mic", "microphone", "pcm")
                    output = {}
                    for key, item in value.items():
                        key = str(key)
                        normalized = _key(key)
                        secret_field = normalized in SECRET_KEYS or normalized.endswith(("apikey", "accesstoken", "refreshtoken", "clientsecret"))
                        private_field = normalized in PRIVATE_KEYS or private_event and normalized in ("text", "delta", "content", "summary", "message")
                        binary_field = normalized in PAYLOAD_KEYS or normalized == "audio" and not isinstance(item, dict)
                        binary_field |= audio_event and normalized in ("data", "payload", "base64", "buffer")
                        binary_field |= audio_event and "transcript" not in event_type and normalized == "delta"
                        if binary_field:
                            continue
                        if secret_field or private_field:
                            output[self.text(key)] = REDACTED
                        else:
                            output[self.text(key)] = self.clean(item, depth+1, active, private_event)
                    return output
                if len(value) == 2 and isinstance(value[0], str) and _key(value[0]) in SECRET_KEYS:
                    return [self.text(value[0]), REDACTED]
                return [self.clean(item, depth+1, active, private_context) for item in value]
            finally:
                active.remove(id(value))
        return self.text(str(value))


def _atomic_json(path: Path, value: dict):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write((json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class TrialJournal(logging.Handler):
    def __init__(self, directory: Path, metadata: dict, cleaner: _Cleaner, *, new=False):
        super().__init__()
        self.directory = directory
        self.trace_id = metadata["trace_id"]
        self._metadata = metadata
        self._cleaner = cleaner
        self._guard = threading.RLock()
        self._started = time.perf_counter()
        self._path = directory / "events.jsonl"
        self._index = []
        self._ids = []
        self.sequence = 0
        if new:
            with self._path.open("xb"):
                pass
        else:
            self._scan()

    @property
    def metadata(self) -> dict:
        with self._guard:
            return copy.deepcopy(self._cleaner.clean({**self._metadata, "event_count": len(self._index), "head": self.sequence}))

    def _scan(self):
        recovery = []
        try:
            with self._path.open("rb") as handle:
                line_number = 0
                while True:
                    offset = handle.tell()
                    raw = handle.readline()
                    if not raw:
                        break
                    line_number += 1
                    reason = None
                    try:
                        row = json.loads(raw)
                        row_id = row.get("id") if isinstance(row, dict) else None
                        if not isinstance(row_id, int) or isinstance(row_id, bool) or row_id <= self.sequence:
                            reason = "invalid_or_nonincreasing_id"
                        elif not isinstance(row.get("event"), dict):
                            reason = "invalid_event_row"
                        else:
                            self._index.append((row_id, offset, len(raw)))
                            self._ids.append(row_id)
                            self.sequence = row_id
                    except (ValueError, UnicodeDecodeError):
                        reason = "invalid_json_line"
                    if reason or not raw.endswith(b"\n"):
                        recovery.append({"line": line_number, "offset": offset, "bytes": len(raw),
                                         "reason": reason or "missing_final_newline", "preserved": True,
                                         "sha256": hashlib.sha256(raw).hexdigest()})
        except FileNotFoundError:
            recovery.append({"reason": "missing_event_file", "preserved": True})
        if recovery:
            self._metadata["recovery"] = recovery

    def _save_metadata(self):
        self._metadata.update(event_count=len(self._index), head=self.sequence)
        _atomic_json(self.directory / "manifest.json", self._cleaner.clean(self._metadata))

    def _prepare_record(self, event, source, recorded_at, elapsed_ms):
        if not isinstance(event, dict):
            raise TypeError("event must be a dictionary")
        if not isinstance(source, str) or not source:
            raise ValueError("source must be a nonempty string")
        observable = dict(event)
        if event.get("type") == "audio":
            payload = event.get("pcm")
            observable.update(type="audio_delivery", original_type="audio", payload_redacted=True)
            if isinstance(payload, (str, bytes, bytearray, memoryview)):
                observable["encoded_bytes" if isinstance(payload, str) else "payload_bytes"] = len(payload)
        return {"at": recorded_at, "elapsed_ms": round(elapsed_ms, 3),
                "source": self._cleaner.text(source), "event": self._cleaner.clean(observable),
                "clock_id": f"trace_server:{self.trace_id}", "timestamp_basis": "server_receipt"}

    def record(self, event: dict, source="server"):
        prepared = self._prepare_record(event, source, time.time(), (time.perf_counter()-self._started)*1000)
        self._append_prepared([prepared])

    def _append_prepared(self, prepared):
        """Persist an ordered sanitized batch using one append and manifest replace."""
        if not prepared:
            return
        with self._guard:
            if self._metadata.get("status") in TERMINAL:
                raise RuntimeError("Cannot record into a finished trace")
            rows = [{"id": self.sequence+index+1, **row} for index, row in enumerate(prepared)]
            lines = [(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8") for row in rows]
            with self._path.open("ab") as handle:
                offset = handle.tell()
                handle.write(b"".join(lines))
                handle.flush()
                os.fsync(handle.fileno())
            for row, raw in zip(rows, lines):
                self._index.append((row["id"], offset, len(raw)))
                self._ids.append(row["id"])
                offset += len(raw)
            self.sequence = rows[-1]["id"]
            self._metadata["updated_at"] = _utc()
            self._save_metadata()

    def since(self, cursor, limit=400) -> dict:
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise ValueError("cursor must be a nonnegative integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        limit = min(limit, MAX_PAGE)
        with self._guard:
            selected = self._index[bisect_right(self._ids, cursor):][:limit]
            rows = self._read_rows(selected)
            return {"events": rows, "cursor": rows[-1]["id"] if rows else self.sequence,
                    "head": self.sequence, "oldest": self._ids[0] if self._ids else 0,
                    "capacity": None, "persistent": True, "page_limit": limit, "trace_id": self.trace_id}

    def _read_rows(self, selected):
        if not selected:
            return []
        with self._path.open("rb") as handle:
            rows = []
            for _, offset, size in selected:
                handle.seek(offset)
                rows.append(self._cleaner.clean(json.loads(handle.read(size))))
            return rows

    def finish(self, status="ended", reason=None):
        if status not in TERMINAL:
            raise ValueError("status must describe a finished session")
        with self._guard:
            if self._metadata.get("status") in TERMINAL:
                return self.metadata
            self._metadata.update(status=status, ended_at=_utc(), updated_at=_utc(),
                                  reason=self._cleaner.clean(reason))
            self._save_metadata()
            return self.metadata

    def export(self) -> dict:
        with self._guard:
            return {"session": self.metadata, "events": self._read_rows(self._index)}

    def emit(self, record):
        try:
            event = json.loads(record.getMessage())
            if isinstance(event, dict):
                self.record(event, "provider")
        except (ValueError, TypeError, RuntimeError):
            pass


class TraceWriteError(OSError):
    """The background writer could not persist the accepted trace events."""


class BufferedJournal(logging.Handler):
    """Keep disk I/O off the voice loop; finish/flush must run off that loop too.

    record() deep-copies through redaction before queuing, preserving receipt time.
    One writer batches at most 50 events or 50 ms. An abrupt process exit can lose
    queued events; finish() drains them before finalizing the durable journal.
    """
    BATCH_EVENTS = 50
    BATCH_SECONDS = .05
    JOIN_SECONDS = 30

    def __init__(self, journal: TrialJournal):
        super().__init__()
        if journal.metadata.get("status") in TERMINAL:
            raise ValueError("BufferedJournal requires an active journal")
        self.journal = journal
        self.trace_id = journal.trace_id
        self._condition = threading.Condition()
        self._finish_lock = threading.Lock()
        self._queue = deque()
        self._accepted = 0
        self._persisted = 0
        self._inflight = 0
        self._flush_target = 0
        self._closing = False
        self._late_records = 0
        self._error = None
        self._writer = threading.Thread(target=self._write_loop, name=f"trace-{self.trace_id[:8]}", daemon=True)
        self._writer.start()

    def _buffer_metadata(self):
        with self._condition:
            return {"accepted_events": self._accepted, "persisted_events": self._persisted,
                    "queued_events": len(self._queue), "inflight_events": self._inflight,
                    "pending_events": self._accepted-self._persisted, "closing": self._closing,
                    "late_records_ignored": self._late_records, "writer_error": self._error,
                    "batch_events": self.BATCH_EVENTS, "batch_wait_ms": self.BATCH_SECONDS*1000}

    @property
    def error(self):
        with self._condition:
            return self._error

    @property
    def metadata(self):
        return {**self.journal.metadata, "buffer": self._buffer_metadata()}

    @property
    def sequence(self):
        return self.journal.sequence

    def _raise_error(self):
        if self._error:
            raise TraceWriteError(self._error)

    def record(self, event, source="server"):
        recorded_at, elapsed_ms = time.time(), (time.perf_counter()-self.journal._started)*1000
        # This lock serializes producer ordering but is never held during disk I/O.
        with self._condition:
            self._raise_error()
            if self._closing:
                self._late_records = min(2**31-1, self._late_records+1)
                return
            prepared = self.journal._prepare_record(event, source, recorded_at, elapsed_ms)
            self._queue.append(prepared)
            self._accepted += 1
            self._condition.notify_all()

    def _fail(self, error):
        with self._condition:
            self._error = self.journal._cleaner.text(f"{type(error).__name__}: {error}")
            self._closing = True
            self._condition.notify_all()

    def _write_loop(self):
        try:
            while True:
                with self._condition:
                    while not self._queue and not self._closing:
                        self._condition.wait()
                    if not self._queue:
                        return
                    deadline = time.monotonic()+self.BATCH_SECONDS
                    while len(self._queue) < self.BATCH_EVENTS and not self._closing and self._flush_target <= self._persisted:
                        remaining = deadline-time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(remaining)
                    batch = [self._queue.popleft() for _ in range(min(self.BATCH_EVENTS, len(self._queue)))]
                    self._inflight = len(batch)
                self.journal._append_prepared(batch)
                with self._condition:
                    self._persisted += len(batch)
                    self._inflight = 0
                    self._condition.notify_all()
        except Exception as error:
            self._fail(error)

    def flush(self, timeout=30):
        """Wait for events accepted before this call; surface disk failures."""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        deadline = time.monotonic()+timeout
        with self._condition:
            self._raise_error()
            target = self._accepted
            self._flush_target = max(self._flush_target, target)
            self._condition.notify_all()
            while self._persisted < target:
                self._raise_error()
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TraceWriteError("Timed out waiting for trace storage")
                self._condition.wait(remaining)
            self._raise_error()

    def since(self, cursor, limit=400):
        result = self.journal.since(cursor, limit)
        result["buffer"] = self._buffer_metadata()
        return result

    def export(self):
        self.flush()
        result = self.journal.export()
        result["session"]["buffer"] = self._buffer_metadata()
        return result

    def finish(self, status="ended", reason=None):
        if status not in TERMINAL:
            raise ValueError("status must describe a finished session")
        with self._finish_lock:
            with self._condition:
                self._closing = True
                self._condition.notify_all()
            self._writer.join(self.JOIN_SECONDS)
            if self._writer.is_alive():
                self._fail(TimeoutError("Trace writer did not finish before the shutdown deadline"))
            with self._condition:
                self._raise_error()
            try:
                self.journal.finish(status, reason)
            except Exception as error:
                self._fail(error)
                self._raise_error()
            return self.metadata

    def emit(self, record):
        try:
            event = json.loads(record.getMessage())
        except (ValueError, TypeError):
            return
        if isinstance(event, dict):
            self.record(event, "provider")


class TraceStore:
    def __init__(self, root: str | Path, *, secrets: Iterable[str] = ()):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._cleaner = _Cleaner(secrets)
        self._guard = threading.RLock()
        self._sessions = {}
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir() or not TRACE_ID.fullmatch(directory.name):
                continue
            journal = self._load(directory.name)
            if journal is not None and journal.metadata.get("status") not in TERMINAL:
                journal.finish("interrupted", "Server restarted before this session finished")

    def _directory(self, trace_id):
        if not isinstance(trace_id, str) or TRACE_ID.fullmatch(trace_id) is None:
            return None
        directory = self.root / trace_id
        if directory.is_symlink() or directory.resolve().parent != self.root:
            return None
        return directory

    def _load(self, trace_id):
        directory = self._directory(trace_id)
        if directory is None or not directory.is_dir():
            return None
        if any((directory / name).is_symlink() for name in ("manifest.json", "events.jsonl")):
            return None
        try:
            metadata = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or metadata.get("trace_id") != trace_id:
                return None
            journal = TrialJournal(directory, metadata, self._cleaner)
        except (OSError, ValueError, KeyError):
            return None
        self._sessions[trace_id] = journal
        return journal

    def start(self, config: dict):
        if not isinstance(config, dict):
            raise TypeError("config must be a dictionary")
        with self._guard:
            trace_id = uuid.uuid4().hex
            directory = self.root / trace_id
            directory.mkdir()
            metadata = {"schema_version": 1, "trace_id": trace_id, "status": "active",
                        "started_at": _utc(), "updated_at": _utc(), "ended_at": None,
                        "event_count": 0, "head": 0, "config": self._cleaner.clean(config),
                        "local_only": True, "clock_basis": "server receipt; embedded event clocks remain distinct"}
            journal = TrialJournal(directory, metadata, self._cleaner, new=True)
            journal._save_metadata()
            self._sessions[trace_id] = journal
            return journal

    def get(self, trace_id):
        with self._guard:
            if self._directory(trace_id) is None:
                return None
            return self._sessions.get(trace_id) or self._load(trace_id)

    def list_sessions(self):
        with self._guard:
            return sorted((journal.metadata for journal in self._sessions.values()),
                          key=lambda metadata: (metadata.get("started_at", ""), metadata["trace_id"]), reverse=True)
