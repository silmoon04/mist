"""Offline protocol and delivery-commit tests for Eleven v4 Turbo streaming."""
import asyncio
import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.tts import EXPRESSIVE_MODEL, FLASH_MODEL, StreamingTTS


class Socket:
    closed = False

    def __init__(self):
        self.sent = []
        self.gate = None
        self.started = asyncio.Event()

    async def send_json(self, event):
        self.sent.append(event)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()

    async def close(self):
        self.closed = True


class ExpressiveProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []

        async def emit(event):
            self.events.append(event)

        self.emit = emit
        self.socket = Socket()
        self.tts = StreamingTTS('fixture-key', emit, model_id=EXPRESSIVE_MODEL)
        self.tts.socket = self.socket

    async def test_context_is_lazy_and_early_delivery_is_committed_once(self):
        t = self.tts
        await t.begin('a', delivery='neutral')
        context = t.active
        self.assertEqual(self.socket.sent, [])
        self.assertTrue(t.set_delivery('warm'))
        await t.text('Hello there.')
        self.assertEqual(self.socket.sent[0], {'context_id': context, 'voices': ['24AMj4dc02cYAwoUnqzN']})
        first = self.socket.sent[1]
        self.assertEqual(first['inputs'][0]['text'], '[warmly] Hello there. ')
        self.assertEqual(first['inputs'][0]['voice_id'], '24AMj4dc02cYAwoUnqzN')
        self.assertTrue(first['flush'])
        self.assertFalse(t.set_delivery('bright'))
        await t.text('The light is blue.')
        self.assertEqual(self.socket.sent[2]['inputs'][0]['text'], 'The light is blue. ')
        styles = [event for event in self.events if event.get('type') == 'speech_style']
        self.assertEqual(len(styles), 1)
        self.assertEqual((styles[0]['phase'], styles[0]['delivery'], styles[0]['model']),
                         ('committed', 'warm', EXPRESSIVE_MODEL))
        self.assertEqual(styles[0]['epoch'], 0)
        self.assertNotIn('[warmly]', str(styles[0]))
        await t.finish()
        self.assertEqual(self.socket.sent[-1], {'context_id': context, 'close_context': True})

    async def test_delivery_cannot_change_after_provider_initialization_started(self):
        gate = asyncio.Event()
        self.socket.gate = gate
        await self.tts.begin('race')
        self.assertTrue(self.tts.set_delivery('gentle'))
        task = asyncio.create_task(self.tts.text('Take your time.'))
        await self.socket.started.wait()
        self.assertFalse(self.tts.set_delivery('bright'))
        gate.set()
        await task
        self.assertEqual(self.socket.sent[1]['inputs'][0]['text'], '[gently] Take your time. ')

    async def test_following_context_gets_its_own_tag_and_audio_order_stays_serial(self):
        t = self.tts
        await t.begin('one', delivery='bright')
        first = t.active
        await t.text('That is delightful.')
        await t.finish()
        await t.begin('two', delivery='serious')
        second = t.active
        await t.text('We should be careful.')
        await t.finish()
        self.assertNotEqual(first, second)
        self.assertIn('[excited]', self.socket.sent[1]['inputs'][0]['text'])
        second_init = next(i for i, msg in enumerate(self.socket.sent) if msg.get('context_id') == second)
        self.assertEqual(self.socket.sent[second_init]['voices'], ['24AMj4dc02cYAwoUnqzN'])
        self.assertIn('[seriously]', self.socket.sent[second_init + 1]['inputs'][0]['text'])

        await t.output({'context_id': second, 'audio': base64.b64encode(b'\x01\x00' * 100).decode(),
                        'is_final': True})
        self.assertFalse(any(event.get('type') == 'audio' for event in self.events))
        await t.output({'context_id': first, 'audio': base64.b64encode(b'\x02\x00' * 100).decode(),
                        'is_final': True})
        packets = [event for event in self.events if event.get('type') == 'audio']
        self.assertEqual([base64.b64decode(event['pcm']) for event in packets],
                         [b'\x02\x00' * 100, b'\x01\x00' * 100])

    async def test_provider_error_is_visible_and_interrupt_closes_initialized_context(self):
        await self.tts.begin('a')
        await self.tts.text('A short sentence.')
        context = self.tts.active
        await self.tts.output({'context_id': context, 'error': {'message': 'provider rejected context'}})
        self.assertTrue(self.tts.muted)
        self.assertTrue(any(event.get('type') == 'voice_warning' for event in self.events))
        self.assertEqual(self.socket.sent[-1], {'context_id': context, 'close_context': True})

    async def test_interrupted_lazy_context_does_not_send_unregistered_close(self):
        await self.tts.begin('a')
        context = self.tts.active
        await self.tts.interrupt()
        self.assertEqual(self.socket.sent, [])
        await self.tts.output({'context_id': context, 'audio': base64.b64encode(b'\x01\x00' * 100).decode()})
        self.assertFalse(any(event.get('type') == 'audio' for event in self.events))

    async def test_control_tags_are_removed_from_provider_alignment_and_captions(self):
        t = self.tts
        await t.begin('a', delivery='warm')
        context = t.active
        await t.text('Hello.')
        chars = list('[warmly] Hello. ')
        starts = [index * 30 for index in range(len(chars))]
        lengths = [30] * len(chars)
        pcm = base64.b64encode(b'\x20\x00' * 24_000).decode()
        await t.output({'context_id': context, 'audio': pcm,
                        'alignment': {'chars': chars, 'char_start_times_ms': starts,
                                      'char_durations_ms': lengths}})
        emitted = [event for event in self.events if event.get('type') == 'audio']
        captions = [cue['text'] for event in emitted for cue in event.get('caption_cues', [])]
        self.assertTrue(captions)
        self.assertTrue(all('[warmly]' not in caption for caption in captions))
        self.assertTrue(any('Hello.' in caption for caption in captions))
        self.assertEqual(emitted[0]['alignment_source'], 'elevenlabs_characters_audio_gated')

    async def test_v4_packet_relative_alignment_is_shifted_to_reply_clock(self):
        t = self.tts
        await t.begin('packet-clock')
        context = t.active
        await t.output({'context_id': context, 'audio': base64.b64encode(b'\x58\x1b' * 2400).decode(),
                        'normalized_alignment': {'chars': ['m'], 'char_start_times_ms': [0],
                                                 'char_durations_ms': [100]}})
        await t.output({'context_id': context, 'audio': base64.b64encode(b'\x58\x1b' * 2400).decode(),
                        'normalized_alignment': {'chars': ['f'], 'char_start_times_ms': [0],
                                                 'char_durations_ms': [100]}})
        emitted = [event for event in self.events if event.get('type') == 'audio']
        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[0]['alignment_source'], 'elevenlabs_characters_audio_gated')
        self.assertEqual(emitted[1]['alignment_source'], 'elevenlabs_characters_audio_gated')
        self.assertEqual(emitted[1]['mouth_cues'][0]['viseme'], 'FV')
        self.assertEqual(emitted[1]['caption_cues'][0], {'time': 0, 'text': 'mf'})

    async def test_empty_turn_finishes_without_opening_a_provider_context(self):
        await self.tts.begin('empty')
        await self.tts.finish()
        self.assertEqual(self.socket.sent, [])
        self.assertFalse(self.tts.pending)


class FlashCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_during_initialization_never_submits_late_speech(self):
        for model in (FLASH_MODEL, EXPRESSIVE_MODEL):
            with self.subTest(model=model):
                events=[];entered=asyncio.Event();release=asyncio.Event()
                class PausedSocket(Socket):
                    async def send_json(self,event):
                        self.sent.append(event)
                        if 'voices' in event or 'voice_settings' in event:
                            entered.set();await release.wait()
                async def emit(event):events.append(event)
                socket=PausedSocket();tts=StreamingTTS('fixture',emit,model_id=model);tts.socket=socket
                await tts.begin('cancelled',delivery='warm')
                writing=asyncio.create_task(tts.text('Hello there.'))
                await entered.wait()
                await tts.interrupt()
                release.set();await writing
                close_index=next(i for i,event in enumerate(socket.sent) if event.get('close_context'))
                self.assertEqual(socket.sent[close_index+1:],[])
                self.assertFalse(any(event.get('type')=='speech_style' for event in events))
                self.assertFalse(tts.pending)

    async def test_flash_still_uses_tts_multi_context_and_stability_without_tags(self):
        events = []

        async def emit(event):
            events.append(event)

        socket = Socket()
        tts = StreamingTTS('fixture-key', emit, model_id=FLASH_MODEL)
        tts.socket = socket
        await tts.begin('flash', delivery='gentle')
        self.assertEqual(socket.sent, [])
        self.assertTrue(tts.set_delivery('bright'))
        await tts.text('Hello there.')
        init = socket.sent[0]
        self.assertEqual(init['voice_settings']['stability'], .3)
        self.assertIn('text', socket.sent[1])
        self.assertNotIn('inputs', socket.sent[1])
        self.assertNotIn('[excited]', str(socket.sent))
        self.assertEqual(init['generation_config']['chunk_length_schedule'], [120, 160, 250, 290])

    async def test_unknown_model_is_rejected(self):
        async def emit(event):
            pass

        with self.assertRaises(ValueError):
            StreamingTTS('fixture-key', emit, model_id='eleven_v4')


if __name__ == '__main__':
    unittest.main(verbosity=2)
