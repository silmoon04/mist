"""Phone UI and paired full-duplex native-voice sessions. No actuator transport."""
import argparse
import asyncio
import base64
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
from pathlib import Path
import secrets
import ssl
import sys
import time
import uuid
from aiohttp import web,ClientSession,ClientTimeout

BRAIN=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BRAIN));sys.path.insert(0,str(BRAIN/'harness'))
from pi_client import load_env
from duplex.runtime import RobotRuntime,EXPRESSIONS
from duplex.native import NativeVoice
from duplex.conversion import VoiceMask,VOICE
from duplex.tts import StreamingTTS
from duplex.background import BackgroundBrain
from duplex.debugging import DebugJournal
from duplex.sensors import consume as consume_sensors

class Conversation:
    def __init__(self,app,ws):
        self.app=app;self.ws=ws;self.runtime=app['runtime'];self.voice=None;self.mask=None;self.alive=True;self.warm_task=None
        self.sent_at=time.monotonic();self.blocked=False;self.old_turn=None;self.assistant_turn=None;self.assistant_done=True;self.events=[];self.last_context=0;self.user_since_interrupt=False;self.user_start_ms=0;self.user_partial=''
        self.tts=app.get('speech_backend')=='streaming-tts'
        self.tts_prepared=False
        self.user_revision=0;self.user_speaking=False;self.playback_busy=False;self.result_task=None
        self.expression_revision=0
        self.user_request_text=''
        self.user_turn_id=None;self.user_turn_open=False;self.pending_user_prefix=''
        self.brain=BackgroundBrain(self.runtime,self.runtime.run_dir/'background',self.emit,lambda:self.user_revision)
    async def emit(self,event):
        if not self.alive or self.ws.closed:return
        remote=self.app.get('remote_access')
        if remote:event=remote.scrub(event)
        if self.app.get('journal'):self.app['journal'].record(event)
        if event.get('type')=='audio':self.playback_busy=True
        if event.get('type') not in ('audio','listener_cue'):self.events.append({**event,'at':time.monotonic()})
        self.events=self.events[-250:]
        await self.ws.send_json(event)
        if event.get('type')=='tool' and event.get('name')=='set_expression':
            result=event.get('result',{})
            if result.get('status')!='display_requested' or event.get('expression_revision')!=self.expression_revision:return
            await self.emit({'type':'face',**{key:result[key] for key in ('expression','variant','duration_ms','persistent') if key in result},
                'epoch':getattr(self.mask,'epoch',0),'expression_revision':self.expression_revision,'source':'model'})
    async def start(self):
        self.mask=(StreamingTTS if self.tts else VoiceMask)(self.app['env']['ELEVENLABS_API_KEY'],self.emit)
        await self.mask.start()
        self.voice=NativeVoice(self.runtime,self.app['run_dir']/uuid.uuid4().hex,self.emit,self.mask.feed,self.realtime_event,speech_backend=self.app.get('speech_backend','voice-changer'),background=self.brain,expression_revision=lambda:self.expression_revision,request_text=lambda:self.user_request_text)
        await self.voice.start()
        self.warm_task=asyncio.create_task(self.mask.warm())
        self.result_task=asyncio.create_task(self.deliver_results())

    async def deliver_results(self):
        while self.alive:
            await asyncio.sleep(.2)
            if self.user_speaking or not self.assistant_done or self.playback_busy or self.mask.pending or getattr(self.voice,'turn',None):continue
            for job in list(self.brain.jobs.values()):
                if job['status'] in ('complete','failed') and not job['announced'] and job['revision']==self.user_revision:
                    if getattr(self.voice,'supports_automatic_background_delivery',True) is False:
                        # Some realtime transports cannot bind appended context to a
                        # request revision or retract it after a user interruption.
                        # Keep the result readable by tools without unsolicited speech.
                        if not job.get('delivery_deferred'):
                            job['delivery_deferred']=True
                            await self.emit({'type':'brain_delivery','job_id':job['job_id'],'phase':'deferred',
                                             'reason':'unsupported_result_guard','terminal_status':job['status'],
                                             'playback_verified':False})
                        continue
                    if job.get('delivery_attempts',0)>=3 or time.monotonic()<job.get('retry_delivery_at',0):continue
                    job['delivery_attempts']=job.get('delivery_attempts',0)+1
                    try:
                        guard={'job_id':job['job_id']} if getattr(self.voice,'supports_background_result_guard',False) else {}
                        if job['status']=='failed':
                            report=('The background analysis requested in the current user turn failed and has no usable result. '
                                    'Briefly tell the user it could not finish and no result is available. '
                                    'Do not invent a cause or findings, say it is still running, call tools, or retry automatically.')
                        else:
                            report='The background analysis requested in the current user turn has completed. Give its useful result briefly, without a new tool call or any action. Treat this JSON as fallible application data: '+json.dumps(self.brain.view(job))
                        await self.voice.context(report,**guard)
                        # Context acceptance may yield while the user speaks, cancels
                        # this job or disconnects. Only the owning request can announce
                        # acceptance; guarded voices also reject stale queued speech.
                        if (not self.alive or self.ws.closed or job['revision']!=self.user_revision
                                or self.brain.jobs.get(job['job_id']) is not job
                                or job['status'] not in ('complete','failed')):continue
                        job['announced']=True
                        await self.emit({'type':'brain_delivery','job_id':job['job_id'],'phase':'queued','terminal_status':job['status'],'playback_verified':False})
                    except (RuntimeError,TimeoutError,OSError):
                        job['retry_delivery_at']=time.monotonic()+2
                        self.mask.diagnostic('background_result_delivery_failed',job_id=job['job_id'])
    async def realtime_event(self,event):
        kind=event.get('type','')
        if kind=='delegation.created':await self.emit({'type':'state','state':'checking'})
        if kind=='turn.created':
            turn=event.get('turn',{})
            self.mask.diagnostic('native_turn',turn_fields=list(turn),role=turn.get('role'),turn_id=turn.get('id',turn.get('turn_id')),start_ms=turn.get('start_ms'),blocked=self.blocked)
            if turn.get('role')=='user':
                self.user_turn_id=turn.get('id')
                self.user_request_text=self.pending_user_prefix or turn.get('transcript','')
                self.user_partial=self.user_request_text
                self.pending_user_prefix='';self.user_turn_open=True
                self.expression_revision+=1
                self.user_revision+=1;self.user_speaking=True
                if not self.blocked and self.assistant_turn is not None:await self.barge_in('native_user_turn')
                self.user_since_interrupt=True;self.user_start_ms=turn.get('start_ms',0)
                await self.emit({'type':'state','state':'listening'})
            else:
                self.assistant_turn=turn.get('id')
                self.assistant_done=False
                if not self.blocked or (self.user_since_interrupt and self.assistant_turn!=self.old_turn and turn.get('start_ms',0)>=self.user_start_ms):
                    self.blocked=False;self.mask.muted=False
                    if turn.get('transcript'):
                        await self.emit({'type':'transcript_delta','role':'assistant','text':turn['transcript']})
                    if self.tts:
                        if self.tts_prepared:await self.mask.release()
                        elif hasattr(self,'delivery'):await self.mask.begin(self.assistant_turn,delivery=self.delivery)
                        else:await self.mask.begin(self.assistant_turn)
                        self.tts_prepared=True
        if kind=='turn.delta' and event.get('turn_id')==self.assistant_turn and not self.blocked:
            await self.emit({'type':'transcript_delta','role':'assistant','text':event.get('delta','')})
        if kind in ('input_transcript.added','output_transcript.added'):
            role='user' if kind.startswith('input') else 'assistant'
            delta=event.get('item',{}).get('text','')
            # Predictive output has no turn ID. Only tagged turn events update
            # visible assistant text; predictive text still prewarms speech.
            if role=='user':await self.emit({'type':'transcript_delta','role':role,'text':delta})
            if role=='assistant' and self.tts:
                # V3 sends predictive transcript fragments before turn.created.
                # Prepare their audio now, but wait for the turn boundary to play it.
                if not self.tts_prepared and self.assistant_done and (not self.blocked or self.user_since_interrupt):
                    self.mask.muted=False
                    await self.mask.begin(None,audible=False)
                    self.tts_prepared=True
                if self.tts_prepared:await self.mask.text(delta)
            if role=='user':
                if not self.user_turn_open:
                    self.pending_user_prefix=(self.pending_user_prefix+delta)[-1000:]
                    self.user_partial=self.pending_user_prefix
                else:self.user_partial=(self.user_partial+delta)[-1000:]
                self.user_request_text=self.user_partial
            if role=='user':self.user_speaking=True
            if role=='user' and re.fullmatch(r'\s*(stop|freeze)( moving)?[.!]?\s*',self.user_partial,re.I) and not self.runtime.estop:
                result=self.runtime.call('stop_robot',{})
                await self.emit({'type':'tool','name':'stop_robot','result':result,'source':'early_stop_transcript'})
        if kind=='turn.done':
            turn=event.get('turn',{});role=turn.get('role');text=turn.get('transcript','')
            if role=='assistant':
                if turn.get('id')!=self.assistant_turn:return
                self.assistant_done=True
                if self.blocked:return
            await self.emit({'type':'transcript_done','role':role,'text':text})
            if role=='assistant':
                if self.tts and not self.blocked:
                    await self.mask.finish(text)
                    self.tts_prepared=False
            if role=='user':
                if self.user_turn_id is not None and turn.get('id') not in (None,self.user_turn_id):return
                self.user_turn_open=False
                self.user_speaking=False
                self.user_request_text=text or self.user_partial
                self.user_partial=''
                self.pending_user_prefix=''
                if text.strip().lower().strip(' .!?') in ('stop','freeze','stop moving') and not self.runtime.estop:
                    receipt=self.runtime.call('stop_robot',{});await self.emit({'type':'tool','name':'stop_robot','result':receipt})
                await self.emit({'type':'state','state':'thinking'})
        if kind=='error':await self.emit({'type':'error','message':'Native voice reported an error'})
    async def barge_in(self,reason='explicit_stop'):
        self.expression_revision+=1
        self.mask.diagnostic('playback_interrupt',reason=reason,epoch=self.mask.epoch)
        self.old_turn=self.assistant_turn;self.blocked=True;self.mask.muted=True;self.user_since_interrupt=False
        self.tts_prepared=False
        await self.mask.interrupt();await self.voice.interrupt()
    async def handle(self,packet):
        kind=packet.get('type')
        if kind=='mic':
            raw=base64.b64decode(packet.get('pcm',''),validate=True)
            try:
                self.accept_microphone(raw, packet)
            except ValueError as error:
                if 'uplink is behind' not in str(error):raise
                await self.emit({'type':'error','fatal':True,'reconnect_required':True,
                                 'message':'Microphone input fell behind. Recording was retained on the host; reconnect before continuing.'})
                await self.ws.close()
        elif kind=='listener_ack_config':
            listener=getattr(self.voice,'listener',None)
            if listener:await listener.configure(packet.get('enabled'))
        elif kind=='listener_cue_state':
            if packet.get('phase') not in ('started','ended','cancelled','skipped'):
                raise ValueError('Unsupported listener cue state')
            listener=getattr(self.voice,'listener',None)
            if listener:await listener.receipt(packet)
        elif kind=='barge_in':await self.barge_in()
        elif kind=='playback_state':
            if type(packet.get('playing')) is not bool:raise ValueError('Playback state must be boolean')
            self.playback_busy=packet['playing']
        elif kind=='debug_client':
            event=packet.get('event',{})
            if not isinstance(event,dict) or event.get('type') not in ('playback_started','playback_idle','playback_configuration','expression','microphone_stats','audio_scheduled','caption_progress'):raise ValueError('Unsupported debug event')
            if self.app.get('journal'):self.app['journal'].record(event,'browser')
        elif kind=='cancel_background':
            receipt=await self.brain.handle('cancel_background_task',{'job_id':packet.get('job_id')})
            cancel=getattr(self.voice,'cancel_background_delivery',None)
            if cancel:await cancel(receipt.get('job_id'))
        elif kind=='start_background':
            self.user_revision+=1
            result=await self.brain.handle('start_background_task',{'question':packet.get('question'),
                                           'task_type':packet.get('task_type','analysis')})
            await self.emit({'type':'brain_job',**result})
        elif kind=='sensors':
            snapshot=self.runtime.ingest(packet.get('packet'))
            await self.emit({'type':'sensors','snapshot':snapshot})
            # The model reads sensors through its tool. Appending live readings
            # to V3 conversation history can trigger unsolicited spoken replies.
        elif kind=='stop_robot':
            await self.barge_in();await self.emit({'type':'tool','name':'stop_robot','result':self.runtime.call('stop_robot',{})})
        elif kind=='resume_preview':
            self.runtime.estop=False;await self.emit({'type':'sensors','snapshot':self.runtime.snapshot()})
        elif kind=='expression':
            result=self.runtime.call('set_expression',{key:packet[key] for key in ('expression','variant','duration_ms') if key in packet})
            await self.emit({'type':'face',**{key:result[key] for key in ('expression','variant','duration_ms','persistent')},
                'epoch':getattr(self.mask,'epoch',0),'expression_revision':self.expression_revision,'source':'manual'})
        elif kind=='fixture' and self.app['enable_fixtures']:
            text=packet.get('text','')
            if not isinstance(text,str) or not 1<=len(text)<=220:raise ValueError('Fixture text is too long')
            async with ClientSession(timeout=ClientTimeout(total=15)) as http:
                async with http.post('https://api.deepgram.com/v1/speak?model=aura-2-thalia-en&encoding=linear16&sample_rate=16000&container=none',
                    headers={'Authorization':'Token '+self.app['env']['DEEPGRAM_API_KEY']},json={'text':text}) as response:
                    if response.status!=200:raise ValueError('Synthetic input provider failed')
                    pcm=await response.read()
            # Feed the fixture at microphone speed rather than overflowing the bounded uplink.
            started=time.monotonic()
            for i in range(0,len(pcm),640):
                if not self.alive:break
                await asyncio.sleep(max(0,started+i/32000-time.monotonic()))
                self.accept_microphone(pcm[i:i+640], {'source':'synthetic_fixture'})
            await self.emit({'type':'fixture_finished','text':text})
        elif kind=='ping':await self.emit({'type':'pong'})
        else:raise ValueError('Unsupported session event')

    def accept_microphone(self, raw, metadata=None):
        listener=getattr(self.voice,'listener',None)
        if listener:listener.note_pcm(raw)
        self.voice.track.append(raw)
    async def close(self):
        if not self.alive:return
        self.alive=False
        if self.result_task:
            self.result_task.cancel();await asyncio.gather(self.result_task,return_exceptions=True)
        await self.brain.close()
        if self.warm_task:
            self.warm_task.cancel()
            await asyncio.gather(self.warm_task,return_exceptions=True)
        if self.voice:await self.voice.close()
        if self.mask:await self.mask.close()

