"""Real Luna conversations against isolated production application tools.

Run this before and after a prompt change. Each arc retains one model session and
one temporary RobotRuntime. Background work is an explicitly labelled stub.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(BRAIN / 'harness'))
from codex_client import CodexClient
from duplex.background import specs as background_specs
from duplex.phrasing import next_phrase
from duplex.runtime import RobotRuntime, specs as runtime_specs

FIXTURES = BRAIN / 'harness/luna_conversation_cases.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def choose_binary():
    if os.environ.get('MIST_CODEX_BINARY'):
        return
    root = Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI/Codex/bin'
    candidates = sorted(root.glob('*/codex.exe'), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        os.environ['MIST_CODEX_BINARY'] = str(candidates[0])


class RuntimeBridge:
    """Production bounded tools with temporary state and no actuator transport."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.receipts = []
        self.profile = 'none'
        self.sequence = 0
        self.job = None
        self.request_text = None

    def prepare(self, profile, request_text=None):
        self.profile = profile
        self.request_text = request_text
        self.receipts = []
        self.runtime.samples = {}

    def update_sensor_fixture(self):
        if self.profile == 'none':
            return
        self.sequence += 1
        now = time.time()
        if self.profile in ('fresh_battery', 'stale_battery'):
            kind, values = 'phone_battery', {'level': .62}
        elif self.profile == 'fresh_orientation':
            kind, values = 'phone_orientation', {'alpha': 5, 'beta': 35, 'gamma': 2}
        else:
            raise ValueError('Unknown sensor fixture')
        self.runtime.ingest({'kind': kind, 'seq': self.sequence, 'captured_at': now, 'values': values}, now=now)
        if self.profile == 'stale_battery':
            self.runtime.samples[kind]['captured_at'] = now - 30
            self.runtime.samples[kind]['received_at'] = now - 30

    def request(self, method, params, **kwargs):
        if method != 'call':
            raise ValueError('Only application tool calls are available')
        name, args = params.get('name'), params.get('arguments')
        started = time.perf_counter()
        error = False
        try:
            if name in ('start_background_task', 'background_task_status', 'cancel_background_task'):
                value = self.background(name, args)
            else:
                if name == 'get_sensor_snapshot':
                    self.update_sensor_fixture()
                value = self.runtime.call(name, args, request_text=self.request_text)
        except (ValueError, TypeError, KeyError) as exc:
            value, error = {'error': str(exc)}, True
        self.receipts.append({'name': name, 'arguments': args, 'result': value,
                              'is_error': error, 'execution_s': time.perf_counter() - started})
        return {'isError': error, 'result': {'content': [{'type': 'text', 'text': json.dumps(value)}]}}

    def background(self, name, args):
        if not isinstance(args, dict):
            raise ValueError('Expected an object')
        if name == 'start_background_task':
            question = args.get('question', '')
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1200:
                raise ValueError('Invalid background question')
            self.job = {'job_id': 'fixture-job', 'status': 'running', 'fixture': True,
                        'note': 'Evaluation stub only. No analyst is running and no result is available.'}
        elif self.job is None:
            return {'status': 'not_found', 'fixture': True}
        elif name == 'cancel_background_task':
            self.job['status'] = 'cancelled'
        return dict(self.job)

    def close(self):
        pass


class TimingCapture:
    def __init__(self):
        self.started = time.perf_counter()
        self.events = []
        self.text = ''
        self.first_phrase = None
        self.last_tool_end = None
        self.first_text_after_tool = None

    def __call__(self, event):
        elapsed = time.perf_counter() - self.started
        self.events.append({'at_s': elapsed, **event})
        kind = event.get('type')
        if kind == 'tool_execution_end':
            self.last_tool_end = elapsed
        elif kind == 'message_update':
            chunk = event.get('assistantMessageEvent', {}).get('delta', '')
            self.text += chunk
            if chunk.strip() and self.last_tool_end is not None and self.first_text_after_tool is None:
                self.first_text_after_tool = elapsed
            if self.first_phrase is None:
                boundary = next_phrase(self.text)
                if boundary:
                    end, reason = boundary
                    self.first_phrase = {'at_s': elapsed, 'text': self.text[:end], 'boundary': reason}


