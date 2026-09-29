"""Bounded, opt-in HTTPS smoke test of a paired remote MIST trial.

Only --run opens a network connection. The output directory is private: it
contains transcripts and received speech. This is a protocol test, not a
microphone, speaker, rendered-face, or physical-robot test.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import wave

import aiohttp


BRAIN = Path(__file__).resolve().parents[1]
FIXTURE_MANIFEST = BRAIN / 'deploy/fixtures/manifest.json'
FIXTURE_ID = 'mist.naturalness.v1.audio_phrase_seam'
STARTUP_LIMIT = 45
TURN_LIMIT = 45
HTTP_LIMIT = 10


def fixture_pcm():
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding='utf-8'))
    case = next(c for c in manifest['fixtures'] if c['id'] == FIXTURE_ID)
    step = next(s for s in case['steps'] if s['type'] == 'audio')
    name = step['file']
    assert Path(name).name == name
    expected = next(a['sha256'] for a in manifest['assets'] if a['file'] == name)
    path = FIXTURE_MANIFEST.parent / 'audio' / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'Speech fixture hash changed'
    with wave.open(str(path), 'rb') as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 16000)
        pcm = audio.readframes(audio.getnframes())
    return case['id'], name, pcm


def base_origin(raw):
    parsed = urlsplit(raw)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.port is not None or parsed.username or parsed.password or \
            parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('--base must be an HTTPS origin without path, query, or credentials')
    return raw.rstrip('/')


def public_event(event):
    """Keep useful evidence while excluding raw PCM and arbitrary provider payloads."""
    kind = event.get('type')
    fields = ('type', 'role', 'text', 'state', 'name', 'message', 'fatal', 'epoch',
              'seq', 'sample_rate', 'expression', 'variant', 'source', 'turn_id')
    row = {key: event[key] for key in fields if key in event}
    if kind == 'audio':
        row['pcm_bytes'] = len(base64.b64decode(event.get('pcm', ''), validate=True))
    if kind == 'tool':
        result = event.get('result') or {}
        row['result'] = {key: result[key] for key in ('status', 'expression', 'variant', 'action') if key in result}
    return row


class Attempt:
    def __init__(self, name, out):
        self.name, self.out = name, out
        self.events, self.audio, self.failures = [], bytearray(), []
        self.trace_id = None
        self.started = time.monotonic()
        self.audio_rate = None

    def record(self, event):
        if event.get('type') == 'studio_session':
            self.trace_id = event.get('trace_id')
        if event.get('type') == 'audio':
            rate = event.get('sample_rate')
            if self.audio_rate is not None and rate != self.audio_rate:
                self.failures.append('transport: audio sample rate changed')
            self.audio_rate = rate
            self.audio.extend(base64.b64decode(event.get('pcm', ''), validate=True))
        row = public_event(event)
        row['elapsed_ms'] = round((time.monotonic() - self.started) * 1000)
        self.events.append(row)
        if event.get('type') == 'error':
            message = str(event.get('message', 'error'))[:240]
            provider = event.get('fatal') or any(word in message.casefold() for word in
                                                   ('provider', 'recognition', 'text model', 'model turn', 'tts'))
            self.failures.append(('provider' if provider else 'application') + ': ' + message)

    def finish(self, checks):
        self.out.mkdir(parents=True, exist_ok=True)
        if self.audio:
            with wave.open(str(self.out / 'received.wav'), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(self.audio_rate or 24000)
                audio.writeframes(self.audio)
        report = {'case': self.name, 'trace_id': self.trace_id, 'checks': checks,
                  'failures': self.failures, 'received_pcm_bytes': len(self.audio), 'events': self.events}
        (self.out / 'attempt.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return {key: value for key, value in report.items() if key != 'events'}


async def receive_until(ws, attempt, predicate, seconds):
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('event deadline exceeded')
        message = await ws.receive(timeout=remaining)
        if message.type != aiohttp.WSMsgType.TEXT:
            raise ConnectionError(f'WebSocket closed during event wait ({message.type})')
        event = json.loads(message.data)
        attempt.record(event)
        if event.get('type') == 'error' and event.get('fatal'):
            raise RuntimeError(event.get('message', 'provider startup failed'))
        if predicate(event, attempt.events):
            return event


async def paced_input(ws, pcm):
    started = time.monotonic()
    for offset in range(0, len(pcm), 640):
        await asyncio.sleep(max(0, started + offset / 32000 - time.monotonic()))
        await ws.send_json({'type': 'mic', 'pcm': base64.b64encode(pcm[offset:offset + 640]).decode('ascii')})


def reply_text(rows, *, after=0):
    return ' '.join(str(e.get('text', '')).strip() for e in rows[after:]
                    if e.get('type') == 'transcript_done' and e.get('role') == 'assistant').strip()


def has_reply(rows, *, after=0, topic=()):
    text = reply_text(rows, after=after)
    return len(text) >= 12 and (not topic or any(re.search(r'\b' + re.escape(word) + r'\b', text, re.I)
                                                  for word in topic))


def plan_from_catalog(catalog, primary, alternate, pcm):
    for identifier in (primary, alternate):
        if identifier not in catalog or not catalog[identifier].get('available'):
            raise ValueError(f'Architecture {identifier!r} is absent or unavailable in remote catalog')
    if primary == alternate:
        raise ValueError('Primary and alternate architecture must differ')
    return [(primary, 'speech_input', 'audio', pcm),
            (primary, 'expression', 'expression', None),
            (primary, 'interrupt', 'interrupt', None),
            (alternate, 'architecture_switch', 'switch', None)]


async def check_boundary(http, base, origin):
    results = {}
    async with http.get(base + '/trial/session') as response:
        body = await response.json()
        results['session_unauthenticated'] = response.status == 200 and body.get('authenticated') is False
    async with http.get(base + '/trial/catalog') as response:
        results['catalog_unauthorized'] = response.status == 401
    async with http.post(base + '/trial/pair', json={'code': 'unused'},
                         headers={'Origin': 'https://wrong.example'}) as response:
        results['pair_bad_origin_denied'] = response.status == 403
    try:
        async with http.ws_connect(base + '/trial/voice?architecture=cerebras-balanced',
                                   headers={'Origin': origin}):
            results['anonymous_ws_denied'] = False
    except aiohttp.WSServerHandshakeError as error:
        results['anonymous_ws_denied'] = error.status == 401
    return results


async def one(http, base, origin, arch, name, out, kind, pcm=None):
    attempt = Attempt(name, out)
    checks = {'architecture': arch, 'ready': False, 'assistant_reply': False,
              'received_pcm': False, 'closed_cleanly': False, 'safe_to_continue': False}
    ws = None
    try:
        startup_started = time.monotonic()
        ws = await http.ws_connect(base + '/trial/voice?architecture=' + arch,
                                   headers={'Origin': origin}, timeout=STARTUP_LIMIT, heartbeat=20)
        await receive_until(ws, attempt, lambda e, _: e.get('type') == 'ready',
                            max(0.01, STARTUP_LIMIT - (time.monotonic() - startup_started)))
        checks['ready'] = True
        ping_started = time.monotonic()
        await ws.send_json({'type': 'ping'})
        await receive_until(ws, attempt, lambda e, _: e.get('type') == 'pong', 10)
        checks['ping_rtt_ms'] = round((time.monotonic() - ping_started) * 1000)
        if kind == 'audio':
            await asyncio.wait_for(paced_input(ws, pcm), 20)
            await receive_until(ws, attempt, lambda e, rows: has_reply(rows, topic=('screw', 'washer', 'bracket'))
                                and bool(attempt.audio), TURN_LIMIT)
            checks['user_recognized'] = any(e.get('type') == 'transcript_done' and e.get('role') == 'user'
                                            and any(word in str(e.get('text', '')).casefold()
                                                    for word in ('screw', 'washer', 'bracket'))
                                            for e in attempt.events)
        elif kind == 'expression':
            await ws.send_json({'type': 'text', 'text': 'Please set a sad expression, then say a short calm goodnight.'})
            await receive_until(ws, attempt, lambda e, rows: has_reply(rows) and bool(attempt.audio), TURN_LIMIT)
            checks['expression_tool_state'] = any(e.get('type') == 'tool' and e.get('name') == 'set_expression'
                                                  for e in attempt.events)
        elif kind == 'interrupt':
            await ws.send_json({'type': 'text', 'text': 'Explain the screw, washer, and bracket in three short sentences.'})
            await receive_until(ws, attempt, lambda e, _: e.get('type') in ('audio', 'transcript_delta') and
                                (e.get('type') == 'audio' or e.get('role') == 'assistant'), TURN_LIMIT)
            await ws.send_json({'type': 'barge_in'})
            await receive_until(ws, attempt, lambda e, _: e.get('type') == 'audio_reset', 10)
            marker = len(attempt.events)
            await ws.send_json({'type': 'text', 'text': 'New request: say one brief sentence about a blue bracket.'})
            await receive_until(ws, attempt, lambda e, rows: has_reply(rows, after=marker, topic=('bracket',)) and
                                any(row.get('type') == 'audio' for row in rows[marker:]), TURN_LIMIT)
            checks['fresh_reply_after_interrupt'] = True
        else:
            await ws.send_json({'type': 'text', 'text': 'Say one brief sentence about a blue bracket.'})
            await receive_until(ws, attempt, lambda e, rows: has_reply(rows, topic=('bracket',)) and
                                bool(attempt.audio), TURN_LIMIT)
        topic = ('screw', 'washer', 'bracket') if kind == 'audio' else ('bracket',) if kind in ('interrupt', 'switch') else ()
        checks['assistant_reply'] = has_reply(attempt.events, topic=topic)
        checks['received_pcm'] = len(attempt.audio) >= 2 and len(attempt.audio) % 2 == 0
    except Exception as error:
        category = 'transport' if isinstance(error, (TimeoutError, ConnectionError, aiohttp.ClientError)) else 'application_or_provider'
        attempt.failures.append(f'{category}: {type(error).__name__}: {str(error)[:240]}')
    finally:
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), 10)
                checks['websocket_closed'] = ws.closed
            except Exception as error:
                attempt.failures.append(f'transport_cleanup: {type(error).__name__}')
        # The server retires provider tasks after WebSocket close. This endpoint
        # also prevents a new architecture from racing that retirement.
        try:
            async with http.post(base + '/trial/disconnect', json={}, headers={'Origin': origin},
                                 timeout=aiohttp.ClientTimeout(total=20)) as response:
                if response.status != 200:
                    attempt.failures.append(f'cleanup: disconnect HTTP {response.status}')
                else:
                    checks['disconnect_succeeded'] = True
        except Exception as error:
            attempt.failures.append(f'transport_cleanup: {type(error).__name__}')
        if attempt.trace_id:
            try:
                async with http.get(base + '/trial/export', params={'session': attempt.trace_id},
                                    timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        exported = await response.json()
                        checks['server_status'] = exported.get('session', {}).get('status')
                        checks['closed_cleanly'] = checks.get('websocket_closed') is True and \
                            checks['server_status'] == 'ended'
                    else:
                        attempt.failures.append(f'cleanup: export HTTP {response.status}')
            except Exception as error:
                attempt.failures.append(f'transport_cleanup: export {type(error).__name__}')
        checks['safe_to_continue'] = checks.get('disconnect_succeeded') is True and \
            (ws is None or checks.get('websocket_closed') is True) and \
            (attempt.trace_id is None or checks.get('server_status') in ('ended', 'failed'))
    return attempt.finish(checks)


async def run(args):
    base = base_origin(args.base)
    out = Path(args.out).resolve()
    if not args.run:
        fixture_id, name, pcm = fixture_pcm()
        print(json.dumps({'network_calls': 0, 'base': base, 'out': str(out),
                          'fixture_id': fixture_id, 'fixture_file': name, 'pcm_bytes': len(pcm),
                          'primary_architecture': args.architecture, 'alternate_architecture': args.alternate,
                          'planned_conversations': 4}, indent=2))
        return 0
    if not args.pair_code_file:
        raise ValueError('--pair-code-file is required with --run')
    code = Path(args.pair_code_file).read_text(encoding='ascii').strip()
    if len(code) < 32:
        raise ValueError('Pair code file must contain a high-entropy code')
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        out.chmod(0o700)
    fixture_id, name, pcm = fixture_pcm()
    summary = {'base': base, 'fixture_id': fixture_id, 'fixture_file': name, 'boundary': {}, 'attempts': []}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=HTTP_LIMIT),
                                         cookie_jar=aiohttp.CookieJar()) as http:
            summary['boundary'] = await check_boundary(http, base, base)
            if not all(summary['boundary'].values()):
                raise RuntimeError('Remote authentication boundary check failed; provider calls skipped')
            async with http.post(base + '/trial/pair', json={'code': code}, headers={'Origin': base}) as response:
                if response.status != 200:
                    raise RuntimeError(f'Pairing failed: HTTP {response.status}')
            async with http.get(base + '/trial/session') as response:
                if response.status != 200 or not (await response.json()).get('authenticated'):
                    raise RuntimeError('Paired session cookie was not accepted')
            async with http.post(base + '/trial/bootstrap', json={}, headers={'Origin': base}) as response:
                if response.status != 200:
                    raise RuntimeError(f'Paired bootstrap failed: HTTP {response.status}')
            async with http.get(base + '/trial/catalog') as response:
                if response.status != 200:
                    raise RuntimeError(f'Catalog failed: HTTP {response.status}')
                catalog = {item['id']: item for item in (await response.json())['architectures']}
            plan = plan_from_catalog(catalog, args.architecture, args.alternate, pcm)
            for index, (arch, name, kind, input_pcm) in enumerate(plan, 1):
                result = await one(http, base, base, arch, name, out / f'{index:02d}_{name}', kind, input_pcm)
                summary['attempts'].append(result)
                (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
                if not result['checks']['safe_to_continue']:
                    summary['stopped_after'] = name
                    summary['stop_reason'] = 'Provider retirement or session cleanup was not confirmed; server may be quarantined.'
                    break
    except Exception as error:
        summary['setup_failure'] = f'{type(error).__name__}: {str(error)[:240]}'
    (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    required = ('ready', 'assistant_reply', 'received_pcm', 'closed_cleanly')
    return 0 if not summary.get('setup_failure') and all(summary['boundary'].values()) and \
        len(summary['attempts']) == 4 and all(not a['failures'] and
        all(a['checks'].get(key) is True for key in required) and
        (a['case'] != 'speech_input' or a['checks'].get('user_recognized') is True) and
        (a['case'] != 'expression' or a['checks'].get('expression_tool_state') is True) and
        (a['case'] != 'interrupt' or a['checks'].get('fresh_reply_after_interrupt') is True)
        for a in summary['attempts']) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='Exact public HTTPS origin')
    parser.add_argument('--pair-code-file', help='Private local path; never printed or stored in results')
    parser.add_argument('--out', required=True, help='Private result directory')
    parser.add_argument('--architecture', default='qwen-affect', help='Primary architecture ID from /trial/catalog')
    parser.add_argument('--alternate', default='qwen-low', help='Second architecture ID from /trial/catalog')
    parser.add_argument('--run', action='store_true', help='Actually pair and make up to four provider conversations')
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == '__main__':
    main()
