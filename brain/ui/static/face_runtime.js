/* MIST face geometry and animation. Coordinates use the original 100 x 64 screen. */
(function (root) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const CYAN = '#5eeadd';
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : lo));
  const mix = (a, b, t) => a + (b - a) * t;
  const ease = t => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
  const clone = layer => ({...layer, points: layer.points.map(p => p.slice())});
  let instanceId = 0;

  function bounds(layers) {
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (const layer of layers) {
      if (layer.opacity <= 0.001) continue;
      const r = layer.mode === 'stroke' ? layer.width / 2 : 0;
      for (const [x, y] of layer.points) {
        left = Math.min(left, x - r); right = Math.max(right, x + r);
        top = Math.min(top, y - r); bottom = Math.max(bottom, y + r);
      }
    }
    return Number.isFinite(left) ? {left, top, right, bottom} : null;
  }

  function transform(layer, sx, sy, cx, cy, dx = 0, dy = 0) {
    return {...layer, width: layer.width * Math.min(Math.abs(sx), Math.abs(sy)),
      points: layer.points.map(([x, y]) => [cx + (x - cx) * sx + dx, cy + (y - cy) * sy + dy])};
  }

  function fit(layers, box) {
    const b = bounds(layers);
    if (!b) return layers;
    const scale = Math.min(1, (box.right - box.left) / Math.max(.001, b.right - b.left),
      (box.bottom - box.top) / Math.max(.001, b.bottom - b.top));
    const cx = (b.left + b.right) / 2, cy = (b.top + b.bottom) / 2;
    const hw = (b.right - b.left) * scale / 2, hh = (b.bottom - b.top) * scale / 2;
    const dx = clamp(cx, box.left + hw, box.right - hw) - cx;
    const dy = clamp(cy, box.top + hh, box.bottom - hh) - cy;
    return layers.map(l => transform(l, scale, scale, cx, cy, dx, dy));
  }

  function partLayer(data, slot, role) {
    const p = data.parts[slot.part];
    if (!p) throw new Error('Unknown MIST part: ' + slot.part);
    const angle = (slot.rot || 0) * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle);
    return {role, part:slot.part, mode:p.kind === 'fill' ? 'fill' : 'stroke',
      width:p.width || 0, opacity:1, open:!!slot.open || /^(open_C|e_shape)/.test(p.family || slot.part),
      points:p.points.map(([x, y]) => {
        x *= slot.mirror ? -1 : 1;
        return [slot.x + c * x - s * y, slot.y + s * x + c * y];
      })};
  }

  function mouthBridge(from, to, progress) {
    const t=clamp(progress,0,1);
    if(!from && !to) return [];
    if(!from) return [{...clone(to),opacity:to.opacity*ease(t)}];
    if(!to) return [{...clone(from),opacity:from.opacity*(1-ease(t))}];
    if(t===0) return [clone(from)];
    if(t===1) return [clone(to)];
    const a=bounds([from]),b=bounds([to]);
    const ax=(a.left+a.right)/2,ay=(a.top+a.bottom)/2,bx=(b.left+b.right)/2,by=(b.top+b.bottom)/2;
    const move=ease((t-.27)/.46),cx=mix(ax,bx,move),cy=mix(ay,by,move);
    if(t>=.38 && t<=.62) return [{role:'mouth',part:'closure',mode:'stroke',width:3.2,opacity:1,
      points:Array.from({length:48},(_,i)=>[cx-7+i*14/47,cy])}];
    const item=t<.5?from:to,box=t<.5?a:b;
    const collapse=t<.5?ease(t/.38):1-ease((t-.62)/.38);
    const ix=(box.left+box.right)/2,iy=(box.top+box.bottom)/2;
    const padding=item.mode==='stroke'?item.width:0;
    const w=Math.max(.01,box.right-box.left-padding),h=Math.max(.01,box.bottom-box.top-padding);
    const sx=mix(1,(14+(item.mode==='fill'?3.2:0))/w,collapse);
    const sy=mix(1,item.mode==='fill'?3.2/h:0,collapse);
    const result=transform(item,sx,sy,ix,iy,cx-ix,cy-iy);
    result.width=item.mode==='stroke'?mix(item.width,3.2,collapse):0;
    result.opacity=mix(item.opacity,1,collapse);
    return [result];
  }

  function faceLayers(data, face) {
    const layers = [];
    for (const role of ['eye_L', 'eye_R', 'mouth'])
      if (face.slots[role]) layers.push(partLayer(data, face.slots[role], role));
    (face.slots.tears || []).forEach((slot, i) => layers.push(partLayer(data, slot, 'tear' + i)));
    return fit(layers, {left:8, top:8, right:92, bottom:56});
  }

  // Speech outlines share angular correspondence; closed lips retain their thickness.
  function radialOutline(points) {
    const sorted = points.map(p => p.slice()).sort((a,b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (a,b,c) => (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
    const lower = [], upper = [];
    for (const p of sorted) { while (lower.length>1 && cross(lower.at(-2),lower.at(-1),p)<=0) lower.pop(); lower.push(p); }
    for (const p of sorted.slice().reverse()) { while (upper.length>1 && cross(upper.at(-2),upper.at(-1),p)<=0) upper.pop(); upper.push(p); }
    const hull = lower.slice(0,-1).concat(upper.slice(0,-1));
    const cx = hull.reduce((n,p)=>n+p[0],0)/hull.length, cy = hull.reduce((n,p)=>n+p[1],0)/hull.length;
    return Array.from({length:64}, (_,i) => {
      const a = -Math.PI/2 + i*Math.PI/32, dx = Math.cos(a), dy = Math.sin(a);
      let distance = Infinity;
      for (let j=0;j<hull.length;j++) {
        const p=hull[j], q=hull[(j+1)%hull.length], ex=q[0]-p[0], ey=q[1]-p[1];
        const den=dx*ey-dy*ex;
        if (Math.abs(den)<1e-9) continue;
        const px=p[0]-cx, py=p[1]-cy;
        const t=(px*ey-py*ex)/den, u=(px*dy-py*dx)/den;
        if (t>=0 && u>=-1e-8 && u<=1+1e-8) distance=Math.min(distance,t);
      }
      return [cx+dx*(Number.isFinite(distance)?distance:0),cy+dy*(Number.isFinite(distance)?distance:0)];
    });
  }

  function pathData(points, closed) {
    if (!points.length) return '';
    const f = n => n.toFixed(3);
    if (closed) {
      let d = `M${f((points[0][0]+points.at(-1)[0])/2)} ${f((points[0][1]+points.at(-1)[1])/2)}`;
      points.forEach((p,i) => { const q=points[(i+1)%points.length]; d+=` Q${f(p[0])} ${f(p[1])} ${f((p[0]+q[0])/2)} ${f((p[1]+q[1])/2)}`; });
      return d+' Z';
    }
    let d=`M${f(points[0][0])} ${f(points[0][1])}`;
    for(let i=1;i<points.length-1;i++) {
      const p=points[i],q=points[i+1];
      d+=` Q${f(p[0])} ${f(p[1])} ${f((p[0]+q[0])/2)} ${f((p[1]+q[1])/2)}`;
    }
    return d+` L${f(points.at(-1)[0])} ${f(points.at(-1)[1])}`;
  }

  function create(container, options = {}) {
    const data = options.data || root.MIST_DATA;
    if (!data || !data.faces || !data.parts) throw new Error('MIST_DATA must load before the face renderer');
    const clock = options.now || (() => root.performance.now());
    const reduced = options.reducedMotion ?? !!root.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const faces = new Map(data.faces.map(f => [f.id, f]));
    const prepared = new Map(data.faces.map(f => [f.id, faceLayers(data,f)]));
    const visemes = Object.fromEntries(Object.entries(data.visemes).map(([name,v]) => [name,radialOutline(v.points)]));
    visemes.TH ??= visemes.LNT;
    let current = faces.has(options.expression) ? options.expression : '39_neutral_02_manual';
    if (!faces.has(current)) current = data.faces[0].id;
    let transition = null, speech = null, release = null, manualGaze = null;
    let started = clock(), blinkStart = -Infinity, lastNow = started, raf = null, dead = false;
    let nextBlink = started + 3350, blinkCount = 0;
    let svg = null, group = null;
    const paths = [];

    if (container) {
      const doc = container.ownerDocument || root.document, id = 'mist-'+(++instanceId);
      svg = container.namespaceURI === NS && container.tagName.toLowerCase() === 'svg' ? container : doc.createElementNS(NS,'svg');
      svg.setAttribute('viewBox','0 0 100 64'); svg.setAttribute('role','img');
      svg.setAttribute('aria-label','MIST robot face');
      svg.setAttribute('preserveAspectRatio','xMidYMid meet');
      svg.style.cssText='display:block;width:100%;height:100%;overflow:hidden';
      svg.innerHTML=`<defs><clipPath id="${id}-clip"><rect x="3" y="3" width="94" height="58" rx="4"/></clipPath><filter id="${id}-glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="0.55"/><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><g clip-path="url(#${id}-clip)"><g filter="url(#${id}-glow)"></g></g>`;
      group=svg.lastElementChild.firstElementChild;
      if(svg!==container) container.appendChild(svg);
    }

    function baseAt(now) {
      const target=prepared.get(current);
      if(!transition) return target.map(clone);
      const t=clamp((now-transition.at)/transition.duration,0,1);
      if(t>=1) return target.map(clone);
      const roles=new Set([...transition.from.map(l=>l.role),...target.map(l=>l.role)]);
      const result=[];
      for(const role of roles) {
        const from=transition.from.filter(l=>l.role===role), to=target.find(l=>l.role===role);
        // Identical parts keep their contour throughout the expression change.
        if(from.length===1 && to && from[0].part===to.part && from[0].points.length===to.points.length) {
          const a=from[0], e=ease(t);
          result.push({...to,opacity:mix(a.opacity,1,e),width:mix(a.width,to.width,e),
            points:a.points.map((p,i)=>[mix(p[0],to.points[i][0],e),mix(p[1],to.points[i][1],e)])});
        } else if(role.startsWith('eye')) {
          // A quick lid closure hides incompatible contours without twisting them.
          const opening=t>=.46, phase=opening?ease((t-.46)/.54):1-ease(t/.46);
          const items=opening?(to?[to]:[]):from;
          for(const item of items) {
            const b=bounds([item]),cx=(b.left+b.right)/2,cy=(b.top+b.bottom)/2;
            const out=transform(item,1,.08+.92*phase,cx,cy,
              ((role==='eye_L'?28:72)-cx)*(1-phase),(33.7-cy)*(1-phase));
            out.width=item.width*(.68+.32*phase); out.opacity=item.opacity;
            result.push(out);
          }
        } else if(role==='mouth') {
          result.push(...mouthBridge(from.find(l=>l.opacity>0.001),to,t));
        } else {
          for(const item of from) result.push({...clone(item),opacity:item.opacity*(1-ease(t/.58))});
          if(to) result.push({...clone(to),opacity:ease((t-.42)/.58)});
        }
      }
      return result;
    }

    function speechShape(name, amount=1) {
      name=visemes[name]?name:'rest';
      const p=visemes[name], b=bounds([{points:p,width:0,mode:'fill',opacity:1}]);
      const scale=Math.min(1,19/(b.right-b.left),11/(b.bottom-b.top));
      const cx=(b.left+b.right)/2,cy=(b.top+b.bottom)/2;
      const open=['rest','MBP','FV'].includes(name)?1:.72+.28*clamp(amount,0,1);
      return {role:'mouth',part:'speech-'+name,mode:'fill',opacity:1,width:0,
        points:p.map(([x,y])=>[50+(x-cx)*scale,49.5+(y-cy)*scale*open])};
    }

    function speechAt(now, secondsOverride) {
      if(!speech) return null;
      if(speech.sample) {
        const cue=speech.sample()||{},active=cue.active!==false;
        const viseme=active&&visemes[cue.viseme]?cue.viseme:'rest',amount=clamp(cue.amount??1,0,1);
        return {layer:speechShape(viseme,amount),time:Math.max(0,Number(cue.time)||0),viseme,amount,active,source:cue.source};
      }
      let seconds=secondsOverride ?? speech.clock();
      seconds=clamp(seconds,0,Number.MAX_SAFE_INTEGER);
      const events=speech.events;
      if(seconds>=speech.duration) return {layer:speechShape('rest'),time:seconds,viseme:'rest'};
      let index=-1;
      for(let i=0;i<events.length && events[i].time<=seconds;i++) index=i;
      const event=index<0?{time:0,viseme:'rest'}:events[index];
      const previous=index<=0?{viseme:'rest'}:events[index-1];
      const a=speechShape(previous.viseme,previous.amount),b=speechShape(event.viseme,event.amount);
      const gap=index>=0 && index+1<events.length?events[index+1].time-event.time:.12;
      const e=ease((seconds-event.time)/Math.max(.012,Math.min(.065,gap*.55)));
      b.points=a.points.map((p,i)=>[mix(p[0],b.points[i][0],e),mix(p[1],b.points[i][1],e)]);
      return {layer:b,time:seconds,viseme:event.viseme,amount:event.amount};
    }

    function snapshot(now=clock()) {
      const layers=baseAt(now);
      const talking=speechAt(now);
      let mouthLayers=layers.filter(l=>l.role==='mouth');
      let final=layers.filter(l=>l.role!=='mouth');
      if(talking) {
        mouthLayers=mouthBridge(speech.sourceMouth,talking.layer,talking.time/.13);
      } else if(release) {
        mouthLayers=mouthBridge(release.layer,mouthLayers[0],(now-release.at)/130);
      }
      final=final.concat(mouthLayers).filter(l=>l.opacity>.0001);
      const blinkAge=now-blinkStart;
      const blink = blinkAge<0 || blinkAge>185?0:blinkAge<65?ease(blinkAge/65):blinkAge<92?1:1-ease((blinkAge-92)/93);
      final=final.map(layer=>{
        if(!layer.role.startsWith('eye') || !layer.open || blink===0) return layer;
        const b=bounds([layer]), out=transform(layer,1,1-.94*blink,50,b.bottom-layer.width/2);
        out.width=layer.width*(1-.18*blink);
        return out;
      });
      const age=(now-started)/1000;
      const gx=reduced?0:manualGaze?manualGaze.x:Math.sin(age*.53)*.28+Math.sin(age*1.13)*.09;
      const gy=reduced?0:manualGaze?manualGaze.y:Math.sin(age*.41+.8)*.18;
      const breath=reduced?1:1+.0035*Math.sin(age*Math.PI*2/5.8);
      final=final.map(layer=>transform(layer,breath,breath,50,32,gx*(layer.role.startsWith('eye')?1.65:1.2),gy*.8));
      final=fit(final,{left:5.5,top:5.5,right:94.5,bottom:58.5});
      return {expression:current,speaking:!!speech,time:now,speechTime:talking?.time??null,
        viseme:talking?.viseme??null,amount:talking?.amount??0,audioActive:talking?.active??!!speech,
        alignmentSource:talking?.source??null,blink,layers:final,bounds:bounds(final)};
    }

    function draw(state) {
      if(!group) return;
      while(paths.length<state.layers.length) {
        const p=svg.ownerDocument.createElementNS(NS,'path');group.appendChild(p);paths.push(p);
      }
      paths.forEach((p,i)=>{
        const l=state.layers[i];
        if(!l) {p.setAttribute('display','none');return;}
        p.removeAttribute('display');p.setAttribute('data-role',l.role);
        p.setAttribute('d',pathData(l.points,l.mode==='fill'));
        p.setAttribute('fill',l.mode==='fill'?CYAN:'none');
        p.setAttribute('stroke',l.mode==='stroke'?CYAN:'none');
        p.setAttribute('stroke-width',l.width);p.setAttribute('stroke-linecap','round');p.setAttribute('stroke-linejoin','round');
        p.setAttribute('opacity',l.opacity);
      });
      svg.setAttribute('data-expression',state.expression);svg.setAttribute('data-speaking',String(state.speaking));
    }

    function update(now=clock()) {
      if(dead) return null;
      lastNow=now;
      if(!reduced && now>=nextBlink) {
        blinkStart=now;blinkCount++;
        nextBlink=now+[4100,5200,3600,6100,4450][blinkCount%5];
      }
      if(transition && now>=transition.at+transition.duration) transition=null;
      if(release && now>=release.at+130) release=null;
      const state=snapshot(now);draw(state);return state;
    }

    const api={
      setExpression(id,{durationMs=260}={}) {
        if(!faces.has(id)) return false;
        if(current===id) return true;
        const now=clock(),from=baseAt(now);current=id;
        transition={at:now,from,duration:reduced?1:clamp(durationMs,1,1200)};
        return true;
      },
      startSpeech({events=[],duration,clock:audioClock,sample}={}) {
        const at=clock();
        const sourceMouth=speech?speechAt(at).layer:release?release.layer:baseAt(at).find(l=>l.role==='mouth');
        const clean=events.filter(e=>Number.isFinite(e.time)&&e.time>=0).map(e=>({
          time:e.time,viseme:visemes[e.viseme]?e.viseme:'rest',amount:clamp(e.amount??1,0,1)
        })).sort((a,b)=>a.time-b.time);
        speech={at,sourceMouth,events:clean,sample:typeof sample==='function'?sample:null,duration:Number.isFinite(duration)&&duration>0?duration:(clean.at(-1)?.time??0)+.3,
          clock:typeof audioClock==='function'?audioClock:()=>Math.max(0,(clock()-at)/1000)};
        release=null;
      },
      stopSpeech() {
        if(speech) release={at:clock(),layer:speechAt(clock()).layer};
        speech=null;
      },
      setGaze(x,y) {manualGaze={x:clamp(x,-1,1),y:clamp(y,-1,1)};},
      clearGaze() {manualGaze=null;},
      blink() {blinkStart=clock();nextBlink=blinkStart+4300;},
      update,snapshot,
      destroy() {dead=true;if(raf!==null) root.cancelAnimationFrame?.(raf);if(svg && svg!==container) svg.remove();},
      get expression() {return current;},get speaking() {return !!speech;},
      get svg() {return svg;},get expressions() {return data.faces.map(f=>({id:f.id,name:f.name,group:f.group}));}
    };
    update(started);
    if(options.autoStart!==false && root.requestAnimationFrame) {
      const frame=now=>{if(dead)return;update(now);raf=root.requestAnimationFrame(frame);};
      raf=root.requestAnimationFrame(frame);
    }
    return api;
  }
  root.MistFaceRuntime={create,bounds,pathData,version:'2.0.0'};
  if(typeof module!=='undefined' && module.exports) module.exports=root.MistFaceRuntime;
})(typeof window!=='undefined'?window:globalThis);
