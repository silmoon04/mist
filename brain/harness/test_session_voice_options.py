"""Offline studio session settings, public diagnostics, and typed-input contracts."""
import asyncio
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(BRAIN / 'harness'))
sys.path.insert(0, str(BRAIN / 'benchmarks' / 'naturalness'))
from cascade_voice import CascadeVoice, RevisionBridge, StaleTurn
from duplex.native import NativeVoice
from duplex.background import BackgroundBrain
from duplex.runtime import RobotRuntime


class SessionVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runtime = RobotRuntime(self.directory.name)
        self.events = []
        self.protocol = []

    async def emit(self, event):
        self.events.append(event)

    async def dc_event(self, event):
        self.protocol.append(event)

    def cascade(self, **options):
        return CascadeVoice(self.runtime, self.directory.name, self.emit, None,
                            self.dc_event, **options)

    def native(self, **options):
        return NativeVoice(self.runtime, self.directory.name, self.emit, None,
                           self.dc_event, **options)

    async def test_overrides_are_session_local_and_environment_is_unchanged(self):
        environment = {'MIST_LAB_PROVIDER': 'cerebras', 'MIST_LAB_MODEL': 'gpt-oss-120b',
                       'MIST_LAB_FLOOR': 'cancel_all', 'MIST_LAB_ENDPOINT_MS': '300',
                       'MIST_LAB_INCOMPLETE_HOLD_MS': '900', 'MIST_BRAIN_MODEL': 'gpt-6-luna',
                       'MIST_BACKGROUND_MODEL': 'gpt-6-luna', 'MIST_LAB_REASONING': 'low'}
        with patch.dict(os.environ, environment):
            original = dict(os.environ)
            a = self.cascade(provider='codex', model='gpt-6-sol', floor='selective',
                             endpoint_ms=700, incomplete_hold_ms=1200, reasoning_effort='high')
            b = self.cascade()
            native = self.native(model='gpt-6-sol')
            background = BackgroundBrain(self.runtime, self.directory.name, self.emit, lambda: 0,
                                         model='gpt-6-astra')
            self.assertEqual((a.provider, a.model, a.floor, a.endpoint_ms, a.hold_s),
                             ('codex', 'gpt-6-sol', 'selective', 700, 1.2))
            self.assertEqual((b.provider, b.model, b.floor, b.endpoint_ms, b.hold_s),
                             ('cerebras', 'gpt-oss-120b', 'cancel_all', 300, .9))
            self.assertEqual(native.model, 'gpt-6-sol')
            self.assertEqual(background.model, 'gpt-6-astra')
            self.assertEqual(dict(os.environ), original)
            self.assertEqual(a.reasoning_effort, 'high')
            self.assertEqual(b.reasoning_effort, 'low')

    async def test_reasoning_controls_are_validated_by_provider_and_model(self):
        qwen = self.cascade(provider='cerebras', model='qwen-3.8-27b', reasoning_effort='none')
        self.assertEqual(qwen.reasoning_controls, {'reasoning_effort':'none', 'reasoning_format':'parsed', 'clear_thinking':True})
        self.assertEqual(qwen.max_output_tokens, 1024)
        for options in [dict(provider='cerebras',model='gpt-oss-120b',reasoning_effort='none'),
                        dict(provider='cerebras',model='qwen-3.8-27b',reasoning_effort='xhigh'),
                        dict(provider='codex',model='gpt-6-luna',reasoning_effort='none')]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.cascade(**options)
        await qwen.debug('offline_settings_check')
        self.assertEqual(self.events[-1]['reasoning_effort'], 'none')
        self.assertEqual(self.events[-1]['reasoning_controls']['reasoning_format'], 'parsed')
        self.assertEqual(self.events[-1]['output_limit_kind'], 'completion_cap_including_reasoning')

    async def test_factory_uses_captured_effort_not_later_environment(self):
        qwen = self.cascade(provider='cerebras',model='qwen-3.8-27b',reasoning_effort='none',max_output_tokens=4096)
        qwen.env['CEREBRAS_API_KEY'] = 'offline-placeholder'
        client = SimpleNamespace(new_session=Mock(), close=Mock())
        with patch.dict(os.environ, {'MIST_LAB_REASONING':'high'}), \
                patch('cerebras_client.CerebrasClient', return_value=client) as factory, \
                patch('httpx.Client'):
            self.assertIs(qwen.make_client(), client)
            self.assertEqual(factory.call_args.kwargs['thinking'], 'none')
            self.assertEqual(factory.call_args.kwargs['model'], 'qwen-3.8-27b')
            self.assertEqual(factory.call_args.kwargs['max_output_tokens'], 4096)
        codex = self.cascade(provider='codex',model='gpt-6-luna',reasoning_effort='medium')
        with patch('codex_client.CodexClient',return_value=client) as factory, patch('duplex.native.choose_binary'):
            self.assertIs(codex.make_client(),client)
            self.assertEqual(factory.call_args.kwargs['thinking'], 'medium')
            self.assertEqual(factory.call_args.kwargs['max_output_tokens'], 400)

    async def test_cerebras_connection_retry_is_opt_in_at_voice_factory(self):
        client=SimpleNamespace(new_session=Mock(),close=Mock())
        for retries in (0,1):
            voice=self.cascade(provider='cerebras',model='qwen-3.8-27b',connect_retries=retries)
            voice.env['CEREBRAS_API_KEY']='offline-placeholder'
            with patch('cerebras_client.CerebrasClient',return_value=client) as factory, \
                    patch('httpx.Client') as http, patch('httpx.HTTPTransport') as transport:
                voice.make_client()
                self.assertEqual(factory.call_args.kwargs['connect_retries'],retries)
                if retries:
                    transport.assert_called_once_with(retries=1)
                    self.assertIs(http.call_args.kwargs['transport'],transport.return_value)
                else:
                    transport.assert_not_called()
                    self.assertIsNone(http.call_args.kwargs['transport'])
        for invalid in (True,False,-1,2,'1'):
            with self.assertRaisesRegex(ValueError,'connect_retries'):
                self.cascade(connect_retries=invalid)

    async def test_completion_cap_is_explicit_bounded_and_preserves_legacy_defaults(self):
        self.assertEqual(self.cascade().max_output_tokens, 1024)
        for model in ('gpt-oss-120b', 'qwen-3.8-27b'):
            for cap in (128, 4096):
                with self.subTest(model=model, cap=cap):
                    voice = self.cascade(provider='cerebras', model=model, max_output_tokens=cap)
                    self.assertEqual(voice.max_output_tokens, cap)
                    await voice.debug('offline_cap_check')
                    self.assertEqual(self.events[-1]['max_output_tokens'], cap)
            for cap in (True, False, '4096', 1024.0, 127, 4097, 0, -1):
                with self.subTest(model=model, invalid_cap=cap), self.assertRaisesRegex(ValueError, 'completion cap'):
                    self.cascade(provider='cerebras', model=model, max_output_tokens=cap)
        self.assertEqual(self.cascade(provider='codex',model='gpt-6-luna',max_output_tokens=400).max_output_tokens,400)
        with self.assertRaisesRegex(ValueError,'remains 400'):
            self.cascade(provider='codex',model='gpt-6-luna',max_output_tokens=4096)

    async def test_conversation_policy_is_session_local_and_reaches_model(self):
        from duplex.conversation_policy import ADAPTIVE_PROMPT
        client=SimpleNamespace(new_session=Mock(),close=Mock())
        for policy in ('standard','responsive'):
            voice=self.cascade(provider='cerebras',model='qwen-3.8-27b',conversation_policy=policy)
            voice.env['CEREBRAS_API_KEY']='offline-placeholder'
            with patch('cerebras_client.CerebrasClient',return_value=client) as factory,patch('httpx.Client'):
                voice.make_client()
                self.assertEqual(ADAPTIVE_PROMPT in factory.call_args.kwargs['system_prompt'],policy=='responsive')
            await voice.debug('offline_policy_check')
            self.assertEqual(self.events[-1]['conversation_policy'],policy)
        with self.assertRaises(ValueError):self.cascade(conversation_policy='invented')

    async def test_invalid_explicit_options_fail_before_connection(self):
        invalid = [{'provider': ''}, {'provider': 'unknown'}, {'floor': 'unknown'},
                   {'model': ''}, {'model': False}, {'model': 'bad model'},
                   {'endpoint_ms': True}, {'endpoint_ms': 700.5}, {'endpoint_ms': 99},
                   {'endpoint_ms': 2001}, {'incomplete_hold_ms': True},
                   {'incomplete_hold_ms': float('nan')}, {'incomplete_hold_ms': float('inf')},
                   {'incomplete_hold_ms': -1}, {'incomplete_hold_ms': 3001}]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises((ValueError, TypeError)):
                self.cascade(**options)
        for model in ('', False, 'bad model'):
            with self.subTest(model=model), self.assertRaises(ValueError):
                self.native(model=model)
            with self.subTest(background_model=model), self.assertRaises(ValueError):
                BackgroundBrain(self.runtime, self.directory.name, self.emit, lambda: 0, model=model)

    async def test_native_start_uses_captured_session_model(self):
        voice = self.native(model='gpt-6-sol')
        with patch.dict(os.environ, {'MIST_BRAIN_MODEL': 'gpt-6-luna'}), \
                patch('duplex.native.choose_binary'), \
                patch('duplex.native.CodexClient', side_effect=RuntimeError('offline constructor boundary')) as factory:
            with self.assertRaisesRegex(RuntimeError, 'offline constructor'):
                await voice.start()
            self.assertEqual(factory.call_args.kwargs['model'], 'gpt-6-sol')

    async def test_cancelled_cascade_start_waits_for_worker_and_retires_client(self):
        entered, release = threading.Event(), threading.Event()
        client = SimpleNamespace(close=Mock())
        def construct():
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Offline startup gate timed out')
            return client
        voice = self.cascade()
        voice.env['DEEPGRAM_API_KEY'] = 'offline-placeholder'
        with patch.object(voice, 'make_client', side_effect=construct), \
                patch('cascade_voice.aiohttp.ClientSession', side_effect=AssertionError('Cancelled startup must not connect')):
            start = asyncio.create_task(voice.start())
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            start.cancel()
            close = asyncio.create_task(voice.close())
            try:
                await asyncio.sleep(.025)
                self.assertFalse(start.done())
                self.assertFalse(close.done())
                self.assertFalse(voice._startup_worker.cancelled())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(start, 1)
            await asyncio.wait_for(close, 1)
        client.close.assert_called_once()
        self.assertTrue(voice._close_complete)

    async def test_cancelled_native_start_joins_constructor_and_session_setup(self):
        for phase in ('constructor', 'new_session'):
            with self.subTest(phase=phase):
                entered, release = threading.Event(), threading.Event()
                def gate():
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('Offline startup gate timed out')
                client = SimpleNamespace(close=Mock(), new_session=Mock(side_effect=gate if phase == 'new_session' else None),
                                         _rpc=SimpleNamespace(request=Mock()), thread_id='offline')
                def construct(**options):
                    if phase == 'constructor':
                        gate()
                    return client
                voice = self.native()
                with patch('duplex.native.choose_binary'), patch('duplex.native.CodexClient', side_effect=construct), \
                        patch('duplex.native.RTCPeerConnection', side_effect=AssertionError('Cancelled startup must not connect')):
                    start = asyncio.create_task(voice.start())
                    self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                    start.cancel()
                    close = asyncio.create_task(voice.close())
                    try:
                        await asyncio.sleep(.025)
                        self.assertFalse(start.done())
                        self.assertFalse(close.done())
                    finally:
                        release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(start, 1)
                    await asyncio.wait_for(close, 1)
                client.close.assert_called_once()
                self.assertTrue(voice._close_complete)

    async def test_cascade_submission_queues_real_user_events_without_asking_model(self):
        voice = self.cascade()
        voice.client = SimpleNamespace(ask=Mock(side_effect=AssertionError('Submission must not wait for generation')))
        response = await asyncio.wait_for(voice.submit_text('Read this sentence.'), timeout=.2)
        self.assertEqual(response['status'], 'queued')
        self.assertEqual([event['type'] for event in self.protocol],
                         ['turn.created', 'input_transcript.added', 'turn.done'])
        self.assertEqual(self.protocol[-1]['turn']['transcript'], 'Read this sentence.')
        queued = voice.requests.get_nowait()
        self.assertEqual(queued[1], 'Read this sentence.')
        voice.client.ask.assert_not_called()
        self.assertTrue(voice.supports_text_input)

    async def test_cascade_closed_or_unstarted_submission_is_rejected(self):
        voice = self.cascade()
        with self.assertRaisesRegex(RuntimeError, 'not ready'):
            await voice.submit_text('Hi')
        self.assertEqual(self.protocol, [])

    async def test_cascade_model_diagnostics_do_not_expose_reasoning(self):
        voice = self.cascade()
        await voice.model_event({'type': 'message_update', 'assistantMessageEvent': {
            'type': 'thinking_delta', 'delta': 'PRIVATE_REASONING'}}, 0, 'turn')
        self.assertEqual(self.events, [])
        self.assertEqual(self.protocol, [])
        await voice.model_event({'type': 'message_update', 'assistantMessageEvent': {
            'type': 'text_delta', 'delta': 'Useful words.'}}, 0, 'turn')
        public = next(event for event in self.events if event.get('phase') == 'text_delta')
        self.assertEqual(public['delta'], 'Useful words.')
        self.assertEqual(public['source'], 'cascade_text_model')

    async def test_native_submission_explicitly_unsupported_without_rpc(self):
        voice = self.native()
        voice.client = SimpleNamespace(_rpc=Mock())
        with self.assertRaisesRegex(NotImplementedError, 'Native typed user turns'):
            await voice.submit_text('Read this sentence.')
        self.assertEqual(voice.client._rpc.mock_calls, [])
        self.assertEqual(self.protocol, [])
        self.assertFalse(voice.supports_text_input)

    async def test_tool_success_has_paired_identity_receipt_and_timing(self):
        voice = self.cascade(provider='codex', model='gpt-6-sol')
        bridge = RevisionBridge(voice, 0, 0, 'Read the sensors.')
        receipt = await bridge.execute({'name': 'get_sensor_snapshot', 'arguments': {}})
        diagnostics = [event for event in self.events if event.get('type') == 'debug_tool']
        self.assertEqual([event['phase'] for event in diagnostics], ['started', 'finished'])
        start, finish = diagnostics
        self.assertEqual(start['call_id'], finish['call_id'])
        self.assertEqual((finish['provider'], finish['model']), ('codex', 'gpt-6-sol'))
        self.assertEqual(finish['arguments'], {})
        self.assertEqual(finish['result'], json.loads(receipt['result']['content'][0]['text']))
        self.assertTrue(finish['success'])
        self.assertGreaterEqual(finish['duration_ms'], finish['execution_ms'])
        self.assertIsNone(finish['error_type'])

    async def test_tool_exception_always_finishes_without_exception_payload(self):
        voice = self.cascade()
        class BrokenBackground:
            async def handle(inner, name, args):
                raise RuntimeError('PRIVATE_EXCEPTION_PAYLOAD')
        voice.background = BrokenBackground()
        bridge = RevisionBridge(voice, 0, 0, 'Compare these options.')
        with self.assertRaises(RuntimeError):
            await bridge.execute({'name': 'start_background_task', 'arguments': {'question': 'Compare these options'}})
        diagnostics = [event for event in self.events if event.get('type') == 'debug_tool']
        self.assertEqual([event['phase'] for event in diagnostics], ['started', 'finished'])
        self.assertEqual(diagnostics[-1]['error_type'], 'RuntimeError')
        self.assertFalse(diagnostics[-1]['success'])
        self.assertNotIn('PRIVATE_EXCEPTION_PAYLOAD', json.dumps(self.events))

    async def test_cancelled_tool_finishes_and_does_not_emit_applied_receipt(self):
        voice = self.cascade()
        entered = asyncio.Event()
        class WaitingBackground:
            async def handle(inner, name, args):
                entered.set()
                await asyncio.Event().wait()
        voice.background = WaitingBackground()
        bridge = RevisionBridge(voice, 0, 0, 'Compare these options.')
        task = asyncio.create_task(bridge.execute({'name': 'start_background_task', 'arguments': {'question': 'Compare'}}))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        finish = next(event for event in self.events if event.get('phase') == 'finished')
        self.assertEqual(finish['error_type'], 'CancelledError')
        self.assertFalse(any(event.get('type') == 'tool' for event in self.events))

    async def test_stale_tool_diagnostic_does_not_execute(self):
        voice = self.cascade()
        bridge = RevisionBridge(voice, 0, 0, 'Stop.')
        voice.revision += 1
        with self.assertRaises(StaleTurn):
            await bridge.execute({'name': 'stop_robot', 'arguments': {}})
        self.assertFalse(self.runtime.estop)
        finish = next(event for event in self.events if event.get('phase') == 'finished')
        self.assertTrue(finish['stale'])
        self.assertIsNone(finish['execution_ms'])

    async def test_background_reason_uses_session_model_and_only_public_text(self):
        class Client:
            def __init__(inner, **options):
                inner.options = options
                self.created = inner
            def new_session(inner):
                pass
            def ask(inner, prompt, timeout, on_event):
                on_event({'type': 'message_update', 'assistantMessageEvent': {'type': 'thinking_delta', 'delta': 'PRIVATE_REASONING'}})
                on_event({'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'Useful answer.'}})
                on_event({'type': 'tool_execution_start', 'toolName': 'recall', 'args': {}})
                on_event({'type': 'tool_execution_end', 'toolName': 'recall', 'args': {}, 'result': {'content': []}, 'isError': False})
                return SimpleNamespace(text='Useful answer.', errors=[], tool_calls=[])
            def close(inner):
                pass
        brain = BackgroundBrain(self.runtime, self.directory.name, self.emit, lambda: 0, model='gpt-6-astra')
        with patch('codex_client.CodexClient', Client), patch('duplex.native.choose_binary'), \
                patch.dict(os.environ, {'MIST_BACKGROUND_MODEL': 'gpt-6-luna'}):
            receipt = await brain.handle('start_background_task', {'question': 'Compare these options.'})
            await asyncio.gather(*list(brain.tasks.values()))
        self.assertEqual(self.created.options['model'], 'gpt-6-astra')
        self.assertEqual(brain.jobs[receipt['job_id']]['status'], 'complete')
        self.assertNotIn('PRIVATE_REASONING', json.dumps(self.events))
        self.assertTrue(any(event.get('delta') == 'Useful answer.' for event in self.events))
        self.assertTrue(any(event.get('type') == 'debug_background_tool' and event['model'] == 'gpt-6-astra' for event in self.events))
        await brain.close()

    async def test_native_backing_debug_filters_reasoning_and_retains_tool_failure(self):
        notifications = queue.Queue()
        replies = []
        rpc = SimpleNamespace(events=notifications, send=replies.append)
        voice = self.native(model='gpt-6-sol')
        voice.client = SimpleNamespace(thread_id='session', model='gpt-6-sol', _rpc=rpc)
        for event in [
            {'method': 'turn/started', 'params': {'turn': {'id': 'turn'}}},
            {'method': 'item/reasoning/textDelta', 'params': {'delta': 'PRIVATE_REASONING'}},
            {'method': 'item/agentMessage/delta', 'params': {'delta': 'Useful words.'}},
            {'id': 1, 'method': 'item/tool/call', 'params': {'turnId': 'turn', 'tool': 'get_sensor_snapshot', 'arguments': {}}},
            {'method': 'turn/completed', 'params': {'turn': {'id': 'turn', 'status': 'completed'}}}, None]:
            notifications.put(event)
        with patch.object(self.runtime, 'call', side_effect=RuntimeError('PRIVATE_EXCEPTION_PAYLOAD')):
            await voice.events()
        self.assertNotIn('PRIVATE_REASONING', json.dumps(self.events))
        self.assertNotIn('PRIVATE_EXCEPTION_PAYLOAD', json.dumps(self.events))
        self.assertTrue(any(event.get('delta') == 'Useful words.' for event in self.events))
        finish = next(event for event in self.events if event.get('type') == 'debug_tool' and event.get('phase') == 'finished')
        self.assertFalse(finish['success'])
        self.assertEqual(finish['error_type'], 'RuntimeError')
        self.assertEqual(finish['result']['status'], 'refused')
        self.assertGreaterEqual(finish['duration_ms'], 0)
        self.assertFalse(replies[0]['result']['success'])


if __name__ == '__main__':
    unittest.main()
