// Speech owns the clock. Queued faces cannot run ahead of unheard words.
export class SpeechFaceCues {
  constructor({apply, now=()=>performance.now(), trace=()=>{}, manual=()=>false}={}) {
    this.apply=apply;this.now=now;this.trace=trace;this.manual=manual;
    this.epoch=0;this.turn=null;this.caption='';this.pending=[];this.applied=new Set();this.lastAt=-Infinity;this.visual=null;
  }
  reset(epoch=this.epoch+1) {
    this.epoch=epoch;this.turn=null;this.caption='';this.pending=[];this.applied.clear();this.lastAt=-Infinity;this.visual=null;
  }
  enqueue(cue) {
    if(!cue||cue.epoch!==this.epoch||typeof cue.turn_id!=='string'||!Number.isInteger(cue.cue_index))return false;
    const key=`${cue.epoch}:${cue.turn_id}:${cue.cue_index}`;
    if(this.applied.has(key)||this.pending.some(item=>item.key===key)||this.pending.length>=16)return false;
    this.pending.push({...cue,key,character_offset:Math.max(0,Number(cue.character_offset??cue.char_offset)||0)});
    this.pending.sort((a,b)=>a.cue_index-b.cue_index);return true;
  }
  progress({epoch,turn_id,caption='',active=false}={}) {
    if(epoch!==this.epoch||!active||typeof turn_id!=='string')return;
    if(this.turn!==turn_id){this.turn=turn_id;this.caption='';}
    this.caption=String(caption).replace(/\s+/g,' ').trim();
    const cue=this.pending.find(item=>item.turn_id===turn_id&&item.character_offset<=this.caption.length);
    if(!cue)return;
    // Leave time for a visible transition, but prefer the matching speech cue
    // over a long default face duration that would make the sequence fall behind.
    if(this.now()-this.lastAt<450)return;
    this.pending=this.pending.filter(item=>item!==cue);this.applied.add(cue.key);
    if(this.manual()){this.trace({type:'face_cue_result',status:'manual_preview',cue_index:cue.cue_index,turn_id,epoch,character_offset:cue.character_offset});return;}
    this.lastAt=this.now();this.apply(cue);
    this.trace({type:'face_cue_result',status:'played',cue_index:cue.cue_index,turn_id,epoch,character_offset:cue.character_offset});
  }
  startVisual(packet){
    if(packet?.visual_only!==true||packet.epoch!==this.epoch||typeof packet.turn_id!=='string'||!Array.isArray(packet.cues)||packet.cues.length>16)return false;
    this.pending=this.pending.filter(cue=>cue.turn_id!==packet.turn_id);
    this.visual={turn_id:packet.turn_id,cues:packet.cues.filter(cue=>cue.epoch===this.epoch&&cue.turn_id===packet.turn_id),index:0,nextAt:this.now()};
    this.advanceVisual();return true;
  }
  advanceVisual(){
    const sequence=this.visual;if(!sequence||this.now()<sequence.nextAt)return;
    if(this.manual()||sequence.index>=sequence.cues.length){this.visual=null;return;}
    const cue=sequence.cues[sequence.index++];
    this.apply(cue);this.trace({type:'face_cue_result',status:'played',visual_only:true,epoch:this.epoch,turn_id:sequence.turn_id,cue_index:cue.cue_index,character_offset:cue.character_offset||0});
    sequence.nextAt=this.now()+Math.max(450,Math.min(6000,Number(cue.duration_ms)||2200));
  }
  snapshot(){return {epoch:this.epoch,turn_id:this.turn,pending:this.pending.length,applied:this.applied.size,visual_only:!!this.visual};}
}
