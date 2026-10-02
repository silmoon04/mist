import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const load=source=>import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const policy=await load(await readFile(new URL('../duplex/static/expression_policy.js',import.meta.url),'utf8'));
const playback=await load(await readFile(new URL('../duplex/static/playback.js',import.meta.url),'utf8'));
const delivery=await load(await readFile(new URL('../duplex/static/delivery.js',import.meta.url),'utf8'));
const activity=await load(await readFile(new URL('../duplex/static/activity_state.js',import.meta.url),'utf8'));
const dependencies={};
for(const file of ['speech_status.mjs','user_transcript_state.mjs','listener_cue.js','microphone_signal.mjs','animation_picker.mjs','speech_face_cues.mjs'])
  Object.assign(dependencies,await load(await readFile(new URL(`../duplex/static/${file}`,import.meta.url),'utf8')));
const map=JSON.parse(await readFile(new URL('../duplex/face_map.json',import.meta.url),'utf8'));
let source=await readFile(new URL('../duplex/static/app.js',import.meta.url),'utf8');
source=source.replace(/^import .*;\r?\n/gm,'');
source='const {Playback,ExpressionPolicy,replyExpression,ActivityState,SpeechStatus,UserTranscriptState,newListenerCue,MicrophoneSignal,AnimationPreview,VisiblePreviewScheduler,installAnimationPicker,SpeechFaceCues}=globalThis.__expressionTest;\n'+source;
source+='\nexport {receive,start,stopPlayback,expressionPolicy,activityState,renderExpression,end,speechStatus}; export const testPlayer=()=>player;';
const elements=new Map(),faces=[],sent=[],faceEvents=[];
let createdElements=0;
const element=id=>{
  if(!elements.has(id))elements.set(id,{attrs:{},dataset:{},classList:{add(){},remove(){},toggle(){}},children:[],checked:false,value:'',
    add(){},addEventListener(){},querySelector(){return element(`${id}:p`);},replaceChildren(...values){this.children=values;},append(value){this.children.push(value);},setAttribute(key,value){this.attrs[key]=value;},getAttribute(key){return this.attrs[key];}});
  return elements.get(id);
};
const face={ready:Promise.resolve(false),clearPreviewActivity(){},expression:null,handleEvent(event){faceEvents.push(event);},setExpression(id){this.expression=id;faces.push(id);},setGaze(){},clearGaze(){},startSpeech(){},stopSpeech(){},snapshot(){return{viseme:'rest'};}};
class FakeContext{
  constructor(){this.currentTime=0;this.state='running';this.destination={};this.scheduled=[];FakeContext.latest=this;}
  async resume(){}
  createBuffer(_channels,length,rate){return{duration:length/rate,getChannelData(){return new Float32Array(length);}};}
  createBufferSource(){return{connect(){},disconnect(){},start:at=>this.scheduled.push(at),stop(){}};}
}
class FakeSocket{
  static OPEN=1;
  constructor(){this.readyState=1;FakeSocket.latest=this;}
  send(value){sent.push(JSON.parse(value));}
  close(){}
}
const restore=new Map();
function setGlobal(name,value){restore.set(name,Object.getOwnPropertyDescriptor(globalThis,name));Object.defineProperty(globalThis,name,{value,writable:true,configurable:true});}
setGlobal('__expressionTest',{...policy,...playback,...delivery,...activity,...dependencies});
setGlobal('document',{getElementById:element,createElement:()=>element(`created:${++createdElements}`),documentElement:{},addEventListener(){}});
setGlobal('window',{MistDrawnFaceRuntime:{create:()=>face,timing:{transitionMs:600}},MIST_DATA:{},parent:{postMessage(){}},addEventListener(){}});
setGlobal('navigator',{});setGlobal('location',{protocol:'http:',host:'test.invalid'});
setGlobal('AudioContext',FakeContext);setGlobal('WebSocket',FakeSocket);
setGlobal('MutationObserver',class{observe(){}});setGlobal('Option',class{});
setGlobal('matchMedia',()=>({matches:false,addEventListener(){}}));
setGlobal('fetch',async url=>({json:async()=>url.startsWith('/face-map')?map:{paired:true,expressions:Object.keys(map.expressions),fixtures_enabled:true,speech_backend:'streaming-tts'}}));
setGlobal('setInterval',()=>1);setGlobal('setTimeout',()=>1);setGlobal('clearTimeout',()=>{});
try{
  const app=await load(source);
  let now=0;app.expressionPolicy.now=()=>now;app.activityState.now=()=>now;app.speechStatus.now=()=>now;
  await app.start(true);await app.receive({type:'ready'});
  await app.receive({type:'face',expression:'happy',variant:1,duration_ms:3000,epoch:0,source:'model'});
  assert.equal(face.expression,'12_happy_09_manual','mouthless requested pose is visible while quiet');
  const pcm=Buffer.alloc(24000);for(let i=0;i<pcm.length;i+=2)pcm.writeInt16LE(4000,i);
  await app.receive({type:'audio',epoch:0,seq:0,pcm:pcm.toString('base64'),sample_rate:24000});
  assert.equal(FakeContext.latest.scheduled[0],.2,'an ordinary session retains the 200 ms startup buffer');
  assert.equal(app.renderExpression().state,'available','queued audio is not yet audible');
  assert.equal(element('status').textContent,'Preparing voice');
  FakeContext.latest.currentTime=.3;
  await app.receive({type:'state',state:'thinking'});
  assert.equal(app.renderExpression().state,'speaking','an early thinking message must not mask audible speech');
  assert.equal(faceEvents.at(-1).state,'speaking','thinking artwork cannot continue over audible speech');
  assert.equal(face.expression,'09_happy_03_manual','mouthless happy temporarily uses a speaking variant');
  assert.equal(app.expressionPolicy.snapshot().requestedFaceId,'12_happy_09_manual');
  await app.receive({type:'state',state:'listening'});
  assert.equal(app.renderExpression().state,'speaking','listening status cannot stop the mouth during audible output');
  FakeContext.latest.currentTime=.8;
  assert.equal(app.renderExpression().faceId,'12_happy_09_manual','quiet restores requested pose within its hold');
  await app.receive({type:'transcript_delta',role:'assistant',text:'A tiny robot crept out from under the cabinet'});
  assert.equal(element('caption').textContent,'','generated text must not run ahead of audio');
  app.stopPlayback();
  assert.equal(app.expressionPolicy.snapshot().state,'listening');
  const before=faces.length;
  await app.receive({type:'face',expression:'angry',epoch:0,source:'model'});
  assert.equal(faces.length,before,'late old-epoch tool face must be ignored after local stop');
  await app.receive({type:'audio_reset',epoch:1});
  assert.equal(element('caption').textContent,'','reset clears the interrupted visible caption');
  // An interrupted native turn no longer gets transcript_done. The reset must
  // clear its accumulated text before the next accepted opening phrase arrives.
  await app.receive({type:'transcript_delta',role:'assistant',text:'I\u2019ll be quiet'});
  await app.receive({type:'audio',epoch:1,seq:0,pcm:pcm.toString('base64'),sample_rate:24000,
    caption_cues:[{time:0,text:'I\u2019ll'},{time:.1,text:'I\u2019ll be quiet'},{time:.3,text:'I\u2019ll be quiet until you\u2019re ready.'}],caption_source:'elevenlabs_normalized_alignment'});
  await app.receive({type:'transcript_delta',role:'assistant',text:' until you\u2019re ready.'});
  assert.equal(element('caption').textContent,'','caption waits through the output buffer');
  FakeContext.latest.currentTime=1.01;app.renderExpression();
  assert.equal(element('caption').textContent,'I\u2019ll','first audible words appear without the interrupted prefix');
  FakeContext.latest.currentTime=1.15;app.renderExpression();
  assert.equal(element('caption').textContent,'I\u2019ll be quiet','later generated words are still withheld');
  FakeContext.latest.currentTime=1.35;app.renderExpression();
  assert.equal(element('caption').textContent,'I\u2019ll be quiet until you\u2019re ready.');
  await app.receive({type:'audio_reset',epoch:0});
  assert.equal(element('caption').textContent,'I\u2019ll be quiet until you\u2019re ready.','a late reset from an old epoch cannot clear the new reply');
  await app.receive({type:'transcript_done',role:'assistant',text:'I\u2019ll be quiet until you\u2019re ready.'});
  assert.equal(element('transcript').children.at(-1).textContent,'MIST: I\u2019ll be quiet until you\u2019re ready.');
  await app.receive({type:'face',expression:'curious',variant:1,duration_ms:900,epoch:1,source:'model'});
  assert.equal(face.expression,map.expressions.curious.alts[0]);
  await app.receive({type:'face',expression:'does_not_exist',epoch:1,source:'model'});
  assert.equal(face.expression,map.expressions.curious.alts[0]);
  now=950;app.renderExpression();assert.equal(app.expressionPolicy.snapshot().expression,'curious','requested hold starts after its 600 ms entrance');
  now=1550;FakeContext.latest.currentTime=1.6;app.renderExpression();assert.equal(app.expressionPolicy.snapshot().expression,'neutral');
  await app.end();assert.equal(app.expressionPolicy.snapshot().state,'available');
  await app.start(true);
  const beforeStudioReady=sent.length;
  const configurationEvents=()=>sent.filter(event=>event.type==='debug_client'&&event.event?.type==='playback_configuration');
  const priorConfigurations=configurationEvents().length;
  await app.receive({type:'studio_session',trace_id:'fast-test',architecture:{id:'fast-test',playback_buffer_ms:120}});
  assert.equal(sent.length,beforeStudioReady,'studio_session must not send debug packets while the provider is connecting');
  assert.equal(window.MistDebug.snapshot().playback.playback_buffer_ms,120,'snapshot exposes the effective session setting');
  await app.receive({type:'ready'});
  assert.equal(configurationEvents().length,priorConfigurations+1,'readiness emits exactly one deferred configuration');
  assert.equal(configurationEvents().at(-1).event.playback_buffer_ms,120);
  assert.equal(configurationEvents().at(-1).event.source,'studio_session');
  await app.receive({type:'ready'});
  assert.equal(configurationEvents().length,priorConfigurations+1,'duplicate ready events cannot duplicate the configuration');
  const began=FakeContext.latest.currentTime;
  await app.receive({type:'audio',epoch:0,seq:0,pcm:pcm.toString('base64'),sample_rate:24000,
    caption_cues:[{time:0,text:'Hello'}],caption_source:'elevenlabs_normalized_alignment'});
  assert.equal(FakeContext.latest.scheduled.at(-1),began+.12,'architecture setting applies before the first audio packet');
  FakeContext.latest.currentTime=began+.119;
  assert.equal(window.MistDebug.snapshot().playback.active,false);
  assert.equal(element('caption').textContent,'','smaller reserve still withholds words before output');
  FakeContext.latest.currentTime=began+.121;
  assert.equal(window.MistDebug.snapshot().playback.active,true);
  assert.equal(element('caption').textContent,'Hello','captions keep the same output clock with the smaller reserve');
  await app.end();await app.start(true);
  const beforeDefaultReady=sent.length;
  await app.receive({type:'studio_session',trace_id:'default-test',architecture:{id:'default-test'}});
  assert.equal(window.MistDebug.snapshot().playback.playback_buffer_ms,200,'a later session cannot inherit the 120 ms setting');
  assert.equal(sent.length,beforeDefaultReady,'default studio configuration also waits for readiness');
  await app.receive({type:'ready'});
  assert.equal(configurationEvents().at(-1).event.playback_buffer_ms,200);
  await app.receive({type:'studio_session',trace_id:'invalid-test',architecture:{playback_buffer_ms:119}});
  assert.match(element('error').textContent,/120.*400/,'invalid architecture configuration is explicitly rejected');
  assert.equal(window.MistDebug.snapshot().ready,false,'invalid settings end the session rather than silently changing the comparison');
  await app.start(true);
  const beforeNormalReady=configurationEvents().length;
  await app.receive({type:'ready'});
  assert.equal(configurationEvents().length,beforeNormalReady+1,'ordinary non-studio usage records its default only when ready');
  assert.equal(configurationEvents().at(-1).event.playback_buffer_ms,200);
  assert.equal(configurationEvents().at(-1).event.source,'default');
  const beforeLongStops=sent.filter(event=>event.type==='barge_in').length;
  const longStart=FakeContext.latest.currentTime;
  for(let i=0;i<90;i++)await app.receive({type:'audio',epoch:0,seq:0,pcm:pcm.toString('base64'),sample_rate:24000,
    caption_cues:[{time:0,text:`Phrase ${i}`}],caption_source:'elevenlabs_normalized_alignment'});
  assert.equal(sent.filter(event=>event.type==='barge_in').length,beforeLongStops,'a 45-second generated answer must not send an artificial user stop');
  FakeContext.latest.currentTime=longStart+.2+44.99;app.renderExpression();
  assert.equal(window.MistDebug.snapshot().playback.active,true);
  assert.equal(element('caption').textContent,'Phrase 89','the long answer reaches its final caption');
  const beforeOversized=FakeContext.latest.scheduled.length;
  await app.receive({type:'audio',epoch:0,seq:0,sample_rate:8000,pcm:Buffer.alloc(8000*2*121).toString('base64')});
  assert.equal(FakeContext.latest.scheduled.length,beforeOversized,'the rejected oversized packet is never scheduled');
  assert.match(element('error').textContent,/playback buffer exceeded 120 seconds/,'a genuine bound failure is visible');
  const limitEvents=sent.filter(event=>event.type==='debug_client'&&event.event?.phase==='buffer_limit');
  assert.equal(limitEvents.length,1);assert.equal(limitEvents[0].event.type,'audio_scheduled','use the existing allowed debug event type');
  assert.equal(limitEvents[0].event.status,'error');assert.equal(limitEvents[0].event.reason,'playback_buffer_limit');
  assert.equal(limitEvents[0].event.limit_ms,120000);
  assert.equal(sent.filter(event=>event.type==='barge_in').at(-1).reason,'playback_buffer_limit','the command distinguishes overflow from a user stop');
  assert.equal(element('caption').textContent,'');assert.equal(window.MistDebug.snapshot().playback.active,false);
  await app.receive({type:'audio',epoch:0,seq:0,pcm:pcm.toString('base64'),sample_rate:24000});
  assert.equal(FakeContext.latest.scheduled.length,beforeOversized,'late overflow-generation audio cannot replay');
  await app.end();await app.start(true);await app.receive({type:'ready'});
  now=5000;
  await app.receive({type:'face',expression:'sad',duration_ms:2200,persistent:true,epoch:0,source:'director'});
  assert.equal(app.expressionPolicy.snapshot().expiresAt,null,'omitted director duration means a persistent face even though the receipt has a normalized duration');
  assert.equal(app.expressionPolicy.snapshot().source,'director','the browser preserves the director source in its face receipt');
  now=12000;app.renderExpression();
  assert.equal(app.expressionPolicy.snapshot().expression,'sad','a delayed answer does not outlast its requested face');
  await app.receive({type:'audio_reset',epoch:1});
  assert.equal(app.expressionPolicy.snapshot().expression,'sad','audio interruption preserves the persistent mood');
  await app.receive({type:'face',expression:'happy',duration_ms:400,persistent:false,epoch:1,source:'model',face_serial:11,expression_revision:7});
  now=13000;app.renderExpression();
  assert.equal(app.expressionPolicy.snapshot().expression,'sad','an explicitly timed flash expires back to the persistent face');
  const faceAcks=()=>sent.filter(event=>event.type==='debug_client'&&event.event?.type==='expression'&&event.event?.phase==='transition'&&Number.isInteger(event.event.face_serial));
  assert.equal(faceAcks().at(-1).event.face_serial,11);
  assert.equal(faceAcks().at(-1).event.epoch,1);
  assert.equal(faceAcks().at(-1).event.expression_revision,7);
  assert.equal(faceAcks().at(-1).event.to,map.expressions.sad.primary,'the transition receipt names the face actually restored');
  await app.receive({type:'face',expression:'sad',duration_ms:400,persistent:false,epoch:1,source:'model',face_serial:12,expression_revision:7});
  now=13999;app.renderExpression();
  assert.equal(faceAcks().at(-1).event.face_serial,11,'no receipt is sent before the timed hold completes');
  now=14000;app.renderExpression();
  assert.equal(faceAcks().at(-1).event.face_serial,12,'a timed flash matching the base still gets a lifecycle receipt');
  assert.equal(faceAcks().at(-1).event.from,faceAcks().at(-1).event.to);
  assert.equal(sent.some(event=>event.type==='debug_client'&&event.event?.phase==='request'&&event.event?.expression==='sad'&&event.event?.source==='director'&&event.event?.persistent===true),true,'browser records the director request without transcript content');
  await app.end();
  now=20000;await app.start(true);await app.receive({type:'ready'});
  await app.receive({type:'state',state:'thinking'});
  now=20649;app.renderExpression();
  assert.equal(face.expression,map.expressions.neutral.primary,'brief thinking waits do not flash a different face');
  assert.equal(faceEvents.at(-1).state,'available');
  const thinkingFaces=[];
  for(const at of [20650,24850,29050]) {
    now=at;const shot=app.renderExpression();thinkingFaces.push(face.expression);
    assert.equal(shot.source,'state');assert.equal(faceEvents.at(-1).state,'thinking');
  }
  assert.deepEqual(thinkingFaces,['20_neutral_06_manual','40_neutral_03_manual','39_neutral_02_manual'],
    'the actual app sends all three existing thinking faces to its renderer');
  await app.receive({type:'face',expression:'happy',duration_ms:6000,epoch:0,source:'model'});
  now=34000;app.renderExpression();
  assert.equal(face.expression,map.expressions.happy.primary,'a model face remains visible through a long thinking wait');
  const speechAt=FakeContext.latest.currentTime;
  await app.receive({type:'audio',epoch:0,seq:0,pcm:pcm.toString('base64'),sample_rate:24000});
  FakeContext.latest.currentTime=speechAt+.3;
  await app.receive({type:'state',state:'thinking'});
  assert.equal(app.renderExpression().state,'speaking');
  assert.equal(faceEvents.at(-1).state,'speaking','audible speech clears the thinking activity immediately');
  assert.equal(face.expression,map.expressions.happy.primary);
  now=46000;const speaking=app.renderExpression();
  assert.equal(speaking.state,'speaking');assert.equal(speaking.mouthMode,'audio');
  assert.equal(speaking.gaze,null);assert.equal(speaking.faceId,map.expressions.neutral.primary,
    'the thinking rotation stays off after the explicit face expires during audible speech');
  app.stopPlayback();
  assert.equal(app.renderExpression().state,'listening');
  now=46650;app.renderExpression();
  assert.equal(face.expression,map.expressions.neutral.primary,'cancelled thinking cannot reappear');
  await app.end();
  now=50000;await app.start(true);await app.receive({type:'ready'});
  const continuityStart=FakeContext.latest.currentTime;
  const speechPacket={type:'audio',epoch:0,seq:0,turn_id:'continuous-answer',pcm:pcm.toString('base64'),sample_rate:24000,
    caption_cues:[{time:0,text:'First phrase'}],caption_source:'elevenlabs_alignment'};
  const renderAt=(audioTime,wallTime)=>{FakeContext.latest.currentTime=audioTime;now=wallTime;app.testPlayer().tick();return app.renderExpression();};
  const busyEvents=()=>sent.filter(event=>event.type==='playback_state');
  await app.receive(speechPacket);
  renderAt(continuityStart+.21,50210);
  assert.equal(element('status').textContent,'Speaking, still listening');
  assert.equal(window.MistDebug.snapshot().playback.active,true);
  await app.receive({type:'brain_job',job_id:'research',status:'running',question:'Check the weather'});
  await app.receive({type:'state',state:'checking'});
  await app.receive({type:'state',state:'listening'});
  assert.equal(element('status').textContent,'Speaking, still listening','background state events cannot replace active foreground delivery');
  renderAt(continuityStart+.69,50690);
  const busyBeforeDrain=busyEvents().length;
  const quietGap=renderAt(continuityStart+.71,50710);
  assert.equal(window.MistDebug.snapshot().playback.active,false,'the actual mouth sample closes when PCM runs out');
  assert.equal(quietGap.state,'available','holding speaking status cannot animate the mouth over silence');
  assert.equal(element('status').textContent,'Speaking, still listening','a brief same-answer gap holds the top status');
  assert.equal(busyEvents().length,busyBeforeDrain,'packet drain before caption_final must not report listening to the server');
  await app.receive({type:'state',state:'checking'});
  await app.receive({...speechPacket,caption_cues:[{time:0,text:'First phrase, second phrase'}]});
  assert(Math.abs(FakeContext.latest.scheduled.at(-1)-(continuityStart+.72))<1e-8,'short underrun resumes with only a 10 ms lead');
  renderAt(continuityStart+.74,50740);
  assert.equal(window.MistDebug.snapshot().playback.active,true);
  assert.equal(element('status').textContent,'Speaking, still listening');
  await app.receive({type:'caption_final',epoch:0,seq:0,text:'First phrase, second phrase.',turn_id:'continuous-answer'});
  await app.receive({type:'state',state:'listening'});
  assert.equal(busyEvents().at(-1).playing,true,'caption_final before the audible tail cannot clear playback ownership');
  renderAt(continuityStart+1.20,51200);
  assert.equal(element('status').textContent,'Speaking, still listening');
  renderAt(continuityStart+1.23,51230);
  assert.equal(element('status').textContent,'Listening','completion releases ownership after the output tail');
  assert.equal(busyEvents().at(-1).playing,false);
  assert.equal(element('caption').textContent,'First phrase, second phrase.');
  await app.receive({...speechPacket,seq:99,caption_cues:undefined,caption_source:undefined,alignment_source:'audio_energy'});
  const convertedStart=FakeContext.latest.scheduled.at(-1);
  renderAt(convertedStart+.02,51260);
  assert.equal(element('caption').textContent,'','converted PCM has no fabricated caption');
  await app.receive({type:'audio_complete',epoch:0,seq:99,source:'voice_conversion'});
  assert.equal(element('status').textContent,'Speaking, still listening','backend-neutral completion waits for queued converted PCM');
  assert.equal(busyEvents().at(-1).playing,true);
  renderAt(convertedStart+.51,51800);
  assert.equal(element('status').textContent,'Listening','converted audio releases ownership after its audible tail');
  assert.equal(busyEvents().at(-1).playing,false);
  assert.equal(element('caption').textContent,'','audio completion does not synthesize caption text');
  const stopStart=FakeContext.latest.currentTime;
  await app.receive({...speechPacket,seq:1});renderAt(stopStart+.22,52020);
  app.stopPlayback();
  assert.equal(window.MistDebug.snapshot().playback.active,false);
  assert.equal(element('status').textContent,'Listening','explicit stop bypasses status grace');
  assert.equal(busyEvents().at(-1).playing,false);
  assert.equal(app.speechStatus.streams.size,0);
  await app.receive({...speechPacket,epoch:1,seq:1});renderAt(stopStart+.44,52240);
  await app.receive({type:'audio_reset',epoch:2});
  assert.equal(element('status').textContent,'Listening','server reset also bypasses status grace');
  assert.equal(window.MistDebug.snapshot().playback.active,false);
  await app.receive({...speechPacket,epoch:2,seq:2});renderAt(stopStart+.66,52460);
  FakeSocket.latest.onclose();
  assert.equal(element('status').textContent,'Disconnected');
  assert.equal(window.MistDebug.snapshot().ready,false);
  assert.equal(window.MistDebug.snapshot().playback.active,false);
  assert.equal(app.speechStatus.streams.size,0,'disconnect cancels unfinished stream ownership immediately');
  now=60000;await app.start(true);await app.receive({type:'ready'});
  const replayBase=FakeContext.latest.currentTime,replayWall=now;
  const arrivals=[0];
  for(let i=1;i<27;i++)arrivals.push(arrivals.at(-1)+(i===12?7.845:[.2,.3,.4][(i-1)%3]));
  const shortPacket={...speechPacket,pcm:Buffer.alloc(4800).toString('base64'),caption_cues:undefined};
  const beforeReplayBarge=sent.filter(event=>event.type==='barge_in').length;
  const beforeReplayBusy=busyEvents().length;
  let nextPacket=0,lastPacketEnd=null,missingAudioSeconds=0,scheduledGapSeconds=0,waitingSamples=0;
  for(let elapsed=0;elapsed<=arrivals.at(-1)+.9;elapsed+=.005){
    renderAt(replayBase+elapsed,replayWall+elapsed*1000);
    while(nextPacket<arrivals.length&&arrivals[nextPacket]<=elapsed+1e-8){
      await app.receive(shortPacket);
      const scheduled=FakeContext.latest.scheduled.at(-1);
      if(lastPacketEnd!==null){
        missingAudioSeconds+=Math.max(0,FakeContext.latest.currentTime-lastPacketEnd);
        scheduledGapSeconds+=Math.max(0,scheduled-lastPacketEnd);
      }
      lastPacketEnd=scheduled+.1;
      await app.receive({type:'state',state:nextPacket%2?'checking':'listening'});
      nextPacket++;
    }
    if(nextPacket){
      assert.notEqual(element('status').textContent,'Listening','unfinished 27-packet reply never hands the floor back during delivery gaps');
      if(element('status').textContent==='Waiting for speech'){
        waitingSamples++;
        assert.equal(window.MistDebug.snapshot().playback.active,false,'a waiting status must never imply audible speech');
        assert(now-app.speechStatus.lastAudible>=450,'waiting replaces the speaking label only after the bounded 450 ms grace');
      }
    }
  }
  assert.equal(nextPacket,27);
  assert(waitingSamples>1000,'the 7.845-second hole is exposed as waiting instead of concealed by status grace');
  assert(missingAudioSeconds>7,'missing upstream PCM produces unavoidable silence in this synthetic stress replay');
  assert(scheduledGapSeconds>=missingAudioSeconds,'scheduling cannot play bytes before they arrive');
  assert.equal(busyEvents().slice(beforeReplayBusy).some(event=>event.playing===false),false,'packet starvation emits no false playback-idle report');
  assert.equal(sent.filter(event=>event.type==='barge_in').length,beforeReplayBarge,'delivery jitter and upstream stall cannot synthesize user barge-in');
  await app.receive({type:'caption_final',epoch:0,seq:0,text:'Synthetic replay complete.'});
  assert.equal(element('status').textContent,'Listening');
  await app.receive({...shortPacket,seq:1});
  const stalledStart=FakeContext.latest.scheduled.at(-1);
  renderAt(stalledStart+.05,now+250);
  renderAt(stalledStart+1,now+950);
  assert.equal(element('status').textContent,'Waiting for speech');
  app.stopPlayback();
  assert.equal(element('status').textContent,'Listening','true user interruption remains immediate after a long upstream stall');
  assert.equal(app.speechStatus.streams.size,0);
  assert.equal(sent.filter(event=>event.type==='barge_in').length,beforeReplayBarge+1);
  console.log(`Synthetic 27-packet replay: missing PCM ${missingAudioSeconds.toFixed(3)} s; scheduled silence ${scheduledGapSeconds.toFixed(3)} s; no false Listening/barge-in; explicit stop immediate.`);
  await app.end();
  console.log('PASS: actual app handlers, real PCM underrun/background status continuity with silent mouth, completion after audible tail, immediate stop/reset/disconnect, three thinking faces, expression holds, 45-second answer, bounded overflow, session buffers, ready ordering and output-clock captions.');
}finally{
  for(const [name,descriptor] of restore)if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];
}
