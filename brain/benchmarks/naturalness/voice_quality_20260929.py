"""Paired, blind listening and latency benchmark for Eleven Flash and v4 Turbo.

Run from the repository root. Reads the local Eleven key without printing or
serializing it. One synthesis request is sent for each scheduled sample; no
synthesis retries occur. A sample has a 15-second final-event deadline.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
import wave

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'brain'))
from duplex.conversion import VOICE
from duplex.tts import EXPRESSIVE_MODEL, FLASH_MODEL, StreamingTTS

RESULTS = ROOT / 'brain' / 'results' / 'brain-quality-20260929' / 'voice-after'
SAMPLE_TIMEOUT_S = 15.0
CONNECT_TIMEOUT_S = 9.0
SAMPLE_RATE = 24000
CASES = [
    {'case_id': 'control-neutral', 'label': 'Neutral control', 'delivery': 'neutral',
     'text': 'The workshop opens at nine. The blue light is still on.'},
    {'case_id': 'response-warm', 'label': 'Warm acknowledgement', 'delivery': 'warm',
     'text': 'Thanks for waiting with me. I appreciate your patience.'},
    {'case_id': 'news-bright', 'label': 'Bright good news', 'delivery': 'bright',
     'text': 'That is wonderful news. I am so glad it worked!'},
]
MODELS = [FLASH_MODEL, EXPRESSIVE_MODEL]


def load_key() -> str | None:
    explicit = os.environ.get('ELEVENLABS_API_KEY')
    if explicit:
        return explicit.strip()
    for name in ('project.env', '.env'):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('ELEVENLABS_API_KEY='):
                value = line.split('=', 1)[1].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                return value or None
    return None


def build_schedule():
    result = []
    pair_index = 0
    for case in CASES:
        for repeat in (1, 2):
            pair = MODELS if pair_index % 2 == 0 else list(reversed(MODELS))
            result.extend({**case, 'repeat': repeat, 'model': model} for model in pair)
            pair_index += 1
    return result


def blind_id(index: int, sample: dict) -> str:
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, str(index) + sample['case_id']).hex[:6]
    return f'clip-{index + 1:02d}-{suffix}'


def write_wav(path: Path, pcm: bytes):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)


def failure(stage: str, error: BaseException) -> dict:
    return {'stage': stage, 'error_type': type(error).__name__}


async def render_one(key: str, index: int, sample: dict, audio_dir: Path) -> dict:
    started = time.monotonic()
    first_text = first_audio = None
    chunks = []
    total_bytes = aligned_bytes = 0
    source_bytes = {}
    warning_codes = []
    metric = None
    sample_blind_id = blind_id(index, sample)

    async def emit(event):
        nonlocal first_audio, total_bytes, aligned_bytes, metric
        if event.get('type') == 'audio':
            if first_audio is None:
                first_audio = time.monotonic()
            raw = base64.b64decode(event.get('pcm', ''), validate=True)
            chunks.append(raw)
            total_bytes += len(raw)
            source = str(event.get('alignment_source', 'unavailable'))
            source_bytes[source] = source_bytes.get(source, 0) + len(raw)
            if source.startswith('elevenlabs_'):
                aligned_bytes += len(raw)
        elif event.get('type') == 'latency':
            metric = event.get('tts')
        elif event.get('type') == 'voice_warning':
            code = event.get('code')
            if isinstance(code, str) and re.fullmatch(r'[a-z0-9_]{1,64}', code):
                warning_codes.append(code)

    tts = StreamingTTS(key, emit, model_id=sample['model'])
    connect_s = None
    status, problem = 'not_started', None
    try:
        try:
            await asyncio.wait_for(tts.start(), timeout=CONNECT_TIMEOUT_S)
            connect_s = time.monotonic() - started
        except Exception as error:
            status, problem = 'connect_failed', failure('connect', error)
            return record(index, sample, sample_blind_id, status, connect_s, None, None,
                          0, 0, {}, warning_codes, problem, time.monotonic()-started)

        await tts.begin(f'voice-quality-{index}', delivery=sample['delivery'])
        if tts.active is None or tts.muted:
            status, problem = 'begin_failed', {'stage': 'begin', 'error_type': 'no_active_context'}
            return record(index, sample, sample_blind_id, status, connect_s, None, None,
                          0, 0, {}, warning_codes, problem, time.monotonic()-started)

        first_text = time.monotonic()
        try:
            await tts.text(sample['text'])
            await tts.finish(sample['text'])
        except Exception as error:
            status, problem = 'send_failed', failure('send', error)
        else:
            deadline = time.monotonic() + SAMPLE_TIMEOUT_S
            while time.monotonic() < deadline:
                if metric is not None:
                    status = 'ok'
                    break
                if warning_codes or tts.muted:
                    status = 'provider_error'
                    problem = {'stage': 'synthesis', 'warning_codes': warning_codes[:3]}
                    break
                await asyncio.sleep(.02)
            else:
                status = 'final_timeout'
                problem = {'stage': 'synthesis', 'deadline_s': SAMPLE_TIMEOUT_S,
                           'audio_received': bool(total_bytes), 'final_received': False}

        pcm = b''.join(chunks)
        if pcm:
            write_wav(audio_dir / f'{sample_blind_id}.wav', pcm)
        text_audio = None
        if metric and isinstance(metric.get('first_byte_s'), (int, float)):
            text_audio = float(metric['first_byte_s'])
        elif first_text is not None and first_audio is not None:
            text_audio = first_audio-first_text
        return record(index, sample, sample_blind_id, status, connect_s, text_audio,
                      None if first_audio is None else first_audio-started,
                      total_bytes, aligned_bytes, source_bytes, warning_codes, problem,
                      time.monotonic()-started, bool(metric), len(pcm)/(SAMPLE_RATE*2),
                      f'{sample_blind_id}.wav' if pcm else None)
    finally:
        await tts.close()


def record(index, sample, sample_blind_id, status, connect_s, text_audio, onset,
           audio_bytes, aligned_bytes, source_bytes, warnings, problem, elapsed,
           final=False, duration=0, wav_file=None):
    return {'schedule_index': index, 'blind_id': sample_blind_id,
            'case_id': sample['case_id'], 'repeat': sample['repeat'],
            'model': sample['model'], 'delivery': sample['delivery'], 'text': sample['text'],
            'status': status, 'connect_s': connect_s,
            'first_text_to_first_audio_s': text_audio,
            'sample_start_to_first_audio_s': onset, 'audio_bytes': audio_bytes,
            'audio_duration_s': duration,
            'alignment_coverage': aligned_bytes/audio_bytes if audio_bytes else 0,
            'alignment_source_bytes': source_bytes, 'warning_codes': warnings[:3],
            'failure': problem, 'provider_final': final, 'elapsed_s': elapsed,
            'wav_file': wav_file}


def ms(value):
    return '' if value is None else f'{value*1000:.0f}'


def write_listen_page(rows, output):
    rendered = []
    for row in rows:
        label = next(case['label'] for case in CASES if case['case_id'] == row['case_id'])
        audio = (f'<audio controls preload="none" src="audio/{html.escape(row["wav_file"], quote=True)}"></audio>'
                 if row.get('wav_file') else '<span>Audio unavailable</span>')
        rendered.append('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            html.escape(row['blind_id']), html.escape(label), row['repeat'], audio,
            ms(row.get('connect_s')), ms(row.get('first_text_to_first_audio_s'))))
    page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>MIST voice quality blind A/B</title><style>body{font:16px/1.5 system-ui;max-width:1040px;margin:30px auto;padding:0 20px;color:#263832;background:#f6f6f0}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:10px;border-bottom:1px solid #d9e0d8}audio{width:220px}small{color:#5a625d}</style>'
        '<h1>MIST voice quality blind A/B</h1><p>Model identity is withheld from this page. Each pair uses the same fixed words, target delivery, clone and repeat. Rate emotion fit, naturalness, identity similarity, intelligibility and overacting separately.</p>'
        '<table><thead><tr><th>Blind clip</th><th>Case</th><th>Repeat</th><th>Audio</th><th>Connect ms</th><th>Text to first audio ms</th></tr></thead><tbody>'
        + ''.join(rendered) + '</tbody></table><p><small>Latency is a local provider measurement, not an end-to-end playback time.</small></p>')
    output.write_text(page, encoding='utf-8')


async def run_schedule(key, schedule, output_dir):
    audio_dir = output_dir / 'audio'
    audio_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    result_path = output_dir / 'results.json'
    metadata = {'date': '2026-09-29', 'voice_id': VOICE,
        'method': 'Three fixed utterances x two repeats x two models; paired model order alternates; one synthesis call per sample; no retries.',
        'connect_timeout_s': CONNECT_TIMEOUT_S, 'final_timeout_after_text_s': SAMPLE_TIMEOUT_S,
        'quality_note': 'No human quality ratings are inferred.', 'samples': rows}
    result_path.write_text(json.dumps(metadata, indent=2)+'\n', encoding='utf-8')
    for index, sample in enumerate(schedule):
        row = await render_one(key, index, sample, audio_dir)
        rows.append(row)
        result_path.write_text(json.dumps(metadata, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'sample': index+1, 'total': len(schedule), 'case_id': row['case_id'],
                          'model': row['model'], 'status': row['status'],
                          'connect_ms': None if row['connect_s'] is None else round(row['connect_s']*1000),
                          'first_audio_ms': None if row['first_text_to_first_audio_s'] is None else round(row['first_text_to_first_audio_s']*1000)}), flush=True)
    (output_dir/'blind-key.json').write_text(json.dumps([
        {k:r.get(k) for k in ('blind_id','case_id','repeat','model','delivery','text','status','wav_file')}
        for r in rows], indent=2)+'\n', encoding='utf-8')
    write_listen_page(rows, output_dir/'listen.html')
    print(json.dumps({'results':str(result_path),'listen_page':str(output_dir/'listen.html'),
                      'samples':len(rows),'failures':sum(r['status']!='ok' for r in rows)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='show schedule only; no key lookup or network access')
    parser.add_argument('--output-dir', type=Path, default=RESULTS,
                        help='new result folder; an existing results.json is never overwritten')
    args = parser.parse_args()
    schedule = build_schedule()
    if args.plan:
        print(json.dumps({'samples':len(schedule),'timeout_s':SAMPLE_TIMEOUT_S,
            'order':[{'case_id':r['case_id'],'repeat':r['repeat'],'model':r['model']} for r in schedule]},indent=2))
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if (args.output_dir/'results.json').exists():
        raise SystemExit(f'Refusing to overwrite previous run: {args.output_dir / "results.json"}')
    key = load_key()
    if not key:
        raise SystemExit('ElevenLabs key was not found in the configured local environment.')
    asyncio.run(run_schedule(key, schedule, args.output_dir))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
