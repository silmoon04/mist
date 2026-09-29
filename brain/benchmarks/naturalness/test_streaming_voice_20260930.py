"""Offline provider event and floor-policy contract tests; no paid calls."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from streaming_voice_20260930 import StreamingComparisonVoice, explicit_stop
from duplex.runtime import RobotRuntime


class Sink:
    def __init__(self):
        self.events = []
        self.playback_busy = False
        self.mask = SimpleNamespace(pending={})

    async def emit(self, event):
        self.events.append(event)

    async def dc_event(self, event):
        self.events.append(event)


class Client:
    def close(self):
        pass


class Listener:
    async def begin(self):
        pass

    async def partial(self, text):
        pass

    async def finish(self, text):
        pass

    async def cancel(self, reason):
        pass

    async def close(self):
        pass

    def possible_cue_echo(self, text):
        return False


class FakePolicy:
    def __init__(self, decision='take_turn', delay=0):
        self.decision = decision
        self.delay = delay
        self.snapshots = []

    def update_current(self, snapshot):
        self.snapshots.append(snapshot)

    def submit(self, snapshot):
        async def classify():
            await asyncio.sleep(self.delay)
            return SimpleNamespace(decision=self.decision, applicable=True, source='jev', latency_ms=self.delay*1000,
                                   usage={}, raw={}, hold_until=asyncio.get_running_loop().time()+.03)
        return asyncio.create_task(classify())

    async def aclose(self):
        return True


class StreamingVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.sink = Sink()
        self.voice = StreamingComparisonVoice(RobotRuntime(Path(self.folder.name)), self.folder.name,
                                              self.sink.emit, None, self.sink.dc_event,
                                              asr_provider='nova', provider='cerebras',
                                              model='qwen-3.8-27b', reasoning_effort='low')
        self.voice.client = Client()
        self.voice.loop = asyncio.get_running_loop()
        self.voice.listener = Listener()
        self.voice.hold_s = .025

    async def asyncTearDown(self):
        await self.voice.close()

    async def wait_for(self, predicate):
        for _ in range(300):
            if predicate():
                return
            await asyncio.sleep(.005)
        self.fail('event did not arrive')

    def commits(self):
        return [e['turn']['transcript'] for e in self.sink.events
                if e.get('type') == 'turn.done' and e['turn']['role'] == 'user']

    async def event(self, kind, text='', turn='a', segment_id=None, cumulative=False):
        await self.voice.asr_event({'type': kind, 'turn_id': turn, 'segment': text,
                                    'segment_id': segment_id, 'cumulative': cumulative})

    async def replay_flux(self, entries):
        """Replay normalized events with the Flux adapter's raw TurnInfo shape."""
        wire_names = {'speech_start': 'StartOfTurn', 'partial': 'Update',
                      'final': 'EndOfTurn', 'turn_end': 'EndOfTurn'}
        start = asyncio.get_running_loop().time()
        for at_ms, kind, turn, transcript in entries:
            await asyncio.sleep(max(0, start + at_ms / 1000 - asyncio.get_running_loop().time()))
            raw = {'type': 'TurnInfo', 'event': wire_names[kind],
                   'turn_index': int(turn), 'transcript': transcript,
                   'end_of_turn_confidence': .87 if kind == 'turn_end' else None}
            await self.voice.asr_event({'type': kind, 'turn_id': turn,
                                        'segment_id': int(turn), 'segment': transcript,
                                        'text': transcript, 'cumulative': True,
                                        'metadata': {'source': 'flux_model',
                                                     'turn_index': int(turn),
                                                     'end_of_turn_confidence': raw['end_of_turn_confidence']},
                                        'raw': raw})

    async def test_split_finals_preserved_and_duplicate_end_commits_once(self):
        await self.event('final', 'I wanted to', segment_id='1')
        await self.event('turn_end', turn='a')
        await self.event('turn_resumed', turn='a')
        await self.event('final', 'read that again.', segment_id='2')
        await self.event('turn_end', turn='a')
        await self.wait_for(lambda: len(self.commits()) == 1)
        await self.event('turn_end', turn='a')
        await asyncio.sleep(.04)
        self.assertEqual(self.commits(), ['I wanted to read that again.'])

    async def test_flux_cumulative_replaces_prior_version(self):
        self.voice.asr_provider = 'flux'
        await self.event('final', 'Could you', segment_id='turn', cumulative=True)
        await self.event('final', 'Could you read it slower?', segment_id='turn', cumulative=True)
        await self.event('turn_end', turn='a')
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), ['Could you read it slower?'])

    async def test_flux_new_provider_turn_preserves_held_previous_turn(self):
        self.voice.asr_provider = 'flux'
        await self.event('final', 'I wanted to', turn='1', cumulative=True)
        await self.event('turn_end', turn='1')
        await self.event('speech_start', turn='2')
        await self.event('final', 'read that again.', turn='2', cumulative=True)
        await self.event('turn_end', turn='2')
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), ['I wanted to read that again.'])

    async def test_live_flux_complete_question_can_take_a_trailing_instruction(self):
        self.voice.asr_provider = 'flux'
        self.voice.live_mode = True
        self.voice.hold_s = 0
        question = 'What is the prototype called now, and do we know its battery life?'
        await self.replay_flux([
            (0, 'speech_start', '1', 'What is the prototype called'),
            (5, 'partial', '1', question),
            (10, 'final', '1', question),
            (11, 'turn_end', '1', question),
            (250, 'speech_start', '2', 'Please keep'),
            (270, 'final', '2', 'Please keep it brief.'),
            (271, 'turn_end', '2', 'Please keep it brief.'),
        ])
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), [question + ' Please keep it brief.'])

    async def test_live_flux_true_end_commits_with_bounded_delay(self):
        self.voice.asr_provider = 'flux'
        self.voice.live_mode = True
        self.voice.hold_s = 0
        started = asyncio.get_running_loop().time()
        await self.replay_flux([(0, 'speech_start', '1', 'What time'),
                                (5, 'final', '1', 'What time is it?'),
                                (6, 'turn_end', '1', 'What time is it?')])
        self.assertEqual(self.commits(), [])
        await self.wait_for(lambda: bool(self.commits()))
        elapsed = asyncio.get_running_loop().time() - started
        self.assertGreaterEqual(elapsed, .30)
        self.assertLess(elapsed, .7)

    async def test_flux_final_revision_wins_without_contextual_name_guessing(self):
        self.voice.asr_provider = 'flux'
        self.voice.live_mode = True
        self.voice.hold_s = 0
        self.voice.complete_hold_s = .01
        await self.replay_flux([(0, 'speech_start', '1', 'Was it Claude?'),
                                (5, 'partial', '1', 'Was it cloud?'),
                                (10, 'final', '1', 'Was it cloud?'),
                                (11, 'turn_end', '1', 'Was it cloud?')])
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), ['Was it cloud?'])

    async def test_live_flux_displays_start_and_corrected_cumulative_partial(self):
        self.voice.asr_provider = 'flux'
        self.voice.live_mode = True
        self.voice.hold_s = 0
        self.sink.playback_busy = True
        await self.event('speech_start', 'Can you read', turn='1')
        self.assertEqual(self.voice._display(), 'Can you read')
        self.assertEqual(self.voice.revision, 0)
        await self.event('final', 'Can you read', turn='1', cumulative=True)
        await self.event('partial', 'Can you repeat', turn='1', cumulative=True)
        self.assertEqual(self.voice._display(), 'Can you repeat')
        await self.event('final', 'Can you repeat that?', turn='1', cumulative=True)
        await self.event('turn_end', 'Can you repeat that?', turn='1')
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), ['Can you repeat that?'])
        self.assertEqual(self.voice.revision, 1)
        self.assertIsNone(self.voice._candidate_task)
        await self.event('speech_start', 'And slower', turn='2')
        self.assertEqual(self.commits(), ['Can you repeat that?'])

    async def test_live_flux_acknowledgement_and_stop_guard(self):
        self.voice.asr_provider = 'flux'
        self.voice.live_mode = True
        self.voice.hold_s = 0
        self.sink.playback_busy = True
        await self.replay_flux([(0, 'speech_start', '1', 'Yeah')])
        self.assertEqual(self.voice.revision, 0)
        await self.replay_flux([(0, 'final', '1', 'Yeah'), (1, 'turn_end', '1', 'Yeah')])
        self.assertEqual(self.commits(), [])
        await self.replay_flux([(0, 'speech_start', '2', 'Stop talking now')])
        self.assertEqual(self.voice.revision, 1)
        self.assertTrue(any(e.get('phase') == 'explicit_stop_yield' for e in self.sink.events))

    async def test_live_negated_and_quoted_stop_do_not_preempt(self):
        self.voice.asr_provider = 'flux'
        self.voice.live_mode = True
        self.sink.playback_busy = True
        await self.event('speech_start', 'Do not stop, I am listening', turn='1')
        await self.event('partial', 'She said "stop talking" yesterday', turn='1')
        self.assertEqual(self.voice.revision, 0)
        await self.event('partial', 'Stop', turn='1')
        self.assertEqual(self.voice.revision, 1)

    async def test_negated_stop_endpoint_keeps_playback_floor(self):
        self.voice.asr_provider = 'flux'
        self.voice.live_mode = True
        self.voice.hold_s = 0
        self.sink.playback_busy = True
        await self.event('speech_start', turn='1')
        await self.event('final', 'Do not stop, I am listening', turn='1', cumulative=True)
        await self.event('turn_end', turn='1')
        self.assertEqual(self.commits(), [])
        self.assertEqual(self.voice.revision, 0)
        self.assertTrue(any(e.get('phase') == 'floor_preserved' and
                            e.get('reason') == 'local_continue' for e in self.sink.events))

    async def test_live_incomplete_endpoint_waits_for_continuation_at_zero_legacy_hold(self):
        self.voice.asr_provider = 'flux'
        self.voice.live_mode = True
        self.voice.hold_s = 0
        self.voice.incomplete_min_hold_s = .06
        await self.event('final', 'I mean, like...', turn='1', cumulative=True)
        await self.event('turn_end', turn='1')
        self.assertEqual(self.commits(), [])
        await asyncio.sleep(.01)
        await self.event('speech_start', turn='2')
        await self.event('final', 'read it again.', turn='2', cumulative=True)
        await self.event('turn_end', turn='2')
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), ['I mean, like... read it again.'])
        await asyncio.sleep(.07)
        self.assertEqual(len(self.commits()), 1)

    async def test_live_incomplete_hold_has_total_deadline(self):
        self.voice.asr_provider = 'flux'
        self.voice.live_mode = True
        self.voice.hold_s = 0
        self.voice.incomplete_min_hold_s = .06
        self.voice.incomplete_max_hold_s = .075
        await self.event('final', 'So', turn='1', cumulative=True)
        await self.event('turn_end', turn='1')
        await asyncio.sleep(.025)
        await self.event('turn_resumed', turn='1')
        await self.event('turn_end', turn='1')
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), ['So'])

    async def test_ack_during_playback_keeps_floor(self):
        self.sink.playback_busy = True
        revision = self.voice.revision
        await self.event('speech_start')
        await self.event('final', 'Yeah', segment_id='1')
        await self.event('turn_end')
        await self.wait_for(lambda: any(e.get('floor_preserved') for e in self.sink.events))
        self.assertEqual(self.voice.revision, revision)
        self.assertEqual(self.commits(), [])

    async def test_explicit_stop_yields_before_endpoint_but_quoted_stop_does_not(self):
        self.sink.playback_busy = True
        await self.event('speech_start')
        await self.event('partial', 'She said "stop talking" yesterday')
        await self.event('partial', "Don't stop, keep explaining")
        self.assertEqual(self.voice.revision, 0)
        await self.event('partial', 'Stop talking now')
        self.assertEqual(self.voice.revision, 1)

    async def test_jev_wait_continuation_invalidates_old_endpoint(self):
        self.voice.jev_enabled = True
        self.voice.jev_policy = FakePolicy('wait')
        await self.event('final', 'I wanted to', segment_id='1')
        await self.event('turn_end')
        await asyncio.sleep(.01)
        self.assertEqual(self.commits(), [])
        await self.event('turn_resumed')
        await self.event('final', 'read it again.', segment_id='2')
        self.voice.jev_policy.decision = 'take_turn'
        await self.event('turn_end')
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), ['I wanted to read it again.'])

    async def test_late_jev_advice_cannot_commit_after_resumption(self):
        self.voice.jev_enabled = True
        self.voice.jev_policy = FakePolicy('take_turn', delay=.04)
        await self.event('final', 'Wait', segment_id='1')
        await self.event('turn_end')
        await self.event('turn_resumed')
        await asyncio.sleep(.06)
        self.assertEqual(self.commits(), [])

    async def test_jev_advice_rechecks_live_playback_state(self):
        self.voice.jev_enabled = True
        self.voice.jev_policy = FakePolicy('keep_speaking', delay=.02)
        self.sink.playback_busy = True
        await self.event('final', 'Please read that again.', segment_id='1')
        await self.event('turn_end')
        self.sink.playback_busy = False
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), ['Please read that again.'])
        self.assertTrue(any(e.get('phase') == 'jev_local_state_changed' for e in self.sink.events))

    async def test_shadow_events_are_diagnostic_only(self):
        await self.voice.asr_event({'type': 'final', 'turn_id': 'shadow-turn',
                                    'segment': 'Wrong shadow transcript', 'shadow': True,
                                    'metadata': {'audio_start': 1.2}, 'words': [{'text': 'Wrong'}]})
        self.assertIsNone(self.voice._provider_turn)
        self.assertEqual(self.voice._text(), '')
        diagnostic = [e for e in self.sink.events if e.get('phase') == 'asr_event'][-1]
        self.assertEqual(diagnostic['metadata'], {'audio_start': 1.2})
        self.assertEqual(diagnostic['words'], [{'text': 'Wrong'}])
        self.assertTrue(diagnostic['shadow'])

    async def test_late_scribe_alignment_does_not_rewind_new_turn(self):
        self.voice.asr_provider = 'scribe'
        await self.event('speech_start', turn='1')
        await self.event('partial', 'Read this', turn='1')
        await self.voice.asr_event({'type': 'alignment', 'turn_id': '0',
                                    'segment_id': '0', 'words': [{'text': 'earlier'}],
                                    'metadata': {'timestamps_delayed': True}})
        self.assertEqual(self.voice._provider_turn, '1')
        self.assertEqual(self.voice._interim, 'Read this')
        self.assertIsNone(self.voice._candidate_task)
        await self.event('final', 'Read this slowly.', turn='1', segment_id='1')
        await self.event('turn_end', turn='1')
        await self.wait_for(lambda: bool(self.commits()))
        self.assertEqual(self.commits(), ['Read this slowly.'])

    async def test_audio_sender_delegates_pacing_to_common_pcm_clock(self):
        self.voice.asr_provider = 'flux'
        self.voice.asr_adapter = SimpleNamespace(send_pcm16k=AsyncMock(), close=AsyncMock())
        with patch('streaming_voice_20260930.send_paced_pcm', new_callable=AsyncMock) as pace:
            await self.voice.send_audio()
        pace.assert_awaited_once()
        self.assertIs(pace.call_args.args[0], self.voice.track)


class StopCommandTests(unittest.TestCase):
    def test_only_direct_commands(self):
        self.assertTrue(explicit_stop('Stop talking now.'))
        self.assertTrue(explicit_stop('MIST, please pause.'))
        self.assertFalse(explicit_stop('She said "stop talking" yesterday.'))
        self.assertFalse(explicit_stop("Don't stop, keep explaining."))


if __name__ == '__main__':
    unittest.main()
