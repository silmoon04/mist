const TICK_MS=1000/15;

export function animationCatalog(map,manifest){
 const faces=Object.entries(map.faces||{}).map(([id,face])=>({id,group:face.group,label:`${face.group.replaceAll('_',' ')} ${face.name.slice(face.group.length+1).replaceAll('_',' · ')}`}));
 const activities=(manifest?.activities||[]).map(({id,label,placement})=>({id,label,group:placement==='above'?'Overhead':'Side motion',placement}));
 return {faces,activities};
}

export class AnimationPreview{
 constructor(face){this.face=face;this.faceId=null;this.activityId=null;}
 get active(){return !!(this.faceId||this.activityId);}
 selectFace(id){this.faceId=id||null;}
 selectActivity(id){if(id){const result=this.face.previewActivity(id);if(!result?.accepted)return false;}else this.face.clearPreviewActivity();this.activityId=id||null;return true;}
 release(){this.selectFace(null);this.selectActivity(null);}
 displayedFace(liveFaceId){return this.faceId||liveFaceId;}
}

/** One 15 fps clock for every thumbnail; callers register only visible card IDs. */
export class VisiblePreviewScheduler{
 constructor({requestFrame=callback=>requestAnimationFrame(callback),cancelFrame=id=>cancelAnimationFrame(id),now=()=>performance.now(),onFrame,period=TICK_MS}={}){
  this.requestFrame=requestFrame;this.cancelFrame=cancelFrame;this.now=now;this.onFrame=onFrame;this.period=period;
  this.visible=new Set();this.open=false;this.reduced=false;this.frame=null;this.last=0;this.dead=false;
  this.tick=time=>{this.frame=null;if(this.dead||!this.open)return;if(time-this.last>=this.period-0.5){this.last=time;this.onFrame?.([...this.visible],time);}this.schedule();};
 }
 schedule(){if(this.frame===null&&!this.dead&&this.open&&!this.reduced&&this.visible.size)this.frame=this.requestFrame(this.tick);}
 setVisible(id,visible){if(this.dead)return;if(visible)this.visible.add(id);else this.visible.delete(id);if(!this.visible.size)this.pause();else this.schedule();}
 setOpen(open){if(this.dead)return;this.open=!!open;if(this.open){this.last=0;this.schedule();}else this.pause();}
 setReducedMotion(value){this.reduced=!!value;if(this.reduced){this.pause();if(this.open)this.onFrame?.([...this.visible],this.now());}else this.schedule();}
 pause(){if(this.frame!==null){this.cancelFrame(this.frame);this.frame=null;}}
 destroy(){if(this.dead)return;this.dead=true;this.pause();this.visible.clear();this.onFrame=null;}
}

/** Owns card runtimes so closing the tray and recycling offscreen cards releases every renderer. */
export class PreviewLifecycle{
 constructor(create){this.create=create;this.records=new Map();this.open=false;this.dead=false;}
 register(id,item,mount){if(this.dead)return;this.remove(id);this.records.set(id,{item,mount,visible:false,preview:null});}
 setVisible(id,visible){const record=this.records.get(id);if(!record||this.dead)return;record.visible=!!visible;if(!this.open||!record.visible){this.dispose(record);return;}if(!record.preview)record.preview=this.create(record.item,record.item.mode,record.mount)||null;}
 dispose(record){if(!record.preview)return;record.preview.destroy?.();record.preview=null;record.mount.replaceChildren();}
 setOpen(open){if(this.dead)return;this.open=!!open;for(const record of this.records.values()){if(!this.open)this.dispose(record);else if(record.visible&&!record.preview)record.preview=this.create(record.item,record.item.mode,record.mount)||null;}}
 update(ids,time){for(const id of ids)this.records.get(id)?.preview?.update?.(time);}
 remove(id){const record=this.records.get(id);if(record)this.dispose(record);this.records.delete(id);}
 destroy(){if(this.dead)return;for(const record of this.records.values())this.dispose(record);this.records.clear();this.dead=true;this.create=null;}
}

