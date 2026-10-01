import assert from 'node:assert/strict';
import test from 'node:test';

class Element {
  constructor() { Object.assign(this, {children:[], hidden:false, value:'', textContent:'', dataset:{}, listeners:{}, style:{}, attrs:{}, paused:true}); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(key, value) { this.attrs[key] = value; }
  removeAttribute(key) { delete this.attrs[key]; }
  querySelectorAll() { return []; }
  pause() { this.paused = true; }
  load() {}
}

const ids = ['review-audio','review-face','review-tracks','review-time','review-seek','review-audio-note','review-play','review-source','review-view','live-view','view-title','view-badge','pause','transcript-list','transcript-search','failure-list','failure-count','metrics','tab-transcript','tab-failures','tab-timing','tab-events','panel-transcript','panel-failures','panel-timing','panel-events','review-speed','review-back','review-forward'];
function setup(fetchImpl) {
  const nodes = new Map(ids.map(id => [id, new Element()]));
  nodes.get('review-audio').paused = true;
  const timers = new Map(); let timerId = 0;
  globalThis.document = {getElementById:id=>nodes.get(id), createElement:()=>new Element(), createDocumentFragment:()=>new Element(), createTextNode:text=>({textContent:text}), querySelector:()=>new Element()};
  globalThis.location = {origin:'https://mist.example'};
  globalThis.window = {};
  globalThis.fetch = fetchImpl;
  globalThis.setTimeout = callback => {const id=++timerId;timers.set(id,callback);return id;};
  globalThis.clearTimeout = id => timers.delete(id);
  globalThis.cancelAnimationFrame = ()=>{};
  globalThis.requestAnimationFrame = ()=>1;
  return {nodes,timers};
}
const settle = async () => { for(let i=0;i<8;i++) await new Promise(setImmediate); };
const text = node => [node.textContent,...(node.children||[]).map(child=>typeof child==='string'?child:text(child))].join(' ');

async function createUI() {
  const {createReviewUI} = await import('../duplex/static/trial_review.mjs?test');
  return createReviewUI();
}

test('stalled review is aborted at 20 seconds and shows an explicit retry', async () => {
  let request;
  const {nodes,timers} = setup((url, options) => {request={url,options};return new Promise(()=>{});});
  const ui = await createUI();
  const loading = ui.load('recording'); await settle();
  assert.equal(request.options.credentials,'same-origin');
  assert.equal(request.options.signal.aborted,false);
  assert.equal(timers.size,1);
  [...timers.values()][0](); await loading;
  assert.equal(request.options.signal.aborted,true);
  assert.match(text(nodes.get('transcript-list')),/within 20 seconds/);
  assert.equal(nodes.get('transcript-list').children.at(-1).textContent,'Retry review');
  assert.match(nodes.get('review-audio-note').textContent,/Raw events remain available/);
});

test('an AbortError caused by the timeout is still shown as a timeout', async () => {
  const {nodes,timers} = setup((url, options) => new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>{const error=Error('aborted');error.name='AbortError';reject(error);});
  }));
  const ui = await createUI();
  const loading = ui.load('recording'); await settle();
  [...timers.values()][0](); await loading;
  assert.match(text(nodes.get('transcript-list')),/within 20 seconds/);
  assert.equal(nodes.get('transcript-list').children.at(-1).textContent,'Retry review');
});

test('changing session aborts the old review and ignores its stale failure', async () => {
  const pending = [];
  const {nodes} = setup((url, options) => new Promise(resolve=>pending.push({url,options,resolve})));
  const ui = await createUI();
  const old = ui.load('old'); await settle();
  const current = ui.load('current'); await settle();
  assert.equal(pending[0].options.signal.aborted,true);
  pending[0].resolve({ok:false,status:500}); await old;
  pending[1].resolve({ok:false,status:503}); await current;
  assert.match(text(nodes.get('transcript-list')),/Request failed \(503\)/);
  assert.doesNotMatch(text(nodes.get('transcript-list')),/500/);
});

test('leaving history mode aborts pending review and suppresses cancellation UI', async () => {
  let request;
  const {nodes,timers} = setup((url, options) => {request={options};return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{const error=Error('aborted');error.name='AbortError';reject(error);}));});
  const ui = await createUI();
  const loading = ui.load('recording'); await settle();
  ui.showMode('live'); await loading;
  assert.equal(request.options.signal.aborted,true);
  assert.equal(text(nodes.get('transcript-list')).includes('Retry review'),false);
});
