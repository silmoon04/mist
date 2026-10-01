"""Local voice comparisons with isolated conversations and saved observable traces."""
import argparse
import asyncio
import base64
import contextvars
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import time

from aiohttp import web

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN))
from duplex import server
from duplex.background import BackgroundBrain, specs as background_specs
from duplex.native import NativeVoice
from duplex.runtime import RobotRuntime, specs as runtime_specs
from duplex.tts import StreamingTTS
from duplex.conversation_policy import POLICIES
from duplex.affect_director import AffectDirector
from duplex.affect_controller import AffectController
from duplex.expression_requests import expression_request_constraint
from duplex.expression_policy import normalize_expression
from duplex.trial_traces import BufferedJournal, TraceStore
from duplex.database_traces import DatabaseTraceStore
from duplex.session_memory import SessionMemory
from duplex.playback_receipts import PlaybackReceipts
from duplex.remote_access import RemoteAccess
from benchmarks.naturalness.cascade_voice import CascadeVoice
from benchmarks.naturalness.streaming_voice_20260930 import StreamingComparisonVoice

VOICES = (
    {'id':'mist', 'label':'MIST (refined)', 'voice_id':server.VOICE},
    {'id':'jessica', 'label':'Jessica', 'voice_id':'cgSgspJ2msm6clMCkdW9'},
    {'id':'laura', 'label':'Laura', 'voice_id':'FGY2WhTYpPnrIDTdsKH5'},
    {'id':'callum', 'label':'Callum', 'voice_id':'N2lVS1w4EtoT3dr4eOWO'},
)

ARCHITECTURES = [
    dict(id='qwen-memory',label='Qwen · memory + Flux',provider='cerebras',
         model='qwen-3.8-27b',reasoning_effort='low',endpoint_ms=None,floor='selective',
         background_provider='codex',background_model='gpt-6-luna',background_reasoning_effort='low',
         conversation_policy='grounded',background_context=True,parallel_tool_calls=True,playback_buffer_ms=120,
         affect_model='qwen-3.8-27b',affect_reasoning='none',session_memory=True,
         summary_model='qwen-3.8-27b',summary_effort='none',summary_every=4,
         asr_provider='flux',turn_policy='flux_with_continuation_hold',incomplete_hold_ms=1200,jev_enabled=False,
         description='Qwen speaks; a separate Qwen keeps sourced working notes. Flux listens, Luna can handle deeper tool work. Audio and history are saved on the host.'),
    dict(id='cerebras-balanced', label='Cerebras · more pause time', provider='cerebras',
         model='gpt-oss-120b', endpoint_ms=700, floor='selective',
         description='Waits 700 ms for a pause. Lets brief acknowledgements pass; waits for recognised words before interrupting.'),
    dict(id='cerebras-high', label='GPT-OSS · high reasoning', provider='cerebras',
         model='gpt-oss-120b', endpoint_ms=700, floor='selective', reasoning_effort='high',
         description='The same 700 ms pause setting, with more reasoning before an answer.'),
    dict(id='qwen-none', label='Qwen 27B · reasoning off', provider='cerebras',
         model='qwen-3.8-27b', endpoint_ms=700, floor='selective', reasoning_effort='none',
         description='Qwen 3.8 with reasoning disabled. Uses the same 700 ms pause setting and MIST voice.'),
    dict(id='qwen-low', label='Qwen 27B · low reasoning', provider='cerebras',
         model='qwen-3.8-27b', endpoint_ms=700, floor='selective', reasoning_effort='low',
         description='Qwen 3.8 with low reasoning. Uses the same 700 ms pause setting and MIST voice.'),
    dict(id='qwen-assist',label='Qwen · conversation + analyst',provider='cerebras',
         model='qwen-3.8-27b',reasoning_effort='low',endpoint_ms=500,floor='selective',
         background_provider='cerebras',background_model='gpt-oss-120b',background_reasoning_effort='medium',
         conversation_policy='responsive',background_context=True,parallel_tool_calls=True,playback_buffer_ms=120,
         description='Qwen handles the conversation; an optional GPT-OSS analyst handles deeper questions. Shorter pauses and grouped tool requests. Experimental.'),
    dict(id='qwen-affect',label='Qwen · separate expression reader',provider='cerebras',
         model='qwen-3.8-27b',reasoning_effort='low',endpoint_ms=500,floor='selective',
         background_provider='cerebras',background_model='gpt-oss-120b',background_reasoning_effort='medium',
         conversation_policy='responsive',background_context=True,parallel_tool_calls=True,playback_buffer_ms=120,
         affect_model='qwen-3.8-27b',affect_reasoning='none',
         description='A second Qwen reads the conversation for face and voice variation. Speech starts independently; late suggestions are discarded. Experimental.'),
    dict(id='qwen-flux',label='Qwen · Flux + MIST',provider='cerebras',
         model='qwen-3.8-27b',reasoning_effort='low',endpoint_ms=None,floor='selective',
         background_provider='cerebras',background_model='gpt-oss-120b',background_reasoning_effort='medium',
         conversation_policy='responsive',background_context=True,parallel_tool_calls=True,playback_buffer_ms=120,
         affect_model='qwen-3.8-27b',affect_reasoning='none',
         asr_provider='flux',turn_policy='flux_end_of_turn',incomplete_hold_ms=0,jev_enabled=False,
         description='Qwen and the separate expression reader use MIST voice. Deepgram Flux decides when your turn ends; brief acknowledgements preserve the floor. Experimental.'),
    dict(id='oss-affect',label='Qwen · GPT-OSS expression reader',provider='cerebras',
         model='qwen-3.8-27b',reasoning_effort='low',endpoint_ms=500,floor='selective',
         background_provider='cerebras',background_model='gpt-oss-120b',background_reasoning_effort='medium',
         conversation_policy='responsive',background_context=True,parallel_tool_calls=True,playback_buffer_ms=120,
         affect_model='gpt-oss-120b',affect_reasoning='low',
         description='GPT-OSS selects face and voice variation beside the Qwen speaker. Speech never waits for it. Experimental.'),
    dict(id='cerebras-fast', label='Cerebras · shorter pauses', provider='cerebras',
         model='gpt-oss-120b', endpoint_ms=300, floor='selective',
         description='Waits 300 ms. Starts sooner, but can mistake a pause inside your sentence for its end.'),
    dict(id='cerebras-interrupt', label='Cerebras · immediate interruption', provider='cerebras',
         model='gpt-oss-120b', endpoint_ms=300, floor='cancel_all',
         description='Stops on detected speech, before recognising the words. Brief acknowledgements can cut it off.'),
    dict(id='luna', label='Luna · separate speech recognition', provider='codex',
         model='gpt-6-luna', endpoint_ms=300, floor='selective',
         description='Deepgram recognises speech, Luna writes the reply and calls tools. Compare its judgement and wording with Cerebras.'),
    dict(id='native', label='Codex native voice · Luna tools', provider='native',
         model='Codex native V3', endpoint_ms=None, floor='native',
         description='Codex handles listening and turn taking. Luna handles delegated tools. Its reply text uses the same MIST voice.'),
]
ARCHITECTURES.insert(1, {**ARCHITECTURES[0], 'id':'qwen-expressive',
    'label':'Qwen · expressive MIST (experimental)', 'tts_model':'eleven_v4_turbo',
    'description':'The memory and Flux setup with Eleven v4 Turbo delivery cues. Experimental: voice timing and mouth sync may differ from standard MIST.'})
