"""Optional SensorServer adapter. Android monotonic times are not UTC."""
import asyncio
import json
import math
import time
from urllib.parse import urlsplit,urlencode
from aiohttp import ClientSession,ClientTimeout,WSMsgType

TYPES={'android.sensor.accelerometer':'phone_accelerometer','android.sensor.gyroscope':'phone_gyroscope',
       'android.sensor.magnetic_field':'phone_magnetometer'}

class SensorClock:
    def __init__(self):self.anchor=None;self.last={};self.seq=0
    def packet(self,event,now=None):
        now=time.time() if now is None else now
        name=event.get('type');values=event.get('values');stamp=event.get('timestamp')
        if name not in TYPES or not isinstance(values,list) or len(values)!=3:raise ValueError('Invalid SensorServer vector')
        if type(stamp) not in (float,int) or not math.isfinite(stamp) or stamp<0:raise ValueError('Invalid monotonic timestamp')
        if stamp<=self.last.get(name,-1):raise ValueError('Out-of-order SensorServer timestamp')
        if self.anchor is None:self.anchor=(stamp,now)
        # Android timestamps are nanoseconds since boot. Only relative freshness
        # after this connection can be estimated; the first sample's age is unknown.
        captured=self.anchor[1]+(stamp-self.anchor[0])/1e9
        if not now-5<=captured<=now+2:raise ValueError('SensorServer timestamp drift or stale sample')
        self.last[name]=stamp;self.seq+=1
        return {'kind':TYPES[name],'seq':self.seq,'captured_at':captured,'values':dict(zip(('x','y','z'),values))}

async def consume(runtime,url,emit_status):
    parsed=urlsplit(url)
    if parsed.scheme not in ('ws','wss') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('SensorServer URL must be a credential-free ws/wss URL')
    delay=1
    while True:
        clock=SensorClock()
        try:
            async with ClientSession(timeout=ClientTimeout(total=None,sock_connect=5)) as http:
                async with http.ws_connect(url,heartbeat=15,max_msg_size=4096) as ws:
                    for kind in TYPES.values():runtime.sequence.pop(kind,None)
                    await emit_status({'type':'sensor_connection','connected':True,'source':'SensorServer','timestamp_basis':'relative to first arrival; first sample age unknown'})
                    delay=1
                    async for msg in ws:
                        if msg.type!=WSMsgType.TEXT:continue
                        try:
                            packet=clock.packet(json.loads(msg.data));runtime.ingest(packet)
                            runtime.samples[packet['kind']]['source']='SensorServer_report'
                            runtime.samples[packet['kind']]['timestamp_basis']='Android monotonic anchored at first arrival; initial absolute age unknown'
                        except (ValueError,TypeError,KeyError):continue
        except asyncio.CancelledError:raise
        except Exception:await emit_status({'type':'sensor_connection','connected':False,'source':'SensorServer'})
        await asyncio.sleep(delay);delay=min(20,delay*2)
