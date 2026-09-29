"""A current, timely director proposal shares the studio's face dispatcher."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from duplex.live_studio import TrialConversation,ARCHITECTURES

class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.events=[]
        target=self.events
        class Socket:
            closed=False
            async def send_json(self,value):target.append(value)
        journal=SimpleNamespace(trace_id='fixture',record=lambda *a:None)
        config=next(a for a in ARCHITECTURES if a['id']=='qwen-affect')
        self.c=TrialConversation({'run_dir':Path(self.tmp.name),'speech_backend':'streaming-tts'},Socket(),config,journal)
        self.c.mask=SimpleNamespace(epoch=0)
        self.c.user_request_text='I had a difficult day.'
        self.decision={'change':True,'expression':'curious','variant':0,'delivery':'gentle','evidence':[]}

    async def test_timely_proposal_changes_persistent_face_and_delivery(self):
        result=await self.c.apply_affect(self.decision,self.c.affect_token())
        self.assertEqual(result,'applied_before_speech')
        self.assertEqual(self.c.delivery,'gentle')
        self.assertTrue(self.events[-1]['persistent'])
        self.assertEqual(self.events[-1]['source'],'director')

    async def test_earlier_user_revision_and_manual_replacement_win(self):
        token=self.c.affect_token();self.c.user_revision+=1
        self.assertEqual(await self.c.apply_affect(self.decision,token),'stale_or_replaced')
        token=self.c.affect_token()
        await self.c.handle({'type':'expression','expression':'sad'})
        before=len(self.events)
        self.assertEqual(await self.c.apply_affect(self.decision,token),'stale_or_replaced')
        self.assertEqual(len(self.events),before)
        self.assertEqual(self.c.current_face['expression'],'sad')

    async def test_first_speech_locks_delivery_and_does_not_apply_late_face(self):
        self.c.affect_speech_locked=True
        self.assertEqual(await self.c.apply_affect(self.decision,self.c.affect_token()),'speech_already_started')
        self.assertEqual(self.c.delivery,'neutral');self.assertFalse(self.events)

    async def test_current_keep_face_blocks_change_but_can_accept_gentle_voice(self):
        self.c.user_request_text='Keep the face unchanged and speak gently.'
        self.assertEqual(await self.c.apply_affect(self.decision,self.c.affect_token()),'request_constraint')
        decision={**self.decision,'change':False,'expression':'neutral'}
        self.assertEqual(await self.c.apply_affect(decision,self.c.affect_token()),'applied_before_speech')
        self.assertFalse(self.events);self.assertEqual(self.c.delivery,'gentle')

    async def test_new_user_turn_resets_previous_delivery(self):
        self.c.delivery='gentle'
        self.c.mask.diagnostic=lambda *args,**kwargs:None
        await self.c.realtime_event({'type':'turn.created','turn':{'id':'user-2','role':'user','start_ms':1}})
        self.assertEqual(self.c.delivery,'neutral')

    async def test_unverified_generated_reply_is_not_public_affect_history(self):
        captured=[]
        class Affect:
            async def submit(self,snapshot,token):captured.append(snapshot)
        self.c.affect=Affect()
        self.c.voice=SimpleNamespace(history=[
            {'role':'assistant_generated','text':'Unheard private words','playback_verified':False},
            {'role':'user','text':'A previous user turn'}])
        await self.c.realtime_event({'type':'turn.done','turn':{'role':'user','transcript':'Now answer me.'}})
        self.assertEqual(len(captured),1)
        self.assertNotIn('Unheard private words',[record['text'] for record in captured[0]['records']])

    async def test_timed_face_uses_persistent_base_and_holds_reader_face_changes_until_ack(self):
        await self.c.emit({'type':'face','expression':'sad','variant':0,'duration_ms':2200,
                           'persistent':True,'source':'model','epoch':0,
                           'expression_revision':self.c.expression_revision})
        await self.c.emit({'type':'face','expression':'happy','variant':0,'duration_ms':400,
                           'persistent':False,'source':'model','epoch':0,
                           'expression_revision':self.c.expression_revision})
        self.assertEqual(self.c.current_face,{'expression':'sad','variant':0})
        captured=[]
        class Affect:
            async def submit(self,snapshot,token):captured.append(snapshot)
        self.c.affect=Affect()
        self.c.voice=SimpleNamespace(history=[])
        await self.c.realtime_event({'type':'turn.done','turn':{'role':'user','transcript':'Hello again.'}})
        self.assertEqual(captured[0]['current_face'],{'expression':'sad','variant':0})
        self.assertTrue(captured[0]['face_override'])
        decision={**self.decision,'delivery':'gentle'}
        self.assertEqual(await self.c.apply_affect(decision,self.c.affect_token()),'temporary_face_active')
        delivery_only={**decision,'change':False,'expression':'sad'}
        self.assertEqual(await self.c.apply_affect(delivery_only,self.c.affect_token()),'applied_before_speech')
        self.assertEqual(self.c.delivery,'gentle')
        base_face=self.c.runtime.call('set_expression',{'expression':'sad'})['face_id']
        await self.c.handle({'type':'debug_client','event':{'type':'expression','phase':'transition',
            'to':base_face,'epoch':1,'expression_revision':self.c.expression_revision,
            'face_serial':self.c.face_serial}})
        self.assertEqual(await self.c.apply_affect(decision,self.c.affect_token()),'temporary_face_active')
        await self.c.handle({'type':'debug_client','event':{'type':'expression','phase':'transition',
            'to':base_face,'epoch':0,'expression_revision':self.c.expression_revision,
            'face_serial':self.c.face_serial-1}})
        self.assertEqual(await self.c.apply_affect(decision,self.c.affect_token()),'temporary_face_active')
        await self.c.handle({'type':'debug_client','event':{'type':'expression','phase':'transition',
            'to':base_face,'epoch':0,'expression_revision':self.c.expression_revision,
            'face_serial':self.c.face_serial}})
        self.assertEqual(await self.c.apply_affect(decision,self.c.affect_token()),'applied_before_speech')

    async def test_audio_reset_releases_temporary_face_hold_and_invalidates_old_token(self):
        await self.c.emit({'type':'face','expression':'happy','variant':0,'duration_ms':400,
                           'persistent':False,'source':'model','epoch':0,
                           'expression_revision':self.c.expression_revision})
        token=self.c.affect_token()
        self.assertIsNotNone(self.c.temporary_face)
        await self.c.emit({'type':'audio_reset','epoch':1})
        self.assertIsNone(self.c.temporary_face)
        self.assertNotEqual(token,self.c.affect_token())
        self.assertEqual(self.c.current_face,{'expression':'neutral','variant':0})

    async def test_timed_flash_to_existing_base_does_not_wait_for_missing_transition(self):
        await self.c.emit({'type':'face','expression':'sad','variant':0,'duration_ms':2200,
                           'persistent':True,'source':'model','epoch':0,
                           'expression_revision':self.c.expression_revision})
        await self.c.emit({'type':'face','expression':'sad','variant':0,'duration_ms':400,
                           'persistent':False,'source':'model','epoch':0,
                           'expression_revision':self.c.expression_revision})
        self.assertIsNone(self.c.temporary_face)
        self.assertEqual(self.c.current_face,{'expression':'sad','variant':0})

    async def test_reply_creation_keeps_director_window_open_until_tts_commits(self):
        chosen=[]
        async def begin(*args,**kwargs):pass
        self.c.mask=SimpleNamespace(epoch=0,muted=False,begin=begin,active='context',diagnostic=lambda *args,**kw:None,
                                    set_delivery=lambda value:chosen.append(value) or True)
        await self.c.realtime_event({'type':'turn.created','turn':{'id':'a-1','role':'assistant'}})
        self.assertEqual(await self.c.apply_affect(self.decision,self.c.affect_token()),'applied_before_speech')
        self.assertEqual(chosen,['gentle'])
        await self.c.emit({'type':'speech_style','phase':'committed','turn_id':'a-1','delivery':'gentle','epoch':0})
        self.assertEqual(await self.c.apply_affect(self.decision,self.c.affect_token()),'speech_already_started')

    async def test_initialized_tts_rejects_director_before_face_is_changed(self):
        self.c.mask=SimpleNamespace(epoch=0,active='context',set_delivery=lambda value:False)
        self.assertEqual(await self.c.apply_affect(self.decision,self.c.affect_token()),'speech_already_started')
        self.assertEqual(self.c.current_face,{'expression':'neutral','variant':0})
        self.assertEqual(self.c.delivery,'neutral')

    async def test_old_speech_style_cannot_lock_new_user_turn(self):
        self.c.mask=SimpleNamespace(epoch=2)
        self.c.assistant_turn='new'
        await self.c.emit({'type':'speech_style','phase':'committed','turn_id':'old','delivery':'neutral','epoch':1})
        self.assertFalse(self.c.affect_speech_locked)

if __name__=='__main__':unittest.main()
