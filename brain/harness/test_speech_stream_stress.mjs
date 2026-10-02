import assert from 'node:assert/strict';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {SpeechStatus} from '../duplex/static/speech_status.mjs';
const source=await readFile(new URL('../duplex/static/playback.js',import.meta.url),'utf8');
const {Playback}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const originals={setTimeout,clearTimeout};let timer=0;
globalThis.setTimeout=()=>++timer;globalThis.clearTimeout=()=>{};
const failures=[],counts={};
const verifyFailureExit=process.argv.includes('--verify-failure-exit');
function fixture(seed){
  let wall=0,randomState=seed;
  const random=()=>{randomState=(Math.imul(randomState,1664525)+1013904223)>>>0;return randomState/4294967296;};
  const sources=[];
  const context={currentTime:0,state:'running',outputLatency:random()*.06,baseLatency:.01,destination:{},
    createBuffer(c,n,r){const data=new Float32Array(n);return {duration:n/r,getChannelData:()=>data};},
    createBufferSource(){const value={connect(){},disconnect(){},start(at){this.at=at;},stop(){this.stopped=true;}};sources.push(value);return value;}};
  const player=new Playback(context,{startSpeech(){},stopSpeech(){}});
  const status=new SpeechStatus({now:()=>wall});
  const advance=(seconds,paused=false)=>{wall+=seconds*1000;context.state=paused?'suspended':'running';if(!paused)context.currentTime+=seconds;for(const s of sources)if(!s.stopped&&s.buffer&&context.currentTime>=s.at+s.buffer.duration)s.onended?.();player.tick();return sample();};
  const sample=()=>{const audio=player.sample();const delivery=status.sample({audible:audio.active&&audio.amount>0,queued:player.playing,paused:context.state==='suspended'});return {audio,delivery};};
  const packet=(seq,silent=false)=>({epoch:player.epoch,seq,sample_rate:24000,pcm:Buffer.alloc(2400,silent?0:25).toString('base64'),caption_cues:[{time:0,text:`seq ${seq}`}],caption_source:'alignment'});
  const append=event=>{player.append(event);if(event.epoch===player.epoch&&player.segments.some(s=>s.event===event))status.audio(event);};
  const complete=event=>{player.completeCaption(event);status.complete(event);};
  return {player,status,context,sources,random,advance,sample,packet,append,complete};
}
function run(category,seed,body){counts[category]=(counts[category]||0)+1;const f=fixture(seed);try{body(f);}catch(error){failures.push({category,seed,error:error.message});}finally{f.player.reset();}}
try{
  if(verifyFailureExit)run('intentional_failure_exit_probe',0,()=>assert.fail('Intentional failure to verify nonzero exit status'));
  for(let seed=1;seed<=1200;seed++){
    run('jitter_ordered_sequences_completion_tail',seed,f=>{
      const sequences=1+Math.floor(f.random()*5),firstStarts=new Map();
      for(let seq=0;seq<sequences;seq++){
        for(let n=0;n<1+Math.floor(f.random()*6);n++){
          const event=f.packet(seq,n%3===0);f.append(event);if(!firstStarts.has(seq))firstStarts.set(seq,f.player.segments.at(-1).start);
          f.advance(f.random()*.13,f.random()<.15);
          const s=f.sample(),at=f.player.outputTime();
          const segment=f.player.segments.find(s=>at>=s.start&&at<s.end);
          if(segment&&segment.event.pcm===Buffer.alloc(2400).toString('base64'))assert.equal(s.audio.amount,0,'silent PCM opens mouth');
          assert.ok(f.player.end-f.context.currentTime<=120,'queued duration exceeds bound');
          if(at<firstStarts.get(seq))assert.notEqual(s.audio.caption,`seq ${seq}`,'caption precedes output');
        }
        const done={epoch:f.player.epoch,seq,text:`final ${seq}`};f.complete(done);f.complete(done);
        f.complete({...done,epoch:done.epoch-1,text:'stale'});
      }
      f.advance(15);assert.equal(f.player.playing,false);assert.equal(f.sample().delivery.busy,false,'all complete streams wait forever');assert.equal(f.player.sources.size,0);
    });
    run('true_reset_stop_stale_replay',seed,f=>{
      const old=f.packet(0);f.append(old);f.advance(f.random());f.player.reset();f.status.reset(f.player.epoch);f.append(old);
      assert.equal(f.player.playing,false);assert.equal(f.sample().audio.caption,'');assert.equal(f.sample().delivery.busy,false);
      f.append({...f.packet(1),pcm:''});assert.equal(f.player.playing,false);assert.equal(f.sample().delivery.busy,false);
    });
  }
  run('unknown_completion_memory',2001,f=>{for(let seq=0;seq<1000;seq++)f.complete({epoch:0,seq,text:'unknown'});assert.equal(f.player.finalCaptions.size,0,'unknown finals retained without audio ownership');});
  run('throttled_timer_skips_sequence_tail',2005,f=>{f.append(f.packet(0));f.complete({epoch:0,seq:0,text:'final zero'});f.append(f.packet(1));f.complete({epoch:0,seq:1,text:'final one'});f.advance(3);assert.equal(f.sample().audio.caption,'final one');assert.equal(f.player.finalCaptions.size,0,'skipped earlier completed sequence retains final forever');});
  run('invalid_sequence_rejection',2002,f=>{f.append({...f.packet(0),seq:'wrong'});assert.equal(f.player.playing,false,'noninteger sequence schedules audio');});
  run('bounded_queue_overflow',2004,f=>{const large={...f.packet(0),pcm:Buffer.alloc(480000).toString('base64')};for(let n=0;n<13;n++)f.append(large);assert.equal(f.player.playing,false);assert.equal(f.player.sources.size,0);assert.ok(f.player.epoch>large.epoch);f.status.reset(f.player.epoch);assert.equal(f.sample().delivery.busy,false);});
  run('output_timestamp_tail',2003,f=>{f.context.getOutputTimestamp=()=>({contextTime:Math.max(0,f.context.currentTime-.09),performanceTime:performance.now()});f.append(f.packet(0));f.complete({epoch:0,seq:0,text:'final'});f.advance(.26);assert.notEqual(f.sample().audio.caption,'final','final precedes audible tail');f.advance(.2);assert.equal(f.sample().audio.caption,'final');assert.equal(f.sample().delivery.busy,false);});
}finally{globalThis.setTimeout=originals.setTimeout;globalThis.clearTimeout=originals.clearTimeout;}
const report={schedules:Object.values(counts).reduce((a,b)=>a+b,0),counts,failures};
const directory=new URL('../results/speech-continuity-20261002/more-checks/',import.meta.url);
if(!verifyFailureExit){await mkdir(directory,{recursive:true});await writeFile(new URL('seeded-stream-stress.json',directory),JSON.stringify(report,null,2)+'\n');}
console.log(JSON.stringify({...report,failures:failures.slice(0,8),failureCount:failures.length},null,2));if(failures.length)process.exitCode=1;
