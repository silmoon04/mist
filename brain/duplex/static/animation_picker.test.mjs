import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {AnimationPreview,PreviewLifecycle,VisiblePreviewScheduler,animationCatalog} from './animation_picker.mjs';

const map=JSON.parse(readFileSync(new URL('../face_map.json',import.meta.url)));
const assetsRoot=new URL('../../art_direction/artist_studio_20260916/reuse/handdrawn_v6/',import.meta.url);
const manifest=JSON.parse(readFileSync(new URL('../../art_direction/artist_studio_20260916/reuse/handdrawn_v6/manifest.json',import.meta.url)));

test('catalog includes every exact face ID and all nine activity motions with preview frames',()=>{
 const catalog=animationCatalog(map,manifest);
 assert.equal(catalog.faces.length,40);
 assert.deepEqual(catalog.faces.map(item=>item.id).sort(),Object.keys(map.faces).sort());
 assert.equal(catalog.activities.length,9);
 assert.deepEqual(catalog.activities.map(item=>item.id).sort(),manifest.activities.map(item=>item.id).sort());
 assert.ok(catalog.faces.every(item=>item.label&&item.group));
 for(const activity of catalog.activities){
  const frames=manifest.assets[activity.id]?.frames||[];
  assert.equal(frames.length,12,`${activity.id} should have a full motion preview`);
  for(const frame of frames)assert.ok(existsSync(new URL(frame.file,assetsRoot)),`${activity.id}: missing ${frame.file}`);
 }
});

test('manual face stays selected over live updates and Return to live restores the latest expression',()=>{
 const calls=[];
 const preview=new AnimationPreview({previewActivity:id=>{calls.push(['preview',id]);return {accepted:true};},clearPreviewActivity:()=>calls.push(['clear'])});
 let liveFace='39_neutral_02_manual';
 preview.selectFace('01_angry_01_manual');
 assert.equal(preview.displayedFace(liveFace),'01_angry_01_manual');
 assert.equal(preview.selectActivity('planning'),true);
 liveFace='09_happy_03_manual';
 assert.equal(preview.displayedFace(liveFace),'01_angry_01_manual');
 preview.release();
 assert.equal(preview.displayedFace(liveFace),liveFace);
 assert.equal(preview.active,false);
 assert.deepEqual(calls,[['preview','planning'],['clear']]);
});

test('rejected activity selection keeps the current preview and can still be returned to live',()=>{
 const preview=new AnimationPreview({previewActivity:id=>({accepted:id==='reading'}),clearPreviewActivity(){}});
 assert.equal(preview.selectActivity('reading'),true);
 assert.equal(preview.selectActivity('unknown'),false);
 assert.equal(preview.activityId,'reading');
 preview.release();
 assert.equal(preview.active,false);
});

test('selecting an active activity again returns to live animation',()=>{
 const calls=[];
 const preview=new AnimationPreview({previewActivity:id=>{calls.push(['preview',id]);return {accepted:true};},clearPreviewActivity:()=>calls.push(['clear'])});
 assert.equal(preview.selectActivity('reading'),true);
 assert.equal(preview.selectActivity('reading'),true);
 assert.equal(preview.activityId,null);
 assert.equal(preview.active,false);
 assert.deepEqual(calls,[['preview','reading'],['clear']]);
});

test('one shared scheduler ticks visible cards at 15 fps and cancels on close, reduced motion, and destroy',()=>{
 let next=0;const queued=new Map(),cancelled=[],frames=[];
 const scheduler=new VisiblePreviewScheduler({requestFrame:callback=>{const id=++next;queued.set(id,callback);return id;},cancelFrame:id=>{cancelled.push(id);queued.delete(id);},onFrame:(ids,time)=>frames.push([ids,time])});
 const step=time=>{const [id,callback]=queued.entries().next().value||[];if(!callback)return false;queued.delete(id);callback(time);return true;};
 scheduler.setVisible('a',true);assert.equal(queued.size,0);
 scheduler.setOpen(true);assert.equal(queued.size,1);
 step(0);step(66);assert.equal(frames.length,0);
 step(67);assert.equal(frames.length,1);assert.deepEqual(frames[0][0],['a']);
 scheduler.setOpen(false);assert.ok(cancelled.length>=1);assert.equal(queued.size,0);
 scheduler.setOpen(true);scheduler.setReducedMotion(true);assert.equal(queued.size,0);
 assert.equal(frames.length,2,'reduced motion draws one static frame');
 scheduler.setReducedMotion(false);assert.equal(queued.size,1);
 scheduler.destroy();assert.equal(queued.size,0);scheduler.setVisible('a',false);assert.equal(scheduler.visible.size,0);
});

test('preview lifecycle reuses visible card resources and releases them on hide, close, and teardown',()=>{
 const mounts=()=>({clears:0,replaceChildren(){this.clears++;}}),created=[],destroyed=[],updated=[],modes=[],receivedMounts=[];
 const lifecycle=new PreviewLifecycle((item,mode,mount)=>{modes.push(mode);receivedMounts.push(mount);const record={id:item.id,mount,destroy(){destroyed.push(this.id);},update(time){updated.push([this.id,time]);}};created.push(record.id);return record;});
 const mount=mounts();lifecycle.register('face',{id:'face',mode:'faces'},mount);
 lifecycle.setVisible('face',true);assert.deepEqual(created,[],'hidden picker must not create a renderer');
 lifecycle.setOpen(true);assert.deepEqual(created,['face']);assert.deepEqual(modes,['faces']);assert.strictEqual(receivedMounts[0],mount);lifecycle.update(['face'],67);assert.deepEqual(updated,[['face',67]]);
 lifecycle.setVisible('face',false);assert.deepEqual(destroyed,['face']);assert.equal(mount.clears,1);
 lifecycle.setVisible('face',true);assert.deepEqual(created,['face','face']);assert.deepEqual(modes,['faces','faces']);assert.strictEqual(receivedMounts[1],mount);
 lifecycle.setOpen(false);assert.deepEqual(destroyed,['face','face']);assert.equal(mount.clears,2);
 lifecycle.setOpen(true);assert.deepEqual(created,['face','face','face']);
 lifecycle.destroy();assert.deepEqual(destroyed,['face','face','face']);assert.equal(mount.clears,3);
 lifecycle.setOpen(true);assert.equal(created.length,3,'destroyed lifecycle rejects later activation');
});
