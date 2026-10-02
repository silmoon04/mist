"""Ordered native-audio conversion with recoverable transport failures."""
import asyncio
import base64
from collections import deque
import json
import logging
import time
import traceback

import aiohttp
import numpy as np

VOICE = '24AMj4dc02cYAwoUnqzN'
LOG = logging.getLogger('mist.voice')
TRANSIENT = (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, asyncio.TimeoutError)


class ProviderError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'Voice conversion returned HTTP {status}')


class Segmenter:
    def __init__(self, seconds=1.8):
        self.limit = int(seconds * 32000) // 2 * 2
        self.reset()

    def reset(self):
        self.buffer = bytearray()
        self.pre = deque(maxlen=4)
        self.silence = 0
        self.active = False

    def feed(self, pcm):
        rms = float(np.sqrt(np.mean(np.frombuffer(pcm, dtype='<i2').astype(float) ** 2))) if pcm else 0
        voiced = rms > 90
        if not self.active:
            self.pre.append(pcm)
            if not voiced:
                return []
            self.active = True
            self.buffer.extend(b''.join(self.pre))
            self.pre.clear()
        else:
            self.buffer.extend(pcm)
        self.silence = 0 if voiced else self.silence + len(pcm)
        result = []
        # Use a short pause as a boundary once there is enough phonetic context.
        # The same cap applies to the first and subsequent passages: a tiny first
        # passage would finish playing before the next upload is ready.
        pause = self.silence >= 2560 and len(self.buffer) >= 25600
        if pause or self.silence >= 5760:
            if self.buffer:
                result.append(bytes(self.buffer))
            self.reset()
        else:
            while len(self.buffer) >= self.limit:
                result.append(bytes(self.buffer[:self.limit]))
                del self.buffer[:self.limit]
        return result


