"""One ElevenLabs WebSocket per conversation; one ordered context per reply."""
import asyncio
import base64
from collections import deque
import json
import logging
import re
import time
import uuid

import aiohttp
from duplex.conversion import VOICE
from duplex.phrasing import next_phrase
from duplex.lipsync import mouth_cues, slice_cues, caption_cues, canonical_caption_progress

LOG = logging.getLogger('mist.voice')

# Flash settings change delivery variation, not a guaranteed named emotion.
# Select once per reply so streamed phrases share a stable synthesis context.
DELIVERY_STABILITY = {'neutral': .5, 'warm': .4, 'gentle': .65, 'bright': .3, 'serious': .75,
                      'curious': .45, 'amused': .4, 'reassuring': .6, 'urgent': .65}
DELIVERY_TAGS = {'neutral': None, 'warm': '[warmly]', 'gentle': '[gently]',
                 'bright': '[excited]', 'serious': '[seriously]', 'curious': '[curiously]',
                 'amused': '[amused]', 'reassuring': '[reassuringly]', 'urgent': '[calmly]'}
DELIVERY_TAG_PATTERN = re.compile(r'\[(?:warmly|gently|excited|seriously|curiously|amused|reassuringly|calmly)\]\s*', re.IGNORECASE)
FLASH_MODEL = 'eleven_flash_v2_5'
EXPRESSIVE_MODEL = 'eleven_v4_turbo'
PCM_PACKET_BYTES = 24000  # At most 500ms of 24kHz signed 16-bit PCM.


