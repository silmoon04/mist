import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import vm from 'node:vm';

const sourcePath=new URL('../deploy/pages/app.js',import.meta.url);
const source=readFileSync(existsSync(sourcePath)?sourcePath:new URL('../../docs/app.js',import.meta.url),'utf8');
function fixture(first){
 const nodes=new Map(), timers=new Map(), listeners=new Map();let endpoint=first,next=0;
 const document={hidden:false,getElementById(id){if(!nodes.has(id))nodes.set(id,{hidden:false,textContent:'',href:'',removeAttribute(k){delete this[k];},addEventListener(){}});return nodes.get(id);},addEventListener(name,fn){listeners.set(name,fn);}};
 const context={document,window:{addEventListener(name,fn){listeners.set(name,fn);}},URL,Date,AbortSignal,
  fetch:async()=>({ok:true,json:async()=>endpoint}),
  setTimeout(fn,ms){timers.set(++next,{fn,ms});return next;},clearTimeout(id){timers.delete(id);}};
 vm.runInNewContext(source,context);
 return {nodes,timers,listeners,document,set(value){endpoint=value;}};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const online=host=>({status:'online',api_origin:`https://${host}.trycloudflare.com`,updated_at:'2026-09-29T20:35:03Z'});

test('open launcher discovers a changed tunnel without a reload',async()=>{
 const f=fixture(online('old-host'));await flush();
 assert.equal(f.nodes.get('open').href,'https://old-host.trycloudflare.com/try');
 assert.equal(f.timers.size,1,'A saved address must be refreshed while the launcher stays open');
 f.set(online('new-host'));const [id,timer]=[...f.timers][0];f.timers.delete(id);await timer.fn();await flush();
 assert.equal(f.nodes.get('open').href,'https://new-host.trycloudflare.com/try');
 assert.equal(f.timers.size,1);
});
test('offline launcher retries and becomes usable when the host recovers',async()=>{
 const f=fixture({status:'offline'});await flush();assert.equal(f.nodes.get('open').hidden,true);
 assert.equal(f.timers.size,1);f.set(online('recovered-host'));
 const [id,timer]=[...f.timers][0];f.timers.delete(id);await timer.fn();await flush();
 assert.equal(f.nodes.get('open').hidden,false);
});
test('network recovery and returning to the tab refresh the address',async()=>{
 const f=fixture(online('first'));await flush();
 assert.equal(typeof f.listeners.get('online'),'function');
 assert.equal(typeof f.listeners.get('visibilitychange'),'function');
 f.set(online('second'));await f.listeners.get('online')();await flush();
 assert.equal(f.nodes.get('open').href,'https://second.trycloudflare.com/try');
});
