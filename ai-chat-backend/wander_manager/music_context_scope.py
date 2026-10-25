"""Read-only scope for extra song-choice facts, never for canonical history."""

import json
from dataclasses import dataclass
from pathlib import Path

from time_utils import parse_ts


RESET_PATH = Path(__file__).resolve().parents[1] / 'data' / 'wander_music_context_reset.json'


@dataclass(frozen=True)
class MusicContextScope:
    session_id: str
    active_date: str
    completed_since: str = ''

    def facts(self):
        return {'active_date': self.active_date, 'completed_since': self.completed_since or None,
                'source': '漫想事件已绑定的活跃日；临时清空点仅影响额外选歌背景'}


def current_scope(session_id: str, *, reset_path=None) -> MusicContextScope:
    from conversation_manager_v2 import resolve_active_date

    active_date = resolve_active_date(session_id) if session_id else ''
    path = Path(reset_path) if reset_path is not None else RESET_PATH
    since = ''
    if path.exists():
        reset = json.loads(path.read_text(encoding='utf-8'))
        if reset.get('session_id') == session_id and reset.get('active_date') == active_date:
            since = str(reset['completed_since'])
            if parse_ts(since).year == 1:
                raise ValueError('invalid_music_context_reset_time')
    return MusicContextScope(session_id, active_date, since)
