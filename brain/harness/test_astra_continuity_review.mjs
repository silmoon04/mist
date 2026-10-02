import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {SpeechStatus} from '../duplex/static/speech_status.mjs';

// Execute the production renderer with a deterministic audio clock and DOM sink.
const app = readFileSync(new URL('../duplex/static/app.js', import.meta.url), 'utf8');
const render = app.slice(app.indexOf('function renderExpression(){'), app.indexOf('\nfunction expression('));
const state = app.match(/function state\(value\)\{[^\n]+\}/)[0];
const receive = app.slice(app.indexOf('async function receive(event){'), app.indexOf("\n$('talk').onclick"));

function fixture() {
  let audible = false, queued = false, now = 0;
  const elements = new Map();
  const events = [];
  const policy = {faceId:'neutral', expression:'neutral', source:'state', state:'available'};
  const sandbox = {
    player:{epoch:0,segments:[],sample:()=>({active:audible}), get playing(){return queued;},
      append(event){this.segments.push({event});}},
    speechStatus:new SpeechStatus({now:()=>now}),
    context:{state:'running'}, conversationState:'thinking', priorSpeechBusy:false,
    reportedPlaybackBusy:false, ready:true,stateRevision:0,speechStateRevision:0,
    $:id=>{if(!elements.has(id))elements.set(id,{textContent:'',getAttribute:()=>null,setAttribute(){}});return elements.get(id);},
    send:event=>events.push(event),
    expressionPolicy:{setState:value=>({...policy,state:value})},
    timedFaceReceipt:null,activityState:{update:value=>value},lastActivityState:null,
    face:{handleEvent(){},setExpression(){},clearGaze(){},setGaze(){}},
    currentFace:null,expressionPinned:false,lastFaceId:null,displayedFaceId:null,lastGaze:null,
    trace(){},trialArchitecture:null,preview:{active:false,displayedFace:value=>value},map:{expressions:{neutral:{primary:'neutral'}}},
    listenerCue:null,transcript:{assistant:'',user:''},pendingCaption:'',addTranscript(){},performance,
    atob:value=>Buffer.from(value,'base64').toString('binary'),
  };
  vm.createContext(sandbox);
  vm.runInContext(render+'\n'+state+'\n'+receive,sandbox);
  return {
    sandbox, events,
    status:()=>elements.get('status').textContent,
    render:()=>sandbox.renderExpression(),
    state:value=>sandbox.state(value),
    receive:event=>sandbox.receive(event),
    clock:(at,audio,queue)=>{now=at;audible=audio;queued=queue;},
    audio:seq=>sandbox.receive({type:'audio',epoch:0,seq,pcm:'AAA=',sample_rate:24000}),
    complete:seq=>sandbox.speechStatus.complete({epoch:0,seq}),
  };
}

test('actual UI holds unfinished speech and releases a completed audible tail',()=>{
  const ui=fixture();
  ui.audio(0);ui.clock(0,true,true);ui.render();
  assert.equal(ui.status(),'Speaking, still listening');
  ui.clock(300,false,false);ui.state('checking');
  assert.equal(ui.status(),'Speaking, still listening');
  assert.equal(ui.events.at(-1).playing,true);
  ui.clock(600,false,false);ui.render();
  assert.equal(ui.status(),'Waiting for speech');
  assert.equal(ui.events.at(-1).playing,true);
  ui.clock(700,true,true);ui.complete(0);ui.render();
  assert.equal(ui.events.at(-1).playing,true);
  ui.clock(1000,false,false);ui.render();
  assert.equal(ui.events.at(-1).playing,false);
});

test('actual UI keeps paused output owned until resume or interruption',()=>{
  const ui=fixture();
  ui.audio(0);ui.clock(0,true,true);ui.render();
  ui.sandbox.context.state='suspended';ui.clock(900,false,true);ui.render();
  assert.equal(ui.status(),'Audio paused');
  assert.equal(ui.events.at(-1).playing,true);
  ui.sandbox.context.state='running';ui.clock(950,true,true);ui.render();
  assert.equal(ui.status(),'Speaking, still listening');
  ui.sandbox.speechStatus.reset(1);ui.clock(960,false,false);ui.render();
  assert.equal(ui.status(),'Listening');
  assert.equal(ui.events.at(-1).playing,false);
});

