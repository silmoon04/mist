"""Live user captions and an independent, bounded listening-cue channel."""
from __future__ import annotations

import asyncio
from array import array
import math
import re
import sys
import time
import uuid

from duplex.listener_backchannels import ListenerBackchannelPolicy


class ListenerFeedback:
    def __init__(self, emit, lookup, busy, epoch, *, clock=time.monotonic, policy=None):
        self.emit, self.lookup, self.busy, self.epoch = emit, lookup, busy, epoch
        self.clock = clock
        self.policy = policy or ListenerBackchannelPolicy()
        self.enabled = True
        self.quiet = False
        self.practice = False
        self.floor_quiet = False
        self.floor_id = None
        self.text = ''
        self.revision = 0
        self.finalized = True
        self.last_voiced = -math.inf
        self.last_text = -math.inf
        self.pending = None
        self.pending_id = None
        self.pending_started = False
        self.last_sent = -math.inf
        self.sent_floor = None
        self.last_decision_reason = None
        self.echo_until = -math.inf
        self.timer = None
        self.closed = False

    async def begin(self):
        await self.cancel('new_user_floor' if self.finalized else 'speech_resumed')
        if self.finalized:
            self.floor_id = 'heard-' + uuid.uuid4().hex
            self.text = ''
            self.revision = 0
            self.finalized = False
            self.floor_quiet = False
            self.policy.evaluate(self.clock(), floor_id=self.floor_id, transcript='',
                                 speech_active=True, pause_ms=0)
            # Keep the last caption until recognition produces actual words.

    async def publish(self, final):
        await self.emit({'type': 'user_transcript', 'utterance_id': self.floor_id,
                         'revision': self.revision, 'text': self.text, 'final': final})

    def note_pcm(self, pcm):
        # Energy can veto a cue; it never commits a turn or interrupts the model.
        if not pcm or len(pcm) % 2:
            return
        samples = array('h', pcm)
        if sys.byteorder != 'little':
            samples.byteswap()
        values = samples[::8]
        if values and math.sqrt(sum(value * value for value in values) / len(values)) / 32768 > .012:
            self.last_voiced = self.clock()

    async def partial(self, text):
        if self.closed:
            return
        if self.finalized:
            await self.begin()
        text = text[-4000:]
        if text == self.text:
            return
        await self.cancel('transcript_updated')
        self.text = text
        self.revision += 1
        self.last_text = self.clock()
        lowered = text.casefold()
        if re.search(r"\b(?:don't|do not|stop)\s+(?:interrupting|interrupt|acknowledging|backchanneling|talking|talk|speaking|speak)\b|\b(?:stay|keep|be) quiet\b", lowered):
            self.quiet = True
        if re.search(r'\b(?:you can|please|start|resume)\s+(?:acknowledg\w*|backchannel\w*)\b', lowered):
            self.quiet = False
        if re.search(r'\blet me (?:finish|talk|speak)\b', lowered):
            self.floor_quiet = True
        if re.search(r'\b(?:speaking|speech|presentation)\s+(?:practice|exercise|practise)|\b(?:practise|practice)\s+(?:speaking|my speech)', lowered):
            self.practice = True
        if re.search(r'\b(?:stop|end|finish|finished|done with)\s+(?:the )?(?:practice|exercise)|\bback to (?:normal|chatting)\b', lowered):
            self.practice = False
        await self.publish(False)
        # Record the floor's age even while speech is still active.
        self.evaluate()
        self.timer = asyncio.create_task(self.poll())

    def evaluate(self):
        now = self.clock()
        pause_ms = max(0, (now - max(self.last_voiced, self.last_text)) * 1000)
        return self.policy.evaluate(now, floor_id=self.floor_id, transcript=self.text,
            speech_active=now - self.last_voiced < .32, pause_ms=pause_ms,
            finalized=self.finalized, quiet_requested=self.quiet or not self.enabled,
            speaking_practice=self.practice or self.floor_quiet)

    async def poll(self):
        try:
            for _ in range(25):
                await asyncio.sleep(.06)
                if self.closed or self.finalized:
                    return
                if await self.try_cue():
                    return
        except asyncio.CancelledError:
            return

    async def try_cue(self):
        if (not self.enabled or self.closed or self.finalized or self.busy() or self.pending
                or self.sent_floor == self.floor_id or self.clock() - self.last_sent < 18):
            return False
        decision = self.evaluate()
        if decision.reason != self.last_decision_reason:
            self.last_decision_reason = decision.reason
            await self.emit({'type': 'listener_feedback', 'phase': 'decision',
                             'eligible': decision.emit, 'reason': decision.reason,
                             'utterance_id': self.floor_id})
        if not decision.emit:
            return False
        clip = self.lookup(decision.cue)
        if not clip:
            self.policy.cancel(decision)
            await self.emit({'type': 'listener_feedback', 'phase': 'skipped', 'reason': 'voice_clip_unavailable'})
            return True
        self.pending, self.pending_id = decision, uuid.uuid4().hex
        self.pending_started = False
        self.last_sent, self.sent_floor = self.clock(), self.floor_id
        duration_ms = round(clip.get('duration_s', 1) * 1000)
        self.echo_until = self.clock() + duration_ms / 1000 + .4
        await self.emit({**clip, 'type': 'listener_cue', 'duration_ms': duration_ms, 'cue_id': self.pending_id,
                         'utterance_id': self.floor_id, 'transcript_revision': self.revision,
                         'epoch': self.epoch(), 'valid_for_ms': 350,
                         'reason': decision.reason, 'trigger': decision.trigger})
        return True

    async def receipt(self, packet):
        if packet.get('cue_id') != self.pending_id or not self.pending:
            return
        phase = packet.get('phase')
        if phase == 'started':
            self.pending_started = True
            self.policy.playback_started(self.pending, self.clock())
        elif phase in ('ended', 'cancelled', 'skipped'):
            self.policy.cancel(self.pending)
            self.pending = self.pending_id = None
            self.pending_started = False
        await self.emit({'type': 'listener_feedback', 'phase': phase, 'cue_id': packet.get('cue_id'),
                         'utterance_id': self.floor_id, 'source': 'browser_playback_receipt'})

    async def cancel(self, reason):
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        self.timer = None
        if self.pending:
            if self.pending_started and reason in ('speech_resumed', 'transcript_updated', 'user_turn_complete'):
                return
            self.policy.cancel(self.pending)
            await self.emit({'type': 'listener_cue_cancel', 'cue_id': self.pending_id, 'reason': reason})
            self.pending = self.pending_id = None
            self.pending_started = False

    def possible_cue_echo(self, text):
        words = re.sub(r'[^a-z ]', '', text.casefold().replace('-', ' ')).strip()
        return self.clock() < self.echo_until and words in ('mm hmm', 'mm hm', 'mhm', 'hmm', 'uh huh')

    async def finish(self, text):
        if self.finalized:
            await self.begin()
        await self.cancel('user_turn_complete')
        self.text = text[-4000:]
        self.revision += 1
        self.finalized = True
        await self.publish(True)

    async def configure(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Listening acknowledgements must be enabled or disabled')
        self.enabled = enabled
        if enabled:
            self.quiet = self.practice = self.floor_quiet = False
        if not enabled:
            await self.cancel('disabled')
        await self.emit({'type': 'listener_feedback', 'phase': 'configuration', 'enabled': enabled})

    async def close(self):
        self.closed = True
        await self.cancel('disconnected')