def create_app(args):
    app=web.Application(client_max_size=64*1024)
    app['journal']=DebugJournal()
    env={**load_env(),**os.environ}
    app['env']=env;app['run_dir']=Path(args.run_dir).resolve();app['runtime']=RobotRuntime(app['run_dir'])
    app['pair_code']=args.pair_code or secrets.token_urlsafe(12);app['cookie']=secrets.token_urlsafe(32)
    app['enable_fixtures']=args.enable_fixtures;app['sessions']={'owner':None};app['tls']=bool(args.cert)
    app['speech_backend']=getattr(args,'speech_backend','streaming-tts')
    def paired(request):
        remote=app.get('remote_access')
        return remote.authenticated(request) if remote else secrets.compare_digest(request.cookies.get('mist_session',''),app['cookie'])
    def same_origin(request):
        origin=request.headers.get('Origin')
        return origin==app.get('public_origin',f"{'https' if app['tls'] else 'http'}://{request.host}")
    async def index(request):return web.FileResponse(BRAIN/'duplex/static/index.html',headers={'Cache-Control':'no-store'})
    async def pair(request):
        if not same_origin(request):raise web.HTTPForbidden()
        data=await request.json()
        if not secrets.compare_digest(str(data.get('code','')),app['pair_code']):
            await asyncio.sleep(.5);raise web.HTTPForbidden(text='Pairing code not accepted')
        response=web.json_response({'paired':True});response.set_cookie('mist_session',app['cookie'],httponly=True,samesite='Strict',secure=app['tls']);return response
    async def config(request):
        return web.json_response({'paired':paired(request),'remote_mode':bool(app.get('remote_access')),'voice_id':VOICE,'voice_name':'MIST (refined)',
            'voice_backend':'Codex native V3 + ElevenLabs Flash streaming TTS' if app['speech_backend']=='streaming-tts' else 'Codex native V3 + ElevenLabs Voice Changer',
            'speech_backend':app['speech_backend'],
            'backing_model':os.environ.get('MIST_BRAIN_MODEL','gpt-6-luna'),
            'background_model':os.environ.get('MIST_BACKGROUND_MODEL','gpt-6-luna'),
            'reasoning':'low','service_tier':'default',
            'fixtures_enabled':app['enable_fixtures'],'expressions':list(EXPRESSIONS),
            'hardware_connected':False,'microphone_requires_secure_context':True})
    async def websocket(request):
        if not paired(request) or not same_origin(request):raise web.HTTPForbidden()
        if app['sessions']['owner'] is not None:raise web.HTTPConflict(text='A voice session is already active')
        ws=web.WebSocketResponse(max_msg_size=16384,heartbeat=20);await ws.prepare(request)
        session=Conversation(app,ws);app['sessions']['owner']=session
        for kind in ('phone_motion','phone_orientation','phone_battery','robot_telemetry'):app['runtime'].sequence.pop(kind,None)
        try:
            await session.emit({'type':'state','state':'connecting'});await session.start()
            async for message in ws:
                if message.type==web.WSMsgType.TEXT:
                    try:
                        data=json.loads(message.data)
                        if not isinstance(data,dict):raise ValueError('Session event must be an object')
                        await session.handle(data)
                    except (ValueError,TypeError,KeyError) as exc:await session.emit({'type':'error','message':str(exc)[:180]})
        except Exception as exc:
            await session.emit({'type':'error','message':str(exc)[:180] if isinstance(exc,(RuntimeError,TimeoutError)) else 'Session failed. Check the local runtime.'})
        finally:
            await session.close();app['sessions']['owner']=None
            if not ws.closed:await ws.close()
        return ws
    app.router.add_get('/',index);app.router.add_post('/pair',pair);app.router.add_get('/config',config);app.router.add_get('/voice',websocket)
    async def debug_page(r):return web.FileResponse(BRAIN/'duplex/static/debug.html',headers={'Cache-Control':'no-store'})
    async def expressions_page(r):return web.FileResponse(BRAIN/'duplex/static/expressions.html',headers={'Cache-Control':'no-store'})
    async def debug_events(r):
        if not paired(r):raise web.HTTPForbidden(text='Pair this browser in MIST first.')
        try:cursor=max(0,int(r.query.get('after','0')))
        except ValueError:raise web.HTTPBadRequest()
        if r.query.get('trace') and r.query['trace']!=app['journal'].trace_id:cursor=0
        return web.json_response(app['journal'].since(cursor),headers={'Cache-Control':'no-store'})
    app.router.add_get('/debug',debug_page);app.router.add_get('/expressions',expressions_page);app.router.add_get('/debug/events',debug_events)
    async def outfit_font(r):
        return web.FileResponse(BRAIN/'duplex/static/fonts/Outfit.woff2',
                                headers={'Content-Type':'font/woff2'})
    app.router.add_get('/duplex/fonts/Outfit.woff2',outfit_font)
    app.router.add_static('/duplex/',BRAIN/'duplex/static',show_index=False)
    async def face_runtime(r):return web.FileResponse(BRAIN/'ui/static/face_runtime.js')
    async def drawn_runtime(r):return web.FileResponse(BRAIN/'ui/static/drawn_face_renderer.js')
    async def face_data(r):return web.FileResponse(BRAIN/'face_assets/app/app_data.js')
    async def face_map(r):return web.FileResponse(BRAIN/'duplex/face_map.json')
    app.router.add_get('/face-runtime.js',face_runtime);app.router.add_get('/face-data.js',face_data);app.router.add_get('/face-map.json',face_map)
    app.router.add_get('/drawn-face-runtime.js',drawn_runtime)
    app.router.add_static('/drawn/',BRAIN/'art_direction/artist_studio_20260916/reuse/drawn',show_index=False)
    app.router.add_static('/studio/',BRAIN/'art_direction/artist_studio_20260916/reuse',show_index=False)
    async def sensors_lifecycle(app):
        diagnostic_logger=logging.getLogger('mist.voice');diagnostic_logger.addHandler(app['journal'])
        task=None
        if getattr(args,'sensor_url',None):
            async def emit(event):
                owner=app['sessions']['owner']
                if owner:await owner.emit(event)
            task=asyncio.create_task(consume_sensors(app['runtime'],args.sensor_url,emit))
        yield
        diagnostic_logger.removeHandler(app['journal'])
        if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
    app.cleanup_ctx.append(sensors_lifecycle)
    async def cleanup(app):
        if app['sessions']['owner']:await app['sessions']['owner'].close()
    app.on_shutdown.append(cleanup)
    return app

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8790)
    p.add_argument('--run-dir',default=str(BRAIN/'results/duplex-current'));p.add_argument('--pair-code');p.add_argument('--cert');p.add_argument('--key');p.add_argument('--enable-fixtures',action='store_true')
    p.add_argument('--sensor-url',help='Explicit SensorServer WebSocket URL; omitted means no phone connection attempt')
    p.add_argument('--speech-backend',choices=['streaming-tts','voice-changer'],default='streaming-tts')
    args=p.parse_args()
    if args.host not in ('localhost','127.0.0.1','::1') and not (args.cert and args.key):p.error('LAN serving requires a trusted TLS certificate and key. Use adb reverse with loopback for USB testing.')
    app=create_app(args);context=None
    logger=logging.getLogger('mist.voice');logger.setLevel(logging.INFO);logger.propagate=False
    handler=RotatingFileHandler(app['run_dir']/'voice_diagnostics.jsonl',maxBytes=1_000_000,backupCount=2,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'));logger.addHandler(handler)
    if args.cert:context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(args.cert,args.key)
    if not app['env'].get('ELEVENLABS_API_KEY'):p.error('ELEVENLABS_API_KEY is missing from the repository environment')
    print('MIST pairing code:',app['pair_code'],flush=True)
    web.run_app(app,host=args.host,port=args.port,ssl_context=context)

if __name__=='__main__':main()
