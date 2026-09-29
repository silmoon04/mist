"""Architecture isolation, cancellation and saved browser/server observations."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.live_studio import create_studio


class Mask:
    def __init__(self, key, emit):
        self.emit = emit
        self.pending = False
        self.epoch = 0
        self.muted = False
    async def start(self): pass
    async def close(self): pass
    async def warm(self): pass
    async def feed(self, *args): pass
    def diagnostic(self, *args, **kwargs): pass


class Voice:
    instances = []
    startup_gate = None
    post_ready_gate = None
    def __init__(self, runtime, run_dir, emit, audio, dc_event, **kwargs):
        self.runtime, self.emit, self.dc_event = runtime, emit, dc_event
        self.kwargs = kwargs
        self.closed = False
        self.submitted = []
        self.start_finished = False
        self.turn = None
        self.track = SimpleNamespace(append=lambda pcm: None)
        Voice.instances.append(self)
    async def start(self):
        if Voice.startup_gate:
            await Voice.startup_gate.wait()
        await self.emit({'type':'ready'})
        if Voice.post_ready_gate:
            await Voice.post_ready_gate.wait()
        self.start_finished = True
    async def close(self): self.closed = True
    async def submit_text(self, text):
        self.submitted.append(text)
        await self.emit({'type':'transcript_done', 'role':'user', 'text':text})
        result = self.runtime.call('set_expression', {'expression':'sad', 'duration_ms':2000})
        await self.emit({'type':'tool', 'name':'set_expression', 'arguments':{'expression':'sad'},
                         'result':result, 'expression_revision':0})


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Voice.instances.clear()
        Voice.startup_gate = None
        Voice.post_ready_gate = None
        self.mask_patch = patch('duplex.live_studio.StreamingTTS', Mask)
        self.mask_patch.start()
        self.addCleanup(self.mask_patch.stop)
        self.app = create_studio(SimpleNamespace(run_dir=self.tmp.name))
        self.app['env'].update(ELEVENLABS_API_KEY='fake-key', DEEPGRAM_API_KEY='fake-key', CEREBRAS_API_KEY='fake-key')
        self.app['trial_voice_factory'] = Voice
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.origin = str(self.client.make_url('/')).rstrip('/')
        self.headers = {'Origin':self.origin}
        result = await self.client.post('/trial/bootstrap', json={}, headers=self.headers)
        self.assertEqual(result.status, 200)

    async def connect(self, architecture='cerebras-balanced', wait_ready=True):
        ws = await self.client.ws_connect('/trial/voice?architecture='+architecture, headers=self.headers)
        first = await ws.receive_json(timeout=3)
        self.assertEqual(first['type'], 'studio_session')
        if wait_ready:
            while (await ws.receive_json(timeout=3))['type'] != 'ready':
                pass
        return ws, first['trace_id']

    async def end(self, ws):
        await ws.close()
        await asyncio.wait_for(self.app['trial_closed'].wait(), 3)

    async def test_catalog_is_explicit_and_does_not_change_global_environment(self):
        data = await (await self.client.get('/trial/catalog')).json()
        self.assertEqual(len(data['architectures']), 11)
        self.assertEqual(data['default'], 'cerebras-balanced')
        ws, trace = await self.connect('cerebras-fast')
        self.assertEqual(Voice.instances[-1].kwargs['endpoint_ms'], 300)
        await self.end(ws)
        ws, trace = await self.connect('luna')
        self.assertEqual(Voice.instances[-1].kwargs['model'], 'gpt-6-luna')
        self.assertEqual(Voice.instances[-1].kwargs['provider'], 'codex')
        await self.end(ws)

    async def test_reasoning_catalog_reaches_session_factory_and_saved_configuration(self):
        data = await (await self.client.get('/trial/catalog')).json()
        catalog = {a['id']:a for a in data['architectures']}
        for identifier, model, effort in [('cerebras-balanced','gpt-oss-120b','low'),
                                           ('cerebras-high','gpt-oss-120b','high'),
                                           ('qwen-none','qwen-3.8-27b','none'),
                                           ('qwen-low','qwen-3.8-27b','low')]:
            with self.subTest(identifier=identifier):
                architecture = catalog[identifier]
                self.assertEqual(architecture['reasoning_effort'],effort)
                self.assertEqual(architecture['endpoint_ms'],700)
                self.assertEqual(architecture['floor'],'selective')
                self.assertEqual(architecture['max_output_tokens'],4096)
                ws, trace = await self.connect(identifier)
                options = Voice.instances[-1].kwargs
                self.assertEqual((options['model'],options['reasoning_effort']),(model,effort))
                self.assertEqual(options['max_output_tokens'],4096)
                await self.end(ws)
                saved = await (await self.client.get('/trial/export?session='+trace)).json()
                self.assertEqual(saved['session']['config']['reasoning_effort'],effort)
                self.assertEqual(saved['session']['config']['model'],model)
                self.assertEqual(saved['session']['config']['max_output_tokens'],4096)
                self.assertIn('previous 1024-token cap',saved['session']['config']['output_limit_note'])
        for architecture in catalog.values():
            if architecture['provider']=='cerebras':
                self.assertEqual(architecture['max_output_tokens'],4096)
            elif architecture['provider']=='codex':
                self.assertEqual(architecture['max_output_tokens'],400)
        self.assertEqual(data['default'],'cerebras-balanced')

    async def test_switch_keeps_archives_and_fresh_runtime(self):
        ws, first = await self.connect()
        Voice.instances[-1].runtime.memory.append({'note':'private first conversation'})
        await self.end(ws)
        ws, second = await self.connect('native')
        self.assertNotEqual(first, second)
        self.assertEqual(Voice.instances[-1].runtime.memory, [])
        await self.end(ws)
        sessions = (await (await self.client.get('/trial/sessions')).json())['sessions']
        self.assertEqual(len(sessions), 2)
        saved = await (await self.client.get('/trial/export?session='+first)).json()
        self.assertTrue(saved['events'])
        self.assertNotEqual(saved['session']['status'], 'running')

    async def test_assisted_architecture_is_explicit_and_retains_context(self):
        ws,trace=await self.connect('qwen-assist')
        voice=Voice.instances[-1]
        self.assertEqual(voice.kwargs['model'],'qwen-3.8-27b')
        self.assertEqual(voice.kwargs['conversation_policy'],'responsive')
        self.assertEqual(voice.kwargs['endpoint_ms'],500)
        self.assertTrue(voice.kwargs['parallel_tool_calls'])
        analyst=self.app['trial_owner']['session'].brain
        self.assertEqual((analyst.provider,analyst.model,analyst.reasoning_effort),('cerebras','gpt-oss-120b','medium'))
        voice.history=[{'role':'user','text':'Keep the latest constraint.'}]
        self.assertEqual(analyst.snapshot_context()['records'],voice.history)
        await self.end(ws)
        saved=await (await self.client.get('/trial/export?session='+trace)).json()
        config=saved['session']['config']
        self.assertEqual(config['playback_buffer_ms'],120)
        self.assertEqual(config['background_reasoning_effort'],'medium')

    async def test_actual_face_request_and_browser_playback_are_saved(self):
        ws, trace = await self.connect()
        await ws.send_json({'type':'text','text':'Please look sad.'})
        while (await ws.receive_json(timeout=3))['type'] != 'face':
            pass
        await ws.send_json({'type':'debug_client','event':{'type':'playback_started','client_ms':123.5}})
        await ws.send_json({'type':'ping'})
        while (await ws.receive_json(timeout=3))['type'] != 'pong':
            pass
        await self.end(ws)
        saved = await (await self.client.get('/trial/export?session='+trace)).json()
        events = saved['events']
        self.assertTrue(any(r['event'].get('type') == 'face' for r in events))
        self.assertTrue(any(r['source'] == 'browser' and r['event'].get('client_ms') == 123.5 for r in events))

    async def test_disconnect_cancels_startup_then_allows_new_architecture(self):
        Voice.startup_gate = asyncio.Event()
        ws, trace = await self.connect(wait_ready=False)
        response = await self.client.post('/trial/disconnect', json={}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertIsNone(self.app['trial_owner'])
        self.assertTrue(Voice.instances[-1].closed)
        Voice.startup_gate = None
        ws2, second = await self.connect('luna')
        await self.end(ws2)
        await ws.close()

    async def test_ready_accepts_packets_while_provider_start_is_finishing(self):
        Voice.startup_gate = asyncio.Event()
        Voice.post_ready_gate = asyncio.Event()
        ws, trace = await self.connect(wait_ready=False)
        try:
            await ws.send_json({'type':'text', 'text':'Too early.'})
            while (event := await ws.receive_json(timeout=3))['type'] != 'error':
                self.assertNotEqual(event['type'], 'ready')
            self.assertEqual(event['message'], 'Voice is still connecting.')
            self.assertFalse(self.app['trial_owner']['session'].started)
            self.assertEqual(Voice.instances[-1].submitted, [])

            Voice.startup_gate.set()
            while (await ws.receive_json(timeout=3))['type'] != 'ready':
                pass
            self.assertFalse(Voice.instances[-1].start_finished)
            self.assertFalse(self.app['trial_owner']['start'].done())
            await ws.send_json({'type':'debug_client', 'event':{
                'type':'playback_configuration', 'playback_buffer_ms':120, 'client_ms':42}})
            await ws.send_json({'type':'text', 'text':'Ready now.'})
            await ws.send_json({'type':'ping'})
            observed = []
            while True:
                event = await ws.receive_json(timeout=3)
                self.assertNotEqual(event['type'], 'error', 'ready must mean packets are accepted before start() returns')
                observed.append(event)
                if event['type'] == 'pong':
                    break
            self.assertTrue(self.app['trial_owner']['session'].started)
            self.assertFalse(Voice.instances[-1].start_finished, 'the test must retain the post-ready race window')
            self.assertEqual(Voice.instances[-1].submitted, ['Ready now.'])
            self.assertTrue(any(e.get('text') == 'Ready now.' for e in observed))
        finally:
            Voice.startup_gate.set()
            Voice.post_ready_gate.set()
            await self.end(ws)
        saved = await (await self.client.get('/trial/export?session='+trace)).json()
        self.assertTrue(any(r['source'] == 'browser' and r['event'].get('type') == 'playback_configuration'
                            and r['event'].get('playback_buffer_ms') == 120 for r in saved['events']))

    async def test_requires_pairing_origin_and_allowed_architecture(self):
        self.assertEqual((await self.client.post('/trial/bootstrap', json={})).status, 403)
        self.assertEqual((await self.client.post('/trial/disconnect', json={}, headers={'Origin':'https://elsewhere.invalid'})).status, 403)
        self.assertEqual((await self.client.get('/trial/voice?architecture=unknown', headers=self.headers)).status, 400)
        self.assertEqual((await self.client.get('/trial/export?session=../../.env')).status, 404)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get('/trial/sessions')).status, 403)

    async def test_rejects_overlapping_sessions(self):
        ws, trace = await self.connect()
        response = await self.client.get('/trial/voice?architecture=luna', headers=self.headers)
        self.assertEqual(response.status, 409)
        await self.end(ws)

    async def test_failed_provider_close_prevents_replacement(self):
        ws, trace = await self.connect()
        async def fail_close():
            raise RuntimeError('Provider retirement failed')
        Voice.instances[-1].close = fail_close
        await self.end(ws)
        self.assertTrue(self.app['trial_health']['quarantined'])
        response = await self.client.get('/trial/voice?architecture=luna', headers=self.headers)
        self.assertEqual(response.status, 503)
        saved = await (await self.client.get('/trial/export?session='+trace)).json()
        self.assertEqual(saved['session']['status'], 'failed')

    async def test_history_metadata_retains_the_original_configuration(self):
        ws, trace = await self.connect('cerebras-fast')
        await self.end(ws)
        data = await (await self.client.get('/trial/sessions')).json()
        selected = next(s for s in data['sessions'] if s['trace_id'] == trace)
        self.assertEqual(selected['architecture']['id'], 'cerebras-fast')
        self.assertEqual(selected['architecture']['endpoint_ms'], 300)

    async def test_bad_packets_and_cursors_are_visible(self):
        ws, trace = await self.connect()
        await ws.send_str('bad json')
        event = await ws.receive_json(timeout=3)
        self.assertEqual(event['type'], 'error')
        await self.end(ws)
        self.assertEqual((await self.client.get('/trial/events?session='+trace+'&after=bad')).status, 400)
        data = await (await self.client.get('/trial/events?session='+trace+'&limit=2')).json()
        self.assertEqual(len(data['events']), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
