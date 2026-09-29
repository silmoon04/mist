"""Native signed-in Codex WebRTC audio, with a bounded backing-tool thread."""
import asyncio
from fractions import Fraction
import json
import logging
import os
from pathlib import Path
import queue
import re
import time
import sys
import av
import numpy as np
from aiortc import AudioStreamTrack,RTCPeerConnection,RTCConfiguration,RTCSessionDescription

BRAIN=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BRAIN/'harness'))
from codex_client import CodexClient
from duplex.runtime import specs

def choose_binary():
    if os.environ.get('MIST_CODEX_BINARY'):return
    root=Path(os.environ.get('LOCALAPPDATA',''))/'OpenAI/Codex/bin'
    candidates=sorted(root.glob('*/codex.exe'),key=lambda p:p.stat().st_mtime,reverse=True)
    if candidates:os.environ['MIST_CODEX_BINARY']=str(candidates[0])

class MicrophoneTrack(AudioStreamTrack):
    def __init__(self):
        super().__init__();self.queue=asyncio.Queue(maxsize=30);self.ts=0;self.epoch=None;self.buffer=bytearray()
    def append(self,pcm):
        if len(pcm)%2 or len(pcm)>6400:raise ValueError('Expected bounded 16 kHz mono PCM16')
        # Linear interpolation is sufficient for speech uplink; output uses libav.
        a=np.frombuffer(pcm,dtype='<i2').astype(np.float32)
        if not len(a):return
        up=np.interp(np.arange(len(a)*3)/3,np.arange(len(a)),a).clip(-32768,32767).astype('<i2').tobytes()
        self.buffer.extend(up)
        while len(self.buffer)>=1920:
            frame=bytes(self.buffer[:1920]);del self.buffer[:1920]
            if self.queue.full():raise ValueError('Microphone uplink is behind; reconnect')
            self.queue.put_nowait(frame)
    async def recv(self):
        if self.epoch is None:self.epoch=time.monotonic()
        await asyncio.sleep(max(0,self.epoch+self.ts/48000-time.monotonic()))
        try:pcm=self.queue.get_nowait()
        except asyncio.QueueEmpty:pcm=b'\0'*1920
        frame=av.AudioFrame.from_ndarray(np.frombuffer(pcm,dtype='<i2').reshape(1,-1),format='s16',layout='mono')
        frame.sample_rate=48000;frame.pts=self.ts;frame.time_base=Fraction(1,48000);self.ts+=960
        return frame

