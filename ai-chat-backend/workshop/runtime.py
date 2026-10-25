"""Opt-in artifact bridge to the existing Affect authority, never a second recognizer."""
from html.parser import HTMLParser
from .store import now

FAMILIES = frozenset(('愉悦', '亲近', '安定', '兴趣', '低落', '担忧', '恼怒', '排斥', '惊讶', '复杂/未归类'))


class _Capabilities(HTMLParser):
    enabled = False
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'meta' and values.get('name') == 'mirrow-workshop-capabilities':
            self.enabled = 'mood.v1' in values.get('content', '').split()


def supports_mood(path):
    if path.suffix.lower() != '.html' or path.stat().st_size > 100000:
        return False
    parser = _Capabilities()
    parser.feed(path.read_text(encoding='utf-8'))
    return parser.enabled


def project_mood(affect):
    result = {'status': 'unavailable', 'family': None, 'checked_at': now()}
    try:
        settings = affect.settings()
        if settings.get('engine_mode') != 'enabled' or not settings.get('resonance_recognition_enabled') or not settings.get('resonance_channel_enabled'):
            return result
        dominant = (affect.get_projection().get('resonance') or {}).get('dominant') or {}
        if dominant.get('family') in FAMILIES:
            result.update(status='available', family=dominant['family'])
    except Exception:
        pass
    return result
