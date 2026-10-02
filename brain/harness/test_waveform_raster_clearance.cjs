'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),drawn=path.join(root,'brain/art_direction/artist_studio_20260916/reuse/drawn');
global.window=global;
require('../face_assets/app/app_data.js');require('../ui/static/face_runtime.js');require('../ui/static/drawn_face_renderer.js');
const runtime=require('../art_direction/artist_studio_20260916/reuse/handdrawn_v6/runtime.js');
const atlas=JSON.parse(fs.readFileSync(path.join(drawn,'atlas.json')));
const manifest=JSON.parse(fs.readFileSync(path.join(drawn,'../handdrawn_v6/manifest.json')));
// Decode accepted PNGs with the same alpha keying as keySheet, then expose real pixels to lidSupport.
const decoder=spawnSync(process.env.PYTHON||'python',['-c',String.raw`
from pathlib import Path
from PIL import Image
import json,base64,sys
root=Path(sys.argv[1]); atlas=json.loads((root/'atlas.json').read_text()); files={}; out={}
for asset in atlas['assets'].values():
 if asset.get('kind')!='eye': continue
 for f in asset['frames']: files.setdefault(f.get('file',asset['file']),[]).append(f)
for name,frames in files.items():
 im=Image.open(root/name).convert('RGBA'); px=list(im.getdata()); samples=px[::100]
 transparent=sum(p[3]<30 for p in samples); white=sum(p[3]>=30 and min(p[:3])>235 for p in samples)
 if white>transparent:
  vals=[]
  for r,g,b,a in px:
   low=min(r,g,b); high=max(r,g,b); factor=0 if high-low<22 else min(1,(255-low)/161)
   vals.append(round(a*(0 if factor<.035 else factor)))
  alpha=Image.new('L',im.size); alpha.putdata(vals)
 else: alpha=im.getchannel('A')
 out[name]={}
 for f in frames:
  key=','.join(str(f[k]) for k in ['x','y','w','h'])
  out[name][key]=base64.b64encode(alpha.crop((f['x'],f['y'],f['x']+f['w'],f['y']+f['h'])).tobytes()).decode()
print(json.dumps(out))
`,drawn],{encoding:'utf8',maxBuffer:32*1024*1024});
assert.equal(decoder.status,0,decoder.stderr);
const decoded=JSON.parse(decoder.stdout),images={},handdrawnImages={},pixelCache=new Map();
function pixels(file,frame){
 const key=[frame.x,frame.y,frame.w,frame.h].join(','),cacheKey=file+':'+key;
 if(!pixelCache.has(cacheKey))pixelCache.set(cacheKey,Buffer.from(decoded[file][key],'base64'));
 return pixelCache.get(cacheKey);
}
for(const [id,asset] of Object.entries(atlas.assets)){
 for(const file of new Set([asset.file,...asset.frames.map(frame=>frame.file).filter(Boolean)])){
  images[file]=decoded[file]?{getContext(){return {getImageData(x,y,w,h){
   const alpha=pixels(file,{x,y,w,h}),data=new Uint8Array(w*h*4);
   for(let i=0;i<alpha.length;i++)data[i*4+3]=alpha[i];return {data};
  }};}}:{};
 }
 images[id]=images[asset.file];
}
for(const asset of Object.values(manifest.assets))for(const frame of asset.frames)handdrawnImages[frame.file]={};
let time=0;
(async()=>{
 const face=runtime.create(null,{data:MIST_DATA,atlas,images,manifest,handdrawnImages,autoStart:false,reducedMotion:false,now:()=>time});
 assert.equal(await face.ready,true);
 const failures=[];let rasterChecks=0,vectorChecks=0,snapshots=0,collisionCount=0,envelopeCollisionCount=0;
 for(const definition of MIST_DATA.faces){
  face.setExpression(definition.id);
  for(let tick=0;tick<18;tick++){
   time+=1000/15;if(tick===10)face.blink();
   const state=face.snapshot(time);snapshots++;
   for(const transform of [{x:0,y:0,scale:1},{x:2,y:8.32,scale:.74},{x:14,y:15,scale:.72}]){
    for(const gaze of [{x:-1.65,y:-.8},{x:0,y:0},{x:1.65,y:.8}]){
     const geometry=runtime.helpers.listeningGeometry(state.drawnEyeBounds,transform,gaze);
     assert.equal(geometry.segments.length,3,definition.id+' preserves side traces and centre bridge');
     const plot=([x,y])=>[transform.x+(x+gaze.x)*transform.scale,transform.y+(y+gaze.y)*transform.scale];
     const radius=.275*transform.scale;
     const collides=(point,envelope=false)=>{
      const [x,y]=plot(point);
      const amplitude=envelope?2.2*transform.scale:0;
      const traceY=Math.max(geometry.y-amplitude,Math.min(geometry.y+amplitude,y));
      return geometry.segments.some(([a,b])=>Math.hypot(x-Math.max(a,Math.min(b,x)),y-traceY)<=radius);
     };
     for(const layer of [...state.drawnLayers,...state.drawnPlacements.filter(p=>p.layer).map(p=>p.layer)]){
      if(!layer.role.startsWith('eye'))continue;
      const pad=(layer.mode==='stroke'?layer.width/2:0)*transform.scale;
      const xs=layer.points.map(point=>plot(point)[0]),left=Math.min(...xs)-pad,right=Math.max(...xs)+pad;
      assert(geometry.segments.every(([a,b])=>b+radius<left||a-radius>right),'Vector eye retains horizontal clearance');vectorChecks++;
     }
     for(const placement of state.drawnPlacements){
      if(!placement.role.startsWith('eye')||placement.opacity<=0||placement.layer||!placement.offset)continue;
      const asset=atlas.assets[placement.id],frame=asset.frames[placement.frame],alpha=pixels(frame.file||asset.file,frame);
      const cx=frame.cx??frame.x+frame.w/2,cy=frame.cy??frame.y+frame.h/2,c=Math.cos(placement.rotation),s=Math.sin(placement.rotation);
      let collision=null,envelopeCollision=false;
      for(let n=0;n<alpha.length;n++){
       if(alpha[n]<=64)continue;
       const x=frame.x+n%frame.w+.5,y=frame.y+Math.floor(n/frame.w)+.5;
       const u=((x-cx)*(frame.sourceScale??1)-placement.offset.x)*placement.scale*(placement.mirror??1);
       const v=((y-cy)*(frame.sourceScale??1)-placement.offset.y)*placement.scale;
       const point=[placement.x+c*u-s*v,placement.y+s*u+c*v];
       if(collides(point,true))envelopeCollision=true;
       if(collides(point)){collision=point;break;}
      }
      rasterChecks++;
      if(collision)collisionCount++;
      if(envelopeCollision)envelopeCollisionCount++;
      if(collision&&transform.scale===1&&gaze.x===0)failures.push({expression:definition.id,tick,role:placement.role,pose:placement.pose,point:collision,traceY:geometry.y});
     }
    }
   }
  }
 }
 face.destroy();
 const report={passed:collisionCount===0&&envelopeCollisionCount===0,faces:MIST_DATA.faces.length,snapshots,rasterChecks,vectorChecks,collisionCount,envelopeCollisionCount,failures};
 console.log(JSON.stringify(report,null,2));
 assert.equal(collisionCount,0,'Actual accepted raster eyelid pixels overlap the flat microphone trace');
 assert.equal(envelopeCollisionCount,0,'Actual accepted raster eyelid pixels overlap the full microphone signal envelope');
})().catch(error=>{console.error(error);process.exitCode=1;});
