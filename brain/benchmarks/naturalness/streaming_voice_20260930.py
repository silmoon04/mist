"""Isolated live speech comparison on the production Conversation/TTS path.

The ASR adapters produce normalized events. This controller owns the local floor
policy and optional bounded JEV decision; neither changes the production server.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid


try:
    from .cascade_voice import CascadeVoice, incomplete_clause, is_backchannel, send_paced_pcm
except ImportError:
    from cascade_voice import CascadeVoice, incomplete_clause, is_backchannel, send_paced_pcm

from duplex.turn_policy import backchannel, continue_assistant, direct_stop, incomplete_turn


def explicit_stop(text: str) -> bool:
    """Only a direct, complete stop command can preempt at the partial stage."""
    return direct_stop(text)


class StreamingComparisonVoice(CascadeVoice):
    """Nova, Flux, or Scribe recognition with matched local floor policies."""

    def __init__(self, *args, asr_provider='nova', asr_adapter=None,
                 jev_enabled=False, jev_policy=None, live_mode=False,
                 complete_hold_ms=350,
                 incomplete_min_hold_ms=900, incomplete_max_hold_ms=1500, **kwargs):
        # The comparison uses one local floor policy in both conditions. JEV
        # supplies semantic advice only at a candidate endpoint.
        kwargs['floor'] = 'selective'
        if asr_provider == 'flux' and kwargs.get('endpoint_ms') is None:
            # CascadeVoice validates this legacy setting, but Flux does not use it.
            kwargs['endpoint_ms'] = 500
        super().__init__(*args, **kwargs)
        if asr_provider not in ('nova', 'flux', 'scribe'):
            raise ValueError('Unknown comparison ASR provider')
        if jev_enabled and jev_policy is None:
            raise ValueError('JEV enabled comparison requires a policy')
        self.asr_provider = asr_provider
        self.live_mode = live_mode
        if not 0 <= complete_hold_ms <= 1500:
            raise ValueError('Invalid complete endpoint hold')
        self.complete_hold_s = complete_hold_ms / 1000
        if not 0 <= incomplete_min_hold_ms <= incomplete_max_hold_ms:
            raise ValueError('Invalid incomplete endpoint hold bounds')
        self.incomplete_min_hold_s = incomplete_min_hold_ms / 1000
        self.incomplete_max_hold_s = incomplete_max_hold_ms / 1000
        self._incomplete_hold_started_at = None
        self.asr_adapter = asr_adapter
        self.jev_enabled = bool(jev_enabled)
        self.jev_policy = jev_policy
        self._asr_epoch = 0
        self._endpoint_serial = 0
        self._candidate_task = None
        self._provider_turn = None
        self._final_segments: list[tuple[str, str]] = []
        self._aggregate_provider_turns: set[str] = set()
        self._interim = ''
        self._committed_provider_turns: set[str] = set()
        self._provider_turn_serial = 0
        self._local_stop_fired = False
        self.jev_drained = None

    async def debug(self, phase, **detail):
        if self.asr_provider == 'flux':
            detail.update(endpoint_ms=None, turn_policy='flux_end_of_turn')
        await super().debug(phase, **detail)

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._startup_worker = asyncio.create_task(asyncio.to_thread(self.make_client))
        try:
            self.client = await asyncio.wait_for(asyncio.shield(self._startup_worker), timeout=22)
            if self.closed:
                raise RuntimeError('Voice session closed during model startup')
            if self.asr_adapter is None:
                try:
                    from . import streaming_asr_20260930 as asr_module
                except ImportError:
                    import streaming_asr_20260930 as asr_module
                cls = getattr(asr_module, {'nova': 'Nova3ASR', 'flux': 'FluxASR',
                                           'scribe': 'ScribeRealtimeASR'}[self.asr_provider])
                key_name = 'ELEVENLABS_API_KEY' if self.asr_provider == 'scribe' else 'DEEPGRAM_API_KEY'
                key = self.env.get(key_name, '').strip('"\'')
                if not key:
                    raise RuntimeError(f'{key_name} is unavailable')
                self.asr_adapter = cls(key, sample_rate=16000)
            await self.asr_adapter.start(self.asr_event)
            self.task(self.send_audio())
            self.task(self.model_worker())
            mode = 'Live' if self.live_mode else 'Benchmark'
            await self.emit({'type': 'ready', 'provider': f'{mode} streaming: {self.asr_provider} + {self.provider}',
                             'asr_provider': self.asr_provider, 'jev_enabled': self.jev_enabled,
                             'turn_policy': 'flux_end_of_turn' if self.asr_provider == 'flux' else 'provider_endpoint',
                             'listener_ack_supported': True, 'speech_backend': 'streaming-tts',
                             'backing_model': self.model, 'voice_id': '24AMj4dc02cYAwoUnqzN',
                             'hardware_connected': False, 'floor_policy': 'selective',
                             'reasoning_effort': self.reasoning_effort,
                             'conversation_policy': self.conversation_policy,
                             'parallel_tool_calls': self.parallel_tool_calls,
                             'reasoning_controls': dict(self.reasoning_controls),
                             'max_output_tokens': self.max_output_tokens,
                             'output_limit_kind': self.output_limit_kind,
                             'endpoint_ms': None if self.asr_provider == 'flux' else self.endpoint_ms,
                             'incomplete_hold_ms': max(self.hold_s, self.incomplete_min_hold_s) * 1000 if self.live_mode else self.hold_s * 1000,
                             'incomplete_max_hold_ms': self.incomplete_max_hold_s * 1000,
                             'complete_hold_ms': self.complete_hold_s * 1000 if self.live_mode and self.asr_provider == 'flux' else 0,
                             'typed_input_supported': True,
                             'startup': {'media_ready_ms': round((time.monotonic() - self.started_at) * 1000)}})
            await self.debug('streaming_comparison_ready', asr_provider=self.asr_provider,
                             jev_enabled=self.jev_enabled, recognition_during_playback=True)
        except BaseException:
            await self.close()
            raise

    async def send_audio(self):
        try:
            # Flux batches these paced frames to 80 ms inside its adapter.
            await send_paced_pcm(self.track, self.asr_adapter.send_pcm16k, lambda: self.closed)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.transport_failure('stt_send_failed', error)

    def _text(self):
        return ' '.join(text for _, text in self._final_segments if text).strip()

    def _display(self):
        final = self._text()
        if self.asr_provider == 'flux' and self._interim:
            # Flux updates are cumulative within a provider turn. Replace that
            # turn's older final while retaining any earlier held turns.
            current = f'turn:{self._provider_turn}'
            previous = ' '.join(text for key, text in self._final_segments if key != current)
            return ' '.join(part for part in (previous, self._interim) if part).strip()
        return (final + ' ' + self._interim).strip() if self._interim else final

    def _snapshot(self, endpoint_id, event='candidate_endpoint'):
        try:
            from .jev_turn_policy_20260930 import TurnSnapshot
        except ImportError:
            from jev_turn_policy_20260930 import TurnSnapshot
        history = tuple(('assistant' if row['role'] == 'assistant_generated' else 'user', row['text'])
                        for row in self.history[-6:] if row['role'] in ('user', 'assistant_generated'))
        return TurnSnapshot(endpoint_id=endpoint_id, epoch=self._asr_epoch,
                            revision=self.revision, transcript=self._text(), event=event,
                            short_history=history,
                            assistant_is_speaking=self.speaking(),
                            local_turn_ready=bool(self._text()),
                            user_floor_held=self.speech_active,
                            speech_active=self.speech_active, overlap_stable=self.overlap,
                            explicit_local_stop=self._local_stop_fired)

    def _invalidate_candidate(self):
        self._asr_epoch += 1
        self._endpoint_serial += 1
        if self._candidate_task:
            self._candidate_task.cancel()
            self._candidate_task = None
        if self.jev_enabled:
            self.jev_policy.update_current(self._snapshot(f'changed-{self._endpoint_serial}', 'stable_overlap'))

    async def _diagnose_asr(self, event):
        await self.debug('asr_event', asr_provider=self.asr_provider,
                         event_type=event.get('type'), provider_turn_id=event.get('turn_id'),
                         segment_id=event.get('segment_id'), text=event.get('text'),
                         segment=event.get('segment'), transcript=event.get('transcript'),
                         metadata=event.get('metadata'), words=event.get('words'),
                         shadow=bool(event.get('shadow')),
                         raw=event.get('raw'))

    async def _maybe_yield_partial(self):
        if not self.overlap:
            return
        text = self._display()
        if explicit_stop(text) and not self._local_stop_fired:
            self._local_stop_fired = True
            await self.debug('explicit_stop_yield', text=text)
            await self.open_user_turn()
        # General partials are provisional ASR hypotheses. Word count does
        # not distinguish a takeover from negation or quoted speech.

    async def asr_event(self, event):
        """Handle provider events quickly; semantic work runs in a separate task."""
        if self.closed:
            return
        await self._diagnose_asr(event)
        if event.get('shadow') or str(event.get('type', '')).startswith('shadow_'):
            return
        kind = event.get('type')
        if kind == 'error':
            await self.transport_failure('stt_receive_failed', RuntimeError('ASR provider error'))
            return
        # Timestamp/alignment updates may arrive after the next VAD turn has
        # started. They are diagnostic data, never floor or turn transitions.
        if kind not in ('speech_start', 'turn_resumed', 'partial', 'final', 'turn_end'):
            return
        provider_turn = event.get('turn_id')
        if provider_turn is not None:
            provider_turn = str(provider_turn)
            if self._provider_turn is None:
                self._provider_turn = provider_turn
            elif provider_turn != self._provider_turn:
                if self._text() and self.asr_provider != 'flux':
                    await self._candidate_endpoint('provider_turn_changed')
                self._provider_turn = provider_turn
        if kind in ('speech_start', 'turn_resumed'):
            self._invalidate_candidate()
            await self.listener.begin()
            if not self.speech_active:
                self.overlap = self.speaking()
                self.user_start_ms = round((time.monotonic() - self.started_at) * 1000)
            self.speech_active = True
            await self.debug('speech_started' if kind == 'speech_start' else 'turn_resumed',
                             asr_provider=self.asr_provider, overlap=self.overlap,
                             provider_turn_id=self._provider_turn)
            if self.asr_provider == 'flux':
                first_text = str(event.get('segment') or event.get('text') or '').strip()
                if first_text and not self.listener.possible_cue_echo(first_text):
                    self._interim = first_text
                    await self.listener.partial(self._display())
                    await self._maybe_yield_partial()
            return
        if kind not in ('partial', 'final', 'turn_end'):
            return
        text = str(event.get('segment') or event.get('text') or '').strip()
        if text and self.listener.possible_cue_echo(text):
            await self.debug('listener_cue_echo_ignored', text=text)
            return
        if text:
            if not self.speech_active:
                self.overlap = self.speaking()
                self.user_start_ms = round((time.monotonic() - self.started_at) * 1000)
                self.speech_active = True
            self._invalidate_candidate()
            if kind == 'partial':
                self._interim = text
            elif kind == 'final':
                if event.get('cumulative'):
                    # Flux revises the entire current turn instead of sending
                    # independently finalized phrases. Prior provider turns
                    # held by the floor policy remain part of this user turn.
                    key = f'turn:{self._provider_turn}'
                    for index, (old_key, _) in enumerate(self._final_segments):
                        if old_key == key:
                            self._final_segments[index] = (key, text)
                            break
                    else:
                        self._final_segments.append((key, text))
                else:
                    key = str(event.get('segment_id') or f'piece-{len(self._final_segments)}')
                    for index, (old_key, _) in enumerate(self._final_segments):
                        if old_key == key:
                            self._final_segments[index] = (key, text)
                            break
                    else:
                        if not self._final_segments or self._final_segments[-1][1] != text:
                            self._final_segments.append((key, text))
                if self._provider_turn is not None:
                    self._aggregate_provider_turns.add(self._provider_turn)
                self._interim = ''
            await self.listener.partial(self._display())
            await self.debug('stt_final' if kind == 'final' else 'stt_partial',
                             text=text, assembled=self._display(), overlap=self.overlap,
                             asr_provider=self.asr_provider)
            if kind == 'partial':
                await self._maybe_yield_partial()
        if kind == 'turn_end':
            if not self._text() and text:
                self._final_segments = [('endpoint', text)]
                self._interim = ''
            self.speech_active = False
            await self._candidate_endpoint('provider_turn_end')

    async def _candidate_endpoint(self, source):
        text = self._text()
        if not text:
            return
        provider_key = self._provider_turn or f'local-{self._provider_turn_serial}'
        if provider_key in self._committed_provider_turns:
            await self.debug('duplicate_endpoint_ignored', provider_turn_id=provider_key)
            return
        self._endpoint_serial += 1
        serial = self._endpoint_serial
        if self._candidate_task:
            self._candidate_task.cancel()
        snapshot = self._snapshot(f'{provider_key}:{serial}') if self.jev_enabled else None
        await self.debug('candidate_endpoint', text=text, source=source,
                         snapshot=vars(snapshot) if snapshot else None,
                         provider_turn_id=provider_key, serial=serial)
        if (self.asr_provider == 'flux' and not self.jev_enabled
                and not self.hold_s and not incomplete_turn(text)
                and (not (self.live_mode and self.complete_hold_s)
                     or (self.overlap and self.user_turn is None
                         and (backchannel(text) or continue_assistant(text))))):
            # EndOfTurn is authoritative. Commit before reading another Flux
            # packet so a new StartOfTurn cannot cancel the finished turn.
            await self._resolve_endpoint(serial, provider_key, source, snapshot)
        else:
            # Never await network classification in the ASR callback.
            self._candidate_task = self.task(self._resolve_endpoint(serial, provider_key, source, snapshot))

    async def _resolve_endpoint(self, serial, provider_key, source, snapshot):
        text = self._text()
        semantic_wait_applied = False
        semantic_turn_ready = False
        if self.jev_enabled:
            started = time.monotonic()
            try:
                self.jev_policy.update_current(snapshot)
                await self.debug('jev_request', snapshot=vars(snapshot), provider_turn_id=provider_key)
                advice = await self.jev_policy.submit(snapshot)
                await self.debug('jev_result', decision=advice.decision,
                                 source=advice.source,
                                 applicable=advice.applicable, latency_ms=advice.latency_ms,
                                 elapsed_ms=round((time.monotonic()-started)*1000, 2),
                                 usage=advice.usage, raw=advice.raw,
                                 provider_turn_id=provider_key)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                advice = None
                await self.debug('jev_failed', error_type=type(error).__name__, provider_turn_id=provider_key)
            if serial != self._endpoint_serial or provider_key != (self._provider_turn or provider_key):
                await self.debug('jev_late', provider_turn_id=provider_key, serial=serial)
                return
            live_snapshot = self._snapshot(snapshot.endpoint_id)
            live_matches = live_snapshot.identity() == snapshot.identity()
            if not live_matches:
                await self.debug('jev_local_state_changed', provider_turn_id=provider_key,
                                 snapshot=vars(snapshot), live=vars(live_snapshot))
            decision = (advice.decision if advice is not None and advice.applicable and live_matches
                        else 'uncertain')
            if decision == 'keep_speaking' and self.overlap and self.user_turn is None:
                await self._keep_floor(text, source, 'jev_keep_speaking')
                return
            if decision == 'wait' and advice is not None and advice.source == 'jev' and advice.hold_until:
                grace = max(0, min(1.5, advice.hold_until-time.monotonic())) if advice and advice.hold_until else 1.5
                await self.debug('jev_wait_applied', text=text, grace_ms=round(grace*1000), provider_turn_id=provider_key)
                semantic_wait_applied = True
                await asyncio.sleep(grace)
                if serial != self._endpoint_serial:
                    return
            elif decision in ('yield', 'take_turn'):
                semantic_turn_ready = True
                await self.debug('jev_turn_applied', decision=decision, provider_turn_id=provider_key)
        # Both arms share the same local backchannel/incomplete rules. Semantic
        # wait can extend a clause beyond the lexical rule, but never forces a
        # standalone acknowledgement into a reply.
        if self.overlap and self.user_turn is None and (backchannel(text) or continue_assistant(text)):
            await self._keep_floor(text, source,
                                   'local_backchannel' if backchannel(text) else 'local_continue')
            return
        if (self.asr_provider == 'flux' and self.live_mode and self.complete_hold_s
                and not incomplete_turn(text)
                and not (semantic_wait_applied or semantic_turn_ready)):
            # A Flux EndOfTurn can precede another StartOfTurn in the same
            # spoken request. Keep the completed clause pending briefly so
            # the next provider turn can join it before model work starts.
            await self.debug('floor_continuation_hold', text=text,
                             hold_ms=round(self.complete_hold_s * 1000))
            await asyncio.sleep(self.complete_hold_s)
            if serial != self._endpoint_serial:
                return
        if incomplete_turn(text) and not (semantic_wait_applied or semantic_turn_ready):
            now = time.monotonic()
            if getattr(self, '_incomplete_hold_started_at', None) is None:
                self._incomplete_hold_started_at = now
            configured = self.hold_s
            if self.live_mode:
                configured = max(configured, getattr(self, 'incomplete_min_hold_s', .9))
            remaining = max(0, getattr(self, 'incomplete_max_hold_s', 1.5) - (now - self._incomplete_hold_started_at))
            grace = min(configured, remaining)
            await self.debug('floor_incomplete_hold', text=text, hold_ms=round(grace*1000),
                             remaining_ms=round(remaining*1000))
            if grace:
                await asyncio.sleep(grace)
            if serial != self._endpoint_serial:
                return
        await self._commit_provider_turn(provider_key, source)

    async def _keep_floor(self, text, source, reason):
        provider_key = self._provider_turn or f'local-{self._provider_turn_serial}'
        await self.listener.finish(text)
        await self.debug('floor_preserved', text=text, endpoint=source, reason=reason)
        await self.emit({'type': 'transcript_done', 'role': 'user', 'text': text,
                         'backchannel': True, 'floor_preserved': True})
        self._committed_provider_turns.add(provider_key)
        self._committed_provider_turns.update(self._aggregate_provider_turns)
        self._reset_asr_turn()

    async def _commit_provider_turn(self, provider_key, source):
        if provider_key in self._committed_provider_turns:
            return
        text = self._text()
        if not text:
            return
        await self.open_user_turn()
        await self.listener.finish(text)
        await self.dc_event({'type': 'input_transcript.added', 'item': {'text': text}})
        await self.dc_event({'type': 'turn.done', 'turn': {'id': self.user_turn, 'role': 'user',
                             'transcript': text, 'start_ms': self.user_start_ms}})
        await self.debug('user_committed', text=text, endpoint=source,
                         asr_provider=self.asr_provider, provider_turn_id=provider_key)
        if self.requests.full():
            raise RuntimeError('Lab model queue is full')
        self.requests.put_nowait((self.revision, text, self.user_start_ms,
                                  self.expression_revision(), False, None))
        self._committed_provider_turns.add(provider_key)
        self._committed_provider_turns.update(self._aggregate_provider_turns)
        self._reset_asr_turn()

    def _reset_asr_turn(self):
        self._final_segments = []
        self._aggregate_provider_turns = set()
        self._interim = ''
        self._provider_turn = None
        self._provider_turn_serial += 1
        self._local_stop_fired = False
        self._incomplete_hold_started_at = None
        self._candidate_task = None
        self.clear_utterance()

    async def close(self):
        try:
            await super().close()
        finally:
            try:
                if self.asr_adapter is not None:
                    await self.asr_adapter.close()
            finally:
                if self.jev_enabled and self.jev_policy is not None:
                    self.jev_drained = await self.jev_policy.aclose()


# The runner may prefer this short name when installing server.NativeVoice.
NativeVoice = StreamingComparisonVoice
