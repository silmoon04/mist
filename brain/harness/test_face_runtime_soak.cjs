'use strict';
// Real runtime control flow and canvas commands; this is not a pixel/raster audit.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const ROOT=path.resolve(__dirname,'../..');global.window=global;
require('../face_assets/app/app_data.js');require('../ui/static/face_runtime.js');require('../ui/static/drawn_face_renderer.js');
const runtime=require('../art_direction/artist_studio_20260916/reuse/handdrawn_v6/runtime.js');
const manifest=JSON.parse(fs.readFileSync(path.join(ROOT,'brain/art_direction/artist_studio_20260916/reuse/handdrawn_v6/manifest.json')));
const atlas=JSON.parse(fs.readFileSync(path.join(ROOT,'brain/art_direction/artist_studio_20260916/reuse/drawn/atlas.json')));
const images={},handdrawnImages={};
for(const [id,a] of Object.entries(atlas.assets)){images[id]=images[a.file]={};for(const f of a.frames)if(f.file)images[f.file]={};}
for(const a of Object.values(manifest.assets))for(const f of a.frames)handdrawnImages[f.file]={};
global.Path2D=class{constructor(value){assert(!String(value).includes('NaN'));}};
let canvasPaints=0,commands=0,wall=0;
const ctx={};for(const name of ['save','restore','setTransform','beginPath','rect','clip','translate','rotate','scale','fill','stroke','drawImage'])ctx[name]=(...args)=>{commands++;for(const n of args)if(typeof n==='number')assert(Number.isFinite(n));};
ctx.clearRect=()=>{canvasPaints++;};
const canvas=()=>({width:1000,height:640,dataset:{},getContext:()=>ctx});
const options={data:MIST_DATA,atlas,images,manifest,handdrawnImages,autoStart:false,now:()=>wall};
(async()=>{
 const start=performance.now(),face=runtime.create(null,{...options,reducedMotion:false});assert(await face.ready);
 const target=canvas();let paints=0,geometryChecks=0,sample={active:true,viseme:'AA',amount:.7,time:0};
 face.startSpeech({sample:()=>sample,clock:()=>sample.time,duration:Infinity});
 // Ten minutes at 60 Hz, every face and activity recurring, with live mic samples.
 const updates=36000;
 for(let i=0;i<updates;i++){
  wall=i*1000/60;sample.time=wall/1000;sample.active=i%300<260;sample.viseme=['AA','OO','EE','MBP','rest'][Math.floor(i/30)%5];
  if(i%900===0)face.setExpression(MIST_DATA.faces[i/900%40].id);
  if(i%240===0)face.previewActivity(manifest.activities[Math.floor(i/240)%9].id);
  if(i%173===0)face.blink();
  face.setGaze(Math.sin(i*.013),Math.cos(i*.011));
  face.setListeningSignal({active:true,level:(i%100)/100,samples:Array.from({length:48},(_,n)=>Math.sin(n*.4+i*.1))});
  if(i%1200===0){target.width=i%2400?360:1000;target.height=i%2400?240:640;}
  const s=face.renderTo(target,wall);if(s.drawnPainted)paints++;
  assert.equal(s.speechTime,sample.time);
  if(i%15===0){
   const t=s.handdrawn.faceTransform,g={x:Math.sin(i*.013)*1.65,y:Math.cos(i*.011)*.8};
   const geo=runtime.helpers.listeningGeometry(s.drawnEyeBounds,t,g);
   for(const [a,b] of geo.segments){assert(a>=geo.left-1e-8&&b<=geo.right+1e-8&&b>a);for(const e of s.drawnEyeBounds){const left=t.x+(e.left+g.x)*t.scale-1.8*t.scale,right=t.x+(e.right+g.x)*t.scale+1.8*t.scale;assert(b<=left+1e-8||a>=right-1e-8);geometryChecks++;}}
  }
 }
 assert(paints<=updates/4+1000,'Combined signal updates remain bounded near 15 fps plus immediate closures');
 face.destroy();const after=canvasPaints;assert.equal(face.update(wall+1000),null);assert.equal(canvasPaints,after);
 const {PreviewLifecycle,VisiblePreviewScheduler}=await import('../duplex/static/animation_picker.mjs');
 let live=0,created=0,destroyed=0,nextId=0;const pending=new Map();
 const lifecycle=new PreviewLifecycle((item)=>{const f=runtime.create(null,{...options,expression:item.id,reducedMotion:false});live++;created++;const c=canvas();return {update(t){f.renderTo(c,t);},destroy(){f.destroy();live--;destroyed++;}};});
 const scheduler=new VisiblePreviewScheduler({requestFrame:cb=>{pending.set(++nextId,cb);return nextId;},cancelFrame:id=>pending.delete(id),now:()=>wall,onFrame:(ids,t)=>lifecycle.update(ids,t)});
 for(const f of MIST_DATA.faces)lifecycle.register(f.id,{id:f.id},{replaceChildren(){}});
 for(let cycle=0;cycle<100;cycle++){
  lifecycle.setOpen(true);scheduler.setOpen(true);
  for(let j=0;j<40;j++){const id=MIST_DATA.faces[j].id,visible=(j+cycle)%8===0;lifecycle.setVisible(id,visible);scheduler.setVisible(id,visible);}
  await Promise.resolve();await Promise.resolve();assert.equal(live,5);assert(pending.size<=1);
  for(let tick=0;tick<12;tick++){wall+=1000/60;const batch=[...pending.values()];pending.clear();for(const cb of batch)cb(wall);}
  scheduler.setReducedMotion(true);assert.equal(pending.size,0);scheduler.setReducedMotion(false);
  lifecycle.setOpen(false);scheduler.setOpen(false);assert.equal(live,0);assert.equal(pending.size,0);
  const closedPaints=canvasPaints;lifecycle.update(MIST_DATA.faces.map(f=>f.id),wall+100);assert.equal(canvasPaints,closedPaints);
 }
 lifecycle.destroy();scheduler.destroy();assert.equal(created,destroyed);assert.equal(lifecycle.records.size,0);assert.equal(scheduler.visible.size,0);
 const gentle=runtime.create(null,{...options,reducedMotion:true});assert(await gentle.ready);gentle.setListeningSignal({active:true,level:1,samples:[1,-1]});assert.equal(gentle.setListeningSignal({active:true,level:.2,samples:[-1,1]}),false);
 const resizeTarget=canvas();gentle.renderTo(resizeTarget,wall);resizeTarget.width=360;resizeTarget.height=240;
 const resizeRepaintWithinSameTick=gentle.renderTo(resizeTarget,wall).drawnPainted;
 assert.equal(resizeRepaintWithinSameTick,true,'Resizing clears the canvas bitmap and must repaint within the same animation tick');
 assert.equal(gentle.renderTo(resizeTarget,wall).drawnPainted,false,'An unchanged resized canvas must retain normal frame caching');
 gentle.destroy();
 const broken=runtime.create(null,{...options,manifest:{version:0}});assert.equal(await broken.ready,false);assert(broken.error);broken.destroy();
 const report={passed:true,simulatedMinutes:10,updates,faces:40,activities:9,paintedFrames:paints,geometryChecks,pickerCycles:100,createdPreviews:created,destroyedPreviews:destroyed,pendingFrames:pending.size,canvasCommands:commands,resizeRepaintWithinSameTick,findings:resizeRepaintWithinSameTick?[]:['Changing canvas width/height within the same animation tick does not invalidate paint cache; browser bitmap stays cleared until next tick'],elapsedMs:Math.round(performance.now()-start),limitations:['Canvas command instrumentation does not verify decoded PNG pixels or browser memory','Loading failure covers malformed manifest; network decode failures require browser verification']};
 fs.writeFileSync(path.join(__dirname,'face_runtime_soak_checks.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