ARCHITECTURES.insert(2, {**ARCHITECTURES[0], 'id':'qwen-memory-pause',
    'label':'Qwen · more time to continue', 'complete_hold_ms':800,
    'description':'Adds an 800 ms continuation window after a complete Flux endpoint. Gives you more time to add a clause, but delays replies after genuine endings.'})
for architecture in ARCHITECTURES:
    architecture.setdefault('tts_model', 'eleven_flash_v2_5')
    architecture.update(backing_model='gpt-6-luna',
                        speech_backend='streaming-tts', voice_id=server.VOICE,
                        hardware_connected=False)
    architecture.setdefault('incomplete_hold_ms', 900)
    if architecture.get('asr_provider') == 'flux':
        architecture.setdefault('complete_hold_ms', 350 if architecture.get('turn_policy') == 'flux_with_continuation_hold' else 0)
    architecture.setdefault('reasoning_effort', 'low')
    for key,value in dict(background_model='gpt-6-luna',background_provider='codex',background_reasoning_effort='low',
                          background_context=False,conversation_policy='standard',parallel_tool_calls=False,playback_buffer_ms=200).items():
        architecture.setdefault(key,value)
    if architecture['provider'] != 'native':
        architecture['max_output_tokens'] = 4096 if architecture['provider'] == 'cerebras' else 400
        architecture['output_limit_kind'] = ('completion_cap_including_reasoning' if architecture['provider'] == 'cerebras'
                                             else 'output_target_not_hard_cap')
        if architecture['provider'] == 'cerebras':
            architecture['output_limit_note'] = ('Matches the 4096-token comparison budget. Some high-effort answers '
                                                'exceeded the previous 1024-token cap, which includes reasoning.')

ACTIVE_TRACE = contextvars.ContextVar('mist_trial_trace', default=None)


class TraceLogHandler(logging.Handler):
    def emit(self, record):
        journal = ACTIVE_TRACE.get()
        if journal is None:
            return
        try:
            journal.record(json.loads(record.getMessage()), 'provider')
        except (ValueError, TypeError, RuntimeError, OSError):
            pass