def structural_check(case, result, receipts):
    """Only objective contract checks; conversation quality needs transcript review."""
    failures = []
    calls = result.tool_calls
    names = [call.name for call in calls]
    word_count = len(re.findall(r"\b[\w]+(?:['’][\w]+)?\b", result.text))
    if result.errors:
        failures.append('backend_error')
    if any(call.is_error for call in calls):
        failures.append('tool_rejected')
    if len(calls) > case.get('max_tools', 2):
        failures.append('excess_tool_calls')
    if case.get('required_tool') and case['required_tool'] not in names:
        failures.append('required_tool_missing')
    if case.get('first_tool') and (not names or names[0] != case['first_tool']):
        failures.append('priority_tool_not_first')
    if set(names) & set(case.get('forbidden_tools', [])):
        failures.append('forbidden_tool')
    face_calls = [call for call in calls if call.name == 'set_expression']
    if len(face_calls) > 1:
        failures.append('expression_churn')
    for field in ('expression', 'variant', 'duration_ms'):
        if field in case and (len(face_calls) != 1 or face_calls[0].args.get(field) != case[field]):
            failures.append('requested_' + field + '_not_selected')
    if 'allowed_expressions' in case and any(call.args.get('expression') not in case['allowed_expressions'] for call in face_calls):
        failures.append('expression_outside_case_set')
    if 'saved_note' in case and not any(r['name'] == 'remember' and r['arguments'].get('note') == case['saved_note'] for r in receipts):
        failures.append('exact_preference_not_saved')
    if 'max_words' in case and word_count > case['max_words']:
        failures.append('requested_brevity_exceeded')
    if result.text.count('?') > case.get('max_questions', 1):
        failures.append('too_many_questions')
    if result.text.count('?') < case.get('min_questions', 0):
        failures.append('clarification_missing')
    for required in case.get('contains', []):
        if required.lower() not in result.text.lower():
            failures.append('expected_detail_missing:' + required)
    if '[[face:' in result.text or '<expression' in result.text:
        failures.append('spoken_metadata')
    return {'passed': not failures, 'failures': failures, 'word_count': word_count,
            'manual_quality_verdict': 'pending independent transcript review'}


def state_snapshot(runtime):
    return json.loads(json.dumps({'memory': runtime.memory, 'intents': runtime.intents,
                                  'estop': runtime.estop}))


def state_check(case, before, after):
    """Keep state protection separate from whether the model chose correctly."""
    requested = case.get('required_tool')
    allowed_changes = {'remember': {'memory'}, 'preview_motion': {'intents'},
                       'pan_phone': {'intents'}, 'stop_robot': {'intents', 'estop'}}.get(requested, set())
    changed = [field for field in before if before[field] != after[field]]
    unexpected = [field for field in changed if field not in allowed_changes]
    return {'state_protected': not unexpected, 'unexpected_changes': unexpected,
            'changed_fields': changed, 'allowed_change_fields': sorted(allowed_changes),
            'basis': 'Only the fixture explicitly requested these durable/action changes; face display is checked separately.'}


