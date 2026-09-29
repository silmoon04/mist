import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Exercise the browser handlers with small DOM/transport stand-ins. This test
// never starts a voice server, opens a socket, or calls a provider.
const source = readFileSync(new URL('../duplex/static/app.js', import.meta.url), 'utf8');

function section(start, end) {
  const from = source.indexOf(start);
  assert.notEqual(from, -1, `Missing ${start}`);
  const to = source.indexOf(end, from);
  assert.notEqual(to, -1, `Missing end of ${start}`);
  return source.slice(from, to);
}

const receiveSource = section('async function receive(event)', "\n$('talk').onclick");
const endSource = section('async function end()', '\nasync function start(');

function receiver() {
  const calls = [];
  const nodes = new Map();
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {});
    return nodes.get(id);
  };
  const context = {
    listenerCue: null,
    face: {handleEvent: () => calls.push('face')},
    end: async () => calls.push('end'),
    error: message => calls.push(['error', message]),
    $,
  };
  vm.runInNewContext(receiveSource, context);
  return {receive: context.receive, calls, $};
}

test('terminal model error ends the session and offers reconnect', async () => {
  const {receive, calls, $} = receiver();
  await receive({type: 'error', reconnect_required: true, message: 'Lab text model failed; reconnect to continue.'});
  assert.deepEqual(calls, ['face', 'end', ['error', 'Lab text model failed; reconnect to continue.']]);
  assert.equal($('status').textContent, 'Reconnect to continue');
  assert.equal($('talk').textContent, 'Reconnect');
});

test('ordinary error leaves the session connected', async () => {
  const {receive, calls} = receiver();
  await receive({type: 'error', message: 'Recoverable warning'});
  assert.deepEqual(calls, ['face', ['error', 'Recoverable warning']]);
});

test('existing fatal error still ends the session', async () => {
  const {receive, calls} = receiver();
  await receive({type: 'error', fatal: true, message: 'Fatal error'});
  assert.deepEqual(calls, ['face', 'end', ['error', 'Fatal error']]);
});

test('end clears microphone, socket, playback, caption and face activity', async () => {
  const calls = [];
  const nodes = new Map();
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {});
    return nodes.get(id);
  };
  const context = {
    listenerCue: {reset: () => calls.push('cue reset')},
    connectionGeneration: 2,
    starting: true,
    ready: true,
    pendingPlaybackConfiguration: 'old',
    timedFaceReceipt: {},
    pendingCaption: 'old speech',
    lastSpeechKey: 'old',
    stream: {getTracks: () => [{stop: () => calls.push('microphone stop')}]},
    capture: {disconnect: () => calls.push('capture disconnect')},
    player: {reset: () => calls.push('playback reset')},
    ws: {close: () => calls.push('socket close')},
    wake: {release: async () => calls.push('wake release')},
    $,
    transcript: {user: 'old user', assistant: 'old assistant'},
    renderUserTranscript: () => calls.push('user transcript reset'),
    userTranscript: {reset: () => ({})},
    backgroundJob: {},
    face: {resetActivities: () => calls.push('face activity reset')},
    connectionActivity: 'old token',
    expressionPolicy: {reset: () => calls.push('expression reset')},
    state: value => calls.push(['state', value]),
  };
  vm.runInNewContext(endSource, context);
  await context.end();
  assert.deepEqual(calls, [
    'cue reset', 'microphone stop', 'capture disconnect', 'playback reset',
    'socket close', 'user transcript reset', 'face activity reset',
    'expression reset', ['state', 'available'], 'wake release',
  ]);
  assert.equal(context.ws, null);
  assert.equal(context.ready, false);
  assert.equal(context.pendingCaption, '');
  assert.equal(context.connectionActivity, null);
  assert.equal($('caption').textContent, '');
  assert.equal($('talk').textContent, 'Start conversation');
  assert.equal($('stop').disabled, true);
});
