"""Ordered converted PCM completion; no provider or speech transcript fixture."""
import asyncio
from collections import deque
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from duplex.conversion import VoiceMask

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_finalized_second_passage_waits_for_first_then_completes_after_pcm(self):
        events=[]
        async def emit(event):events.append(event)
        mask=VoiceMask('fixture',emit)
        mask.pending={0:{'epoch':0,'chunks':deque([b'\0\0'*2400]),'done':False},
                      1:{'epoch':0,'chunks':deque([b'\0\0'*2400]),'done':True}}
        await mask.drain()
        self.assertEqual([(e['type'],e['seq']) for e in events],[('audio',0)])
        mask.pending[0]['done']=True
        await mask.drain()
        self.assertEqual([(e['type'],e['seq']) for e in events],
                         [('audio',0),('audio_complete',0),('audio',1),('audio_complete',1)])
        self.assertTrue(all('text' not in e for e in events),'completion never fabricates captions')

    async def test_interrupt_while_emitting_does_not_complete_stale_passage(self):
        events=[]
        async def emit(event):
            events.append(event)
            if event['type']=='audio':mask.epoch=1
        mask=VoiceMask('fixture',emit)
        mask.pending={0:{'epoch':0,'chunks':deque([b'\0\0'*2400]),'done':True}}
        await mask.drain()
        self.assertEqual([e['type'] for e in events],['audio'])

if __name__=='__main__':unittest.main()
