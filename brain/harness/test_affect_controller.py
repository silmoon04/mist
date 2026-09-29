"""The director must neither serialize speech nor apply old decisions."""
import asyncio
from pathlib import Path
import sys
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from duplex.affect_controller import AffectController
from duplex.trial_traces import _Cleaner


class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_does_not_wait_and_busy_input_does_not_start_second_reader(self):
        gate=threading.Event();events=[];applied=[]
        class Reader:
            model='fixture';calls=0
            def decide_with_trace(self,snapshot):
                self.calls+=1;gate.wait(2)
                return {'change':True},{'status':'accepted'}
        async def emit(value):events.append(value)
        async def apply(decision,token):applied.append(token);return 'applied'
        reader=Reader();c=AffectController(reader,emit,apply)
        await c.submit({},1)
        await c.submit({},2)
        self.assertTrue(any(e['phase']=='skipped_busy' for e in events))
        gate.set();await c.task
        self.assertEqual(reader.calls,1);self.assertEqual(applied,[1])
        await c.close()

    async def test_current_owner_rejects_stale_result_and_close_suppresses_output(self):
        events=[];applied=[]
        class Reader:
            model='fixture'
            def decide_with_trace(self,snapshot):return {'change':True},{'status':'accepted'}
        async def emit(value):events.append(value)
        async def apply(decision,token):
            if token!=2:return 'stale'
            applied.append(token);return 'applied'
        c=AffectController(Reader(),emit,apply)
        await c.submit({},1);await c.task
        self.assertEqual(events[-1]['outcome'],'stale');self.assertFalse(applied)
        self.assertEqual(_Cleaner().clean(events[-1])['revision_context'], 1)
        await c.submit({},2);await c.close()
        self.assertFalse(applied)

    async def test_invalid_proposal_never_reaches_executor(self):
        class Reader:
            model='fixture'
            def decide_with_trace(self,snapshot):return {},{'status':'invalid_decision'}
        async def emit(value):pass
        async def apply(*args):self.fail('No invalid proposal should execute')
        c=AffectController(Reader(),emit,apply)
        await c.submit({},1);await c.task;await c.close()


if __name__=='__main__':unittest.main()
