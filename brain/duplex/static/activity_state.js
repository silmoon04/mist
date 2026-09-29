// A brief model wait does not need a full activity entrance and exit.
export class ActivityState {
  constructor({now=()=>performance.now(),delayMs=650}={}) {
    this.now=now;this.delayMs=delayMs;this.candidate=null;this.since=0;
  }
  update(state) {
    const at=this.now();
    if(state!==this.candidate){this.candidate=state;this.since=at;}
    return (state==='thinking'||state==='checking')&&at-this.since<this.delayMs?'available':state;
  }
}