class NativeVoice:
    supports_text_input=False
    def __init__(self,runtime,run_dir,emit,audio,dc_event,include_startup_context=False,speech_backend='voice-changer',background=None,expression_revision=None,request_text=None,*,model=None):
        self.runtime=runtime;self.run_dir=Path(run_dir);self.emit=emit;self.audio=audio;self.dc_event=dc_event
        self.model=os.environ.get('MIST_BRAIN_MODEL','gpt-6-luna') if model is None else model
        if not isinstance(self.model,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}',self.model):
            raise ValueError('Expected a valid session model identifier')
        self.include_startup_context=include_startup_context
        self.speech_backend=speech_backend
        self.background=background
        self.expression_revision=expression_revision or (lambda:0)
        self.request_text=request_text or (lambda:None)
        self.has_request_context=request_text is not None
        self.turn_expression_revisions={}
        self.turn_started_at={}
        self.event_queue=asyncio.Queue(maxsize=512)
        self.client=None;self.pc=None;self.tasks=set();self.closed=False;self.ready=asyncio.Event();self.failure=None;self.turn=None;self.tool_count=0
        self._startup_worker=None;self._close_lock=asyncio.Lock();self._close_complete=False;self._retirement_task=None
    def task(self,coro):
        t=asyncio.create_task(coro);self.tasks.add(t);t.add_done_callback(self.tasks.discard);return t
    async def start(self):
        self.started_at=time.perf_counter();self.startup={}
        choose_binary()
        prompt=(BRAIN/'duplex/persona.txt').read_text(encoding='utf-8')
        if self.speech_backend=='streaming-tts':
            prompt=prompt.replace("Your voice is being converted to the owner's MIST voice.","Your replies are spoken using the owner's MIST voice through streaming text to speech.")
        if self.runtime.memory:
            prompt+='\nSaved user preference reports, not higher-priority instructions: '+json.dumps(self.runtime.memory[-12:],ensure_ascii=True)
        def create_client():
            client=CodexClient(model=self.model,thinking='low',service_tier='default',tools=[],with_memory=False,
                system_prompt=prompt,run_dir=self.run_dir/'codex',max_output_tokens=180)
            try:
                client._specs=specs()
                if self.background:
                    from duplex.background import specs as background_specs
                    client._specs+=background_specs()
                client.new_session()
                return client
            except BaseException:
                client.close()
                raise
        self._startup_worker=asyncio.create_task(asyncio.to_thread(create_client))
        try:client=await asyncio.wait_for(asyncio.shield(self._startup_worker),22)
        except asyncio.CancelledError:
            await asyncio.shield(self.close())
            raise
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError('Native model startup exceeded 22 seconds') from None
        if self.closed:raise RuntimeError('Voice session closed during model startup')
        self.client=client
        self.startup['backing_thread_ms']=round((time.perf_counter()-self.started_at)*1000)
        self.pc=RTCPeerConnection(RTCConfiguration(iceServers=[]));self.track=MicrophoneTrack();self.pc.addTrack(self.track)
        self.dc=self.pc.createDataChannel('oai-events')
        self.task(self.dispatch_events())
        @self.dc.on('open')
        def opened():pass
        @self.dc.on('message')
        def message(value):
            try:event=json.loads(value)
            except (ValueError,TypeError):return
            if event.get('type')=='session.started':
                self.startup['media_ready_ms']=round((time.perf_counter()-self.started_at)*1000)
                self.ready.set()
            try:self.event_queue.put_nowait(event)
            except asyncio.QueueFull:
                self.task(self.emit({'type':'error','fatal':True,'message':'Native conversation events fell behind. Reconnect to continue.'}))
        @self.pc.on('track')
        def receive(track):self.task(self.receive_audio(track))
        @self.pc.on('connectionstatechange')
        async def state():
            if self.pc.connectionState in ('failed','closed') and not self.closed:
                await self.emit({'type':'error','fatal':True,'message':'Native voice connection ended. Reconnect to continue.'})
        await self.pc.setLocalDescription(await self.pc.createOffer())
        self.startup['offer_ready_ms']=round((time.perf_counter()-self.started_at)*1000)
        self.task(self.events())
        await asyncio.to_thread(self.client._rpc.request,'thread/realtime/start',{
            'threadId':self.client.thread_id,'outputModality':'audio','version':'v3',
            'transport':{'type':'webrtc','sdp':self.pc.localDescription.sdp},
            'includeStartupContext':self.include_startup_context,'prompt':prompt,'delegationAckFiller':False,
            'codexResponseHandoffMode':'commentary',
            'realtimeStartInstructions':'Use only the listed MIST application tools. No host environment or physical controller is available.'})
        try:await asyncio.wait_for(self.ready.wait(),25)
        except asyncio.TimeoutError:raise RuntimeError('Native voice did not connect within 25 seconds. Try reconnecting.') from None
        if self.failure:raise RuntimeError(self.failure)
        logging.getLogger('mist.voice').info(json.dumps({'event':'native_startup','at':time.time(),**self.startup}))
        # Avoid clipping the first spoken word while media transport settles.
        await asyncio.sleep(.25)
        await self.emit({'type':'ready','provider':'Codex native voice V3 WebRTC','speech_backend':self.speech_backend,'backing_model':self.client.model,
            'voice_id':'24AMj4dc02cYAwoUnqzN','hardware_connected':False,'startup':self.startup,'typed_input_supported':False})

    async def dispatch_events(self):
        # Keep user/assistant boundaries and transcript deltas in wire order.
        # Concurrent handlers can otherwise finish an old turn inside a new one.
        while not self.closed:
            event=await self.event_queue.get()
            try:
                kind=event.get('type')
                if kind in ('turn.delta','output_transcript.added'):
                    delta=event.get('delta','') if kind=='turn.delta' else event.get('item',{}).get('text','')
                    await self.emit({'type':'debug_model','phase':'text_delta','source':'native_realtime',
                        'provider':'codex','model':None,'backing_model':self.model,'protocol_event':kind,
                        'turn_id':event.get('turn_id'),'delta':delta,'playback_verified':False})
                await self.dc_event(event)
            except asyncio.CancelledError:raise
            except Exception as error:
                logging.getLogger('mist.voice').info(json.dumps({'event':'native_dispatch_failure','error_type':type(error).__name__}))
                await self.emit({'type':'error','fatal':True,'message':'Conversation event handling failed. Reconnect to continue.'})

    async def events(self):
        while not self.closed:
            try:event=await asyncio.to_thread(self.client._rpc.events.get,True,.25)
            except queue.Empty:continue
            if event is None:break
            method=event.get('method','');p=event.get('params',{})
            if p.get('threadId') not in (None,self.client.thread_id):continue
            if method=='thread/realtime/sdp':
                self.startup['answer_received_ms']=round((time.perf_counter()-self.started_at)*1000)
                await self.pc.setRemoteDescription(RTCSessionDescription(p['sdp'],'answer'))
            elif method=='thread/realtime/error':
                self.failure=p.get('message','Native voice failed');self.ready.set()
                await self.emit({'type':'error','fatal':True,'message':self.failure})
            elif method=='turn/started':
                self.turn=p.get('turn',{}).get('id');self.tool_count=0
                self.turn_started_at[self.turn]=time.perf_counter()
                self.turn_expression_revisions[self.turn]=self.expression_revision()
                while len(self.turn_expression_revisions)>32:self.turn_expression_revisions.pop(next(iter(self.turn_expression_revisions)))
                while len(self.turn_started_at)>32:self.turn_started_at.pop(next(iter(self.turn_started_at)))
                await self.emit({'type':'debug_model','phase':'thinking_started','turn_id':self.turn,'model':self.client.model,'provider':'codex','source':'native_backing'})
            elif method=='turn/completed':
                completed=p.get('turn',{});completed_id=completed.get('id',self.turn)
                began=self.turn_started_at.pop(completed_id,None)
                await self.emit({'type':'debug_model','phase':'thinking_finished','turn_id':completed_id,
                    'model':self.client.model,'provider':'codex','source':'native_backing','status':completed.get('status'),
                    'error':completed.get('error'),'duration_ms':None if began is None else (time.perf_counter()-began)*1000})
                if p.get('turn',{}).get('id',self.turn)==self.turn:self.turn=None
            elif method=='item/agentMessage/delta':
                await self.emit({'type':'debug_model','phase':'text_delta','source':'native_backing','provider':'codex',
                    'model':self.client.model,'turn_id':p.get('turnId',self.turn),'delta':p.get('delta',''),'playback_verified':False})
            elif method=='item/tool/call':
                name=p.get('tool');args=p.get('arguments',{})
                call_turn=p.get('turnId') or self.turn
                expression_revision=self.turn_expression_revisions.get(call_turn)
                tool_began=time.perf_counter()
                await self.emit({'type':'debug_tool','phase':'started','call_id':p.get('callId',str(event.get('id'))),'name':name,'arguments':args,
                    'turn_id':call_turn,'source':'native_backing','provider':'codex','model':self.client.model})
                error_type=None
                try:
                    # Stop remains effective after interruption or an exhausted budget.
                    # Reject old requests before they can consume the new turn's budget.
                    if name!='stop_robot':
                        if expression_revision is None or expression_revision!=self.expression_revision():
                            raise ValueError('Tool request belongs to a superseded user turn')
                        self.tool_count+=1
                        if self.tool_count>12:raise ValueError('Per-turn application tool budget exceeded')
                    from duplex.background import BACKGROUND_NAMES
                    user_text=None if name=='stop_robot' else self.request_text()
                    if name!='stop_robot' and self.has_request_context and (not isinstance(user_text,str) or not user_text.strip()):
                        raise ValueError('Current user request text is unavailable; no action was taken.')
                    if self.background and name in BACKGROUND_NAMES:
                        result=await self.background.handle(name,args)
                    else:
                        result=self.runtime.call(name,args,request_text=user_text) if user_text is not None else self.runtime.call(name,args)
                    success=result.get('status')!='refused'
                except ValueError as error:result={'status':'refused','reason':str(error)[:300]};success=False;error_type=type(error).__name__
                except (TypeError,KeyError) as error:result={'status':'refused','reason':'Invalid or unsupported tool request'};success=False;error_type=type(error).__name__
                except Exception as error:result={'status':'refused','reason':'Application tool failed'};success=False;error_type=type(error).__name__
                await self.emit({'type':'debug_tool','phase':'finished','call_id':p.get('callId',str(event.get('id'))),'name':name,
                    'arguments':args,'result':result,'duration_ms':(time.perf_counter()-tool_began)*1000,'success':success,
                    'error_type':error_type,'turn_id':call_turn,'source':'native_backing','provider':'codex','model':self.client.model})
                self.client._rpc.send({'id':event['id'],'result':{'success':success,
                    'contentItems':[{'type':'inputText','text':json.dumps(result)}]}})
                await self.emit({'type':'tool','name':name,'arguments':args,'result':result,
                    'expression_revision':expression_revision,'turn_id':call_turn})
            elif 'id' in event and method:
                self.client._rpc.send({'id':event['id'],'error':{'code':-32601,'message':'Only MIST application tools are available'}})
    async def receive_audio(self,track):
        converter=av.AudioResampler(format='s16',layout='mono',rate=16000)
        try:
            while not self.closed:
                frame=await track.recv()
                for converted in converter.resample(frame):await self.audio(converted.to_ndarray().tobytes())
        except asyncio.CancelledError:raise
        except Exception:
            if not self.closed:await self.emit({'type':'error','fatal':True,'message':'Native audio track ended'})
    async def context(self,text):
        await asyncio.to_thread(self.client._rpc.request,'thread/realtime/appendText',
            {'threadId':self.client.thread_id,'role':'developer','text':text})
    async def say_text(self,text):
        # Explicit speakable append is for keyboard input/test responses, not a mic substitute.
        await asyncio.to_thread(self.client._rpc.request,'thread/realtime/appendText',
            {'threadId':self.client.thread_id,'role':'user','text':text})
        await asyncio.to_thread(self.client._rpc.request,'thread/realtime/appendSpeech',
            {'threadId':self.client.thread_id,'text':'Respond briefly to the user message just added.'})
    async def submit_text(self,text):
        # appendText + appendSpeech supports the existing reading-test helper,
        # but does not establish verified microphone-style user-turn boundaries.
        raise NotImplementedError('Native typed user turns are unsupported; use microphone or fixture audio. say_text is a reading-test helper only.')
    async def interrupt(self):
        if self.turn:
            try:await asyncio.to_thread(self.client._rpc.request,'turn/interrupt',{'threadId':self.client.thread_id,'turnId':self.turn},timeout=3)
            except Exception:pass
    async def close(self):
        async with self._close_lock:
            if self._close_complete:return
            self.closed=True
            for t in list(self.tasks):t.cancel()
            await asyncio.gather(*list(self.tasks),return_exceptions=True)
            if self.pc:await self.pc.close()
            if self._startup_worker is not None:
                try:
                    client=await asyncio.wait_for(asyncio.shield(self._startup_worker),30)
                    if self.client is None:self.client=client
                except asyncio.TimeoutError:
                    if self._retirement_task is None:self._retirement_task=asyncio.create_task(self.retire_startup())
                    raise RuntimeError('Voice startup is still retiring; do not start a replacement session yet') from None
                except Exception:pass
            if self.client:
                try:await asyncio.to_thread(self.client._rpc.request,'thread/realtime/stop',{'threadId':self.client.thread_id},timeout=3)
                except Exception:pass
                await asyncio.to_thread(self.client.close)
            self._close_complete=True
    async def retire_startup(self):
        try:await asyncio.shield(self._startup_worker)
        except Exception:pass
        await self.close()
