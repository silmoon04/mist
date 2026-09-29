"""Rerun MIST's conversation regressions locally; no provider requests."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
TESTS = [
    'brain.harness.test_live_studio', 'brain.harness.test_live_studio_remote',
    'brain.harness.test_affect_studio', 'brain.harness.test_affect_controller',
    'brain.harness.test_background', 'brain.harness.test_codex_client',
    'brain.harness.test_session_voice_options', 'brain.harness.test_session_memory',
    'brain.harness.test_memory_requests', 'brain.harness.test_streaming_tts',
    'brain.harness.test_tts_expressive_protocol_20260929', 'brain.harness.test_lipsync',
    'brain.harness.test_phrasing',
    'brain.benchmarks.naturalness.test_streaming_voice_20260930',
    'brain.benchmarks.naturalness.robust_conversation_20260929.test_runner',
]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = args.output or ROOT/'brain/results/quality-gates'/f'{stamp}-{uuid.uuid4().hex[:6]}'
    out.mkdir(parents=True, exist_ok=False)
    commands = [('python-regressions', [sys.executable, '-B', '-m', 'unittest', *TESTS, '-q']),
                ('turn-gap-sweep', [sys.executable, '-B', 'brain/benchmarks/naturalness/turn_gap_benchmark_20260929.py', '--output', str(out/'turn-gap')])]
    node = shutil.which('node')
    if node:
        commands += [('animation-grid', [node, '--test', 'brain/duplex/static/animation_picker.test.mjs']),
                     ('playback', [node, '--test', 'brain/harness/test_duplex_playback.mjs'])]
    else:
        commands += [('animation-grid', None), ('playback', None)]
    report = {'created_utc':stamp, 'scope':'Offline control-flow, storage, protocol and rendering contracts. No audio quality or live model score.',
              'source_hashes':{}, 'checks':[], 'human_naturalness':None}
    for relative in ['brain/duplex/live_studio.py','brain/duplex/tts.py','brain/duplex/background.py',
                     'brain/duplex/session_memory.py','brain/duplex/conversation_policy.py',
                     'brain/benchmarks/naturalness/streaming_voice_20260930.py',
                     'brain/benchmarks/naturalness/robust_conversation_20260929/cases.json']:
        report['source_hashes'][relative] = hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
    for name, command in commands:
        started = time.monotonic()
        if command is None:
            row = {'name':name,'status':'unsupported','reason':'Node runtime missing'}
        else:
            try:
                completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                    encoding='utf-8', errors='replace', timeout=180,
                    env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
                (out/f'{name}.log').write_text(completed.stdout+completed.stderr, encoding='utf-8')
                row = {'name':name,'status':'passed' if completed.returncode==0 else 'failed',
                       'exit_code':completed.returncode,'elapsed_s':time.monotonic()-started}
            except subprocess.TimeoutExpired:
                row = {'name':name,'status':'timeout','elapsed_s':time.monotonic()-started}
        report['checks'].append(row)
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(row),flush=True)
    print(str(out))
    return 0 if all(item['status']=='passed' for item in report['checks']) else 1

if __name__ == '__main__':
    raise SystemExit(main())
