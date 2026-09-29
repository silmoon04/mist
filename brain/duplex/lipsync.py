"""Audio-relative mouth cues. Character labels approximate speech shapes, not phonemes."""
import math
import struct


def shape(character, following=''):
    pair = (character + following).lower()
    if pair[:2] in ('ee', 'ea', 'ie', 'ey'):
        return 'EE'
    if pair[:2] in ('oo', 'ou', 'ow', 'ue'):
        return 'OO'
    if pair[:2] in ('sh', 'ch'):
        return 'S'
    if pair[:2] in ('th', 'dh'):
        return 'TH'
    for letters, name in (('a', 'AA'), ('e', 'EH'), ('iy', 'EE'), ('o', 'OH'),
                          ('uwqr', 'OO'), ('mbp', 'MBP'), ('fv', 'FV'),
                          ('lntd', 'LNT'), ('szcxjg', 'S'), ('hk', 'EH')):
        if character.lower() in letters and character:
            return name
    return 'EH' if character.isalnum() else 'rest'


def span_shape(spans, index):
    """Keep the dental spelling cue through both character spans of th/dh."""
    char = spans[index][0]
    if index and (spans[index - 1][0] + char).lower() in ('th', 'dh'):
        return 'TH'
    return shape(char, spans[index + 1][0] if index + 1 < len(spans) else '')


def spans_from_message(message, duration, audio_offset=0):
    """Convert provider reply-relative times to this PCM chunk's local clock.

    Multi-context Flash responses with sync_alignment carry cumulative times,
    including across flushes. Small edge overlaps are clipped, never stretched.
    """
    alignment = (message.get('normalizedAlignment') or message.get('normalized_alignment')
                 or message.get('alignment'))
    if not isinstance(alignment, dict):
        return []
    chars = alignment.get('chars')
    starts = alignment.get('charStartTimesMs', alignment.get('char_start_times_ms'))
    lengths = alignment.get('charDurationsMs', alignment.get('char_durations_ms', alignment.get('chars_durations_ms')))
    if not all(isinstance(value, list) for value in (chars, starts, lengths)):
        return []
    if not chars or len(chars) != len(starts) or len(chars) != len(lengths) or len(chars) > 20000:
        return []
    result = []
    previous = -1
    for char, start, length in zip(chars, starts, lengths):
        if (not isinstance(char, str) or not isinstance(start, (int, float))
                or not isinstance(length, (int, float)) or isinstance(start,bool) or isinstance(length,bool) or not math.isfinite(start)
                or not math.isfinite(length) or start < previous or start < 0 or length < 0):
            return []
        previous = start
        local_start,local_end=start/1000-audio_offset,(start+length)/1000-audio_offset
        if local_start > duration + .1 or local_end < -.1:
            return []
        result.append((char, max(0,min(duration,local_start)), max(0,min(duration,local_end))))
    return result


def caption_cues(message,duration,audio_offset=0,prefix=''):
    """Reveal only the provider-aligned spoken prefix at character onset times."""
    spans=spans_from_message(message,duration,audio_offset)
    if not spans:return [],prefix,'unavailable'
    text=prefix;events=[]
    for char,start,_ in spans:
        text+=char
        if start<duration:
            cue={'time':round(start,6),'text':text}
            if events and events[-1]['time']==cue['time']:events[-1]=cue
            else:events.append(cue)
    source='elevenlabs_normalized_alignment' if message.get('normalizedAlignment') or message.get('normalized_alignment') else 'elevenlabs_alignment'
    return events,text,source


def mouth_cues(raw, message, rate=24000, audio_offset=0):
    """Return 20 ms energy samples plus exact character boundaries for one PCM chunk."""
    samples = [value[0] / 32768 for value in struct.iter_unpack('<h', raw)]
    duration = len(samples) / rate
    spans = spans_from_message(message, duration, audio_offset)
    step = max(1, round(rate * .02))
    levels = [math.sqrt(sum(value * value for value in samples[i:i + step]) / len(samples[i:i + step]))
              for i in range(0, len(samples), step)]
    threshold = max(.0025, min(.01, max(levels, default=0) * .035))
    times = {i * step / rate for i in range(len(levels))}
    times.update(at for _, start, end in spans for at in (start, end) if at < duration)
    result = []
    for at in sorted(times):
        rms = levels[min(len(levels) - 1, int(at * rate / step))]
        active = next((i for i, (_, start, end) in enumerate(spans) if start <= at < end), None)
        if rms < threshold:
            viseme, amount = 'rest', 0
        else:
            # Unaligned material still follows sound, never fabricated word timings.
            viseme = span_shape(spans, active) if active is not None else 'AA'
            if viseme=='rest':
                # Orthographic spaces/punctuation do not prove an acoustic pause.
                before=next((i for i in range(active-1,-1,-1) if spans[i][0].isalnum()),None) if active is not None else None
                after=next((i for i in range(active+1,len(spans)) if spans[i][0].isalnum()),None) if active is not None else None
                viseme=span_shape(spans,before) if before is not None else span_shape(spans,after) if after is not None else 'AA'
            amount = 0 if viseme == 'rest' else min(.95, .2 + rms * 6)
        result.append({'time': round(at, 6), 'viseme': viseme, 'amount': round(amount, 4)})
    return result, 'elevenlabs_characters_audio_gated' if spans else 'audio_energy'


def slice_cues(cues, start, end):
    """Carry the active shape into each browser packet without a synthetic rest."""
    previous = next((cue for cue in reversed(cues) if cue['time'] <= start), None)
    result = [{**previous, 'time': 0}] if previous else []
    result.extend({**cue, 'time': round(cue['time'] - start, 6)} for cue in cues if start < cue['time'] < end)
    return result