class VoiceMask:
    def __init__(self, key, emit, segment_seconds=1.8):
        self.key, self.emit = key, emit
        self.segmenter = Segmenter(segment_seconds)
        self.epoch = self.sequence = self.next = 0
        self.pending = {}
        self.tasks = set()
        self.metrics = []
        self.client = None
        self.draining = asyncio.Lock()
        self.connections = asyncio.Semaphore(3)
        self.muted = False
        self.closed = False

    async def start(self):
        connector = aiohttp.TCPConnector(limit=4, limit_per_host=4, ttl_dns_cache=300)
        self.client = aiohttp.ClientSession(connector=connector,
            timeout=aiohttp.ClientTimeout(total=2.4, connect=1.2, sock_read=1.8))

    def diagnostic(self, event, **values):
        LOG.info(json.dumps({'event': event, 'at': time.time(), **values}, allow_nan=False))

    async def warm(self):
        """Open a provider connection while the user begins speaking."""
        try:
            async with self.client.get(f'https://api.elevenlabs.io/v1/voices/{VOICE}',
                    headers={'xi-api-key': self.key}) as response:
                await response.read()
                self.diagnostic('provider_warm', status=response.status)
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            self.diagnostic('provider_warm_failed', error_type=type(error).__name__)

    async def interrupt(self):
        self.epoch += 1
        self.segmenter.reset()
        self.pending.clear()
        self.next = self.sequence
        for task in list(self.tasks):
            if task is not asyncio.current_task():
                task.cancel()
        await self.emit({'type': 'audio_reset', 'epoch': self.epoch})

    async def abandon_reply(self, code, message, fatal=False):
        await self.interrupt()
        self.muted = True
        await self.emit({'type': 'error' if fatal else 'voice_warning', 'fatal': fatal,
                         'code': code, 'message': message})

    async def feed(self, pcm):
        if self.muted or self.closed:
            return
        for chunk in self.segmenter.feed(pcm):
            if len(self.tasks) >= 6:
                await self.abandon_reply('conversion_backlog',
                    'That reply fell behind and was cleared. I am still listening; ask me to repeat it.')
                return
            seq, epoch = self.sequence, self.epoch
            self.sequence += 1
            task = asyncio.create_task(self.convert(chunk, seq, epoch))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    def form(self, pcm):
        form = aiohttp.FormData()
        form.add_field('audio', pcm, filename='performance.pcm', content_type='application/octet-stream')
        form.add_field('model_id', 'eleven_multilingual_sts_v2')
        form.add_field('file_format', 'pcm_s16le_16')
        form.add_field('voice_settings', json.dumps({'stability': .45,
            'similarity_boost': .8, 'style': 0, 'use_speaker_boost': False}))
        return form

    async def convert(self, pcm, seq, epoch):
        began = time.perf_counter()
        if epoch != self.epoch or self.closed:
            return
        entry = {'chunks': deque(), 'done': False, 'epoch': epoch, 'sent_bytes': 0}
        self.pending[seq] = entry
        first = None
        size = 0
        attempt = 0
        try:
            async with asyncio.timeout(3.6):
                async with self.connections:
                    for attempt in range(1, 4):
                        if epoch != self.epoch:
                            return
                        size = 0
                        first = None
                        try:
                            async with self.client.post(
                                    f'https://api.elevenlabs.io/v1/speech-to-speech/{VOICE}/stream?output_format=pcm_24000',
                                    headers={'xi-api-key': self.key}, data=self.form(pcm)) as response:
                                if response.status != 200:
                                    raise ProviderError(response.status)
                                remainder = b''
                                async for block in response.content.iter_chunked(2400):
                                    if epoch != self.epoch:
                                        return
                                    if first is None:
                                        first = time.perf_counter() - began
                                    block = remainder + block
                                    remainder = block[len(block) // 2 * 2:]
                                    block = block[:len(block) // 2 * 2]
                                    size += len(block)
                                    if size > len(pcm) * 5 + 48000:
                                        raise RuntimeError('Converted audio exceeded its duration bound')
                                    if block:
                                        entry['chunks'].append(block)
                                        await self.drain()
                                if remainder or not size:
                                    raise RuntimeError('Voice converter returned incomplete PCM audio')
                            break
                        except (*TRANSIENT, ProviderError) as error:
                            transient = not isinstance(error, ProviderError) or error.status in (500, 502, 503, 504)
                            remaining = 3.6 - (time.perf_counter() - began)
                            retry = transient and attempt < 3 and entry['sent_bytes'] == 0 and remaining > .6
                            self.diagnostic('conversion_attempt_failed', error_type=type(error).__name__,
                                status=getattr(error, 'status', None),
                                errno=getattr(getattr(error, 'os_error', None), 'errno', None),
                                winerror=getattr(getattr(error, 'os_error', None), 'winerror', None),
                                sequence=seq, epoch=epoch, attempt=attempt, retry=retry,
                                elapsed_ms=round((time.perf_counter() - began) * 1000), sent_bytes=entry['sent_bytes'])
                            if not retry:
                                raise
                            entry['chunks'].clear()
                            await asyncio.sleep(.06 * attempt)
            if epoch != self.epoch:
                return
            entry['done'] = True
            await self.drain()
            metric = {'input_s': len(pcm) / 32000, 'first_byte_s': first,
                'total_s': time.perf_counter() - began, 'output_s': size / 48000,
                'epoch': epoch, 'sequence': seq, 'attempts': attempt}
            self.metrics.append(metric)
            self.metrics = self.metrics[-200:]
            self.diagnostic('conversion_complete', **metric)
            await self.emit({'type': 'latency', 'conversion': metric})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if epoch != self.epoch or self.closed:
                return
            self.diagnostic('conversion_failure', error_type=type(error).__name__,
                sequence=seq, epoch=epoch, attempts=attempt, sent_bytes=entry['sent_bytes'],
                elapsed_ms=round((time.perf_counter() - began) * 1000),
                frames=[{'function': f.name, 'line': f.lineno, 'file': f.filename.replace('\\', '/').rsplit('/', 1)[-1]}
                    for f in traceback.extract_tb(error.__traceback__)[-5:]])
            if isinstance(error, ProviderError) and error.status in (401, 402, 403, 404):
                await self.abandon_reply(f'voice_http_{error.status}',
                    f'ElevenLabs returned HTTP {error.status}. Check voice access or account credits.', fatal=True)
            else:
                await self.abandon_reply(type(error).__name__,
                    'That reply lost its voice connection. I am still listening; ask me to repeat it.')

    async def drain(self):
        async with self.draining:
            epoch = self.epoch
            while self.next in self.pending and epoch == self.epoch:
                seq = self.next
                entry = self.pending[seq]
                if entry['epoch'] != epoch:
                    self.pending.pop(seq, None)
                    self.next += 1
                    continue
                while entry['chunks'] and epoch == self.epoch:
                    block = entry['chunks'].popleft()
                    entry['sent_bytes'] = entry.get('sent_bytes', 0) + len(block)
                    await self.emit({'type': 'audio', 'epoch': epoch, 'seq': seq,
                        'sample_rate': 24000, 'pcm': base64.b64encode(block).decode()})
                if epoch != self.epoch or not entry['done']:
                    break
                self.pending.pop(seq, None)
                self.next = seq + 1
                if epoch == self.epoch:
                    await self.emit({'type':'audio_complete','epoch':epoch,'seq':seq,
                                     'source':'voice_conversion'})

    async def close(self):
        self.closed = True
        await self.interrupt()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if self.client:
            await self.client.close()
