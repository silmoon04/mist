import assert from 'node:assert/strict';
import {SpeechStatus} from './speech_status.mjs';
let now=0;const status=new SpeechStatus({now:()=>now});
status.audio({epoch:0,seq:0});
assert.equal(status.sample({queued:true}).label,'Preparing voice');
assert.equal(status.sample({audible:true,queued:true,fallback:'checking'}).state,'speaking');
for(now of [90,190,290,390]){
 const shot=status.sample({fallback:'listening'});
 assert.equal(shot.label,'Speaking, still listening','brief PCM gaps cannot flip the foreground to listening');
 assert.equal(shot.busy,true,'background results must not preempt an unfinished reply');
}
now=800;assert.equal(status.sample({fallback:'thinking'}).label,'Waiting for speech','a real stall is visible, without a false thinking animation');
assert.equal(status.sample({paused:true}).label,'Audio paused');
status.complete({epoch:0,seq:0});
assert.equal(status.sample({queued:true}).busy,true,'provider completion cannot discard queued speaker output');
assert.equal(status.sample({fallback:'listening'}).busy,false,'completed audible tail releases the floor');
status.audio({epoch:0,seq:1});status.sample({audible:true,queued:true});
status.reset(1);assert.equal(status.sample().label,'Listening','real interruption releases immediately');
status.audio({epoch:0,seq:1});assert.equal(status.sample().busy,false,'late interrupted packets cannot reacquire the floor');
status.audio({epoch:1,seq:0});status.complete({epoch:0,seq:0});
assert.equal(status.sample().busy,true,'stale completion cannot finish a newer reply');
status.reset(0);for(let i=0;i<27;i++){
 now=i*300;status.audio({epoch:0,seq:0});status.sample({audible:true,queued:true});now+=100;
 assert.equal(status.sample({fallback:i%2?'listening':'checking'}).state,'speaking');
}
console.log('PASS: same-reply gaps, background state precedence, stalled voice, paused output, final tail and immediate true interruption.');
