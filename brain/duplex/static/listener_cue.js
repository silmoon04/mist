const REST = Object.freeze({viseme:'rest',amount:0,active:false,source:'silence'});
const VISEMES = new Set(['rest','AA','EH','EE','OH','OO','MBP','FV','LNT','TH','S']);
const MAX_CUE_MS = 1500;
const MAX_VALID_FOR_MS = 350;
const START_DELAY_SECONDS = .02;
const CUE_GAIN = .65;

function boundedText(value, limit=128){
  return typeof value === 'string' && value.length > 0 && value.length <= limit;
}

function decodePcm(value){
  if(typeof value !== 'string' || value.length > 400000)throw new Error('Invalid listener cue PCM');
  let binary;
  try{binary=atob(value);}catch{throw new Error('Invalid listener cue PCM');}
  if(binary.length===0 || binary.length%2)throw new Error('Invalid listener cue PCM');
  const bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  const samples=new Float32Array(bytes.length/2);
  for(let i=0;i<samples.length;i++)samples[i]=view.getInt16(i*2,true)/32768;
  return samples;
}

/**
 * Plays one short, cached listener acknowledgement beside the normal reply
 * player. It never changes conversation state, transcript captions or floor.
 * The caller must observe listener_cue_cancel when a normal assistant reply
 * starts; audio_reset and newer user transcript revisions also cancel it.
 */
