"""Offline Flux turn-gap sweep through the live voice event handler.

Run: python brain/benchmarks/naturalness/turn_gap_benchmark_20260929.py
No provider credentials or network calls are used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from test_streaming_voice_20260930 import StreamingVoiceTests


BRAIN = Path(__file__).resolve().parents[2]
QUESTION = 'What is the prototype called now, and do we know its battery life?'
TAIL = 'Please keep it brief.'


async def replay_case(complete_hold_ms: int, gap_ms: int | None) -> dict:
    fixture = StreamingVoiceTests('test_flux_cumulative_replaces_prior_version')
    await fixture.asyncSetUp()
    voice = fixture.voice
    voice.asr_provider = 'flux'
    voice.live_mode = True
    voice.hold_s = 0
    voice.complete_hold_s = complete_hold_ms / 1000
    loop = asyncio.get_running_loop()
    commits = []
    endpoints = {}
    original_dc_event = voice.dc_event

    async def record_dc_event(event):
        if event.get('type') == 'turn.done' and event['turn']['role'] == 'user':
            commits.append((loop.time(), event['turn']['transcript']))
        await original_dc_event(event)

    voice.dc_event = record_dc_event
    wire_names = {'speech_start': 'StartOfTurn', 'final': 'EndOfTurn',
                  'turn_end': 'EndOfTurn'}
    entries = [(0, 'speech_start', '1', QUESTION[:28]),
               (4, 'final', '1', QUESTION),
               (5, 'turn_end', '1', QUESTION)]
    if gap_ms is not None:
        entries.extend([(5 + gap_ms, 'speech_start', '2', 'Please keep'),
                        (15 + gap_ms, 'final', '2', TAIL),
                        (16 + gap_ms, 'turn_end', '2', TAIL)])
    began = loop.time()
    try:
        for at_ms, kind, turn, transcript in entries:
            await asyncio.sleep(max(0, began + at_ms / 1000 - loop.time()))
            raw = {'type': 'TurnInfo', 'event': wire_names[kind],
                   'turn_index': int(turn), 'transcript': transcript,
                   'end_of_turn_confidence': .87 if kind == 'turn_end' else None}
            if kind == 'turn_end':
                endpoints[turn] = loop.time()
            await voice.asr_event({'type': kind, 'turn_id': turn,
                                   'segment_id': int(turn), 'segment': transcript,
                                   'text': transcript, 'cumulative': True,
                                   'metadata': {'source': 'flux_model',
                                                'turn_index': int(turn),
                                                'end_of_turn_confidence': raw['end_of_turn_confidence']},
                                   'raw': raw})
        await asyncio.sleep(complete_hold_ms / 1000 + .06)
        last_turn = '2' if gap_ms is not None else '1'
        latency_ms = round((commits[-1][0] - endpoints[last_turn]) * 1000) if commits else None
        return {'complete_hold_ms': complete_hold_ms, 'gap_ms': gap_ms,
                'turn_count': len(commits), 'commits': [text for _, text in commits],
                'last_endpoint_to_last_commit_ms': latency_ms}
    finally:
        await fixture.asyncTearDown()
        fixture.doCleanups()


async def run(output_dir: Path) -> dict:
    cases = []
    for hold_ms in (0, 350, 800):
        for gap_ms in (None, 250, 600):
            cases.append(await replay_case(hold_ms, gap_ms))
    expected = {(0, None): 1, (0, 250): 2, (0, 600): 2,
                (350, None): 1, (350, 250): 1, (350, 600): 2,
                (800, None): 1, (800, 250): 1, (800, 600): 1}
    for case in cases:
        key = case['complete_hold_ms'], case['gap_ms']
        assert case['turn_count'] == expected[key], (key, case['commits'])
    report = {'clock': 'local asyncio monotonic',
              'input': 'scheduled normalized Flux events with TurnInfo payload shape',
              'cases': cases}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'turn-gap-sweep.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', '--output-dir', dest='output_dir', type=Path,
                        default=BRAIN / 'results/brain-quality-20260929/turn')
    args = parser.parse_args()
    result = asyncio.run(run(args.output_dir))
    for case in result['cases']:
        print(case['complete_hold_ms'], case['gap_ms'], case['turn_count'],
              case['last_endpoint_to_last_commit_ms'])
