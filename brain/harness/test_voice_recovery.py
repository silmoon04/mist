"""Reproduce a dropped conversion connection at the real streaming boundary."""
import asyncio
from pathlib import Path
import sys
import unittest
import aiohttp
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from duplex.conversion import VoiceMask

class Content:
    def __init__(self,fail=False):self.fail=fail
    async def iter_chunked(self,n):
        if self.fail:raise aiohttp.ServerDisconnectedError('Disconnected before audio')
        for _ in range(3):
            await asyncio.sleep(0)
            yield b'\x01\x00'*1200

class Response:
    status=200
    headers={}
    def __init__(self,fail=False):self.content=Content(fail)
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False

class Session:
    def __init__(self):self.calls=0
    def post(self,*args,**kwargs):
        self.calls+=1
        return Response(fail=self.calls==1)

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_drop_before_audio_does_not_kill_conversation(self):
        events=[]
        async def emit(e):events.append(e)
        m=VoiceMask('fake',emit);m.client=Session();m.sequence=1
        await m.convert(b'\1\0'*6400,0,0)
        self.assertFalse([e for e in events if e['type']=='error' and e.get('fatal')],events)
        self.assertTrue([e for e in events if e['type']=='audio'])
        self.assertEqual(m.client.calls,2)

    async def test_actual_connector_error_is_retried(self):
        class ConnectorResponse(Response):
            async def __aenter__(self):
                raise aiohttp.ClientConnectorError(SimpleNamespace(host='api.elevenlabs.io',port=443,ssl=True),OSError(10051,'Network unavailable'))
        class ConnectorSession(Session):
            def post(self,*a,**kw):
                self.calls+=1
                return ConnectorResponse() if self.calls==1 else Response()
        events=[]
        async def emit(e):events.append(e)
        m=VoiceMask('fake',emit);m.client=ConnectorSession();m.sequence=1
        await m.convert(b'\1\0'*6400,0,0)
        self.assertEqual(m.client.calls,2)
        self.assertTrue(any(e['type']=='audio' for e in events))
        self.assertFalse(any(e['type'] in ('error','voice_warning') for e in events))

    async def test_exhausted_retry_allows_next_reply(self):
        class BrokenSession(Session):
            def post(self,*a,**kw):self.calls+=1;return Response(fail=True)
        events=[]
        async def emit(e):events.append(e)
        m=VoiceMask('fake',emit);m.client=BrokenSession();m.sequence=1
        await m.convert(b'\1\0'*6400,0,0)
        self.assertEqual(m.client.calls,3)
        self.assertTrue(any(e['type']=='voice_warning' for e in events))
        self.assertFalse(any(e['type']=='error' for e in events))
        m.client=Session();m.client.calls=1;m.muted=False
        seq=m.sequence;m.sequence+=1
        await m.convert(b'\1\0'*6400,seq,m.epoch)
        self.assertTrue(any(e['type']=='audio' and e['epoch']==1 for e in events))

    async def test_no_replay_after_partial_audio(self):
        class Partial:
            async def iter_chunked(self,n):
                yield b'\1\0'*1200
                raise aiohttp.ClientPayloadError('Partial body')
        class PartialSession(Session):
            def post(self,*a,**kw):self.calls+=1;r=Response();r.content=Partial();return r
        events=[]
        async def emit(e):events.append(e)
        m=VoiceMask('fake',emit);m.client=PartialSession();m.sequence=1
        await m.convert(b'\1\0'*6400,0,0)
        self.assertEqual(m.client.calls,1)
        self.assertEqual(sum(e['type']=='audio' for e in events),1)
        self.assertTrue(any(e['type']=='voice_warning' for e in events))

    async def test_auth_failure_is_explicit_and_not_retried(self):
        class Denied(Session):
            def post(self,*a,**kw):self.calls+=1;r=Response();r.status=401;return r
        events=[]
        async def emit(e):events.append(e)
        m=VoiceMask('fake',emit);m.client=Denied();m.sequence=1
        await m.convert(b'\1\0'*6400,0,0)
        self.assertEqual(m.client.calls,1)
        self.assertEqual(next(e for e in events if e['type']=='error')['code'],'voice_http_401')

    async def test_concurrent_interruptions_do_not_become_converter_errors(self):
        events=[]
        async def emit(e):events.append(e);await asyncio.sleep(0)
        m=VoiceMask('fake',emit);m.client=Session();m.client.calls=1
        for _ in range(50):
            jobs=[]
            for n in range(3):
                seq=m.sequence;m.sequence+=1
                task=asyncio.create_task(m.convert(b'\1\0'*6400,seq,m.epoch))
                m.tasks.add(task);task.add_done_callback(m.tasks.discard);jobs.append(task)
            await asyncio.sleep(0);await asyncio.sleep(0);await m.interrupt()
            await asyncio.gather(*jobs,return_exceptions=True)
        self.assertFalse([e for e in events if e['type']=='error'],events)

if __name__=='__main__':unittest.main(verbosity=2)
