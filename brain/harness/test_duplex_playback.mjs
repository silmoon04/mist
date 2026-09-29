import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../duplex/static/playback.js',import.meta.url),'utf8');
const {Playback}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const realSetTimeout=globalThis.setTimeout,realClearTimeout=globalThis.clearTimeout;
let timerId=0;const timers=new Map();
globalThis.setTimeout=(fn)=>{timers.set(++timerId,fn);return timerId;};
globalThis.clearTimeout=id=>timers.delete(id);
const pcm=Buffer.alloc(4800);for(let i=0;i<pcm.length;i+=2)pcm.writeInt16LE(7000,i);
const packet={epoch:0,seq:0,pcm:pcm.toString('base64'),sample_rate:24000,
  mouth_cues:[{time:0,viseme:'MBP',amount:.5},{time:.04,viseme:'AA',amount:.8}],alignment_source:'elevenlabs_characters_audio_gated'};
function fixture(){
  const made=[],speech=[],overflows=[];let stops=0,idle=0,lag=0,starts=0,buffers=0;
  const context={currentTime:0,state:'running',destination:{},
    createBuffer(channels,length,rate){buffers++;return{duration:length/rate,getChannelData(){return new Float32Array(length);}};},
    createBufferSource(){const s={connect(){},disconnect(){},start(at){this.startAt=at;},stop(){stops++;}};made.push(s);return s;}};
  const face={startSpeech(value){speech.push(value);},stopSpeech(){}};
  const player=new Playback(context,face,event=>{lag++;overflows.push(event);},()=>idle++,()=>starts++);
  return {player,context,made,speech,overflows,get counts(){return{stops,idle,lag,starts,buffers};}};
}
function tick(player){if(player.timer!==null)timers.delete(player.timer);player.tick();}
try {
  for(const seconds of [45,60]){
    const long=fixture(),chunks=seconds*10;
    for(let i=0;i<chunks;i++)long.player.append({...packet,caption_cues:[{time:0,text:`chunk ${i}`}],caption_source:'elevenlabs_alignment'});
    assert.equal(long.counts.lag,0,`${seconds}s of valid generated speech must not trigger an interruption`);
    assert.equal(long.made.length,chunks);assert.equal(long.speech.length,1);
    assert(Math.abs(long.player.end-(seconds+.2))<1e-7);
    for(let i=0;i<chunks;i++){
      long.context.currentTime=.2+i*.1+.075;tick(long.player);
      const heard=long.player.sample();assert.equal(heard.active,true);assert.equal(heard.viseme,'AA');
      assert.equal(heard.caption,`chunk ${i}`,'every queued caption survives until its output time');
    }
    assert.equal(long.counts.starts,1,'a long contiguous answer keeps one mouth timeline');
    long.context.currentTime=seconds+.201;tick(long.player);
    assert.equal(long.counts.idle,1);assert.equal(long.player.playing,false);
    assert.equal(long.player.sample().caption,`chunk ${chunks-1}`,'the final spoken caption is retained');long.player.reset();
  }
  const interruptedLong=fixture();
  for(let i=0;i<450;i++)interruptedLong.player.append({...packet,caption_cues:[{time:0,text:`word ${i}`}]});
  interruptedLong.context.currentTime=.23;tick(interruptedLong.player);interruptedLong.player.reset(1);
  assert.equal(interruptedLong.counts.stops,450);assert.equal(interruptedLong.player.sources.size,0);
  assert.equal(interruptedLong.player.segments.length,0);assert.equal(interruptedLong.player.timer,null);
  assert.equal(interruptedLong.player.sample().active,false);assert.equal(interruptedLong.player.sample().caption,'');
  interruptedLong.player.append(packet);assert.equal(interruptedLong.player.playing,false,'late packets cannot restore an interrupted long answer');
  const limited=fixture(),tenSeconds={...packet,pcm:Buffer.alloc(24000*2*10).toString('base64')};
  for(let i=0;i<11;i++)limited.player.append(tenSeconds);
  assert.equal(limited.counts.buffers,11);assert.equal(limited.counts.lag,0);
  limited.player.append(tenSeconds);
  assert.equal(limited.counts.buffers,11,'the incoming packet is included in the limit before its audio allocation');
  assert.equal(limited.counts.lag,1);assert.equal(limited.counts.stops,11);assert.equal(limited.player.playing,false);
  assert.equal(limited.overflows[0].reason,'playback_buffer_limit');assert.equal(limited.overflows[0].limit_ms,120000);
  assert.equal(limited.overflows[0].incoming_ms,10000);assert(limited.overflows[0].projected_ms>120000);
  limited.player.append(packet);assert.equal(limited.player.playing,false,'overflow blocks the rejected epoch until the server catches up');
  const oversized=fixture();oversized.player.append({...packet,sample_rate:8000,pcm:Buffer.alloc(8000*2*121).toString('base64')});
  assert.equal(oversized.counts.buffers,0,'a single oversized packet must be rejected before audio allocation');
  assert.equal(oversized.counts.lag,1);
  const defaultBuffer=fixture();defaultBuffer.player.append(packet);
  assert.equal(defaultBuffer.made[0].startAt,.2,'existing sessions retain their 200 ms initial reserve');
  defaultBuffer.player.reset();
  const quick=fixture();
  assert.equal(quick.player.setStartBufferMs(120),120);
  for(const bad of [119,401,120.5,'120',null,undefined,NaN,Infinity,true]){
    assert.throws(()=>quick.player.setStartBufferMs(bad),/120.*400/,'invalid session settings must not be coerced or clamped');
  }
  assert.equal(quick.player.sample().playback_buffer_ms,120,'snapshot exposes the effective setting even while idle');
  for(const arrival of [0,.045,.132,.211,.30]){
    quick.context.currentTime=arrival;quick.player.append(packet);tick(quick.player);
  }
  assert.equal(quick.made[0].startAt,.12,'only the selected session gets the smaller reserve');
  for(let i=1;i<quick.made.length;i++)assert(Math.abs(quick.made[i].startAt-quick.made[i-1].startAt-.1)<1e-8,'jittered delivery remains contiguous');
  assert.equal(quick.speech.length,1,'jitter must not restart the mouth timeline');
  assert.equal(quick.counts.starts,1,'one sequence emits one start across queued packets');
  quick.context.currentTime=.345;assert.equal(quick.player.sample().viseme,'MBP');
  quick.context.currentTime=.37;assert.equal(quick.player.sample().viseme,'AA','cue offsets remain relative to each unmodified audio chunk');
  quick.context.state='suspended';tick(quick.player);
  assert.equal(quick.player.sample().active,false);assert.equal(quick.player.sample().playback_buffer_ms,120);
  quick.context.state='running';tick(quick.player);
  assert.equal(quick.player.sample().viseme,'AA');assert.equal(quick.counts.starts,1,'resume does not restart a contiguous sequence');
  quick.player.reset(1);assert.equal(quick.counts.stops,5);
  quick.player.append(packet);assert.equal(quick.player.playing,false,'interrupted packets stay cancelled');
  quick.context.currentTime=.4;quick.player.append({...packet,epoch:1});
  assert.equal(quick.made.at(-1).startAt,.52,'the session setting survives interruption');
  quick.player.reset();quick.player.setStartBufferMs(400);
  quick.player.append({...packet,epoch:2});assert.equal(quick.made.at(-1).startAt,.8,'upper bound is supported');quick.player.reset();
  assert.equal(fixture().player.sample().playback_buffer_ms,200,'new sessions do not inherit a prior session setting');
  const captions=fixture();
  captions.player.append({...packet,caption_cues:[{time:0,text:'M'},{time:.04,text:'Maybe'}],caption_source:'elevenlabs_normalized_alignment'});
  assert.equal(captions.player.sample().caption,'','generated captions must not appear during the startup buffer');
  captions.context.currentTime=.219;
  assert.equal(captions.player.sample().caption,'M','caption must advance at the same output time as the mouth');
  captions.context.currentTime=.25;
  assert.equal(captions.player.sample().caption,'Maybe');
  captions.player.reset(1);
  assert.equal(captions.player.sample().caption,'','interruption must immediately clear the spoken caption');
  const captionClock=fixture(),captionChanges=[];
  captionClock.player.onCaption=value=>captionChanges.push(value);
  captionClock.context.baseLatency=.08;
  captionClock.player.append({...packet,caption_cues:[{time:0,text:'One'},{time:.06,text:'One two'}],caption_source:'elevenlabs_alignment'});
  captionClock.context.currentTime=.25;tick(captionClock.player);
  assert.equal(captionChanges.length,0,'caption cannot use the render-ahead audio clock');
  captionClock.context.currentTime=.30;tick(captionClock.player);
  assert.equal(captionChanges.at(-1).text,'One');
  captionClock.context.state='suspended';captionClock.context.currentTime=.37;tick(captionClock.player);
  assert.equal(captionClock.player.sample().caption,'One','suspension freezes the spoken prefix');
  captionClock.context.state='running';tick(captionClock.player);
  assert.equal(captionChanges.at(-1).text,'One two');
  const unchangedCount=captionChanges.length;captionClock.player.sample();
  assert.equal(captionChanges.length,unchangedCount,'unchanged caption does not trigger another callback');
  captionClock.context.currentTime=.40;tick(captionClock.player);
  assert.equal(captionClock.player.sample().caption,'One two','last spoken caption survives packet retirement');
  captionClock.player.reset(1);assert.equal(captionChanges.at(-1).text,'');
  captionClock.player.append({...packet,caption_cues:[{time:0,text:'Stale'}]});
  assert.equal(captionClock.player.sample().caption,'','cancelled generation cannot restore a caption');
  const f=fixture(),p=f.player;
  p.append(packet);assert(p.playing);assert.equal(f.speech.length,1);
  assert.equal(p.sample().active,false,'mouth must be inactive during startup buffer');
  f.context.currentTime=.219;tick(p);assert.equal(p.sample().viseme,'MBP');assert.equal(f.counts.starts,1);
  f.context.currentTime=.25;assert.equal(p.sample().viseme,'AA');
  const oldEnd=f.made[0].onended;
  p.reset(1);assert.equal(f.counts.stops,1);assert.equal(p.sample().active,false);
  p.append(packet);assert(!p.playing,'old generation cannot revive audio');
  p.append({...packet,epoch:1});oldEnd();assert(p.playing,'old onended cannot clear new audio');p.reset();

  const burst=fixture();
  for(let i=0;i<100;i++)burst.player.append(packet);
  assert.equal(burst.made[0].startAt,.2);
  for(let i=1;i<100;i++)assert(Math.abs(burst.made[i].startAt-burst.made[i-1].startAt-.1)<1e-8);
  assert.equal(burst.speech.length,1,'packet delivery must not restart the mouth attack');
  burst.context.currentTime=.299999;assert.equal(burst.player.sample().viseme,'AA');
  burst.context.currentTime=.300001;assert.equal(burst.player.sample().viseme,'MBP');
  burst.player.reset();

  const output=fixture();output.context.outputLatency=.07;output.context.baseLatency=.01;output.player.append(packet);
  output.context.currentTime=.25;assert.equal(output.player.sample().active,false,'device latency must delay the mouth too');
  output.context.currentTime=.299;tick(output.player);assert.equal(output.player.sample().viseme,'MBP');
  output.made[0].onended();assert(output.player.playing,'render completion must preserve the audible tail');
  output.context.currentTime=.35;tick(output.player);assert.equal(output.player.sample().viseme,'AA');assert.equal(output.counts.idle,0);
  output.context.currentTime=.39;tick(output.player);assert.equal(output.counts.idle,1);assert(!output.player.playing);
  output.player.reset();

  const suspended=fixture();suspended.player.append(packet);suspended.context.currentTime=.26;suspended.context.state='suspended';tick(suspended.player);
  assert.equal(suspended.player.sample().active,false);assert.equal(suspended.counts.starts,0);assert(suspended.player.playing);
  suspended.context.state='running';tick(suspended.player);assert.equal(suspended.player.sample().viseme,'AA');assert.equal(suspended.counts.starts,1);
  suspended.context.currentTime=.32;tick(suspended.player);assert.equal(suspended.counts.idle,1);
  suspended.player.append(packet);assert.equal(suspended.player.sample().active,false,'underrun reserve must close the mouth');
  suspended.context.currentTime=.54;tick(suspended.player);assert.equal(suspended.counts.starts,2,'same reply resumes after underrun');suspended.player.reset();

  const stamped=fixture();stamped.context.currentTime=.4;stamped.player.append(packet);
  stamped.context.currentTime=.75;stamped.context.getOutputTimestamp=()=>({contextTime:.619,performanceTime:performance.now()});
  assert.equal(stamped.player.sample().viseme,'MBP');assert.equal(stamped.player.clockSource,'output_timestamp');
  stamped.context.getOutputTimestamp=()=>({contextTime:.3,performanceTime:performance.now()-3000});
  assert.equal(stamped.player.sample().active,false,'stale output stamps must not freeze old mouth poses');stamped.player.reset();

  const energy=fixture();energy.player.append({...packet,mouth_cues:undefined,alignment_source:undefined});
  energy.context.currentTime=.23;assert.equal(energy.player.sample().source,'audio_energy');assert.equal(energy.player.sample().viseme,'AA');energy.player.reset();
  energy.player.append({...packet,epoch:1,pcm:Buffer.alloc(4800).toString('base64'),mouth_cues:undefined});
  energy.context.currentTime=.46;assert.equal(energy.player.sample().viseme,'rest');energy.player.reset();
  assert.throws(()=>energy.player.append({...packet,epoch:2,sample_rate:0}),/sample rate/);
  assert.throws(()=>energy.player.append({...packet,epoch:2,pcm:'AA=='}),/Invalid PCM/);
  const dental=fixture();
  dental.player.append({...packet,mouth_cues:[{time:0,viseme:'TH',amount:.7}]});
  dental.context.currentTime=.23;
  assert.equal(dental.player.sample().viseme,'TH','live player must preserve the distinct dental cue');
  assert.equal(dental.player.sample().source,'elevenlabs_characters_audio_gated');
  assert.equal(dental.speech.length,1);dental.player.reset();
  const published=await readFile(new URL('../art_direction/artist_studio_20260916/reuse/runtime/playback.js',import.meta.url),'utf8');
  const {Playback:PublishedPlayback}=await import('data:text/javascript;base64,'+Buffer.from(published).toString('base64'));
  const publishedDental=new PublishedPlayback(dental.context,{startSpeech(){},stopSpeech(){}});
  publishedDental.append({...packet,mouth_cues:[{time:0,viseme:'TH',amount:.7}]});
  dental.context.currentTime=.46;
  assert.equal(publishedDental.sample().viseme,'TH','published preview player must also preserve TH');publishedDental.reset();
  console.log('PASS: complete 45/60-second bursts, single mouth timeline and captions, pre-allocation 120-second bound, long-answer interruption, session buffers, jitter, output latency/tail, suspension and stale packets.');
} finally {globalThis.setTimeout=realSetTimeout;globalThis.clearTimeout=realClearTimeout;}
