"""Bounded in-memory diagnostics. Never retain PCM or authentication fields."""
from collections import deque
import json,logging,time,uuid,math

class DebugJournal(logging.Handler):
    def __init__(self):
        super().__init__();self.rows=deque(maxlen=2000);self.sequence=0;self.started=time.perf_counter();self.audio={};self.trace_id=uuid.uuid4().hex
    def clean(self,value,depth=0):
        if depth>7:return '[depth limit]'
        if isinstance(value,dict):return {str(k):self.clean(v,depth+1) for k,v in list(value.items())[:60] if str(k).lower() not in ('pcm','audio','xi_api_key','xi-api-key','authorization','cookie','token','api_key')}
        if isinstance(value,(list,tuple)):return [self.clean(v,depth+1) for v in value[:60]]
        if isinstance(value,str):return value[:4000]
        if isinstance(value,float) and not math.isfinite(value):return None
        if value is None or isinstance(value,(int,float,bool)):return value
        return str(value)[:200]
    def record(self,event,source='server'):
        if event.get('type')=='audio':
            key=(event.get('epoch'),event.get('seq'));now=time.monotonic()
            previous=self.audio.get(key,0)
            if now-previous<.5:return
            self.audio[key]=now
            if len(self.audio)>100:self.audio.pop(next(iter(self.audio)))
            event={k:v for k,v in event.items() if k!='pcm'}|{'type':'audio_delivery','encoded_bytes':len(event.get('pcm',''))}
        self.sequence+=1
        self.rows.append({'id':self.sequence,'at':time.time(),'elapsed_ms':round((time.perf_counter()-self.started)*1000,2),'source':source,'event':self.clean(event)})
    def emit(self,record):
        try:self.record(json.loads(record.getMessage()),'provider')
        except (ValueError,TypeError):pass
    def since(self,cursor):
        events=[r for r in self.rows if r['id']>cursor][:400]
        return {'events':events,'cursor':events[-1]['id'] if events else self.sequence,'head':self.sequence,'oldest':self.rows[0]['id'] if self.rows else 0,'capacity':2000,'trace_id':self.trace_id}
