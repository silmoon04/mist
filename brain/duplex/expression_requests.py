"""Constrain clear English face requests before a model's display call.

This is deliberately not a general intent or translation model. Unrecognised
phrasing keeps ordinary model choice; runtime argument validation still applies.
"""
import re

from duplex.expression_policy import FACE_MAP

_PRESETS = FACE_MAP['expressions']
_WORDS = {'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,
          'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
_NUMBER_WORD = '|'.join(_WORDS)
_NUMBER = re.compile(
    rf'(?:(?:minus|negative)\s+)?(?:[+-]?\d+(?:\.\d+)?(?:\s*/\s*\d+)?'
    rf'|(?:{_NUMBER_WORD})(?:\s+point\s+(?:{_NUMBER_WORD})(?:\s+(?:{_NUMBER_WORD}))*)?'
    rf'(?:\s+and\s+a\s+half)?|(?:a\s+)?half)\b',re.I)
_VARIANT = re.compile(r'\b(?:zero[ -]based\s+)?(?:variant|variation|index)\s*(?:number\s+|#\s*)?',re.I)
_TOKEN = r"[a-z][a-z0-9_-]*"
_GENERIC = {'a','an','the','your','my','our','his','her','their','some','current','same','this','that','requested','exact','existing','quick','brief','zero-based','based','face','expression','preset','variant','variation','index'}
_GENERIC.update({'show','select','use','display','choose','set','change','inspect','see','give'})
_GENERIC.update({'with','of','for','at','number','to','on','its','it','is'})
_COMMAND = re.compile(
    rf'\b(?:show|select|use|display|choose|set|change|inspect|see|give|put\s+on)\s+'
    rf'(?:(?:me|your|the|my|a|an|this|that|requested|exact|named|preset|face|expression|to)\s+)*'
    rf"[\"']?(?P<name>{_TOKEN})[\"']?",re.I)
_LABEL = re.compile(rf"[\"']?(?P<name>{_TOKEN})[\"']?\s+(?P<label>preset|face|expression)\b",re.I)
_INDEXED = re.compile(rf"[\"']?(?P<name>{_TOKEN})[\"']?(?=\s*,?\s*(?:zero[ -]based\s+)?(?:variant|variation|index)\b)",re.I)
_READ_ONLY = re.compile(
    r'\b(?:read|repeat|quote|translate|explain|describe|analyse|analyze|summarise|summarize)\b'
    r'|\b(?:what does|what is the verb|grammar|log entry|quoted|quotation)\b',re.I)
_NO_ACTION = re.compile(
    r'\b(?:without\s+(?:doing|executing|performing)|do\s+not\s+(?:do|run|execute|perform)|'
    r"don['’]t\s+(?:do|run|execute|perform)|not\s+(?:a\s+)?(?:face\s+)?(?:request|command|instruction))\b",re.I)
_UNCHANGED = re.compile(
    r'\b(?:keep|leave)\s+(?:(?:the|your|my|this|that|current|same|existing)\s+)*'
    r'(?:face|expression|display)\s*(?:(?:exactly|completely)\s+)?'
    r'(?:unchanged|alone|the same|as it is|as is)\b'
    r'|\b(?:keep|leave)\s+(?:(?:the|your|my)\s+)?(?:same|current|existing|this|that)\s+'
    r'(?:face|expression|display)\b'
    r'|\b(?:do not|don[\x27’]t|never)\s+(?:change|alter|replace|switch)\s+'
    r'(?:(?:the|your|my|current|same)\s+)*(?:face|expression|display)\b',re.I)
_CONDITIONAL = re.compile(r'\b(?:if|unless)\b',re.I)
_NEGATED = re.compile(r"\b(?:do not|don['’]t|never)\s+(?:show|select|use|display|choose)\s+"
                      rf"(?:(?:me|your|the|a|an|preset|face|expression)\s+)*[\"']?(?P<name>{_TOKEN})[\"']?(?:\s+(?:face|expression|preset))?",re.I)
_QUOTES = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d|\x27[^\x27\n]*\x27')


def _number(text):
    match = _NUMBER.match(text.strip())
    if not match:
        return None
    value = match.group().lower().strip()
    sign = -1 if value.startswith(('minus ', 'negative ')) else 1
    value = re.sub(r'^(?:minus|negative)\s+', '', value)
    try:
        if '/' in value:
            numerator, denominator = value.split('/')
            return sign * float(numerator) / float(denominator)
        if value in ('half','a half'):
            return sign * .5
        if ' and a half' in value:
            return sign * (_WORDS[value.split()[0]] + .5)
        if ' point ' in value:
            whole, decimal = value.split(' point ')
            return sign * float(str(_WORDS[whole]) + '.' + ''.join(str(_WORDS[w]) for w in decimal.split()))
        return sign * (_WORDS[value] if value in _WORDS else float(value))
    except (ValueError, KeyError, ZeroDivisionError):
        return None


def _unconditional_unchanged(text):
    for match in _UNCHANGED.finditer(text):
        # A later valid request may say "if unavailable, keep the face". That
        # conditional does not prohibit the valid selection itself.
        clause = re.split(r'[.!?;]', text[:match.start()])[-1]
        if not _CONDITIONAL.search(clause):
            return True
    return False


def expression_request_constraint(user_text):
    """Return a narrow constraint, or None for unconstrained ordinary language."""
    if not isinstance(user_text, str) or not user_text.strip():
        return None
    text = re.sub(r'\bdead\s+inside\b','dead_inside',user_text.replace('\u2212','-'),flags=re.I).strip()
    lower = text.lower()
    if _unconditional_unchanged(text):
        return {'forbid':True,'reason':'The user asked to keep the face unchanged.'}
    quoted_commands=[m for m in _QUOTES.finditer(text) if _COMMAND.search(m.group()[1:-1])]
    masked=text
    for span in reversed(quoted_commands):
        masked=masked[:span.start()]+' '*(span.end()-span.start())+masked[span.end():]
    outside_commands=[m for m in _COMMAND.finditer(masked) if m['name'].lower() not in _GENERIC
                      and not re.search(r"(?:do not|don['’]t|never)\s*$",masked[max(0,m.start()-16):m.start()],re.I)]
    if _READ_ONLY.search(text) and (_NO_ACTION.search(text) or (not outside_commands and (quoted_commands or re.search(r'\b(?:log entry|grammar|quoted|quotation)\b',text,re.I)))):
        return {'forbid':True,'reason':'This is a read-only or quoted request.'}

    candidates = []
    negatives=list(_NEGATED.finditer(text))
    for pattern in (_COMMAND, _LABEL, _INDEXED):
        for match in pattern.finditer(masked):
            name = match['name'].lower()
            if name in _GENERIC:
                continue
            if any(n.start() <= match.start() < n.end() for n in negatives):
                continue
            before = lower[max(0,match.start()-16):match.start()]
            if re.search(r"(?:do not|don['’]t|never)\s*$",before):
                continue
            # Keep descriptions like "show excitement" open, but a named
            # preset, numbered variation, known preset, or code-like name is exact.
            tail = masked[match.end():]
            named_preset = (pattern is _LABEL and match['label'].lower()=='preset') or (pattern is _COMMAND and bool(re.search(r'\bpreset\b',match.group(),re.I)))
            exact = name in _PRESETS or '_' in name or named_preset or bool(_VARIANT.search(tail))
            if exact:
                candidates.append((match.start(),match.end(),name,tail))
    if not candidates:
        if negatives:
            return {'forbid_names':[n['name'].lower() for n in negatives]}
        return None
    _,_,name,tail = max(candidates,key=lambda row:row[0])
    variants = list(_VARIANT.finditer(tail))
    variant = _number(tail[variants[0].end():]) if variants else None
    if name not in _PRESETS:
        return {'forbid':True,'reason':'The exact requested preset is unavailable.','requested_expression':name}
    if variants:
        count=1+len(_PRESETS[name].get('alts',[]))
        if variant is None or not float(variant).is_integer() or not 0 <= variant < count:
            return {'forbid':True,'reason':'The exact requested variant is unavailable.','requested_expression':name,'requested_variant':variant}
        return {'expression':name,'variant':int(variant)}
    return {'expression':name}


def validate_expression_request(user_text, args):
    """Raise ValueError for a display change forbidden by a clear user request.

    Returns None on success. Call the normal runtime validator afterward. This
    guard never changes, rounds, substitutes, or executes model arguments.
    """
    constraint = expression_request_constraint(user_text)
    if constraint is None:
        return
    suffix = ' Face unchanged; do not substitute another face.'
    if constraint.get('forbid'):
        raise ValueError(constraint['reason'] + suffix)
    if not isinstance(args,dict):
        return  # The ordinary argument validator owns malformed calls.
    if 'forbid_names' in constraint:
        if args.get('expression') in constraint['forbid_names']:
            raise ValueError('The requested face was explicitly negated.' + suffix)
        return
    if args.get('expression') != constraint['expression']:
        raise ValueError('The call differs from the exact requested preset.' + suffix)
    if 'variant' in constraint and args.get('variant',0) != constraint['variant']:
        raise ValueError('The call differs from the exact requested variant.' + suffix)
