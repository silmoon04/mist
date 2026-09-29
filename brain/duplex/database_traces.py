"""SQLite-backed trial journals with read access to older JSONL traces."""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
import threading
import time
from typing import Iterable
import uuid

from .session_store import SessionStore
from .trial_traces import MAX_PAGE, TERMINAL, TRACE_ID, TraceStore, TrialJournal, _Cleaner, _utc


class DatabaseJournal(logging.Handler):
    """The small journal interface used by live_studio and BufferedJournal."""

    def __init__(self, database: SessionStore, metadata: dict, cleaner: _Cleaner):
        super().__init__()
        self.database = database
        self.trace_id = metadata["trace_id"]
        self._metadata = copy.deepcopy(metadata)
        self._cleaner = cleaner
        self._guard = threading.RLock()
        self._started = time.perf_counter()
        self.sequence = int(metadata.get("head", 0))

    @property
    def metadata(self) -> dict:
        with self._guard:
            return copy.deepcopy(self._cleaner.clean({**self._metadata, "event_count": self.sequence,
                                                       "head": self.sequence}))

    # BufferedJournal uses these two hooks to sanitize before enqueueing and to
    # commit a whole batch on its writer thread. Keep the same receipt envelope.
    _prepare_record = TrialJournal._prepare_record

    def _append_prepared(self, prepared: list[dict]):
        if not prepared:
            return
        with self._guard:
            if self._metadata.get("status") in TERMINAL:
                raise RuntimeError("Cannot record into a finished trace")
            rows = [{"id": self.sequence + index + 1, **row}
                    for index, row in enumerate(prepared)]
            self.database.append_events(self.trace_id, rows)
            self.sequence = rows[-1]["id"]
            self._metadata["updated_at"] = _utc()

    def record(self, event: dict, source="server"):
        prepared = self._prepare_record(event, source, time.time(),
                                        (time.perf_counter() - self._started) * 1000)
        self._append_prepared([prepared])

    def since(self, cursor, limit=400) -> dict:
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise ValueError("cursor must be a nonnegative integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        limit = min(limit, MAX_PAGE)
        with self._guard:
            stored = self.database.list_events(self.trace_id, after_source_event_id=cursor,
                                               limit=limit)
            rows = [self._cleaner.clean(item["payload"]) for item in stored]
            first = self.database.list_events(self.trace_id, limit=1)
            oldest = first[0]["payload"]["id"] if first else 0
            return {"events": rows, "cursor": rows[-1]["id"] if rows else self.sequence,
                    "head": self.sequence, "oldest": oldest, "capacity": None,
                    "persistent": True, "page_limit": limit, "trace_id": self.trace_id}

    def export(self) -> dict:
        with self._guard:
            return {"session": self.metadata,
                    "events": [self._cleaner.clean(row["payload"])
                               for row in self.database.list_events(self.trace_id)]}

    def finish(self, status="ended", reason=None):
        if status not in TERMINAL:
            raise ValueError("status must describe a finished session")
        with self._guard:
            if self._metadata.get("status") in TERMINAL:
                return self.metadata
            now = _utc()
            final_metadata = {**self._metadata, "status": status, "ended_at": now,
                              "updated_at": now, "reason": self._cleaner.clean(reason),
                              "event_count": self.sequence, "head": self.sequence}
            self.database.finish_session(self.trace_id, status, final_metadata)
            self._metadata = final_metadata
            return self.metadata

    def emit(self, record):
        try:
            event = json.loads(record.getMessage())
            if isinstance(event, dict):
                self.record(event, "provider")
        except (ValueError, TypeError, RuntimeError):
            pass


class DatabaseTraceStore:
    """New traces go to sessions.sqlite3; preexisting JSONL traces stay readable."""

    def __init__(self, root: str | Path, *, secrets: Iterable[str] = ()):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._cleaner = _Cleaner(secrets)
        self._guard = threading.RLock()
        self.database = SessionStore(self.root.parent / "sessions.sqlite3")
        self._legacy = TraceStore(self.root, secrets=secrets)
        self._sessions: dict[str, DatabaseJournal] = {}
        for saved in self.database.recover_active():
            metadata = self._metadata_from_session(saved)
            metadata.update(status="interrupted", ended_at=_utc(), updated_at=_utc(),
                            reason="Server restarted before this session finished")
            self.database.finish_session(metadata["trace_id"], "interrupted", metadata)

    def start(self, config: dict) -> DatabaseJournal:
        if not isinstance(config, dict):
            raise TypeError("config must be a dictionary")
        with self._guard:
            trace_id = uuid.uuid4().hex
            now = _utc()
            metadata = {"schema_version": 1, "trace_id": trace_id, "status": "active",
                        "started_at": now, "updated_at": now, "ended_at": None,
                        "event_count": 0, "head": 0, "config": self._cleaner.clean(config),
                        "local_only": True,
                        "clock_basis": "server receipt; embedded event clocks remain distinct"}
            self.database.start_session(trace_id, metadata)
            journal = DatabaseJournal(self.database, metadata, self._cleaner)
            self._sessions[trace_id] = journal
            return journal

    def get(self, trace_id):
        if not isinstance(trace_id, str) or TRACE_ID.fullmatch(trace_id) is None:
            return None
        with self._guard:
            if trace_id in self._sessions:
                return self._sessions[trace_id]
            saved = self.database.get_session(trace_id)
            if saved is not None:
                metadata = self._metadata_from_session(saved)
                journal = DatabaseJournal(self.database, metadata, self._cleaner)
                self._sessions[trace_id] = journal
                return journal
            return self._legacy.get(trace_id)

    def _metadata_from_session(self, saved):
        metadata = copy.deepcopy(saved.get("metadata", saved))
        metadata["trace_id"] = saved.get("id", metadata.get("trace_id"))
        metadata["status"] = saved.get("status") or metadata.get("status", "active")
        metadata["head"] = max((row["payload"]["id"]
                                for row in self.database.list_events(metadata["trace_id"])), default=0)
        metadata["event_count"] = metadata["head"]
        return metadata

    def list_sessions(self):
        with self._guard:
            db_items = [self.get(item["id"]).metadata
                        for item in self.database.list_sessions()]
            legacy_items = self._legacy.list_sessions()
            return sorted(db_items + legacy_items,
                          key=lambda item: (item.get("started_at", ""), item["trace_id"]),
                          reverse=True)

    def close(self):
        self.database.close()
