"""Derive a private session review without changing its original ledger.

Audio file offsets and server receipt times are separate clocks. Generated audio
is not proof of browser playback; the review keeps that distinction visible.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from urllib.parse import urlencode


def _number(value, default=0.0):
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else default


def _text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _segments(chunks):
    streams = defaultdict(list)
    for chunk in chunks:
        rate = _number(chunk.get('sample_rate')) * max(1, _number(chunk.get('channels'), 1)) * 2
        if not rate or not _number(chunk.get('byte_count')):
            continue
        stream = chunk['stream']
        start = _number(chunk.get('timestamp_ms'))
        length = _number(chunk['byte_count']) / rate * 1000
        offset = _number(chunk.get('offset_bytes')) / rate * 1000
        metadata = chunk.get('metadata', {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        epoch = metadata.get('epoch')
        turn_id, sequence = metadata.get('turn_id'), metadata.get('seq')
        kind = metadata.get('type', 'audio')
        previous = streams[stream][-1] if streams[stream] else None
        # Mic packets have a capture receipt clock. Generated TTS can arrive much
        # faster than real time, so group a reply by epoch, not packet arrival gap.
        contiguous = previous and abs(previous['audio_end_ms'] - offset) < 1
        same_reply = (stream == 'assistant_generated' and previous and
            previous.get('epoch') == epoch and previous.get('turn_id') == turn_id and
            previous.get('sequence') == sequence and previous.get('kind') == kind)
        near_capture = stream == 'mic' and previous and abs(start - previous['end_ms']) < 250
        if contiguous and (same_reply or near_capture):
            previous['audio_end_ms'] = offset + length
            previous['end_ms'] = (previous['start_ms'] + previous['audio_end_ms'] - previous['audio_start_ms']
                                  if same_reply else max(previous['end_ms'], start + length))
        else:
            streams[stream].append(dict(start_ms=start, end_ms=start + length,
                audio_start_ms=offset, audio_end_ms=offset + length, stream=stream,
                epoch=epoch, turn_id=turn_id, sequence=sequence, kind=kind,
                clock='generated_receipt' if stream == 'assistant_generated' else 'capture_receipt'))
    return streams


def build_session_review(export, stored_turns, audio_chunks, annotations=None):
    """Return compact, path-free data for the paired review UI."""
    metadata = export.get('session', {})
    sid = metadata['trace_id']
    rows = export.get('events', [])
    parsed = [(row, row.get('event', {}), _number(row.get('elapsed_ms'))) for row in rows]
    duration = max((at for _, _, at in parsed), default=0)
    streams = _segments(audio_chunks)
    # A generated reply can contain audio beyond the browser's final interruption.
    # Its concatenated WAV must not extend the measured session duration.
    capture_end = max((s['end_ms'] for s in streams.get('mic', [])), default=0)
    duration = max(duration, capture_end)
    if not duration:
        duration = max((s['end_ms'] for group in streams.values() for s in group), default=0)
    audio = [dict(stream=name, duration_ms=max(s['audio_end_ms'] for s in segments),
                  url='/trial/audio?' + urlencode({'session': sid, 'stream': name}),
                  label='You · captured audio' if name == 'mic' else 'MIST · generated audio',
                  clock='capture_receipt' if name == 'mic' else 'generated_receipt', segments=segments)
             for name, segments in streams.items() if name in ('mic', 'assistant_generated')]
    issues, timings = [], []
    tracks = dict(user=streams.get('mic', []), mist=streams.get('assistant_generated', []), face=[], tools=[])
    finals = defaultdict(list)
    latest_captions = {}
    caption_turns = {}
    for row, event, at in parsed:
        kind = event.get('type', event.get('event', 'event'))
        if kind == 'transcript_done':
            finals[(event.get('role'), _text(event.get('text')))].append((at, event.get('turn_id')))
        if kind == 'caption_progress' and event.get('text'):
            latest_captions[(event.get('epoch'), event.get('sequence'))] = (event['text'], at, row.get('id'))
            if event.get('turn_id'):
                caption_turns[event['turn_id']] = (event['text'], at, row.get('id'))
        if kind in ('error', 'voice_warning', 'close_error', 'client_error'):
            issues.append(dict(id=f"event-{row.get('id')}", at_ms=at, severity='error',
                title='Voice or connection error', detail=str(event.get('message', event.get('error', kind)))[:1500], event_id=row.get('id')))
        if kind == 'expression' and event.get('phase') == 'transition':
            previous = tracks['face'][-1] if tracks['face'] else None
            if previous:
                previous['end_ms'] = at
            tracks['face'].append(dict(start_ms=at, end_ms=duration, expression=event.get('expression', 'unknown'),
                face_id=event.get('to'), source=event.get('source'), event_id=row.get('id')))
        if kind == 'tool':
            tracks['tools'].append(dict(start_ms=at, end_ms=min(duration, at + 500),
                name=event.get('name', 'tool'), status=(event.get('result') or {}).get('status'), event_id=row.get('id')))
        if kind == 'latency':
            metric = event.get('tts', event.get('conversion', {}))
            if isinstance(metric.get('first_byte_s'), (int, float)):
                timings.append(dict(label='Voice first audio', value_ms=metric['first_byte_s'] * 1000,
                    provenance='provider first frame after first text send', at_ms=at, event_id=row.get('id')))
        if kind == 'affect_decision' and event.get('phase') == 'finished' and isinstance(event.get('elapsed_ms'), (float, int)):
            timings.append(dict(label='Expression reader', value_ms=event['elapsed_ms'],
                provenance='server model request duration', at_ms=at, event_id=row.get('id')))
    fallback = [(row, event, at) for row, event, at in parsed if event.get('type') == 'audio_scheduled' and event.get('renderer') == 'original']
    if fallback:
        loaded = next((at for _, event, at in parsed if event.get('type') == 'audio_scheduled' and event.get('renderer') in ('drawn', 'handdrawn')), None)
        issues.append(dict(id='renderer-fallback', at_ms=fallback[0][2], severity='info' if loaded is not None else 'warning',
            title='Drawn animations loaded late' if loaded is not None else 'Drawn animations did not load',
            detail=(f"The drawn renderer appeared at {loaded / 1000:.1f} seconds. " if loaded is not None else '') +
                f"The browser reported its fallback renderer in {len(fallback)} playback snapshots.", event_id=fallback[0][0].get('id')))
    turns = []
    user_index = 0
    reviewed = {item.get('turn'): item for item in (annotations or {}).get('recognition_comparisons', [])
                if isinstance(item, dict)} if (annotations or {}).get('session_id') == sid else {}
    assistant_segments = [segment for segment in streams.get('assistant_generated', [])
                          if segment.get('kind') != 'listener_cue']
    used_segments = set()
    used_times = set()
    for stored in stored_turns:
        role = stored.get('role')
        if role not in ('user', 'assistant'):
            continue
        raw_text = stored.get('text', '')
        candidates = finals.get((role, _text(raw_text)), [])
        final = next((value for value in candidates if (role, value[0]) not in used_times), None)
        at, turn_id = final if final else (None, None)
        if at is None:
            # Stored turns use UTC milliseconds; avoid treating that as elapsed.
            at = max(0, _number(stored.get('timestamp_ms')) - _number(metadata.get('created_ms')))
            if at > duration:
                at = turns[-1]['at_ms'] if turns else 0
        used_times.add((role, at))
        turn = dict(id=stored.get('id'), role=role, text=raw_text, at_ms=at,
            transcript_source='live recognition' if role == 'user' else 'model generated',
            playback_verified=bool(stored.get('playback_verified')), played_text=stored.get('played_text'))
        if role == 'user':
            user_index += 1
            alternate = reviewed.get(user_index)
            if alternate:
                turn['recognition_review'] = dict(text=alternate.get('slow', ''),
                    provider='Deepgram Nova-3', basis='second ASR pass; not a human transcript',
                    assessment=alternate.get('assessment', ''))
                intervals = alternate.get('asr_intervals_wav_s', [])
                if intervals:
                    start = _number(intervals[0][0]) * 1000
                    end = _number(intervals[-1][1]) * 1000
                    turn['audio'] = dict(stream='mic', start_ms=start, end_ms=end,
                        mapping='second ASR word timestamps')
                    turn['start_ms'] = start + _number(alternate.get('session_offset_s')) * 1000
                    turn['end_ms'] = end + _number(alternate.get('session_offset_s')) * 1000
        if role == 'assistant':
            segment = next((item for item in assistant_segments if id(item) not in used_segments and
                            turn_id and item.get('turn_id') == turn_id), None)
            if segment is None:
                segment = next((item for item in assistant_segments if id(item) not in used_segments and
                                not item.get('turn_id')), None)
            if segment:
                used_segments.add(id(segment))
                turn['audio'] = dict(stream='assistant_generated', start_ms=segment['audio_start_ms'],
                    end_ms=segment['audio_end_ms'], mapping='turn identity; generation receipt' if segment.get('turn_id') else 'response order; generation receipt')
                turn['start_ms'] = segment['start_ms']
                turn['end_ms'] = segment['end_ms']
                matches = []
                if segment.get('turn_id') in caption_turns:
                    matches = [caption_turns[segment['turn_id']]]
                elif segment.get('sequence') is not None:
                    value = latest_captions.get((segment.get('epoch'), segment['sequence']))
                    matches = [value] if value else []
                elif sum(item.get('epoch') == segment.get('epoch') for item in assistant_segments) == 1:
                    matches = [value for (epoch, _), value in latest_captions.items() if epoch == segment.get('epoch')]
                if matches:
                    caption, caption_at, caption_event = max(matches, key=lambda value: value[1])
                    turn['displayed_caption'] = caption
                    if _text(caption) != _text(raw_text) and not _text(raw_text).startswith(_text(caption)):
                        issues.append(dict(id=f"caption-{stored.get('id')}", at_ms=caption_at, severity='warning',
                            title='Displayed caption differs from generated speech',
                            detail='Generated: ' + _text(raw_text) + '\nDisplayed: ' + _text(caption), event_id=caption_event))
        turns.append(turn)
    # Keep high-rate receipts in the raw ledger. This view only carries bounded
    # summaries; selecting Events can page the original endpoint separately.
    interesting = [dict(id=row.get('id'), at_ms=at, type=event.get('type', event.get('event', 'event')), event=event)
        for row, event, at in parsed if event.get('type', event.get('event')) not in
        ('debug_client', 'audio_delivery', 'caption_progress', 'playback_receipt', 'microphone_stats', 'microphone_transport',
         'transcript_delta', 'turn.delta', 'output_transcript.added', 'user_transcript')][-500:]
    return dict(schema_version=1, session=dict(trace_id=sid, started_at=metadata.get('started_at'),
        ended_at=metadata.get('ended_at'), status=metadata.get('status'), architecture=metadata.get('config', metadata.get('architecture', {}))),
        duration_ms=duration, turns=turns, tracks=tracks, audio=audio, issues=issues, timings=timings,
        events=interesting, event_count=len(rows), event_preview_limit=500,
        playback_note='MIST audio contains generated speech. Browser playback and interruptions are shown separately in Events. Times use server receipts.')
