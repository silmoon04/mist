"""Small, local floor decisions for streaming speech.

These rules intentionally act only on high-confidence forms. Ambiguous speech
waits for an ASR endpoint; it must never cancel playback on word count alone.
"""
from __future__ import annotations

import re


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+(?:'[a-z]+)?", text.casefold())


def direct_stop(text: str) -> bool:
    """A stop addressed to the assistant, rather than a mention of stopping."""
    value = text.strip().casefold().strip(" .!?,")
    value = re.sub(r"^(?:(?:hey|okay|ok)\s+)?mist\s*[, ]\s*", "", value)
    value = re.sub(r"^please\s+", "", value)
    value = re.sub(r"\s+please$", "", value)
    value = re.sub(r"\s+now$", "", value)
    # An anchored imperative excludes negation, quotation, and reported speech.
    return bool(re.fullmatch(
        r"(?:stop(?:\s+(?:talking|speaking|moving))?|be quiet|"
        r"pause(?:\s+(?:speaking|for a (?:second|moment)))?|freeze|"
        r"that's enough)", value))


def backchannel(text: str) -> bool:
    words = _words(text)
    if not words:
        return False
    if ' '.join(words) in {
        'yeah', 'yep', 'right', 'okay', 'ok', 'hmm', 'hm', 'mhm',
        'mm hm', 'mm hmm', 'uh huh', 'aha', 'i see', 'i follow',
        'yeah i follow', 'okay go on', 'right go on', 'go on',
    }:
        return True
    return False


def continue_assistant(text: str) -> bool:
    """A short instruction to retain the assistant floor during playback."""
    value = ' '.join(_words(text))
    if not value:
        return False
    if re.fullmatch(r"(?:no )?(?:keep (?:going|talking|explaining)|"
                    r"(?:please )?(?:go on|carry on|continue))(?: please)?", value):
        return True
    if re.fullmatch(r"(?:i said )?(?:do not|don't|dont|never) stop"
                    r"(?: (?:talking|speaking))?"
                    r"(?: (?:i am|i'm|im) (?:still )?listening| keep (?:going|explaining))?", value):
        return True
    return False


def incomplete_turn(text: str) -> bool:
    """Conservative syntactic signs that a candidate endpoint is a fragment."""
    words = _words(text)
    if not words:
        return False
    value = ' '.join(words)
    if words[-1] in {'and', 'but', 'because', 'if', 'when', 'although',
                     'unless', 'so', 'um', 'uh', 'like', 'the', 'a', 'an', 'to'}:
        return True
    if value in {'so', 'i mean', 'the thing is', 'what i meant was',
                 'i was thinking', 'hold on', 'wait'}:
        return True
    if re.search(r"\b(?:i (?:was going to|wanted to|was thinking)|"
                 r"can you|could you|let me|what i meant was|the thing is)$", value):
        return True
    # An ellipsis after a discourse start is a self-repair cue even when the
    # last word itself is ordinary vocabulary.
    if re.search(r"(?:\.{2,}|…)\s*$", text) and re.match(
            r"^(?:i mean|so|well|the thing is|what i meant|hold on)\b", value):
        return True
    return False
