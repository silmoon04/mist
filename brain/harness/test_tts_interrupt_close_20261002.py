"""Cancellation must not close an already closing TTS context twice."""
import asyncio
from collections import Counter
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.tts import EXPRESSIVE_MODEL, FLASH_MODEL, StreamingTTS


class ProviderSocket:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.close_counts = Counter()
        self.errors = []
        self.close_started = asyncio.Event()
        self.close_gate = None

    async def send_json(self, event):
        self.sent.append(event)
        if event.get('close_context'):
            context = event['context_id']
            self.close_counts[context] += 1
            self.close_started.set()
            if self.close_counts[context] > 1:
                # A global provider rejection cannot be correlated with a retired
                # context, matching the live expressive interruption failure.
                self.errors.append({'error': 'context already closed'})
            elif self.close_gate is not None:
                await self.close_gate.wait()

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class InterruptCloseTests(unittest.IsolatedAsyncioTestCase):
    async def make_tts(self, model=EXPRESSIVE_MODEL):
        events = []

        async def emit(event):
            events.append(event)

        tts = StreamingTTS('fixture-key', emit, model_id=model)
        tts.socket = ProviderSocket()
        await tts.begin('reply')
        context = tts.active
        await tts.text('A screw holds the bracket firmly.')
        return tts, context, events

    async def test_interrupt_finished_reply_waiting_for_audio_never_duplicates_close(self):
        for model in (EXPRESSIVE_MODEL, FLASH_MODEL):
            with self.subTest(model=model):
                tts, context, events = await self.make_tts(model)
                await tts.finish()
                self.assertIn(context, tts.pending)
                socket = tts.socket
                await tts.interrupt()
                for error in socket.errors:
                    await tts.output(error)
                self.assertEqual(socket.close_counts[context], 1)
                self.assertEqual([e['epoch'] for e in events if e['type']=='audio_reset'], [1])
                self.assertFalse(any(e['type']=='voice_warning' for e in events))

    async def test_interrupt_during_close_send_never_duplicates_inflight_close(self):
        tts, context, events = await self.make_tts()
        socket = tts.socket
        socket.close_gate = asyncio.Event()
        finishing = asyncio.create_task(tts.finish())
        await asyncio.wait_for(socket.close_started.wait(), 1)
        try:
            await asyncio.wait_for(tts.interrupt(), 1)
            self.assertEqual(socket.close_counts[context], 1)
            self.assertEqual([e['epoch'] for e in events if e['type']=='audio_reset'], [1])
        finally:
            socket.close_gate.set()
            await finishing

    async def test_open_context_is_closed_once_when_interrupted(self):
        tts, context, events = await self.make_tts()
        await tts.interrupt()
        self.assertEqual(tts.socket.close_counts[context], 1)
        self.assertEqual(tts.pending, {})
        self.assertFalse(any(e['type']=='voice_warning' for e in events))

    async def test_genuine_global_provider_error_still_warns_and_resets(self):
        tts, _, events = await self.make_tts()
        await tts.output({'error':'real provider failure'})
        self.assertTrue(tts.muted)
        self.assertEqual(tts.epoch, 1)
        self.assertEqual([e['code'] for e in events if e['type']=='voice_warning'], ['tts_provider_error'])

    async def test_genuine_socket_disconnect_still_warns_and_resets(self):
        tts, _, events = await self.make_tts()
        await tts.receive(tts.socket)
        self.assertTrue(tts.muted)
        self.assertEqual(tts.epoch, 1)
        self.assertEqual([e['code'] for e in events if e['type']=='voice_warning'], ['tts_connection_lost'])


if __name__=='__main__':
    unittest.main()