test('a delegation started during the spoken tail remains visible after audio drains',()=>{
  const ui=fixture();
  ui.audio(0);ui.clock(0,true,true);ui.render();ui.complete(0);
  // server.py emits checking for delegation.created while foreground PCM may remain queued.
  ui.clock(100,true,true);ui.state('checking');
  assert.equal(ui.status(),'Speaking, still listening');
  ui.clock(1000,false,false);ui.render();
  assert.equal(ui.status(),'Checking','draining prior PCM must not erase a newer delegation state');
});

for(const pending of ['thinking','checking'])test(`newer ${pending} status survives the completed tail`,()=>{
  const ui=fixture();ui.state(pending);
  ui.audio(0);ui.clock(0,true,true);ui.render();ui.complete(0);
  ui.clock(100,true,true);ui.state(pending);
  ui.clock(1000,false,false);ui.render();
  assert.equal(ui.status(),pending==='thinking'?'Thinking':'Checking');
});

test('a transient speaking state during output does not become a stuck idle status',()=>{
  const ui=fixture();ui.audio(0);ui.clock(0,true,true);ui.render();
  ui.state('speaking');ui.complete(0);ui.clock(1000,false,false);ui.render();
  assert.equal(ui.status(),'Listening');
});

test('completion of one stream cannot release a second unfinished stream',()=>{
  const ui=fixture();ui.audio(0);ui.clock(0,true,true);ui.render();
  ui.audio(1);ui.complete(0);ui.clock(200,false,false);ui.render();
  assert.equal(ui.status(),'Speaking, still listening');
  assert.equal(ui.events.at(-1).playing,true);
  ui.clock(600,false,false);ui.render();assert.equal(ui.status(),'Waiting for speech');
  ui.complete(1);ui.clock(1000,false,false);ui.render();
  assert.equal(ui.status(),'Listening');
  assert.equal(ui.events.at(-1).playing,false);
});

test('a queued second reply consumes the thinking state that preceded its own audio',()=>{
  const ui=fixture();ui.audio(0);ui.clock(0,true,true);ui.render();ui.complete(0);
  ui.state('thinking');
  ui.audio(1);ui.clock(100,true,true);ui.render();ui.complete(1);
  ui.clock(1000,false,false);ui.render();
  assert.equal(ui.status(),'Listening','new reply audio fulfils its preceding thinking state even without a queue gap');
});

test('tool-only assistant completion returns a silent completed reply to listening',async()=>{
  const ui=fixture();ui.state('thinking');
  await ui.receive({type:'transcript_done',role:'assistant',text:''});
  ui.clock(1000,false,false);ui.render();
  assert.equal(ui.status(),'Listening','no audio exists to cause a busy-to-idle transition');
});

test('a silent assistant completion does not erase a newer checking state',async()=>{
  const ui=fixture();ui.state('thinking');ui.state('checking');
  await ui.receive({type:'transcript_done',role:'assistant',text:''});
  ui.render();assert.equal(ui.status(),'Checking');
});

test('a nonempty final-only reply remains pending while its TTS has not arrived',async()=>{
  const ui=fixture();ui.state('thinking');
  await ui.receive({type:'transcript_done',role:'assistant',text:'This reply still needs speech.'});
  ui.render();assert.equal(ui.status(),'Thinking');
});

test('a blank final cannot discard streamed words awaiting their first PCM',async()=>{
  const ui=fixture();ui.state('thinking');
  await ui.receive({type:'transcript_delta',role:'assistant',text:'Already sent to speech.'});
  await ui.receive({type:'transcript_done',role:'assistant',text:''});
  ui.render();assert.equal(ui.status(),'Thinking');
});

test('another packet of the same reply cannot consume a newer checking state',()=>{
  const ui=fixture();ui.audio(0);ui.clock(0,true,true);ui.render();
  ui.state('checking');ui.audio(0);ui.complete(0);
  ui.clock(1000,false,false);ui.render();assert.equal(ui.status(),'Checking');
});