class StreamingTTS:
    def __init__(self, key, emit, model_id=FLASH_MODEL, voice_id=VOICE):
        if model_id not in (FLASH_MODEL, EXPRESSIVE_MODEL):
            raise ValueError('Unsupported ElevenLabs streaming model')
        if not isinstance(voice_id, str) or not re.fullmatch(r'[A-Za-z0-9]{20}', voice_id):
            raise ValueError('Invalid ElevenLabs voice ID')
        self.key, self.emit = key, emit
        self.model_id = model_id
        self.voice_id = voice_id
        self.epoch = self.sequence = 0
        self.muted = self.closed = False
        self.client = self.socket = self.reader = self.watchdog = None
        self.pending = {}
        self.active = None
        self.metrics = []
        self.connect_lock = asyncio.Lock()
        self.drain_lock = asyncio.Lock()

    def diagnostic(self, event, **values):
        LOG.info(json.dumps({'event': event, 'at': time.time(), **values}, allow_nan=False))

    async def _provider_send(self, event):
        """Time provider writes without logging text, credentials or PCM."""
        began = time.monotonic()
        started_at = time.time()
        await self.socket.send_json(event)
        entry = self.pending.get(event.get('context_id'))
        self.diagnostic('tts_provider_send', sequence=entry['seq'] if entry else None,
            epoch=entry['epoch'] if entry else self.epoch, send_started_at=started_at,
            send_completed_at=time.time(), elapsed_ms=round((time.monotonic()-began)*1000,3),
            flush=bool(event.get('flush')), close_context=bool(event.get('close_context')),
            keep_alive=bool(event.get('keep_alive')), has_text=bool(event.get('text') or event.get('inputs')))

    async def start(self):
        self.client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5, connect=2))
        await self.connect()
        self.watchdog = asyncio.create_task(self.watch())

    async def connect(self):
        async with self.connect_lock:
            if self.closed:
                raise RuntimeError('TTS session is closed')
            if self.socket is not None and not self.socket.closed:
                return
            began = time.monotonic()
            if self.model_id == EXPRESSIVE_MODEL:
                url = ('wss://api.elevenlabs.io/v1/text-to-dialogue/multi-stream-input'
                       f'?model_id={self.model_id}&output_format=pcm_24000&sync_alignment=true')
            else:
                url = (f'wss://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/multi-stream-input'
                       f'?model_id={self.model_id}&output_format=pcm_24000&inactivity_timeout=180&sync_alignment=true')
            try:
                for attempt in range(3):
                    try:
                        self.socket = await self.client.ws_connect(url, headers={'xi-api-key': self.key},
                                                                  heartbeat=20, max_msg_size=4*1024*1024)
                        break
                    except (aiohttp.ClientConnectionError,asyncio.TimeoutError):
                        if attempt==2 or self.closed:raise
                        await asyncio.sleep(.1*(attempt+1))
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                self.diagnostic('tts_connect_failed', error_type=type(error).__name__, status=getattr(error,'status',None))
                raise RuntimeError('ElevenLabs streaming TTS could not connect. Check voice access and try again.') from None
            self.diagnostic('tts_connected', elapsed_ms=round((time.monotonic()-began)*1000))
            self.reader = asyncio.create_task(self.receive(self.socket))

    async def warm(self):
        await self.connect()

    async def feed(self, pcm):
        # Native audio is deliberately not sent to the converter or the speaker.
        pass

    async def begin(self, turn_id, audible=True, delivery='neutral'):
        if delivery not in DELIVERY_STABILITY:
            raise ValueError('Unknown speech delivery profile')
        if self.closed or self.muted:
            return
        requested_epoch = self.epoch
        if self.active is not None:
            await self.finish()
        try:
            await self.connect()
            if self.closed or self.muted or self.epoch != requested_epoch:
                return
            if len(self.pending) >= 4:
                raise RuntimeError('Too many pending TTS replies')
            context = uuid.uuid4().hex
            self.active = context
            self.pending[context] = {'epoch': self.epoch, 'seq': self.sequence, 'chunks': deque(),
                'done': False, 'audible': audible, 'text': '', 'buffer': '', 'first_text': None, 'first_audio': None,
                'bytes': 0, 'last_send': time.monotonic(), 'last_keepalive': time.monotonic(),
                'finished': False, 'phrase_count': 0,
                'caption_text':'','caption_alignment_complete':True,'delivery':delivery,'turn_id':turn_id,
                'provider_started':False,'provider_initialized':False,'alignment_tag_state':{},
                'alignment_expected_tag':None,'next_phrase_delivery':None,
                'provider_chunks':0,'last_provider_audio':None,'last_audio_emit':None}
            self.sequence += 1
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, RuntimeError) as error:
            await self.fail(type(error).__name__)

    def set_delivery(self, delivery):
        """Update the active reply's delivery until its first provider frame is committed."""
        if delivery not in DELIVERY_STABILITY or self.closed or self.muted:
            return False
        entry = self.pending.get(self.active)
        if entry is None or entry['provider_started'] or entry['provider_initialized']:
            return False
        entry['delivery'] = delivery
        return True

    def set_next_phrase_delivery(self, delivery):
        """Apply an expressive tag to the next unsent phrase in this context."""
        if delivery not in DELIVERY_STABILITY or self.model_id != EXPRESSIVE_MODEL:
            return False
        entry = self.pending.get(self.active)
        if entry is None or entry['finished'] or self.closed or self.muted:
            return False
        if not entry['provider_started']:
            return self.set_delivery(delivery)
        entry['next_phrase_delivery'] = delivery
        return True

    def _current(self, context, entry):
        return (not self.closed and not self.muted and entry['epoch'] == self.epoch
                and self.pending.get(context) is entry)

    async def _start_provider_context(self, context, entry, first_text):
        if not self._current(context, entry):
            return None
        if entry['provider_started']:
            return first_text
        # Commit synchronously before awaiting I/O so an affect result cannot race
        # between provider initialization and the first tagged text frame.
        entry['provider_started'] = True
        delivery = entry['delivery']
        if self.model_id == EXPRESSIVE_MODEL:
            await self._provider_send({'context_id': context, 'voices': [self.voice_id]})
            if not self._current(context, entry):
                return None
            tag = DELIVERY_TAGS[delivery]
            entry['alignment_expected_tag'] = tag
            normalized_text = first_text if first_text.endswith(' ') else first_text + ' '
            tagged_text = f'{tag} {normalized_text}' if tag else normalized_text
            await self._provider_send({'context_id': context,
                'inputs': [{'text': tagged_text, 'voice_id': self.voice_id}], 'flush': True})
            if not self._current(context, entry):
                return None
            entry['provider_initialized'] = True
            await self.emit({'type':'speech_style','phase':'committed','turn_id':entry['turn_id'],
                'context_id':context,'epoch':entry['epoch'],'delivery':delivery,'tagged':tag is not None,
                'model':self.model_id,'voice_id':self.voice_id,'tag_name':tag[1:-1] if tag else None,
                'named_emotion_guaranteed':False})
            return None
        await self._provider_send({'context_id': context, 'text': ' ',
            'voice_settings': {'stability': DELIVERY_STABILITY[delivery], 'similarity_boost': .8, 'use_speaker_boost': False},
            'generation_config': {'chunk_length_schedule': [120, 160, 250, 290]}})
        if not self._current(context, entry):
            return None
        entry['provider_initialized'] = True
        await self.emit({'type':'speech_style','phase':'committed','turn_id':entry['turn_id'],
            'context_id':context,'epoch':entry['epoch'],'delivery':delivery,'stability':DELIVERY_STABILITY[delivery],
            'model':self.model_id,'voice_id':self.voice_id,'named_emotion_guaranteed':False})
        return first_text

    async def _send_phrase(self, context, entry, text):
        if not self._current(context, entry):
            return
        if self.model_id == EXPRESSIVE_MODEL:
            if not entry['provider_started']:
                remainder = await self._start_provider_context(context, entry, text)
                if remainder is None:
                    return
                text = remainder
            if not self._current(context, entry):
                return
            next_delivery = entry['next_phrase_delivery']
            if next_delivery is not None:
                entry['next_phrase_delivery'] = None
                tag = DELIVERY_TAGS[next_delivery]
                if tag:
                    text = f'{tag} {text}'
                    entry['alignment_tag_state'] = {}
                    entry['alignment_expected_tag'] = tag
            await self._provider_send({'context_id':context,
                'inputs':[{'text':text if text.endswith(' ') else text+' ','voice_id':self.voice_id}],
                'flush':True})
        else:
            if not entry['provider_started']:
                await self._start_provider_context(context, entry, text)
            if not self._current(context, entry):
                return
            await self._provider_send({'context_id':context,'text':text if text.endswith(' ') else text+' ','flush':True})

    async def text(self, delta):
        if self.muted or self.closed or not delta or self.active not in self.pending:
            return
        context = self.active
        entry = self.pending[context]
        entry['text'] += delta
        entry['buffer'] += delta
        if len(entry['text']) > 8000:
            await self.fail('reply_too_long')
            return
        now = time.monotonic()
        if entry['first_text'] is None and delta.strip():
            entry['first_text'] = now
        entry['last_send'] = now
        try:
            while (boundary:=next_phrase(entry['buffer'])) is not None:
                end,reason=boundary
                text,entry['buffer']=entry['buffer'][:end],entry['buffer'][end:]
                await self._send_phrase(context,entry,text)
                if not self._current(context, entry):
                    return
                entry['phrase_count']+=1
                self.diagnostic('tts_phrase',sequence=entry['seq'],characters=len(text),boundary=reason)
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as error:
            if self._current(context, entry):
                await self.fail(type(error).__name__)

    async def finish(self, transcript=''):
        context = self.active
        if self.muted or context not in self.pending:
            return
        entry = self.pending[context]
        # Final-only native events must still speak. Append only an unsent suffix,
        # never replay an already streamed reply because punctuation changed.
        sent = entry['text'].lstrip()
        final = transcript.strip()
        if final and not sent.strip():
            await self.text(final)
        elif final.startswith(sent) and len(final) > len(sent):
            await self.text(final[len(sent):])
        if context not in self.pending:
            return
        entry['finished'] = True
        entry['last_send'] = time.monotonic()
        if self.active == context:
            self.active = None
        if not entry['provider_started'] and not entry['buffer'].strip():
            self.pending.pop(context, None)
            return
        try:
            if entry['buffer'].strip():
                await self._send_phrase(context,entry,entry['buffer'])
                entry['buffer'] = ''
            if not self._current(context, entry):
                return
            await self._provider_send({'context_id': context, 'flush': True})
            if not self._current(context, entry):
                return
            await self._provider_send({'context_id': context, 'close_context': True})
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as error:
            if self._current(context, entry):
                await self.fail(type(error).__name__)

    async def receive(self, socket):
        try:
            async for message in socket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    if message.type == aiohttp.WSMsgType.ERROR:
                        raise RuntimeError('TTS socket error')
                    continue
                await self.output(json.loads(message.data))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.diagnostic('tts_receive_failed', error_type=type(error).__name__)
        finally:
            if not self.closed and socket is self.socket:
                self.socket = None
                await socket.close()
                if self.pending:
                    await self.fail('tts_connection_lost')

    async def output(self, message):
        context = message.get('contextId', message.get('context_id'))
        if message.get('error'):
            if context is not None and context not in self.pending:
                return
            await self.fail('tts_provider_error')
            return
        entry = self.pending.get(context)
        if entry is None or entry['epoch'] != self.epoch or self.muted:
            return
        encoded = message.get('audio')
        if encoded:
            received = time.monotonic()
            received_at = time.time()
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) % 2:
                raise ValueError('Incomplete TTS PCM sample')
            if entry['first_audio'] is None:
                entry['first_audio'] = received
            previous_receive = entry['last_provider_audio']
            provider_gap_ms = None if previous_receive is None else round((received-previous_receive)*1000, 3)
            entry['last_provider_audio'] = received
            entry['provider_chunks'] += 1
            entry['last_send'] = received
            transport = {'provider_chunk':entry['provider_chunks'], 'provider_received_at':received_at,
                         'provider_gap_ms':provider_gap_ms}
            self.diagnostic('tts_provider_audio', sequence=entry['seq'], epoch=entry['epoch'],
                            turn_id=entry['turn_id'], bytes=len(raw), duration_ms=len(raw)/48, **transport)
            audio_offset=entry['bytes']/48000
            entry['bytes'] += len(raw)
            if entry['bytes'] > 48000*90:
                raise ValueError('TTS reply exceeded duration bound')
            aligned_message = self._without_delivery_tag_alignment(message, entry)
            if self.model_id == EXPRESSIVE_MODEL:
                aligned_message = self._shift_chunk_alignment(aligned_message, audio_offset)
            cues, alignment_source = mouth_cues(raw, aligned_message,audio_offset=audio_offset)
            captions,entry['caption_text'],caption_source=caption_cues(aligned_message,len(raw)/48000,audio_offset,entry['caption_text'])
            if captions and entry['text'].strip():
                repaired = []
                for cue in captions:
                    corrected, inferred = canonical_caption_progress(cue['text'], entry['text'])
                    repaired.append({**cue, 'text': corrected})
                    if inferred:
                        caption_source += '_text_fallback' if not caption_source.endswith('_text_fallback') else ''
                captions = repaired
            if caption_source=='unavailable':entry['caption_alignment_complete']=False
            elif not entry['caption_alignment_complete']:caption_source+='_partial'
            entry['chunks'].append((raw,cues,alignment_source,captions,caption_source,received,transport))
        if message.get('isFinal') or message.get('is_final'):
            entry['done'] = True
            if entry['text'].strip() and not entry['bytes']:
                code, message = await self.empty_reply_error()
                await self.fail(code, message)
                return
            first = entry['first_audio']
            metric = {'backend': 'streaming_tts', 'sequence': entry['seq'], 'epoch': entry['epoch'],
                'first_byte_s': None if first is None or entry['first_text'] is None else first-entry['first_text'],
                'output_s': entry['bytes']/48000, 'characters': len(entry['text']), 'phrases':entry['phrase_count']}
            self.metrics.append(metric)
            self.metrics = self.metrics[-200:]
            self.diagnostic('tts_complete', **metric)
            await self.emit({'type': 'latency', 'tts': metric})
        await self.drain()

    @staticmethod
    def _without_delivery_tag_alignment(message, entry=None):
        """Hide any provider-returned control-tag characters from captions/cues."""
        result = dict(message)
        for key in ('normalizedAlignment', 'normalized_alignment', 'alignment'):
            alignment = result.get(key)
            if not isinstance(alignment, dict):
                continue
            chars = alignment.get('chars')
            starts = alignment.get('charStartTimesMs', alignment.get('char_start_times_ms'))
            lengths_key = next((name for name in ('charDurationsMs','char_durations_ms','chars_durations_ms')
                                if isinstance(alignment.get(name), list)), None)
            lengths = alignment.get(lengths_key) if lengths_key else None
            if not all(isinstance(value, list) for value in (chars, starts, lengths)):
                continue
            if not (len(chars) == len(starts) == len(lengths)):
                continue
            joined = ''.join(char if isinstance(char, str) else '\0' for char in chars)
            remove = set()
            # The first control tag can straddle provider packets. Track its
            # prefix independently for each alignment representation.
            expected = entry.get('alignment_expected_tag') if entry else None
            if expected:
                states = entry.setdefault('alignment_tag_state', {})
                state = states.setdefault(key, {'index': 0, 'done': False})
                for index, char in enumerate(joined):
                    if state['done']:
                        break
                    offset = state['index']
                    if offset < len(expected) and char.lower() == expected[offset].lower():
                        remove.add(index)
                        state['index'] += 1
                    elif offset == len(expected) and char.isspace():
                        remove.add(index)
                    else:
                        state['done'] = True
            for match in DELIVERY_TAG_PATTERN.finditer(joined):
                remove.update(range(match.start(), match.end()))
            if remove:
                alignment_copy = dict(alignment)
                keep = [i for i in range(len(chars)) if i not in remove]
                alignment_copy['chars'] = [chars[i] for i in keep]
                if 'charStartTimesMs' in alignment_copy:
                    alignment_copy['charStartTimesMs'] = [starts[i] for i in keep]
                if 'char_start_times_ms' in alignment_copy:
                    alignment_copy['char_start_times_ms'] = [starts[i] for i in keep]
                alignment_copy[lengths_key] = [lengths[i] for i in keep]
                result[key] = alignment_copy
        return result

    @staticmethod
    def _shift_chunk_alignment(message, audio_offset):
        """Convert TTD packet-relative character times to the shared reply clock.

        Text-to-Dialogue returns normalized_alignment starts relative to each
        audio packet. The lipsync helpers consume reply-relative times, so add
        the preceding PCM duration before their existing audio_offset subtraction.
        """
        result = dict(message)
        shift_ms = audio_offset * 1000
        for key in ('normalizedAlignment', 'normalized_alignment', 'alignment'):
            alignment = result.get(key)
            if not isinstance(alignment, dict):
                continue
            alignment_copy = dict(alignment)
            shifted = False
            for name in ('charStartTimesMs', 'char_start_times_ms'):
                starts = alignment_copy.get(name)
                if isinstance(starts, list):
                    alignment_copy[name] = [value + shift_ms if isinstance(value, (int, float))
                                             and not isinstance(value, bool) else value for value in starts]
                    shifted = True
            if shifted:
                result[key] = alignment_copy
        return result

    async def empty_reply_error(self):
        """The WebSocket can end without an error when account billing blocks speech."""
        if self.client is not None and hasattr(self.client, 'get'):
            try:
                async with self.client.get('https://api.elevenlabs.io/v1/user/subscription',
                        headers={'xi-api-key': self.key}, timeout=aiohttp.ClientTimeout(total=3)) as response:
                    if response.status == 200:
                        payload = await response.json()
                        if payload.get('status') == 'past_due':
                            return ('tts_payment_issue',
                                'ElevenLabs has a failed or incomplete payment. Resolve it in ElevenLabs, then reconnect. I am still listening.')
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
                pass
        return 'tts_empty_reply', None

    async def drain(self):
        async with self.drain_lock:
            epoch = self.epoch
            while self.pending and epoch == self.epoch:
                context = next(iter(self.pending))
                entry = self.pending[context]
                if not entry['audible']:
                    break
                while entry['chunks'] and epoch == self.epoch:
                    raw,cues,alignment_source,captions,caption_source,received,transport = entry['chunks'].popleft()
                    # Batch only audio already received. Never wait for another
                    # provider chunk, including for the first audio packet.
                    for i in range(0, len(raw), PCM_PACKET_BYTES):
                        if epoch != self.epoch:
                            return
                        began = time.monotonic()
                        packet_transport = {**transport, 'provider_chunk_offset_ms':i/48,
                            'emit_started_at':time.time(), 'receive_to_emit_ms':round((began-received)*1000,3)}
                        await self.emit({'type': 'audio', 'epoch': epoch, 'seq': entry['seq'],
                            'turn_id': entry['turn_id'],
                            'sample_rate': 24000, 'pcm': base64.b64encode(raw[i:i+PCM_PACKET_BYTES]).decode(),
                            'mouth_cues': slice_cues(cues, i/48000, min(len(raw), i+PCM_PACKET_BYTES)/48000),
                            'caption_cues':slice_cues(captions,i/48000,min(len(raw),i+PCM_PACKET_BYTES)/48000),
                            'transport':packet_transport,
                            'caption_source':caption_source,
                            'alignment_source': alignment_source})
                        completed = time.monotonic()
                        previous_emit = entry['last_audio_emit']
                        entry['last_audio_emit'] = completed
                        self.diagnostic('tts_audio_emit', sequence=entry['seq'], epoch=epoch,
                            turn_id=entry['turn_id'], **packet_transport,
                            emit_completed_at=time.time(), emit_elapsed_ms=round((completed-began)*1000,3),
                            emit_gap_ms=None if previous_emit is None else round((began-previous_emit)*1000,3))
                if epoch != self.epoch or not entry['done']:
                    break
                self.pending.pop(context, None)
                # Completion belongs to the ordered playback stream. A final
                # provider message can arrive while an earlier reply or release
                # gate still prevents this context's PCM from being emitted.
                await self.emit({'type': 'caption_final', 'epoch': entry['epoch'],
                    'seq': entry['seq'], 'turn_id': entry['turn_id'],
                    'text': entry['text'].strip(),
                    'source': 'reply_text_audio_end_fallback'})

    async def release(self):
        if self.active in self.pending:
            self.pending[self.active]['audible'] = True
            await self.drain()

    async def interrupt(self):
        contexts = [context for context, entry in self.pending.items() if entry['provider_started']]
        self.epoch += 1
        self.pending.clear()
        self.active = None
        await self.emit({'type': 'audio_reset', 'epoch': self.epoch})
        if self.socket is not None and not self.socket.closed:
            for context in contexts:
                # A context may not have reached its first provider send yet.
                # Close contexts whose first provider send has started, including in-flight sends.
                # The pending map is already cleared, so retain no audio or text.
                try:
                    await self._provider_send({'context_id': context, 'close_context': True})
                except (aiohttp.ClientError, ConnectionError, RuntimeError):
                    break

    async def fail(self, code, message=None):
        self.diagnostic('tts_failure', code=code, epoch=self.epoch)
        self.muted = True
        await self.interrupt()
        await self.emit({'type': 'voice_warning', 'code': code,
            'message': message or 'That reply lost its speech connection. I am still listening; ask me to repeat it.'})

    def stalled_contexts(self, now=None):
        now = time.monotonic() if now is None else now
        return [context for context, entry in self.pending.items()
                if entry['provider_started'] and now-entry['last_send'] > 45]

    async def maintain_contexts(self, now=None):
        """Keep an open text-to-dialogue context alive while text is still arriving."""
        if self.model_id != EXPRESSIVE_MODEL or self.socket is None or self.socket.closed:
            return
        now = time.monotonic() if now is None else now
        for context, entry in list(self.pending.items()):
            if (not self._current(context, entry) or not entry['provider_initialized']
                    or entry['finished'] or now-max(entry['last_send'], entry['last_keepalive']) < 15):
                continue
            try:
                await self._provider_send({'context_id': context, 'keep_alive': True})
                entry['last_keepalive'] = now
            except (aiohttp.ClientError, ConnectionError, RuntimeError):
                if self._current(context, entry):
                    await self.fail('tts_connection_lost')
                return

    async def watch(self):
        while not self.closed:
            await asyncio.sleep(1)
            await self.maintain_contexts()
            # A context awaiting generated audio can sit idle well beyond ten
            # seconds, especially after a flush. Only a started provider context
            # with no incoming audio/text for this grace period is stalled.
            if self.stalled_contexts():
                await self.fail('tts_timeout')

    async def close(self):
        self.closed = True
        await self.interrupt()
        for task in (self.reader, self.watchdog):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self.reader,self.watchdog) if t), return_exceptions=True)
        if self.socket is not None:
            await self.socket.close()
        if self.client is not None:
            await self.client.close()
