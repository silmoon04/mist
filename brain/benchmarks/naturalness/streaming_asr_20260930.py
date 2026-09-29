"""Isolated 16/24 kHz PCM16 streaming ASR adapters for the September 2026 lab.

No keys are loaded here. Callers own audio pacing (including silence), credentials,
and the lifetime of each connection. `process_raw` permits deterministic replay.
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import json
import time
from urllib.parse import urlencode

import aiohttp


SAMPLE_RATE = 16000
SUPPORTED_SAMPLE_RATES = {16000, 24000}
_PRIVATE = {'authorization', 'xi-api-key', 'api_key', 'token', 'cookie',
            'audio_base_64', 'audio', 'pcm'}


def _safe(value):
    """Retain provider diagnostics and text, never auth or audio payloads."""
    if isinstance(value, dict):
        return {k: ('[redacted]' if k.lower() in _PRIVATE else _safe(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


def _join(*parts):
    return ' '.join(str(part).strip() for part in parts if str(part).strip())


class StreamingASR:
    provider = ''
    model = ''

    def __init__(self, api_key: str, *, sample_rate: int = SAMPLE_RATE,
                 session: aiohttp.ClientSession | None = None):
        if not api_key:
            raise ValueError('ASR credential is required')
        if sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError('Expected 16 or 24 kHz mono PCM16')
        self._key = api_key
        self.sample_rate = sample_rate
        self._session = session
        self._owns_session = False
        self._ws = None
        self._reader = None
        self._callback = None
        self._committed = []
        self._partial = ''
        self.raw_events = []
        self.events = []
        self._closed = False
        self._received_close = False
        self.sent_pcm_bytes = 0
        self._opened_at = None
        self._closed_at = None
        self._first_wire_start = None
        self._first_wire_end = None
        self._last_wire_start = None
        self._last_wire_end = None
        self._first_voiced_send = None
        self._last_voiced_send = None
        self._first_text_receive = None
        self._wire_pcm_bytes = 0
        self._wire_send_count = 0
        self._max_send_await_s = 0.0
        self._max_buffered_frames = 0
        self._input_progress = []
        self._next_progress_second = 1

    @property
    def connected_duration_s(self):
        if self._opened_at is None:
            return 0.0
        return (self._closed_at or time.monotonic()) - self._opened_at

    @property
    def sent_pcm_seconds(self):
        """Audio duration submitted, including silence; billing proxy, not invoice."""
        return self.sent_pcm_bytes / (self.sample_rate * 2)

    @property
    def timing_summary(self):
        relative = lambda value: None if value is None or self._opened_at is None else round(value - self._opened_at, 4)
        return {
            'connected_duration_s': round(self.connected_duration_s, 4),
            'input_pcm_bytes': self.sent_pcm_bytes,
            'input_pcm_seconds_billing_proxy': round(self.sent_pcm_seconds, 4),
            'wire_pcm_bytes': self._wire_pcm_bytes,
            'wire_send_count': self._wire_send_count,
            'first_send_start_after_open_s': relative(self._first_wire_start),
            'first_send_end_after_open_s': relative(self._first_wire_end),
            'last_send_start_after_open_s': relative(self._last_wire_start),
            'last_send_end_after_open_s': relative(self._last_wire_end),
            'first_non_silent_send_after_open_s': relative(self._first_voiced_send),
            'last_non_silent_send_after_open_s': relative(self._last_voiced_send),
            'first_text_receive_after_open_s': relative(self._first_text_receive),
            'max_send_await_ms': round(self._max_send_await_s * 1000, 3),
            'max_buffered_frames_20ms': self._max_buffered_frames,
            'input_progress': list(self._input_progress),
        }

    def _note_buffered(self, count_bytes):
        frame_bytes = self.sample_rate * 2 // 50
        self._max_buffered_frames = max(
            self._max_buffered_frames, (count_bytes + frame_bytes - 1) // frame_bytes)

    async def _send_wire(self, pcm, awaitable):
        started = time.monotonic()
        if self._first_wire_start is None:
            self._first_wire_start = started
        self._last_wire_start = started
        if any(pcm) and self._first_voiced_send is None:
            self._first_voiced_send = started
        if any(pcm):
            self._last_voiced_send = started
        try:
            await awaitable
        finally:
            ended = time.monotonic()
            self._max_send_await_s = max(self._max_send_await_s, ended - started)
        if self._first_wire_end is None:
            self._first_wire_end = ended
        self._last_wire_end = ended
        self._wire_pcm_bytes += len(pcm)
        self._wire_send_count += 1

    @property
    def transcript(self):
        return _join(*self._committed, self._partial)

    @property
    def url(self):
        raise NotImplementedError

    @property
    def headers(self):
        raise NotImplementedError

    async def start(self, on_event):
        if self._ws is not None:
            raise RuntimeError('ASR already started')
        self._callback = on_event
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        try:
            self._ws = await asyncio.wait_for(
                self._session.ws_connect(self.url, headers=self.headers,
                                         heartbeat=20, max_msg_size=2**20), timeout=8)
        except BaseException:
            if self._owns_session:
                await self._session.close()
            raise
        self._reader = asyncio.create_task(self._read_loop())
        self._opened_at = time.monotonic()
        return self

    async def send_pcm(self, pcm: bytes):
        if not isinstance(pcm, bytes) or len(pcm) % 2:
            raise ValueError('Expected mono little-endian PCM16 bytes')
        if self._ws is None or self._closed:
            raise RuntimeError('ASR connection is not active')
        if pcm:
            await self._send_audio(pcm)
            self.sent_pcm_bytes += len(pcm)
            if self._opened_at is not None:
                wall = time.monotonic() - self._opened_at
                while wall >= self._next_progress_second and len(self._input_progress) < 32:
                    self._input_progress.append({
                        'wall_s': self._next_progress_second,
                        'pcm_s': round(self.sent_pcm_seconds, 3)})
                    self._next_progress_second += 1

    async def _send_audio(self, pcm):
        await self._send_wire(pcm, self._ws.send_bytes(pcm))

    async def send_pcm16k(self, pcm: bytes):
        if self.sample_rate != 16000:
            raise ValueError('send_pcm16k requires a 16 kHz adapter')
        await self.send_pcm(pcm)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        if self._ws is not None:
            try:
                should_drain = await self._finish_stream()
                if should_drain and self._reader is not None:
                    await asyncio.wait_for(self._reader, timeout=2)
            except asyncio.TimeoutError:
                pass
            finally:
                try:
                    await asyncio.wait_for(self._ws.close(), timeout=2)
                except asyncio.TimeoutError:
                    pass
                if self._reader is not None and not self._reader.done():
                    self._reader.cancel()
                if self._reader is not None:
                    await asyncio.gather(self._reader, return_exceptions=True)
        if self._owns_session:
            await self._session.close()
        self._closed_at = time.monotonic()

    async def _finish_stream(self):
        return False

    async def _read_loop(self):
        try:
            async for message in self._ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        packet = json.loads(message.data)
                    except (ValueError, TypeError):
                        await self._emit('error', metadata={'code': 'invalid_provider_json'})
                        continue
                    await self.process_raw(packet)
                elif message.type == aiohttp.WSMsgType.ERROR:
                    await self._emit('error', metadata={'code': 'websocket_error',
                                                       'exception_type': type(self._ws.exception()).__name__})
                    return
            if not self._closed and not self._received_close:
                await self._emit('error', metadata={'code': 'unexpected_websocket_close'})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._emit('error', metadata={'code': 'receiver_exception',
                                               'exception_type': type(error).__name__})

    async def _emit(self, kind, *, segment='', words=None, metadata=None, raw=None):
        if segment and kind in ('partial', 'final', 'speech_start') and self._first_text_receive is None:
            self._first_text_receive = time.monotonic()
        meta = metadata or {}
        event = {'type': kind, 'provider': self.provider, 'model': self.model,
                 'transcript': self.transcript, 'segment': segment, 'text': segment,
                 'turn_id': meta.get('turn_id'),
                 'segment_id': meta.get('segment_id'),
                 'cumulative': bool(meta.get('cumulative', False)),
                 'words': words or [], 'metadata': metadata or {},
                 'raw': _safe(raw) if raw is not None else None}
        self.events.append(event)
        if self._callback is not None:
            result = self._callback(event)
            if inspect.isawaitable(result):
                await result
        return event

    async def process_raw(self, raw: dict):
        if not isinstance(raw, dict):
            raise TypeError('Expected provider JSON object')
        self.raw_events.append(_safe(raw))
        await self._process(raw)

    async def _process(self, raw):
        raise NotImplementedError


class Nova3ASR(StreamingASR):
    provider, model = 'deepgram', 'nova-3'

    def __init__(self, api_key, **kwargs):
        super().__init__(api_key, **kwargs)
        self._segments = set()
        self._last_endpoint = None
        self._turn_index = 0
        self._turn_start = 0

    @property
    def url(self):
        return 'wss://api.deepgram.com/v1/listen?' + urlencode({
            'model': self.model, 'encoding': 'linear16', 'sample_rate': self.sample_rate,
            'channels': 1, 'interim_results': 'true', 'endpointing': 500,
            'vad_events': 'true', 'filler_words': 'true', 'language': 'en-GB',
            'punctuate': 'true', 'smart_format': 'false'})

    @property
    def headers(self):
        return {'Authorization': 'Token ' + self._key}

    async def _finish_stream(self):
        await self._ws.send_json({'type': 'CloseStream'})
        return True

    async def _process(self, raw):
        kind = raw.get('type')
        if kind == 'SpeechStarted':
            await self._emit('speech_start', metadata={'source': 'provider_vad',
                                                       'turn_id': self._turn_index}, raw=raw)
        elif kind == 'Results':
            alternatives = raw.get('channel', {}).get('alternatives') or []
            alt = alternatives[0] if alternatives else {}
            segment = alt.get('transcript', '').strip()
            words = alt.get('words') or []
            boundary = (raw.get('start'), raw.get('duration'))
            meta = {'source': 'nova_segment', 'start': raw.get('start'),
                    'duration': raw.get('duration'), 'speech_final': bool(raw.get('speech_final')),
                    'is_final': bool(raw.get('is_final')), 'turn_id': self._turn_index,
                    'segment_id': f"{self._turn_index}:{raw.get('start')}",
                    'audio_start': raw.get('start'),
                    'audio_end': (raw['start'] + raw['duration']
                                  if isinstance(raw.get('start'), (int, float)) and
                                  isinstance(raw.get('duration'), (int, float)) else None)}
            if raw.get('is_final'):
                # Deepgram segments are deltas; repeated packets at the same audio
                # window are not new words, even if their text is identical.
                key = boundary
                if segment and key not in self._segments:
                    self._segments.add(key)
                    self._committed.append(segment)
                    self._partial = ''
                    await self._emit('final', segment=segment, words=words,
                                     metadata=meta, raw=raw)
                else:
                    self._partial = ''
            elif segment and segment != self._partial:
                self._partial = segment
                await self._emit('partial', segment=segment, words=words,
                                 metadata=meta, raw=raw)
            if raw.get('speech_final') and boundary != self._last_endpoint:
                self._last_endpoint = boundary
                await self._emit('turn_end', segment=_join(*self._committed[self._turn_start:]),
                                 metadata={**meta, 'source': 'silence_endpoint',
                                           'cumulative': True}, raw=raw)
                self._turn_index += 1
                self._turn_start = len(self._committed)
        elif kind == 'Error':
            await self._emit('error', metadata={'code': raw.get('code'),
                                                'detail': raw.get('description')}, raw=raw)


class FluxASR(StreamingASR):
    provider, model = 'deepgram', 'flux-general-en'

    def __init__(self, api_key, **kwargs):
        super().__init__(api_key, **kwargs)
        self._ended_turns = set()
        self._audio_buffer = bytearray()
        self.packet_ms = 80

    @property
    def url(self):
        # No eager_eot_threshold: EagerEndOfTurn/TurnResumed should not occur.
        return 'wss://api.deepgram.com/v2/listen?' + urlencode({
            'model': self.model, 'encoding': 'linear16', 'sample_rate': self.sample_rate,
            'eot_threshold': 0.85, 'eot_timeout_ms': 8000})

    @property
    def headers(self):
        return {'Authorization': 'Token ' + self._key}

    async def _finish_stream(self):
        if self._audio_buffer:
            pcm = bytes(self._audio_buffer)
            await self._send_wire(pcm, self._ws.send_bytes(pcm))
            self._audio_buffer.clear()
        await self._ws.send_json({'type': 'CloseStream'})
        return True

    async def _send_audio(self, pcm):
        self._audio_buffer.extend(pcm)
        self._note_buffered(len(self._audio_buffer))
        size = self.sample_rate * 2 * self.packet_ms // 1000
        while len(self._audio_buffer) >= size:
            packet = bytes(self._audio_buffer[:size])
            await self._send_wire(packet, self._ws.send_bytes(packet))
            del self._audio_buffer[:size]

    async def _process(self, raw):
        if raw.get('type') == 'Error':
            await self._emit('error', metadata={'code': raw.get('code'),
                                                'detail': raw.get('description')}, raw=raw)
            return
        if raw.get('type') != 'TurnInfo':
            return
        kind = raw.get('event')
        segment = raw.get('transcript', '').strip()
        words = raw.get('words') or []
        index = raw.get('turn_index')
        meta = {'source': 'flux_model', 'turn_index': index, 'turn_id': index,
                'segment_id': index, 'cumulative': True,
                'audio_window_start': raw.get('audio_window_start'),
                'audio_window_end': raw.get('audio_window_end'),
                'audio_start': raw.get('audio_window_start'),
                'audio_end': raw.get('audio_window_end'),
                'trigger': raw.get('trigger'),
                'end_of_turn_confidence': raw.get('end_of_turn_confidence')}
        if kind == 'StartOfTurn':
            self._partial = segment
            await self._emit('speech_start', segment=segment, words=words,
                             metadata=meta, raw=raw)
        elif kind == 'TurnResumed':
            self._partial = segment
            await self._emit('turn_resumed', segment=segment, words=words,
                             metadata=meta, raw=raw)
        elif kind in ('Update', 'EagerEndOfTurn'):
            if segment and segment != self._partial:
                self._partial = segment
                await self._emit('partial', segment=segment, words=words,
                                 metadata={**meta, 'speculative': kind == 'EagerEndOfTurn'}, raw=raw)
        elif kind == 'EndOfTurn' and index not in self._ended_turns:
            self._ended_turns.add(index)
            self._partial = ''
            if segment:
                self._committed.append(segment)
                await self._emit('final', segment=segment, words=words,
                                 metadata=meta, raw=raw)
            await self._emit('turn_end', segment=segment, words=words,
                             metadata=meta, raw=raw)


class ScribeRealtimeASR(StreamingASR):
    provider, model = 'elevenlabs', 'scribe_v2_realtime'

    def __init__(self, api_key, *, vad_silence_secs=0.5, **kwargs):
        super().__init__(api_key, **kwargs)
        self.vad_silence_secs = vad_silence_secs
        self._last_commit_index = None
        self._unaligned_finals = []
        self._commit_count = 0
        self._audio_buffer = bytearray()
        self.packet_ms = 100

    @property
    def url(self):
        return 'wss://api.elevenlabs.io/v1/speech-to-text/realtime?' + urlencode({
            'model_id': self.model, 'audio_format': f'pcm_{self.sample_rate}',
            'commit_strategy': 'vad', 'vad_threshold': 0.4,
            'vad_silence_threshold_secs': self.vad_silence_secs,
            'min_speech_duration_ms': 100, 'min_silence_duration_ms': 100,
            'include_timestamps': 'true', 'no_verbatim': 'false'})

    @property
    def headers(self):
        return {'xi-api-key': self._key}

    async def _send_audio(self, pcm):
        self._audio_buffer.extend(pcm)
        self._note_buffered(len(self._audio_buffer))
        size = self.sample_rate * 2 * self.packet_ms // 1000
        while len(self._audio_buffer) >= size:
            await self._send_chunk(bytes(self._audio_buffer[:size]))
            del self._audio_buffer[:size]

    async def _send_chunk(self, pcm):
        await self._send_wire(pcm, self._ws.send_json({
            'message_type': 'input_audio_chunk',
            'audio_base_64': base64.b64encode(pcm).decode('ascii'),
            'sample_rate': self.sample_rate}))

    async def _finish_stream(self):
        if self._audio_buffer:
            await self._send_chunk(bytes(self._audio_buffer))
            self._audio_buffer.clear()
        return False

    async def _process(self, raw):
        kind = raw.get('message_type')
        segment = raw.get('text', '').strip()
        if kind == 'partial_transcript':
            if segment and not self._partial:
                await self._emit('speech_start', metadata={'source': 'first_partial',
                                                           'turn_id': self._commit_count,
                                                           'segment_id': self._commit_count}, raw=raw)
            if segment != self._partial:
                self._partial = segment
                await self._emit('partial', segment=segment,
                                 metadata={'source': 'scribe_partial',
                                           'turn_id': self._commit_count,
                                           'segment_id': self._commit_count}, raw=raw)
        elif kind == 'committed_transcript':
            self._partial = ''
            self._last_commit_index = self._commit_count
            self._commit_count += 1
            if segment:
                self._committed.append(segment)
                final = await self._emit('final', segment=segment,
                                 metadata={'source': 'vad_commit', 'commit_index': self._last_commit_index,
                                           'turn_id': self._last_commit_index,
                                           'segment_id': self._last_commit_index}, raw=raw)
                self._unaligned_finals.append(final)
            await self._emit('turn_end', segment=segment,
                             metadata={'source': 'vad_commit', 'commit_index': self._last_commit_index,
                                       'turn_id': self._last_commit_index,
                                       'segment_id': self._last_commit_index}, raw=raw)
        elif kind == 'committed_transcript_with_timestamps':
            # Delayed metadata for the preceding commit; never append text twice.
            for index, final in enumerate(self._unaligned_finals):
                if final['segment'] == segment:
                    self._unaligned_finals.pop(index)
                    words = raw.get('words') or []
                    final['words'] = words
                    final['metadata']['timestamps_delayed'] = True
                    await self._emit('alignment', segment=segment, words=words,
                                     metadata={'source': 'timestamp_update',
                                               'turn_id': final['turn_id'],
                                               'segment_id': final['segment_id']}, raw=raw)
                    break
        elif kind in ('error', 'auth_error', 'quota_exceeded', 'transcriber_error',
                      'input_error', 'invalid_request', 'commit_throttled',
                      'unaccepted_terms', 'rate_limited', 'queue_overflow',
                      'resource_exhausted', 'session_time_limit_exceeded',
                      'chunk_size_exceeded', 'insufficient_audio_activity'):
            await self._emit('error', metadata={'code': kind, 'detail': raw.get('error')}, raw=raw)


class FluxScribeShadowASR:
    """Flux drives the floor; Scribe is a passive, independently queued observer.

    Queue overflow and Scribe connection/send failures produce `shadow` diagnostics.
    They never change Flux transcript or turn signals. PCM is forwarded once to
    each provider; the second stream can therefore incur separate ASR charges.
    """
    provider, model = 'deepgram', 'flux-general-en'

    def __init__(self, flux_key: str, scribe_key: str, *, sample_rate=16000,
                 flux=None, scribe=None, shadow_queue_frames=200):
        self.flux = flux or FluxASR(flux_key, sample_rate=sample_rate)
        self.scribe = scribe or ScribeRealtimeASR(scribe_key, sample_rate=sample_rate)
        self.sample_rate = sample_rate
        self.packet_ms = self.flux.packet_ms
        self._callback = None
        self._shadow_queue = asyncio.Queue(maxsize=shadow_queue_frames)
        self._shadow_task = None
        self._shadow_active = False
        self._closed = False
        self.shadow_dropped_pcm_bytes = 0
        self.shadow_events = []
        self._shadow_queued_bytes = 0
        self._shadow_max_queued_frames = 0

    @property
    def transcript(self):
        return self.flux.transcript

    @property
    def url(self):
        return self.flux.url

    @property
    def events(self):
        return self.flux.events

    @property
    def raw_events(self):
        return {'flux': self.flux.raw_events, 'scribe_shadow': self.scribe.raw_events}

    @property
    def sent_pcm_bytes(self):
        return self.flux.sent_pcm_bytes

    @property
    def sent_pcm_seconds(self):
        return self.flux.sent_pcm_seconds

    @property
    def connected_duration_s(self):
        return self.flux.connected_duration_s

    @property
    def shadow_sent_pcm_bytes(self):
        return self.scribe.sent_pcm_bytes

    @property
    def shadow_sent_pcm_seconds(self):
        return self.scribe.sent_pcm_seconds

    @property
    def shadow_connected_duration_s(self):
        return self.scribe.connected_duration_s

    @property
    def timing_summary(self):
        return {'flux': self.flux.timing_summary,
                'scribe_shadow': self.scribe.timing_summary,
                'shadow_max_queued_frames_20ms': self._shadow_max_queued_frames,
                'shadow_dropped_pcm_bytes': self.shadow_dropped_pcm_bytes}

    async def _dispatch(self, event):
        if self._callback is not None:
            result = self._callback(event)
            if inspect.isawaitable(result):
                await result

    async def _shadow_event(self, event):
        diagnostic = {**event, 'type': 'shadow', 'shadow': True,
                      'provider': 'elevenlabs', 'model': 'scribe_v2_realtime',
                      'metadata': {**event.get('metadata', {}),
                                   'original_event_type': event.get('type'),
                                   'original_provider': event.get('provider'),
                                   'shadow_queue_frames': self._shadow_queue.qsize()}}
        self.shadow_events.append(diagnostic)
        await self._dispatch(diagnostic)

    async def _shadow_failure(self, code, error_type=None):
        await self._shadow_event({'type': 'error', 'provider': 'elevenlabs',
                                  'model': 'scribe_v2_realtime', 'text': '', 'segment': '',
                                  'transcript': self.scribe.transcript,
                                  'turn_id': None, 'segment_id': None,
                                  'cumulative': False, 'words': [], 'raw': None,
                                  'metadata': {'code': code, 'exception_type': error_type,
                                               'dropped_pcm_bytes': self.shadow_dropped_pcm_bytes}})

    async def start(self, on_event):
        self._callback = on_event
        await self.flux.start(self._dispatch)
        try:
            await self.scribe.start(self._shadow_event)
        except Exception as error:
            await self._shadow_failure('shadow_connect_failed', type(error).__name__)
        else:
            self._shadow_active = True
            self._shadow_task = asyncio.create_task(self._pump_shadow())
        return self

    async def _pump_shadow(self):
        try:
            while True:
                pcm = await self._shadow_queue.get()
                try:
                    await self.scribe.send_pcm(pcm)
                finally:
                    self._shadow_queued_bytes -= len(pcm)
                    self._shadow_queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._shadow_active = False
            await self._shadow_failure('shadow_send_failed', type(error).__name__)

    async def send_pcm(self, pcm: bytes):
        if self._closed:
            raise RuntimeError('ASR connection is closed')
        await self.flux.send_pcm(pcm)
        if pcm and self._shadow_active:
            try:
                self._shadow_queue.put_nowait(pcm)
                self._shadow_queued_bytes += len(pcm)
                frame_bytes = self.sample_rate * 2 // 50
                self._shadow_max_queued_frames = max(
                    self._shadow_max_queued_frames,
                    (self._shadow_queued_bytes + frame_bytes - 1) // frame_bytes)
            except asyncio.QueueFull:
                self._shadow_active = False
                self.shadow_dropped_pcm_bytes += len(pcm) + sum(
                    len(chunk) for chunk in list(self._shadow_queue._queue))
                await self._shadow_failure('shadow_queue_overflow')
                if self._shadow_task is not None:
                    self._shadow_task.cancel()

    async def send_pcm16k(self, pcm: bytes):
        if self.sample_rate != 16000:
            raise ValueError('send_pcm16k requires a 16 kHz adapter')
        await self.send_pcm(pcm)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        if self._shadow_task is not None:
            if self._shadow_active:
                try:
                    await asyncio.wait_for(self._shadow_queue.join(), timeout=2)
                except asyncio.TimeoutError:
                    self.shadow_dropped_pcm_bytes += sum(len(x) for x in list(self._shadow_queue._queue))
                    await self._shadow_failure('shadow_close_queue_lag')
            self._shadow_task.cancel()
            await asyncio.gather(self._shadow_task, return_exceptions=True)
        flux_result, scribe_result = await asyncio.gather(
            self.flux.close(), self.scribe.close(), return_exceptions=True)
        if isinstance(scribe_result, BaseException):
            await self._shadow_failure('shadow_close_failed', type(scribe_result).__name__)
        if isinstance(flux_result, BaseException):
            raise flux_result


__all__ = ['Nova3ASR', 'FluxASR', 'ScribeRealtimeASR',
           'FluxScribeShadowASR', 'SAMPLE_RATE']
