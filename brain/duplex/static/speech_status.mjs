// Reply ownership survives PCM gaps; the mouth still follows audible samples.
export class SpeechStatus {
  constructor({now=()=>performance.now(),graceMs=450}={}) {
    this.now=now;this.graceMs=graceMs;this.reset(0);
  }
  reset(epoch=0) {
    this.epoch=epoch;this.streams=new Map();this.started=false;this.lastAudible=-Infinity;
  }
  audio(event) {
    if(!Number.isInteger(event.epoch)||event.epoch<this.epoch||!Number.isInteger(event.seq))return;
    if(event.epoch>this.epoch)this.reset(event.epoch);
    if(!this.streams.has(event.seq))this.streams.set(event.seq,{complete:false});
  }
  complete(event) {
    if(event.epoch!==this.epoch)return;
    const stream=this.streams.get(event.seq);if(stream)stream.complete=true;
  }
  sample({audible=false,queued=false,paused=false,fallback='listening'}={}) {
    const at=this.now();
    if(audible){this.started=true;this.lastAudible=at;}
    const unfinished=[...this.streams.values()].some(stream=>!stream.complete);
    const busy=queued||unfinished;
    if(!busy){this.streams.clear();this.started=false;}
    if(busy&&paused)return {busy:true,state:'available',label:'Audio paused'};
    if(audible||(busy&&this.started&&at-this.lastAudible<this.graceMs))
      return {busy:true,state:'speaking',label:'Speaking, still listening'};
    if(busy)return {busy:true,state:'available',label:this.started?'Waiting for speech':'Preparing voice'};
    return {busy:false,state:fallback,label:{listening:'Listening',thinking:'Thinking',checking:'Checking',available:'Available',connecting:'Connecting'}[fallback]||'Connecting'};
  }
}
