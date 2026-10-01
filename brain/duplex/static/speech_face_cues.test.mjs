import {test} from 'node:test';
import assert from 'node:assert/strict';
import {SpeechFaceCues} from './speech_face_cues.mjs';

test('a queued face waits for its turn and audible words',()=>{
  let now=0;const shown=[];const cues=new SpeechFaceCues({apply:cue=>shown.push(cue.expression),now:()=>now});
  cues.enqueue({epoch:0,turn_id:'a',cue_index:0,expression:'sad',character_offset:10});
  cues.progress({epoch:0,turn_id:'a',caption:'1234567890',active:false});
  cues.progress({epoch:0,turn_id:'other',caption:'1234567890',active:true});
  cues.progress({epoch:0,turn_id:'a',caption:'123',active:true});
  assert.deepEqual(shown,[]);
  cues.progress({epoch:0,turn_id:'a',caption:'1234567890',active:true});assert.deepEqual(shown,['sad']);
  cues.progress({epoch:0,turn_id:'a',caption:'1234567890',active:true});assert.equal(shown.length,1);
});
test('interrupt removes old cues and manual previews stay in charge',()=>{
  const shown=[];const cues=new SpeechFaceCues({apply:cue=>shown.push(cue),manual:()=>true});
  cues.enqueue({epoch:0,turn_id:'a',cue_index:0,expression:'sad'});
  cues.progress({epoch:0,turn_id:'a',active:true});assert.equal(shown.length,0);
  cues.reset(1);assert.equal(cues.snapshot().pending,0);
  assert.equal(cues.enqueue({epoch:0,turn_id:'a',cue_index:1,expression:'happy'}),false);
});
test('ten faces have bounded spacing and never overwrite in one tick',()=>{
  let now=0;const shown=[];const cues=new SpeechFaceCues({apply:cue=>shown.push(cue.cue_index),now:()=>now});
  for(let n=0;n<10;n++)cues.enqueue({epoch:0,turn_id:'a',cue_index:n,character_offset:n*10,expression:'happy'});
  for(let n=0;n<10;n++){cues.progress({epoch:0,turn_id:'a',caption:'x'.repeat(n*10),active:true});now+=500;}
  assert.deepEqual(shown,[0,1,2,3,4,5,6,7,8,9]);
});
test('successful silent visual sequence has its own dwell and is cancelled on interruption',()=>{
  let now=0;const shown=[];const cues=new SpeechFaceCues({apply:cue=>shown.push(cue.expression),now:()=>now});
  const first={epoch:0,turn_id:'a',cue_index:0,expression:'sad',duration_ms:2000};
  const second={...first,cue_index:1,expression:'happy'};
  cues.enqueue(first);cues.enqueue(second);
  assert.equal(cues.startVisual({epoch:0,turn_id:'a',visual_only:true,cues:[first,second]}),true);
  assert.deepEqual(shown,['sad']);assert.equal(cues.snapshot().pending,0);
  now=1999;cues.advanceVisual();assert.equal(shown.length,1);
  now=2000;cues.advanceVisual();assert.deepEqual(shown,['sad','happy']);
  cues.reset(1);now=5000;cues.advanceVisual();assert.equal(cues.snapshot().visual_only,false);
});
