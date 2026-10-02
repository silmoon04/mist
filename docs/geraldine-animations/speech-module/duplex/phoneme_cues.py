"""Optional English pronunciation cues in the existing mouth-cue wire format.

Provider character times bound each complete word. Dictionary phones choose its
mouth shapes; duration weights estimate their positions inside that word. Those
positions are not measured phoneme timestamps. PCM energy always gates opening.
Unknown words and words split at message edges use the existing character cues.
Each call uses only the current PCM/alignment payload and never waits for audio.
"""
from bisect import bisect_right
from functools import lru_cache
import json
import math
from pathlib import Path
import re
import struct

from .lipsync import span_shape


DICTIONARY_PATH = (Path(__file__).resolve().parents[1] / "art_direction" /
                   "mist_geraldine_animation_pack_20261002" / "speech" / "cmudict-subset.json")
WORD = re.compile(r"(?<![\w'\u2019])[A-Za-z]+(?:['\u2019][A-Za-z]+)*(?![\w'\u2019])")
PHONE_SHAPES = {
    "AA": ("AA",), "AE": ("AA",), "AH": ("AA",), "AO": ("OH",),
    "AW": ("AA", "OO"), "AY": ("AA", "EE"), "EH": ("EH",),
    "ER": ("OO",), "EY": ("EH", "EE"), "IH": ("EE",), "IY": ("EE",),
    "OW": ("OH", "OO"), "OY": ("OH", "EE"), "UH": ("OO",), "UW": ("OO",),
    "B": ("MBP",), "P": ("MBP",), "M": ("MBP",), "F": ("FV",),
    "V": ("FV",), "TH": ("TH",), "DH": ("TH",), "L": ("LNT",),
    "N": ("LNT",), "T": ("LNT",), "D": ("LNT",), "NG": ("LNT",),
    "S": ("S",), "Z": ("S",), "SH": ("S",), "ZH": ("S",),
    "CH": ("S",), "JH": ("S",), "K": ("EH",), "G": ("EH",),
    "HH": ("EH",), "R": ("OO",), "W": ("OO",), "Y": ("EE",),
}
VOWELS = frozenset(("AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER",
                    "EY", "IH", "IY", "OW", "OY", "UH", "UW"))
PHONEME_SOURCE = "elevenlabs_word_spans_estimated_phonemes_audio_gated"
CHARACTER_SOURCE = "elevenlabs_characters_audio_gated"


