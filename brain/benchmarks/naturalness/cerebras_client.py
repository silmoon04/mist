"""Cerebras streaming text/tool adapter; application tools remain the only actions."""
from __future__ import annotations

import json
import copy
import os
from pathlib import Path
import sys
import time
import uuid

import httpx
import jsonschema

BRAIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BRAIN / 'harness'))
from codex_client import CodexTurnResult
from pi_client import ToolCall


MODEL_OPTIONS = {
    'gpt-oss-120b': {'reasoning_efforts': ('low', 'medium', 'high'),
                    'reasoning_format': 'hidden', 'provider_default_effort': 'medium'},
    'qwen-3.8-27b': {'reasoning_efforts': ('none', 'low', 'medium', 'high'),
                    'reasoning_format': 'parsed', 'provider_default_effort': 'high'},
}


def request_controls(model, thinking):
    """Use Cerebras's model-specific API contract, verified on 2026-09-24."""
    options = MODEL_OPTIONS.get(model)
    if options is None:
        raise ValueError('Unsupported Cerebras model: ' + str(model))
    if thinking not in options['reasoning_efforts']:
        raise ValueError('Unsupported reasoning effort for ' + model + ': ' + str(thinking))
    controls = {'reasoning_effort': thinking, 'reasoning_format': options['reasoning_format']}
    if model == 'qwen-3.8-27b':
        # We never retain or replay private reasoning in application history.
        controls['clear_thinking'] = True
    return controls


def api_key():
    key = os.environ.get('CEREBRAS_API_KEY', '').strip()
    if not key:
        for line in (BRAIN.parent / '.env').read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name, value = line.split('=', 1)
                if name.strip() == 'CEREBRAS_API_KEY':
                    key = value.strip().strip('"').strip("'")
                    break
    if not key:
        raise RuntimeError('CEREBRAS_API_KEY is unavailable')
    return key


def collect_delta(calls, delta):
    """Reassemble indexed streamed tool arguments before any tool executes."""
    for part in delta.get('tool_calls') or []:
        index = part.get('index')
        if type(index) is not int or index < 0 or index >= 16:
            raise ValueError('Invalid streamed tool index')
        call = calls.setdefault(index, {'id': '', 'name': '', 'arguments': ''})
        if part.get('id'):
            if call['id'] and call['id'] != part['id']:
                raise ValueError('Conflicting tool call ID')
            call['id'] = part['id']
        function = part.get('function') or {}
        for field in ('name', 'arguments'):
            value = function.get(field, '')
            if not isinstance(value, str):
                raise ValueError('Non-text streamed function field')
            call[field] += value
        if len(call['arguments']) > 16384 or len(call['name']) > 128:
            raise ValueError('Streamed tool exceeded bounds')


