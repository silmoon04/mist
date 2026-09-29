/* Activity symbols alongside the original MIST eyes, blinks and speaking mouth. */
(function(root){
  'use strict';
  const FPS=15, TICK=1000/FPS;
  const IDS=['coding','reading','writing','email','searching','planning','calculating','connecting','checking'];
  const sharedLoads=new Map();
  const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
  const ease=n=>{n=clamp(n,0,1);return n*n*(3-2*n);};
  const mix=(a,b,t)=>a+(b-a)*t;
  const LOOP_HOLDS={coding:[3,2,4,3],reading:[4,3,4,3],writing:[3,3,4,2],email:[4,4,4,4],searching:[4,4,4,4],planning:[3,3,4,50],calculating:[3,3,3,5],connecting:[4,4,4,4],checking:[3,3,4,50]};

  function createActivityClock(now=()=>root.performance.now()){
    let current=null,serial=0;
    function sample(time=now()){
      if(!current)return null;
      const age=Math.max(0,time-current.at),tick=Math.floor(age/TICK+1e-7);
      if(current.exiting){
        if(tick>=8){current=null;return null;}
        return {...current,phase:'exit',frame:8+Math.floor(tick/2),alpha:1-ease(tick/8),age};
      }
      if(tick<8)return {...current,phase:'entrance',frame:Math.floor(tick/2),alpha:ease((tick+1)/8),age};
      const holds=LOOP_HOLDS[current.id],length=holds.reduce((a,b)=>a+b,0);
      let rest=(tick-8)%length,index=0;
      while(rest>=holds[index])rest-=holds[index++];
      return {...current,phase:'loop',frame:4+index,alpha:1,age};
    }
    return {
      set(id,options={}){
        if(!IDS.includes(id))return {accepted:false,reason:'unknown_activity'};
        if(current?.id===id&&current.owner===(options.owner??null)&&!current.exiting&&!options.restart)return {accepted:true,token:current.token,id};
        current={id,token:++serial,owner:options.owner??null,at:now(),exiting:false};
        return {accepted:true,token:current.token,id};
      },
      clear(token,options={}){
        if(!current||(token!==undefined&&token!==null&&current.token!==token))return false;
        if(options.immediate)current=null;
        else if(!current.exiting)current={...current,at:now(),exiting:true};
        return true;
      },sample,
      get token(){return current?.token??null;}
    };
  }

  function validateManifest(manifest){
    if(manifest?.version!==6||manifest.fps!==15)throw new Error('Expected hand-drawn manifest v6 at 15 fps');
    for(const id of IDS){
      const asset=manifest.assets?.[id],length=12;
      if(!asset||asset.frames?.length!==length||asset.frames.some(frame=>typeof frame.file!=='string'||!frame.file.startsWith('frames/')))throw new Error('Invalid hand-drawn asset: '+id);
    }
    for(const id of IDS){
      const item=manifest.activities?.find(a=>a.id===id);
      if(!item||!['side','above'].includes(item.placement))throw new Error('Invalid activity placement: '+id);
    }
    return manifest;
  }

  function create(container,options={}){
    const now=options.now||(()=>root.performance.now()),data=options.data||root.MIST_DATA;
    const oldRuntime=options.drawnRuntime||root.MistDrawnFaceRuntime;
    if(!oldRuntime)throw new Error('Load the drawn face runtime before hand-drawn motion');
    const reduced=options.reducedMotion??!!root.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    let manifest=options.manifest||null,images=options.handdrawnImages||{},ready=false,dead=false,error=null,raf=null;
    let revision=0,lastTargets=null,eyeTransition=null,held={},expression=options.expression||data.faces[0].id;
    const clock=createActivityClock(now),previewClock=createActivityClock(now),faces=new Map(data.faces.map(f=>[f.id,f]));
    let previewActivity=false;
    const activeActivity=time=>previewActivity?previewClock.sample(time):clock.sample(time);
    const origin=now(),sampleTime=time=>origin+Math.floor((time-origin)/TICK+1e-7)*TICK;
    const activityConfig=id=>manifest?.activities.find(a=>a.id===id);
    function targets(id=expression,activity=activeActivity()){
      const config=activity&&!activity.exiting?activityConfig(activity.id):null;
      const side=config?.placement==='side',above=config?.placement==='above';
      return {
        id,activity:config?.id||null,
        transform:side?{x:2,y:8.32,scale:.74}:above?{x:14,y:15,scale:.72}:{x:0,y:0,scale:1}
      };
    }
    function transitionToNext(){
      const at=sampleTime(now()),to=targets(),from=layout(at);
      if(JSON.stringify(lastTargets)===JSON.stringify(to))return;
      eyeTransition=reduced?null:{at,from,to,duration:9*TICK};lastTargets=to;revision++;
    }
    function layout(time){
      const target=lastTargets||targets();
      if(!eyeTransition)return target;
      const t=clamp((time-eyeTransition.at)/eyeTransition.duration,0,1);
      if(t>=1){eyeTransition=null;return target;}
      const a=eyeTransition.from,b=eyeTransition.to,m=ease(t);
      return {...b,transform:{x:mix(a.transform.x,b.transform.x,m),y:mix(a.transform.y,b.transform.y,m),scale:mix(a.transform.scale,b.transform.scale,m)}};
    }
    function shot(time=now()){
      const at=sampleTime(time),activity=activeActivity(at),model=layout(at);
      const symbol=activity?{...activity,frame:Number.isInteger(held.activityFrame)?clamp(held.activityFrame,0,11):reduced?7:activity.frame}:null;
      if(symbol)symbol.file=manifest?.assets[symbol.id]?.frames[symbol.frame]?.file||null;
      const face=faces.get(expression)||data.faces[0];
      return {ready,error:error?.message||null,fps:FPS,time:at,expression,eyeSource:'original-mist',
        eyes:['eye_L','eye_R'].map(role=>({role,...face.slots[role]})),faceTransform:model.transform,
        activity:symbol,transitioning:!!eyeTransition,layers:held.layers||'all'};
    }
    function drawFrame(context,id,index,x,y,size,mirror=1,alpha=1){
      const asset=manifest.assets[id],frame=asset.frames[Number.isInteger(index)?clamp(index,0,asset.frames.length-1):0],image=images[frame.file];
      context.save();context.globalAlpha=alpha;context.translate(x,y);context.scale(mirror,1);
      context.drawImage(image,-size/2,-size*(asset.type==='eye'?(asset.baseline??204)/256:.5),size,size);context.restore();
    }
    const layer={
      get ready(){return ready;},get revision(){return revision;},
      replaceEyes:false,
      get showEyes(){return !held.layers||held.layers==='all'||held.layers==='eyes';},
      get showDecorations(){return !held.layers||held.layers==='all';},
      get showMouth(){return !held.layers||held.layers==='all'||held.layers==='mouth';},
      faceTransform(state){return shot(state.drawnAnimation.time).faceTransform;},
      paint(context,state,gaze){
        const s=shot(state.drawnAnimation.time),layers=s.layers;
        const a=s.activity;if(a&&(layers==='all'||layers==='activity')){
          const above=activityConfig(a.id).placement==='above';
          drawFrame(context,a.id,a.frame,above?50:84,above?14:31,above?42:29,1,a.alpha);
        }
      }
    };
    const base=oldRuntime.create(container,{...options,autoStart:false,fps:FPS,eyeLayer:layer});
    expression=base.expression;lastTargets=targets();
    const owners=new Map();let stateToken=null,stateActivity=null;
    function setActivity(id,settings={}){
      if(dead)return {accepted:false,reason:'destroyed'};
      const result=clock.set(id,settings);if(result.accepted){transitionToNext();revision++;}return result;
    }
    function clearActivity(token,settings={}){
      const result=clock.clear(token,settings);if(result){transitionToNext();revision++;}return result;
    }
    function finishOwner(owner){
      const entry=owners.get(owner);if(!entry)return;
      owners.delete(owner);if(clock.token!==entry.token)return;
      const previous=[...owners.entries()].at(-1);
      if(previous){const [key,value]=previous;value.token=setActivity(value.id,{owner:key}).token;}
      else if(stateActivity)stateToken=setActivity(stateActivity,{owner:'state'}).token;
      else clearActivity(entry.token);
    }
    function handleEvent(event){
      if(!event||typeof event!=='object')return false;
      if(event.type==='state'){
        const id=event.state==='thinking'?'planning':event.state==='checking'?'checking':null;
        stateActivity=id;
        if(id){const active=clock.sample();if(!active||active.exiting||active.owner==='state')stateToken=setActivity(id,{owner:'state'}).token;}
        else if(stateToken!==null){clearActivity(stateToken);stateToken=null;}
        return true;
      }
      if(event.type==='brain_job'&&event.job_id){
        const owner='brain:'+event.job_id;
        if(event.status==='running')owners.set(owner,{id:'planning',token:setActivity('planning',{owner}).token});
        else finishOwner(owner);
        return true;
      }
      if(event.type==='debug_tool'){
        const supported={get_sensor_snapshot:'checking',recall:'reading',remember:'writing'};
        const id=supported[event.name];if(!id||!event.call_id)return false;
        const owner='tool:'+event.call_id;
        if(event.phase==='started')owners.set(owner,{id,token:setActivity(id,{owner}).token});
        else if(event.phase==='finished')finishOwner(owner);
        return true;
      }
      return false;
    }
    const api={
      setExpression(id,settings={}){const ok=base.setExpression(id,settings);if(ok&&expression!==id){expression=id;transitionToNext();}return ok;},
      startSpeech(settings){return base.startSpeech(settings);},stopSpeech(){return base.stopSpeech();},
      setGaze(x,y){base.setGaze(x,y);},clearGaze(){base.clearGaze();},
      blink(){revision++;base.blink();},
      setActivity,clearActivity,handleEvent,
      previewActivity(id){
        const result=previewClock.set(id,{owner:'preview'});
        if(result.accepted){previewActivity=true;transitionToNext();revision++;}
        return result;
      },
      clearPreviewActivity(){
        if(!previewActivity)return;
        previewActivity=false;previewClock.clear(undefined,{immediate:true});transitionToNext();revision++;
      },
      inspect(settings={}){held={...settings};revision++;return shot();},
      resetActivities(){owners.clear();stateToken=null;stateActivity=null;clearActivity(undefined,{immediate:true});},
      update(time=now()){
        if(dead)return null;
        const state=base.update(time);return state?{...state,handdrawn:shot(time),renderer:ready&&state.drawnReady?'handdrawn':state.renderer}:null;
      },
      snapshot(time=now(),overrides={}){const s=base.snapshot(time,overrides);return {...s,handdrawn:shot(time),renderer:ready&&s.drawnReady?'handdrawn':s.renderer};},
      renderTo(canvas,time=now(),overrides={}){const s=base.renderTo(canvas,time,overrides);return {...s,handdrawn:shot(time),renderer:ready&&s.drawnReady?'handdrawn':s.renderer};},
      setFps(){return base.setFps(FPS);},
      destroy(){dead=true;owners.clear();if(raf!==null)root.cancelAnimationFrame?.(raf);base.destroy();},
      get expression(){return base.expression;},get expressions(){return base.expressions;},get speaking(){return base.speaking;},
      get canvas(){return base.canvas;},get svg(){return base.svg;},get loaded(){return ready&&base.loaded;},get error(){return error||base.error;},
      get manifest(){return manifest;}
    };
    api.ready=(async()=>{
      try{
        const manifestUrl=options.manifestUrl||'/studio/handdrawn_v6/manifest.json';
        if(!manifest){const response=await root.fetch(manifestUrl,{cache:'no-cache'});if(!response.ok)throw new Error('Hand-drawn manifest HTTP '+response.status);manifest=await response.json();}
        validateManifest(manifest);
        const files=[...new Set(IDS.flatMap(id=>manifest.assets[id].frames.map(f=>f.file)))];
        if(!options.handdrawnImages)await Promise.all(files.map(async file=>{
          const url=new URL(file,new URL(manifestUrl,root.location.href)).href;
          if(!sharedLoads.has(url))sharedLoads.set(url,(async()=>{
            const img=new root.Image();img.src=url;await img.decode();
            if(img.naturalWidth!==256||img.naturalHeight!==256)throw new Error('Unregistered hand-drawn frame: '+file);
            return img;
          })().catch(problem=>{sharedLoads.delete(url);throw problem;}));
          const img=await sharedLoads.get(url);if(!dead)images[file]=img;
        }));
        if(files.some(file=>!images[file]))throw new Error('Hand-drawn frames are incomplete');
        if(dead)return false;
        ready=true;transitionToNext();revision++;
        await base.ready;if(dead)return false;api.update(now());return base.loaded;
      }catch(problem){if(dead)return false;error=problem;ready=false;options.onError?.(problem);return false;}
    })();
    if(options.autoStart!==false&&root.requestAnimationFrame){const tick=time=>{if(dead)return;api.update(time);raf=root.requestAnimationFrame(tick);};raf=root.requestAnimationFrame(tick);}
    return api;
  }
  root.MistHanddrawnRuntime={create,version:'6.1.0',fps:FPS,activities:IDS,helpers:{createActivityClock,validateManifest}};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.MistHanddrawnRuntime;
})(typeof window!=='undefined'?window:globalThis);
