"""Bounded expression requests shared by the robot tool and browser contract."""
import json
from pathlib import Path

FACE_MAP = json.loads((Path(__file__).with_name('face_map.json')).read_text(encoding='utf-8'))
EXPRESSIONS = tuple(FACE_MAP['expressions'])
MIN_DURATION_MS = 400
MAX_DURATION_MS = 6000


def normalize_expression(args):
    if not isinstance(args, dict) or set(args) - {'expression', 'variant', 'duration_ms'}:
        raise ValueError('Expected expression, with optional variant and duration_ms')
    name = args.get('expression')
    if not isinstance(name, str) or name not in FACE_MAP['expressions']:
        raise ValueError('Unknown expression')
    preset = FACE_MAP['expressions'][name]
    variants = [preset['primary'], *preset.get('alts', [])]
    variant = args.get('variant', 0)
    if type(variant) is not int or not 0 <= variant < len(variants):
        raise ValueError('Variant is outside this expression preset')
    duration = args.get('duration_ms', preset.get('duration_ms', 2800))
    if type(duration) is not int:
        raise ValueError('duration_ms must be an integer')
    return {
        'expression': name,
        'variant': variant,
        'face_id': variants[variant],
        'duration_ms': max(MIN_DURATION_MS, min(MAX_DURATION_MS, duration)),
        'persistent': 'duration_ms' not in args,
    }


def expression_parameters():
    ranges = '; '.join(
        f"{name}: 0" + (f" to {len(preset.get('alts', []))}" if preset.get('alts') else '')
        for name, preset in FACE_MAP['expressions'].items()
    )
    return {
        'expression': {'type': 'string', 'enum': list(EXPRESSIONS)},
        'variant': {'type': 'integer', 'minimum': 0, 'maximum': 3,
                    'description': 'Optional zero-based integer; omit for the standard face. Valid indices: ' + ranges +
                                   '. If the requested index is missing, negative or fractional, make no expression call. Do not round or substitute.'},
        'duration_ms': {'type': 'integer', 'minimum': MIN_DURATION_MS, 'maximum': MAX_DURATION_MS,
                        'description': 'Optional timed flash after the entrance transition, only when a duration is requested. Omit to keep the face until it is changed. Listening and speaking do not reset a persistent face.'},
    }
