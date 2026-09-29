"""Cached, short spoken listening acknowledgements.

These clips are synthesized ahead of time and are only available to the live
path after :meth:`VoiceCache.warm` has completed successfully. They never make
a provider request from ``event_for``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
from pathlib import Path
import struct

import aiohttp

from duplex.lipsync import mouth_cues


SAMPLE_RATE = 24000
MODEL = 'eleven_flash_v2_5'
DEFAULT_VOICE_ID = '24AMj4dc02cYAwoUnqzN'
MAX_SECONDS = 1.5
MAX_RESPONSE_SECONDS = 10.0
MARGIN_SECONDS = 0.015
TEXTS = {'mhm': 'Mm-hmm.', 'uh_huh': 'Uh-huh.'}
VOICE_SETTINGS = {'stability': 0.65, 'similarity_boost': 0.8, 'use_speaker_boost': False}
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / 'results' / 'listener-cues'


def _trim_silence(raw: bytes) -> bytes:
    """Trim PCM16 mono edge silence while retaining a 15 ms natural margin."""
    if not isinstance(raw, bytes) or not raw or len(raw) % 2:
        raise ValueError('invalid PCM')
    samples = [sample[0] for sample in struct.iter_unpack('<h', raw)]
    if not samples:
        raise ValueError('empty PCM')
    window = max(1, round(SAMPLE_RATE * 0.01))
    rms = []
    for start in range(0, len(samples), window):
        values = samples[start:start + window]
        rms.append(math.sqrt(sum(value * value for value in values) / len(values)))
    peak = max(rms, default=0)
    if peak < 45:
        raise ValueError('silent PCM')
    threshold = max(35.0, peak * 0.025)
    active = [index for index, value in enumerate(rms) if value >= threshold]
    if not active:
        raise ValueError('silent PCM')
    margin = round(SAMPLE_RATE * MARGIN_SECONDS)
    first = max(0, active[0] * window - margin)
    last = min(len(samples), (active[-1] + 1) * window + margin)
    trimmed = raw[first * 2:last * 2]
    if not trimmed or len(trimmed) % 2:
        raise ValueError('invalid trimmed PCM')
    if len(trimmed) / (SAMPLE_RATE * 2) > MAX_SECONDS:
        raise ValueError('clip too long')
    return trimmed


class VoiceCache:
    """Warm at startup; the synchronous lookup path only serves ready clips."""

    def __init__(self, key: str, cache_dir: str | Path = DEFAULT_CACHE_DIR,
                 voice_id: str = DEFAULT_VOICE_ID, *, session=None, max_jobs: int = 2):
        self.key = str(key or '').strip()
        self.voice_id = str(voice_id or '').strip()
        self.cache_dir = Path(cache_dir)
        self.session = session
        self._owns_session = False
        self._semaphore = asyncio.Semaphore(max(1, min(2, int(max_jobs))))
        self._warm_lock = asyncio.Lock()
        self._clips: dict[str, bytes] = {}
        self._warmed = False

    def _identity(self, cue: str) -> tuple[str, str]:
        text = TEXTS[cue]
        voice_hash = hashlib.sha256(self.voice_id.encode('utf-8')).hexdigest()
        cache_id = hashlib.sha256(
            f'{MODEL}\0{voice_hash}\0{SAMPLE_RATE}\0{text}'.encode('utf-8')
        ).hexdigest()[:24]
        return cache_id, voice_hash

    async def warm(self) -> bool:
        """Load valid cached clips and synthesize only cache misses.

        At most two provider jobs can run at once. A provider/cache failure is
        isolated to its cue and never escapes into the recognition path.
        """
        async with self._warm_lock:
            if self._warmed:
                return bool(self._clips)
            self._warmed = True
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return False
            try:
                await asyncio.gather(*(self._warm_one(cue) for cue in TEXTS))
            finally:
                if self._owns_session:
                    await self.close()
            return bool(self._clips)

    async def _warm_one(self, cue: str) -> None:
        cache_id, voice_hash = self._identity(cue)
        audio_path = self.cache_dir / f'{cue}-{cache_id}.pcm'
        metadata_path = self.cache_dir / f'{cue}-{cache_id}.json'
        raw = self._load_cache(audio_path, metadata_path, voice_hash, TEXTS[cue])
        if raw is None:
            if not self.key or not self.voice_id:
                return
            async with self._semaphore:
                try:
                    raw = await self._synthesize(TEXTS[cue])
                except Exception:
                    # Fail closed: listener cues are optional and must never
                    # turn provider or local cache trouble into a turn delay.
                    return
            try:
                self._save_cache(audio_path, metadata_path, raw, voice_hash, TEXTS[cue])
            except OSError:
                # Playback can use a freshly generated clip even if disk cache
                # is unavailable; the clip remains absent next process start.
                pass
        self._clips[cue] = raw

    def _load_cache(self, audio_path: Path, metadata_path: Path,
                    voice_hash: str, text: str) -> bytes | None:
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            if (not isinstance(metadata, dict) or metadata.get('model') != MODEL
                    or metadata.get('voice_hash') != voice_hash
                    or metadata.get('sample_rate') != SAMPLE_RATE
                    or metadata.get('text') != text
                    or metadata.get('voice_settings') != VOICE_SETTINGS):
                return None
            raw = audio_path.read_bytes()
            if not raw or len(raw) % 2 or len(raw) / (SAMPLE_RATE * 2) > MAX_SECONDS:
                return None
            duration = len(raw) / (SAMPLE_RATE * 2)
            if not math.isclose(float(metadata.get('duration_s', -1)), duration, abs_tol=0.0001):
                return None
            # Reject silent/corrupt files and validate mouth cue inputs.
            if max((abs(value[0]) for value in struct.iter_unpack('<h', raw)), default=0) < 45:
                return None
            mouth_cues(raw, {}, SAMPLE_RATE)
            return raw
        except (OSError, ValueError, TypeError, json.JSONDecodeError, struct.error):
            return None

    def _save_cache(self, audio_path: Path, metadata_path: Path,
                    raw: bytes, voice_hash: str, text: str) -> None:
        duration = len(raw) / (SAMPLE_RATE * 2)
        # Metadata intentionally contains no credential and no provider body.
        metadata = {'model': MODEL, 'voice_hash': voice_hash,
                    'sample_rate': SAMPLE_RATE, 'duration_s': round(duration, 6), 'text': text,
                    'voice_settings': VOICE_SETTINGS}
        audio_path.write_bytes(raw)
        metadata_path.write_text(json.dumps(metadata, separators=(',', ':')), encoding='utf-8')

    async def _synthesize(self, text: str) -> bytes:
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8, connect=2, sock_read=6))
            self._owns_session = True
        url = (f'https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream'
               '?output_format=pcm_24000')
        async with self.session.post(
                url, headers={'xi-api-key': self.key, 'Content-Type': 'application/json'},
                json={'text': text, 'model_id': MODEL,
                      'voice_settings': VOICE_SETTINGS},
                timeout=aiohttp.ClientTimeout(total=8, connect=2, sock_read=6)) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f'provider HTTP {response.status}')
            raw = await response.read()
        if not raw or len(raw) % 2 or len(raw) > SAMPLE_RATE * 2 * MAX_RESPONSE_SECONDS:
            raise ValueError('invalid provider PCM')
        return _trim_silence(raw)

    def event_for(self, cue: str) -> dict | None:
        """Return a ready browser audio event, or ``None`` without waiting."""
        if cue not in TEXTS:
            return None
        raw = self._clips.get(cue)
        if raw is None:
            cache_id, voice_hash = self._identity(cue)
            raw = self._load_cache(self.cache_dir / f'{cue}-{cache_id}.pcm',
                                   self.cache_dir / f'{cue}-{cache_id}.json',
                                   voice_hash, TEXTS[cue])
            if raw is None:
                return None
            self._clips[cue] = raw
        cues, _source = mouth_cues(raw, {}, SAMPLE_RATE)
        # There is no provider alignment for these cached PCM-only clips. Keep
        # timing acoustic, while using a restrained articulation hint for the
        # closed-mouth hum and small acknowledgement vowel.
        for item in cues:
            if item['viseme'] != 'rest':
                item['viseme'] = 'MBP' if cue == 'mhm' else 'EH'
                item['amount'] = min(item['amount'], 0.35)
        return {'type': 'audio', 'sample_rate': SAMPLE_RATE,
                'pcm': base64.b64encode(raw).decode('ascii'),
                'duration_s': round(len(raw) / (SAMPLE_RATE * 2), 6),
                'text': TEXTS[cue], 'mouth_cues': cues}

    async def close(self) -> None:
        if self._owns_session and self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None if self._owns_session else self.session
        self._owns_session = False
