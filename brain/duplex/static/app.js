import {Playback} from './playback.js?v=20260924-long-buffer1';
import {replyExpression} from './delivery.js?v=20260922-expression-policy';
import {ExpressionPolicy} from './expression_policy.js?v=20260929-listening-rest1';
import {ActivityState} from './activity_state.js?v=20260924-sync1';
import {UserTranscriptState} from './user_transcript_state.mjs?v=20260928-listener3';
import {newListenerCue} from './listener_cue.js?v=20260928-listener3';
import {MicrophoneSignal} from './microphone_signal.mjs?v=20260929-listening-signal1';
import {AnimationPreview,VisiblePreviewScheduler,installAnimationPicker} from './animation_picker.mjs?v=20260929-activity-lifecycle2';
const $=id=>document.getElementById(id);let ws=null,context=null,player=null,stream=null,capture=null,ready=false,fixtureMode=false,seq=0,lastSensor=0,wake=null;
const trialArchitecture=new URLSearchParams(location.search).get('architecture');
const trialMemoryMode=new URLSearchParams(location.search).get('memory_mode')==='speech_feedback'?'speech_feedback':'discussion';
const trialVoice=new URLSearchParams(location.search).get('voice');
let starting=false,connectionGeneration=0;
let listenerCue=null;
if(trialArchitecture){document.body.classList.add('trial-frame');$('text-box').hidden=trialArchitecture==='native';}
let pendingCaption='',lastSpeechKey=null,backgroundJob=null,micFrames=0,micPower=0,pendingPlaybackConfiguration=null;
const backgroundJobs=new Map();
function renderBackgroundJobs(){
 const active=[...backgroundJobs.values()].filter(job=>job.status==='running');
 $('brain-status').textContent=active.length?`${active.length} background task${active.length===1?'':'s'} running.`:'No background tasks running.';
 $('cancel-background').hidden=true;$('background-jobs').replaceChildren();
 for(const job of [...backgroundJobs.values()].reverse()){
  const row=document.createElement('div');row.className='background-job';
  const info=document.createElement('span');info.textContent=`${job.question||'Task'} · ${job.status}${job.elapsed_s?` · ${job.elapsed_s.toFixed(1)} s`:''}${job.context_changed?' · earlier context':''}`;
  row.append(info);if(job.status==='running'){const button=document.createElement('button');button.textContent='Cancel';button.setAttribute('aria-label',`Cancel ${job.question||'task'}`);button.onclick=()=>send({type:'cancel_background',job_id:job.job_id});row.append(button);}
  $('background-jobs').append(row);
 }
}
let micSeq=0,micSignalTimer=null,lastMicDraw=0,storageState=null,latestSummary=null;
const micSignal=new MicrophoneSignal();
const signal=$('listening-signal'),signalBars=[];
for(let i=0;i<8;i++){const bar=document.createElementNS('http://www.w3.org/2000/svg','path');bar.setAttribute('d',`M${17+i*14} 26v4`);$('signal-bars').append(bar);signalBars.push(bar);}
function drawMicSignal(levels=micSignal.levels){
 const now=performance.now();if(now-lastMicDraw<30)return;lastMicDraw=now;
 for(let i=0;i<signalBars.length;i++){
  const level=levels[i],height=reducedMotion?4:4+level*32,x=17+i*14;
  signalBars[i].setAttribute('d',`M${x} ${28-height/2}v${height}`);
  signalBars[i].style.opacity=String(.35+level*.65);
 }
}
function stopMicSignal(){clearTimeout(micSignalTimer);micSignalTimer=null;micSignal.clear();signal.hidden=true;}
function updateStorage(event){
 if(event.recording!==true)return;
 storageState={sessionId:String(event.session_id||''),status:event.status,location:event.location,audio:event.audio};
 const label=event.status==='recording'?'Recording on MIST host':event.status==='saved'?'Session saved on MIST host':'Recording unavailable';
 const status=$('storage-status');status.hidden=false;status.dataset.status=event.status;status.textContent=label;
 const details=$('storage-details');details.hidden=false;
 details.textContent=`${event.status==='saved'?'Latest saved session':'Current session'}: ${storageState.sessionId || 'pending'} · microphone + MIST audio · MIST host${event.status==='error'?' · recording failed':''}`;
}
function finishStorageStatus(){
 if(storageState?.status!=='recording')return;
 $('storage-status').textContent='Recording ended · check MIST host sessions';
 $('storage-details').textContent=`Latest session: ${storageState.sessionId} · final storage status pending on MIST host`;
}
const userTranscript=new UserTranscriptState();
function renderUserTranscript(snapshot=userTranscript.snapshot()){
 const band=$('user-transcript');const line=band.querySelector('p');line.textContent=snapshot.text;
 band.hidden=!snapshot.visible;$('companion').classList.toggle('has-user-transcript',snapshot.visible);
 line.scrollTop=line.scrollHeight;
}
const face=(window.MistHanddrawnRuntime||window.MistDrawnFaceRuntime||MistFaceRuntime).create($('face'),{data:window.MIST_DATA,atlasUrl:'/drawn/atlas.json',manifestUrl:'/studio/handdrawn_v6/manifest.json'});const map=await(await fetch('/face-map.json?v=20260922-expression-policy')).json();
let connectionActivity=null;
const activityState=new ActivityState();let lastActivityState=null;
const preview=new AnimationPreview(face);
window.MistActivity=Object.freeze({setActivity:(id,options)=>face.setActivity?.(id,options),clearActivity:(token,options)=>face.clearActivity?.(token,options),snapshot:()=>face.snapshot().handdrawn});
const config=await(await fetch('/config')).json();let pairOK=config.paired;let currentFace='neutral';let expressionPinned=false;
if(config.remote_mode)$('local-study-links').hidden=true;
const reducedMotion=!!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const expressionPolicy=new ExpressionPolicy(map,{transitionMs:reducedMotion?0:(window.MistDrawnFaceRuntime?.timing?.transitionMs??260)});let conversationState='available',lastFaceId=null,displayedFaceId=null,lastGaze=null,timedFaceReceipt=null;
window.MistDebug=Object.freeze({snapshot:()=>({ready,conversationState,preview:{faceId:preview.faceId,activityId:preview.activityId,active:preview.active},userTranscript:userTranscript.snapshot(),listenerCue:listenerCue?.sample()||null,expression:expressionPolicy.snapshot(),playback:player?.sample()||null,face:face.snapshot(),sessionStorage:storageState,sessionSummary:latestSummary})});
if(config.speech_backend==='voice-changer'){$('backend-description').textContent='Codex native voice, converted to MIST. The microphone stays available while MIST speaks.';$('backend-note').textContent='Voice conversion adds a delay. Original Codex audio is never played.';}
function renderExpression(){
 const shot=expressionPolicy.setState(player?.sample()?.active?'speaking':conversationState);
 if(timedFaceReceipt&&shot.timed&&shot.requestedFaceId===timedFaceReceipt.faceId)timedFaceReceipt.seen=true;
 const timedReturn=timedFaceReceipt?.seen&&!shot.timed;
 const receiptFields=timedReturn?{epoch:timedFaceReceipt.epoch,expression_revision:timedFaceReceipt.expressionRevision,face_serial:timedFaceReceipt.faceSerial}:{};
 const activity=activityState.update(shot.state);
 if(activity!==lastActivityState){lastActivityState=activity;face.handleEvent?.({type:'state',state:activity});}
 currentFace=shot.expression;expressionPinned=shot.source!=='state'&&shot.source!=='automatic';
 if(lastFaceId!==shot.faceId){const previous=lastFaceId;lastFaceId=shot.faceId;trace({type:'expression',phase:'transition',from:previous,to:shot.faceId,expression:shot.expression,source:shot.source,queue_depth:shot.queueDepth,expires_at_ms:shot.expiresAt,...receiptFields});if(trialArchitecture)trace({type:'expression_transition',from:previous,to:shot.faceId,expression:shot.expression,source:shot.source});}
 else if(timedReturn&&timedFaceReceipt.lastAckTarget!==shot.faceId)trace({type:'expression',phase:'transition',from:shot.faceId,to:shot.faceId,expression:shot.expression,source:shot.source,queue_depth:shot.queueDepth,expires_at_ms:shot.expiresAt,...receiptFields});
 if(timedReturn){timedFaceReceipt.lastAckTarget=shot.faceId;if(shot.faceId===(shot.baseFaceId??map.expressions.neutral.primary))timedFaceReceipt=null;}
 const shown=preview.displayedFace(shot.faceId);if(shown!==displayedFaceId){displayedFaceId=shown;face.setExpression(shown);}
 const gaze=JSON.stringify(preview.faceId?null:shot.gaze);if(gaze!==lastGaze){lastGaze=gaze;if(!preview.faceId&&shot.gaze)face.setGaze(...shot.gaze);else face.clearGaze();}
 const label=preview.active?`MIST animation preview: ${preview.faceId||shot.expression}${preview.activityId?`, ${preview.activityId}`:''}`:`MIST face: ${shot.expression}${shot.speechFallback?' (speaking variant)':''}`;
 if($('face').getAttribute('aria-label')!==label)$('face').setAttribute('aria-label',label);
 return shot;
}
function expression(command,options={}){const requested=typeof command==='string'?{expression:command}:command;if(options.source==='manual'&&(requested.persistent??!Object.hasOwn(requested,'duration_ms')))timedFaceReceipt=null;const result=expressionPolicy.request(requested,options);renderExpression();trace({type:'expression',phase:'request',expression:requested.expression,source:options.source||'model',status:result.status,reason:result.reason||null,queue_depth:result.queueDepth??expressionPolicy.snapshot().queueDepth,persistent:requested.persistent??!Object.hasOwn(requested,'duration_ms')});return result;}
function state(value){conversationState=value;renderExpression();}
renderExpression();setInterval(renderExpression,50);
const scheduler=new VisiblePreviewScheduler();let picker=null,activityAssetsReady=false;
function renderPreviewStatus(){
 const parts=[];
 if(preview.faceId)parts.push(`face ${map.faces[preview.faceId]?.name||preview.faceId}`);
 if(preview.activityId)parts.push(`activity ${face.manifest?.activities?.find(item=>item.id===preview.activityId)?.label||preview.activityId}`);
 $('preview-status').textContent=parts.length?`Previewing ${parts.join(' with ')}.`:'Following live MIST.';
 $('preview-auto').disabled=!preview.active;$('preview-indicator').hidden=!preview.active;$('companion').classList.toggle('has-preview',preview.active);renderExpression();
}
function releasePreview(){preview.release();picker?.markSelected(null,null);renderPreviewStatus();}
function chooseAnimation(item,mode){
 if(mode==='faces'){preview.selectFace(item.id);picker?.markSelected(item.id,'faces');}
 else if(preview.selectActivity(item.id)){picker?.markSelected(preview.activityId,preview.activityId?'activities':null);}
 renderPreviewStatus();
}
face.ready.then(ready=>{
 if(!ready||!face.loaded||!face.manifest?.activities?.length)throw new Error('Activity assets unavailable');
 activityAssetsReady=true;
 const Runtime=window.MistHanddrawnRuntime||window.MistDrawnFaceRuntime||window.MistFaceRuntime;
 picker=installAnimationPicker({map,manifest:face.manifest,grid:$('animation-grid'),groupSelect:$('animation-group'),tabs:{faces:$('face-tab'),activities:$('activity-tab')},scheduler,onSelect:chooseAnimation,
  makePreview(item,mode,mount){const instance=Runtime.create(mount,{data:window.MIST_DATA,atlasUrl:'/drawn/atlas.json',manifestUrl:'/studio/handdrawn_v6/manifest.json',manifest:face.manifest,expression:mode==='faces'?item.id:map.expressions.neutral.primary,previewCanvasWidth:250,skipHiddenFallbackUpdates:true,autoStart:false});if(mode==='activities')instance.ready.then(ok=>{if(ok)instance.previewActivity(item.id);});return {update:time=>instance.update(time),destroy:()=>instance.destroy()};}});
 picker.setOpen(!$('animation-picker').hidden&&!document.hidden);$('face-count').textContent=picker.catalog.faces.length;$('activity-count').textContent=picker.catalog.activities.length;
 renderPreviewStatus();
}).catch(()=>{activityAssetsReady=false;$('activity-tab').disabled=true;$('activity-count').textContent='unavailable';$('preview-status').textContent='Activity previews are unavailable; live MIST is unchanged.';});
$('preview-auto').onclick=releasePreview;
$('pair-form').hidden=pairOK;$('pair-state').textContent=pairOK?'This screen is paired.':'Enter the code printed by the local MIST server.';$('fixture-box').hidden=!config.fixtures_enabled;
function send(data){if(ws?.readyState===WebSocket.OPEN)ws.send(JSON.stringify(data));}
function error(message){$('error').textContent=message;$('status').textContent=message;$('panel').hidden=false;}
function stopPlayback(){listenerCue?.reset('explicit_stop');player?.reset();expressionPolicy.interrupt();state('listening');send({type:'barge_in'});}
async function end(){listenerCue?.reset('disconnected');connectionGeneration++;starting=false;ready=false;pendingPlaybackConfiguration=null;timedFaceReceipt=null;pendingCaption='';lastSpeechKey=null;stopMicSignal();finishStorageStatus();stream?.getTracks().forEach(t=>t.stop());stream=null;capture?.disconnect();capture=null;player?.reset();ws?.close();ws=null;const oldWake=wake;wake=null;$('talk').textContent='Start conversation';$('stop').disabled=true;$('status').textContent='Conversation ended';$('caption').textContent='';transcript.user='';transcript.assistant='';renderUserTranscript(userTranscript.reset());backgroundJob=null;backgroundJobs.clear();renderBackgroundJobs();releasePreview();face.resetActivities?.();connectionActivity=null;expressionPolicy.reset();state('available');await oldWake?.release().catch(()=>{});}
async function start(test=false){
 if(ws||starting)return end();if(!pairOK){$('panel').hidden=false;return;}
 releasePreview();
 starting=true;const generation=++connectionGeneration;
 fixtureMode=test;micSeq=0;lastSpeechKey=null;pendingPlaybackConfiguration='default';renderUserTranscript(userTranscript.reset());transcript.user='';$('error').textContent='';context??=new AudioContext();await context.resume();
 listenerCue?.reset('new_session');
 listenerCue=newListenerCue(context,face,event=>{if(ready)send(event);});
 player=new Playback(context,face,detail=>{
   expressionPolicy.interrupt();state('listening');
   trace({type:'audio_scheduled',phase:'buffer_limit',status:'error',...detail,playing:false,audio_active:false});
   send({type:'playback_state',playing:false});send({type:'barge_in',reason:detail.reason});
   error(`Speech stopped: playback buffer exceeded ${detail.limit_ms/1000} seconds.`);
 },()=>{
   send({type:'playback_state',playing:false});trace({type:'playback_idle'});
   if(ready){$('status').textContent='Listening';state('listening');}
 },event=>{
   send({type:'playback_state',playing:true});
   trace({type:'playback_started',epoch:event.epoch,sequence:event.seq,queue_ms:Math.max(0,player.end-player.outputTime())*1000,
     playback_buffer_ms:player.startBufferMs,clock_source:player.clockSource,alignment_source:event.alignment_source||'audio_energy'});
   const key=`${event.epoch}:${event.seq}`;
   if(key!==lastSpeechKey){lastSpeechKey=key;const cue=replyExpression(pendingCaption);expression({expression:cue,duration_ms:map.expressions[cue].duration_ms},{source:'automatic'});}
   renderExpression();
   $('status').textContent='Speaking, still listening';
 },caption=>{
   $('caption').textContent=caption.text.slice(-220);
   $('caption').setAttribute('data-source',caption.source);
   trace({type:'caption_progress',text:caption.text,source:caption.source,epoch:caption.epoch,sequence:caption.sequence});
 });
 if(!test){
  if(!window.isSecureContext){starting=false;error('Microphone access needs HTTPS, or localhost through adb reverse.');return;}
  try{stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});for(const t of stream.getAudioTracks())t.onended=()=>end();}
  catch(e){starting=false;error('Microphone was not enabled. You can reconnect when ready.');return;}
 }
 if(generation!==connectionGeneration){stream?.getTracks().forEach(t=>t.stop());stream=null;return;}
 const voicePath=trialArchitecture?`/trial/voice?architecture=${encodeURIComponent(trialArchitecture)}&memory_mode=${trialMemoryMode}${trialVoice?`&voice=${encodeURIComponent(trialVoice)}`:''}`:'/voice';
 ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}${voicePath}`);starting=false;$('status').textContent='Connecting voice';$('talk').textContent='End conversation';
 connectionActivity=face.setActivity?.('connecting',{owner:'transport'}).token;
 const socket=ws;socket.onmessage=event=>{if(ws===socket)receive(JSON.parse(event.data));};socket.onerror=()=>{if(ws===socket){face.clearActivity?.(connectionActivity,{immediate:true});error('Voice connection failed. Check pairing and local server.');}};
 socket.onclose=()=>{if(ws!==socket)return;listenerCue?.reset('disconnected');ready=false;pendingPlaybackConfiguration=null;timedFaceReceipt=null;stopMicSignal();finishStorageStatus();stream?.getTracks().forEach(t=>t.stop());stream=null;capture?.disconnect();capture=null;player?.reset();renderUserTranscript(userTranscript.reset());transcript.user='';releasePreview();face.resetActivities?.();expressionPolicy.reset();state('available');wake?.release().catch(()=>{});wake=null;ws=null;$('talk').textContent='Start conversation';$('stop').disabled=true;$('status').textContent='Disconnected';};
 try{wake=await navigator.wakeLock?.request('screen');}catch{}
}
const transcript={user:'',assistant:''};
async function microphone(){
 await context.audioWorklet.addModule('/duplex/capture-worklet.js');const source=context.createMediaStreamSource(stream);capture=new AudioWorkletNode(context,'mist-capture');source.connect(capture);const mute=context.createGain();mute.gain.value=0;capture.connect(mute).connect(context.destination);
 capture.port.onmessage=event=>{if(!ready)return;const {pcm,rms}=event.data;const captureMs=performance.now();micFrames++;micPower+=(rms||0)**2;
  micSignal.observe(pcm);signal.hidden=false;drawMicSignal();clearTimeout(micSignalTimer);micSignalTimer=setTimeout(()=>{micSignal.clear();drawMicSignal();},180);
  let chars='';for(const byte of new Uint8Array(pcm))chars+=String.fromCharCode(byte);send({type:'mic',seq:micSeq++,capture_ms:captureMs,pcm:btoa(chars)});
  // Keep the uplink active. Native user-turn detection handles interruption;
  // raw microphone volume cannot distinguish speech from loudspeaker echo.
 };
}
function addTranscript(role,text){const p=document.createElement('p');p.textContent=(role==='user'?'You: ':'MIST: ')+text;$('transcript').append(p);while($('transcript').children.length>60)$('transcript').firstChild.remove();}
async function receive(event){
 listenerCue?.observe(event);
 if(event.type!=='state')face.handleEvent?.(event);
 if(event.type==='session_storage'){updateStorage(event);}
 else if(event.type==='session_summary'){latestSummary=event;}
 else if(event.type==='studio_session'){
   const bufferMs=Object.hasOwn(event.architecture||{},'playback_buffer_ms')?event.architecture.playback_buffer_ms:200;
   try{player.setStartBufferMs(bufferMs);}catch{await end();error('Invalid playback buffer setting. Expected an integer from 120 to 400 ms.');return;}
   pendingPlaybackConfiguration='studio_session';
   window.parent.postMessage({type:'mist-session',trace_id:event.trace_id,architecture:event.architecture},location.origin);
 }
 else if(event.type==='ready'){
   face.clearActivity?.(connectionActivity);connectionActivity=null;ready=true;
   if(pendingPlaybackConfiguration){const source=pendingPlaybackConfiguration;pendingPlaybackConfiguration=null;trace({type:'playback_configuration',playback_buffer_ms:player.startBufferMs,source});}
   state('listening');$('status').textContent='Listening';$('stop').disabled=false;
   const ackSupported=event.listener_ack_supported===true;$('listener-acks').disabled=!ackSupported;$('listener-ack-status').textContent=ackSupported?'':'Unavailable for this voice';
   if(ackSupported)send({type:'listener_ack_config',enabled:$('listener-acks').checked});
   if(!fixtureMode)await microphone();
 }
 else if(event.type==='listener_cue'){listenerCue?.play(event,{normalPlaying:player?.playing||false,enabled:ready&&$('listener-acks').checked&&!$('listener-acks').disabled});}
 else if(event.type==='audio'){listenerCue?.reset('assistant_speaking');player.append(event);}
 else if(event.type==='audio_reset'){if(player&&event.epoch<player.epoch)return;player?.reset(event.epoch);expressionPolicy.interrupt();state('listening');send({type:'playback_state',playing:false});$('caption').textContent='';pendingCaption='';transcript.assistant='';}
 else if(event.type==='user_transcript'){renderUserTranscript(userTranscript.replace(event));}
 else if(event.type==='transcript_delta'){transcript[event.role]+=event.text;if(event.role==='user')renderUserTranscript(userTranscript.appendFallback(event.text));if(event.role==='assistant')pendingCaption=transcript.assistant;}
 else if(event.type==='transcript_done'){addTranscript(event.role,event.text);if(event.role==='user'&&userTranscript.snapshot().source!=='server')renderUserTranscript(userTranscript.finalizeFallback(event.text));transcript[event.role]='';if(event.role==='assistant'){
   pendingCaption=event.text;
 }}
 else if(event.type==='face'){if(player&&event.epoch!==player.epoch)return;const command={expression:event.expression};for(const key of ['variant','duration_ms','persistent'])if(event[key]!==undefined)command[key]=event[key];if(event.persistent===true)timedFaceReceipt=null;const source=['manual','director'].includes(event.source)?event.source:'model';const result=expression(command,{source,sequenceKey:event.sequence_id??null});if(['applied','queued','unchanged'].includes(result.status)){
   if(event.persistent===false&&Number.isInteger(event.face_serial)&&Number.isInteger(event.expression_revision)&&Number.isInteger(event.epoch)){
     const shot=expressionPolicy.snapshot();timedFaceReceipt={faceId:result.faceId,faceSerial:event.face_serial,expressionRevision:event.expression_revision,epoch:event.epoch,seen:shot.timed&&shot.requestedFaceId===result.faceId,lastAckTarget:null};
   }
  }}
 else if(event.type==='state'){state(event.state);$('status').textContent=player?.sample()?.active?'Speaking, still listening':event.state==='listening'?'Listening':event.state==='thinking'?'Thinking':event.state==='checking'?'Checking':'Connecting';}
 else if(event.type==='tool'){addTranscript('assistant',event.name==='remember'&&event.result.status==='saved'?`Saved preference: ${event.result.note}`:`[${event.name}: ${event.result.status||'read'}]`);if(event.name==='stop_robot'){expression('alert');$('motion-status').textContent='Preview stopped. Hardware stop is not connected.';}if(event.result.status==='preview_only'){$('motion-status').textContent=`Prepared ${event.result.action.replaceAll('_',' ')} preview. No hardware movement.`;if(event.result.action==='phone_pan')face.setGaze(Math.max(-1,Math.min(1,event.result.angle_rad/Math.PI)),0);}}
 else if(event.type==='sensors')$('sensor-status').textContent=JSON.stringify(event.snapshot,null,2);
 else if(event.type==='brain_job'){backgroundJob=event.job_id;backgroundJobs.set(event.job_id,{...backgroundJobs.get(event.job_id),...event});while(backgroundJobs.size>12){const finished=[...backgroundJobs.keys()].find(id=>backgroundJobs.get(id).status!=='running');if(!finished)break;backgroundJobs.delete(finished);}renderBackgroundJobs();}
 else if(event.type==='latency'){const m=event.tts||event.conversion;$('latency').textContent=`${event.tts?'Streaming TTS':'Voice conversion'} ${(m.first_byte_s||0).toFixed(2)} s`;}
 else if(event.type==='voice_warning'){$('error').textContent=event.message;$('status').textContent='Listening; voice interrupted';}
 else if(event.type==='error'){if(event.reconnect_required===true){await end();error(event.message);$('status').textContent='Reconnect to continue';$('talk').textContent='Reconnect';}else{if(event.fatal)await end();error(event.message);}}
}
$('talk').onclick=()=>start(false).catch(e=>error(e.message));$('test-connect').onclick=()=>start(true).catch(e=>error(e.message));$('stop').onclick=stopPlayback;
$('listener-acks').onchange=()=>{if(!$('listener-acks').checked)listenerCue?.reset('disabled');if(ready)send({type:'listener_ack_config',enabled:$('listener-acks').checked});};
$('cancel-background').onclick=()=>send({type:'cancel_background',job_id:backgroundJob});
$('background-form').onsubmit=event=>{event.preventDefault();if(!ready){error('Start a conversation first.');return;}send({type:'start_background',question:$('background-question').value,task_type:$('background-type').value});$('background-question').value='';};
const pickerMotion=matchMedia('(prefers-reduced-motion: reduce)');scheduler.setReducedMotion(pickerMotion.matches);pickerMotion.addEventListener?.('change',event=>scheduler.setReducedMotion(event.matches));document.addEventListener('visibilitychange',()=>scheduler.setOpen(!$('animation-picker').hidden&&!document.hidden));
function closePicker(){const tray=$('animation-picker');picker?.setOpen(false);tray.hidden=true;$('companion').classList.remove('preview-open');$('animations').setAttribute('aria-expanded','false');$('animations').focus();}
$('settings').onclick=()=>{if(!$('animation-picker').hidden)closePicker();$('panel').hidden=false;};
$('animations').onclick=()=>{const tray=$('animation-picker');if(!tray.hidden){closePicker();return;}$('panel').hidden=true;tray.hidden=false;$('companion').classList.add('preview-open');$('animations').setAttribute('aria-expanded','true');picker?.setOpen(true);$('face-tab').focus();};
$('close-animations').onclick=closePicker;
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('animation-picker').hidden)closePicker();});
$('close-panel').onclick=()=>{$('panel').hidden=true;};
$('pair-form').onsubmit=async event=>{event.preventDefault();const r=await fetch('/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:$('pair-code').value})});if(!r.ok){error('Pairing code not accepted');return;}pairOK=true;$('pair-code').value='';$('pair-form').hidden=true;$('pair-state').textContent='This screen is paired.';};
$('robot-stop').onclick=()=>{stopPlayback();send({type:'stop_robot'});};$('resume').onclick=()=>send({type:'resume_preview'});
$('fullscreen').onclick=()=>document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen();
$('fixture').onsubmit=event=>{event.preventDefault();if(!ready){error('Connect a test session first');return;}send({type:'fixture',text:$('fixture-text').value});};
$('text-form').onsubmit=event=>{event.preventDefault();if(!ready){error('Start a conversation or connect a test session first.');return;}send({type:'text',text:$('text-input').value});$('text-input').value='';};
function sensor(kind,values){if(!ready||!$('sensors').checked||Date.now()-lastSensor<500)return;lastSensor=Date.now();send({type:'sensors',packet:{kind,seq:seq++,captured_at:Date.now()/1000,values}});}
$('sensors').onchange=async()=>{if(!$('sensors').checked)return;try{if(typeof window.DeviceMotionEvent?.requestPermission==='function')await window.DeviceMotionEvent.requestPermission();$('sensor-status').textContent='Waiting for reported phone motion. No body-attitude transform is applied.';}catch{error('Motion permission was not enabled');}};
window.addEventListener('devicemotion',e=>{const a=e.accelerationIncludingGravity||{},g=e.rotationRate||{};const values={ax:a.x,ay:a.y,az:a.z,gx:g.alpha,gy:g.beta,gz:g.gamma};sensor('phone_motion',Object.fromEntries(Object.entries(values).filter(([,v])=>Number.isFinite(v))));});
window.addEventListener('beforeunload',()=>{picker?.destroy();scheduler.destroy();listenerCue?.reset('page_closed');stopMicSignal();stream?.getTracks().forEach(t=>t.stop());player?.reset();context?.close().catch(()=>{});ws?.close();});
window.addEventListener('error',event=>{if(trialArchitecture)trace({type:'client_error',message:event.message});});
window.addEventListener('unhandledrejection',event=>{if(trialArchitecture)trace({type:'client_error',message:String(event.reason)});});

function trace(event){if(ready)send({type:'debug_client',event:{...event,client_ms:performance.now()}});}
new MutationObserver(()=>trace({type:'expression',name:currentFace,variant:face.expression,pinned:expressionPinned})).observe($('face'),{attributes:true,attributeFilter:['aria-label']});
setInterval(()=>{if(!ready)return;trace({type:'microphone_stats',frames:micFrames,rms:micFrames?Math.sqrt(micPower/micFrames):0,fixture:fixtureMode});micFrames=0;micPower=0;const cue=player?.sample();trace({type:'audio_scheduled',queue_ms:Math.max(0,(player?.end||0)-(player?.outputTime()||0))*1000,playback_buffer_ms:player?.startBufferMs??null,playing:player?.playing||false,viseme:face.snapshot().viseme,audio_active:cue?.active||false,alignment_source:cue?.source||null,clock_source:player?.clockSource||null,renderer:face.snapshot().renderer||'original'});},1000);