class TrialConversation(server.Conversation):
    def __init__(self, app, ws, architecture, journal):
        self.architecture, self.journal = architecture, journal
        run_dir = app['run_dir'] / 'conversations' / journal.trace_id
        local = dict(app)
        local.update(runtime=RobotRuntime(run_dir / 'state'), run_dir=run_dir, journal=journal)
        super().__init__(local, ws)
        self.database = getattr(app.get('trial_store'), 'database', None)
        if self.database:
            self.runtime.session_database=self.database
            self.runtime.session_id=journal.trace_id
            self.runtime.memory=list(reversed(self.database.list_preferences()))
        self.memory = None
        self.storage_task = None
        self.storage_started = time.monotonic()
        self.storage_failed = False
        self._remembered_turns = set()
        self._last_mic_seq = None
        self._mic_gaps = 0
        self._last_mic_report = 0
        self.playback_receipts = PlaybackReceipts()
        if self.database and architecture.get('session_memory'):
            self.memory = SessionMemory(self.database, journal.trace_id,
                summary_client_factory=app.get('summary_client_factory'),
                summary_every=architecture.get('summary_every', 4),
                mode=architecture.get('memory_mode','discussion'))
            self.runtime.session_memory = self.memory
            self.memory.playback_context = self.playback_receipts.context
        self.brain = BackgroundBrain(self.runtime, run_dir / 'background', self.emit,
                                     lambda: self.user_revision, model=architecture['background_model'],
                                     provider=architecture['background_provider'],reasoning_effort=architecture['background_reasoning_effort'],
                                     context=(lambda:[{'role':'application_report','text':self.memory.context(self.user_request_text, max_chars=5000)}]
                                              if self.memory else getattr(self.voice,'history',[])) if architecture['background_context'] else None)
        self.input_tasks = set()
        self.started = False
        self.current_face = {'expression':'neutral','variant':0}
        self.face_serial = 0
        self.temporary_face = None
        self.affect_speech_locked = False
        self.affect = None
        if architecture.get('affect_model'):
            self.delivery = 'neutral'
            self.affect = AffectController(AffectDirector(model=architecture['affect_model'],
                reasoning_effort=architecture['affect_reasoning']), self.emit, self.apply_affect)

    def affect_token(self):
        return {'user_revision':self.user_revision,'expression_revision':self.expression_revision,
                'face_serial':self.face_serial}

    async def apply_affect(self, decision, token):
        if not self.alive or self.ws.closed or token != self.affect_token():
            return 'stale_or_replaced'
        if self.affect_speech_locked or self.user_speaking:
            return 'speech_already_started' if self.affect_speech_locked else 'user_speaking'
        if decision['change'] and self.temporary_face is not None:
            return 'temporary_face_active'
        update_delivery = getattr(self.mask, 'set_delivery', None)
        if getattr(self.mask, 'active', None) is not None and callable(update_delivery):
            if not update_delivery(decision['delivery']):
                return 'speech_already_started'
        # No await separates the final ownership checks from local state changes.
        # The face packet still carries its revision for browser-side rejection.
        if decision['change']:
            try:
                result=self.runtime.call('set_expression',
                    {'expression':decision['expression'],'variant':decision['variant']},
                    request_text=self.user_request_text)
            except ValueError:
                if getattr(self.mask, 'active', None) is not None and callable(update_delivery):
                    update_delivery(self.delivery)
                return 'request_constraint'
        self.delivery=decision['delivery']
        if decision['change']:
            await self.emit({'type':'face',**{key:result[key] for key in ('expression','variant','duration_ms','persistent')},
                'epoch':getattr(self.mask,'epoch',0),'expression_revision':self.expression_revision,'source':'director'})
        return 'applied_before_speech'

    async def emit(self, event):
        if event.get('type') == 'speech_style' and event.get('phase') == 'committed' and \
                event.get('epoch') == getattr(self.mask, 'epoch', None) and \
                event.get('turn_id') == self.assistant_turn:
            self.affect_speech_locked = True
        remote = self.app.get('remote_access')
        if remote:
            event = remote.scrub(event)
        if event.get('type') == 'audio':
            self.playback_receipts.observe_audio(event)
        elif event.get('type') == 'audio_reset':
            self.playback_receipts.reset(event.get('epoch'))
        if self.database and event.get('type') in ('audio','listener_cue') and event.get('pcm'):
            try:
                raw = base64.b64decode(event['pcm'], validate=True)
                if not self.database.enqueue_audio(self.journal.trace_id, 'assistant_generated', raw,
                        sample_rate=event.get('sample_rate', 24000),
                        timestamp_ms=round((time.monotonic()-self.storage_started)*1000),
                        metadata={key:event[key] for key in ('type','epoch','turn_id','cue_id','chunk_id','text') if key in event}):
                    self.storage_failed = True
            except (ValueError, OSError, RuntimeError):
                self.storage_failed = True
        if self.memory and event.get('type') == 'tool':
            self.memory.observe('tool', json.dumps({'name':event.get('name'),'result':event.get('result')},ensure_ascii=False))
        if self.database and event.get('type') == 'transcript_done' and event.get('role') == 'user' and event.get('floor_preserved') and event.get('text','').strip():
            text=self.journal.journal._cleaner.text(event['text'])
            if self.memory:self.memory.observe('user',text)
            else:self.database.append_turn(self.journal.trace_id,'user',text)
        if event.get('type') == 'audio_reset':
            await super().emit(event)
            temporary=self.temporary_face
            if temporary is not None and type(event.get('epoch')) is int and \
                    type(temporary.get('epoch')) is int and event['epoch'] > temporary['epoch']:
                self.temporary_face=None
                self.face_serial+=1
            return
        if event.get('type') == 'face':
            self.face_serial+=1
            if event.get('persistent',True):
                self.current_face={key:event.get(key,0) for key in ('expression','variant')}
                self.temporary_face=None
            elif self.temporary_face is not None or \
                    {key:event.get(key,0) for key in ('expression','variant')} != self.current_face:
                self.temporary_face={'serial':self.face_serial,'epoch':event.get('epoch'),
                    'expression_revision':event.get('expression_revision')}
            event={**event,'face_serial':self.face_serial}
        if event.get('type') == 'ready' and self.alive and not self.ws.closed:
            # Provider input paths are initialized before ready reaches the client.
            self.started = True
        await super().emit(event)

    async def start(self):
        if self.memory:self.memory.start()
        if self.database:
            await self.emit({'type':'session_storage','session_id':self.journal.trace_id,
                             'recording':True,'status':'recording','audio':['mic','assistant'], 'location':'host'})
            self.storage_task = asyncio.create_task(self.monitor_storage())
        tts_options = {'model_id':self.architecture['tts_model']} if self.architecture.get('tts_model') else {}
        if self.architecture.get('voice_id') != server.VOICE:
            tts_options['voice_id'] = self.architecture['voice_id']
        self.mask = StreamingTTS(self.app['env']['ELEVENLABS_API_KEY'], self.emit, **tts_options)
        await self.mask.start()
        kwargs = dict(speech_backend='streaming-tts', background=self.brain,
                      expression_revision=lambda: self.expression_revision,
                      request_text=lambda: self.user_request_text)
        a = self.architecture
        if a['provider'] == 'native':
            cls = NativeVoice
            kwargs.update(model=a['backing_model'])
        else:
            cls = StreamingComparisonVoice if a.get('asr_provider') == 'flux' else CascadeVoice
            kwargs.update(provider=a['provider'], model=a['model'], floor=a['floor'],
                          endpoint_ms=a['endpoint_ms'], incomplete_hold_ms=a['incomplete_hold_ms'],
                          reasoning_effort=a['reasoning_effort'], max_output_tokens=a['max_output_tokens'],
                          conversation_policy=a['conversation_policy'],parallel_tool_calls=a['parallel_tool_calls'])
            if a.get('asr_provider') == 'flux':
                kwargs.update(asr_provider='flux', jev_enabled=False, live_mode=True,
                              complete_hold_ms=a['complete_hold_ms'])
            if a['provider'] == 'cerebras' and self.app.get('remote_access'):
                kwargs['connect_retries'] = 1
        factory = self.app.get('trial_voice_factory', cls)
        self.voice = factory(self.runtime, self.app['run_dir'] / 'voice', self.emit,
                             self.mask.feed, self.realtime_event, **kwargs)
        if getattr(self.voice, 'listener_voice', None):
            self.voice.listener_voice.voice_id = a['voice_id']
        self.journal.record({'type':'configuration', 'architecture':a,
                             'tools':runtime_specs() + background_specs(),
                             'persona':(BRAIN / 'duplex/persona.txt').read_text(encoding='utf-8'),
                             'conversation_policy_append':POLICIES[a['conversation_policy']],
                             'history':'Fresh conversation and preferences for every connection.'})
        await self.voice.start()
        self.warm_task = asyncio.create_task(self.mask.warm())
        self.result_task = asyncio.create_task(self.deliver_results())

    async def realtime_event(self, event):
        self.journal.record(event, 'model_protocol')
        turn=event.get('turn',{})
        if self.database and event.get('type') == 'turn.done' and turn.get('transcript','').strip():
            role=turn.get('role')
            identity=f"{role}:{turn.get('id')}"
            if role in ('user','assistant') and identity not in self._remembered_turns:
                clean=self.journal.journal._cleaner.text(turn['transcript'])
                if self.memory:self.memory.observe(role,clean,event_id=identity,playback_verified=False)
                else:self.database.append_turn(self.journal.trace_id,role,clean,source_event_id=identity,playback_verified=False)
                self._remembered_turns.add(identity)
        if event.get('type')=='turn.created':
            if turn.get('role')=='user':
                self.affect_speech_locked=False
                if self.affect:self.delivery='neutral'
        await super().realtime_event(event)
        if self.affect and event.get('type')=='turn.done' and turn.get('role')=='user' and turn.get('transcript','').strip():
            records=[]
            for index,record in enumerate(getattr(self.voice,'history',[])[-12:]):
                role=record.get('role')
                if role not in ('user','assistant','assistant_audible'):continue
                records.append({'id':str(index),'role':role,'text':record.get('text','')})
            text=self.user_request_text
            snapshot={'records':records,'current_user':text,'current_face':dict(self.current_face),
                      'face_override':self.temporary_face is not None or expression_request_constraint(text) is not None}
            await self.affect.submit(snapshot,self.affect_token())

    def acknowledge_face_transition(self,event):
        temporary=self.temporary_face
        if temporary is None or not isinstance(event,dict):return
        if event.get('type')!='expression' or event.get('phase')!='transition':return
        if type(event.get('face_serial')) is not int or event['face_serial']!=temporary['serial']:return
        if type(event.get('expression_revision')) is not int or event['expression_revision']!=temporary['expression_revision']:return
        if type(event.get('epoch')) is not int or event['epoch']!=temporary['epoch']:return
        base=normalize_expression(self.current_face)['face_id']
        if event.get('to')!=base:return
        self.temporary_face=None
        self.face_serial+=1

    async def handle(self, packet):
        kind = packet.get('type')
        if kind != 'mic':
            self.journal.record(packet, 'browser_command')
        if kind == 'debug_client' and isinstance(packet.get('event'),dict):
            self.acknowledge_face_transition(packet['event'])
            receipt = self.playback_receipts.accept(packet['event'])
            if receipt:
                self.journal.record(receipt, 'browser_receipt')
        if kind == 'debug_client' and packet.get('event', {}).get('type') in ('expression_transition', 'client_error'):
            self.journal.record(packet['event'], 'browser')
        elif kind == 'text':
            text = packet.get('text', '')
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1200:
                raise ValueError('Enter 1 to 1200 characters.')
            submit = getattr(self.voice, 'submit_text', None)
            if submit is None:
                raise ValueError('Use the microphone or synthetic speech with this architecture.')
            await submit(text.strip())
        elif kind == 'fixture':
            # Keep reading mic, cancellation and playback messages during paced input.
            if self.input_tasks:
                raise ValueError('A synthetic phrase is already playing.')
            async def feed():
                try:
                    await super(TrialConversation, self).handle(packet)
                except Exception as exc:
                    await self.emit({'type':'error', 'message':str(exc)[:200]})
            task = asyncio.create_task(feed())
            self.input_tasks.add(task)
            task.add_done_callback(self.input_tasks.discard)
        else:
            await super().handle(packet)

    def accept_microphone(self, raw, metadata=None):
        if not isinstance(raw,bytes) or len(raw)%2 or len(raw)>6400:
            raise ValueError('Expected bounded 16 kHz mono PCM16')
        metadata=metadata or {}
        seq=metadata.get('seq')
        if type(seq) is int:
            if self._last_mic_seq is not None and seq != self._last_mic_seq+1:self._mic_gaps+=1
            self._last_mic_seq=seq
        if self.database and raw:
            accepted=self.database.enqueue_audio(self.journal.trace_id,'mic',raw,sample_rate=16000,
                timestamp_ms=round((time.monotonic()-self.storage_started)*1000),
                metadata={key:metadata[key] for key in ('seq','capture_ms','source') if key in metadata})
            if not accepted:self.storage_failed=True
        # Capture precedes the provider queue so rejected packets remain available.
        try:super().accept_microphone(raw, metadata)
        finally:
            now=time.monotonic()
            if now-self._last_mic_report>=1:
                self._last_mic_report=now
                stats=getattr(self.voice.track,'statistics',lambda:{})()
                self.journal.record({'type':'microphone_transport','seq':seq,'sequence_gaps':self._mic_gaps,**stats})

    async def monitor_storage(self):
        previous=None
        while self.alive:
            await asyncio.sleep(.5)
            if self.memory:
                details=self.memory.diagnostics()
                signature=json.dumps(details,sort_keys=True)
                if signature!=previous:
                    previous=signature
                    await self.emit({'type':'session_summary',**details})
            status=self.database.audio_status()
            if self.storage_failed or status.get('errors') or status.get('dropped'):
                await self.emit({'type':'session_storage','session_id':self.journal.trace_id,
                                 'recording':False,'status':'error','location':'host'})
                self.storage_failed=True
                return

    async def close(self):
        if self.storage_task:
            self.storage_task.cancel()
            await asyncio.gather(self.storage_task,return_exceptions=True)
        if self.affect:self.affect.closed=True
        for task in self.input_tasks:
            task.cancel()
        await asyncio.gather(*self.input_tasks, return_exceptions=True)
        self.input_tasks.clear()
        await super().close()
        if self.affect:await self.affect.close()
        if self.memory:await asyncio.to_thread(self.memory.close)
        if self.database:
            await asyncio.to_thread(self.database.flush_audio)
            status=self.database.audio_status()
            self.journal.record({'type':'session_storage','session_id':self.journal.trace_id,
                'recording':False,'status':'error' if self.storage_failed or status.get('errors') or status.get('dropped') else 'saved',
                'audio':['mic','assistant'],'location':'host'})


