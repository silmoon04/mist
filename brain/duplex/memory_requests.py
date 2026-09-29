"""Require an explicit persistence request before a model writes a preference."""
import re


def validate_memory_request(text):
    if not isinstance(text, str):
        raise ValueError('Saving needs an explicit request to remember a preference for later.')
    # Quoted examples do not authorize actions; quoted preference content may
    # still follow a real request such as 'Remember this: "..."'.
    plain = re.sub(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d', '', text).lower().replace('\u2019', "'")
    if re.search(r"\b(?:do not|don't|never|without)\s+(?:(?:want|need)(?: you)? to\s+)?(?:save|store|remember)\b|\bstop\s+(?:saving|storing)\b", plain):
        raise ValueError('The user did not authorize saving this. Keep it in conversation context only.')
    if re.search(r'\b(?:read|quote|repeat|translate)\b.{0,45}\b(?:note|words|sentence|command|text)\b', plain):
        raise ValueError('Quoted or discussed instructions do not authorize a preference write.')
    future = bool(re.search(r'\b(?:from now on|for future (?:chats|conversations|sessions)|for next time|for later)\b', plain))
    temporary = bool(re.search(r'\b(?:for now|today|this (?:chat|conversation|session)|at the moment|right now)\b', plain))
    requested = bool(re.search(
        r"(?:^|[,;.!?]\s*)(?:(?:and|also|please|mist)[,\s]+|(?:can|could|would|will) you\s+|i(?:'d| would) like you to\s+|i want you to\s+)*"
        r'(?:remember|save|store)\b', plain.strip()))
    continuing = bool(re.match(r'^(?:from now on|for future (?:chats|conversations|sessions))\b', plain.strip()))
    if not (continuing or requested) or (temporary and not future):
        raise ValueError('No explicit request to save this for later. Use the current conversation context; no preference was saved.')
