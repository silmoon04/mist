/* Generated cels driven by the face runtime's audio clock. */
(function (root) {
  'use strict';
  const CYAN = [94, 234, 221];
  const TIMING={fps:15,blinkMs:400,closedStartMs:100,closedEndMs:100+2000/15,transitionMs:600};
  const FAMILY_PARTS = {
    round: ['circle_gap_02', 'circle_gap_03', 'open_C_43', 'open_C_50'],
    hook: ['angry_hook_00', 'droop_hook_08', 'droop_hook_09', 'droop_hook_10', 'circle_gap_04', 'open_C_42', 'open_C_46', 'open_C_47', 'open_C_49'],
    arc: ['closed_lid_arc_05', 'closed_lid_arc_06', 'closed_lid_arc_07', 'happy_arc_15', 'happy_arc_16', 'happy_arc_17', 'happy_arc_18', 'happy_arc_19'],
    loop: ['e_shape_11', 'e_shape_12', 'e_shape_13'],
    slot: ['open_C_44', 'open_C_45', 'open_C_48', 'open_C_51'],
    chevron: ['chevron_01'], bar: ['flat_bar_14']
  };
  const REFERENCES = {round:'circle_gap_03', hook:'angry_hook_00', arc:'happy_arc_16', loop:'e_shape_12', slot:'open_C_44', chevron:'chevron_01', bar:'flat_bar_14'};
  const PART_FAMILY = Object.fromEntries(Object.entries(FAMILY_PARTS).flatMap(([family, parts]) => parts.map(part => [part, family])));
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const ease = value => {const t = clamp(value, 0, 1); return t*t*(3-2*t);};
  const normalPose = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const axisAngle = angle => {while(angle>Math.PI/2)angle-=Math.PI;while(angle<=-Math.PI/2)angle+=Math.PI;return angle;};
  const bounds = (points, pad=0) => {
    const xs=points.map(p=>p[0]), ys=points.map(p=>p[1]);
    const x=Math.min(...xs)-pad, y=Math.min(...ys)-pad;
    return {x,y,w:Math.max(...xs)+pad-x,h:Math.max(...ys)+pad-y};
  };

  function resample(part) {
    const points=part.points.slice(); if(part.kind==='fill') points.push(points[0]);
    const lengths=[0];
    for(let i=1;i<points.length;i++) lengths.push(lengths.at(-1)+Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]));
    const result=[];
    for(let n=0;n<64;n++) {
      const t=lengths.at(-1)*n/(part.kind==='fill'?64:63); let i=1;
      while(i<lengths.length-1 && lengths[i]<t) i++;
      const amount=(t-lengths[i-1])/(lengths[i]-lengths[i-1]||1);
      result.push([points[i-1][0]+(points[i][0]-points[i-1][0])*amount,points[i-1][1]+(points[i][1]-points[i-1][1])*amount]);
    }
    const mean=[0,1].map(axis=>result.reduce((sum,p)=>sum+p[axis],0)/result.length);
    return result.map(p=>[p[0]-mean[0],p[1]-mean[1]]);
  }

  function align(source, target) {
    const a=resample(source), b=resample(target); let best={score:Infinity,rotation:0,mirror:false};
    for(const mirror of [false,true]) for(const reverse of [false,true]) for(let shift=0;shift<(source.kind==='fill'?64:1);shift+=4) {
      let dot=0,cross=0;
      for(let i=0;i<a.length;i++) {
        const x=a[i][0]*(mirror?-1:1),y=a[i][1],q=b[((reverse?63-i:i)+shift)%64];
        dot+=x*q[0]+y*q[1];cross+=x*q[1]-y*q[0];
      }
      const angle=Math.atan2(cross,dot),c=Math.cos(angle),s=Math.sin(angle);let score=0;
      for(let i=0;i<a.length;i++) {
        const x=a[i][0]*(mirror?-1:1),y=a[i][1],q=b[((reverse?63-i:i)+shift)%64];
        score+=(c*x-s*y-q[0])**2+(s*x+c*y-q[1])**2;
      }
      score+=Math.abs(angle)*1e-8+(mirror?1e-8:0);
      if(score<best.score) best={score,rotation:angle,mirror};
    }
    return best;
  }

  function poseIndex(asset, names, fallback=0) {
    for(const name of names) {
      const index=asset.frames.findIndex(frame=>normalPose(frame.pose)===normalPose(name));
      if(index>=0) return index;
    }
    return clamp(fallback,0,asset.frames.length-1);
  }

  function blinkPose(asset, age) {
    if(age<0 || age>=TIMING.blinkMs) return poseIndex(asset,['rest','open']);
    const detailed=asset.frames.some(frame=>normalPose(frame.pose)==='nearclosed');
    const sequence=asset.blinkSequence || (detailed?[
      {until:40,pose:'anticipation'}, {until:80,pose:'closing'},
      {until:TIMING.closedStartMs,pose:'near_closed'}, {until:TIMING.closedEndMs,pose:'closed'},
      {until:300,pose:'opening_low'}, {until:350,pose:'opening'},
      {until:TIMING.blinkMs,pose:'settled'}
    ]:[
      {until:40,pose:'anticipation'}, {until:TIMING.closedStartMs,pose:'closing'},
      {until:TIMING.closedEndMs,pose:'closed'}, {until:333.3333333333,pose:'opening'},
      {until:TIMING.blinkMs,pose:'settled'}
    ]);
    const phase=sequence.find(item=>age<item.until) || sequence.at(-1);
    return poseIndex(asset,[phase.pose,phase.pose==='closed'?'squeeze':'rest']);
  }

  function mouthPose(asset, viseme, amount=1) {
    const key=normalPose(viseme);
    const quiet=amount<.28;
    const aliases={rest:['rest','neutral'],mbp:['MBP','closed'],
      aa:quiet?['release','small_AA','AA']:amount<.5?['small_AA','AA']:['AA','small_AA'],smallaa:['small_AA','AA'],
      eh:quiet?['wide_close','EH','EE']:['EH','EE'],ee:quiet?['wide_close','EE','EH']:['EE','EH'],
      oh:amount<.35?['round_close','OH','OO']:['OH','OO'],oo:amount<.35?['round_close','OO','OH']:['OO','OH'],
      fv:['FV','S','release','MBP'],lnt:['LNT','L','EH'],s:['S','EE'],th:['TH','LNT'],
      release:['release','small_AA'],wideclose:['wide_close','MBP'],roundclose:['round_close','OO'],
      smile:asset.allowSmileCue!==false&&(asset.id==='mouth_smile'||asset.emotion==='happy')?['SMILE','EE']:quiet?['wide_close','EE','rest']:['EE','EH','rest']};
    return poseIndex(asset,aliases[key]||['rest']);
  }

  function mouthAssetForFace(faceId,atlas) {
    if(atlas?.mouthFamilies)return atlas.mouthFamilies[faceId]??null;
    const preferred=/happy|love/.test(faceId)||faceId==='37_misc_06_manual'?'mouth_smile':'mouth_neutral';
    return atlas?.assets[preferred]?preferred:'mouth_neutral';
  }

  function colorizePixels(pixels) {
    let transparent=0,white=0;
    for(let n=0;n<pixels.length;n+=400) {
      if(pixels[n+3]<30) transparent++;
      else if(Math.min(pixels[n],pixels[n+1],pixels[n+2])>235) white++;
    }
    const whiteBackground=white>transparent;
    for(let n=0;n<pixels.length;n+=4) {
      if(whiteBackground) {
        const low=Math.min(pixels[n],pixels[n+1],pixels[n+2]),high=Math.max(pixels[n],pixels[n+1],pixels[n+2]);
        const alpha=high-low<22?0:Math.min(1,(255-low)/161);
        pixels[n+3]*=alpha<.035?0:alpha;
      }
      pixels[n]=CYAN[0];pixels[n+1]=CYAN[1];pixels[n+2]=CYAN[2];
    }
    return pixels;
  }

  function keySheet(image, document) {
    const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    const context=canvas.getContext('2d',{willReadFrequently:true});context.drawImage(image,0,0);
    const pixels=context.getImageData(0,0,canvas.width,canvas.height);colorizePixels(pixels.data);context.putImageData(pixels,0,0);
    return canvas;
  }

  function dimensions(asset) {
    const first=asset.frames[0];return {w:asset.masterWidth||first.w,h:asset.masterHeight||first.h};
  }

  function upperLidAngle(points) {
    const box=bounds(points),samples=[];let longest=null;
    for(let i=1;i<10;i++) {
      const group=points.filter(p=>p[0]>=box.x+box.w*(i-.5)/10&&p[0]<box.x+box.w*(i+.5)/10);
      if(group.length)samples.push([box.x+box.w*i/10,Math.min(...group.map(p=>p[1]))]);
    }
    for(let i=0;i<points.length-5;i++) {
      let length=0;
      for(let j=i+1;j<points.length;j++) {
        length+=Math.hypot(points[j][0]-points[j-1][0],points[j][1]-points[j-1][1]);
        const a=points[i],b=points[j],chord=Math.hypot(b[0]-a[0],b[1]-a[1]);
        if(j-i<4||Math.abs(b[0]-a[0])<box.w*.45||(a[1]+b[1])/2>box.y+box.h*.72||length/chord>1.012)continue;
        if(!longest||chord>longest.chord)longest={chord,angle:axisAngle(Math.atan2(b[1]-a[1],b[0]-a[0]))};
      }
    }
    const mx=samples.reduce((sum,p)=>sum+p[0],0)/samples.length,my=samples.reduce((sum,p)=>sum+p[1],0)/samples.length;
    const slope=Math.atan2(samples.reduce((sum,p)=>sum+(p[0]-mx)*(p[1]-my),0),samples.reduce((sum,p)=>sum+(p[0]-mx)**2,0));
    return {slope:Number.isFinite(slope)?slope:0,straight:longest?.angle};
  }

  const RIG_CACHE=new WeakMap();
  function create(container, options={}) {
    const runtime=options.runtime||root.MistFaceRuntime, data=options.data||root.MIST_DATA;
    if(!runtime || !data) throw new Error('MIST face runtime and data must load first');
    const now=options.now||(()=>root.performance.now());
    const reduced=options.reducedMotion??!!root.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const document=container?.ownerDocument||root.document;
    let fallbackHost=null,canvas=null,context=null;
    if(container) {
      fallbackHost=document.createElement('div');fallbackHost.style.cssText='position:absolute;inset:0';
      canvas=document.createElement('canvas');canvas.width=Number.isFinite(options.previewCanvasWidth)?Math.round(clamp(options.previewCanvasWidth,100,1000)):1000;canvas.height=Math.round(canvas.width*.64);canvas.hidden=true;
      canvas.style.cssText='display:none;position:absolute;inset:0;width:100%;height:100%;object-fit:contain';
      canvas.setAttribute('aria-hidden','true');
      const position=container.style.position||root.getComputedStyle?.(container)?.position;
      if(!position||position==='static') container.style.position='relative';
      container.append(fallbackHost,canvas);context=canvas.getContext('2d');
    }
    const base=runtime.create(fallbackHost,{...options,autoStart:false});
    let byRuntime=RIG_CACHE.get(data);
    if(!byRuntime){byRuntime=new WeakMap();RIG_CACHE.set(data,byRuntime);}
    let geometry=byRuntime.get(runtime);
    if(!geometry){
    const faces=new Map(data.faces.map(face=>[face.id,face]));
    const rigs=new Map();
    const alignments={};
    for(const [part,family] of Object.entries(PART_FAMILY)) if(data.parts[part]) alignments[part]=align(data.parts[REFERENCES[family]],data.parts[part]);
    for(const face of data.faces) {
      const still=runtime.create(null,{data,expression:face.id,reducedMotion:true,autoStart:false,now:()=>0});
      const layers=still.snapshot(0).layers;still.destroy();
      const role=layers.find(layer=>face.slots[layer.role]),slot=face.slots[role.role],part=data.parts[slot.part];
      const angle=(slot.rot||0)*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle);
      const raw=part.points.map(([px,py])=>{const x=px*(slot.mirror?-1:1);return [slot.x+c*x-s*py,slot.y+s*x+c*py];});
      let index=1;while(index<raw.length && Math.hypot(raw[index][0]-raw[0][0],raw[index][1]-raw[0][1])<.001) index++;
      const scale=index<raw.length?Math.hypot(role.points[index][0]-role.points[0][0],role.points[index][1]-role.points[0][1])/Math.hypot(raw[index][0]-raw[0][0],raw[index][1]-raw[0][1]):1;
      rigs.set(face.id,{face,layers,scale,dx:role.points[0][0]-raw[0][0]*scale,dy:role.points[0][1]-raw[0][1]*scale});
    }
      geometry={faces,rigs,alignments};byRuntime.set(runtime,geometry);
    }
    const {faces,rigs,alignments}=geometry;
    let atlas=options.atlas||null,images=options.images||{},loaded=!!atlas && !!options.images,error=null,dead=false,raf=null;
    let current=base.expression,transition=null;
    let started=now(),blinkAt=-Infinity,nextBlink=started+3350,blinkCount=0,gaze=null;
    let fps=clamp(options.fps??TIMING.fps,1,60),frameMs=1000/fps,paintHistory=new WeakMap(),renderRevision=0;
    const animationFrame=time=>Math.floor((time-started)/frameMs+1e-8);
    const animationTime=time=>started+animationFrame(time)*frameMs;
    let speechSample=null,speechFamily=null,lastSpeechCue=null,pendingSpeechFamily=null,familySwitchReason='initial';
    const inkSupports=new Map(),lidSupports=new Map(),blinkPlanes=new Map();

    function inkSupport(id) {
      if(inkSupports.has(id))return inkSupports.get(id);
      const asset=atlas.assets[id],frame=asset.frames[0],master=dimensions(asset),image=images[id];
      const points=[];let method='source-contour-fallback';
      if(image?.getContext) {
        const pixels=image.getContext('2d',{willReadFrequently:true}).getImageData(frame.x,frame.y,frame.w,frame.h).data;
        const cx=frame.cx??frame.x+frame.w/2,cy=frame.cy??frame.y+frame.h/2;
        for(let y=0;y<frame.h;y++) {
          let left=frame.w,right=-1;
          for(let x=0;x<frame.w;x++)if(pixels[(y*frame.w+x)*4+3]>64){left=Math.min(left,x);right=x;}
          if(right>=0)for(const x of [left,right+1])points.push([frame.x+x-cx,frame.y+y-cy+master.h/2]);
        }
        if(points.length)method='measured-raster';
      }
      if(!points.length) {
        const part=data.parts[REFERENCES[id]],box=bounds(part.points,(part.width||0)/2),cx=box.x+box.w/2,cy=box.y+box.h/2;
        const radius=part.kind==='fill'?0:(part.width||0)/2;
        for(const [x,y] of part.points)for(let i=0;i<(radius?8:1);i++) {
          const angle=i*Math.PI/4;points.push([(x+Math.cos(angle)*radius-cx)*master.w/box.w,(y+Math.sin(angle)*radius-cy)*master.h/box.h]);
        }
      }
      const result={points,method};inkSupports.set(id,result);return result;
    }

    const layerCenter=layer=>{const box=bounds(layer.points,layer.mode==='stroke'?layer.width/2:0);return [box.x+box.w/2,box.y+box.h/2];};
    const translated=(layer,x,y,opacity=1)=>{
      const center=layerCenter(layer),dx=x-center[0],dy=y-center[1];return {...layer,opacity:(layer.opacity??1)*opacity,
        points:layer.points.map(p=>[p[0]+dx,p[1]+dy])};
    };
    const mix=(a,b,t)=>a+(b-a)*t;
    function blinkPlane(rig,role) {
      const key=rig.face.id+':'+role;if(blinkPlanes.has(key))return blinkPlanes.get(key);
      const layer=rig.layers.find(item=>item.role===role),family=PART_FAMILY[layer.part],points=layer.points;
      const centers=['eye_L','eye_R'].map(side=>layerCenter(rig.layers.find(item=>item.role===side)));
      const eyeLine=axisAngle(Math.atan2(centers[1][1]-centers[0][1],centers[1][0]-centers[0][0]));
      const upper=upperLidAngle(points),ends=axisAngle(Math.atan2(points.at(-1)[1]-points[0][1],points.at(-1)[0]-points[0][0]));
      const degree=Math.PI/180;
      const angle=family==='round'||family==='chevron'?clamp(eyeLine,-4*degree,4*degree):
        family==='hook'?clamp((upper.straight??upper.slope)*.65,-18*degree,18*degree):
        family==='loop'?clamp((upper.straight??upper.slope)*.6,-10*degree,10*degree):
        family==='arc'?clamp(ends,-20*degree,20*degree):clamp(family==='bar'?ends:upper.slope,-7*degree,7*degree);
      const center=layerCenter(layer),c=Math.cos(angle),s=Math.sin(angle);
      const local=points.map(([x,y])=>[c*(x-center[0])+s*(y-center[1]),-s*(x-center[0])+c*(y-center[1])]);
      const box=bounds(local,layer.mode==='stroke'?layer.width/2:0),u=box.x+box.w/2,v=box.y+box.h*.72;
      const result={angle,center,box,x:center[0]+c*u-s*v,y:center[1]+s*u+c*v,width:box.w,family,
        preserveContour:family==='arc'||family==='bar'};
      blinkPlanes.set(key,result);return result;
    }
    function lidSupport(id) {
      if(lidSupports.has(id))return lidSupports.get(id);
      const asset=atlas.assets[id],frame=asset.frames[poseIndex(asset,['closed'])],ink=frame.inkBounds||frame;
      const cx=frame.cx??frame.x+frame.w/2,cy=frame.cy??frame.y+frame.h/2,points=[];
      // Measured from the accepted sheets; headless consumers may supply image handles without pixels.
      const fallback={round:-.070343846852,hook:10.295432606808,arc:-.59840834891,loop:-.190763761345,slot:.479462762011,chevron:4.131636607298,bar:-.696800837704};
      let angle=(fallback[id]||0)*Math.PI/180,method='accepted-sheet-PCA';
      const image=images[frame.file||id];
      if(image?.getContext)try {
        const pixels=image.getContext('2d',{willReadFrequently:true}).getImageData(frame.x,frame.y,frame.w,frame.h).data;
        for(let y=0;y<frame.h;y++)for(let x=0;x<frame.w;x++)if(pixels[(y*frame.w+x)*4+3]>64)points.push([frame.x+x-cx,frame.y+y-cy]);
        if(points.length>2) {
          const mx=points.reduce((sum,p)=>sum+p[0],0)/points.length,my=points.reduce((sum,p)=>sum+p[1],0)/points.length;
          let xx=0,yy=0,xy=0;for(const [x,y]of points){xx+=(x-mx)**2;yy+=(y-my)**2;xy+=(x-mx)*(y-my);}
          angle=.5*Math.atan2(2*xy,xx-yy);method='measured-raster-PCA';
        }
      } catch(_) { /* Optional pixel access is absent in some headless renderers. */ }
      if(!points.length)for(const x of [ink.x,ink.x+ink.w])for(const y of [ink.y,ink.y+ink.h])points.push([x-cx,y-cy]);
      const c=Math.cos(angle),s=Math.sin(angle),projected=bounds(points.map(([x,y])=>[c*x+s*y,-s*x+c*y]));
      const result={angle,method,width:projected.w,offset:{x:ink.x+ink.w/2-cx,y:ink.y+ink.h/2-cy}};
      lidSupports.set(id,result);return result;
    }
    function squeezedLayer(view,closure=view.closure) {
      const rig=rigs.get(view.rigId),source=rig.layers.find(layer=>layer.role===view.role),plane=blinkPlane(rig,view.role);
      const layer=translated(source,view.x,view.y,view.opacity),center=layerCenter(layer),c=Math.cos(plane.angle),s=Math.sin(plane.angle);
      // An already closed arc keeps its signed curvature and its endpoint direction.
      return {...layer,points:layer.points.map(([x,y])=>{
        const u=c*(x-center[0])+s*(y-center[1]),v=(-s*(x-center[0])+c*(y-center[1]))*(1-.22*closure);
        return[center[0]+c*u-s*v,center[1]+s*u+c*v];
      })};
    }
    function lidPlacement(view,frameIndex) {
      const rig=rigs.get(view.rigId),plane=blinkPlane(rig,view.role),id=plane.family,support=lidSupport(id),mirror=rig.face.slots[view.role].mirror?-1:1;
      return {kind:'eyelid',role:view.role,id,frame:frameIndex,rigId:view.rigId,pose:view.pose,opacity:view.opacity,
        x:plane.x+view.x-plane.center[0],y:plane.y+view.y-plane.center[1],rotation:plane.angle-mirror*support.angle,mirror,
        scale:plane.width/support.width,offset:support.offset,planeAngle:plane.angle,sourceAngle:support.angle,registration:support.method};
    }
    function trimBlinkStroke(layer,plane,mainOnly) {
      const c=Math.cos(plane.angle),s=Math.sin(plane.angle),distance=point=>-s*(point[0]-plane.x)+c*(point[1]-plane.y)-plane.top;
      const segments=[];let current=[];
      const finish=()=>{if(current.length>1)segments.push(current);current=[];};
      for(let i=1;i<layer.points.length;i++) {
        const a=layer.points[i-1],b=layer.points[i],da=distance(a),db=distance(b),insideA=da>=0,insideB=db>=0;
        if(insideA&&insideB) {if(!current.length)current.push(a);current.push(b);}
        else if(insideA!==insideB) {
          const t=da/(da-db),cut=[mix(a[0],b[0],t),mix(a[1],b[1],t)];
          if(insideA){if(!current.length)current.push(a);current.push(cut);finish();}
          else current=[cut,b];
        } else finish();
      }
      finish();
      if(mainOnly&&segments.length>1) {
        const length=points=>points.slice(1).reduce((sum,p,i)=>sum+Math.hypot(p[0]-points[i][0],p[1]-points[i][1]),0);
        return [segments.reduce((longest,points)=>length(points)>length(longest)?points:longest)];
      }
      return segments;
    }
    function blinkClosure(age) {
      const keys=[[0,0],[40,.18],[80,.7],[TIMING.closedStartMs,1],[TIMING.closedEndMs,1],[300,.4],[350,.06],[TIMING.blinkMs,0]];
      for(let i=1;i<keys.length;i++)if(age<keys[i][0]) {
        const a=keys[i-1],b=keys[i];return mix(a[1],b[1],ease((age-a[0])/(b[0]-a[0])));
      }
      return 0;
    }
    function restLayout(id) {
      const rig=rigs.get(id);
      return rig.layers.map(layer=>{const [x,y]=layerCenter(layer);return {rigId:id,role:layer.role,part:layer.part,x,y,opacity:1,closure:0,opening:false,pose:'rest'};});
    }
    function weightedCenter(items,fallback) {
      const weight=items.reduce((sum,item)=>sum+item.opacity,0);
      return weight>.000001?[items.reduce((sum,item)=>sum+item.x*item.opacity,0)/weight,items.reduce((sum,item)=>sum+item.y*item.opacity,0)/weight]:fallback;
    }
    function closingPose(closure,opening,id) {
      const asset=atlas?.assets[id];
      if(closure<=.001)return 'rest';
      if(closure>=.94)return 'closed';
      if(opening) {
        if(closure>=.6&&asset?.frames.some(f=>f.pose==='opening_low'))return 'opening_low';
        return closure>.16?'opening':'settled';
      }
      if(closure>=.65&&asset?.frames.some(f=>f.pose==='near_closed'))return 'near_closed';
      return closure>.22?'closing':'anticipation';
    }
    function mouthLayer(view) {
      if(view.restLayer)return view.restLayer;
      return rigs.get(view.rigId).layers.find(layer=>layer.role==='mouth');
    }
    function speechGeometry(view) {
      if(view.speechWidth!==undefined)return {width:view.speechWidth,rotation:view.speechRotation};
      const rig=rigs.get(view.rigId),id=mouthAsset(rig);
      if(!atlas?.assets[id]) {
        const layer=mouthLayer(view),box=bounds(layer.points,layer.mode==='stroke'?layer.width/2:0);
        return {width:Math.max(8,box.w),rotation:(rig.face.slots.mouth?.rot||0)*Math.PI/180};
      }
      const placement=mouthPlacement(rig,id,[view.x,view.y]);
      return {width:placement.scale*placement.master.w,rotation:placement.rotation};
    }
    function closedMouthLayer(source,target,t,x,y) {
      const opening=t>=.5,layer=opening?(target||source):(source||target);
      if(!layer)return null;
      if(t>=.38&&t<=.62)return {role:'mouth',part:'transition-closure',mode:'stroke',width:3.2,opacity:1,points:[[x-7,y],[x+7,y]]};
      const b=bounds(layer.points,layer.mode==='stroke'?layer.width/2:0),cx=b.x+b.w/2,cy=b.y+b.h/2;
      const collapse=opening?1-ease((t-.62)/.38):ease(t/.38);
      const padding=layer.mode==='stroke'?layer.width:0,w=Math.max(.001,b.w-padding),h=Math.max(.001,b.h-padding);
      const sx=mix(1,(14+(layer.mode==='fill'?3.2:0))/w,collapse),sy=mix(1,layer.mode==='fill'?3.2/h:0,collapse);
      return {...layer,opacity:1,width:layer.mode==='stroke'?mix(layer.width,3.2,collapse):0,
        points:layer.points.map(([px,py])=>[x+(px-cx)*sx,y+(py-cy)*sy])};
    }
    function closedEyeSpec(view) {
      if(view.kind==='eye_bridge')return {x:view.x,y:view.y,width:view.width,angle:view.angle,contour:view.contour};
      const rig=rigs.get(view.rigId),plane=blinkPlane(rig,view.role);
      const x=(plane.preserveContour?plane.center[0]:plane.x)+view.x-plane.center[0];
      const y=(plane.preserveContour?plane.center[1]:plane.y)+view.y-plane.center[1];
      let contour=null;
      if(plane.preserveContour) {
        const layer=squeezedLayer({...view,x,y},1),source={points:layer.points,kind:layer.mode};
        contour=resample(source);
        const contourBox=bounds(contour);contour=contour.map(([px,py])=>[px-contourBox.x-contourBox.w/2,py-contourBox.y-contourBox.h/2]);
        if(contour[0][0]>contour.at(-1)[0])contour.reverse();
        const c=Math.cos(plane.angle),s=Math.sin(plane.angle);
        contour=contour.map(([px,py])=>[(c*px+s*py)/plane.width,(-s*px+c*py)/plane.width]);
      }
      return {x,y,width:plane.width,angle:plane.angle,contour};
    }
    function layoutAt(time) {
      if(!transition||time>=transition.at+transition.duration-.000001) {
        const age=time-blinkAt;
        return restLayout(current).map(view=>{
          if(!view.role.startsWith('eye')||age<0||age>=TIMING.blinkMs)return view;
          const asset=atlas?.assets[PART_FAMILY[view.part]];if(!asset)return view;
          const pose=asset.frames[blinkPose(asset,age)].pose;
          return {...view,pose,closure:blinkClosure(age),opening:age>=TIMING.closedEndMs};
        });
      }
      const t=clamp((time-transition.at)/transition.duration,0,1),from=transition.from,to=transition.to;
      if(t===0)return from.map(view=>({...view}));
      const roles=new Set([...from.map(view=>view.role),...to.map(view=>view.role)]),result=[];
      for(const role of roles) {
        const sources=from.filter(view=>view.role===role),target=to.find(view=>view.role===role);
        if(role.startsWith('eye')&&target&&sources.length) {
          const source=sources.reduce((a,b)=>a.opacity>b.opacity?a:b);
          if(t<transition.closeUntil) {
            const closure=mix(source.closure,1,ease(t/transition.closeUntil));
            const pose=closure-source.closure<.1?source.pose:closingPose(closure,false,PART_FAMILY[source.part]);
            result.push({...source,opacity:1,closure,pose,opening:false,transitionClosed:true});
          } else if(t>.65) {
            const closure=1-ease((t-.65)/.35);
            result.push({...target,opacity:1,closure,opening:true,pose:closingPose(closure,true,PART_FAMILY[target.part]),transitionClosed:true});
          } else {
            const a=closedEyeSpec(source),b=closedEyeSpec(target),move=ease((t-transition.closeUntil)/(.65-transition.closeUntil));
            result.push({kind:'eye_bridge',role,part:'shared-closed-lid',rigId:target.rigId,
              x:mix(a.x,b.x,move),y:mix(a.y,b.y,move),width:mix(a.width,b.width,move),angle:mix(a.angle,b.angle,move),
              contour:a.contour||b.contour?Array.from({length:64},(_,i)=>{
                const ap=a.contour?.[i]||[i/63-.5,0],bp=b.contour?.[i]||[i/63-.5,0];return[mix(ap[0],bp[0],move),mix(ap[1],bp[1],move)];
              }):null,
              opacity:1,closure:1,opening:false,pose:'closed'});
          }
          continue;
        }
        if(role==='mouth') {
          const source=sources.length?sources.reduce((a,b)=>a.opacity>b.opacity?a:b):null;
          const a=source?[source.x,source.y]:[target.x,target.y],b=target?[target.x,target.y]:a,move=ease(t);
          const x=mix(a[0],b[0],move),y=mix(a[1],b[1],move);
          const opacity=source&&target?1:source?1-ease(t/.65):ease((t-.35)/.65);
          if(opacity>.000001) {
            const restLayer=closedMouthLayer(source?mouthLayer(source):null,target?mouthLayer(target):null,t,x,y);
            const sourceRig=source?.speechFromRig||source?.rigId||target.rigId,targetRig=target?.rigId||sourceRig;
            const sourceSpeech=speechGeometry(source||target),targetSpeech=speechGeometry(target||source);
            result.push({kind:'mouth_bridge',role,part:restLayer.part,rigId:t<.5?sourceRig:targetRig,
              speechFromRig:sourceRig,speechToRig:targetRig,speechProgress:move,
              speechWidth:mix(sourceSpeech.width,targetSpeech.width,move),speechRotation:mix(sourceSpeech.rotation,targetSpeech.rotation,move),
              x,y,opacity,closure:0,opening:t>.5,pose:'closure',restLayer});
          }
          continue;
        }
        const sourceCenter=weightedCenter(sources,target?[target.x,target.y]:[50,32]);
        const targetCenter=target?[target.x,target.y]:sourceCenter;
        const eye=role.startsWith('eye'),mouth=role==='mouth';
        const travel=ease(eye?(t-.24)/.4:t);
        const center=[mix(sourceCenter[0],targetCenter[0],travel),mix(sourceCenter[1],targetCenter[1],travel)];
        const exchange=ease(eye?(t-.36)/.24:mouth?(t-.2)/.6:t);
        for(const source of sources) {
          const opacity=source.opacity*(1-exchange);if(opacity<=.000001)continue;
          const closure=eye?mix(source.closure,1,ease(t/.25)):0;
          const id=PART_FAMILY[source.part];
          const pose=eye&&(closure-source.closure<.1)?source.pose:closingPose(closure,false,id);
          result.push({...source,x:source.x+center[0]-sourceCenter[0],y:source.y+center[1]-sourceCenter[1],opacity,closure,opening:false,pose});
        }
        if(target&&exchange>.000001) {
          const closure=eye?1-ease((t-.64)/.36):0;
          result.push({...target,x:center[0],y:center[1],opacity:exchange,closure,opening:eye,pose:closingPose(closure,true,PART_FAMILY[target.part])});
        }
      }
      return result;
    }
    function mouthAsset(rig) {
      return mouthAssetForFace(rig.face.id,atlas);
    }
    function queueSpeechFamily(target,time) {
      if(!target||target===speechFamily)pendingSpeechFamily=null;
      else if(pendingSpeechFamily?.target!==target)pendingSpeechFamily={target,since:time};
    }
    function speechFamilyAt(state,sampledTime) {
      const target=mouthAssetForFace(current,atlas),cue=normalPose(state.viseme||'rest');
      const closed=!state.speaking||cue==='rest'||cue==='mbp';
      const boundary=lastSpeechCue!==null&&lastSpeechCue!==cue;
      queueSpeechFamily(target,state.time);
      const age=pendingSpeechFamily?sampledTime-pendingSpeechFamily.since:0;
      const quiet=age>=150&&Number.isFinite(state.amount)&&state.amount<.35;
      const expired=age>=400-1e-6;
      if(!speechFamily||closed||boundary||quiet||expired) {
        if(target&&target!==speechFamily)familySwitchReason=!speechFamily?'initial':closed?'closed-cue':boundary?'cue-boundary':quiet?'quiet-trough':'bounded-timeout';
        speechFamily=target||speechFamily;pendingSpeechFamily=null;
      }
      lastSpeechCue=state.speaking?cue:null;
      return speechFamily;
    }
    function eyePlacement(rig, role, frameIndex) {
      const slot=rig.face.slots[role];if(!slot) return null;
      const id=PART_FAMILY[slot.part],asset=atlas?.assets[id];if(!asset) return null;
      const part=data.parts[slot.part],box=bounds(part.points,(part.width||0)/2),master=dimensions(asset),fit=alignments[slot.part];
      const support=inkSupport(id),c=Math.cos(fit.rotation),s=Math.sin(fit.rotation);
      const ink=bounds(support.points.map(([px,y])=>{const x=px*(fit.mirror?-1:1);return[c*x-s*y,s*x+c*y];}));
      return {role,id,frame:frameIndex,slot,box,master,fit,inkFit:support.method,
        inkOffset:{x:-(ink.x+ink.w/2),y:-(ink.y+ink.h/2)},scale:Math.min(box.w/ink.w,box.h/ink.h)};
    }
    function mouthPlacement(rig,id,anchor=null,speechGeometry=null) {
      const layer=rig.layers.find(item=>item.role==='mouth');if(!layer) return null;
      const slot=rig.face.slots.mouth,asset=atlas.assets[id],master=dimensions(asset);
      const box=bounds(layer.points,layer.mode==='stroke'?layer.width/2:0),x=anchor?.[0]??box.x+box.w/2,y=anchor?.[1]??box.y+box.h/2;
      const part=data.parts[slot.part],raw=bounds(part.points,(part.width||0)/2);
      const angle=speechGeometry?.rotation??(slot.rot||0)*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle),mirror=slot.mirror?-1:1;
      // A resting dot still needs a readable aperture while speaking.
      let scale=(speechGeometry?.width??Math.max(8,raw.w*rig.scale))/master.w;
      for(const frame of asset.frames) {
        const cx=frame.cx??frame.x+frame.w/2,cy=frame.cy??frame.y+frame.h/2;
        const sourceScale=frame.sourceScale??1;
        for(const [px,py] of [[frame.x,frame.y],[frame.x+frame.w,frame.y],[frame.x,frame.y+frame.h],[frame.x+frame.w,frame.y+frame.h]]) {
          const dx=(px-cx)*sourceScale*mirror,dy=(py-cy)*sourceScale-master.h/2;
          const rx=c*dx-s*dy,ry=s*dx+c*dy;
          if(rx>0)scale=Math.min(scale,(94-x)/rx);if(rx<0)scale=Math.min(scale,(x-6)/-rx);
          if(ry>0)scale=Math.min(scale,(58-y)/ry);if(ry<0)scale=Math.min(scale,(y-6)/-ry);
        }
      }
      return {role:'mouth',id,x,y,scale:Math.max(.0001,scale),rotation:angle,mirror,master};
    }

    function stateAt(time=now(),overrides={}) {
      let state=base.snapshot(time);
      if(speechSample) {
        const sample=speechSample()||{};
        state={...state,speaking:!!sample.active,viseme:sample.active?(sample.viseme||'rest'):'rest',
          amount:sample.active?(sample.amount??1):0,speechTime:sample.time??null,syncSource:sample.source||'audio-clock'};
      }
      const sampledTime=animationTime(time),age=sampledTime-blinkAt,visuals=layoutAt(sampledTime),placements=[],layers=[];
      const family=Number.isInteger(overrides.mouthFrame)?null:speechFamilyAt(state,sampledTime);
      for(const view of visuals)if(view.role.startsWith('eye')) {
        const requested=typeof overrides.blinkFrame==='number'?overrides.blinkFrame:overrides.blinkFrame?.[view.role];
        if(Number.isInteger(requested)&&loaded&&view.kind!=='eye_bridge') {
          const asset=atlas.assets[PART_FAMILY[view.part]],frame=asset.frames[clamp(requested,0,asset.frames.length-1)];
          view.pose=frame.pose;view.opening=normalPose(frame.pose).startsWith('opening')||frame.pose==='settled';
          view.closure=({rest:0,anticipation:.12,closing:.5,nearclosed:.8,closed:1,openinglow:.75,opening:.4,settled:0})[normalPose(frame.pose)]??0;
          if(overrides.rasterEyes){view.closure=1;view.rawRaster=true;}
        }
      }
      if(loaded)for(const view of visuals) {
        const rig=rigs.get(view.rigId),layer=view.restLayer||rig.layers.find(item=>item.role===view.role),center=layerCenter(layer);
        if(view.role.startsWith('eye')) {
          if(view.kind==='eye_bridge'||(view.transitionClosed&&view.closure>=.94)) {
            const spec=view.kind==='eye_bridge'?view:closedEyeSpec(view);
            const id='arc',asset=atlas.assets[id],frame=poseIndex(asset,['closed']),support=lidSupport(id);
            const contour=spec.contour,angle=spec.angle,c=Math.cos(angle),s=Math.sin(angle);
            placements.push({role:view.role,id,frame,kind:'eye_bridge',rigId:view.rigId,x:spec.x,y:spec.y,rotation:angle-support.angle,
              scale:spec.width/support.width,offset:support.offset,mirror:1,planeAngle:angle,opacity:1,pose:'closed',
              layer:contour?{role:view.role,part:'closed-contour-bridge',mode:'stroke',width:layer.width||3.2,opacity:1,
                points:contour.map(([x,y])=>[spec.x+(c*x-s*y)*spec.width,spec.y+(s*x+c*y)*spec.width])}:null});
            continue;
          }
          const id=PART_FAMILY[view.part],asset=atlas.assets[id];
          const plane=blinkPlane(rig,view.role),frame=poseIndex(asset,[view.pose,'rest']);
          if(view.rawRaster) {
            const placement=eyePlacement(rig,view.role,frame);
            placements.push({...placement,rigId:view.rigId,x:view.x,y:view.y,dx:view.x-center[0],dy:view.y-center[1],opacity:view.opacity,pose:view.pose});
          } else if(view.closure<=.001) {
            layers.push(translated(layer,view.x,view.y,view.opacity));
            // Keep registration metadata available without replacing the original resting paths.
            placements.push({...eyePlacement(rig,view.role,frame),rigId:view.rigId,x:view.x,y:view.y,dx:0,dy:0,opacity:0,originalOpacity:1,pose:view.pose});
          } else if(plane.preserveContour) {
            layers.push(squeezedLayer(view));
          } else if(view.closure<.65) {
            const clipped=translated(layer,view.x,view.y,view.opacity),box=plane.box;
            clipped.blinkClip={x:view.x,y:view.y,angle:plane.angle,left:box.x-1,width:box.w+2,
              top:box.y+box.h*.72*view.closure,bottom:box.y+box.h+1};
            if(clipped.mode==='stroke')clipped.blinkSegments=trimBlinkStroke(clipped,clipped.blinkClip,id==='round');
            layers.push(clipped);
          } else {
            const narrow=['nearclosed','closed','openinglow'].includes(normalPose(view.pose))?frame:poseIndex(asset,['closed']);
            placements.push(lidPlacement(view,narrow));
          }
        } else if(view.role==='mouth'&&(Number.isInteger(overrides.mouthFrame)||(state.speaking&&state.viseme&&state.viseme!=='rest'))) {
          const id=Number.isInteger(overrides.mouthFrame)?mouthAsset(rig):(family||mouthAsset(rig)),mouth=mouthPlacement(rig,id,[view.x,view.y],view.kind==='mouth_bridge'?speechGeometry(view):null);
          if(mouth) {
            placements.push({...mouth,x:view.x,y:view.y,rigId:view.rigId,opacity:view.opacity,
              frame:Number.isInteger(overrides.mouthFrame)?clamp(overrides.mouthFrame,0,atlas.assets[id].frames.length-1):mouthPose(atlas.assets[id],state.viseme,state.amount??1)});
          }
        } else layers.push(translated(layer,view.x,view.y,view.opacity));
      }
      const rawProgress=transition?(sampledTime-transition.at)/transition.duration:1;
      const progress=rawProgress>=1-1e-8?1:clamp(rawProgress,0,1);
      return {...state,renderer:loaded?'drawn':'original',drawnReady:loaded,drawnError:error?.message||null,
        drawnPlacements:placements,drawnLayers:layers,drawnVisuals:visuals,drawnRig:current,drawnBlinkAge:age,drawnGaze:overrides.gaze||null,
        drawnEyeBounds:visuals.filter(view=>view.role.startsWith('eye')&&view.opacity>.001).map(view=>{
          if(view.kind==='eye_bridge')return {role:view.role,left:view.x-view.width/2-1.6,right:view.x+view.width/2+1.6,top:view.y-1.6,bottom:view.y+1.6};
          const layer=rigs.get(view.rigId).layers.find(item=>item.role===view.role),box=bounds(layer.points,layer.mode==='stroke'?layer.width/2:0),center=layerCenter(layer);
          return {role:view.role,left:box.x+view.x-center[0],right:box.x+box.w+view.x-center[0],top:box.y+view.y-center[1],bottom:box.y+box.h+view.y-center[1]};
        }),
        drawnAnimation:{fps,frameMs,index:animationFrame(time),time:sampledTime},
        drawnMouthFamily:{assigned:mouthAssetForFace(current,atlas),active:family||mouthAssetForFace(current,atlas),pending:!!family&&family!==mouthAssetForFace(current,atlas),pendingMs:pendingSpeechFamily?Math.max(0,time-pendingSpeechFamily.since):0,switchReason:familySwitchReason,switchPolicy:'cue-boundary-or-400ms'},
        drawnTransition:{target:current,progress,active:progress<1,durationMs:transition?.duration??0,phase:progress>=1?'rest':progress<(transition?.closeUntil??.25)?'closing':progress<.65?'moving':'opening'},
        drawnIdentity:visuals.map(view=>({role:view.role,part:view.part,expression:view.rigId,opacity:view.opacity,pose:view.pose}))};
    }

    function paintLayer(layer,alpha=1) {
      context.save();context.globalAlpha=(layer.opacity??1)*alpha;context.fillStyle=context.strokeStyle='#5eeadd';
      if(layer.blinkClip&&!layer.blinkSegments) {
        const clip=layer.blinkClip;context.translate(clip.x,clip.y);context.rotate(clip.angle);
        context.beginPath();context.rect(clip.left,clip.top,clip.width,Math.max(.001,clip.bottom-clip.top));context.clip();
        context.rotate(-clip.angle);context.translate(-clip.x,-clip.y);
      }
      context.lineWidth=layer.width||1;context.lineCap='round';context.lineJoin='round';
      for(const points of layer.blinkSegments||[layer.points]) {
        const path=new root.Path2D(runtime.pathData(points,layer.mode==='fill'));
        if(layer.mode==='fill')context.fill(path);else context.stroke(path);
      }
      context.restore();
    }
    function drawAsset(placement) {
      const asset=atlas.assets[placement.id],frame=asset.frames[placement.frame],image=frame?.file?images[frame.file]:images[placement.id];
      if(!frame || !image)return;
      const cx=frame.cx??frame.x+frame.w/2,cy=frame.cy??frame.y+frame.h/2;
      const scale=placement.scale*(frame.sourceScale??1);
      context.drawImage(image,frame.x,frame.y,frame.w,frame.h,(frame.x-cx)*scale,(frame.y-cy)*scale,frame.w*scale,frame.h*scale);
    }
    function draw(state) {
      if(!loaded || !context)return;
      const age=(state.drawnAnimation.time-started)/1000;
      const chosenGaze=state.drawnGaze||gaze;
      const gx=reduced?0:chosenGaze?chosenGaze.x:Math.sin(age*.53)*.28+Math.sin(age*1.13)*.09;
      const gy=reduced?0:chosenGaze?chosenGaze.y:Math.sin(age*.41+.8)*.18;
      const customEyes=options.eyeLayer?.ready===true;
      const replaceEyes=customEyes&&options.eyeLayer.replaceEyes!==false;
      const showEyes=!customEyes||options.eyeLayer.showEyes!==false;
      const mouthOffset=customEyes?(options.eyeLayer.mouthOffset?.(state)||{x:0,y:0}):{x:0,y:0};
      context.setTransform(canvas.width/100,0,0,canvas.height/64,0,0);context.clearRect(0,0,100,64);
      context.save();context.beginPath();context.rect(3,3,94,58);context.clip();
      context.save();
      if(customEyes&&options.eyeLayer.faceTransform){const t=options.eyeLayer.faceTransform(state);context.translate(t.x,t.y);context.scale(t.scale,t.scale);}
      for(const layer of state.drawnLayers) {
        if((replaceEyes||!showEyes)&&layer.role.startsWith('eye'))continue;
        if(customEyes&&layer.role==='mouth'&&options.eyeLayer.showMouth===false)continue;
        if(customEyes&&!layer.role.startsWith('eye')&&layer.role!=='mouth'&&options.eyeLayer.showDecorations===false)continue;
        context.save();context.translate(gx*(layer.role.startsWith('eye')?1.65:layer.role==='mouth'?1.2:0)+(layer.role==='mouth'?mouthOffset.x:0),(layer.role.startsWith('eye')||layer.role==='mouth'?gy*.8:0)+(layer.role==='mouth'?mouthOffset.y:0));paintLayer(layer);context.restore();
      }
      for(const placement of state.drawnPlacements.filter(p=>!replaceEyes&&showEyes&&p.role.startsWith('eye')&&p.opacity>.000001)) {
        if(placement.kind==='eye_bridge'||placement.kind==='eyelid') {
          context.save();context.globalAlpha=placement.opacity;
          if(placement.layer) {
            context.translate(gx*1.65,gy*.8);paintLayer(placement.layer);context.restore();continue;
          }
          context.translate(placement.x+gx*1.65,placement.y+gy*.8);context.rotate(placement.rotation);context.scale(placement.mirror??1,1);
          context.translate(-placement.offset.x*placement.scale,-placement.offset.y*placement.scale);drawAsset(placement);context.restore();continue;
        }
        const rig=rigs.get(placement.rigId);
        const {slot,box,master,fit}=placement;
        context.save();context.globalAlpha=placement.opacity;context.translate(gx*1.65+placement.dx,gy*.8+placement.dy);context.translate(rig.dx,rig.dy);context.scale(rig.scale,rig.scale);
        context.translate(slot.x,slot.y);context.rotate((slot.rot||0)*Math.PI/180);context.scale(slot.mirror?-1:1,1);
        context.translate(box.x+box.w/2+placement.inkOffset.x*placement.scale,box.y+box.h/2+placement.inkOffset.y*placement.scale);context.rotate(fit.rotation);context.scale(fit.mirror?-1:1,1);
        context.translate(0,master.h*placement.scale/2);drawAsset(placement);context.restore();
      }
      for(const mouth of state.drawnPlacements.filter(p=>p.role==='mouth'&&(!customEyes||options.eyeLayer.showMouth!==false))) {
        context.save();context.globalAlpha=mouth.opacity;context.translate(gx*1.2,gy*.8);
        context.translate(mouthOffset.x,mouthOffset.y);
        context.translate(mouth.x,mouth.y);context.rotate(mouth.rotation);context.scale(mouth.mirror,1);
        context.translate(0,-mouth.master.h*mouth.scale/2);drawAsset(mouth);
        context.restore();
      }
      context.restore();
      if(customEyes){context.save();options.eyeLayer.paint(context,state,{x:gx*1.65,y:gy*.8});context.restore();}
      context.restore();
      canvas.dataset.expression=state.expression;canvas.dataset.speaking=String(state.speaking);canvas.dataset.viseme=state.viseme||'rest';
    }

    function paintIfDue(state,overrides={},force=false) {
      if(!canvas)return false;
      const prior=paintHistory.get(canvas),index=state.drawnAnimation.index;
      const mouth=state.speaking?(state.viseme||'rest'):'rest';
      const closeNow=(mouth==='rest'||mouth==='MBP')&&prior?.mouth!==mouth;
      const inspector=JSON.stringify([overrides.blinkFrame??null,overrides.mouthFrame??null,overrides.rasterEyes??false,options.eyeLayer?.revision??0]);
      if(force||!prior||prior.index!==index||prior.revision!==renderRevision||prior.inspector!==inspector||closeNow) {
        draw(state);paintHistory.set(canvas,{index,mouth,revision:renderRevision,inspector});return true;
      }
      return false;
    }
    function update(time=now()) {
      if(dead)return null;
      if(!reduced && time>=nextBlink) {blinkAt=animationTime(time);nextBlink=time+[4100,5200,3600,6100,4450][++blinkCount%5];}
      if(!options.skipHiddenFallbackUpdates||!loaded)base.update(time);const state=stateAt(time);state.drawnPainted=paintIfDue(state);return state;
    }
    function renderTo(targetCanvas,time=now(),overrides={}) {
      const oldCanvas=canvas,oldContext=context;
      try {
        canvas=targetCanvas;context=targetCanvas.getContext('2d');
        const state=stateAt(time,overrides);state.drawnPainted=paintIfDue(state,overrides);return state;
      } finally {canvas=oldCanvas;context=oldContext;}
    }
    const api={
      setExpression(id,settings={}) {
        if(!faces.has(id))return false;if(current===id)return true;
        const at=animationTime(now()),from=layoutAt(at);current=id;
        queueSpeechFamily(mouthAssetForFace(id,atlas),now());
        const leastClosed=Math.min(...from.filter(view=>view.role.startsWith('eye')).map(view=>view.closure));
        const duration=Math.ceil(clamp(settings.durationMs??TIMING.transitionMs,400,1600)/frameMs-1e-8)*frameMs;
        transition=reduced?null:{at,from,to:restLayout(id),duration,closeUntil:.25*(1-clamp(leastClosed,0,1))};
        renderRevision++;
        blinkAt=-Infinity;nextBlink=at+4000;
        return base.setExpression(id,settings);
      },
      startSpeech(settings={}) {if(!base.speaking){speechFamily=mouthAssetForFace(current,atlas);lastSpeechCue=null;pendingSpeechFamily=null;}speechSample=typeof settings.sample==='function'?settings.sample:null;base.startSpeech(settings);},
      stopSpeech(){speechSample=null;speechFamily=mouthAssetForFace(current,atlas);lastSpeechCue=null;pendingSpeechFamily=null;base.stopSpeech();paintIfDue(stateAt(now()),{},true);},
      setGaze(x,y){gaze={x:clamp(x,-1,1),y:clamp(y,-1,1)};base.setGaze(x,y);},clearGaze(){gaze=null;base.clearGaze();},
      blink(){blinkAt=animationTime(now());nextBlink=blinkAt+4300;base.blink();renderRevision++;},update,snapshot:stateAt,renderTo,
      setFps(value){fps=clamp(Number(value)||TIMING.fps,1,60);frameMs=1000/fps;paintHistory=new WeakMap();return fps;},
      destroy(){dead=true;if(raf!==null)root.cancelAnimationFrame?.(raf);base.destroy();canvas?.remove();fallbackHost?.remove();},
      get expression(){return base.expression;},get speaking(){return speechSample?stateAt(now()).speaking:base.speaking;},get expressions(){return base.expressions;},
      get svg(){return base.svg;},get canvas(){return canvas;},get loaded(){return loaded;},get error(){return error;}
    };
    api.ready=(async()=> {
      try {
        const atlasUrl=options.atlasUrl||'/drawn/atlas.json';
        if(!atlas) {const response=await root.fetch(atlasUrl,{cache:'no-cache'});if(!response.ok)throw new Error('Drawn atlas HTTP '+response.status);atlas=await response.json();}
        for(const id of [...Object.keys(FAMILY_PARTS),'mouth_neutral','mouth_smile']) {
          if(!atlas.assets[id]?.frames?.length)throw new Error('Missing drawn asset: '+id);
        }
        if(atlas.mouthFamilies)for(const face of data.faces) {
          if(!Object.prototype.hasOwnProperty.call(atlas.mouthFamilies,face.id))throw new Error('Missing mouth-family assignment: '+face.id);
          const family=mouthAssetForFace(face.id,atlas);
          if((family&&!atlas.assets[family]?.frames?.length)||(!family&&face.slots.mouth))throw new Error('Invalid mouth-family assignment: '+face.id+' / '+family);
        }
        const sheetFiles=new Set(Object.values(atlas.assets).flatMap(asset=>[asset.file,...asset.frames.map(frame=>frame.file).filter(Boolean)]));
        if(!options.images) await Promise.all([...sheetFiles].map(async file=> {
          const image=new root.Image();image.src=new URL(file,new URL(atlasUrl,root.location.href)).href;
          await image.decode();if(dead)return;images[file]=keySheet(image,document);
        }));
        for(const asset of Object.values(atlas.assets)) {
          images[asset.id]??=images[asset.file];images[asset.file]??=images[asset.id];
          if(!images[asset.id])throw new Error('Missing drawn sheet: '+asset.file);
          for(const frame of asset.frames)if(frame.file&&!images[frame.file])throw new Error('Missing replacement sheet: '+frame.file);
        }
        if(dead)return false;loaded=true;
        if(canvas){canvas.hidden=false;canvas.style.display='block';fallbackHost.style.visibility='hidden';}
        update(now());return true;
      } catch(problem) {error=problem;loaded=false;options.onError?.(problem);return false;}
    })();
    update(now());
    if(options.autoStart!==false && root.requestAnimationFrame) {
      const tick=time=>{if(dead)return;update(time);raf=root.requestAnimationFrame(tick);};raf=root.requestAnimationFrame(tick);
    }
    return api;
  }
  root.MistDrawnFaceRuntime={create,version:'1.4.0',timing:TIMING,helpers:{align,blinkPose,mouthPose,mouthAssetForFace,colorizePixels,PART_FAMILY}};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.MistDrawnFaceRuntime;
})(typeof window!=='undefined'?window:globalThis);
