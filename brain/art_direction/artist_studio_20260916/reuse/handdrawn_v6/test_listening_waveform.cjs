const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../../../../..');global.window=global;
require(path.join(root,'brain/face_assets/app/app_data.js'));
require(path.join(root,'brain/ui/static/face_runtime.js'));
require(path.join(root,'brain/ui/static/drawn_face_renderer.js'));
const runtime=require('./runtime.js');
const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'manifest.json'))),atlas=JSON.parse(fs.readFileSync(path.join(__dirname,'../drawn/atlas.json')));
const images={},handdrawnImages={};
for(const [id,asset] of Object.entries(atlas.assets)){images[id]=images[asset.file]={};for(const frame of asset.frames)if(frame.file)images[frame.file]={};}
for(const asset of Object.values(manifest.assets))for(const frame of asset.frames)handdrawnImages[frame.file]={};
let time=0,checks=0,strokes=[];global.Path2D=class{constructor(value){this.value=value;}};
const context={};for(const name of ['save','restore','setTransform','clearRect','beginPath','rect','clip','translate','rotate','scale','fill','drawImage'])context[name]=()=>{};
context.stroke=path=>{if(context.strokeStyle==='#72cbb9')strokes.push(path.value);};
const canvas={width:1000,height:640,dataset:{},getContext:()=>context};
(async()=>{
  const face=runtime.create(null,{data:MIST_DATA,atlas,images,manifest,handdrawnImages,autoStart:false,reducedMotion:false,now:()=>time});assert.equal(await face.ready,true);
  face.setListeningSignal({active:true,level:1,samples:Array.from({length:48},(_,i)=>Math.sin(i*.45))});
  const families=new Set();
  for(const definition of MIST_DATA.faces){
    face.setExpression(definition.id);time+=1200;const shot=face.snapshot(time),eyes=shot.drawnEyeBounds;
    assert.equal(eyes.length,2,definition.id+' exposes both original eye extents');
    for(const role of ['eye_L','eye_R'])families.add(MIST_DATA.parts[definition.slots[role].part].family);
    for(const transform of [{x:0,y:0,scale:1},{x:2,y:8.32,scale:.74},{x:14,y:15,scale:.72}]){
      const geometry=runtime.helpers.listeningGeometry(eyes,transform,{x:.4,y:.2});
      assert.equal(geometry.segments.length,3,definition.id+' keeps left, bridge and right trace');
      for(const [left,right] of geometry.segments){assert.ok(right>left);for(const eye of eyes){const maskLeft=transform.x+(eye.left+.4)*transform.scale-1.8*transform.scale,maskRight=transform.x+(eye.right+.4)*transform.scale+1.8*transform.scale;assert.ok(right<=maskLeft+1e-8||left>=maskRight-1e-8,definition.id+' eye remains clear');checks++;}}
    }
    strokes=[];face.renderTo(canvas,time);assert.equal(strokes.length,3,definition.id+' draws three smooth sections');assert.ok(strokes.every(path=>path.includes(' Q')&&!path.includes('NaN')));checks+=2;
    face.blink();for(const offset of [0,80,160,320]){const geometry=runtime.helpers.listeningGeometry(face.snapshot(time+offset).drawnEyeBounds);assert.equal(geometry.segments.length,3);checks++;}
  }
  let paints=0;const start=time;
  for(let i=0;i<1000;i++){
    time=start+i;face.setListeningSignal({active:true,level:.8,samples:Array.from({length:48},(_,n)=>Math.sin(n*.4+i*.1))});
    if(face.renderTo(canvas,time).drawnPainted)paints++;
  }
  assert.ok(paints<=16,'1000 microphone revisions paint at most once per animation frame');checks++;
  for(let i=0;i<40;i++)face.setListeningSignal({active:true,level:0,samples:[]});
  assert.equal(face.setListeningSignal({active:true,level:0,samples:[]}),false,'Silence converges to exact zero and subsequent samples are no-ops');checks++;
  time+=100;strokes=[];face.renderTo(canvas,time);
  for(const path of strokes){const values=path.match(/-?\d+\.\d+/g).map(Number),ys=values.filter((_,index)=>index%2===1);assert.ok(Math.max(...ys)-Math.min(...ys)<.001,'Decayed trace is flat');checks++;}
  face.setListeningSignal({active:false});strokes=[];face.renderTo(canvas,time+100);assert.equal(strokes.length,0,'Disconnected microphone draws no trace');
  assert.equal(face.setListeningSignal({active:false}),false,'Repeated inactive calls are no-ops');checks++;
  face.destroy();
  const gentle=runtime.create(null,{data:MIST_DATA,atlas,images,manifest,handdrawnImages,autoStart:false,reducedMotion:true,now:()=>time});await gentle.ready;
  gentle.setListeningSignal({active:true,level:1,samples:[1,-1]});assert.equal(gentle.setListeningSignal({active:true,level:.2,samples:[-1,1]}),false,'Reduced motion ignores waveform and level changes');gentle.destroy();checks++;
  console.log(JSON.stringify({passed:true,checks,signalPaintsPerSecond:paints,faces:MIST_DATA.faces.length,eyeFamilies:[...families]},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