export function installAnimationPicker({map,manifest,grid,groupSelect,tabs,scheduler,onSelect,makePreview,onDestroy=()=>{}}){
 const catalog=animationCatalog(map,manifest),items=new Map(),visibleIds=new Set(),lifecycle=new PreviewLifecycle(makePreview),observerFactory=typeof IntersectionObserver==='function'?callback=>new IntersectionObserver(callback,{root:grid,rootMargin:'80px'}):null;
 let mode='faces',group='all',destroyed=false,observer=null;
 const observerCallback=entries=>{for(const entry of entries){const id=entry.target.dataset.animationId,visible=entry.isIntersecting&&entry.intersectionRatio>0;scheduler.setVisible(id,visible);lifecycle.setVisible(id,visible);if(visible)visibleIds.add(id);else visibleIds.delete(id);}};
 function source(){return mode==='faces'?catalog.faces:catalog.activities;}
 function visibleFrame(ids,time){lifecycle.update(ids,time);}
 let selected=null;
 function render(){if(destroyed)return;observer?.disconnect();for(const record of items.values()){scheduler.setVisible(record.item.id,false);lifecycle.remove(record.item.id);}items.clear();visibleIds.clear();grid.replaceChildren();const values=source().filter(item=>group==='all'||item.group===group);
  for(const item of values){const card=document.createElement('button');card.type='button';card.className='animation-card';card.dataset.animationId=item.id;card.setAttribute('aria-pressed',String(selected?.id===item.id&&selected.mode===mode));card.setAttribute('aria-label',`Preview ${item.label}`);const mount=document.createElement('span');mount.className='animation-thumb';mount.setAttribute('aria-hidden','true');const title=document.createElement('span');title.className='animation-card-name';title.textContent=item.label;const badge=document.createElement('span');badge.className='animation-card-selected';badge.textContent='Selected';card.append(mount,title,badge);card.addEventListener('click',()=>{selected={id:item.id,mode};onSelect(item,mode);for(const rec of items.values())rec.card.setAttribute('aria-pressed',String(rec.item.id===item.id&&rec.mode===mode));});grid.append(card);items.set(item.id,{item,mode,card,mount});lifecycle.register(item.id,{...item,mode},mount);}
  observer=observerFactory?.(observerCallback)||null;if(observer)for(const record of items.values())observer.observe(record.card);else for(const record of items.values()){scheduler.setVisible(record.item.id,true);lifecycle.setVisible(record.item.id,true);visibleIds.add(record.item.id);}
 }
 function setMode(value){mode=value==='activities'?'activities':'faces';group='all';groupSelect.replaceChildren(new Option('All groups','all'));const groups=[...new Set(source().map(item=>item.group))];for(const name of groups)groupSelect.add(new Option(name,name));groupSelect.value=group;grid.setAttribute('aria-labelledby',mode==='faces'?tabs.faces.id:tabs.activities.id);grid.setAttribute('aria-label',mode==='faces'?'Face animations':'Activity motions');tabs.faces.setAttribute('aria-selected',String(mode==='faces'));tabs.activities.setAttribute('aria-selected',String(mode==='activities'));render();}
 groupSelect.addEventListener('change',()=>{group=groupSelect.value;render();});tabs.faces.addEventListener('click',()=>setMode('faces'));tabs.activities.addEventListener('click',()=>setMode('activities'));
 scheduler.onFrame=visibleFrame;setMode('faces');
 return {catalog,setMode,get mode(){return mode;},setOpen(open){scheduler.setOpen(open);lifecycle.setOpen(open);},markSelected(id,selectedMode){selected=id?{id,mode:selectedMode}:null;for(const record of items.values())record.card.setAttribute('aria-pressed',String(record.item.id===id&&record.mode===selectedMode));},destroy(){if(destroyed)return;destroyed=true;observer?.disconnect();for(const id of [...items.keys()]){scheduler.setVisible(id,false);lifecycle.remove(id);}items.clear();grid.replaceChildren();scheduler.destroy();lifecycle.destroy();onDestroy();}};
}
