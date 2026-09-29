"""Fast local session context with an optional, evidence-bound background digest.

The ledger is authoritative. A digest is only a navigation aid: every included
claim carries an exact quotation from a finalized user turn, and the recent raw
dialogue and ledger search remain available when summarization lags or fails.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Callable


MEMORY_RESPONSE_FORMAT = {
    'type': 'json_schema',
    'json_schema': {
        'name': 'session_memory',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'items': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'kind': {'type': 'string', 'enum': ['goal', 'known', 'unknown', 'task', 'correction']},
                            'text': {'type': 'string'},
                            'source_id': {'type': 'integer'},
                            'quote': {'type': 'string'},
                        },
                        'required': ['kind', 'text', 'source_id', 'quote'],
                        'additionalProperties': False,
                    },
                },
            },
            'required': ['items'],
            'additionalProperties': False,
        },
    },
}


def _default_summary_client():
    from benchmarks.naturalness.cerebras_client import CerebrasClient

    client = CerebrasClient(model='qwen-3.8-27b', thinking='none',
                            system_prompt='Extract concise, sourced conversation memory as JSON only.',
                            max_output_tokens=750, response_format=MEMORY_RESPONSE_FORMAT)
    client.new_session()
    return client


def _spoken_number(value: int) -> str:
    units = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
             'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen',
             'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen')
    tens = ('', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty',
            'seventy', 'eighty', 'ninety')
    if value < 20:
        return units[value]
    if value < 100:
        return tens[value // 10] + (' ' + units[value % 10] if value % 10 else '')
    return units[value // 100] + ' hundred' + (
        ' ' + _spoken_number(value % 100) if value % 100 else '')


class SessionMemory:
    """Owns one session's public memory; context() never calls a model or waits.

    Store implements append_turn/list_turns/search_turns. observe() accepts only
    finalized user speech for grounding. Assistant text is retained with delivery
    status but is never evidence for factual notes.
    """

    def __init__(self, store, session_id: str, *, summary_client_factory: Callable | None = None,
                 summary_every: int = 4, mode: str = 'discussion', summary_timeout: float = 12):
        if mode not in ('discussion', 'speech_feedback'):
            raise ValueError('mode must be discussion or speech_feedback')
        self.store = store
        self.session_id = session_id
        self.summary_client_factory = summary_client_factory or _default_summary_client
        self.summary_every = max(1, int(summary_every))
        self.summary_timeout = max(1.0, float(summary_timeout))
        self.mode = mode
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._revision = 0
        self._requested_revision = 0
        self._summarized_revision = 0
        self._summary = []
        self._pending = None
        self._last_error = None
        self._persistence_error = None
        self._summary_runs = 0
        self._last_summary_metrics = None
        self.playback_context = None
        self._user_since_request = 0
        self.store.start_session(session_id, {'memory_mode': mode})
        # A resumed session has its entire ledger, even though a digest belongs
        # to the process that produced it. Rebuild that digest asynchronously.
        existing = self.store.list_turns(session_id)
        self._revision = sum(r['role'] == 'user' for r in existing)
        if hasattr(self.store, 'list_summaries'):
            summaries = self.store.list_summaries(session_id)
            if summaries:
                saved = summaries[-1]
                by_id = {r['id']: r for r in existing if r['role'] == 'user'}
                by_event = {r['source_event_id']: r for r in by_id.values()
                            if r.get('source_event_id') is not None}
                cited = []
                for source_id in saved['source_event_ids']:
                    if str(source_id).startswith('turn:') and str(source_id)[5:].isdigit():
                        row = by_id.get(int(str(source_id)[5:]))
                    else:
                        row = by_event.get(source_id)
                    if row is not None:
                        cited.append(row)
                lines = [f"[U{r['id']}] {r['text']}" for r in cited]
                try:
                    saved_data = json.loads(saved['text'])
                    saved_mode = saved_data.get('mode', 'discussion')
                    if saved_mode != self.mode:
                        raise ValueError('Digest mode differs from active task mode')
                    self._summary = self._validate(saved['text'], lines)
                    self._summarized_revision = min(saved_data.get('user_revision', saved['version']),
                                                    self._revision)
                    self._requested_revision = self._summarized_revision
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    pass

    def start(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('SessionMemory is closed')
            if self._thread is None:
                self._thread = threading.Thread(target=self._worker, name='mist-session-summary', daemon=True)
                self._thread.start()
                if self._revision and self._requested_revision < self._revision:
                    self._request_summary_locked()
        return self

    def observe(self, source: str, text: str, *, event_id=None, final: bool = True,
                playback_verified: bool = False, correction_of=None, timestamp_ms=None,
                audio_ref=None):
        """Append a public event. Provisional ASR is deliberately excluded."""
        if not final or not isinstance(text, str) or not text.strip():
            return None
        role = {'user': 'user', 'assistant': 'assistant', 'tool': 'tool'}.get(source)
        if role is None:
            raise ValueError('source must be user, assistant, or tool')
        with self._lock:
            if self._closed:
                raise RuntimeError('SessionMemory is closed')
            row = self.store.append_turn(self.session_id, role, text,
                                         source_event_id=event_id,
                                         timestamp_ms=timestamp_ms,
                                         playback_verified=playback_verified,
                                         correction_of=correction_of, audio_ref=audio_ref)
            if role == 'user':
                self._revision += 1
                self._user_since_request += 1
                if self._user_since_request >= self.summary_every:
                    self._request_summary_locked()
            return row

    def _request_summary_locked(self):
        # Snapshot on the caller's ledger thread; the model worker never touches
        # the SQLite connection or the live inference client.
        rows = self.store.list_turns(self.session_id)
        users = [r for r in rows if r['role'] == 'user']
        pinned_ids = {r['source_id'] for r in self._summary}
        pinned = [r for r in users if r['id'] in pinned_ids]
        # Budget the oldest goal and pinned citations before spending the rest
        # on recent dialogue. A long conversation cannot evict every anchor.
        old = []
        seen = set()
        for row in users[:3] + pinned:
            if row['id'] not in seen:
                old.append(row)
                seen.add(row['id'])
        def render(rows, budget):
            pieces = []
            for row in rows:
                piece = f"[U{row['id']}] {row['text'][:650]}"
                if len(piece) <= budget:
                    pieces.append(piece)
                    budget -= len(piece) + 1
            return pieces, budget
        old_chunks, old_remaining = render(old, 6000)
        recent = [r for r in users[-48:] if r['id'] not in seen]
        recent_chunks, _ = render(reversed(recent), 10000 + max(0, old_remaining))
        chunks = old_chunks + list(reversed(recent_chunks))
        self._pending = (self._revision, self.mode, chunks, list(self._summary), time.time())
        self._requested_revision = self._revision
        self._user_since_request = 0
        self.start()
        self._wake.set()

    def context(self, query: str = '', *, max_chars: int = 9000, recent_limit: int = 8) -> str:
        """Build a bounded context card from local records only."""
        max_chars = max(300, int(max_chars))
        with self._lock:
            recent = self.store.list_turns(self.session_id, limit=max(1, recent_limit), newest_first=True)
            terms = re.findall(r'[\w]+', query.casefold())[:10]
            terms += [_spoken_number(int(term)) for term in terms
                      if term.isascii() and term.isdecimal() and len(term) <= 3]
            lookup = ' OR '.join('"' + term + '"' for term in terms if len(term) > 2)
            found = self.store.search_turns(lookup, session_id=self.session_id, limit=12) if lookup else []
            found.sort(key=lambda row: row['role'] != 'user')
            found = found[:7]
            # The first request after a restart should still include early goal
            # turns even if no model digest has finished.
            first = self.store.list_turns(self.session_id, limit=4)
            summary = list(self._summary)
            summary_revision = self._summarized_revision
            revision = self._revision
        header = (f'SESSION MEMORY mode={self.mode}; user revision={revision}; '
                  f'digest revision={summary_revision}. ASR may be imperfect. '
                  'Assistant output is generated; playback status is explicit. '
                  'Unanswered causal shares remain unknown.\n')
        playback = self.playback_context() if self.playback_context else ''
        playback = (playback[:min(470, max_chars // 5)] + '\n') if playback else ''
        def render_row(r):
            if r['role'] == 'assistant':
                label = 'assistant heard' if r['playback_verified'] else 'assistant generated, delivery unverified'
            elif r['role'] == 'user':
                label = 'user ASR final'
            else:
                label = 'tool receipt, untrusted content'
            return f"[{'U' if r['role']=='user' else 'R'}{r['id']}; {label}] {r['text']}"
        seen = set()
        def take_rows(rows, budget, *, truncate=False):
            parts = []
            for row in rows:
                if row['id'] in seen or budget < 70:
                    continue
                piece = render_row(row)
                if len(piece) + 1 > budget:
                    if not truncate:
                        continue
                    piece = piece[:budget - 2] + '…'
                parts.append(piece)
                seen.add(row['id'])
                budget -= len(piece) + 1
            return parts, budget
        available = max_chars - len(header) - len(playback)
        recent_budget = int(available * .58)
        recent_parts, recent_left = take_rows(recent, recent_budget, truncate=True)
        found_budget = int(available * .27) + recent_left
        found_parts, found_left = take_rows(found, found_budget, truncate=True)
        note_budget = int(available * .15) + found_left
        note_parts = []
        for item in summary:
            piece = f"- {item['kind']}: {item['text']} [U{item['source_id']}: “{item['quote']}”]"
            if len(piece) + 1 <= note_budget:
                note_parts.append(piece)
                note_budget -= len(piece) + 1
        first_parts, _ = take_rows(first, note_budget, truncate=True)
        return (header + playback + '\n'.join(recent_parts + found_parts + note_parts + first_parts))[:max_chars]

    def _worker(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                if self._closed:
                    return
                job = self._pending
                self._pending = None
            if job is None:
                continue
            version, mode, source_lines, previous, requested_at = job
            started = time.perf_counter()
            try:
                client = self.summary_client_factory()
                try:
                    prompt = self._prompt(source_lines, previous, mode)
                    result = client.ask(prompt, timeout=self.summary_timeout)
                    errors = getattr(result, 'errors', [])
                    if errors:
                        raise RuntimeError('; '.join(map(str, errors)))
                    items = self._validate(getattr(result, 'text', ''), source_lines)
                finally:
                    close = getattr(client, 'close', None)
                    if close:
                        close()
                with self._lock:
                    if self._closed:
                        return
                    if mode != self.mode:
                        # A mode change queued a replacement digest. The old
                        # result must never become current notes or be saved.
                        continue
                    if version >= self._summarized_revision:
                        self._summary = items
                        self._summarized_revision = version
                    self._last_error = None
                    self._summary_runs += 1
                    self._last_summary_metrics = {
                        'requested_at': requested_at, 'finished_at': time.time(),
                        'duration_s': round(time.perf_counter() - started, 4),
                        'version': version, 'mode': mode,
                        'model': getattr(client, 'model', None),
                        'input_tokens': getattr(result, 'input_tokens', None),
                        'output_tokens': getattr(result, 'output_tokens', None),
                        'cache_read_tokens': getattr(result, 'cache_read_tokens', None),
                        'provider_total_s': getattr(result, 'total_s', None)}
                if hasattr(self.store, 'save_summary'):
                    try:
                        with self._lock:
                            if self._closed:
                                return
                            if mode != self.mode:
                                continue
                            cited_rows = {r['id']: r for r in self.store.list_turns(self.session_id)
                                          if r['role'] == 'user'}
                            source_refs = [str(cited_rows[item['source_id']]['source_event_id'])
                                           if cited_rows[item['source_id']].get('source_event_id') is not None
                                           else f"turn:{item['source_id']}" for item in items]
                            self.store.save_summary(self.session_id,
                                                    json.dumps({'mode': mode, 'user_revision': version,
                                                                'items': items}, ensure_ascii=False),
                                                    source_refs)
                            self._persistence_error = None
                    except Exception as exc:
                        # Keep the in-process digest usable, but make the lost
                        # restart guarantee visible in diagnostics.
                        with self._lock:
                            self._persistence_error = f'{type(exc).__name__}: {exc}'[:240]
            except Exception as exc:
                with self._lock:
                    self._last_error = f'{type(exc).__name__}: {exc}'[:240]
            # A newer snapshot replaces queued intermediate snapshots. Its raw
            # turns have been in context() all along.
            with self._lock:
                if self._pending is not None:
                    self._wake.set()

    def _prompt(self, source_lines, previous, mode):
        focus = ('Retain user goals, corrections, open questions, and precise claims. '
                 'Preserve speech fillers or self-repairs only where they matter to what '
                 'the user meant or to requested speech feedback.' if mode == 'speech_feedback'
                 else 'Retain goals, corrections, open questions, precise claims, and '
                 'meaningful uncertainty. Compress incidental speech disfluency while '
                 'keeping the exact evidence quote.')
        return ("Create a compact task-adaptive memory from FINAL USER TURNS only. "
                "Prior notes are hints, never independent evidence. " + focus + " "
                "Return JSON only: {\"items\":[{\"kind\":\"goal|known|unknown|task|correction\","
                "\"text\":\"short cautious note\",\"source_id\":integer,"
                "\"quote\":\"exact contiguous substring of that user's turn\"}]}. "
                "Maximum 12 items. Never invent source IDs, causality, facts, or code inspection. "
                "Do not cite assistant/generated/tool text. Unknown causal shares stay unknown.\n"
                "Previous notes: " + json.dumps(previous, ensure_ascii=False)[:2500] + '\n'
                "Final user turns:\n" + '\n'.join(source_lines))

    @staticmethod
    def _validate(raw, source_lines):
        # Optional Markdown fences are tolerated, but arbitrary surrounding prose
        # is not: failures leave the prior digest intact.
        raw = raw.strip()
        if raw.startswith('```'):
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise ValueError('Invalid memory JSON')
        sources = {}
        for line in source_lines:
            match = re.match(r'^\[U(\d+)\] (.*)$', line, re.S)
            if match:
                sources[int(match.group(1))] = match.group(2)
        accepted = []
        for item in data['items'][:12]:
            if not isinstance(item, dict):
                continue
            kind, statement, source_id, quote = (item.get(k) for k in ('kind', 'text', 'source_id', 'quote'))
            if (kind not in ('goal', 'known', 'unknown', 'task', 'correction')
                    or type(source_id) is not int or source_id not in sources
                    or not isinstance(statement, str) or not statement.strip()
                    or not isinstance(quote, str) or len(quote.strip()) < 3
                    or quote not in sources[source_id]):
                continue
            accepted.append({'kind': kind, 'text': statement.strip()[:240],
                             'source_id': source_id, 'quote': quote[:240]})
        if not accepted:
            raise ValueError('No source-verified notes')
        return accepted

    def diagnostics(self):
        with self._lock:
            return {'user_revision': self._revision,
                    'summary_revision': self._summarized_revision,
                    'requested_revision': self._requested_revision,
                    'summary_runs': self._summary_runs,
                    'summary_pending': self._pending is not None,
                    'summary_error': self._last_error,
                    'persistence_error': self._persistence_error,
                    'last_summary': dict(self._last_summary_metrics) if self._last_summary_metrics else None,
                    'mode': self.mode}

    def set_mode(self, mode: str):
        if mode not in ('discussion', 'speech_feedback'):
            raise ValueError('mode must be discussion or speech_feedback')
        with self._lock:
            if self._closed:
                raise RuntimeError('SessionMemory is closed')
            if self.mode != mode:
                self.mode = mode
                self._summary = []
                self._summarized_revision = 0
                if self._revision:
                    self._request_summary_locked()

    @property
    def version(self):
        """Change token for context caches; changes on user turn or digest update."""
        with self._lock:
            return (self._revision, self._summarized_revision, self.mode)

    def close(self, timeout=None):
        with self._lock:
            self._closed = True
            self._wake.set()
            worker = self._thread
        if worker and worker is not threading.current_thread():
            worker.join(timeout=self.summary_timeout + 2 if timeout is None else timeout)
        return worker is None or not worker.is_alive()
