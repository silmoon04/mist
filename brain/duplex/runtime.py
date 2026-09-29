"""Fresh sensor snapshots and an explicit, non-actuating R5 intent boundary."""
import hashlib
import json
import math
import time
from pathlib import Path
from duplex.expression_policy import EXPRESSIONS, normalize_expression, expression_parameters
from duplex.expression_requests import validate_expression_request
from duplex.memory_requests import validate_memory_request
from duplex.motion_requests import validate_motion_request

BRAIN=Path(__file__).resolve().parents[1]
MANIFEST=BRAIN.parent/'design/hexapod_phone_quad_r5_20260912/cad/output/assembly_manifest.json'

def finite(value,low,high):
    if type(value) not in (int,float):raise ValueError('Expected a finite numeric value')
    try:value=float(value)
    except (ValueError,OverflowError):raise ValueError('Expected a finite numeric value') from None
    if not math.isfinite(value) or not low<=value<=high:raise ValueError('Value outside accepted range')
    return value

class RobotRuntime:
    def __init__(self,run_dir):
        self.run_dir=Path(run_dir);self.run_dir.mkdir(parents=True,exist_ok=True)
        self.samples={};self.sequence={};self.estop=False;self.intents=[]
        self.manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
        self.manifest_hash=hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
        self.memory_path=self.run_dir/'preferences.json'
        try:self.memory=json.loads(self.memory_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.memory=[]

    def ingest(self,packet,now=None):
        now=time.time() if now is None else now
        if not isinstance(packet,dict):raise ValueError('Sensor packet must be an object')
        kind=packet.get('kind');seq=packet.get('seq');captured=finite(packet.get('captured_at'),0,now+2)
        if kind not in ('phone_motion','phone_orientation','phone_battery','phone_accelerometer','phone_gyroscope','phone_magnetometer','robot_telemetry'):raise ValueError('Unknown sensor kind')
        if type(seq) is not int or seq<0 or seq<=self.sequence.get(kind,-1):raise ValueError('Out-of-order sensor packet')
        if now-captured>5:raise ValueError('Sensor packet is stale')
        values=packet.get('values')
        if not isinstance(values,dict):raise ValueError('Sensor values must be an object')
        allowed={'phone_motion':{'ax':100,'ay':100,'az':100,'gx':2000,'gy':2000,'gz':2000},
                 'phone_orientation':{'alpha':360,'beta':180,'gamma':180},
                 'phone_battery':{'level':1},
                 'phone_accelerometer':{'x':100,'y':100,'z':100},
                 'phone_gyroscope':{'x':35,'y':35,'z':35},
                 'phone_magnetometer':{'x':2000,'y':2000,'z':2000},
                 'robot_telemetry':{'battery_v':20,'servo_temp_c':150,'max_current_a':50,'pan_deg':180}}
        clean={}
        for key,limit in allowed[kind].items():
            if key in values:clean[key]=finite(values[key],0 if key in ('level','battery_v','max_current_a') else -limit,limit)
        if not clean:raise ValueError('No accepted sensor values')
        self.sequence[kind]=seq
        self.samples[kind]={'values':clean,'captured_at':captured,'received_at':now,
            'source':'paired_phone_report' if kind.startswith('phone_') else 'paired_client_telemetry_report',
            'coordinate_frame':'phone_device' if kind.startswith('phone_') else 'reported_robot',
            'units':{'phone_motion':'acceleration m/s2; angular rate degrees/s',
                'phone_accelerometer':'m/s2','phone_gyroscope':'rad/s','phone_magnetometer':'microtesla',
                'phone_orientation':'degrees','phone_battery':'fraction','robot_telemetry':'volts, Celsius, amperes, degrees'}[kind],
            'authenticated_hardware':False}
        return self.snapshot(now)

    def snapshot(self,now=None):
        now=time.time() if now is None else now
        return {'hardware_connected':False,'motion_mode':'preview_only','emergency_stop':self.estop,
            'body':'R5 quadruped, four legs and phone pan, thirteen joints, no wheels',
            'manifest_sha256':self.manifest_hash,'camera':'not_connected',
            'sensors':{k:dict(v,age_s=round(max(0,now-v['captured_at']),3),fresh=0<=now-v['captured_at']<=2)
                for k,v in self.samples.items()},
            'limits':'Phone orientation is not robot body attitude. Telemetry is client-reported, not a validated L0 connection.'}

    def call(self,name,args,*,request_text=None):
        if not isinstance(args,dict):raise ValueError('Tool arguments must be an object')
        if name=='get_sensor_snapshot':return self.snapshot()
        if name=='stop_robot':
            self.estop=True;self.intents.clear()
            return {'status':'preview_stopped','hardware_stop_confirmed':False,'latched':True}
        if name=='set_expression':
            if request_text is not None:validate_expression_request(request_text,args)
            return {'status':'display_requested',**normalize_expression(args)}
        if name=='remember':
            if request_text is not None:validate_memory_request(request_text)
            note=args.get('note','')
            if not isinstance(note,str) or not 1<=len(note.strip())<=240:raise ValueError('Preference must be 1 to 240 characters')
            self.memory=(self.memory+[{'text':note.strip(),'source':'user_report','saved_at':time.time()}])[-60:]
            self.memory_path.write_text(json.dumps(self.memory,indent=2),encoding='utf-8')
            return {'status':'saved','note':note.strip()}
        if name=='recall':return {'notes':self.memory[-12:],'source':'saved_user_reports'}
        if name not in ('preview_motion','pan_phone'):raise ValueError('Tool not allowed')
        if request_text is not None:validate_motion_request(request_text,'phone_pan' if name=='pan_phone' else args.get('action'))
        if self.estop:return {'status':'refused','reason':'Preview stop is latched. Resume explicitly in the interface.'}
        fresh=self.snapshot()['sensors'].get('robot_telemetry',{})
        if fresh.get('fresh') and fresh['values'].get('servo_temp_c',0)>=50:
            return {'status':'refused','reason':'Reported servo temperature is at least 50 C'}
        if name=='pan_phone':
            if args.get('usb_connected') is not False:return {'status':'refused','reason':'Confirm USB disconnected before pan preview'}
            angle=finite(args.get('angle_deg'),-180,180)
            intent={'action':'phone_pan','angle_rad':math.radians(angle),'servo_id':13}
        else:
            action=args.get('action')
            if action not in ('walk','turn','stand','sit'):raise ValueError('Only walk, turn, stand and sit previews exist')
            intent={'action':action}
            if action=='walk':
                try:intent['distance_m']=finite(args.get('amount'),-.25,.25)
                except ValueError:raise ValueError('Walk amount must be in metres from -0.25 to 0.25; 10 centimetres is 0.1 metres.') from None
            if action=='turn':
                try:intent['angle_deg']=finite(args.get('amount'),-19,19)
                except ValueError:raise ValueError('Turn amount must be in degrees from -19 to 19.') from None
        intent.update(status='preview_only',hardware_executed=False,
            reason='Prepared for display only. Calibrated motor transport is absent; unplugging phone USB does not change controller availability.',
            expires_at=time.time()+2,manifest_sha256=self.manifest_hash)
        self.intents=(self.intents+[intent])[-20:]
        return intent

def specs():
    def tool(name,desc,props,required=()):
        return {'type':'function','name':name,'description':desc,'inputSchema':{'type':'object','properties':props,'required':list(required),'additionalProperties':False}}
    return [tool('get_sensor_snapshot','Read current reported sensors and age. Missing or stale sensors are unavailable.',{}),
        tool('preview_motion','Prepare a bounded robot preview only for an explicit movement or posture request. Conversational company such as sit here with me needs no movement tool. No physical motion or servo packets.',{'action':{'type':'string','enum':['walk','turn','stand','sit']},'amount':{'type':'number','description':'For walk: metres from -0.25 to 0.25 (10 centimetres = 0.1). For turn: degrees from -19 to 19. Omit for stand or sit.'}},['action']),
        tool('pan_phone','Preview a phone angle in degrees. No physical motor connection.',{'angle_deg':{'type':'number','minimum':-180,'maximum':180},'usb_connected':{'type':'boolean'}},['angle_deg','usb_connected']),
        tool('stop_robot','Immediately clear and latch the preview command path. No hardware stop acknowledgement.',{}),
        tool('set_expression','Change the face to one available preset, retained until changed. Use duration_ms only for an explicitly timed flash. When a requested preset or variant is unavailable, make no call; an uncertain face is still a change. Omit optional fields unless requested. Listening, thinking and speaking are handled by the interface. Do not call per word or audio chunk.',expression_parameters(),['expression']),
        tool('remember','Save a non-sensitive preference only when explicitly asked to remember or save it for future conversations. Do not save casual facts or temporary guidance such as for now, stop joking, or keep it gentle. Current conversation context already retains those. Never store secrets or guesses.',{'note':{'type':'string','maxLength':240,'description':'The preference as requested, without added conditions. If asked to save exact words, copy them verbatim, including punctuation.'}},['note']),
        tool('recall','Read saved user preferences as fallible reports.',{})]
