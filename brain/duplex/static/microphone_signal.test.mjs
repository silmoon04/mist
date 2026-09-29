import assert from 'node:assert/strict';
import {MicrophoneSignal} from './microphone_signal.mjs';

const meter=new MicrophoneSignal();
assert.deepEqual(meter.observe(new Int16Array(320)),Array(8).fill(0));
const packet=new Int16Array(320);
packet.fill(12000,80,120);
const levels=meter.observe(packet);
assert.equal(levels[2]>0.1,true);
assert.equal(levels.every((level,index)=>index===2||level===0),true);
const previous=levels[2];
const falling=meter.observe(new Int16Array(320));
assert.equal(falling[2]<previous,true);
assert.equal(falling[2]>0,true);
assert.deepEqual(meter.clear(),Array(8).fill(0));
console.log('Microphone signal follows packet energy and clears to a quiet state.');
