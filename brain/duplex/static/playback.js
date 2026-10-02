const REST = Object.freeze({viseme:'rest',amount:0,active:false,source:'silence'});
const VISEMES = new Set(['rest','AA','EH','EE','OH','OO','MBP','FV','LNT','TH','S']);
const MAX_QUEUED_SECONDS=120;
const MAX_FALLBACK_CAPTION_STEP=24;
const MAX_PACKET_RESUME_GAP_SECONDS=.25;
const PACKET_RESUME_LEAD_SECONDS=.01;

export class Playback {
  #startBufferMs=200;
  constructor(context,face,onLag=()=>{},onIdle=()=>{},onStart=()=>{},onCaption=()=>{}) {
    this.context=context;this.face=face;this.onLag=onLag;this.onIdle=onIdle;this.onStart=onStart;
    this.epoch=0;this.end=0;this.sources=new Set();this.segments=[];this.timer=null;
    this.startedKey=null;this.origin=0;this.speechAttached=false;this.clockSource='context';
    this.onCaption=onCaption;this.caption='';this.captionSource='unavailable';this.captionKey=null;this.captionSequence=null;this.captionTurnId=null;
    this.finalCaptions=new Map();
    this.lastPacketKey=null;
  }
  get startBufferMs(){return this.#startBufferMs;}
  setStartBufferMs(value){
    if(!Number.isInteger(value)||value<120||value>400)throw new RangeError('Playback start buffer must be an integer from 120 to 400 ms');
    this.#startBufferMs=value;return value;
  }
  get playing(){return this.segments.length>0;}
  outputTime(){
    // The output timestamp follows the speaker rather than the render queue.
    const now=this.context.currentTime;
    const stamp=this.context.getOutputTimestamp?.();
    if(stamp && Number.isFinite(stamp.contextTime) && stamp.performanceTime>0 && this.context.state!=='suspended'){
      const elapsed=Math.max(0,(performance.now()-stamp.performanceTime)/1000);
      if(elapsed<.25){this.clockSource='output_timestamp';return Math.min(now,stamp.contextTime+elapsed);}
    }
    const latency=Math.max(0,Number(this.context.outputLatency)||0)+Math.max(0,Number(this.context.baseLatency)||0);
    this.clockSource=latency?'context_minus_latency':'context';
    return Math.max(0,now-latency);
  }
  sample(){
    const at=this.outputTime();
    if(this.context.state==='suspended' || this.context.state==='interrupted')return {...REST,time:Math.max(0,at-this.origin),caption:this.caption,captionSource:this.captionSource,playback_buffer_ms:this.startBufferMs};
    this.advanceCaption(at);
    const segment=this.segments.find(s=>at>=s.start && at<s.end);
    if(!segment)return {...REST,time:Math.max(0,at-this.origin),caption:this.caption,captionSource:this.captionSource,playback_buffer_ms:this.startBufferMs};
    const t=at-segment.start;
    let cue=REST;
    for(const e of segment.events){if(e.time>t)break;cue=e;}
    return {...cue,time:Math.max(0,at-this.origin),active:true,source:segment.alignment_source,
      epoch:this.epoch,sequence:segment.event.seq,turn_id:segment.event.turn_id,
      audioTime:at,caption:this.caption,captionSource:this.captionSource,playback_buffer_ms:this.startBufferMs};
  }
  advanceCaption(at){
    const previous=`${this.captionKey}|${this.captionSource}|${this.caption}`;
    for(const segment of this.segments){
      if(at<segment.start)break;
      const key=`${this.epoch}:${segment.event.seq}`;
      if(key!==this.captionKey){this.captionKey=key;this.captionSequence=segment.event.seq;this.captionTurnId=segment.event.turn_id??null;this.caption='';this.captionSource='unavailable';}
      for(const cue of segment.captions){
        if(cue.time>at-segment.start)break;
        const candidate=cue.text.replace(/\s+/g,' ').trimStart();
        if(segment.caption_source.includes('text_fallback')&&this.caption&&
          (!candidate.startsWith(this.caption)||candidate.length-this.caption.length>MAX_FALLBACK_CAPTION_STEP))continue;
        this.caption=candidate;this.captionSource=segment.caption_source;
      }
    }
    for(const [sequence,final] of this.finalCaptions){
      const last=[...this.segments].reverse().find(s=>s.event.seq===sequence);
      if(last && at>=last.end && this.captionSequence===sequence){
        this.captionKey=`${this.epoch}:${sequence}`;this.captionSequence=sequence;this.captionTurnId=final.turn_id??this.captionTurnId;
        this.caption=final.text;this.captionSource=final.source;this.finalCaptions.delete(sequence);
      }
    }
    if(previous!==`${this.captionKey}|${this.captionSource}|${this.caption}`){
      this.onCaption({text:this.caption,source:this.captionSource,epoch:this.epoch,sequence:this.captionSequence,turn_id:this.captionTurnId});
    }
  }
  reset(epoch=this.epoch+1){
    this.epoch=epoch;
    for(const source of this.sources){source.onended=null;try{source.stop();}catch{}try{source.disconnect();}catch{}}
    this.sources.clear();this.segments=[];
    if(this.timer!==null)clearTimeout(this.timer);
    this.timer=null;this.end=this.context.currentTime;this.startedKey=null;this.speechAttached=false;
    this.lastPacketKey=null;
    this.caption='';this.captionSource='unavailable';this.captionKey=null;this.captionSequence=null;this.captionTurnId=null;
    this.finalCaptions.clear();
    this.onCaption({text:'',source:'reset',epoch:this.epoch,sequence:null,turn_id:null});
    this.face.stopSpeech();
  }
  completeCaption(event){
    if(event.epoch!==this.epoch||!Number.isInteger(event.seq)||typeof event.text!=='string')return;
    this.finalCaptions.set(event.seq,{text:event.text,source:event.source||'reply_text_audio_end_fallback',turn_id:event.turn_id??null});
    if(!this.segments.some(s=>s.event.seq===event.seq)&&this.captionSequence===event.seq){
      this.caption=event.text;this.captionSource=event.source||'reply_text_audio_end_fallback';
      this.captionTurnId=event.turn_id??this.captionTurnId;
      this.finalCaptions.delete(event.seq);
      this.onCaption({text:this.caption,source:this.captionSource,epoch:this.epoch,sequence:event.seq,turn_id:this.captionTurnId});
    }
  }
  tick(){
    this.timer=null;
    const at=this.outputTime();
    if(this.context.state!=='suspended' && this.context.state!=='interrupted'){
      this.advanceCaption(at);
      const segment=this.segments.find(s=>at>=s.start && at<s.end);
      if(segment){
        const key=`${this.epoch}:${segment.event.seq}`;
        if(key!==this.startedKey){this.startedKey=key;this.onStart(segment.event);}
      }
      // Retain the current packet until its final sample reaches the output.
      while(this.segments.length && this.segments[0].end<=at)this.segments.shift();
      if(!this.segments.length && this.speechAttached){
        this.speechAttached=false;this.startedKey=null;this.face.stopSpeech();this.onIdle();return;
      }
    }
    if(this.segments.length)this.timer=setTimeout(()=>this.tick(),16);
  }
  append(event){
    if(!Number.isInteger(event.epoch) || event.epoch<this.epoch)return;
    if(event.epoch>this.epoch)this.reset(event.epoch);
    if(!Number.isFinite(event.sample_rate) || event.sample_rate<8000 || event.sample_rate>96000)throw Error('Invalid PCM sample rate');
    const binary=atob(event.pcm);if(binary.length%2)throw Error('Invalid PCM');
    if(!binary.length)return;
    const now=this.context.currentTime;
    const packetKey=`${event.epoch}:${event.seq}`;
    // A brief packet underrun has already spent the initial buffering reserve.
    // Reapplying it inserts a new audible pause on every late packet.
    const resuming=packetKey===this.lastPacketKey&&now-this.end<=MAX_PACKET_RESUME_GAP_SECONDS;
    const start=this.end>now?this.end:now+(resuming?PACKET_RESUME_LEAD_SECONDS:this.startBufferMs/1000);
    const duration=binary.length/(2*event.sample_rate),projected=start-now+duration;
    if(projected>MAX_QUEUED_SECONDS+1e-7){
      const detail={reason:'playback_buffer_limit',limit_ms:MAX_QUEUED_SECONDS*1000,
        queued_ms:Math.max(0,this.end-now)*1000,incoming_ms:duration*1000,projected_ms:projected*1000,
        rejected_epoch:event.epoch};
      this.reset(event.epoch+1);this.onLag(detail);return;
    }
    const bytes=Uint8Array.from(binary,c=>c.charCodeAt(0)),view=new DataView(bytes.buffer);
    const audio=this.context.createBuffer(1,bytes.length/2,event.sample_rate),samples=audio.getChannelData(0);
    for(let i=0;i<samples.length;i++)samples[i]=view.getInt16(i*2,true)/32768;
    const source=this.context.createBufferSource();source.buffer=audio;source.connect(this.context.destination);
    this.end=start+audio.duration;
    this.lastPacketKey=packetKey;
    const events=Array.isArray(event.mouth_cues)?event.mouth_cues.filter(e=>
      Number.isFinite(e.time)&&e.time>=0&&e.time<audio.duration&&VISEMES.has(e.viseme)).map(e=>({
        time:e.time,viseme:e.viseme,amount:Math.max(0,Math.min(1,Number(e.amount)||0))
      })).sort((a,b)=>a.time-b.time):[];
    if(!events.length){
      // Voice conversion has no character timings. Its fallback follows PCM energy.
      const step=Math.max(1,Math.round(event.sample_rate*.02));
      for(let i=0;i<samples.length;i+=step){
        let power=0;const count=Math.min(step,samples.length-i);
        for(let j=i;j<i+count;j++)power+=samples[j]*samples[j];
        const rms=Math.sqrt(power/count);
        events.push({time:i/event.sample_rate,viseme:rms<.006?'rest':'AA',amount:rms<.006?0:Math.min(.9,.2+rms*6)});
      }
    }
    const captions=Array.isArray(event.caption_cues)?event.caption_cues.filter(c=>
      Number.isFinite(c.time)&&c.time>=0&&c.time<audio.duration&&typeof c.text==='string').map(c=>({time:c.time,text:c.text})).sort((a,b)=>a.time-b.time):[];
    this.segments.push({start,end:this.end,events,event,captions,caption_source:event.caption_source||'unavailable',alignment_source:event.alignment_source||'audio_energy'});
    this.sources.add(source);
    source.onended=()=>{this.sources.delete(source);try{source.disconnect();}catch{}};
    source.start(start);
    if(!this.speechAttached){
      this.speechAttached=true;this.origin=start;
      this.face.startSpeech({events:[],duration:Infinity,clock:()=>this.outputTime()-this.origin,sample:()=>this.sample()});
    }
    if(this.timer===null)this.timer=setTimeout(()=>this.tick(),0);
  }
}
