import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {AnimationPreview, animationCatalog} from './animation_picker.mjs';

const map=JSON.parse(readFileSync(new URL('../face_map.json',import.meta.url)));
const manifest=JSON.parse(readFileSync(new URL('../../art_direction/artist_studio_20260916/reuse/handdrawn_v6/manifest.json',import.meta.url)));

test('catalog exposes every exact face and every current activity',()=>{
 const catalog=animationCatalog(map,manifest);
 assert.equal(catalog.faces.length,40);
 assert.deepEqual(new Set(catalog.faces.map(item=>item.id)),new Set(Object.keys(map.faces)));
 assert.deepEqual(new Set(catalog.activities.map(item=>item.id)),new Set(manifest.activities.map(item=>item.id)));
 assert.ok(catalog.faces.every(item=>item.label&&item.group));
});

test('manual selection stays local and returns to the latest live face',()=>{
 const calls=[];
 const preview=new AnimationPreview({
  previewActivity:id=>{calls.push(['preview',id]);return {accepted:true};},
  clearPreviewActivity:()=>calls.push(['clear']),
 });
 let liveFace='39_neutral_02_manual';
 preview.selectFace('01_angry_01_manual');
 assert.equal(preview.displayedFace(liveFace),'01_angry_01_manual');
 preview.selectActivity('planning');
 liveFace='09_happy_03_manual';
 assert.equal(preview.displayedFace(liveFace),'01_angry_01_manual');
 preview.release();
 assert.equal(preview.displayedFace(liveFace),liveFace);
 assert.equal(preview.active,false);
 assert.deepEqual(calls,[['preview','planning'],['clear']]);
});