def run_arc(arc, args, persona):
    path = args.output / arc['id']
    path.mkdir(parents=True, exist_ok=True)
    result = {'id': arc['id'], 'style': arc['style'], 'split': arc['split'], 'turns': [], 'errors': []}
    client = None
    with tempfile.TemporaryDirectory(prefix='mist-luna-eval-') as temp:
        runtime = RobotRuntime(Path(temp))
        bridge = RuntimeBridge(runtime)
        try:
            client = CodexClient(model=args.model, thinking='low', service_tier=args.service_tier,
                                 tools=[], with_memory=False, system_prompt=persona,
                                 run_dir=path, max_output_tokens=180)
            client._specs = runtime_specs() + background_specs()
            client._selected = {spec['name'] for spec in client._specs}
            client._bridge = bridge
            client.new_session()
            result['backend'] = client.backend_info
            result['thread_id'] = client.thread_id
            for index, case in enumerate(arc['turns']):
                bridge.prepare(case.get('sensor_profile', 'none'), case['text'])
                before_state = state_snapshot(runtime)
                timing = TimingCapture()
                turn = client.ask(case['text'], timeout=args.timeout, on_event=timing)
                phrase = timing.first_phrase
                if phrase is None and turn.text.strip():
                    phrase = {'at_s': turn.total_s, 'text': turn.text, 'boundary': 'final_flush'}
                row = {'index': index + 1, 'input': case['text'], 'text': turn.text,
                       'tools': [asdict(call) for call in turn.tool_calls], 'receipts': list(bridge.receipts),
                       'errors': turn.errors, 'timings': turn.timings,
                       'first_text_s': turn.ttft_s, 'first_tool_s': turn.ttf_tool_s,
                       'first_phrase': phrase, 'first_text_after_tool_s': timing.first_text_after_tool,
                       'total_s': turn.total_s, 'input_tokens': turn.input_tokens,
                       'output_tokens': turn.output_tokens, 'cache_read_tokens': turn.cache_read_tokens,
                       'checks': structural_check(case, turn, bridge.receipts),
                       'state_before': before_state, 'state_after': state_snapshot(runtime)}
                row['application_invariants'] = state_check(case, before_state, row['state_after'])
                result['turns'].append(row)
                (path / f'turn-{index+1:02d}-events.json').write_text(json.dumps(timing.events, indent=2), encoding='utf-8')
                (path / 'transcript.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
                print(json.dumps({'arc': arc['id'], 'turn': index+1, 'checks': row['checks'],
                                  'first_text_s': row['first_text_s'], 'total_s': row['total_s']}), flush=True)
                if turn.errors:
                    result['errors'].append('A backend error stopped the arc; later turns were not executed.')
                    break
        except Exception as exc:
            result['errors'].append(type(exc).__name__ + ': ' + str(exc)[:600])
        finally:
            if client:
                client.close()
            result['final_memory'] = runtime.memory
    result['expected_turns'] = len(arc['turns'])
    result['completed_turns'] = len(result['turns'])
    result['structural_pass'] = (not result['errors'] and len(result['turns']) == len(arc['turns'])
                                 and all(t['checks']['passed'] for t in result['turns']))
    (path / 'transcript.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def distribution(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {'n': 0}
    return {'n': len(values), 'median_s': statistics.median(values),
            'p90_s': values[max(0, math.ceil(len(values)*.9)-1)], 'max_s': max(values),
            'p90_method': 'nearest rank'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='gpt-6-luna')
    parser.add_argument('--service-tier', default='default', choices=['default', 'priority'])
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=float, default=45)
    parser.add_argument('--split', default='baseline', choices=['baseline', 'heldout', 'regression', 'all'])
    parser.add_argument('--arcs', help='Optional comma-separated IDs within the selected split')
    parser.add_argument('--persona', type=Path, default=BRAIN / 'duplex/persona.txt')
    parser.add_argument('--output', type=Path, default=BRAIN / 'results/luna_conversations_20260922/baseline')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixtures_bytes = FIXTURES.read_bytes()
    fixtures = json.loads(fixtures_bytes)
    persona_bytes = args.persona.read_bytes()
    persona = persona_bytes.decode('utf-8').replace("Your voice is being converted to the owner's MIST voice.",
                    "Your replies are spoken using the owner's MIST voice through streaming text to speech.")
    # This is the same backend prompt as production; the direct text adapter adds
    # its normal output target and host-isolation instructions in new_session().
    arcs = [a for a in fixtures['arcs'] if args.split == 'all' or a['split'] == args.split]
    if args.arcs:
        selected = set(args.arcs.split(','))
        arcs = [a for a in arcs if a['id'] in selected]
        if selected != {a['id'] for a in arcs}:
            raise SystemExit('Unknown or excluded requested arc')
    choose_binary()
    metadata = {'model': args.model, 'reasoning': 'low', 'service_tier': args.service_tier,
                'fixture_sha256': digest(fixtures_bytes), 'persona_sha256': digest(persona_bytes),
                'runtime_sha256': digest((BRAIN/'duplex/runtime.py').read_bytes()),
                'specs_sha256': digest(json.dumps(runtime_specs()+background_specs(), sort_keys=True).encode()),
                'started_at': time.time(), 'expected_arcs': len(arcs),
                'expected_turns': sum(len(a['turns']) for a in arcs), 'rubric': fixtures['review_rubric'],
                'limits': ['No audio is generated; measured latency is text/backend latency only.',
                           'Each arc uses one persistent session and isolated real runtime state.',
                           'Only background analysis is stubbed, marked in every background receipt.',
                           'No host tools, hardware transports or production preferences are available.',
                           'Structural assertions do not establish natural conversation quality.'],
                'production_model_changed': False}
    metadata['request_text_guard_enabled'] = True
    metadata['state_checks'] = 'Durable preferences, motion intents and stop state are audited separately from strict model calls. A refused model call remains a strict model failure.'
    (args.output/'run_metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    (args.output/'fixtures_frozen.json').write_bytes(fixtures_bytes)
    (args.output/'persona_frozen.txt').write_bytes(persona_bytes)
    rows = []
    with ThreadPoolExecutor(max_workers=max(1, min(2, args.workers))) as executor:
        jobs = [executor.submit(run_arc, arc, args, persona) for arc in arcs]
        for job in as_completed(jobs):
            rows.append(job.result())
    order = {a['id']: i for i, a in enumerate(arcs)}
    rows.sort(key=lambda a: order[a['id']])
    turns = [turn for arc in rows for turn in arc['turns']]
    report = {**metadata, 'elapsed_s': time.time()-metadata['started_at'], 'arcs': rows,
              'completed_arcs': sum(a['completed_turns'] == a['expected_turns'] for a in rows),
              'completed_turns': len(turns), 'structural_passes': sum(t['checks']['passed'] for t in turns),
              'model_strict_passes': sum(t['checks']['passed'] for t in turns),
              'state_protected_turns': sum(t['application_invariants']['state_protected'] for t in turns),
              'all_state_protected': all(t['application_invariants']['state_protected'] for t in turns),
              'all_structural_passed': len(rows) == len(arcs) and all(a['structural_pass'] for a in rows),
              'latency': {'first_text': distribution(t['first_text_s'] for t in turns),
                          'first_phrase': distribution(t['first_phrase']['at_s'] for t in turns if t['first_phrase']),
                          'first_tool': distribution(t['first_tool_s'] for t in turns),
                          'completion': distribution(t['total_s'] for t in turns),
                          'no_tool_first_text': distribution(t['first_text_s'] for t in turns if not t['tools']),
                          'with_tool_first_text': distribution(t['first_text_s'] for t in turns if t['tools'])}}
    (args.output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('completed_turns','structural_passes','all_structural_passed','latency')}), flush=True)
    return 0 if report['all_structural_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
