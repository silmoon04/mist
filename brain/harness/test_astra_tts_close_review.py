"""Independent flush/interrupt/replacement interleaving on the production TTS path."""
import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.tts import EXPRESSIVE_MODEL, StreamingTTS


class FlushGateSocket:
    closed = False

    def __init__(self):
        self.sent = []
        self.flush_started = asyncio.Event()
        self.release_flush = asyncio.Event()

    async def send_json(self, event):
        self.sent.append(dict(event))
        if set(event) == {'context_id', 'flush'} and event['flush']:
            self.flush_started.set()
            await self.release_flush.wait()


class CloseInterleaving(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_wins_during_flush_and_retired_finish_cannot_close_new_reply(self):
        events = []

        async def emit(event):
            events.append(event)

        tts = StreamingTTS('offline-fixture', emit, model_id=EXPRESSIVE_MODEL)
        tts.socket = FlushGateSocket()
        await tts.begin('retired')
        retired = tts.active
        await tts.text('The first reply has enough words to start its own speech context.')
        finishing = asyncio.create_task(tts.finish())
        await asyncio.wait_for(tts.socket.flush_started.wait(), 1)
        try:
            await asyncio.wait_for(tts.interrupt(), 1)
            await tts.begin('replacement')
            replacement = tts.active
            await tts.text('The replacement reply owns the new speech epoch and remains open.')
        finally:
            tts.socket.release_flush.set()
            await finishing
        closes = [event['context_id'] for event in tts.socket.sent if event.get('close_context')]
        self.assertEqual(closes, [retired])
        self.assertNotEqual(replacement, retired)
        self.assertEqual(tts.active, replacement)
        self.assertEqual(list(tts.pending), [replacement])
        self.assertEqual(tts.pending[replacement]['epoch'], 1)
        await tts.output({'context_id': retired, 'error': 'retired context'})
        await tts.output({'context_id': retired, 'isFinal': True, 'audio': 'AAA='})
        self.assertEqual(list(tts.pending), [replacement])
        self.assertEqual([event['epoch'] for event in events if event['type'] == 'audio_reset'], [1])
        self.assertFalse(any(event['type'] in ('voice_warning', 'caption_final', 'audio') for event in events))


if __name__ == '__main__':
    unittest.main()
