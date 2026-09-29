"""The remote trial server exposes no voice or saved traces before pairing."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.live_studio import create_studio
from brain.harness.test_live_studio import Mask, Voice


ORIGIN = 'https://mist.example.test'
CODE = 'q1Z8vR2tY7nB4mL9cD3fH6jK5pS0wX8a'


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict('os.environ', {'MIST_REMOTE_PAIR_CODE':CODE}):
            self.app = create_studio(SimpleNamespace(run_dir=self.tmp.name,
                remote_origin=ORIGIN, remote_pair_code_file=None))
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {'Host':'mist.example.test', 'Origin':ORIGIN}

    async def request(self, method, path, **kwargs):
        headers = {**self.headers, **kwargs.pop('headers', {})}
        return await self.client.request(method, path, headers=headers, **kwargs)

    async def test_unauthenticated_routes_and_underlying_pair_are_closed(self):
        session = await self.request('GET', '/trial/session')
        self.assertEqual(await session.json(), {'authenticated':False,'remote_mode':True})
        for method,path in [('POST','/trial/bootstrap'),('GET','/trial/catalog'),
                            ('GET','/trial/sessions'),('GET','/trial/events?session=x'),
                            ('GET','/trial/export?session=x'),
                            ('GET','/trial/memory?session=x'),
                            ('GET','/trial/audio?session=x&stream=mic'),
                            ('GET','/trial/audio?session=x&stream=assistant_generated'),
                            ('GET','/config'),
                            ('GET','/debug/events'),('GET','/'),('GET','/duplex/app.js'),
                            ('POST','/pair')]:
            with self.subTest(path=path):
                response = await self.request(method,path)
                self.assertEqual(response.status, 401)
        self.assertEqual((await self.request('GET','/try')).status,200)
        self.assertEqual((await self.request('GET','/duplex/trials.css')).status,200)
        with self.assertRaises(WSServerHandshakeError) as caught:
            await self.client.ws_connect('/trial/voice?architecture=native', headers=self.headers)
        self.assertEqual(caught.exception.status,401)

    async def test_host_origin_and_forwarded_headers_cannot_bypass_gate(self):
        for headers in [
            {'Host':'evil.test','Origin':ORIGIN},
            {'Host':'mist.example.test','Origin':'https://evil.test'},
            {'Host':'127.0.0.1','Origin':ORIGIN,'X-Forwarded-Host':'mist.example.test'},
            {'Host':'mist.example.test','Origin':'http://mist.example.test','X-Forwarded-Proto':'https'},
        ]:
            with self.subTest(headers=headers):
                response = await self.client.post('/trial/pair',json={'code':CODE},headers=headers)
                self.assertEqual(response.status,403)
        response = await self.client.post('/trial/pair',json={'code':CODE},headers={'Host':'mist.example.test'})
        self.assertEqual(response.status,403)

    async def test_pair_cookie_bootstrap_logout_and_rate_limit(self):
        non_ascii = await self.request('POST','/trial/pair',json={'code':'é' * 32})
        self.assertEqual(non_ascii.status,401)
        malformed = await self.request('POST','/trial/pair',data='{broken',headers={'Content-Type':'application/json'})
        self.assertEqual(malformed.status,400)
        non_object = await self.request('POST','/trial/pair',json=['wrong'])
        self.assertEqual(non_object.status,400)
        bad = await self.request('POST','/trial/pair',json={'code':'wrong'})
        self.assertEqual(bad.status,401)
        paired = await self.request('POST','/trial/pair',json={'code':CODE})
        self.assertEqual(paired.status,200)
        cookie = paired.headers['Set-Cookie']
        for part in ('HttpOnly','Secure','SameSite=Strict'):
            self.assertIn(part,cookie)
        self.headers['Cookie'] = cookie.split(';',1)[0]
        self.assertEqual((await self.request('GET','/trial/session')).status,200)
        self.assertTrue((await (await self.request('GET','/trial/session')).json())['authenticated'])
        self.assertEqual((await self.request('POST','/trial/bootstrap',json={})).status,200)
        catalog = await (await self.request('GET','/trial/catalog')).json()
        self.assertEqual(catalog['default'],'qwen-memory')
        self.assertTrue(next(item for item in catalog['architectures'] if item['id']=='qwen-memory')
                        ['session_memory'])
        for path in ('/trial/memory?session=x', '/trial/audio?session=x&stream=mic'):
            self.assertEqual((await self.request('GET', path,
                headers={'Origin':'https://evil.test'})).status, 403)
        self.assertEqual(next(item for item in catalog['architectures'] if item['id']=='qwen-affect')
                         ['connect_retries_configured'],1)
        self.assertEqual(next(item for item in catalog['architectures'] if item['id']=='qwen-flux')
                         ['connect_retries_configured'],1)
        self.assertEqual((await self.request('GET','/trial/sessions')).status,200)
        self.assertEqual((await self.request('GET','/config')).status,200)
        self.assertTrue((await (await self.request('GET','/config')).json())['remote_mode'])
        with self.assertRaises(WSServerHandshakeError) as caught:
            await self.client.ws_connect('/trial/voice?architecture=native',
                headers={**self.headers,'Origin':'https://evil.test'})
        self.assertEqual(caught.exception.status,403)
        with self.assertRaises(WSServerHandshakeError) as caught:
            await self.client.ws_connect('/trial/voice?architecture=invalid', headers=self.headers)
        self.assertEqual(caught.exception.status,400)
        self.assertEqual(self.app['remote_access'].scrub({'message':f'bad {CODE}'}),
                         {'message':'bad [redacted]'})
        self.assertEqual((await self.request('POST','/trial/logout',json={})).status,200)
        self.assertEqual((await self.request('GET','/trial/sessions')).status,401)
        for _ in range(5):
            response = await self.request('POST','/trial/pair',json={'code':'wrong'})
        self.assertEqual(response.status,429)
        response = await self.request('POST','/trial/pair',json={'code':CODE})
        self.assertEqual(response.status,429)

    async def test_logout_closes_authenticated_trial_websocket(self):
        paired = await self.request('POST','/trial/pair',json={'code':CODE})
        self.headers['Cookie'] = paired.headers['Set-Cookie'].split(';',1)[0]
        self.app['env'].update(ELEVENLABS_API_KEY='fake-key', DEEPGRAM_API_KEY='fake-key', CEREBRAS_API_KEY='fake-key')
        self.app['trial_voice_factory'] = Voice
        with patch('duplex.live_studio.StreamingTTS', Mask):
            flux_ws = await self.client.ws_connect('/trial/voice?architecture=qwen-flux', headers=self.headers)
            self.assertEqual((await flux_ws.receive_json(timeout=3))['type'],'studio_session')
            while (await flux_ws.receive_json(timeout=3))['type'] != 'ready':
                pass
            self.assertEqual(Voice.instances[-1].kwargs['connect_retries'],1)
            await flux_ws.close()
            await asyncio.wait_for(self.app['trial_closed'].wait(),3)
            ws = await self.client.ws_connect('/trial/voice?architecture=cerebras-balanced', headers=self.headers)
            self.assertEqual((await ws.receive_json(timeout=3))['type'],'studio_session')
            while (await ws.receive_json(timeout=3))['type'] != 'ready':
                pass
            self.assertEqual(Voice.instances[-1].kwargs['connect_retries'],1)
            logout = await self.request('POST','/trial/logout',json={})
            self.assertEqual(logout.status,200)
            saw_close = False
            for _ in range(10):
                message = await ws.receive(timeout=3)
                if message.type.name in ('CLOSE','CLOSED','CLOSING'):
                    saw_close = True
                    break
            self.assertTrue(saw_close)
            await ws.close()
            self.assertTrue(ws.closed)
            self.assertEqual((await self.request('GET','/trial/sessions')).status,401)
            await asyncio.wait_for(self.app['trial_closed'].wait(),3)


if __name__ == '__main__':
    unittest.main()