export function newListenerCue(context,face,onEvent=()=>{}){
  let audioEpoch=0,openUtterance=null,active=null,timer=null;
  const seenCueIds=new Set();

  function emit(entry,phase,reason){
    try{onEvent({type:'listener_cue_state',cue_id:entry?.cue_id??null,
      utterance_id:entry?.utterance_id??null,transcript_revision:entry?.transcript_revision??null,
      phase,...(reason?{reason}:{})});}catch{}
  }

  function outputTime(){
    const now=context.currentTime;
    const stamp=context.getOutputTimestamp?.();
    if(stamp && Number.isFinite(stamp.contextTime) && stamp.performanceTime>0 && context.state!=='suspended' && context.state!=='interrupted'){
      const elapsed=Math.max(0,(performance.now()-stamp.performanceTime)/1000);
      if(elapsed<.25)return Math.min(now,stamp.contextTime+elapsed);
    }
    const latency=Math.max(0,Number(context.outputLatency)||0)+Math.max(0,Number(context.baseLatency)||0);
    return Math.max(0,now-latency);
  }

  function stopTimer(){if(timer!==null){clearTimeout(timer);timer=null;}}
  function detachSpeech(entry){
    if(!entry?.speechAttached)return;
    entry.speechAttached=false;
    face.stopSpeech();
  }
  function cancel(reason='cancelled'){
    const entry=active;
    if(!entry)return false;
    active=null;stopTimer();
    entry.source.onended=null;
    try{entry.source.stop();}catch{}
    try{entry.source.disconnect();}catch{}
    try{entry.gain.disconnect();}catch{}
    detachSpeech(entry);
    emit(entry,'cancelled',reason);
    return true;
  }
  function skip(event,reason){emit(event,'skipped',reason);return false;}
  function tick(){
    timer=null;
    const entry=active;
    if(!entry)return;
    const at=outputTime();
    // A throttled tab can miss timer ticks while the AudioContext is
    // suspended. Check the wall-clock TTL before treating a scheduled start
    // time as reached, otherwise a stale clip would play immediately on resume.
    if(!entry.started && Date.now()>=entry.expiresAt){
      active=null;
      entry.source.onended=null;
      try{entry.source.stop();}catch{}
      try{entry.source.disconnect();}catch{}
      try{entry.gain.disconnect();}catch{}
      emit(entry,'skipped','expired_before_start');
      return;
    }
    if(context.state!=='suspended' && context.state!=='interrupted'){
      if(!entry.started && at>=entry.start){
        entry.started=true;entry.origin=entry.start;
        face.startSpeech({events:[],duration:Infinity,
          clock:()=>outputTime()-entry.origin,sample:()=>sample()});
        entry.speechAttached=true;
        emit(entry,'started');
      }
      if(entry.started && at>=entry.end){
        active=null;
        detachSpeech(entry);
        try{entry.source.disconnect();}catch{}
        try{entry.gain.disconnect();}catch{}
        emit(entry,'ended');
        return;
      }
    }
    timer=setTimeout(tick,16);
  }
  function sample(){
    const entry=active;
    if(!entry || !entry.started || context.state==='suspended' || context.state==='interrupted')
      return {...REST,time:entry?Math.max(0,outputTime()-entry.start):0,cue_id:entry?.cue_id??null};
    const at=outputTime(),elapsed=at-entry.start;
    if(elapsed<0 || elapsed>=entry.duration)return {...REST,time:Math.max(0,elapsed),cue_id:entry.cue_id};
    let cue=REST;
    for(const event of entry.events){if(event.time>elapsed)break;cue=event;}
    return {...cue,time:elapsed,active:true,source:'listener_cue',cue_id:entry.cue_id,epoch:entry.epoch};
  }

  function observe(event){
    if(!event || typeof event!=='object')return false;
    if(event.type==='user_transcript'){
      if(!boundedText(event.utterance_id) || !Number.isInteger(event.revision) || event.revision<0 || typeof event.final!=='boolean')return false;
      const previous=openUtterance;
      const same=previous?.utterance_id===event.utterance_id;
      if(same && event.revision<previous.revision)return false;
      // A newer transcript revision invalidates a queued cue. Once its first
      // sample has reached output, let that short syllable end under the same
      // user floor; a new utterance still cancels it immediately.
      if(active && !same)cancel('new_user_floor');
      else if(active && (event.revision>previous.revision || event.final) && !active.started)
        cancel(event.final?'user_turn_finished':'new_user_transcript');
      if(event.final)openUtterance=null;
      else openUtterance={utterance_id:event.utterance_id,revision:event.revision};
      return true;
    }
    if(event.type==='listener_cue_cancel'){
      if(event.cue_id && event.cue_id!==active?.cue_id)return false;
      return cancel(typeof event.reason==='string'?event.reason:'cancelled');
    }
    if(event.type==='audio_reset'){
      if(!Number.isInteger(event.epoch) || event.epoch<audioEpoch)return false;
      audioEpoch=event.epoch;
      cancel('audio_reset');
      return true;
    }
    return false;
  }

  function play(event,{normalPlaying=false,enabled=true}={}){
    if(!enabled){cancel('disabled');return skip(event,'disabled');}
    if(normalPlaying){cancel('assistant_playing');return skip(event,'assistant_playing');}
    if(!event || typeof event!=='object')return skip(null,'invalid_event');
    if(!openUtterance || event.utterance_id!==openUtterance.utterance_id || event.transcript_revision!==openUtterance.revision)
      return skip(event,'stale_user_transcript');
    if(!boundedText(event.cue_id) || !Number.isInteger(event.epoch) || event.epoch!==audioEpoch)
      return skip(event,'stale_audio_epoch');
    if(seenCueIds.has(event.cue_id))return skip(event,'duplicate_cue');
    if(!Number.isFinite(event.valid_for_ms) || event.valid_for_ms<=0 || event.valid_for_ms>MAX_VALID_FOR_MS)
      return skip(event,'expired_cue');
    const sampleRate=event.sample_rate;
    if(!Number.isFinite(sampleRate) || sampleRate<8000 || sampleRate>96000 ||
       !Number.isFinite(event.duration_ms) || event.duration_ms<=0 || event.duration_ms>MAX_CUE_MS)
      return skip(event,'invalid_duration');
    let samples;
    try{samples=decodePcm(event.pcm);}catch{return skip(event,'invalid_pcm');}
    const duration=samples.length/sampleRate;
    if(duration<=0 || duration*1000>MAX_CUE_MS+1 || Math.abs(duration*1000-event.duration_ms)>Math.max(35,event.duration_ms*.15))
      return skip(event,'invalid_duration');
    if(active)return skip(event,'cue_already_playing');
    let buffer,gain,source;
    try{
      buffer=context.createBuffer(1,samples.length,sampleRate);
      buffer.getChannelData(0).set(samples);
      source=context.createBufferSource();source.buffer=buffer;
      gain=context.createGain();gain.gain.value=CUE_GAIN;
      source.connect(gain);gain.connect(context.destination);
    }catch{return skip(event,'audio_unavailable');}
    const start=context.currentTime+START_DELAY_SECONDS;
    const entry={cue_id:event.cue_id,utterance_id:event.utterance_id,
      transcript_revision:event.transcript_revision,epoch:event.epoch,source,gain,start,
      end:start+duration,duration,started:false,speechAttached:false,origin:start,
      expiresAt:Date.now()+event.valid_for_ms,events:normalizeMouthCues(event.mouth_cues,duration,samples,sampleRate)};
    seenCueIds.add(event.cue_id);
    if(seenCueIds.size>128)seenCueIds.delete(seenCueIds.values().next().value);
    active=entry;
    try{source.start(start);}catch{active=null;try{source.disconnect();}catch{}try{gain.disconnect();}catch{}return skip(event,'audio_unavailable');}
    tick();
    return true;
  }

  function reset(reason='reset'){
    cancel(reason);
    openUtterance=null;
    seenCueIds.clear();
  }

  return Object.freeze({play,observe,reset,sample});
}

function normalizeMouthCues(values,duration,samples,sampleRate){
  const events=Array.isArray(values)?values.filter(event=>Number.isFinite(event?.time)&&event.time>=0&&event.time<duration&&VISEMES.has(event.viseme))
    .map(event=>({time:event.time,viseme:event.viseme,amount:Math.max(0,Math.min(1,Number(event.amount)||0))}))
    .sort((a,b)=>a.time-b.time):[];
  if(events.length)return events;
  const step=Math.max(1,Math.round(sampleRate*.02));
  for(let i=0;i<samples.length;i+=step){
    let power=0;const count=Math.min(step,samples.length-i);
    for(let j=i;j<i+count;j++)power+=samples[j]*samples[j];
    const rms=Math.sqrt(power/count);
    events.push({time:i/sampleRate,viseme:rms<.006?'rest':'AA',amount:rms<.006?0:Math.min(.9,.2+rms*6)});
  }
  return events;
}
