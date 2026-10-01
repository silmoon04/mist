"""Delivery, packet boundaries and session-local voice contracts."""
import asyncio
import base64
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from duplex.tts import StreamingTTS, EXPRESSIVE_MODEL, DELIVERY_TAGS
from duplex.affect_director import DELIVERIES
from duplex.live_studio import create_studio, VOICES
from test_tts_expressive_protocol_20260929 import Socket
from test_live_studio import Voice, Mask

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_reader_styles_have_bounded_single_reply_tags(self):
        self.assertEqual(set(DELIVERIES), set(DELIVERY_TAGS))
        for delivery in DELIVERIES:
            events=[]
            async def emit(event): events.append(event)
            t=StreamingTTS('fixture', emit, model_id=EXPRESSIVE_MODEL,
                           voice_id='cgSgspJ2msm6clMCkdW9')
            t.socket=Socket()
            await t.begin('test', delivery=delivery)
            await t.text('The first sentence. Another sentence.')
            await t.finish()
            self.assertEqual(t.socket.sent[0]['voices'], ['cgSgspJ2msm6clMCkdW9'])
            inputs=[msg['inputs'][0] for msg in t.socket.sent if 'inputs' in msg]
            self.assertTrue(all(i['voice_id']==t.voice_id for i in inputs))
            tag=DELIVERY_TAGS[delivery]
            self.assertEqual(sum(i['text'].count(tag) for i in inputs) if tag else 0,
                             1 if tag else 0)
            style=next(e for e in events if e['type']=='speech_style')
            self.assertEqual(style['tag_name'],tag[1:-1] if tag else None)
            self.assertFalse(t.set_delivery('bright'))

    async def test_invalid_voice_cannot_change_provider_url(self):
        async def emit(event): pass
        for value in ['../voice','x?model_id=other',None,42,'a'*21,'']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                StreamingTTS('fixture',emit,voice_id=value)

    async def test_control_tag_split_between_packets_never_reaches_captions(self):
        events=[]
        async def emit(event): events.append(event)
        t=StreamingTTS('fixture',emit,model_id=EXPRESSIVE_MODEL); t.socket=Socket()
        await t.begin('split',delivery='curious'); context=t.active
        await t.text('Hello.')
        for text in ['[cur','iously] Hello. ']:
            await t.output({'context_id':context,'audio':base64.b64encode(b'\x58\x1b'*12000).decode(),
                'normalized_alignment':{'chars':list(text),'char_start_times_ms':[i*20 for i in range(len(text))],
                                        'char_durations_ms':[20]*len(text)}})
        captions=[c['text'] for e in events if e['type']=='audio' for c in e.get('caption_cues',[])]
        self.assertTrue(any('Hello.' in c for c in captions))
        self.assertTrue(all('cur' not in c and 'iously' not in c and '[' not in c for c in captions))

    async def test_empty_final_is_failure_without_success_metric(self):
        events=[]
        async def emit(event): events.append(event)
        t=StreamingTTS('fixture',emit,model_id=EXPRESSIVE_MODEL); t.socket=Socket()
        await t.begin('empty'); context=t.active; await t.text('Hello.')
        await t.output({'context_id':context,'is_final':True})
        self.assertFalse(any(e['type']=='latency' for e in events))
        self.assertTrue(any(e.get('code')=='tts_empty_reply' for e in events))

class VoiceSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        Voice.instances.clear(); Voice.startup_gate=Voice.post_ready_gate=None
        class VoiceMask(Mask):
            def __init__(self,key,emit,model_id='eleven_flash_v2_5',voice_id=VOICES[0]['voice_id']):
                super().__init__(key,emit,model_id); self.voice_id=voice_id
        self.patcher=patch('duplex.live_studio.StreamingTTS',VoiceMask)
        self.patcher.start(); self.addCleanup(self.patcher.stop)
        self.app=create_studio(SimpleNamespace(run_dir=self.tmp.name))
        self.app['env'].update(ELEVENLABS_API_KEY='fixture',DEEPGRAM_API_KEY='fixture',CEREBRAS_API_KEY='fixture')
        self.app['trial_voice_factory']=Voice
        self.client=TestClient(TestServer(self.app)); await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers={'Origin':str(self.client.make_url('/')).rstrip('/')}
        await self.client.post('/trial/bootstrap',json={},headers=self.headers)

    async def test_listed_voices_are_recorded_and_used_without_changing_default(self):
        for choice in VOICES:
            ws=await self.client.ws_connect('/trial/voice?architecture=qwen-expressive&voice='+choice['id'],headers=self.headers)
            session=await ws.receive_json(timeout=3)
            self.assertEqual(session['architecture']['voice_id'],choice['voice_id'])
            while (await ws.receive_json(timeout=3))['type']!='ready': pass
            self.assertEqual(self.app['trial_owner']['session'].mask.voice_id,choice['voice_id'])
            await ws.close(); await asyncio.wait_for(self.app['trial_closed'].wait(),3)
        catalog=await (await self.client.get('/trial/catalog')).json()
        self.assertEqual(catalog['default'],'qwen-memory')
        self.assertTrue(all(a['voice_id']==VOICES[0]['voice_id'] for a in catalog['architectures']))

    async def test_unlisted_voice_rejected_before_any_provider_starts(self):
        r=await self.client.get('/trial/voice?architecture=qwen-expressive&voice=arbitrary',headers=self.headers)
        self.assertEqual(r.status,400); self.assertFalse(Voice.instances)

if __name__=='__main__': unittest.main()
