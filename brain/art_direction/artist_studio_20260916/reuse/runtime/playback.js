const REST = Object.freeze({viseme:'rest',amount:0,active:false,source:'silence'});
const VISEMES = new Set(['rest','AA','EH','EE','OH','OO','MBP','FV','LNT','TH','S']);

export class Playback {
  constructor(context,face,onLag=()=>{},onIdle=()=>{},onStart=()=>{}) {
    this.context=context;this.face=face;this.onLag=onLag;this.onIdle=onIdle;this.onStart=onStart;
    this.epoch=0;this.end=0;this.sources=new Set();this.segments=[];this.timer=null;
    this.startedKey=null;this.origin=0;this.speechAttached=false;this.clockSource='context';
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
    if(this.context.state==='suspended' || this.context.state==='interrupted')return {...REST,time:Math.max(0,at-this.origin)};
    const segment=this.segments.find(s=>at>=s.start && at<s.end);
    if(!segment)return {...REST,time:Math.max(0,at-this.origin)};
    const t=at-segment.start;
    let cue=REST;
    for(const e of segment.events){if(e.time>t)break;cue=e;}
    return {...cue,time:Math.max(0,at-this.origin),active:true,source:segment.alignment_source,
      epoch:this.epoch,sequence:segment.event.seq,audioTime:at};
  }
  reset(epoch=this.epoch+1){
    this.epoch=epoch;
    for(const source of this.sources){source.onended=null;try{source.stop();}catch{}try{source.disconnect();}catch{}}
    this.sources.clear();this.segments=[];
    if(this.timer!==null)clearTimeout(this.timer);
    this.timer=null;this.end=this.context.currentTime;this.startedKey=null;this.speechAttached=false;
    this.face.stopSpeech();
  }
  tick(){
    this.timer=null;
    const at=this.outputTime();
    if(this.context.state!=='suspended' && this.context.state!=='interrupted'){
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
    const bytes=Uint8Array.from(binary,c=>c.charCodeAt(0)),view=new DataView(bytes.buffer);
    if(!bytes.length)return;
    const now=this.context.currentTime;
    if(this.end-now>30){this.reset(event.epoch);this.onLag();return;}
    const audio=this.context.createBuffer(1,bytes.length/2,event.sample_rate),samples=audio.getChannelData(0);
    for(let i=0;i<samples.length;i++)samples[i]=view.getInt16(i*2,true)/32768;
    const source=this.context.createBufferSource();source.buffer=audio;source.connect(this.context.destination);
    const start=this.end>now?this.end:now+.2;this.end=start+audio.duration;
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
    this.segments.push({start,end:this.end,events,event,alignment_source:event.alignment_source||'audio_energy'});
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