class CerebrasClient:
    def __init__(self, model='gpt-oss-120b', thinking='low', system_prompt='',
                 max_output_tokens=1024, run_dir=None, http_client=None,
                 parallel_tool_calls=False, connect_retries=0, response_format=None, **unused):
        if type(parallel_tool_calls) is not bool:
            raise ValueError('parallel_tool_calls must be a boolean')
        if type(connect_retries) is not int or connect_retries not in (0, 1):
            raise ValueError('connect_retries must be 0 or 1')
        controls = request_controls(model, thinking)
        if response_format is not None:
            if not isinstance(response_format, dict) or response_format.get('type') not in ('json_object', 'json_schema'):
                raise ValueError('response_format must be a JSON object or schema configuration')
            if response_format['type'] == 'json_schema':
                spec = response_format.get('json_schema')
                if not isinstance(spec, dict) or not isinstance(spec.get('name'), str) or not isinstance(spec.get('schema'), dict):
                    raise ValueError('response_format json_schema requires name and schema')
                jsonschema.Draft202012Validator.check_schema(spec['schema'])
        self.model = model
        self.thinking = thinking
        self.prompt = system_prompt
        self.max_output_tokens = max_output_tokens
        self.response_format = copy.deepcopy(response_format)
        # This batches model selections; the validated actions still run in order.
        self.parallel_tool_calls = parallel_tool_calls
        self.connect_retries = connect_retries
        self._specs = []
        self._selected = set()
        self._bridge = None
        self.thread_id = None
        self.messages = []
        self.needs_reset = False
        self.http = http_client or httpx.Client(
            base_url='https://api.cerebras.ai/v1',
            headers={'Authorization': 'Bearer ' + api_key()}, timeout=30,
            follow_redirects=False,
            transport=httpx.HTTPTransport(retries=1) if connect_retries else None)
        self.backend_info = {'provider': 'cerebras', 'model': model, 'streaming': True,
                             'reasoning_format': controls['reasoning_format'], 'reasoning_effort': thinking,
                             'request_controls': dict(controls),
                             'supported_reasoning_efforts': list(MODEL_OPTIONS[model]['reasoning_efforts']),
                             'provider_default_effort': MODEL_OPTIONS[model]['provider_default_effort'],
                             'private_reasoning_policy': 'Discard separate reasoning fields; do not emit, save or replay them.',
                             'effort_mapping': 'Cerebras high selects Qwen native xhigh' if model == 'qwen-3.8-27b' else 'Provider low/medium/high',
                             'max_completion_tokens': max_output_tokens,
                             'retry_policy': ('one transport connection retry for ConnectError/ConnectTimeout only; '
                                              'no status, read, write or tool retry' if connect_retries else
                                              'none; rate limits and failures are retained'),
                             'connect_retries_configured': connect_retries,
                             'tool_execution': 'local production allowlist, sequential',
                             'parallel_tool_calls': parallel_tool_calls,
                             'tool_limits': {'per_turn': 16, 'model_rounds': 9},
                             'history': 'explicit messages including tool receipts, no hidden reasoning replay',
                             'startup_s': 0, 'service_tier': 'provider_default',
                             'deadline_policy': 'Cooperative turn deadline; HTTP reads bounded to at most 10 seconds. An in-flight local tool is not forcibly cancelled.',
                             'partial_failure_policy': 'Retain provisional output and receipts; block follow-up until a new session.',
                             'limits': 'Completion cap includes reasoning. No native voice, TTS or physical tools.'}

    def new_session(self):
        self.messages = [{'role': 'system', 'content': self.prompt}]
        self.thread_id = 'cerebras-local-' + uuid.uuid4().hex
        self.needs_reset = False

    def ask(self, prompt, timeout=45, on_event=None):
        start = time.perf_counter()
        deadline = start + timeout
        result = CodexTurnResult(prompt=prompt)
        result.timings = {'tools': [], 'http_requests': [], 'client_lock_wait_s': 0,
                          'reasoning_output_tokens': 0, 'streamed_visible_text': '',
                          'deadline_overrun_s': 0}
        if self.needs_reset:
            result.errors.append('Session requires reset after an incomplete turn; no request sent')
            result.total_s = time.perf_counter()-start
            return result
        self.messages.append({'role': 'user', 'content': prompt})
        def emit(event):
            if on_event:
                on_event(event)
        specs = {s['name']: s for s in self._specs}
        tools = [{'type': 'function', 'function': {'name': s['name'], 'description': s['description'],
                  'parameters': s['inputSchema'], 'strict': False}} for s in self._specs]
        try:
            for round_index in range(9):
                remaining = deadline-time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError('Turn deadline exceeded')
                body = {'model': self.model, 'messages': self.messages, 'stream': True,
                        'max_completion_tokens': self.max_output_tokens,
                        **request_controls(self.model, self.thinking)}
                if self.response_format is not None:
                    body['response_format'] = copy.deepcopy(self.response_format)
                if tools:
                    body.update(tools=tools, tool_choice='auto', parallel_tool_calls=self.parallel_tool_calls)
                text, calls, finish, done = '', {}, None, False
                request_start = time.perf_counter()
                with self.http.stream('POST', '/chat/completions', json=body,
                                      timeout=httpx.Timeout(max(.01, min(10, remaining)), connect=max(.01, min(10, remaining)))) as response:
                    request = {'round': round_index, 'status': response.status_code,
                               'connect_retries_configured': self.connect_retries,
                               'headers_ms': (time.perf_counter()-request_start)*1000}
                    result.timings['http_requests'].append(request)
                    if response.status_code != 200:
                        raise RuntimeError('Cerebras HTTP ' + str(response.status_code))
                    for line in response.iter_lines():
                        if time.perf_counter() > deadline:
                            raise TimeoutError('Turn deadline exceeded')
                        if not line.startswith('data:'):
                            continue
                        payload = line[5:].strip()
                        if payload == '[DONE]':
                            done = True
                            break
                        packet = json.loads(payload)
                        if packet.get('error'):
                            raise RuntimeError('Cerebras stream reported an error')
                        usage = packet.get('usage')
                        if usage:
                            request['usage'] = usage
                        for choice in packet.get('choices', []):
                            delta = choice.get('delta', {})
                            # Hidden reasoning is never sent to speech, UI, history or diagnostics.
                            content = delta.get('content') or ''
                            if not isinstance(content, str):
                                raise ValueError('Invalid content delta')
                            if content:
                                text += content
                                result.timings['streamed_visible_text'] += content
                                if content.strip() and result.ttft_s is None:
                                    result.ttft_s = time.perf_counter()-start
                                    result.ttf_any_s = result.ttft_s if result.ttf_any_s is None else result.ttf_any_s
                                emit({'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': content}})
                            collect_delta(calls, delta)
                            if choice.get('finish_reason'):
                                finish = choice['finish_reason']
                    request['total_ms'] = (time.perf_counter()-request_start)*1000
                    request['selected_tool_calls'] = len(calls)
                usage = request.get('usage', {})
                result.input_tokens += usage.get('prompt_tokens', 0)
                result.output_tokens += usage.get('completion_tokens', 0)
                result.cache_read_tokens += usage.get('prompt_tokens_details', {}).get('cached_tokens', 0)
                result.timings['reasoning_output_tokens'] += usage.get('completion_tokens_details', {}).get('reasoning_tokens', 0)
                if not done or finish not in ('stop', 'tool_calls'):
                    raise RuntimeError('Incomplete response: ' + str(finish))
                result.turns += 1
                result.text += text
                if calls:
                    if finish != 'tool_calls':
                        raise RuntimeError('Tool stream has no tool completion boundary')
                    ordered = [calls[i] for i in sorted(calls)]
                    if len(result.tool_calls) + len(ordered) > 16:
                        raise RuntimeError('Tool budget exceeded')
                    if len({c['id'] for c in ordered}) != len(ordered) or any(not c['id'] for c in ordered):
                        raise ValueError('Missing or duplicate tool call identity')
                    self.messages.append({'role': 'assistant', 'content': text or None,
                        'tool_calls': [{'id': c['id'], 'type': 'function', 'function': {'name': c['name'], 'arguments': c['arguments']}} for c in ordered]})
                    for call in ordered:
                        name = call['name']
                        at = time.perf_counter()-start
                        result.ttf_tool_s = at if result.ttf_tool_s is None else result.ttf_tool_s
                        result.ttf_any_s = at if result.ttf_any_s is None else result.ttf_any_s
                        args = None
                        emit({'type': 'tool_execution_start', 'toolName': name, 'toolCallId': call['id'], 'args': None})
                        try:
                            args = json.loads(call['arguments'])
                            if name not in self._selected or name not in specs:
                                raise ValueError('Tool not on application allowlist')
                            jsonschema.validate(args, specs[name]['inputSchema'])
                            if time.perf_counter() > deadline:
                                raise TimeoutError('Turn deadline exceeded before action')
                            receipt = self._bridge.request('call', {'name': name, 'arguments': args})
                        except (ValueError, TypeError, jsonschema.ValidationError):
                            receipt = {'isError': True, 'result': {'content': [{'type': 'text', 'text': 'Tool rejected: invalid arguments or unlisted tool.'}]}}
                        error = bool(receipt.get('isError'))
                        result.tool_calls.append(ToolCall(name=name, args=args, t_offset_s=at, is_error=error))
                        content = receipt.get('result', {})
                        self.messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(content)})
                        elapsed = time.perf_counter()-start
                        result.timings['tools'].append({'name': name, 'request_s': at, 'reply_s': elapsed, 'execution_s': elapsed-at})
                        emit({'type': 'tool_execution_end', 'toolName': name, 'toolCallId': call['id'], 'args': args,
                              'result': content, 'isError': error})
                        if time.perf_counter() > deadline:
                            raise TimeoutError('Turn deadline exceeded after action; receipt retained')
                else:
                    if finish != 'stop' or not text.strip():
                        raise RuntimeError('No completed visible answer')
                    self.messages.append({'role': 'assistant', 'content': text})
                    break
            else:
                raise RuntimeError('Tool round limit reached')
        except (httpx.HTTPError, TimeoutError) as exc:
            result.errors.append('Cerebras timeout or transport failure: ' + type(exc).__name__)
        except (ValueError, RuntimeError, jsonschema.SchemaError) as exc:
            result.errors.append(str(exc)[:200])
        result.total_s = time.perf_counter()-start
        result.timings['deadline_overrun_s'] = max(0, result.total_s-timeout)
        if result.errors:
            self.needs_reset = True
        return result

    def close(self):
        self.http.close()