def create_studio(args):
    remote_origin = getattr(args, 'remote_origin', None)
    code_file = getattr(args, 'remote_pair_code_file', None)
    env_code = os.environ.get('MIST_REMOTE_PAIR_CODE')
    if remote_origin:
        if Path(args.run_dir).resolve() == (BRAIN / 'results/live-studio').resolve():
            raise ValueError('Remote mode requires a separate run directory.')
        if bool(code_file) == bool(env_code):
            raise ValueError('Remote mode requires exactly one pairing code source: --remote-pair-code-file or MIST_REMOTE_PAIR_CODE.')
        pair_code = Path(code_file).read_text(encoding='utf-8').strip() if code_file else env_code
    elif code_file or env_code:
        # A code in the environment alone does not turn on remote serving.
        pair_code = None
    else:
        pair_code = None
    args.cert = None
    args.key = None
    args.pair_code = None
    args.sensor_url = None
    args.enable_fixtures = True
    args.speech_backend = 'streaming-tts'
    app = server.create_app(args)
    remote = RemoteAccess(remote_origin, pair_code, app) if remote_origin else None
    app['remote_access'] = remote
    if remote:
        app['public_origin'] = remote.origin
        app.middlewares.append(remote.middleware)
    private_values = [v for k, v in app['env'].items() if ('KEY' in k or 'TOKEN' in k or 'CODE' in k) and len(v) > 6]
    if remote:
        private_values.append(pair_code)
        remote.secret_values = tuple(private_values)
    store = DatabaseTraceStore(app['run_dir'] / 'traces', secrets=private_values)
    app['trial_store'] = store
    app['trial_source_hashes'] = {name:hashlib.sha256((BRAIN / name).read_bytes()).hexdigest()
        for name in ('duplex/live_studio.py', 'duplex/server.py', 'duplex/native.py',
                     'benchmarks/naturalness/cascade_voice.py', 'benchmarks/naturalness/cerebras_client.py',
                     'benchmarks/naturalness/streaming_voice_20260930.py',
                     'benchmarks/naturalness/streaming_asr_20260930.py',
                     'duplex/background.py', 'duplex/affect_director.py', 'duplex/affect_controller.py',
                     'duplex/session_store.py', 'duplex/session_memory.py', 'duplex/database_traces.py', 'duplex/playback_receipts.py',
                     'duplex/turn_policy.py', 'duplex/static/microphone_signal.mjs', 'duplex/static/trial_memory.mjs',
                     'duplex/static/animation_picker.mjs',
                     'duplex/expression_policy.py', 'duplex/expression_requests.py',
                     'duplex/persona.txt', 'duplex/conversation_policy.py', 'duplex/tts.py', 'duplex/static/app.js',
                     'duplex/static/playback.js', 'duplex/lipsync.py',
                     'duplex/listener_feedback.py', 'duplex/listener_backchannels.py', 'duplex/listener_voice.py',
                     'duplex/static/listener_cue.js', 'duplex/static/user_transcript_state.mjs',
                     'duplex/static/index.html', 'duplex/static/style.css', 'duplex/static/trials.js',
                     'duplex/static/activity_state.js', 'duplex/static/expression_policy.js',
                     'art_direction/artist_studio_20260916/reuse/handdrawn_v6/runtime.js',
                     'ui/static/drawn_face_renderer.js')}
    app['trial_owner'] = None
    app['trial_health'] = {'quarantined':False}
    app['trial_closed'] = asyncio.Event()
    app['trial_closed'].set()

    def origin_ok(r):
        if remote:
            return remote.request_origin_ok(r, required=True)
        return (r.remote in ('127.0.0.1', '::1') and
                r.host.split(':')[0] in ('127.0.0.1', 'localhost', '[') and
                r.headers.get('Origin') == 'http://' + r.host)

    def authorize(r, mutation=False):
        if remote and not remote.authenticated(r):
            raise web.HTTPUnauthorized()
        if not remote and not secrets.compare_digest(r.cookies.get('mist_session', ''), app['cookie']):
            raise web.HTTPForbidden(text='Open the live test page in this browser first.')
        if mutation and not origin_ok(r):
            raise web.HTTPForbidden()

    def catalog():
        result = []
        for item in ARCHITECTURES:
            required = ['ELEVENLABS_API_KEY']
            if item['provider'] != 'native':
                required.append('DEEPGRAM_API_KEY')
            if item['provider'] == 'cerebras':
                required.append('CEREBRAS_API_KEY')
            missing = [key for key in required if not app['env'].get(key)]
            result.append(dict(item, available=not missing, missing=missing,
                               connect_retries_configured=1 if remote and item['provider'] == 'cerebras' else 0,
                               source_hashes=app['trial_source_hashes']))
        return result

    async def page(r):
        return web.FileResponse(BRAIN / 'duplex/static/trials.html', headers={'Cache-Control':'no-store'})

    async def remote_session(r):
        return web.json_response({'authenticated':remote.authenticated(r) if remote else True,
                                  'remote_mode':bool(remote)}, headers={'Cache-Control':'no-store'})

    async def remote_pair(r):
        if not remote:
            raise web.HTTPNotFound()
        if not remote.request_origin_ok(r, required=True):
            raise web.HTTPForbidden()
        try:
            data = await r.json()
        except (ValueError, TypeError):
            raise web.HTTPBadRequest()
        if not isinstance(data, dict):
            raise web.HTTPBadRequest()
        if not remote.pair(data.get('code')):
            now = time.monotonic()
            status = 429 if len(remote.failures) >= 5 and remote.failures[0] > now - 60 else 401
            return web.json_response({'error':'too_many_attempts' if status == 429 else 'pairing_failed'},
                                     status=status, headers={'Cache-Control':'no-store'})
        owner = app['trial_owner']
        if owner:
            owner['reason'] = 'new_pairing'
            if owner['start']:
                owner['start'].cancel()
            asyncio.create_task(owner['session'].ws.close())
        response = web.json_response({'paired':True}, headers={'Cache-Control':'no-store'})
        remote.set_cookie(response)
        return response

    async def remote_logout(r):
        if not remote:
            raise web.HTTPNotFound()
        remote.revoke()
        owner = app['trial_owner']
        if owner:
            owner['reason'] = 'logout'
            if owner['start']:
                owner['start'].cancel()
            asyncio.create_task(owner['session'].ws.close())
        response = web.json_response({'authenticated':False}, headers={'Cache-Control':'no-store'})
        response.del_cookie('mist_session', path='/')
        return response

    async def bootstrap(r):
        if remote:
            authorize(r, mutation=True)
            return web.json_response({'paired':True}, headers={'Cache-Control':'no-store'})
        if not origin_ok(r):
            raise web.HTTPForbidden()
        response = web.json_response({'paired':True})
        response.set_cookie('mist_session', app['cookie'], httponly=True, samesite='Strict')
        return response

    async def architectures(r):
        authorize(r)
        return web.json_response({'architectures':catalog(),
                                  'voices':[{'id':v['id'], 'label':v['label']} for v in VOICES],
                                  'default':'qwen-memory'})

    async def sessions(r):
        authorize(r)
        saved = await asyncio.to_thread(store.list_sessions)
        return web.json_response({'sessions':[dict(s, architecture=s.get('config', {})) for s in saved]}, headers={'Cache-Control':'no-store'})

    def selected_trace(r):
        authorize(r)
        trace = store.get(r.query.get('session', ''))
        if trace is None:
            raise web.HTTPNotFound(text='Conversation not found.')
        return trace

    async def events(r):
        trace = selected_trace(r)
        try:
            cursor = max(0, int(r.query.get('after', '0')))
            limit = min(1000, max(1, int(r.query.get('limit', '400'))))
        except ValueError:
            raise web.HTTPBadRequest(text='Invalid event cursor.')
        page = await asyncio.to_thread(trace.since, cursor, limit=limit)
        return web.json_response(page, headers={'Cache-Control':'no-store'})

    async def export(r):
        trace = selected_trace(r)
        data = await asyncio.to_thread(trace.export)
        return web.json_response(data, headers={'Cache-Control':'no-store',
            'Content-Disposition':f'attachment; filename="mist-{trace.trace_id}.json"'})

    async def saved_memory(r):
        trace=selected_trace(r)
        audio=await asyncio.to_thread(store.database.audio_summary,trace.trace_id)
        return web.json_response({'session_id':trace.trace_id,
            'turns':await asyncio.to_thread(store.database.list_turns,trace.trace_id),
            'summaries':await asyncio.to_thread(store.database.list_summaries,trace.trace_id),
            'audio':[{k:v for k,v in stream.items() if k!='path'} for stream in audio['streams']],
            'audio_status':audio['audio_status']},headers={'Cache-Control':'no-store'})

    async def saved_audio(r):
        trace=selected_trace(r)
        stream=r.query.get('stream','mic')
        if stream not in ('mic','assistant_generated'):raise web.HTTPBadRequest()
        path=store.database.audio_path(trace.trace_id,stream)
        if not path.is_file():raise web.HTTPNotFound(text='No recorded audio for this session.')
        return web.FileResponse(path,headers={'Cache-Control':'no-store','Content-Type':'audio/wav'})

    voice_lab_dir = Path(app['run_dir']) / 'voice-lab'

    async def voice_lab(r):
        authorize(r)
        path = voice_lab_dir / 'listen.html'
        if not path.is_file():
            raise web.HTTPNotFound(text='Voice comparison clips have not been rendered on this host.')
        return web.FileResponse(path, headers={'Cache-Control':'no-store'})

    async def voice_lab_audio(r):
        authorize(r)
        # The generated report is the allowlist; never accept a caller's path.
        name = r.match_info['filename']
        try:
            report = json.loads((voice_lab_dir / 'results.json').read_text(encoding='utf-8'))
            samples = report.get('samples', [])
        except (OSError, ValueError):
            raise web.HTTPNotFound()
        allowed = {sample.get('wav_file') for sample in samples if isinstance(sample, dict)}
        if name not in allowed or Path(name).name != name or not name.endswith('.wav'):
            raise web.HTTPNotFound()
        path = voice_lab_dir / 'audio' / name
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Cache-Control':'no-store', 'Content-Type':'audio/wav'})

    async def disconnect(r):
        authorize(r, mutation=True)
        owner = app['trial_owner']
        if owner:
            owner['reason'] = 'architecture_switch'
            # Cancelling startup also closes providers that have not reached ready.
            owner['start'].cancel()
            await owner['session'].ws.close()
        try:
            await asyncio.wait_for(app['trial_closed'].wait(), 15)
        except asyncio.TimeoutError:
            raise web.HTTPServiceUnavailable(text='Previous conversation is still closing. Try again.')
        if app['trial_health']['quarantined']:
            raise web.HTTPServiceUnavailable(text='A provider did not close correctly. Restart the local voice trial server before reconnecting.')
        return web.json_response({'closed':True})

    async def websocket(r):
        authorize(r, mutation=True)
        architecture = next((a for a in catalog() if a['id'] == r.query.get('architecture')), None)
        if architecture is None:
            raise web.HTTPBadRequest(text='Choose a listed architecture.')
        if not architecture['available']:
            raise web.HTTPServiceUnavailable(text='A required provider key is missing.')
        mode=r.query.get('memory_mode','discussion')
        if mode not in ('discussion','speech_feedback'):raise web.HTTPBadRequest(text='Unknown memory mode.')
        voice = next((v for v in VOICES if v['id'] == r.query.get('voice','mist')), None)
        if voice is None:
            raise web.HTTPBadRequest(text='Choose a listed voice.')
        architecture={**architecture,'memory_mode':mode, 'voice_id':voice['voice_id'],
                      'voice_name':voice['label'], 'voice_choice':voice['id']}
        if app['trial_health']['quarantined']:
            raise web.HTTPServiceUnavailable(text='A previous provider has not retired safely. Restart the local voice trial server.')
        if app['trial_owner'] is not None or app['sessions']['owner'] is not None:
            raise web.HTTPConflict(text='End the active conversation before starting another.')
        ws = web.WebSocketResponse(max_msg_size=16384, heartbeat=20)
        journal = BufferedJournal(store.start(architecture))
        token = ACTIVE_TRACE.set(journal)
        app['trial_closed'].clear()
        try:
            session = TrialConversation(app, ws, architecture, journal)
        except Exception as exc:
            journal.record({'type':'error', 'message':str(exc)[:240]})
            await asyncio.to_thread(journal.finish, 'failed', reason='construction_failed')
            ACTIVE_TRACE.reset(token)
            app['trial_closed'].set()
            raise
        owner = {'session':session, 'start':None, 'reason':'disconnected'}
        app['trial_owner'] = owner
        expiry_task = None
        status = 'ended'
        try:
            await ws.prepare(r)
            if remote:
                async def expire_session():
                    await asyncio.sleep(max(0, remote.expires - time.monotonic()))
                    if remote.authenticated(r):
                        remote.revoke()
                    owner['reason'] = 'session_expired'
                    await ws.close()
                expiry_task = asyncio.create_task(expire_session())
            await session.emit({'type':'studio_session', 'trace_id':journal.trace_id, 'architecture':architecture})
            await session.emit({'type':'state', 'state':'connecting'})
            async def start():
                try:
                    await session.start()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    owner['reason'] = 'startup_failed'
                    await session.emit({'type':'error', 'fatal':True, 'message':str(exc)[:240]})
                    await ws.close()
            owner['start'] = asyncio.create_task(start())
            async for message in ws:
                if message.type != web.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(message.data)
                    if not isinstance(data, dict):
                        raise ValueError('Expected an event object.')
                    if not session.started:
                        raise ValueError('Voice is still connecting.')
                    await session.handle(data)
                except (ValueError, TypeError, KeyError, RuntimeError) as exc:
                    await session.emit({'type':'error', 'message':str(exc)[:240]})
        except Exception as exc:
            status = 'failed'
            journal.record({'type':'error', 'message':str(exc)[:240]})
        finally:
            if expiry_task:
                expiry_task.cancel()
            async def retire():
                final_status = status
                try:
                    if owner['start']:
                        owner['start'].cancel()
                        await asyncio.gather(owner['start'], return_exceptions=True)
                    await session.close()
                    if not ws.closed:
                        await ws.close()
                except Exception as exc:
                    final_status = 'failed'
                    journal.record({'type':'close_error', 'message':str(exc)[:240]})
                    pending = getattr(session.voice, '_retirement_task', None)
                    if pending is not None:
                        journal.record({'type':'provider_retirement', 'phase':'waiting'})
                        try:
                            await asyncio.shield(pending)
                            journal.record({'type':'provider_retirement', 'phase':'finished'})
                        except Exception:
                            app['trial_health']['quarantined'] = True
                    else:
                        app['trial_health']['quarantined'] = True
                    if session.mask:
                        await session.mask.close()
                finally:
                    if owner['reason'] == 'startup_failed':
                        final_status = 'failed'
                    try:
                        await asyncio.to_thread(journal.finish, status=final_status, reason=owner['reason'])
                    except OSError:
                        app['trial_health']['quarantined'] = True
                    finally:
                        if app['trial_owner'] is owner:
                            app['trial_owner'] = None
                            app['trial_closed'].set()
            retirement = asyncio.create_task(retire())
            try:
                await asyncio.shield(retirement)
            except asyncio.CancelledError:
                await retirement
                raise
            finally:
                ACTIVE_TRACE.reset(token)
        return ws

    app.router.add_get('/try', page)
    app.router.add_get('/trial/session', remote_session)
    app.router.add_post('/trial/pair', remote_pair)
    app.router.add_post('/trial/logout', remote_logout)
    app.router.add_post('/trial/bootstrap', bootstrap)
    app.router.add_get('/trial/catalog', architectures)
    app.router.add_get('/trial/sessions', sessions)
    app.router.add_get('/trial/events', events)
    app.router.add_get('/trial/export', export)
    app.router.add_get('/trial/memory', saved_memory)
    app.router.add_get('/trial/audio', saved_audio)
    app.router.add_get('/voice-lab/', voice_lab)
    app.router.add_get('/voice-lab/audio/{filename}', voice_lab_audio)
    app.router.add_post('/trial/disconnect', disconnect)
    app.router.add_get('/trial/voice', websocket)

    async def lifecycle(app):
        logger = logging.getLogger('mist.voice')
        handler = TraceLogHandler()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        yield
        logger.removeHandler(handler)
        await asyncio.to_thread(store.close)

    async def shutdown(app):
        owner = app['trial_owner']
        if owner:
            owner['reason'] = 'server_shutdown'
            if owner['start']:
                owner['start'].cancel()
            await owner['session'].ws.close()
            await asyncio.wait_for(app['trial_closed'].wait(), 20)

    app.cleanup_ctx.append(lifecycle)
    app.on_shutdown.append(shutdown)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int)
    parser.add_argument('--run-dir', default=str(BRAIN / 'results/live-studio'))
    parser.add_argument('--remote-origin', help='Exact public HTTPS origin; enables authenticated remote mode.')
    parser.add_argument('--remote-pair-code-file', help='File containing a high-entropy remote pairing token.')
    args = parser.parse_args()
    if args.port is None:
        args.port = 9067 if args.remote_origin else 9053
    if args.remote_origin and Path(args.run_dir).resolve() == (BRAIN / 'results/live-studio').resolve():
        parser.error('Remote mode requires a separate --run-dir.')
    app = create_studio(args)
    print(f'Live voice tests: http://127.0.0.1:{args.port}/try', flush=True)
    web.run_app(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
