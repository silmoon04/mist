class Capture extends AudioWorkletProcessor{
  constructor(){super();this.samples=[];this.phase=0;this.rms=0;this.n=0;this.sum=0;this.count=0;}
  process(inputs){
    const channel=inputs[0]?.[0];if(!channel)return true;
    for(const sample of channel){
      this.rms+=sample*sample;this.n++;this.phase+=16000;this.sum+=sample;this.count++;
      if(this.phase>=sampleRate){this.phase-=sampleRate;const averaged=this.sum/this.count;this.sum=0;this.count=0;this.samples.push(Math.max(-32768,Math.min(32767,Math.round(averaged*32767))));}
      if(this.samples.length===320){
        const pcm=Int16Array.from(this.samples);this.port.postMessage({pcm:pcm.buffer,rms:Math.sqrt(this.rms/this.n)},[pcm.buffer]);this.samples=[];this.rms=0;this.n=0;
      }
    }
    return true;
  }
}
registerProcessor('mist-capture',Capture);