@lru_cache(maxsize=1)
def bundled_pronunciations():
    """Load once, with a character fallback when the optional data is absent."""
    try:
        payload = json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))
        entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
        return entries if isinstance(entries, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _phones(value):
    if isinstance(value, str):
        value = value.split()
    if not isinstance(value, (list, tuple)) or not value or len(value) > 128:
        return None
    if not all(isinstance(phone, str) and re.fullmatch(r"[A-Z]+[012]?", phone)
               and re.sub(r"[012]$", "", phone) in PHONE_SHAPES for phone in value):
        return None
    return tuple(value)


def word_pronunciation(word, *, variant=0, pronunciations=None, overrides=None):
    """Return CMU ARPABET phones. Ambiguous readings need an explicit variant.

    ``overrides={"read": "R IY1 D"}`` can select a known reading or a name.
    ``pronunciations`` accepts a complete CMU-style word-to-variants dictionary.
    No sentence-level pronunciation inference or acoustic recognition occurs.
    """
    if not isinstance(word, str) or not isinstance(variant, int) or isinstance(variant, bool) or variant < 0:
        return None
    key = word.casefold().replace("\u2019", "'")
    if hasattr(overrides, "get") and key in overrides:
        return _phones(overrides[key])
    dictionary = bundled_pronunciations() if pronunciations is None else pronunciations
    if not hasattr(dictionary, "get"):
        return None
    candidates = dictionary.get(key)
    if not isinstance(candidates, (list, tuple)) or variant >= len(candidates):
        return None
    return _phones(candidates[variant])


def word_visemes(word, **options):
    """Expose the selected pronunciation's shapes without claiming timings."""
    phones = word_pronunciation(word, **options)
    if phones is None:
        return None
    return tuple(shape for phone in phones for shape in PHONE_SHAPES[re.sub(r"[012]$", "", phone)])


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _provider_spans(message, duration, audio_offset):
    alignment = (message.get("normalizedAlignment") or message.get("normalized_alignment")
                 or message.get("alignment"))
    if not isinstance(alignment, dict):
        return []
    chars = alignment.get("chars")
    starts = alignment.get("charStartTimesMs", alignment.get("char_start_times_ms"))
    lengths = alignment.get("charDurationsMs", alignment.get("char_durations_ms", alignment.get("chars_durations_ms")))
    if not all(isinstance(values, list) for values in (chars, starts, lengths)):
        return []
    if not chars or len(chars) != len(starts) or len(chars) != len(lengths) or len(chars) > 20000:
        return []
    result = []
    previous = -1
    for char, start, length in zip(chars, starts, lengths):
        if (not isinstance(char, str) or len(char) != 1 or not _finite_number(start)
                or not _finite_number(length) or start < previous or start < 0 or length < 0
                or start + length > 90000):
            return []
        previous = start
        # Keep original boundaries while fitting phones, then clip to this PCM
        # window. Clipping a word first would stretch its pronunciation again.
        result.append((char, start / 1000 - audio_offset, (start + length) / 1000 - audio_offset))
    if not any(end > 0 and start < duration for _, start, end in result):
        return []
    return result


def _estimated_words(spans, duration, audio_offset, pronunciations, overrides, final, leading_word_complete):
    text = "".join(char for char, _, _ in spans)
    result = []
    for token in WORD.finditer(text):
        complete_start = token.start() > 0 or audio_offset == 0 or leading_word_complete
        complete_end = token.end() < len(text) or final
        if not complete_start or not complete_end:
            continue
        phones = word_pronunciation(token.group(), pronunciations=pronunciations, overrides=overrides)
        if not phones:
            continue
        start = spans[token.start()][1]
        end = max(span[2] for span in spans[token.start():token.end()])
        if end <= start or start >= duration or end <= 0:
            continue
        weighted = []
        for stressed in phones:
            phone = re.sub(r"[012]$", "", stressed)
            # Vowels take more of the word. Diphthongs retain their two shapes.
            # These fixed proportions are animation estimates, not alignment.
            weight = 1.0 if phone in VOWELS else .55
            shapes = PHONE_SHAPES[phone]
            for index, shape in enumerate(shapes):
                fraction = (1.0,) if len(shapes) == 1 else (.65, .35)
                weighted.append((shape, weight * fraction[index]))
        total = sum(weight for _, weight in weighted)
        cursor = start
        for index, (shape, weight) in enumerate(weighted):
            next_at = end if index == len(weighted) - 1 else cursor + (end - start) * weight / total
            if next_at > 0 and cursor < duration:
                result.append((shape, max(0.0, cursor), min(duration, next_at)))
            cursor = next_at
    return sorted(result, key=lambda span: span[1])


def _active_index(spans, starts, maximum_ends, at):
    index = bisect_right(starts, at) - 1
    while index >= 0:
        if maximum_ends[index] <= at:
            return None
        if spans[index][1] <= at < spans[index][2]:
            return index
        index -= 1
    return None


def _maximum_ends(spans):
    result = []
    maximum = -math.inf
    for _, _, end in spans:
        maximum = max(maximum, end)
        result.append(maximum)
    return result


def phoneme_mouth_cues(raw, message, rate=24000, audio_offset=0, *, pronunciations=None,
                       overrides=None, final=False, leading_word_complete=False):
    """Return ``(cues, source)`` for one PCM16LE chunk, without buffering.

    Call after the existing TTS code removes delivery tags and translates the
    provider's clock. Supply ``final=True`` only when the last aligned word is
    complete. Otherwise an unbounded trailing word keeps the character fallback.
    A later chunk's first word also falls back unless its leading separator is
    present or the caller knows that it is complete. No state waits for a word.

    ``source`` names estimated phonemes and marks mixed character fallback.
    Missing/invalid alignment produces only ``audio_energy``. Missing dictionary
    data, unknown words, and invalid overrides retain measured character timing.
    Slice the resulting list with ``duplex.lipsync.slice_cues`` as usual.
    """
    if not isinstance(raw, (bytes, bytearray, memoryview)) or len(raw) % 2:
        raise ValueError("PCM must contain complete little-endian 16-bit samples")
    if not _finite_number(rate) or rate <= 0 or not _finite_number(audio_offset) or audio_offset < 0:
        raise ValueError("Sample rate must be positive and audio offset must be finite and nonnegative")
    message = message if isinstance(message, dict) else {}
    samples = [value[0] / 32768 for value in struct.iter_unpack("<h", raw)]
    if not samples:
        return [], "audio_energy"
    duration = len(samples) / rate
    spans = _provider_spans(message, duration, audio_offset)
    final = final is True or message.get("isFinal") is True or message.get("is_final") is True
    phonemes = _estimated_words(spans, duration, audio_offset, pronunciations, overrides,
                               final, leading_word_complete is True)
    step = max(1, round(rate * .02))
    levels = [math.sqrt(sum(sample * sample for sample in samples[index:index + step]) /
                        len(samples[index:index + step])) for index in range(0, len(samples), step)]
    threshold = max(.0025, min(.01, max(levels, default=0) * .035))
    times = {index * step / rate for index in range(len(levels))}
    times.update(at for _, start, end in spans + phonemes for at in (start, end) if 0 <= at < duration)
    char_starts = [start for _, start, _ in spans]
    phone_starts = [start for _, start, _ in phonemes]
    char_ends = _maximum_ends(spans)
    phone_ends = _maximum_ends(phonemes)
    # Punctuation within voiced audio keeps the nearest aligned speech shape.
    # Written spaces do not establish acoustic pauses.
    neighbor = []
    before = None
    for index, (char, _, _) in enumerate(spans):
        if char.isalnum():
            before = index
        neighbor.append(before)
    after = None
    for index in range(len(spans) - 1, -1, -1):
        if spans[index][0].isalnum():
            after = index
        if neighbor[index] is None:
            neighbor[index] = after
    result = []
    used_phoneme = used_character = False
    for at in sorted(times):
        cue_time = round(at, 6)
        if cue_time >= duration:
            continue
        rms = levels[min(len(levels) - 1, int(at * rate / step))]
        if rms < threshold:
            viseme, amount = "rest", 0
        else:
            phone = _active_index(phonemes, phone_starts, phone_ends, at)
            char = _active_index(spans, char_starts, char_ends, at)
            if phone is not None:
                viseme = phonemes[phone][0]
                used_phoneme = True
            elif char is not None:
                index = char if spans[char][0].isalnum() else neighbor[char]
                last_phone = bisect_right(phone_starts, at) - 1
                if (not spans[char][0].isalnum() and index is not None and last_phone >= 0
                        and spans[index][1] <= phonemes[last_phone][2] + 1e-9
                        and spans[index][2] >= phonemes[last_phone][1]):
                    viseme = phonemes[last_phone][0]
                    used_phoneme = True
                else:
                    viseme = span_shape(spans, index) if index is not None else "AA"
                    used_character = True
            else:
                viseme = "AA"
                used_character = True
            amount = min(.95, .2 + rms * 6)
        result.append({"time": cue_time, "viseme": viseme, "amount": round(amount, 4)})
    # Very close estimated boundaries can round to the same microsecond.
    deduplicated = []
    for cue in result:
        if deduplicated and cue["time"] == deduplicated[-1]["time"]:
            deduplicated[-1] = cue
        else:
            deduplicated.append(cue)
    if not spans:
        source = "audio_energy"
    elif used_phoneme or phonemes:
        source = PHONEME_SOURCE + ("_mixed_character_fallback" if used_character else "")
    else:
        source = CHARACTER_SOURCE
    return deduplicated, source
