"""Streaming text, cancellation, ordering and native event regressions."""
import asyncio, base64, sys, unittest
import tempfile
import json
import aiohttp
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from duplex.tts import StreamingTTS, EXPRESSIVE_MODEL
from duplex.native import NativeVoice
from duplex.server import Conversation
from duplex.runtime import RobotRuntime

class Socket:
    closed=False
    def __init__(self):self.sent=[]
    async def send_json(self,event):self.sent.append(event)
    async def close(self):self.closed=True

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_partial_alignment_uses_reply_words(self):
        trace = Path(__file__).resolve().parents[1] / 'results' / 'v4-expressive-20261001' / 'live-smoke' / 'browser-gentle.json'
        events = json.loads(trace.read_text(encoding='utf-8'))['events']
        script = next(row['event']['text'].strip() for row in events
                      if row['event'].get('type') == 'transcript_done' and row['event'].get('role') == 'assistant')
        aligned = next(row['event']['text'] for row in reversed(events)
                       if row['event'].get('type') == 'caption_progress' and row['event']['text'])
        from duplex.lipsync import canonical_caption_progress
        caption, partial = canonical_caption_progress(aligned, script)
        self.assertEqual(caption, script)
        self.assertTrue(partial)

    async def test_short_repeated_alignment_word_does_not_jump_to_late_phrase(self):
        from duplex.lipsync import canonical_caption_progress
        script = "The happy face is up. The sad one didn't take for some reason, so I'll just say the gentle part out loud."
        caption, partial = canonical_caption_progress('The happy face is up. T the', script)
        self.assertTrue(partial)
        self.assertTrue(caption.startswith('The happy face is up.'))
        self.assertLessEqual(len(caption), len('The happy face is up. The'))

    async def test_partial_provider_alignment_reaches_browser_with_reply_words(self):
        t = StreamingTTS('fake', self.tts.emit, model_id=EXPRESSIVE_MODEL)
        t.socket = Socket()
        await t.begin('reply', delivery='gentle')
        context = t.active
        script = "It's okay. Tomorrow is a fresh page. Goodnight."
        await t.text(script)
        await t.finish(script)
        fragment = "It's is a page. Goodnight."
        raw = b'\0\x20' * 24000
        await t.output({'contextId': context, 'audio': base64.b64encode(raw).decode(),
                        'normalizedAlignment': {
                            'chars': list(fragment),
                            'charStartTimesMs': [i * 25 for i in range(len(fragment))],
                            'charDurationsMs': [25] * len(fragment)}, 'isFinal': True})
        packets = [event for event in self.events if event['type'] == 'audio']
        captions = [cue['text'] for packet in packets for cue in packet['caption_cues']]
        self.assertIn(script, captions)
        self.assertEqual(packets[0]['turn_id'], 'reply')
        self.assertTrue(any('text_fallback' in event['caption_source'] for event in packets))

    async def test_long_provider_idle_has_grace_then_reports_stall(self):
        t = self.tts
        await t.begin('reply')
        context = t.active
        await t.text('Hello.')
        entry = t.pending[context]
        self.assertTrue(entry['provider_started'])
        began = entry['last_send']
        self.assertEqual(t.stalled_contexts(began + 20), [])
        self.assertEqual(t.stalled_contexts(began + 46), [context])
        await t.interrupt()
        self.assertEqual(t.stalled_contexts(began + 46), [])

    async def test_open_expressive_context_sends_keepalive_without_hiding_stall(self):
        t = StreamingTTS('fake', self.tts.emit, model_id=EXPRESSIVE_MODEL)
        t.socket = Socket()
        await t.begin('reply')
        context = t.active
        await t.text('First sentence. ')
        entry = t.pending[context]
        idle_at = entry['last_send'] + 16
        await t.maintain_contexts(idle_at)
        self.assertEqual(t.socket.sent[-1], {'context_id': context, 'keep_alive': True})
        self.assertEqual(t.stalled_contexts(entry['last_send'] + 46), [context])
        await t.maintain_contexts(idle_at + 1)
        self.assertEqual(sum('keep_alive' in event for event in t.socket.sent), 1)

    async def test_next_phrase_delivery_uses_same_expressive_context(self):
        t = StreamingTTS('fake', self.tts.emit, model_id=EXPRESSIVE_MODEL)
        t.socket = Socket()
        await t.begin('reply', delivery='neutral')
        context = t.active
        await t.text('First sentence. ')
        self.assertTrue(t.set_next_phrase_delivery('gentle'))
        await t.text('Second sentence. ')
        await t.finish()
        spoken = [event for event in t.socket.sent if event.get('inputs')]
        self.assertEqual(len(spoken), 2)
        self.assertEqual([event['context_id'] for event in spoken], [context, context])
        self.assertEqual(spoken[0]['inputs'][0]['text'].strip(), 'First sentence.')
        self.assertEqual(spoken[1]['inputs'][0]['text'].strip(), '[gently] Second sentence.')
        self.assertFalse(t.set_next_phrase_delivery('bright'))

    async def test_next_phrase_tag_split_across_provider_packets_stays_hidden(self):
        t = StreamingTTS('fake', self.tts.emit, model_id=EXPRESSIVE_MODEL)
        t.socket = Socket()
        await t.begin('reply', delivery='neutral')
        await t.text('First sentence. ')
        self.assertTrue(t.set_next_phrase_delivery('gentle'))
        await t.text('Second sentence. ')
        entry = t.pending[t.active]
        def alignment(chars):
            return {'normalizedAlignment': {'chars': list(chars),
                    'charStartTimesMs': list(range(len(chars))),
                    'charDurationsMs': [1] * len(chars)}}
        first = t._without_delivery_tag_alignment(alignment('[gen'), entry)
        second = t._without_delivery_tag_alignment(alignment('tly] S'), entry)
        self.assertEqual(first['normalizedAlignment']['chars'], [])
        self.assertEqual(second['normalizedAlignment']['chars'], ['S'])
    async def test_delivery_profile_is_fixed_for_one_context_and_does_not_enter_text(self):
        t=self.tts
        await t.begin('gentle',delivery='gentle')
        await t.text('That sounds difficult.')
        first=t.socket.sent[0]
        await t.finish()
        await t.begin('bright',delivery='bright')
        await t.text('That is wonderful.')
        second=next(event for event in t.socket.sent if event.get('voice_settings',{}).get('stability')==.3)
        await t.finish()
        self.assertGreater(first['voice_settings']['stability'],second['voice_settings']['stability'])
        self.assertNotEqual(first['context_id'],second['context_id'])
        self.assertNotIn('[gentle]',''.join(e.get('text','') for e in t.socket.sent))
        self.assertTrue(all('voice_settings' not in e for e in t.socket.sent if e.get('text','').strip()))

    async def test_invalid_delivery_does_not_open_a_context(self):
        with self.assertRaises(ValueError):await self.tts.begin('bad',delivery='shouting')
        self.assertFalse(self.tts.pending)

    async def test_context_relative_alignment_survives_second_provider_chunk(self):
        t=self.tts;await t.begin('context-clock');context=t.active
        await t.output({'contextId':context,'audio':base64.b64encode(b'\x58\x1b'*24000).decode(),
                        'normalizedAlignment':{'chars':['m'],'charStartTimesMs':[0],'charDurationsMs':[1000]}})
        await t.output({'contextId':context,'audio':base64.b64encode(b'\x58\x1b'*2400).decode(),
                        'normalizedAlignment':{'chars':['f'],'charStartTimesMs':[1000],'charDurationsMs':[100]}})
        second=[e for e in self.events if e['type']=='audio'][-1]
        self.assertEqual(second['alignment_source'],'elevenlabs_characters_audio_gated')
        self.assertEqual(second['mouth_cues'][0]['viseme'],'FV')
        self.assertEqual(second['caption_cues'][0],{'time':0,'text':'mf'})
        self.assertEqual(second['caption_source'],'elevenlabs_normalized_alignment')

    async def test_new_context_resets_caption_prefix_and_alignment_origin(self):
        t=self.tts;await t.begin('a');a=t.active
        await t.output({'contextId':a,'audio':base64.b64encode(b'\x58\x1b'*2400).decode(),
                        'alignment':{'chars':['A'],'charStartTimesMs':[0],'charDurationsMs':[100]}})
        await t.interrupt();await t.begin('b');b=t.active
        await t.output({'contextId':b,'audio':base64.b64encode(b'\x58\x1b'*2400).decode(),
                        'alignment':{'chars':['B'],'charStartTimesMs':[0],'charDurationsMs':[100]}})
        packet=[e for e in self.events if e['type']=='audio'][-1]
        self.assertEqual(packet['caption_cues'][0]['text'],'B')
        self.assertEqual(packet['epoch'],1)

    async def test_initial_connection_drop_retries_before_speech(self):
        class Client:
            attempts=0
            async def ws_connect(self,*args,**kwargs):
                self.attempts+=1
                if self.attempts==1:raise aiohttp.ClientConnectionError('fixture')
                return Socket()
            async def close(self):pass
        t=self.tts;t.socket=None;t.client=Client()
        async def receive(socket):await asyncio.sleep(30)
        t.receive=receive
        await t.connect();self.assertEqual(t.client.attempts,2);await t.close()
    async def asyncSetUp(self):
        self.events=[]
        async def emit(event):self.events.append(event)
        self.tts=StreamingTTS('fake',emit);self.tts.socket=Socket()
    async def test_short_reply_flush_and_no_duplicate_final(self):
        t=self.tts;await t.begin('a');await t.text(' Blue');await t.text(' teacup.');await t.finish('Blue teacup.')
        text=''.join(e.get('text','') for e in t.socket.sent).strip()
        self.assertEqual(text,'Blue teacup.')
        self.assertTrue(t.socket.sent[-2]['flush']);self.assertTrue(t.socket.sent[-1]['close_context'])
    async def test_incomplete_tokens_stay_together(self):
        t=self.tts;await t.begin('a');await t.text('hel');await t.text('lo ');await t.text('wor');await t.text('ld');await t.finish()
        self.assertEqual(''.join(e.get('text','') for e in t.socket.sent).strip(),'hello world')
    async def test_final_only_response_is_spoken(self):
        t=self.tts;await t.begin('a');await t.finish('Yes.')
        self.assertEqual(''.join(e.get('text','') for e in t.socket.sent).strip(),'Yes.')
    async def test_final_unsent_suffix_once(self):
        t=self.tts;await t.begin('a');await t.text('I am ');await t.finish('I am listening.')
        self.assertEqual(''.join(e.get('text','') for e in t.socket.sent).strip(),'I am listening.')
    async def test_late_audio_cannot_revive_interrupted_context(self):
        t=self.tts;await t.begin('a');old=t.active;await t.text('Old sentence.');await t.interrupt()
        await t.output({'contextId':old,'audio':base64.b64encode(b'\1\0'*100).decode()})
        self.assertFalse(any(e['type']=='audio' for e in self.events))
        await t.begin('b');new=t.active;await t.text('New sentence.');await t.finish()
        await t.output({'contextId':new,'audio':base64.b64encode(b'\2\0'*100).decode(),'isFinal':True})
        audio=[e for e in self.events if e['type']=='audio'];self.assertEqual(len(audio),1);self.assertEqual(audio[0]['epoch'],1)
    async def test_context_audio_is_ordered(self):
        t=self.tts;await t.begin('a');a=t.active;await t.text('One.');await t.finish();await t.begin('b');b=t.active;await t.text('Two.');await t.finish()
        await t.output({'context_id':b,'audio':base64.b64encode(b'\2\0').decode(),'is_final':True})
        self.assertFalse(any(e['type']=='audio' for e in self.events))
        await t.output({'contextId':a,'audio':base64.b64encode(b'\1\0').decode(),'isFinal':True})
        self.assertEqual([base64.b64decode(e['pcm']) for e in self.events if e['type']=='audio'],[b'\1\0',b'\2\0'])
    async def test_alignment_survives_browser_packet_boundaries(self):
        t=self.tts;await t.begin('a');context=t.active
        raw=b'\0\x20'*7200
        await t.output({'contextId':context,'audio':base64.b64encode(raw).decode(),
            'normalizedAlignment':{'chars':['m','a','f'],'charStartTimesMs':[0,60,160],'charDurationsMs':[60,100,140]}})
        audio=[e for e in self.events if e['type']=='audio']
        self.assertEqual(len(audio),3)
        self.assertEqual(b''.join(base64.b64decode(e['pcm']) for e in audio),raw)
        self.assertTrue(all(e['alignment_source']=='elevenlabs_characters_audio_gated' for e in audio))
        self.assertEqual([e['mouth_cues'][0]['viseme'] for e in audio],['MBP','AA','FV'])
        self.assertTrue(all(e['mouth_cues'][0]['time']==0 for e in audio))
    async def test_no_pcm_conversion_path(self):
        await self.tts.feed(b'\1\0'*16000)
        self.assertEqual(self.tts.socket.sent,[])
    async def test_old_context_error_does_not_cancel_new_reply(self):
        t=self.tts;await t.begin('a');old=t.active;await t.interrupt();await t.begin('b');new=t.active
        await t.output({'contextId':old,'error':'context already closed'})
        self.assertEqual(t.active,new);self.assertFalse(t.muted)
    async def test_empty_provider_reply_is_visible(self):
        t=self.tts;await t.begin('a');a=t.active;await t.text('Hello.');await t.finish();await t.output({'contextId':a,'isFinal':True})
        self.assertTrue(any(e.get('code')=='tts_empty_reply' for e in self.events))
    async def test_empty_provider_reply_explains_known_payment_blocker(self):
        class Response:
            status=200
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def json(self):return {'status':'past_due'}
        class Client:
            calls=0
            def get(self,*args,**kwargs):self.calls+=1;return Response()
        t=self.tts;t.client=Client()
        await t.begin('a');a=t.active;await t.text('Hello.');await t.finish()
        await t.output({'contextId':a,'isFinal':True})
        self.assertTrue(any(e.get('code')=='tts_payment_issue' for e in self.events))
        self.assertEqual(t.client.calls,1)
        self.assertTrue(t.muted)
    async def test_native_event_handlers_are_serial(self):
        order=[];gate=asyncio.Event()
        async def handler(event):
            order.append(('start',event['n']))
            if event['n']==1:await gate.wait()
            order.append(('end',event['n']))
        n=NativeVoice(None,'.',None,None,handler);task=asyncio.create_task(n.dispatch_events())
        n.event_queue.put_nowait({'type':'fixture','n':1});n.event_queue.put_nowait({'type':'fixture','n':2});await asyncio.sleep(.01)
        self.assertEqual(order,[('start',1)])
        gate.set();await asyncio.sleep(.01);task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(order,[('start',1),('end',1),('start',2),('end',2)])
    async def test_predictive_prefix_is_preserved_but_not_played_early(self):
        class Browser:
            closed=False
            async def send_json(self,event):pass
        with tempfile.TemporaryDirectory() as tmp:
            c=Conversation({'runtime':RobotRuntime(tmp),'speech_backend':'streaming-tts'},Browser())
            c.mask=self.tts
            await c.realtime_event({'type':'output_transcript.added','item':{'text':'The legs exist. '}})
            context=self.tts.active
            await self.tts.output({'contextId':context,'audio':base64.b64encode(b'\1\0'*100).decode()})
            self.assertFalse(any(e['type']=='audio' for e in self.events),'predictive text must not talk over the user')
            await c.realtime_event({'type':'turn.created','turn':{'role':'assistant','id':'a','start_ms':100}})
            self.assertTrue(any(e['type']=='audio' for e in self.events))
            await c.realtime_event({'type':'output_transcript.added','item':{'text':'Excellent.'}})
            await c.realtime_event({'type':'turn.done','turn':{'role':'assistant','id':'a','transcript':'The legs exist. Excellent.'}})
            self.assertEqual(''.join(e.get('text','') for e in self.tts.socket.sent).strip(),'The legs exist. Excellent.')

if __name__=='__main__':unittest.main(verbosity=2)
