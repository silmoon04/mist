"""Private, durable local conversation ledger and nonblocking PCM recorder.

Original events and turns are append-only. Summaries and notes are derived records;
callers must retain source event IDs so they can be checked against originals.
Audio uses signed 16-bit little-endian PCM. The recorder stores received bytes,
never decodes compressed audio or guesses a format from arbitrary bytes.
"""

from __future__ import annotations

import json
import os
import queue
import re
import sqlite3
import struct
import threading
import time
from pathlib import Path
from typing import Any, Mapping


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SECRET_KEY = re.compile(r"(^|[_-])(auth|authorization|api[_-]?key|secret|password|cookie|token)([_-]|$)", re.I)
_STREAMS = {"mic", "assistant_generated", "assistant_played"}
_ENCODED_AUDIO_HEADERS = (b"RIFF", b"OggS", b"ID3", b"fLaC", b"FORM", b"\xff\xfb", b"\xff\xf3")


def _id(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Invalid local session ID")
    return value


def _clean(value: Any) -> Any:
    """Strip credential-shaped fields and reject binary payloads before SQLite."""
    if isinstance(value, Mapping):
        return {str(k): ("[redacted]" if _SECRET_KEY.search(str(k)) else _clean(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("Binary data belongs in an audio stream, not event metadata")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported event metadata type: {type(value).__name__}")


def _json(value: Any) -> str:
    return json.dumps(_clean(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _wav_header(byte_count: int, sample_rate: int, channels: int) -> bytes:
    if byte_count > 0xFFFFFFFF - 36:
        raise ValueError("WAV stream exceeds RIFF size limit")
    rate = sample_rate * channels * 2
    return (b"RIFF" + struct.pack("<I", 36 + byte_count) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, rate, channels * 2, 16)
            + b"data" + struct.pack("<I", byte_count))


class SessionStore:
    """SQLite ledger with a bounded background audio writer.

    ``enqueue_audio`` returns immediately. False means the bounded queue was full;
    inspect ``audio_status`` for dropped chunks and writer errors. Call ``close`` at
    shutdown to drain the queue and complete WAV headers. On restart, audio files
    are repaired to the last indexed chunk, discarding any crash orphan tail.
    """

    def __init__(self, db_path: str | Path, *, audio_queue_size: int = 512):
        if audio_queue_size < 1:
            raise ValueError("audio_queue_size must be positive")
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.audio_root = self.db_path.parent / "sessions"
        self.audio_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._audio_errors: list[str] = []
        self._init_schema()
        self._repair_audio()
        self._audio_queue: queue.Queue[Any] = queue.Queue(maxsize=audio_queue_size)
        self._audio_admission = threading.Lock()
        self._audio_stop = object()
        self._audio_dropped = 0
        self._closed = False
        self._audio_thread = threading.Thread(target=self._audio_loop, name="mist-audio-writer", daemon=True)
        self._audio_thread.start()

    def _init_schema(self) -> None:
        with self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, created_ms INTEGER NOT NULL, metadata_json TEXT NOT NULL,
                    ended_ms INTEGER, status TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    timestamp_ms INTEGER NOT NULL, event_type TEXT NOT NULL,
                    source_event_id TEXT, payload_json TEXT NOT NULL,
                    UNIQUE(session_id, source_event_id)
                );
                CREATE INDEX IF NOT EXISTS events_order ON events(session_id, id);
                CREATE INDEX IF NOT EXISTS events_source_number ON events(session_id, CAST(source_event_id AS INTEGER));
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    timestamp_ms INTEGER NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
                    source_event_id TEXT, playback_verified INTEGER NOT NULL DEFAULT 0,
                    played_text TEXT, correction_of INTEGER REFERENCES turns(id), audio_ref TEXT,
                    UNIQUE(session_id, source_event_id)
                );
                CREATE INDEX IF NOT EXISTS turns_order ON turns(session_id, id);
                CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
                    text, content='turns', content_rowid='id', tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS turns_fts_insert AFTER INSERT ON turns BEGIN
                    INSERT INTO turns_fts(rowid,text) VALUES(new.id,new.text);
                END;
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    version INTEGER NOT NULL, created_ms INTEGER NOT NULL, text TEXT NOT NULL,
                    source_event_ids_json TEXT NOT NULL, UNIQUE(session_id,version)
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    created_ms INTEGER NOT NULL, text TEXT NOT NULL,
                    source_event_ids_json TEXT NOT NULL, correction_of INTEGER REFERENCES notes(id)
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY, created_ms INTEGER NOT NULL, text TEXT NOT NULL,
                    session_id TEXT REFERENCES sessions(id), source_event_id TEXT
                );
                CREATE TABLE IF NOT EXISTS audio_chunks (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    stream TEXT NOT NULL, timestamp_ms INTEGER NOT NULL,
                    source_event_id TEXT, offset_bytes INTEGER NOT NULL, byte_count INTEGER NOT NULL,
                    sample_rate INTEGER NOT NULL, channels INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(session_id,stream,offset_bytes)
                );
                CREATE INDEX IF NOT EXISTS audio_order ON audio_chunks(session_id,stream,id);
            """)

    def _execute(self, sql: str, values: tuple = ()) -> int:
        with self._lock, self._connection:
            return self._connection.execute(sql, values).lastrowid

    def start_session(self, session_id: str, metadata: Mapping[str, Any] | None = None) -> None:
        session_id = _id(session_id)
        self._execute("INSERT OR IGNORE INTO sessions(id,created_ms,metadata_json) VALUES(?,?,?)",
                      (session_id, _now_ms(), _json(metadata or {})))

    def get_session(self, session_id: str) -> dict | None:
        _id(session_id)
        with self._lock:
            row = self._connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None: return None
        result = dict(row)
        result["metadata"] = json.loads(result["metadata_json"])
        return result

    def list_sessions(self, *, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM sessions ORDER BY created_ms DESC,id DESC"
        if limit is not None:
            if limit < 0: raise ValueError("limit must be nonnegative")
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._connection.execute(sql).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata_json"])} for row in rows]

    def finish_session(self, session_id: str, status: str = "finished",
                       metadata: Mapping[str, Any] | None = None) -> None:
        _id(session_id)
        with self._lock, self._connection:
            row = self._connection.execute("SELECT metadata_json FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None: raise KeyError(session_id)
            merged = {**json.loads(row[0]), **_clean(metadata or {})}
            self._connection.execute("""UPDATE sessions SET ended_ms=COALESCE(ended_ms,?),
                status=?,metadata_json=? WHERE id=?""", (_now_ms(), status, _json(merged), session_id))

    def recover_active(self) -> list[dict]:
        """Return sessions still active after a previous process stopped."""
        return [row for row in self.list_sessions() if row["ended_ms"] is None]

    def append_event(self, session_id: str, event_type: str, payload: Any = None,
                     *, source_event_id: str | None = None,
                     timestamp_ms: int | None = None) -> int:
        _id(session_id)
        if not event_type:
            raise ValueError("event_type is required")
        self.start_session(session_id)
        return self._execute("""INSERT INTO events(session_id,timestamp_ms,event_type,source_event_id,payload_json)
                              VALUES(?,?,?,?,?)""",
                             (session_id, _now_ms() if timestamp_ms is None else timestamp_ms,
                              event_type, source_event_id, _json(payload or {})))

    def append_events(self, session_id: str, rows: list[Mapping[str, Any]]) -> list[int]:
        """Atomically append complete trace envelopes in their supplied order."""
        _id(session_id)
        self.start_session(session_id)
        prepared = []
        for row in rows:
            event = row.get("event", {})
            event_type = event.get("type", "unknown") if isinstance(event, Mapping) else "unknown"
            source_id = str(row["id"]) if row.get("id") is not None else None
            prepared.append((session_id, _now_ms(), str(event_type), source_id, _json(row)))
        with self._lock, self._connection:
            return [self._connection.execute("""INSERT INTO events(session_id,timestamp_ms,event_type,
                       source_event_id,payload_json) VALUES(?,?,?,?,?)""", values).lastrowid
                    for values in prepared]

    def append_turn(self, session_id: str, role: str, text: str,
                    *, source_event_id: str | None = None,
                    timestamp_ms: int | None = None, playback_verified: bool = False,
                    correction_of: int | None = None, audio_ref: str | None = None) -> dict:
        _id(session_id)
        if role not in {"user", "assistant", "system", "tool"} or not isinstance(text, str) or not text:
            raise ValueError("A turn requires a known role and nonempty text")
        if audio_ref is not None and (not isinstance(audio_ref, str) or ".." in Path(audio_ref).parts):
            raise ValueError("Unsafe audio reference")
        self.start_session(session_id)
        turn_id = self._execute("""INSERT INTO turns(session_id,timestamp_ms,role,text,source_event_id,
                                 playback_verified,correction_of,audio_ref) VALUES(?,?,?,?,?,?,?,?)""",
                                (session_id, _now_ms() if timestamp_ms is None else timestamp_ms,
                                 role, text, source_event_id, int(playback_verified), correction_of, audio_ref))
        return self.get_turn(turn_id)

    def get_turn(self, turn_id: int) -> dict:
        with self._lock:
            row = self._connection.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        if row is None:
            raise KeyError(turn_id)
        result = dict(row)
        result["playback_verified"] = bool(result["playback_verified"])
        return result

    def mark_turn_played(self, turn_id: int, played_text: str | None = None) -> dict:
        """Record a playback receipt; a prefix does not verify the whole turn."""
        turn = self.get_turn(turn_id)
        if turn["role"] != "assistant":
            raise ValueError("Only assistant turns have playback receipts")
        if played_text is None:
            played_text = turn["text"]
        if not turn["text"].startswith(played_text):
            raise ValueError("Played text must be a prefix of generated text")
        self._execute("UPDATE turns SET played_text=?, playback_verified=? WHERE id=?",
                      (played_text, int(played_text == turn["text"]), turn_id))
        return self.get_turn(turn_id)

    def list_events(self, session_id: str, *, after_id: int | None = None,
                    after_source_event_id: int | None = None,
                    limit: int | None = None) -> list[dict]:
        _id(session_id)
        sql = "SELECT * FROM events WHERE session_id=?"
        args: tuple = (session_id,)
        if after_id is not None:
            sql += " AND id>?"
            args += (after_id,)
        if after_source_event_id is not None:
            sql += " AND source_event_id GLOB '[0-9]*' AND CAST(source_event_id AS INTEGER)>?"
            args += (after_source_event_id,)
        sql += " ORDER BY id"
        if limit is not None:
            if limit < 0: raise ValueError("limit must be nonnegative")
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._connection.execute(sql, args).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def list_turns(self, session_id: str, *, limit: int | None = None,
                   newest_first: bool = False) -> list[dict]:
        _id(session_id)
        sql = "SELECT id FROM turns WHERE session_id=? ORDER BY id " + ("DESC" if newest_first else "ASC")
        if limit is not None:
            if limit < 0: raise ValueError("limit must be nonnegative")
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            ids = [row[0] for row in self._connection.execute(sql, (session_id,)).fetchall()]
        return [self.get_turn(turn_id) for turn_id in ids]

    def search_turns(self, query: str, session_id: str | None = None, *, limit: int = 10) -> list[dict]:
        """Search all original turns. FTS syntax errors are treated as empty results."""
        if session_id is not None: _id(session_id)
        if not query.strip() or limit < 1: return []
        sql = ("SELECT t.id FROM turns_fts JOIN turns t ON t.id=turns_fts.rowid "
               "WHERE turns_fts MATCH ?")
        args: list[Any] = [query]
        if session_id is not None:
            sql += " AND t.session_id=?"
            args.append(session_id)
        sql += " ORDER BY bm25(turns_fts),t.id LIMIT ?"
        args.append(limit)
        with self._lock:
            try:
                ids = [row[0] for row in self._connection.execute(sql, args).fetchall()]
            except sqlite3.OperationalError:
                return []
        return [self.get_turn(turn_id) for turn_id in ids]

    def save_summary(self, session_id: str, text: str, source_event_ids: list[str] | tuple[str, ...],
                     *, version: int | None = None) -> int:
        _id(session_id)
        if not text or not source_event_ids: raise ValueError("Summary needs text and source event IDs")
        self.start_session(session_id)
        with self._lock, self._connection:
            if version is None:
                version = self._connection.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM summaries WHERE session_id=?", (session_id,)).fetchone()[0]
            self._connection.execute("INSERT INTO summaries(session_id,version,created_ms,text,source_event_ids_json) VALUES(?,?,?,?,?)",
                                     (session_id, version, _now_ms(), text, _json(source_event_ids)))
        return version

    def list_summaries(self, session_id: str) -> list[dict]:
        _id(session_id)
        with self._lock:
            rows = self._connection.execute("SELECT * FROM summaries WHERE session_id=? ORDER BY version", (session_id,)).fetchall()
        return [{**dict(row), "source_event_ids": json.loads(row["source_event_ids_json"])} for row in rows]

    def save_note(self, session_id: str, text: str, source_event_ids: list[str] | tuple[str, ...],
                  *, correction_of: int | None = None) -> int:
        _id(session_id)
        if not text or not source_event_ids: raise ValueError("Note needs text and source event IDs")
        self.start_session(session_id)
        return self._execute("INSERT INTO notes(session_id,created_ms,text,source_event_ids_json,correction_of) VALUES(?,?,?,?,?)",
                             (session_id, _now_ms(), text, _json(source_event_ids), correction_of))

    def list_notes(self, session_id: str) -> list[dict]:
        _id(session_id)
        with self._lock:
            rows = self._connection.execute("SELECT * FROM notes WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        return [{**dict(row), "source_event_ids": json.loads(row["source_event_ids_json"])} for row in rows]

    def save_preference(self, text: str, session_id: str | None = None,
                        source_event_id: str | None = None) -> int:
        """Save an exact, explicitly requested preference for future sessions."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Preference text is required")
        if session_id is not None:
            self.start_session(_id(session_id))
        return self._execute("""INSERT INTO preferences(created_ms,text,session_id,source_event_id)
                              VALUES(?,?,?,?)""", (_now_ms(), text, session_id, source_event_id))

    def list_preferences(self, limit: int = 60) -> list[dict]:
        if not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a nonnegative integer")
        with self._lock:
            return [dict(row) for row in self._connection.execute(
                "SELECT * FROM preferences ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def audio_path(self, session_id: str, stream: str) -> Path:
        _id(session_id)
        if stream not in _STREAMS: raise ValueError("Unknown audio stream")
        parent = self.audio_root / session_id
        path = parent / f"{stream}.wav"
        if self.audio_root.is_symlink() or parent.is_symlink() or path.is_symlink():
            raise ValueError("Audio paths must remain within the local session directory")
        if not path.resolve().is_relative_to(self.audio_root.resolve()):
            raise ValueError("Audio path escapes the local session directory")
        return path

    def enqueue_audio(self, session_id: str, stream: str, pcm_bytes: bytes,
                      *, sample_rate: int, channels: int = 1,
                      timestamp_ms: int | None = None,
                      source_event_id: str | None = None,
                      metadata: Mapping[str, Any] | None = None) -> bool:
        _id(session_id)
        if stream not in _STREAMS: raise ValueError("Unknown audio stream")
        if not isinstance(pcm_bytes, bytes) or not pcm_bytes or sample_rate < 8000 or sample_rate > 192000 or channels not in (1, 2):
            raise ValueError("Expected nonempty signed 16-bit PCM bytes and valid format")
        if len(pcm_bytes) % (2 * channels): raise ValueError("Incomplete PCM frame")
        if pcm_bytes.startswith(_ENCODED_AUDIO_HEADERS):
            raise ValueError("Encoded or container audio must be decoded to signed 16-bit PCM first")
        metadata_json = _json(metadata or {})
        item = (session_id, stream, pcm_bytes, sample_rate, channels,
                _now_ms() if timestamp_ms is None else timestamp_ms, source_event_id, metadata_json)
        with self._audio_admission:
            if self._closed: raise RuntimeError("SessionStore is closed")
            try:
                self._audio_queue.put_nowait(item)
                return True
            except queue.Full:
                self._audio_dropped += 1
                return False

    def _repair_audio(self) -> None:
        with self._lock:
            rows = self._connection.execute("""SELECT session_id,stream,MAX(offset_bytes+byte_count) AS size,
                            MIN(sample_rate) AS rate,MIN(channels) AS channels
                            FROM audio_chunks GROUP BY session_id,stream""").fetchall()
        for row in rows:
            path = self.audio_path(row["session_id"], row["stream"])
            if not path.exists():
                continue
            size = row["size"]
            with path.open("r+b") as file:
                if file.seek(0, os.SEEK_END) < size + 44:
                    self._audio_errors.append(f"Truncated audio: {row['session_id']}/{row['stream']}")
                    continue
                file.truncate(size + 44)
                file.seek(0)
                file.write(_wav_header(size, row["rate"], row["channels"]))

    def _write_audio_batch(self, items: list[tuple]) -> None:
        """One file sync and one SQLite commit per stream batch; file I/O holds no DB lock."""
        groups: dict[tuple[str, str], list[tuple]] = {}
        for item in items:
            groups.setdefault((item[0], item[1]), []).append(item)
        for (session_id, stream), chunks in groups.items():
            try:
                self.start_session(session_id)
                path = self.audio_path(session_id, stream)
                path.parent.mkdir(parents=True, exist_ok=True)
                with self._lock:
                    row = self._connection.execute("""SELECT offset_bytes+byte_count,sample_rate,channels
                           FROM audio_chunks WHERE session_id=? AND stream=? ORDER BY id DESC LIMIT 1""",
                           (session_id, stream)).fetchone()
                offset = row[0] if row else 0
                rate, channels = (row[1], row[2]) if row else chunks[0][3:5]
                valid = []
                for item in chunks:
                    if item[3:5] != (rate, channels):
                        self._audio_errors.append("ValueError: Audio format changed within a stream")
                    else:
                        valid.append(item)
                if not valid: continue
                if row and not path.exists():
                    raise FileNotFoundError(f"Indexed audio stream is missing: {session_id}/{stream}")
                if not path.exists():
                    with path.open("wb") as file:
                        file.write(_wav_header(0, rate, channels))
                inserts = []
                with path.open("r+b") as file:
                    file.seek(44 + offset)
                    for item in valid:
                        _, _, data, _, _, timestamp_ms, source_event_id, metadata_json = item
                        file.write(data)
                        inserts.append((session_id, stream, timestamp_ms, source_event_id,
                                        offset, len(data), rate, channels, metadata_json))
                        offset += len(data)
                    file.seek(0)
                    file.write(_wav_header(offset, rate, channels))
                    file.flush()
                    os.fsync(file.fileno())
                with self._lock, self._connection:
                    self._connection.executemany("""INSERT INTO audio_chunks(session_id,stream,timestamp_ms,
                        source_event_id,offset_bytes,byte_count,sample_rate,channels,metadata_json)
                        VALUES(?,?,?,?,?,?,?,?,?)""", inserts)
            except Exception as exc:
                self._audio_errors.append(f"{type(exc).__name__}: {exc}")

    def _audio_loop(self) -> None:
        while True:
            first = self._audio_queue.get()
            if first is self._audio_stop:
                self._audio_queue.task_done()
                return
            batch = [first]
            stopping = False
            deadline = time.monotonic() + 0.05
            while len(batch) < 128:
                remaining = deadline - time.monotonic()
                if remaining <= 0: break
                try:
                    item = self._audio_queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is self._audio_stop:
                    stopping = True
                    self._audio_queue.task_done()
                    break
                batch.append(item)
            try:
                self._write_audio_batch(batch)
            finally:
                for _ in batch:
                    self._audio_queue.task_done()
            if stopping: return

    def flush_audio(self) -> None:
        self._audio_queue.join()

    def audio_status(self) -> dict:
        return {"queued": self._audio_queue.qsize(), "dropped": self._audio_dropped,
                "errors": list(self._audio_errors)}

    def list_audio_chunks(self, session_id: str, stream: str | None = None) -> list[dict]:
        _id(session_id)
        if stream is not None and stream not in _STREAMS: raise ValueError("Unknown audio stream")
        sql = "SELECT * FROM audio_chunks WHERE session_id=?"
        args: tuple = (session_id,)
        if stream is not None:
            sql += " AND stream=?"
            args += (stream,)
        sql += " ORDER BY id"
        with self._lock:
            return [{**dict(row), "metadata": json.loads(row["metadata_json"])}
                    for row in self._connection.execute(sql, args).fetchall()]

    def audio_summary(self, session_id: str) -> dict:
        """Small per-stream aggregate for UI/status; does not materialize chunk rows."""
        _id(session_id)
        with self._lock:
            rows = self._connection.execute("""SELECT stream, COUNT(*) AS chunk_count,
                SUM(byte_count) AS byte_count, MIN(timestamp_ms) AS first_timestamp_ms,
                MAX(timestamp_ms) AS last_timestamp_ms, MIN(sample_rate) AS min_sample_rate,
                MAX(sample_rate) AS max_sample_rate, MIN(channels) AS min_channels,
                MAX(channels) AS max_channels
                FROM audio_chunks WHERE session_id=? GROUP BY stream ORDER BY stream""",
                (session_id,)).fetchall()
        streams = []
        for row in rows:
            item = dict(row)
            stream = item["stream"]
            path = self.audio_path(session_id, stream)
            item["sample_rate"] = item["min_sample_rate"] if item["min_sample_rate"] == item["max_sample_rate"] else None
            item["channels"] = item["min_channels"] if item["min_channels"] == item["max_channels"] else None
            item["duration_ms"] = (round(1000 * item["byte_count"] /
                                         (2 * item["channels"] * item["sample_rate"]))
                                   if item["sample_rate"] and item["channels"] else None)
            item["path"] = str(path)
            item["exists"] = path.is_file()
            for internal in ("min_sample_rate", "max_sample_rate", "min_channels", "max_channels"):
                del item[internal]
            streams.append(item)
        return {"session_id": session_id, "streams": streams, "audio_status": self.audio_status()}

    def close(self) -> None:
        with self._audio_admission:
            if self._closed: return
            self._closed = True
            self._audio_queue.put(self._audio_stop)
        self._audio_thread.join()
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SessionStore": return self

    def __exit__(self, *_: Any) -> None: self.close()
