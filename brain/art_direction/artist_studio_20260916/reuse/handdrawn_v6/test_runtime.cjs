const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const dir=__dirname,root=path.resolve(dir,'../../../../..');
global.window=global;
require(path.join(root,'brain/face_assets/app/app_data.js'));
require(path.join(root,'brain/ui/static/face_runtime.js'));
require(path.join(root,'brain/ui/static/drawn_face_renderer.js'));
global.Path2D=class{constructor(value){this.value=value;}};
const runtime=require('./runtime.js');
const manifest=JSON.parse(fs.readFileSync(path.join(dir,'manifest.json'))),atlas=JSON.parse(fs.readFileSync(path.join(dir,'../drawn/atlas.json')));
const images={},handdrawnImages={};
for(const [id,asset]of Object.entries(atlas.assets)){
  images[asset.file]={file:asset.file};images[id]=images[asset.file];
  for(const frame of asset.frames)if(frame.file)images[frame.file]={file:frame.file};
}
for(const asset of Object.values(manifest.assets))for(const frame of asset.frames)handdrawnImages[frame.file]={file:frame.file,width:256,height:256};
let time=0,checks=0;
function ok(value,message){checks++;assert.ok(value,message);}
function eq(a,b,message){checks++;assert.deepEqual(a,b,message);}
const options={data:MIST_DATA,atlas,images,manifest,handdrawnImages,expression:'39_neutral_02_manual',autoStart:false,reducedMotion:false,now:()=>time};
function canvas(){
  const calls=[],context={};
  for(const name of ['save','restore','setTransform','clearRect','beginPath','rect','clip','translate','rotate','scale','stroke','fill'])context[name]=(...args)=>calls.push({name,args});
  context.drawImage=(...args)=>calls.push({name:'drawImage',args});
  return {width:1000,height:640,dataset:{},calls,getContext:()=>context};
}
(async()=>{
  runtime.helpers.validateManifest(manifest);
  const clock=runtime.helpers.createActivityClock(()=>time);
  let samples=0;
  for(const id of runtime.activities){
    time=0;const result=clock.set(id,{owner:id});ok(result.accepted,id+' starts');
    eq(clock.sample(0).phase,'entrance');eq(clock.sample(9*1000/15).phase,'loop');
    for(let i=0;i<1200;i++){
      const s=clock.sample(i*1000/15+((i%3)-1)*1e-9);samples++;
      ok(Number.isInteger(s.frame)&&s.frame>=0&&s.frame<8,id+' valid frame at '+i);
      ok(s.alpha>=0&&s.alpha<=1,id+' valid alpha');
    }
    time=180000;eq(clock.sample().phase,'loop',id+' never self-completes');clock.clear(result.token);
    for(let i=0;i<8;i++){const s=clock.sample(time+i*1000/15);eq(s.phase,'exit');ok(s.frame>=8&&s.frame<=11);}
    eq(clock.sample(time+9*1000/15),null);
  }
  time=0;const a=clock.set('reading',{owner:'old'}),b=clock.set('writing',{owner:'new'});
  eq(clock.clear(a.token),false,'stale completion cannot clear newer owner');eq(clock.sample().id,'writing');
  eq(clock.set('writing',{owner:'new'}).token,b.token,'same owner does not restart');
  eq(clock.set('invented').accepted,false);eq(clock.sample().id,'writing');clock.clear(b.token,{immediate:true});eq(clock.sample(),null);
  const face=runtime.create(null,options);eq(await face.ready,true);const target=canvas();
  let rendered=0;
  for(const config of manifest.activities){
    const receipt=face.setActivity(config.id,{owner:'test:'+config.id});
    for(let frame=0;frame<90;frame++){
      time+=1000/15;face.update(time);const start=target.calls.length,state=face.renderTo(target,time);rendered++;
      eq(state.renderer,'handdrawn');
      eq(state.handdrawn.eyeSource,'original-mist');
      for(const eye of state.handdrawn.eyes)ok(MIST_DATA.parts[eye.part],config.id+' uses original eye part');
      const emitted=target.calls.slice(start).filter(c=>c.name==='drawImage'&&c.args[0].file.startsWith('frames/'));
      eq(emitted.length,1,'only activity symbol uses new image frames');
      for(const draw of emitted)eq(draw.args.length,5,'draw complete registered square, never crop to ink box');
    }
    face.clearActivity(receipt.token,{immediate:true});
  }
  face.resetActivities();
  let expressionChecks=0;
  for(const expression of MIST_DATA.faces){
    eq(face.setExpression(expression.id),true);time+=700;
    const s=face.renderTo(target,time);expressionChecks++;
    eq(s.expression,expression.id,'expression ID retained');
    for(const eye of s.handdrawn.eyes)eq(eye.part,expression.slots[eye.role].part,'expression uses exact original eye part');
    const before=s.drawnMouthFamily.assigned;
    let audioTime=1.123456;
    face.startSpeech({sample:()=>({active:true,viseme:'AA',amount:.8,time:audioTime,source:'audio-clock'})});
    const talking=face.snapshot(time);eq(talking.speechTime,audioTime,'audio clock is not quantized to 15fps');eq(talking.drawnMouthFamily.assigned,before,'emotion mouth retained');
    audioTime=1.12789;eq(face.snapshot(time).speechTime,audioTime,'audio updates within one visual frame');face.stopSpeech();eq(face.snapshot(time).speaking,false);
  }
  face.setExpression('39_neutral_02_manual');face.resetActivities();
  face.handleEvent({type:'brain_job',job_id:'b1',status:'running'});eq(face.snapshot().handdrawn.activity.id,'planning');
  face.handleEvent({type:'debug_tool',name:'recall',call_id:'t1',phase:'started'});eq(face.snapshot().handdrawn.activity.id,'reading');
  face.handleEvent({type:'debug_tool',name:'remember',call_id:'t2',phase:'started'});eq(face.snapshot().handdrawn.activity.id,'writing');
  face.handleEvent({type:'debug_tool',name:'recall',call_id:'t1',phase:'finished'});eq(face.snapshot().handdrawn.activity.id,'writing');
  face.handleEvent({type:'debug_tool',name:'remember',call_id:'t2',phase:'finished'});eq(face.snapshot().handdrawn.activity.id,'planning','background resumes after foreground tool');
  face.handleEvent({type:'brain_job',job_id:'b1',status:'complete'});eq(face.snapshot().handdrawn.activity.phase,'exit');
  face.resetActivities();eq(face.snapshot().handdrawn.activity,null);
  eq(face.handleEvent({type:'debug_tool',name:'send_email',call_id:'fake',phase:'started'}),false,'no unsupported email access inferred');
  face.handleEvent({type:'state',state:'thinking'});eq(face.snapshot().handdrawn.activity.id,'planning');
  eq(face.previewActivity('searching').accepted,true,'manual activity preview starts');
  eq(face.snapshot().handdrawn.activity.id,'searching','preview covers live planning');
  face.handleEvent({type:'state',state:'checking'});
  eq(face.snapshot().handdrawn.activity.id,'searching','live state cannot displace manual preview');
  face.clearPreviewActivity();
  eq(face.snapshot().handdrawn.activity.id,'checking','release reveals newest live activity');
  face.handleEvent({type:'state',state:'listening'});eq(face.snapshot().handdrawn.activity.phase,'exit');
  face.resetActivities();
  face.handleEvent({type:'debug_tool',name:'recall',call_id:'ending-tool',phase:'started'});
  face.handleEvent({type:'debug_tool',name:'recall',call_id:'ending-tool',phase:'finished'});
  time+=200;
  face.handleEvent({type:'state',state:'thinking'});
  eq(face.snapshot().handdrawn.activity.id,'planning','eligible thinking replaces an exiting tool');
  time+=1400;eq(face.snapshot().handdrawn.activity.frame,7,'planning rests after a brief pulse');
  time+=2000;eq(face.snapshot().handdrawn.activity.frame,7,'planning does not pulse every second');
  const calm=runtime.create(null,{...options,reducedMotion:true});await calm.ready;
  calm.setActivity('planning');time+=700;
  const still=calm.snapshot().handdrawn.activity.frame;
  time+=300;eq(calm.snapshot().handdrawn.activity.frame,still,'reduced motion uses a still activity pose');
  calm.destroy();
  face.resetActivities();face.setActivity('reading');time+=1000;face.inspect({layers:'eyes'});
  const isolated=canvas();face.renderTo(isolated,time);
  const draws=isolated.calls.filter(c=>c.name==='drawImage');ok(draws.every(c=>!c.args[0].file.startsWith('frames/')),'eyes-only inspector never emits new eye or activity cels');
  ok(isolated.calls.some(c=>c.name==='stroke'),'original vector eye strokes remain visible');
  time=0;const comparison=runtime.create(null,options),original=MistDrawnFaceRuntime.create(null,options);
  await Promise.all([comparison.ready,original.ready]);let preservedSnapshots=0;
  for(const expression of MIST_DATA.faces){
    comparison.setExpression(expression.id);original.setExpression(expression.id);
    comparison.setActivity('reading',{owner:'identity'});
    for(let n=0;n<11;n++){
      time+=1000/15;const a=comparison.snapshot(time),b=original.snapshot(time);preservedSnapshots++;
      eq(a.drawnLayers,b.drawnLayers,'exact original layers throughout face transition');
      eq(a.drawnPlacements,b.drawnPlacements,'exact original raster lid and mouth placements');
    }
    comparison.blink();original.blink();
    for(let n=0;n<8;n++){
      time+=1000/15;const a=comparison.snapshot(time),b=original.snapshot(time);preservedSnapshots++;
      eq(a.drawnLayers,b.drawnLayers,'exact original blink contour');eq(a.drawnPlacements,b.drawnPlacements,'exact original blink cels');
    }
  }
  comparison.destroy();original.destroy();
  face.destroy();eq(face.update(),null);eq(face.setActivity('email').accepted,false);
  let rejectFetch,lateError=false;const originalFetch=global.fetch;
  global.fetch=()=>new Promise((resolve,reject)=>{rejectFetch=reject;});
  const doomed=runtime.create(null,{...options,manifest:undefined,onError:()=>lateError=true});doomed.destroy();rejectFetch(new Error('late network failure'));
  eq(await doomed.ready,false);eq(lateError,false,'destroy suppresses late error callback');global.fetch=originalFetch;
  let reported=false;const invalid=runtime.create(null,{...options,manifest:{...manifest,version:5},onError:()=>reported=true});
  eq(await invalid.ready,false);ok(reported);eq(invalid.snapshot().renderer,'drawn','fallback remains when manifest fails');invalid.destroy();
  const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const report={passed:true,checks,activitySamples:samples,renderedActivityFrames:rendered,expressionChecks,preservedOriginalSnapshots:preservedSnapshots,
    runtimeSha256:hash(path.join(dir,'runtime.js')),rendererSha256:hash(path.join(root,'brain/ui/static/drawn_face_renderer.js')),
    coverage:['all activity entrance/loop/exit sequences','real renderer canvas hook','activity-only PNG emission','40 exact original eye identities','760 exact original eye/transition/blink geometry comparisons','40 emotion mouth families','uniform whole-face layout','unquantized audio sample timing','stale completion and overlapping tools','background resumption','inspector layers','late async failure after destroy','missing assets fallback'],
    limits:['Canvas calls use image placeholders; visual PNG quality is checked separately.','No paid model or voice provider calls.']};
  fs.writeFileSync(path.join(dir,'runtime_checks.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
