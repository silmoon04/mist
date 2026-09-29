const asArray=value=>Array.isArray(value)?value:[];
const plain=value=>typeof value==='string'?value.trim():'';

export function latestSourcedNotes(summaries) {
  const latest=asArray(summaries).filter(item=>item&&typeof item==='object')
    .sort((a,b)=>(Number(a.version)||0)-(Number(b.version)||0)).at(-1);
  if(!latest)return {version:null,notes:[],total:0};
  let body=latest.text;
  if(typeof body==='string'){try{body=JSON.parse(body);}catch{return {version:latest.version??null,notes:body.trim()?[{text:body.trim(),source:'',kind:'',quote:''}]:[],total:body.trim()?1:0};}}
  const entries=Array.isArray(body)?body:asArray(body?.items).length?body.items:
    asArray(body?.sourced_notes).length?body.sourced_notes:
    asArray(body?.notes).length?body.notes:asArray(body?.facts).length?body.facts:
    asArray(body?.memories).length?body.memories:[];
  const notes=entries.map(item=>{
    if(typeof item==='string')return {text:item.trim(),source:'',kind:'',quote:''};
    if(!item||typeof item!=='object')return {text:'',source:'',kind:'',quote:''};
    const text=plain(item.text)||plain(item.note)||plain(item.fact)||plain(item.content)||plain(item.value);
    const sources=item.source_id??item.source_turn_ids??item.turn_ids??item.sources??item.source??item.turn_id;
    const source=Array.isArray(sources)?sources.map(String).join(', '):sources==null?'':String(sources);
    return {text,source,kind:plain(item.kind),quote:plain(item.quote)};
  }).filter(item=>item.text);
  if(!notes.length){const text=plain(body?.summary)||plain(body?.text);if(text)notes.push({text,source:'',kind:'',quote:''});}
  return {version:latest.version??null,notes:notes.slice(0,3),total:notes.length};
}

export function availableAudio(audio) {
  return asArray(audio).filter(item=>item&&['mic','assistant_generated'].includes(item.stream)&&
    Number(item.chunk_count)>0&&Number(item.byte_count)>0);
}
