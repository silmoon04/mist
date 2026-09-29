"""Conservative checks for explicit English robot-preview requests."""
import re


def validate_motion_request(text, action):
    if not isinstance(text, str):
        raise ValueError('A movement preview needs an explicit movement request.')
    plain = re.sub(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d', '', text).lower().replace('\u2019', "'")
    plain = re.sub(r'\s+', ' ', plain).strip()
    if re.search(r'\bwalk (?:me|us) through\b|\bstand by\b|\bsit (?:here |with me|with us)\b|\bstick around\b|\bstay with me\b', plain):
        raise ValueError('This is a conversational request, not a movement command. No preview was prepared.')
    if re.search(r"\b(?:don't|do not|never|without)\s+(?:actually\s+)?(?:walk|move|turn|rotate|pan|stand|sit)\b", plain):
        raise ValueError('The user did not request movement. No preview was prepared.')
    if re.search(r"\b(?:don't|do not|never|without)\s+(?:(?:want|need)(?: you)? to\s+)?(?:prepare|make|create|start|run|execute)\b.{0,35}\b(?:preview|motion|movement)\b", plain):
        raise ValueError('The user declined a movement preview. No preview was prepared.')
    if re.search(r'\b(?:read|quote|repeat|translate|explain|describe)\b', plain):
        raise ValueError('Reading or explaining motion does not request a movement preview.')
    quantity = r'(?:[-+]?\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|a quarter|half)'
    distance = quantity + r'\s*(?:cm|mm|m\b|centimet(?:er|re)s?|millimet(?:er|re)s?|met(?:er|re)s?|inches|feet|steps?)\b'
    angle = quantity + r'\s*(?:degrees?|\u00b0)'
    direction = r'(?:forwards?|backwards?|left|right)\b'
    if action == 'phone_pan':
        explicit = bool(re.search(r'\bpan\s+(?:(?:(?:the|your|my)\s+)?phone\b|' + direction + '|' + angle + r')|\b(?:rotate|turn)\b.{0,30}\bphone\b|\blook\s+(?:' + direction + '|' + angle + ')', plain))
    elif action == 'walk':
        explicit = bool(re.search(r'\b(?:walk|move|step|go)\s+(?:about\s+|by\s+)?(?:' + direction + '|' + distance + ')', plain))
    elif action == 'turn':
        explicit = bool(re.search(r'\b(?:turn|rotate)\s+(?:(?:the|your) (?:robot|body|base)\s+)?(?:' + direction + '|' + angle + ')', plain))
    elif action == 'stand':
        explicit = bool(re.search(r'\bstand (?:up|upright|tall|the robot|your body)\b', plain))
    elif action == 'sit':
        explicit = bool(re.search(r'\bsit down\b|\blower (?:the|your) body\b', plain))
    else:
        raise ValueError('Unknown preview action')
    word = {'phone_pan':'pan', 'walk':'walk', 'turn':'turn', 'stand':'stand', 'sit':'sit'}[action]
    explicit |= bool(re.fullmatch(r'(?:mist[, ]+)?(?:please )?' + word + r'(?: please)?[.!]?', plain))
    explicit |= bool(re.search(r'\bpreview\b.{0,35}\b' + word + r'(?:ing)?\b|\b' + word + r'(?:ing)?\b.{0,25}\bpreview\b', plain))
    if not explicit:
        raise ValueError('No clear movement request was found. Ask what movement is wanted; no preview was prepared.')
