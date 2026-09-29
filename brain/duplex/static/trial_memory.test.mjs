import assert from 'node:assert/strict';
import {latestSourcedNotes,availableAudio} from './trial_memory.mjs';

const older={version:1,text:JSON.stringify({items:[{text:'Old detail',source_id:1}]})};
const current={version:2,text:JSON.stringify({items:[{kind:'known',text:'Preferred name is MIST',source_id:5,quote:'Call me MIST.'},{kind:'task',text:'Built six legs',source_id:9}]})};
assert.deepEqual(latestSourcedNotes([current,older]),{version:2,notes:[{text:'Preferred name is MIST',source:'5',kind:'known',quote:'Call me MIST.'},{text:'Built six legs',source:'9',kind:'task',quote:''}],total:2});
assert.deepEqual(latestSourcedNotes([]),{version:null,notes:[],total:0});
assert.deepEqual(availableAudio([{stream:'mic',chunk_count:2,byte_count:320},{stream:'assistant_generated',chunk_count:0,byte_count:0},{stream:'private',chunk_count:1,byte_count:20}]).map(item=>item.stream),['mic']);
console.log('Saved session notes and audio metadata are selected correctly.');
