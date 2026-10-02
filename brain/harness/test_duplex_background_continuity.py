"""Offline mailbox regressions at real Conversation/TrialConversation seams."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.server import Conversation
from duplex.live_studio import TrialConversation, ARCHITECTURES


class BackgroundContinuity(unittest.IsolatedAsyncioTestCase):
    def conversation(self, trial=False, guarded=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ws = SimpleNamespace(closed=False, send_json=AsyncMock())
        runtime = SimpleNamespace(run_dir=Path(tmp.name))
        app = {'runtime':runtime, 'run_dir':Path(tmp.name), 'speech_backend':'streaming-tts'}
        if trial:
            journal = SimpleNamespace(trace_id='offline',record=lambda *a:None)
            architecture = dict(ARCHITECTURES[0], affect_model=None, session_memory=False)
            c = TrialConversation(app, ws, architecture, journal)
        else:
            c = Conversation(app, ws)
        c.mask = SimpleNamespace(pending=False, epoch=4, diagnostic=lambda *a,**k:None,
                                 interrupt=AsyncMock(),close=AsyncMock())
        c.voice = SimpleNamespace(turn=None, context=AsyncMock(), interrupt=AsyncMock(),
                                 close=AsyncMock(),supports_background_result_guard=guarded)
        c.brain = SimpleNamespace(jobs={},view=lambda job:{'answer':'result'},close=AsyncMock())
        self.addAsyncCleanup(c.close)
        return c

    def job(self, c, **overrides):
        row = dict(job_id='job',status='complete',announced=False,revision=c.user_revision)
        row.update(overrides)
        c.brain.jobs['job'] = row
        return row

    async def tick(self, c):
        c.result_task = asyncio.create_task(c.deliver_results())
        await asyncio.sleep(.23)
        c.result_task.cancel()
        await asyncio.gather(c.result_task,return_exceptions=True)
        c.result_task = None

    async def test_completed_result_waits_for_foreground_through_starvation(self):
        for trial in (False,True):
            for gate in ('user_speaking','playback_busy','assistant_done','pending','turn'):
                with self.subTest(trial=trial,gate=gate):
                    c = self.conversation(trial)
                    job = self.job(c)
                    if gate == 'pending': c.mask.pending=True
                    elif gate == 'turn': c.voice.turn=object()
                    elif gate == 'assistant_done': c.assistant_done=False
                    else: setattr(c,gate,True)
                    await self.tick(c)
                    c.voice.context.assert_not_awaited()
                    c.mask.interrupt.assert_not_awaited()
                    self.assertFalse(job['announced'])
                    if gate == 'pending': c.mask.pending=False
                    elif gate == 'turn': c.voice.turn=None
                    elif gate == 'assistant_done': c.assistant_done=True
                    else: setattr(c,gate,False)
                    await self.tick(c)
                    c.voice.context.assert_awaited_once()
                    self.assertTrue(job['announced'])
                    c.mask.interrupt.assert_not_awaited()

    async def test_failed_result_delivered_without_fabricated_findings(self):
        c = self.conversation(guarded=True)
        self.job(c,status='failed')
        await self.tick(c)
        report = c.voice.context.await_args.args[0]
        self.assertIn('no usable result',report)
        self.assertIn('Do not invent',report)
        self.assertEqual(c.voice.context.await_args.kwargs,{'job_id':'job'})

    async def test_stale_and_cancelled_jobs_never_take_floor(self):
        for status,revision in (('complete',-1),('failed',-1),('cancelled',0)):
            c = self.conversation()
            self.job(c,status=status,revision=revision)
            await self.tick(c)
            c.voice.context.assert_not_awaited()

    async def test_timeout_is_bounded_and_does_not_reset_audio(self):
        c = self.conversation()
        job = self.job(c)
        c.voice.context.side_effect=TimeoutError('offline')
        for _ in range(5):
            job['retry_delivery_at']=0
            await self.tick(c)
        self.assertEqual(c.voice.context.await_count,3)
        self.assertFalse(job['announced'])
        c.mask.interrupt.assert_not_awaited()

    async def test_disconnect_cancels_pending_delivery_and_blocks_future_audio(self):
        c = self.conversation()
        self.job(c)
        c.playback_busy=True
        c.result_task=asyncio.create_task(c.deliver_results())
        await c.close()
        await c.emit({'type':'audio','pcm':'AA=='})
        c.ws.send_json.assert_not_awaited()
        c.voice.context.assert_not_awaited()
        self.assertTrue(c.result_task.done())
        self.assertFalse(c.alive)

    async def test_new_user_revision_invalidates_result_after_interrupt(self):
        c = self.conversation()
        job=self.job(c)
        c.assistant_turn='spoken'
        await c.realtime_event({'type':'turn.created','turn':{'role':'user','id':'repeat','transcript':'repeat that'}})
        await c.realtime_event({'type':'turn.done','turn':{'role':'user','id':'repeat','transcript':'repeat that'}})
        await self.tick(c)
        self.assertNotEqual(job['revision'],c.user_revision)
        c.voice.context.assert_not_awaited()
        c.mask.interrupt.assert_awaited_once()




    async def test_actual_flux_acknowledgment_preserves_pending_foreground_and_job(self):
        from benchmarks.naturalness.streaming_voice_20260930 import StreamingComparisonVoice
        c = self.conversation()
        c.assistant_done=False
        c.playback_busy=True
        job=self.job(c)
        voice=object.__new__(StreamingComparisonVoice)
        voice._provider_turn='ack'
        voice._provider_turn_serial=1
        voice.listener=SimpleNamespace(finish=AsyncMock())
        voice.debug=AsyncMock()
        voice.emit=c.emit
        voice._committed_provider_turns=set()
        voice._aggregate_provider_turns=set()
        voice._reset_asr_turn=lambda:None
        await voice._keep_floor('okay','flux','backchannel')
        self.assertEqual(c.user_revision,job['revision'])
        self.assertFalse(c.assistant_done)
        self.assertTrue(c.playback_busy)
        self.assertTrue(c.ws.send_json.await_args.args[0]['floor_preserved'])
        await self.tick(c)
        c.voice.context.assert_not_awaited()
        c.mask.interrupt.assert_not_awaited()




    async def test_result_replaced_during_context_await_is_not_announced(self):
        for replacement in ('new_user', 'cancel', 'disconnect'):
            with self.subTest(replacement=replacement):
                c = self.conversation(guarded=True)
                job = self.job(c)
                async def replace(*args, **kwargs):
                    if replacement == 'new_user': c.user_revision += 1
                    elif replacement == 'cancel': job['status'] = 'cancelled'
                    else: c.ws.closed = True
                c.voice.context.side_effect = replace
                await self.tick(c)
                self.assertFalse(job['announced'])
                self.assertFalse(any(call.args[0].get('type') == 'brain_delivery'
                                     and call.args[0].get('phase') == 'queued'
                                     for call in c.ws.send_json.await_args_list))

    async def test_native_without_revision_guard_keeps_results_available_without_auto_speech(self):
        from duplex.native import NativeVoice
        c = self.conversation()
        job = self.job(c)
        c.voice = object.__new__(NativeVoice)
        c.voice.closed = False
        c.voice.turn = None
        c.voice.context = AsyncMock()
        c.voice.close = AsyncMock()
        await self.tick(c)
        await self.tick(c)
        c.voice.context.assert_not_awaited()
        self.assertFalse(job['announced'])
        self.assertEqual(job['status'], 'complete')
        deferred = [call.args[0] for call in c.ws.send_json.await_args_list
                    if call.args[0].get('type') == 'brain_delivery']
        self.assertEqual(len(deferred), 1)
        self.assertEqual(deferred[0]['phase'], 'deferred')
        self.assertEqual(deferred[0]['reason'], 'unsupported_result_guard')

if __name__ == '__main__': unittest.main()
